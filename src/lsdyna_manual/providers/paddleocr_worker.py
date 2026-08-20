"""Standalone local PaddleOCR worker used by the isolated runtime venv."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import math
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit, urlunsplit


# These strings are copied from the llama.cpp server response observed in the
# R17 local run. Recovery is intentionally gated on the exact response, not a
# fuzzy/case-insensitive interpretation of an arbitrary 500 error.
_PEG_NATIVE_FORMAT_ERROR = (
    "The model produced output that does not match the expected peg-native format"
)
_CONTENT_ONLY_FORMAT_ERROR = (
    "The model produced output that does not match the expected Content-only format"
)
_IMAGE_DATA_HEADER = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64\Z")


def _normalized_openai_server_url(server_url: str) -> str:
    """Validate and normalize the configured llama.cpp OpenAI base URL."""

    parsed = urlsplit(server_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("llama-server URL must be an absolute HTTP(S) URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("llama-server URL contains an invalid port") from exc
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        raise ValueError("llama-server URL must end with /v1")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _url_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL has no trusted HTTP(S) origin")
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.casefold(), parsed.port or default_port


def _llama_server_root(server_url: str) -> str:
    """Return the non-OpenAI llama-server root for a configured ``.../v1`` URL."""

    parsed = urlsplit(_normalized_openai_server_url(server_url))
    path = parsed.path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def _is_peg_native_format_error(
    exc: Exception,
    *,
    expected_origin: tuple[str, str, int],
    expected_chat_url: str,
) -> bool:
    """Match only the structured llama.cpp 500 response observed in R17."""

    if getattr(exc, "status_code", None) != 500:
        return False
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    if set(body) == {"error"} and isinstance(body.get("error"), dict):
        body = body["error"]
    if set(body) != {"code", "message", "type"}:
        return False
    if (
        type(body.get("code")) is not int
        or body.get("code") != 500
        or body.get("type") != "server_error"
        or body.get("message") != _PEG_NATIVE_FORMAT_ERROR
    ):
        return False
    request = getattr(exc, "request", None)
    request_url = getattr(request, "url", None)
    if request_url is None:
        return False
    try:
        parsed = urlsplit(str(request_url))
        expected = urlsplit(expected_chat_url)
        return (
            _url_origin(str(request_url)) == expected_origin
            and parsed.path.rstrip("/") == expected.path.rstrip("/")
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _paddle_message_payload(
    messages: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Validate the exact one-image/one-query shape emitted by PaddleX."""

    if not isinstance(messages, list) or len(messages) != 1:
        raise ValueError("SSE byte recovery requires one PaddleX message")
    message = messages[0]
    if not isinstance(message, dict) or set(message) != {"role", "content"}:
        raise ValueError("unsupported PaddleX message fields")
    if message.get("role") != "user":
        raise ValueError("SSE byte recovery requires a user message")
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 2:
        raise ValueError("SSE byte recovery requires one image and one query")
    image_part, text_part = content
    if (
        not isinstance(image_part, dict)
        or set(image_part) != {"type", "image_url"}
        or image_part.get("type") != "image_url"
        or not isinstance(image_part.get("image_url"), dict)
        or set(image_part["image_url"]) != {"url"}
    ):
        raise ValueError("unsupported PaddleX image message shape")
    if (
        not isinstance(text_part, dict)
        or set(text_part) != {"type", "text"}
        or text_part.get("type") != "text"
        or not isinstance(text_part.get("text"), str)
        or not text_part["text"].strip()
    ):
        raise ValueError("unsupported PaddleX text message shape")

    value = image_part["image_url"]["url"]
    if not isinstance(value, str) or not value.startswith("data:image/"):
        raise ValueError(
            "llama.cpp SSE byte recovery requires a data:image URL"
        )
    header, separator, encoded = value.partition(",")
    if not separator or not _IMAGE_DATA_HEADER.fullmatch(header) or not encoded:
        raise ValueError(
            "llama.cpp SSE byte recovery requires a base64 image URL"
        )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid base64 image sent to llama.cpp") from exc
    if not decoded:
        raise ValueError("empty base64 image sent to llama.cpp")
    return [encoded], [text_part["text"]]


class _LlamaSSEByteRecoveryTransport:
    """Recover llama.cpp output from audited native streaming token bytes.

    The normal PaddleX route remains authoritative. This transport is invoked
    only after llama.cpp has already generated OCR text but rejected it as an
    invalid PEG-native chat response. ``/apply-template`` keeps the exact model
    prompt. The non-OpenAI streaming ``/completion`` endpoint still applies its
    Content-only parser at finalization, so recovery never trusts its text
    fields. Instead, ``n_probs=1`` exposes each emitted ``text_to_send`` byte
    sequence in ``completion_probabilities[0].bytes``. A recovery is accepted
    only when a complete, structurally valid token stream ends in the exact
    known Content-only parser error.
    """

    def __init__(self, server_url: str, *, session: Any | None = None) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._root = _llama_server_root(server_url)
        self._props: dict[str, Any] | None = None

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _json_response(response: Any, endpoint: str) -> dict[str, Any]:
        if getattr(response, "status_code", None) != 200:
            detail = str(getattr(response, "text", ""))[:500]
            raise RuntimeError(
                f"llama-server {endpoint} request failed: {detail}"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"llama-server {endpoint} returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"llama-server {endpoint} returned a non-object")
        return payload

    def _server_props(self, timeout: float) -> dict[str, Any]:
        if self._props is None:
            response = self._session.get(
                f"{self._root}/props",
                timeout=timeout,
                allow_redirects=False,
            )
            self._props = self._json_response(response, "/props")
        return self._props

    @staticmethod
    def _completion_options(kwargs: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        options = dict(kwargs)
        timeout_value = options.pop("timeout", 600)
        if (
            isinstance(timeout_value, bool)
            or not isinstance(timeout_value, (int, float))
            or not math.isfinite(timeout_value)
            or timeout_value <= 0
        ):
            raise ValueError("llama.cpp SSE recovery timeout must be positive")
        timeout = float(timeout_value)

        stream = options.pop("stream", False)
        if stream is not False:
            raise ValueError("llama.cpp SSE recovery requires non-streaming chat input")
        completions = options.pop("n", 1)
        if (
            isinstance(completions, bool)
            or not isinstance(completions, int)
            or completions != 1
        ):
            raise ValueError("llama.cpp SSE recovery requires n=1")

        forbidden = (
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "functions",
            "function_call",
            "response_format",
            "logprobs",
            "top_logprobs",
        )
        present_forbidden = [name for name in forbidden if name in options]
        if present_forbidden:
            raise ValueError(
                "unsupported llama.cpp SSE recovery chat features: "
                + ", ".join(sorted(present_forbidden))
            )

        extra_body = options.pop("extra_body", {})
        if not isinstance(extra_body, dict):
            raise ValueError("llama.cpp extra_body must be an object")
        payload: dict[str, Any] = {
            "stream": True,
            "n_probs": 1,
            "n_cmpl": 1,
            "post_sampling_probs": False,
            "return_progress": False,
            "return_tokens": True,
        }

        max_tokens = options.pop("max_tokens", None)
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens <= 0
        ):
            raise ValueError("llama.cpp SSE recovery requires max_tokens")
        payload["n_predict"] = max_tokens

        temperature = options.pop("temperature", None)
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or temperature != 0
        ):
            raise ValueError(
                "llama.cpp SSE recovery requires deterministic temperature=0"
            )
        payload["temperature"] = temperature

        top_p = options.pop("top_p", None)
        if top_p is not None:
            if (
                isinstance(top_p, bool)
                or not isinstance(top_p, (int, float))
                or not math.isfinite(top_p)
                or not 0 <= top_p <= 1
            ):
                raise ValueError("invalid top_p for llama.cpp SSE recovery")
            payload["top_p"] = top_p

        stop = options.pop("stop", None)
        if stop is not None:
            if isinstance(stop, str) and stop:
                stop = [stop]
            elif not (
                isinstance(stop, list)
                and stop
                and all(isinstance(value, str) and value for value in stop)
            ):
                raise ValueError("invalid stop value for llama.cpp SSE recovery")
            payload["stop"] = stop

        seed = options.pop("seed", None)
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ValueError("invalid seed for llama.cpp SSE recovery")
            payload["seed"] = seed

        if options:
            raise ValueError(
                "unsupported llama.cpp SSE recovery options: "
                + ", ".join(sorted(options))
            )
        extra = dict(extra_body)
        repetition_penalty = extra.pop("repetition_penalty", None)
        if repetition_penalty is not None:
            if (
                isinstance(repetition_penalty, bool)
                or not isinstance(repetition_penalty, (int, float))
                or not math.isfinite(repetition_penalty)
                or repetition_penalty <= 0
            ):
                raise ValueError("invalid repetition_penalty")
            payload["repeat_penalty"] = repetition_penalty
        skip_special_tokens = extra.pop("skip_special_tokens", None)
        if skip_special_tokens is not None:
            if not isinstance(skip_special_tokens, bool):
                raise ValueError("skip_special_tokens must be boolean")
            payload["special"] = not bool(skip_special_tokens)
        if extra:
            raise ValueError(
                "unsupported llama.cpp SSE recovery extra_body options: "
                + ", ".join(sorted(extra))
            )
        return timeout, payload

    @staticmethod
    def _byte_array(value: Any, field: str) -> bytes:
        if not isinstance(value, list):
            raise RuntimeError(f"llama-server SSE {field} is not a byte array")
        if any(
            isinstance(item, bool)
            or not isinstance(item, int)
            or not 0 <= item <= 255
            for item in value
        ):
            raise RuntimeError(f"llama-server SSE {field} contains invalid bytes")
        return bytes(value)

    @staticmethod
    def _finite_number(value: Any, field: str) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise RuntimeError(f"llama-server SSE {field} is not finite")

    @classmethod
    def _token_bytes(
        cls,
        event: dict[str, Any],
        *,
        expected_token_count: int,
        max_tokens: int,
        allow_empty: bool = False,
    ) -> bytes:
        required_keys = {
            "index",
            "content",
            "tokens",
            "stop",
            "tokens_predicted",
            "tokens_evaluated",
            "completion_probabilities",
        }
        event_keys = set(event)
        if event_keys != required_keys and event_keys != required_keys | {"id_slot"}:
            raise RuntimeError(
                "llama-server SSE token event has unsupported fields or is missing fields"
            )
        if event.get("index") != 0 or event.get("stop") is not False:
            raise RuntimeError("llama-server SSE token event has invalid completion state")
        if not isinstance(event.get("content"), str):
            raise RuntimeError("llama-server SSE token content is not a string")
        if "id_slot" in event and event["id_slot"] is not None and (
            isinstance(event["id_slot"], bool)
            or not isinstance(event["id_slot"], int)
        ):
            raise RuntimeError(
                "llama-server SSE token event has invalid slot ID: "
                f"{event['id_slot']!r}"
            )
        token_count = event.get("tokens_predicted")
        if (
            isinstance(token_count, bool)
            or not isinstance(token_count, int)
            or token_count != expected_token_count
            or token_count > max_tokens
        ):
            raise RuntimeError("llama-server SSE token count is not consecutive")
        evaluated = event.get("tokens_evaluated")
        if (
            isinstance(evaluated, bool)
            or not isinstance(evaluated, int)
            or evaluated <= 0
        ):
            raise RuntimeError("llama-server SSE prompt token count is invalid")

        tokens = event.get("tokens")
        if (
            not isinstance(tokens, list)
            or len(tokens) != 1
            or isinstance(tokens[0], bool)
            or not isinstance(tokens[0], int)
            or tokens[0] < 0
        ):
            raise RuntimeError("llama-server SSE event must contain one token ID")

        probabilities = event.get("completion_probabilities")
        if not isinstance(probabilities, list) or len(probabilities) != 1:
            raise RuntimeError(
                "llama-server SSE event must contain one completion probability"
            )
        probability = probabilities[0]
        if not isinstance(probability, dict) or set(probability) != {
            "id",
            "token",
            "bytes",
            "logprob",
            "top_logprobs",
        }:
            raise RuntimeError("llama-server SSE completion probability is malformed")
        if probability["id"] != tokens[0] or isinstance(probability["id"], bool):
            raise RuntimeError("llama-server SSE probability token ID does not match")
        if not isinstance(probability["token"], str):
            raise RuntimeError("llama-server SSE probability token is not a string")
        cls._finite_number(probability["logprob"], "token logprob")
        emitted = cls._byte_array(probability["bytes"], "token bytes")
        if not emitted and not allow_empty:
            raise RuntimeError("llama-server SSE token bytes are empty")

        top = probability["top_logprobs"]
        if not isinstance(top, list) or len(top) != 1 or not isinstance(top[0], dict):
            raise RuntimeError("llama-server SSE must contain one top probability")
        candidate = top[0]
        if set(candidate) != {"id", "token", "bytes", "logprob"}:
            raise RuntimeError("llama-server SSE top probability is malformed")
        if candidate["id"] != tokens[0] or isinstance(candidate["id"], bool):
            raise RuntimeError("llama-server SSE top token does not match sampled token")
        if not isinstance(candidate["token"], str):
            raise RuntimeError("llama-server SSE top token is not a string")
        if candidate["token"] != probability["token"]:
            raise RuntimeError("llama-server SSE top token text does not match sampled token")
        cls._finite_number(candidate["logprob"], "top token logprob")
        if candidate["logprob"] != probability["logprob"]:
            raise RuntimeError("llama-server SSE top token score does not match sampled token")
        top_bytes = cls._byte_array(candidate["bytes"], "top token bytes")
        if top_bytes != emitted:
            raise RuntimeError("llama-server SSE top token bytes do not match sampled token")
        if not top_bytes and not allow_empty:
            raise RuntimeError("llama-server SSE top token bytes are empty")
        return emitted

    @staticmethod
    def _is_content_only_stream_error(event: dict[str, Any]) -> bool:
        if set(event) != {"error"} or not isinstance(event["error"], dict):
            return False
        error = event["error"]
        return (
            set(error) == {"code", "message", "type"}
            and type(error.get("code")) is int
            and error.get("code") == 500
            and error.get("type") == "server_error"
            and error.get("message") == _CONTENT_ONLY_FORMAT_ERROR
        )

    @staticmethod
    def _sse_events(response: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        data_lines: list[bytes] = []

        def finish_event() -> None:
            if not data_lines:
                return
            if len(data_lines) != 1:
                raise RuntimeError("llama-server SSE event has multiple data lines")
            raw = data_lines.pop()
            if raw == b"[DONE]":
                raise RuntimeError("llama-server SSE ended normally instead of failing")

            def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                value: dict[str, Any] = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError(f"duplicate JSON key: {key}")
                    value[key] = item
                return value

            try:
                event = json.loads(raw, object_pairs_hook=unique_object)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError("llama-server SSE returned invalid JSON") from exc
            if not isinstance(event, dict):
                raise RuntimeError("llama-server SSE event is not an object")
            events.append(event)

        try:
            lines = response.iter_lines(decode_unicode=False)
            for line in lines:
                if not isinstance(line, bytes):
                    raise RuntimeError("llama-server SSE yielded a non-byte line")
                if line == b"":
                    finish_event()
                    continue
                if line.startswith(b":"):
                    if data_lines:
                        raise RuntimeError("llama-server SSE comment interrupted an event")
                    continue
                if not line.startswith(b"data: "):
                    raise RuntimeError("llama-server SSE contains an unsupported field")
                data_lines.append(line[6:])
            finish_event()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("llama-server SSE stream was interrupted") from exc
        return events

    def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        timeout, completion_payload = self._completion_options(kwargs)
        images, original_texts = _paddle_message_payload(messages)
        props = self._server_props(timeout)
        modalities = props.get("modalities")
        if not (
            isinstance(modalities, dict) and modalities.get("vision") is True
        ):
            raise RuntimeError("llama-server does not report vision support")
        media_marker = props.get("media_marker")
        if not isinstance(media_marker, str) or not media_marker:
            raise RuntimeError("llama-server did not report a media marker")
        if any(media_marker in text for text in original_texts):
            raise RuntimeError("llama-server media marker occurs in original text")

        template_response = self._session.post(
            f"{self._root}/apply-template",
            json={"messages": messages},
            timeout=timeout,
            allow_redirects=False,
        )
        template = self._json_response(template_response, "/apply-template")
        prompt = template.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RuntimeError("llama-server /apply-template returned no prompt")
        if prompt.count(media_marker) != len(images):
            raise RuntimeError(
                "llama-server prompt media markers do not match input images"
            )

        completion_payload["prompt"] = {
            "prompt_string": prompt,
            "multimodal_data": images,
        }
        completion_response = self._session.post(
            f"{self._root}/completion",
            json=completion_payload,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            if getattr(completion_response, "status_code", None) != 200:
                detail = str(getattr(completion_response, "text", ""))[:500]
                raise RuntimeError(
                    f"llama-server /completion request failed: {detail}"
                )
            headers = getattr(completion_response, "headers", {})
            content_type = headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().casefold() != "text/event-stream":
                raise RuntimeError("llama-server /completion did not return SSE")
            events = self._sse_events(completion_response)
        finally:
            close = getattr(completion_response, "close", None)
            if callable(close):
                close()

        if len(events) < 2 or not self._is_content_only_stream_error(events[-1]):
            raise RuntimeError(
                "llama-server SSE did not end with the expected Content-only error"
            )
        output = bytearray()
        token_events = events[:-1]
        if not token_events:
            raise RuntimeError("llama-server SSE recovery produced no token events")
        expected_slot: int | None = None
        expected_slot_presence: bool | None = None
        expected_evaluated: int | None = None
        for token_index, event in enumerate(token_events):
            token_count = token_index + 1
            if "error" in event or event.get("stop") is True:
                raise RuntimeError("llama-server SSE ended before the expected parser error")
            is_terminal_empty = token_index == len(token_events) - 1
            emitted = self._token_bytes(
                event,
                expected_token_count=token_count,
                max_tokens=completion_payload["n_predict"],
                allow_empty=is_terminal_empty,
            )
            slot_present = "id_slot" in event
            if expected_slot_presence is None:
                expected_slot_presence = slot_present
                expected_slot = event.get("id_slot")
                expected_evaluated = event["tokens_evaluated"]
            elif (
                slot_present != expected_slot_presence
                or event.get("id_slot") != expected_slot
                or event["tokens_evaluated"] != expected_evaluated
            ):
                raise RuntimeError("llama-server SSE token stream changed slot or prompt count")
            if is_terminal_empty and not emitted and event["content"] != "":
                raise RuntimeError("llama-server SSE terminal empty token has content")
            output.extend(emitted)
        if not output:
            raise RuntimeError("llama-server SSE recovery produced no output bytes")
        content = bytes(output).decode("utf-8", errors="replace")
        if not content.strip():
            raise RuntimeError("llama-server SSE recovery produced empty content")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _PegNativeFallbackClient:
    """Preserve Paddle's chat path and retry only parser-rejected responses."""

    def __init__(
        self,
        delegate: Any,
        server_url: str,
        *,
        recovery_transport: _LlamaSSEByteRecoveryTransport | None = None,
    ) -> None:
        if getattr(delegate, "backend", None) != "llama-cpp-server":
            raise ValueError("PEG-native fallback requires llama-cpp-server")
        normalized_url = _normalized_openai_server_url(server_url)
        openai_client = getattr(delegate, "openai_client", None)
        delegate_url = getattr(openai_client, "base_url", None)
        if delegate_url is None or _normalized_openai_server_url(
            str(delegate_url)
        ) != normalized_url:
            raise ValueError("PaddleX client and SSE recovery server URLs differ")
        self.backend = delegate.backend
        self._delegate = delegate
        self._origin = _url_origin(normalized_url)
        self._chat_url = f"{normalized_url}/chat/completions"
        self._recovery = recovery_transport or _LlamaSSEByteRecoveryTransport(
            normalized_url
        )
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._audit_lock = threading.Lock()
        self._audit_events: list[dict[str, Any]] = []

    def _complete(self, messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> Any:
        try:
            return self._delegate.create_chat_completion(
                messages,
                return_future=False,
                **kwargs,
            )
        except Exception as exc:
            if not _is_peg_native_format_error(
                exc,
                expected_origin=self._origin,
                expected_chat_url=self._chat_url,
            ):
                raise
            print(
                "paddleocr-worker: llama.cpp rejected generated OCR text as "
                "PEG-native chat output; retrying the same block via audited "
                "SSE token bytes",
                flush=True,
            )
            try:
                result = self._recovery.create_chat_completion(messages, **kwargs)
            except Exception as fallback_exc:
                print(
                    "paddleocr-worker: SSE byte recovery rejected; "
                    f"preserving original page failure: {fallback_exc}",
                    flush=True,
                )
                raise exc from fallback_exc
            recovered_content = result.choices[0].message.content
            if not isinstance(recovered_content, str):
                raise exc
            with self._audit_lock:
                self._audit_events.append(
                    {
                        "reason": "expected_peg_native_format",
                        "transport": "llama_cpp_apply_template_sse_token_bytes",
                        "decoded_output": recovered_content,
                        "replacement_character_count": recovered_content.count("\ufffd"),
                    }
                )
            return result

    def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        return_future: bool = False,
        **kwargs: Any,
    ) -> Any:
        if return_future:
            return self._executor.submit(self._complete, messages, dict(kwargs))
        return self._complete(messages, dict(kwargs))

    def drain_audit_events(self) -> list[dict[str, Any]]:
        with self._audit_lock:
            events = list(self._audit_events)
            self._audit_events.clear()
        return events

    def close(self) -> None:
        self._executor.shutdown(wait=True)
        try:
            self._recovery.close()
        finally:
            self._delegate.close()


def _install_peg_native_fallback(
    pipeline: Any, server_url: str
) -> _PegNativeFallbackClient:
    paddlex_pipeline = getattr(pipeline, "paddlex_pipeline", None)
    vl_rec_model = getattr(paddlex_pipeline, "vl_rec_model", None)
    delegate = getattr(vl_rec_model, "genai_client", None)
    if delegate is None or getattr(delegate, "backend", None) != "llama-cpp-server":
        raise RuntimeError("unable to locate PaddleOCR llama.cpp GenAI client")
    client = _PegNativeFallbackClient(delegate, server_url)
    vl_rec_model._genai_client = client
    return client


def _json_default(value: Any) -> Any:
    """Serialize NumPy scalars/arrays emitted by PaddleX result objects."""
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _result_value(result: Any) -> Any:
    # PaddleX result objects subclass dict but expose a normalized `.json`
    # representation. Treat only a plain dict as already serialized.
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
    save = getattr(result, "save_to_json", None)
    if callable(save):
        with tempfile.TemporaryDirectory(prefix="paddleocr-worker-") as directory:
            save(save_path=directory)
            files = sorted(Path(directory).glob("*.json"))
            if files:
                return json.loads(files[0].read_text(encoding="utf-8"))
    raise TypeError(f"unsupported PaddleOCR result object: {type(result).__name__}")


def _provider_markdown_text(result: Any) -> str | None:
    """Return Paddle's Markdown text without retaining non-JSON image data."""
    try:
        markdown = (
            result.get("markdown")
            if type(result) is dict
            else getattr(result, "markdown", None)
        )
        markdown = markdown() if callable(markdown) else markdown
    except Exception:
        # Markdown is an auxiliary raw artifact. Its projection must not turn
        # an otherwise valid structured provider result into a failed page.
        return None
    if isinstance(markdown, str):
        return markdown
    if isinstance(markdown, dict):
        for key in ("text", "markdown_texts"):
            text = markdown.get(key)
            if isinstance(text, str):
                return text
    return None


def _attach_provider_markdown(
    layout_result: dict[str, Any], markdown_text: str | None
) -> dict[str, Any]:
    existing_text = _provider_markdown_text(layout_result)
    if existing_text or not markdown_text:
        return layout_result
    normalized = dict(layout_result)
    normalized["markdown"] = {"text": markdown_text, "images": {}}
    return normalized


def _layout_result(result: Any) -> dict[str, Any]:
    payload = _result_value(result)
    markdown_text = _provider_markdown_text(result)
    if not isinstance(payload, dict):
        raise TypeError("PaddleOCR returned a non-object result")
    layouts = payload.get("layoutParsingResults")
    if isinstance(layouts, list):
        if len(layouts) != 1:
            raise ValueError("single-page request returned multiple layout results")
        normalized = _layout_result(layouts[0])
        return _attach_provider_markdown(normalized, markdown_text)
    for key in ("result", "res"):
        if isinstance(payload.get(key), dict):
            normalized = _layout_result(payload[key])
            return _attach_provider_markdown(normalized, markdown_text)
    if "prunedResult" in payload:
        return _attach_provider_markdown(payload, markdown_text)
    if "parsing_res_list" in payload:
        normalized = dict(payload)
        normalized["prunedResult"] = {
            "parsing_res_list": payload.get("parsing_res_list", [])
        }
        return _attach_provider_markdown(normalized, markdown_text)
    raise ValueError("PaddleOCR result has no parsing result blocks")


class WorkerServer(HTTPServer):
    pipeline: Any
    fallback_client: _PegNativeFallbackClient


class Handler(BaseHTTPRequestHandler):
    server: WorkerServer

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            default=_json_default,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        if self.path == "/health":
            self._json_response(200, {"status": "ready"})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        if self.path != "/predict":
            self._json_response(404, {"error": "not found"})
            return
        try:
            self.server.fallback_client.drain_audit_events()
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            input_path = Path(request["path"])
            if not input_path.is_file():
                raise FileNotFoundError(input_path)
            outputs = list(self.server.pipeline.predict(str(input_path)))
            if len(outputs) != 1:
                raise ValueError(
                    f"single-page input returned {len(outputs)} PaddleOCR results"
                )
            recoveries = self.server.fallback_client.drain_audit_events()
            metadata = {}
            if recoveries:
                metadata["llama_cpp_sse_byte_recovery"] = {
                    "count": len(recoveries),
                    "reason": "expected_peg_native_format",
                    "transport": "apply-template+sse-token-bytes",
                    "outputs": [
                        {
                            "decoded_output": event["decoded_output"],
                            "replacement_character_count": event[
                                "replacement_character_count"
                            ],
                        }
                        for event in recoveries
                    ],
                }
            self._json_response(
                200,
                {
                    "layout_result": _layout_result(outputs[0]),
                    "metadata": metadata,
                },
            )
        except Exception as exc:
            self.server.fallback_client.drain_audit_events()
            self._json_response(
                500,
                {"error": f"{type(exc).__name__}: {exc}"},
            )

    def log_message(self, format: str, *args: Any) -> None:
        print(f"paddleocr-worker: {format % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pipeline-version", default="v1.6")
    parser.add_argument("--llama-server-url", required=True)
    args = parser.parse_args()

    from paddleocr import PaddleOCRVL

    pipeline = PaddleOCRVL(
        pipeline_version=args.pipeline_version,
        vl_rec_backend="llama-cpp-server",
        vl_rec_server_url=args.llama_server_url,
        vl_rec_max_concurrency=1,
    )
    fallback_client = _install_peg_native_fallback(pipeline, args.llama_server_url)
    server = WorkerServer((args.host, args.port), Handler)
    server.pipeline = pipeline
    server.fallback_client = fallback_client
    print(f"paddleocr-worker ready on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
