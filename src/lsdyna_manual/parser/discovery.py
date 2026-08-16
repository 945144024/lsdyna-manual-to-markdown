"""Manual volume discovery and filename-based metadata extraction.

Official file naming is not uniform across releases: R13/R14 files use
``LS-DYNA_Manual_Volume_I_R13.pdf`` while R15+ files use
``LS-DYNA_Manual_Vol_I_R17.pdf``, and older releases mixed case. The
pattern below tolerates both spellings and case differences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ROMAN_TO_VOLUME = {"i": 1, "ii": 2, "iii": 3}

MANUAL_FILENAME_RE = re.compile(
    r"^ls-dyna_manual_(?:volume|vol)_(?P<volume>i{1,3})_r(?P<release>\d+(?:\.\d+)?)\.pdf$",
    re.IGNORECASE,
)


class DiscoveryError(Exception):
    """Raised when manual discovery cannot produce an unambiguous result."""


@dataclass(frozen=True)
class ManualFileInfo:
    volume: int
    release: str
    path: Path


def parse_manual_filename(path: Path) -> ManualFileInfo | None:
    """Derive volume number and release from an official manual filename.

    Returns None when the filename does not follow the Keyword Manual
    naming pattern; non-keyword manuals (e.g. Theory Manual) do not match.
    """
    match = MANUAL_FILENAME_RE.match(path.name)
    if match is None:
        return None
    return ManualFileInfo(
        volume=_ROMAN_TO_VOLUME[match.group("volume").lower()],
        release=f"R{match.group('release')}",
        path=path,
    )


def discover_volumes(
    manuals_dir: Path,
    expected_release: str | None = None,
) -> list[ManualFileInfo]:
    """Find Keyword Manual volumes in a directory.

    When expected_release is given, only files of that release are
    considered. Raises DiscoveryError when no matching file exists, or when
    the directory contains ambiguous candidates.
    """
    candidates: dict[int, list[ManualFileInfo]] = {}
    for pdf in sorted(manuals_dir.glob("*.pdf")):
        info = parse_manual_filename(pdf)
        if info is not None:
            candidates.setdefault(info.volume, []).append(info)

    if expected_release is not None:
        expected = expected_release.upper()
        candidates = {
            volume: [info for info in infos if info.release == expected]
            for volume, infos in candidates.items()
        }
        candidates = {volume: infos for volume, infos in candidates.items() if infos}
        if not candidates:
            raise DiscoveryError(
                f"no Keyword Manual volumes for release {expected} in {manuals_dir}"
            )
    else:
        for volume, infos in candidates.items():
            releases = {info.release for info in infos}
            if len(releases) > 1:
                raise DiscoveryError(
                    f"multiple releases for volume {volume} in {manuals_dir}: "
                    f"{sorted(releases)}; set manual.release"
                )

    result: list[ManualFileInfo] = []
    for volume in sorted(candidates):
        infos = candidates[volume]
        if len(infos) > 1:
            raise DiscoveryError(
                f"multiple candidates for volume {volume}: "
                f"{[info.path.name for info in infos]}"
            )
        result.append(infos[0])
    return result
