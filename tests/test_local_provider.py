"""Tests for the local PaddleOCR-VL provider and runtime boundary."""

import json

import pytest

from lsdyna_manual.config import LocalProviderConfig, ParserConfig
from lsdyna_manual.providers.base import ProviderError
from lsdyna_manual.providers.local_runtime import LocalRuntimeError, LocalRuntimeManager
from lsdyna_manual.providers.paddleocr_vl_local import PaddleOCRVLLocalProvider
from lsdyna_manual.providers.paddleocr_worker import _json_default, _result_value


def test_local_parser_forces_single_page_batches():
    config = ParserConfig.model_validate(
        {"provider": "paddleocr-vl-local", "max_batch_pages": 20}
    )

    assert config.max_batch_pages == 1
    assert config.local.max_concurrency == 1
    assert config.local.model_source == "bos"


def test_worker_uses_paddlex_json_for_dict_subclass():
    class PaddleResult(dict):
        @property
        def json(self):
            return {"res": {"parsing_res_list": [{"block_content": "normalized"}]}}

    result = PaddleResult(parsing_res_list=[object()])

    assert _result_value(result)["res"]["parsing_res_list"][0]["block_content"] == (
        "normalized"
    )


def test_worker_serializes_array_like_values():
    class ArrayLike:
        def tolist(self):
            return [[1, 2], [3, 4]]

    assert _json_default(ArrayLike()) == [[1, 2], [3, 4]]


def test_local_runtime_does_not_install_without_explicit_authorization(
    monkeypatch, tmp_path
):
    config = LocalProviderConfig(
        runtime_dir=tmp_path / "runtime",
        auto_prepare_runtime=True,
    )
    manager = LocalRuntimeManager(config)
    monkeypatch.setattr(manager, "_modules_available", lambda _python: False)
    install_calls = []
    monkeypatch.setattr(
        manager,
        "_install_python_dependencies",
        lambda _python: install_calls.append(True),
    )

    with pytest.raises(LocalRuntimeError, match="--allow-runtime-install"):
        manager.ensure_ready(allow_install=False)

    assert install_calls == []


def test_local_runtime_does_not_download_layout_model_without_authorization(
    monkeypatch, tmp_path
):
    config = LocalProviderConfig(
        runtime_dir=tmp_path / "runtime",
        paddleocr_python=tmp_path / "python",
        llama_server_path=tmp_path / "llama-server",
        model_path=tmp_path / "model.gguf",
        mmproj_path=tmp_path / "mmproj.gguf",
        auto_prepare_runtime=True,
    )
    for path in (
        config.llama_server_path,
        config.model_path,
        config.mmproj_path,
    ):
        path.touch()
    manager = LocalRuntimeManager(config)
    monkeypatch.setattr(manager, "_modules_available", lambda _python: True)
    download_calls = []
    monkeypatch.setattr(
        manager,
        "_download_layout_model",
        lambda _paths: download_calls.append(True),
    )

    with pytest.raises(LocalRuntimeError, match="layout model is missing"):
        manager.ensure_ready(allow_install=False)

    assert download_calls == []


def test_local_result_normalizes_paddle_result_envelope():
    layout = PaddleOCRVLLocalProvider._as_layout_result(
        {
            "res": {
                "parsing_res_list": [
                    {"block_label": "text", "block_content": "hello"}
                ],
                "markdown": {"text": "hello", "images": {}},
            }
        }
    )

    assert layout["prunedResult"]["parsing_res_list"][0]["block_content"] == "hello"
    assert layout["markdown"]["text"] == "hello"


def test_local_provider_rejects_multi_page_batch(tmp_path):
    provider = object.__new__(PaddleOCRVLLocalProvider)
    provider.config = LocalProviderConfig()
    provider.model = "PaddleOCR-VL-1.6"

    with pytest.raises(ProviderError, match="exactly one"):
        provider.parse_pdf_batch(
            tmp_path / "batch.pdf",
            document_id="keyword-volume-2",
            pdf_pages=[1, 2],
        )


def test_local_provider_returns_remote_compatible_jsonl(tmp_path):
    provider = object.__new__(PaddleOCRVLLocalProvider)
    provider.local_config = LocalProviderConfig()
    provider.config = LocalProviderConfig()
    provider.model = "PaddleOCR-VL-1.6"
    provider._predict = lambda _path: {
        "parsing_res_list": [
            {"block_label": "text", "block_content": "local page"}
        ],
        "prunedResult": {
            "parsing_res_list": [
                {"block_label": "text", "block_content": "local page"}
            ]
        },
        "markdown": {"text": "local page", "images": {}},
    }
    events = []

    result = provider.parse_pdf_batch(
        tmp_path / "page.pdf",
        document_id="keyword-volume-2",
        pdf_pages=[17],
        on_progress=lambda phase, details: events.append((phase, details)),
    )
    payload = json.loads(result.raw_jsonl_text)
    layout = payload["result"]["layoutParsingResults"][0]

    assert result.provider == "paddleocr-vl-local"
    assert layout["prunedResult"]["parsing_res_list"][0]["block_content"] == "local page"
    assert [phase for phase, _details in events] == ["local_started", "local_done"]
