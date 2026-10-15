"""Trusted, provider-neutral model metadata for the Pi runtime.

The desktop settings record is the authority for declared model capabilities.
Model identifiers are deliberately not guessed: an unknown or local-looking
context label degrades to a conservative 32K text model and records why.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping


MODEL_RUNTIME_SCHEMA = "scansci.model-runtime.v1"
DEFAULT_CONTEXT_WINDOW_TOKENS = 32 * 1024
MIN_CONTEXT_WINDOW_TOKENS = 16 * 1024
_MAX_CONTEXT_WINDOW_TOKENS = 4 * 1024 * 1024
_CONTEXT_WINDOW = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[kKmM])?$")
_KNOWN_CAPABILITIES = frozenset({"reasoning", "tool", "vision", "audio", "coding", "embedding", "reranking"})
_DESCRIPTOR_FIELDS = frozenset({
    "schema_version",
    "provider_id",
    "provider_kind",
    "model_id",
    "api_surface",
    "context_window_tokens",
    "provider_input_tokens",
    "context_guard_tokens",
    "compaction_reserve_tokens",
    "keep_recent_tokens",
    "max_output_tokens",
    "input_modalities",
    "capabilities",
    "reasoning",
    "tool_use",
    "context_provenance",
    "capability_provenance",
    "degraded",
    "degradation_reasons",
})


def _parsed_context_window(value: object) -> tuple[int, bool]:
    if isinstance(value, bool):
        return DEFAULT_CONTEXT_WINDOW_TOKENS, False
    if isinstance(value, int):
        if MIN_CONTEXT_WINDOW_TOKENS <= value <= _MAX_CONTEXT_WINDOW_TOKENS:
            return value, True
        return DEFAULT_CONTEXT_WINDOW_TOKENS, False
    if not isinstance(value, str):
        return DEFAULT_CONTEXT_WINDOW_TOKENS, False
    match = _CONTEXT_WINDOW.fullmatch(value.strip())
    if match is None:
        return DEFAULT_CONTEXT_WINDOW_TOKENS, False
    number = float(match.group("value"))
    multiplier = {"": 1, "k": 1024, "m": 1024 * 1024}[(match.group("unit") or "").lower()]
    parsed = int(number * multiplier)
    if (
        not math.isfinite(number)
        or parsed < MIN_CONTEXT_WINDOW_TOKENS
        or parsed > _MAX_CONTEXT_WINDOW_TOKENS
    ):
        return DEFAULT_CONTEXT_WINDOW_TOKENS, False
    return parsed, True


def parse_context_window_tokens(value: object) -> int:
    """Parse a trusted settings value, falling back conservatively to 32K."""

    return _parsed_context_window(value)[0]


def _rounded_down(value: int, quantum: int = 1024) -> int:
    return max(quantum, (int(value) // quantum) * quantum)


def _bounded(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _budgets(context_window_tokens: int) -> dict[str, int]:
    window = max(MIN_CONTEXT_WINDOW_TOKENS, int(context_window_tokens))
    if window < DEFAULT_CONTEXT_WINDOW_TOKENS:
        guard = 1024
        reserve = _bounded(_rounded_down(window // 8), 1024, 32 * 1024)
        keep_recent = _bounded(_rounded_down(window // 10), 1024, 32 * 1024)
        return {
            "provider_input_tokens": window - reserve - guard,
            "context_guard_tokens": guard,
            "compaction_reserve_tokens": reserve,
            "keep_recent_tokens": keep_recent,
            "max_output_tokens": reserve,
        }
    guard = _bounded(_rounded_down(window // 32), 1024, 8192)
    reserve = _bounded(_rounded_down(window // 8), 4096, 32 * 1024)
    keep_recent = _bounded(_rounded_down(window // 10), 4096, 32 * 1024)
    max_output = _bounded(_rounded_down(window // 16), 4096, 16 * 1024)
    provider_input = max(4096, window - reserve - guard)
    return {
        "provider_input_tokens": provider_input,
        "context_guard_tokens": guard,
        "compaction_reserve_tokens": reserve,
        "keep_recent_tokens": keep_recent,
        "max_output_tokens": min(max_output, reserve),
    }


@dataclass(frozen=True)
class ModelRuntimeDescriptor:
    schema_version: str
    provider_id: str
    provider_kind: str
    model_id: str
    api_surface: str
    context_window_tokens: int
    provider_input_tokens: int
    context_guard_tokens: int
    compaction_reserve_tokens: int
    keep_recent_tokens: int
    max_output_tokens: int
    input_modalities: tuple[str, ...]
    capabilities: tuple[str, ...]
    reasoning: bool
    tool_use: bool
    context_provenance: str
    capability_provenance: str
    degraded: bool
    degradation_reasons: tuple[str, ...]

    @classmethod
    def for_testing(
        cls,
        *,
        context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        input_modalities: tuple[str, ...] = ("text",),
    ) -> "ModelRuntimeDescriptor":
        budgets = _budgets(context_window_tokens)
        return cls(
            schema_version=MODEL_RUNTIME_SCHEMA,
            provider_id="fixture",
            provider_kind="openai-compatible",
            model_id="fixture-model",
            api_surface="chat_completions",
            context_window_tokens=context_window_tokens,
            input_modalities=input_modalities,
            capabilities=("reasoning", "vision") if "image" in input_modalities else ("reasoning",),
            reasoning=True,
            tool_use=False,
            context_provenance="test",
            capability_provenance="test",
            degraded=False,
            degradation_reasons=(),
            **budgets,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ModelRuntimeDescriptor":
        if type(payload) is not dict or set(payload) != _DESCRIPTOR_FIELDS:
            raise ValueError("Model runtime descriptor must be a canonical v1 object")
        if str(payload.get("schema_version", "")) != MODEL_RUNTIME_SCHEMA:
            raise ValueError("Invalid model runtime descriptor schema")
        context, valid = _parsed_context_window(payload.get("context_window_tokens"))
        if not valid:
            raise ValueError("Invalid model runtime context window")
        expected = _budgets(context)
        for key, expected_value in expected.items():
            raw = payload.get(key)
            if isinstance(raw, bool) or not isinstance(raw, int) or raw != expected_value:
                raise ValueError(f"Invalid model runtime budget: {key}")
        raw_modalities = payload.get("input_modalities")
        if type(raw_modalities) is not list:
            raise ValueError("Invalid model runtime input modalities")
        modalities = tuple(raw_modalities)
        if modalities not in {("text",), ("text", "image")}:
            raise ValueError("Invalid model runtime input modalities")
        raw_capabilities = payload.get("capabilities")
        raw_reasons = payload.get("degradation_reasons")
        if (
            type(raw_capabilities) is not list
            or any(type(value) is not str or value not in _KNOWN_CAPABILITIES for value in raw_capabilities)
            or len(set(raw_capabilities)) != len(raw_capabilities)
            or type(raw_reasons) is not list
            or any(type(value) is not str or not value or len(value) > 160 for value in raw_reasons)
            or any(type(payload.get(key)) is not bool for key in ("reasoning", "tool_use", "degraded"))
        ):
            raise ValueError("Invalid model runtime capability metadata")
        capabilities = tuple(raw_capabilities)
        reasons = tuple(raw_reasons)
        if (
            ("image" in modalities) != ("vision" in capabilities)
            or bool(payload["reasoning"]) != ("reasoning" in capabilities)
            or bool(payload["tool_use"]) != ("tool" in capabilities)
            or bool(payload["degraded"]) != bool(reasons)
        ):
            raise ValueError("Invalid model runtime modality/capability consistency")
        for key, maximum in (
            ("provider_id", 160),
            ("provider_kind", 80),
            ("model_id", 300),
            ("api_surface", 40),
            ("context_provenance", 120),
            ("capability_provenance", 120),
        ):
            value = payload.get(key)
            if type(value) is not str or not value.strip() or len(value) > maximum:
                raise ValueError(f"Invalid model runtime descriptor field: {key}")
        return cls(
            schema_version=MODEL_RUNTIME_SCHEMA,
            provider_id=str(payload.get("provider_id", ""))[:160],
            provider_kind=str(payload.get("provider_kind", ""))[:80],
            model_id=str(payload.get("model_id", ""))[:300],
            api_surface=str(payload.get("api_surface", "chat_completions"))[:40],
            context_window_tokens=context,
            input_modalities=modalities,
            capabilities=capabilities,
            reasoning=payload["reasoning"],
            tool_use=payload["tool_use"],
            context_provenance=str(payload.get("context_provenance", "unknown"))[:120],
            capability_provenance=str(payload.get("capability_provenance", "unknown"))[:120],
            degraded=payload["degraded"],
            degradation_reasons=reasons,
            **expected,
        )

    @property
    def supports_images(self) -> bool:
        return "image" in self.input_modalities

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "model_id": self.model_id,
            "api_surface": self.api_surface,
            "context_window_tokens": self.context_window_tokens,
            "provider_input_tokens": self.provider_input_tokens,
            "context_guard_tokens": self.context_guard_tokens,
            "compaction_reserve_tokens": self.compaction_reserve_tokens,
            "keep_recent_tokens": self.keep_recent_tokens,
            "max_output_tokens": self.max_output_tokens,
            "input_modalities": list(self.input_modalities),
            "capabilities": list(self.capabilities),
            "reasoning": self.reasoning,
            "tool_use": self.tool_use,
            "context_provenance": self.context_provenance,
            "capability_provenance": self.capability_provenance,
            "degraded": self.degraded,
            "degradation_reasons": list(self.degradation_reasons),
        }


def descriptor_from_model_record(
    *,
    provider_id: str,
    provider_kind: str,
    model_id: str,
    model_record: Mapping[str, Any] | None,
    api_surface: str = "chat_completions",
) -> ModelRuntimeDescriptor:
    record = dict(model_record or {})
    context, context_valid = _parsed_context_window(record.get("context_window"))
    raw_capabilities = record.get("capabilities")
    capabilities = tuple(
        dict.fromkeys(
            str(value).strip().lower()
            for value in (raw_capabilities if isinstance(raw_capabilities, list) else [])
            if str(value).strip().lower() in _KNOWN_CAPABILITIES
        )
    )
    reasons: list[str] = []
    if not context_valid:
        reasons.append("context_window_defaulted_to_32k")
    if not isinstance(raw_capabilities, list):
        reasons.append("capabilities_missing")
    modalities = ("text", "image") if "vision" in capabilities else ("text",)
    return ModelRuntimeDescriptor(
        schema_version=MODEL_RUNTIME_SCHEMA,
        provider_id=str(provider_id),
        provider_kind=str(provider_kind),
        model_id=str(model_id),
        api_surface=str(api_surface or "chat_completions"),
        context_window_tokens=context,
        input_modalities=modalities,
        capabilities=capabilities,
        reasoning="reasoning" in capabilities,
        tool_use="tool" in capabilities,
        context_provenance="settings:model-record" if context_valid else "default:32k",
        capability_provenance="settings:model-record" if isinstance(raw_capabilities, list) else "default:text-only",
        degraded=bool(reasons),
        degradation_reasons=tuple(reasons),
        **_budgets(context),
    )


def descriptor_from_settings(
    settings: Mapping[str, Any],
    *,
    provider_id: str,
    model_id: str,
    provider_kind: str = "",
    api_surface: str = "chat_completions",
) -> ModelRuntimeDescriptor:
    provider = next(
        (
            dict(item)
            for item in list(settings.get("providers", []) or [])
            if isinstance(item, Mapping) and str(item.get("id", "")) == str(provider_id)
        ),
        {},
    )
    model = next(
        (
            dict(item)
            for item in list(provider.get("models", []) or [])
            if isinstance(item, Mapping) and str(item.get("id", "")) == str(model_id)
        ),
        {},
    )
    return descriptor_from_model_record(
        provider_id=provider_id,
        provider_kind=provider_kind or str(provider.get("kind", "openai-compatible")),
        model_id=model_id,
        model_record=model,
        api_surface=api_surface,
    )


__all__ = [
    "DEFAULT_CONTEXT_WINDOW_TOKENS",
    "MODEL_RUNTIME_SCHEMA",
    "ModelRuntimeDescriptor",
    "descriptor_from_model_record",
    "descriptor_from_settings",
    "parse_context_window_tokens",
]
