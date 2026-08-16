"""Document Inspection / Segmentation (deterministic, no model calls).

Builds the page navigation map described in docs/parser-interface.md:

- PageMap: per-page manual page number with an evidence class
  (footer / anchor / interpolated, or None when undetermined);
- SectionMap: Manual entry -> candidate PDF pages. Candidate pages are
  not a strict partition: adjacent sections may share a boundary page,
  and larger conservative overlaps are allowed when evidence is weak.

Entry start pages are located by the body keyword title line (a
standalone `*NAME` line or a `*NAME_OPTION` / `*NAME_{OPTION}` variant
declaration line). Running headers lag entry starts and carry alias
forms, so they are used only as attribution/verification evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from lsdyna_manual.parser.text_extractor import TextExtractor

FOOTER_RE = re.compile(r"(\d+)-(\d+)\s*\(([^)]+)\)")
TOC_DOTTED_RE = re.compile(r"^(\s*)(\S.*?)\s*\.{3,}\s*(\d+-\d+)\s*$")
TOC_BARE_NAME_RE = re.compile(r"^(\s*)(\*?[A-Za-z][A-Za-z0-9_()/ ]*?)\s*$")
ALIAS_LINE_RE = re.compile(r"^\s*\*(MAT_\d+[A-Z_]*)\s*:\s+\*([A-Z][A-Z0-9_]*)")


@dataclass
class PageMapEntry:
    pdf_page: int
    manual_page: str | None
    evidence: str | None  # "footer" | "anchor" | "interpolated" | None


@dataclass
class TOCEntry:
    name: str
    manual_page: str
    indent: int


@dataclass
class Section:
    keyword_id: str
    name: str
    volume: int
    pdf_pages: list[int]
    manual_pages: list[str | None]


@dataclass
class InspectionIssue:
    volume: int
    pdf_page: int | None
    manual_page: str | None
    keyword_id: str | None
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict:
        return {
            "volume": self.volume,
            "pdf_page": self.pdf_page,
            "manual_page": self.manual_page,
            "keyword_id": self.keyword_id,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class InspectionResult:
    volume: int
    pagemap: list[PageMapEntry] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    toc_index: list[TOCEntry] = field(default_factory=list)
    legacy_alias_map: dict[str, list[str]] = field(default_factory=dict)
    issues: list[InspectionIssue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _page_lines(page_text: str) -> list[str]:
    return [line.rstrip() for line in page_text.splitlines() if line.strip()]


def _chapter_of(manual_page: str) -> int:
    return int(manual_page.split("-")[0])


def _page_number_of(manual_page: str) -> int:
    return int(manual_page.split("-")[1])


def _title_line_re(name: str) -> re.Pattern[str]:
    """Match a standalone entry title line: the exact keyword name, or the
    name plus variant-declaration suffixes. A suffix starts with an
    OPTION token (`_OPTION`, `_{OPTION}`, `_OPTION_MODEL`,
    `_OPTION1_{OPTION2}`); ordinary name extensions do not match
    (`*MAT_EXAMPLE_PLASTIC` is a different keyword, not a variant of
    `*MAT_EXAMPLE`)."""
    tail = r"(?:_[A-Z][A-Z0-9]*)*"
    option = rf"_(?:OPTION[0-9]*|\{{OPTION[0-9]*\}}){tail}"
    suffix = rf"(?:{option})*"
    return re.compile(rf"^{re.escape(name)}{suffix}$")


def _scan_footers(pages: list[str]) -> tuple[dict[int, tuple[str, str]], set[int]]:
    """Return {pdf_page: (manual_page, tag)} and the set of TOC pages.

    Matches are validated as plausible footers (small chapter number,
    bounded page number, uppercase tag) so that body text in footer-less
    pages cannot masquerade as a printed page number.
    """
    footer_map: dict[int, tuple[str, str]] = {}
    toc_pages: set[int] = set()
    for index, page_text in enumerate(pages):
        pdf_page = index + 1
        lines = _page_lines(page_text)
        match = None
        if lines:
            match = FOOTER_RE.search(lines[-1])
            if match is None and len(lines) > 1:
                match = FOOTER_RE.search(lines[-2])
        if match is not None:
            chapter, page_number, tag = int(match.group(1)), int(match.group(2)), match.group(3).strip()
            # chapters extend past 50 for Volume I appendices (A-W); the
            # bound only rejects numbers matched inside body text
            if chapter > 200 or page_number > 3000 or not tag.isupper():
                continue
            manual_page = f"{match.group(1)}-{match.group(2)}"
            footer_map[pdf_page] = (manual_page, tag)
            if tag == "TABLE OF CONTENTS":
                toc_pages.add(pdf_page)
    return footer_map, toc_pages


def _parse_toc(pages: list[str], toc_pages: set[int]) -> list[TOCEntry]:
    """Parse TOC entries, merging keyword names wrapped across two lines."""
    entries: list[TOCEntry] = []
    pending: tuple[int, str] | None = None  # (indent, name) awaiting a page
    for pdf_page in sorted(toc_pages):
        lines = _page_lines(pages[pdf_page - 1])
        for line in lines:
            dotted = TOC_DOTTED_RE.match(line)
            if dotted is not None:
                indent_raw, label, manual_page = dotted.groups()
                if pending is not None:
                    label = f"{pending[1]} {label.strip()}".strip()
                    indent_raw = " " * pending[0]
                    pending = None
                entries.append(
                    TOCEntry(name=label, manual_page=manual_page, indent=len(indent_raw))
                )
                continue
            bare = TOC_BARE_NAME_RE.match(line)
            if bare is not None and bare.group(2):
                pending = (len(bare.group(1)), bare.group(2))
    return entries


def _scan_legacy_alias_map(pages: list[str]) -> dict[str, list[str]]:
    """Parse official `*MAT_NNN: *NAME` mapping lines (Volume II).

    One alias may legitimately map to several keywords (for example both
    *MAT_STEINBERG and *MAT_STEINBERG_LUND are material type 11), so the
    map is alias -> list of candidate canonical names.
    """
    alias_map: dict[str, list[str]] = {}
    for page_text in pages:
        for line in _page_lines(page_text):
            match = ALIAS_LINE_RE.match(line)
            if match is not None:
                alias, canonical = match.group(1), match.group(2)
                candidates = alias_map.setdefault(alias, [])
                if canonical not in candidates:
                    candidates.append(canonical)
    return alias_map


def _locate_entry_starts(
    pages: list[str],
    entries: list[TOCEntry],
    toc_pages: set[int],
    footer_map: dict[int, tuple[str, str]],
    issues: list[InspectionIssue],
    volume: int,
) -> dict[str, int]:
    """Locate the start page of each TOC entry.

    Where the entry's TOC page number maps back to a printed footer
    (footer-reverse), that page is the start - the footer is printed
    evidence and is not affected by keyword-name listings on overview or
    family-introduction pages. Only in footer-less regions is the start
    located by the body keyword title line, searched monotonically.
    """
    reverse_footer = {manual: pdf for pdf, (manual, _tag) in footer_map.items()}
    starts: dict[str, int] = {}
    search_from = 1
    for entry in entries:
        found = reverse_footer.get(entry.manual_page)
        if found is None:
            pattern = _title_line_re(entry.name)
            for index in range(search_from - 1, len(pages)):
                pdf_page = index + 1
                if pdf_page in toc_pages:
                    continue
                lines = _page_lines(pages[index])
                # skip the running header line (first non-empty line)
                for line in lines[1:]:
                    if pattern.match(line.strip()):
                        found = pdf_page
                        break
                if found is not None:
                    break
            if found is not None and found < search_from:
                issues.append(
                    InspectionIssue(
                        volume=volume,
                        pdf_page=found,
                        manual_page=entry.manual_page,
                        keyword_id=entry.name.lstrip("*"),
                        severity="warning",
                        code="SECTION_BOUNDARY_UNCERTAIN",
                        message=f"non-monotonic start for {entry.name}",
                    )
                )
        if found is None:
            issues.append(
                InspectionIssue(
                    volume=volume,
                    pdf_page=None,
                    manual_page=entry.manual_page,
                    keyword_id=entry.name.lstrip("*"),
                    severity="warning",
                    code="TOC_ENTRY_UNRESOLVED",
                    message=f"entry start page not located for {entry.name}",
                )
            )
            continue
        starts[entry.name] = found
        search_from = found
    return starts


def _build_pagemap(
    page_count: int,
    footer_map: dict[int, tuple[str, str]],
    entries: list[TOCEntry],
    starts: dict[str, int],
    volume: int,
    issues: list[InspectionIssue],
) -> list[PageMapEntry]:
    """Combine footer evidence and TOC+title anchors, then interpolate
    locally between anchors whose arithmetic is consistent."""
    manual_of: dict[int, str] = {}
    evidence_of: dict[int, str] = {}

    for pdf_page, (manual_page, _tag) in footer_map.items():
        manual_of[pdf_page] = manual_page
        evidence_of[pdf_page] = "footer"

    for entry in entries:
        start = starts.get(entry.name)
        if start is None:
            continue
        if start in manual_of:
            if manual_of[start] != entry.manual_page:
                issues.append(
                    InspectionIssue(
                        volume=volume,
                        pdf_page=start,
                        manual_page=entry.manual_page,
                        keyword_id=entry.name.lstrip("*"),
                        severity="warning",
                        code="ANCHOR_CONFLICT",
                        message=(
                            f"footer reports {manual_of[start]} but TOC reports "
                            f"{entry.manual_page} for {entry.name}"
                        ),
                    )
                )
            continue  # footer evidence wins; already recorded
        manual_of[start] = entry.manual_page
        evidence_of[start] = "anchor"

    # local interpolation between consecutive anchors
    anchors = sorted(manual_of)
    for prev, nxt in zip(anchors, anchors[1:]):
        same_chapter = _chapter_of(manual_of[prev]) == _chapter_of(manual_of[nxt])
        arithmetic_ok = (
            nxt - prev == _page_number_of(manual_of[nxt]) - _page_number_of(manual_of[prev])
        )
        if not (same_chapter and arithmetic_ok):
            continue
        for pdf_page in range(prev + 1, nxt):
            if pdf_page in manual_of:
                continue
            page_no = _page_number_of(manual_of[prev]) + (pdf_page - prev)
            manual_of[pdf_page] = f"{_chapter_of(manual_of[prev])}-{page_no}"
            evidence_of[pdf_page] = "interpolated"

    # global monotonic validation; violations are reported (they indicate
    # conflicting evidence rather than something we silently repair)
    known = sorted(manual_of)
    for prev, nxt in zip(known, known[1:]):
        if not (
            _chapter_of(manual_of[prev]) < _chapter_of(manual_of[nxt])
            or (
                _chapter_of(manual_of[prev]) == _chapter_of(manual_of[nxt])
                and _page_number_of(manual_of[prev]) < _page_number_of(manual_of[nxt])
            )
        ):
            issues.append(
                InspectionIssue(
                    volume=volume,
                    pdf_page=nxt,
                    manual_page=manual_of[nxt],
                    keyword_id=None,
                    severity="warning",
                    code="ANCHOR_CONFLICT",
                    message=(
                        f"page map not monotonic at pdf page {nxt} "
                        f"({manual_of[prev]} -> {manual_of[nxt]})"
                    ),
                )
            )

    return [
        PageMapEntry(
            pdf_page=pdf_page,
            manual_page=manual_of.get(pdf_page),
            evidence=evidence_of.get(pdf_page),
        )
        for pdf_page in range(1, page_count + 1)
    ]


def _build_sections(
    entries: list[TOCEntry],
    starts: dict[str, int],
    pagemap: list[PageMapEntry],
    content_end: int,
    volume: int,
) -> list[Section]:
    """Candidate pages per entry: [start_i, start_{i+1}] inclusive, so
    adjacent sections share the boundary page by construction."""
    located = [entry for entry in entries if entry.name in starts]
    sections: list[Section] = []
    manual_by_pdf = {entry.pdf_page: entry.manual_page for entry in pagemap}
    for position, entry in enumerate(located):
        start = starts[entry.name]
        end = starts[located[position + 1].name] if position + 1 < len(located) else content_end
        end = max(end, start)
        pdf_pages = list(range(start, end + 1))
        sections.append(
            Section(
                keyword_id=entry.name.lstrip("*"),
                name=entry.name,
                volume=volume,
                pdf_pages=pdf_pages,
                manual_pages=[manual_by_pdf.get(p) for p in pdf_pages],
            )
        )
    return sections


def inspect_volume(volume: int, pdf_path: Path, extractor: TextExtractor) -> InspectionResult:
    """Run deterministic inspection for one Manual volume."""
    pages = extractor.extract_pages(pdf_path)
    result = InspectionResult(volume=volume)

    footer_map, toc_pages = _scan_footers(pages)
    result.toc_index = _parse_toc(pages, toc_pages)
    result.legacy_alias_map = _scan_legacy_alias_map(pages)

    keyword_entries = [entry for entry in result.toc_index if entry.name.startswith("*")]
    # TOC indent depth semantics differ across volumes: Volume II lists
    # independent *MAT_ADD_* entries at indent 12 (verified: every one
    # has its own title line), while Volume I/III use indent 6 for
    # variants and deeper indents for entry-internal subheadings which
    # must stay out of the SectionMap. Allow indent 12 for Volume II
    # only; for other volumes keep the 0/6 entry levels.
    max_indent = 12 if volume == 2 else 6
    keyword_entries = [
        entry for entry in keyword_entries if entry.indent <= max_indent
    ]
    starts = _locate_entry_starts(
        pages, keyword_entries, toc_pages, footer_map, result.issues, volume
    )

    result.pagemap = _build_pagemap(
        len(pages), footer_map, keyword_entries, starts, volume, result.issues
    )

    header_token_re = re.compile(r"^\*[A-Za-z]")
    content_end = 1
    for index, page_text in enumerate(pages):
        lines = _page_lines(page_text)
        if lines and header_token_re.match(lines[0]):
            content_end = index + 1
    content_end = max(content_end, max(starts.values(), default=1))

    result.sections = _build_sections(
        keyword_entries, starts, result.pagemap, content_end, volume
    )

    filled = [e for e in result.pagemap if e.manual_page is not None]
    evidence_counts: dict[str, int] = {}
    for entry in filled:
        evidence_counts[entry.evidence or "unknown"] = (
            evidence_counts.get(entry.evidence or "unknown", 0) + 1
        )
    result.stats = {
        "pdf_pages": len(pages),
        "footer_pages": len(footer_map),
        "toc_entries_total": len(result.toc_index),
        "toc_keyword_entries": len(keyword_entries),
        "sections_located": len(result.sections),
        "sections_unresolved": len(keyword_entries) - len(starts),
        "pagemap_filled": len(filled),
        "pagemap_none": len(pages) - len(filled),
        "evidence": evidence_counts,
        "legacy_aliases": len(result.legacy_alias_map),
        "issues_by_code": {
            code: sum(1 for i in result.issues if i.code == code)
            for code in sorted({i.code for i in result.issues})
        },
    }
    return result


def write_inspection_artifacts(
    results: list[InspectionResult], output_dir: Path
) -> Path:
    """Write intermediate navigation artifacts; returns the output dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        volume_dir = output_dir / f"volume-{result.volume}"
        volume_dir.mkdir(parents=True, exist_ok=True)
        (volume_dir / "pagemap.json").write_text(
            json.dumps(
                [e.__dict__ for e in result.pagemap], indent=1, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        (volume_dir / "sectionmap.json").write_text(
            json.dumps(
                [s.__dict__ for s in result.sections], indent=1, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        (volume_dir / "toc_index.json").write_text(
            json.dumps(
                [e.__dict__ for e in result.toc_index], indent=1, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        (volume_dir / "legacy_alias_map.json").write_text(
            json.dumps(result.legacy_alias_map, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        (volume_dir / "issues.jsonl").write_text(
            "".join(
                json.dumps(issue.to_dict(), ensure_ascii=False) + "\n"
                for issue in result.issues
            ),
            encoding="utf-8",
        )
    summary = {
        "volumes": {
            result.volume: result.stats for result in results
        },
        "issues": [issue.to_dict() for result in results for issue in result.issues],
    }
    (output_dir / "inspection_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return output_dir
