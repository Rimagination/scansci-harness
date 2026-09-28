from __future__ import annotations

import inspect

import pytest

from scansci_html import scansci_harness as harness_module
from scansci_html.research_agent import ResearchAgentRuntime
from scansci_html.scansci_harness import (
    HARNESS_SCHEMA_VERSION,
    HarnessContractError,
    ScanSciHarness,
    default_harness_descriptor,
)


def _task_contract() -> dict[str, object]:
    return {
        "schema_version": "scansci.task-contract.v2",
        "version": 2,
        "goal": "Answer the user's question",
        "allowed_tools": [],
        "initial_tools": [],
    }


class _FakePiClient:
    instances: list["_FakePiClient"] = []

    def __init__(self, **options: object) -> None:
        self.options = dict(options)
        self.calls: list[dict[str, object]] = []
        self.closed = False
        self.active_request_id = "request-test"
        self.instances.append(self)

    def stream_chat(self, **payload: object):
        self.calls.append(dict(payload))
        yield {"type": "delta", "content": "你好"}
        yield {"type": "done", "stats": {"tokens": {"output": 1}}}

    def close(self) -> None:
        self.closed = True


def test_harness_descriptor_declares_thin_pi_boundary_without_secrets() -> None:
    descriptor = default_harness_descriptor()

    assert descriptor["id"] == "scansci-harness"
    assert descriptor["schema_version"] == HARNESS_SCHEMA_VERSION
    assert descriptor["driver"] == "pi"
    assert descriptor["driver_protocol"] == 7
    assert descriptor["task_contract"] == "scansci.task-contract.v2"
    assert "branchable_history" in descriptor["capabilities"]
    assert "capability_profiles" in descriptor["capabilities"]
    assert "api_key" not in repr(descriptor).casefold()


def test_harness_has_no_second_model_driver_or_product_specific_plugin_branch() -> None:
    source = inspect.getsource(harness_module)

    assert "driver" not in inspect.signature(ScanSciHarness).parameters
    assert "_PiHarnessDriver" not in source
    assert "_MODEL_ADVISORY_TASK_PARTS" not in source
    assert 'task_contract.get("task_profile")' in source
    for product_name in ("scansci_pdf", "MiniCPM", "Qwen", "ollama", "lm-studio", "zotero-search"):
        assert product_name not in source


def test_harness_rejects_invalid_contract_before_starting_pi() -> None:
    _FakePiClient.instances.clear()
    harness = ScanSciHarness(
        pi_options={"workspace": "workspace.sqlite", "api_key": "must-not-leak"},
        pi_client_factory=_FakePiClient,
    )

    with pytest.raises(HarnessContractError, match="task contract"):
        harness.prepare_turn(
            messages=[{"role": "user", "content": "你好"}],
            task_mode="general",
            task_contract={"schema_version": "unknown", "version": 99},
        )

    assert _FakePiClient.instances == []
    assert "must-not-leak" not in repr(harness)


def test_pi_receives_canonical_turn_and_preserves_event_protocol() -> None:
    _FakePiClient.instances.clear()
    harness = ScanSciHarness(
        pi_options={"workspace": "workspace.sqlite"},
        pi_client_factory=_FakePiClient,
    )
    turn = harness.prepare_turn(
        request_id="request-1",
        conversation_id="conversation-1",
        session_id="session-1",
        messages=[{"role": "user", "content": "你好"}],
        task_mode="general",
        task_contract=_task_contract(),
        provider_kind="openai-compatible",
        base_url="https://example.invalid/v1",
        api_key="secret",
        model_id="cheap-model",
    )

    events = list(harness.stream(turn))

    assert events == [
        {"type": "delta", "content": "你好"},
        {"type": "done", "stats": {"tokens": {"output": 1}}},
    ]
    client = _FakePiClient.instances[0]
    assert client.calls[0]["messages"] == [{"role": "user", "content": "你好"}]
    assert client.calls[0]["task_mode"] == "general"
    assert client.calls[0]["task_contract"]["schema_version"] == "scansci.task-contract.v2"
    assert client.calls[0]["session_id"] == "session-1"
    assert "request_id" not in client.calls[0]

    harness.close()
    assert client.closed is True


def test_harness_warns_for_local_evidence_models_without_blocking_the_turn() -> None:
    _FakePiClient.instances.clear()
    harness = ScanSciHarness(
        pi_options={"workspace": "workspace.sqlite"},
        pi_client_factory=_FakePiClient,
    )
    turn = harness.prepare_turn(
        request_id="request-local-1",
        messages=[{"role": "user", "content": "检索并总结文献"}],
        task_mode="research",
        task_contract={**_task_contract(), "task_profile": {"evidence_policy": "strict"}},
        provider_kind="openai-compatible",
        provider_id="local-runtime-ollama-qwen",
        base_url="http://127.0.0.1:11434/v1",
        model_id="qwen3.8:4b",
    )

    events = list(harness.stream(turn))

    assert events[0]["type"] == "notice"
    assert events[0]["code"] == "local-model-evidence-boundary"
    assert events[0]["level"] == "warning"
    assert events[0]["blocking"] is False
    assert "小型或蒸馏模型" in events[0]["message"]
    assert [event["type"] for event in events[1:]] == ["delta", "done"]
    assert "model_advisories" not in _FakePiClient.instances[0].calls[0]

    harness.close()


def test_harness_does_not_warn_for_online_api_models_on_evidence_tasks() -> None:
    _FakePiClient.instances.clear()
    harness = ScanSciHarness(
        pi_options={"workspace": "workspace.sqlite"},
        pi_client_factory=_FakePiClient,
    )
    turn = harness.prepare_turn(
        request_id="request-online-1",
        messages=[{"role": "user", "content": "检索并总结文献"}],
        task_mode="research",
        task_contract={**_task_contract(), "task_profile": {"evidence_policy": "strict"}},
        provider_kind="openai-compatible",
        provider_id="openai",
        base_url="https://api.openai.com/v1",
        model_id="gpt-5.2",
    )

    events = list(harness.stream(turn))

    assert [event["type"] for event in events] == ["delta", "done"]
    harness.close()


def test_research_runtime_routes_top_level_pi_turns_through_scansci_harness() -> None:
    source = inspect.getsource(ResearchAgentRuntime._pi_model_events)

    assert "ScanSciHarness(" in source
    assert "PiAgentClient(" not in source
