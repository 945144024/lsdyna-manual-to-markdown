"""Tests for deterministic real-PDF regression metadata."""

from lsdyna_manual.regression import apply_review_baseline, review_pages
from lsdyna_manual.parser.segmentation import (
    InspectionResult,
    PageMapEntry,
    Section,
)


def _identity_record() -> dict:
    return {
        "release": "R17",
        "document_id": "theory",
        "source_sha256": "source",
        "pagemap_sha256": "pagemap",
        "sectionmap_sha256": "sectionmap",
        "pdf_pages": 100,
        "sections": 4,
        "pagemap_filled": 96,
        "llm_review_status": "pending",
    }


def test_review_pages_samples_boundaries_and_evidence():
    result = InspectionResult(
        volume=None,
        document_id="theory",
        manual_type="theory",
        pagemap=[
            PageMapEntry(pdf_page=page, manual_page=f"1-{page}", evidence=evidence)
            for page, evidence in ((4, "anchor"), (5, "interpolated"), (6, "footer"))
        ],
        sections=[
            Section(
                section_id=str(page),
                keyword_id=None,
                name=str(page),
                volume=None,
                kind="theory",
                parent_section_id=None,
                pdf_pages=[page],
                manual_pages=[f"1-{page}"],
                document_id="theory",
            )
            for page in (10, 30, 50, 70, 90)
        ],
        stats={"pdf_pages": 100},
    )

    selected = review_pages(result)

    assert {1, 2, 3, 4, 5, 6, 10, 30, 50, 70, 90, 100} <= set(selected)


def test_review_baseline_requires_exact_source_and_artifact_identity():
    record = _identity_record()
    baseline_record = {
        **record,
        "llm_review_status": "passed",
        "llm_reviewed_pdf_pages": [1, 25, 50, 100],
    }
    baseline = {
        "review": {
            "method": "visual-model-review",
            "reviewed_at": "2026-08-17",
        },
        "documents": [baseline_record],
    }

    apply_review_baseline([record], baseline)

    assert record["llm_review_status"] == "passed"
    assert record["llm_reviewed_pdf_pages"] == [1, 25, 50, 100]

    changed = _identity_record()
    changed["sectionmap_sha256"] = "changed"
    apply_review_baseline([changed], baseline)
    assert changed["llm_review_status"] == "pending"
    assert "llm_reviewed_pdf_pages" not in changed
