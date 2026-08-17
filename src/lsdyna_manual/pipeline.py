"""Build and inspection pipeline orchestration."""

from __future__ import annotations

import json
import subprocess
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
from lsdyna_manual.markdown.renderer import render_keywords
from lsdyna_manual.parser.discovery import (
    DiscoveryError,
    discover_documents,
    parse_document_filename,
)
from lsdyna_manual.parser.adapters.paddleocr_vl import PaddleOCRVLAdapter
from lsdyna_manual.parser.document_parser import DocumentParser
from lsdyna_manual.parser.ingest import DocumentIngestInfo, ingest_document
from lsdyna_manual.parser.parse_plan import build_parse_plan, limit_parse_plan
from lsdyna_manual.parser.page_ir import ParseIssue, load_page_ir
from lsdyna_manual.parser.parse_state import ParseStateStore
from lsdyna_manual.parser.progress import (
    ParseProgressCallback,
    ParseProgressEvent,
    TerminalParseProgress,
)
from lsdyna_manual.parser.segmentation import (
    InspectionResult,
    PageMapEntry,
    Section,
    inspect_document,
    write_inspection_artifacts,
)
from lsdyna_manual.parser.quality import evaluate_inspection
from lsdyna_manual.parser.text_extractor import PopplerLayoutExtractor
from lsdyna_manual.providers.base import (
    DocumentProvider,
    ProviderError,
    ProviderQuotaError,
)
from lsdyna_manual.providers.paddleocr_vl_remote import (
    PaddleOCRVLRemoteConfig,
    PaddleOCRVLRemoteProvider,
)
from lsdyna_manual.providers.paddleocr_vl_local import PaddleOCRVLLocalProvider
from lsdyna_manual.reconstruction.section_ir import assemble_sections
from lsdyna_manual.reconstruction.keyword_ir import reconstruct_keywords
from lsdyna_manual.regression_sampling import load_sample_page_keys
from lsdyna_manual.validation.text_layer import (
    TextLayerComparisonReport,
    compare_text_layer_samples,
)

EXIT_SUCCESS = 0
EXIT_WARNING = 1
EXIT_FAILED = 2
EXIT_PAUSED = 3


@dataclass
class BuildResult:
    exit_code: int
    status: str
    release: str | None = None
    documents: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)


@dataclass
class ParsingResult:
    exit_code: int
    status: str
    total_pages: int
    completed_pages: int
    failed_pages: int
    checkpoint_path: Path


@dataclass
class ReconstructionResult:
    exit_code: int
    status: str
    section_count: int
    success_count: int
    warning_count: int
    failed_count: int
    manifest_path: Path
    reports_path: Path


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


def _load_parse_navigation(
    intermediate_dir: Path,
    documents: list[ManualDocument],
) -> tuple[list[Section], dict[str, list[PageMapEntry]]]:
    sections: list[Section] = []
    pagemap_by_document: dict[str, list[PageMapEntry]] = {}
    for document in documents:
        document_dir = intermediate_dir / document.document_id
        pagemap_path = document_dir / "pagemap.json"
        sectionmap_path = document_dir / "sectionmap.json"
        if not pagemap_path.is_file() or not sectionmap_path.is_file():
            raise ConfigError(
                f"inspection artifacts missing for {document.document_id}; "
                "run 'lsdyna-manual inspect' first"
            )
        try:
            pagemap_payload = json.loads(pagemap_path.read_text(encoding="utf-8"))
            sectionmap_payload = json.loads(
                sectionmap_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(
                f"invalid inspection artifacts for {document.document_id}: {exc}"
            ) from exc
        for payload, name in (
            (pagemap_payload, "pagemap"),
            (sectionmap_payload, "sectionmap"),
        ):
            artifact_document = payload.get("document", {})
            if payload.get("schema_version") != "0.1":
                raise ConfigError(
                    f"unsupported {name} schema for {document.document_id}"
                )
            if artifact_document.get("document_id") != document.document_id:
                raise ConfigError(
                    f"{name} document identity mismatch for {document.document_id}"
                )
            if artifact_document.get("release") != document.release:
                raise ConfigError(
                    f"{name} release mismatch for {document.document_id}"
                )
        try:
            pagemap_by_document[document.document_id] = [
                PageMapEntry(**item) for item in pagemap_payload.get("pages", [])
            ]
            sections.extend(
                Section(**item) for item in sectionmap_payload.get("sections", [])
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"invalid navigation record for {document.document_id}: {exc}"
            ) from exc
    return sections, pagemap_by_document


def run_parsing(
    config_path: Path | str,
    *,
    document_id: str | None = None,
    max_pages: int | None = None,
    log: Callable[[str], None] = print,
    provider: DocumentProvider | None = None,
    on_progress: ParseProgressCallback | None = None,
    allow_runtime_install: bool = False,
    sample_manifest_path: Path | str | None = None,
    intermediate_dir: Path | str | None = None,
) -> ParsingResult:
    """Run resumable page parsing from existing inspection artifacts."""
    config_path = Path(config_path)
    config = load_config(config_path)
    release, documents = _resolve_documents(config)
    if document_id is not None and document_id not in {
        document.document_id for document in documents
    }:
        raise ConfigError(f"configured manuals do not include {document_id}")

    log(f"lsdyna-manual-builder {__version__}")
    log(f"parse release {release}: load PageMap / SectionMap")
    navigation_root = (
        Path(intermediate_dir)
        if intermediate_dir is not None
        else config.output.corpus_dir / "intermediate"
    )
    sections, pagemap_by_document = _load_parse_navigation(navigation_root, documents)
    plan = build_parse_plan(
        sections,
        pagemap_by_document,
        start_page=config.options.start_page,
        end_page=config.options.end_page,
        max_batch_pages=config.parser.max_batch_pages,
    )
    selected_pages = None
    if sample_manifest_path is not None:
        try:
            selected_pages = load_sample_page_keys(
                Path(sample_manifest_path),
                release=release,
                documents={document.document_id: document for document in documents},
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ConfigError(f"invalid sample manifest: {exc}") from exc
    plan = limit_parse_plan(
        plan,
        document_id=document_id,
        max_pages=max_pages,
        selected_pages=selected_pages,
    )
    if not plan.entries:
        raise ConfigError("parse plan contains no pages for the requested range")

    if provider is None:
        if config.parser.provider == "paddleocr-vl-local":
            provider = PaddleOCRVLLocalProvider(
                config.parser.local,
                model=config.parser.model,
                allow_install=allow_runtime_install,
            )
        else:
            if config.parser.api_key is None:
                raise ConfigError(
                    "PaddleOCR API key is missing; set parser.api_key in the local config"
                )
            provider = PaddleOCRVLRemoteProvider(
                PaddleOCRVLRemoteConfig(
                    job_url=config.parser.job_url,
                    model=config.parser.model,
                    api_key=config.parser.api_key.get_secret_value(),
                    timeout_seconds=config.parser.timeout_seconds,
                    poll_interval_seconds=config.parser.poll_interval_seconds,
                    max_retries=config.parser.max_retries,
                    quota_exhausted_codes=config.parser.quota_exhausted_codes,
                )
            )

    parsing_root = config.output.corpus_dir / "parsing"
    checkpoint_path = parsing_root / "state.json"
    state_store = ParseStateStore(checkpoint_path)
    parser = DocumentParser(
        provider,
        state_store=state_store,
        raw_root=parsing_root / "raw",
        pageir_root=parsing_root / "pageir",
    )
    selected_documents = [
        document
        for document in documents
        if any(
            entry.document_id == document.document_id for entry in plan.entries
        )
    ]
    cached_pages: set[tuple[str, int]] = set()
    for document in selected_documents:
        cached_pages.update(
            (document.document_id, page)
            for page in parser.cached_raw_pages(
                plan, document.path, document_id=document.document_id
            )
        )
    terminal: TerminalParseProgress | None = None
    if on_progress is None:
        terminal = TerminalParseProgress(
            plan.page_count, completed_pages=len(cached_pages)
        )
        on_progress = terminal
    parser.on_progress = on_progress
    on_progress(
        ParseProgressEvent(
            phase="checkpoint_validated",
            completed_delta=0,
            message=f"{len(cached_pages)} cached pages",
        )
    )
    log(
        f"parse plan: {plan.page_count} unique pages, {len(plan.batches)} batches, "
        f"{len(cached_pages)} locally validated"
    )

    quota_error: ProviderQuotaError | None = None
    for document in selected_documents:
        try:
            parser.parse_raw_for_document(
                plan, document.path, document_id=document.document_id
            )
        except ProviderQuotaError as exc:
            quota_error = exc
        except ProviderError:
            if terminal is not None:
                terminal.finish("failed")
            raise
        parser.build_pageir_for_document(
            plan,
            PaddleOCRVLAdapter(),
            document_id=document.document_id,
            source_pdf_path=document.path,
        )
        if quota_error is not None:
            break

    completed_pages = 0
    failed_pages = 0
    for entry in plan.entries:
        state = state_store.get(entry.document_id, entry.pdf_page)
        if state is not None and state.status in {"raw_done", "done"}:
            completed_pages += 1
        elif state is not None and state.status == "failed":
            failed_pages += 1

    if quota_error is not None:
        if terminal is not None:
            terminal.finish("paused_quota")
        code = (
            f" business code {quota_error.business_code}"
            if quota_error.business_code is not None
            else ""
        )
        log(
            f"PaddleOCR quota exhausted{code}; checkpoint saved at "
            f"{checkpoint_path}. Re-run later or replace parser.api_key."
        )
        return ParsingResult(
            exit_code=EXIT_PAUSED,
            status="paused_quota",
            total_pages=plan.page_count,
            completed_pages=completed_pages,
            failed_pages=failed_pages,
            checkpoint_path=checkpoint_path,
        )

    status = "warning" if failed_pages else "success"
    exit_code = EXIT_WARNING if failed_pages else EXIT_SUCCESS
    if terminal is not None:
        terminal.finish("completed")
    log(
        f"parse status: {status}; completed={completed_pages}/{plan.page_count}, "
        f"failed={failed_pages}, checkpoint={checkpoint_path}"
    )
    return ParsingResult(
        exit_code=exit_code,
        status=status,
        total_pages=plan.page_count,
        completed_pages=completed_pages,
        failed_pages=failed_pages,
        checkpoint_path=checkpoint_path,
    )


def run_reconstruction(
    config_path: Path | str,
    *,
    document_id: str | None = None,
    log: Callable[[str], None] = print,
) -> ReconstructionResult:
    """Build Markdown and manifest artifacts from existing PageIR files."""

    config_path = Path(config_path)
    config = load_config(config_path)
    release, documents = _resolve_documents(config)
    if document_id is not None and document_id not in {
        document.document_id for document in documents
    }:
        raise ConfigError(f"configured manuals do not include {document_id}")

    sections, _pagemap_by_document = _load_parse_navigation(
        config.output.corpus_dir / "intermediate", documents
    )
    selected_sections = [
        section
        for section in sections
        if section.kind == "keyword"
        and (document_id is None or section.document_id == document_id)
    ]
    if not selected_sections:
        raise ConfigError(
            "reconstruction contains no keyword sections for the requested document"
        )

    pageir_root = config.output.corpus_dir / "parsing" / "pageir"
    page_irs = {}
    for document in documents:
        if document_id is not None and document.document_id != document_id:
            continue
        document_pageir_root = pageir_root / document.document_id
        if not document_pageir_root.is_dir():
            continue
        for path in sorted(document_pageir_root.glob("page_*.json")):
            try:
                page_ir = load_page_ir(path)
            except (OSError, ValueError, KeyError) as exc:
                log(f"warning: unable to load PageIR {path}: {exc}")
                continue
            page_irs[(document.document_id, page_ir.pdf_page)] = page_ir

    text_layer_reports: list[TextLayerComparisonReport] = []
    if config.validation.text_layer_enabled:
        extractor = PopplerLayoutExtractor()
        for document in documents:
            document_pages = {
                pdf_page: page_ir
                for (loaded_document_id, pdf_page), page_ir in page_irs.items()
                if loaded_document_id == document.document_id
            }
            if not document_pages:
                continue
            try:
                report = compare_text_layer_samples(
                    document_id=document.document_id,
                    pdf_path=document.path,
                    page_irs=document_pages,
                    extractor=extractor,
                    sample_count=config.validation.text_layer_sample_pages,
                    min_tokens=config.validation.text_layer_min_tokens,
                    min_visual_recall=config.validation.text_layer_min_visual_recall,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                report = TextLayerComparisonReport(
                    document_id=document.document_id,
                    issues=[
                        ParseIssue(
                            severity="info",
                            code="TEXT_LAYER_COMPARISON_SKIPPED",
                            message=f"text-layer comparison skipped: {exc}",
                        )
                    ],
                )
                log(f"info: text-layer comparison skipped for {document.document_id}: {exc}")
            text_layer_reports.append(report)

    legacy_ids_by_section: dict[tuple[str, str], list[str]] = {}
    for document in documents:
        alias_path = (
            config.output.corpus_dir
            / "intermediate"
            / document.document_id
            / "legacy_alias_map.json"
        )
        if not alias_path.is_file():
            continue
        try:
            alias_payload = json.loads(alias_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log(f"warning: unable to load legacy aliases {alias_path}: {exc}")
            continue
        if not isinstance(alias_payload, dict):
            log(f"warning: legacy aliases must be an object: {alias_path}")
            continue
        for legacy_id, keyword_ids in alias_payload.items():
            if not isinstance(keyword_ids, list):
                continue
            for keyword_id in keyword_ids:
                if isinstance(keyword_id, str):
                    legacy_ids_by_section.setdefault(
                        (document.document_id, keyword_id), []
                    ).append(str(legacy_id))

    section_irs = assemble_sections(
        selected_sections,
        page_irs,
        legacy_ids_by_section=legacy_ids_by_section,
    )
    keyword_irs = reconstruct_keywords(section_irs)
    rendered = render_keywords(
        keyword_irs,
        corpus_root=config.output.corpus_dir,
        release=release,
    )
    records = [item.manifest_record for item in rendered]
    records.sort(
        key=lambda record: (
            record["document_id"],
            record["source_pages"][0]["pdf_page"] if record["source_pages"] else 0,
            record["keyword_id"] or record["name"] or "",
        )
    )

    success_count = sum(item.section.status == "success" for item in rendered)
    warning_count = sum(item.section.status == "warning" for item in rendered)
    failed_count = sum(item.section.status == "failed" for item in rendered)
    text_layer_warning = any(
        issue.severity in {"warning", "error"}
        for report in text_layer_reports
        for issue in report.issues
    )
    text_layer_sample_count = sum(
        len(report.samples) for report in text_layer_reports
    )
    text_layer_divergence_count = sum(
        sum(issue.code == "TEXT_LAYER_DIVERGENCE" for issue in report.issues)
        for report in text_layer_reports
    )
    text_layer_issue_count = sum(
        len(report.issues) for report in text_layer_reports
    )
    status = (
        "failed"
        if failed_count
        else "warning"
        if warning_count or text_layer_warning
        else "success"
    )
    exit_code = (
        EXIT_FAILED
        if failed_count
        else EXIT_WARNING
        if warning_count or text_layer_warning
        else EXIT_SUCCESS
    )

    ingested = []
    document_records: list[dict] = []
    for document in documents:
        try:
            info = ingest_document(document)
        except Exception as exc:
            log(f"warning: unable to ingest metadata for {document.document_id}: {exc}")
            continue
        ingested.append(info)
        document_records.append({**document.metadata(), "status": "success"})

    stats = {
        "entry_count": len(records),
        "family_count": len({record["family"] for record in records if record["family"]}),
        "status_success": success_count,
        "status_warning": warning_count,
        "status_failed": failed_count,
        "text_layer_sample_count": text_layer_sample_count,
        "text_layer_issue_count": text_layer_issue_count,
        "text_layer_divergence_count": text_layer_divergence_count,
    }
    writer.write_corpus(
        config.output.corpus_dir,
        release=release,
        documents=ingested,
        parser_provider=config.parser.provider,
        parser_model=config.parser.model,
        stats=stats,
    )
    writer.write_manifest(config.output.corpus_dir, records)

    issue_records: list[dict] = []
    for item in keyword_irs:
        first_page = item.source_pages[0] if item.source_pages else None
        for issue in item.issues:
            issue_records.append(
                {
                    "document_id": item.document_id,
                    "manual_type": item.manual_type,
                    "volume": item.volume,
                    "pdf_page": first_page.pdf_page if first_page else None,
                    "manual_page": first_page.manual_page if first_page else None,
                    "keyword_id": item.keyword_id,
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                }
            )

    for report in text_layer_reports:
        sample_issue_ids = {
            id(issue)
            for sample in report.samples
            for issue in sample.issues
        }
        for sample in report.samples:
            for issue in sample.issues:
                issue_records.append(
                    {
                        "document_id": report.document_id,
                        "manual_type": "document",
                        "volume": None,
                        "pdf_page": sample.pdf_page,
                        "manual_page": sample.manual_page,
                        "keyword_id": None,
                        "severity": issue.severity,
                        "code": issue.code,
                        "message": issue.message,
                    }
                )
        for issue in report.issues:
            if id(issue) in sample_issue_ids:
                continue
            issue_records.append(
                {
                    "document_id": report.document_id,
                    "manual_type": "document",
                    "volume": None,
                    "pdf_page": None,
                    "manual_page": None,
                    "keyword_id": None,
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                }
            )

    summary = {
        "builder_version": __version__,
        "status": status,
        "manual_release": release,
        "documents": document_records,
        "text_layer_comparison": {
            "enabled": config.validation.text_layer_enabled,
            "sample_count": config.validation.text_layer_sample_pages,
            "actual_sample_count": text_layer_sample_count,
            "issue_count": text_layer_issue_count,
            "divergence_count": text_layer_divergence_count,
            "documents": [report.to_dict() for report in text_layer_reports],
        },
        **stats,
    }
    reports_dir = config.output.corpus_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "text_layer_comparison.json").write_text(
        json.dumps(
            {
                "enabled": config.validation.text_layer_enabled,
                "sample_count": config.validation.text_layer_sample_pages,
                "actual_sample_count": text_layer_sample_count,
                "issue_count": text_layer_issue_count,
                "divergence_count": text_layer_divergence_count,
                "documents": [report.to_dict() for report in text_layer_reports],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    writer.write_reports(reports_dir, summary=summary, issues=issue_records)
    manifest_path = config.output.corpus_dir / "manifest.jsonl"
    log(
        f"reconstruction status: {status}; sections={len(records)}, "
        f"success={success_count}, warning={warning_count}, failed={failed_count}"
    )
    return ReconstructionResult(
        exit_code=exit_code,
        status=status,
        section_count=len(records),
        success_count=success_count,
        warning_count=warning_count,
        failed_count=failed_count,
        manifest_path=manifest_path,
        reports_path=reports_dir,
    )


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
