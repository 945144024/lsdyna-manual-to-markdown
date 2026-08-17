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
from lsdyna_manual.markdown.renderer import render_sections


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
    assert any(issue.code == "SECTION_PAGEIR_MISSING" for issue in result.issues)


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
    assert len(keyword.cards[0].fields) == 1
    assert keyword.cards[0].fields[0].variable == "EOSID"
    assert keyword.cards[0].fields[0].field_type is None
    assert keyword.cards[0].fields[0].default is None
    assert keyword.variable_catalog == ["EOSID"]
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
    assert [block.block.text for block in keyword.unclassified_blocks] == [
        "This is Equation of State Form 2."
    ]
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
    assert "## Source Material" in markdown
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
    assert any(
        issue.code == "CARD_DEFINITION_VARIABLE_ROW_MISSING"
        for issue in keyword.issues
    )
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
    assert any(
        issue.code == "VARIABLE_DESCRIPTION_UNMATCHED_TITLE"
        for issue in keyword.issues
    )
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
