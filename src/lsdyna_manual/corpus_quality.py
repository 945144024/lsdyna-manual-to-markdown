"""Deterministic acceptance checks for generated Corpus artifacts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


class CorpusQualityError(ValueError):
    """Raised when a configured acceptance baseline is invalid."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(paths):
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusQualityError(f"unable to read JSON artifact {path}: {exc}") from exc


def measure_corpus(corpus_root: Path) -> dict[str, Any]:
    """Measure stable, content-addressed properties of a Corpus."""

    summary = _load_json(corpus_root / "reports" / "summary.json")
    if not isinstance(summary, dict):
        raise CorpusQualityError("reports/summary.json must contain an object")
    manifest_path = corpus_root / "manifest.jsonl"
    if not manifest_path.is_file():
        raise CorpusQualityError("manifest.jsonl is missing")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusQualityError(f"manifest line {line_number} is invalid JSON") from exc
        if not isinstance(record, dict):
            raise CorpusQualityError(f"manifest line {line_number} must be an object")
        records.append(record)

    markdown_files = sorted(
        str(path.relative_to(corpus_root)).replace("\\", "/")
        for path in (corpus_root / "markdown").rglob("*.md")
        if path.is_file()
    ) if (corpus_root / "markdown").is_dir() else []
    nonempty = sum(bool((corpus_root / path).read_bytes()) for path in markdown_files)
    manifest_paths = sorted(
        str(record["markdown_path"])
        for record in records
        if record.get("markdown_path")
    )
    missing_manifest_paths = sorted(
        path for path in manifest_paths if not (corpus_root / path).is_file()
    )
    unlisted_markdown = sorted(set(markdown_files) - set(manifest_paths))
    duplicate_manifest_paths = sorted(
        path for path, count in Counter(manifest_paths).items() if count > 1
    )
    records_without_markdown = sum(
        record.get("status") != "failed" and not record.get("markdown_path")
        for record in records
    )
    failed_records_with_markdown = sum(
        record.get("status") == "failed" and bool(record.get("markdown_path"))
        for record in records
    )
    issues_path = corpus_root / "reports" / "issues.jsonl"
    issue_records: list[dict[str, Any]] = []
    if not issues_path.is_file():
        raise CorpusQualityError("reports/issues.jsonl is missing")
    for line_number, line in enumerate(
        issues_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            issue = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusQualityError(
                f"issues report line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(issue, dict):
            raise CorpusQualityError(
                f"issues report line {line_number} must be an object"
            )
        issue_records.append(issue)
    status_counts = Counter(str(record.get("status")) for record in records)
    return {
        "release": summary.get("manual_release"),
        "parse": {
            key: summary.get(key)
            for key in ("parse_total", "parse_completed", "parse_failed", "parse_missing")
        },
        "entries": {
            "count": len(records),
            "status_success": status_counts.get("success", 0),
            "status_warning": status_counts.get("warning", 0),
            "status_failed": status_counts.get("failed", 0),
        },
        "issues_by_severity": dict(
            sorted(Counter(str(issue.get("severity")) for issue in issue_records).items())
        ),
        "issues_by_code": dict(
            sorted(Counter(str(issue.get("code")) for issue in issue_records).items())
        ),
        "markdown": {
            "count": len(markdown_files),
            "nonempty": nonempty,
            "manifest_paths_match": not missing_manifest_paths and not unlisted_markdown,
            "missing_manifest_paths": missing_manifest_paths,
            "unlisted_markdown": unlisted_markdown,
            "duplicate_manifest_paths": duplicate_manifest_paths,
            "records_without_markdown": records_without_markdown,
            "failed_records_with_markdown": failed_records_with_markdown,
            "sha256": _tree_digest(corpus_root, markdown_files),
        },
        "manifest_sha256": _sha256_file(manifest_path),
    }


def run_quality_gate(
    corpus_root: Path,
    baseline_path: Path,
    *,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare a Corpus with a checked-in baseline and write no source data."""

    baseline = _load_json(baseline_path)
    if not isinstance(baseline, dict) or baseline.get("schema_version") != "0.1":
        raise CorpusQualityError("quality baseline schema_version must be '0.1'")
    expected = baseline.get("expected")
    if not isinstance(expected, dict):
        raise CorpusQualityError("quality baseline expected must be an object")
    actual = measure_corpus(corpus_root)
    failures: list[str] = []
    markdown = actual["markdown"]
    if markdown["nonempty"] != markdown["count"]:
        failures.append("one or more Markdown files are empty")
    if not markdown["manifest_paths_match"]:
        failures.append("manifest Markdown paths do not match files on disk")
    if markdown["duplicate_manifest_paths"]:
        failures.append("manifest contains duplicate Markdown paths")
    if markdown["records_without_markdown"]:
        failures.append("non-failed manifest records are missing Markdown paths")
    if markdown["failed_records_with_markdown"]:
        failures.append("failed manifest records must not have Markdown paths")
    for key in ("release", "parse", "entries", "issues_by_severity", "issues_by_code", "markdown", "manifest_sha256"):
        if key not in expected:
            continue
        if actual.get(key) != expected[key]:
            failures.append(f"{key} differs from baseline")

    issue_list = issues
    if issue_list is None:
        issue_path = corpus_root / "reports" / "issues.jsonl"
        issue_list = [json.loads(line) for line in issue_path.read_text(encoding="utf-8").splitlines() if line]
    for requirement in baseline.get("required_evidence", []):
        if not isinstance(requirement, dict):
            raise CorpusQualityError("required_evidence entries must be objects")
        selector = {k: v for k, v in requirement.items() if k != "minimum"}
        matches = [
            issue for issue in issue_list
            if all(issue.get(field) == value for field, value in selector.items())
        ]
        minimum = int(requirement.get("minimum", 1))
        if len(matches) < minimum:
            failures.append(f"required evidence missing: {selector} (need {minimum})")

    result = {
        "schema_version": "0.1",
        "status": "passed" if not failures else "failed",
        "baseline": str(baseline_path),
        "failures": failures,
        "actual": actual,
    }
    (corpus_root / "reports" / "acceptance.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result
