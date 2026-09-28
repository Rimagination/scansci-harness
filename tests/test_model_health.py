from scansci_html.model_health import build_model_health, model_health_key


def test_model_health_separates_configured_remote_from_ready_local_models():
    settings = {
        "providers": [
            {
                "id": "cloud",
                "kind": "openai-compatible",
                "enabled": True,
                "api_key_configured": True,
                "models": [{"id": "cloud-chat", "capabilities": ["reasoning"]}],
            },
            {
                "id": "local-huggingface",
                "kind": "local",
                "enabled": True,
                "models": [{"id": "Qwen/chat", "capabilities": ["reasoning"]}],
            },
            {
                "id": "local-runtime-chat",
                "kind": "openai-compatible",
                "auth_mode": "local",
                "enabled": True,
                "local_model_id": "lm-studio",
                "models": [{"id": "local-chat", "capabilities": ["reasoning"]}],
            },
        ],
        "local_models": [
            {
                "id": "lm-studio",
                "runtime": "lm-studio",
                "base_url": "http://127.0.0.1:1234/v1",
                "model_id": "local-chat",
                "enabled": True,
                "capabilities": ["reasoning"],
            }
        ],
    }

    health = build_model_health(
        settings,
        installed_models=[{"id": "Qwen/chat", "ready": True, "runtime_compatible": True}],
        runtime_checks={"lm-studio": {"ok": True, "models": ["local-chat"]}},
    )

    assert health["providers"]["cloud"]["status"] == "configured"
    assert health["models"][model_health_key("cloud", "cloud-chat")]["status"] == "configured"
    assert health["providers"]["local-huggingface"]["status"] == "ready"
    assert health["models"][model_health_key("local-huggingface", "Qwen/chat")]["status"] == "ready"
    assert health["providers"]["local-runtime-chat"]["status"] == "ready"
    assert health["models"][model_health_key("local-runtime-chat", "local-chat")]["status"] == "ready"


def test_model_health_marks_missing_and_incompatible_local_models_unavailable():
    settings = {
        "providers": [
            {
                "id": "local-huggingface",
                "kind": "local",
                "enabled": True,
                "models": [
                    {"id": "missing", "capabilities": ["reasoning"]},
                    {
                        "id": "broken",
                        "capabilities": ["vision"],
                        "available": False,
                        "unavailable_reason": "GPU 显存不足，当前显存约 8.0 GB。",
                    },
                ],
            }
        ],
        "local_models": [],
    }

    health = build_model_health(
        settings,
        installed_models=[{"id": "broken", "ready": True, "runtime_compatible": False, "runtime_message": "格式不兼容"}],
    )

    assert health["providers"]["local-huggingface"]["status"] == "unavailable"
    assert health["models"][model_health_key("local-huggingface", "missing")]["status"] == "not_installed"
    assert health["models"][model_health_key("local-huggingface", "broken")]["status"] == "incompatible"
    assert "显存不足" in health["models"][model_health_key("local-huggingface", "broken")]["detail"]
