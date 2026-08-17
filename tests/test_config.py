"""Tests for parser configuration and credential handling."""

import pytest

from lsdyna_manual.config import ConfigError, load_config
from lsdyna_manual.providers.base import ProviderError
from lsdyna_manual.providers.paddleocr_vl_remote import (
    PaddleOCRVLRemoteConfig,
    PaddleOCRVLRemoteProvider,
)


def _write_config(tmp_path, api_key):
    value = "null" if api_key is None else f"\"{api_key}\""
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""manual:
  release: "R17"
  manuals_dir: "./manuals"
parser:
  provider: "paddleocr-vl-remote"
  model: "PaddleOCR-VL-1.6"
  api_key: {value}
output:
  corpus_dir: "./workspace/test"
""",
        encoding="utf-8",
    )
    return path


def test_api_key_is_loaded_as_secret(tmp_path):
    marker = "unit-test-placeholder"
    config = load_config(_write_config(tmp_path, marker))

    assert config.parser.api_key is not None
    assert config.parser.api_key.get_secret_value() == marker
    assert marker not in repr(config)
    assert marker not in str(config.parser)


def test_api_key_may_be_absent_for_local_pipeline_stages(tmp_path):
    config = load_config(_write_config(tmp_path, None))
    assert config.parser.api_key is None


def test_remote_provider_requires_explicit_api_key():
    with pytest.raises(ProviderError, match="parser.api_key"):
        PaddleOCRVLRemoteProvider(PaddleOCRVLRemoteConfig())

    remote_config = PaddleOCRVLRemoteConfig(api_key="unit-test-placeholder")
    assert "unit-test-placeholder" not in repr(remote_config)
    provider = PaddleOCRVLRemoteProvider(
        PaddleOCRVLRemoteConfig(api_key="unit-test-placeholder")
    )
    assert provider._headers()["Authorization"] == "bearer unit-test-placeholder"
    assert provider._redact("error: unit-test-placeholder") == "error: <redacted>"


def test_removed_parser_fields_are_rejected(tmp_path):
    path = _write_config(tmp_path, None)
    text = path.read_text(encoding="utf-8").replace(
        "  api_key: null", "  api_key_env: PADDLEOCR_API_KEY"
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="api_key_env"):
        load_config(path)
