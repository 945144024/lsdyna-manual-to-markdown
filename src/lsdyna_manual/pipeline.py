"""Build and inspection pipeline orchestration."""

from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from lsdyna_manual import __version__
from lsdyna_manual.config import BuildConfig, ConfigError, load_config
from lsdyna_manual.corpus_quality import CorpusQualityError, run_quality_gate
from lsdyna_manual.documents import (
    MANUAL_TYPE_KEYWORD,
    MANUAL_TYPE_THEORY,
    ManualDocument,
    keyword_document_id,
    normalize_release,
)
from lsdyna_manual.manifest import writer
from lsdyna_manual.markdown.renderer import render_keywords, render_theory
from lsdyna_manual.parser.discovery import (
    DiscoveryError,
    discover_documents,
    parse_document_filename,
)
from lsdyna_manual.parser.adapters.paddleocr_vl import PaddleOCRVLAdapter
from lsdyna_manual.parser.document_parser import DocumentParser
from lsdyna_manual.parser.ingest import DocumentIngestInfo, ingest_document, sha256_of
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
from lsdyna_manual.reconstruction.theory_ir import reconstruct_theory
from lsdyna_manual.regression_sampling import load_sample_page_keys
from lsdyna_manual.validation.text_layer import (
    TextLayerComparisonReport,
    compare_text_layer_samples,
)

EXIT_SUCCESS = 0
EXIT_WARNING = 1
EXIT_FAILED = 2
EXIT_PAUSED = 3


class _CachedRawProvider:
    """Provider identity used when every requested raw artifact is local."""

    def __init__(self, provider_name: str, model: str, semantic_identity: str) -> None:
        self.provider_name = provider_name
        self.config = SimpleNamespace(model=model)
        self._semantic_identity = semantic_identity

    def semantic_identity(self) -> str:
        return self._semantic_identity

    def parse_pdf_batch(self, *args, **kwargs):
        raise ProviderError("cached raw provider cannot submit model inference")


def _source_sha256_for(
    document: ManualDocument, cache: dict[str, str]
) -> str:
    cached = cache.get(document.document_id)
    if cached is not None:
        return cached
    digest = sha256_of(document.path)
    cache[document.document_id] = digest
    return digest


def _cached_raw_provider(
    state_store: ParseStateStore,
    plan,
    documents: list[ManualDocument],
) -> _CachedRawProvider | None:
    document_by_id = {document.document_id: document for document in documents}
    identities: set[tuple[str, str, str]] = set()
    source_hashes: dict[str, str] = {}
    for entry in plan.entries:
        state = state_store.get(entry.document_id, entry.pdf_page)
        document = document_by_id.get(entry.document_id)
        if state is None or document is None:
            return None
        source_hash = _source_sha256_for(document, source_hashes)
        if (
            state.status not in {"raw_done", "done"}
            or state.source_sha256 != source_hash
            or not state.provider
            or not state.model
            or not state.semantic_config_hash
            or not state.raw_json_path
            or not Path(state.raw_json_path).is_file()
        ):
            return None
        identities.add(
            (state.provider, state.model, state.semantic_config_hash)
        )
    if len(identities) != 1:
        return None
    provider_name, model, semantic_identity = identities.pop()
    return _CachedRawProvider(provider_name, model, semantic_identity)


@dataclass
class BuildResult:
    exit_code: int
    status: str
    release: str | None = None
    documents: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    total_pages: int = 0
    completed_pages: int = 0
    failed_pages: int = 0
    section_count: int = 0
    manifest_path: Path | None = None
    reports_path: Path | None = None
    stage_durations: dict[str, float] = field(default_factory=dict)
    total_duration_seconds: float = 0.0


@dataclass
class ParsingResult:
    exit_code: int
    status: str
    total_pages: int
    completed_pages: int
    failed_pages: int
    checkpoint_path: Path
    elapsed_seconds: float = 0.0


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
    elapsed_seconds: float = 0.0


@dataclass
class _ParseCoverage:
    total: int
    completed: int
    failed: int
    missing: int
    by_document: dict[str, dict[str, int]]
    failure_issues: list[dict]


def run_inspection(
    config_path: Path | str, log: Callable[[str], None] = print
) -> list[InspectionResult]:
    """Inspect any configured same-release combination of Manual documents."""
    config_path = Path(config_path)
    config = load_config(config_path)
    log(f"LS-DYNA Manual to Markdown {__version__}")
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
                "run 'manual-to-markdown inspect' first"
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
    started_at = time.monotonic()
    config_path = Path(config_path)
    config = load_config(config_path)
    release, documents = _resolve_documents(config)
    if document_id is not None and document_id not in {
        document.document_id for document in documents
    }:
        raise ConfigError(f"configured manuals do not include {document_id}")

    log(f"LS-DYNA Manual to Markdown {__version__}")
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

    parsing_root = config.output.corpus_dir / "parsing"
    checkpoint_path = parsing_root / "state.json"
    state_store = ParseStateStore(checkpoint_path)
    selected_documents = [
        document
        for document in documents
        if any(
            entry.document_id == document.document_id for entry in plan.entries
        )
    ]
    if provider is None:
        provider = _cached_raw_provider(state_store, plan, selected_documents)
        if provider is not None:
            log("all requested raw artifacts are cached; skipping provider startup")
        elif config.parser.provider == "paddleocr-vl-local":
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
    parser = DocumentParser(
        provider,
        state_store=state_store,
        raw_root=parsing_root / "raw",
        pageir_root=parsing_root / "pageir",
    )
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
        pageir_results = parser.build_pageir_for_document(
            plan,
            PaddleOCRVLAdapter(),
            document_id=document.document_id,
            source_pdf_path=document.path,
        )
        retry_pages = {
            result.pdf_page
            for result in pageir_results
            if result.status == "parse_empty"
        }
        if retry_pages:
            retry_plan = limit_parse_plan(
                plan,
                selected_pages={
                    (document.document_id, pdf_page)
                    for pdf_page in retry_pages
                },
            )
            parser.parse_raw_for_document(
                retry_plan,
                document.path,
                document_id=document.document_id,
            )
            parser.build_pageir_for_document(
                retry_plan,
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
            elapsed_seconds=time.monotonic() - started_at,
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
        elapsed_seconds=time.monotonic() - started_at,
    )


def _load_inspection_issue_records(
    intermediate_root: Path,
    documents: list[ManualDocument],
    *,
    log: Callable[[str], None],
) -> list[dict]:
    """Load inspection issues into the final report namespace.

    Inspection issues are intentionally kept in their intermediate files rather
    than copied into PageIR.  Reconstruction is the point where all stage
    reports are combined, so a standalone ``reconstruct`` produces the same
    issue inventory as ``build``.
    """

    records: list[dict] = []
    for document in documents:
        path = intermediate_root / document.document_id / "issues.jsonl"
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log(f"warning: unable to load inspection issues {path}: {exc}")
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                log(
                    f"warning: unable to decode inspection issue {path}:"
                    f"{line_number}: {exc}"
                )
                continue
            if not isinstance(payload, dict):
                log(
                    f"warning: ignoring non-object inspection issue {path}:"
                    f"{line_number}"
                )
                continue
            code = payload.get("code")
            message = payload.get("message")
            severity = payload.get("severity")
            if not all(isinstance(value, str) for value in (code, message, severity)):
                log(
                    f"warning: ignoring malformed inspection issue {path}:"
                    f"{line_number}"
                )
                continue
            records.append(
                {
                    "document_id": document.document_id,
                    "manual_type": document.manual_type,
                    "volume": payload.get("volume", document.volume),
                    "pdf_page": payload.get("pdf_page"),
                    "manual_page": payload.get("manual_page"),
                    "keyword_id": payload.get("keyword_id"),
                    "section_id": payload.get("section_id"),
                    "severity": severity,
                    "code": code,
                    "message": message,
                }
            )
    return records


def _infer_parse_coverage(
    *,
    config: BuildConfig,
    documents: list[ManualDocument],
    sections: list[Section],
    pagemap_by_document: dict[str, list[PageMapEntry]],
    page_irs: dict[tuple[str, int], object],
    document_id: str | None,
) -> _ParseCoverage:
    """Infer parse coverage for a standalone reconstruction.

    The parse checkpoint is authoritative for explicit page failures.  A page
    is counted as completed only when its PageIR is actually available to
    reconstruction; ``raw_done`` alone means that only the provider artifact
    exists and is therefore reported as missing here.
    """

    plan = build_parse_plan(
        sections,
        pagemap_by_document,
        start_page=config.options.start_page,
        end_page=config.options.end_page,
        max_batch_pages=config.parser.max_batch_pages,
    )
    if document_id is not None:
        plan = limit_parse_plan(plan, document_id=document_id)

    checkpoint_path = config.output.corpus_dir / "parsing" / "state.json"
    if checkpoint_path.is_file():
        try:
            state_store = ParseStateStore(checkpoint_path)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid parsing checkpoint {checkpoint_path}: {exc}") from exc
    else:
        state_store = None

    by_document: dict[str, dict[str, int]] = {
        document.document_id: {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "missing": 0,
        }
        for document in documents
        if document_id is None or document.document_id == document_id
    }
    document_by_id = {document.document_id: document for document in documents}
    source_hashes: dict[str, str] = {}
    failure_issues: list[dict] = []
    completed = failed = missing = 0
    for entry in plan.entries:
        counts = by_document.setdefault(
            entry.document_id,
            {"total": 0, "completed": 0, "failed": 0, "missing": 0},
        )
        counts["total"] += 1
        state = (
            state_store.get(entry.document_id, entry.pdf_page)
            if state_store is not None
            else None
        )
        document = document_by_id.get(entry.document_id)
        state_failure_is_current = state is not None and state.status == "failed"
        if (
            state_failure_is_current
            and state.source_sha256 is not None
            and document is not None
        ):
            state_failure_is_current = state.source_sha256 == _source_sha256_for(
                document, source_hashes
            )
        if state_failure_is_current:
            failed += 1
            counts["failed"] += 1
            failure_issues.append(
                {
                    "document_id": entry.document_id,
                    "manual_type": document.manual_type if document else None,
                    "volume": entry.volume,
                    "pdf_page": entry.pdf_page,
                    "manual_page": entry.manual_page,
                    "keyword_id": None,
                    "section_id": None,
                    "severity": "error",
                    "code": "PAGE_PARSE_FAILED",
                    "message": state.error
                    or "page parse failed without an error message in checkpoint",
                }
            )
        elif (entry.document_id, entry.pdf_page) in page_irs:
            completed += 1
            counts["completed"] += 1
        else:
            missing += 1
            counts["missing"] += 1

    return _ParseCoverage(
        total=plan.page_count,
        completed=completed,
        failed=failed,
        missing=missing,
        by_document=by_document,
        failure_issues=failure_issues,
    )


def run_reconstruction(
    config_path: Path | str,
    *,
    document_id: str | None = None,
    log: Callable[[str], None] = print,
) -> ReconstructionResult:
    """Build Markdown and manifest artifacts from existing PageIR files."""
    started_at = time.monotonic()

    config_path = Path(config_path)
    config = load_config(config_path)
    release, documents = _resolve_documents(config)
    if document_id is not None and document_id not in {
        document.document_id for document in documents
    }:
        raise ConfigError(f"configured manuals do not include {document_id}")

    active_documents = [
        document
        for document in documents
        if document_id is None or document.document_id == document_id
    ]
    sections, pagemap_by_document = _load_parse_navigation(
        config.output.corpus_dir / "intermediate", documents
    )
    selected_sections = [
        section
        for section in sections
        if section.kind in {"keyword", "theory"}
        and (document_id is None or section.document_id == document_id)
    ]
    if not selected_sections:
        raise ConfigError(
            "reconstruction contains no keyword or theory sections for the "
            "requested document"
        )

    pageir_root = config.output.corpus_dir / "parsing" / "pageir"
    checkpoint_path = config.output.corpus_dir / "parsing" / "state.json"
    parse_state_store = None
    if checkpoint_path.is_file():
        try:
            parse_state_store = ParseStateStore(checkpoint_path)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid parsing checkpoint {checkpoint_path}: {exc}") from exc
    page_irs = {}
    source_hashes: dict[str, str] = {}
    manual_pages_by_document = {
        loaded_document_id: {
            entry.pdf_page: entry.manual_page for entry in entries
        }
        for loaded_document_id, entries in pagemap_by_document.items()
    }
    for document in active_documents:
        document_pageir_root = pageir_root / document.document_id
        if not document_pageir_root.is_dir():
            continue
        for path in sorted(document_pageir_root.glob("page_*.json")):
            try:
                page_ir = load_page_ir(path)
            except (OSError, ValueError, KeyError) as exc:
                log(f"warning: unable to load PageIR {path}: {exc}")
                continue
            # A failed or incomplete checkpoint may leave a PageIR from an
            # earlier attempt on disk. Do not let that stale artifact mask the
            # current page failure during reconstruction. Checkpoint-less
            # artifacts remain accepted for standalone/offline reconstruction.
            if parse_state_store is not None:
                state = parse_state_store.get(document.document_id, page_ir.pdf_page)
                if state is not None:
                    if state.status != "done" or state.pageir_path is None:
                        continue
                    if state.source_sha256 is not None:
                        source_hash = _source_sha256_for(document, source_hashes)
                        if state.source_sha256 != source_hash:
                            continue
                    if state.pageir_path is not None:
                        try:
                            if Path(state.pageir_path).resolve() != path.resolve():
                                continue
                        except OSError:
                            continue
            if page_ir.document_id != document.document_id:
                log(f"warning: PageIR document identity mismatch in {path}")
                continue
            try:
                filename_page = int(path.stem.removeprefix("page_"))
            except ValueError:
                log(f"warning: invalid PageIR filename {path}")
                continue
            if filename_page != page_ir.pdf_page:
                log(f"warning: PageIR filename/page mismatch in {path}")
                continue
            current_manual_pages = manual_pages_by_document.get(
                document.document_id, {}
            )
            if page_ir.pdf_page in current_manual_pages:
                # PageMap is the authoritative printed-page mapping. It may be
                # corrected independently of the PageIR cache identity.
                page_ir.manual_page = current_manual_pages[page_ir.pdf_page]
            page_irs[(document.document_id, page_ir.pdf_page)] = page_ir

    text_layer_reports: list[TextLayerComparisonReport] = []
    if config.validation.text_layer_enabled:
        extractor = PopplerLayoutExtractor()
        for document in active_documents:
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
    for document in active_documents:
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
    theory_irs = reconstruct_theory(section_irs)
    rendered = [
        *render_keywords(
            keyword_irs,
            corpus_root=config.output.corpus_dir,
            release=release,
        ),
        *render_theory(
            theory_irs,
            corpus_root=config.output.corpus_dir,
            release=release,
        ),
    ]
    records = [item.manifest_record for item in rendered]
    records.sort(
        key=lambda record: (
            record["document_id"],
            record["source_pages"][0]["pdf_page"] if record["source_pages"] else 0,
            record.get("keyword_id")
            or record.get("section_id")
            or record.get("name")
            or record.get("title")
            or "",
        )
    )

    success_count = sum(item.section.status == "success" for item in rendered)
    warning_count = sum(item.section.status == "warning" for item in rendered)
    failed_count = sum(item.section.status == "failed" for item in rendered)
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
    support_warning = any(
        document.support_level == "best-effort" for document in active_documents
    )
    parse_coverage = _infer_parse_coverage(
        config=config,
        documents=active_documents,
        sections=sections,
        pagemap_by_document=pagemap_by_document,
        page_irs=page_irs,
        document_id=document_id,
    )

    issue_records = _load_inspection_issue_records(
        config.output.corpus_dir / "intermediate",
        active_documents,
        log=log,
    )
    if support_warning:
        issue_records.append(
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
    issue_records.extend(parse_coverage.failure_issues)
    for item in [*keyword_irs, *theory_irs]:
        first_page = item.source_pages[0] if item.source_pages else None
        for issue in item.issues:
            issue_pdf_page = getattr(issue, "pdf_page", None)
            issue_manual_page = getattr(issue, "manual_page", None)
            report_pdf_page = (
                issue_pdf_page
                if issue_pdf_page is not None
                else first_page.pdf_page if first_page else None
            )
            report_manual_page = issue_manual_page
            if report_manual_page is None and first_page is not None:
                if issue_pdf_page is None or issue_pdf_page == first_page.pdf_page:
                    report_manual_page = first_page.manual_page
            issue_records.append(
                {
                    "document_id": item.document_id,
                    "manual_type": item.manual_type,
                    "volume": getattr(item, "volume", None),
                    "pdf_page": report_pdf_page,
                    "manual_page": report_manual_page,
                    "keyword_id": getattr(item, "keyword_id", None),
                    "section_id": getattr(item, "section_id", None),
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                }
            )

    document_by_id = {
        document.document_id: document for document in active_documents
    }
    for report in text_layer_reports:
        document = document_by_id.get(report.document_id)
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
                        "manual_type": document.manual_type if document else None,
                        "volume": document.volume if document else None,
                        "pdf_page": sample.pdf_page,
                        "manual_page": sample.manual_page,
                        "keyword_id": None,
                        "section_id": None,
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
                    "manual_type": document.manual_type if document else None,
                    "volume": document.volume if document else None,
                    "pdf_page": getattr(issue, "pdf_page", None),
                    "manual_page": getattr(issue, "manual_page", None),
                    "keyword_id": None,
                    "section_id": None,
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                }
            )

    ingested = []
    for document in active_documents:
        try:
            info = ingest_document(document)
        except Exception as exc:
            log(f"warning: unable to ingest metadata for {document.document_id}: {exc}")
            issue_records.append(
                _issue(
                    document,
                    severity="error",
                    code="DOCUMENT_INGEST_FAILED",
                    message=str(exc),
                )
            )
            continue
        ingested.append(info)

    issues_by_severity = dict(
        sorted(Counter(issue["severity"] for issue in issue_records).items())
    )
    issues_by_code = dict(
        sorted(Counter(issue["code"] for issue in issue_records).items())
    )
    has_report_warning = any(
        issue["severity"] in {"warning", "error"} for issue in issue_records
    )
    status = (
        "failed"
        if failed_count or issues_by_code.get("DOCUMENT_INGEST_FAILED", 0)
        else "warning"
        if warning_count
        or parse_coverage.failed
        or parse_coverage.missing
        or has_report_warning
        else "success"
    )
    exit_code = {
        "success": EXIT_SUCCESS,
        "warning": EXIT_WARNING,
        "failed": EXIT_FAILED,
    }[status]

    document_records: list[dict] = []
    for document in active_documents:
        entry_statuses = [
            item.section.status
            for item in rendered
            if item.section.document_id == document.document_id
        ]
        parse_counts = parse_coverage.by_document.get(
            document.document_id,
            {"total": 0, "completed": 0, "failed": 0, "missing": 0},
        )
        document_has_warning = any(
            issue.get("document_id") == document.document_id
            and issue.get("severity") in {"warning", "error"}
            for issue in issue_records
        )
        document_ingest_failed = any(
            issue.get("document_id") == document.document_id
            and issue.get("code") == "DOCUMENT_INGEST_FAILED"
            for issue in issue_records
        )
        document_status = (
            "failed"
            if "failed" in entry_statuses or document_ingest_failed
            else "warning"
            if "warning" in entry_statuses
            or parse_counts["failed"]
            or parse_counts["missing"]
            or document_has_warning
            or document.support_level == "best-effort"
            else "success"
        )
        document_records.append(
            {
                **document.metadata(),
                "status": document_status,
                "parse_total": parse_counts["total"],
                "parse_completed": parse_counts["completed"],
                "parse_failed": parse_counts["failed"],
                "parse_missing": parse_counts["missing"],
            }
        )

    stats = {
        "entry_count": len(records),
        "family_count": len(
            {record.get("family") for record in records if record.get("family")}
        ),
        "status_success": success_count,
        "status_warning": warning_count,
        "status_failed": failed_count,
        "parse_total": parse_coverage.total,
        "parse_completed": parse_coverage.completed,
        "parse_failed": parse_coverage.failed,
        "parse_missing": parse_coverage.missing,
        "issue_count": len(issue_records),
        "issues_by_severity": issues_by_severity,
        "issues_by_code": issues_by_code,
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
    if config.quality_gate.baseline is not None:
        try:
            acceptance = run_quality_gate(
                config.output.corpus_dir,
                config.quality_gate.baseline,
                issues=issue_records,
            )
        except CorpusQualityError as exc:
            raise ConfigError(f"invalid Corpus quality gate: {exc}") from exc
        log(
            "Corpus quality gate: "
            f"{acceptance['status']}; baseline={config.quality_gate.baseline}"
        )
        if acceptance["status"] == "failed":
            status = "failed"
            exit_code = EXIT_FAILED
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
        elapsed_seconds=time.monotonic() - started_at,
    )


def run_build(
    config_path: Path | str,
    log: Callable[[str], None] = print,
    *,
    allow_runtime_install: bool = False,
    provider: DocumentProvider | None = None,
    on_progress: ParseProgressCallback | None = None,
) -> BuildResult:
    """Run inspect, resumable parsing, and reconstruction in one command."""

    config_path = Path(config_path)
    started_at = time.monotonic()
    config = load_config(config_path)
    release, documents = _resolve_documents(config)
    document_records = [document.metadata() for document in documents]
    log(f"LS-DYNA Manual to Markdown {__version__}")
    log(f"build release {release}: {len(documents)} document(s)")

    inspect_started = time.monotonic()
    log("[1/3] inspect: generate PageMap / SectionMap")
    run_inspection(config_path, log=log)
    inspect_seconds = time.monotonic() - inspect_started
    log(f"      inspect elapsed={inspect_seconds:.1f}s")

    log("[2/3] parse: resume or generate PageIR")
    parsing = run_parsing(
        config_path,
        log=log,
        provider=provider,
        on_progress=on_progress,
        allow_runtime_install=allow_runtime_install,
    )
    parse_seconds = parsing.elapsed_seconds
    log(f"      parse elapsed={parse_seconds:.1f}s")
    if parsing.exit_code == EXIT_PAUSED:
        log(
            "build paused during parsing; re-run the same command to resume "
            f"from {parsing.checkpoint_path}"
        )
        return BuildResult(
            exit_code=EXIT_PAUSED,
            status=parsing.status,
            release=release,
            documents=document_records,
            total_pages=parsing.total_pages,
            completed_pages=parsing.completed_pages,
            failed_pages=parsing.failed_pages,
            stage_durations={"inspect": inspect_seconds, "parse": parse_seconds},
            total_duration_seconds=time.monotonic() - started_at,
        )

    log("[3/3] reconstruct: write Markdown, manifest, and reports")
    reconstruction = run_reconstruction(config_path, log=log)
    reconstruct_seconds = reconstruction.elapsed_seconds
    log(f"      reconstruct elapsed={reconstruct_seconds:.1f}s")
    status = reconstruction.status
    exit_code = reconstruction.exit_code
    if parsing.exit_code == EXIT_WARNING and exit_code == EXIT_SUCCESS:
        status = "warning"
        exit_code = EXIT_WARNING

    issues: list[dict] = []
    issues_path = reconstruction.reports_path / "issues.jsonl"
    if issues_path.is_file():
        for line in issues_path.read_text(encoding="utf-8").splitlines():
            try:
                issue = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(issue, dict):
                issues.append(issue)

    # Reconstruction is the authoritative source for per-document status and
    # parse coverage.  Keep the paused-build response lightweight, but expose
    # the final report contract on completed builds as well.
    final_documents = document_records
    summary_path = reconstruction.reports_path / "summary.json"
    if summary_path.is_file():
        try:
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary_payload = None
        if isinstance(summary_payload, dict) and isinstance(
            summary_payload.get("documents"), list
        ):
            final_documents = [
                item
                for item in summary_payload["documents"]
                if isinstance(item, dict)
            ]

    log(
        f"build status: {status}; pages={parsing.completed_pages}/"
        f"{parsing.total_pages}, sections={reconstruction.section_count}, "
        f"manifest={reconstruction.manifest_path}"
    )
    total_seconds = time.monotonic() - started_at
    log(
        "build elapsed: "
        f"inspect={inspect_seconds:.1f}s parse={parse_seconds:.1f}s "
        f"reconstruct={reconstruct_seconds:.1f}s total={total_seconds:.1f}s"
    )
    return BuildResult(
        exit_code=exit_code,
        status=status,
        release=release,
        documents=final_documents,
        issues=issues,
        total_pages=parsing.total_pages,
        completed_pages=parsing.completed_pages,
        failed_pages=parsing.failed_pages,
        section_count=reconstruction.section_count,
        manifest_path=reconstruction.manifest_path,
        reports_path=reconstruction.reports_path,
        stage_durations={
            "inspect": inspect_seconds,
            "parse": parse_seconds,
            "reconstruct": reconstruct_seconds,
        },
        total_duration_seconds=total_seconds,
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
        "section_id": None,
        "severity": severity,
        "code": code,
        "message": message,
    }
