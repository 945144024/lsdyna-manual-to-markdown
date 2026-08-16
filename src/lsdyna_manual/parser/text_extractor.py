"""Text-layer extraction abstraction for document inspection.

Inspection needs the TOC, running headers, footers, and text layout
positions - not high-quality body Markdown. `pdftotext -layout` is the
default extractor; segmentation logic depends only on this interface.
"""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path


class TextExtractor(ABC):
    """Extracts one layout-preserving text page per PDF page."""

    @abstractmethod
    def extract_pages(self, pdf_path: Path) -> list[str]:
        """Return page texts in PDF order, indexed from 0 (pdf page 1)."""


class PopplerLayoutExtractor(TextExtractor):
    """Extracts page texts via `pdftotext -layout` (poppler-utils)."""

    def extract_pages(self, pdf_path: Path) -> list[str]:
        binary = shutil.which("pdftotext")
        if binary is None:
            raise RuntimeError(
                "pdftotext not found; install poppler-utils "
                "(see README for environment requirements)"
            )
        result = subprocess.run(
            [binary, "-layout", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            check=True,
        )
        pages = result.stdout.split("\f")
        if pages and pages[-1] == "":
            pages = pages[:-1]
        return pages
