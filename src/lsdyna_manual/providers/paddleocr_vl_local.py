"""Local PaddleOCR-VL provider backed by a CUDA llama.cpp server."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lsdyna_manual.config import LocalProviderConfig
from lsdyna_manual.providers.base import (
    DocumentProvider,
    ProviderError,
    ProviderJobResult,
    ProviderProgressCallback,
)
from lsdyna_manual.providers.local_runtime import LocalRuntimeManager, LocalRuntimePaths


class PaddleOCRVLLocalProvider(DocumentProvider):
    """Run the complete PaddleOCR-VL pipeline locally, one PDF page at a time.

    PaddleOCR performs layout detection and region extraction. Its VLM
    recognition backend is pointed at the local llama-server instance. The
    provider normalizes the result object into the same JSONL envelope used by
    the remote PaddleOCR provider, so raw storage and PageIR adaptation remain
    shared.
    """

    def __init__(
        self,
        config: LocalProviderConfig | None = None,
        *,
        model: str = "PaddleOCR-VL-1.6",
        allow_install: bool = False,
    ) -> None:
        self.local_config = config or LocalProviderConfig()
        self.model = model
        # DocumentParser reads provider.config.model as part of the raw cache
        # identity. Keep that stable without mixing transport settings into it.
        self.config = SimpleNamespace(model=model)
        self.runtime = LocalRuntimeManager(self.local_config)
        # Initialize cleanup state before any runtime step can fail. The
        # provider is registered with atexit early so partially constructed
        # instances still release a server process or log handle safely.
        self._worker_process: subprocess.Popen[bytes] | None = None
        self._worker_log: Any | None = None
        atexit.register(self.close)
        self.paths = self.runtime.ensure_ready(allow_install=allow_install)
        if self.local_config.auto_start_server:
            self.runtime.start(self.paths)
        elif not self.runtime.is_healthy():
            raise ProviderError(
                f"llama-server is not healthy at {self.local_config.llama_server_url}"
            )
        self._start_worker()

    @property
    def provider_name(self) -> str:
        return "paddleocr-vl-local"

    def semantic_identity(self) -> str:
        return f"{self.provider_name}:{self.model}:{self.runtime.fingerprint(self.paths)}"

    def _worker_url(self, path: str) -> str:
        return (
            f"http://{self.local_config.worker_host}:"
            f"{self.local_config.worker_port}{path}"
        )

    def _worker_is_healthy(self) -> bool:
        try:
            import requests

            response = requests.get(self._worker_url("/health"), timeout=2)
        except requests.RequestException:
            return False
        return response.status_code == 200

    def _worker_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        runtime_home = (self.local_config.runtime_dir / "home").resolve()
        runtime_home.mkdir(parents=True, exist_ok=True)
        # Paddle derives its dataset cache from expanduser("~") on Windows and
        # exposes no dedicated override. Keep all isolated-worker caches inside
        # the configured runtime instead of depending on the user profile.
        environment["HOME"] = str(runtime_home)
        environment["USERPROFILE"] = str(runtime_home)
        environment["PADDLE_PDX_CACHE_HOME"] = str(self.paths.paddlex_cache.resolve())
        if self.local_config.model_source:
            environment["PADDLE_PDX_MODEL_SOURCE"] = self.local_config.model_source
            environment["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        return environment

    def _start_worker(self) -> None:
        if self._worker_is_healthy():
            return
        log_dir = self.local_config.runtime_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._worker_log = (log_dir / "paddleocr-worker.log").open("ab")
        worker_script = Path(__file__).with_name("paddleocr_worker.py")
        command = [
            str(self.paths.paddleocr_python),
            str(worker_script),
            "--host",
            self.local_config.worker_host,
            "--port",
            str(self.local_config.worker_port),
            "--pipeline-version",
            self.local_config.pipeline_version,
            "--llama-server-url",
            self.local_config.llama_server_url,
        ]
        environment = self._worker_environment()
        try:
            self._worker_process = subprocess.Popen(
                command,
                stdout=self._worker_log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
        except OSError as exc:
            self._close_worker_log()
            raise ProviderError("failed to start the isolated PaddleOCR worker") from exc

        deadline = time.monotonic() + self.local_config.worker_start_timeout_seconds
        while time.monotonic() < deadline:
            if self._worker_is_healthy():
                return
            if self._worker_process.poll() is not None:
                self._close_worker_log()
                raise ProviderError(
                    f"PaddleOCR worker exited during startup; inspect {log_dir / 'paddleocr-worker.log'}"
                )
            time.sleep(0.5)
        self._stop_worker()
        raise ProviderError(
            f"PaddleOCR worker did not start within "
            f"{self.local_config.worker_start_timeout_seconds}s"
        )

    def _predict(
        self, input_pdf_path: Path
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            import requests

            response = requests.post(
                self._worker_url("/predict"),
                json={"path": str(input_pdf_path.resolve())},
                timeout=self.local_config.inference_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            detail = getattr(response, "text", "") if "response" in locals() else ""
            raise ProviderError(
                f"local PaddleOCR worker request failed: {detail[:500]}"
            ) from exc
        layout_result = payload.get("layout_result")
        if not isinstance(layout_result, dict):
            raise ProviderError("local PaddleOCR worker returned no layout result")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ProviderError("local PaddleOCR worker returned invalid metadata")
        return layout_result, metadata

    @staticmethod
    def _value_from_result(result: Any) -> Any:
        if type(result) is dict:
            return result
        for name in ("json", "to_json", "to_dict"):
            value = getattr(result, name, None)
            if value is None:
                continue
            try:
                value = value() if callable(value) else value
            except TypeError:
                continue
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    continue
            if isinstance(value, dict):
                return value

        # PaddleOCR result objects consistently expose save_to_json even when
        # their in-memory representation differs between minor releases.
        save = getattr(result, "save_to_json", None)
        if callable(save):
            with tempfile.TemporaryDirectory(prefix="paddleocr-local-") as directory:
                save(save_path=directory)
                files = sorted(Path(directory).glob("*.json"))
                if files:
                    return json.loads(files[0].read_text(encoding="utf-8"))
        raise ProviderError(
            f"unsupported PaddleOCR result object: {type(result).__name__}"
        )

    @classmethod
    def _as_layout_result(cls, result: Any) -> dict[str, Any]:
        payload = cls._value_from_result(result)
        if not isinstance(payload, dict):
            raise ProviderError("local PaddleOCR returned a non-object result")
        layouts = payload.get("layoutParsingResults")
        if isinstance(layouts, list):
            if len(layouts) != 1:
                raise ProviderError(
                    "local single-page inference returned an unexpected number of layout results"
                )
            return cls._as_layout_result(layouts[0])
        if isinstance(payload.get("result"), dict):
            return cls._as_layout_result(payload["result"])
        if isinstance(payload.get("res"), dict):
            return cls._as_layout_result(payload["res"])
        if "prunedResult" in payload:
            return payload

        # Local PaddleOCR result JSON uses parsing_res_list at the top level;
        # the remote API wraps the same structure under prunedResult.
        if "parsing_res_list" in payload:
            normalized = dict(payload)
            normalized["prunedResult"] = {
                "parsing_res_list": payload.get("parsing_res_list", [])
            }
            return normalized
        raise ProviderError("local PaddleOCR result has no parsing result blocks")

    def parse_pdf_batch(
        self,
        input_pdf_path: Path,
        *,
        document_id: str,
        pdf_pages: list[int],
        volume: int | None = None,
        resume_job_id: str | None = None,
        on_progress: ProviderProgressCallback | None = None,
    ) -> ProviderJobResult:
        del document_id, volume
        if len(pdf_pages) != 1:
            raise ProviderError(
                "paddleocr-vl-local requires exactly one source page per batch"
            )
        if resume_job_id is not None:
            # Local requests are synchronous and have no server-side job to
            # resume. Repeating the uncommitted page is idempotent at raw-store
            # level and is safer than inventing a remote job state.
            if on_progress is not None:
                on_progress("retrying", {"job_id": resume_job_id})

        started = time.monotonic()
        if on_progress is not None:
            on_progress("local_started", {"job_id": "pending"})
        try:
            layout_result, worker_metadata = self._predict(input_pdf_path)
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"local PaddleOCR inference failed: {exc}") from exc
        job_id = f"local-{uuid.uuid4().hex}"
        if on_progress is not None:
            on_progress("local_done", {"job_id": job_id})
        return ProviderJobResult(
            provider=self.provider_name,
            model=self.model,
            job_id=job_id,
            state="done",
            raw_jsonl_text=json.dumps(
                {"result": {"layoutParsingResults": [layout_result]}},
                ensure_ascii=False,
            ),
            metadata={
                "runtime": "local",
                "timing": {"total_seconds": round(time.monotonic() - started, 3)},
                "transport": worker_metadata,
            },
        )

    def close(self) -> None:
        self._stop_worker()
        self.runtime.close()

    def _close_worker_log(self) -> None:
        if self._worker_log is not None:
            self._worker_log.close()
            self._worker_log = None

    def _stop_worker(self) -> None:
        process = self._worker_process
        self._worker_process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._close_worker_log()
