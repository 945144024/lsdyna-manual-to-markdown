"""Tests for machine-comparable Corpus acceptance gates."""

import json

from lsdyna_manual.corpus_quality import measure_corpus, run_quality_gate


def _write_corpus(tmp_path):
    root = tmp_path / "corpus"
    markdown = root / "markdown" / "volume-1" / "MAT"
    reports = root / "reports"
    markdown.mkdir(parents=True)
    reports.mkdir(parents=True)
    relative = "markdown/volume-1/MAT/MAT_TEST.md"
    (root / relative).write_text("# *MAT_TEST\n", encoding="utf-8")
    record = {
        "document_id": "keyword-volume-1",
        "manual_type": "keyword",
        "keyword_id": "MAT_TEST",
        "markdown_path": relative,
        "status": "warning",
    }
    (root / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    summary = {
        "manual_release": "R17",
        "parse_total": 1,
        "parse_completed": 1,
        "parse_failed": 0,
        "parse_missing": 0,
        "issues_by_severity": {"warning": 1},
        "issues_by_code": {"MODEL_OUTPUT_BYTE_RECOVERY": 1},
    }
    (reports / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    issue = {
        "document_id": "keyword-volume-1",
        "pdf_page": 3002,
        "severity": "warning",
        "code": "MODEL_OUTPUT_BYTE_RECOVERY",
    }
    (reports / "issues.jsonl").write_text(json.dumps(issue) + "\n", encoding="utf-8")
    return root


def _write_baseline(tmp_path, root):
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "expected": measure_corpus(root),
                "required_evidence": [
                    {
                        "document_id": "keyword-volume-1",
                        "pdf_page": 3002,
                        "code": "MODEL_OUTPUT_BYTE_RECOVERY",
                        "minimum": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_exact_corpus_acceptance_baseline_passes(tmp_path):
    root = _write_corpus(tmp_path)
    result = run_quality_gate(root, _write_baseline(tmp_path, root))

    assert result["status"] == "passed"
    assert result["failures"] == []
    assert (root / "reports" / "acceptance.json").is_file()


def test_content_drift_fails_even_when_counts_do_not_change(tmp_path):
    root = _write_corpus(tmp_path)
    baseline = _write_baseline(tmp_path, root)
    (root / "markdown/volume-1/MAT/MAT_TEST.md").write_text(
        "# changed\n", encoding="utf-8"
    )

    result = run_quality_gate(root, baseline)

    assert result["status"] == "failed"
    assert "markdown differs from baseline" in result["failures"]


def test_empty_or_missing_markdown_fails_structural_gate(tmp_path):
    root = _write_corpus(tmp_path)
    baseline = _write_baseline(tmp_path, root)
    (root / "markdown/volume-1/MAT/MAT_TEST.md").write_text("", encoding="utf-8")

    result = run_quality_gate(root, baseline)

    assert "one or more Markdown files are empty" in result["failures"]


def test_release_issue_and_required_evidence_drift_fail(tmp_path):
    root = _write_corpus(tmp_path)
    baseline = _write_baseline(tmp_path, root)
    summary_path = root / "reports/summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["manual_release"] = "R16"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    (root / "reports/issues.jsonl").write_text("", encoding="utf-8")

    result = run_quality_gate(root, baseline)

    assert "release differs from baseline" in result["failures"]
    assert "issues_by_code differs from baseline" in result["failures"]
    assert any("required evidence missing" in item for item in result["failures"])
