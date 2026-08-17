"""End-to-end raw capture + PageIR orchestration tests with a fake provider."""

import json
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from lsdyna_manual.parser.adapters.base import PageAdapter
from lsdyna_manual.parser.ingest import sha256_of
from lsdyna_manual.parser.document_parser import DocumentParser
from lsdyna_manual.parser.page_ir import PageIR, TextBlock
from lsdyna_manual.parser.parse_plan import build_parse_plan
from lsdyna_manual.parser.parse_state import ParseStateStore
from lsdyna_manual.parser.segmentation import PageMapEntry, Section
from lsdyna_manual.providers.base import ProviderJobResult


class FakeProvider:
    provider_name = "fake-provider"

    def __init__(self):
        self.config = SimpleNamespace(model="fake-model")
        self.calls = 0

    def semantic_identity(self):
        return "fake-provider:fake-model"

    def parse_pdf_batch(self, input_pdf_path, *, document_id, pdf_pages, volume=None):
        self.calls += 1
        self.last_pdf_pages = list(pdf_pages)
        pages = [
            {
                "markdown": {"text": f"# page {pdf_page}", "images": {}},
                "outputImages": {},
                "prunedResult": {"parsing_res_list": []},
            }
            for pdf_page in pdf_pages
        ]
        raw = json.dumps({"result": {"layoutParsingResults": pages}})
        return ProviderJobResult(
            provider=self.provider_name,
            model="fake-model",
            job_id=f"job-{self.calls}",
            state="done",
            raw_jsonl_text=raw,
            metadata={"job_data": {"state": "done"}},
        )


class FakeAdapter(PageAdapter):
    def identity(self):
        return "fake-adapter:1"

    def adapt_page(self, raw_page_json_path, *, pdf_page, manual_page):
        return PageIR(
            pdf_page=pdf_page,
            manual_page=manual_page,
            blocks=[TextBlock(text=f"page {pdf_page}")],
            issues=[],
        )


def _source_pdf(tmp_path, pages=3):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    path = tmp_path / "source.pdf"
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_document_parser_raw_and_pageir_with_resume(tmp_path):
    source = _source_pdf(tmp_path, 3)
    sections = [
        Section(
            section_id="A",
            keyword_id="A",
            name="*A",
            volume=2,
            kind="keyword",
            parent_section_id=None,
            pdf_pages=[1, 2, 3],
            manual_pages=[None, None, None],
            document_id="keyword-volume-2",
        )
    ]
    pagemap = {
        "keyword-volume-2": [
            PageMapEntry(pdf_page=1, manual_page="2-1", evidence="footer"),
            PageMapEntry(pdf_page=2, manual_page="2-2", evidence="footer"),
            PageMapEntry(pdf_page=3, manual_page="2-3", evidence="footer"),
        ]
    }
    plan = build_parse_plan(sections, pagemap, batch_size=2)

    state_store = ParseStateStore(tmp_path / "parsing" / "state.json")
    provider = FakeProvider()
    parser = DocumentParser(
        provider,
        state_store=state_store,
        raw_root=tmp_path / "raw",
        pageir_root=tmp_path / "pageir",
    )

    raw_results = parser.parse_raw_for_document(plan, source, document_id="keyword-volume-2")
    assert [r.status for r in raw_results] == ["raw_done", "raw_done", "raw_done"]
    assert provider.calls == 2

    # Resume: no additional provider calls.
    parser.parse_raw_for_document(plan, source, document_id="keyword-volume-2")
    assert provider.calls == 2

    pageir_results = parser.build_pageir_for_document(
        plan, FakeAdapter(), document_id="keyword-volume-2", source_pdf_path=source
    )
    assert [r.status for r in pageir_results] == ["done", "done", "done"]
    assert (tmp_path / "pageir" / "keyword-volume-2" / "page_000001.json").exists()

    # Resume PageIR generation.
    parser.build_pageir_for_document(
        plan, FakeAdapter(), document_id="keyword-volume-2", source_pdf_path=source
    )
    assert state_store.is_done(
        "keyword-volume-2",
        3,
        provider="fake-provider",
        model="fake-model",
        source_sha256=state_store.get("keyword-volume-2", 3).source_sha256,
        semantic_config_hash="fake-provider:fake-model",
        adapter_identity="fake-adapter:1",
        pageir_schema_version="0.1",
    )


def test_resume_resubmits_only_unfinished_page_from_prior_batch(tmp_path):
    source = _source_pdf(tmp_path, 3)
    sections = [
        Section(
            section_id="A",
            keyword_id="A",
            name="*A",
            volume=2,
            kind="keyword",
            parent_section_id=None,
            pdf_pages=[1, 2, 3],
            manual_pages=[None, None, None],
            document_id="keyword-volume-2",
        )
    ]
    pagemap = {
        "keyword-volume-2": [
            PageMapEntry(pdf_page=page, manual_page=None, evidence=None)
            for page in (1, 2, 3)
        ]
    }
    plan = build_parse_plan(sections, pagemap, batch_size=3)

    source_sha256 = sha256_of(source)
    state_store = ParseStateStore(tmp_path / "parsing" / "state.json")
    for pdf_page in (1, 2):
        state_store.set(
            __import__("lsdyna_manual.parser.parse_state", fromlist=["PageParseState"]).PageParseState(
                document_id="keyword-volume-2",
                volume=2,
                pdf_page=pdf_page,
                status="raw_done",
                provider="fake-provider",
                model="fake-model",
                source_sha256=source_sha256,
                semantic_config_hash="fake-provider:fake-model",
            )
        )

    provider = FakeProvider()
    parser = DocumentParser(
        provider,
        state_store=state_store,
        raw_root=tmp_path / "raw",
        pageir_root=tmp_path / "pageir",
    )
    # The prior batch contained pages 1-3, but only page 3 is still pending.
    parser.parse_raw_for_document(plan, source, document_id="keyword-volume-2")
    assert provider.last_pdf_pages == [3]

def test_document_parser_supports_theory_document(tmp_path):
    source = _source_pdf(tmp_path, 2)
    sections = [
        Section(
            section_id="2",
            keyword_id=None,
            name="Solid Elements",
            volume=None,
            kind="theory",
            parent_section_id=None,
            pdf_pages=[1, 2],
            manual_pages=["2-1", "2-2"],
            document_id="theory",
            section_number="2",
        )
    ]
    pagemap = {
        "theory": [
            PageMapEntry(pdf_page=1, manual_page="2-1", evidence="footer"),
            PageMapEntry(pdf_page=2, manual_page="2-2", evidence="footer"),
        ]
    }
    plan = build_parse_plan(sections, pagemap, batch_size=2)
    provider = FakeProvider()
    parser = DocumentParser(
        provider,
        state_store=ParseStateStore(tmp_path / "parsing" / "state.json"),
        raw_root=tmp_path / "raw",
        pageir_root=tmp_path / "pageir",
    )

    raw = parser.parse_raw_for_document(
        plan,
        source,
        document_id="theory",
    )
    assert [result.status for result in raw] == ["raw_done", "raw_done"]
    pageir = parser.build_pageir_for_document(
        plan,
        FakeAdapter(),
        document_id="theory",
        source_pdf_path=source,
    )
    assert [result.status for result in pageir] == ["done", "done"]
    pageir_path = tmp_path / "pageir" / "theory" / "page_000001.json"
    assert pageir_path.exists()
    assert json.loads(pageir_path.read_text())["document_id"] == "theory"
