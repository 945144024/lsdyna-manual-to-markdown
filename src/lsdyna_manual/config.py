"""Configuration loading and validation for build runs."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or invalid."""


class ManualConfig(BaseModel):
    release: str | None = None
    manuals_dir: Path = Path("./manuals")
    volumes: dict[int, Path] | None = None
    require_all_volumes: bool = True

    @field_validator("volumes")
    @classmethod
    def _check_volume_keys(cls, value: dict[int, Path] | None) -> dict[int, Path] | None:
        if value is None:
            return value
        invalid = set(value) - {1, 2, 3}
        if invalid:
            raise ValueError(f"volume keys must be 1, 2, or 3; got {sorted(invalid)}")
        return value


class ParserConfig(BaseModel):
    provider: str = "openai-compatible"
    model: str = "your-model-name"
    # OpenAI-compatible endpoint base URL. Not used by paddleocr-vl-remote.
    base_url: str | None = None
    api_key_env: str = "PARSER_API_KEY"
    # PaddleOCR-VL remote job endpoint. Only used by paddleocr-vl-remote.
    job_url: str | None = None
    timeout_seconds: int = 1800
    poll_interval_seconds: int = 5
    max_retries: int = 2
    batch_size: int = 5


class OutputConfig(BaseModel):
    corpus_dir: Path


class OptionsConfig(BaseModel):
    start_page: int = 1
    end_page: int | None = None
    concurrency: int = 4


class BuildConfig(BaseModel):
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
