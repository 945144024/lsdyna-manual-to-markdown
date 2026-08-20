"""Tests for the local PaddleOCR-VL provider and runtime boundary."""

import copy
import json
from types import SimpleNamespace

import pytest

from lsdyna_manual.config import LocalProviderConfig, ParserConfig
from lsdyna_manual.providers.base import ProviderError
from lsdyna_manual.providers.local_runtime import LocalRuntimeError, LocalRuntimeManager
from lsdyna_manual.providers.paddleocr_vl_local import PaddleOCRVLLocalProvider
from lsdyna_manual.providers.paddleocr_worker import (
    _LlamaSSEByteRecoveryTransport,
    _PegNativeFallbackClient,
    _json_default,
    _layout_result,
    _llama_server_root,
    _result_value,
)


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


def test_worker_resolves_non_openai_llama_server_root():
    assert _llama_server_root("http://127.0.0.1:8111/v1") == (
        "http://127.0.0.1:8111"
    )
    assert _llama_server_root("http://server/prefix/v1/") == (
        "http://server/prefix"
    )
    with pytest.raises(ValueError, match="end with /v1"):
        _llama_server_root("http://127.0.0.1:8111")
    with pytest.raises(ValueError, match="absolute HTTP"):
        _llama_server_root("http://user:password@127.0.0.1:8111/v1")


class _FakeHTTPResponse:
    def __init__(
        self,
        payload=None,
        *,
        text="",
        status_code=200,
        headers=None,
        lines=None,
        iter_error=None,
    ):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.lines = list(lines or [])
        self.iter_error = iter_error
        self.closed = False

    def json(self):
        return self._payload

    def iter_lines(self, *, decode_unicode):
        assert decode_unicode is False
        for line in self.lines:
            yield line
        if self.iter_error is not None:
            raise self.iter_error

    def close(self):
        self.closed = True


def _token_event(token_count, emitted, *, token_id=None):
    token_id = 1000 + token_count if token_id is None else token_id
    display = emitted.decode("utf-8", errors="replace")
    probability = {
        "id": token_id,
        "token": display,
        "bytes": list(emitted),
        "logprob": -0.01,
        "top_logprobs": [
            {
                "id": token_id,
                "token": display,
                "bytes": list(emitted),
                "logprob": -0.01,
            }
        ],
    }
    return {
        "index": 0,
        "content": display,
        "tokens": [token_id],
        "stop": False,
        "id_slot": 0,
        "tokens_predicted": token_count,
        "tokens_evaluated": 416,
        "completion_probabilities": [probability],
    }


def _content_only_error_event(*, message=None):
    return {
        "error": {
            "code": 500,
            "message": message
            or (
                "The model produced output that does not match the expected "
                "Content-only format"
            ),
            "type": "server_error",
        }
    }


def _default_sse_events():
    return [
        _token_event(1, b"*"),
        _token_event(2, b"\x95"),
        _token_event(3, b"WORD"),
        # llama.cpp emits an empty text_to_send for the terminal special token.
        _token_event(4, b""),
        _content_only_error_event(),
    ]


def _sse_lines(events):
    lines = []
    for event in events:
        lines.extend(
            [
                b"data: "
                + json.dumps(event, ensure_ascii=True, separators=(",", ":")).encode(
                    "ascii"
                ),
                b"",
            ]
        )
    return lines


class _FakeLlamaSession:
    def __init__(
        self,
        *,
        prompt="<media>\nOCR",
        props=None,
        statuses=None,
        events=None,
        completion_headers=None,
        completion_lines=None,
        completion_iter_error=None,
    ):
        self.prompt = prompt
        self.props = props or {
            "modalities": {"vision": True, "audio": False},
            "media_marker": "<media>",
        }
        self.statuses = statuses or {}
        self.events = _default_sse_events() if events is None else events
        self.completion_headers = completion_headers or {
            "Content-Type": "text/event-stream; charset=utf-8"
        }
        self.completion_lines = completion_lines
        self.completion_iter_error = completion_iter_error
        self.calls = []
        self.closed = False

    def get(self, url, *, timeout, allow_redirects):
        self.calls.append(("GET", url, None, timeout, allow_redirects))
        return _FakeHTTPResponse(
            self.props,
            status_code=self.statuses.get("props", 200),
        )

    def post(self, url, *, json, timeout, allow_redirects, stream=False):
        self.calls.append(("POST", url, json, timeout, allow_redirects, stream))
        if url.endswith("/apply-template"):
            return _FakeHTTPResponse(
                {"prompt": self.prompt},
                status_code=self.statuses.get("apply-template", 200),
            )
        if url.endswith("/completion"):
            return _FakeHTTPResponse(
                text="completion failed",
                status_code=self.statuses.get("completion", 200),
                headers=self.completion_headers,
                lines=(
                    self.completion_lines
                    if self.completion_lines is not None
                    else _sse_lines(self.events)
                ),
                iter_error=self.completion_iter_error,
            )
        raise AssertionError(url)

    def close(self):
        self.closed = True


def _vision_messages():
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,YWJj"},
                },
                {"type": "text", "text": "OCR"},
            ],
        }
    ]


def _completion_kwargs():
    return {
        "timeout": 12,
        "max_tokens": 256,
        "temperature": 0,
        "top_p": 0.9,
        "extra_body": {"skip_special_tokens": True},
    }


class _PegNativeError(RuntimeError):
    def __init__(
        self,
        *,
        status_code=500,
        message=(
            "The model produced output that does not match the expected "
            "peg-native format"
        ),
        code=500,
        error_type="server_error",
        url="http://127.0.0.1:8111/v1/chat/completions",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"code": code, "message": message, "type": error_type}
        self.request = SimpleNamespace(url=url)


class _Delegate:
    backend = "llama-cpp-server"

    def __init__(self, result_or_error):
        self.openai_client = SimpleNamespace(
            base_url="http://127.0.0.1:8111/v1/"
        )
        self.result_or_error = result_or_error
        self.calls = 0
        self.closed = False

    def create_chat_completion(self, *_args, **_kwargs):
        self.calls += 1
        if isinstance(self.result_or_error, Exception):
            raise self.result_or_error
        return self.result_or_error

    def close(self):
        self.closed = True


class _RecoveryTransport:
    def __init__(self, result_or_error="byte recovered text"):
        self.result_or_error = result_or_error
        self.calls = []
        self.closed = False

    def create_chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if isinstance(self.result_or_error, Exception):
            raise self.result_or_error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.result_or_error)
                )
            ]
        )

    def close(self):
        self.closed = True


def test_worker_sse_byte_recovery_preserves_prompt_image_and_output():
    session = _FakeLlamaSession()
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=session,
    )

    result = transport.create_chat_completion(
        _vision_messages(),
        **_completion_kwargs(),
    )

    assert result.choices[0].message.content == "*\ufffdWORD"
    method, url, payload, timeout, allow_redirects, stream = session.calls[-1]
    assert (method, url, timeout, allow_redirects, stream) == (
        "POST",
        "http://127.0.0.1:8111/completion",
        12,
        False,
        True,
    )
    assert payload == {
        "stream": True,
        "n_probs": 1,
        "n_cmpl": 1,
        "post_sampling_probs": False,
        "return_progress": False,
        "return_tokens": True,
        "n_predict": 256,
        "temperature": 0,
        "top_p": 0.9,
        "special": False,
        "prompt": {
            "prompt_string": "<media>\nOCR",
            "multimodal_data": ["YWJj"],
        },
    }


def test_worker_sse_byte_recovery_accepts_consistently_absent_slot_metadata():
    events = _default_sse_events()
    for event in events[:-1]:
        event.pop("id_slot")
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(events=events),
    )

    result = transport.create_chat_completion(
        _vision_messages(), **_completion_kwargs()
    )

    assert result.choices[0].message.content == "*\ufffdWORD"


def test_worker_sse_byte_recovery_accepts_consistently_null_slot_metadata():
    events = _default_sse_events()
    for event in events[:-1]:
        event["id_slot"] = None
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(events=events),
    )

    result = transport.create_chat_completion(
        _vision_messages(), **_completion_kwargs()
    )

    assert result.choices[0].message.content == "*\ufffdWORD"


def test_worker_sse_byte_recovery_accepts_consistent_negative_slot_sentinel():
    events = _default_sse_events()
    for event in events[:-1]:
        event["id_slot"] = -1
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(events=events),
    )

    result = transport.create_chat_completion(
        _vision_messages(), **_completion_kwargs()
    )

    assert result.choices[0].message.content == "*\ufffdWORD"


def test_worker_sse_byte_recovery_maps_supported_generation_options():
    session = _FakeLlamaSession()
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=session,
    )

    transport.create_chat_completion(
        _vision_messages(),
        timeout=12,
        max_tokens=256,
        temperature=0,
        top_p=0.8,
        stop="END",
        seed=42,
        extra_body={
            "skip_special_tokens": False,
            "repetition_penalty": 1.05,
        },
    )

    payload = session.calls[-1][2]
    assert payload["stop"] == ["END"]
    assert payload["seed"] == 42
    assert payload["repeat_penalty"] == 1.05
    assert payload["special"] is True


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda event: event.update({"model": "unexpected"}), "unsupported fields"),
        (lambda event: event.update({"index": 1}), "completion state"),
        (lambda event: event.update({"stop": True}), "ended before"),
        (lambda event: event.update({"content": None}), "content is not a string"),
        (lambda event: event.update({"id_slot": True}), "slot ID"),
        (lambda event: event.update({"tokens_predicted": 2}), "not consecutive"),
        (lambda event: event.update({"tokens_predicted": 257}), "not consecutive"),
        (lambda event: event.update({"tokens_evaluated": 0}), "prompt token count"),
        (lambda event: event.update({"tokens": []}), "one token ID"),
        (
            lambda event: event.update({"completion_probabilities": []}),
            "one completion probability",
        ),
        (
            lambda event: event["completion_probabilities"][0].update({"id": 2}),
            "probability token ID",
        ),
        (
            lambda event: event["completion_probabilities"][0].update(
                {"bytes": [256]}
            ),
            "invalid bytes",
        ),
        (
            lambda event: event["completion_probabilities"][0].update(
                {"logprob": float("nan")}
            ),
            "not finite",
        ),
        (
            lambda event: event["completion_probabilities"][0].update(
                {"top_logprobs": []}
            ),
            "one top probability",
        ),
        (
            lambda event: event["completion_probabilities"][0]["top_logprobs"][
                0
            ].update({"id": 2}),
            "top token does not match",
        ),
        (
            lambda event: event["completion_probabilities"][0]["top_logprobs"][
                0
            ].update({"token": "different"}),
            "top token text does not match",
        ),
        (
            lambda event: event["completion_probabilities"][0]["top_logprobs"][
                0
            ].update({"bytes": [65]}),
            "top token bytes do not match",
        ),
        (
            lambda event: event["completion_probabilities"][0]["top_logprobs"][
                0
            ].update({"logprob": -0.02}),
            "top token score does not match",
        ),
        (
            lambda event: event["completion_probabilities"][0].update({"bytes": []}),
            "token bytes are empty",
        ),
    ],
)
def test_worker_sse_byte_recovery_rejects_malformed_token_events(mutate, message):
    events = _default_sse_events()
    mutate(events[0])
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(events=events),
    )

    with pytest.raises(RuntimeError, match=message):
        transport.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )


@pytest.mark.parametrize(
    ("lines", "iter_error", "message"),
    [
        ([b"data: {", b""], None, "invalid JSON"),
        ([b"data: {\"index\":0,\"index\":1}", b""], None, "invalid JSON"),
        ([b"event: message", b""], None, "unsupported field"),
        ([b"data: {}", b"data: {}", b""], None, "multiple data lines"),
        (["data: {}", b""], None, "non-byte line"),
        ([b"data: [DONE]", b""], None, "ended normally"),
        ([], ConnectionError("cut stream"), "stream was interrupted"),
    ],
)
def test_worker_sse_byte_recovery_rejects_invalid_sse_framing(
    lines, iter_error, message
):
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(
            completion_lines=lines,
            completion_iter_error=iter_error,
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        transport.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([_content_only_error_event()], "did not end"),
        (
            [_token_event(1, b"text")],
            "did not end with the expected Content-only error",
        ),
        (
            [
                _token_event(1, b"text"),
                _content_only_error_event(message="different server failure"),
            ],
            "did not end with the expected Content-only error",
        ),
        (
            [
                _content_only_error_event(),
                _token_event(1, b"text"),
                _content_only_error_event(),
            ],
            "ended before the expected parser error",
        ),
        (
            [_token_event(1, b""), _content_only_error_event()],
            "produced no output bytes",
        ),
    ],
)
def test_worker_sse_byte_recovery_requires_complete_exact_failure(events, message):
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(events=events),
    )

    with pytest.raises(RuntimeError, match=message):
        transport.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda events: events[1].update({"id_slot": 1}),
            "changed slot or prompt count",
        ),
        (
            lambda events: events[1].pop("id_slot"),
            "changed slot or prompt count",
        ),
        (
            lambda events: events[1].update({"tokens_evaluated": 417}),
            "changed slot or prompt count",
        ),
        (
            lambda events: events[-2].update({"content": "not empty"}),
            "terminal empty token has content",
        ),
    ],
)
def test_worker_sse_byte_recovery_requires_one_consistent_token_stream(
    mutate, message
):
    events = _default_sse_events()
    mutate(events)
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(events=events),
    )

    with pytest.raises(RuntimeError, match=message):
        transport.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )


def test_worker_sse_byte_recovery_rejects_non_sse_response():
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(
            completion_headers={"Content-Type": "application/json"}
        ),
    )

    with pytest.raises(RuntimeError, match="did not return SSE"):
        transport.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )


def test_worker_sse_byte_recovery_requires_exact_media_marker_count():
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(prompt="OCR without image marker"),
    )

    with pytest.raises(RuntimeError, match="media markers"):
        transport.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"stream": True}, "streaming"),
        ({"n": 2}, "n=1"),
        ({"n": 1.0}, "n=1"),
        ({"response_format": {"type": "json_object"}}, "chat features"),
        ({"temperature": 0.1}, "temperature=0"),
        ({"max_tokens": None}, "max_tokens"),
        ({"stop": ""}, "invalid stop"),
        ({"extra_body": {"grammar": "root ::= text"}}, "extra_body"),
    ],
)
def test_worker_sse_byte_recovery_rejects_non_equivalent_options(changes, message):
    kwargs = _completion_kwargs()
    kwargs.update(changes)
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(),
    )

    with pytest.raises(ValueError, match=message):
        transport.create_chat_completion(_vision_messages(), **kwargs)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda messages: messages[0]["content"][0]["image_url"].update(
                {"url": "https://example.invalid/page.png"}
            ),
            "data:image",
        ),
        (
            lambda messages: messages[0]["content"][0]["image_url"].update(
                {"url": "/tmp/page.png"}
            ),
            "data:image",
        ),
        (
            lambda messages: messages[0]["content"][0]["image_url"].update(
                {"url": "data:image/png;base64,not-base64!"}
            ),
            "invalid base64",
        ),
        (
            lambda messages: messages[0]["content"].reverse(),
            "image message shape",
        ),
        (
            lambda messages: messages.append(copy.deepcopy(messages[0])),
            "one PaddleX message",
        ),
    ],
)
def test_worker_sse_byte_recovery_rejects_unsupported_messages(mutate, message):
    messages = _vision_messages()
    mutate(messages)
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(),
    )

    with pytest.raises(ValueError, match=message):
        transport.create_chat_completion(messages, **_completion_kwargs())


@pytest.mark.parametrize(
    ("session", "message"),
    [
        (
            _FakeLlamaSession(
                props={"modalities": {"vision": False}, "media_marker": "<media>"}
            ),
            "vision support",
        ),
        (
            _FakeLlamaSession(
                props={"modalities": {"vision": True}, "media_marker": ""}
            ),
            "media marker",
        ),
        (
            _FakeLlamaSession(statuses={"apply-template": 500}),
            "/apply-template request failed",
        ),
        (
            _FakeLlamaSession(prompt="   "),
            "returned no prompt",
        ),
        (
            _FakeLlamaSession(statuses={"completion": 500}),
            "/completion request failed",
        ),
    ],
)
def test_worker_sse_byte_recovery_fails_closed_on_native_endpoint_gates(
    session, message
):
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=session,
    )

    with pytest.raises(RuntimeError, match=message):
        transport.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )


def test_worker_sse_byte_recovery_rejects_marker_in_original_text():
    messages = _vision_messages()
    messages[0]["content"][1]["text"] = "OCR <media>"
    transport = _LlamaSSEByteRecoveryTransport(
        "http://127.0.0.1:8111/v1",
        session=_FakeLlamaSession(),
    )

    with pytest.raises(RuntimeError, match="original text"):
        transport.create_chat_completion(messages, **_completion_kwargs())


def test_worker_retries_only_peg_native_chat_parser_failures():
    delegate = _Delegate(_PegNativeError())
    recovery = _RecoveryTransport()
    client = _PegNativeFallbackClient(
        delegate,
        "http://127.0.0.1:8111/v1",
        recovery_transport=recovery,
    )

    result = client.create_chat_completion(
        _vision_messages(), return_future=True, **_completion_kwargs()
    ).result(timeout=2)
    events = client.drain_audit_events()
    client.close()

    assert result.choices[0].message.content == "byte recovered text"
    assert len(recovery.calls) == 1
    assert events == [
        {
            "reason": "expected_peg_native_format",
            "transport": "llama_cpp_apply_template_sse_token_bytes",
            "decoded_output": "byte recovered text",
            "replacement_character_count": 0,
        }
    ]
    assert client.drain_audit_events() == []
    assert recovery.closed is True
    assert delegate.closed is True


def test_worker_normal_success_never_invokes_sse_byte_recovery():
    normal = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="normal"))]
    )
    delegate = _Delegate(normal)
    recovery = _RecoveryTransport(AssertionError("recovery must not run"))
    client = _PegNativeFallbackClient(
        delegate,
        "http://127.0.0.1:8111/v1",
        recovery_transport=recovery,
    )

    result = client.create_chat_completion(
        _vision_messages(), return_future=True, **_completion_kwargs()
    ).result(timeout=2)
    client.close()

    assert result.choices[0].message.content == "normal"
    assert recovery.calls == []


def test_worker_keeps_sibling_futures_independent_during_recovery():
    normal = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="normal"))]
    )

    class SequenceDelegate(_Delegate):
        def __init__(self):
            super().__init__(normal)
            self.results = [normal, _PegNativeError()]

        def create_chat_completion(self, *_args, **_kwargs):
            self.calls += 1
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    recovery = _RecoveryTransport("recovered")
    client = _PegNativeFallbackClient(
        SequenceDelegate(),
        "http://127.0.0.1:8111/v1",
        recovery_transport=recovery,
    )

    first = client.create_chat_completion(
        _vision_messages(), return_future=True, **_completion_kwargs()
    )
    second = client.create_chat_completion(
        _vision_messages(), return_future=True, **_completion_kwargs()
    )

    assert first.result(timeout=2).choices[0].message.content == "normal"
    assert second.result(timeout=2).choices[0].message.content == "recovered"
    assert len(recovery.calls) == 1
    client.close()


@pytest.mark.parametrize(
    "error",
    [
        _PegNativeError(status_code=400),
        _PegNativeError(message="server is out of memory"),
        _PegNativeError(code=501),
        _PegNativeError(error_type="invalid_request_error"),
        _PegNativeError(url="http://127.0.0.1:9111/v1/chat/completions"),
        _PegNativeError(url="http://127.0.0.1:8111/v1/completions"),
    ],
)
def test_worker_does_not_fallback_without_exact_structured_error(error):
    recovery = _RecoveryTransport(AssertionError("recovery must not run"))
    client = _PegNativeFallbackClient(
        _Delegate(error),
        "http://127.0.0.1:8111/v1",
        recovery_transport=recovery,
    )

    with pytest.raises(type(error), match=str(error)):
        client.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )
    client.close()

    assert recovery.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {
            "code": "500",
            "message": (
                "The model produced output that does not match the expected "
                "peg-native format"
            ),
            "type": "server_error",
        },
        {
            "code": 500,
            "message": (
                "the model produced output that does not match the expected "
                "peg-native format"
            ),
            "type": "server_error",
        },
        {
            "code": 500,
            "message": (
                "The model produced output that does not match the expected "
                "peg-native format"
            ),
            "type": "server_error",
            "detail": "unexpected",
        },
    ],
)
def test_worker_recovery_gate_requires_exact_peg_error_object(body):
    error = _PegNativeError()
    error.body = body
    recovery = _RecoveryTransport(AssertionError("recovery must not run"))
    client = _PegNativeFallbackClient(
        _Delegate(error),
        "http://127.0.0.1:8111/v1",
        recovery_transport=recovery,
    )

    with pytest.raises(_PegNativeError):
        client.create_chat_completion(_vision_messages(), **_completion_kwargs())
    client.close()

    assert recovery.calls == []


def test_worker_does_not_fallback_for_unrelated_server_error():
    delegate = _Delegate(RuntimeError("server is out of memory"))
    recovery = _RecoveryTransport(AssertionError("recovery must not run"))
    client = _PegNativeFallbackClient(
        delegate,
        "http://127.0.0.1:8111/v1",
        recovery_transport=recovery,
    )

    with pytest.raises(RuntimeError, match="out of memory"):
        client.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )
    client.close()

    assert recovery.calls == []


def test_worker_preserves_original_peg_failure_when_sse_gate_fails():
    original = _PegNativeError()
    recovery = _RecoveryTransport(RuntimeError("media markers do not match"))
    client = _PegNativeFallbackClient(
        _Delegate(original),
        "http://127.0.0.1:8111/v1",
        recovery_transport=recovery,
    )

    with pytest.raises(_PegNativeError) as caught:
        client.create_chat_completion(
            _vision_messages(), **_completion_kwargs()
        )
    client.close()

    assert caught.value is original
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert len(recovery.calls) == 1


def test_worker_rejects_delegate_on_a_different_server():
    delegate = _Delegate(SimpleNamespace())
    delegate.openai_client.base_url = "http://127.0.0.1:9111/v1"

    with pytest.raises(ValueError, match="URLs differ"):
        _PegNativeFallbackClient(
            delegate,
            "http://127.0.0.1:8111/v1",
            recovery_transport=_RecoveryTransport(),
        )


def test_worker_preserves_provider_markdown_text_without_image_objects():
    class PaddleResult(dict):
        @property
        def json(self):
            return {
                "res": {
                    "parsing_res_list": [
                        {"block_label": "text", "block_content": "raw block"}
                    ]
                }
            }

        @property
        def markdown(self):
            return {
                "markdown_texts": "# Provider Markdown\n\nBody",
                "markdown_images": {"figure.png": object()},
            }

    layout = _layout_result(PaddleResult())

    assert layout["markdown"] == {
        "text": "# Provider Markdown\n\nBody",
        "images": {},
    }
    assert layout["prunedResult"]["parsing_res_list"][0]["block_content"] == (
        "raw block"
    )


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
    provider._predict = lambda _path: (
        {
            "parsing_res_list": [
                {"block_label": "text", "block_content": "local page"}
            ],
            "prunedResult": {
                "parsing_res_list": [
                    {"block_label": "text", "block_content": "local page"}
                ]
            },
            "markdown": {"text": "local page", "images": {}},
        },
        {
            "llama_cpp_sse_byte_recovery": {
                "count": 1,
                "reason": "expected_peg_native_format",
            }
        },
    )
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
    assert result.metadata["transport"][
        "llama_cpp_sse_byte_recovery"
    ]["count"] == 1
    assert layout["prunedResult"]["parsing_res_list"][0]["block_content"] == "local page"
    assert [phase for phase, _details in events] == ["local_started", "local_done"]
