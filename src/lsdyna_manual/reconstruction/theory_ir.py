"""Theory section reconstruction with deterministic title-anchor ownership."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from lsdyna_manual.parser.page_ir import HeaderBlock, FooterBlock, ParseIssue, TextBlock
from lsdyna_manual.reconstruction.keyword_ir import BlockSourceRef, SourcedBlock
from lsdyna_manual.reconstruction.section_ir import SectionIR, SectionSourcePage


@dataclass
class TheoryIR:
    document_id: str
    section_id: str
    section_number: str | None
    title: str
    parent_section_id: str | None
    source_pages: list[SectionSourcePage] = field(default_factory=list)
    owned_sources: list[BlockSourceRef] = field(default_factory=list)
    content_blocks: list[SourcedBlock] = field(default_factory=list)
    ignored_blocks: list[SourcedBlock] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    status: str = "failed"

    @property
    def manual_type(self) -> str:
        return "theory"


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    source: BlockSourceRef | SectionSourcePage | None = None,
) -> ParseIssue:
    return ParseIssue(
        severity=severity,
        code=code,
        message=message,
        pdf_page=source.pdf_page if source is not None else None,
        manual_page=source.manual_page if source is not None else None,
    )


def _normalize(text: str) -> str:
    """Normalize presentation-only title differences conservatively."""

    value = unicodedata.normalize("NFKD", text)
    value = (
        value.replace("—", "-")
        .replace("–", "-")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    value = value.replace("$", "")
    value = re.sub(r"\\pm(?=\s|[-+0-9]|$)", "±", value)
    value = re.sub(r"\\mp(?=\s|[-+0-9]|$)", "∓", value)
    value = re.sub(
        r"\\(?:mathrm|text|textrm|rm|bf|mathbf|mathit|it)\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s*\^\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"_\s*\{([^{}]*)\}", r"_\1", value)
    value = value.replace("{", "").replace("}", "")
    value = value.replace("_", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _title_anchor_candidates(section: SectionIR) -> list[tuple[int, int, int]]:
    """Return ``(pdf_page, block_index, rank)`` title candidates.

    Rank 3 is an exact number/title anchor; rank 2 is a title-prefix anchor.
    Exact candidates always outrank longer OCR/title prefixes.
    """

    number = _normalize(section.section_number or section.section_id)
    title = _normalize(section.name)
    expected = f"{number} {title}".strip()
    candidates: list[tuple[int, int, int]] = []
    for page in section.pages:
        for index, block in enumerate(page.blocks):
            if not isinstance(block, (TextBlock, HeaderBlock)):
                continue
            text = _normalize(block.text)
            rank: int | None = None
            if isinstance(block, TextBlock) and text == expected:
                rank = 3
            elif isinstance(block, TextBlock) and text.startswith(f"{expected} "):
                rank = 2
            elif text == number and index + 1 < len(page.blocks):
                next_block = page.blocks[index + 1]
                if isinstance(next_block, TextBlock):
                    next_text = _normalize(next_block.text)
                    if next_text == title:
                        rank = 3
                    elif next_text.startswith(f"{title} "):
                        rank = 2
            if rank is not None:
                candidates.append((page.pdf_page, index, rank))
    return candidates


def _select_title_anchor(section: SectionIR) -> tuple[int, int] | None:
    candidates = _title_anchor_candidates(section)
    if not candidates:
        return None
    best_rank = max(item[2] for item in candidates)
    best = [item for item in candidates if item[2] == best_rank]
    if len(best) == 1:
        page, index, _rank = best[0]
        return page, index

    # Repeated exact anchors immediately adjacent in one page are a single
    # layout artefact.  Distant duplicates remain ambiguous.
    if len({page for page, _index, _rank in best}) == 1:
        page_number = best[0][0]
        page = next(
            (item for item in section.pages if item.pdf_page == page_number),
            None,
        )
        if page is not None:
            indices = sorted(index for _page, index, _rank in best)
            if all(
                all(
                    isinstance(block, (HeaderBlock, FooterBlock))
                    or (isinstance(block, TextBlock) and not block.text.strip())
                    for block in page.blocks[left + 1 : right]
                )
                for left, right in zip(indices, indices[1:])
            ):
                return page_number, indices[0]
    return None


def _title_anchor(section: SectionIR) -> tuple[int, int] | None:
    return _select_title_anchor(section)


def _source_ref(section: SectionIR, page, block_index: int) -> BlockSourceRef:
    return BlockSourceRef(
        document_id=section.document_id,
        pdf_page=page.pdf_page,
        manual_page=page.manual_page,
        block_index=block_index,
    )


def _build_owned_stream(
    section: SectionIR,
    start: tuple[int, int],
    end: tuple[int, int] | None,
) -> tuple[list[BlockSourceRef], list[SourcedBlock], list[SourcedBlock]]:
    owned: list[BlockSourceRef] = []
    content: list[SourcedBlock] = []
    ignored: list[SourcedBlock] = []
    for page in sorted(section.pages, key=lambda item: item.pdf_page):
        if page.pdf_page < start[0] or (end is not None and page.pdf_page > end[0]):
            continue
        block_start = start[1] if page.pdf_page == start[0] else 0
        block_end = end[1] if end is not None and page.pdf_page == end[0] else len(page.blocks)
        for block_index in range(block_start, block_end):
            source = _source_ref(section, page, block_index)
            sourced = SourcedBlock(source=source, block=page.blocks[block_index])
            owned.append(source)
            if isinstance(sourced.block, (HeaderBlock, FooterBlock)):
                ignored.append(sourced)
            else:
                content.append(sourced)
    return owned, content, ignored


def reconstruct_theory(sections: list[SectionIR]) -> list[TheoryIR]:
    """Assign Theory blocks from one strong title anchor to the next."""

    theory_sections = [section for section in sections if section.kind == "theory"]
    anchors = {
        index: anchor
        for index, section in enumerate(theory_sections)
        if (anchor := _title_anchor(section)) is not None
    }
    results: list[TheoryIR] = []
    for index, section in enumerate(theory_sections):
        issues = list(section.issues)
        anchor = anchors.get(index)
        if anchor is None:
            issues.append(
                _issue(
                    "THEORY_TITLE_ANCHOR_MISSING",
                    f"section {section.section_id} has no unique number/title anchor; "
                    "candidate PageIR content is preserved without ownership slicing",
                )
            )
            if section.pages:
                first_page = min(section.pages, key=lambda page: page.pdf_page)
                start = (first_page.pdf_page, 0)
            else:
                start = (0, 0)
            end = None
        else:
            end_candidates = [
                candidate
                for other_index, candidate in anchors.items()
                if other_index != index and candidate > anchor
            ]
            end = min(end_candidates) if end_candidates else None
            max_source_page = max(
                (page.pdf_page for page in section.source_pages), default=anchor[0]
            )
            if end is not None and end[0] > max_source_page:
                end = None
            start = anchor
            issues = [
                issue
                for issue in issues
                if issue.code
                not in {
                    "SECTION_SHARED_BOUNDARY_PAGE",
                    "THEORY_HIERARCHICAL_PAGE_OVERLAP",
                }
            ]
            if end is not None:
                issues.append(
                    _issue(
                        "THEORY_BOUNDARY_RESOLVED",
                        f"section {section.section_id} owns blocks from {anchor[0]}:"
                        f"{anchor[1]} to the next Theory title anchor at "
                        f"{end[0]}:{end[1]}",
                        severity="info",
                        source=next(
                            (
                                page
                                for page in section.source_pages
                                if page.pdf_page == anchor[0]
                            ),
                            None,
                        ),
                    )
                )

        owned, content, ignored = _build_owned_stream(section, start, end)
        if not content:
            issues.append(
                _issue(
                    "THEORY_CONTENT_EMPTY",
                    f"section {section.section_id} has no owned content blocks",
                    severity="error",
                )
            )
            status = "failed"
        elif any(issue.severity in {"warning", "error"} for issue in issues):
            status = "warning"
        else:
            status = "success"
        results.append(
            TheoryIR(
                document_id=section.document_id,
                section_id=section.section_id,
                section_number=section.section_number,
                title=section.name,
                parent_section_id=section.parent_section_id,
                source_pages=list(section.source_pages),
                owned_sources=owned,
                content_blocks=content,
                ignored_blocks=ignored,
                issues=issues,
                status=status,
            )
        )
    return results
