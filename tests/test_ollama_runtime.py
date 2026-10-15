from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scansci_html import ollama_runtime


def test_ollama_status_recognizes_the_exact_minicpm_tag(monkeypatch):
    def fake_request(_base, path, **_kwargs):
        if path == "/api/version":
            return {"version": "0.11.0"}
        return {"models": [{"name": "minicpm-v4.6:latest"}]}

    monkeypatch.setattr(ollama_runtime, "_request_json", fake_request)
    status = ollama_runtime.ollama_status()

    assert status["reachable"] is True
    assert status["model_ready"] is True
    assert status["model_id"] == "minicpm-v4.6"


def test_ollama_status_checks_the_requested_text_model(monkeypatch):
    def fake_request(_base, path, **_kwargs):
        if path == "/api/version":
            return {"version": "0.33.3"}
        return {"models": [{"name": "qwen3.5:4b"}, {"name": "minicpm-v4.6:latest"}]}

    monkeypatch.setattr(ollama_runtime, "_request_json", fake_request)

    qwen = ollama_runtime.ollama_status(model_id="qwen3.5:4b")
    missing = ollama_runtime.ollama_status(model_id="qwen3.8:4b")

    assert qwen["model_ready"] is True
    assert qwen["model_id"] == "qwen3.5:4b"
    assert missing["model_ready"] is False


def test_ollama_status_reports_server_error_instead_of_claiming_service_is_stopped(monkeypatch):
    def fake_request(_base, path, **_kwargs):
        if path == "/api/version":
            return {"version": "0.35.1"}
        raise HTTPError(
            "http://127.0.0.1:11434/api/tags", 500, "Internal Server Error", {},
            BytesIO(b'{"error":"mkdir Z:\\\\MissingModelDir: path not found"}'),
        )

    monkeypatch.setattr(ollama_runtime, "_request_json", fake_request)

    status = ollama_runtime.ollama_status()

    assert status["reachable"] is False
    assert "HTTP 500" in status["error"]
    assert "Z:\\MissingModelDir" in status["error"]
    assert "未运行" not in status["error"]


def test_warm_ollama_chat_model_only_loads_a_cold_model(monkeypatch):
    calls = []
    loaded = False

    def fake_request(_base, path, **kwargs):
        nonlocal loaded
        calls.append((path, kwargs))
        if path == "/api/ps":
            return {"models": [{"name": "qwen3.5:4b"}]} if loaded else {"models": []}
        loaded = True
        return {"done_reason": "load"}

    monkeypatch.setattr(ollama_runtime, "_request_json", fake_request)

    events = list(ollama_runtime.warm_ollama_chat_model("http://127.0.0.1:11434/v1", "qwen3.5:4b"))
    assert [event["status"] for event in events] == ["local_model_loading", "local_model_ready"]
    assert calls[1] == (
        "/api/chat",
        {"method": "POST", "payload": {"model": "qwen3.5:4b", "messages": [], "stream": False, "keep_alive": "5m"}, "timeout": 120.0},
    )
    assert list(ollama_runtime.warm_ollama_chat_model("http://127.0.0.1:11434/v1", "qwen3.5:4b")) == []
    assert [path for path, _kwargs in calls] == ["/api/ps", "/api/chat", "/api/ps"]


def test_warm_ollama_chat_model_reports_the_native_load_failure(monkeypatch):
    def fake_request(_base, path, **_kwargs):
        if path == "/api/ps":
            return {"models": []}
        raise HTTPError(
            "http://127.0.0.1:11434/api/chat", 500, "Internal Server Error", {},
            BytesIO(b'{"error":"model directory unavailable"}'),
        )

    monkeypatch.setattr(ollama_runtime, "_request_json", fake_request)
    events = iter(ollama_runtime.warm_ollama_chat_model("http://127.0.0.1:11434/v1", "qwen3.5:4b"))

    assert next(events)["status"] == "local_model_loading"
    with pytest.raises(RuntimeError, match="HTTP 500.*model directory unavailable"):
        next(events)


def test_ollama_install_manager_reuses_an_already_pulled_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        ollama_runtime,
        "ollama_status",
        lambda _base, **_kwargs: {"reachable": True, "model_ready": True, "error": ""},
    )
    completed = []
    manager = ollama_runtime.OllamaInstallManager(state_path=tmp_path / "jobs.json")
    job = manager.start(on_complete=completed.append)

    assert job["state"] == "ready"
    assert job["progress"] == 1.0
    assert completed and completed[0]["current_model"] == "minicpm-v4.6"


def test_ollama_install_manager_resumes_the_original_chat_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        ollama_runtime,
        "ollama_status",
        lambda _base, *, model_id: {"reachable": True, "model_ready": model_id == "qwen3.5:9b", "error": ""},
    )
    manager = ollama_runtime.OllamaInstallManager(state_path=tmp_path / "jobs.json")
    manager._jobs["ollama:qwen3.5:9b"] = {"job_id": "ollama:qwen3.5:9b", "current_model": "qwen3.5:9b", "state": "paused"}

    job = manager.resume("ollama:qwen3.5:9b")

    assert job["state"] == "ready"
    assert job["current_model"] == "qwen3.5:9b"


def test_ollama_chat_probe_checks_citation_and_tool_call(monkeypatch):
    monkeypatch.setattr(ollama_runtime, "ollama_status", lambda *_args, **_kwargs: {"model_ready": True})
    replies = iter([
        {"choices": [{"message": {"content": "12 株乔木 [E1]"}}], "usage": {"completion_tokens": 24}},
        {"choices": [{"message": {"tool_calls": [{"function": {"name": "lookup_evidence", "arguments": '{"id":"E1"}'}}]}}]},
        {"message": {"content": "样地 A 有 12 株乔木 [E1]"}},
    ])
    requests = []

    def fake_request(_base, path, **kwargs):
        requests.append((path, kwargs["payload"]))
        return next(replies)

    monkeypatch.setattr(ollama_runtime, "_request_json", fake_request)

    result = ollama_runtime.probe_ollama_chat_model("http://127.0.0.1:11434", "qwen3.5:9b")

    assert result["citation_verified"] is True
    assert result["tool_call_verified"] is True
    assert result["pi_context_verified"] is True
    assert result["completion_tokens"] == 24
    assert [path for path, _payload in requests] == ["/v1/chat/completions"] * 2 + ["/api/chat"]
    assert all(payload["reasoning_effort"] == "none" for _path, payload in requests[:2])
    assert requests[2][1]["options"]["num_ctx"] == 16384
    assert requests[2][1]["think"] is False


def test_local_qwen_uses_ollama_supported_thinking_parameter():
    assert ollama_runtime.chat_thinking_parameters("http://127.0.0.1:11434/v1", "qwen3.5:9b", None) == {
        "reasoning_effort": "none",
    }
    assert ollama_runtime.chat_thinking_parameters("http://127.0.0.1:11434/v1", "qwen3.5:9b", "enabled") == {
        "reasoning_effort": "medium",
    }
    assert ollama_runtime.chat_thinking_parameters("http://localhost:11555/v1", "qwen3.5:9b", None) == {
        "reasoning_effort": "none",
    }
    assert ollama_runtime.chat_thinking_parameters("https://example.com/v1", "qwen3.5:9b", "disabled") == {
        "thinking": {"type": "disabled"},
    }


def test_ollama_install_manager_marks_previous_stream_as_interrupted(tmp_path: Path):
    state_path = tmp_path / "jobs.json"
    state_path.write_text(
        '{"jobs":[{"job_id":"ollama:minicpm-v4.6","state":"downloading","progress":0.4}]}'
    )

    manager = ollama_runtime.OllamaInstallManager(state_path=state_path)

    job = manager.status("ollama:minicpm-v4.6")
    assert job["state"] == "interrupted"
    assert "继续下载" in job["message"]
