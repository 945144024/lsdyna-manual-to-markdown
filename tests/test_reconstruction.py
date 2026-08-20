"""Tests for SectionIR assembly and conservative Markdown rendering."""

import json

import yaml
from pypdf import PdfWriter

from lsdyna_manual.parser.page_ir import (
    Cell,
    FigureBlock,
    FooterBlock,
    HeaderBlock,
    MathBlock,
    PageIR,
    ParseIssue,
    TableBlock,
    TextBlock,
    save_page_ir,
)
from lsdyna_manual.parser.segmentation import Section
from lsdyna_manual.pipeline import run_reconstruction
from lsdyna_manual.reconstruction.keyword_ir import (
    reconstruct_keywords,
    validate_keyword_ir,
)
from lsdyna_manual.reconstruction.section_ir import assemble_sections
from lsdyna_manual.reconstruction.theory_ir import reconstruct_theory
from lsdyna_manual.markdown.renderer import render_sections, render_theory


def _section(pages=(1, 2)):
    return Section(
        section_id="MAT_EXAMPLE",
        keyword_id="MAT_EXAMPLE",
        name="*MAT_EXAMPLE",
        volume=2,
        kind="keyword",
        parent_section_id=None,
        pdf_pages=list(pages),
        manual_pages=["2-1", "2-2"][: len(pages)],
        document_id="keyword-volume-2",
        section_number=None,
    )


def _row(row_index, *values):
    return [
        Cell(text=value, row=row_index, column=column)
        for column, value in enumerate(values)
    ]


def test_assemble_sections_preserves_source_range_and_missing_pages():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="body")],
    )
    result = assemble_sections([_section()], {("keyword-volume-2", 1): page})[0]

    assert [item.pdf_page for item in result.source_pages] == [1, 2]
    assert [item.pdf_page for item in result.pages] == [1]
    assert result.status == "warning"
    missing = next(
        issue for issue in result.issues if issue.code == "SECTION_PAGEIR_MISSING"
    )
    assert (missing.pdf_page, missing.manual_page) == (2, "2-2")


def test_assemble_sections_fills_pageir_issue_provenance_without_mutating_page():
    page_issue = ParseIssue(
        severity="warning",
        code="TABLE_STRUCTURE_UNCERTAIN",
        message="inspect table",
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page=None,
        blocks=[TextBlock(text="body")],
        issues=[page_issue],
    )

    result = assemble_sections(
        [_section((1,))], {("keyword-volume-2", 1): page}
    )[0]
    reconstructed_issue = next(
        issue
        for issue in result.issues
        if issue.code == "TABLE_STRUCTURE_UNCERTAIN"
    )

    assert (reconstructed_issue.pdf_page, reconstructed_issue.manual_page) == (
        1,
        "2-1",
    )
    assert page_issue.pdf_page is None
    assert page_issue.manual_page is None


def test_source_blank_page_does_not_create_empty_content_warning():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[],
        issues=[
            ParseIssue(
                severity="info",
                code="SOURCE_BLANK_PAGE",
                message="blank",
            )
        ],
    )
    result = assemble_sections(
        [_section((1,))], {("keyword-volume-2", 1): page}
    )[0]

    assert result.status == "success"
    assert not any(issue.code == "SECTION_CONTENT_EMPTY" for issue in result.issues)


def test_assemble_sections_marks_shared_boundary_pages():
    first = _section((1,))
    second = Section(
        section_id="MAT_SECOND",
        keyword_id="MAT_SECOND",
        name="*MAT_SECOND",
        volume=2,
        kind="keyword",
        parent_section_id=None,
        pdf_pages=[1],
        manual_pages=["2-1"],
        document_id="keyword-volume-2",
        section_number=None,
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="shared")],
    )

    results = assemble_sections(
        [first, second],
        {("keyword-volume-2", 1): page},
    )

    assert all(result.status == "warning" for result in results)
    assert all(
        any(issue.code == "SECTION_SHARED_BOUNDARY_PAGE" for issue in result.issues)
        for result in results
    )
    assert all(
        [
            (issue.pdf_page, issue.manual_page)
            for issue in result.issues
            if issue.code == "SECTION_SHARED_BOUNDARY_PAGE"
        ]
        == [(1, "2-1")]
        for result in results
    )


def test_assemble_sections_treats_theory_parent_overlap_as_informational():
    parent = Section(
        section_id="35",
        keyword_id=None,
        name="Parent",
        volume=None,
        kind="theory",
        parent_section_id=None,
        pdf_pages=[1],
        manual_pages=["35-1"],
        document_id="theory",
        section_number="35",
    )
    child = Section(
        section_id="35.1",
        keyword_id=None,
        name="Child",
        volume=None,
        kind="theory",
        parent_section_id="35",
        pdf_pages=[1],
        manual_pages=["35-1"],
        document_id="theory",
        section_number="35.1",
    )
    page = PageIR(
        document_id="theory",
        pdf_page=1,
        manual_page="35-1",
        blocks=[TextBlock(text="35 Parent"), TextBlock(text="35.1 Child")],
    )

    results = assemble_sections([parent, child], {("theory", 1): page})

    assert all(result.status == "success" for result in results)
    assert all(
        any(
            issue.code == "THEORY_HIERARCHICAL_PAGE_OVERLAP"
            and issue.severity == "info"
            for issue in result.issues
        )
        for result in results
    )


def test_theory_ir_assigns_parent_and_siblings_by_title_anchors():
    sections = [
        Section("35", None, "Parent", None, "theory", None, [1, 2], ["35-1", "35-2"], "theory", "35"),
        Section("35.1", None, "First", None, "theory", "35", [1, 2], ["35-1", "35-2"], "theory", "35.1"),
        Section("35.2", None, "Second", None, "theory", "35", [2], ["35-2"], "theory", "35.2"),
    ]
    pages = {
        ("theory", 1): PageIR(
            document_id="theory",
            pdf_page=1,
            manual_page="35-1",
            blocks=[
                HeaderBlock(text="Theory Manual"),
                TextBlock(text="35"),
                TextBlock(text="Parent"),
                TextBlock(text="Parent introduction"),
                TextBlock(text="35.1 First"),
                TextBlock(text="First body"),
                FooterBlock(text="35-1"),
            ],
        ),
        ("theory", 2): PageIR(
            document_id="theory",
            pdf_page=2,
            manual_page="35-2",
            blocks=[
                TextBlock(text="First continuation"),
                TextBlock(text="35.2 Second"),
                TextBlock(text="Second body"),
            ],
        ),
    }

    theories = reconstruct_theory(assemble_sections(sections, pages))
    by_id = {theory.section_id: theory for theory in theories}

    assert [block.block.text for block in by_id["35"].content_blocks] == [
        "35",
        "Parent",
        "Parent introduction",
    ]
    assert [block.block.text for block in by_id["35.1"].content_blocks] == [
        "35.1 First",
        "First body",
        "First continuation",
    ]
    assert [block.block.text for block in by_id["35.2"].content_blocks] == [
        "35.2 Second",
        "Second body",
    ]
    owned = [source for theory in theories for source in theory.owned_sources]
    assert len(owned) == len(set(owned))
    assert all(theory.status == "success" for theory in theories)
    assert all(
        not any(issue.code == "SECTION_SHARED_BOUNDARY_PAGE" for issue in theory.issues)
        for theory in theories
    )
    parent_boundary = next(
        issue
        for issue in by_id["35"].issues
        if issue.code == "THEORY_BOUNDARY_RESOLVED"
    )
    assert (parent_boundary.pdf_page, parent_boundary.manual_page) == (1, "35-1")


def test_theory_title_anchor_accepts_presentation_only_variants():
    cases = [
        ("6.4", "Shell ±29", r"6.4 Shell $\pm29$"),
        ("11.1", "C0 formulation", r"11.1 C $ ^{0} $ formulation"),
        ("23.36", "Fung's model", "23.36 Fung’s model"),
        ("23.83", "Rigid body mechanics", "23.83 Rigid_body mechanics"),
    ]

    for page_number, (number, title, anchor) in enumerate(cases, start=1):
        section = Section(
            section_id=number,
            keyword_id=None,
            name=title,
            volume=None,
            kind="theory",
            parent_section_id=None,
            pdf_pages=[page_number],
            manual_pages=[f"{number}-1"],
            document_id="theory",
            section_number=number,
        )
        page = PageIR(
            document_id="theory",
            pdf_page=page_number,
            manual_page=f"{number}-1",
            blocks=[TextBlock(text=anchor), TextBlock(text="Body")],
        )

        theory = reconstruct_theory(
            assemble_sections([section], {("theory", page_number): page})
        )[0]

        assert theory.status == "success"
        assert [block.block.text for block in theory.content_blocks] == [
            anchor,
            "Body",
        ]
        assert not any(
            issue.code == "THEORY_TITLE_ANCHOR_MISSING"
            for issue in theory.issues
        )


def test_theory_title_anchor_prefers_exact_over_longer_prefix():
    section = Section(
        section_id="23.120",
        keyword_id=None,
        name="Exact title",
        volume=None,
        kind="theory",
        parent_section_id=None,
        pdf_pages=[1],
        manual_pages=["23-120"],
        document_id="theory",
        section_number="23.120",
    )
    page = PageIR(
        document_id="theory",
        pdf_page=1,
        manual_page="23-120",
        blocks=[
            TextBlock(text="23.120 Exact title extra suffix"),
            TextBlock(text="Prefix-owned text"),
            TextBlock(text="23.120 Exact title"),
            TextBlock(text="Exact body"),
        ],
    )

    theory = reconstruct_theory(
        assemble_sections([section], {("theory", 1): page})
    )[0]

    assert [block.block.text for block in theory.content_blocks] == [
        "23.120 Exact title",
        "Exact body",
    ]
    assert theory.status == "success"


def test_theory_title_anchor_accepts_header_number_followed_by_text_title():
    section = Section(
        section_id="12",
        keyword_id=None,
        name="Special theory",
        volume=None,
        kind="theory",
        parent_section_id=None,
        pdf_pages=[1],
        manual_pages=["12-1"],
        document_id="theory",
        section_number="12",
    )
    page = PageIR(
        document_id="theory",
        pdf_page=1,
        manual_page="12-1",
        blocks=[
            HeaderBlock(text="12"),
            TextBlock(text="Special_theory"),
            TextBlock(text="Body"),
        ],
    )

    theory = reconstruct_theory(
        assemble_sections([section], {("theory", 1): page})
    )[0]

    assert [block.block.text for block in theory.content_blocks] == [
        "Special_theory",
        "Body",
    ]
    assert theory.status == "success"


def test_theory_root_title_without_number_keeps_missing_anchor_warning():
    section = Section(
        section_id="45",
        keyword_id=None,
        name="Linear shells",
        volume=None,
        kind="theory",
        parent_section_id=None,
        pdf_pages=[1],
        manual_pages=["45-1"],
        document_id="theory",
        section_number="45",
    )
    page = PageIR(
        document_id="theory",
        pdf_page=1,
        manual_page="45-1",
        blocks=[TextBlock(text="Linear shells"), TextBlock(text="Body")],
    )

    theory = reconstruct_theory(
        assemble_sections([section], {("theory", 1): page})
    )[0]

    assert theory.status == "warning"
    assert any(
        issue.code == "THEORY_TITLE_ANCHOR_MISSING"
        for issue in theory.issues
    )
    assert [block.block.text for block in theory.content_blocks] == [
        "Linear shells",
        "Body",
    ]


def test_assemble_sections_attaches_legacy_ids():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="body")],
    )

    result = assemble_sections(
        [_section((1,))],
        {("keyword-volume-2", 1): page},
        legacy_ids_by_section={
            ("keyword-volume-2", "MAT_EXAMPLE"): ["MAT_001"]
        },
    )[0]

    assert result.legacy_ids == ["MAT_001"]


def test_keyword_ir_splits_shared_page_at_strong_title_anchor(tmp_path):
    first = Section(
        section_id="EOS_A",
        keyword_id="EOS_A",
        name="*EOS_A",
        volume=2,
        kind="keyword",
        parent_section_id=None,
        pdf_pages=[1, 2],
        manual_pages=["1-1", "1-2"],
        document_id="keyword-volume-2",
        section_number=None,
    )
    second = Section(
        section_id="EOS_B",
        keyword_id="EOS_B",
        name="*EOS_B",
        volume=2,
        kind="keyword",
        parent_section_id=None,
        pdf_pages=[2, 3],
        manual_pages=["1-2", "1-3"],
        document_id="keyword-volume-2",
        section_number=None,
    )
    page_irs = {
        ("keyword-volume-2", 1): PageIR(
            document_id="keyword-volume-2",
            pdf_page=1,
            manual_page="1-1",
            blocks=[TextBlock(text="*EOS_A"), TextBlock(text="A body")],
        ),
        ("keyword-volume-2", 2): PageIR(
            document_id="keyword-volume-2",
            pdf_page=2,
            manual_page="1-2",
            blocks=[
                HeaderBlock(text="*EOS"),
                TextBlock(text="A continuation"),
                TextBlock(text="*EOS_B"),
                TextBlock(text="B body"),
                FooterBlock(text="1-2 (EOS)"),
            ],
        ),
        ("keyword-volume-2", 3): PageIR(
            document_id="keyword-volume-2",
            pdf_page=3,
            manual_page="1-3",
            blocks=[TextBlock(text="B continuation")],
        ),
    }

    section_irs = assemble_sections([first, second], page_irs)
    keywords = reconstruct_keywords(section_irs)
    first_refs = {
        (block.source.pdf_page, block.source.block_index)
        for block in keywords[0].accounted_blocks()
    }
    second_refs = {
        (block.source.pdf_page, block.source.block_index)
        for block in keywords[1].accounted_blocks()
    }

    assert first_refs == {(1, 0), (1, 1), (2, 0), (2, 1)}
    assert second_refs == {(2, 2), (2, 3), (2, 4), (3, 0)}
    assert first_refs.isdisjoint(second_refs)
    assert keywords[0].status == "success"
    assert keywords[1].status == "success"
    assert all(
        any(issue.code == "KEYWORD_BOUNDARY_RESOLVED" for issue in keyword.issues)
        for keyword in keywords
    )
    assert all(
        [
            (issue.pdf_page, issue.manual_page)
            for issue in keyword.issues
            if issue.code == "KEYWORD_BOUNDARY_RESOLVED"
        ]
        == [(2, "1-2")]
        for keyword in keywords
    )
    assert not any(
        issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
        for keyword in keywords
        for issue in keyword.issues
    )

    rendered = render_sections(section_irs, corpus_root=tmp_path, release="R17")
    first_markdown = rendered[0].markdown_path.read_text(encoding="utf-8")
    second_markdown = rendered[1].markdown_path.read_text(encoding="utf-8")
    assert "A continuation" in first_markdown
    assert "*EOS_B" not in first_markdown
    assert "B body" not in first_markdown
    assert "*EOS_B" in second_markdown
    assert "B body" in second_markdown


def test_keyword_boundary_accepts_presentation_variants_and_option_placeholders():
    anchors = [
        " $ ^{*} $ EOS B-C",
        r"$\mathrm{*EOS\_B\_C}$",
        "*EOS_B_C_{OPTION}",
        "*EOS B C {OPTIONS}",
    ]

    for anchor in anchors:
        first = Section(
            section_id="EOS_A",
            keyword_id="EOS_A",
            name="*EOS_A",
            volume=2,
            kind="keyword",
            parent_section_id=None,
            pdf_pages=[1, 2],
            manual_pages=["2-1", "2-2"],
            document_id="keyword-volume-2",
            section_number=None,
        )
        second = Section(
            section_id="EOS_B_C",
            keyword_id="EOS_B_C",
            name="*EOS_B_C",
            volume=2,
            kind="keyword",
            parent_section_id=None,
            pdf_pages=[2, 3],
            manual_pages=["2-2", "2-3"],
            document_id="keyword-volume-2",
            section_number=None,
        )
        pages = {
            ("keyword-volume-2", 1): PageIR(
                document_id="keyword-volume-2",
                pdf_page=1,
                manual_page="2-1",
                blocks=[TextBlock(text="A body")],
            ),
            ("keyword-volume-2", 2): PageIR(
                document_id="keyword-volume-2",
                pdf_page=2,
                manual_page="2-2",
                blocks=[
                    TextBlock(text="A continuation"),
                    HeaderBlock(text="*EOS_B_C"),
                    TextBlock(text=anchor),
                    TextBlock(text="B body"),
                ],
            ),
            ("keyword-volume-2", 3): PageIR(
                document_id="keyword-volume-2",
                pdf_page=3,
                manual_page="2-3",
                blocks=[TextBlock(text="B continuation")],
            ),
        }

        keywords = reconstruct_keywords(
            assemble_sections([first, second], pages)
        )

        assert all(
            any(
                issue.code == "KEYWORD_BOUNDARY_RESOLVED"
                for issue in keyword.issues
            )
            for keyword in keywords
        )
        assert [
            block.block.text
            for block in keywords[1].accounted_blocks()
            if block.source.pdf_page == 2
        ] == [anchor, "B body"]


def test_keyword_boundary_prefers_exact_title_over_option_placeholder():
    first = Section(
        "EOS_A",
        "EOS_A",
        "*EOS_A",
        2,
        "keyword",
        None,
        [1, 2],
        ["2-1", "2-2"],
        "keyword-volume-2",
        None,
    )
    second = Section(
        "EOS_B",
        "EOS_B",
        "*EOS_B",
        2,
        "keyword",
        None,
        [2, 3],
        ["2-2", "2-3"],
        "keyword-volume-2",
        None,
    )
    pages = {
        ("keyword-volume-2", 1): PageIR(
            document_id="keyword-volume-2",
            pdf_page=1,
            manual_page="2-1",
            blocks=[TextBlock(text="A body")],
        ),
        ("keyword-volume-2", 2): PageIR(
            document_id="keyword-volume-2",
            pdf_page=2,
            manual_page="2-2",
            blocks=[
                TextBlock(text="A continuation"),
                TextBlock(text="*EOS_B_{OPTION}"),
                TextBlock(text="Still A-side source"),
                TextBlock(text="*EOS_B"),
                TextBlock(text="B body"),
            ],
        ),
        ("keyword-volume-2", 3): PageIR(
            document_id="keyword-volume-2",
            pdf_page=3,
            manual_page="2-3",
            blocks=[TextBlock(text="B continuation")],
        ),
    }

    first_keyword, second_keyword = reconstruct_keywords(
        assemble_sections([first, second], pages)
    )

    assert [
        block.block.text
        for block in first_keyword.accounted_blocks()
        if block.source.pdf_page == 2
    ] == ["A continuation", "*EOS_B_{OPTION}", "Still A-side source"]
    assert [
        block.block.text
        for block in second_keyword.accounted_blocks()
        if block.source.pdf_page == 2
    ] == ["*EOS_B", "B body"]


def test_keyword_boundary_rejects_fuzzy_mentions_and_noncontiguous_duplicates():
    candidates = [
        [TextBlock(text="A continuation"), TextBlock(text="*EOS_C")],
        [TextBlock(text="A continuation"), TextBlock(text="*EOS/B_C")],
        [
            TextBlock(text="A continuation"),
            TextBlock(text="See *EOS_B for details."),
        ],
        [
            TextBlock(text="A continuation"),
            TextBlock(text="*EOS_B"),
            TextBlock(text="unrelated source between repeated titles"),
            TextBlock(text="*EOS_B"),
            TextBlock(text="B body"),
        ],
    ]

    for shared_blocks in candidates:
        first = Section(
            "EOS_A",
            "EOS_A",
            "*EOS_A",
            2,
            "keyword",
            None,
            [1, 2],
            ["2-1", "2-2"],
            "keyword-volume-2",
            None,
        )
        second = Section(
            "EOS_B",
            "EOS_B",
            "*EOS_B",
            2,
            "keyword",
            None,
            [2, 3],
            ["2-2", "2-3"],
            "keyword-volume-2",
            None,
        )
        pages = {
                ("keyword-volume-2", 1): PageIR(
                    document_id="keyword-volume-2",
                    pdf_page=1,
                    manual_page="2-1",
                    blocks=[TextBlock(text="A body")],
                ),
                ("keyword-volume-2", 2): PageIR(
                    document_id="keyword-volume-2",
                    pdf_page=2,
                    manual_page="2-2",
                    blocks=shared_blocks,
                ),
                ("keyword-volume-2", 3): PageIR(
                    document_id="keyword-volume-2",
                    pdf_page=3,
                    manual_page="2-3",
                    blocks=[TextBlock(text="B body")],
                ),
        }

        keywords = reconstruct_keywords(
            assemble_sections([first, second], pages)
        )

        assert all(
            any(
                issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
                for issue in keyword.issues
            )
            for keyword in keywords
        )
        assert not any(
            issue.code == "KEYWORD_BOUNDARY_RESOLVED"
            for keyword in keywords
            for issue in keyword.issues
        )


def test_keyword_boundary_requires_header_for_missing_star_or_separator():
    for anchor in ["EOS_B_C", "*EOS B C", "*EOS-B-C"]:
        first = Section(
            "EOS_A",
            "EOS_A",
            "*EOS_A",
            2,
            "keyword",
            None,
            [1, 2],
            ["2-1", "2-2"],
            "keyword-volume-2",
            None,
        )
        second = Section(
            "EOS_B_C",
            "EOS_B_C",
            "*EOS_B_C",
            2,
            "keyword",
            None,
            [2, 3],
            ["2-2", "2-3"],
            "keyword-volume-2",
            None,
        )
        pages = {
            ("keyword-volume-2", 1): PageIR(
                document_id="keyword-volume-2",
                pdf_page=1,
                manual_page="2-1",
                blocks=[TextBlock(text="A body")],
            ),
            ("keyword-volume-2", 2): PageIR(
                document_id="keyword-volume-2",
                pdf_page=2,
                manual_page="2-2",
                blocks=[TextBlock(text="A continuation"), TextBlock(text=anchor)],
            ),
            ("keyword-volume-2", 3): PageIR(
                document_id="keyword-volume-2",
                pdf_page=3,
                manual_page="2-3",
                blocks=[TextBlock(text="B continuation")],
            ),
        }
        keywords = reconstruct_keywords(assemble_sections([first, second], pages))
        assert all(
            any(issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS" for issue in keyword.issues)
            for keyword in keywords
        )


def test_keyword_boundary_rejects_ordered_multi_owner_title_only_slice():
    sections = [
        Section(
            "EOS_A",
            "EOS_A",
            "*EOS_A",
            2,
            "keyword",
            None,
            [1, 2],
            ["2-1", "2-2"],
            "keyword-volume-2",
            None,
        ),
        Section(
            "EOS_B",
            "EOS_B",
            "*EOS_B",
            2,
            "keyword",
            None,
            [2],
            ["2-2"],
            "keyword-volume-2",
            None,
        ),
        Section(
            "EOS_C",
            "EOS_C",
            "*EOS_C",
            2,
            "keyword",
            None,
            [2, 3],
            ["2-2", "2-3"],
            "keyword-volume-2",
            None,
        ),
    ]
    pages = {
        ("keyword-volume-2", 1): PageIR(
            document_id="keyword-volume-2",
            pdf_page=1,
            manual_page="2-1",
            blocks=[TextBlock(text="A body")],
        ),
        ("keyword-volume-2", 2): PageIR(
            document_id="keyword-volume-2",
            pdf_page=2,
            manual_page="2-2",
            blocks=[
                TextBlock(text="A continuation"),
                TextBlock(text="*EOS_B"),
                TextBlock(text="*EOS_C"),
                TextBlock(text="Shared B/C body"),
            ],
        ),
        ("keyword-volume-2", 3): PageIR(
            document_id="keyword-volume-2",
            pdf_page=3,
            manual_page="2-3",
            blocks=[TextBlock(text="C continuation")],
        ),
    }

    keywords = reconstruct_keywords(assemble_sections(sections, pages))

    assert all(
        any(
            issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
            for issue in keyword.issues
        )
        for keyword in keywords
    )
    assert not any(
        issue.code == "KEYWORD_BOUNDARY_RESOLVED"
        for keyword in keywords
        for issue in keyword.issues
    )


def test_keyword_ir_preserves_ambiguous_shared_page_with_warning():
    first = _section((1, 2))
    second = Section(
        section_id="MAT_SECOND",
        keyword_id="MAT_SECOND",
        name="*MAT_SECOND",
        volume=2,
        kind="keyword",
        parent_section_id=None,
        pdf_pages=[2, 3],
        manual_pages=["2-2", "2-3"],
        document_id="keyword-volume-2",
        section_number=None,
    )
    page_irs = {
        ("keyword-volume-2", page): PageIR(
            document_id="keyword-volume-2",
            pdf_page=page,
            manual_page=f"2-{page}",
            blocks=[TextBlock(text=f"body {page}")],
        )
        for page in (1, 2, 3)
    }

    keywords = reconstruct_keywords(
        assemble_sections([first, second], page_irs)
    )

    assert all(keyword.status == "warning" for keyword in keywords)
    assert all(
        any(issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS" for issue in keyword.issues)
        for keyword in keywords
    )
    assert all(
        len(keyword.unclassified_blocks) == 2 for keyword in keywords
    )
    assert all(
        [
            (issue.pdf_page, issue.manual_page)
            for issue in keyword.issues
            if issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
        ]
        == [(2, "2-2")]
        for keyword in keywords
    )


def test_keyword_ir_validation_detects_duplicate_assignment():
    section = assemble_sections(
        [_section((1,))],
        {
            ("keyword-volume-2", 1): PageIR(
                document_id="keyword-volume-2",
                pdf_page=1,
                manual_page="2-1",
                blocks=[TextBlock(text="body")],
            )
        },
    )
    keyword = reconstruct_keywords(section)[0]
    keyword.unclassified_blocks.append(keyword.unclassified_blocks[0])

    issues = validate_keyword_ir(keyword)

    assert any(issue.code == "KEYWORD_BLOCK_ASSIGNED_MULTIPLE_TIMES" for issue in issues)


def test_keyword_ir_classifies_strong_semantic_anchors(tmp_path):
    section = Section(
        section_id="EOS_JWL",
        keyword_id="EOS_JWL",
        name="*EOS_JWL",
        volume=2,
        kind="keyword",
        parent_section_id=None,
        pdf_pages=[1],
        manual_pages=["1-1"],
        document_id="keyword-volume-2",
        section_number=None,
    )
    summary_table = TableBlock(
        rows=[
            [Cell(text="EOSID", row=0, column=0), Cell(text="A", row=0, column=1)]
        ]
    )
    card_table = TableBlock(
        rows=[
            [Cell(text="Card 1", row=0, column=0), Cell(text="1", row=0, column=1)],
            [Cell(text="Variable", row=1, column=0), Cell(text="EOSID", row=1, column=1)],
        ]
    )
    variable_table = TableBlock(
        rows=[
            [
                Cell(text="VARIABLE", row=0, column=0),
                Cell(text="DESCRIPTION", row=0, column=1),
            ],
            [Cell(text="EOSID", row=1, column=0), Cell(text="Identifier", row=1, column=1)],
        ]
    )
    blocks = [
        TextBlock(text="*EOS_JWL_{OPTION}"),
        TextBlock(text="This is Equation of State Form 2."),
        TextBlock(text="Purpose: Define the synthetic EOS."),
        TextBlock(text="Available options are:"),
        TextBlock(text="<BLANK>"),
        TextBlock(text="AFTERBURN"),
        TextBlock(text="Card Summary:"),
        TextBlock(text="Card 1. This card is required."),
        summary_table,
        TextBlock(text="Data Card Definitions:"),
        card_table,
        variable_table,
        TextBlock(text="EOSID"),
        TextBlock(text="Identifier description."),
        TextBlock(text="Remarks:"),
        TextBlock(text="A source remark."),
        TextBlock(text="References:"),
        TextBlock(text="A source reference."),
    ]
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="1-1",
        blocks=blocks,
    )

    keyword = reconstruct_keywords(
        assemble_sections([section], {("keyword-volume-2", 1): page})
    )[0]

    assert len(keyword.title_blocks) == 1
    assert [block.block.text for block in keyword.purpose_blocks] == [
        "Purpose: Define the synthetic EOS."
    ]
    assert keyword.option_names == ["AFTERBURN"]
    assert keyword.options[0].full_name == "*EOS_JWL_AFTERBURN"
    assert len(keyword.option_intro_blocks) == 2
    assert len(keyword.card_intro_blocks) == 2
    assert [card.label for card in keyword.cards] == ["Card 1"]
    assert len(keyword.cards[0].condition_blocks) == 1
    assert len(keyword.cards[0].tables) == 2
    assert [table.role for table in keyword.cards[0].tables] == [
        "summary",
        "definition",
    ]
    assert len(keyword.card_table_blocks) == 2
    assert len(keyword.cards[0].fields) == 2
    assert keyword.cards[0].fields[0].variable == "EOSID"
    assert keyword.cards[0].fields[1].variable == "A"
    assert keyword.cards[0].fields[0].field_type is None
    assert keyword.cards[0].fields[0].default is None
    assert keyword.variable_catalog == ["EOSID", "A"]
    assert len(keyword.variable_description_blocks) == 1
    assert [description.variable for description in keyword.variable_descriptions] == [
        "EOSID"
    ]
    assert len(keyword.variable_descriptions[0].tables) == 1
    assert [block.block.text for block in keyword.variable_descriptions[0].blocks] == [
        "EOSID",
        "Identifier description.",
    ]
    assert len(keyword.remarks_blocks) == 2
    assert len(keyword.references_blocks) == 2
    assert [block.block.text for block in keyword.description_blocks] == [
        "This is Equation of State Form 2."
    ]
    assert not keyword.unclassified_blocks
    assert len(keyword.accounted_blocks()) == len(blocks)
    assert not validate_keyword_ir(keyword)

    rendered = render_sections(
        assemble_sections([section], {("keyword-volume-2", 1): page}),
        corpus_root=tmp_path,
        release="R17",
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")
    assert markdown.splitlines().count("# *EOS_JWL") == 1
    assert "- AFTERBURN" in markdown
    assert "## Card Definitions" in markdown
    assert "### Card 1" in markdown
    assert "## Variable Descriptions" in markdown
    assert "### EOSID" in markdown
    assert "| Variable | Description |" in markdown
    assert markdown.count("Identifier description.") == 1
    assert "## Description" in markdown
    assert "## Source Material" not in markdown
    assert "This is Equation of State Form 2." in markdown


def test_card_definition_splits_multiple_cards_without_duplicate_accounting():
    table = TableBlock(
        rows=[
            _row(0, "Card 1", "1", "2"),
            _row(1, "Variable", "A", "B"),
            _row(2, "Type", "F", "F"),
            _row(3, "Card 2", "1", "2"),
            _row(4, "Variable Type", "B", "C"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [card.label for card in keyword.cards] == ["Card 1", "Card 2"]
    assert [(table.row_start, table.row_end) for table in keyword.cards[0].tables] == [
        (0, 3)
    ]
    assert [(table.row_start, table.row_end) for table in keyword.cards[1].tables] == [
        (3, 5)
    ]
    assert all(card.tables[0].role == "definition" for card in keyword.cards)
    assert len(keyword.card_table_blocks) == 1
    assert len(keyword.accounted_blocks()) == 2
    assert keyword.variable_catalog == ["A", "B", "C"]
    assert not validate_keyword_ir(keyword)


def test_card_fields_preserve_empty_slots_and_short_ocr_rows():
    table = TableBlock(
        rows=[
            _row(0, "Card 1", "1", "2", "3", "4", "5", "6", "7", "8"),
            _row(1, "Variable", "MID", "RO"),
            _row(2, "Type", "I"),
            _row(3, "Default", "", "1.0"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]
    fields = keyword.cards[0].fields

    assert len(fields) == 8
    assert (fields[0].variable, fields[0].field_type, fields[0].default) == (
        "MID",
        "I",
        None,
    )
    assert (fields[1].variable, fields[1].field_type, fields[1].default) == (
        "RO",
        None,
        "1.0",
    )
    assert all(field.variable is None for field in fields[2:])
    assert [field.slot for field in fields] == list(range(1, 9))
    assert fields[-1].source.to_dict() == {
        "document_id": "keyword-volume-2",
        "pdf_page": 1,
        "manual_page": "2-1",
        "block_index": 1,
        "row": 0,
        "column": 8,
    }
    assert keyword.variable_catalog == ["MID", "RO"]


def test_reconstruction_uses_span_projection_for_card_and_variable_tables():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    [
                        Cell(text="Card 1", row=0, column=0),
                        Cell(text="1", row=0, column=1),
                        Cell(text="2", row=0, column=2),
                    ],
                    [
                        Cell(text="Variable", row=1, column=0),
                        Cell(text="A", row=1, column=1, colspan=2),
                    ],
                    [
                        Cell(text="Type", row=2, column=0),
                        Cell(text="F", row=2, column=1, colspan=2),
                    ],
                ]
            ),
            TextBlock(text="VARIABLE"),
            TableBlock(
                rows=[
                    [
                        Cell(text="VARIABLE", row=0, column=0),
                        Cell(text="DESCRIPTION", row=0, column=1),
                    ],
                    [
                        Cell(text="A", row=1, column=0, rowspan=2),
                        Cell(text="first", row=1, column=1),
                    ],
                    [Cell(text="continued", row=2, column=1)],
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [(field.variable, field.field_type) for field in keyword.cards[0].fields] == [
        ("A", "F"),
        (None, None),
    ]
    assert keyword.variable_descriptions[0].variable == "A"
    assert keyword.variable_descriptions[0].tables[0].row_end == 3


def test_card_fields_recover_merged_labels_and_compressed_rows():
    table = TableBlock(
        rows=[
            _row(0, "Card 2a.1", "1", "2"),
            _row(1, "Variable\nType", "XI\\nF", "ETA\\nI"),
            _row(2, "Default", "none", "0"),
            _row(3, "Card 2a.2", "1", "2"),
            _row(4, "Variable Type Default", "ALPHA", "BETA"),
            _row(5, "F", "I"),
            _row(6, "1.0", "2"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [card.label for card in keyword.cards] == ["Card 2a.1", "Card 2a.2"]
    assert [
        (field.variable, field.field_type, field.default)
        for field in keyword.cards[0].fields
    ] == [("XI", "F", "none"), ("ETA", "I", "0")]
    assert [
        (field.variable, field.field_type, field.default)
        for field in keyword.cards[1].fields
    ] == [("ALPHA", "F", "1.0"), ("BETA", "I", "2")]
    assert keyword.variable_catalog == ["XI", "ETA", "ALPHA", "BETA"]


def test_card_definition_missing_variable_row_retains_slots_with_issue():
    table = TableBlock(
        rows=[
            _row(0, "Card 1", "1", "2"),
            _row(1, "Type", "I", "F"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert len(keyword.cards[0].fields) == 2
    assert all(field.variable is None for field in keyword.cards[0].fields)
    assert [field.field_type for field in keyword.cards[0].fields] == ["I", "F"]
    issue = next(
        issue
        for issue in keyword.issues
        if issue.code == "CARD_DEFINITION_VARIABLE_ROW_MISSING"
    )
    assert (issue.pdf_page, issue.manual_page) == (1, "2-1")
    assert keyword.status == "warning"
    assert not validate_keyword_ir(keyword)


def test_card_definition_reports_invalid_header_and_ambiguous_rows():
    table = TableBlock(
        rows=[
            _row(0, "Card 1", "A", "B"),
            _row(1, "Variable", "FIRST", "VALUE"),
            _row(2, "Variable", "SECOND", "IGNORED"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [field.variable for field in keyword.cards[0].fields] == [
        "FIRST",
        "VALUE",
    ]
    assert {
        issue.code for issue in keyword.issues
    } >= {
        "CARD_DEFINITION_SLOT_HEADER_INVALID",
        "CARD_DEFINITION_ROW_AMBIGUOUS",
    }
    assert not validate_keyword_ir(keyword)


def test_card_prose_summary_table_is_not_invented_as_definition():
    table = TableBlock(
        rows=[
            _row(0, "Card", "Description"),
            _row(1, "Card 1", "Required input data."),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.cards == []
    assert [block.block for block in keyword.description_blocks] == [table]
    assert not any(
        issue.code.startswith("CARD_DEFINITION_") for issue in keyword.issues
    )
    assert not validate_keyword_ir(keyword)


def test_card_definition_ignores_only_globally_trailing_empty_columns():
    table = TableBlock(
        rows=[
            _row(0, "Card 1", "1", "2", "", ""),
            _row(1, "Variable", "A", "B", "", ""),
            _row(2, "Type", "F", "I", "", ""),
            _row(3, "Default", "0.0", "1", "", ""),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [
        (field.slot, field.variable, field.field_type, field.default)
        for field in keyword.cards[0].fields
    ] == [
        (1, "A", "F", "0.0"),
        (2, "B", "I", "1"),
    ]
    assert not any(
        issue.code == "CARD_DEFINITION_SLOT_HEADER_INVALID"
        for issue in keyword.issues
    )


def test_card_definition_stops_at_explicit_variable_description_header():
    table = TableBlock(
        rows=[
            _row(0, "Card 1", "1", "2"),
            _row(1, "Variable", "A", "B"),
            _row(2, "Type", "F", "I"),
            _row(3, "VARIABLE", "DESCRIPTION", ""),
            _row(4, "Variable", "This is prose, not a second Card row", ""),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [field.variable for field in keyword.cards[0].fields] == ["A", "B"]
    assert not any(
        issue.code == "CARD_DEFINITION_ROW_AMBIGUOUS"
        for issue in keyword.issues
    )
    assert not validate_keyword_ir(keyword)


def test_ariable_header_is_accepted_as_explicit_variable_description_table():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1"),
                    _row(1, "Variable", "A"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "ARIABLE", "DESCRIPTION"),
                    _row(1, "A", "Catalog-backed description."),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [item.variable for item in keyword.variable_descriptions] == ["A"]
    assert keyword.variable_descriptions[0].tables[0].row_start == 1
    assert not any(
        issue.code
        in {
            "VARIABLE_DESCRIPTION_UNMATCHED_TITLE",
            "VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN",
        }
        for issue in keyword.issues
    )


def test_structurally_complete_unknown_card_label_is_accepted_off_boundary():
    table = TableBlock(
        rows=[
            _row(0, "Additional option card", "1", "2"),
            _row(1, "Variable", "A", "B"),
            _row(2, "Type", "F", "I"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="*MAT_EXAMPLE"), table],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [card.label for card in keyword.cards] == ["Additional option card"]
    assert [field.variable for field in keyword.cards[0].fields] == ["A", "B"]
    assert keyword.variable_catalog == ["A", "B"]


def test_later_card_definition_preseeds_earlier_variable_description_catalog():
    description_table = TableBlock(
        rows=[
            _row(0, "VARIABLE", "DESCRIPTION"),
            _row(1, "A", "Description before the Card definition."),
        ]
    )
    card_table = TableBlock(
        rows=[
            _row(0, "Card 1", "1"),
            _row(1, "Variable", "A"),
            _row(2, "Type", "F"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            description_table,
            card_table,
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.variable_catalog == ["A"]
    assert [item.variable for item in keyword.variable_descriptions] == ["A"]
    assert not any(
        issue.code
        in {
            "VARIABLE_DESCRIPTION_CATALOG_UNAVAILABLE",
            "VARIABLE_DESCRIPTION_UNMATCHED_TITLE",
        }
        for issue in keyword.issues
    )


def test_card_field_slash_and_or_aliases_match_exact_unique_titles():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2"),
                    _row(1, "Variable", "PID/PSID", "BETA or MCID"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "PID", "Part identifier."),
                    _row(2, "PSID", "Part-set identifier."),
                    _row(3, "BETA", "Angle value."),
                    _row(4, "MCID", "Coordinate-system identifier."),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.variable_catalog == ["PID/PSID", "BETA or MCID"]
    descriptions = {
        item.variable: item for item in keyword.variable_descriptions
    }
    assert set(descriptions) == {"PID", "PSID", "BETA", "MCID"}
    assert descriptions["PID"].applies_to == ["PID/PSID"]
    assert descriptions["PSID"].applies_to == ["PID/PSID"]
    assert descriptions["BETA"].applies_to == ["BETA or MCID"]
    assert descriptions["MCID"].applies_to == ["BETA or MCID"]
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )


def test_card_field_aliases_require_valid_syntax_and_unique_origin():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3"),
                    _row(
                        1,
                        "Variable",
                        "PID/PSID",
                        "PID/OTHER",
                        "BAD or prose value",
                    ),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "PID", "Ambiguous across two Card fields."),
                    _row(2, "BAD", "Malformed source cell."),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.variable_descriptions == []
    unmatched = [
        issue.message
        for issue in keyword.issues
        if issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
    ]
    assert any("'PID'" in message for message in unmatched)
    assert any("'BAD'" in message for message in unmatched)


def test_variable_type_default_triplets_are_split_only_by_strict_grammar():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3", "4"),
                    _row(
                        1,
                        "Variable Type Default",
                        "TITLE A70 none",
                        "RHO F 0.0",
                        "INVALID FF none",
                        "BAD-TOKEN I 0",
                    ),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "TITLE", "Title text."),
                    _row(2, "RHO", "Density."),
                    _row(3, "INVALID", "Invalid type evidence."),
                    _row(4, "BAD", "Invalid identifier evidence."),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    fields = keyword.cards[0].fields
    assert (fields[0].variable, fields[0].field_type, fields[0].default) == (
        "TITLE",
        "A70",
        "none",
    )
    assert (fields[1].variable, fields[1].field_type, fields[1].default) == (
        "RHO",
        "F",
        "0.0",
    )
    assert (fields[2].variable, fields[2].field_type, fields[2].default) == (
        "INVALID FF none",
        None,
        None,
    )
    assert (fields[3].variable, fields[3].field_type, fields[3].default) == (
        "BAD-TOKEN I 0",
        None,
        None,
    )
    assert [item.variable for item in keyword.variable_descriptions] == [
        "TITLE",
        "RHO",
    ]
    unmatched = [
        issue.message
        for issue in keyword.issues
        if issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
    ]
    assert any("'INVALID'" in message for message in unmatched)
    assert any("'BAD'" in message for message in unmatched)


def test_variable_type_default_triplet_rejects_extra_row_semantics():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1"),
                    _row(1, "Variable Type Default Remarks", "A F 0.0"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "A", "No exact compressed-row contract."),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.cards[0].fields[0].variable is None
    assert any(
        issue.code == "CARD_DEFINITION_VARIABLE_ROW_MISSING"
        for issue in keyword.issues
    )
    assert any(
        issue.code == "VARIABLE_DESCRIPTION_CATALOG_UNAVAILABLE"
        for issue in keyword.issues
    )


def test_shared_keyword_boundary_disables_structural_unknown_card_fallback():
    first = Section(
        "MAT_FIRST",
        "MAT_FIRST",
        "*MAT_FIRST",
        2,
        "keyword",
        None,
        [1, 2],
        ["2-1", "2-2"],
        "keyword-volume-2",
        None,
    )
    second = Section(
        "MAT_SECOND",
        "MAT_SECOND",
        "*MAT_SECOND",
        2,
        "keyword",
        None,
        [2, 3],
        ["2-2", "2-3"],
        "keyword-volume-2",
        None,
    )
    structural_table = TableBlock(
        rows=[
            _row(0, "Unknown option card", "1", "2"),
            _row(1, "Variable", "A", "B"),
        ]
    )
    pages = {
        ("keyword-volume-2", 1): PageIR(
            document_id="keyword-volume-2",
            pdf_page=1,
            manual_page="2-1",
            blocks=[TextBlock(text="First body")],
        ),
        ("keyword-volume-2", 2): PageIR(
            document_id="keyword-volume-2",
            pdf_page=2,
            manual_page="2-2",
            blocks=[TextBlock(text="Unresolved shared body"), structural_table],
        ),
        ("keyword-volume-2", 3): PageIR(
            document_id="keyword-volume-2",
            pdf_page=3,
            manual_page="2-3",
            blocks=[TextBlock(text="Second body")],
        ),
    }

    keywords = reconstruct_keywords(
        assemble_sections([first, second], pages)
    )

    assert all(keyword.cards == [] for keyword in keywords)
    assert all(keyword.variable_catalog == [] for keyword in keywords)
    assert all(
        any(
            issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
            for issue in keyword.issues
        )
        for keyword in keywords
    )


def test_card_conditions_are_structured_without_rewriting_source(tmp_path):
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TextBlock(
                text=(
                    "Card 2a. This card is included if OPT = 1 or 2 and "
                    "CONM.GT.0.0."
                )
            ),
            TableBlock(
                rows=[
                    _row(0, "OPT", "CONM"),
                ]
            ),
        ],
    )
    sections = assemble_sections(
        [_section((1,))], {("keyword-volume-2", 1): page}
    )
    keyword = reconstruct_keywords(sections)[0]

    assert [condition.variable for condition in keyword.cards[0].conditions] == [
        "OPT",
        "CONM",
    ]
    assert [condition.operator for condition in keyword.cards[0].conditions] == [
        "=",
        "GT.",
    ]
    assert keyword.cards[0].conditions[0].values == ("1", "2")
    assert keyword.cards[0].conditions[1].values == ("0.0",)

    rendered = render_sections(
        sections, corpus_root=tmp_path, release="R17"
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")
    assert "#### Conditions" in markdown
    assert "`OPT = 1 or 2`" in markdown
    assert "`CONM GT. 0.0`" in markdown
    assert "This card is included if OPT = 1 or 2" in markdown


def test_card_table_continuation_merges_rows_and_fields(tmp_path):
    pages = {
        ("keyword-volume-2", 1): PageIR(
            document_id="keyword-volume-2",
            pdf_page=1,
            manual_page="2-1",
            blocks=[
                TextBlock(text="*MAT_EXAMPLE"),
                TableBlock(
                    rows=[
                        _row(0, "Card 1", "1", "2"),
                        _row(1, "Variable", "A", "B"),
                    ]
                ),
            ],
        ),
        ("keyword-volume-2", 2): PageIR(
            document_id="keyword-volume-2",
            pdf_page=2,
            manual_page="2-2",
            blocks=[
                TableBlock(
                    rows=[
                        _row(0, "Type", "F", "I"),
                        _row(1, "Default", "0.0", "1"),
                    ]
                )
            ],
        ),
    }
    keyword = reconstruct_keywords(assemble_sections([_section()], pages))[0]

    card = keyword.cards[0]
    assert len(card.tables) == 2
    assert card.tables[1].continuation_of == card.tables[0].source_block.source
    assert [(field.variable, field.field_type, field.default) for field in card.fields] == [
        ("A", "F", "0.0"),
        ("B", "I", "1"),
    ]
    assert len(keyword.card_table_blocks) == 2
    assert len(keyword.accounted_blocks()) == len(keyword.owned_sources)
    assert not validate_keyword_ir(keyword)

    rendered = render_sections(
        assemble_sections([_section()], pages), corpus_root=tmp_path, release="R17"
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")
    assert markdown.count("| Card 1 |") == 1
    assert "| Default | 0.0 | 1 |" in markdown


def test_variable_description_continuation_merges_rendered_table(tmp_path):
    pages = {
        ("keyword-volume-2", 1): PageIR(
            document_id="keyword-volume-2",
            pdf_page=1,
            manual_page="2-1",
            blocks=[
                TextBlock(text="*MAT_EXAMPLE"),
                TableBlock(
                    rows=[
                        _row(0, "Card 1", "1"),
                        _row(1, "Variable", "A"),
                    ]
                ),
                TableBlock(
                    rows=[
                        _row(0, "VARIABLE", "DESCRIPTION"),
                        _row(1, "A", "First line"),
                    ]
                ),
            ],
        ),
        ("keyword-volume-2", 2): PageIR(
            document_id="keyword-volume-2",
            pdf_page=2,
            manual_page="2-2",
            blocks=[
                TableBlock(
                    rows=[
                        _row(0, "VARIABLE", "DESCRIPTION"),
                        _row(1, "", "Continuation line"),
                    ]
                )
            ],
        ),
    }
    section_irs = assemble_sections([_section()], pages)
    keyword = reconstruct_keywords(section_irs)[0]
    tables = keyword.variable_descriptions[0].tables

    assert len(tables) == 2
    assert tables[1].continuation_of == tables[0].source_block.source
    assert len(keyword.accounted_blocks()) == len(keyword.owned_sources)
    assert not validate_keyword_ir(keyword)

    rendered = render_sections(
        section_irs, corpus_root=tmp_path, release="R17"
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")
    assert markdown.count("| Variable | Description |") == 1
    assert "Continuation line" in markdown


def test_variable_descriptions_split_rows_and_keep_continuations():
    card_table = TableBlock(
        rows=[
            _row(0, "Card 1", "1", "2"),
            _row(1, "Variable", "A", "B"),
        ]
    )
    descriptions = TableBlock(
        rows=[
            _row(0, "VARIABLE", "DESCRIPTION"),
            _row(1, "A", "First line"),
            _row(2, "", "A continuation"),
            _row(3, "B", "Second variable"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            card_table,
            TextBlock(text="VARIABLE"),
            TextBlock(text="DESCRIPTION"),
            descriptions,
            TextBlock(text="A"),
            TextBlock(text="Additional A detail."),
            TextBlock(text="Remarks:"),
            TextBlock(text="Remark body."),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.variable_catalog == ["A", "B"]
    assert [item.variable for item in keyword.variable_descriptions] == ["A", "B"]
    a_description, b_description = keyword.variable_descriptions
    assert [(table.row_start, table.row_end) for table in a_description.tables] == [
        (1, 3)
    ]
    assert [(table.row_start, table.row_end) for table in b_description.tables] == [
        (3, 4)
    ]
    assert [block.block.text for block in a_description.blocks] == [
        "A",
        "Additional A detail.",
    ]
    assert len(keyword.variable_description_blocks) == 3
    assert len(keyword.accounted_blocks()) == len(keyword.owned_sources)
    assert not validate_keyword_ir(keyword)


def test_variable_description_unmatched_text_remains_raw_with_issue():
    card_table = TableBlock(
        rows=[
            _row(0, "Card 1", "1"),
            _row(1, "Variable", "KNOWN"),
        ]
    )
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            card_table,
            TextBlock(text="VARIABLE"),
            TextBlock(text="DESCRIPTION"),
            TextBlock(text="UNKNOWN"),
            TextBlock(text="Unknown description body."),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.variable_descriptions == []
    assert [block.block.text for block in keyword.variable_description_blocks] == [
        "VARIABLE",
        "DESCRIPTION",
        "UNKNOWN",
        "Unknown description body.",
    ]
    issue = next(
        issue
        for issue in keyword.issues
        if issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
    )
    assert (issue.pdf_page, issue.manual_page) == (1, "2-1")
    assert not validate_keyword_ir(keyword)


def test_variable_description_families_map_to_concrete_catalog_slots(tmp_path):
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3", "4"),
                    _row(1, "Variable", "A10", "A11", "A20", "B1"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "Aij", "A-family description."),
                    _row(2, "Ai, Bi", "A and B family description."),
                ]
            ),
        ],
    )
    section_irs = assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    keyword = reconstruct_keywords(section_irs)[0]

    families = {description.variable: description for description in keyword.variable_descriptions}
    assert families["Aij"].applies_to == ["A10", "A11", "A20"]
    assert families["Ai, Bi"].applies_to == ["A10", "A11", "A20", "B1"]
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )
    assert not validate_keyword_ir(keyword)

    rendered = render_sections(
        section_irs, corpus_root=tmp_path, release="R17"
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")
    assert "### Aij" in markdown
    assert "Applies to: `A10`, `A11`, `A20`" in markdown


def test_indexed_parameter_phrase_maps_unique_numeric_card_family():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3"),
                    _row(1, "Variable", "P1", "P2", "P3"),
                ]
            ),
            TextBlock(text="VARIABLE"),
            TextBlock(text="DESCRIPTION"),
            TextBlock(text="$ i^{th} $ property parameter"),
        ],
    )
    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    description = keyword.variable_descriptions[0]
    assert description.applies_to == ["P1", "P2", "P3"]
    assert description.blocks[0].block.text == "$ i^{th} $ property parameter"
    assert not validate_keyword_ir(keyword)


def test_variable_descriptions_map_indexed_and_unique_confusable_names():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3", "4"),
                    _row(1, "Variable", "B1BEG", "B2BEG", "EO", "VO"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "B[N]BEG", "Indexed beam range."),
                    _row(2, "E0", "Energy."),
                    _row(3, "V0", "Volume."),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]
    descriptions = {item.variable: item for item in keyword.variable_descriptions}

    assert descriptions["B[N]BEG"].applies_to == ["B1BEG", "B2BEG"]
    assert {"EO", "VO"} <= descriptions.keys()
    assert sum(
        issue.code == "VARIABLE_IDENTIFIER_CONFUSABLE_MATCH"
        for issue in keyword.issues
    ) == 2
    assert all(
        issue.severity == "info"
        for issue in keyword.issues
        if issue.code == "VARIABLE_IDENTIFIER_CONFUSABLE_MATCH"
    )
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )


def test_unlabeled_value_card_recovers_variable_group():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*INITIAL_INTERNAL_DOF_SOLID"),
            TableBlock(rows=[_row(0, "Card 1", "1"), _row(1, "Variable", "LID")]),
            TextBlock(text="Value Cards. Include one card for each value."),
            TableBlock(
                rows=[
                    _row(0, "Card", "1", "2", "3"),
                    _row(1, "Variable", "VALX", "VALY", "VALZ"),
                    _row(2, "Type", "F", "F", "F"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "VALX", "x component"),
                    _row(2, "VALY", "y component"),
                    _row(3, "VALZ", "z component"),
                ]
            ),
        ],
    )
    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert keyword.variable_catalog == ["LID", "VALX", "VALY", "VALZ"]
    assert {card.label for card in keyword.cards} == {"Card 1", "Card"}
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )
    assert not validate_keyword_ir(keyword)


def test_variable_axis_family_accepts_ocr_bracket_damage():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*ICFD_CONTROL_OUTPUT_SUBDOM"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3", "4"),
                    _row(1, "Variable", "PMINX", "PMINY", "PMINZ", "RADIUS"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "PMINX, Y, Z]", "minimum coordinates"),
                    _row(2, "RADIUS", "sphere radius"),
                ]
            ),
        ],
    )
    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]
    descriptions = {item.variable: item for item in keyword.variable_descriptions}

    assert descriptions["PMINX, Y, Z]"].applies_to == ["PMINX", "PMINY", "PMINZ"]
    assert descriptions["RADIUS"].applies_to == ["RADIUS"]
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )


def test_slash_variable_group_owns_following_eq_list():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*ICFD_CONTROL_OUTPUT_VAR"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3", "4"),
                    _row(1, "Variable", "VEL", "AVGVEL", "VORT", "PRE"),
                ]
            ),
            TextBlock(text="VARIABLE"),
            TextBlock(text="DESCRIPTION"),
            TextBlock(text="VEL/AVGVEL/ Velocity and average velocity:\nVORT EQ.0: Is output.\nEQ.1: Is not output."),
        ],
    )
    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]
    descriptions = {item.variable: item for item in keyword.variable_descriptions}

    assert descriptions["VEL, AVGVEL, VORT"].applies_to == ["VEL", "AVGVEL", "VORT"]
    assert descriptions["VEL, AVGVEL, VORT"].blocks[0].block.text.startswith("VEL/AVGVEL")
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )
    assert not validate_keyword_ir(keyword)


def test_multiline_variable_label_and_simple_math_heading_use_catalog():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3"),
                    _row(1, "Variable", "IDAM", "VS", "PNAK"),
                ]
            ),
            TextBlock(text="VARIABLE\nIDAM"),
            TextBlock(text="DESCRIPTION"),
            TextBlock(text="IDAM EQ.0: Use the damage value."),
            TextBlock(text="$$ V_s $$"),
            TextBlock(text="Velocity scale."),
            MathBlock(text=r"$$ P_{\mathrm{N a K}}, $$"),
        ],
    )
    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    descriptions = {item.variable: item for item in keyword.variable_descriptions}
    assert {"IDAM", "VS", "PNAK"} <= descriptions.keys()
    assert any(
        block.block.text == "Velocity scale."
        for block in descriptions["VS"].blocks
    )
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )
    assert not validate_keyword_ir(keyword)


def test_variable_descriptions_use_explicit_header_without_card_catalog():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "CID", "Coordinate system identifier."),
                    _row(2, "A", "Single-letter variable."),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]

    assert [item.variable for item in keyword.variable_descriptions] == ["CID", "A"]
    assert any(
        issue.code == "VARIABLE_DESCRIPTION_CATALOG_UNAVAILABLE"
        for issue in keyword.issues
    )
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )


def test_variable_value_tables_attach_to_catalog_heading():
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2", "3"),
                    _row(1, "Variable", "A1", "XP", "YP"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "A1 value", "Description"),
                    _row(2, "1", "First choice"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "A1 value", "Description"),
                    _row(1, "2", "Second choice"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "XP, YP", "Shared coordinate description"),
                ]
            ),
        ],
    )

    keyword = reconstruct_keywords(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})
    )[0]
    descriptions = {item.variable: item for item in keyword.variable_descriptions}

    assert len(descriptions["A1"].tables) == 2
    assert descriptions["XP, YP"].applies_to == ["XP", "YP"]
    assert not any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )


def test_renderer_normalizes_literal_cell_newlines_and_keeps_latex(tmp_path):
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1"),
                    _row(1, "Variable", "A"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "A", "Line one\\nEQ.0: None; $\\nabla x$"),
                ]
            ),
        ],
    )

    rendered = render_sections(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page}),
        corpus_root=tmp_path,
        release="R17",
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")

    assert "Line one <br> EQ.0: None" in markdown
    assert "$\\nabla x$" in markdown
    assert "Line one\\nEQ" not in markdown


def test_renderer_skips_redundant_summary_and_exact_duplicate_descriptions(tmp_path):
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="*MAT_EXAMPLE"),
            TextBlock(text="Card Summary:"),
            TextBlock(text="Card 1."),
            TableBlock(rows=[_row(0, "A", "B")]),
            TextBlock(text="Data Card Definitions:"),
            TableBlock(
                rows=[
                    _row(0, "Card 1", "1", "2"),
                    _row(1, "Variable", "A", "B"),
                    _row(2, "Type", "F", "F"),
                ]
            ),
            TableBlock(
                rows=[
                    _row(0, "VARIABLE", "DESCRIPTION"),
                    _row(1, "A", "Repeated description"),
                ]
            ),
            TableBlock(rows=[_row(0, "A", "Repeated description")]),
        ],
    )

    rendered = render_sections(
        assemble_sections([_section((1,))], {("keyword-volume-2", 1): page}),
        corpus_root=tmp_path,
        release="R17",
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")

    assert "\n| A | B |\n" not in markdown
    assert markdown.count("Repeated description") == 1


def test_renderer_writes_structured_markdown(tmp_path):
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            HeaderBlock(text="*MAT_EXAMPLE"),
            TextBlock(text="Body page 1."),
            TableBlock(
                rows=[
                    [Cell(text="Variable", row=0, column=0), Cell(text="MID", row=0, column=1)],
                    [Cell(text="Type", row=1, column=0), Cell(text="I", row=1, column=1)],
                ]
            ),
            MathBlock(text=r"E=mc^2"),
            FigureBlock(),
            FooterBlock(text="2-1 (R17)"),
        ],
    )
    section = assemble_sections([_section((1,))], {("keyword-volume-2", 1): page})[0]
    rendered = render_sections([section], corpus_root=tmp_path, release="R17")[0]

    assert rendered.markdown_path is not None
    markdown = rendered.markdown_path.read_text(encoding="utf-8")
    assert "keyword_id: MAT_EXAMPLE" in markdown
    assert "# *MAT_EXAMPLE" in markdown
    assert "Body page 1." in markdown
    assert "| Variable | MID |" in markdown
    assert "$$\nE=mc^2\n$$" in markdown
    assert "Figure omitted" in markdown
    assert "2-1 (R17)" not in markdown


def test_theory_renderer_writes_chapter_markdown_and_manifest(tmp_path):
    section = Section(
        section_id="35.1",
        keyword_id=None,
        name="First",
        volume=None,
        kind="theory",
        parent_section_id="35",
        pdf_pages=[1],
        manual_pages=["35-1"],
        document_id="theory",
        section_number="35.1",
    )
    page = PageIR(
        document_id="theory",
        pdf_page=1,
        manual_page="35-1",
        blocks=[TextBlock(text="35.1 First"), TextBlock(text="Theory body")],
    )
    theory = reconstruct_theory(
        assemble_sections([section], {("theory", 1): page})
    )[0]

    rendered = render_theory(
        [theory], corpus_root=tmp_path, release="R17"
    )[0]

    assert rendered.markdown_path == tmp_path / "markdown" / "theory" / "35.1.md"
    markdown = rendered.markdown_path.read_text(encoding="utf-8")
    assert "section_id: '35.1'" in markdown
    assert "parent_section_id: '35'" in markdown
    assert markdown.count("35.1 First") == 1
    assert "Theory body" in markdown
    assert rendered.manifest_record == {
        "document_id": "theory",
        "manual_type": "theory",
        "section_id": "35.1",
        "section_number": "35.1",
        "title": "First",
        "parent_section_id": "35",
        "source_pages": [{"pdf_page": 1, "manual_page": "35-1"}],
        "markdown_path": "markdown/theory/35.1.md",
        "status": "success",
    }


def test_theory_renderer_preserves_text_merged_into_title_anchor(tmp_path):
    theory = reconstruct_theory(
        assemble_sections(
            [
                Section(
                    "35",
                    None,
                    "Parent",
                    None,
                    "theory",
                    None,
                    [1],
                    ["35-1"],
                    "theory",
                    "35",
                )
            ],
            {
                ("theory", 1): PageIR(
                    document_id="theory",
                    pdf_page=1,
                    manual_page="35-1",
                    blocks=[TextBlock(text="35 Parent introduction continues")],
                )
            },
        )
    )[0]

    rendered = render_theory(
        [theory], corpus_root=tmp_path, release="R17"
    )[0]
    markdown = rendered.markdown_path.read_text(encoding="utf-8")

    assert "35 Parent introduction continues" in markdown


def test_run_reconstruction_writes_theory_to_unified_manifest(tmp_path):
    manual = tmp_path / "LS-DYNA_Manual_Theory_R17.pdf"
    pdf_writer = PdfWriter()
    pdf_writer.add_blank_page(width=612, height=792)
    with manual.open("wb") as handle:
        pdf_writer.write(handle)

    corpus = tmp_path / "corpus"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""manual:
  release: R17
  documents:
    - path: "{manual}"
parser:
  provider: paddleocr-vl-remote
  model: PaddleOCR-VL-1.6
  api_key: null
output:
  corpus_dir: "{corpus}"
""",
        encoding="utf-8",
    )
    intermediate = corpus / "intermediate" / "theory"
    intermediate.mkdir(parents=True)
    document = {
        "document_id": "theory",
        "manual_type": "theory",
        "release": "R17",
        "volume": None,
    }
    (intermediate / "pagemap.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "document": document,
                "pages": [
                    {"pdf_page": 1, "manual_page": "1-1", "evidence": "footer"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (intermediate / "sectionmap.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "document": document,
                "sections": [
                    {
                        "section_id": "1",
                        "keyword_id": None,
                        "name": "Abstract",
                        "volume": None,
                        "kind": "theory",
                        "parent_section_id": None,
                        "pdf_pages": [1],
                        "manual_pages": ["1-1"],
                        "document_id": "theory",
                        "section_number": "1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pageir_path = corpus / "parsing" / "pageir" / "theory" / "page_000001.json"
    pageir_path.parent.mkdir(parents=True)
    save_page_ir(
        PageIR(
            document_id="theory",
            pdf_page=1,
            manual_page="1-1",
            blocks=[TextBlock(text="1 Abstract"), TextBlock(text="Theory body")],
        ),
        pageir_path,
    )

    result = run_reconstruction(config, log=lambda _message: None)

    assert result.status == "success"
    record = json.loads((corpus / "manifest.jsonl").read_text(encoding="utf-8"))
    assert record["manual_type"] == "theory"
    assert record["section_id"] == "1"
    assert "keyword_id" not in record
    assert record["markdown_path"] == "markdown/theory/1.md"
    assert (corpus / record["markdown_path"]).is_file()


def test_run_reconstruction_writes_corpus_outputs(tmp_path):
    manual = tmp_path / "LS-DYNA_Manual_Vol_II_R17.pdf"
    pdf_writer = PdfWriter()
    pdf_writer.add_blank_page(width=612, height=792)
    with manual.open("wb") as handle:
        pdf_writer.write(handle)

    corpus = tmp_path / "corpus"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""manual:
  release: R17
  documents:
    - path: \"{manual}\"
parser:
  provider: paddleocr-vl-remote
  model: PaddleOCR-VL-1.6
  api_key: null
output:
  corpus_dir: \"{corpus}\"
""",
        encoding="utf-8",
    )
    intermediate = corpus / "intermediate" / "keyword-volume-2"
    intermediate.mkdir(parents=True)
    document = {
        "document_id": "keyword-volume-2",
        "manual_type": "keyword",
        "release": "R17",
        "volume": 2,
    }
    (intermediate / "pagemap.json").write_text(
        json.dumps({
            "schema_version": "0.1",
            "document": document,
            "pages": [{"pdf_page": 1, "manual_page": "2-1", "evidence": "footer"}],
        }),
        encoding="utf-8",
    )
    section = _section((1,))
    (intermediate / "sectionmap.json").write_text(
        json.dumps({
            "schema_version": "0.1",
            "document": document,
            "sections": [{
                "section_id": section.section_id,
                "keyword_id": section.keyword_id,
                "name": section.name,
                "volume": section.volume,
                "kind": section.kind,
                "parent_section_id": section.parent_section_id,
                "pdf_pages": section.pdf_pages,
                "manual_pages": section.manual_pages,
                "document_id": section.document_id,
                "section_number": section.section_number,
            }],
        }),
        encoding="utf-8",
    )
    (intermediate / "legacy_alias_map.json").write_text(
        json.dumps({"MAT_001": ["MAT_EXAMPLE"]}),
        encoding="utf-8",
    )
    pageir_path = corpus / "parsing" / "pageir" / "keyword-volume-2" / "page_000001.json"
    pageir_path.parent.mkdir(parents=True)
    save_page_ir(
        PageIR(
            document_id="keyword-volume-2",
            pdf_page=1,
            manual_page="2-1",
            blocks=[TextBlock(text="reconstructed")],
        ),
        pageir_path,
    )

    result = run_reconstruction(config, log=lambda _message: None)

    assert result.status == "success"
    assert result.success_count == 1
    record = json.loads((corpus / "manifest.jsonl").read_text(encoding="utf-8"))
    assert record["markdown_path"] == "markdown/volume-2/MAT/MAT_EXAMPLE.md"
    assert record["legacy_ids"] == ["MAT_001"]
    assert (corpus / record["markdown_path"]).is_file()
    markdown = (corpus / record["markdown_path"]).read_text(encoding="utf-8")
    assert "legacy_ids:\n- MAT_001" in markdown
    summary = json.loads((corpus / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert summary["entry_count"] == 1
    assert summary["text_layer_comparison"]["enabled"] is True
    assert summary["text_layer_comparison"]["actual_sample_count"] == 1
    assert summary["text_layer_comparison"]["divergence_count"] == 0
    assert (corpus / "reports" / "text_layer_comparison.json").is_file()
    corpus_yaml = yaml.safe_load((corpus / "corpus.yaml").read_text(encoding="utf-8"))
    assert corpus_yaml["stats"]["status_success"] == 1
    assert corpus_yaml["stats"]["text_layer_sample_count"] == 1
