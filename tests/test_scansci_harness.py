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


def test_turn_selected_cloud_model_overrides_stale_local_default(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = {
        "active_model": {"provider_id": "local-test", "model_id": "qwen-local"},
        "providers": [
            {
                "id": "local-test", "name": "Local", "kind": "local", "auth_mode": "local",
                "base_url": "http://127.0.0.1:11434/v1", "enabled": True,
                "models": [{"id": "qwen-local", "capabilities": ["reasoning"]}],
            },
            {
                "id": "l0veyou", "name": "l0veyou", "kind": "openai-compatible",
                "base_url": "https://l0veyou.com/v1", "enabled": True,
                "models": [{"id": "deepseek/deepseek-v4.1-flash", "capabilities": ["reasoning"]}],
            },
        ],
    }
    monkeypatch.setattr("scansci_html.research_agent.load_settings", lambda _workspace: settings)
    monkeypatch.setattr("scansci_html.research_agent.get_provider_api_key", lambda *_args: "test-key")
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    payload = {"messages": [{"role": "user", "content": "今天天气如何"}], "_skip_run_manifest": True}
    selected = runtime._direct_chat_request({
        **payload,
        "model": {"provider_id": "l0veyou", "model_id": "deepseek/deepseek-v4.1-flash"},
    })
    harness = ScanSciHarness()
    turn = harness.prepare_turn(
        messages=selected.messages,
        task_mode="research",
        task_contract={**_task_contract(), "task_profile": {"evidence_policy": "strict"}},
        provider_kind=selected.provider_kind,
        provider_id=selected.provider_id,
        base_url=selected.base_url,
        model_id=selected.model_id,
        model_runtime=selected.model_runtime,
    )

    assert (selected.provider_id, selected.model_id) == ("l0veyou", "deepseek/deepseek-v4.1-flash")
    assert turn.model_advisories == ()
    assert runtime._direct_chat_request(payload).provider_id == "local-test"
    observed_title_model = []

    def fake_title_completion(_kind, **kwargs):
        observed_title_model.append(kwargs["model"])
        return "今日天气"

    monkeypatch.setattr("scansci_html.research_agent.complete_chat_text", fake_title_completion)
    title = runtime.generate_conversation_title({
        "question": "今天天气如何",
        "model": {"provider_id": "l0veyou", "model_id": "deepseek/deepseek-v4.1-flash"},
    })
    assert title["source"] == "model"
    assert observed_title_model == ["deepseek/deepseek-v4.1-flash"]
    with pytest.raises(ValueError, match="重新选择"):
        runtime._direct_chat_request({**payload, "model": {"provider_id": "l0veyou", "model_id": "unknown"}})
    cloud_model = settings["providers"][1]["models"][0]
    cloud_model["show_in_composer"] = False
    assert runtime._direct_chat_request({
        **payload,
        "model": {"provider_id": "l0veyou", "model_id": cloud_model["id"]},
    }).provider_id == "l0veyou"
    for unavailable_fields in (
        {"available": False},
        {"readiness": "standby"},
        {"capabilities": ["embedding"]},
        {"capabilities": ["image"]},
    ):
        cloud_model.update(unavailable_fields)
        with pytest.raises(ValueError, match="重新选择"):
            runtime._direct_chat_request({
                **payload,
                "model": {"provider_id": "l0veyou", "model_id": cloud_model["id"]},
            })
        for field in unavailable_fields:
            cloud_model.pop(field)
        cloud_model["capabilities"] = ["reasoning"]
    settings["providers"][1]["enabled"] = False
    with pytest.raises(ValueError, match="已停用"):
        runtime._direct_chat_request({
            **payload,
            "model": {"provider_id": "l0veyou", "model_id": "deepseek/deepseek-v4.1-flash"},
        })


def test_research_runtime_routes_top_level_pi_turns_through_scansci_harness() -> None:
    source = inspect.getsource(ResearchAgentRuntime._pi_model_events)

    assert "ScanSciHarness(" in source
    assert "PiAgentClient(" not in source
