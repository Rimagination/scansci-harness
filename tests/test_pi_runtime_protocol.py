from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
import threading
import time

import pytest

from scansci_html.model_metadata import ModelRuntimeDescriptor
from scansci_html.pi_agent import PiAgentClient, _PI_REQUIRED_FEATURES


_TASK_CONTRACT_V2 = {"schema_version": "scansci.task-contract.v2", "version": 2}


def _tool_names(payload: dict[str, object]) -> set[str]:
    return {
        str(dict(item.get("function", {}) or {}).get("name", ""))
        for item in list(payload.get("tools", []) or [])
        if isinstance(item, dict)
    }


class _DynamicToolHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        turn = len(type(self).request_payloads)
        if turn == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-search-tools",
                    "type": "function",
                    "function": {
                        "name": "search_tools",
                        "arguments": json.dumps({
                            "query": "workspace status",
                            "names": ["inspect_workspace"],
                            "activate": True,
                        }),
                    },
                }],
            }
            finish = "tool_calls"
        elif turn == 2:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-inspect-workspace",
                    "type": "function",
                    "function": {"name": "inspect_workspace", "arguments": "{}"},
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "Dynamic activation completed."}
            finish = "stop"
        chunks = [
            {
                "id": f"chatcmpl-dynamic-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-dynamic-{turn}",
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


class _RateLimitedAfterToolHandler(_DynamicToolHandler):
    def do_POST(self) -> None:
        if len(type(self).request_payloads) != 2:
            return super().do_POST()
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        type(self).request_payloads.append(payload)
        body = json.dumps({"error": {"code": "rate_limit_exceeded", "message": "429 temporary rate limit"}}).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_native_pi_retry_preserves_completed_tool_result(tmp_path: Path) -> None:
    _RateLimitedAfterToolHandler.request_payloads = []
    server, thread = _serve(_RateLimitedAfterToolHandler)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(client.stream_chat(
            provider_kind="openai-compatible", base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture", model_id="fixture-model",
            messages=[{"role": "user", "content": "Inspect the workspace."}],
            thinking_level="off", task_mode="general",
            task_contract={
                **_TASK_CONTRACT_V2, "contract_id": "retry-after-tool", "autonomy": "read_only",
                "risk_level": "read_only", "allowed_tools": ["inspect_workspace"],
                "initial_tools": [], "required_tool_groups": [],
                "initial_tool_budget": 3, "max_tool_budget": 6,
            },
            session_id="retry-after-tool", timeout_seconds=60,
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert events[-1]["type"] == "done"
    assert sum(e.get("type") == "tool.completed" and e.get("name") == "inspect_workspace" for e in events) == 1
    assert sum(e.get("status") == "retry" for e in events) == 1
    assert len(_RateLimitedAfterToolHandler.request_payloads) == 4
    before, after = _RateLimitedAfterToolHandler.request_payloads[2:]
    completed_tools = lambda payload: [m for m in payload["messages"] if m["role"] == "tool"]
    assert completed_tools(before) == completed_tools(after)
    assert completed_tools(after)


class _InventoryCaptureHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        chunk = {
            "id": "chatcmpl-inventory",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}],
        }
        stop = {
            **chunk,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        body = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(stop)}\n\ndata: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _PersistentActivationHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        turn = len(type(self).request_payloads)
        if turn == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-persistent-search",
                    "type": "function",
                    "function": {
                        "name": "search_tools",
                        "arguments": json.dumps({"names": ["inspect_workspace"], "activate": True}),
                    },
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": f"turn-{turn}-complete"}
            finish = "stop"
        chunks = [
            {
                "id": f"chatcmpl-persistent-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-persistent-{turn}",
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


class _EmptyLeaseSearchHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        turn = len(type(self).request_payloads)
        if turn == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call-empty-search",
                    "type": "function",
                    "function": {
                        "name": "search_tools",
                        "arguments": json.dumps({"names": ["inspect_workspace"], "activate": True}),
                    },
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "Empty inventory confirmed."}
            finish = "stop"
        chunks = [
            {
                "id": f"chatcmpl-empty-search-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-empty-search-{turn}",
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


class _SiblingToolHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []
    tool_name = ""

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payloads.append(json.loads(self.rfile.read(length)))
        turn = len(type(self).request_payloads)
        if turn == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": index,
                        "id": f"sibling-{index}",
                        "type": "function",
                        "function": {
                            "name": type(self).tool_name,
                            "arguments": json.dumps(
                                {"query": f"parallel fixture {index}"}
                                if type(self).tool_name == "search_web"
                                else {"notebook_id": f"fixture-{index}"}
                                if type(self).tool_name == "inspect_workspace"
                                else {}
                            ),
                        },
                    }
                    for index in range(2)
                ],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "Sibling calls completed."}
            finish = "stop"
        chunks = [
            {
                "id": f"chatcmpl-siblings-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-siblings-{turn}",
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


def _serve(handler: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _sibling_calls_arrive_before_first_result(tmp_path: Path, tool_name: str) -> bool:
    _SiblingToolHandler.request_payloads = []
    _SiblingToolHandler.tool_name = tool_name
    server, server_thread = _serve(_SiblingToolHandler)
    node, sidecar = PiAgentClient.runtime_paths()
    environment = PiAgentClient._node_environment()
    environment["SCANSCIPI_PROVIDER_KEY"] = "fixture-key"
    process = subprocess.Popen(
        [str(node), str(sidecar)],
        cwd=Path(__file__).parents[1],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output: Queue[str | None] = Queue()

    def drain_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)
        output.put(None)

    reader = threading.Thread(target=drain_stdout, daemon=True)
    reader.start()
    request_id = f"execution-mode-{tool_name}"
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps({
            "type": "run.start",
            "pi_protocol_version": 7,
            "required_features": list(_PI_REQUIRED_FEATURES),
            "request_id": request_id,
            "session_id": request_id,
            "ephemeral_session": True,
            "cwd": str(tmp_path),
            "agent_dir": str(tmp_path / ".agent"),
            "provider_kind": "openai-compatible",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model_id": "fixture-model",
            "api_surface": "chat_completions",
            "thinking_level": "off",
            "system_prompt": "",
            "prompt": "Call both supplied sibling tools exactly once.",
            "images": [],
            "model_runtime": ModelRuntimeDescriptor.for_testing().to_dict(),
            "task_mode": "general",
            "task_contract": {
                **_TASK_CONTRACT_V2,
                "contract_id": request_id,
                "risk_level": "read_only",
                "allowed_tools": [tool_name],
                "initial_tools": [tool_name],
                "initial_tool_budget": 4,
                "max_tool_budget": 6,
            },
            "mcp_servers": [],
            "disabled_tools": [],
        }) + "\n")
        process.stdin.flush()

        calls: list[dict[str, object]] = []
        deadline = time.monotonic() + 20
        while not calls:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "sidecar did not emit the first sibling tool call"
            try:
                line = output.get(timeout=min(1.0, remaining))
            except Empty:
                continue
            assert line is not None, process.stderr.read() if process.stderr else "sidecar closed"
            event = json.loads(line)
            if event.get("type") == "run.failed":
                raise AssertionError(event)
            if event.get("type") == "tool.call":
                calls.append(event)

        probe_deadline = time.monotonic() + 0.75
        while len(calls) < 2 and time.monotonic() < probe_deadline:
            try:
                line = output.get(timeout=max(0.01, probe_deadline - time.monotonic()))
            except Empty:
                break
            assert line is not None, "sidecar closed before sibling dispatch completed"
            event = json.loads(line)
            if event.get("type") == "tool.call":
                calls.append(event)
            elif event.get("type") == "run.failed":
                raise AssertionError(event)
        arrived_before_result = len(calls) == 2

        def send_result(call: dict[str, object]) -> None:
            assert process.stdin is not None
            process.stdin.write(json.dumps({
                "type": "tool.result",
                "request_id": request_id,
                "call_id": str(call["call_id"]),
                "ok": True,
                "result": {"ok": True},
            }) + "\n")
            process.stdin.flush()

        send_result(calls[0])
        if len(calls) < 2:
            while len(calls) < 2:
                line = output.get(timeout=10)
                assert line is not None, "sidecar closed before sequential sibling dispatch"
                event = json.loads(line)
                if event.get("type") == "tool.call":
                    calls.append(event)
                elif event.get("type") == "run.failed":
                    raise AssertionError(event)
        send_result(calls[1])
        while True:
            line = output.get(timeout=10)
            assert line is not None, "sidecar closed before run completion"
            event = json.loads(line)
            if event.get("type") == "run.failed":
                raise AssertionError(event)
            if event.get("type") == "run.completed":
                break
        return arrived_before_result
    finally:
        process.kill()
        process.wait(timeout=5)
        server.shutdown()
        server_thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize(
    ("tool_name", "expected_parallel"),
    [
        ("search_web", True),
        ("inspect_workspace", False),
        ("self_assess", False),
    ],
)
def test_real_sdk_sibling_dispatch_honors_tool_definition_execution_mode(
    tmp_path: Path,
    tool_name: str,
    expected_parallel: bool,
) -> None:
    assert _sibling_calls_arrive_before_first_result(tmp_path, tool_name) is expected_parallel


def test_sidecar_starts_bootstrap_only_then_additively_activates_an_authorized_tool(tmp_path: Path) -> None:
    _DynamicToolHandler.request_payloads = []
    server, thread = _serve(_DynamicToolHandler)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "Inspect the workspace after discovering the right tool."}],
            thinking_level="off",
            task_mode="general",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "dynamic-loader",
                "autonomy": "read_only",
                "risk_level": "read_only",
                "allowed_tools": ["inspect_workspace", "search_web"],
                "initial_tools": [],
                "required_tool_groups": [["inspect_workspace"]],
                "initial_tool_budget": 3,
                "max_tool_budget": 6,
            },
            session_id="dynamic-loader-session",
            timeout_seconds=30,
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(_DynamicToolHandler.request_payloads) == 3
    first, second, third = map(_tool_names, _DynamicToolHandler.request_payloads)
    assert first == {
        "ask_user",
        "load_skill",
        "search_skills",
        "search_tools",
        "submit_plan",
    }
    assert second == first | {"inspect_workspace"}
    assert third == second
    assert "search_web" not in third
    assert any(item.get("type") == "tool.completed" and item.get("name") == "inspect_workspace" for item in events)
    searches = [
        item for item in events
        if item.get("type") == "status"
        and item.get("status") == "tool_catalog_searched"
        and item.get("name") == "search_tools"
    ]
    assert searches
    decision = dict(dict(searches[-1].get("details", {}) or {}).get("decision", {}) or {})
    assert decision["schema_version"] == "scansci.discovery-decision.v1"
    assert decision["source"] == "local_heuristic"
    assert decision["fallback_used"] is True
    assert decision["threshold"] == 0.65
    assert events[-1]["type"] == "done"
    inventory = events[-1]["stats"]["toolInventory"]
    assert inventory["registered"] == 5
    assert inventory["active"] == 4
    assert inventory["registeredNames"] == [
        "ask_user",
        "inspect_workspace",
        "search_tools",
        "search_web",
        "submit_plan",
    ]
    assert events[-1]["stats"]["prefixShape"]["components"]["registered_tool_count"] == 5
    assert events[-1]["stats"]["prefixShape"]["components"]["active_tool_count"] == 4


def test_explicit_empty_lease_keeps_loader_but_returns_an_empty_domain_inventory(tmp_path: Path) -> None:
    _InventoryCaptureHandler.request_payloads = []
    server, thread = _serve(_InventoryCaptureHandler)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "Hello"}],
            thinking_level="off",
            task_mode="general",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "explicit-empty",
                "risk_level": "none",
                "allowed_tools": [],
                "initial_tools": [],
                "initial_tool_budget": 2,
                "max_tool_budget": 4,
            },
            session_id="explicit-empty-session",
            timeout_seconds=30,
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert _tool_names(_InventoryCaptureHandler.request_payloads[0]) == {
        "ask_user",
        "load_skill",
        "search_skills",
        "search_tools",
        "submit_plan",
    }
    assert events[-1]["stats"]["toolInventory"]["registered"] == 3


def test_reused_logical_session_revokes_previous_domain_inventory_on_the_next_turn(tmp_path: Path) -> None:
    _InventoryCaptureHandler.request_payloads = []
    server, thread = _serve(_InventoryCaptureHandler)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    common = dict(
        provider_kind="openai-compatible",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key="fixture-key",
        model_id="fixture-model",
        thinking_level="off",
        task_mode="general",
        session_id="revocation-session",
        timeout_seconds=30,
    )
    try:
        first = list(client.stream_chat(
            messages=[{"role": "user", "content": "First"}],
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "grant",
                "risk_level": "read_only",
                "allowed_tools": ["inspect_workspace"],
                "initial_tools": ["inspect_workspace"],
            },
            **common,
        ))
        second = list(client.stream_chat(
            messages=[{"role": "user", "content": "Second"}],
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "revoke",
                "risk_level": "none",
                "allowed_tools": [],
                "initial_tools": [],
            },
            **common,
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert "inspect_workspace" in _tool_names(_InventoryCaptureHandler.request_payloads[0])
    assert "inspect_workspace" not in _tool_names(_InventoryCaptureHandler.request_payloads[1])
    assert first[-1]["type"] == "done"
    assert second[-1]["type"] == "done"


def test_additive_activation_survives_the_next_turn_in_the_same_session(tmp_path: Path) -> None:
    _PersistentActivationHandler.request_payloads = []
    server, thread = _serve(_PersistentActivationHandler)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    common = dict(
        provider_kind="openai-compatible",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key="fixture-key",
        model_id="fixture-model",
        thinking_level="off",
        task_mode="general",
        session_id="persistent-activation-session",
        timeout_seconds=30,
    )
    contract = {
        **_TASK_CONTRACT_V2,
        "risk_level": "read_only",
        "allowed_tools": ["inspect_workspace", "search_web"],
        "initial_tools": [],
        "initial_tool_budget": 3,
        "max_tool_budget": 6,
        "recovery_budget": 2,
        "model_token_budget": 48_000,
        "max_model_token_budget": 192_000,
    }
    try:
        first = list(client.stream_chat(
            messages=[
                {"role": "system", "content": "Use the first-turn response policy."},
                {"role": "user", "content": "Discover the workspace tool."},
            ],
            task_contract={**contract, "contract_id": "continuity-first", "goal": "first"},
            **common,
        ))
        second = list(client.stream_chat(
            messages=[
                {"role": "system", "content": "Use the second-turn response policy."},
                {"role": "user", "content": "Continue with the same tool inventory."},
            ],
            task_contract={**contract, "contract_id": "continuity-second", "goal": "second"},
            **{**common, "thinking_level": "high", "task_mode": "knowledge"},
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(_PersistentActivationHandler.request_payloads) == 3
    first_surface, activated_surface, resumed_surface = map(
        _tool_names,
        _PersistentActivationHandler.request_payloads,
    )
    assert first_surface == {
        "ask_user",
        "load_skill",
        "search_skills",
        "search_tools",
        "submit_plan",
    }
    assert activated_surface == first_surface | {"inspect_workspace"}
    assert resumed_surface == activated_surface
    assert "search_web" not in resumed_surface
    assert next(event for event in second if event.get("type") == "session")["resumed"] is True
    assert any(
        message.get("role") == "user"
        and "Discover the workspace tool." in str(message.get("content"))
        for message in _PersistentActivationHandler.request_payloads[2]["messages"]
    )
    assert first[-1]["type"] == "done"
    assert second[-1]["type"] == "done"


def test_empty_lease_loader_executes_but_cannot_activate_a_domain_tool(tmp_path: Path) -> None:
    _EmptyLeaseSearchHandler.request_payloads = []
    server, thread = _serve(_EmptyLeaseSearchHandler)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "Check whether a workspace tool is available."}],
            thinking_level="off",
            task_mode="general",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "empty-loader-call",
                "risk_level": "none",
                "allowed_tools": [],
                "initial_tools": [],
                "initial_tool_budget": 2,
                "max_tool_budget": 4,
            },
            session_id="empty-loader-call-session",
            timeout_seconds=30,
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(_EmptyLeaseSearchHandler.request_payloads) == 2
    governance = {
        "ask_user",
        "load_skill",
        "search_skills",
        "search_tools",
        "submit_plan",
    }
    assert _tool_names(_EmptyLeaseSearchHandler.request_payloads[0]) == governance
    assert _tool_names(_EmptyLeaseSearchHandler.request_payloads[1]) == governance
    tool_messages = [
        message
        for message in list(_EmptyLeaseSearchHandler.request_payloads[1].get("messages", []) or [])
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    loader_result = json.loads(str(tool_messages[-1]["content"]))
    assert loader_result["matches"] == []
    assert loader_result["activated"] == []
    assert loader_result["rejected"] == [{
        "name": "inspect_workspace",
        "reason": "not_authorized_or_unavailable",
    }]
    assert not any(
        event.get("type") == "tool.completed" and event.get("name") == "inspect_workspace"
        for event in events
    )
    assert events[-1]["stats"]["toolInventory"]["registeredNames"] == [
        "ask_user",
        "search_tools",
        "submit_plan",
    ]


def test_ping_advertises_dynamic_tool_discovery() -> None:
    status = PiAgentClient.runtime_status()

    assert "dynamic_tools" in status["capabilities"]
    assert "ephemeral_sessions" in status["capabilities"]
    assert "ephemeral_sessions" in _PI_REQUIRED_FEATURES
    assert set(_PI_REQUIRED_FEATURES) <= set(status["capabilities"])
