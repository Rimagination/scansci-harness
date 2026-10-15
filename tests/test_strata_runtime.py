"""Contract checks for the optional Strata loopback route."""

from __future__ import annotations

import pytest

from scansci_html.app_settings import (
    SettingsError, load_settings, local_model_presets, provider_request_api_key,
    save_settings, with_discovered_local_runtime_models,
)
from scansci_html.model_health import build_model_health
from scansci_html import research_tools


def test_strata_preset_is_keyless_and_rejects_remote_routes(tmp_path):
    preset = next(item for item in local_model_presets() if item["id"] == "strata")
    assert preset["enabled"] is False
    assert preset["base_url"] == "http://127.0.0.1:8080/v1"

    settings = {"providers": [], "local_models": [{**preset, "enabled": True, "model_id": "strata"}]}
    catalog = with_discovered_local_runtime_models(settings, {})
    provider = next(item for item in catalog["providers"] if item["id"] == "local-runtime-strata")
    with pytest.raises(SettingsError, match="未通过 Agent 验证"):
        provider_request_api_key(tmp_path, provider)
    configured = load_settings(tmp_path)
    configured["local_models"].append({**preset, "enabled": True, "model_id": "strata", "agent_verified_for": "http://127.0.0.1:8080/v1\nstrata"})
    save_settings(tmp_path, configured)
    assert provider_request_api_key(tmp_path, provider) == "local"
    assert provider["auth_mode"] == "local"

    remote = {"providers": [], "local_models": [{**preset, "enabled": True, "base_url": "https://example.org/v1", "model_id": "strata"}]}
    assert with_discovered_local_runtime_models(remote, {})["providers"] == []
    with pytest.raises(ValueError, match="本机 HTTP"):
        research_tools.test_local_model_connection(remote["local_models"][0])


def test_strata_health_and_agent_probe_require_streamed_evidence_and_tool_call(monkeypatch):
    seen = []

    def fake_json(url, *, timeout, **kwargs):
        seen.append(url)
        if url.endswith("/health"):
            return {"status": "ok", "loaded": True, "max_context": 32768}
        if url.endswith("/models"):
            return {"data": [{"id": "strata"}]}
        if url.endswith("/chat/completions"):
            return {"choices": [{"message": {"tool_calls": [{"function": {"name": "lookup_evidence", "arguments": '{"id":"E1"}'}}]}}]}
        raise AssertionError(url)

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"12 "}}]}\n'
            yield b'data: {"choices":[{"delta":{"content":"[E1]"}}]}\n'
            yield b"data: [DONE]\n"

    monkeypatch.setattr(research_tools, "_request_json", fake_json)
    monkeypatch.setattr(research_tools.request, "urlopen", lambda *_args, **_kwargs: FakeStream())
    status = research_tools.test_local_model_connection({"runtime": "strata", "base_url": "http://127.0.0.1:8080/v1"})
    probe = research_tools.probe_strata_agent_model("http://127.0.0.1:8080/v1", "strata")

    assert status["models"] == ["strata"]
    assert status["max_context"] == 32768
    assert probe == {"model_id": "strata", "stream_verified": True, "citation_verified": True, "tool_call_verified": True}
    assert seen == ["http://127.0.0.1:8080/health", "http://127.0.0.1:8080/v1/models", "http://127.0.0.1:8080/v1/chat/completions"]
    monkeypatch.setattr(research_tools, "_request_json", lambda *_args, **_kwargs: {
        "choices": [{"message": {"tool_calls": [{"function": {"name": "lookup_evidence", "arguments": "{}"}}]}}],
    })
    with pytest.raises(RuntimeError, match="工具调用预检未通过"):
        research_tools.probe_strata_agent_model("http://127.0.0.1:8080/v1", "strata")


def test_strata_connection_timeout_has_a_local_actionable_reason(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise RuntimeError("无法连接远程服务：timed out")

    monkeypatch.setattr(research_tools, "_request_json", timeout)
    status = research_tools.test_local_model_connection({"runtime": "strata", "base_url": "http://127.0.0.1:8080/v1"})
    assert status["ok"] is False
    assert "Strata 本机连接超时" in status["error"]
    assert "远程服务" not in status["error"]


def test_strata_health_only_selects_the_exact_verified_route():
    local = {
        "id": "strata", "runtime": "strata", "base_url": "http://127.0.0.1:8080/v1",
        "model_id": "strata", "enabled": True, "capabilities": ["reasoning", "tool"],
    }
    settings = with_discovered_local_runtime_models({"providers": [], "local_models": [local]}, {})
    check = {"strata": {"ok": True, "models": ["strata"]}}
    health = build_model_health(settings, runtime_checks=check)
    assert health["models"]["local-runtime-strata::strata"]["status"] == "unverified"

    local["agent_verified_for"] = "http://127.0.0.1:8080/v1\nstrata"
    settings = with_discovered_local_runtime_models({"providers": [], "local_models": [local]}, {})
    health = build_model_health(settings, runtime_checks=check)
    assert health["models"]["local-runtime-strata::strata"]["status"] == "ready"

    local["base_url"] = "http://127.0.0.1:8081/v1"
    settings = with_discovered_local_runtime_models({"providers": [], "local_models": [local]}, {})
    health = build_model_health(settings, runtime_checks=check)
    assert health["models"]["local-runtime-strata::strata"]["status"] == "unverified"


def test_strata_route_change_requires_verification_and_removal_clears_roles(tmp_path):
    settings = load_settings(tmp_path)
    preset = next(item for item in local_model_presets() if item["id"] == "strata")
    settings["local_models"].append({
        **preset, "enabled": True, "model_id": "strata",
        "agent_verified_for": "http://127.0.0.1:8080/v1\nstrata",
    })
    settings["local_models"].append({
        "id": "ollama-check", "name": "Ollama Check", "runtime": "ollama",
        "base_url": "http://127.0.0.1:11434/v1", "model_id": "qwen3:8b", "enabled": True,
    })
    settings["active_model"] = {"provider_id": "local-runtime-strata", "model_id": "strata"}
    settings["model_roles"]["reasoning"] = "provider:local-runtime-ollama-check:qwen3:8b"
    settings["model_roles"]["writing"] = "provider:local-runtime-strata:strata"
    save_settings(tmp_path, settings)

    candidate = load_settings(tmp_path)
    next(item for item in candidate["local_models"] if item["id"] == "strata")["base_url"] = "http://127.0.0.1:8081/v1"
    with pytest.raises(SettingsError, match="验证后切换"):
        save_settings(tmp_path, candidate)
    assert load_settings(tmp_path)["active_model"] == {"provider_id": "local-runtime-strata", "model_id": "strata"}

    candidate["local_models"] = [item for item in candidate["local_models"] if item["id"] != "strata"]
    removed = save_settings(tmp_path, candidate)
    assert removed["active_model"]["provider_id"] != "local-runtime-strata"
    assert removed["model_roles"]["writing"] == ""
    assert removed["model_roles"]["reasoning"] == "provider:local-runtime-ollama-check:qwen3:8b"
