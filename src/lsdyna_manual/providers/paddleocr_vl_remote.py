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

from lsdyna_manual.providers.base import DocumentProvider, ProviderError, ProviderJobResult

DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_MAX_RETRIES = 2


@dataclass
class PaddleOCRVLRemoteConfig:
    job_url: str = DEFAULT_JOB_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    optional_payload: dict[str, Any] | None = None


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

    def submit_pdf(self, pdf_path: Path) -> str:
        if not pdf_path.is_file():
            raise ProviderError(f"input PDF not found: {pdf_path}")
        payload = {
            "model": self.config.model,
            "optionalPayload": json.dumps(self._optional_payload()),
        }
        with open(pdf_path, "rb") as fh:
            response = self._post_with_retry(
                self.config.job_url,
                timeout=120,
                headers=self._headers(),
                data=payload,
                files={"file": fh},
            )
        if response.status_code != 200:
            raise ProviderError(
                f"job submission failed with HTTP {response.status_code}: "
                f"{self._redact(response.text[:500])}"
            )
        try:
            return str(response.json()["data"]["jobId"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("unexpected job submission response") from exc

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self._get_with_retry(
            f"{self.config.job_url}/{job_id}",
            timeout=60,
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise ProviderError(
                f"job status request failed with HTTP {response.status_code}: "
                f"{self._redact(response.text[:500])}"
            )
        try:
            return response.json()["data"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("unexpected job status response") from exc

    def wait_for_job(self, job_id: str) -> dict[str, Any]:
        deadline = time.time() + self.config.timeout_seconds
        while True:
            data = self.get_job(job_id)
            state = data.get("state")
            if state == "done":
                return data
            if state == "failed":
                raise ProviderError(
                    f"job {job_id} failed: {self._redact(data.get('errorMsg', 'unknown error'))}"
                )
            if state not in {"pending", "running"}:
                raise ProviderError(f"job {job_id} entered unknown state: {state!r}")
            if time.time() >= deadline:
                raise ProviderError(f"job {job_id} timed out after {self.config.timeout_seconds}s")
            time.sleep(self.config.poll_interval_seconds)

    def download_result_text(self, result_url: str) -> str:
        response = self._get_with_retry(result_url, timeout=300)
        response.raise_for_status()
        return response.text

    def parse_pdf_batch(
        self,
        input_pdf_path: Path,
        *,
        document_id: str,
        pdf_pages: list[int],
        volume: int | None = None,
    ) -> ProviderJobResult:
        del document_id, volume, pdf_pages  # Transport ignores document semantics.
        job_id = self.submit_pdf(input_pdf_path)
        job_data = self.wait_for_job(job_id)
        result_url = job_data.get("resultUrl", {}).get("jsonUrl")
        if not result_url:
            raise ProviderError(f"job {job_id} completed without a JSONL result URL")
        raw_text = self.download_result_text(result_url)
        return ProviderJobResult(
            provider=self.provider_name,
            model=self.config.model,
            job_id=job_id,
            state="done",
            raw_jsonl_text=raw_text,
            metadata={
                "job_data": job_data,
                "result_url": result_url,
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
