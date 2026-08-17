"""Provider abstractions.

A Provider owns backend transport only. It receives a transport batch
(a temporary PDF made from unique source pages) and returns a provider
job result. It has no knowledge of keywords or SectionMap semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ProviderError(Exception):
    """Raised when a document provider cannot complete a job."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_error",
        business_code: int | str | None = None,
        http_status: int | None = None,
        job_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.business_code = business_code
        self.http_status = http_status
        self.job_id = job_id


class ProviderQuotaError(ProviderError):
    """Raised when the provider reports that the account quota is exhausted."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.pop("category", None)
        super().__init__(message, category="quota_exhausted", **kwargs)


ProviderProgressCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class ProviderJobResult:
    provider: str
    model: str
    job_id: str
    state: str
    raw_jsonl_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentProvider(ABC):
    """Parse one transport batch and return provider-specific raw output."""

    @abstractmethod
    def parse_pdf_batch(
        self,
        input_pdf_path: Path,
        *,
        document_id: str,
        pdf_pages: list[int],
        volume: int | None = None,
        resume_job_id: str | None = None,
        on_progress: ProviderProgressCallback | None = None,
    ) -> ProviderJobResult:
        """Submit a PDF batch and wait for the raw provider result."""

    def semantic_identity(self) -> str:
        """Stable identity for output-affecting provider configuration.

        Transport settings such as timeout, poll interval, retry count, or
        batch size must not be part of this identity.
        """
        provider_name = getattr(self, "provider_name", self.__class__.__name__)
        model = getattr(getattr(self, "config", None), "model", "unknown-model")
        return f"{provider_name}:{model}"
