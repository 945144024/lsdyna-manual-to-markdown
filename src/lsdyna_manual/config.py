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


class LocalProviderConfig(_ConfigModel):
    """Configuration for the local PaddleOCR-VL + llama.cpp runtime."""

    runtime_dir: Path = Path(".runtime/paddleocr-local")
    llama_server_path: Path | None = None
    llama_server_url: str = "http://127.0.0.1:8111/v1"
    llama_server_host: str = "127.0.0.1"
    llama_server_port: int = 8111
    llama_server_args: list[str] = []
    model_path: Path | None = None
    mmproj_path: Path | None = None
    model_repo: str = "PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
    model_revision: str | None = None
    llama_server_download_url: str | None = None
    llama_server_archive_url: str | None = None
    llama_cuda_archive_url: str | None = None
    paddleocr_package: str = "paddleocr[doc-parser]>=3.6.0"
    paddlepaddle_package: str = "paddlepaddle-gpu==3.2.1"
    paddlepaddle_index_url: str | None = (
        "https://www.paddlepaddle.org.cn/packages/stable/cu126/"
    )
    pipeline_version: Literal["v1.6"] = "v1.6"
    auto_prepare_runtime: bool = False
    auto_start_server: bool = True
    max_concurrency: int = 1
    health_timeout_seconds: int = 60
    worker_host: str = "127.0.0.1"
    worker_port: int = 8112
    worker_start_timeout_seconds: int = 600
    inference_timeout_seconds: int = 1800
    paddleocr_python: Path | None = None
    # PaddleX model hoster used for auxiliary layout models. BOS is generally
    # reachable in mainland China; set null to inherit the environment.
    model_source: Literal["bos", "huggingface", "modelscope", "aistudio"] | None = (
        "bos"
    )
    paddlex_cache_dir: Path | None = None
    layout_model_archive_url: str = (
        "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
        "official_inference_model/paddle3.0.0/PP-DocLayoutV3_infer.tar"
    )

    @model_validator(mode="after")
    def _check_local_runtime(self) -> "LocalProviderConfig":
        if not 1 <= self.llama_server_port <= 65535:
            raise ValueError("parser.local.llama_server_port must be valid")
        if not 1 <= self.worker_port <= 65535:
            raise ValueError("parser.local.worker_port must be valid")
        if self.worker_port == self.llama_server_port:
            raise ValueError("parser.local.worker_port must differ from llama_server_port")
        if self.max_concurrency != 1:
            raise ValueError(
                "parser.local.max_concurrency must be 1 until local batching is validated"
            )
        if self.health_timeout_seconds <= 0:
            raise ValueError("parser.local.health_timeout_seconds must be positive")
        if self.worker_start_timeout_seconds <= 0 or self.inference_timeout_seconds <= 0:
            raise ValueError("parser.local worker timeouts must be positive")
        return self


class ParserConfig(_ConfigModel):
    provider: Literal["paddleocr-vl-remote", "paddleocr-vl-local"] = (
        "paddleocr-vl-remote"
    )
    model: str = "PaddleOCR-VL-1.6"
    api_key: SecretStr | None = None
    job_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    timeout_seconds: int = 1800
    poll_interval_seconds: int = 5
    max_retries: int = 2
    max_batch_pages: int = 1
    quota_exhausted_codes: tuple[int, ...] = ()
    local: "LocalProviderConfig" = None  # type: ignore[assignment]

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_batch_size(cls, data):
        if not isinstance(data, dict) or "batch_size" not in data:
            return data
        if "max_batch_pages" in data:
            raise ValueError(
                "set parser.max_batch_pages or legacy parser.batch_size, not both"
            )
        migrated = dict(data)
        migrated["max_batch_pages"] = migrated.pop("batch_size")
        return migrated

    @model_validator(mode="after")
    def _check_transport_limits(self) -> "ParserConfig":
        if self.local is None:
            self.local = LocalProviderConfig()
        if self.max_batch_pages <= 0:
            raise ValueError("parser.max_batch_pages must be positive")
        if self.provider == "paddleocr-vl-local":
            # The local pipeline owns its model lifecycle and processes one
            # document page per request. Keep this invariant in config rather
            # than relying on every caller to remember it.
            self.max_batch_pages = 1
        if self.timeout_seconds <= 0 or self.poll_interval_seconds <= 0:
            raise ValueError("parser timeout and poll interval must be positive")
        if self.max_retries < 0:
            raise ValueError("parser.max_retries must not be negative")
        return self


class OutputConfig(_ConfigModel):
    corpus_dir: Path


class OptionsConfig(_ConfigModel):
    start_page: int = 1
    end_page: int | None = None
    concurrency: int = 4


class ValidationConfig(_ConfigModel):
    text_layer_enabled: bool = True
    text_layer_sample_pages: int = 3
    text_layer_min_tokens: int = 8
    text_layer_min_visual_recall: float = 0.65

    @model_validator(mode="after")
    def _check_text_layer(self) -> "ValidationConfig":
        if self.text_layer_sample_pages < 0:
            raise ValueError("validation.text_layer_sample_pages must not be negative")
        if self.text_layer_min_tokens < 0:
            raise ValueError("validation.text_layer_min_tokens must not be negative")
        if not 0 <= self.text_layer_min_visual_recall <= 1:
            raise ValueError(
                "validation.text_layer_min_visual_recall must be between 0 and 1"
            )
        return self


class BuildConfig(_ConfigModel):
    manual: ManualConfig
    parser: ParserConfig
    output: OutputConfig
    options: OptionsConfig = OptionsConfig()
    validation: ValidationConfig = ValidationConfig()


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
