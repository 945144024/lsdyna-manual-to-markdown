"""End-to-end tests for the ingest-only build pipeline.

All test PDFs are synthetic blank documents generated on the fly; no
official Manual content is used (see tests/synthetic/README.md).
"""

import json

import yaml
from pypdf import PdfWriter
import pytest

from lsdyna_manual.config import ConfigError
from lsdyna_manual.pipeline import run_build

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
"""


def _make_synthetic_manual(directory, name, pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(directory / name, "wb") as fh:
        writer.write(fh)


def _write_config(
    tmp_path, manuals_dir, corpus_dir, release="R17"
):
    config = tmp_path / "config.yaml"
    config.write_text(
        CONFIG_TEMPLATE.format(
            manuals_dir=manuals_dir,
            corpus_dir=corpus_dir,
            release=release,
        ),
        encoding="utf-8",
    )
    return config


def test_build_ingest_only_success(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II", "III"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R17.pdf")
    corpus = tmp_path / "corpus"

    result = run_build(_write_config(tmp_path, manuals, corpus), log=lambda _msg: None)

    assert result.exit_code == 0
    assert result.status == "success"
    assert result.release == "R17"

    corpus_yaml = yaml.safe_load((corpus / "corpus.yaml").read_text(encoding="utf-8"))
    assert corpus_yaml["manual"]["release"] == "R17"
    assert len(corpus_yaml["manual"]["documents"]) == 3
    assert corpus_yaml["stats"]["entry_count"] == 0

    assert (corpus / "manifest.jsonl").read_text(encoding="utf-8") == ""
    assert (corpus / "markdown").is_dir()

    summary = json.loads((corpus / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "success"
    assert summary["entry_count"] == 0
    assert len(summary["documents"]) == 3
    assert all(v["status"] == "success" for v in summary["documents"])

    issues = [
        json.loads(line)
        for line in (corpus / "reports" / "issues.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    codes = {issue["code"] for issue in issues}
    assert "PARSE_NOT_IMPLEMENTED" in codes


def test_build_accepts_keyword_subset(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R17.pdf")

    result = run_build(_write_config(tmp_path, manuals, tmp_path / "corpus"), log=lambda _msg: None)
    assert result.exit_code == 0
    assert len(result.documents) == 2

def test_build_missing_volume_warns_when_allowed(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R17.pdf")
    corpus = tmp_path / "corpus"

    result = run_build(
        _write_config(tmp_path, manuals, corpus),
        log=lambda _msg: None,
    )

    assert result.exit_code == 0
    assert result.status == "success"
    corpus_yaml = yaml.safe_load((corpus / "corpus.yaml").read_text(encoding="utf-8"))
    assert len(corpus_yaml["manual"]["documents"]) == 2


def test_build_release_mismatch_fails(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II", "III"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R98.pdf")
    config = _write_config(tmp_path, manuals, tmp_path / "corpus", release="R17")
    # filenames say R98 while the config says R17 -> ConfigError
    with pytest.raises(ConfigError, match="release"):
        run_build(config, log=lambda _msg: None)

def test_build_accepts_keyword_and_theory_subset(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _make_synthetic_manual(manuals, "LS-DYNA_Manual_Vol_I_R17.pdf")
    _make_synthetic_manual(manuals, "LS-DYNA_Manual_Theory_R17.pdf")
    corpus = tmp_path / "corpus"

    result = run_build(
        _write_config(tmp_path, manuals, corpus),
        log=lambda _msg: None,
    )

    assert result.exit_code == 0
    assert [document["document_id"] for document in result.documents] == [
        "keyword-volume-1",
        "theory",
    ]
    corpus_yaml = yaml.safe_load(
        (corpus / "corpus.yaml").read_text(encoding="utf-8")
    )
    assert [
        document["document_id"]
        for document in corpus_yaml["manual"]["documents"]
    ] == ["keyword-volume-1", "theory"]


def test_unverified_release_runs_with_warning(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _make_synthetic_manual(manuals, "LS-DYNA_Manual_Theory_R18.pdf")
    corpus = tmp_path / "corpus"

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
    assert any(issue["code"] == "UNVERIFIED_RELEASE" for issue in result.issues)
