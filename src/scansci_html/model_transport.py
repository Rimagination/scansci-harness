"""Provider-neutral Chat Completions and Responses transport.

This is deliberately a small transport layer, not another agent framework.
It owns API-surface selection, capability validation, retry policy, response
parsing, and normalized streaming events.  Agent loops remain in Pi or in an
optional Python harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import time
from collections.abc import Iterator, Mapping
from typing import Any

import requests

from .ollama_runtime import chat_thinking_parameters

from .agent_events import (
    COMPLETED,
    ERROR,
    REASONING_DELTA,
    RETRY,
    TEXT_DELTA,
    TOOL_CALL,
    TOOL_RESULT,
    USAGE,
    make_event,
    normalize_usage,
)


CHAT_COMPLETIONS = "chat_completions"
RESPONSES = "responses"
ANTHROPIC_MESSAGES = "anthropic_messages"
AUTO = "auto"
SUPPORTED_API_SURFACES = frozenset({CHAT_COMPLETIONS, RESPONSES, ANTHROPIC_MESSAGES})


class ModelTransportError(RuntimeError):
    """A public, provider-safe model transport failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        api_surface: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = bool(retryable)
        self.api_surface = str(api_surface or "")


class ModelCapabilityError(ModelTransportError):
    """Raised when a request asks a provider for an unsupported capability."""


@dataclass(frozen=True)
class ModelCapabilities:
    provider_id: str
    provider_kind: str
    model: str
    api_surfaces: frozenset[str]
    supports_reasoning: bool = False
    supports_previous_response_id: bool = False
    supports_structured_output: bool = False
    supports_hosted_tools: bool = False
    supports_streaming: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "model": self.model,
            "api_surfaces": sorted(self.api_surfaces),
            "supports_reasoning": self.supports_reasoning,
            "supports_previous_response_id": self.supports_previous_response_id,
            "supports_structured_output": self.supports_structured_output,
            "supports_hosted_tools": self.supports_hosted_tools,
            "supports_streaming": self.supports_streaming,
        }


@dataclass(frozen=True)
class ModelRequest:
    provider_kind: str
    base_url: str
    api_key: str
    model: str
    messages: list[dict[str, Any]]
    provider_id: str = ""
    api_surface: str = CHAT_COMPLETIONS
    responses_enabled: bool = False
    timeout: float = 90.0
    max_tokens: int | None = 8192
    temperature: float | None = 0.4
    thinking_mode: str | None = None
    reasoning_effort: str | None = None
    previous_response_id: str | None = None
    response_format: dict[str, Any] | None = None
    max_requests: int = 2
    session: Any | None = None
    run_id: str = ""
    provider_session_id: str = ""
    request_id: str = field(default_factory=lambda: f"model-{os.urandom(8).hex()}")


@dataclass(frozen=True)
class ModelResult:
    text: str
    usage: dict[str, int]
    response_id: str = ""
    finish_reason: str = "stop"
    api_surface: str = CHAT_COMPLETIONS
    raw_type: str = ""


def _normalise(value: object) -> str:
    return str(value or "").strip().lower()


def provider_request_headers(provider_id: object, session_id: object) -> dict[str, str]:
    """Return provider-required identity headers without exposing local ids."""

    if _normalise(provider_id) != "opencode-go":
        return {}
    stable_id = str(session_id or "").strip()
    if not stable_id:
        return {}
    digest = hashlib.sha256(stable_id.encode("utf-8")).hexdigest()[:32]
    return {
        "User-Agent": "ScanSci",
        "x-opencode-session": f"scansci-{digest}",
    }


def merge_request_headers(
    base_headers: Mapping[str, str],
    extra_headers: Mapping[str, str] | None,
) -> dict[str, str]:
    """Merge internal provider headers while protecting authentication fields."""

    merged = dict(base_headers)
    protected = {"authorization", "content-type", "x-api-key", "anthropic-version"}
    for raw_name, raw_value in dict(extra_headers or {}).items():
        name = str(raw_name or "").strip()
        value = str(raw_value or "").strip()
        if not name or not value or name.casefold() in protected or "\r" in value or "\n" in value:
            continue
        merged[name] = value
    return merged


def _responses_models_from_environment() -> set[str]:
    return {
        item.strip()
        for item in str(os.getenv("SCANSCI_RESPONSES_MODELS", "")).split(",")
        if item.strip()
    }


_OPENCODE_GO_RESPONSES_MODELS = frozenset(
    {
        "grok-4.6",
        "gpt-5.6-luna",
        "muse-spark-1.2-contributor",
    }
)
_OPENCODE_GO_ANTHROPIC_MODELS = frozenset(
    {
        "minimax-m3",
        "minimax-m2.7",
        "minimax-m2.5",
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen3.6-plus",
    }
)


def opencode_go_api_surface(model: str) -> str:
    """Return the endpoint family documented for one OpenCode Go model."""

    model_id = _normalise(model)
    for prefix in ("opencode-go/", "opencode-go:"):
        if model_id.startswith(prefix):
            model_id = model_id[len(prefix) :]
            break
    if model_id in _OPENCODE_GO_RESPONSES_MODELS:
        return RESPONSES
    if model_id in _OPENCODE_GO_ANTHROPIC_MODELS:
        return ANTHROPIC_MESSAGES
    return CHAT_COMPLETIONS


def capabilities_for(
    provider_kind: str,
    model: str,
    *,
    provider_id: str = "",
    responses_enabled: bool = False,
) -> ModelCapabilities:
    """Return a conservative capability profile for a configured model.

    Generic OpenAI-compatible gateways default to Chat Completions.  A
    Responses route must be explicitly enabled on the provider or listed in
    ``SCANSCI_RESPONSES_MODELS``; this prevents a gateway from receiving a
    Responses payload merely because its model is named like an OpenAI model.
    """

    kind = _normalise(provider_kind)
    identifier = _normalise(provider_id)
    model_name = str(model or "").strip()
    if kind in {"anthropic", "anthropic-compatible"}:
        return ModelCapabilities(
            provider_id=identifier,
            provider_kind=kind,
            model=model_name,
            api_surfaces=frozenset({ANTHROPIC_MESSAGES}),
            supports_reasoning=True,
            supports_structured_output=True,
        )
    if identifier == "opencode-go":
        surface = opencode_go_api_surface(model_name)
        return ModelCapabilities(
            provider_id=identifier,
            provider_kind=kind,
            model=model_name,
            api_surfaces=frozenset({surface}),
            supports_reasoning=True,
            supports_previous_response_id=surface == RESPONSES,
            supports_structured_output=True,
            supports_hosted_tools=surface == RESPONSES,
        )
    can_use_responses = bool(
        responses_enabled
        or identifier == "openai"
        or model_name in _responses_models_from_environment()
    )
    surfaces = {CHAT_COMPLETIONS}
    if can_use_responses:
        surfaces.add(RESPONSES)
    return ModelCapabilities(
        provider_id=identifier,
        provider_kind=kind,
        model=model_name,
        api_surfaces=frozenset(surfaces),
        supports_reasoning=True,
        supports_previous_response_id=RESPONSES in surfaces,
        supports_structured_output=True,
        supports_hosted_tools=RESPONSES in surfaces,
    )


def select_api_surface(
    requested: str | None,
    *,
    provider_kind: str,
    provider_id: str = "",
    model: str = "",
    responses_enabled: bool = False,
) -> str:
    """Resolve ``auto`` once and reject unsupported explicit choices."""

    value = _normalise(requested or CHAT_COMPLETIONS)
    if value not in {AUTO, *SUPPORTED_API_SURFACES}:
        raise ModelCapabilityError(f"Unsupported API surface: {requested}")
    capabilities = capabilities_for(
        provider_kind,
        model,
        provider_id=provider_id,
        responses_enabled=responses_enabled,
    )
    if value == AUTO:
        if _normalise(provider_id) == "opencode-go":
            return opencode_go_api_surface(model)
        if _normalise(provider_kind) in {"anthropic", "anthropic-compatible"}:
            return ANTHROPIC_MESSAGES
        return RESPONSES if _normalise(provider_id) == "openai" and RESPONSES in capabilities.api_surfaces else CHAT_COMPLETIONS
    # The legacy default is Chat Completions. Treat it as an omitted surface
    # for OpenCode Go so callers that predate the provider still follow the
    # documented model-specific endpoint automatically.
    if _normalise(provider_id) == "opencode-go" and value == CHAT_COMPLETIONS:
        return opencode_go_api_surface(model)
    if value not in capabilities.api_surfaces:
        supported = ", ".join(sorted(capabilities.api_surfaces))
        raise ModelCapabilityError(
            f"Provider {provider_id or provider_kind} does not advertise API surface {value}; "
            f"supported surfaces: {supported}",
            api_surface=value,
        )
    return value


def _public_error(error: BaseException, *, surface: str, base_url: str = "") -> ModelTransportError:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    retryable = status_code in {408, 409, 425, 429, 500, 502, 503, 504} or isinstance(
        error, (requests.Timeout, requests.ConnectionError)
    )
    suffix = f" (HTTP {status_code})" if status_code else ""
    # Loopback runtimes are shipped by ScanSci, so their structured error is
    # safe and materially more useful than a generic HTTP 400.  Never expose
    # response bodies from remote providers because they may contain secrets
    # or provider-specific request details.
    normalized_url = str(base_url or "").strip().lower()
    if normalized_url.startswith(("http://127.0.0.1", "https://127.0.0.1", "http://localhost", "https://localhost", "http://[::1]", "https://[::1]")):
        try:
            payload = response.json() if response is not None else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        detail = payload.get("error", {}).get("message") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else ""
        if detail:
            return ModelTransportError(
                f"Model {surface} request failed{suffix}: {str(detail)[:1000]}",
                status_code=status_code,
                retryable=retryable,
                api_surface=surface,
            )
    return ModelTransportError(
        f"Model {surface} request failed{suffix}: {type(error).__name__}",
        status_code=status_code,
        retryable=retryable,
        api_surface=surface,
    )


def _retry_delay(error: BaseException, attempt: int) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("Retry-After", headers.get("retry-after", 0)) if hasattr(headers, "get") else 0
    try:
        requested = float(raw or 0)
    except (TypeError, ValueError):
        requested = 0.0
    return min(30.0, max(requested, float(2 ** max(1, attempt))))


def _request_with_retry(
    request: ModelRequest,
    *,
    surface: str,
    body: dict[str, Any],
    stream: bool = False,
) -> tuple[Any, Iterator[dict[str, Any]] | None]:
    client = request.session or requests.Session()
    url_suffix = "/messages" if surface == ANTHROPIC_MESSAGES else "/responses" if surface == RESPONSES else "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if surface == ANTHROPIC_MESSAGES:
        headers.update({"x-api-key": request.api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {request.api_key}"
    headers = merge_request_headers(
        headers,
        provider_request_headers(
            request.provider_id,
            request.provider_session_id or request.run_id or request.request_id,
        ),
    )
    last_error: BaseException | None = None
    limit = max(1, min(8, int(request.max_requests)))
    for attempt in range(limit):
        try:
            response = client.post(
                f"{request.base_url.rstrip('/')}{url_suffix}",
                headers=headers,
                json=body,
                timeout=request.timeout,
                **({"stream": True} if stream else {}),
            )
            response.raise_for_status()
            if stream:
                return response, _iter_sse(response)
            return response, None
        except requests.RequestException as error:
            last_error = error
            public = _public_error(error, surface=surface, base_url=request.base_url)
            if not public.retryable or attempt >= limit - 1:
                raise public from error
            delay = _retry_delay(error, attempt)
            time.sleep(delay)
    raise _public_error(last_error or RuntimeError("request failed"), surface=surface, base_url=request.base_url)


def _iter_sse(response: Any) -> Iterator[dict[str, Any]]:
    for raw_line in response.iter_lines(decode_unicode=False):
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def _chat_body(request: ModelRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "messages": request.messages,
        "stream": False,
    }
    if request.max_tokens is not None:
        body["max_tokens"] = max(64, int(request.max_tokens))
    # OpenAI reasoning models commonly reject temperature alongside a
    # reasoning budget.  Omitting it is safer than relying on provider-side
    # parameter dropping, which would otherwise be a silent capability change.
    if request.temperature is not None and not request.reasoning_effort and request.thinking_mode != "enabled":
        body["temperature"] = float(request.temperature)
    body.update(chat_thinking_parameters(request.base_url, request.model, request.thinking_mode))
    return body


def _responses_body(request: ModelRequest, *, stream: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "input": request.messages,
        "stream": bool(stream),
    }
    if request.previous_response_id:
        body["previous_response_id"] = request.previous_response_id
    if request.reasoning_effort:
        body["reasoning"] = {"effort": request.reasoning_effort}
    elif request.thinking_mode == "enabled":
        body["reasoning"] = {"effort": "medium"}
    if request.temperature is not None and not request.reasoning_effort and request.thinking_mode != "enabled":
        body["temperature"] = float(request.temperature)
    if request.max_tokens is not None:
        body["max_output_tokens"] = max(64, int(request.max_tokens))
    if request.response_format:
        response_format = dict(request.response_format)
        if response_format.get("type") == "json_object":
            body["text"] = {"format": {"type": "json_object"}}
        elif response_format.get("type") == "json_schema":
            body["text"] = {"format": response_format}
        else:
            body["text"] = {"format": response_format}
    return body


def _anthropic_body(request: ModelRequest, *, stream: bool = False) -> dict[str, Any]:
    system = "\n\n".join(str(item.get("content", "")) for item in request.messages if item.get("role") == "system")
    conversation = [item for item in request.messages if item.get("role") in {"user", "assistant"}]
    body: dict[str, Any] = {"model": request.model, "max_tokens": max(64, int(request.max_tokens if request.max_tokens is not None else 8192)), "messages": conversation}
    if stream:
        body["stream"] = True
    if system:
        body["system"] = system
    return body


def _extract_chat(payload: Mapping[str, Any]) -> ModelResult:
    choices = payload.get("choices")
    message = choices[0].get("message", {}) if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
    content = message.get("content", "") if isinstance(message, Mapping) else ""
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, Mapping))
    return ModelResult(
        text=str(content or "").strip(),
        usage=normalize_usage(payload.get("usage")),
        response_id=str(payload.get("id", "") or ""),
        finish_reason=str(choices[0].get("finish_reason", "stop") if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else "stop"),
        api_surface=CHAT_COMPLETIONS,
        raw_type="chat.completion",
    )


def _extract_responses(payload: Mapping[str, Any]) -> ModelResult:
    chunks: list[str] = []
    finish_reason = "stop"
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type", ""))
        if item_type == "message":
            for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
                if isinstance(content, Mapping) and content.get("type") in {"output_text", "text"}:
                    chunks.append(str(content.get("text", "")))
        elif item_type == "output_text":
            chunks.append(str(item.get("text", "")))
    if not chunks and isinstance(payload.get("output_text"), str):
        chunks.append(str(payload["output_text"]))
    status = str(payload.get("status", "completed") or "completed")
    if status not in {"completed", "in_progress"}:
        finish_reason = status
    return ModelResult(
        text="".join(chunks).strip(),
        usage=normalize_usage(payload.get("usage")),
        response_id=str(payload.get("id", "") or ""),
        finish_reason=finish_reason,
        api_surface=RESPONSES,
        raw_type="response",
    )


def _extract_anthropic(payload: Mapping[str, Any]) -> ModelResult:
    blocks = payload.get("content", [])
    text = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, Mapping)) if isinstance(blocks, list) else ""
    return ModelResult(
        text=text.strip(),
        usage=normalize_usage(payload.get("usage")),
        response_id=str(payload.get("id", "") or ""),
        finish_reason=str(payload.get("stop_reason", "stop") or "stop"),
        api_surface=ANTHROPIC_MESSAGES,
        raw_type="message",
    )


def complete_model(request: ModelRequest) -> ModelResult:
    """Execute one bounded non-streaming model request."""

    surface = select_api_surface(
        request.api_surface,
        provider_kind=request.provider_kind,
        provider_id=request.provider_id,
        model=request.model,
        responses_enabled=request.responses_enabled,
    )
    if not request.base_url or not request.api_key or not request.model or not request.messages:
        raise ValueError("base_url, api_key, model, and messages are required")
    if surface == CHAT_COMPLETIONS:
        # Keep the existing LiteLLM and retry behavior as the compatibility
        # implementation for the production default.
        from .llm import complete_chat_text

        result = complete_chat_text(
            request.provider_kind,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            messages=request.messages,
            timeout=request.timeout,
            session=request.session,
            thinking_mode=request.thinking_mode,
            include_usage=True,
            max_tokens=request.max_tokens,
            temperature=request.temperature if request.temperature is not None else 0.4,
            max_requests=request.max_requests,
            api_surface=CHAT_COMPLETIONS,
            provider_id=request.provider_id,
            responses_enabled=request.responses_enabled,
            provider_session_id=request.provider_session_id or request.run_id or request.request_id,
        )
        text, usage = result if isinstance(result, tuple) else (str(result), {})
        return ModelResult(text=str(text), usage=normalize_usage(usage), api_surface=surface, raw_type="chat.completion")
    body = _anthropic_body(request) if surface == ANTHROPIC_MESSAGES else _responses_body(request)
    response, _ = _request_with_retry(request, surface=surface, body=body)
    try:
        payload = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ModelTransportError(f"Model {surface} response was not valid JSON", api_surface=surface) from error
    result = _extract_anthropic(payload) if surface == ANTHROPIC_MESSAGES else _extract_responses(payload)
    if not result.text:
        raise ModelTransportError(f"Model {surface} returned an empty response", api_surface=surface)
    return result


def _stream_chat_events(request: ModelRequest) -> Iterator[dict[str, Any]]:
    from .llm import stream_chat_text

    for event in stream_chat_text(
        request.provider_kind,
        base_url=request.base_url,
        api_key=request.api_key,
        model=request.model,
        messages=request.messages,
        timeout=request.timeout,
        session=request.session,
        thinking_mode=request.thinking_mode,
        max_tokens=request.max_tokens,
        temperature=request.temperature if request.temperature is not None else 0.4,
        max_requests=request.max_requests,
        api_surface=CHAT_COMPLETIONS,
        provider_id=request.provider_id,
        responses_enabled=request.responses_enabled,
        provider_session_id=request.provider_session_id or request.run_id or request.request_id,
    ):
        event_type = str(event.get("type", ""))
        if event_type == "delta":
            yield make_event(TEXT_DELTA, run_id=request.run_id, request_id=request.request_id, content=str(event.get("content", "")))
        elif event_type == "retry":
            yield make_event(RETRY, run_id=request.run_id, request_id=request.request_id, **dict(event))
        elif event_type == "done":
            yield make_event(USAGE, run_id=request.run_id, request_id=request.request_id, usage=normalize_usage(event.get("usage")))
            yield make_event(COMPLETED, run_id=request.run_id, request_id=request.request_id, finish_reason=str(event.get("finish_reason", "stop")), truncated=bool(event.get("truncated", False)))
        else:
            yield make_event(event_type or "provider_event", run_id=request.run_id, request_id=request.request_id, **dict(event))


def _stream_responses_events(request: ModelRequest, *, surface: str) -> Iterator[dict[str, Any]]:
    body = _anthropic_body(request, stream=True) if surface == ANTHROPIC_MESSAGES else _responses_body(request, stream=True)
    stream_response, records = _request_with_retry(request, surface=surface, body=body, stream=True)
    if records is None:
        return
    usage: dict[str, int] = {}
    response_id = ""
    finish_reason = "stop"
    try:
        for payload in records:
            event_type = str(payload.get("type", ""))
            response_id = str(payload.get("response", {}).get("id", "") or payload.get("id", "") or response_id)
            if surface == RESPONSES:
                if event_type == "response.output_text.delta":
                    yield make_event(TEXT_DELTA, run_id=request.run_id, request_id=request.request_id, content=str(payload.get("delta", "")))
                elif event_type == "response.reasoning_summary_text.delta":
                    yield make_event(REASONING_DELTA, run_id=request.run_id, request_id=request.request_id, content=str(payload.get("delta", "")))
                elif event_type == "response.output_item.added":
                    item = payload.get("item")
                    if isinstance(item, Mapping) and item.get("type") in {"function_call", "computer_call"}:
                        yield make_event(TOOL_CALL, run_id=request.run_id, request_id=request.request_id, item=dict(item))
                elif event_type == "response.function_call_arguments.delta":
                    yield make_event(TOOL_CALL, run_id=request.run_id, request_id=request.request_id, call_id=str(payload.get("item_id", "")), arguments_delta=str(payload.get("delta", "")))
                elif event_type in {"response.completed", "response.done", "response.incomplete"}:
                    response_payload = payload.get("response") if isinstance(payload.get("response"), Mapping) else payload
                    usage = normalize_usage(response_payload.get("usage") if isinstance(response_payload, Mapping) else {})
                    details = response_payload.get("incomplete_details") if isinstance(response_payload, Mapping) else None
                    reason = str(details.get("reason", "") if isinstance(details, Mapping) else "")
                    finish_reason = "length" if reason == "max_output_tokens" else "incomplete" if event_type == "response.incomplete" else "stop"
                    yield make_event(USAGE, run_id=request.run_id, request_id=request.request_id, usage=usage)
                    yield make_event(COMPLETED, run_id=request.run_id, request_id=request.request_id, response_id=response_id, finish_reason=finish_reason)
                elif event_type in {"response.failed", "error"}:
                    message = payload.get("error", payload.get("message", "Responses request failed"))
                    yield make_event(ERROR, run_id=request.run_id, request_id=request.request_id, error=str(message)[:500])
                    raise ModelTransportError(str(message)[:500], api_surface=surface)
            else:
                if event_type == "message_start":
                    message = payload.get("message")
                    usage = normalize_usage(message.get("usage") if isinstance(message, Mapping) else {})
                elif event_type == "content_block_delta":
                    delta = payload.get("delta")
                    if isinstance(delta, Mapping) and delta.get("type") == "text_delta":
                        yield make_event(TEXT_DELTA, run_id=request.run_id, request_id=request.request_id, content=str(delta.get("text", "")))
                    elif isinstance(delta, Mapping) and delta.get("type") == "thinking_delta":
                        yield make_event(REASONING_DELTA, run_id=request.run_id, request_id=request.request_id, content=str(delta.get("thinking", "")))
                elif event_type == "message_delta":
                    usage = normalize_usage(payload.get("usage")) or usage
                    delta = payload.get("delta")
                    if isinstance(delta, Mapping) and delta.get("stop_reason"):
                        finish_reason = "length" if delta["stop_reason"] == "max_tokens" else str(delta["stop_reason"])
                elif event_type == "message_stop":
                    yield make_event(USAGE, run_id=request.run_id, request_id=request.request_id, usage=usage)
                    yield make_event(COMPLETED, run_id=request.run_id, request_id=request.request_id, response_id=response_id, finish_reason=finish_reason)
    finally:
        close = getattr(stream_response, "close", None)
        if callable(close):
            close()


def stream_model_events(request: ModelRequest) -> Iterator[dict[str, Any]]:
    """Stream normalized events with explicit API-surface selection."""

    surface = select_api_surface(
        request.api_surface,
        provider_kind=request.provider_kind,
        provider_id=request.provider_id,
        model=request.model,
        responses_enabled=request.responses_enabled,
    )
    if surface == CHAT_COMPLETIONS:
        yield from _stream_chat_events(request)
    else:
        # A provider may accept the request and then close the SSE connection
        # before the first visible token.  Replaying after visible output
        # would duplicate the assistant message, so recovery is deliberately
        # limited to the pre-output boundary.
        for attempt in range(max(1, min(3, int(request.max_requests)))):
            visible_output = False
            try:
                for event in _stream_responses_events(request, surface=surface):
                    if event.get("type") in {TEXT_DELTA, REASONING_DELTA} and str(event.get("content", "")):
                        visible_output = True
                    yield event
                return
            except (requests.RequestException, ModelTransportError) as error:
                retryable = isinstance(error, requests.RequestException) or bool(getattr(error, "retryable", False))
                if visible_output or not retryable or attempt >= max(1, min(3, int(request.max_requests))) - 1:
                    raise
                delay = min(8.0, max(1.0, float(2 ** (attempt + 1))))
                yield make_event(
                    RETRY,
                    run_id=request.run_id,
                    request_id=request.request_id,
                    reason="temporary_upstream_error",
                    delay_seconds=delay,
                    attempt=attempt + 1,
                )
                time.sleep(delay)


__all__ = [
    "ANTHROPIC_MESSAGES",
    "AUTO",
    "CHAT_COMPLETIONS",
    "ModelCapabilities",
    "ModelCapabilityError",
    "ModelRequest",
    "ModelResult",
    "ModelTransportError",
    "RESPONSES",
    "SUPPORTED_API_SURFACES",
    "capabilities_for",
    "complete_model",
    "opencode_go_api_surface",
    "select_api_surface",
    "stream_model_events",
]
