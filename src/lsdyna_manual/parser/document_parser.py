"""Document-level parser orchestration for the Reliable PageIR stage.

This module currently performs the raw-capture phase only: it turns a
page-centric ParsePlan into transport batches, calls a Provider, persists
provider raw artifacts, and updates per-page checkpoint state. Adapter
and PageIR generation are added after raw output has been inspected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lsdyna_manual.parser.adapters.base import PageAdapter
from lsdyna_manual.parser.ingest import sha256_of
from lsdyna_manual.parser.page_ir import SCHEMA_VERSION, save_page_ir, validate_page_ir
from lsdyna_manual.parser.parse_plan import ParsePlan
from lsdyna_manual.parser.parse_state import PageParseState, ParseStateStore
from lsdyna_manual.parser.raw_store import PageRawArtifact, store_paddle_bundle
from lsdyna_manual.providers.base import DocumentProvider, ProviderError


@dataclass
class ParseRunPageResult:
    volume: int
    pdf_page: int
    status: str
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
    ) -> None:
        self.provider = provider
        self.state_store = state_store
        self.raw_root = raw_root
        self.pageir_root = pageir_root or (raw_root.parent / "pageir")

    def parse_raw_for_volume(
        self,
        plan: ParsePlan,
        source_pdf_path: Path,
        volume: int,
    ) -> list[ParseRunPageResult]:
        """Capture provider raw artifacts for one volume.

        Batches whose pages are already marked raw_done are skipped.
        A failed batch marks every page in that batch as failed and then
        continues with the next batch.
        """
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
        for batch in plan.batches_for_volume(volume):
            pending_pages = [
                pdf_page
                for pdf_page in batch.pdf_pages
                if not self.state_store.is_raw_done(
                    volume,
                    pdf_page,
                    provider=provider_name,
                    model=model,
                    source_sha256=source_sha256,
                    semantic_config_hash=semantic_config_hash,
                )
            ]
            if not pending_pages:
                continue

            batch_input = self._make_batch_pdf(reader, pending_pages)
            try:
                job_result = self.provider.parse_pdf_batch(
                    batch_input,
                    volume=volume,
                    pdf_pages=pending_pages,
                )
                stored = store_paddle_bundle(
                    job_result,
                    root=self.raw_root,
                    volume=volume,
                    pdf_pages=pending_pages,
                    batch_id=batch.batch_id,
                    input_pdf_path=batch_input,
                )
            except (ProviderError, ValueError, KeyError) as exc:
                for pdf_page in pending_pages:
                    state = self.state_store.get(volume, pdf_page) or PageParseState(
                        volume=volume,
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
                            volume=volume,
                            pdf_page=pdf_page,
                            status="failed",
                            error=str(exc),
                        )
                    )
                continue

            for artifact in stored.page_artifacts:
                state = self.state_store.get(
                    artifact.volume, artifact.pdf_page
                ) or PageParseState(
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
                        volume=artifact.volume,
                        pdf_page=artifact.pdf_page,
                        status="raw_done",
                        raw_artifact=artifact,
                    )
                )

        return results

    def build_pageir_for_volume(
        self,
        plan: ParsePlan,
        adapter: PageAdapter,
        volume: int,
        source_pdf_path: Path,
    ) -> list[ParseRunPageResult]:
        """Convert already captured raw page artifacts into PageIR."""
        if not source_pdf_path.is_file():
            raise ValueError(f"source PDF not found: {source_pdf_path}")

        results: list[ParseRunPageResult] = []
        provider_name = getattr(self.provider, "provider_name", "document-provider")
        model = getattr(self.provider.config, "model", None)
        source_sha256 = sha256_of(source_pdf_path)
        source_file = source_pdf_path.name
        semantic_config_hash = self.provider.semantic_identity()
        adapter_identity = adapter.identity()

        for entry in plan.entries:
            if entry.volume != volume:
                continue
            if self.state_store.is_done(
                volume,
                entry.pdf_page,
                provider=provider_name,
                model=model,
                source_sha256=source_sha256,
                semantic_config_hash=semantic_config_hash,
                adapter_identity=adapter_identity,
                pageir_schema_version=SCHEMA_VERSION,
            ):
                continue
            state = self.state_store.get(volume, entry.pdf_page)
            if state is None or state.status != "raw_done":
                continue
            if (
                state.source_sha256 != source_sha256
                or state.semantic_config_hash != semantic_config_hash
            ):
                # Raw cache identity no longer matches; raw capture must
                # run again before this page can produce PageIR.
                continue
            if not state.raw_json_path or not Path(state.raw_json_path).is_file():
                state.status = "failed"
                state.error = "raw page artifact missing; re-run raw capture"
                self.state_store.set(state)
                results.append(
                    ParseRunPageResult(
                        volume=volume,
                        pdf_page=entry.pdf_page,
                        status="failed",
                        error=state.error,
                    )
                )
                continue

            try:
                page_ir = adapter.adapt_page(
                    Path(state.raw_json_path),
                    pdf_page=entry.pdf_page,
                    manual_page=entry.manual_page,
                )
                validation_issues = validate_page_ir(
                    page_ir, expected_pdf_page=entry.pdf_page
                )
                page_ir.issues.extend(validation_issues)
                pageir_path = (
                    self.pageir_root
                    / f"volume-{volume}"
                    / f"page_{entry.pdf_page:06d}.json"
                )
                save_page_ir(page_ir, pageir_path)
            except Exception as exc:
                state.status = "failed"
                state.error = str(exc)
                self.state_store.set(state)
                results.append(
                    ParseRunPageResult(
                        volume=volume,
                        pdf_page=entry.pdf_page,
                        status="failed",
                        error=str(exc),
                    )
                )
                continue

            state.status = "done"
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
                    volume=volume,
                    pdf_page=entry.pdf_page,
                    status="done",
                )
            )

        return results

    def _make_batch_pdf(self, reader, pdf_pages: list[int]) -> Path:
        from pypdf import PdfWriter

        writer = PdfWriter()
        for pdf_page in pdf_pages:
            writer.add_page(reader.pages[pdf_page - 1])
        transport_dir = self.raw_root / ".transport"
        transport_dir.mkdir(parents=True, exist_ok=True)
        output = transport_dir / (
            f"batch_{pdf_pages[0]:06d}_{pdf_pages[-1]:06d}.pdf"
        )
        with open(output, "wb") as fh:
            writer.write(fh)
        return output
