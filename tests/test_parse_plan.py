"""Unit tests for page-centric parse planning."""

from lsdyna_manual.parser.parse_plan import build_parse_plan
from lsdyna_manual.parser.segmentation import PageMapEntry, Section


def _section(section_id, volume, pages):
    return Section(
        section_id=section_id,
        keyword_id=section_id,
        name=f"*{section_id}",
        volume=volume,
        kind="keyword",
        parent_section_id=None,
        pdf_pages=pages,
        manual_pages=[None] * len(pages),
        document_id=f"keyword-volume-{volume}",
    )


def test_build_parse_plan_deduplicates_overlapping_candidates():
    sections = [
        _section("MAT_ELASTIC", 2, [197, 198, 199, 200]),
        _section("MAT_NEXT", 2, [200, 201]),
    ]
    pagemap = {
        "keyword-volume-2": [
            PageMapEntry(pdf_page=197, manual_page="2-131", evidence="footer"),
            PageMapEntry(pdf_page=198, manual_page="2-132", evidence="footer"),
            PageMapEntry(pdf_page=199, manual_page="2-133", evidence="footer"),
            PageMapEntry(pdf_page=200, manual_page="2-134", evidence="footer"),
            PageMapEntry(pdf_page=201, manual_page="2-135", evidence="footer"),
        ]
    }

    plan = build_parse_plan(sections, pagemap, batch_size=3)

    assert plan.page_count == 5
    by_page = {(entry.document_id, entry.pdf_page): entry for entry in plan.entries}
    assert by_page[("keyword-volume-2", 200)].candidate_sections == ("MAT_ELASTIC", "MAT_NEXT")
    assert len(plan.batches) == 2
    assert plan.batches[0].pdf_pages == (197, 198, 199)
    assert plan.batches[1].pdf_pages == (200, 201)


def test_build_parse_plan_splits_on_gaps_and_page_range():
    sections = [_section("A", 1, [2, 3, 10, 11])]
    pagemap = {
        "keyword-volume-1": [
            PageMapEntry(pdf_page=page, manual_page=None, evidence=None)
            for page in range(1, 12)
        ]
    }

    plan = build_parse_plan(sections, pagemap, start_page=2, end_page=11, batch_size=5)

    assert [entry.pdf_page for entry in plan.entries] == [2, 3, 10, 11]
    assert [batch.pdf_pages for batch in plan.batches] == [(2, 3), (10, 11)]
    assert plan.batches[1].layout_index_for_pdf_page(11) == 1


def test_build_parse_plan_splits_at_section_starts_without_duplicate_pages():
    sections = [
        _section("A", 1, [1, 2, 3, 4]),
        _section("B", 1, [4, 5, 6]),
    ]
    pagemap = {
        "keyword-volume-1": [
            PageMapEntry(pdf_page=page, manual_page=None, evidence=None)
            for page in range(1, 7)
        ]
    }

    plan = build_parse_plan(sections, pagemap, batch_size=5)

    assert [batch.pdf_pages for batch in plan.batches] == [(1, 2, 3), (4, 5, 6)]
    assert [entry.pdf_page for entry in plan.entries] == [1, 2, 3, 4, 5, 6]
    assert plan.entry_for("keyword-volume-1", 4).candidate_sections == ("A", "B")


def test_build_parse_plan_defaults_to_single_page_batches():
    sections = [_section("A", 1, [1, 2, 3])]
    pagemap = {
        "keyword-volume-1": [
            PageMapEntry(pdf_page=page, manual_page=None, evidence=None)
            for page in range(1, 4)
        ]
    }

    plan = build_parse_plan(sections, pagemap)

    assert [batch.pdf_pages for batch in plan.batches] == [(1,), (2,), (3,)]


def test_limit_parse_plan_selects_sample_pages():
    sections = [_section("A", 1, [1, 2, 3, 4, 5])]
    pagemap = {
        "keyword-volume-1": [
            PageMapEntry(pdf_page=page, manual_page=None, evidence=None)
            for page in range(1, 6)
        ]
    }
    plan = build_parse_plan(sections, pagemap)

    from lsdyna_manual.parser.parse_plan import limit_parse_plan

    selected = limit_parse_plan(
        plan,
        selected_pages={
            ("keyword-volume-1", 2),
            ("keyword-volume-1", 5),
        },
    )

    assert [entry.pdf_page for entry in selected.entries] == [2, 5]
    assert [batch.pdf_pages for batch in selected.batches] == [(2,), (5,)]
