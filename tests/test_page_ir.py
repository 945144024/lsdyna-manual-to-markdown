"""Unit tests for the Canonical PageIR v0.1 data model."""

from lsdyna_manual.parser.page_ir import (
    Cell,
    HeaderBlock,
    MathBlock,
    PageIR,
    ParseIssue,
    TableBlock,
    TextBlock,
    block_from_dict,
    validate_page_ir,
)


def _page_ir():
    return PageIR(
        pdf_page=197,
        manual_page="2-131",
        blocks=[
            HeaderBlock(text="*MAT_ELASTIC"),
            TextBlock(text="Material type 1 description."),
            TableBlock(
                rows=[
                    [
                        Cell(text="Variable", row=0, column=0),
                        Cell(text="MID", row=0, column=1),
                    ],
                    [
                        Cell(text="Type", row=1, column=0),
                        Cell(text="I", row=1, column=1),
                    ],
                ]
            ),
            MathBlock(text=r"K=\frac{E}{3(1-2\nu)}"),
        ],
        issues=[ParseIssue(severity="info", code="TEST", message="ok")],
    )


def test_page_ir_roundtrip():
    original = _page_ir()
    restored = PageIR.from_dict(original.to_dict())

    assert restored.pdf_page == 197
    assert restored.manual_page == "2-131"
    assert isinstance(restored.blocks[0], HeaderBlock)
    assert isinstance(restored.blocks[1], TextBlock)
    assert isinstance(restored.blocks[2], TableBlock)
    assert restored.blocks[2].rows[1][1].text == "I"
    assert isinstance(restored.blocks[3], MathBlock)
    assert restored.issues[0].code == "TEST"
    assert restored.to_dict()["schema_version"] == "0.1"


def test_block_from_dict_rejects_unknown_type():
    import pytest

    with pytest.raises(ValueError, match="unknown PageIR block type"):
        block_from_dict({"type": "mystery"})


def test_validate_page_ir_identity_and_shape():
    page_ir = _page_ir()

    page_ir.document_id = "theory"
    assert validate_page_ir(
        page_ir, expected_document_id="theory", expected_pdf_page=197
    ) == []

    document_issues = validate_page_ir(
        page_ir, expected_document_id="keyword-volume-1"
    )
    assert any(
        issue.code == "PAGEIR_DOCUMENT_IDENTITY_MISMATCH"
        for issue in document_issues
    )

    identity_issues = validate_page_ir(page_ir, expected_pdf_page=198)
    assert any(issue.code == "PAGEIR_PAGE_IDENTITY_MISMATCH" for issue in identity_issues)

    bad = PageIR(
        pdf_page=0,
        manual_page=None,
        blocks=[],
        issues=[ParseIssue(severity="critical", code="X", message="bad")],
    )
    issues = validate_page_ir(bad)
    codes = {issue.code for issue in issues}
    assert "PAGEIR_INVALID_PDF_PAGE" in codes
    assert "PAGEIR_INVALID_ISSUE_SEVERITY" in codes
