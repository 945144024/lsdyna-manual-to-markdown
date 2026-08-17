"""Tests for PageMap/SectionMap quality gates."""

from lsdyna_manual.parser.quality import evaluate_inspection
from lsdyna_manual.parser.segmentation import (
    InspectionResult,
    PageMapEntry,
    Section,
    TOCEntry,
)


def _result(manual_type: str, coverage: float) -> InspectionResult:
    volume = 1 if manual_type == "keyword" else None
    document_id = "keyword-volume-1" if volume else "theory"
    return InspectionResult(
        volume=volume,
        document_id=document_id,
        manual_type=manual_type,
        pagemap=[
            PageMapEntry(pdf_page=page, manual_page=None, evidence=None)
            for page in range(1, 101)
        ],
        toc_index=[TOCEntry(name="entry", manual_page="1-1", indent=0)],
        sections=[
            Section(
                section_id="entry",
                keyword_id="entry" if volume else None,
                name="entry",
                volume=volume,
                kind=manual_type,
                parent_section_id=None,
                pdf_pages=[1],
                manual_pages=["1-1"],
                document_id=document_id,
            )
        ],
        stats={
            "pdf_pages": 100,
            "sections_located": 1,
            "sections_unresolved": 0,
            "pagemap_filled": int(coverage * 100),
            "pagemap_coverage": coverage,
        },
    )


def test_manual_profiles_have_distinct_coverage_thresholds():
    assert not evaluate_inspection(_result("keyword", 0.97)).passed
    assert evaluate_inspection(_result("theory", 0.97)).passed


def test_unresolved_sections_fail_quality_gate():
    result = _result("theory", 0.99)
    result.stats["sections_unresolved"] = 1

    quality = evaluate_inspection(result)

    assert not quality.passed
    assert {issue.code for issue in quality.issues} == {"SECTIONS_UNRESOLVED"}


def test_empty_navigation_artifacts_fail_quality_gate():
    result = InspectionResult(
        volume=None,
        document_id="theory",
        manual_type="theory",
        stats={
            "pdf_pages": 1,
            "sections_located": 0,
            "sections_unresolved": 0,
            "pagemap_filled": 0,
            "pagemap_coverage": 0.0,
        },
    )

    codes = {issue.code for issue in evaluate_inspection(result).issues}

    assert {"TOC_EMPTY", "SECTIONMAP_EMPTY", "PAGEMAP_COVERAGE_LOW"} <= codes


def test_contract_identity_and_page_sequence_are_quality_gates():
    result = _result("theory", 0.99)
    result.sections[0].document_id = "keyword-volume-1"
    result.pagemap[1].pdf_page = 1

    codes = {issue.code for issue in evaluate_inspection(result).issues}

    assert "SECTION_DOCUMENT_ID_MISMATCH" in codes
    assert "PAGEMAP_PDF_SEQUENCE_INVALID" in codes
