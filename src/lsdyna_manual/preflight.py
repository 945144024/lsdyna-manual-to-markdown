"""Deterministic checks run before model inference starts."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lsdyna_manual.config import BuildConfig, ConfigError, load_config
from lsdyna_manual.providers.local_runtime import LocalRuntimeManager


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class PreflightResult:
    checks: tuple[PreflightCheck, ...]

    @property
    def failed(self) -> bool:
        return any(check.status == "failed" for check in self.checks)

    @property
    def warning(self) -> bool:
        return any(check.status == "warning" for check in self.checks)


def _check_path(checks: list[PreflightCheck], name: str, path: Path) -> None:
    if path.is_file():
        checks.append(PreflightCheck(name, "ok", str(path)))
    else:
        checks.append(PreflightCheck(name, "warning", f"file not found: {path}"))


def run_preflight(
    config_path: Path | str,
    *,
    log: Callable[[str], None] = print,
) -> PreflightResult:
    """Validate config, source PDFs, tools, output, and provider prerequisites.

    This function never starts a server, imports Paddle, calls a remote API, or
    downloads an artifact. It is therefore safe to run before every build.
    """

    config_path = Path(config_path)
    config: BuildConfig = load_config(config_path)
    checks: list[PreflightCheck] = []
    try:
        from lsdyna_manual.pipeline import _resolve_documents

        _release, documents = _resolve_documents(config)
    except ConfigError as exc:
        checks.append(PreflightCheck("manuals", "failed", str(exc)))
        documents = []
    else:
        checks.append(
            PreflightCheck(
                "manuals",
                "ok",
                f"{len(documents)} document(s): "
                + ", ".join(document.path.name for document in documents),
            )
        )

    poppler = shutil.which("pdftotext")
    checks.append(
        PreflightCheck(
            "pdftotext",
            "ok" if poppler else "failed",
            poppler or "not found; install Poppler (poppler-utils)",
        )
    )

    output_dir = Path(config.output.corpus_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".preflight-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        checks.append(PreflightCheck("output", "failed", f"not writable: {exc}"))
    else:
        checks.append(PreflightCheck("output", "ok", str(output_dir)))

    if config.parser.provider == "paddleocr-vl-remote":
        if config.parser.api_key and config.parser.api_key.get_secret_value().strip():
            checks.append(PreflightCheck("remote credentials", "ok", "API key configured"))
        else:
            checks.append(
                PreflightCheck(
                    "remote credentials",
                    "warning",
                    "parser.api_key is missing; cached raw artifacts can still be used",
                )
            )
    else:
        paths = LocalRuntimeManager(config.parser.local).paths()
        for name, path in (
            ("llama-server", paths.llama_server),
            ("local model", paths.model),
            ("local mmproj", paths.mmproj),
            ("paddleocr Python", paths.paddleocr_python),
        ):
            _check_path(checks, name, path)
        if config.parser.local.auto_prepare_runtime:
            checks = [
                PreflightCheck(
                    check.name,
                    check.status,
                    check.message + "; auto_prepare_runtime may provision it",
                )
                if check.name in {"llama-server", "local model", "local mmproj", "paddleocr Python"}
                and check.status == "warning"
                else check
                for check in checks
            ]

    result = PreflightResult(tuple(checks))
    outcome = (
        "failed"
        if result.failed
        else "passed with warnings"
        if result.warning
        else "passed"
    )
    log(f"preflight: {len(checks)} checks, {outcome}")
    for check in checks:
        log(f"  [{check.status}] {check.name}: {check.message}")
    return result
