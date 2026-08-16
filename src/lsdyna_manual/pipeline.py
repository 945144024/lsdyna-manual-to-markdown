"""Build pipeline orchestration.

v0.1 implements the ingest stage only: config loading, manual volume
discovery, and per-volume metadata (sha256, page count). Parsing,
reconstruction and Markdown rendering are not implemented yet; the build
outputs report zero entries honestly instead of fabricating results.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lsdyna_manual import __version__
from lsdyna_manual.config import BuildConfig, ConfigError, load_config
from lsdyna_manual.manifest import writer
from lsdyna_manual.parser.discovery import (
    DiscoveryError,
    ManualFileInfo,
    discover_volumes,
    parse_manual_filename,
)
from lsdyna_manual.parser.ingest import VolumeIngestInfo, ingest_volume
from lsdyna_manual.parser.segmentation import (
    InspectionResult,
    inspect_volume,
    write_inspection_artifacts,
)
from lsdyna_manual.parser.text_extractor import PopplerLayoutExtractor

EXIT_SUCCESS = 0
EXIT_WARNING = 1
EXIT_FAILED = 2

ALL_VOLUMES = (1, 2, 3)


@dataclass
class BuildResult:
    exit_code: int
    status: str
    release: str | None = None
    volumes: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)


def run_inspection(
    config_path: Path | str, log: Callable[[str], None] = print
) -> list[InspectionResult]:
    """Run deterministic document inspection (PageMap/SectionMap) for the
    configured volumes and write intermediate navigation artifacts."""
    config_path = Path(config_path)
    config = load_config(config_path)
    log(f"lsdyna-manual-builder {__version__}")
    log(f"[1/3] load config: {config_path}")

    _release, infos, _missing = _resolve_volumes(config)
    log(f"[2/3] inspect volumes: {[info.path.name for info in infos]}")
    extractor = PopplerLayoutExtractor()
    results = [inspect_volume(info.volume, info.path, extractor) for info in infos]

    output_dir = config.output.corpus_dir / "intermediate"
    log(f"[3/3] write navigation artifacts: {output_dir}")
    write_inspection_artifacts(results, output_dir)

    for result in results:
        stats = result.stats
        log(
            f"      volume {result.volume}: pages={stats['pdf_pages']} "
            f"footer={stats['footer_pages']} "
            f"pagemap filled={stats['pagemap_filled']} "
            f"(none={stats['pagemap_none']}) "
            f"evidence={stats['evidence']} "
            f"sections={stats['sections_located']}/"
            f"{stats['toc_keyword_entries']}"
        )
        if stats["issues_by_code"]:
            log(f"      issues: {stats['issues_by_code']}")
    return results


def run_build(config_path: Path | str, log: Callable[[str], None] = print) -> BuildResult:
    """Run the build pipeline described by a config file."""
    config_path = Path(config_path)
    config = load_config(config_path)
    log(f"lsdyna-manual-builder {__version__}")
    log(f"[1/5] load config: {config_path}")

    release, infos, missing = _resolve_volumes(config)
    log(f"[2/5] resolve manuals: release {release}")
    for info in infos:
        log(f"      volume {info.volume}: {info.path.name}")
    for volume in missing:
        log(f"      warning: volume {volume} ({writer.VOLUME_NAMES[volume]}) missing")

    issues: list[dict] = [
        _issue(
            volume,
            severity="warning",
            code="VOLUME_MISSING",
            message=f"{writer.VOLUME_NAMES[volume]} not provided; excluded from this build",
        )
        for volume in missing
    ]

    log("[3/5] ingest volumes")
    volumes: list[dict] = []
    ingested: list[VolumeIngestInfo] = []
    for info in infos:
        record: dict = {
            "volume": info.volume,
            "name": writer.VOLUME_NAMES[info.volume],
            "source_file": info.path.name,
            "release": info.release,
        }
        try:
            volume_info = ingest_volume(info)
        except Exception as exc:
            record["status"] = "failed"
            volumes.append(record)
            issues.append(
                _issue(
                    info.volume,
                    severity="error",
                    code="VOLUME_INGEST_FAILED",
                    message=f"failed to ingest {info.path.name}: {exc}",
                )
            )
            log(f"      volume {info.volume}: FAILED ({exc})")
            continue
        record.update(
            pdf_page_count=volume_info.pdf_page_count,
            sha256=volume_info.sha256,
            status="success",
        )
        volumes.append(record)
        ingested.append(volume_info)
        log(
            f"      volume {info.volume}: {volume_info.pdf_page_count} pages, "
            f"sha256 {volume_info.sha256[:12]}"
        )

    corpus_dir = config.output.corpus_dir
    log(f"[4/5] write corpus skeleton: {corpus_dir}")
    writer.write_corpus(
        corpus_dir,
        release=release,
        volumes=ingested,
        parser_provider=config.parser.provider,
        parser_model=config.parser.model,
    )
    writer.write_manifest(corpus_dir, records=[])

    issues.append(
        _issue(
            None,
            severity="info",
            code="PARSE_NOT_IMPLEMENTED",
            message="PDF parsing is not implemented yet; this run performs "
            "discovery and ingestion only",
        )
    )

    severities = {issue["severity"] for issue in issues}
    if "error" in severities:
        status, exit_code = "failed", EXIT_FAILED
    elif "warning" in severities:
        status, exit_code = "warning", EXIT_WARNING
    else:
        status, exit_code = "success", EXIT_SUCCESS

    summary = {
        "builder_version": __version__,
        "timestamp": writer.utc_now_iso(),
        "status": status,
        "manual_release": release,
        "volumes": volumes,
        "entry_count": 0,
        "status_success": 0,
        "status_warning": 0,
        "status_failed": 0,
        "notes": [
            "PDF parsing is not implemented yet; this run performs "
            "discovery and ingestion only."
        ],
    }
    log("[5/5] write reports")
    writer.write_reports(corpus_dir / "reports", summary=summary, issues=issues)

    log(f"status: {status} (exit {exit_code}) - 0 entries; parsing not implemented yet")
    return BuildResult(
        exit_code=exit_code,
        status=status,
        release=release,
        volumes=volumes,
        issues=issues,
    )


def _resolve_volumes(
    config: BuildConfig,
) -> tuple[str, list[ManualFileInfo], list[int]]:
    """Resolve the three manual volumes from config.

    Returns (release, found volume infos sorted by volume, missing volume
    numbers). Raises ConfigError for invalid input: missing files, release
    ambiguity, or missing volumes when they are required.
    """
    if config.manual.volumes:
        infos: list[ManualFileInfo] = []
        for volume, path in sorted(config.manual.volumes.items()):
            if not path.is_file():
                raise ConfigError(f"manual.volumes[{volume}]: file not found: {path}")
            info = parse_manual_filename(path)
            if info is None:
                if config.manual.release is None:
                    raise ConfigError(
                        f"manual.volumes[{volume}]: cannot determine release from "
                        f"filename {path.name}; set manual.release"
                    )
                info = ManualFileInfo(
                    volume=volume, release=config.manual.release.upper(), path=path
                )
            elif info.volume != volume:
                raise ConfigError(
                    f"manual.volumes[{volume}]: filename {path.name} looks like "
                    f"volume {info.volume}"
                )
            infos.append(info)
    else:
        if not config.manual.manuals_dir.is_dir():
            raise ConfigError(
                f"manual.manuals_dir not found: {config.manual.manuals_dir}"
            )
        try:
            infos = discover_volumes(
                config.manual.manuals_dir, expected_release=config.manual.release
            )
        except DiscoveryError as exc:
            raise ConfigError(str(exc)) from exc

    if not infos:
        raise ConfigError(
            "no Keyword Manual volumes found; check manual.manuals_dir or set "
            "manual.volumes"
        )

    releases = {info.release for info in infos}
    if config.manual.release is not None:
        expected = config.manual.release.upper()
        mismatched = [info.path.name for info in infos if info.release != expected]
        if mismatched:
            raise ConfigError(
                f"release mismatch: config expects {expected}, files report "
                f"{sorted(releases)} ({', '.join(mismatched)})"
            )
        release = expected
    elif len(releases) == 1:
        release = next(iter(releases))
    else:
        raise ConfigError(
            f"manuals mix releases {sorted(releases)}; set manual.release or "
            "manual.volumes"
        )

    found = {info.volume for info in infos}
    missing = [volume for volume in ALL_VOLUMES if volume not in found]
    if missing and config.manual.require_all_volumes:
        names = [writer.VOLUME_NAMES[volume] for volume in missing]
        raise ConfigError(
            f"missing required volumes {names}; set manual.require_all_volumes: "
            "false to build without them"
        )
    infos.sort(key=lambda info: info.volume)
    return release, infos, missing


def _issue(
    volume: int | None, *, severity: str, code: str, message: str
) -> dict:
    return {
        "volume": volume,
        "pdf_page": None,
        "manual_page": None,
        "keyword_id": None,
        "severity": severity,
        "code": code,
        "message": message,
    }
