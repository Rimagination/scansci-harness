from __future__ import annotations

import base64
import hashlib
import json

import pytest

import scansci_html.context_policy as context_policy
from scansci_html.context_policy import (
    ContextEnvelopeError,
    build_token_envelope,
    estimate_message_tokens,
    estimate_text_tokens,
)
from scansci_html.model_metadata import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    ModelRuntimeDescriptor,
    descriptor_from_model_record,
    parse_context_window_tokens,
)
from scansci_html.research_agent import ResearchAgentRuntime


def _fixed_high_entropy_ascii(label: str, length: int) -> str:
    blocks: list[str] = []
    size = 0
    counter = 0
    while size < length:
        block = base64.b64encode(hashlib.sha256(f"{label}:{counter}".encode()).digest()).decode().rstrip("=")
        blocks.append(block)
        size += len(block)
        counter += 1
    return "".join(blocks)[:length]


_HIGH_ENTROPY_ASCII = _fixed_high_entropy_ascii("ascii", 70_000)
_HIGH_ENTROPY_JSON = json.dumps(
    {"records": [{"id": index, "nonce": _fixed_high_entropy_ascii(f"json-{index}", 160)} for index in range(390)]},
    separators=(",", ":"),
)
_HIGH_ENTROPY_TOOL_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            f"field_{index}": {
                "type": "string",
                "enum": [_fixed_high_entropy_ascii(f"schema-{index}-{item}", 48) for item in range(4)],
            }
            for index in range(245)
        },
    },
    separators=(",", ":"),
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (32_768, 32_768),
        ("16K", 16 * 1024),
        ("32K", 32 * 1024),
        ("200K", 200 * 1024),
        ("1.5M", int(1.5 * 1024 * 1024)),
        (" 200 k ", 200 * 1024),
        ("32768", 32_768),
    ],
)
def test_context_window_parser_accepts_numeric_and_human_units(raw: object, expected: int) -> None:
    assert parse_context_window_tokens(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "本地", "unknown", -1, 0, 8_192, "8K", True, False, object()])
def test_context_window_parser_fails_closed_to_32k(raw: object) -> None:
    assert parse_context_window_tokens(raw) == DEFAULT_CONTEXT_WINDOW_TOKENS


def test_runtime_descriptor_uses_trusted_record_and_declares_budget_invariants() -> None:
    descriptor = descriptor_from_model_record(
        provider_id="fixture",
        provider_kind="openai-compatible",
        model_id="vision-model",
        model_record={
            "id": "vision-model",
            "context_window": "200K",
            "capabilities": ["reasoning", "tool", "vision"],
        },
        api_surface="responses",
    )

    assert descriptor.schema_version == "scansci.model-runtime.v1"
    assert descriptor.context_window_tokens == 204_800
    assert descriptor.input_modalities == ("text", "image")
    assert descriptor.capability_provenance == "settings:model-record"
    assert descriptor.context_provenance == "settings:model-record"
    assert descriptor.degraded is False
    assert descriptor.provider_input_tokens == 173_056
    assert descriptor.compaction_reserve_tokens == 25_600
    assert descriptor.keep_recent_tokens == 20_480
    assert descriptor.max_output_tokens == 12_288

    small = descriptor_from_model_record(
        provider_id="fixture",
        provider_kind="openai-compatible",
        model_id="small",
        model_record={"id": "small", "context_window": "32K", "capabilities": ["reasoning"]},
    )
    assert small.provider_input_tokens == 27_648
    assert small.compaction_reserve_tokens == 4_096
    assert small.keep_recent_tokens == 4_096
    assert small.max_output_tokens == 4_096

    local = descriptor_from_model_record(
        provider_id="local-runtime-ollama-qwen3.5-9b",
        provider_kind="openai-compatible",
        model_id="qwen3.5:9b",
        model_record={"id": "qwen3.5:9b", "context_window": "16K", "capabilities": ["reasoning", "tool"]},
    )
    assert local.context_window_tokens == 16_384
    assert local.provider_input_tokens == 13_312
    assert local.compaction_reserve_tokens == 2_048
    assert local.max_output_tokens == 2_048
    assert ModelRuntimeDescriptor.from_payload(local.to_dict()) == local


def test_runtime_descriptor_marks_unknown_local_and_boolean_metadata_as_degraded() -> None:
    for value in ("本地", "unknown", -5, True):
        descriptor = descriptor_from_model_record(
            provider_id="local",
            provider_kind="local",
            model_id="fixture",
            model_record={"id": "fixture", "context_window": value, "capabilities": ["reasoning"]},
        )
        assert descriptor.context_window_tokens == DEFAULT_CONTEXT_WINDOW_TOKENS
        assert descriptor.degraded is True
        assert descriptor.degradation_reasons


def test_runtime_descriptor_payload_rejects_noncanonical_or_inconsistent_metadata() -> None:
    payload = ModelRuntimeDescriptor.for_testing().to_dict()
    with pytest.raises(ValueError, match="canonical"):
        ModelRuntimeDescriptor.from_payload({**payload, "unexpected": True})

    with pytest.raises(ValueError, match="modalit"):
        ModelRuntimeDescriptor.from_payload({
            **payload,
            "input_modalities": ["text", "image"],
            "capabilities": ["reasoning"],
        })


def test_token_estimator_is_conservative_for_english_chinese_json_and_emoji() -> None:
    assert estimate_text_tokens("a" * 400) >= 100
    assert estimate_text_tokens("科研能力" * 25) >= 100
    assert estimate_text_tokens("🧪" * 40) >= 40
    fixture = json.dumps({"query": "检索", "items": ["alpha", "beta"]}, ensure_ascii=False)
    assert estimate_text_tokens(fixture) >= 15


@pytest.mark.parametrize(
    ("fixture", "known_token_count"),
    [
        (_HIGH_ENTROPY_ASCII, 50_154),
        (_HIGH_ENTROPY_JSON, 47_488),
        (_HIGH_ENTROPY_TOOL_SCHEMA, 37_645),
    ],
    ids=["ascii", "json", "tool-schema"],
)
def test_unknown_tokenizer_fallback_is_an_upper_bound_for_high_entropy_fixtures(
    fixture: str,
    known_token_count: int,
) -> None:
    assert estimate_text_tokens(fixture) >= known_token_count


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [("gpt-4o", 4_375), ("gpt-4", 8_750)],
)
def test_known_openai_tokenizer_counts_repeated_text_without_byte_overrejection(model_id: str, expected: int) -> None:
    descriptor = descriptor_from_model_record(
        provider_id="openai",
        provider_kind="openai-compatible",
        model_id=model_id,
        model_record={"id": model_id, "context_window": "200K", "capabilities": ["reasoning", "tool"]},
    )

    assert estimate_text_tokens("X" * 70_000, descriptor=descriptor) == expected


def test_unknown_model_tokenizer_falls_back_to_utf8_byte_upper_bound() -> None:
    descriptor = ModelRuntimeDescriptor.for_testing(context_window_tokens=200 * 1024)

    assert estimate_text_tokens(_HIGH_ENTROPY_ASCII, descriptor=descriptor) == len(_HIGH_ENTROPY_ASCII.encode("utf-8"))


def test_image_base64_is_not_counted_as_text_tokens() -> None:
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "inspect this image"},
            {"type": "image", "data": "A" * 4_000_000, "mimeType": "image/png"},
        ],
    }
    assert estimate_message_tokens(message) < 5_000


def test_high_resolution_image_uses_a_conservative_dimension_aware_token_charge() -> None:
    raw = bytearray(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    )
    raw[16:20] = (8_000).to_bytes(4, "big")
    raw[20:24] = (5_000).to_bytes(4, "big")
    message = {
        "role": "user",
        "content": [{
            "type": "image",
            "data": base64.b64encode(raw).decode("ascii"),
            "mimeType": "image/png",
        }],
    }

    assert estimate_message_tokens(message) > ModelRuntimeDescriptor.for_testing().provider_input_tokens


def test_token_envelope_keeps_host_contract_explicit_skill_and_final_user_sentinel() -> None:
    descriptor = ModelRuntimeDescriptor.for_testing(context_window_tokens=32_768)
    messages = [
        {
            "role": "system",
            "content": "HOST-OWNED TASK CONTRACT sentinel-contract",
            "context_kind": "host_contract",
        },
        {
            "role": "system",
            "content": "<selected_skill id=\"method\">sentinel-skill</selected_skill>",
            "context_kind": "explicit_skill",
        },
        {
            "role": "user",
            "content": "old-user " + ("x" * 100_000),
            "context_ref": "turn-old",
        },
        {"role": "assistant", "content": "old assistant " + ("y" * 100_000)},
        {
            "role": "tool",
            "content": "REFERENCED-TOOL " + ("z" * 100_000),
            "context_ref": "tool-result-old",
        },
        {"role": "user", "content": "FINAL-USER-SENTINEL: answer this exact request"},
    ]

    projected, report = build_token_envelope(
        messages,
        descriptor=descriptor,
        host_contract={"contract_id": "contract", "allowed_tools": []},
    )
    encoded = json.dumps(projected, ensure_ascii=False)

    assert "sentinel-contract" in encoded
    assert "sentinel-skill" in encoded
    assert "FINAL-USER-SENTINEL" in encoded
    assert ("old-user" in encoded) is ("old assistant" in encoded)
    assert "old-user" not in encoded
    assert "REFERENCED-TOOL" in encoded
    assert report.estimated_tokens <= descriptor.provider_input_tokens
    assert report.omitted_messages >= 1 or report.truncated_messages >= 1
    assert "omission_sha256" in encoded


def test_long_context_twenty_turn_batch_recovers_every_sentinel() -> None:
    descriptor = ModelRuntimeDescriptor.for_testing(context_window_tokens=768_000)
    messages = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"LONG-CONTEXT-SENTINEL-{index:02d} " + (chr(65 + index) * 5_000),
        }
        for index in range(20)
    ]

    projected, report = build_token_envelope(messages, descriptor=descriptor)

    encoded = json.dumps(projected, ensure_ascii=False)
    assert sum(len(str(message["content"])) for message in messages) >= 100_000
    assert all(f"LONG-CONTEXT-SENTINEL-{index:02d}" in encoded for index in range(20))
    assert report.estimated_tokens <= descriptor.provider_input_tokens


def test_token_envelope_never_truncates_mandatory_final_user_request() -> None:
    descriptor = ModelRuntimeDescriptor.for_testing(context_window_tokens=32_768)
    final_request = "FINAL-SENTINEL " + ("重要" * 20_000)

    with pytest.raises(ContextEnvelopeError, match="mandatory"):
        build_token_envelope(
            [{"role": "user", "content": final_request}],
            descriptor=descriptor,
        )


def test_host_contract_budget_does_not_count_the_duplicate_raw_request() -> None:
    descriptor = ModelRuntimeDescriptor.for_testing(context_window_tokens=200 * 1024)
    final_request = "a" * 100_000

    projected, report = build_token_envelope(
        [{"role": "user", "content": final_request}],
        descriptor=descriptor,
        host_contract={
            "schema_version": "scansci.task-contract.v2",
            "version": 2,
            "contract_id": "duplicate-request-budget",
            "goal": "Answer the final user request.",
            "request": final_request,
            "allowed_tools": [],
        },
    )

    assert projected == [{"role": "user", "content": final_request}]
    assert report.host_contract_tokens < 2_000
    assert report.estimated_tokens <= descriptor.provider_input_tokens


def test_referenced_omission_uses_bounded_tokenizer_probes_for_high_entropy_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = descriptor_from_model_record(
        provider_id="openai",
        provider_kind="openai-compatible",
        model_id="gpt-4",
        model_record={"id": "gpt-4", "context_window": "200K", "capabilities": ["reasoning"]},
    )
    edge = _fixed_high_entropy_ascii("omission-edge", 1_000)
    message = {
        "role": "tool",
        "content": edge + ("A" * 30_000) + edge,
        "context_ref": "bounded-omission",
    }
    original_estimator = context_policy.estimate_message_tokens
    probes = 0

    def bounded_estimator(*args, **kwargs):
        nonlocal probes
        probes += 1
        if probes > 64:
            raise AssertionError("referenced omission used an unbounded linear tokenizer loop")
        return original_estimator(*args, **kwargs)

    monkeypatch.setattr(context_policy, "estimate_message_tokens", bounded_estimator)

    projected, digest = context_policy._truncate_referenced_message(
        message,
        budget_tokens=2_500,
        descriptor=descriptor,
    )

    assert projected is not None
    assert digest
    assert probes <= 64
    assert original_estimator(projected, descriptor=descriptor) <= 2_500


def test_follow_up_with_ten_or_fewer_large_messages_is_still_enveloped() -> None:
    messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn-{index} " + "x" * 20_000}
        for index in range(10)
    ]
    messages[-1] = {"role": "user", "content": "FINAL-FOLLOWUP-SENTINEL"}

    normalized = ResearchAgentRuntime._follow_up_messages(messages, max_recent=10, max_chars=12_000)
    descriptor = ModelRuntimeDescriptor.for_testing(context_window_tokens=32_768)
    projected, report = build_token_envelope(normalized, descriptor=descriptor)

    assert "FINAL-FOLLOWUP-SENTINEL" in projected[-1]["content"]
    assert report.estimated_tokens <= descriptor.provider_input_tokens
    assert report.omitted_messages > 0
