"""Adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from lsdyna_manual.parser.page_ir import PageIR


class PageAdapter(ABC):
    @abstractmethod
    def adapt_page(
        self,
        raw_page_json_path: Path,
        *,
        pdf_page: int,
        manual_page: str | None,
    ) -> PageIR:
        """Convert one provider page artifact into Canonical PageIR."""

    @abstractmethod
    def identity(self) -> str:
        """Adapter version identity used to invalidate PageIR cache."""
