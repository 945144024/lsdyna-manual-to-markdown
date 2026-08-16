"""PaddleOCR-VL remote raw result -> Canonical PageIR adapter.

The adapter consumes the page-level raw JSON persisted by
``raw_store``. It prefers the structured ``prunedResult.parsing_res_list``
over the rendered Markdown text because that list preserves block labels,
bboxes, and ordering information. The Paddle Markdown remains a raw
debugging artifact only.
"""

from __future__ import annotations

import html
import json
from html.parser import HTMLParser
from pathlib import Path

from lsdyna_manual.parser.adapters.base import PageAdapter
from lsdyna_manual.parser.page_ir import (
    Block,
    Cell,
    FigureBlock,
    FooterBlock,
    HeaderBlock,
    MathBlock,
    PageIR,
    ParseIssue,
    TableBlock,
    TextBlock,
)


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._in_cell = False
        self.has_spans = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"}:
            self._current_cell = []
            self._in_cell = True
            attr_dict = dict(attrs)
            if "rowspan" in attr_dict or "colspan" in attr_dict:
                self.has_spans = True

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_cell is not None:
            self._in_cell = False
            if self._current_row is not None:
                self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None


def _parse_html_table(table_html: str) -> tuple[list[list[str]], bool]:
    parser = _TableHTMLParser()
    parser.feed(table_html)
    parser.close()
    return parser.rows, parser.has_spans


def _bbox_from_list(value: list[float] | None) -> tuple[float, float, float, float] | None:
    if value is None or len(value) != 4:
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _block_from_parsing_result(item: dict) -> tuple[Block, list[ParseIssue]]:
    label = str(item.get("block_label") or "text")
    content = str(item.get("block_content") or "")
    bbox = _bbox_from_list(item.get("block_bbox"))
    issues: list[ParseIssue] = []

    if label == "header":
        return HeaderBlock(text=content, bbox=bbox), issues
    if label == "footer":
        return FooterBlock(text=content, bbox=bbox), issues
    if label in {"formula", "equation"}:
        return MathBlock(text=content, bbox=bbox), issues
    if label in {"image", "figure", "figure_image", "seal"}:
        return FigureBlock(text=content, bbox=bbox), issues
    if label == "table":
        rows, has_spans = _parse_html_table(content)
        cells = [
            [
                Cell(text=cell_text, row=row_index, column=col_index)
                for col_index, cell_text in enumerate(row)
            ]
            for row_index, row in enumerate(rows)
        ]
        if has_spans:
            issues.append(
                ParseIssue(
                    severity="warning",
                    code="TABLE_STRUCTURE_UNCERTAIN",
                    message=(
                        "Paddle table contains rowspan/colspan; current PageIR "
                        "projects it into a rectangular table without guessing "
                        "cell text"
                    ),
                )
            )
        return TableBlock(rows=cells, bbox=bbox), issues

    # paragraph_title, figure_title, text, and other text-like labels all
    # become TextBlock for PageIR v0.1. No new subtypes are introduced
    # until real-page review proves they are required.
    return TextBlock(text=content, bbox=bbox), issues


class PaddleOCRVLAdapter(PageAdapter):
    ADAPTER_VERSION = "paddleocr-vl-adapter:1"

    def identity(self) -> str:
        return self.ADAPTER_VERSION

    def adapt_page(
        self,
        raw_page_json_path: Path,
        *,
        pdf_page: int,
        manual_page: str | None,
    ) -> PageIR:
        record = json.loads(raw_page_json_path.read_text(encoding="utf-8"))
        layout_result = record.get("layout_result") or {}
        pruned = layout_result.get("prunedResult") or {}
        parsing_results = pruned.get("parsing_res_list") or []

        if not parsing_results:
            fallback_text = (
                layout_result.get("markdown", {}).get("text", "")
                if isinstance(layout_result, dict)
                else ""
            )
            blocks: list[Block] = (
                [TextBlock(text=fallback_text)] if fallback_text.strip() else []
            )
            issues = [
                ParseIssue(
                    severity="warning",
                    code="READING_ORDER_AMBIGUOUS",
                    message="Paddle raw result contains no parsing_res_list blocks",
                )
            ]
        else:
            blocks = []
            issues = []
            for item in parsing_results:
                block, block_issues = _block_from_parsing_result(item)
                blocks.append(block)
                issues.extend(block_issues)

        # Reject malformed table projection only when it is obviously broken.
        for block in blocks:
            if isinstance(block, TableBlock):
                widths = {len(row) for row in block.rows}
                if len(widths) > 1:
                    issues.append(
                        ParseIssue(
                            severity="error",
                            code="TABLE_STRUCTURE_UNCERTAIN",
                            message=(
                                "projected table has unequal row widths; raw "
                                "artifact must be inspected before using this PageIR"
                            ),
                        )
                    )

        return PageIR(
            pdf_page=pdf_page,
            manual_page=manual_page,
            blocks=blocks,
            issues=issues,
        )
