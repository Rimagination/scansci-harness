"""Host effect admission must survive repeated Pi tool calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import threading

from scansci_html.pi_agent import PiAgentClient, _PendingToolCall, _RunContext
from scansci_html.research_agent import ResearchAgentRuntime
from scansci_html.tool_effect_ledger import ToolEffectLedger


def _client(workspace: Path, *, effect_scope: str = "") -> PiAgentClient:
    return PiAgentClient(
        workspace=workspace,
        evidence_db=workspace.with_name("evidence.sqlite"),
        effect_scope=effect_scope,
    )


def _pending(call_id: str, *, hint: str = "sidecar-hint") -> _PendingToolCall:
    return _PendingToolCall(
        call_id=call_id,
        name="generate_image",
        arguments={"prompt": "a bird", "_scansci_idempotency_key": hint},
        parallel_safe=False,
        effectful=True,
    )


def test_effect_is_admitted_once_per_host_request_across_clients(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    executed: list[dict[str, object]] = []
    first_client = _client(workspace)
    second_client = _client(workspace)
    first_client._execute_tool = lambda _name, arguments: executed.append(arguments) or {"ok": True}
    second_client._execute_tool = first_client._execute_tool
    first_context = _RunContext(request_id="host-request-1", session_id="same-session", generation=1)

    first = first_client._execute_tool_worker(first_context, _pending("call-1"))
    duplicate = second_client._execute_tool_worker(first_context, _pending("call-2", hint="changed-hint"))
    next_turn = second_client._execute_tool_worker(
        _RunContext(request_id="host-request-2", session_id="same-session", generation=2),
        _pending("call-3"),
    )

    assert first.error_type == ""
    assert duplicate.error_type and "already" in duplicate.error.lower()
    assert next_turn.error_type == ""
    assert executed == [{"prompt": "a bird"}, {"prompt": "a bird"}]


def test_effect_scope_blocks_replay_after_new_pi_request_for_same_user_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    executed: list[str] = []
    first = _client(workspace, effect_scope="user-turn-1")
    recovered = _client(workspace, effect_scope="user-turn-1")
    next_turn = _client(workspace, effect_scope="user-turn-2")
    for client in (first, recovered, next_turn):
        client._execute_tool = lambda _name, _args: executed.append("effect") or {"ok": True}

    first_result = first._execute_tool_worker(
        _RunContext(request_id="pi-request-1", session_id="same-session", generation=1, effect_scope=first.effect_scope),
        _pending("first"),
    )
    recovered_result = recovered._execute_tool_worker(
        _RunContext(request_id="pi-request-2", session_id="same-session", generation=2, effect_scope=recovered.effect_scope),
        _pending("retry"),
    )
    next_result = next_turn._execute_tool_worker(
        _RunContext(request_id="pi-request-3", session_id="same-session", generation=3, effect_scope=next_turn.effect_scope),
        _pending("explicit-new-turn"),
    )

    assert first_result.error_type == ""
    assert "already" in recovered_result.error.lower()
    assert next_result.error_type == ""
    assert executed == ["effect", "effect"]


def test_recovered_pi_request_cannot_rephrase_an_existing_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    client = _client(workspace, effect_scope="one-user-turn")
    executed: list[dict[str, object]] = []
    client._execute_tool = lambda _name, arguments: executed.append(arguments) or {"ok": True}

    first = client._execute_tool_worker(
        _RunContext(request_id="first-pi-request", session_id="session", generation=1, effect_scope=client.effect_scope),
        _pending("first"),
    )
    rephrased = client._execute_tool_worker(
        _RunContext(request_id="recovered-pi-request", session_id="session", generation=2, effect_scope=client.effect_scope),
        replace(_pending("rephrased"), arguments={"prompt": "a blue bird"}),
    )

    assert first.error_type == ""
    assert "already" in rephrased.error.lower()
    assert executed == [{"prompt": "a bird"}]


def test_original_pi_request_can_submit_a_distinct_second_effect(tmp_path: Path) -> None:
    client = _client(tmp_path / "workspace.sqlite", effect_scope="one-user-turn")
    executed: list[dict[str, object]] = []
    client._execute_tool = lambda _name, arguments: executed.append(arguments) or {"ok": True}
    context = _RunContext(request_id="first-pi-request", session_id="session", generation=1, effect_scope=client.effect_scope)

    first = client._execute_tool_worker(context, _pending("first"))
    second = client._execute_tool_worker(
        context, replace(_pending("second"), arguments={"prompt": "a blue bird"}),
    )

    assert first.error_type == second.error_type == ""
    assert executed == [{"prompt": "a bird"}, {"prompt": "a blue bird"}]


def test_legacy_effect_claim_fails_closed_when_request_owner_is_unknown(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    with sqlite3.connect(agent_dir / "effect-claims.sqlite3") as connection:
        connection.execute(
            "create table effect_claims (effect_key text primary key, scope_id text not null, "
            "tool_name text not null, arguments_sha256 text not null, state text not null, updated_at integer not null)"
        )
        connection.execute(
            "insert into effect_claims values ('old', 'one-user-turn', 'generate_image', 'old-hash', 'completed', 1)"
        )

    _, admitted = ToolEffectLedger(agent_dir).claim(
        "one-user-turn", "new-pi-request", "generate_image", {"prompt": "a blue bird"},
    )

    assert not admitted


def test_concurrent_legacy_effect_ledger_upgrade_is_serialized(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    with sqlite3.connect(agent_dir / "effect-claims.sqlite3") as connection:
        connection.execute(
            "create table effect_claims (effect_key text primary key, scope_id text not null, "
            "tool_name text not null, arguments_sha256 text not null, state text not null, updated_at integer not null)"
        )
    start = threading.Barrier(2)

    def open_ledger() -> ToolEffectLedger:
        start.wait(timeout=5)
        return ToolEffectLedger(agent_dir)

    with ThreadPoolExecutor(max_workers=2) as pool:
        ledgers = list(pool.map(lambda _index: open_ledger(), range(2)))

    assert len(ledgers) == 2
    with sqlite3.connect(ledgers[0].path) as connection:
        columns = {row[1] for row in connection.execute("pragma table_info(effect_claims)")}
    assert "request_id" in columns


def test_research_runtime_preserves_effect_scope_through_repair(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    payload = {"messages": [{"role": "user", "content": "请生成一张图。"}], "_skip_run_manifest": True}
    request = runtime._direct_chat_request(payload)
    observed: list[str] = []

    def fake_stream(client, **_kwargs):
        observed.append(client.effect_scope)
        yield {"type": "done", "usage": {}}

    monkeypatch.setattr(PiAgentClient, "stream_chat", fake_stream)
    list(runtime._pi_model_events(request))
    list(runtime._pi_model_events(replace(request, messages=[*request.messages])))
    list(runtime._pi_model_events(runtime._direct_chat_request(payload)))

    assert len(observed) == 3
    assert observed[:2] == [request.effect_scope, request.effect_scope]
    assert observed[2] != request.effect_scope


def test_uncertain_effect_failure_cannot_be_executed_again(tmp_path: Path) -> None:
    client = _client(tmp_path / "workspace.sqlite")
    executed: list[str] = []

    def uncertain(_name, _arguments):
        executed.append("possibly wrote a file")
        raise TimeoutError("response lost after effect")

    client._execute_tool = uncertain
    context = _RunContext(request_id="uncertain-request", session_id="session", generation=1)

    first = client._execute_tool_worker(context, _pending("first"))
    second = client._execute_tool_worker(context, _pending("second"))

    assert first.error_type == "TimeoutError"
    assert second.error_type and "already" in second.error.lower()
    assert executed == ["possibly wrote a file"]


def test_effect_claim_database_failure_stops_dispatch(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path / "workspace.sqlite")
    executed: list[str] = []
    client._execute_tool = lambda _name, _args: executed.append("effect") or {"ok": True}
    monkeypatch.setattr(
        client._effect_ledger, "claim",
        lambda *_args: (_ for _ in ()).throw(sqlite3.OperationalError("database unavailable")),
    )

    result = client._execute_tool_worker(
        _RunContext(request_id="claim-failed", session_id="session", generation=1),
        _pending("first"),
    )

    assert result.error_type == "OperationalError"
    assert executed == []


def test_lost_completion_record_preserves_running_claim(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path / "workspace.sqlite")
    executed: list[str] = []
    client._execute_tool = lambda _name, _args: executed.append("effect") or {"ok": True}
    monkeypatch.setattr(
        client._effect_ledger, "complete",
        lambda *_args: (_ for _ in ()).throw(sqlite3.OperationalError("write failed")),
    )
    context = _RunContext(request_id="completion-failed", session_id="session", generation=1)

    first = client._execute_tool_worker(context, _pending("first"))
    duplicate = client._execute_tool_worker(context, _pending("second"))

    assert first.error_type == "OperationalError"
    assert "already" in duplicate.error.lower()
    assert executed == ["effect"]


def test_cancellation_during_effect_admission_stops_dispatch(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path / "workspace.sqlite")
    context = _RunContext(request_id="cancelled-request", session_id="session", generation=1)
    executed: list[str] = []
    client._execute_tool = lambda _name, _arguments: executed.append("effect") or {"ok": True}
    original_claim = client._effect_ledger.claim

    def cancel_after_claim(scope_id, request_id, tool_name, arguments):
        claimed = original_claim(scope_id, request_id, tool_name, arguments)
        context.cancel_event.set()
        return claimed

    monkeypatch.setattr(client._effect_ledger, "claim", cancel_after_claim)
    completion = client._execute_tool_worker(context, _pending("cancelled-call"))

    assert completion.error_type == "InterruptedError"
    assert executed == []


def test_concurrent_duplicate_cannot_start_a_second_effect(tmp_path: Path) -> None:
    client = _client(tmp_path / "workspace.sqlite")
    started = threading.Event()
    release = threading.Event()
    executed: list[str] = []

    def slow_effect(_name, _arguments):
        executed.append("started")
        started.set()
        assert release.wait(5)
        return {"ok": True}

    client._execute_tool = slow_effect
    context = _RunContext(request_id="concurrent-request", session_id="session", generation=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client._execute_tool_worker, context, _pending("first"))
        assert started.wait(5)
        duplicate = pool.submit(client._execute_tool_worker, context, _pending("second"))
        assert duplicate.result(timeout=5).error_type
        release.set()
        assert first.result(timeout=5).error_type == ""

    assert executed == ["started"]


def test_authorized_bridge_calls_share_the_host_effect_claim(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path / "workspace.sqlite", effect_scope="logical-user-turn")
    executed: list[dict[str, object]] = []
    observed_scopes: list[str] = []
    written: list[dict[str, object]] = []
    original_claim = client._effect_ledger.claim

    def observe_claim(scope_id, request_id, tool_name, arguments):
        observed_scopes.append(scope_id)
        return original_claim(scope_id, request_id, tool_name, arguments)

    monkeypatch.setattr(client._effect_ledger, "claim", observe_claim)
    monkeypatch.setattr(client, "_ensure_process", lambda **_kwargs: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(client, "_write", lambda message: written.append(dict(message)))
    monkeypatch.setattr(client, "_execute_tool", lambda _name, args: executed.append(dict(args)) or {"ok": True})
    for call_id in ("first-call", "second-call"):
        client._output.put(json.dumps({
            "type": "tool.call", "request_id": "effect-request", "call_id": call_id,
            "name": "generate_image",
            "arguments": {"prompt": "a bird", "_scansci_idempotency_key": f"untrusted-{call_id}"},
        }))
    client._output.put(json.dumps({"type": "run.completed", "request_id": "effect-request", "stats": {}}))

    events = list(client._run_request({
        "type": "run.start", "request_id": "effect-request", "session_id": "same-session",
        "task_mode": "general",
        "task_contract": {
            "schema_version": "scansci.task-contract.v2",
            "allowed_tools": ["generate_image"],
            "risk_level": "reversible",
            "max_tool_budget": 2,
        },
    }, api_key="fixture", timeout_seconds=5))

    assert executed == [{"prompt": "a bird"}]
    assert observed_scopes == ["logical-user-turn", "logical-user-turn"]
    assert len([message for message in written if message.get("type") == "tool.result" and message.get("ok")]) == 1
    assert len([message for message in written if message.get("type") == "tool.result" and message.get("ok") is False]) == 1
    assert len([event for event in events if event.get("type") == "tool.authorization" and event.get("decision") == "allow"]) == 2
