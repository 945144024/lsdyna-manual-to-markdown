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
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

from lsdyna_manual.documents import MANUAL_TYPE_KEYWORD, MANUAL_TYPE_THEORY, ManualDocument
from lsdyna_manual.parser.text_extractor import TextExtractor

FOOTER_RE = re.compile(r"(\d+)-(\d+)\s*\(([^)]+)\)")
TOC_DOTTED_RE = re.compile(r"^(\s*)(\S.*?)\s*\.{3,}\s*(\d+-\d+)\s*$")
TOC_BARE_NAME_RE = re.compile(r"^(\s*)(\*?[A-Za-z][A-Za-z0-9_()/ ]*?)\s*$")
ALIAS_LINE_RE = re.compile(r"^\s*\*(MAT_\d+[A-Z_]*)\s*:\s+\*([A-Z][A-Z0-9_]*)")
THEORY_TOC_ENTRY_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)\s+(?P<title>.+)$")


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
class SectionSpec:
    """One SectionMap candidate selected from the TOC.

    ``section_id`` is the normalized identifier used in SectionMap and
    issues. For Keyword entries it equals ``keyword_id``; for document
    sections it is a hierarchical path such as
    ``INTRODUCTION_MATERIAL_MODELS``.
    """

    section_id: str
    name: str
    manual_page: str
    indent: int
    kind: str  # "keyword" | "document" | "theory"
    parent_section_id: str | None = None
    section_number: str | None = None


@dataclass
class Section:
    section_id: str
    keyword_id: str | None
    name: str
    volume: int | None
    kind: str  # "keyword" | "document" | "theory"
    parent_section_id: str | None
    pdf_pages: list[int]
    manual_pages: list[str | None]
    document_id: str | None = None
    section_number: str | None = None


@dataclass
class InspectionIssue:
    volume: int | None
    pdf_page: int | None
    manual_page: str | None
    keyword_id: str | None
    severity: str
    code: str
    message: str
    document_id: str | None = None
    manual_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "manual_type": self.manual_type,
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
    volume: int | None
    document_id: str | None = None
    manual_type: str = MANUAL_TYPE_KEYWORD
    release: str | None = None
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


def _title_line_re(name: str, *, allow_family_token: bool = False) -> re.Pattern[str]:
    """Match a standalone entry title line or a variant-declaration line.

    The exact name always matches. Variant suffixes start with an
    OPTION token (`_OPTION`, `_OPTION_MODEL`, `_{OPTION2}`,
    `_OPTION1_{OPTION2}`); ordinary name extensions do not match
    (`*MAT_EXAMPLE_PLASTIC` is a different keyword, not a variant of
    `*MAT_EXAMPLE`). Real Manual titles also use plural OPTIONS,
    ellipsis-separated option lists (`_..._`) and, for nested family
    entries, one leading family token (`_WELDTYPE_{OPTION}`);
    ``allow_family_token`` enables that last form for indented TOC
    entries while top-level chapters keep the stricter OPTION-only form.
    """
    token = r"[A-Z][A-Z0-9]*"
    braced_option = r"\{OPTION[A-Z0-9]*\}"
    option = rf"(?:OPTION[A-Z0-9]*|{braced_option})"
    if allow_family_token:
        first = rf"_(?:{token}_)?{option}"
    else:
        first = rf"_{option}"
    rest = rf"(?:_(?:{token}|{braced_option}))*"
    ellipsis = rf"(?:_\.\.\.(?:_(?:{token}|{braced_option}))*)*"
    suffix = first + rest + ellipsis
    return re.compile(rf"^{re.escape(name)}(?:{suffix})?$")


def _normalize_title_line(line: str) -> str:
    # NFKD decomposes ligatures such as U+FB02 "ﬂ" to "fl"; Manual TOC
    # text and body text are not always consistent about ligatures.
    normalized = unicodedata.normalize("NFKD", line)
    return re.sub(r"\s+", " ", normalized.strip())


def _lines_have_title(
    lines: list[str], spec: SectionSpec, *, skip_first: bool = False
) -> bool:
    if skip_first:
        lines = lines[1:]
    if spec.kind == "keyword":
        pattern = _title_line_re(spec.name, allow_family_token=spec.indent > 0)
        return any(pattern.match(_normalize_title_line(line)) for line in lines)

    if spec.kind == "theory":
        normalized_lines = [_normalize_title_line(line) for line in lines]
        title = _normalize_title_line(spec.name)
        number = spec.section_number or spec.section_id

        def compact(value: str) -> str:
            return re.sub(r"-\s+", "-", value)

        for index, line in enumerate(normalized_lines):
            if not (line == number or line.startswith(f"{number} ")):
                continue
            for end in range(index + 1, min(index + 5, len(normalized_lines) + 1)):
                candidate = compact(" ".join(normalized_lines[index:end]))
                expected = f"{number} {title}"
                if candidate == expected or candidate.startswith(expected + " "):
                    return True
        return False

    # Document sections may be titled with their full TOC name or, for
    # appendix-style names, with the leading "APPENDIX X" token. A few
    # titles wrap across two layout lines; the leading-token check keeps
    # those valid without requiring exact wrapped-line reconstruction.
    prefix = _normalize_title_line(spec.name.split(":", 1)[0]) or _normalize_title_line(
        spec.name
    )
    pattern = re.compile(rf"^{re.escape(prefix)}(?:[\s.:]|$)", re.IGNORECASE)
    return any(pattern.match(_normalize_title_line(line)) for line in lines)


def _page_has_title(
    page_text: str, spec: SectionSpec, *, skip_first: bool = False
) -> bool:
    return _lines_have_title(_page_lines(page_text), spec, skip_first=skip_first)


def _page_has_title_in_top_lines(
    page_text: str, spec: SectionSpec, *, skip_first: bool = True
) -> bool:
    """Title evidence near the top of a page is a strong entry-start signal.

    Overview/family pages often mention child keywords in body lists far
    below the running header. Restricting the first pass to the first few
    non-empty lines after the running header avoids treating those
    mentions as entry starts while still accepting normal title lines;
    entries starting farther down a page are still recovered by the
    second, whole-page pass.
    """
    lines = _page_lines(page_text)
    window = lines[1:5] if skip_first else lines[:4]
    return _lines_have_title(window, spec)


def _find_title_page(
    pages: list[str],
    spec: SectionSpec,
    start_page: int,
    toc_pages: set[int],
) -> int | None:
    """Find a title-evidence page for ``spec`` starting at ``start_page``.

    The first non-empty line is treated as a running header and skipped:
    on continuation pages it often names the previous entry while the
    body title names the entry that starts on that page. First pass:
    only title lines near the top of a page (strong signal). Second pass:
    any non-header title-like line, for entries that genuinely start
    mid-page after a long preceding tail.
    """
    for use_strong_only in (True, False):
        for index in range(max(start_page, 1) - 1, len(pages)):
            pdf_page = index + 1
            if pdf_page in toc_pages:
                continue
            page_text = pages[index]
            if use_strong_only:
                if _page_has_title_in_top_lines(page_text, spec):
                    return pdf_page
            elif _page_has_title(page_text, spec, skip_first=True):
                return pdf_page
    return None


_VERSION_LIKE_RE = re.compile(r"^(?:\d{2,}|R\d|9\d)")


def _section_id(name: str) -> str:
    """Normalize a TOC name into an uppercase underscore-separated id.

    Non-ASCII symbols are dropped (e.g. ``LS-PrePost®`` becomes
    ``LS_PREPOST``); punctuation, slashes, spaces and colons become
    underscores.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").upper()
    return normalized or "UNTITLED"


def _select_section_specs(toc_index: list[TOCEntry]) -> list[SectionSpec]:
    """Select SectionMap candidates from the parsed TOC index.

    Keyword entries (``*`` names) are selected regardless of indent and
    deduplicated by first occurrence. Top-level non-keyword document
    sections are selected together with their nested TOC subsections;
    nested subsection ids are hierarchical paths such as
    ``INTRODUCTION_MATERIAL_MODELS``. Version-history leaves (``R7.0``,
    ``1989-1990``, ...) and TOC running headers stay out.
    """
    specs: list[SectionSpec] = []
    seen_keyword_ids: set[str] = set()
    seen_document_ids: set[str] = set()
    document_root: SectionSpec | None = None

    for entry in toc_index:
        name = entry.name
        if name.upper().startswith("TABLE OF CONTENTS"):
            continue

        if name.startswith("*"):
            document_root = None
            keyword_id = name[1:]
            if keyword_id not in seen_keyword_ids:
                seen_keyword_ids.add(keyword_id)
                specs.append(
                    SectionSpec(
                        section_id=keyword_id,
                        name=name,
                        manual_page=entry.manual_page,
                        indent=entry.indent,
                        kind="keyword",
                    )
                )
            continue

        if entry.indent == 0:
            document_root = None
            if _VERSION_LIKE_RE.match(name):
                continue
            root_id = _section_id(name)
            if root_id in seen_document_ids:
                continue
            seen_document_ids.add(root_id)
            document_root = SectionSpec(
                section_id=root_id,
                name=name,
                manual_page=entry.manual_page,
                indent=entry.indent,
                kind="document",
            )
            specs.append(document_root)
        elif document_root is not None and not _VERSION_LIKE_RE.match(name):
            child_id = f"{document_root.section_id}_{_section_id(name)}"
            if child_id in seen_document_ids:
                continue
            seen_document_ids.add(child_id)
            child = SectionSpec(
                section_id=child_id,
                name=name,
                manual_page=entry.manual_page,
                indent=entry.indent,
                kind="document",
                parent_section_id=document_root.section_id,
            )
            specs.append(child)

    return specs


def _select_theory_section_specs(toc_index: list[TOCEntry]) -> list[SectionSpec]:
    """Select numbered Theory chapters and preserve their hierarchy."""
    specs: list[SectionSpec] = []
    seen: set[str] = set()
    available_numbers: set[str] = set()

    for entry in toc_index:
        match = THEORY_TOC_ENTRY_RE.match(_normalize_title_line(entry.name))
        if match is None:
            continue
        number = match.group("number")
        title = match.group("title").strip()
        if number in seen:
            continue
        parent_number = number.rsplit(".", 1)[0] if "." in number else None
        parent_id = parent_number if parent_number in available_numbers else None
        specs.append(
            SectionSpec(
                section_id=number,
                section_number=number,
                name=title,
                manual_page=entry.manual_page,
                indent=entry.indent,
                kind="theory",
                parent_section_id=parent_id,
            )
        )
        seen.add(number)
        available_numbers.add(number)
    return specs


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
            # The final-line position and numeric bounds are the primary
            # safeguards. Keyword tags are uppercase while Theory tags use
            # title case, so tag casing must not be part of validity.
            if chapter > 200 or page_number > 3000 or not tag or len(tag) > 200:
                continue
            manual_page = f"{match.group(1)}-{match.group(2)}"
            footer_map[pdf_page] = (manual_page, tag)
            if tag.casefold() == "table of contents":
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
            # the running header on TOC pages is not an entry name; skipping
            # it prevents it from being merged into the first entry below
            if bare is not None and bare.group(2):
                if bare.group(2).strip().upper() == "TABLE OF CONTENTS":
                    continue
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
    specs: list[SectionSpec],
    toc_pages: set[int],
    footer_map: dict[int, tuple[str, str]],
    issues: list[InspectionIssue],
    volume: int | None,
) -> dict[str, int]:
    """Locate the start page of each SectionMap candidate.

    The printed footer map is the primary evidence: when the TOC page
    number maps back to a printed footer page, that page is checked for a
    matching body title line. If the title is missing there (the Manual
    TOC itself contains page-number errors in several releases), a title
    search runs forward and the stronger title evidence wins. In
    footer-less regions the body title line is searched monotonically;
    the first pass only accepts titles near the top of a page so overview
    pages that merely list child keywords are not mistaken for starts.
    """
    # Some older releases (notably R12) reuse the same printed page number
    # at several subchapter resets (multiple "12-1" pages). Keep all
    # candidates and select the first one at/after the monotonic search
    # cursor instead of letting a single-value reverse map hide earlier
    # legitimate pages.
    footer_candidates: dict[str, list[int]] = {}
    for pdf_page, (manual_page, _tag) in sorted(footer_map.items()):
        footer_candidates.setdefault(manual_page, []).append(pdf_page)

    starts: dict[str, int] = {}
    search_from = 1

    for spec in specs:
        candidates = footer_candidates.get(spec.manual_page, [])
        pos = bisect_left(candidates, search_from)
        found: int | None = None

        if pos < len(candidates):
            # The same printed page number may legitimately appear multiple
            # times. Prefer a candidate with a title near the top of the page;
            # that distinguishes the real entry page from overview pages that
            # list child keywords farther down in the body.
            candidate_pages = candidates[pos:]
            strong_candidate = next(
                (
                    candidate
                    for candidate in candidate_pages
                    if _page_has_title_in_top_lines(pages[candidate - 1], spec)
                ),
                None,
            )
            weak_candidate = next(
                (
                    candidate
                    for candidate in candidate_pages
                    if _page_has_title(pages[candidate - 1], spec)
                ),
                None,
            )

            if strong_candidate is not None:
                found = strong_candidate
            elif weak_candidate is not None:
                # A weak match (title farther down the page) is still valid
                # for entries that start mid-page; accepting it preserves
                # coverage. The strong-first selection above already handles
                # the common duplicate-page/overview-page ambiguity.
                found = weak_candidate
            else:
                fallback_from = max(candidates[pos], search_from)
                fallback = _find_title_page(
                    pages, spec, fallback_from, toc_pages
                )
                if fallback is not None:
                    issues.append(
                        InspectionIssue(
                            volume=volume,
                            pdf_page=fallback,
                            manual_page=spec.manual_page,
                            keyword_id=spec.section_id,
                            severity="warning",
                            code="ANCHOR_CONFLICT",
                            message=(
                                f"TOC reports {spec.manual_page} for {spec.name} "
                                f"but footer candidate pdf page {candidates[pos]} "
                                f"has no title; using title evidence at pdf page {fallback}"
                            ),
                        )
                    )
                    found = fallback
                else:
                    issues.append(
                        InspectionIssue(
                            volume=volume,
                            pdf_page=candidates[pos],
                            manual_page=spec.manual_page,
                            keyword_id=spec.section_id,
                            severity="warning",
                            code="TOC_PAGE_TITLE_NOT_FOUND",
                            message=(
                                f"TOC reports {spec.manual_page} for {spec.name} "
                                f"and footer maps to pdf page {candidates[pos]}, "
                                "but no title evidence was found there or forward"
                            ),
                        )
                    )
                    found = candidates[pos]
        else:
            found = _find_title_page(pages, spec, search_from, toc_pages)

        if found is None:
            issues.append(
                InspectionIssue(
                    volume=volume,
                    pdf_page=None,
                    manual_page=spec.manual_page,
                    keyword_id=spec.section_id,
                    severity="warning",
                    code="TOC_ENTRY_UNRESOLVED",
                    message=f"entry start page not located for {spec.name}",
                )
            )
            continue

        if found < search_from:
            issues.append(
                InspectionIssue(
                    volume=volume,
                    pdf_page=found,
                    manual_page=spec.manual_page,
                    keyword_id=spec.section_id,
                    severity="warning",
                    code="SECTION_BOUNDARY_UNCERTAIN",
                    message=f"non-monotonic start for {spec.name}",
                )
            )
        starts[spec.section_id] = found
        search_from = max(search_from, found)
    return starts


def _build_pagemap(
    page_count: int,
    footer_map: dict[int, tuple[str, str]],
    specs: list[SectionSpec],
    starts: dict[str, int],
    volume: int | None,
    issues: list[InspectionIssue],
) -> list[PageMapEntry]:
    """Combine footer evidence and TOC+title anchors, then interpolate
    locally between anchors whose arithmetic is consistent."""
    manual_of: dict[int, str] = {}
    evidence_of: dict[int, str] = {}

    for pdf_page, (manual_page, _tag) in footer_map.items():
        manual_of[pdf_page] = manual_page
        evidence_of[pdf_page] = "footer"

    for spec in specs:
        start = starts.get(spec.section_id)
        if start is None:
            continue
        if start in manual_of:
            if manual_of[start] != spec.manual_page:
                duplicate = any(
                    issue.code == "ANCHOR_CONFLICT"
                    and issue.keyword_id == spec.section_id
                    and issue.pdf_page == start
                    for issue in issues
                )
                if not duplicate:
                    issues.append(
                        InspectionIssue(
                            volume=volume,
                            pdf_page=start,
                            manual_page=spec.manual_page,
                            keyword_id=spec.section_id,
                            severity="warning",
                            code="ANCHOR_CONFLICT",
                            message=(
                                f"footer reports {manual_of[start]} but TOC reports "
                                f"{spec.manual_page} for {spec.name}"
                            ),
                        )
                    )
            continue  # footer evidence wins; already recorded
        manual_of[start] = spec.manual_page
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

    # Global monotonic validation is defensive, but some Manual releases
    # legitimately reset printed page numbers at a section boundary
    # (R12 has several "12-1" pages inside the CONTROL chapter). Resets
    # that coincide with a located section start are accepted; other
    # decreases still indicate conflicting evidence.
    section_start_pages = set(starts.values())
    known = sorted(manual_of)
    for prev, nxt in zip(known, known[1:]):
        if not (
            _chapter_of(manual_of[prev]) < _chapter_of(manual_of[nxt])
            or (
                _chapter_of(manual_of[prev]) == _chapter_of(manual_of[nxt])
                and _page_number_of(manual_of[prev]) < _page_number_of(manual_of[nxt])
            )
        ):
            if prev in section_start_pages or nxt in section_start_pages:
                # Page-number resets at/near a located section boundary are
                # common in older releases and are not conflicts.
                continue
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
    specs: list[SectionSpec],
    starts: dict[str, int],
    pagemap: list[PageMapEntry],
    content_end: int,
    volume: int | None,
    document_id: str | None = None,
) -> list[Section]:
    """Build SectionMap entries with candidate page ranges.

    Keyword entries are flat candidates: each ends where the next selected
    TOC entry starts, so adjacent keyword sections share a boundary page.
    Document sections keep their chapter/subsection shape: a document root
    spans until the next top-level TOC entry, while a document subsection
    spans until the next selected subsection or chapter boundary.
    """
    located = [spec for spec in specs if spec.section_id in starts]
    sections: list[Section] = []
    manual_by_pdf = {entry.pdf_page: entry.manual_page for entry in pagemap}

    for position, spec in enumerate(located):
        start = starts[spec.section_id]
        if spec.kind == "theory":
            current_depth = len((spec.section_number or spec.section_id).split("."))
            end = content_end
            for next_spec in located[position + 1 :]:
                if next_spec.kind != "theory":
                    end = starts[next_spec.section_id]
                    break
                next_depth = len(
                    (next_spec.section_number or next_spec.section_id).split(".")
                )
                if next_depth <= current_depth:
                    end = starts[next_spec.section_id]
                    break
        elif spec.kind == "keyword" or spec.parent_section_id is not None:
            # Flat boundary: the next selected TOC entry, except that a
            # document root is bounded by the next top-level entry (below).
            if position + 1 < len(located):
                end = starts[located[position + 1].section_id]
            else:
                end = content_end
        else:
            # Document root: include all of its subsections and stop at the
            # next top-level entry (keyword chapter or document chapter).
            end = content_end
            for next_spec in located[position + 1 :]:
                if next_spec.indent == 0:
                    end = starts[next_spec.section_id]
                    break
        end = max(end, start)
        pdf_pages = list(range(start, end + 1))
        sections.append(
            Section(
                section_id=spec.section_id,
                keyword_id=spec.section_id if spec.kind == "keyword" else None,
                name=spec.name,
                volume=volume,
                kind=spec.kind,
                parent_section_id=spec.parent_section_id,
                pdf_pages=pdf_pages,
                manual_pages=[manual_by_pdf.get(p) for p in pdf_pages],
                document_id=document_id,
                section_number=spec.section_number,
            )
        )
    return sections


def inspect_document(
    document: ManualDocument,
    extractor: TextExtractor,
) -> InspectionResult:
    """Run deterministic inspection using the profile for one document."""
    return _inspect(
        volume=document.volume,
        document_id=document.document_id,
        manual_type=document.manual_type,
        release=document.release,
        pdf_path=document.path,
        extractor=extractor,
    )


def _inspect(
    *,
    volume: int | None,
    document_id: str,
    manual_type: str,
    release: str | None,
    pdf_path: Path,
    extractor: TextExtractor,
) -> InspectionResult:
    pages = extractor.extract_pages(pdf_path)
    result = InspectionResult(
        volume=volume,
        document_id=document_id,
        manual_type=manual_type,
        release=release,
    )

    footer_map, toc_pages = _scan_footers(pages)
    result.toc_index = _parse_toc(pages, toc_pages)
    result.legacy_alias_map = (
        _scan_legacy_alias_map(pages)
        if manual_type == MANUAL_TYPE_KEYWORD
        else {}
    )

    if not result.toc_index:
        result.issues.append(
            InspectionIssue(
                volume=volume,
                pdf_page=None,
                manual_page=None,
                keyword_id=None,
                severity="error",
                code="TOC_EMPTY",
                message="no table-of-contents entries were parsed",
            )
        )

    section_specs = (
        _select_section_specs(result.toc_index)
        if manual_type == MANUAL_TYPE_KEYWORD
        else _select_theory_section_specs(result.toc_index)
    )
    if not section_specs:
        result.issues.append(
            InspectionIssue(
                volume=volume,
                pdf_page=None,
                manual_page=None,
                keyword_id=None,
                severity="error",
                code="SECTION_SPECS_EMPTY",
                message="no SectionMap candidates were selected from the TOC",
            )
        )

    starts = _locate_entry_starts(
        pages, section_specs, toc_pages, footer_map, result.issues, volume
    )
    result.pagemap = _build_pagemap(
        len(pages), footer_map, section_specs, starts, volume, result.issues
    )

    if manual_type == MANUAL_TYPE_KEYWORD:
        header_token_re = re.compile(r"^\*[A-Za-z]")
        content_end = 1
        for index, page_text in enumerate(pages):
            lines = _page_lines(page_text)
            if lines and header_token_re.match(lines[0]):
                content_end = index + 1
    else:
        content_end = max(footer_map, default=1)
    content_end = max(content_end, max(starts.values(), default=1))

    result.sections = _build_sections(
        section_specs,
        starts,
        result.pagemap,
        content_end,
        volume,
        document_id,
    )
    if not result.sections:
        result.issues.append(
            InspectionIssue(
                volume=volume,
                pdf_page=None,
                manual_page=None,
                keyword_id=None,
                severity="error",
                code="SECTIONMAP_EMPTY",
                message="no sections were located",
            )
        )

    for issue in result.issues:
        issue.document_id = document_id
        issue.manual_type = manual_type

    filled = [entry for entry in result.pagemap if entry.manual_page is not None]
    evidence_counts: dict[str, int] = {}
    for entry in filled:
        evidence_counts[entry.evidence or "unknown"] = (
            evidence_counts.get(entry.evidence or "unknown", 0) + 1
        )
    result.stats = {
        "pdf_pages": len(pages),
        "footer_pages": len(footer_map),
        "toc_entries_total": len(result.toc_index),
        "toc_keyword_entries": sum(1 for spec in section_specs if spec.kind == "keyword"),
        "toc_theory_entries": sum(1 for spec in section_specs if spec.kind == "theory"),
        "toc_document_entries": sum(1 for spec in section_specs if spec.kind == "document"),
        "sections_located": len(result.sections),
        "sections_keyword": sum(1 for section in result.sections if section.kind == "keyword"),
        "sections_theory": sum(1 for section in result.sections if section.kind == "theory"),
        "sections_document": sum(1 for section in result.sections if section.kind == "document"),
        "sections_unresolved": len(section_specs) - len(starts),
        "pagemap_filled": len(filled),
        "pagemap_none": len(pages) - len(filled),
        "pagemap_coverage": len(filled) / len(pages) if pages else 0.0,
        "evidence": evidence_counts,
        "legacy_aliases": len(result.legacy_alias_map),
        "issues_by_code": {
            code: sum(1 for issue in result.issues if issue.code == code)
            for code in sorted({issue.code for issue in result.issues})
        },
    }
    return result


def _document_metadata(result: InspectionResult) -> dict:
    return {
        "document_id": result.document_id,
        "manual_type": result.manual_type,
        "release": result.release,
        "volume": result.volume,
    }


def write_inspection_artifacts(
    results: list[InspectionResult], output_dir: Path
) -> Path:
    """Write versioned PageMap/SectionMap v0.1 intermediate artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        document_id = result.document_id or f"keyword-volume-{result.volume}"
        document_dir = output_dir / document_id
        document_dir.mkdir(parents=True, exist_ok=True)
        document = _document_metadata(result)

        (document_dir / "pagemap.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "document": document,
                    "pages": [entry.__dict__ for entry in result.pagemap],
                },
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (document_dir / "sectionmap.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "document": document,
                    "sections": [section.__dict__ for section in result.sections],
                },
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (document_dir / "toc_index.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "document": document,
                    "entries": [entry.__dict__ for entry in result.toc_index],
                },
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (document_dir / "legacy_alias_map.json").write_text(
            json.dumps(result.legacy_alias_map, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        (document_dir / "issues.jsonl").write_text(
            "".join(
                json.dumps(issue.to_dict(), ensure_ascii=False) + "\n"
                for issue in result.issues
            ),
            encoding="utf-8",
        )

    summary = {
        "schema_version": "0.1",
        "documents": {
            result.document_id or f"keyword-volume-{result.volume}": result.stats
            for result in results
        },
        "issues": [issue.to_dict() for result in results for issue in result.issues],
    }
    (output_dir / "inspection_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return output_dir
