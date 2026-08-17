"""Writers for corpus.yaml, manifest.jsonl and build reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lsdyna_manual import __version__
from lsdyna_manual.parser.ingest import DocumentIngestInfo

VOLUME_NAMES = {1: "Volume I", 2: "Volume II", 3: "Volume III"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _document_record(document: DocumentIngestInfo) -> dict:
    name = (
        "Theory Manual"
        if document.manual_type == "theory"
        else f"Keyword Manual {VOLUME_NAMES[document.volume]}"
    )
    return {
        "document_id": document.document_id,
        "manual_type": document.manual_type,
        "volume": document.volume,
        "name": name,
        "source_file": document.source_file,
        "pdf_page_count": document.pdf_page_count,
        "sha256": document.sha256,
        "support_level": document.support_level,
    }


def write_corpus(
    corpus_dir: Path,
    *,
    release: str,
    documents: list[DocumentIngestInfo],
    parser_provider: str,
    parser_model: str,
    stats: dict | None = None,
) -> None:
    """Write corpus.yaml with optional reconstructed-entry statistics."""
    _ensure_dirs(corpus_dir)
    document_records = [_document_record(document) for document in documents]
    data = {
        "schema_version": "0.1",
        "manual": {
            "product": "LS-DYNA Manuals",
            "release": release,
            "documents": document_records,
        },
        "builder": {
            "version": __version__,
            "parser_provider": parser_provider,
            "parser_model": parser_model,
            "timestamp": utc_now_iso(),
        },
        "stats": stats
        or {
            "entry_count": 0,
            "family_count": 0,
            "status_success": 0,
            "status_warning": 0,
            "status_failed": 0,
        },
    }
    (corpus_dir / "corpus.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def write_manifest(corpus_dir: Path, records: list[dict]) -> None:
    _write_jsonl(corpus_dir / "manifest.jsonl", records)


def write_reports(reports_dir: Path, summary: dict, issues: list[dict]) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_jsonl(reports_dir / "issues.jsonl", issues)


def _ensure_dirs(corpus_dir: Path) -> None:
    (corpus_dir / "markdown").mkdir(parents=True, exist_ok=True)
    (corpus_dir / "reports").mkdir(parents=True, exist_ok=True)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
