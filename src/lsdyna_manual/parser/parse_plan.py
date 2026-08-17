"""Page-centric parse planning across Manual documents."""

from __future__ import annotations

from dataclasses import dataclass, field

from lsdyna_manual.parser.segmentation import PageMapEntry, Section


@dataclass(frozen=True)
class PagePlanEntry:
    document_id: str
    pdf_page: int
    manual_page: str | None
    candidate_sections: tuple[str, ...]
    volume: int | None = None


@dataclass(frozen=True)
class ParseBatch:
    batch_id: int
    document_id: str
    pdf_pages: tuple[int, ...]
    volume: int | None = None

    def layout_index_for_pdf_page(self, pdf_page: int) -> int:
        """Index of pdf_page in the provider result for this batch."""
        return self.pdf_pages.index(pdf_page)


@dataclass
class ParsePlan:
    entries: list[PagePlanEntry] = field(default_factory=list)
    batches: list[ParseBatch] = field(default_factory=list)

    def entry_for(self, document_id: str, pdf_page: int) -> PagePlanEntry | None:
        for entry in self.entries:
            if entry.document_id == document_id and entry.pdf_page == pdf_page:
                return entry
        return None

    def batches_for_document(self, document_id: str) -> list[ParseBatch]:
        return [
            batch for batch in self.batches if batch.document_id == document_id
        ]

    @property
    def page_count(self) -> int:
        return len(self.entries)


def _document_id_for_section(section: Section) -> str:
    if section.document_id is None:
        raise ValueError(f"section {section.section_id} has no document_id")
    return section.document_id


def _manual_page_map(
    pagemap_by_document: dict[str, list[PageMapEntry]],
) -> dict[tuple[str, int], str | None]:
    manual_by_page: dict[tuple[str, int], str | None] = {}
    for document_id, pagemap in pagemap_by_document.items():
        for entry in pagemap:
            manual_by_page[(document_id, entry.pdf_page)] = entry.manual_page
    return manual_by_page


def build_parse_plan(
    sections: list[Section],
    pagemap_by_document: dict[str, list[PageMapEntry]],
    *,
    start_page: int = 1,
    end_page: int | None = None,
    max_batch_pages: int = 1,
    batch_size: int | None = None,
) -> ParsePlan:
    """Build a deduplicated page plan from SectionMap candidates.

    Overlapping candidate ranges are expected. A page is included once per
    source document and records every candidate section that references it.
    """
    if batch_size is not None:
        if max_batch_pages != 1:
            raise ValueError("set max_batch_pages or legacy batch_size, not both")
        max_batch_pages = batch_size
    if max_batch_pages <= 0:
        raise ValueError("max_batch_pages must be positive")

    manual_by_page = _manual_page_map(pagemap_by_document)
    candidates: dict[tuple[str, int], set[str]] = {}
    section_starts: set[tuple[str, int]] = set()
    volume_by_document: dict[str, int | None] = {}

    for section in sections:
        document_id = _document_id_for_section(section)
        volume_by_document.setdefault(document_id, section.volume)
        if section.pdf_pages:
            section_start = section.pdf_pages[0]
            if section_start >= start_page and (
                end_page is None or section_start <= end_page
            ):
                section_starts.add((document_id, section_start))
        for pdf_page in section.pdf_pages:
            if pdf_page < start_page:
                continue
            if end_page is not None and pdf_page > end_page:
                continue
            candidates.setdefault((document_id, pdf_page), set()).add(
                section.section_id
            )

    entries: list[PagePlanEntry] = []
    for (document_id, pdf_page), section_ids in sorted(candidates.items()):
        entries.append(
            PagePlanEntry(
                document_id=document_id,
                volume=volume_by_document.get(document_id),
                pdf_page=pdf_page,
                manual_page=manual_by_page.get((document_id, pdf_page)),
                candidate_sections=tuple(sorted(section_ids)),
            )
        )

    batches: list[ParseBatch] = []
    current_document_id: str | None = None
    current_volume: int | None = None
    current_pages: list[int] = []
    current_batch_id = 1

    def flush_batch() -> None:
        nonlocal current_document_id, current_volume, current_pages, current_batch_id
        if current_document_id is not None and current_pages:
            batches.append(
                ParseBatch(
                    batch_id=current_batch_id,
                    document_id=current_document_id,
                    volume=current_volume,
                    pdf_pages=tuple(current_pages),
                )
            )
            current_batch_id += 1
        current_pages = []

    for entry in entries:
        should_split = False
        if current_document_id != entry.document_id:
            should_split = True
        elif current_pages and entry.pdf_page != current_pages[-1] + 1:
            should_split = True
        elif current_pages and (entry.document_id, entry.pdf_page) in section_starts:
            should_split = True
        elif len(current_pages) >= max_batch_pages:
            should_split = True

        if should_split:
            flush_batch()

        current_document_id = entry.document_id
        current_volume = entry.volume
        current_pages.append(entry.pdf_page)

    flush_batch()
    return ParsePlan(entries=entries, batches=batches)


def limit_parse_plan(
    plan: ParsePlan,
    *,
    document_id: str | None = None,
    max_pages: int | None = None,
    selected_pages: set[tuple[str, int]] | None = None,
) -> ParsePlan:
    """Select requested pages without changing planned batch identity."""
    if max_pages is not None and max_pages <= 0:
        raise ValueError("max_pages must be positive")
    entries = [
        entry
        for entry in plan.entries
        if document_id is None or entry.document_id == document_id
        if selected_pages is None
        or (entry.document_id, entry.pdf_page) in selected_pages
    ]
    if max_pages is not None:
        entries = entries[:max_pages]
    selected = {(entry.document_id, entry.pdf_page) for entry in entries}
    batches: list[ParseBatch] = []
    for batch in plan.batches:
        pages = tuple(
            page
            for page in batch.pdf_pages
            if (batch.document_id, page) in selected
        )
        if pages:
            batches.append(
                ParseBatch(
                    batch_id=batch.batch_id,
                    document_id=batch.document_id,
                    pdf_pages=pages,
                    volume=batch.volume,
                )
            )
    return ParsePlan(entries=entries, batches=batches)
