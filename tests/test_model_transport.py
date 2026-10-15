from __future__ import annotations

import pytest
import requests

from scansci_html.llm import OpenAIResponsesJsonClient, complete_chat_text, stream_chat_text
from scansci_html.model_transport import (
    ANTHROPIC_MESSAGES,
    CHAT_COMPLETIONS,
    RESPONSES,
    ModelCapabilityError,
    ModelRequest,
    capabilities_for,
    complete_model,
    opencode_go_api_surface,
    provider_request_headers,
    select_api_surface,
    stream_model_events,
)


class FakeResponse:
    def __init__(self, payload: dict, lines: list[bytes] | None = None):
        self.payload = payload
        self.lines = lines or []
        self.closed = False
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_lines(self, *, decode_unicode):
        assert decode_unicode is False
        return iter(self.lines)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, *, headers, json, timeout, **kwargs):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout, **kwargs})
        return self.response


class SequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, headers, json, timeout, **kwargs):
        self.calls.append({"url": url, "json": json, **kwargs})
        return self.responses.pop(0)


def _request(session: FakeSession, **kwargs) -> ModelRequest:
    return ModelRequest(
        provider_kind="openai-compatible",
        provider_id="gateway",
        base_url="https://example.test/v1",
        api_key="secret",
        model="responses-model",
        messages=[{"role": "user", "content": "hello"}],
        api_surface=RESPONSES,
        responses_enabled=True,
        session=session,
        **kwargs,
    )


def test_select_api_surface_never_silently_downgrades_generic_gateway():
    assert select_api_surface(
        "auto",
        provider_kind="openai-compatible",
        provider_id="gateway",
        model="model",
    ) == CHAT_COMPLETIONS
    with pytest.raises(ModelCapabilityError):
        select_api_surface(
            RESPONSES,
            provider_kind="openai-compatible",
            provider_id="gateway",
            model="model",
        )


@pytest.mark.parametrize(
    ("model", "surface"),
    [
        ("grok-4.6", RESPONSES),
        ("gpt-5.6-luna", RESPONSES),
        ("muse-spark-1.2-contributor", RESPONSES),
        ("glm-5.3-flash", CHAT_COMPLETIONS),
        ("deepseek-v4-flash-vision-exp", CHAT_COMPLETIONS),
        ("minimax-m3", ANTHROPIC_MESSAGES),
        ("qwen3.8-flash", ANTHROPIC_MESSAGES),
    ],
)
def test_opencode_go_routes_each_model_to_its_documented_api_surface(model, surface):
    assert opencode_go_api_surface(model) == surface
    assert select_api_surface(
        "auto",
        provider_kind="openai-compatible",
        provider_id="opencode-go",
        model=model,
    ) == surface
    # Existing callers use Chat Completions as their default argument. Keep
    # that legacy default safe for OpenCode Go's non-Chat model families.
    assert select_api_surface(
        CHAT_COMPLETIONS,
        provider_kind="openai-compatible",
        provider_id="opencode-go",
        model=model,
    ) == surface
    assert surface in capabilities_for(
        "openai-compatible",
        model,
        provider_id="opencode-go",
    ).api_surfaces


def test_opencode_go_anthropic_model_posts_messages_payload():
    response = FakeResponse({"id": "msg_go", "content": [{"type": "text", "text": "ready"}], "usage": {"input_tokens": 3, "output_tokens": 2}})
    session = FakeSession(response)
    result = complete_model(
        ModelRequest(
            provider_kind="openai-compatible",
            provider_id="opencode-go",
            base_url="https://opencode.example/v1",
            api_key="go-secret",
            model="minimax-m3",
            messages=[{"role": "user", "content": "hello"}],
            api_surface="auto",
            session=session,
        )
    )
    assert result.text == "ready"
    assert result.api_surface == ANTHROPIC_MESSAGES
    assert session.calls[0]["url"] == "https://opencode.example/v1/messages"
    assert session.calls[0]["headers"]["x-api-key"] == "go-secret"
    assert "Authorization" not in session.calls[0]["headers"]
    assert complete_model(
        ModelRequest(
            provider_kind="openai-compatible",
            provider_id="opencode-go",
            base_url="https://opencode.example/v1",
            api_key="go-secret",
            model="minimax-m3",
            messages=[{"role": "user", "content": "hello"}],
            api_surface="auto",
            max_tokens=None,
            session=session,
        )
    ).text == "ready"
    assert session.calls[1]["json"]["max_tokens"] == 8192


def test_complete_model_posts_openai_responses_payload():
    response = FakeResponse(
        {
            "id": "resp_123",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ready"}]}],
            "usage": {"input_tokens": 7, "output_tokens": 2},
        }
    )
    session = FakeSession(response)
    result = complete_model(_request(session, previous_response_id="resp_prev", reasoning_effort="low"))

    assert result.text == "ready"
    assert result.response_id == "resp_123"
    assert result.usage == {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
    assert session.calls[0]["url"] == "https://example.test/v1/responses"
    assert session.calls[0]["json"]["previous_response_id"] == "resp_prev"
    assert session.calls[0]["json"]["reasoning"] == {"effort": "low"}
    assert complete_model(_request(session, max_tokens=None)).text == "ready"
    assert "max_output_tokens" not in session.calls[1]["json"]


def test_opencode_go_responses_carries_the_same_provider_session_header():
    response = FakeResponse(
        {
            "id": "resp_go",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ready"}]}],
        }
    )
    session = FakeSession(response)
    result = complete_model(
        ModelRequest(
            provider_kind="openai-compatible",
            provider_id="opencode-go",
            provider_session_id="conversation-42",
            base_url="https://opencode.ai/zen/go/v1",
            api_key="go-secret",
            model="grok-4.6",
            messages=[{"role": "user", "content": "hello"}],
            api_surface="auto",
            session=session,
        )
    )

    assert result.text == "ready"
    assert session.calls[0]["headers"]["x-opencode-session"] == provider_request_headers(
        "opencode-go", "conversation-42"
    )["x-opencode-session"]
    assert session.calls[0]["headers"]["User-Agent"] == "ScanSci"


def test_stream_model_events_normalizes_responses_sse():
    lines = [
        b'data: {"type":"response.created","response":{"id":"resp_stream"}}',
        b'data: {"type":"response.reasoning_summary_text.delta","delta":"Checking sources"}',
        b'data: {"type":"response.output_text.delta","delta":"Hel"}',
        b'data: {"type":"response.output_text.delta","delta":"lo"}',
        b'data: {"type":"response.completed","response":{"id":"resp_stream","usage":{"input_tokens":4,"output_tokens":2}}}',
        b"data: [DONE]",
    ]
    response = FakeResponse({}, lines)
    session = FakeSession(response)
    events = list(stream_model_events(_request(session)))

    assert [event["type"] for event in events] == ["reasoning_delta", "text_delta", "text_delta", "usage", "completed"]
    assert events[0]["content"] == "Checking sources"
    assert "".join(event.get("content", "") for event in events if event["type"] == "text_delta") == "Hello"
    assert events[-2]["usage"] == {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
    assert response.closed is True


def test_legacy_llm_stream_can_select_responses_without_changing_chat_default():
    response = FakeResponse(
        {},
        [
            b'data: {"type":"response.output_text.delta","delta":"ok"}',
            b'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1}}}',
            b"data: [DONE]",
        ],
    )
    session = FakeSession(response)
    events = list(
        stream_chat_text(
            "openai-compatible",
            provider_id="gateway",
            responses_enabled=True,
            api_surface=RESPONSES,
            base_url="https://example.test/v1",
            api_key="secret",
            model="responses-model",
            messages=[{"role": "user", "content": "hello"}],
            session=session,
        )
    )
    assert events[0] == {"type": "delta", "content": "ok"}
    assert events[-1]["type"] == "done"
    assert session.calls[0]["url"].endswith("/responses")


def test_responses_incomplete_output_continues_without_a_prompt_budget():
    session = SequenceSession([
        FakeResponse({}, [
            b'data: {"type":"response.output_text.delta","delta":"first "}',
            b'data: {"type":"response.incomplete","response":{"id":"resp_1","incomplete_details":{"reason":"max_output_tokens"},"usage":{"input_tokens":2,"output_tokens":3}}}',
        ]),
        FakeResponse({}, [
            b'data: {"type":"response.output_text.delta","delta":"second"}',
            b'data: {"type":"response.completed","response":{"id":"resp_2","usage":{"input_tokens":6,"output_tokens":2}}}',
        ]),
    ])
    events = list(stream_chat_text(
        "openai-compatible",
        provider_id="gateway",
        responses_enabled=True,
        api_surface=RESPONSES,
        base_url="https://example.test/v1",
        api_key="secret",
        model="responses-model",
        messages=[{"role": "user", "content": "long answer"}],
        max_tokens=None,
        max_continuations=1,
        max_requests=1,
        session=session,
    ))
    assert "".join(event.get("content", "") for event in events if event["type"] == "delta") == "first second"
    assert events[-1]["finish_reason"] == "stop"
    assert events[-1]["truncated"] is False
    assert events[-1]["usage"]["total_tokens"] == 13
    assert all("max_output_tokens" not in call["json"] for call in session.calls)
    assert session.calls[1]["json"]["input"][-2]["content"] == "first "


def test_anthropic_length_stop_continues_and_marks_exhaustion():
    limited = FakeResponse({}, [
        b'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"Planning"}}',
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"first "}}',
        b'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":3}}',
        b'data: {"type":"message_stop"}',
    ])
    finished = FakeResponse({}, [
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"second"}}',
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}',
        b'data: {"type":"message_stop"}',
    ])
    args = dict(
        provider_id="opencode-go",
        base_url="https://opencode.example/v1",
        api_key="secret",
        model="minimax-m3",
        messages=[{"role": "user", "content": "long answer"}],
        max_tokens=None,
        max_requests=1,
    )
    truncated = list(stream_chat_text("openai-compatible", max_continuations=0, session=FakeSession(limited), **args))
    assert truncated[0] == {"type": "reasoning", "content": "Planning"}
    assert truncated[-1]["finish_reason"] == "length"
    assert truncated[-1]["truncated"] is True

    session = SequenceSession([limited, finished])
    events = list(stream_chat_text("openai-compatible", max_continuations=1, session=session, **args))
    assert "".join(event.get("content", "") for event in events if event["type"] == "delta") == "first second"
    assert events[-1]["truncated"] is False
    assert len(session.calls) == 2
    assert session.calls[0]["json"]["max_tokens"] == 8192
    assert session.calls[0]["json"]["stream"] is True
    assert session.calls[1]["json"]["messages"][-2]["content"] == "first "


def test_responses_json_client_reuses_structured_output_contract():
    response = FakeResponse(
        {
            "id": "resp_json",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"queries":["BERT"]}'}]}],
        }
    )
    session = FakeSession(response)
    client = OpenAIResponsesJsonClient(
        base_url="https://example.test/v1",
        api_key="secret",
        model="responses-model",
        provider_id="gateway",
        responses_enabled=True,
        session=session,
    )
    assert client.complete_json([{"role": "user", "content": "BERT"}], schema_name="retrieval_queries") == {
        "queries": ["BERT"]
    }
    assert session.calls[0]["json"]["text"]["format"] == {"type": "json_object"}


def test_responses_stream_retries_before_first_delta_after_broken_sse(monkeypatch):
    broken = FakeResponse({}, [])

    def broken_lines(*, decode_unicode):
        del decode_unicode
        raise requests.ConnectionError("stream closed before first token")
        yield b""

    broken.iter_lines = broken_lines
    recovered = FakeResponse(
        {},
        [
            b'data: {"type":"response.output_text.delta","delta":"recovered"}',
            b'data: {"type":"response.completed","response":{}}',
        ],
    )
    session = SequenceSession([broken, recovered])
    monkeypatch.setattr("scansci_html.model_transport.time.sleep", lambda _delay: None)
    events = list(stream_model_events(_request(session, max_requests=2)))

    assert events[0]["type"] == "retry"
    assert events[0]["reason"] == "temporary_upstream_error"
    assert any(event.get("content") == "recovered" for event in events)
    assert len(session.calls) == 2


def test_responses_stream_does_not_retry_after_visible_reasoning():
    broken = FakeResponse({}, [])

    def broken_lines(*, decode_unicode):
        del decode_unicode
        yield b'data: {"type":"response.reasoning_summary_text.delta","delta":"Checking"}'
        raise requests.ConnectionError("stream closed after reasoning")

    broken.iter_lines = broken_lines
    session = SequenceSession([broken])
    events = []
    with pytest.raises(requests.ConnectionError):
        for event in stream_model_events(_request(session, max_requests=2)):
            events.append(event)

    assert [event["type"] for event in events] == ["reasoning_delta"]
    assert len(session.calls) == 1
