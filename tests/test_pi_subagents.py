from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import time

import pytest

from scansci_html.agent_capabilities import builtin_capability_descriptor
from scansci_html.agent_contract import compile_task_contract
from scansci_html.agent_reasoning import evidence_budget_for_thinking, normalize_thinking_level
import scansci_html.research_agent as research_agent_module
import scansci_html.pi_agent as pi_agent_module
from scansci_html.pi_agent import PiAgentClient
from scansci_html.research_agent import ResearchAgentRuntime, _DirectChatRequest
from scansci_html.research_runs import StageSpec
from scansci_html.research_subagents import validate_scientific_resource_uri


def _parent(runtime: ResearchAgentRuntime, *, allowed_tools: list[str] | None = None) -> dict:
    contract = compile_task_contract(
        task_mode="research",
        user_text="Compare the evidence with bounded scientific delegates",
        workflow_type="deep_research",
    )
    if allowed_tools is not None:
        contract["allowed_tools"] = list(allowed_tools)
        contract["initial_tools"] = list(allowed_tools)
        if "delegate_scientific_agents" in allowed_tools or "cancel_scientific_agents" in allowed_tools:
            contract["risk_level"] = "reversible"
        lease = dict(contract.get("capability_lease", {}) or {})
        lease["requested_tools"] = list(allowed_tools)
        lease["allowed_tools"] = list(allowed_tools)
        contract["capability_lease"] = lease
    return runtime.store.create_run(
        notebook_id="library",
        workflow_type="deep_research",
        title="Parent research",
        input_payload={"question": "Compare the evidence"},
        stages=[StageSpec("answer", "Answer", "model")],
        task_contract=contract,
        metadata={"thinking_level": "max"},
    )


class _OpenAIScientificAgentsHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        tool_messages = [message for message in payload.get("messages", []) if message.get("role") == "tool"]
        if not tool_messages:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-delegate",
                    "type": "function",
                    "function": {
                        "name": "delegate_scientific_agents",
                        "arguments": json.dumps({
                            "roles": ["literature_scout", "fulltext_analyst"],
                            "idempotency_key": "model-e2e",
                        }),
                    },
                }],
            }
            finish = "tool_calls"
        elif len(tool_messages) == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-collect",
                    "type": "function",
                    "function": {"name": "collect_scientific_agents", "arguments": "{}"},
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "Scientific handoffs collected"}
            finish = "stop"
        chunks = [
            {
                "id": "chatcmpl-scientific",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-scientific",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAINativeScientificAgentsHandler(BaseHTTPRequestHandler):
    """Fixture that distinguishes the parent Pi session from native children."""

    request_payloads: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        messages = list(payload.get("messages", []) or [])
        joined = json.dumps(messages, ensure_ascii=False)
        is_child = "NATIVE_PI_CHILD_ROLE=" in joined
        has_native_result = any(
            message.get("role") == "tool"
            and "pi-native" in str(message.get("content", ""))
            for message in messages
        )
        if is_child:
            role = "unknown"
            for marker in ("literature_scout", "fulltext_analyst", "evidence_auditor", "synthesis_writer"):
                if marker in joined:
                    role = marker
                    break
            delta = {"role": "assistant", "content": f"NATIVE_CHILD_RESULT:{role}"}
            finish = "stop"
        elif not has_native_result:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-native-delegate",
                    "type": "function",
                    "function": {
                        "name": "delegate_scientific_agents",
                        "arguments": json.dumps({
                            "roles": ["literature_scout", "fulltext_analyst"],
                            "instruction": "Use the Pi child sessions and return their findings.",
                            "idempotency_key": "native-e2e",
                        }),
                    },
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "PARENT_USED_NATIVE_CHILD_RESULTS"}
            finish = "stop"
        chunks = [
            {
                "id": "chatcmpl-native-scientific",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-native-scientific",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIGenericSubagentHandler(BaseHTTPRequestHandler):
    """Fixture for the generic Pi-style subagent API."""

    request_payloads: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        messages = list(payload.get("messages", []) or [])
        joined = json.dumps(messages, ensure_ascii=False)
        is_child = "NATIVE_PI_CHILD_PROFILE=" in joined
        has_native_result = any(
            message.get("role") == "tool"
            and "pi-native" in str(message.get("content", ""))
            for message in messages
        )
        if is_child:
            profile = "default"
            for marker in ("explore", "review", "research"):
                if f"NATIVE_PI_CHILD_PROFILE={marker}" in joined:
                    profile = marker
                    break
            delta = {"role": "assistant", "content": f"GENERIC_CHILD_RESULT:{profile}"}
            finish = "stop"
        elif not has_native_result:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-generic-subagent",
                    "type": "function",
                    "function": {
                        "name": "subagent",
                        "arguments": json.dumps({
                            "calls": [
                                {"agent": "explore", "prompt": "GENERIC_TASK_ONE: inspect the evidence surface."},
                                {"agent": "review", "prompt": "GENERIC_TASK_TWO: challenge the proposed conclusion."},
                            ],
                            "idempotency_key": "generic-e2e",
                        }),
                    },
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "GENERIC_PARENT_USED_CHILD_RESULTS"}
            finish = "stop"
        chunks = [
            {
                "id": "chatcmpl-generic-subagent",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-generic-subagent",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_pi_scientific_control_tools_have_host_owned_effect_metadata() -> None:
    expected = {
        "delegate_scientific_agents": ("reversible", False),
        "list_scientific_agents": ("read_only", True),
        "collect_scientific_agents": ("read_only", True),
        "cancel_scientific_agents": ("reversible", False),
    }
    for name, (risk, idempotent) in expected.items():
        descriptor = builtin_capability_descriptor(name)
        assert descriptor is not None
        assert descriptor["risk_level"] == risk
        assert descriptor["idempotent"] is idempotent
        assert descriptor["subagent_allowed"] is False


def test_scientific_read_controls_are_actually_parallel_safe_in_python_host() -> None:
    assert {"list_scientific_agents", "collect_scientific_agents"} <= set(
        pi_agent_module._PARALLEL_SAFE_TOOL_NAMES
    )
    assert {
        "delegate_scientific_agents",
        "cancel_scientific_agents",
    }.isdisjoint(pi_agent_module._PARALLEL_SAFE_TOOL_NAMES)


def test_research_contract_exposes_all_generic_subagent_controls_to_parent_pi() -> None:
    contract = compile_task_contract(
        task_mode="research",
        user_text="Coordinate independent scientific agents for this review.",
        workflow_type="deep_research",
    )

    assert {
        "subagent",
        "list_subagents",
        "collect_subagents",
        "cancel_subagents",
    } <= set(contract["allowed_tools"])


def test_research_contract_exposes_generic_subagent_controls_not_domain_roles() -> None:
    contract = compile_task_contract(
        task_mode="research",
        user_text="Coordinate independent child sessions for this review.",
        workflow_type="deep_research",
    )

    allowed = set(contract["allowed_tools"])
    assert {"subagent", "list_subagents", "collect_subagents", "cancel_subagents"} <= allowed
    assert not ({
        "delegate_scientific_agents",
        "list_scientific_agents",
        "collect_scientific_agents",
        "cancel_scientific_agents",
    } & allowed)


def test_atomic_delegation_reserves_at_most_three_under_ten_concurrent_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)
    submitted: list[str] = []
    submitted_lock = threading.Lock()

    def submit(run_id: str) -> None:
        with submitted_lock:
            submitted.append(run_id)

    monkeypatch.setattr(runtime, "_submit", submit)

    def delegate(index: int) -> dict:
        return runtime.delegate_scientific_agents(
            parent["run_id"],
            {
                "roles": ["literature_scout", "fulltext_analyst", "evidence_auditor"],
                "idempotency_key": f"batch-{index}",
            },
        )

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(delegate, range(10)))

    children = runtime.list_scientific_agents(parent["run_id"])["children"]
    assert len(children) == 3
    assert len(set(submitted)) == 3
    assert sum(int(result["accepted"]) for result in results) == 3


def test_delegation_idempotency_key_replays_same_reserved_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)
    submitted: list[str] = []
    monkeypatch.setattr(runtime, "_submit", submitted.append)
    payload = {"roles": ["literature_scout", "fulltext_analyst"], "idempotency_key": "same-call"}

    first = runtime.delegate_scientific_agents(parent["run_id"], payload)
    second = runtime.delegate_scientific_agents(parent["run_id"], payload)

    assert [item["run_id"] for item in second["children"]] == [item["run_id"] for item in first["children"]]
    assert second["replayed"] is True
    assert submitted == [item["run_id"] for item in first["children"]]


def test_deleting_scientific_child_or_parent_cleans_membership_foreign_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)

    first_parent = _parent(runtime)
    first_child = runtime.delegate_scientific_agents(
        first_parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "delete-child"},
    )["children"][0]
    runtime.store.mark_cancelled(first_child["run_id"])
    assert runtime.store.delete_run(first_child["run_id"])["deleted"] is True
    assert runtime.store.list_scientific_children(first_parent["run_id"]) == []

    second_parent = _parent(runtime)
    second_child = runtime.delegate_scientific_agents(
        second_parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "delete-parent"},
    )["children"][0]
    runtime.store.mark_cancelled(second_parent["run_id"])
    assert runtime.store.delete_run(second_parent["run_id"])["deleted"] is True
    assert runtime.store.get_run(second_child["run_id"])["parent_run_id"] == second_parent["run_id"]


def test_one_submit_failure_marks_only_that_child_and_submits_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)
    attempted: list[str] = []

    def submit(run_id: str) -> None:
        attempted.append(run_id)
        if len(attempted) == 1:
            raise RuntimeError("worker unavailable")

    monkeypatch.setattr(runtime, "_submit", submit)
    result = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"idempotency_key": "submit-isolation"},
    )

    statuses = [runtime.store.get_run(child["run_id"])["status"] for child in result["children"]]
    assert len(attempted) == 3
    assert statuses == ["failed", "queued", "queued"]


def test_scientific_membership_controls_survive_runtime_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace.sqlite"
    evidence_db = tmp_path / "evidence.sqlite"
    first = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db)
    parent = _parent(first)
    monkeypatch.setattr(first, "_submit", lambda _run_id: None)
    children = first.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout", "fulltext_analyst"], "idempotency_key": "restart"},
    )["children"]

    restarted = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db)
    listed = restarted.list_scientific_agents(parent["run_id"])
    assert [item["run_id"] for item in listed["children"]] == [item["run_id"] for item in children]
    collected = restarted.collect_scientific_agents(parent["run_id"])
    assert collected["counts"]["running"] == 2
    cancelled = restarted.cancel_scientific_agents(parent["run_id"], [children[0]["run_id"]])
    assert cancelled["children"][0]["status"] == "cancelled"


def test_restart_replay_resumes_only_app_interrupted_reserved_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace.sqlite"
    evidence_db = tmp_path / "evidence.sqlite"
    first = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db)
    parent = _parent(first)
    monkeypatch.setattr(first, "_submit", lambda _run_id: None)
    payload = {"roles": ["literature_scout"], "idempotency_key": "crash-before-submit"}
    child = first.delegate_scientific_agents(parent["run_id"], payload)["children"][0]
    assert child["status"] == "queued"

    restarted = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db)
    interrupted = restarted.store.get_run(child["run_id"])
    assert interrupted["status"] == "paused"
    assert interrupted["error"]["code"] == "app_restarted"
    submitted: list[str] = []
    monkeypatch.setattr(restarted, "_submit", submitted.append)

    replay = restarted.delegate_scientific_agents(parent["run_id"], payload)

    assert replay["replayed"] is True
    assert submitted == [child["run_id"]]
    assert restarted.store.get_run(child["run_id"])["status"] == "queued"


def test_child_contract_is_actual_read_only_subset_with_independent_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(
        runtime,
        allowed_tools=["discover_papers", "create_document", "delegate_scientific_agents"],
    )
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)

    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "subset"},
    )["children"][0]
    contract = child["task_contract"]

    assert contract["allowed_tools"] == ["discover_papers"]
    assert set(contract["initial_tools"]) <= set(contract["allowed_tools"])
    assert contract["allowed_mcp_servers"] == []
    assert contract["capability_lease"]["requested_mcp_servers"] == []
    assert contract["capability_lease"]["allowed_mcp_servers"] == []
    assert contract["allow_external_write"] is False
    assert contract["subagent"]["parent_run_id"] == parent["run_id"]
    assert contract["subagent"]["recursive_delegation"] is False
    assert contract["max_tool_budget"] < parent["task_contract"]["max_tool_budget"]
    assert contract["max_model_token_budget"] < parent["task_contract"]["max_model_token_budget"]


def test_persisted_child_contract_is_used_for_actual_child_pi_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    runtime.evidence_db.touch()
    parent = _parent(runtime, allowed_tools=["discover_papers"])
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "actual-start"},
    )["children"][0]

    resolved = runtime._contract_for_active_run(
        child["run_id"],
        fallback={"allowed_tools": ["create_document"], "allowed_mcp_servers": ["unsafe"]},
    )

    assert resolved == child["task_contract"]
    assert resolved["allowed_tools"] == ["discover_papers"]
    assert resolved["allowed_mcp_servers"] == []


def test_partial_collection_counts_statuses_and_keeps_valid_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    children = runtime.delegate_scientific_agents(
        parent["run_id"],
        {
            "roles": ["literature_scout", "fulltext_analyst", "evidence_auditor"],
            "idempotency_key": "partial",
        },
    )["children"]
    valid = children[0]
    invalid = children[1]
    failed = children[2]
    evidence_link = {
        "doc_id": "doc-1",
        "evidence_id": "ev-1",
        "html_anchor": "p-1",
        "exact_quote": "bounded evidence",
    }
    artifact = runtime.store.create_artifact(
        valid["run_id"],
        artifact_type="evidence_answer",
        title="valid",
        summary="valid",
        payload={
            "subagent_handoff": {
                "role": "literature_scout",
                "findings": ["bounded finding"],
                "evidence_uris": ["scansci://evidence/doc-1/ev-1"],
                "uncertainties": [],
                "recommended_next_action": "continue",
            }
        },
        evidence_links=[evidence_link],
    )
    runtime.store.complete_run(valid["run_id"], output_artifact_id=artifact["artifact_id"])
    invalid_artifact = runtime.store.create_artifact(
        invalid["run_id"],
        artifact_type="evidence_answer",
        title="invalid",
        summary="invalid",
        payload={"subagent_handoff": {"role": "wrong"}},
    )
    runtime.store.complete_run(invalid["run_id"], output_artifact_id=invalid_artifact["artifact_id"])
    runtime.store.begin_run(failed["run_id"])
    runtime.store.fail_stage(failed["run_id"], failed["current_stage"], RuntimeError("one child failed"))

    collected = runtime.collect_scientific_agents(parent["run_id"])

    assert collected["counts"] == {
        "valid": 1,
        "running": 0,
        "failed": 1,
        "cancelled": 0,
        "invalid": 1,
        "total": 3,
    }
    assert collected["aggregated_findings"][0]["handoff"]["findings"] == ["bounded finding"]
    assert collected["complete"] is False


def test_collect_scientific_agents_can_wait_bounded_for_async_sibling_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    children = runtime.delegate_scientific_agents(
        parent["run_id"],
        {
            "roles": ["literature_scout", "fulltext_analyst"],
            "idempotency_key": "bounded-wait",
        },
    )["children"]

    def finish_children() -> None:
        time.sleep(0.08)
        artifact = runtime.store.create_artifact(
            children[0]["run_id"],
            artifact_type="scientific_handoff",
            title="valid",
            summary="valid",
            payload={
                "subagent_handoff": {
                    "role": "literature_scout",
                    "findings": [{"claim": "Async valid result"}],
                    "evidence_uris": [],
                    "uncertainties": [],
                    "recommended_next_action": "Continue.",
                }
            },
        )
        runtime.store.complete_run(children[0]["run_id"], output_artifact_id=artifact["artifact_id"])
        runtime.store.begin_run(children[1]["run_id"])
        runtime.store.fail_stage(
            children[1]["run_id"],
            children[1]["current_stage"],
            RuntimeError("isolated async failure"),
        )

    worker = threading.Thread(target=finish_children, daemon=True)
    worker.start()
    collected = runtime.collect_scientific_agents(
        parent["run_id"],
        wait_seconds=1.0,
        poll_interval_ms=20,
    )
    worker.join(timeout=1)

    assert collected["counts"]["valid"] == 1
    assert collected["counts"]["failed"] == 1
    assert collected["counts"]["running"] == 0
    assert collected["wait"]["timed_out"] is False
    assert collected["wait"]["waited_seconds"] >= 0.05


def test_collect_scientific_agents_wait_timeout_returns_partial_running_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "wait-timeout"},
    )

    started = time.monotonic()
    collected = runtime.collect_scientific_agents(
        parent["run_id"],
        wait_seconds=0.12,
        poll_interval_ms=20,
    )
    elapsed = time.monotonic() - started

    assert 0.08 <= elapsed < 0.8
    assert collected["counts"]["running"] == 1
    assert collected["wait"]["timed_out"] is True


def test_collect_scientific_agents_wait_returns_immediately_without_children(tmp_path: Path) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)

    started = time.monotonic()
    collected = runtime.collect_scientific_agents(parent["run_id"], wait_seconds=1.0)

    assert time.monotonic() - started < 0.3
    assert collected["counts"]["total"] == 0
    assert collected["wait"]["timed_out"] is False


def test_child_delivery_persists_host_issued_evidence_membership_for_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    with sqlite3.connect(runtime.evidence_db) as connection:
        connection.execute("create table evidence_spans(evidence_id text, doc_id text)")
        connection.execute(
            "insert into evidence_spans(evidence_id, doc_id) values (?, ?)",
            ("span-1", "doc-1"),
        )
    parent = _parent(runtime, allowed_tools=["discover_papers"])
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "deliver-membership"},
    )["children"][0]
    evidence_uri = "scansci://evidence/doc-1/span-1"
    runtime.store.begin_run(child["run_id"])
    runtime.store.start_stage(child["run_id"], "research")
    runtime.store.complete_stage(
        child["run_id"],
        "research",
        output={
            "subagent_handoff": {
                "role": "literature_scout",
                "findings": [{"claim": "Bounded evidence"}],
                "evidence_uris": [evidence_uri],
                "uncertainties": [],
                "recommended_next_action": "Inspect the source.",
            },
            "_scientific_allowed_uris": [evidence_uri],
        },
    )

    artifact = runtime._deliver(runtime.store.get_run(child["run_id"]))
    runtime.store.complete_run(child["run_id"], output_artifact_id=artifact["artifact_id"])
    collected = runtime.collect_scientific_agents(parent["run_id"])

    assert artifact["evidence_links"][0]["uri"] == evidence_uri
    assert collected["counts"]["valid"] == 1
    assert collected["aggregated_findings"][0]["handoff"]["evidence_uris"] == [evidence_uri]


def test_child_delivery_rejects_laundered_uri_without_owned_evidence_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    runtime.evidence_db.touch()
    parent = _parent(runtime, allowed_tools=["search_web"])
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "uri-laundering"},
    )["children"][0]
    injected = "scansci://evidence/victim/injected"
    runtime.store.begin_run(child["run_id"])
    runtime.store.start_stage(child["run_id"], "research")
    runtime.store.complete_stage(
        child["run_id"],
        "research",
        output={
            "subagent_handoff": {
                "role": "literature_scout",
                "findings": [{"claim": "Untrusted public snippet"}],
                "evidence_uris": [injected],
                "uncertainties": [],
                "recommended_next_action": "Reject it.",
            },
            "_scientific_allowed_uris": [injected],
        },
    )

    with pytest.raises(RuntimeError, match="invalid_evidence_uri"):
        runtime._deliver(runtime.store.get_run(child["run_id"]))
    assert runtime.store.get_run(child["run_id"])["artifacts"] == []


def test_cancel_scientific_agents_rejects_cross_parent_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    first_parent = _parent(runtime)
    second_parent = _parent(runtime)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        first_parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "owned"},
    )["children"][0]

    with pytest.raises(PermissionError, match="owned"):
        runtime.cancel_scientific_agents(second_parent["run_id"], [child["run_id"]])
    assert runtime.store.get_run(child["run_id"])["status"] == "queued"


def test_cancel_scientific_agents_distinguishes_omitted_from_empty_and_counts_transitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    children = runtime.delegate_scientific_agents(
        parent["run_id"],
        {
            "roles": ["literature_scout", "fulltext_analyst"],
            "idempotency_key": "cancel-semantics",
        },
    )["children"]

    empty = runtime.cancel_scientific_agents(parent["run_id"], [])
    assert empty["selected"] == 0
    assert empty["cancelled"] == 0
    assert all(runtime.store.get_run(child["run_id"])["status"] == "queued" for child in children)

    runtime.cancel(children[0]["run_id"])
    selected = runtime.cancel_scientific_agents(
        parent["run_id"],
        [children[0]["run_id"]],
    )
    assert selected["selected"] == 1
    assert selected["cancelled"] == 0
    assert selected["children"][0]["status"] == "cancelled"

    all_children = runtime.cancel_scientific_agents(parent["run_id"])
    assert all_children["selected"] == 2
    assert all_children["cancelled"] == 1
    assert runtime.store.get_run(children[1]["run_id"])["status"] == "cancelled"


@pytest.mark.parametrize(
    "value",
    [
        "scansci://run/child?fake=1",
        "scansci://run/child#fake",
        "scansci://user@run/child",
        "scansci://run:443/child",
        "scansci://run/../child",
        "scansci://run/%252e%252e/child",
        "scansci://run/%2fcontrol",
        "scansci://run/child%5ccontrol",
    ],
)
def test_scansci_membership_uri_rejects_ambiguous_or_control_forms(value: str) -> None:
    assert validate_scientific_resource_uri(value, allowed_uris={"scansci://run/child"}) is None


def test_scansci_membership_uri_requires_host_issued_exact_membership() -> None:
    allowed = {"scansci://run/parent", "scansci://run/child", "scansci://artifact/child/artifact-1"}
    assert validate_scientific_resource_uri("scansci://run/child", allowed_uris=allowed) == "scansci://run/child"
    assert validate_scientific_resource_uri("scansci://run/other", allowed_uris=allowed) is None


def test_reasoning_supports_max_and_clamps_for_provider_without_max() -> None:
    assert normalize_thinking_level("MAX") == "max"
    assert evidence_budget_for_thinking("max") > evidence_budget_for_thinking("high")


def test_desktop_thinking_selector_preserves_xhigh_and_max_for_pi_payloads() -> None:
    app_js = (
        Path(__file__).parents[1] / "src" / "scansci_html" / "web" / "app.js"
    ).read_text(encoding="utf-8")

    assert '["auto", "low", "medium", "high", "xhigh", "max"]' in app_js
    assert '{ value: "xhigh"' in app_js
    assert '{ value: "max"' in app_js
    assert "thinking_level: currentThinkingLevel()" in app_js


def test_pi_runtime_delegates_sparse_thinking_level_clamp_to_sdk() -> None:
    runtime_source = (
        Path(__file__).parents[1] / "pi-runtime" / "src" / "main.ts"
    ).read_text(encoding="utf-8")

    assert "session.setThinkingLevel(requested)" in runtime_source
    assert "const applied = session.thinkingLevel" in runtime_source
    assert "sort((left, right) => order.indexOf(right)" not in runtime_source


def test_pi_runtime_allows_loopback_sse_to_finish_without_terminal_reason() -> None:
    runtime_source = (
        Path(__file__).parents[1] / "pi-runtime" / "src" / "main.ts"
    ).read_text(encoding="utf-8")

    assert '["127.0.0.1", "localhost", "::1"].includes(baseHostname)' in runtime_source
    assert "supportsFinishReason: false" in runtime_source


def test_pause_api_documents_abort_and_resume_not_suspend_semantics() -> None:
    documentation = str(PiAgentClient.pause.__doc__ or "").lower()
    assert "abort-and-resume" in documentation
    assert "not a suspended" in documentation


def test_fresh_session_load_negotiates_protocol_before_any_session_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, _sidecar = PiAgentClient.runtime_paths()
    old_sidecar = tmp_path / "old-session-sidecar.mjs"
    old_sidecar.write_text(
        """
import * as readline from "node:readline";
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  const message = JSON.parse(line);
  if (message.type === "ping") {
    process.stdout.write(JSON.stringify({
      type: "pong", protocol: 5, capabilities: ["acked_session_commands"]
    }) + "\\n");
  } else if (message.type === "session.load") {
    process.stdout.write(JSON.stringify({
      type: "session.loaded",
      command_id: message.command_id,
      generation: message.generation,
      session_id: message.session_id
    }) + "\\n");
  }
});
""".strip(),
        encoding="utf-8",
    )
    session_file = tmp_path / "durable.jsonl"
    session_file.write_text("{}\n", encoding="utf-8")
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    monkeypatch.setattr(client, "runtime_paths", lambda: (node, old_sidecar))
    monkeypatch.setattr(client, "_load_session_registry", lambda: {"durable": str(session_file)})

    try:
        with pytest.raises(pi_agent_module.PiRuntimeUnavailable, match="protocol"):
            client.load_session(
                "durable",
                provider_kind="openai-compatible",
                base_url="http://127.0.0.1:1/v1",
                api_key="fixture",
                model_id="fixture-model",
                timeout_seconds=0.5,
            )
    finally:
        client.close()


def test_new_sidecar_rejects_unversioned_raw_session_load_before_mutation(tmp_path: Path) -> None:
    node, sidecar = PiAgentClient.runtime_paths()
    session_file = tmp_path / "must-not-load.jsonl"
    session_file.write_text("{}\n", encoding="utf-8")
    process = subprocess.Popen(
        [str(node), str(sidecar)],
        env=PiAgentClient._node_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps({
            "type": "session.load",
            "command_id": "legacy-load",
            "generation": 1,
            "session_id": "legacy",
            "session_file": str(session_file),
        }) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
    finally:
        process.kill()
        process.wait(timeout=5)

    assert response["type"] == "protocol.error"
    assert response["failure"]["code"] == "protocol_incompatible"
    assert response["command_id"] == "legacy-load"


def test_pi_scientific_tool_uses_host_active_parent_and_strips_model_parent(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict]] = []
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
        active_run_id="host-parent",
        scientific_agent_control=lambda name, parent_id, arguments: calls.append(
            (name, parent_id, dict(arguments))
        ) or {"ok": True},
    )

    result = client._execute_tool(
        "delegate_scientific_agents",
        {"parent_run_id": "model-fake", "run_id": "model-fake", "roles": ["literature_scout"]},
    )

    assert result == {"ok": True}
    assert calls == [
        ("delegate_scientific_agents", "host-parent", {"roles": ["literature_scout"]})
    ]


def test_actual_child_pi_stream_receives_persisted_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime, allowed_tools=["discover_papers"])
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "wire-contract"},
    )["children"][0]
    captured: dict = {}

    class FakePiAgentClient:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def stream_chat(self, **kwargs: object):
            captured["stream"] = kwargs
            yield {"type": "done", "text": "ok"}

        def close(self) -> None:
            return None

    monkeypatch.setattr(research_agent_module, "PiAgentClient", FakePiAgentClient)
    monkeypatch.setattr(
        runtime,
        "_compile_contract",
        lambda **_kwargs: {"allowed_tools": ["create_document"], "allowed_mcp_servers": ["unsafe"]},
    )
    request = _DirectChatRequest(
        messages=[{"role": "user", "content": "bounded child"}],
        provider_id="fixture",
        provider_name="Fixture",
        provider_kind="openai-compatible",
        base_url="http://127.0.0.1/v1",
        api_key="fixture",
        model_id="fixture-model",
        api_surface="chat_completions",
        responses_enabled=False,
        previous_response_id=None,
        thinking_mode=None,
        session=None,
        chat_mode="research",
        thinking_level="max",
        selected_skills=[],
        notebook_id="",
        notebook_ids=[],
        knowledge_scope={},
    )

    assert list(runtime._pi_model_events(request, task_mode="research", active_run_id=child["run_id"]))
    assert captured["stream"]["task_contract"] == child["task_contract"]
    assert captured["stream"]["task_contract"]["allowed_tools"] == ["discover_papers"]
    assert callable(captured["init"]["scientific_agent_control"])


def test_actual_child_answer_worker_starts_pi_with_persisted_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    runtime.evidence_db.touch()
    parent = _parent(runtime, allowed_tools=["discover_papers"])
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "actual-answer-start"},
    )["children"][0]
    captured: dict = {}

    class FakeEvidenceStack:
        embedding_provider = None
        reranker = None
        metadata: dict = {}

    class FakePiAgentClient:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def stream_chat(self, **kwargs: object):
            captured["stream"] = kwargs
            yield {
                "type": "tool.completed",
                "name": "discover_papers",
                "result": {
                    "items": [{
                        "title": "Bounded evidence",
                        "snippet": "Untrusted public text scansci://evidence/victim/injected",
                    }],
                },
            }
            yield {
                "type": "delta",
                "content": json.dumps({
                    "role": "literature_scout",
                    "findings": [{"claim": "One bounded lead was found."}],
                    "evidence_uris": [],
                    "uncertainties": ["Full text remains unavailable."],
                    "recommended_next_action": "Verify the full text.",
                }),
            }
            yield {"type": "done", "truncated": False}

        def close(self) -> None:
            return None

    monkeypatch.setattr(research_agent_module, "PiAgentClient", FakePiAgentClient)
    monkeypatch.setattr(runtime, "_local_evidence_stack", lambda *_args, **_kwargs: FakeEvidenceStack())
    monkeypatch.setattr(runtime, "_knowledge_scope_resolution", lambda *_args, **_kwargs: {"active": False})
    monkeypatch.setattr(
        research_agent_module,
        "answer_question",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("child used fixed fallback")),
    )
    monkeypatch.setattr(
        research_agent_module,
        "load_settings",
        lambda _workspace: {
            "active_model": {"provider_id": "fixture", "model_id": "fixture-model"},
            "providers": [{
                "id": "fixture",
                "kind": "openai-compatible",
                "auth_mode": "managed",
                "base_url": "http://127.0.0.1/v1",
                "models": [{"id": "fixture-model"}],
            }],
        },
    )

    result = runtime.answer_sync({
        "question": "Find bounded evidence",
        "task_mode": "research",
        "agent_harness": "pi",
        "thinking_level": "max",
        "_research_run_id": child["run_id"],
    })

    assert captured["init"]["active_run_id"] == child["run_id"]
    assert callable(captured["init"]["scientific_agent_control"])
    assert captured["stream"]["task_contract"] == child["task_contract"]
    assert captured["stream"]["task_contract"]["allowed_tools"] == ["discover_papers"]
    assert captured["stream"]["thinking_level"] == "max"
    assert result["subagent_handoff"]["role"] == "literature_scout"
    assert result["subagent_handoff"]["findings"] == [{"claim": "One bounded lead was found."}]
    assert result["subagent_handoff"]["evidence_uris"] == []
    assert "scansci://evidence/victim/injected" not in result["_scientific_allowed_uris"]


@pytest.mark.parametrize(
    ("provider_id", "provider_kind", "auth_mode", "force_local_evidence", "expected_pi_kind"),
    [
        ("fixture", "local", "local", False, "local"),
        ("fixture", "openai-compatible", "managed", True, "openai-compatible"),
        ("local-huggingface", "local", "local", False, "openai-compatible"),
    ],
)
def test_scientific_child_always_uses_pi_and_only_authorizes_owned_evidence_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    provider_kind: str,
    auth_mode: str,
    force_local_evidence: bool,
    expected_pi_kind: str,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    with sqlite3.connect(runtime.evidence_db) as connection:
        connection.execute("create table evidence_spans(evidence_id text, doc_id text)")
        connection.execute(
            "insert into evidence_spans(evidence_id, doc_id) values (?, ?)",
            ("span-1", "doc-1"),
        )
    parent = _parent(runtime, allowed_tools=["search_local_evidence"])
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["fulltext_analyst"], "idempotency_key": f"owned-{provider_id}-{force_local_evidence}"},
    )["children"][0]
    captured: dict = {}

    class FakeEvidenceStack:
        embedding_provider = None
        reranker = None
        metadata: dict = {}

    class FakePiAgentClient:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def stream_chat(self, **kwargs: object):
            captured["stream"] = kwargs
            yield {
                "type": "tool.completed",
                "name": "search_local_evidence",
                "result": {
                    "hits": [{
                        "doc_id": "doc-1",
                        "evidence_id": "span-1",
                        "library_index": 0,
                        "text": "Owned local evidence.",
                    }],
                },
            }
            yield {
                "type": "delta",
                "content": json.dumps({
                    "role": "fulltext_analyst",
                    "findings": [{"claim": "Owned evidence was inspected."}],
                    "evidence_uris": ["scansci://evidence/doc-1/span-1"],
                    "uncertainties": [],
                    "recommended_next_action": "Synthesize the verified span.",
                }),
            }
            yield {"type": "done", "truncated": False}

        def close(self) -> None:
            return None

    monkeypatch.setattr(research_agent_module, "PiAgentClient", FakePiAgentClient)
    monkeypatch.setattr(runtime, "_local_evidence_stack", lambda *_args, **_kwargs: FakeEvidenceStack())
    monkeypatch.setattr(runtime, "_knowledge_scope_resolution", lambda *_args, **_kwargs: {"active": False})
    local_routes: list[str] = []
    monkeypatch.setattr(
        research_agent_module,
        "ensure_local_transformers_runtime",
        lambda model_id: local_routes.append(model_id) or "http://127.0.0.1:51999/v1",
    )
    monkeypatch.setattr(
        research_agent_module,
        "answer_question",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("child used fixed fallback")),
    )
    monkeypatch.setattr(
        research_agent_module,
        "load_settings",
        lambda _workspace: {
            "active_model": {"provider_id": provider_id, "model_id": "fixture-model"},
            "providers": [{
                "id": provider_id,
                "kind": provider_kind,
                "auth_mode": auth_mode,
                "base_url": "http://127.0.0.1/v1",
                "models": [{"id": "fixture-model"}],
            }],
        },
    )

    result = runtime.answer_sync({
        "question": "Inspect owned evidence",
        "task_mode": "research",
        "_research_run_id": child["run_id"],
        "force_local_evidence": force_local_evidence,
    })

    assert captured["init"]["active_run_id"] == child["run_id"]
    assert captured["stream"]["provider_kind"] == expected_pi_kind
    if provider_id == "local-huggingface":
        assert captured["stream"]["base_url"] == "http://127.0.0.1:51999/v1"
        assert local_routes == ["fixture-model"]
    else:
        assert local_routes == []
    assert captured["stream"]["task_contract"] == child["task_contract"]
    assert result["subagent_handoff"]["evidence_uris"] == ["scansci://evidence/doc-1/span-1"]
    assert set(result["_scientific_allowed_uris"]) == {
        "scansci://evidence/doc-1/span-1",
        parent["uri"],
        child["uri"],
    }


def test_model_scientific_calls_round_trip_to_host_and_partial_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    controls = [
        "delegate_scientific_agents",
        "list_scientific_agents",
        "collect_scientific_agents",
        "cancel_scientific_agents",
    ]
    parent = _parent(runtime, allowed_tools=controls)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    _OpenAIScientificAgentsHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIScientificAgentsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(
        workspace=runtime.workspace,
        evidence_db=runtime.evidence_db,
        active_run_id=parent["run_id"],
        scientific_agent_control=runtime._scientific_agent_control,
    )
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "delegate and collect"}],
                thinking_level="off",
                task_mode="research",
                task_contract=parent["task_contract"],
                timeout_seconds=30,
                session_id="scientific-e2e",
            )
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    completed = [
        event for event in events
        if event.get("type") == "status" and event.get("status") == "tool_completed"
    ]
    assert [event["name"] for event in completed] == [
        "delegate_scientific_agents",
        "collect_scientific_agents",
    ]
    tool_results = [
        message
        for payload in _OpenAIScientificAgentsHandler.request_payloads
        for message in list(payload.get("messages", []) or [])
        if message.get("role") == "tool"
    ]
    assert any("pi-native" in str(message.get("content", "")) for message in tool_results)
    assert not any(
        event.get("type") == "tool.call" and event.get("name") == "delegate_scientific_agents"
        for event in events
    )
    advertised = {
        str(dict(tool.get("function", {})).get("name", ""))
        for tool in _OpenAIScientificAgentsHandler.request_payloads[0]["tools"]
    }
    assert set(controls) <= advertised


def test_model_scientific_delegate_runs_native_pi_child_sessions(
    tmp_path: Path,
) -> None:
    """The model-visible delegate must be implemented by nested Pi sessions."""

    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime, allowed_tools=[
        "delegate_scientific_agents",
        "search_web",
        "discover_papers",
    ])
    _OpenAINativeScientificAgentsHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAINativeScientificAgentsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(
        workspace=runtime.workspace,
        evidence_db=runtime.evidence_db,
        active_run_id=parent["run_id"],
        scientific_agent_control=runtime._scientific_agent_control,
    )
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                model_runtime=pi_agent_module.ModelRuntimeDescriptor.for_testing(
                    context_window_tokens=200 * 1024,
                ).to_dict(),
                messages=[{"role": "user", "content": "delegate with native Pi children"}],
                thinking_level="off",
                task_mode="research",
                task_contract=parent["task_contract"],
                timeout_seconds=30,
                session_id="native-scientific-e2e",
            )
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    tool_results = [
        message
        for payload in _OpenAINativeScientificAgentsHandler.request_payloads
        for message in list(payload.get("messages", []) or [])
        if message.get("role") == "tool"
    ]
    assert any("pi-native" in str(message.get("content", "")) for message in tool_results)
    assert "PARENT_USED_NATIVE_CHILD_RESULTS" in "".join(
        str(event.get("content", "")) for event in events if event.get("type") == "delta"
    )
    assert any("NATIVE_PI_CHILD_ROLE=literature_scout" in json.dumps(payload, ensure_ascii=False)
               for payload in _OpenAINativeScientificAgentsHandler.request_payloads)
    assert any("NATIVE_PI_CHILD_ROLE=fulltext_analyst" in json.dumps(payload, ensure_ascii=False)
               for payload in _OpenAINativeScientificAgentsHandler.request_payloads)
    assert len(_OpenAINativeScientificAgentsHandler.request_payloads) >= 4
    subagents = [event for event in events if event.get("type") == "subagent"]
    assert {event.get("event") for event in subagents} >= {"started", "completed"}
    assert {event.get("role") for event in subagents if event.get("event") == "completed"} >= {
        "literature_scout",
        "fulltext_analyst",
    }
    assert not any(
        event.get("type") == "tool.call" and event.get("name") == "delegate_scientific_agents"
        for event in events
    )


def test_model_subagent_api_uses_generic_profiles_and_task_prompts(
    tmp_path: Path,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = _parent(runtime, allowed_tools=[
        "subagent",
        "list_subagents",
        "collect_subagents",
        "cancel_subagents",
        "search_web",
        "discover_papers",
    ])
    _OpenAIGenericSubagentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIGenericSubagentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(
        workspace=runtime.workspace,
        evidence_db=runtime.evidence_db,
        active_run_id=parent["run_id"],
        scientific_agent_control=runtime._scientific_agent_control,
    )
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                model_runtime=pi_agent_module.ModelRuntimeDescriptor.for_testing(
                    context_window_tokens=200 * 1024,
                ).to_dict(),
                messages=[{"role": "user", "content": "Use generic child sessions for this review."}],
                thinking_level="off",
                task_mode="research",
                task_contract=parent["task_contract"],
                timeout_seconds=30,
                session_id="generic-subagent-e2e",
            )
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    first_tools = {
        str(dict(tool.get("function", {})).get("name", ""))
        for tool in _OpenAIGenericSubagentHandler.request_payloads[0]["tools"]
    }
    assert {"subagent", "list_subagents", "collect_subagents", "cancel_subagents"} <= first_tools
    assert not (first_tools & {
        "delegate_scientific_agents",
        "list_scientific_agents",
        "collect_scientific_agents",
        "cancel_scientific_agents",
    })
    subagent_schema = next(
        dict(tool.get("function", {}))
        for tool in _OpenAIGenericSubagentHandler.request_payloads[0]["tools"]
        if dict(tool.get("function", {})).get("name") == "subagent"
    )
    assert subagent_schema["parameters"]["properties"]["calls"]["maxItems"] == 3
    joined = json.dumps(_OpenAIGenericSubagentHandler.request_payloads, ensure_ascii=False)
    assert "NATIVE_PI_CHILD_PROFILE=explore" in joined
    assert "NATIVE_PI_CHILD_PROFILE=review" in joined
    assert "GENERIC_TASK_ONE" in joined
    assert "GENERIC_TASK_TWO" in joined
    assert "GENERIC_PARENT_USED_CHILD_RESULTS" in "".join(
        str(event.get("content", "")) for event in events if event.get("type") == "delta"
    )
    completed_profiles = {
        event.get("profile")
        for event in events
        if event.get("type") == "subagent" and event.get("event") == "completed"
    }
    assert {"explore", "review"} <= completed_profiles
