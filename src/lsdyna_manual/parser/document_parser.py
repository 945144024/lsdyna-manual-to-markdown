"""Document-level parser orchestration for the Reliable PageIR stage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lsdyna_manual.parser.adapters.base import PageAdapter
from lsdyna_manual.parser.ingest import sha256_of
from lsdyna_manual.parser.page_ir import (
    SCHEMA_VERSION,
    ParseIssue,
    save_page_ir,
    validate_page_ir,
)
from lsdyna_manual.parser.parse_plan import ParsePlan
from lsdyna_manual.parser.parse_state import (
    BatchParseState,
    PageParseState,
    ParseStateStore,
)
from lsdyna_manual.parser.progress import ParseProgressCallback, ParseProgressEvent
from lsdyna_manual.parser.raw_store import PageRawArtifact, store_paddle_bundle
from lsdyna_manual.providers.base import (
    DocumentProvider,
    ProviderError,
    ProviderQuotaError,
)


@dataclass
class ParseRunPageResult:
    document_id: str
    pdf_page: int
    status: str
    volume: int | None = None
    raw_artifact: PageRawArtifact | None = None
    error: str | None = None


class DocumentParser:
    def __init__(
        self,
        provider: DocumentProvider,
        *,
        state_store: ParseStateStore,
        raw_root: Path,
        pageir_root: Path | None = None,
        on_progress: ParseProgressCallback | None = None,
    ) -> None:
        self.provider = provider
        self.state_store = state_store
        self.raw_root = raw_root
        self.pageir_root = pageir_root or (raw_root.parent / "pageir")
        self.on_progress = on_progress

    def _emit(self, event: ParseProgressEvent) -> None:
        if self.on_progress is not None:
            self.on_progress(event)

    @staticmethod
    def _source_page_is_blank(reader, pdf_page: int) -> bool:
        """Return true only when a PDF page has no visible content operators."""

        from pypdf.generic import ContentStream

        page = reader.pages[pdf_page - 1]
        if page.get("/Annots"):
            return False
        contents = page.get_contents()
        if contents is None:
            return True
        visible_operators = {
            b"Tj",
            b"TJ",
            b"'",
            b'"',
            b"Do",
            b"S",
            b"s",
            b"f",
            b"F",
            b"f*",
            b"B",
            b"B*",
            b"b",
            b"b*",
            b"sh",
        }
        stream = ContentStream(contents, reader)
        return not any(operator in visible_operators for _, operator in stream.operations)

    @staticmethod
    def _sections_for_pages(
        plan: ParsePlan, document_id: str, pdf_pages: list[int]
    ) -> tuple[str, ...]:
        section_ids: set[str] = set()
        for pdf_page in pdf_pages:
            entry = plan.entry_for(document_id, pdf_page)
            if entry is not None:
                section_ids.update(entry.candidate_sections)
        return tuple(sorted(section_ids))

    def _raw_state_is_valid(
        self,
        document_id: str,
        pdf_page: int,
        *,
        provider: str,
        model: str,
        source_sha256: str,
        semantic_config_hash: str,
    ) -> bool:
        if not self.state_store.is_raw_done(
            document_id,
            pdf_page,
            provider=provider,
            model=model,
            source_sha256=source_sha256,
            semantic_config_hash=semantic_config_hash,
        ):
            return False
        state = self.state_store.get(document_id, pdf_page)
        if state is None or not state.raw_json_path:
            return False
        raw_path = Path(state.raw_json_path)
        if not raw_path.is_file():
            return False
        try:
            record = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(
            record.get("document_id") == document_id
            and record.get("pdf_page") == pdf_page
            and record.get("provider") == provider
            and record.get("model") == model
        )

    def _pageir_state_is_valid(
        self,
        document_id: str,
        pdf_page: int,
        *,
        provider: str,
        model: str,
        source_sha256: str,
        semantic_config_hash: str,
        adapter_identity: str,
    ) -> bool:
        if not self.state_store.is_done(
            document_id,
            pdf_page,
            provider=provider,
            model=model,
            source_sha256=source_sha256,
            semantic_config_hash=semantic_config_hash,
            adapter_identity=adapter_identity,
            pageir_schema_version=SCHEMA_VERSION,
        ):
            return False
        state = self.state_store.get(document_id, pdf_page)
        if state is None or not state.pageir_path:
            return False
        pageir_path = Path(state.pageir_path)
        if not pageir_path.is_file():
            return False
        try:
            payload = json.loads(pageir_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(
            payload.get("document_id") == document_id
            and payload.get("pdf_page") == pdf_page
        )

    def cached_raw_pages(
        self,
        plan: ParsePlan,
        source_pdf_path: Path,
        *,
        document_id: str,
    ) -> set[int]:
        """Return locally validated raw checkpoint pages for progress resume."""
        provider = getattr(self.provider, "provider_name", "document-provider")
        model = getattr(self.provider.config, "model", None)
        source_sha256 = sha256_of(source_pdf_path)
        semantic_config_hash = self.provider.semantic_identity()
        return {
            entry.pdf_page
            for entry in plan.entries
            if entry.document_id == document_id
            and self._raw_state_is_valid(
                document_id,
                entry.pdf_page,
                provider=provider,
                model=model,
                source_sha256=source_sha256,
                semantic_config_hash=semantic_config_hash,
            )
        }

    def parse_raw_for_document(
        self,
        plan: ParsePlan,
        source_pdf_path: Path,
        *,
        document_id: str,
    ) -> list[ParseRunPageResult]:
        """Capture provider raw artifacts for one source document."""
        results: list[ParseRunPageResult] = []
        provider_name = getattr(self.provider, "provider_name", "document-provider")
        model = getattr(self.provider.config, "model", None)
        if not source_pdf_path.is_file():
            raise ValueError(f"source PDF not found: {source_pdf_path}")
        source_sha256 = sha256_of(source_pdf_path)
        source_file = source_pdf_path.name
        semantic_config_hash = self.provider.semantic_identity()

        from pypdf import PdfReader

        reader = PdfReader(str(source_pdf_path))
        for batch in plan.batches_for_document(document_id):
            batch_volume = batch.volume
            pending_pages = [
                pdf_page
                for pdf_page in batch.pdf_pages
                if not self._raw_state_is_valid(
                    document_id,
                    pdf_page,
                    provider=provider_name,
                    model=model,
                    source_sha256=source_sha256,
                    semantic_config_hash=semantic_config_hash,
                )
            ]
            if not pending_pages:
                continue

            sections = self._sections_for_pages(
                plan, document_id, pending_pages
            )
            batch_input = self._make_batch_pdf(reader, pending_pages, document_id)
            batch_state = self.state_store.get_batch(
                document_id, batch.batch_id, pending_pages
            )
            if batch_state is None or (
                batch_state.provider != provider_name
                or batch_state.model != model
                or batch_state.source_sha256 != source_sha256
                or batch_state.semantic_config_hash != semantic_config_hash
            ):
                batch_state = BatchParseState(
                    document_id=document_id,
                    plan_batch_id=batch.batch_id,
                    pdf_pages=tuple(pending_pages),
                    status="planned",
                    provider=provider_name,
                    model=model,
                    source_sha256=source_sha256,
                    semantic_config_hash=semantic_config_hash,
                )
            force_resubmit = any(
                (state := self.state_store.get(document_id, pdf_page)) is not None
                and state.status == "parse_empty"
                for pdf_page in pending_pages
            )
            resume_job_id = None if force_resubmit else batch_state.job_id
            if resume_job_id:
                batch_state.status = "resuming"
            else:
                batch_state.status = "submitting"
            batch_state.error_category = None
            batch_state.business_code = None
            batch_state.error = None
            self.state_store.set_batch(batch_state)
            self._emit(
                ParseProgressEvent(
                    phase=batch_state.status,
                    document_id=document_id,
                    batch_id=batch.batch_id,
                    pdf_pages=tuple(pending_pages),
                    sections=sections,
                    job_id=resume_job_id,
                )
            )

            def provider_progress(phase: str, details: dict) -> None:
                batch_state.status = phase
                job_id = details.get("job_id")
                if job_id:
                    batch_state.job_id = str(job_id)
                self.state_store.set_batch(batch_state)
                self._emit(
                    ParseProgressEvent(
                        phase=phase,
                        document_id=document_id,
                        batch_id=batch.batch_id,
                        pdf_pages=tuple(pending_pages),
                        sections=sections,
                        job_id=batch_state.job_id,
                    )
                )

            try:
                job_result = self.provider.parse_pdf_batch(
                    batch_input,
                    document_id=document_id,
                    volume=batch_volume,
                    pdf_pages=pending_pages,
                    resume_job_id=resume_job_id,
                    on_progress=provider_progress,
                )
                stored = store_paddle_bundle(
                    job_result,
                    root=self.raw_root,
                    document_id=document_id,
                    volume=batch_volume,
                    pdf_pages=pending_pages,
                    batch_id=batch.batch_id,
                    input_pdf_path=batch_input,
                )
            except ProviderQuotaError as exc:
                batch_state.status = "paused_quota"
                batch_state.job_id = exc.job_id or batch_state.job_id
                batch_state.error_category = exc.category
                batch_state.business_code = exc.business_code
                batch_state.error = str(exc)
                self.state_store.set_batch(batch_state)
                for pdf_page in pending_pages:
                    state = self.state_store.get(
                        document_id, pdf_page
                    ) or PageParseState(
                        document_id=document_id,
                        volume=batch_volume,
                        pdf_page=pdf_page,
                        status="paused_quota",
                    )
                    state.status = "paused_quota"
                    state.provider = provider_name
                    state.model = model
                    state.source_sha256 = source_sha256
                    state.source_file = source_file
                    state.semantic_config_hash = semantic_config_hash
                    state.job_id = batch_state.job_id
                    state.batch_id = batch.batch_id
                    state.error = str(exc)
                    self.state_store.set(state)
                self._emit(
                    ParseProgressEvent(
                        phase="paused_quota",
                        document_id=document_id,
                        batch_id=batch.batch_id,
                        pdf_pages=tuple(pending_pages),
                        sections=sections,
                        job_id=batch_state.job_id,
                        message=str(exc),
                    )
                )
                raise
            except (ProviderError, ValueError, KeyError) as exc:
                category = getattr(exc, "category", "local_error")
                batch_state.status = "failed"
                batch_state.job_id = (
                    getattr(exc, "job_id", None) or batch_state.job_id
                )
                batch_state.error_category = category
                batch_state.business_code = getattr(
                    exc, "business_code", None
                )
                batch_state.error = str(exc)
                self.state_store.set_batch(batch_state)
                for pdf_page in pending_pages:
                    state = self.state_store.get(
                        document_id, pdf_page
                    ) or PageParseState(
                        document_id=document_id,
                        volume=batch_volume,
                        pdf_page=pdf_page,
                        status="failed",
                    )
                    state.status = "failed"
                    state.provider = provider_name
                    state.model = model
                    state.source_sha256 = source_sha256
                    state.source_file = source_file
                    state.semantic_config_hash = semantic_config_hash
                    state.error = str(exc)
                    self.state_store.set(state)
                    results.append(
                        ParseRunPageResult(
                            document_id=document_id,
                            volume=batch_volume,
                            pdf_page=pdf_page,
                            status="failed",
                            error=str(exc),
                        )
                    )
                self._emit(
                    ParseProgressEvent(
                        phase="failed",
                        document_id=document_id,
                        batch_id=batch.batch_id,
                        pdf_pages=tuple(pending_pages),
                        sections=sections,
                        job_id=batch_state.job_id,
                        message=str(exc),
                    )
                )
                if category == "auth":
                    raise
                continue

            batch_state.status = "raw_saved"
            batch_state.job_id = job_result.job_id
            batch_state.error_category = None
            batch_state.business_code = None
            batch_state.error = None
            self.state_store.set_batch(batch_state)
            for artifact in stored.page_artifacts:
                state = self.state_store.get(
                    artifact.document_id, artifact.pdf_page
                ) or PageParseState(
                    document_id=artifact.document_id,
                    volume=artifact.volume,
                    pdf_page=artifact.pdf_page,
                    status="raw_done",
                )
                state.status = "raw_done"
                state.provider = job_result.provider
                state.model = job_result.model
                state.source_sha256 = source_sha256
                state.source_file = source_file
                state.semantic_config_hash = semantic_config_hash
                state.job_id = job_result.job_id
                state.batch_id = batch.batch_id
                state.raw_json_path = str(artifact.json_path)
                state.raw_markdown_path = str(artifact.markdown_path)
                state.error = None
                self.state_store.set(state)
                results.append(
                    ParseRunPageResult(
                        document_id=artifact.document_id,
                        volume=artifact.volume,
                        pdf_page=artifact.pdf_page,
                        status="raw_done",
                        raw_artifact=artifact,
                    )
                )
            self._emit(
                ParseProgressEvent(
                    phase="raw_done",
                    document_id=document_id,
                    batch_id=batch.batch_id,
                    pdf_pages=tuple(pending_pages),
                    sections=sections,
                    completed_delta=len(stored.page_artifacts),
                    job_id=job_result.job_id,
                )
            )

        return results

    def build_pageir_for_document(
        self,
        plan: ParsePlan,
        adapter: PageAdapter,
        *,
        document_id: str,
        source_pdf_path: Path,
    ) -> list[ParseRunPageResult]:
        """Convert captured raw artifacts into document-identified PageIR."""
        if not source_pdf_path.is_file():
            raise ValueError(f"source PDF not found: {source_pdf_path}")

        results: list[ParseRunPageResult] = []
        provider_name = getattr(self.provider, "provider_name", "document-provider")
        model = getattr(self.provider.config, "model", None)
        source_sha256 = sha256_of(source_pdf_path)
        source_file = source_pdf_path.name
        semantic_config_hash = self.provider.semantic_identity()
        adapter_identity = adapter.identity()

        from pypdf import PdfReader

        reader = PdfReader(str(source_pdf_path))

        for entry in plan.entries:
            if entry.document_id != document_id:
                continue
            entry_volume = entry.volume
            if self._pageir_state_is_valid(
                document_id,
                entry.pdf_page,
                provider=provider_name,
                model=model,
                source_sha256=source_sha256,
                semantic_config_hash=semantic_config_hash,
                adapter_identity=adapter_identity,
            ):
                continue
            state = self.state_store.get(document_id, entry.pdf_page)
            if state is None or state.status not in {"raw_done", "done"}:
                continue
            if (
                state.source_sha256 != source_sha256
                or state.semantic_config_hash != semantic_config_hash
            ):
                continue
            if not self._raw_state_is_valid(
                document_id,
                entry.pdf_page,
                provider=provider_name,
                model=model,
                source_sha256=source_sha256,
                semantic_config_hash=semantic_config_hash,
            ):
                state.status = "failed"
                state.error = "raw page artifact missing or invalid; re-run raw capture"
                self.state_store.set(state)
                results.append(
                    ParseRunPageResult(
                        document_id=document_id,
                        volume=entry_volume,
                        pdf_page=entry.pdf_page,
                        status="failed",
                        error=state.error,
                    )
                )
                continue

            self._emit(
                ParseProgressEvent(
                    phase="building_pageir",
                    document_id=document_id,
                    pdf_pages=(entry.pdf_page,),
                    sections=entry.candidate_sections,
                )
            )
            try:
                page_ir = adapter.adapt_page(
                    Path(state.raw_json_path),
                    pdf_page=entry.pdf_page,
                    manual_page=entry.manual_page,
                )
                page_ir.document_id = document_id
                if not page_ir.blocks:
                    source_is_blank = self._source_page_is_blank(
                        reader, entry.pdf_page
                    )
                    page_ir.issues = [
                        issue
                        for issue in page_ir.issues
                        if issue.code != "READING_ORDER_AMBIGUOUS"
                    ]
                    if source_is_blank:
                        page_ir.issues.append(
                            ParseIssue(
                                severity="info",
                                code="SOURCE_BLANK_PAGE",
                                message=(
                                    "source PDF page has no visible content "
                                    "operators; empty PageIR is expected"
                                ),
                            )
                        )
                    else:
                        state.empty_parse_attempts += 1
                        final_attempt = state.empty_parse_attempts >= 2
                        page_ir.issues.append(
                            ParseIssue(
                                severity="error" if final_attempt else "warning",
                                code="PAGE_PARSE_EMPTY",
                                message=(
                                    "non-blank source PDF page produced no PageIR "
                                    "blocks after retry"
                                    if final_attempt
                                    else "non-blank source PDF page produced no "
                                    "PageIR blocks; one fresh provider retry is required"
                                ),
                            )
                        )
                validation_issues = validate_page_ir(
                    page_ir,
                    expected_document_id=document_id,
                    expected_pdf_page=entry.pdf_page,
                )
                page_ir.issues.extend(validation_issues)
                pageir_path = (
                    self.pageir_root
                    / document_id
                    / f"page_{entry.pdf_page:06d}.json"
                )
                save_page_ir(page_ir, pageir_path)
            except Exception as exc:
                state.status = "failed"
                state.error = str(exc)
                self.state_store.set(state)
                results.append(
                    ParseRunPageResult(
                        document_id=document_id,
                        volume=entry_volume,
                        pdf_page=entry.pdf_page,
                        status="failed",
                        error=str(exc),
                    )
                )
                continue

            parse_empty = any(
                issue.code == "PAGE_PARSE_EMPTY" for issue in page_ir.issues
            )
            state.status = (
                "failed"
                if parse_empty and state.empty_parse_attempts >= 2
                else "parse_empty"
                if parse_empty
                else "done"
            )
            state.source_sha256 = source_sha256
            state.source_file = source_file
            state.semantic_config_hash = semantic_config_hash
            state.adapter_identity = adapter_identity
            state.pageir_schema_version = SCHEMA_VERSION
            state.pageir_path = str(pageir_path)
            state.error = None
            self.state_store.set(state)
            results.append(
                ParseRunPageResult(
                    document_id=document_id,
                    volume=entry_volume,
                    pdf_page=entry.pdf_page,
                    status=state.status,
                )
            )
            self._emit(
                ParseProgressEvent(
                    phase=(
                        "pageir_done"
                        if state.status == "done"
                        else state.status
                    ),
                    document_id=document_id,
                    pdf_pages=(entry.pdf_page,),
                    sections=entry.candidate_sections,
                )
            )

        return results

    def _make_batch_pdf(
        self,
        reader,
        pdf_pages: list[int],
        document_id: str,
    ) -> Path:
        from pypdf import PdfWriter

        writer = PdfWriter()
        for pdf_page in pdf_pages:
            writer.add_page(reader.pages[pdf_page - 1])
        transport_dir = self.raw_root / ".transport" / document_id
        transport_dir.mkdir(parents=True, exist_ok=True)
        output = transport_dir / (
            f"batch_{pdf_pages[0]:06d}_{pdf_pages[-1]:06d}.pdf"
        )
        with open(output, "wb") as fh:
            writer.write(fh)
        return output
