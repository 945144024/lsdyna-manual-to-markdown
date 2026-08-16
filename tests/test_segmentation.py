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
    assert appendix.pdf_pages == [7]

    # TOC running header must not corrupt entry names
    assert all(
        "TABLE OF CONTENTS" not in section.name for section in result.sections
    )
    assert result.stats["sections_keyword"] == 3
    assert result.stats["sections_document"] == 1
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
    summary = json.loads((out / "inspection_summary.json").read_text())
    assert summary["volumes"]["2"]["pdf_pages"] == 7
