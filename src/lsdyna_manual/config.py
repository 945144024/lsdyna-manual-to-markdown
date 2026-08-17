"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, model_validator


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or invalid."""


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualDocumentConfig(_ConfigModel):
    """One explicitly configured Manual PDF.

    Type and volume may be omitted when the official filename is recognizable.
    """

    path: Path
    manual_type: Literal["keyword", "theory"] | None = None
    volume: int | None = None

    @model_validator(mode="after")
    def _check_identity(self) -> "ManualDocumentConfig":
        if self.manual_type == "keyword" and self.volume not in {1, 2, 3}:
            raise ValueError("keyword documents require volume 1, 2, or 3")
        if self.manual_type == "theory" and self.volume is not None:
            raise ValueError("theory documents must not define volume")
        if self.manual_type is None and self.volume is not None:
            raise ValueError("volume requires manual_type: keyword")
        return self


class ManualConfig(_ConfigModel):
    release: str | None = None
    manuals_dir: Path = Path("./manuals")
    documents: list[ManualDocumentConfig] | None = None

    @model_validator(mode="after")
    def _check_explicit_sources(self) -> "ManualConfig":
        if self.documents == []:
            raise ValueError("manual.documents must not be empty")
        return self


class ParserConfig(_ConfigModel):
    provider: Literal["paddleocr-vl-remote"] = "paddleocr-vl-remote"
    model: str = "PaddleOCR-VL-1.6"
    api_key: SecretStr | None = None
    job_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    timeout_seconds: int = 1800
    poll_interval_seconds: int = 5
    max_retries: int = 2
    batch_size: int = 5


class OutputConfig(_ConfigModel):
    corpus_dir: Path


class OptionsConfig(_ConfigModel):
    start_page: int = 1
    end_page: int | None = None
    concurrency: int = 4


class BuildConfig(_ConfigModel):
    manual: ManualConfig
    parser: ParserConfig
    output: OutputConfig
    options: OptionsConfig = OptionsConfig()


def load_config(path: Path) -> BuildConfig:
    """Load and validate a build configuration file.

    Relative paths in the config are resolved against the working
    directory the command is run from.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    try:
        return BuildConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}:\n{exc}") from exc
