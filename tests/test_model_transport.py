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
        b'data: {"type":"response.output_text.delta","delta":"Hel"}',
        b'data: {"type":"response.output_text.delta","delta":"lo"}',
        b'data: {"type":"response.completed","response":{"id":"resp_stream","usage":{"input_tokens":4,"output_tokens":2}}}',
        b"data: [DONE]",
    ]
    response = FakeResponse({}, lines)
    session = FakeSession(response)
    events = list(stream_model_events(_request(session)))

    assert [event["type"] for event in events] == ["text_delta", "text_delta", "usage", "completed"]
    assert "".join(event.get("content", "") for event in events) == "Hello"
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
