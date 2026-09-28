from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from scansci_html.model_metadata import descriptor_from_model_record
from scansci_html.pi_agent import PiAgentClient, PiAgentRunError, PiMultimodalUnavailable


_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
).decode("ascii")
_IMAGE = {"type": "image", "data": _PNG, "mimeType": "image/png"}
_REPOSITORY = Path(__file__).resolve().parents[1]


def _fixed_high_entropy_ascii(label: str, length: int) -> str:
    output = ""
    counter = 0
    while len(output) < length:
        output += base64.b64encode(hashlib.sha256(f"{label}:{counter}".encode()).digest()).decode().rstrip("=")
        counter += 1
    return output[:length]


def _run_runtime_extension_harness(tmp_path: Path, cases: list[dict[str, object]]) -> list[dict[str, object]]:
    entry = tmp_path / "runtime-extension-harness.ts"
    output = tmp_path / "runtime-extension-harness.mjs"
    runtime_extension = (_REPOSITORY / "pi-runtime" / "src" / "runtime-extension.ts").as_posix()
    token_estimate = (_REPOSITORY / "pi-runtime" / "src" / "token-estimate.ts").as_posix()
    entry.write_text(
        "\n".join([
            f'import {{ buildTokenEnvelopeContextView }} from "{runtime_extension}";',
            f'import {{ conservativeTextTokens }} from "{token_estimate}";',
            f"const cases = {json.dumps(cases, ensure_ascii=False)};",
            "const output = cases.map((item) => {",
            "  try {",
            "    if (typeof item.text === 'string') {",
            "      return { name: item.name, tokens: conservativeTextTokens(item.text, item.descriptor) };",
            "    }",
            "    const projected = buildTokenEnvelopeContextView(item.messages, Number(item.limit), Number(item.reserved || 0));",
            "    return { name: item.name, messages: projected.messages, report: projected.report };",
            "  } catch (error) {",
            "    return { name: item.name, error: String(error?.message || error), code: String(error?.code || '') };",
            "  }",
            "});",
            "process.stdout.write(JSON.stringify(output));",
        ]),
        encoding="utf-8",
    )
    node, _script = PiAgentClient.runtime_paths()
    esbuild = _REPOSITORY / "node_modules" / "esbuild" / "bin" / "esbuild"
    built = subprocess.run(
        [
            str(node),
            str(esbuild),
            str(entry),
            "--bundle",
            "--platform=node",
            "--format=esm",
            "--banner:js=import { createRequire as __scansciCreateRequire } from 'node:module'; const require = __scansciCreateRequire(import.meta.url);",
            f"--outfile={output}",
        ],
        cwd=_REPOSITORY,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    executed = subprocess.run(
        [str(node), str(output)],
        cwd=_REPOSITORY,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert executed.returncode == 0, executed.stderr
    return json.loads(executed.stdout)


def test_node_token_estimator_uses_exact_known_encoding_and_safe_unknown_fallback(tmp_path: Path) -> None:
    repeated = "X" * 70_000
    high_entropy = _fixed_high_entropy_ascii("node-tokenizer", 70_000)
    results = _run_runtime_extension_harness(tmp_path, [
        {
            "name": "o200k",
            "text": repeated,
            "descriptor": {"provider_id": "openai", "model_id": "gpt-4o"},
        },
        {
            "name": "cl100k",
            "text": repeated,
            "descriptor": {"provider_id": "openai", "model_id": "gpt-4"},
        },
        {
            "name": "unknown",
            "text": high_entropy,
            "descriptor": {"provider_id": "custom", "model_id": "fixture-model"},
        },
    ])

    assert results == [
        {"name": "o200k", "tokens": 4_375},
        {"name": "cl100k", "tokens": 8_750},
        {"name": "unknown", "tokens": len(high_entropy.encode("utf-8"))},
    ]


def test_node_token_envelope_keeps_current_tool_turn_atomic_across_provider_shapes(tmp_path: Path) -> None:
    old_turn = [
        {"role": "user", "content": "OLD-USER " + ("x" * 20_000)},
        {"role": "assistant", "content": "OLD-ASSISTANT " + ("y" * 20_000)},
    ]
    cases = [
        {
            "name": "pi-canonical",
            "limit": 4096,
            "messages": [
                *old_turn,
                {"role": "user", "content": "FINAL-USER"},
                {"role": "assistant", "content": [
                    {"type": "toolCall", "id": "call-1", "name": "search_web", "arguments": {"query": "one"}},
                    {"type": "toolCall", "id": "call-2", "name": "search_web", "arguments": {"query": "two"}},
                ]},
                {"role": "toolResult", "toolCallId": "call-1", "content": [{"type": "text", "text": "CURRENT-TOOL-RESULT-1"}]},
                {"role": "toolResult", "toolCallId": "call-2", "content": [{"type": "text", "text": "CURRENT-TOOL-RESULT-2"}]},
            ],
        },
        {
            "name": "openai-like",
            "limit": 4096,
            "messages": [
                *old_turn,
                {"role": "user", "content": "FINAL-USER"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-1", "type": "function", "function": {"name": "search_web", "arguments": "{\"query\":\"one\"}"}},
                    {"id": "call-2", "type": "function", "function": {"name": "search_web", "arguments": "{\"query\":\"two\"}"}},
                ]},
                {"role": "tool", "tool_call_id": "call-1", "content": "CURRENT-TOOL-RESULT-1"},
                {"role": "tool", "tool_call_id": "call-2", "content": "CURRENT-TOOL-RESULT-2"},
            ],
        },
        {
            "name": "anthropic-like",
            "limit": 4096,
            "messages": [
                *old_turn,
                {"role": "user", "content": "FINAL-USER"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "call-1", "name": "search_web", "input": {"query": "one"}},
                    {"type": "tool_use", "id": "call-2", "name": "search_web", "input": {"query": "two"}},
                ]},
                {"role": "toolResult", "toolCallId": "call-1", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "CURRENT-TOOL-RESULT-1"}]},
                {"role": "toolResult", "toolCallId": "call-2", "content": [{"type": "tool_result", "tool_use_id": "call-2", "content": "CURRENT-TOOL-RESULT-2"}]},
            ],
        },
    ]

    results = _run_runtime_extension_harness(tmp_path, cases)

    assert {result["name"] for result in results} == {"pi-canonical", "openai-like", "anthropic-like"}
    for result in results:
        assert "error" not in result
        projected_messages = result["messages"]
        encoded = json.dumps(projected_messages, ensure_ascii=False)
        assert "FINAL-USER" in encoded
        assert "call-1" in encoded and "call-2" in encoded
        assert "CURRENT-TOOL-RESULT-1" in encoded and "CURRENT-TOOL-RESULT-2" in encoded
        assert "OLD-USER" not in encoded and "OLD-ASSISTANT" not in encoded
        final_user_index = next(
            index for index, message in enumerate(projected_messages)
            if message.get("role") == "user" and message.get("content") == "FINAL-USER"
        )
        assistant_calls = [
            message for message in projected_messages[final_user_index + 1:]
            if message.get("role") == "assistant"
        ]
        assert len(assistant_calls) == 1
        assistant_wire = json.dumps(assistant_calls[0], ensure_ascii=False)
        assert "call-1" in assistant_wire and "call-2" in assistant_wire


def test_node_token_envelope_rejects_current_tool_turn_overflow_as_typed_error(tmp_path: Path) -> None:
    results = _run_runtime_extension_harness(tmp_path, [{
        "name": "mandatory-overflow",
        "limit": 4096,
        "messages": [
            {"role": "user", "content": "FINAL-USER"},
            {"role": "assistant", "content": [{
                "type": "toolCall",
                "id": "call-overflow",
                "name": "search_web",
                "arguments": {"query": "Q" * 20_000},
            }]},
            {"role": "toolResult", "toolCallId": "call-overflow", "content": "CURRENT-TOOL-RESULT"},
        ],
    }])

    assert results == [{
        "name": "mandatory-overflow",
        "error": "Current active tool turn exceeds the provider input token limit",
        "code": "SCANSCI_CONTEXT_MANDATORY_OVERFLOW",
    }]


def test_node_token_envelope_reserves_provider_prefix_before_admitting_old_turns(tmp_path: Path) -> None:
    results = _run_runtime_extension_harness(tmp_path, [{
        "name": "provider-prefix-reserve",
        "limit": 4096,
        "reserved": 1600,
        "messages": [
            {"role": "user", "content": "OLD-USER " + ("x" * 2400)},
            {"role": "assistant", "content": "OLD-ASSISTANT"},
            {"role": "user", "content": "FINAL-USER"},
        ],
    }])

    assert "error" not in results[0]
    encoded = json.dumps(results[0]["messages"], ensure_ascii=False)
    assert "FINAL-USER" in encoded
    assert "OLD-USER" not in encoded
    assert results[0]["report"]["provider_prefix_tokens"] == 1600
    assert results[0]["report"]["estimated_tokens"] <= 4096


def _descriptor(
    *,
    vision: bool = True,
    api_surface: str = "chat_completions",
    context_window: str = "32K",
) -> dict[str, object]:
    return descriptor_from_model_record(
        provider_id="fixture",
        provider_kind="openai-compatible",
        model_id="fixture-model",
        model_record={
            "id": "fixture-model",
            "context_window": context_window,
            "capabilities": ["reasoning", "tool", *( ["vision"] if vision else [] )],
        },
        api_surface=api_surface,
    ).to_dict()


def test_python_bridge_forwards_canonical_images_and_runtime_descriptor(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")

    def fake_run_request(start_message, *, api_key, timeout_seconds):
        del api_key, timeout_seconds
        captured.update(start_message)
        yield {"type": "delta", "content": "ok"}
        yield {"type": "done", "stats": {}}

    monkeypatch.setattr(client, "_run_request", fake_run_request)
    events = list(
        client.stream_chat(
            provider_kind="openai-compatible",
            base_url="https://example.invalid/v1",
            api_key="fixture",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "Inspect the image."}],
            images=[_IMAGE],
            model_runtime=_descriptor(),
        )
    )

    assert events[-1]["type"] == "done"
    assert captured["pi_protocol_version"] == 7
    assert captured["images"] == [_IMAGE]
    assert captured["model_runtime"]["context_window_tokens"] == 32_768
    assert "multimodal_turns" in captured["required_features"]


def test_python_bridge_rejects_images_for_text_only_descriptor_before_sidecar(tmp_path: Path, monkeypatch) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(
        client,
        "_run_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sidecar must not start")),
    )

    with pytest.raises(PiMultimodalUnavailable, match="image"):
        list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url="https://example.invalid/v1",
                api_key="fixture",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "Inspect the image."}],
                images=[_IMAGE],
                model_runtime=_descriptor(vision=False),
            )
        )


def test_python_bridge_rejects_descriptor_from_a_different_final_route(tmp_path: Path, monkeypatch) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    descriptor = descriptor_from_model_record(
        provider_id="model-a-provider",
        provider_kind="openai-compatible",
        model_id="model-a",
        model_record={
            "id": "model-a",
            "context_window": "200K",
            "capabilities": ["reasoning", "tool", "vision"],
        },
    )
    monkeypatch.setattr(
        client,
        "_run_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sidecar must not start")),
    )

    with pytest.raises(ValueError, match="route"):
        list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url="https://example.invalid/v1",
                api_key="fixture",
                model_id="model-b",
                messages=[{"role": "user", "content": "Do not rebind model A metadata."}],
                model_runtime=descriptor,
            )
        )


def test_load_session_rejects_descriptor_from_a_different_final_route(tmp_path: Path, monkeypatch) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    session_file = tmp_path / "persisted-session.jsonl"
    session_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(client, "_load_session_registry", lambda: {"session-a": str(session_file)})
    monkeypatch.setattr(
        client,
        "_ensure_process",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("sidecar must not start")),
    )
    descriptor = descriptor_from_model_record(
        provider_id="model-a-provider",
        provider_kind="openai-compatible",
        model_id="model-a",
        model_record={"id": "model-a", "context_window": "200K", "capabilities": ["vision"]},
    )

    with pytest.raises(ValueError, match="route"):
        client.load_session(
            "session-a",
            provider_kind="openai-compatible",
            base_url="https://example.invalid/v1",
            api_key="fixture",
            model_id="model-b",
            model_runtime=descriptor,
        )


def test_steer_and_follow_up_forward_images_in_canonical_shape(tmp_path: Path, monkeypatch) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    request_id = "active-request"
    client._run_contexts[request_id] = SimpleNamespace(active=True, supports_images=True)  # type: ignore[assignment]
    written: list[dict[str, object]] = []
    monkeypatch.setattr(client, "_write", lambda payload: written.append(dict(payload)))

    assert client.steer("look closer", request_id, images=[_IMAGE]) is True
    assert client.follow_up("compare this", request_id, images=[_IMAGE]) is True

    assert written[0]["images"] == [_IMAGE]
    assert written[1]["images"] == [_IMAGE]
    assert _PNG not in json.dumps([{k: v for k, v in item.items() if k != "images"} for item in written])


class _ShapeHandler(BaseHTTPRequestHandler):
    mode = "openai"
    payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).payloads.append(json.loads(self.rfile.read(length)))
        if type(self).mode == "anthropic":
            events = [
                ("message_start", {"type": "message_start", "message": {"id": "msg_fixture", "type": "message", "role": "assistant", "content": [], "model": "fixture-model", "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}}),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            body = "".join(f"event: {name}\ndata: {json.dumps(value)}\n\n" for name, value in events)
        elif type(self).mode == "responses":
            response = {"id": "resp_fixture", "object": "response", "created_at": 1, "status": "completed", "model": "fixture-model", "output": [], "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}}
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress"}},
                {"type": "response.output_item.added", "output_index": 0, "item": {"id": "msg_fixture", "type": "message", "role": "assistant", "content": [], "status": "in_progress"}},
                {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "ok"},
                {"type": "response.output_item.done", "output_index": 0, "item": {"id": "msg_fixture", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok", "annotations": []}], "status": "completed"}},
                {"type": "response.completed", "response": response},
            ]
            body = "".join(f"data: {json.dumps(value)}\n\n" for value in events) + "data: [DONE]\n\n"
        else:
            chunks = [
                {"id": "chat_fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
                {"id": "chat_fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join(f"data: {json.dumps(value)}\n\n" for value in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _provider_payload(tmp_path: Path, *, provider_kind: str, api_surface: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    _ShapeHandler.mode = "anthropic" if provider_kind == "anthropic-compatible" or api_surface == "anthropic_messages" else ("responses" if api_surface == "responses" else "openai")
    _ShapeHandler.payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ShapeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        descriptor = descriptor_from_model_record(
            provider_id="fixture",
            provider_kind=provider_kind,
            model_id="fixture-model",
            model_record={"id": "fixture-model", "context_window": "32K", "capabilities": ["reasoning", "tool", "vision"]},
            api_surface=api_surface,
        ).to_dict()
        events = list(
            client.stream_chat(
                provider_kind=provider_kind,
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                api_surface=api_surface,
                responses_enabled=api_surface == "responses",
                messages=[{"role": "user", "content": "Inspect the image."}],
                images=[_IMAGE],
                model_runtime=descriptor,
                thinking_level="off",
                timeout_seconds=30,
            )
        )
        return events, list(_ShapeHandler.payloads)
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize(
    ("provider_kind", "api_surface"),
    [
        ("openai-compatible", "chat_completions"),
        ("openai-compatible", "responses"),
        ("openai-compatible", "anthropic_messages"),
        ("anthropic-compatible", "chat_completions"),
    ],
)
def test_pi_sdk_serializes_real_provider_multimodal_shapes(
    tmp_path: Path,
    provider_kind: str,
    api_surface: str,
) -> None:
    events, payloads = _provider_payload(tmp_path, provider_kind=provider_kind, api_surface=api_surface)

    assert "".join(str(event.get("content", "")) for event in events if event.get("type") == "delta") == "ok"
    payload = payloads[0]
    encoded = json.dumps(payload, ensure_ascii=False)
    if provider_kind == "anthropic-compatible" or api_surface == "anthropic_messages":
        assert '"type": "image"' in encoded
        assert '"source": {"type": "base64", "media_type": "image/png"' in encoded
    elif api_surface == "responses":
        assert '"type": "input_image"' in encoded
        assert f"data:image/png;base64,{_PNG}" in encoded
    else:
        assert '"type": "image_url"' in encoded
        assert f"data:image/png;base64,{_PNG}" in encoded
    assert _PNG not in json.dumps(events, ensure_ascii=False)


@pytest.mark.parametrize(
    "invalid_image",
    [
        {"type": "image", "data": "A" * 2048, "mimeType": "image/png", "path": "x"},
        {
            "type": "image",
            "data": base64.b64encode(b"\x89PNG\r\n\x1a\ntruncated").decode("ascii"),
            "mimeType": "image/png",
        },
    ],
    ids=["unknown-key", "missing-dimensions"],
)
def test_node_rejects_invalid_image_without_echoing_base64(
    tmp_path: Path,
    invalid_image: dict[str, str],
) -> None:
    node, script = PiAgentClient.runtime_paths()
    process = subprocess.Popen(
        [str(node), str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**PiAgentClient._node_environment(), "SCANSCIPI_PROVIDER_KEY": "fixture"},
    )
    assert process.stdin is not None
    assert process.stdout is not None
    invalid = invalid_image["data"]
    try:
        process.stdin.write(json.dumps({
            "type": "run.start",
            "pi_protocol_version": 7,
            "required_features": ["model_runtime_descriptor", "token_envelope", "multimodal_turns"],
            "request_id": "invalid-image",
            "session_id": "invalid-image",
            "ephemeral_session": True,
            "cwd": str(tmp_path),
            "agent_dir": str(tmp_path / ".agent"),
            "provider_kind": "openai-compatible",
            "base_url": "http://127.0.0.1:1/v1",
            "model_id": "fixture-model",
            "system_prompt": "",
            "prompt": "inspect",
            "images": [invalid_image],
            "model_runtime": _descriptor(),
            "task_contract": {"schema_version": "scansci.task-contract.v2", "version": 2, "allowed_tools": []},
        }) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 10
        lines: list[str] = []
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if '"type":"run.failed"' in line:
                break
        output = "".join(lines)
    finally:
        process.kill()
        process.wait(timeout=5)

    assert '"type":"run.failed"' in output
    assert "image" in output.lower()
    assert invalid not in output


def test_node_rejects_semantically_inconsistent_descriptor_before_network(tmp_path: Path) -> None:
    _ShapeHandler.mode = "openai"
    _ShapeHandler.payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ShapeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    node, script = PiAgentClient.runtime_paths()
    process = subprocess.Popen(
        [str(node), str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**PiAgentClient._node_environment(), "SCANSCIPI_PROVIDER_KEY": "fixture"},
    )
    assert process.stdin is not None
    assert process.stdout is not None
    descriptor = _descriptor()
    descriptor["capabilities"] = ["reasoning", "tool"]
    try:
        process.stdin.write(json.dumps({
            "type": "run.start",
            "pi_protocol_version": 7,
            "required_features": ["model_runtime_descriptor", "token_envelope", "multimodal_turns"],
            "request_id": "invalid-descriptor",
            "session_id": "invalid-descriptor",
            "ephemeral_session": True,
            "cwd": str(tmp_path),
            "agent_dir": str(tmp_path / ".agent"),
            "provider_kind": "openai-compatible",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model_id": "fixture-model",
            "system_prompt": "",
            "prompt": "hello",
            "images": [],
            "model_runtime": descriptor,
            "task_contract": {"schema_version": "scansci.task-contract.v2", "version": 2, "allowed_tools": []},
        }) + "\n")
        process.stdin.flush()
        lines: list[str] = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if '"type":"run.failed"' in line:
                break
        output = "".join(lines)
    finally:
        process.kill()
        process.wait(timeout=5)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert _ShapeHandler.payloads == []
    assert '"type":"run.failed"' in output
    assert "capability" in output.lower() or "modality" in output.lower()


@pytest.mark.parametrize(
    "oversized_prompt",
    ["X" * 150_000, _fixed_high_entropy_ascii("provider-gate", 70_000)],
    ids=["long-repeat", "high-entropy-ascii"],
)
def test_provider_gate_blocks_oversized_final_payload_after_fail_open_hook(
    tmp_path: Path,
    oversized_prompt: str,
) -> None:
    _ShapeHandler.mode = "openai"
    _ShapeHandler.payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ShapeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    node, script = PiAgentClient.runtime_paths()
    process = subprocess.Popen(
        [str(node), str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**PiAgentClient._node_environment(), "SCANSCIPI_PROVIDER_KEY": "fixture"},
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(json.dumps({
            "type": "run.start",
            "pi_protocol_version": 7,
            "required_features": ["model_runtime_descriptor", "token_envelope", "multimodal_turns"],
            "request_id": "provider-hard-gate",
            "session_id": "provider-hard-gate",
            "ephemeral_session": True,
            "cwd": str(tmp_path),
            "agent_dir": str(tmp_path / ".agent"),
            "provider_kind": "openai-compatible",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model_id": "fixture-model",
            "api_surface": "chat_completions",
            "system_prompt": "",
            # Above the descriptor's 27,648 provider-input envelope under a
            # real tokenizer. The extension hook throws, but Pi's
            # runner catches that exception and otherwise sends the request.
            "prompt": oversized_prompt,
            "images": [],
            "model_runtime": _descriptor(),
            "task_contract": {"schema_version": "scansci.task-contract.v2", "version": 2, "allowed_tools": []},
        }) + "\n")
        process.stdin.flush()
        lines: list[str] = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if '"type":"run.failed"' in line or '"type":"run.completed"' in line:
                break
        output = "".join(lines)
    finally:
        process.kill()
        process.wait(timeout=5)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert _ShapeHandler.payloads == []
    assert '"type":"run.failed"' in output
    assert "budget" in output.lower() or "context" in output.lower()


def test_provider_gate_blocks_high_resolution_visual_budget_before_http(tmp_path: Path) -> None:
    _ShapeHandler.mode = "openai"
    _ShapeHandler.payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ShapeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    raw = bytearray(base64.b64decode(_PNG))
    raw[16:20] = (8_000).to_bytes(4, "big")
    raw[20:24] = (5_000).to_bytes(4, "big")
    image = {
        "type": "image",
        "data": base64.b64encode(raw).decode("ascii"),
        "mimeType": "image/png",
    }
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        with pytest.raises(PiAgentRunError, match="budget|context|输入上限"):
            list(client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "Inspect this high-resolution image."}],
                images=[image],
                model_runtime=_descriptor(),
                thinking_level="off",
                timeout_seconds=30,
            ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert _ShapeHandler.payloads == []


def test_degraded_text_only_descriptor_emits_explicit_capability_status(tmp_path: Path) -> None:
    _ShapeHandler.mode = "openai"
    _ShapeHandler.payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ShapeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    descriptor = _descriptor(vision=False)
    descriptor["degraded"] = True
    descriptor["degradation_reasons"] = ["vision_input_degraded_to_ocr_text"]
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "OCR-SENTINEL"}],
            images=[],
            model_runtime=descriptor,
            thinking_level="off",
            timeout_seconds=30,
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    status = next(event for event in events if event.get("type") == "status")
    assert status["status"] == "capability_degraded"
    assert status["details"]["degradation_reasons"] == ["vision_input_degraded_to_ocr_text"]
    assert _ShapeHandler.payloads


def test_final_provider_gate_revalidates_post_hook_model_output_and_aggregate_images(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    bundle = tmp_path / "provider-gate.mjs"
    esbuild = repository / "node_modules" / ".bin" / "esbuild.cmd"
    subprocess.run(
        [
            str(esbuild),
            str(repository / "pi-runtime" / "src" / "scansci-provider.ts"),
            "--bundle",
            "--platform=node",
            "--format=esm",
            f"--outfile={bundle}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    node, _script = PiAgentClient.runtime_paths()
    probe = (
        f'import {{ assertProviderRequest }} from {json.dumps(bundle.as_uri())};'
        "const payload=JSON.parse(process.argv[1]);"
        "const descriptor=JSON.parse(process.argv[2]);"
        "try{assertProviderRequest(payload,descriptor,'openai-completions');"
        "process.stdout.write('accepted')}catch(error){process.stdout.write(String(error));process.exitCode=3}"
    )
    valid = {
        "model": "fixture-model",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 4_096,
    }
    five_images = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG}"}}
        for _index in range(5)
    ]
    invalid_payloads = [
        {key: value for key, value in valid.items() if key != "model"},
        {key: value for key, value in valid.items() if key != "max_tokens"},
        {**valid, "model": "hook-swapped-model"},
        {**valid, "max_tokens": 4_097},
        {**valid, "messages": [{"role": "user", "content": five_images}]},
    ]

    accepted = subprocess.run(
        [str(node), "-e", probe, json.dumps(valid), json.dumps(_descriptor())],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    rejected = [
        subprocess.run(
            [str(node), "-e", probe, json.dumps(payload), json.dumps(_descriptor())],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        for payload in invalid_payloads
    ]

    assert accepted.returncode == 0
    assert accepted.stdout == "accepted"
    assert all(result.returncode == 3 for result in rejected)
    assert all(_PNG not in result.stdout for result in rejected)


def test_real_sidecar_steer_and_followup_images_reach_provider(tmp_path: Path) -> None:
    first_request = threading.Event()
    release_first = threading.Event()

    class QueueShapeHandler(BaseHTTPRequestHandler):
        payloads: list[dict[str, object]] = []

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            type(self).payloads.append(json.loads(self.rfile.read(length)))
            if len(type(self).payloads) == 1:
                first_request.set()
                release_first.wait(timeout=10)
            chunks = [
                {"id": "chat_queue", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
                {"id": "chat_queue", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join(f"data: {json.dumps(value)}\n\n" for value in chunks) + "data: [DONE]\n\n"
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QueueShapeHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    node, script = PiAgentClient.runtime_paths()
    process = subprocess.Popen(
        [str(node), str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**PiAgentClient._node_environment(), "SCANSCIPI_PROVIDER_KEY": "fixture"},
    )
    assert process.stdin is not None
    assert process.stdout is not None
    lines: list[str] = []
    terminal = threading.Event()

    def read_output() -> None:
        for line in process.stdout or ():
            lines.append(line)
            if '"type":"run.completed"' in line or '"type":"run.failed"' in line:
                terminal.set()
                return

    output_thread = threading.Thread(target=read_output, daemon=True)
    output_thread.start()
    start = {
        "type": "run.start",
        "pi_protocol_version": 7,
        "required_features": ["model_runtime_descriptor", "token_envelope", "multimodal_turns"],
        "request_id": "queued-images",
        "session_id": "queued-images",
        "ephemeral_session": True,
        "cwd": str(tmp_path),
        "agent_dir": str(tmp_path / ".agent"),
        "provider_kind": "openai-compatible",
        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "model_id": "fixture-model",
        "api_surface": "chat_completions",
        "thinking_level": "off",
        "system_prompt": "",
        "prompt": "Start the turn.",
        "images": [],
        "model_runtime": _descriptor(),
        "task_contract": {"schema_version": "scansci.task-contract.v2", "version": 2, "allowed_tools": []},
    }
    try:
        process.stdin.write(json.dumps(start) + "\n")
        process.stdin.flush()
        assert first_request.wait(timeout=10)
        process.stdin.write(json.dumps({
            "type": "run.steer",
            "request_id": "queued-images",
            "command_id": "steer-image",
            "text": "Inspect this steered image.",
            "images": [_IMAGE],
        }) + "\n")
        process.stdin.write(json.dumps({
            "type": "run.follow_up",
            "request_id": "queued-images",
            "command_id": "follow-image",
            "text": "Then compare this follow-up image.",
            "images": [_IMAGE],
        }) + "\n")
        process.stdin.flush()
        release_first.set()
        assert terminal.wait(timeout=20)
    finally:
        release_first.set()
        process.kill()
        process.wait(timeout=5)
        output_thread.join(timeout=2)
        server.shutdown()
        server_thread.join(timeout=2)
        server.server_close()

    output = "".join(lines)
    assert '"type":"run.steer_ack"' in output
    assert '"type":"run.follow_up_ack"' in output
    assert '"type":"run.completed"' in output
    assert len(QueueShapeHandler.payloads) >= 3
    provider_wire = "\n".join(json.dumps(payload) for payload in QueueShapeHandler.payloads[1:])
    assert provider_wire.count(f"data:image/png;base64,{_PNG}") >= 2
    assert _PNG not in output


def test_100k_token_20_turn_compaction_preserves_all_sentinels_after_resume(tmp_path: Path) -> None:
    sentinel_pattern = re.compile(r"TURN-SENTINEL-\d{2}")

    class CompactionHandler(BaseHTTPRequestHandler):
        payloads: list[dict[str, object]] = []
        compaction_requests = 0

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            type(self).payloads.append(payload)
            wire = json.dumps(payload, ensure_ascii=False)
            sentinels = sorted(set(sentinel_pattern.findall(wire)))
            if "context summarization assistant" in wire:
                type(self).compaction_requests += 1
                content = "durable compaction summary: " + " ".join(sentinels)
            elif "RECALL-ALL-20-SENTINELS" in wire:
                content = "restored sentinels: " + " ".join(sentinels)
            else:
                content = "ack " + (sentinels[-1] if sentinels else "turn")
            chunks = [
                {
                    "id": "chat_compaction",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
                },
                {
                    "id": "chat_compaction",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            body = "".join(f"data: {json.dumps(value)}\n\n" for value in chunks) + "data: [DONE]\n\n"
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    CompactionHandler.payloads = []
    CompactionHandler.compaction_requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), CompactionHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    workspace = tmp_path / "workspace.sqlite"
    evidence_db = tmp_path / "evidence.sqlite"
    descriptor = _descriptor(context_window="200K")
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    common = {
        "provider_kind": "openai-compatible",
        "base_url": base_url,
        "api_key": "fixture-key",
        "model_id": "fixture-model",
        "model_runtime": descriptor,
        "thinking_level": "off",
        "task_mode": "general",
        "timeout_seconds": 60,
        "session_id": "twenty-turn-compaction",
    }
    body = _fixed_high_entropy_ascii("compaction", 8_000)
    # Precomputed with the two local encodings used by the hard gate:
    # cl100k=5,682 and o200k=5,446 tokens per turn.  The smaller count still
    # proves this is a genuine >100K-token 20-turn history.
    assert 20 * 5_446 >= 100_000
    first_client = PiAgentClient(workspace=workspace, evidence_db=evidence_db)
    session_file: Path | None = None
    try:
        for turn in range(20):
            sentinel = f"TURN-SENTINEL-{turn:02d}"
            events = list(
                first_client.stream_chat(
                    messages=[{"role": "user", "content": f"{sentinel}\n{body}"}],
                    task_contract={
                        "schema_version": "scansci.task-contract.v2",
                        "version": 2,
                        "contract_id": "twenty-turn-compaction",
                        "goal": "Preserve every turn sentinel across compaction and resume.",
                        "allowed_tools": [],
                        "initial_tools": [],
                        "risk_level": "read_only",
                    },
                    **common,
                )
            )
            assert events[-1]["type"] == "done"
            if session_file is None:
                session_file = Path(str(next(event for event in events if event["type"] == "session")["session_file"]))
        compacted = first_client.compact(
            "twenty-turn-compaction",
            instructions="Retain every TURN-SENTINEL value exactly.",
            timeout_seconds=60,
        )
        compaction_payload = next(
            payload
            for payload in CompactionHandler.payloads
            if "context summarization assistant" in json.dumps(payload, ensure_ascii=False)
        )
        summarized_sentinels = set(
            sentinel_pattern.findall(json.dumps(compaction_payload, ensure_ascii=False))
        )
        # Pi deliberately keeps the newest turns as raw entries.  The summary
        # must preserve every older sentinel it was asked to compact; the final
        # post-resume assertion below verifies the summary + raw tail as a whole.
        assert 0 < len(summarized_sentinels) < 20
        assert set(sentinel_pattern.findall(str(compacted.get("summary", "")))) == summarized_sentinels
    finally:
        first_client.close()

    second_client = PiAgentClient(workspace=workspace, evidence_db=evidence_db)
    try:
        restored = list(
            second_client.stream_chat(
                messages=[{"role": "user", "content": "RECALL-ALL-20-SENTINELS"}],
                task_contract={
                    "schema_version": "scansci.task-contract.v2",
                    "version": 2,
                    "contract_id": "twenty-turn-compaction",
                    "goal": "Return every preserved turn sentinel.",
                    "allowed_tools": [],
                    "initial_tools": [],
                    "risk_level": "read_only",
                },
                **common,
            )
        )
    finally:
        second_client.close()
        server.shutdown()
        server_thread.join(timeout=2)
        server.server_close()

    assert session_file is not None and session_file.is_file()
    records = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("type") == "compaction" for record in records)
    assert CompactionHandler.compaction_requests >= 1
    restored_text = "".join(str(event.get("content", "")) for event in restored if event.get("type") == "delta")
    assert set(sentinel_pattern.findall(restored_text)) == {
        f"TURN-SENTINEL-{turn:02d}" for turn in range(20)
    }
