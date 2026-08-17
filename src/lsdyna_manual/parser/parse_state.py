"""Checkpoint and cache for page parsing runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class PageParseState:
    document_id: str
    volume: int | None
    pdf_page: int
    status: str
    provider: str | None = None
    model: str | None = None
    source_sha256: str | None = None
    source_file: str | None = None
    semantic_config_hash: str | None = None
    adapter_identity: str | None = None
    pageir_schema_version: str | None = None
    job_id: str | None = None
    batch_id: int | None = None
    raw_json_path: str | None = None
    raw_markdown_path: str | None = None
    pageir_path: str | None = None
    error: str | None = None
    updated_at: str | None = None

    @staticmethod
    def key(document_id: str, pdf_page: int) -> str:
        return f"{document_id}:{pdf_page}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key(self.document_id, self.pdf_page),
            "document_id": self.document_id,
            "volume": self.volume,
            "pdf_page": self.pdf_page,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "source_sha256": self.source_sha256,
            "source_file": self.source_file,
            "semantic_config_hash": self.semantic_config_hash,
            "adapter_identity": self.adapter_identity,
            "pageir_schema_version": self.pageir_schema_version,
            "job_id": self.job_id,
            "batch_id": self.batch_id,
            "raw_json_path": self.raw_json_path,
            "raw_markdown_path": self.raw_markdown_path,
            "pageir_path": self.pageir_path,
            "error": self.error,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageParseState":
        volume_value = data.get("volume")
        volume = int(volume_value) if volume_value is not None else None
        return cls(
            document_id=str(data["document_id"]),
            volume=volume,
            pdf_page=int(data["pdf_page"]),
            status=str(data["status"]),
            provider=data.get("provider"),
            model=data.get("model"),
            source_sha256=data.get("source_sha256"),
            source_file=data.get("source_file"),
            semantic_config_hash=data.get("semantic_config_hash"),
            adapter_identity=data.get("adapter_identity"),
            pageir_schema_version=data.get("pageir_schema_version"),
            job_id=data.get("job_id"),
            batch_id=data.get("batch_id"),
            raw_json_path=data.get("raw_json_path"),
            raw_markdown_path=data.get("raw_markdown_path"),
            pageir_path=data.get("pageir_path"),
            error=data.get("error"),
            updated_at=data.get("updated_at"),
        )


@dataclass
class BatchParseState:
    document_id: str
    plan_batch_id: int
    pdf_pages: tuple[int, ...]
    status: str
    provider: str | None = None
    model: str | None = None
    source_sha256: str | None = None
    semantic_config_hash: str | None = None
    job_id: str | None = None
    error_category: str | None = None
    business_code: int | str | None = None
    error: str | None = None
    updated_at: str | None = None

    @staticmethod
    def key(document_id: str, plan_batch_id: int, pdf_pages: tuple[int, ...]) -> str:
        pages = ",".join(str(page) for page in pdf_pages)
        return f"{document_id}:{plan_batch_id}:{pages}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key(self.document_id, self.plan_batch_id, self.pdf_pages),
            "document_id": self.document_id,
            "plan_batch_id": self.plan_batch_id,
            "pdf_pages": list(self.pdf_pages),
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "source_sha256": self.source_sha256,
            "semantic_config_hash": self.semantic_config_hash,
            "job_id": self.job_id,
            "error_category": self.error_category,
            "business_code": self.business_code,
            "error": self.error,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchParseState":
        return cls(
            document_id=str(data["document_id"]),
            plan_batch_id=int(data["plan_batch_id"]),
            pdf_pages=tuple(int(page) for page in data.get("pdf_pages", [])),
            status=str(data["status"]),
            provider=data.get("provider"),
            model=data.get("model"),
            source_sha256=data.get("source_sha256"),
            semantic_config_hash=data.get("semantic_config_hash"),
            job_id=data.get("job_id"),
            error_category=data.get("error_category"),
            business_code=data.get("business_code"),
            error=data.get("error"),
            updated_at=data.get("updated_at"),
        )


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class ParseStateStore:
    """Small JSON-file-backed checkpoint store for parse runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._states: dict[str, PageParseState] = {}
        self._batches: dict[str, BatchParseState] = {}
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw.get("pages", []):
                state = PageParseState.from_dict(item)
                self._states[state.key(state.document_id, state.pdf_page)] = state
            for item in raw.get("batches", []):
                state = BatchParseState.from_dict(item)
                key = state.key(
                    state.document_id, state.plan_batch_id, state.pdf_pages
                )
                self._batches[key] = state

    def get(
        self, document_id: str, pdf_page: int
    ) -> PageParseState | None:
        return self._states.get(PageParseState.key(document_id, pdf_page))

    def set(self, state: PageParseState) -> None:
        state.updated_at = utc_now_iso()
        self._states[state.key(state.document_id, state.pdf_page)] = state
        self.save()

    def get_batch(
        self,
        document_id: str,
        plan_batch_id: int,
        pdf_pages: list[int] | tuple[int, ...],
    ) -> BatchParseState | None:
        pages = tuple(pdf_pages)
        return self._batches.get(
            BatchParseState.key(document_id, plan_batch_id, pages)
        )

    def set_batch(self, state: BatchParseState) -> None:
        state.updated_at = utc_now_iso()
        key = state.key(state.document_id, state.plan_batch_id, state.pdf_pages)
        self._batches[key] = state
        self.save()

    def is_raw_done(
        self,
        document_id: str,
        pdf_page: int,
        *,
        provider: str,
        model: str,
        source_sha256: str,
        semantic_config_hash: str,
    ) -> bool:
        state = self.get(document_id, pdf_page)
        return bool(
            state is not None
            and state.status in {"raw_done", "done"}
            and state.provider == provider
            and state.model == model
            and state.source_sha256 == source_sha256
            and state.semantic_config_hash == semantic_config_hash
        )

    def is_done(
        self,
        document_id: str,
        pdf_page: int,
        *,
        provider: str,
        model: str,
        source_sha256: str,
        semantic_config_hash: str,
        adapter_identity: str,
        pageir_schema_version: str,
    ) -> bool:
        state = self.get(document_id, pdf_page)
        return bool(
            state is not None
            and state.status == "done"
            and state.provider == provider
            and state.model == model
            and state.source_sha256 == source_sha256
            and state.semantic_config_hash == semantic_config_hash
            and state.adapter_identity == adapter_identity
            and state.pageir_schema_version == pageir_schema_version
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "0.2",
            "pages": [state.to_dict() for state in self._states.values()],
            "batches": [state.to_dict() for state in self._batches.values()],
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)
