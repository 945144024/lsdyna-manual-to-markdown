"""Build and inspection pipeline orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lsdyna_manual import __version__
from lsdyna_manual.config import BuildConfig, ConfigError, load_config
from lsdyna_manual.documents import (
    MANUAL_TYPE_KEYWORD,
    MANUAL_TYPE_THEORY,
    ManualDocument,
    keyword_document_id,
    normalize_release,
)
from lsdyna_manual.manifest import writer
from lsdyna_manual.parser.discovery import (
    DiscoveryError,
    discover_documents,
    parse_document_filename,
)
from lsdyna_manual.parser.ingest import DocumentIngestInfo, ingest_document
from lsdyna_manual.parser.segmentation import (
    InspectionResult,
    inspect_document,
    write_inspection_artifacts,
)
from lsdyna_manual.parser.quality import evaluate_inspection
from lsdyna_manual.parser.text_extractor import PopplerLayoutExtractor

EXIT_SUCCESS = 0
EXIT_WARNING = 1
EXIT_FAILED = 2


@dataclass
class BuildResult:
    exit_code: int
    status: str
    release: str | None = None
    documents: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)


def run_inspection(
    config_path: Path | str, log: Callable[[str], None] = print
) -> list[InspectionResult]:
    """Inspect any configured same-release combination of Manual documents."""
    config_path = Path(config_path)
    config = load_config(config_path)
    log(f"lsdyna-manual-builder {__version__}")
    log(f"[1/3] load config: {config_path}")

    release, documents = _resolve_documents(config)
    log(f"[2/3] inspect documents: {[document.path.name for document in documents]}")
    extractor = PopplerLayoutExtractor()
    results = [inspect_document(document, extractor) for document in documents]

    output_dir = config.output.corpus_dir / "intermediate"
    log(f"[3/3] write navigation artifacts: {output_dir}")
    write_inspection_artifacts(results, output_dir)

    quality_failures: list[str] = []
    for result in results:
        quality = evaluate_inspection(result)
        result.stats["quality_status"] = quality.status
        result.stats["quality_metrics"] = quality.metrics
        if quality.status != "passed":
            quality_failures.append(
                f"{result.document_id}: "
                + ", ".join(issue.code for issue in quality.issues)
            )

    for result in results:
        stats = result.stats
        log(
            f"      {result.document_id}: pages={stats['pdf_pages']} "
            f"footer={stats['footer_pages']} "
            f"pagemap filled={stats['pagemap_filled']} "
            f"(none={stats['pagemap_none']}) "
            f"evidence={stats['evidence']} "
            f"sections={stats['sections_located']} "
            f"({stats['sections_keyword']} keyword, "
            f"{stats.get('sections_theory', 0)} theory, "
            f"{stats['sections_document']} document)"
        )
        if stats["issues_by_code"]:
            log(f"      issues: {stats['issues_by_code']}")
        log(f"      quality: {stats['quality_status']}")

    if quality_failures:
        raise ConfigError(
            "inspection quality gate failed: " + "; ".join(quality_failures)
        )

    if release not in {f"R{value}" for value in range(12, 18)}:
        log(f"      warning: release {release} is unverified; results are best-effort")
    return results


def run_build(config_path: Path | str, log: Callable[[str], None] = print) -> BuildResult:
    """Run the ingest-only build pipeline for configured documents."""
    config_path = Path(config_path)
    config = load_config(config_path)
    log(f"lsdyna-manual-builder {__version__}")
    log(f"[1/5] load config: {config_path}")

    release, documents = _resolve_documents(config)
    log(f"[2/5] resolve manuals: release {release}")
    for document in documents:
        log(
            f"      {document.document_id}: {document.path.name} "
            f"({document.support_level})"
        )

    issues: list[dict] = []
    if any(document.support_level == "best-effort" for document in documents):
        issues.append(
            _issue(
                None,
                severity="warning",
                code="UNVERIFIED_RELEASE",
                message=(
                    f"release {release} is outside the verified R12-R17 matrix; "
                    "processing continues on a best-effort basis"
                ),
            )
        )

    log("[3/5] ingest documents")
    records: list[dict] = []
    ingested: list[DocumentIngestInfo] = []
    for document in documents:
        record: dict = {
            **document.metadata(),
            "name": document.display_name,
        }
        try:
            document_info = ingest_document(document)
        except Exception as exc:
            record["status"] = "failed"
            records.append(record)
            issues.append(
                _issue(
                    document,
                    severity="error",
                    code="DOCUMENT_INGEST_FAILED",
                    message=f"failed to ingest {document.path.name}: {exc}",
                )
            )
            log(f"      {document.document_id}: FAILED ({exc})")
            continue
        record.update(
            pdf_page_count=document_info.pdf_page_count,
            sha256=document_info.sha256,
            status="success",
        )
        records.append(record)
        ingested.append(document_info)
        log(
            f"      {document.document_id}: {document_info.pdf_page_count} pages, "
            f"sha256 {document_info.sha256[:12]}"
        )

    corpus_dir = config.output.corpus_dir
    log(f"[4/5] write corpus skeleton: {corpus_dir}")
    writer.write_corpus(
        corpus_dir,
        release=release,
        documents=ingested,
        parser_provider=config.parser.provider,
        parser_model=config.parser.model,
    )
    writer.write_manifest(corpus_dir, records=[])

    issues.append(
        _issue(
            None,
            severity="info",
            code="PARSE_NOT_IMPLEMENTED",
            message="PDF parsing is not implemented yet; this run performs "
            "discovery and ingestion only",
        )
    )

    severities = {issue["severity"] for issue in issues}
    if "error" in severities:
        status, exit_code = "failed", EXIT_FAILED
    elif "warning" in severities:
        status, exit_code = "warning", EXIT_WARNING
    else:
        status, exit_code = "success", EXIT_SUCCESS
    summary = {
        "builder_version": __version__,
        "timestamp": writer.utc_now_iso(),
        "status": status,
        "manual_release": release,
        "documents": records,
        "entry_count": 0,
        "status_success": 0,
        "status_warning": 0,
        "status_failed": 0,
        "notes": [
            "PDF parsing is not implemented yet; this run performs "
            "discovery and ingestion only."
        ],
    }
    log("[5/5] write reports")
    writer.write_reports(corpus_dir / "reports", summary=summary, issues=issues)

    log(f"status: {status} (exit {exit_code}) - 0 entries; parsing not implemented yet")
    return BuildResult(
        exit_code=exit_code,
        status=status,
        release=release,
        documents=records,
        issues=issues,
    )


def _explicit_document(
    *,
    path: Path,
    configured_type: str | None,
    configured_volume: int | None,
    configured_release: str | None,
    context: str,
) -> ManualDocument:
    if not path.is_file():
        raise ConfigError(f"{context}: file not found: {path}")

    inferred = parse_document_filename(path)
    if inferred is not None:
        if configured_type is not None and configured_type != inferred.manual_type:
            raise ConfigError(
                f"{context}: configured type {configured_type} conflicts with "
                f"filename {path.name}"
            )
        if configured_volume is not None and configured_volume != inferred.volume:
            raise ConfigError(
                f"{context}: configured volume {configured_volume} conflicts with "
                f"filename {path.name}"
            )
        return inferred

    if configured_release is None:
        raise ConfigError(
            f"{context}: cannot determine release from filename {path.name}; "
            "set manual.release"
        )
    if configured_type is None:
        raise ConfigError(
            f"{context}: cannot determine Manual type from filename {path.name}; "
            "set manual_type"
        )
    if configured_type == MANUAL_TYPE_KEYWORD:
        if configured_volume not in {1, 2, 3}:
            raise ConfigError(f"{context}: keyword documents require volume 1, 2, or 3")
        document_id = keyword_document_id(configured_volume)
    elif configured_type == MANUAL_TYPE_THEORY:
        if configured_volume is not None:
            raise ConfigError(f"{context}: theory documents must not define volume")
        document_id = "theory"
    else:
        raise ConfigError(f"{context}: unsupported Manual type {configured_type!r}")

    return ManualDocument(
        document_id=document_id,
        manual_type=configured_type,
        volume=configured_volume,
        release=configured_release,
        path=path,
    )


def _resolve_documents(
    config: BuildConfig,
) -> tuple[str, list[ManualDocument]]:
    """Resolve an arbitrary same-release set of Manual documents."""
    if config.manual.documents is not None:
        documents = [
            _explicit_document(
                path=item.path,
                configured_type=item.manual_type,
                configured_volume=item.volume,
                configured_release=config.manual.release,
                context=f"manual.documents[{index}]",
            )
            for index, item in enumerate(config.manual.documents)
        ]
    else:
        if not config.manual.manuals_dir.is_dir():
            raise ConfigError(
                f"manual.manuals_dir not found: {config.manual.manuals_dir}"
            )
        try:
            documents = discover_documents(
                config.manual.manuals_dir,
                expected_release=config.manual.release,
            )
        except DiscoveryError as exc:
            raise ConfigError(str(exc)) from exc

    if not documents:
        raise ConfigError(
            "no LS-DYNA Manual documents found; check manual.manuals_dir or "
            "set manual.documents"
        )

    document_ids = [document.document_id for document in documents]
    duplicates = sorted(
        document_id
        for document_id in set(document_ids)
        if document_ids.count(document_id) > 1
    )
    if duplicates:
        raise ConfigError(f"duplicate Manual documents: {duplicates}")

    releases = {document.release for document in documents}
    if config.manual.release is not None:
        expected = normalize_release(config.manual.release)
        mismatched = [
            document.path.name
            for document in documents
            if document.release != expected
        ]
        if mismatched:
            raise ConfigError(
                f"release mismatch: config expects {expected}, files report "
                f"{sorted(releases)} ({', '.join(mismatched)})"
            )
        release = expected
    elif len(releases) == 1:
        release = next(iter(releases))
    else:
        raise ConfigError(
            f"manuals mix releases {sorted(releases)}; one run must use one release"
        )

    order = {"keyword-volume-1": 1, "keyword-volume-2": 2, "keyword-volume-3": 3, "theory": 4}
    documents.sort(key=lambda document: order[document.document_id])
    return release, documents


def _issue(
    document: ManualDocument | None,
    *,
    severity: str,
    code: str,
    message: str,
) -> dict:
    return {
        "document_id": document.document_id if document else None,
        "manual_type": document.manual_type if document else None,
        "volume": document.volume if document else None,
        "pdf_page": None,
        "manual_page": None,
        "keyword_id": None,
        "severity": severity,
        "code": code,
        "message": message,
    }
