"""Provider raw artifact storage."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lsdyna_manual.providers.base import ProviderJobResult


@dataclass(frozen=True)
class PageRawArtifact:
    volume: int | None
    pdf_page: int
    json_path: Path
    markdown_path: Path
    source_line_index: int
    source_layout_index: int
    document_id: str


@dataclass(frozen=True)
class StoredRawBundle:
    batch_dir: Path
    raw_jsonl_path: Path
    job_metadata_path: Path
    page_map_path: Path
    page_artifacts: list[PageRawArtifact]


def _layout_results_from_jsonl(
    raw_text: str,
) -> list[tuple[int, int, dict[str, Any]]]:
    """Return (line_index, layout_index, layout_result) tuples."""
    results: list[tuple[int, int, dict[str, Any]]] = []
    for line_index, line in enumerate(raw_text.strip().splitlines()):
        if not line.strip():
            continue
        payload = json.loads(line)
        result = payload.get("result", {})
        for layout_index, layout in enumerate(
            result.get("layoutParsingResults", [])
        ):
            results.append((line_index, layout_index, layout))
    return results


def store_paddle_bundle(
    job_result: ProviderJobResult,
    *,
    root: Path,
    pdf_pages: list[int],
    batch_id: int,
    input_pdf_path: Path,
    document_id: str,
    volume: int | None,
) -> StoredRawBundle:
    """Persist a Paddle job as a batch plus page-level raw artifacts."""
    if not pdf_pages:
        raise ValueError("pdf_pages must not be empty")
    if job_result.raw_jsonl_text is None:
        raise ValueError("provider result does not contain raw JSONL text")

    layout_results = _layout_results_from_jsonl(job_result.raw_jsonl_text)
    if len(layout_results) != len(pdf_pages):
        raise ValueError(
            f"provider returned {len(layout_results)} page results for "
            f"{len(pdf_pages)} source pages"
        )

    batch_dir = (
        root
        / document_id
        / job_result.provider
        / job_result.model
        / "batches"
        / f"batch_{batch_id:04d}"
    )
    pages_dir = batch_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    raw_jsonl_path = batch_dir / "raw_result.jsonl"
    raw_jsonl_path.write_text(job_result.raw_jsonl_text, encoding="utf-8")

    shutil.copyfile(input_pdf_path, batch_dir / "input.pdf")

    job_data = dict(job_result.metadata.get("job_data", {}))
    if "resultUrl" in job_data:
        job_data["resultUrl"] = "<redacted>"
    metadata = {
        "provider": job_result.provider,
        "model": job_result.model,
        "job_id": job_result.job_id,
        "state": job_result.state,
        "document_id": document_id,
        "volume": volume,
        "pdf_pages": list(pdf_pages),
        "job_data": job_data,
    }
    job_metadata_path = batch_dir / "job.json"
    job_metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    page_artifacts: list[PageRawArtifact] = []
    page_map: list[dict[str, Any]] = []
    for index, (pdf_page, layout_origin) in enumerate(
        zip(pdf_pages, layout_results, strict=True)
    ):
        source_line_index, source_layout_index, layout_result = layout_origin
        page_stem = f"{document_id}_page_{pdf_page:06d}"
        page_record = {
            "document_id": document_id,
            "volume": volume,
            "pdf_page": pdf_page,
            "provider": job_result.provider,
            "model": job_result.model,
            "job_id": job_result.job_id,
            "batch_id": batch_id,
            "source_layout_index": index,
            "layout_result": layout_result,
        }
        json_path = pages_dir / f"{page_stem}.json"
        json_path.write_text(
            json.dumps(page_record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        markdown_text = (
            layout_result.get("markdown", {}).get("text", "")
            if isinstance(layout_result, dict)
            else ""
        )
        markdown_path = pages_dir / f"{page_stem}.md"
        markdown_path.write_text(markdown_text, encoding="utf-8")

        page_artifacts.append(
            PageRawArtifact(
                document_id=document_id,
                volume=volume,
                pdf_page=pdf_page,
                json_path=json_path,
                markdown_path=markdown_path,
                source_line_index=source_line_index,
                source_layout_index=source_layout_index,
            )
        )
        page_map.append(
            {
                "layout_index": index,
                "document_id": document_id,
                "volume": volume,
                "pdf_page": pdf_page,
                "json_path": str(json_path.relative_to(root)),
                "markdown_path": str(markdown_path.relative_to(root)),
            }
        )

    page_map_path = batch_dir / "page_map.json"
    page_map_path.write_text(
        json.dumps(page_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return StoredRawBundle(
        batch_dir=batch_dir,
        raw_jsonl_path=raw_jsonl_path,
        job_metadata_path=job_metadata_path,
        page_map_path=page_map_path,
        page_artifacts=page_artifacts,
    )
