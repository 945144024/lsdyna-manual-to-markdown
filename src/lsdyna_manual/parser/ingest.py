"""Per-volume ingestion: checksums and page counts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from lsdyna_manual.parser.discovery import ManualFileInfo


@dataclass(frozen=True)
class VolumeIngestInfo:
    volume: int
    release: str
    source_file: str
    pdf_page_count: int
    sha256: str


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_volume(info: ManualFileInfo) -> VolumeIngestInfo:
    """Read the page count and checksum for one Manual volume."""
    from pypdf import PdfReader

    reader = PdfReader(str(info.path))
    if reader.is_encrypted:
        raise ValueError("encrypted PDF is not supported")
    return VolumeIngestInfo(
        volume=info.volume,
        release=info.release,
        source_file=info.path.name,
        pdf_page_count=len(reader.pages),
        sha256=sha256_of(info.path),
    )
