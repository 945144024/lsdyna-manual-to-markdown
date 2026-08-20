"""Section-level intermediate representation built from PageIR pages."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Mapping

from lsdyna_manual.parser.page_ir import PageIR, ParseIssue
from lsdyna_manual.parser.segmentation import Section


@dataclass(frozen=True)
class SectionSourcePage:
    """A source page reference retained in the reconstructed section."""

    pdf_page: int
    manual_page: str | None

    def to_dict(self) -> dict[str, int | str | None]:
        return {"pdf_page": self.pdf_page, "manual_page": self.manual_page}


@dataclass
class SectionIR:
    """Ordered section metadata and the PageIR pages assigned to it.

    SectionMap ranges are intentionally conservative and can share boundary
    pages. ``source_pages`` preserves that candidate range exactly, while
    ``pages`` contains only PageIR artifacts that are currently available.
    """

    document_id: str
    section_id: str
    keyword_id: str | None
    name: str
    volume: int | None
    kind: str
    parent_section_id: str | None
    section_number: str | None
    legacy_ids: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    source_pages: list[SectionSourcePage] = field(default_factory=list)
    pages: list[PageIR] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    status: str = "failed"

    @property
    def manual_type(self) -> str:
        if self.kind == "keyword":
            return "keyword"
        if self.kind == "theory":
            return "theory"
        return "document"

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "section_id": self.section_id,
            "keyword_id": self.keyword_id,
            "name": self.name,
            "volume": self.volume,
            "kind": self.kind,
            "parent_section_id": self.parent_section_id,
            "section_number": self.section_number,
            "legacy_ids": list(self.legacy_ids),
            "options": list(self.options),
            "source_pages": [page.to_dict() for page in self.source_pages],
            "page_numbers": [page.pdf_page for page in self.pages],
            "issues": [issue.to_dict() for issue in self.issues],
            "status": self.status,
        }


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    source: SectionSourcePage | None = None,
) -> ParseIssue:
    return ParseIssue(
        severity=severity,
        code=code,
        message=message,
        pdf_page=source.pdf_page if source is not None else None,
        manual_page=source.manual_page if source is not None else None,
    )


def _theory_numbers(section: SectionIR | Section) -> tuple[int, ...]:
    number = getattr(section, "section_number", None) or section.section_id
    try:
        return tuple(int(part) for part in number.split("."))
    except ValueError:
        return ()


def _theory_hierarchy_overlap(left: Section, right: Section) -> bool:
    if left.kind != "theory" or right.kind != "theory":
        return False
    left_number = _theory_numbers(left)
    right_number = _theory_numbers(right)
    if not left_number or not right_number or left_number == right_number:
        return False
    return left_number[: len(right_number)] == right_number or right_number[
        : len(left_number)
    ] == left_number


def assemble_sections(
    sections: list[Section],
    page_irs: Mapping[tuple[str, int], PageIR],
    *,
    legacy_ids_by_section: Mapping[tuple[str, str], list[str]] | None = None,
) -> list[SectionIR]:
    """Assemble SectionIR objects without changing SectionMap page ranges."""

    legacy_ids_by_section = legacy_ids_by_section or {}
    shared_page_counts = Counter(
        (section.document_id or "", pdf_page)
        for section in sections
        for pdf_page in section.pdf_pages
    )
    assembled: list[SectionIR] = []
    for section in sections:
        issues: list[ParseIssue] = []
        if len(section.pdf_pages) != len(section.manual_pages):
            issues.append(
                _issue(
                    "SECTION_PAGE_RANGE_MISMATCH",
                    f"section {section.section_id} has {len(section.pdf_pages)} "
                    f"PDF pages but {len(section.manual_pages)} manual pages",
                    severity="error",
                )
            )

        source_pages = [
            SectionSourcePage(
                pdf_page=pdf_page,
                manual_page=(
                    section.manual_pages[index]
                    if index < len(section.manual_pages)
                    else None
                ),
            )
            for index, pdf_page in enumerate(section.pdf_pages)
        ]
        pages: list[PageIR] = []
        for source_page in source_pages:
            if shared_page_counts[(section.document_id or "", source_page.pdf_page)] > 1:
                owners = [
                    candidate
                    for candidate in sections
                    if candidate.document_id == section.document_id
                    and source_page.pdf_page in candidate.pdf_pages
                ]
                hierarchy_only = len(owners) > 1 and all(
                    _theory_hierarchy_overlap(section, owner)
                    for owner in owners
                    if owner is not section
                )
                if hierarchy_only:
                    issues.append(
                        _issue(
                            "THEORY_HIERARCHICAL_PAGE_OVERLAP",
                            f"PDF page {source_page.pdf_page} is shared by an "
                            "ancestor/descendant Theory range; ownership is "
                            "resolved by numeric section hierarchy",
                            severity="info",
                            source=source_page,
                        )
                    )
                else:
                    issues.append(
                        _issue(
                            "SECTION_SHARED_BOUNDARY_PAGE",
                            f"PDF page {source_page.pdf_page} is shared by multiple "
                            "SectionMap candidates; content is preserved for review",
                            source=source_page,
                        )
                    )
            page_ir = page_irs.get((section.document_id or "", source_page.pdf_page))
            if page_ir is None:
                issues.append(
                    _issue(
                        "SECTION_PAGEIR_MISSING",
                        f"PageIR is missing for PDF page {source_page.pdf_page}",
                        source=source_page,
                    )
                )
                continue
            pages.append(page_ir)
            source_blank = any(
                issue.code == "SOURCE_BLANK_PAGE" for issue in page_ir.issues
            )
            if not page_ir.blocks and not source_blank:
                issues.append(
                    _issue(
                        "SECTION_CONTENT_EMPTY",
                        f"PageIR for PDF page {source_page.pdf_page} has no blocks",
                        source=source_page,
                    )
                )
            issues.extend(
                issue.with_page_source(
                    pdf_page=page_ir.pdf_page,
                    manual_page=(
                        page_ir.manual_page
                        if page_ir.manual_page is not None
                        else source_page.manual_page
                    ),
                )
                for issue in page_ir.issues
            )

        if not pages:
            status = "failed"
        elif any(issue.severity == "error" for issue in issues):
            status = "warning"
        elif any(issue.severity in {"warning", "error"} for issue in issues):
            status = "warning"
        else:
            status = "success"

        assembled.append(
            SectionIR(
                document_id=section.document_id or "",
                section_id=section.section_id,
                keyword_id=section.keyword_id,
                name=section.name,
                volume=section.volume,
                kind=section.kind,
                parent_section_id=section.parent_section_id,
                section_number=section.section_number,
                legacy_ids=sorted(
                    legacy_ids_by_section.get(
                        (section.document_id or "", section.section_id),
                        [],
                    )
                ),
                source_pages=source_pages,
                pages=pages,
                issues=issues,
                status=status,
            )
        )
    return assembled
