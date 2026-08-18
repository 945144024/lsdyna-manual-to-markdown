"""Tests for the PaddleOCR-VL raw result adapter."""

import json

from lsdyna_manual.parser.adapters.paddleocr_vl import PaddleOCRVLAdapter
from lsdyna_manual.parser.page_ir import HeaderBlock, TableBlock, TextBlock


def _write_raw(tmp_path, parsing_res_list):
    path = tmp_path / "page.json"
    path.write_text(
        json.dumps(
            {
                "volume": 2,
                "pdf_page": 197,
                "layout_result": {
                    "markdown": {"text": "raw markdown fallback", "images": {}},
                    "prunedResult": {"parsing_res_list": parsing_res_list},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_adapter_maps_parsing_res_list(tmp_path):
    path = _write_raw(
        tmp_path,
        [
            {
                "block_label": "header",
                "block_content": "*MAT_ELASTIC",
                "block_bbox": [10, 20, 30, 40],
            },
            {
                "block_label": "text",
                "block_content": "Purpose text",
                "block_bbox": [10, 50, 30, 70],
            },
            {
                "block_label": "table",
                "block_content": (
                    "<table><tr><td>Variable</td><td>MID</td></tr>"
                    "<tr><td>Type</td><td>I</td></tr></table>"
                ),
                "block_bbox": [10, 80, 100, 130],
            },
        ],
    )
    adapter = PaddleOCRVLAdapter()
    page_ir = adapter.adapt_page(
        path,
        pdf_page=197,
        manual_page="2-131",
    )

    assert page_ir.pdf_page == 197
    assert page_ir.manual_page == "2-131"
    assert isinstance(page_ir.blocks[0], HeaderBlock)
    assert page_ir.blocks[0].bbox == (10.0, 20.0, 30.0, 40.0)
    assert isinstance(page_ir.blocks[1], TextBlock)
    assert page_ir.blocks[1].text == "Purpose text"
    assert isinstance(page_ir.blocks[2], TableBlock)
    assert page_ir.blocks[2].rows[1][1].text == "I"
    assert page_ir.issues == []


def test_adapter_preserves_colspan_without_uncertainty(tmp_path):
    path = _write_raw(
        tmp_path,
        [
            {
                "block_label": "table",
                "block_content": (
                    '<table><tr><td colspan="2">A</td></tr>'
                    "<tr><td>B</td><td>C</td></tr></table>"
                ),
            }
        ],
    )
    page_ir = PaddleOCRVLAdapter().adapt_page(
        path,
        pdf_page=198,
        manual_page=None,
    )
    table = page_ir.blocks[0]
    assert isinstance(table, TableBlock)
    assert table.rows[0][0].text == "A"
    assert table.rows[0][0].column == 0
    assert table.rows[0][0].colspan == 2
    assert table.rows[1][1].column == 1
    assert page_ir.issues == []


def test_adapter_positions_cells_after_rowspan(tmp_path):
    path = _write_raw(
        tmp_path,
        [
            {
                "block_label": "table",
                "block_content": (
                    '<table><tr><td rowspan="2">MID</td><td>first</td></tr>'
                    "<tr><td>continued<br>line</td></tr></table>"
                ),
            }
        ],
    )

    page_ir = PaddleOCRVLAdapter().adapt_page(
        path,
        pdf_page=198,
        manual_page=None,
    )

    table = page_ir.blocks[0]
    assert isinstance(table, TableBlock)
    assert table.rows[0][0].rowspan == 2
    assert table.rows[1][0].column == 1
    assert table.rows[1][0].text == "continued\nline"
    assert page_ir.issues == []


def test_adapter_reports_invalid_span_attribute(tmp_path):
    path = _write_raw(
        tmp_path,
        [
            {
                "block_label": "table",
                "block_content": '<table><tr><td colspan="many">A</td></tr></table>',
            }
        ],
    )

    page_ir = PaddleOCRVLAdapter().adapt_page(
        path,
        pdf_page=198,
        manual_page=None,
    )

    assert any(issue.code == "TABLE_SPAN_INVALID" for issue in page_ir.issues)


def test_adapter_falls_back_to_markdown_when_no_blocks(tmp_path):
    path = _write_raw(tmp_path, [])
    page_ir = PaddleOCRVLAdapter().adapt_page(
        path,
        pdf_page=199,
        manual_page=None,
    )
    assert isinstance(page_ir.blocks[0], TextBlock)
    assert page_ir.blocks[0].text == "raw markdown fallback"
    assert any(issue.code == "READING_ORDER_AMBIGUOUS" for issue in page_ir.issues)


def test_adapter_uses_pagemap_manual_page_not_paddle_footer(tmp_path):
    path = _write_raw(
        tmp_path,
        [
            {
                "block_label": "footer",
                "block_content": "99-99 (MAT)",
            }
        ],
    )
    page_ir = PaddleOCRVLAdapter().adapt_page(
        path,
        pdf_page=201,
        manual_page="2-135",
    )
    assert page_ir.manual_page == "2-135"
    assert page_ir.blocks[0].text == "99-99 (MAT)"
