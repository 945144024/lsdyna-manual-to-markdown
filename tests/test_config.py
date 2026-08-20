"""Tests for parser configuration and credential handling."""

import pytest

import lsdyna_manual.providers.paddleocr_vl_remote as paddle_remote

from lsdyna_manual.config import ConfigError, load_config
from lsdyna_manual.providers.base import ProviderError, ProviderQuotaError
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
    assert config.parser.max_batch_pages == 1
    assert config.quality_gate.baseline is None


def test_quality_gate_baseline_is_loaded(tmp_path):
    path = _write_config(tmp_path, None)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "quality_gate:\n  baseline: docs/r17-corpus-acceptance-v0.1.json\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.quality_gate.baseline.as_posix() == (
        "docs/r17-corpus-acceptance-v0.1.json"
    )


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


def test_remote_provider_records_batch_timing(monkeypatch, tmp_path):
    provider = PaddleOCRVLRemoteProvider(
        PaddleOCRVLRemoteConfig(api_key="unit-test-placeholder")
    )
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4 test")
    job_data = {
        "state": "done",
        "resultUrl": {"jsonUrl": "https://example.invalid/result.jsonl"},
    }
    monkeypatch.setattr(provider, "submit_pdf", lambda _path: "job-1")
    monkeypatch.setattr(
        provider, "wait_for_job", lambda _job_id, **_kwargs: job_data
    )
    monkeypatch.setattr(provider, "download_result_text", lambda _url: "{}")
    ticks = iter([10.0, 11.25, 15.5, 16.0])
    monkeypatch.setattr(paddle_remote.time, "monotonic", lambda: next(ticks))

    result = provider.parse_pdf_batch(
        input_pdf,
        document_id="keyword-volume-2",
        volume=2,
        pdf_pages=[24],
    )

    assert result.metadata["timing"] == {
        "submit_seconds": 1.25,
        "wait_seconds": 4.25,
        "download_seconds": 0.5,
        "total_seconds": 6.0,
    }


def test_remote_provider_retries_queue_full_submission(monkeypatch, tmp_path):
    provider = PaddleOCRVLRemoteProvider(
        PaddleOCRVLRemoteConfig(
            api_key="unit-test-placeholder",
            max_retries=2,
        )
    )
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4 test")

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.payload = payload
            self.text = "queue response"

        def json(self):
            return self.payload

    responses = iter(
        [
            Response(400, {"code": 10010}),
            Response(200, {"data": {"jobId": "job-after-retry"}}),
        ]
    )
    monkeypatch.setattr(provider, "_post_with_retry", lambda *_args, **_kwargs: next(responses))
    sleeps = []
    monkeypatch.setattr(paddle_remote.time, "sleep", sleeps.append)

    assert provider.submit_pdf(input_pdf) == "job-after-retry"
    assert sleeps == [5]


def test_remote_provider_classifies_configured_quota_business_code(
    monkeypatch, tmp_path
):
    provider = PaddleOCRVLRemoteProvider(
        PaddleOCRVLRemoteConfig(
            api_key="unit-test-placeholder",
            quota_exhausted_codes=(43210,),
        )
    )
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4 test")

    class Response:
        status_code = 200
        text = "quota exhausted"

        @staticmethod
        def json():
            return {"code": 43210, "message": "daily quota exhausted"}

    monkeypatch.setattr(
        provider, "_post_with_retry", lambda *_args, **_kwargs: Response()
    )

    with pytest.raises(ProviderQuotaError) as caught:
        provider.submit_pdf(input_pdf)

    assert caught.value.category == "quota_exhausted"
    assert caught.value.business_code == 43210
    assert caught.value.http_status == 200
