"""Manual document discovery and filename-based metadata extraction.

Official file naming is not uniform across releases: R13/R14 files use
``LS-DYNA_Manual_Volume_I_R13.pdf`` while R15+ files use
``LS-DYNA_Manual_Vol_I_R17.pdf``, and older releases mixed case. The
patterns below tolerate both spellings and case differences.
"""

from __future__ import annotations

import re
from pathlib import Path

from lsdyna_manual.documents import (
    MANUAL_TYPE_KEYWORD,
    MANUAL_TYPE_THEORY,
    ManualDocument,
    keyword_document_id,
    normalize_release,
)

_ROMAN_TO_VOLUME = {"i": 1, "ii": 2, "iii": 3}

MANUAL_FILENAME_RE = re.compile(
    r"^ls-dyna_manual_(?:volume|vol)_(?P<volume>i{1,3})_r(?P<release>\d+(?:\.\d+)?)\.pdf$",
    re.IGNORECASE,
)
THEORY_FILENAME_RE = re.compile(
    r"^ls-dyna_manual_theory_r(?P<release>\d+(?:\.\d+)?)\.pdf$",
    re.IGNORECASE,
)


class DiscoveryError(Exception):
    """Raised when manual discovery cannot produce an unambiguous result."""


def _parse_keyword_filename(path: Path) -> ManualDocument | None:
    """Derive Keyword Manual metadata from an official filename."""
    match = MANUAL_FILENAME_RE.match(path.name)
    if match is None:
        return None
    volume = _ROMAN_TO_VOLUME[match.group("volume").lower()]
    return ManualDocument(
        document_id=keyword_document_id(volume),
        manual_type=MANUAL_TYPE_KEYWORD,
        volume=volume,
        release=f"R{match.group('release')}",
        path=path,
    )


def parse_document_filename(path: Path) -> ManualDocument | None:
    """Parse an official Keyword or Theory Manual filename."""
    keyword = _parse_keyword_filename(path)
    if keyword is not None:
        return keyword
    match = THEORY_FILENAME_RE.match(path.name)
    if match is None:
        return None
    return ManualDocument(
        document_id="theory",
        manual_type=MANUAL_TYPE_THEORY,
        release=f"R{match.group('release')}",
        path=path,
    )


def discover_documents(
    manuals_dir: Path,
    expected_release: str | None = None,
) -> list[ManualDocument]:
    """Find an unambiguous same-release set of Manual documents."""
    documents = [
        document
        for pdf in sorted(manuals_dir.glob("*.pdf"))
        if (document := parse_document_filename(pdf)) is not None
    ]

    if expected_release is not None:
        expected = normalize_release(expected_release)
        documents = [document for document in documents if document.release == expected]
        if not documents:
            raise DiscoveryError(
                f"no LS-DYNA Manual documents for release {expected} in {manuals_dir}"
            )
    elif documents:
        releases = {document.release for document in documents}
        if len(releases) > 1:
            raise DiscoveryError(
                f"multiple releases in {manuals_dir}: {sorted(releases)}; "
                "set manual.release"
            )

    candidates: dict[str, list[ManualDocument]] = {}
    for document in documents:
        candidates.setdefault(document.document_id, []).append(document)

    result: list[ManualDocument] = []
    for document_id in sorted(candidates):
        matches = candidates[document_id]
        if len(matches) > 1:
            raise DiscoveryError(
                f"multiple candidates for {document_id}: "
                f"{[document.path.name for document in matches]}"
            )
        result.append(matches[0])
    return result
