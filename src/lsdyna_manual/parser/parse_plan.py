"""Page-centric parse planning.

A parse plan is built from SectionMap candidates, deduplicated by the
semantic parsing identity ``(volume, pdf_page)``. Multi-page API batches
are transport-only; every page result must still map back to one unique
source PDF page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lsdyna_manual.parser.segmentation import PageMapEntry, Section


@dataclass(frozen=True)
class PagePlanEntry:
    volume: int
    pdf_page: int
    manual_page: str | None
    candidate_sections: tuple[str, ...]


@dataclass(frozen=True)
class ParseBatch:
    batch_id: int
    volume: int
    pdf_pages: tuple[int, ...]

    def layout_index_for_pdf_page(self, pdf_page: int) -> int:
        """Index of ``pdf_page`` in the provider result for this batch."""
        return self.pdf_pages.index(pdf_page)


@dataclass
class ParsePlan:
    entries: list[PagePlanEntry] = field(default_factory=list)
    batches: list[ParseBatch] = field(default_factory=list)

    def entry_for(self, volume: int, pdf_page: int) -> PagePlanEntry | None:
        for entry in self.entries:
            if entry.volume == volume and entry.pdf_page == pdf_page:
                return entry
        return None

    def batches_for_volume(self, volume: int) -> list[ParseBatch]:
        return [batch for batch in self.batches if batch.volume == volume]

    @property
    def page_count(self) -> int:
        return len(self.entries)


def _manual_page_map(
    pagemap_by_volume: dict[int, list[PageMapEntry]],
) -> dict[tuple[int, int], str | None]:
    manual_by_page: dict[tuple[int, int], str | None] = {}
    for volume, pagemap in pagemap_by_volume.items():
        for entry in pagemap:
            manual_by_page[(volume, entry.pdf_page)] = entry.manual_page
    return manual_by_page


def build_parse_plan(
    sections: list[Section],
    pagemap_by_volume: dict[int, list[PageMapEntry]],
    *,
    start_page: int = 1,
    end_page: int | None = None,
    batch_size: int = 5,
) -> ParsePlan:
    """Build a deduplicated page plan from SectionMap candidates.

    Overlapping candidate ranges are expected; a page is included once
    and records every candidate section that references it. Batch groups
    contain consecutive page numbers from the same volume. Non-candidate
    gaps and volume changes start a new batch.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    manual_by_page = _manual_page_map(pagemap_by_volume)
    candidates: dict[tuple[int, int], set[str]] = {}

    for section in sections:
        for pdf_page in section.pdf_pages:
            if pdf_page < start_page:
                continue
            if end_page is not None and pdf_page > end_page:
                continue
            candidates.setdefault((section.volume, pdf_page), set()).add(
                section.section_id
            )

    entries: list[PagePlanEntry] = []
    for (volume, pdf_page), section_ids in sorted(candidates.items()):
        entries.append(
            PagePlanEntry(
                volume=volume,
                pdf_page=pdf_page,
                manual_page=manual_by_page.get((volume, pdf_page)),
                candidate_sections=tuple(sorted(section_ids)),
            )
        )

    batches: list[ParseBatch] = []
    current_volume: int | None = None
    current_pages: list[int] = []
    current_batch_id = 1

    def flush_batch() -> None:
        nonlocal current_volume, current_pages, current_batch_id
        if current_volume is not None and current_pages:
            batches.append(
                ParseBatch(
                    batch_id=current_batch_id,
                    volume=current_volume,
                    pdf_pages=tuple(current_pages),
                )
            )
            current_batch_id += 1
        current_pages = []

    for entry in entries:
        should_split = False
        if current_volume != entry.volume:
            should_split = True
        elif current_pages and entry.pdf_page != current_pages[-1] + 1:
            should_split = True
        elif len(current_pages) >= batch_size:
            should_split = True

        if should_split:
            flush_batch()

        current_volume = entry.volume
        current_pages.append(entry.pdf_page)

    flush_batch()
    return ParsePlan(entries=entries, batches=batches)
