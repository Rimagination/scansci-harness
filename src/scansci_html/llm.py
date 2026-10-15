from __future__ import annotations

import codecs
import json
import os
import re
import time
from threading import Lock
from typing import Any, Iterator, Mapping, Protocol
from urllib.parse import urlparse

from pydantic import ValidationError
import requests
from urllib3.exceptions import ReadTimeoutError

from .model_transport import (
    ANTHROPIC_MESSAGES,
    CHAT_COMPLETIONS,
    RESPONSES,
    ModelRequest,
    complete_model,
    merge_request_headers,
    provider_request_headers,
    select_api_surface,
    stream_model_events,
)
from .ollama_runtime import chat_thinking_parameters, is_local_ollama_url, warm_ollama_chat_model
from .qa.schemas import AnswerPayloadSchema, ClaimVerificationPayloadSchema


_MANAGED_GATEWAY_SESSION = requests.Session()
_DEFAULT_PROVIDER_INPUT_TOKENS = 48_000
_STRUCTURED_PROVIDER_INPUT_TOKENS = 48_000
# Structured-output operations share a strict two-request budget so a malformed
# response cannot quietly multiply paid requests.
_MAX_LOGICAL_REQUESTS = 2
# A managed streaming turn can briefly return 429 while a previous gateway
# worker drains its queue.  Keep the retry budget bounded, but give a transient
# gateway window enough time to recover before surfacing an error to the user.
_STREAM_LOGICAL_REQUESTS = 4

# The desktop server can receive two submissions at nearly the same time (for
# example Enter plus a click, or two windows sharing the same gateway).  A
# process-local gate prevents those requests from stampeding one managed
# endpoint.  The cooldown is deliberately keyed by endpoint, not model: GLM
# and the standby managed model share the same upstream capacity.
_STREAM_GATE_LOCKS: dict[str, Lock] = {}
_STREAM_GATE_LOCKS_GUARD = Lock()
_STREAM_COOLDOWN_UNTIL: dict[str, float] = {}
_STREAM_COOLDOWN_GUARD = Lock()


def _stream_rate_key(provider: str, base_url: str) -> str:
    return f"{str(provider or '').strip().lower()}::{str(base_url or '').rstrip('/').lower()}"


def _is_loopback_endpoint(base_url: str) -> bool:
    try:
        hostname = urlparse(str(base_url or "")).hostname
    except ValueError:
        return False
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _is_stream_read_timeout(error: BaseException) -> bool:
    """Requests wraps an SSE read timeout in ConnectionError after HTTP 200."""

    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (requests.Timeout, ReadTimeoutError)):
            return True
        pending.extend(
            item for item in (*current.args, current.__cause__, current.__context__)
            if isinstance(item, BaseException)
        )
    return False


def _stream_request_gate(rate_key: str) -> Lock:
    with _STREAM_GATE_LOCKS_GUARD:
        gate = _STREAM_GATE_LOCKS.get(rate_key)
        if gate is None:
            gate = Lock()
            _STREAM_GATE_LOCKS[rate_key] = gate
        return gate


def _stream_cooldown_remaining(rate_key: str) -> float:
    with _STREAM_COOLDOWN_GUARD:
        remaining = max(0.0, float(_STREAM_COOLDOWN_UNTIL.get(rate_key, 0.0)) - time.monotonic())
        if remaining <= 0:
            _STREAM_COOLDOWN_UNTIL.pop(rate_key, None)
        return remaining


def _stream_set_cooldown(rate_key: str, delay: float) -> None:
    if delay <= 0:
        return
    with _STREAM_COOLDOWN_GUARD:
        _STREAM_COOLDOWN_UNTIL[rate_key] = max(
            float(_STREAM_COOLDOWN_UNTIL.get(rate_key, 0.0)),
            time.monotonic() + float(delay),
        )


def _stream_clear_cooldown(rate_key: str) -> None:
    with _STREAM_COOLDOWN_GUARD:
        _STREAM_COOLDOWN_UNTIL.pop(rate_key, None)


class StructuredOutputError(ValueError):
    """A bounded, safe-to-repeat structured-output failure.

    The raw model response is intentionally not stored here.  Retry prompts
    need a compact diagnosis, not another copy of potentially large evidence
    or user text.
    """

    def __init__(self, schema_name: str, *, category: str, detail: str = "") -> None:
        self.schema_name = str(schema_name or "unknown")
        self.category = str(category or "invalid_output")
        self.detail = str(detail or "")[:240]
        message = f"Model did not return valid {self.schema_name} JSON"
        if self.detail:
            message += f" ({self.category}: {self.detail})"
        super().__init__(message)


_STRUCTURED_RETRYABLE_SCHEMAS = frozenset(
    {
        "answer_claims",
        "claim_verification",
        "retrieval_queries",
        "evidence_grounded_literature_review",
        "literature_review_overview",
        "scansci_scientific_slides",
    }
)
_STRUCTURED_SCHEMA_MODELS: dict[str, type[Any]] = {
    "answer_claims": AnswerPayloadSchema,
    "claim_verification": ClaimVerificationPayloadSchema,
}


def _structured_attempt_limit(schema_name: str) -> int:
    """Return the correction budget for one logical structured request."""

    normalized = str(schema_name or "").strip()
    # Section JSON is already repaired and validated by the workflow layer;
    # retrying here would multiply slow synthesis calls without new evidence.
    if normalized == "literature_review_section":
        return 1
    return 2 if normalized in _STRUCTURED_RETRYABLE_SCHEMAS else 1


def _validation_error_detail(error: ValidationError) -> str:
    """Summarize validation paths without echoing model-provided values."""

    details: list[str] = []
    for item in error.errors()[:3]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        error_type = str(item.get("type", "invalid"))
        details.append(f"{location}:{error_type}")
    return "; ".join(details) or "schema validation failed"


def _validate_structured_payload(payload: Any, *, schema_name: str) -> Any:
    """Validate known contracts before returning them to workflow code."""

    if not isinstance(payload, dict):
        raise StructuredOutputError(schema_name, category="shape", detail="root must be an object")
    model_type = _STRUCTURED_SCHEMA_MODELS.get(str(schema_name or ""))
    if model_type is None:
        return payload
    try:
        validated = model_type.model_validate(payload)
    except ValidationError as error:
        raise StructuredOutputError(
            schema_name,
            category="schema_validation",
            detail=_validation_error_detail(error),
        ) from error
    return validated.model_dump(mode="python")


def _json_failure_category(content: object) -> tuple[str, str]:
    text = str(content or "").strip()
    if not text:
        return "empty", "no model content"
    if "{" in text and _open_json_delimiters(text):
        return "truncated_json", "unclosed JSON object"
    return "invalid_json", "response was not a complete JSON object"


def _parse_and_validate_structured_content(content: object, *, schema_name: str) -> Any:
    """Parse, narrowly recover, and validate one structured model response."""

    try:
        parsed = _parse_json_content(content)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        recovered = _recover_structured_content(content, schema_name=schema_name)
        if recovered is not None:
            return _validate_structured_payload(recovered, schema_name=schema_name)
        category, detail = _json_failure_category(content)
        raise StructuredOutputError(schema_name, category=category, detail=detail) from error
    return _validate_structured_payload(parsed, schema_name=schema_name)


def _structured_retry_instruction(
    schema_name: str,
    failure: StructuredOutputError,
    *,
    attempt: int,
) -> str:
    """Build a targeted correction prompt from a redacted failure class."""

    contract_hint = {
        "answer_claims": (
            'Return an object with "answer" as a list of items containing non-empty '
            'claim_id, text, and quote_ids; keep quote_ids from the supplied evidence only.'
        ),
        "claim_verification": (
            'Return "claims" as a list with one item per supplied claim_id; use only the '
            'allowed support_status values and a verification_score between 0 and 1.'
        ),
        "retrieval_queries": 'Return an object whose "queries" value is a list of short strings.',
        "evidence_grounded_literature_review": (
            'Keep the planned sections and every factual paragraph tied to supplied citation_ids.'
        ),
        "literature_review_overview": (
            'Keep the required top-level keys and return comparison rows, controversies, and open questions in their specified shapes.'
        ),
    }.get(str(schema_name or ""), "Follow the structured contract already present in the system instruction.")
    final_attempt = " This is the final correction attempt." if attempt > 1 else ""
    return (
        "The previous structured response was invalid. "
        f"Failure class: {failure.category}; diagnosis: {failure.detail}. "
        f"{contract_hint} Output one complete JSON object only, without Markdown fences or commentary, "
        f"and stop immediately after the closing brace.{final_attempt}"
    )


def _compact_provider_input_for_estimate(value: object) -> object:
    """Replace inline image bytes before estimating text-token budget.

    OpenAI-compatible vision requests carry images as base64 data URLs.  The
    bytes are real input to the vision model, but they are not text tokens.
    Counting every base64 character here can reject an otherwise valid image
    request before it ever reaches the selected local or remote vision model.
    """

    if isinstance(value, list):
        return [_compact_provider_input_for_estimate(item) for item in value]
    if not isinstance(value, dict):
        return value

    compacted = {
        key: _compact_provider_input_for_estimate(item)
        for key, item in value.items()
    }
    part_type = str(value.get("type", "")).strip().lower()
    if part_type == "image_url" and isinstance(value.get("image_url"), dict):
        image_url = dict(value["image_url"])
        url = str(image_url.get("url", ""))
        if url.startswith("data:image/"):
            image_url["url"] = "[inline image bytes omitted from text estimate]"
        compacted["image_url"] = image_url
    source = value.get("source")
    if part_type == "image" and isinstance(source, dict) and str(source.get("type", "")).lower() == "base64":
        compacted_source = dict(compacted.get("source", {}) or {})
        compacted_source["data"] = "[inline image bytes omitted from text estimate]"
        compacted["source"] = compacted_source
    return compacted


def _estimate_provider_input_tokens(messages: object) -> int:
    """Conservatively estimate serialized provider input before any paid call."""

    try:
        text = json.dumps(
            _compact_provider_input_for_estimate(messages),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        text = str(messages)
    ascii_chars = sum(1 for char in text if ord(char) <= 0x7F)
    non_ascii_chars = len(text) - ascii_chars
    return (ascii_chars + 3) // 4 + non_ascii_chars


def _ensure_provider_input_budget(messages: object, *, max_input_tokens: int) -> None:
    estimated = _estimate_provider_input_tokens(messages)
    limit = max(1_000, int(max_input_tokens))
    if estimated > limit:
        raise ValueError(
            "Provider input budget exceeded before network request: "
            f"estimated {estimated} tokens (limit {limit}). "
            "Compress the conversation or narrow the attached material before retrying."
        )


def managed_gateway_session() -> requests.Session:
    """Return the desktop-process session used by ScanSci's managed gateway.

    Reusing its connection pool avoids recreating a proxy/TLS connection for
    every short chat turn. The session carries no user model credential.
    """

    return _MANAGED_GATEWAY_SESSION


def _fresh_retry_session(active_session: Any) -> Any:
    """Avoid retrying a failed managed-gateway stream on the same stale socket.

    The managed client intentionally keeps a connection pool for normal desktop
    traffic.  A proxy or worker can still close an idle keep-alive connection;
    retrying that request through a one-shot session is safer than closing the
    shared pool while another ScanSci turn may be streaming.
    """

    if active_session is _MANAGED_GATEWAY_SESSION:
        return requests.Session()
    return active_session


def warm_managed_gateway_connection(base_url: str, *, timeout: float = 20.0) -> bool:
    """Open the managed gateway connection in the background before first use."""

    normalized = str(base_url or "").rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[:-3]
    if not normalized.startswith("https://"):
        return False
    try:
        response = managed_gateway_session().get(f"{normalized}/healthz", timeout=timeout)
    except requests.RequestException:
        return False
    return response.ok


class ChatJsonClient(Protocol):
    def complete_json(self, messages: list[dict[str, str]], *, schema_name: str) -> Any:
        ...

    def complete_text(self, messages: list[dict[str, str]], *, max_tokens: int = 700) -> str:
        ...


class CascadingChatJsonClient:
    """Latch onto the first healthy configured client for a bounded workflow."""

    def __init__(self, clients: list[ChatJsonClient]) -> None:
        if not clients:
            raise ValueError("at least one chat client is required")
        self.clients = list(clients)
        self._active_index = 0
        primary = self.clients[0]
        self.session = getattr(primary, "session", None)
        self.thinking_mode = getattr(primary, "thinking_mode", None)
        self.model = getattr(primary, "model", "")
        # A fallback model is already the second logical attempt. Prevent each
        # provider from also performing its own two-request retry loop.
        if len(self.clients) > 1:
            for client in self.clients:
                if hasattr(client, "logical_request_limit"):
                    setattr(client, "logical_request_limit", 1)

    @property
    def active_model(self) -> str:
        return str(getattr(self.clients[self._active_index], "model", ""))

    def complete_json(self, messages: list[dict[str, str]], *, schema_name: str) -> Any:
        return self._complete("complete_json", messages, schema_name=schema_name)

    def complete_text(self, messages: list[dict[str, str]], *, max_tokens: int = 700) -> str:
        return str(self._complete("complete_text", messages, max_tokens=max_tokens))

    def _complete(self, method_name: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for index in range(self._active_index, len(self.clients)):
            client = self.clients[index]
            try:
                result = getattr(client, method_name)(messages, **kwargs)
            except ValueError as error:
                last_error = error
                # A structured-output parse failure proves that the provider
                # answered even though it did not honor the JSON contract.
                # When a previous provider was unavailable, keep this
                # reachable fallback latched so a subsequent plain-text
                # recovery does not wait on the dead primary again.
                if method_name == "complete_json" and index > self._active_index:
                    self._active_index = index
                continue
            except RuntimeError as error:
                last_error = error
                continue
            self._active_index = index
            return result
        if last_error is not None:
            raise last_error
        raise RuntimeError("no chat client completed the request")


class OpenAICompatibleChatJsonClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        session: Any | None = None,
        thinking_mode: str | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required for openai-compatible chat")
        if not api_key:
            raise ValueError("api_key is required for openai-compatible chat")
        if not model:
            raise ValueError("model is required for openai-compatible chat")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.thinking_mode = thinking_mode if thinking_mode in {"enabled", "disabled"} else None
        self._rate_limited_until = 0.0
        self.logical_request_limit = _MAX_LOGICAL_REQUESTS

    def complete_json(self, messages: list[dict[str, str]], *, schema_name: str) -> Any:
        if time.monotonic() < self._rate_limited_until:
            raise RuntimeError("模型结构化响应暂时受限，已立即切换到本地证据流程。")
        structured_messages = _with_structured_output_instruction(messages, schema_name=schema_name)
        _ensure_provider_input_budget(
            structured_messages,
            max_input_tokens=_STRUCTURED_PROVIDER_INPUT_TOKENS,
        )
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": structured_messages,
            "response_format": {"type": "json_object"},
            # Structured workflows need enough visible completion budget. Some
            # reasoning models otherwise spend their default budget on hidden
            # analysis and return an empty JSON message.
            # A bounded answer-claim budget prevents compatible models from
            # repeating a valid claim until the response is truncated, which
            # would turn otherwise useful RAG output into invalid JSON.
            "max_tokens": (
                768
                if schema_name in {
                    "answer_claims",
                    "claim_verification",
                    "retrieval_queries",
                    "literature_review_citation_attribution",
                }
                # Reasoning-capable compatible models can spend several
                # thousand completion tokens before emitting the visible JSON.
                # A review section is still bounded by its schema and length
                # validator, so the larger ceiling prevents empty responses
                # without permitting unbounded prose.
                # Review sections are capped at 600–1200 Chinese characters;
                # an 8K completion budget makes compatible gateways reject or
                # throttle the request before returning any JSON. Keep enough
                # room for a structured paragraph while avoiding needless
                # provider quota consumption.
                else 3072 if schema_name == "literature_review_section"
                else 1024 if schema_name == "literature_review_overview"
                else 3072 if schema_name == "evidence_grounded_literature_review" else 4096
            ),
            "temperature": (
                0
                if schema_name in {
                    "evidence_grounded_literature_review",
                    "literature_review_section",
                    "literature_review_overview",
                    "literature_review_citation_attribution",
                }
                else 0.2
            ),
        }
        request_body.update(chat_thinking_parameters(self.base_url, self.model, self.thinking_mode))
        # Review synthesis already validates every paragraph and has a
        # grounded evidence fallback. Retrying malformed section JSON here,
        # and then retrying the same section again at the workflow layer,
        # multiplies a slow gateway call without improving the accepted text.
        attempts = _structured_attempt_limit(schema_name)
        requests_used = 0
        last_failure: StructuredOutputError | None = None
        for attempt in range(attempts):
            active_body = dict(request_body)
            if attempt:
                active_body["temperature"] = 0
                retry_instruction = _structured_retry_instruction(
                    schema_name,
                    last_failure
                    or StructuredOutputError(schema_name, category="invalid_output", detail="previous attempt failed"),
                    attempt=attempt,
                )
                active_body["messages"] = [
                    {
                        "role": "system",
                        "content": retry_instruction,
                    },
                    *structured_messages,
                ]
            response: Any | None = None
            while requests_used < self.logical_request_limit:
                try:
                    requests_used += 1
                    response = self.session.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=active_body,
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    self._rate_limited_until = 0.0
                    break
                except requests.RequestException as error:
                    status = getattr(getattr(error, "response", None), "status_code", None)
                    if status not in {429, 502, 503, 504} or requests_used >= self.logical_request_limit:
                        raise _model_transport_exception(error, prefix="模型结构化响应暂时不可用") from error
                    headers = dict(getattr(getattr(error, "response", None), "headers", {}) or {})
                    try:
                        requested_delay = float(headers.get("Retry-After", headers.get("retry-after", 0)) or 0)
                    except (TypeError, ValueError):
                        requested_delay = 0
                    if status == 429:
                        self._rate_limited_until = time.monotonic() + min(120.0, max(30.0, requested_delay))
                        if requested_delay > 8:
                            raise _model_transport_exception(error, prefix="模型结构化响应暂时不可用") from error
                        delay = min(8.0, max(requested_delay, 2.0))
                    else:
                        delay = min(12.0, max(requested_delay, float(2 ** requests_used)))
                    if response is not None:
                        close = getattr(response, "close", None)
                        if callable(close):
                            close()
                        response = None
                    time.sleep(delay)
            if response is None:
                raise RuntimeError("模型结构化响应暂时不可用，请稍后重试。")
            try:
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                return _parse_and_validate_structured_content(content, schema_name=schema_name)
            except StructuredOutputError as error:
                last_failure = error
                if requests_used >= self.logical_request_limit and attempt + 1 < attempts:
                    raise RuntimeError(
                        "Structured-output retry budget was consumed by transport retries"
                    ) from error
                if attempt + 1 >= attempts:
                    raise
            except (TypeError, ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
                category, detail = _json_failure_category(error)
                last_failure = StructuredOutputError(schema_name, category=category, detail=detail)
                if requests_used >= self.logical_request_limit and attempt + 1 < attempts:
                    raise RuntimeError(
                        "Structured-output retry budget was consumed by transport retries"
                    ) from error
                if attempt + 1 >= attempts:
                    raise last_failure from error
        if last_failure is not None:
            raise last_failure
        raise StructuredOutputError(schema_name, category="invalid_output", detail="no usable response")

    def complete_text(self, messages: list[dict[str, str]], *, max_tokens: int = 700) -> str:
        if time.monotonic() < self._rate_limited_until:
            raise RuntimeError("模型文本响应暂时受限，已立即保留原文证据。")
        result = complete_chat_text(
            "openai-compatible",
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            messages=messages,
            timeout=self.timeout,
            session=self.session,
            thinking_mode=self.thinking_mode,
            max_tokens=max_tokens,
            temperature=0.1,
            max_requests=self.logical_request_limit,
        )
        return str(result)


class AnthropicCompatibleChatJsonClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        session: Any | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required for anthropic-compatible chat")
        if not api_key:
            raise ValueError("api_key is required for anthropic-compatible chat")
        if not model:
            raise ValueError("model is required for anthropic-compatible chat")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.logical_request_limit = 1

    def complete_json(self, messages: list[dict[str, str]], *, schema_name: str) -> Any:
        _ensure_provider_input_budget(
            messages,
            max_input_tokens=_STRUCTURED_PROVIDER_INPUT_TOKENS,
        )
        system_parts = [item["content"] for item in messages if item.get("role") == "system"]
        conversation = [item for item in messages if item.get("role") in {"user", "assistant"}]
        response = self.session.post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": "\n\n".join(system_parts) + "\nReturn one valid JSON object only.",
                "messages": conversation,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        content = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict))
        return _parse_json_content(content)

    def complete_text(self, messages: list[dict[str, str]], *, max_tokens: int = 700) -> str:
        result = complete_chat_text(
            "anthropic-compatible",
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            messages=messages,
            timeout=self.timeout,
            session=self.session,
            max_tokens=max_tokens,
            temperature=0.1,
            max_requests=self.logical_request_limit,
        )
        return str(result)


class OpenAIResponsesJsonClient:
    """Structured-output client backed by the OpenAI Responses surface."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        provider_id: str = "",
        responses_enabled: bool = False,
        timeout: float = 60.0,
        session: Any | None = None,
        thinking_mode: str | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.model = str(model or "")
        self.provider_id = str(provider_id or "")
        self.responses_enabled = bool(responses_enabled)
        self.timeout = float(timeout)
        self.session = session
        self.thinking_mode = thinking_mode if thinking_mode in {"enabled", "disabled"} else None
        self.logical_request_limit = _MAX_LOGICAL_REQUESTS
        if not self.base_url or not self.api_key or not self.model:
            raise ValueError("base_url, api_key, and model are required for OpenAI Responses")

    def complete_json(self, messages: list[dict[str, str]], *, schema_name: str) -> Any:
        structured_messages = _with_structured_output_instruction(messages, schema_name=schema_name)
        attempts = min(
            _structured_attempt_limit(schema_name),
            max(1, min(_MAX_LOGICAL_REQUESTS, int(self.logical_request_limit))),
        )
        last_failure: StructuredOutputError | None = None
        for attempt in range(attempts):
            active_messages = structured_messages
            if attempt:
                active_messages = [
                    {
                        "role": "system",
                        "content": _structured_retry_instruction(
                            schema_name,
                            last_failure
                            or StructuredOutputError(
                                schema_name,
                                category="invalid_output",
                                detail="previous attempt failed",
                            ),
                            attempt=attempt,
                        ),
                    },
                    *structured_messages,
                ]
            _ensure_provider_input_budget(
                active_messages,
                max_input_tokens=_STRUCTURED_PROVIDER_INPUT_TOKENS,
            )
            result = complete_model(
                ModelRequest(
                    provider_kind="openai-compatible",
                    provider_id=self.provider_id,
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=self.model,
                    messages=active_messages,
                    api_surface=RESPONSES,
                    responses_enabled=self.responses_enabled,
                    timeout=self.timeout,
                    max_tokens=4096,
                    temperature=0.0,
                    thinking_mode=self.thinking_mode,
                    response_format={"type": "json_object"},
                    # The outer loop owns the shared correction budget. A
                    # single transport request per correction keeps one bad
                    # response from multiplying into four paid calls.
                    max_requests=1,
                    session=self.session,
                )
            )
            try:
                return _parse_and_validate_structured_content(result.text, schema_name=schema_name)
            except StructuredOutputError as error:
                last_failure = error
                if attempt + 1 >= attempts:
                    raise
        raise last_failure or StructuredOutputError(schema_name, category="invalid_output", detail="no usable response")

    def complete_text(self, messages: list[dict[str, str]], *, max_tokens: int = 700) -> str:
        result = complete_model(
            ModelRequest(
                provider_kind="openai-compatible",
                provider_id=self.provider_id,
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                messages=messages,
                api_surface=RESPONSES,
                responses_enabled=self.responses_enabled,
                timeout=self.timeout,
                max_tokens=max_tokens,
                temperature=0.1,
                thinking_mode=self.thinking_mode,
                max_requests=self.logical_request_limit,
            )
        )
        return result.text


def build_chat_json_client(
    provider: str,
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    timeout: float = 60.0,
    session: Any | None = None,
    thinking_mode: str | None = None,
    api_surface: str = CHAT_COMPLETIONS,
    provider_id: str = "",
    responses_enabled: bool = False,
) -> ChatJsonClient:
    name = (provider or "").strip().lower()
    if name in {"openai-compatible", "openai"}:
        resolved_base_url = base_url or os.getenv("SCANSCI_CHAT_BASE_URL", "")
        resolved_api_key = api_key or os.getenv("SCANSCI_CHAT_API_KEY", "")
        resolved_model = model or os.getenv("SCANSCI_CHAT_MODEL", "")
        selected_surface = select_api_surface(
            api_surface,
            provider_kind=name,
            provider_id=provider_id,
            model=resolved_model,
            responses_enabled=responses_enabled,
        )
        if selected_surface == RESPONSES:
            return OpenAIResponsesJsonClient(
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                model=resolved_model,
                provider_id=provider_id,
                responses_enabled=responses_enabled,
                timeout=timeout,
                session=session,
                thinking_mode=thinking_mode,
            )
        if selected_surface == ANTHROPIC_MESSAGES:
            return AnthropicCompatibleChatJsonClient(
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                model=resolved_model,
                timeout=timeout,
                session=session,
            )
        return OpenAICompatibleChatJsonClient(
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            model=resolved_model,
            timeout=timeout,
            session=session,
            thinking_mode=thinking_mode,
        )
    if name in {"anthropic-compatible", "anthropic"}:
        resolved_base_url = base_url or os.getenv("SCANSCI_CHAT_BASE_URL", "")
        resolved_api_key = api_key or os.getenv("SCANSCI_CHAT_API_KEY", "")
        resolved_model = model or os.getenv("SCANSCI_CHAT_MODEL", "")
        return AnthropicCompatibleChatJsonClient(
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            model=resolved_model,
            timeout=timeout,
            session=session,
        )
    raise ValueError(f"Unsupported chat provider: {provider}")


def complete_chat_text(
    provider: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float = 90.0,
    session: Any | None = None,
    thinking_mode: str | None = None,
    include_usage: bool = False,
    max_tokens: int | None = 8192,
    temperature: float = 0.4,
    max_input_tokens: int = _DEFAULT_PROVIDER_INPUT_TOKENS,
    max_requests: int = _MAX_LOGICAL_REQUESTS,
    use_litellm: bool = True,
    api_surface: str = CHAT_COMPLETIONS,
    provider_id: str = "",
    responses_enabled: bool = False,
    previous_response_id: str | None = None,
    response_format: dict[str, Any] | None = None,
    provider_session_id: str = "",
) -> str | tuple[str, dict[str, int]]:
    """Return a plain conversational response without requiring a research library."""

    if not base_url:
        raise ValueError("base_url is required for chat")
    if not api_key:
        raise ValueError("api_key is required for chat")
    if not model:
        raise ValueError("model is required for chat")
    if not messages:
        raise ValueError("messages are required")
    provider_headers = provider_request_headers(
        provider_id,
        provider_session_id or f"request-{os.urandom(8).hex()}",
    )
    selected_surface = select_api_surface(
        api_surface,
        provider_kind=provider,
        provider_id=provider_id,
        model=model,
        responses_enabled=responses_enabled,
    )
    if selected_surface != CHAT_COMPLETIONS:
        result = complete_model(
            ModelRequest(
                provider_kind=provider,
                provider_id=provider_id,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                api_surface=selected_surface,
                responses_enabled=responses_enabled,
                timeout=timeout,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking_mode=thinking_mode,
                previous_response_id=previous_response_id,
                response_format=response_format,
                max_requests=max_requests,
                session=session,
                provider_session_id=provider_session_id,
            )
        )
        return (result.text, result.usage) if include_usage else result.text
    _ensure_provider_input_budget(messages, max_input_tokens=max_input_tokens)
    request_limit = max(1, min(_MAX_LOGICAL_REQUESTS, int(max_requests)))

    name = (provider or "").strip().lower()
    local_qwen = "reasoning_effort" in chat_thinking_parameters(base_url, model, thinking_mode)
    if (
        use_litellm
        and not local_qwen
        and not provider_headers
        and session is None
        and _litellm_enabled()
        and name in {"openai-compatible", "openai", "local", "anthropic-compatible", "anthropic"}
        and (max_tokens is not None or name not in {"anthropic-compatible", "anthropic"})
    ):
        return _litellm_complete_chat(
            name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout,
            thinking_mode=thinking_mode,
            include_usage=include_usage,
            max_tokens=max_tokens,
            temperature=temperature,
            max_requests=request_limit,
        )

    client = session or requests.Session()
    try:
        if name in {"openai-compatible", "openai", "local"}:
            request_body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": float(temperature),
                "stream": False,
            }
            if max_tokens is not None:
                request_body["max_tokens"] = max(64, int(max_tokens))
            request_body.update(chat_thinking_parameters(base_url, model, thinking_mode))
            response = None
            for retry_index in range(request_limit):
                try:
                    response = client.post(
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers=merge_request_headers(
                            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            provider_headers,
                        ),
                        json=request_body,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    break
                except requests.RequestException as error:
                    status = getattr(getattr(error, "response", None), "status_code", None)
                    if status not in {429, 502, 503, 504} or retry_index >= request_limit - 1:
                        raise
                    headers = dict(getattr(getattr(error, "response", None), "headers", {}) or {})
                    try:
                        requested_delay = float(headers.get("Retry-After", headers.get("retry-after", 0)) or 0)
                    except (TypeError, ValueError):
                        requested_delay = 0
                    time.sleep(min(30.0, max(requested_delay, float(2 ** (retry_index + 1)))))
            if response is None:
                raise RuntimeError("模型服务暂时不可用，请稍后重试。")
            response_payload = response.json()
            content = response_payload["choices"][0]["message"].get("content", "")
            if isinstance(content, list):
                content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
            text = str(content).strip()
            usage = _chat_usage(response_payload)
        elif name in {"anthropic-compatible", "anthropic"}:
            system = "\n\n".join(item["content"] for item in messages if item.get("role") == "system")
            conversation = [item for item in messages if item.get("role") in {"user", "assistant"}]
            response = client.post(
                f"{base_url.rstrip('/')}/messages",
                headers=merge_request_headers(
                    {
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    provider_headers,
                ),
                json={"model": model, "max_tokens": max(64, int(max_tokens if max_tokens is not None else 8192)), "system": system, "messages": conversation},
                timeout=timeout,
            )
            response.raise_for_status()
            response_payload = response.json()
            blocks = response_payload.get("content", [])
            text = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict)).strip()
            usage = _chat_usage(response_payload)
        else:
            raise ValueError(f"Unsupported chat provider: {provider}")
    except requests.RequestException as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status == 503:
            message = "内置模型服务尚未启用；请由 ScanSci 管理员保存服务密钥后重试。"
            wrapped = RuntimeError(message)
            wrapped.failure = {  # type: ignore[attr-defined]
                "code": "model_service_not_configured",
                "message": message,
                "detail": message,
                "retryable": False,
                "actions": [
                    {"id": "open_settings", "label": "检查模型服务", "kind": "settings"},
                    {"id": "branch", "label": "切换模型", "kind": "branch"},
                ],
            }
            wrapped.status_code = status  # type: ignore[attr-defined]
            raise wrapped from error
        raise _model_transport_exception(error, prefix="模型服务暂时不可用") from error

    if not text:
        raise RuntimeError("The model returned an empty response")
    if include_usage:
        return text, usage
    return text


def stream_chat_text(
    provider: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float = 90.0,
    session: Any | None = None,
    thinking_mode: str | None = None,
    max_tokens: int | None = 8192,
    max_continuations: int = 3,
    temperature: float = 0.4,
    max_input_tokens: int = _DEFAULT_PROVIDER_INPUT_TOKENS,
    max_requests: int = _STREAM_LOGICAL_REQUESTS,
    api_surface: str = CHAT_COMPLETIONS,
    provider_id: str = "",
    responses_enabled: bool = False,
    previous_response_id: str | None = None,
    response_format: dict[str, Any] | None = None,
    provider_session_id: str = "",
) -> Iterator[dict[str, Any]]:
    """Yield OpenAI-compatible text deltas and a final usage event.

    GLM sends its token accounting in the final SSE chunk.  Keeping that event
    intact lets the local UI show real usage without estimating it in the
    browser.
    """

    if not base_url:
        raise ValueError("base_url is required for chat")
    if not api_key:
        raise ValueError("api_key is required for chat")
    if not model:
        raise ValueError("model is required for chat")
    if not messages:
        raise ValueError("messages are required")
    provider_headers = provider_request_headers(
        provider_id,
        provider_session_id or f"request-{os.urandom(8).hex()}",
    )
    selected_surface = select_api_surface(
        api_surface,
        provider_kind=provider,
        provider_id=provider_id,
        model=model,
        responses_enabled=responses_enabled,
    )
    if selected_surface != CHAT_COMPLETIONS:
        continuation_messages = [dict(item) for item in messages]
        complete_text = ""
        total_usage: dict[str, int] = {}
        response_id = ""
        finish_reason = ""
        incomplete = False
        for attempt in range(max(0, int(max_continuations)) + 1):
            usage: dict[str, int] = {}
            attempt_text = ""
            finish_reason = "incomplete"
            for event in stream_model_events(
                ModelRequest(
                    provider_kind=provider,
                    provider_id=provider_id,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    messages=continuation_messages,
                    api_surface=selected_surface,
                    responses_enabled=responses_enabled,
                    timeout=timeout,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking_mode=thinking_mode,
                    previous_response_id=previous_response_id,
                    response_format=response_format,
                    max_requests=max_requests,
                    session=session,
                    provider_session_id=provider_session_id,
                )
            ):
                event_type = str(event.get("type", ""))
                if event_type == "text_delta":
                    fragment = str(event.get("content", ""))
                    attempt_text += fragment
                    yield {"type": "delta", "content": fragment}
                elif event_type == "reasoning_delta":
                    yield {"type": "reasoning", "content": str(event.get("content", ""))}
                elif event_type == "retry":
                    yield {"type": "retry", **dict(event)}
                elif event_type == "usage":
                    usage = dict(event.get("usage", {}) or {})
                elif event_type == "completed":
                    finish_reason = str(event.get("finish_reason", "stop"))
                    response_id = str(event.get("response_id", ""))
                elif event_type == "error":
                    raise RuntimeError(str(event.get("error", "Model stream failed")))
            complete_text += attempt_text
            total_usage = _merge_chat_usage(total_usage, usage)
            incomplete = finish_reason == "incomplete" or _response_needs_continuation(
                finish_reason=finish_reason,
                attempt_text=attempt_text,
                complete_text=complete_text,
                messages=messages,
            )
            if not incomplete or finish_reason == "incomplete" or attempt >= max_continuations or not attempt_text or response_format:
                break
            yield {"type": "continuation", "attempt": attempt + 1}
            continuation_messages = [
                *messages,
                {"role": "assistant", "content": _continuation_context(complete_text, messages)},
                {"role": "user", "content": "请从断点直接继续，避免重复已有内容，并完成答案。"},
            ]
        yield {
            "type": "done",
            "usage": total_usage,
            "finish_reason": finish_reason,
            "truncated": incomplete,
            "response_id": response_id,
        }
        return
    _ensure_provider_input_budget(messages, max_input_tokens=max_input_tokens)
    request_limit = max(1, min(_STREAM_LOGICAL_REQUESTS, int(max_requests)))
    local_endpoint = _is_loopback_endpoint(base_url)
    ollama_endpoint = (
        (provider_id == "local-runtime-ollama" or provider_id.startswith("local-runtime-ollama-"))
        and is_local_ollama_url(base_url)
    )
    if ollama_endpoint:
        # A cold local model has separate load and prompt-processing phases.
        timeout = max(float(timeout), 120.0)
    managed_endpoint = (
        str(provider_id or "").strip().casefold() == "scansci-managed"
        or "scansci-glm-gateway.932196440.workers.dev" in str(base_url or "").casefold()
    )
    # A local runtime can briefly answer with HTTP 500 while its worker is
    # starting or releasing a previous generation.  Keep the caller's logical
    # request budget intact, while allowing one transport retry for that case.
    if local_endpoint and not ollama_endpoint:
        transport_request_limit = max(request_limit, 2)
    elif managed_endpoint:
        # The managed gateway has its own minute limiter.  Keep one original
        # request and at most one provider retry for a direct compatibility
        # turn, even when callers use the historical default of four.
        transport_request_limit = min(request_limit, 2)
    else:
        transport_request_limit = request_limit

    name = (provider or "").strip().lower()
    local_qwen = "reasoning_effort" in chat_thinking_parameters(base_url, model, thinking_mode)
    if (
        not local_qwen
        and not ollama_endpoint
        and not provider_headers
        and session is None
        and _litellm_enabled()
        and name in {"openai-compatible", "openai", "local", "anthropic-compatible", "anthropic"}
        and (max_tokens is not None or name not in {"anthropic-compatible", "anthropic"})
    ):
        yield from _litellm_stream_chat(
            name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout,
            thinking_mode=thinking_mode,
            max_tokens=max_tokens,
            max_continuations=max_continuations,
            temperature=temperature,
            max_requests=request_limit,
        )
        return
    if name in {"anthropic-compatible", "anthropic"}:
        completed = complete_chat_text(
            provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout,
            session=session,
            thinking_mode=thinking_mode,
            include_usage=True,
            max_tokens=max_tokens,
            temperature=temperature,
            max_input_tokens=max_input_tokens,
            max_requests=request_limit,
            provider_id=provider_id,
            responses_enabled=responses_enabled,
            provider_session_id=provider_session_id,
        )
        text, usage = completed if isinstance(completed, tuple) else (completed, {})
        yield {"type": "delta", "content": text}
        yield {"type": "done", "usage": usage}
        return
    if name not in {"openai-compatible", "openai", "local"}:
        raise ValueError(f"Unsupported chat provider: {provider}")

    if ollama_endpoint:
        yield from warm_ollama_chat_model(base_url, model)

    client = session or requests.Session()
    rate_key = _stream_rate_key(provider, base_url)
    request_gate = _stream_request_gate(rate_key)
    continuation_messages = [dict(item) for item in messages]
    complete_text = ""
    total_usage: dict[str, int] = {}
    final_reason = ""
    final_incomplete = False
    continuation_limit = max(0, int(max_continuations))
    for attempt in range(continuation_limit + 1):
        _ensure_provider_input_budget(
            continuation_messages,
            max_input_tokens=max_input_tokens,
        )
        request_body: dict[str, Any] = {
            "model": model,
            "messages": continuation_messages,
            "temperature": float(temperature),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            request_body["max_tokens"] = max(64, int(max_tokens))
        request_body.update(chat_thinking_parameters(base_url, model, thinking_mode))

        response: Any | None = None
        attempt_text = ""
        attempt_reasoning = False
        attempt_usage: dict[str, int] = {}
        finish_reason = ""
        try:
            for retry_index in range(transport_request_limit):
                queued_delay = _stream_cooldown_remaining(rate_key)
                if queued_delay > 0:
                    yield {
                        "type": "retry",
                        "reason": "rate_limit",
                        "status": 429,
                        "delay_seconds": round(queued_delay, 1),
                        "attempt": retry_index + 1,
                        "queued": True,
                    }
                    time.sleep(queued_delay)
                    # The sleep above is this request's acknowledgement of the
                    # shared cooldown.  Clear the marker before acquiring the
                    # gate again; a final 429 still leaves the marker in place
                    # for the next logical request.
                    _stream_clear_cooldown(rate_key)
                try:
                    with request_gate:
                        gate_delay = _stream_cooldown_remaining(rate_key)
                        if gate_delay > 0:
                            time.sleep(gate_delay)
                            _stream_clear_cooldown(rate_key)
                        response = client.post(
                            f"{base_url.rstrip('/')}/chat/completions",
                            headers=merge_request_headers(
                                {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                provider_headers,
                            ),
                            json=request_body,
                            timeout=timeout,
                            stream=True,
                        )
                        response.raise_for_status()
                    _stream_clear_cooldown(rate_key)
                    break
                except requests.RequestException as error:
                    status = getattr(getattr(error, "response", None), "status_code", None)
                    ollama_timeout = ollama_endpoint and _is_stream_read_timeout(error)
                    # A network/TLS disconnect has no HTTP status.  It is
                    # safe to retry once because no model text was received;
                    # do not make users manually recover from a stale desktop
                    # keep-alive connection.
                    retryable = (
                        (status is None and not ollama_timeout)
                        or status in {429, 502, 503, 504}
                        or (local_endpoint and status == 500)
                    )
                    headers = dict(getattr(getattr(error, "response", None), "headers", {}) or {})
                    try:
                        requested_delay = float(headers.get("Retry-After", headers.get("retry-after", 0)) or 0)
                    except (TypeError, ValueError):
                        requested_delay = 0
                    base_delay = 2 ** (retry_index + 2) if status == 429 else 2 ** (retry_index + 1)
                    delay = min(45.0, max(requested_delay, float(base_delay)))
                    if status == 429:
                        _stream_set_cooldown(rate_key, delay)
                    if not retryable or retry_index >= transport_request_limit - 1:
                        # Release the failed streaming connection before
                        # raising so the socket is not leaked to the caller.
                        if response is not None:
                            response.close()
                            response = None
                        prefix = (
                            f"Ollama 模型 {model} 在 {int(timeout)} 秒内未返回数据"
                            if ollama_timeout
                            else "模型流式响应暂时不可用"
                        )
                        raise _model_transport_exception(
                            error,
                            prefix=prefix,
                            local_timeout=ollama_timeout,
                        ) from error
                    if response is not None:
                        response.close()
                        response = None
                    client = _fresh_retry_session(client)
                    yield {
                        "type": "retry",
                        "reason": "rate_limit" if status == 429 else "temporary_upstream_error",
                        "status": status,
                        "delay_seconds": delay,
                        "attempt": retry_index + 1,
                    }
                    time.sleep(delay)
                    _stream_clear_cooldown(rate_key)
            content_type = str(dict(getattr(response, "headers", {}) or {}).get("Content-Type", "")).lower()
            if "text/event-stream" not in content_type:
                # A managed worker may briefly return a non-SSE gateway page
                # before it has attached the upstream stream.  Restart only
                # before any text is delivered, so a retry can never duplicate
                # part of a user-visible answer.
                if not complete_text and request_limit > 1:
                    yield {
                        "type": "retry",
                        "reason": "temporary_upstream_error",
                        "status": None,
                        "delay_seconds": 1.0,
                        "attempt": 1,
                    }
                    time.sleep(1.0)
                    yield from stream_chat_text(
                        provider,
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        messages=messages,
                        timeout=timeout,
                        session=_fresh_retry_session(client),
                        thinking_mode=thinking_mode,
                        max_tokens=max_tokens,
                        max_continuations=max_continuations,
                        temperature=temperature,
                        max_input_tokens=max_input_tokens,
                        max_requests=1,
                        provider_id=provider_id,
                        responses_enabled=responses_enabled,
                        provider_session_id=provider_session_id,
                    )
                    return
                raise RuntimeError("The model service did not return a streaming response")
            # Some OpenAI-compatible gateways omit `charset=utf-8` for SSE.
            response.encoding = "utf-8"
            decoder = codecs.getincrementaldecoder("utf-8")()
            for raw_line in response.iter_lines(decode_unicode=False):
                if isinstance(raw_line, bytes):
                    line = decoder.decode(raw_line + b"\n").rstrip("\r\n")
                else:
                    line = str(raw_line or "")
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_usage = _chat_usage(event)
                if event_usage:
                    attempt_usage = event_usage
                choices = event.get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = str(choice.get("finish_reason"))
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    attempt_reasoning = True
                    yield {"type": "reasoning", "content": reasoning}
                content = delta.get("content", "")
                if isinstance(content, list):
                    content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
                fragment = str(content or "")
                if fragment:
                    attempt_text += fragment
                    yield {"type": "delta", "content": fragment}
        except requests.RequestException as error:
            ollama_timeout = ollama_endpoint and _is_stream_read_timeout(error)
            # Reading an SSE stream can fail after HTTP 200 (for example, a
            # proxy closes an idle keep-alive connection).  If it fails before
            # the first delta, restart the full request once on a fresh session.
            if (
                not attempt_text and not attempt_reasoning and not complete_text and request_limit > 1
                and not ollama_timeout
            ):
                yield {
                    "type": "retry",
                    "reason": "temporary_upstream_error",
                    "status": getattr(getattr(error, "response", None), "status_code", None),
                    "delay_seconds": 1.0,
                    "attempt": 1,
                }
                time.sleep(1.0)
                yield from stream_chat_text(
                    provider,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    messages=messages,
                    timeout=timeout,
                    session=_fresh_retry_session(client),
                    thinking_mode=thinking_mode,
                    max_tokens=max_tokens,
                    max_continuations=max_continuations,
                    temperature=temperature,
                    max_input_tokens=max_input_tokens,
                    max_requests=1,
                    provider_id=provider_id,
                    responses_enabled=responses_enabled,
                    provider_session_id=provider_session_id,
                )
                return
            prefix = (
                f"Ollama 模型 {model} 在 {int(timeout)} 秒内未返回数据"
                if ollama_timeout
                else "模型流式响应暂时不可用"
            )
            raise _model_transport_exception(
                error,
                prefix=prefix,
                local_timeout=ollama_timeout,
            ) from error
        finally:
            if response is not None:
                response.close()

        complete_text += attempt_text
        total_usage = _merge_chat_usage(total_usage, attempt_usage)
        final_reason = finish_reason
        final_incomplete = _response_needs_continuation(
            finish_reason=finish_reason,
            attempt_text=attempt_text,
            complete_text=complete_text,
            messages=messages,
        )
        if not final_incomplete or attempt >= continuation_limit or not attempt_text:
            break
        yield {"type": "continuation", "attempt": attempt + 1}
        continuation_messages = [
            *messages,
            {"role": "assistant", "content": _continuation_context(complete_text, messages)},
            {
                "role": "user",
                "content": (
                    "请从断点直接继续，不要重复已有内容，不要写‘继续’等过渡语，"
                    "并务必收束成一个完整答案。"
                    + _completion_marker_reminder(messages)
                ),
            },
        ]
    yield {
        "type": "done",
        "usage": total_usage,
        "finish_reason": final_reason or "stop",
        "truncated": final_incomplete,
    }


def _chat_usage(payload: Any) -> dict[str, int]:
    """Normalize token accounting from OpenAI- and Anthropic-style responses."""

    if not isinstance(payload, dict):
        return {}
    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, dict):
        return {}

    def token_count(*keys: str) -> int | None:
        for key in keys:
            value = raw_usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    prompt_tokens = token_count("prompt_tokens", "input_tokens")
    completion_tokens = token_count("completion_tokens", "output_tokens")
    total_tokens = token_count("total_tokens")
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    usage: dict[str, int] = {}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        usage["completion_tokens"] = completion_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    return usage


def _merge_chat_usage(total: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    """Add token usage across bounded automatic continuation requests."""

    merged = dict(total)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = current.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            merged[key] = int(merged.get(key, 0)) + value
    return merged


def _litellm_enabled() -> bool:
    if str(os.getenv("SCANSCI_DISABLE_LITELLM", "")).strip().lower() in {"1", "true", "yes"}:
        return False
    try:
        import litellm  # noqa: F401

        return True
    except ImportError:
        return False


def _litellm_model(provider: str, model: str) -> str:
    if provider in {"anthropic-compatible", "anthropic"}:
        return model if model.startswith("anthropic/") else f"anthropic/{model}"
    return model if model.startswith("openai/") else f"openai/{model}"


def _litellm_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump()
        return payload if isinstance(payload, dict) else {}
    mapping = getattr(value, "json", None)
    if callable(mapping):
        try:
            payload = json.loads(mapping())
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _litellm_kwargs(
    provider: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: float,
    thinking_mode: str | None,
    max_tokens: int | None = 8192,
    temperature: float = 0.4,
    max_requests: int = _MAX_LOGICAL_REQUESTS,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": _litellm_model(provider, model),
        "api_base": base_url.rstrip("/"),
        "api_key": api_key,
        "messages": messages,
        "timeout": timeout,
        "temperature": float(temperature),
        "drop_params": True,
        "max_retries": max(0, min(_MAX_LOGICAL_REQUESTS, int(max_requests)) - 1),
    }
    if max_tokens is not None:
        payload["max_tokens"] = max(64, int(max_tokens))
    payload.update(chat_thinking_parameters(base_url, model, thinking_mode))
    return payload


def _litellm_complete_chat(
    provider: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: float,
    thinking_mode: str | None,
    include_usage: bool,
    max_tokens: int | None = 8192,
    temperature: float = 0.4,
    max_requests: int = _MAX_LOGICAL_REQUESTS,
) -> str | tuple[str, dict[str, int]]:
    import litellm

    try:
        response = litellm.completion(
            **_litellm_kwargs(
                provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                timeout=timeout,
                thinking_mode=thinking_mode,
                max_tokens=max_tokens,
                temperature=temperature,
                max_requests=max_requests,
            )
        )
    except Exception as error:  # LiteLLM normalizes provider-specific failures
        raise _model_transport_exception(error, prefix="模型服务暂时不可用") from error
    payload = _litellm_payload(response)
    choices = list(payload.get("choices", []) or [])
    message = dict(choices[0].get("message", {}) or {}) if choices and isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    text = str(content or "").strip()
    if not text:
        raise RuntimeError("The model returned an empty response")
    usage = _chat_usage(payload)
    return (text, usage) if include_usage else text


def _litellm_stream_chat(
    provider: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: float,
    thinking_mode: str | None,
    max_tokens: int | None = 8192,
    max_continuations: int = 3,
    temperature: float = 0.4,
    max_requests: int = _MAX_LOGICAL_REQUESTS,
) -> Iterator[dict[str, Any]]:
    import litellm

    continuation_messages = [dict(item) for item in messages]
    complete_text = ""
    usage: dict[str, int] = {}
    final_reason = ""
    final_incomplete = False
    continuation_limit = max(0, int(max_continuations))
    try:
        for attempt in range(continuation_limit + 1):
            kwargs = _litellm_kwargs(
                provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=continuation_messages,
                timeout=timeout,
                thinking_mode=thinking_mode,
                max_tokens=max_tokens,
                temperature=temperature,
                max_requests=max_requests,
            )
            kwargs.update({"stream": True, "stream_options": {"include_usage": True}})
            attempt_text = ""
            attempt_usage: dict[str, int] = {}
            finish_reason = ""
            stream = litellm.completion(**kwargs)
            for chunk in stream:
                payload = _litellm_payload(chunk)
                event_usage = _chat_usage(payload)
                if event_usage:
                    attempt_usage = event_usage
                choices = list(payload.get("choices", []) or [])
                choice = dict(choices[0]) if choices and isinstance(choices[0], dict) else {}
                if choice.get("finish_reason"):
                    finish_reason = str(choice.get("finish_reason"))
                delta = dict(choice.get("delta", {}) or {})
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    yield {"type": "reasoning", "content": reasoning}
                content = delta.get("content", "")
                if isinstance(content, list):
                    content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
                if content:
                    fragment = str(content)
                    attempt_text += fragment
                    yield {"type": "delta", "content": fragment}
            complete_text += attempt_text
            usage = _merge_chat_usage(usage, attempt_usage)
            final_reason = finish_reason
            final_incomplete = _response_needs_continuation(
                finish_reason=finish_reason,
                attempt_text=attempt_text,
                complete_text=complete_text,
                messages=messages,
            )
            if not final_incomplete or attempt >= continuation_limit or not attempt_text:
                break
            yield {"type": "continuation", "attempt": attempt + 1}
            continuation_messages = [
                *messages,
                {"role": "assistant", "content": _continuation_context(complete_text, messages)},
                {
                    "role": "user",
                    "content": (
                        "请从断点直接继续，不重复已有内容，并完成答案。"
                        + _completion_marker_reminder(messages)
                    ),
                },
            ]
    except Exception as error:
        raise _model_transport_exception(error, prefix="模型流式响应暂时不可用") from error
    yield {
        "type": "done",
        "usage": usage,
        "finish_reason": final_reason or "stop",
        "truncated": final_incomplete,
    }


def _response_needs_continuation(
    *,
    finish_reason: str,
    attempt_text: str,
    complete_text: str,
    messages: list[dict[str, Any]],
) -> bool:
    """Detect provider-side early stops without extending normal short replies."""

    if finish_reason == "length":
        return True
    marker = _requested_completion_marker(messages)
    if marker and not complete_text.rstrip().endswith(marker):
        return True
    user_text = next(
        (
            str(item.get("content", ""))
            for item in reversed(messages)
            if str(item.get("role", "")).lower() == "user"
        ),
        "",
    ).casefold()
    if (
        re.search(r"html|网页", user_text)
        and re.search(r"完整|可运行|单文件|游戏|complete|standalone|single.file|game", user_text)
        and re.search(r"<html(?:\s|>)", complete_text, re.IGNORECASE)
        and not re.search(r"</html\s*>", complete_text, re.IGNORECASE)
    ):
        # ponytail: Tag presence catches cut-off standalone pages; validate a saved artifact with a parser when HTML export exists.
        return True
    value = attempt_text.rstrip()
    if not value:
        return False
    if value.endswith(("#", "**", "：", ":", "，", ",", "（", "(")):
        return True
    return complete_text.count("```") % 2 == 1


def _continuation_context(complete_text: str, messages: list[dict[str, Any]]) -> str:
    """Keep retries below the managed gateway input ceiling without duplication."""

    original_chars = sum(len(str(item.get("content", ""))) for item in messages)
    # Leave headroom for JSON framing and the continuation instruction under
    # the gateway's 48k character request limit.
    budget = max(2_000, 40_000 - original_chars)
    if len(complete_text) <= budget:
        return complete_text
    return "[较早内容已省略，以下为当前断点前的原文]\n" + complete_text[-budget:]


def _requested_completion_marker(messages: list[dict[str, Any]]) -> str:
    user_text = next(
        (
            str(item.get("content", ""))
            for item in reversed(messages)
            if str(item.get("role", "")).lower() == "user"
        ),
        "",
    )
    if not any(signal in user_text.lower() for signal in ("最后", "末尾", "结尾", "last line", "end with")):
        return ""
    markers = re.findall(r"【[^】\r\n]{1,40}】", user_text)
    return markers[-1] if markers else ""


def _completion_marker_reminder(messages: list[dict[str, Any]]) -> str:
    marker = _requested_completion_marker(messages)
    return f" 最后一行必须保留为 {marker}。" if marker else ""


def _public_model_error(error: BaseException, *, prefix: str) -> str:
    """Expose actionable status without reflecting provider secrets or bodies."""

    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(response, "status_code", None)
    response_url = str(getattr(response, "url", "") or "").lower()
    if response_url.startswith(("http://127.0.0.1", "https://127.0.0.1", "http://localhost", "https://localhost", "http://[::1]", "https://[::1]")):
        payload_reader = getattr(response, "json", None)
        try:
            payload = payload_reader() if callable(payload_reader) else {}
        except (TypeError, ValueError, requests.RequestException):
            payload = {}
        details = payload.get("error") if isinstance(payload, dict) else None
        detail = details.get("message") if isinstance(details, dict) else ""
        if detail:
            if "model runner has unexpectedly stopped" in str(detail).lower():
                return "本地模型运行中断。请检查 Ollama，或换用较小模型后重试。"
            return f"{prefix}（HTTP {status}）：{str(detail)[:1000]}"
    normalized = str(error).lower()
    provider_code = _provider_error_code(error)
    request_id = _provider_request_id(error)
    support_reference = f"（请求编号：{request_id}）" if request_id else ""
    if status == 402 or "insufficient balance" in normalized or "insufficient credit" in normalized:
        return "模型服务余额不足（HTTP 402）。请充值该服务商账户，或切换到其他可用模型。"
    if status in {401, 403}:
        return f"模型服务鉴权失败（HTTP {status}）。请检查 API Key、账户权限或服务地址。"
    if status == 429:
        if provider_code == "rate_limit_exceeded":
            return (
                "ScanSci 托管模型网关当前限流（HTTP 429）。已停止继续请求，请稍后重试。"
                + support_reference
            )
        if provider_code == "upstream_rate_limited":
            return (
                "托管模型的上游服务当前限流（HTTP 429）。ScanSci 已停止继续请求，请稍后重试。"
                + support_reference
            )
        return "模型服务当前限流（HTTP 429）。ScanSci 已停止继续请求，请稍后重试或切换模型。"
    if provider_code == "upstream_timeout":
        return "托管模型服务响应超时。ScanSci 已保留当前进度，请稍后重试。" + support_reference
    if provider_code == "upstream_connection_failed":
        return "暂时无法连接托管模型服务。请检查网络后重试。" + support_reference
    return f"{prefix}（HTTP {status}），请稍后重试。" if status else f"{prefix}，请稍后重试。"


def _model_transport_exception(error: BaseException, *, prefix: str, local_timeout: bool = False) -> RuntimeError:
    """Keep transient transport failures recoverable across the harness boundary."""

    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(response, "status_code", None)
    provider_code = _provider_error_code(error)
    normalized = str(error or "").lower()
    retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
    retryable_codes = {
        "rate_limit_exceeded",
        "upstream_rate_limited",
        "upstream_timeout",
        "upstream_connection_failed",
    }
    retryable = bool(
        status in retryable_statuses
        or provider_code in retryable_codes
        or isinstance(error, (requests.ConnectionError, requests.Timeout))
        or any(
            marker in normalized
            for marker in ("temporarily unavailable", "timed out", "timeout", "connection")
        )
    )
    if local_timeout:
        failure_code = "local_model_timeout"
    elif status == 429 or provider_code in {"rate_limit_exceeded", "upstream_rate_limited"}:
        failure_code = provider_code or "provider_rate_limited"
    elif provider_code:
        failure_code = provider_code
    elif status in retryable_statuses or retryable:
        failure_code = "model_transport_unavailable"
    else:
        failure_code = "model_transport_error"
    message = _public_model_error(error, prefix=prefix)
    headers = dict(getattr(response, "headers", {}) or {})
    try:
        retry_after = max(
            0.0,
            min(120.0, float(headers.get("Retry-After", headers.get("retry-after", 0)) or 0)),
        )
    except (TypeError, ValueError):
        retry_after = 0.0
    actions = [
        {"id": "retry", "label": "重试" if local_timeout else "自动重试", "kind": "retry"},
        {"id": "branch", "label": "保留现场并分支", "kind": "branch"},
    ]
    failure: dict[str, Any] = {
        "code": failure_code,
        "message": message,
        "detail": message,
        "retryable": retryable,
        "actions": actions,
        "recovery_actions": actions,
    }
    if retry_after:
        failure["retry_after_seconds"] = retry_after
    wrapped = RuntimeError(message)
    wrapped.failure = failure  # type: ignore[attr-defined]
    wrapped.status_code = status  # type: ignore[attr-defined]
    return wrapped


def _provider_error_code(error: BaseException) -> str:
    """Read the gateway's safe machine-readable error code when available."""

    response = getattr(error, "response", None)
    payload_reader = getattr(response, "json", None)
    if not callable(payload_reader):
        return ""
    try:
        payload = payload_reader()
    except (TypeError, ValueError, requests.RequestException):
        return ""
    if not isinstance(payload, dict):
        return ""
    details = payload.get("error")
    if not isinstance(details, dict):
        return ""
    return str(details.get("code", "")).strip()


def _provider_request_id(error: BaseException) -> str:
    """Return a safe gateway diagnostic id, never a provider response body."""

    headers = dict(getattr(getattr(error, "response", None), "headers", {}) or {})
    value = str(headers.get("x-scansci-request-id", "")).strip()
    return value if re.fullmatch(r"[A-Za-z0-9-]{8,80}", value) else ""


def analyze_vision_images(
    provider: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    question: str,
    images: list[dict[str, str]],
    timeout: float = 90.0,
    session: Any | None = None,
) -> str:
    """Ask an OpenAI- or Anthropic-compatible vision model about local images."""

    if not images:
        raise ValueError("至少需要一张图片")
    if len(images) > 4:
        raise ValueError("A single vision request is limited to 4 images")
    estimated_image_bytes = 0
    for image in images:
        encoded = str(image.get("data", ""))
        estimated_bytes = (len(encoded) * 3) // 4
        if estimated_bytes > 4 * 1024 * 1024:
            raise ValueError("A single vision image exceeds the 4 MB request limit")
        estimated_image_bytes += estimated_bytes
    if estimated_image_bytes > 10 * 1024 * 1024:
        raise ValueError("Vision images exceed the 10 MB total request limit")
    if len(str(question or "")) > 20_000:
        raise ValueError("The vision question exceeds the request input limit")
    if not base_url or not api_key or not model:
        raise ValueError("视觉模型的地址、密钥或模型 ID 尚未配置")
    prompt = (
        "你是 ScanSci 的图片分析助手。请基于用户上传的图片回答问题；"
        "准确区分图片中直接可见的信息和你的推断。图表请说明坐标、图例、趋势与不确定性；"
        "不要虚构论文、引文或实验结果。回答使用中文，简洁但完整。\n\n"
        f"用户问题：{question}"
    )
    kind = str(provider or "").strip().lower()
    active_session = session or requests.Session()
    if kind in {"local", "openai", "openai-compatible"}:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image['mime_type']};base64,{image['data']}"},
            }
            for image in images
        )
        response = active_session.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 2048,
                "temperature": 0.2,
            },
            timeout=float(timeout),
        )
        response.raise_for_status()
        payload = response.json()
        message = dict((payload.get("choices") or [{}])[0].get("message") or {})
        return _message_text(message.get("content"))
    if kind in {"anthropic", "anthropic-compatible"}:
        content = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": image["mime_type"], "data": image["data"]},
            }
            for image in images
        )
        response = active_session.post(
            f"{base_url.rstrip('/')}/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 2048, "messages": [{"role": "user", "content": content}]},
            timeout=float(timeout),
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return _message_text(blocks)
    raise ValueError(f"当前服务商不支持视觉请求：{provider}")


def _message_text(content: object) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = "\n".join(
            str(item.get("text", "")).strip()
            for item in content
            if isinstance(item, dict) and str(item.get("type", "text")) == "text"
        ).strip()
    else:
        text = ""
    if not text:
        raise ValueError("视觉模型没有返回可用文本")
    return text


def _parse_json_content(content: object) -> Any:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        balanced = _first_balanced_json_object(text)
        if balanced is None:
            raise
        return json.loads(balanced)


def _first_balanced_json_object(text: str) -> str | None:
    """Return one complete leading JSON object while rejecting truncation.

    Some compatible models append a Markdown note or one stray closing brace
    after an otherwise valid object. A quote-aware balance scan can discard
    only that suffix; it never invents missing braces for incomplete output.
    """

    start = str(text or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
            if depth < 0:
                return None
    return None


def _with_structured_output_instruction(
    messages: list[dict[str, str]],
    *,
    schema_name: str,
) -> list[dict[str, str]]:
    """Add a portable JSON contract for gateways that ignore response_format."""

    instruction = {
        "answer_claims": (
            'Return only JSON in this exact shape: {"answer":[{"claim_id":"c0001",'
            '"text":"claim text","quote_ids":["q0001"]}],"limitations":[]}. '
            "Return 1 to at most 4 non-overlapping claims, ordered by importance. "
            "Never repeat a claim. Use only quote_ids present in the evidence table. "
            "Stop immediately after the JSON object and do not return Markdown."
        ),
        "claim_verification": (
            'Return only JSON in this exact shape: {"claims":[{"claim_id":"c0001",'
            '"support_status":"supported","verification_score":0.95}]}. '
            "Return exactly one verdict for every supplied claim_id, never repeat a claim_id, "
            "and stop immediately after the JSON object."
        ),
        "retrieval_queries": (
            'Return only JSON in this exact shape: {"queries":["query one","query two"]}. '
            "Return 2 to 4 short, keyword-rich search queries, include one English query when the question is not English, "
            "preserve named entities and numbers, do not answer the question, and do not return Markdown."
        ),
        "evidence_grounded_literature_review": (
            "Return one complete JSON object only, without Markdown fences, using this section shape exactly: "
            '{"sections":[{"id":"...","title":"...","text":"one paragraph","citation_ids":["1"]}]}. '
            "Do not create a paragraphs array. Keep every section from the supplied plan and write exactly one concise "
            "paragraph per section. Keep each Chinese paragraph within 220 Chinese characters "
            "or each English paragraph within 140 words. Use at most three comparison rows, at most two controversies, "
            "and exactly two open questions. Every factual paragraph or row must cite only supplied citation_ids. "
            "Stop immediately after the closing brace."
        ),
        "literature_review_section": (
            'Return only JSON in this exact shape: {"sentences":[{"text":"one complete sentence",'
            '"citation_ids":["1"]}]}. Every sentence must carry only the supplied citation_ids that directly '
            "support that sentence. Do not collect citations at paragraph end, do not add other keys, and stop "
            "immediately after the closing brace."
        ),
        "literature_review_citation_attribution": (
            'Return only JSON in this exact shape: {"assignments":[{"sentence_id":"s1",'
            '"citation_ids":["1"]}]}. Include every supplied sentence_id exactly once, keep each '
            "citation_ids list to one or two supplied IDs that directly support the complete sentence, "
            "return an empty list when unsupported, do not repeat sentence text, and stop after the closing brace."
        ),
        "literature_review_overview": (
            'Return only JSON with keys "title", "abstract", "comparison_table", "controversies", '
            '"open_questions", and "limitations". Use abstract {"text":"...","citation_ids":["1"]}; '
            'comparison_table {"columns":["研究对象","方法","主要发现","局限"],"rows":'
            '[{"cells":["...","...","...","..."],"citation_ids":["1"]}]}; controversies as at most two '
            'objects {"text":"...","citation_ids":["1"]}; open_questions as exactly two objects '
            '{"text":"...","basis":"...","citation_ids":["1"]}; and limitations as short strings. '
            "Use only supplied citation_ids, output one complete JSON object, and do not return Markdown."
        ),
    }.get(str(schema_name or ""))
    copied = [dict(item) for item in messages]
    if not instruction:
        return copied
    for item in copied:
        if item.get("role") == "system":
            item["content"] = f"{item.get('content', '').rstrip()}\n\n{instruction}"
            return copied
    return [{"role": "system", "content": instruction}, *copied]


def _recover_structured_content(content: object, *, schema_name: str) -> dict[str, Any] | None:
    """Recover the one safe contract emitted as cited Markdown by some models."""

    if schema_name == "literature_review_section":
        return _recover_known_object_keys(content, ("sentences", "text", "citation_ids"))
    if schema_name == "literature_review_citation_attribution":
        return _recover_known_object_keys(content, ("assignments",))
    if schema_name == "literature_review_overview":
        return _recover_known_object_keys(
            content,
            ("title", "abstract", "comparison_table", "controversies", "open_questions", "limitations"),
        )
    if schema_name == "evidence_grounded_literature_review":
        return _recover_review_json(content)
    if schema_name != "answer_claims":
        return None
    text = str(content or "").strip()
    if not text:
        return None
    claims: list[dict[str, Any]] = []
    heading = ""
    for block in re.split(r"\n\s*\n", text):
        clean_block = block.strip()
        quote_ids = list(dict.fromkeys(re.findall(r"\[\s*(q\d{4,})\s*\]", clean_block, flags=re.IGNORECASE)))
        if not quote_ids:
            candidate = re.sub(r"^[#*\s]+|[#*\s]+$", "", clean_block).strip()
            if candidate and "\n" not in candidate and len(candidate) <= 40:
                heading = candidate
            continue
        claim_text = re.sub(r"\[\s*q\d{4,}\s*\]", "", clean_block, flags=re.IGNORECASE)
        claim_text = re.sub(r"^[#*\s]+|[#*\s]+$", "", claim_text).strip()
        if not claim_text:
            continue
        if heading and not claim_text.startswith(heading):
            claim_text = f"{heading}：{claim_text}"
        claims.append(
            {
                "claim_id": f"c{len(claims) + 1:04d}",
                "text": claim_text,
                "quote_ids": [item.lower() for item in quote_ids],
            }
        )
    if not claims:
        return None
    return {"answer": claims, "limitations": []}


def _recover_known_object_keys(content: object, keys: tuple[str, ...]) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    candidate = text
    for key in keys:
        candidate = re.sub(rf'(?<=[,{{])\s*{re.escape(key)}"\s*:', f'"{key}":', candidate)
    try:
        recovered = _parse_json_content(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return recovered if isinstance(recovered, dict) else None


def _recover_review_json(content: object) -> dict[str, Any] | None:
    """Repair two bounded punctuation omissions in the fixed review schema.

    The model sometimes omits either the sections array terminator or the
    opening quote on one of the known top-level keys. Repairs are limited to
    those literal schema keys. The candidate must then pass the regular JSON
    parser; no missing terminal content is invented.
    """

    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    candidate = text
    for key in ("comparison_table", "controversies", "open_questions", "limitations"):
        candidate = re.sub(rf'(?<=[,{{])\s*{re.escape(key)}"\s*:', f'"{key}":', candidate)
    try:
        repaired = _parse_json_content(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        repaired = None
    if isinstance(repaired, dict):
        return repaired

    marker = '"comparison_table"'
    marker_index = candidate.find(marker)
    if marker_index < 0 or '"sections"' not in candidate[:marker_index]:
        return None
    separator_index = candidate.rfind(",", 0, marker_index)
    if separator_index < 0 or _open_json_delimiters(candidate[:separator_index]) != ["{", "["]:
        return None
    candidate = f"{candidate[:separator_index]}]{candidate[separator_index:]}"
    try:
        repaired = _parse_json_content(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return repaired if isinstance(repaired, dict) else None


def _open_json_delimiters(text: str) -> list[str]:
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in str(text or ""):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            expected = "{" if char == "}" else "["
            if not stack or stack[-1] != expected:
                return []
            stack.pop()
    return stack
