"""Unit tests for document inspection on synthetic page texts.

All pages are fabricated layout texts; no official Manual content is
used (see tests/synthetic/README.md).
"""

import json

from lsdyna_manual.parser.segmentation import (
    _parse_toc,
    _scan_footers,
    _scan_legacy_alias_map,
    _title_line_re,
    inspect_volume,
    write_inspection_artifacts,
)
from lsdyna_manual.parser.text_extractor import TextExtractor


class FakeExtractor(TextExtractor):
    def __init__(self, pages):
        self._pages = pages

    def extract_pages(self, pdf_path):
        return self._pages


def test_footer_scan_variants():
    pages = [
        "body text\n2-131 (MAT)                                     R13@bbf (08/27/25)",
        "body text\nR13@bbf (08/27/25)                             2-132 (MAT)",
        "no footer here",
    ]
    footer_map, toc_pages = _scan_footers(pages)
    assert footer_map[1] == ("2-131", "MAT")
    assert footer_map[2] == ("2-132", "MAT")
    assert 3 not in footer_map
    assert toc_pages == set()


def test_toc_parse_with_wrapped_name():
    pages = [
        "*MAT_EXAMPLE_LONG_NAME\n"
        "_WITH_WRAP ............................ 2-10\n"
        "  *MAT_VARIANT ............ 2-11\n"
        "TABLE OF CONTENTS 0-1 (TABLE OF CONTENTS)",
    ]
    entries = _parse_toc(pages, {1})
    assert (entries[0].name, entries[0].manual_page, entries[0].indent) == (
        "*MAT_EXAMPLE_LONG_NAME _WITH_WRAP",
        "2-10",
        0,
    )
    assert entries[1].name == "*MAT_VARIANT"
    assert entries[1].indent == 2


def test_title_line_suffix_patterns():
    pattern = _title_line_re("*MAT_EXAMPLE")
    assert pattern.match("*MAT_EXAMPLE")
    assert pattern.match("*MAT_EXAMPLE_OPTION")
    assert pattern.match("*MAT_EXAMPLE_{OPTION}")
    assert pattern.match("*MAT_EXAMPLE_OPTION1_{OPTION2}_{OPTION3}")
    assert pattern.match("*MAT_EXAMPLE_OPTION_MODEL")
    assert not pattern.match("*MAT_EXAMPLE_PLASTIC")  # different keyword
    assert not pattern.match("*MAT_EXAMPLE_FLUID")    # separate entry
    assert not pattern.match("*MAT_EXAMPLE extra words")

    # Real Manual variant lines seen across R13/R15/R17.
    assert _title_line_re("*AIRBAG_WANG_NEFSKE").match(
        "*AIRBAG_WANG_NEFSKE_{OPTIONS}"
    )
    assert _title_line_re("*AIRBAG_PARTICLE").match(
        "*AIRBAG_PARTICLE_{OPTION1}_..._{OPTION6}"
    )
    family = _title_line_re("*CONSTRAINED_GENERALIZED_WELD", allow_family_token=True)
    assert family.match("*CONSTRAINED_GENERALIZED_WELD_WELDTYPE_{OPTION}")
    # Top-level chapters keep strict OPTION-only matching so child keywords
    # are not mistaken for a variant declaration of the chapter base.
    assert not _title_line_re("*AIRBAG").match("*AIRBAG_PARTICLE_{OPTION}")


def test_alias_map_collision():
    pages = [
        "*MAT_011:             *MAT_STEINBERG [0] {1}\n"
        "*MAT_011:             *MAT_STEINBERG_LUND [0] {1}\n"
        "*MAT_001_FLUID:       *MAT_ELASTIC_FLUID [0] {0}",
    ]
    alias_map = _scan_legacy_alias_map(pages)
    assert alias_map["MAT_011"] == ["MAT_STEINBERG", "MAT_STEINBERG_LUND"]
    assert alias_map["MAT_001_FLUID"] == ["MAT_ELASTIC_FLUID"]


def _synthetic_volume_pages():
    """A miniature manual: TOC page, two entries with footers, then a
    footer-less region where entry starts are located by title lines,
    plus a non-keyword appendix section."""
    return [
        # page 1: TOC (running header line must not be merged into entries)
        "TABLE OF CONTENTS\n"
        "*MAT_EXAMPLE .......................... 2-1\n"
        "  *MAT_VARIANT ......................... 2-3\n"
        "*MAT_OTHER ............................. 2-5\n"
        "APPENDIX A ............................. 2-6\n"
        "0-1 (TABLE OF CONTENTS)",
        # page 2: entry start (header lags: shows family name)
        "*MAT\n"
        "*MAT_EXAMPLE_{OPTION}\n"
        "description text\n"
        "2-1 (MAT)",
        # page 3: continuation with footer
        "*MAT_EXAMPLE                        *MAT\n"
        "body\n"
        "2-2 (MAT)",
        # page 4: variant starts mid-page (no footer from here on)
        "*MAT_EXAMPLE                        *MAT\n"
        "tail of example entry\n"
        "*MAT_VARIANT\n"
        "variant description",
        # page 5: variant continuation, no footer
        "*MAT_VARIANT\n"
        "more variant body",
        # page 6: other entry starts
        "*MAT_VARIANT\n"
        "*MAT_OTHER\n"
        "other body",
        # page 7: appendix section
        "*MAT_OTHER\n"
        "APPENDIX A. Synthetic appendix content\n"
        "appendix body\n"
        "2-6 (MAT)",
    ]


def test_inspect_volume_navigation():
    result = inspect_volume(2, "synthetic.pdf", FakeExtractor(_synthetic_volume_pages()))

    by_page = {entry.pdf_page: entry for entry in result.pagemap}
    assert by_page[2].manual_page == "2-1" and by_page[2].evidence == "footer"
    assert by_page[3].manual_page == "2-2" and by_page[3].evidence == "footer"
    # footer-less region: starts located by title line, manual from TOC
    assert by_page[4].manual_page == "2-3" and by_page[4].evidence == "anchor"
    assert by_page[6].manual_page == "2-5" and by_page[6].evidence == "anchor"
    # page 5 sits between anchors (2-3 and 2-5) with matching arithmetic
    assert by_page[5].manual_page == "2-4" and by_page[5].evidence == "interpolated"

    sections = {section.name: section for section in result.sections}
    assert sections["*MAT_EXAMPLE"].pdf_pages == [2, 3, 4]   # shares page 4
    assert sections["*MAT_VARIANT"].pdf_pages == [4, 5, 6]   # shares pages 4 and 6
    assert sections["*MAT_OTHER"].pdf_pages == [6, 7]        # shares page 7 with appendix
    assert sections["*MAT_VARIANT"].manual_pages == ["2-3", "2-4", "2-5"]

    # non-keyword document section: keyword_id None, still navigable
    appendix = sections["APPENDIX A"]
    assert appendix.keyword_id is None
    assert appendix.section_id == "APPENDIX_A"
    assert appendix.kind == "document"
    assert appendix.pdf_pages == [7]

    # keyword sections keep keyword_id and section_id aligned
    assert sections["*MAT_EXAMPLE"].section_id == "MAT_EXAMPLE"
    assert sections["*MAT_EXAMPLE"].keyword_id == "MAT_EXAMPLE"

    # TOC running header must not corrupt entry names
    assert all(
        "TABLE OF CONTENTS" not in section.name for section in result.sections
    )
    assert result.stats["sections_keyword"] == 3
    assert result.stats["sections_document"] == 1
    assert result.stats["issues_by_code"] == {}


def _nested_document_pages():
    """A synthetic front-matter chapter with nested TOC subsections."""
    return [
        # page 1: TOC with a document chapter, a subsection, and version leaves
        "TABLE OF CONTENTS\n"
        "INTRODUCTION .......................... 1-1\n"
        "  CHRONOLOGICAL HISTORY ............... 1-1\n"
        "    1989-1990 ......................... 1-2\n"
        "  MATERIAL MODELS ..................... 1-3\n"
        "GETTING STARTED ....................... 1-5\n"
        "0-1 (TABLE OF CONTENTS)",
        # pdf page 2: chapter + first subsection start
        "INTRODUCTION\n"
        "CHRONOLOGICAL HISTORY\n"
        "history intro\n"
        "1-1 (INTRODUCTION)",
        # pdf page 3: version-history content (not selected as a section)
        "INTRODUCTION\n"
        "1989-1990\n"
        "version notes\n"
        "1-2 (INTRODUCTION)",
        # pdf page 4: MATERIAL MODELS starts
        "INTRODUCTION\n"
        "MATERIAL MODELS\n"
        "material overview\n"
        "1-3 (INTRODUCTION)",
        # pdf page 5: MATERIAL MODELS continuation
        "INTRODUCTION\n"
        "MATERIAL MODELS\n"
        "more material notes\n"
        "1-4 (INTRODUCTION)",
        # pdf page 6: next document chapter starts
        "INTRODUCTION\n"
        "GETTING STARTED\n"
        "getting started body\n"
        "1-5 (INTRODUCTION)",
    ]


def test_nested_document_subsections_are_selected():
    result = inspect_volume(2, "synthetic.pdf", FakeExtractor(_nested_document_pages()))

    sections = {section.section_id: section for section in result.sections}

    introduction = sections["INTRODUCTION"]
    assert introduction.kind == "document"
    assert introduction.keyword_id is None
    assert introduction.pdf_pages == [2, 3, 4, 5, 6]

    material_models = sections["INTRODUCTION_MATERIAL_MODELS"]
    assert material_models.name == "MATERIAL MODELS"
    assert material_models.kind == "document"
    assert material_models.keyword_id is None
    assert material_models.parent_section_id == "INTRODUCTION"
    assert material_models.pdf_pages == [4, 5, 6]
    assert material_models.manual_pages == ["1-3", "1-4", "1-5"]

    # Version-history leaves remain excluded, while their parent subsection
    # is selected.
    assert "INTRODUCTION_CHRONOLOGICAL_HISTORY" in sections
    assert not any(
        section.section_id.endswith("_1989_1990") for section in result.sections
    )

    assert result.stats["toc_document_entries"] == 4
    assert result.stats["sections_keyword"] == 0
    assert result.stats["sections_document"] == 4
    assert result.stats["sections_unresolved"] == 0
    assert result.stats["issues_by_code"] == {}


def test_duplicate_printed_page_numbers_choose_next_candidate():
    pages = [
        "TABLE OF CONTENTS\n"
        "*MAT_FIRST ............................. 2-1\n"
        "*MAT_SECOND ............................ 2-1\n"  # R12-style page reset
        "0-1 (TABLE OF CONTENTS)",
        # first 2-1 page
        "*MAT\n"
        "*MAT_FIRST\n"
        "first body\n"
        "2-1 (MAT)",
        # continuation between the two 2-1 pages
        "*MAT_FIRST\n"
        "more first body\n"
        "2-2 (MAT)",
        # second 2-1 page, after the monotonic search cursor
        "*MAT\n"
        "*MAT_SECOND\n"
        "second body\n"
        "2-1 (MAT)",
    ]
    result = inspect_volume(2, "synthetic.pdf", FakeExtractor(pages))
    sections = {section.section_id: section for section in result.sections}
    assert sections["MAT_FIRST"].pdf_pages == [2, 3, 4]
    assert sections["MAT_SECOND"].pdf_pages == [4]
    assert result.stats["issues_by_code"] == {}


def test_toc_page_error_falls_back_to_title_evidence():
    pages = [
        "TABLE OF CONTENTS\n"
        "*MAT_EXAMPLE .......................... 2-1\n"
        "*MAT_OTHER ............................. 2-1\n"  # TOC error: actual 2-3
        "0-1 (TABLE OF CONTENTS)",
        # pdf 2: MAT_EXAMPLE starts; MAT_OTHER is not titled here
        "*MAT\n"
        "*MAT_EXAMPLE\n"
        "body\n"
        "2-1 (MAT)",
        # pdf 3: MAT_EXAMPLE continuation
        "*MAT_EXAMPLE\n"
        "continuation\n"
        "2-2 (MAT)",
        # pdf 4: actual MAT_OTHER start
        "*MAT_OTHER\n"
        "*MAT_OTHER\n"
        "other body\n"
        "2-3 (MAT)",
    ]
    result = inspect_volume(2, "synthetic.pdf", FakeExtractor(pages))
    sections = {section.section_id: section for section in result.sections}
    assert sections["MAT_OTHER"].pdf_pages == [4]
    assert sections["MAT_EXAMPLE"].pdf_pages == [2, 3, 4]
    assert any(
        issue.code == "ANCHOR_CONFLICT" and issue.keyword_id == "MAT_OTHER"
        for issue in result.issues
    )


def test_overview_list_mention_is_not_entry_start():
    pages = [
        "TABLE OF CONTENTS\n"
        "*MAT_EXAMPLE .......................... 2-1\n"
        "*MAT_OTHER ............................. 2-3\n"
        "0-1 (TABLE OF CONTENTS)",
        # pdf 2: MAT_EXAMPLE start
        "*MAT\n"
        "*MAT_EXAMPLE\n"
        "body\n"
        "2-1 (MAT)",
        # pdf 3: overview page lists MAT_OTHER far below the running header
        "*MAT_EXAMPLE\n"
        "filler 1\n"
        "filler 2\n"
        "filler 3\n"
        "filler 4\n"
        "filler 5\n"
        "filler 6\n"
        "overview list:\n"
        "*MAT_OTHER\n"
        "2-2 (MAT)",
        # pdf 4: actual MAT_OTHER start, no printed footer
        "*MAT_EXAMPLE\n"
        "*MAT_OTHER\n"
        "other body",
        # pdf 5: continuation
        "*MAT_OTHER\n"
        "more other body",
    ]
    result = inspect_volume(2, "synthetic.pdf", FakeExtractor(pages))
    sections = {section.section_id: section for section in result.sections}
    assert sections["MAT_OTHER"].pdf_pages[0] == 4
    assert result.stats["sections_unresolved"] == 0
    assert result.stats["issues_by_code"] == {}


def test_unresolved_entry_produces_issue(tmp_path):
    pages = _synthetic_volume_pages()
    # rename the *MAT_OTHER body title so the TOC entry cannot be located
    pages[5] = pages[5].replace("*MAT_OTHER", "*MAT_RENAMED")
    result = inspect_volume(2, "synthetic.pdf", FakeExtractor(pages))
    assert all(section.name != "*MAT_OTHER" for section in result.sections)
    assert any(
        issue.code == "TOC_ENTRY_UNRESOLVED" and issue.keyword_id == "MAT_OTHER"
        for issue in result.issues
    )


def test_write_artifacts(tmp_path):
    result = inspect_volume(2, "synthetic.pdf", FakeExtractor(_synthetic_volume_pages()))
    out = write_inspection_artifacts([result], tmp_path)
    volume_dir = out / "volume-2"
    pagemap = json.loads((volume_dir / "pagemap.json").read_text())
    assert pagemap[1] == {"pdf_page": 2, "manual_page": "2-1", "evidence": "footer"}
    sections = json.loads((volume_dir / "sectionmap.json").read_text())
    assert sections[0]["keyword_id"] == "MAT_EXAMPLE"
    assert sections[0]["section_id"] == "MAT_EXAMPLE"
    assert sections[0]["kind"] == "keyword"
    summary = json.loads((out / "inspection_summary.json").read_text())
    assert summary["volumes"]["2"]["pdf_pages"] == 7
