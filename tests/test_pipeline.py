"""End-to-end tests for build pipeline orchestration.

All test PDFs are synthetic blank documents generated on the fly; no
official Manual content is used (see tests/synthetic/README.md).
"""

import json
from types import SimpleNamespace

import yaml
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
import pytest

import lsdyna_manual.pipeline as pipeline
from lsdyna_manual.config import ConfigError
from lsdyna_manual.corpus_quality import measure_corpus
from lsdyna_manual.pipeline import (
    EXIT_PAUSED,
    ParsingResult,
    ReconstructionResult,
    run_build,
    run_reconstruction,
)
from lsdyna_manual.parser.page_ir import PageIR, ParseIssue, TextBlock, save_page_ir
from lsdyna_manual.parser.parse_state import PageParseState, ParseStateStore
from lsdyna_manual.providers.base import ProviderJobResult

CONFIG_TEMPLATE = """\
manual:
  release: "{release}"
  manuals_dir: "{manuals_dir}"
parser:
  provider: "paddleocr-vl-remote"
  model: "PaddleOCR-VL-1.6"
  api_key: null
output:
  corpus_dir: "{corpus_dir}"
validation:
  text_layer_enabled: false
"""


def _make_synthetic_manual(directory, name, pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(directory / name, "wb") as fh:
        writer.write(fh)


def _make_visible_synthetic_manual(directory, name):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(b"BT (visible synthetic content) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(directory / name, "wb") as fh:
        writer.write(fh)


class _BuildProvider:
    provider_name = "paddleocr-vl-remote"

    def __init__(self):
        self.config = SimpleNamespace(model="PaddleOCR-VL-1.6")
        self.calls = 0

    def semantic_identity(self):
        return "paddleocr-vl-remote:PaddleOCR-VL-1.6:build-test"

    def parse_pdf_batch(
        self,
        _input_pdf_path,
        *,
        document_id,
        pdf_pages,
        volume=None,
        resume_job_id=None,
        on_progress=None,
    ):
        self.calls += 1
        layouts = [
            {
                "markdown": {"text": "*MAT_TEST\nBody", "images": {}},
                "outputImages": {},
                "prunedResult": {
                    "parsing_res_list": [
                        {"block_label": "text", "block_content": "*MAT_TEST"},
                        {"block_label": "text", "block_content": "Body"},
                    ]
                },
            }
            for _page in pdf_pages
        ]
        return ProviderJobResult(
            provider=self.provider_name,
            model=self.config.model,
            job_id=resume_job_id or f"job-{self.calls}",
            state="done",
            raw_jsonl_text=json.dumps(
                {"result": {"layoutParsingResults": layouts}}
            ),
        )


def _write_config(
    tmp_path, manuals_dir, corpus_dir, release="R17"
):
    config = tmp_path / "config.yaml"
    config.write_text(
        CONFIG_TEMPLATE.format(
            manuals_dir=manuals_dir.as_posix(),
            corpus_dir=corpus_dir.as_posix(),
            release=release,
        ),
        encoding="utf-8",
    )
    return config


def _stub_build_stages(monkeypatch, corpus, *, reconstruction_status="success"):
    calls = []

    def inspect(_config, *, log):
        calls.append("inspect")
        return []

    def parse(_config, **_kwargs):
        calls.append("parse")
        return ParsingResult(
            exit_code=0,
            status="success",
            total_pages=6,
            completed_pages=6,
            failed_pages=0,
            checkpoint_path=corpus / "parsing" / "state.json",
        )

    def reconstruct(_config, **_kwargs):
        calls.append("reconstruct")
        corpus.mkdir(parents=True, exist_ok=True)
        manifest = corpus / "manifest.jsonl"
        manifest.write_text('{"document_id":"test"}\n', encoding="utf-8")
        reports = corpus / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "issues.jsonl").write_text("", encoding="utf-8")
        exit_code = 1 if reconstruction_status == "warning" else 0
        return ReconstructionResult(
            exit_code=exit_code,
            status=reconstruction_status,
            section_count=4,
            success_count=4,
            warning_count=0,
            failed_count=0,
            manifest_path=manifest,
            reports_path=reports,
        )

    monkeypatch.setattr(pipeline, "run_inspection", inspect)
    monkeypatch.setattr(pipeline, "run_parsing", parse)
    monkeypatch.setattr(pipeline, "run_reconstruction", reconstruct)
    return calls


def test_source_hash_is_cached_per_document(monkeypatch, tmp_path):
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"synthetic pdf bytes")
    document = SimpleNamespace(document_id="keyword-volume-1", path=source)
    calls = []

    def digest(path):
        calls.append(path)
        return "source-digest"

    monkeypatch.setattr(pipeline, "sha256_of", digest)
    cache = {}

    assert pipeline._source_sha256_for(document, cache) == "source-digest"
    assert pipeline._source_sha256_for(document, cache) == "source-digest"
    assert calls == [source]


def test_build_runs_one_click_pipeline_success(monkeypatch, tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II", "III"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R17.pdf")
    corpus = tmp_path / "corpus"
    calls = _stub_build_stages(monkeypatch, corpus)

    result = run_build(_write_config(tmp_path, manuals, corpus), log=lambda _msg: None)

    assert calls == ["inspect", "parse", "reconstruct"]
    assert result.exit_code == 0
    assert result.status == "success"
    assert result.release == "R17"
    assert result.completed_pages == result.total_pages == 6
    assert result.section_count == 4
    assert result.manifest_path == corpus / "manifest.jsonl"


def test_build_real_parse_and_reconstruct_from_one_entry(monkeypatch, tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _make_visible_synthetic_manual(
        manuals, "LS-DYNA_Manual_Vol_I_R17.pdf"
    )
    corpus = tmp_path / "corpus"
    config = _write_config(tmp_path, manuals, corpus)

    def inspect(_config, *, log):
        document_dir = corpus / "intermediate" / "keyword-volume-1"
        document_dir.mkdir(parents=True, exist_ok=True)
        document = {
            "document_id": "keyword-volume-1",
            "manual_type": "keyword",
            "release": "R17",
            "volume": 1,
        }
        (document_dir / "pagemap.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "document": document,
                    "pages": [
                        {"pdf_page": 1, "manual_page": "1-1", "evidence": "test"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        (document_dir / "sectionmap.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "document": document,
                    "sections": [
                        {
                            "section_id": "MAT_TEST",
                            "keyword_id": "MAT_TEST",
                            "name": "*MAT_TEST",
                            "volume": 1,
                            "kind": "keyword",
                            "parent_section_id": None,
                            "pdf_pages": [1],
                            "manual_pages": ["1-1"],
                            "document_id": "keyword-volume-1",
                            "section_number": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (document_dir / "legacy_alias_map.json").write_text(
            "{}\n", encoding="utf-8"
        )
        return []

    monkeypatch.setattr(pipeline, "run_inspection", inspect)
    provider = _BuildProvider()

    result = run_build(config, provider=provider, log=lambda _msg: None)

    assert result.status == "success"
    assert result.completed_pages == result.total_pages == 1
    assert result.section_count == 1
    assert provider.calls == 1
    assert result.documents[0]["status"] == "success"
    assert result.documents[0]["parse_completed"] == 1
    record = json.loads((corpus / "manifest.jsonl").read_text(encoding="utf-8"))
    assert record["keyword_id"] == "MAT_TEST"
    assert (corpus / record["markdown_path"]).is_file()
    summary = json.loads(
        (corpus / "reports" / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["parse_total"] == 1
    assert summary["parse_completed"] == 1
    assert summary["parse_failed"] == 0
    assert summary["parse_missing"] == 0

    resumed = run_build(config, provider=provider, log=lambda _msg: None)

    assert resumed.status == "success"
    assert resumed.completed_pages == 1
    assert provider.calls == 1

    baseline = tmp_path / "acceptance.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "expected": measure_corpus(corpus),
                "required_evidence": [],
            }
        ),
        encoding="utf-8",
    )
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"quality_gate:\n  baseline: '{baseline.as_posix()}'\n",
        encoding="utf-8",
    )

    accepted = run_build(config, provider=provider, log=lambda _msg: None)

    assert accepted.status == "success"
    assert json.loads(
        (corpus / "reports/acceptance.json").read_text(encoding="utf-8")
    )["status"] == "passed"

    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["expected"]["release"] = "R16"
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    rejected = run_build(config, provider=provider, log=lambda _msg: None)

    assert rejected.status == "failed"
    assert rejected.exit_code == pipeline.EXIT_FAILED


def test_reconstruct_reports_parse_coverage_and_all_stage_issues(
    monkeypatch, tmp_path
):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _make_synthetic_manual(
        manuals, "LS-DYNA_Manual_Vol_I_R17.pdf", pages=3
    )
    corpus = tmp_path / "corpus"
    config = _write_config(tmp_path, manuals, corpus)
    intermediate = corpus / "intermediate" / "keyword-volume-1"
    intermediate.mkdir(parents=True)
    document = {
        "document_id": "keyword-volume-1",
        "manual_type": "keyword",
        "release": "R17",
        "volume": 1,
    }
    (intermediate / "pagemap.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "document": document,
                "pages": [
                    {
                        "pdf_page": page,
                        "manual_page": f"1-{page}",
                        "evidence": "test",
                    }
                    for page in range(1, 4)
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
                        "section_id": "MAT_TEST",
                        "keyword_id": "MAT_TEST",
                        "name": "*MAT_TEST",
                        "volume": 1,
                        "kind": "keyword",
                        "parent_section_id": None,
                        "pdf_pages": [1, 2, 3],
                        "manual_pages": ["1-1", "1-2", "1-3"],
                        "document_id": "keyword-volume-1",
                        "section_number": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (intermediate / "issues.jsonl").write_text(
        json.dumps(
            {
                "document_id": "keyword-volume-1",
                "manual_type": "keyword",
                "volume": 1,
                "pdf_page": 1,
                "manual_page": "1-1",
                "keyword_id": "MAT_TEST",
                "severity": "warning",
                "code": "ANCHOR_CONFLICT",
                "message": "synthetic inspection conflict",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pageir_path = (
        corpus
        / "parsing"
        / "pageir"
        / "keyword-volume-1"
        / "page_000001.json"
    )
    save_page_ir(
        PageIR(
            document_id="keyword-volume-1",
            pdf_page=1,
            manual_page="stale-1",
            blocks=[TextBlock(text="*MAT_TEST"), TextBlock(text="Body")],
            issues=[
                ParseIssue(
                    severity="warning",
                    code="CURRENT_MANUAL_PAGE",
                    message="synthetic issue must use the current PageMap",
                ),
                ParseIssue(
                    severity="warning",
                    code="EXPLICIT_PAGE_ONLY",
                    message="synthetic issue with a page but no manual label",
                    pdf_page=9,
                )
            ],
        ),
        pageir_path,
    )
    state_store = ParseStateStore(corpus / "parsing" / "state.json")
    state_store.set(
        PageParseState(
            document_id="keyword-volume-1",
            volume=1,
            pdf_page=2,
            status="failed",
            error="synthetic provider format failure",
        )
    )
    save_page_ir(
        PageIR(
            document_id="keyword-volume-1",
            pdf_page=2,
            manual_page="1-2",
            blocks=[TextBlock(text="STALE PAGEIR MUST NOT BE RENDERED")],
        ),
        corpus / "parsing" / "pageir" / "keyword-volume-1" / "page_000002.json",
    )

    result = run_reconstruction(config, log=lambda _message: None)

    assert result.status == "warning"
    summary = json.loads(
        (corpus / "reports" / "summary.json").read_text(encoding="utf-8")
    )
    assert {
        key: summary[key]
        for key in (
            "parse_total",
            "parse_completed",
            "parse_failed",
            "parse_missing",
        )
    } == {
        "parse_total": 3,
        "parse_completed": 1,
        "parse_failed": 1,
        "parse_missing": 1,
    }
    assert summary["parse_total"] == (
        summary["parse_completed"]
        + summary["parse_failed"]
        + summary["parse_missing"]
    )
    assert summary["documents"] == [
        {
            **document,
            "source_file": "LS-DYNA_Manual_Vol_I_R17.pdf",
            "support_level": "verified",
            "status": "warning",
            "parse_total": 3,
            "parse_completed": 1,
            "parse_failed": 1,
            "parse_missing": 1,
        }
    ]
    issues = [
        json.loads(line)
        for line in (corpus / "reports" / "issues.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    parse_failure = next(
        issue for issue in issues if issue["code"] == "PAGE_PARSE_FAILED"
    )
    assert parse_failure == {
        "document_id": "keyword-volume-1",
        "manual_type": "keyword",
        "volume": 1,
        "pdf_page": 2,
        "manual_page": "1-2",
        "keyword_id": None,
        "section_id": None,
        "severity": "error",
        "code": "PAGE_PARSE_FAILED",
        "message": "synthetic provider format failure",
    }
    missing_page = next(
        issue
        for issue in issues
        if issue["code"] == "SECTION_PAGEIR_MISSING"
        and "page 3" in issue["message"]
    )
    assert missing_page["pdf_page"] == 3
    assert missing_page["manual_page"] == "1-3"
    markdown_text = (corpus / "markdown" / "volume-1" / "MAT" / "MAT_TEST.md").read_text(
        encoding="utf-8"
    )
    assert "STALE PAGEIR MUST NOT BE RENDERED" not in markdown_text
    assert any(issue["code"] == "ANCHOR_CONFLICT" for issue in issues)
    inspection_issue = next(
        issue for issue in issues if issue["code"] == "ANCHOR_CONFLICT"
    )
    assert inspection_issue["section_id"] is None
    explicit_page_issue = next(
        issue for issue in issues if issue["code"] == "EXPLICIT_PAGE_ONLY"
    )
    assert explicit_page_issue["pdf_page"] == 9
    assert explicit_page_issue["manual_page"] is None
    current_page_issue = next(
        issue for issue in issues if issue["code"] == "CURRENT_MANUAL_PAGE"
    )
    assert (current_page_issue["pdf_page"], current_page_issue["manual_page"]) == (
        1,
        "1-1",
    )
    assert summary["issue_count"] == len(issues)
    assert sum(summary["issues_by_severity"].values()) == len(issues)
    assert sum(summary["issues_by_code"].values()) == len(issues)

    stale_failure = state_store.get("keyword-volume-1", 2)
    assert stale_failure is not None
    stale_failure.source_sha256 = "stale-source-digest"
    state_store.set(stale_failure)

    stale_result = run_reconstruction(config, log=lambda _message: None)
    stale_summary = json.loads(
        (corpus / "reports" / "summary.json").read_text(encoding="utf-8")
    )
    stale_issues = [
        json.loads(line)
        for line in (corpus / "reports" / "issues.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert stale_result.status == "warning"
    assert stale_summary["parse_failed"] == 0
    assert stale_summary["parse_missing"] == 2
    assert not any(issue["code"] == "PAGE_PARSE_FAILED" for issue in stale_issues)

    def fail_ingest(_document):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(pipeline, "ingest_document", fail_ingest)
    failed_result = run_reconstruction(config, log=lambda _message: None)
    failed_summary = json.loads(
        (corpus / "reports" / "summary.json").read_text(encoding="utf-8")
    )
    assert failed_result.status == "failed"
    assert failed_summary["status"] == "failed"
    assert failed_summary["documents"][0]["status"] == "failed"
    assert failed_summary["issues_by_code"]["DOCUMENT_INGEST_FAILED"] == 1


def test_build_accepts_keyword_subset(monkeypatch, tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R17.pdf")

    corpus = tmp_path / "corpus"
    _stub_build_stages(monkeypatch, corpus)
    result = run_build(_write_config(tmp_path, manuals, corpus), log=lambda _msg: None)
    assert result.exit_code == 0
    assert len(result.documents) == 2

def test_build_missing_volume_warns_when_allowed(monkeypatch, tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R17.pdf")
    corpus = tmp_path / "corpus"
    _stub_build_stages(monkeypatch, corpus)

    result = run_build(
        _write_config(tmp_path, manuals, corpus),
        log=lambda _msg: None,
    )

    assert result.exit_code == 0
    assert result.status == "success"
    assert len(result.documents) == 2


def test_build_release_mismatch_fails(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II", "III"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R98.pdf")
    config = _write_config(tmp_path, manuals, tmp_path / "corpus", release="R17")
    # filenames say R98 while the config says R17 -> ConfigError
    with pytest.raises(ConfigError, match="release"):
        run_build(config, log=lambda _msg: None)

def test_build_accepts_keyword_and_theory_subset(monkeypatch, tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _make_synthetic_manual(manuals, "LS-DYNA_Manual_Vol_I_R17.pdf")
    _make_synthetic_manual(manuals, "LS-DYNA_Manual_Theory_R17.pdf")
    corpus = tmp_path / "corpus"
    _stub_build_stages(monkeypatch, corpus)

    result = run_build(
        _write_config(tmp_path, manuals, corpus),
        log=lambda _msg: None,
    )

    assert result.exit_code == 0
    assert [document["document_id"] for document in result.documents] == [
        "keyword-volume-1",
        "theory",
    ]
    assert [
        document["document_id"]
        for document in result.documents
    ] == ["keyword-volume-1", "theory"]


def test_unverified_release_runs_with_warning(monkeypatch, tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _make_synthetic_manual(manuals, "LS-DYNA_Manual_Theory_R18.pdf")
    corpus = tmp_path / "corpus"
    _stub_build_stages(monkeypatch, corpus, reconstruction_status="warning")

    result = run_build(
        _write_config(
            tmp_path,
            manuals,
            corpus,
            release="R18",
        ),
        log=lambda _msg: None,
    )

    assert result.exit_code == 1
    assert result.status == "warning"
    assert result.documents[0]["document_id"] == "theory"


def test_build_stops_after_quota_pause(monkeypatch, tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _make_synthetic_manual(manuals, "LS-DYNA_Manual_Vol_I_R17.pdf")
    corpus = tmp_path / "corpus"
    calls = []

    monkeypatch.setattr(
        pipeline,
        "run_inspection",
        lambda _config, *, log: calls.append("inspect"),
    )

    def paused_parse(_config, **_kwargs):
        calls.append("parse")
        return ParsingResult(
            exit_code=EXIT_PAUSED,
            status="paused_quota",
            total_pages=10,
            completed_pages=3,
            failed_pages=0,
            checkpoint_path=corpus / "parsing" / "state.json",
        )

    monkeypatch.setattr(pipeline, "run_parsing", paused_parse)
    monkeypatch.setattr(
        pipeline,
        "run_reconstruction",
        lambda *_args, **_kwargs: calls.append("reconstruct"),
    )

    result = run_build(
        _write_config(tmp_path, manuals, corpus),
        log=lambda _msg: None,
    )

    assert result.exit_code == EXIT_PAUSED
    assert result.status == "paused_quota"
    assert result.completed_pages == 3
    assert calls == ["inspect", "parse"]
