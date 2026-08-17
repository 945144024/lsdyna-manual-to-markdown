"""PaddleOCR-VL remote job provider.

This provider talks to the official PaddleOCR job API. It deliberately
returns the provider-specific raw JSONL text unchanged; the Paddle
Adapter owns conversion to Canonical PageIR.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from lsdyna_manual.providers.base import (
    DocumentProvider,
    ProviderError,
    ProviderJobResult,
    ProviderProgressCallback,
    ProviderQuotaError,
)

DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_MAX_RETRIES = 2
QUEUE_FULL_CODE = 10010
QUOTA_MESSAGE_MARKERS = (
    "quota",
    "resource exhausted",
    "usage limit",
    "limit exceeded",
    "配额",
    "额度不足",
    "次数不足",
    "次数已用完",
    "调用次数已达",
)
AUTH_MESSAGE_MARKERS = (
    "unauthorized",
    "invalid api key",
    "invalid token",
    "鉴权失败",
    "密钥无效",
)


@dataclass
class PaddleOCRVLRemoteConfig:
    job_url: str = DEFAULT_JOB_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    optional_payload: dict[str, Any] | None = None
    quota_exhausted_codes: tuple[int, ...] = ()


class PaddleOCRVLRemoteProvider(DocumentProvider):
    def __init__(self, config: PaddleOCRVLRemoteConfig | None = None) -> None:
        self.config = config or PaddleOCRVLRemoteConfig()
        self._api_key = self.config.api_key
        if not self._api_key:
            raise ProviderError(
                "PaddleOCR API key is missing; set parser.api_key in the local config"
            )

    def _redact(self, value: object) -> str:
        return str(value).replace(self._api_key, "<redacted>")

    @property
    def provider_name(self) -> str:
        return "paddleocr-vl-remote"

    def _headers(self, *, json_content: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"bearer {self._api_key}"}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _post_with_retry(
        self,
        url: str,
        *,
        timeout: int,
        **kwargs: Any,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = requests.post(url, timeout=timeout, **kwargs)
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    time.sleep(min(2**attempt, 10))
        raise ProviderError(f"request failed after retries: {self._redact(last_error)}") from last_error

    def _get_with_retry(
        self, url: str, *, timeout: int, headers: dict[str, str] | None = None
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = requests.get(url, timeout=timeout, headers=headers)
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    time.sleep(min(2**attempt, 10))
        raise ProviderError(f"request failed after retries: {self._redact(last_error)}") from last_error

    @staticmethod
    def _payload(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _business_code(cls, response: requests.Response) -> int | str | None:
        code = cls._payload(response).get("code")
        if code is None:
            return None
        try:
            return int(code)
        except (TypeError, ValueError):
            return str(code)

    @classmethod
    def _response_message(cls, response: requests.Response) -> str:
        payload = cls._payload(response)
        for key in ("message", "msg", "errorMsg", "error", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
        return str(getattr(response, "text", ""))[:500]

    def _error_from_response(
        self, response: requests.Response, *, context: str
    ) -> ProviderError:
        code = self._business_code(response)
        message = self._redact(self._response_message(response))
        lowered = message.casefold()
        details = (
            f"{context} failed with HTTP {response.status_code}"
            + (f", business code {code}" if code is not None else "")
            + (f": {message}" if message else "")
        )
        if (
            isinstance(code, int)
            and code in self.config.quota_exhausted_codes
        ) or any(marker in lowered for marker in QUOTA_MESSAGE_MARKERS):
            return ProviderQuotaError(
                details,
                business_code=code,
                http_status=response.status_code,
            )
        if response.status_code in {401, 403} or any(
            marker in lowered for marker in AUTH_MESSAGE_MARKERS
        ):
            return ProviderError(
                details,
                category="auth",
                business_code=code,
                http_status=response.status_code,
            )
        category = "transient" if response.status_code >= 500 else "provider_error"
        return ProviderError(
            details,
            category=category,
            business_code=code,
            http_status=response.status_code,
        )

    def submit_pdf(self, pdf_path: Path) -> str:
        if not pdf_path.is_file():
            raise ProviderError(f"input PDF not found: {pdf_path}")
        payload = {
            "model": self.config.model,
            "optionalPayload": json.dumps(self._optional_payload()),
        }
        response: requests.Response | None = None
        for attempt in range(self.config.max_retries + 1):
            with open(pdf_path, "rb") as fh:
                response = self._post_with_retry(
                    self.config.job_url,
                    timeout=120,
                    headers=self._headers(),
                    data=payload,
                    files={"file": fh},
                )
            payload_body = self._payload(response)
            response_data = payload_body.get("data")
            job_id = (
                response_data.get("jobId")
                if isinstance(response_data, dict)
                else None
            )
            if response.status_code == 200 and job_id:
                return str(job_id)
            if self._is_queue_full(response) and attempt < self.config.max_retries:
                time.sleep(min(5 * (2**attempt), 30))
                continue
            error = self._error_from_response(response, context="job submission")
            if self._is_queue_full(response):
                error.category = "busy"
            raise error
        if response is None:
            raise ProviderError("job submission produced no response")
        raise ProviderError("unexpected job submission response")

    @staticmethod
    def _is_queue_full(response: requests.Response) -> bool:
        try:
            code = PaddleOCRVLRemoteProvider._payload(response).get("code")
            return int(code) == QUEUE_FULL_CODE
        except (TypeError, ValueError):
            return False

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self._get_with_retry(
            f"{self.config.job_url}/{job_id}",
            timeout=60,
            headers=self._headers(),
        )
        payload = self._payload(response)
        data = payload.get("data")
        code = self._business_code(response)
        if (
            response.status_code == 200
            and code in {None, 0}
            and isinstance(data, dict)
        ):
            return data
        raise self._error_from_response(response, context="job status request")

    def wait_for_job(
        self,
        job_id: str,
        *,
        on_progress: ProviderProgressCallback | None = None,
    ) -> dict[str, Any]:
        deadline = time.time() + self.config.timeout_seconds
        previous_state: str | None = None
        while True:
            try:
                data = self.get_job(job_id)
            except ProviderError as exc:
                exc.job_id = job_id
                raise
            state = data.get("state")
            if on_progress is not None and state != previous_state:
                on_progress("polling", {"job_id": job_id, "remote_state": state})
                previous_state = str(state)
            if state == "done":
                return data
            if state == "failed":
                error_message = self._redact(
                    data.get("errorMsg", "unknown error")
                )
                if any(
                    marker in error_message.casefold()
                    for marker in QUOTA_MESSAGE_MARKERS
                ):
                    raise ProviderQuotaError(
                        f"job {job_id} failed: {error_message}",
                        job_id=job_id,
                    )
                raise ProviderError(
                    f"job {job_id} failed: {error_message}",
                    category="job_failed",
                    job_id=job_id,
                )
            if state not in {"pending", "running"}:
                raise ProviderError(
                    f"job {job_id} entered unknown state: {state!r}",
                    job_id=job_id,
                )
            if time.time() >= deadline:
                raise ProviderError(
                    f"job {job_id} timed out after {self.config.timeout_seconds}s",
                    category="timeout",
                    job_id=job_id,
                )
            time.sleep(self.config.poll_interval_seconds)

    def download_result_text(self, result_url: str) -> str:
        response = self._get_with_retry(result_url, timeout=300)
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(
                f"result download failed with HTTP {response.status_code}",
                category="transient",
                http_status=response.status_code,
            ) from exc
        return response.text

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
        del document_id, volume, pdf_pages  # Transport ignores document semantics.
        total_started = time.monotonic()
        if resume_job_id is None:
            job_id = self.submit_pdf(input_pdf_path)
            if on_progress is not None:
                on_progress("submitted", {"job_id": job_id})
        else:
            job_id = resume_job_id
            if on_progress is not None:
                on_progress("resumed", {"job_id": job_id})
        submitted = time.monotonic()
        job_data = self.wait_for_job(job_id, on_progress=on_progress)
        completed = time.monotonic()
        result_url = job_data.get("resultUrl", {}).get("jsonUrl")
        if not result_url:
            raise ProviderError(
                f"job {job_id} completed without a JSONL result URL",
                job_id=job_id,
            )
        if on_progress is not None:
            on_progress("downloading", {"job_id": job_id})
        try:
            raw_text = self.download_result_text(result_url)
        except ProviderError as exc:
            exc.job_id = job_id
            raise
        downloaded = time.monotonic()
        timing = {
            "submit_seconds": round(submitted - total_started, 3),
            "wait_seconds": round(completed - submitted, 3),
            "download_seconds": round(downloaded - completed, 3),
            "total_seconds": round(downloaded - total_started, 3),
        }
        return ProviderJobResult(
            provider=self.provider_name,
            model=self.config.model,
            job_id=job_id,
            state="done",
            raw_jsonl_text=raw_text,
            metadata={
                "job_data": job_data,
                "result_url": result_url,
                "timing": timing,
            },
        )

    def semantic_identity(self) -> str:
        """Paddle-specific semantic identity.

        Includes the model and the optional payload values that can change
        recognition output. Excludes transport-only settings.
        """
        payload = self._optional_payload()
        payload_json = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:12]
        return f"{self.provider_name}:{self.config.model}:{digest}"

    def _optional_payload(self) -> dict[str, Any]:
        if self.config.optional_payload is not None:
            return dict(self.config.optional_payload)
        return {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
