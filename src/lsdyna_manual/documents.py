"""Manual document identity and release support policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MANUAL_TYPE_KEYWORD = "keyword"
MANUAL_TYPE_THEORY = "theory"
MANUAL_TYPES = {MANUAL_TYPE_KEYWORD, MANUAL_TYPE_THEORY}

VERIFIED_RELEASES = frozenset(f"R{release}" for release in range(12, 18))


def normalize_release(value: str) -> str:
    """Normalize a release label without restricting best-effort releases."""
    normalized = value.strip().upper()
    if not normalized.startswith("R") or len(normalized) == 1:
        raise ValueError(f"invalid release label: {value!r}")
    return normalized


def keyword_document_id(volume: int) -> str:
    if volume not in {1, 2, 3}:
        raise ValueError(f"keyword volume must be 1, 2, or 3; got {volume}")
    return f"keyword-volume-{volume}"


@dataclass(frozen=True)
class ManualDocument:
    """One source PDF participating in a single-release processing run."""

    document_id: str
    manual_type: str
    release: str
    path: Path
    volume: int | None = None

    def __post_init__(self) -> None:
        if self.manual_type not in MANUAL_TYPES:
            raise ValueError(f"unsupported manual type: {self.manual_type!r}")
        object.__setattr__(self, "release", normalize_release(self.release))

        if self.manual_type == MANUAL_TYPE_KEYWORD:
            if self.volume not in {1, 2, 3}:
                raise ValueError("keyword documents require volume 1, 2, or 3")
            expected_id = keyword_document_id(self.volume)
        else:
            if self.volume is not None:
                raise ValueError("theory documents must not define a volume")
            expected_id = "theory"

        if self.document_id != expected_id:
            raise ValueError(
                f"document_id {self.document_id!r} does not match {expected_id!r}"
            )

    @property
    def support_level(self) -> str:
        return "verified" if self.release in VERIFIED_RELEASES else "best-effort"

    @property
    def display_name(self) -> str:
        if self.manual_type == MANUAL_TYPE_THEORY:
            return "Theory Manual"
        roman = {1: "I", 2: "II", 3: "III"}[self.volume]
        return f"Keyword Manual Volume {roman}"

    def metadata(self) -> dict[str, str | int | None]:
        return {
            "document_id": self.document_id,
            "manual_type": self.manual_type,
            "release": self.release,
            "volume": self.volume,
            "source_file": self.path.name,
            "support_level": self.support_level,
        }
