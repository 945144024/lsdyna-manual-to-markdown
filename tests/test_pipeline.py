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
  release: "R99"
  manuals_dir: "{manuals_dir}"
  require_all_volumes: {require_all}
parser:
  provider: "openai-compatible"
  model: "your-model-name"
  base_url: "https://api.example.com/v1"
  api_key_env: "PARSER_API_KEY"
output:
  corpus_dir: "{corpus_dir}"
"""


def _make_synthetic_manual(directory, name, pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(directory / name, "wb") as fh:
        writer.write(fh)


def _write_config(tmp_path, manuals_dir, corpus_dir, require_all=True):
    config = tmp_path / "config.yaml"
    config.write_text(
        CONFIG_TEMPLATE.format(
            manuals_dir=manuals_dir,
            corpus_dir=corpus_dir,
            require_all=str(require_all).lower(),
        ),
        encoding="utf-8",
    )
    return config


def test_build_ingest_only_success(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II", "III"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R99.pdf")
    corpus = tmp_path / "corpus"

    result = run_build(_write_config(tmp_path, manuals, corpus), log=lambda _msg: None)

    assert result.exit_code == 0
    assert result.status == "success"
    assert result.release == "R99"

    corpus_yaml = yaml.safe_load((corpus / "corpus.yaml").read_text(encoding="utf-8"))
    assert corpus_yaml["manual"]["release"] == "R99"
    assert len(corpus_yaml["manual"]["volumes"]) == 3
    assert corpus_yaml["stats"]["entry_count"] == 0

    assert (corpus / "manifest.jsonl").read_text(encoding="utf-8") == ""
    assert (corpus / "markdown").is_dir()

    summary = json.loads((corpus / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "success"
    assert summary["entry_count"] == 0
    assert len(summary["volumes"]) == 3
    assert all(v["status"] == "success" for v in summary["volumes"])

    issues = [
        json.loads(line)
        for line in (corpus / "reports" / "issues.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    codes = {issue["code"] for issue in issues}
    assert "PARSE_NOT_IMPLEMENTED" in codes


def test_build_missing_required_volume_fails(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R99.pdf")

    config = _write_config(tmp_path, manuals, tmp_path / "corpus")
    with pytest.raises(ConfigError, match="Volume III"):
        run_build(config, log=lambda _msg: None)


def test_build_missing_volume_warns_when_allowed(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R99.pdf")
    corpus = tmp_path / "corpus"

    result = run_build(
        _write_config(tmp_path, manuals, corpus, require_all=False),
        log=lambda _msg: None,
    )

    assert result.exit_code == 1
    assert result.status == "warning"
    assert any(issue["code"] == "VOLUME_MISSING" for issue in result.issues)
    corpus_yaml = yaml.safe_load((corpus / "corpus.yaml").read_text(encoding="utf-8"))
    assert len(corpus_yaml["manual"]["volumes"]) == 2


def test_build_release_mismatch_fails(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    for roman in ("I", "II", "III"):
        _make_synthetic_manual(manuals, f"LS-DYNA_Manual_Volume_{roman}_R98.pdf")
    config = _write_config(tmp_path, manuals, tmp_path / "corpus")
    # filenames say R98 while the config template says R99 -> ConfigError
    with pytest.raises(ConfigError, match="release"):
        run_build(config, log=lambda _msg: None)
