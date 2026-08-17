"""Per-document ingestion: checksums and page counts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from lsdyna_manual.documents import ManualDocument


@dataclass(frozen=True)
class DocumentIngestInfo:
    document_id: str
    manual_type: str
    volume: int | None
    release: str
    source_file: str
    pdf_page_count: int
    sha256: str
    support_level: str


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_document(document: ManualDocument) -> DocumentIngestInfo:
    """Read the page count and checksum for one Manual document."""
    from pypdf import PdfReader

    reader = PdfReader(str(document.path))
    if reader.is_encrypted:
        raise ValueError("encrypted PDF is not supported")
    return DocumentIngestInfo(
        document_id=document.document_id,
        manual_type=document.manual_type,
        volume=document.volume,
        release=document.release,
        source_file=document.path.name,
        pdf_page_count=len(reader.pages),
        sha256=sha256_of(document.path),
        support_level=document.support_level,
    )
