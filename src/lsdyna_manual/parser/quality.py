"""Inspection quality gates used by CLI and PDF regression runs."""

from __future__ import annotations

from dataclasses import dataclass

from lsdyna_manual.parser.segmentation import InspectionIssue, InspectionResult

MIN_PAGEMAP_COVERAGE = {
    "keyword": 0.98,
    "theory": 0.95,
}


@dataclass(frozen=True)
class InspectionQuality:
    status: str
    issues: tuple[InspectionIssue, ...]
    metrics: dict[str, int | float | str]

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def _contract_issues(result: InspectionResult) -> list[InspectionIssue]:
    """Validate PageMap/SectionMap v0.1 semantic invariants."""
    found: list[InspectionIssue] = []
    expected_document_id = (
        f"keyword-volume-{result.volume}"
        if result.manual_type == "keyword" and result.volume in {1, 2, 3}
        else "theory" if result.manual_type == "theory" and result.volume is None else None
    )
    if expected_document_id is None or result.document_id != expected_document_id:
        found.append(
            _quality_issue(
                result,
                "DOCUMENT_ID_INVALID",
                "manual_type, volume, and document_id are inconsistent",
            )
        )

    pdf_pages = int(result.stats.get("pdf_pages", 0))
    page_numbers = [entry.pdf_page for entry in result.pagemap]
    if page_numbers != list(range(1, pdf_pages + 1)):
        found.append(
            _quality_issue(
                result,
                "PAGEMAP_PDF_SEQUENCE_INVALID",
                "PageMap must cover 1..pdf_pages in strict order",
            )
        )
    if any(
        (entry.manual_page is None) != (entry.evidence is None)
        for entry in result.pagemap
    ):
        found.append(
            _quality_issue(
                result,
                "PAGEMAP_EVIDENCE_INVALID",
                "manual_page and evidence must both be null or both be present",
            )
        )

    section_ids = [section.section_id for section in result.sections]
    section_id_set = set(section_ids)
    if len(section_ids) != len(section_id_set):
        found.append(
            _quality_issue(
                result,
                "SECTION_ID_DUPLICATE",
                "section_id must be unique within a document",
            )
        )
    for section in result.sections:
        if section.document_id != result.document_id:
            found.append(
                _quality_issue(
                    result,
                    "SECTION_DOCUMENT_ID_MISMATCH",
                    f"section {section.section_id} has a different document_id",
                )
            )
        if section.volume != result.volume:
            found.append(
                _quality_issue(
                    result,
                    "SECTION_VOLUME_MISMATCH",
                    f"section {section.section_id} has a different volume",
                )
            )
        if (
            not section.pdf_pages
            or section.pdf_pages != sorted(set(section.pdf_pages))
            or any(page < 1 or page > pdf_pages for page in section.pdf_pages)
        ):
            found.append(
                _quality_issue(
                    result,
                    "SECTION_PAGE_RANGE_INVALID",
                    f"section {section.section_id} has invalid candidate pages",
                )
            )
        if len(section.pdf_pages) != len(section.manual_pages):
            found.append(
                _quality_issue(
                    result,
                    "SECTION_PAGE_LABEL_COUNT_MISMATCH",
                    f"section {section.section_id} page arrays differ in length",
                )
            )
        if (
            section.parent_section_id is not None
            and section.parent_section_id not in section_id_set
        ):
            found.append(
                _quality_issue(
                    result,
                    "SECTION_PARENT_MISSING",
                    f"section {section.section_id} references a missing parent",
                )
            )
        keyword_identity_valid = (
            section.keyword_id == section.section_id
            if section.kind == "keyword"
            else section.keyword_id is None
        )
        if not keyword_identity_valid:
            found.append(
                _quality_issue(
                    result,
                    "SECTION_KEYWORD_ID_INVALID",
                    f"section {section.section_id} has an invalid keyword_id",
                )
            )
    return found


def evaluate_inspection(result: InspectionResult) -> InspectionQuality:
    """Evaluate a result without hiding parser warnings."""
    stats = result.stats
    issues = list(result.issues)
    threshold = MIN_PAGEMAP_COVERAGE.get(result.manual_type, 0.95)
    coverage = float(stats.get("pagemap_coverage", 0.0))

    quality_issues: list[InspectionIssue] = []
    if not result.toc_index:
        quality_issues.append(
            _quality_issue(result, "TOC_EMPTY", "no TOC entries were parsed")
        )
    if not result.sections:
        quality_issues.append(
            _quality_issue(result, "SECTIONMAP_EMPTY", "no sections were located")
        )
    unresolved = int(stats.get("sections_unresolved", 0))
    if unresolved:
        quality_issues.append(
            _quality_issue(
                result,
                "SECTIONS_UNRESOLVED",
                f"{unresolved} TOC section starts were not located",
            )
        )
    if coverage < threshold:
        quality_issues.append(
            _quality_issue(
                result,
                "PAGEMAP_COVERAGE_LOW",
                f"coverage {coverage:.4f} is below {threshold:.4f}",
            )
        )
    quality_issues.extend(_contract_issues(result))
    quality_issues.extend(issue for issue in issues if issue.severity == "error")

    status = "failed" if quality_issues else "passed"
    metrics = {
        "document_id": result.document_id or "",
        "manual_type": result.manual_type,
        "pdf_pages": int(stats.get("pdf_pages", 0)),
        "sections": int(stats.get("sections_located", 0)),
        "sections_unresolved": unresolved,
        "pagemap_filled": int(stats.get("pagemap_filled", 0)),
        "pagemap_coverage": coverage,
        "warning_count": sum(1 for issue in issues if issue.severity == "warning"),
        "error_count": sum(1 for issue in issues if issue.severity == "error"),
        "quality_status": status,
    }
    return InspectionQuality(status=status, issues=tuple(quality_issues), metrics=metrics)


def _quality_issue(
    result: InspectionResult,
    code: str,
    message: str,
) -> InspectionIssue:
    return InspectionIssue(
        document_id=result.document_id,
        manual_type=result.manual_type,
        volume=result.volume,
        pdf_page=None,
        manual_page=None,
        keyword_id=None,
        severity="error",
        code=code,
        message=message,
    )
