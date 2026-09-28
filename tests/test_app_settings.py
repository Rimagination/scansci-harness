import os
import pytest
import json
from pathlib import Path
import sys

from scansci_html import app_settings
from scansci_html.app_settings import (
    SettingsError,
    configure_managed_glm_4_7_flash,
    get_provider_api_key,
    get_notion_api_token,
    load_settings,
    local_model_presets,
    managed_fallback_model_ids,
    managed_model_ids,
    managed_probe_model_ids,
    provider_presets,
    save_settings,
    set_notion_api_token,
    set_provider_api_key,
    settings_path,
    with_discovered_local_runtime_models,
)
from scansci_html.model_metadata import descriptor_from_settings


class FakeProviderKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.store.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.store[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.store.pop((service_name, username), None)


def test_default_settings_use_the_managed_glm_service_without_a_local_key(tmp_path: Path):
    settings = load_settings(tmp_path / "workspace.sqlite")

    assert settings["active_model"] == {"provider_id": "scansci-managed", "model_id": "glm-4.7-flash"}
    assert settings["providers"][0]["auth_mode"] == "managed"
    assert settings["providers"][0]["api_key_configured"] is True
    assert {
        model["id"] for model in settings["providers"][0]["models"]
    } == {"glm-4.7-flash"}
    assert settings["local_models"][0]["runtime"] == "builtin"
    assert settings["local_models"][0]["name"] == "离线基础检索（非模型）"
    assert settings["local_models"][0]["model_id"] == "local-hash-v1 / local-lexical-v1"
    offline_provider = next(item for item in settings["providers"] if item["id"] == "local-evidence")
    assert offline_provider["name"] == "离线基础检索（非模型）"
    assert offline_provider["models"][0]["name"] == "关键词检索与词法重排（非模型）"
    assert settings["model_roles"]["retrieval"] == "local:builtin-evidence"
    assert settings["model_roles"]["image"] == "auto"
    assert settings["document_processing"]["ocr"]["provider"] == "tesseract"
    assert settings["document_processing"]["mineru"]["base_url"] == "https://mineru.net"
    assert settings["document_processing"]["mineru"]["api_key_configured"] is False
    assert settings["onboarding"] == {
        "welcome_dismissed": False,
        "resource_setup_completed": False,
        "data_setup_completed": False,
    }
    assert settings["appearance"] == {"locale": "zh-CN", "theme": "system", "accent": "jade", "font_scale": "medium"}
    assert not settings_path(tmp_path / "workspace.sqlite").exists()


def test_notion_token_availability_is_redacted_and_scoped_to_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    backend = FakeProviderKeyring()
    monkeypatch.setitem(sys.modules, "keyring", backend)
    workspace = tmp_path / "desktop" / "workspace.sqlite"
    preview_workspace = tmp_path / "preview" / "workspace.sqlite"

    set_notion_api_token(workspace, "notion-test-secret")

    settings = load_settings(workspace)
    assert settings["notion_api_token_configured"] is True
    assert get_notion_api_token(workspace) == "notion-test-secret"
    assert load_settings(preview_workspace)["notion_api_token_configured"] is False
    assert "notion-test-secret" not in json.dumps(settings)


def test_workspace_profile_distinguishes_temporary_preview_from_persistent_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    temporary_root = tmp_path / "system-temp"
    temporary_root.mkdir()
    monkeypatch.setattr(app_settings.tempfile, "gettempdir", lambda: str(temporary_root))

    assert app_settings._workspace_profile(temporary_root / "preview.sqlite") == "temporary"
    assert app_settings._workspace_profile(tmp_path / "desktop.sqlite") == "persistent"


def test_model_provider_credentials_prefer_environment_and_report_redacted_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    backend = FakeProviderKeyring()
    monkeypatch.setitem(sys.modules, "keyring", backend)
    workspace = tmp_path / "workspace.sqlite"

    set_provider_api_key(workspace, "deepseek", "keyring-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")

    assert get_provider_api_key(workspace, "deepseek") == "environment-secret"
    provider = next(item for item in load_settings(workspace)["providers"] if item["id"] == "deepseek")
    assert provider["api_key_configured"] is True
    assert provider["api_key_source"] == "environment"
    assert provider["api_key_env"] == "DEEPSEEK_API_KEY"

    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert get_provider_api_key(workspace, "deepseek") == "keyring-secret"
    provider = next(item for item in load_settings(workspace)["providers"] if item["id"] == "deepseek")
    assert provider["api_key_source"] == "keyring"


def test_environment_provider_key_is_read_only_for_settings_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    backend = FakeProviderKeyring()
    monkeypatch.setitem(sys.modules, "keyring", backend)
    workspace = tmp_path / "workspace.sqlite"
    set_provider_api_key(workspace, "deepseek", "stored-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")

    with pytest.raises(SettingsError, match="DEEPSEEK_API_KEY"):
        set_provider_api_key(workspace, "deepseek", "replacement-secret")
    with pytest.raises(SettingsError, match="DEEPSEEK_API_KEY"):
        set_provider_api_key(workspace, "deepseek", "")

    assert backend.store
    assert get_provider_api_key(workspace, "deepseek") == "environment-secret"


def test_custom_provider_uses_a_stable_environment_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "keyring", FakeProviderKeyring())
    workspace = tmp_path / "workspace.sqlite"
    save_settings(
        workspace,
        {
            "providers": [
                {
                    "id": "research-gateway",
                    "name": "Research gateway",
                    "kind": "openai-compatible",
                    "base_url": "https://example.invalid/v1",
                    "models": [{"id": "small", "name": "Small"}],
                }
            ],
            "active_model": {"provider_id": "research-gateway", "model_id": "small"},
        },
    )
    monkeypatch.setenv("RESEARCH_GATEWAY_API_KEY", "environment-secret")

    assert get_provider_api_key(workspace, "research-gateway") == "environment-secret"
    provider = next(item for item in load_settings(workspace)["providers"] if item["id"] == "research-gateway")
    assert provider["api_key_source"] == "environment"
    assert provider["api_key_env"] == "RESEARCH_GATEWAY_API_KEY"


def test_managed_model_catalog_separates_routes_from_approved_fallbacks():
    assert managed_model_ids() == ("glm-4.7-flash",)
    assert managed_probe_model_ids() == ("glm-4.7-flash",)
    assert managed_fallback_model_ids("glm-4.7-flash") == ()


def test_runtime_inventory_adds_new_models_and_deduplicates_latest_alias():
    settings = {
        "providers": [{"id": "cloud", "models": []}],
        "local_models": [
            {
                "id": "ollama-qwen",
                "name": "Qwen3.5 4B",
                "runtime": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "model_id": "qwen3.5:4b",
                "enabled": True,
                "capabilities": ["reasoning", "tool"],
            }
        ],
    }

    catalog = with_discovered_local_runtime_models(
        settings,
        {
            "ollama-qwen": [
                {"name": "qwen3.5:4b"},
                {"name": "qwen3.8-4b-distill:latest"},
            ]
        },
    )

    assert [item["model_id"] for item in catalog["local_models"]] == [
        "qwen3.5:4b",
        "qwen3.8-4b-distill:latest",
    ]
    discovered = next(item for item in catalog["local_models"] if item["model_id"].startswith("qwen3.8"))
    assert discovered["capabilities"] == ["reasoning"]
    provider = next(item for item in catalog["providers"] if item.get("local_model_id") == discovered["id"])
    assert provider["models"][0]["id"] == "qwen3.8-4b-distill:latest"


@pytest.mark.skip(reason="Qwen removed; WIP")
def test_managed_standby_route_is_visible_but_cannot_be_auto_selected(tmp_path: Path):
    settings = load_settings(tmp_path / "workspace.sqlite")
    managed = next(provider for provider in settings["providers"] if provider["id"] == "scansci-managed")
    qwen = next(model for model in managed["models"] if model["id"] == "Qwen/Qwen2.5-7B-Instruct")

    assert qwen["readiness"] == "standby"
    assert qwen["automatic_fallback"] is False
    assert "待质量验证" in qwen["name"]


def test_existing_workspaces_receive_new_builtin_slide_skills(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings_path(workspace).write_text(
        json.dumps({"skills": [{"id": "literature-search", "name": "Literature search", "enabled": False}]}),
        encoding="utf-8",
    )

    settings = load_settings(workspace)
    skills = {item["id"]: item for item in settings["skills"]}

    assert skills["literature-search"]["enabled"] is False
    assert skills["literature-search"]["name"] == "literature-search"
    assert {"good-question", "good-story", "scientific-slides"} <= set(skills)
    assert skills["good-question"]["source"] == "Rimagination/good-question · MIT"


def test_existing_workspaces_migrate_zotero_from_skill_to_plugin(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings_path(workspace).write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": "good-story",
                        "name": "好故事",
                        "path": "builtin:good-story",
                        "source_type": "builtin",
                        "enabled": False,
                    },
                    {
                        "id": "zotero",
                        "name": "Zotero",
                        "path": "builtin:zotero",
                        "source_type": "builtin",
                        "enabled": True,
                    },
                ],
                "plugins": [],
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(workspace)
    skills = {item["id"]: item for item in settings["skills"]}
    plugins = {item["id"]: item for item in settings["plugins"]}

    assert skills["good-story"]["name"] == "good-story"
    assert skills["good-story"]["enabled"] is False
    assert "zotero" not in skills
    assert plugins["zotero"]["name"] == "Zotero"
    assert plugins["zotero"]["icon"] == "zotero"


def test_default_settings_include_disabled_common_provider_catalog(tmp_path: Path):
    settings = load_settings(tmp_path / "workspace.sqlite")
    providers = {item["id"]: item for item in settings["providers"]}
    visible_provider_ids = [item["id"] for item in settings["providers"] if item["id"] != "local-evidence"]

    assert {"scansci-managed", "openai", "anthropic", "gemini", "deepseek", "dashscope", "zai", "moonshot", "minimax", "openrouter", "opencode-go", "siliconflow"} <= set(providers)
    assert providers["scansci-managed"]["enabled"] is True
    assert visible_provider_ids[:2] == ["scansci-managed", "jev"]
    assert providers["jev"]["logo"] == "typesafe"
    assert providers["jev"]["enabled"] is False
    assert providers["jev"]["model_listing"] is False
    assert providers["jev"]["base_url"] == "https://api.typesafe.ai/v1/systemone"
    assert providers["jev"]["api_surface"] == "jev_systemone"
    assert providers["jev"]["api_key_env"] == "TYPESAFE_API_KEY"
    assert providers["openai"]["enabled"] is False
    assert providers["zai"]["base_url"] == "https://api.z.ai/api/paas/v4"
    assert providers["opencode-go"]["enabled"] is False
    assert providers["opencode-go"]["base_url"] == "https://opencode.ai/zen/go/v1"
    assert providers["opencode-go"]["api_surface"] == "auto"
    assert len(providers["opencode-go"]["models"]) == 26
    assert "vision" in next(model for model in providers["opencode-go"]["models"] if model["id"] == "deepseek-v4-flash-vision-exp")["capabilities"]


def test_jev_is_kept_out_of_the_conversation_model_route(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = load_settings(workspace)
    settings["active_model"] = {"provider_id": "jev", "model_id": "jev-decision-layer"}

    save_settings(workspace, settings)
    restored = load_settings(workspace)

    assert restored["active_model"] == {"provider_id": "scansci-managed", "model_id": "glm-4.7-flash"}


def test_saved_settings_keep_models_and_never_write_api_key(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "active_model": {"provider_id": "custom", "model_id": "small"},
            "providers": [
                {
                    "id": "custom",
                    "name": "My provider",
                    "kind": "openai-compatible",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "must-not-be-written",
                    "models": [{"id": "small", "name": "Small", "context_window": "32k"}],
                }
            ],
            "skills": [],
            "mcp_servers": [{"id": "papers", "name": "Papers", "command": "node server.mjs", "args": "--stdio", "deferred": True}],
            "plugins": [{"id": "ref-manager", "name": "Reference manager", "source": "local"}],
        },
    )

    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    assert settings["active_model"] == {"provider_id": "custom", "model_id": "small"}
    assert settings["providers"][0]["api_key_configured"] is False
    assert persisted["providers"][0]["base_url"] == "https://example.invalid/v1"
    assert "api_key" not in persisted["providers"][0]
    assert "api_key_source" not in persisted["providers"][0]
    assert "api_key_env" not in persisted["providers"][0]
    assert persisted["mcp_servers"][0]["command"] == "node server.mjs"
    assert persisted["mcp_servers"][0]["deferred"] is True


def test_numeric_context_window_round_trips_through_provider_settings(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "active_model": {"provider_id": "custom", "model_id": "numeric-window"},
            "providers": [{
                "id": "custom",
                "name": "Numeric provider",
                "kind": "openai-compatible",
                "base_url": "https://example.invalid/v1",
                "models": [{
                    "id": "numeric-window",
                    "name": "Numeric window",
                    "context_window": 32_768,
                    "capabilities": ["reasoning", "tool"],
                }],
            }],
        },
    )

    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    assert persisted["providers"][0]["models"][0]["context_window"] == "32768"
    descriptor = descriptor_from_settings(settings, provider_id="custom", model_id="numeric-window")
    assert descriptor.context_window_tokens == 32_768
    assert descriptor.degraded is False


def test_mcp_host_owned_tool_policies_round_trip_with_bounded_values(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    policies = [
        {"name": "lookup_records", "effect": "read", "idempotent": True, "freshness": "run"},
        {"tool": "notes.put", "effect": "reversible", "idempotent": False, "freshness": "volatile"},
        {"name": "invalid", "effect": "execute", "idempotent": True, "freshness": "forever"},
        {"name": "x" * 161, "effect": "read", "idempotent": True},
        *({"name": f"bounded_{index}", "effect": "write"} for index in range(80)),
    ]

    saved = save_settings(workspace, {
        "mcp_servers": [{
            "id": "policy-fixture",
            "name": "Policy fixture",
            "command": "node fixture.mjs",
            "tool_effects": {
                "search/library": "read",
                "notes.put": "reversible",
                "accounts.delete": "high",
                "invalid": "execute",
                "y" * 161: "read",
            },
            "tool_policies": policies,
        }],
    })
    loaded = load_settings(workspace)
    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))

    expected_effects = {
        "search/library": "read",
        "notes.put": "reversible",
        "accounts.delete": "high",
    }
    for payload in (saved, loaded, persisted):
        server = payload["mcp_servers"][0]
        assert server["tool_effects"] == expected_effects
        assert server["tool_policies"][:2] == [
            {"name": "lookup_records", "effect": "read", "idempotent": True, "freshness": "run"},
            {"name": "notes.put", "effect": "reversible", "idempotent": False, "freshness": "volatile"},
        ]
        assert len(server["tool_policies"]) == 64
        assert all(policy["effect"] in {"read", "reversible", "write", "high"} for policy in server["tool_policies"])
        assert all(len(policy["name"]) <= 160 for policy in server["tool_policies"])


def test_onboarding_settings_persist_for_later_resume(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"

    settings = save_settings(
        workspace,
        {"onboarding": {"welcome_dismissed": True, "resource_setup_completed": True, "data_setup_completed": True}},
    )

    assert settings["onboarding"] == {
        "welcome_dismissed": True,
        "resource_setup_completed": True,
        "data_setup_completed": True,
    }
    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    assert persisted["onboarding"] == {
        "welcome_dismissed": True,
        "resource_setup_completed": True,
        "data_setup_completed": True,
    }


def test_appearance_preferences_are_persisted_and_normalized(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"

    settings = save_settings(
        workspace,
        {"appearance": {"locale": "en", "theme": "dark", "accent": "ocean"}},
    )

    assert settings["appearance"] == {"locale": "en", "theme": "dark", "accent": "ocean", "font_scale": "medium"}
    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    assert persisted["appearance"] == {"locale": "en", "theme": "dark", "accent": "ocean", "font_scale": "medium"}

    normalized = save_settings(workspace, {"appearance": {"locale": "fr", "theme": "neon", "accent": "red"}})
    assert normalized["appearance"] == {"locale": "zh-CN", "theme": "system", "accent": "jade", "font_scale": "medium"}


def test_general_preferences_are_persisted_and_normalized(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"

    settings = save_settings(
        workspace,
        {
            "general": {
                "conversation": {
                    "send_shortcut": "shift-enter",
                    "completion_notifications": False,
                    "agent_completion_notifications": False,
                    "subagent_completion_notifications": True,
                },
                "directories": {
                    "default_workspace": r"D:\Research\default",
                    "conversation_workspace": r"D:\Research\conversations",
                    "model_cache": r"D:\ScanSci\models",
                    "local_runtime": r"D:\ScanSci\runtime",
                    "vector_index": r"D:\ScanSci\vectors",
                },
                "updates": {"auto_check": False},
            }
        },
    )

    assert settings["general"] == {
        "conversation": {
            "send_shortcut": "shift-enter",
            "completion_notifications": False,
            "agent_completion_notifications": False,
            "subagent_completion_notifications": True,
        },
        "directories": {
            "default_workspace": r"D:\Research\default",
            "conversation_workspace": r"D:\Research\conversations",
            "model_cache": r"D:\ScanSci\models",
            "local_runtime": r"D:\ScanSci\runtime",
            "vector_index": r"D:\ScanSci\vectors",
        },
        "updates": {"auto_check": False},
    }
    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    assert persisted["general"] == settings["general"]

    normalized = save_settings(
        workspace,
        {
            "general": {
                "conversation": {"send_shortcut": "ctrl-enter", "completion_notifications": "yes"},
                "directories": {"default_workspace": 123},
            }
        },
    )
    assert normalized["general"] == {
        "conversation": {
            "send_shortcut": "enter",
            "completion_notifications": True,
            "agent_completion_notifications": True,
            "subagent_completion_notifications": False,
        },
        "directories": {
            "default_workspace": "123",
            "conversation_workspace": "",
            "model_cache": "",
            "local_runtime": "",
            "vector_index": "",
        },
        "updates": {"auto_check": True},
    }


def test_storage_directories_override_process_defaults_and_restore_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    model_cache = tmp_path / "models"
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("HF_HUB_CACHE", "C:/original-hub")
    monkeypatch.setenv("SCANSCI_LOCAL_RUNTIME_ROOT", "C:/original-runtime")

    app_settings.apply_storage_directories(
        {
            "general": {
                "directories": {
                    "model_cache": str(model_cache),
                    "local_runtime": str(runtime_root),
                }
            }
        }
    )
    assert Path(os.environ["HF_HUB_CACHE"]) == model_cache.resolve()
    assert Path(os.environ["SCANSCI_LOCAL_RUNTIME_ROOT"]) == runtime_root.resolve()

    app_settings.apply_storage_directories({"general": {"directories": {}}})
    assert os.environ["HF_HUB_CACHE"] == "C:/original-hub"
    assert os.environ["SCANSCI_LOCAL_RUNTIME_ROOT"] == "C:/original-runtime"


def test_provider_and_local_runtime_presets_are_credential_free():
    providers = provider_presets()
    runtimes = local_model_presets()

    assert {item["id"] for item in providers} >= {"openai", "anthropic", "gemini", "deepseek", "dashscope", "openrouter", "zai", "moonshot", "minimax", "siliconflow", "paratera"}
    assert all("api_key" not in item for item in providers)
    assert {item["runtime"] for item in runtimes} == {"ollama", "lm-studio", "llama.cpp", "local-huggingface"}


def test_ollama_minicpm_preset_is_optional_when_transformers_is_the_default_local_route():
    runtimes = {item["id"]: item for item in local_model_presets()}

    assert runtimes["ollama-minicpm-v4.6"]["enabled"] is False


def test_provider_catalog_includes_cherry_documented_and_desktop_services():
    providers = {item["id"]: item for item in provider_presets()}

    expected = {
        "cherryai", "cherryin", "openai", "gemini", "vertex-ai", "new-api", "one-api",
        "github-copilot", "minimax", "modelscope", "ppio", "dashscope", "siliconflow",
        "volcengine", "huawei-cloud", "infinigence", "qiniu-ai", "xiaomi-mimo", "zhipu",
        "nvidia", "modal", "aihubmix", "ocoolai", "zai", "deepseek", "alaya", "dmxapi",
        "aionly", "burncloud",
    }

    assert expected <= set(providers)
    assert all(item["category"] and item["summary"] for item in providers.values())
    assert providers["cherryai"]["auth_mode"] == "account_or_key"
    assert providers["openai"]["logo"] == "openai"
    assert {"reasoning", "vision", "tool", "coding"} <= set(providers["openai"]["models"][0]["capabilities"])
    assert providers["siliconflow"]["models"][-1]["capabilities"] == ["reranking"]


def test_siliconflow_catalog_preloads_qwen3_embedding_and_reranker_8b():
    providers = {item["id"]: item for item in provider_presets()}
    models = {item["id"]: item for item in providers["siliconflow"]["models"]}

    assert models["Qwen/Qwen3-Embedding-8B"]["capabilities"] == ["embedding"]
    assert models["Qwen/Qwen3-Reranker-8B"]["capabilities"] == ["reranking"]


def test_existing_siliconflow_provider_receives_new_catalog_models(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings_path(workspace).write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "siliconflow",
                        "name": "硅基流动",
                        "kind": "openai-compatible",
                        "base_url": "https://api.siliconflow.cn/v1",
                        "enabled": False,
                        "models": [
                            {
                                "id": "deepseek-ai/DeepSeek-V3",
                                "name": "DeepSeek V3",
                                "capabilities": ["reasoning"],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(workspace)
    provider = next(item for item in settings["providers"] if item["id"] == "siliconflow")
    model_ids = {item["id"] for item in provider["models"]}

    assert "Qwen/Qwen3-Embedding-8B" in model_ids
    assert "Qwen/Qwen3-Reranker-8B" in model_ids


def test_zhipu_catalog_includes_glm_4_7_flash():
    providers = {item["id"]: item for item in provider_presets()}
    model = next(item for item in providers["zhipu"]["models"] if item["id"] == "glm-4.7-flash")

    assert model["name"] == "GLM-4.7 Flash（免费）"
    assert model["context_window"] == "200K"
    assert model["capabilities"] == ["reasoning", "tool", "coding"]
    assert providers["zhipu"]["standard_base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert providers["zhipu"]["coding_plan_base_url"] == "https://open.bigmodel.cn/api/coding/paas/v4"


def test_zhipu_coding_plan_endpoint_switch_round_trips(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = load_settings(workspace)
    provider = next(item for item in settings["providers"] if item["id"] == "zhipu")
    provider.update(
        {
            "standard_base_url": "https://example.invalid/standard",
            "coding_plan_base_url": "https://example.invalid/coding",
            "coding_plan_enabled": True,
            "base_url": "https://example.invalid/coding",
        }
    )

    save_settings(workspace, settings)
    restored = load_settings(workspace)
    provider = next(item for item in restored["providers"] if item["id"] == "zhipu")

    assert provider["coding_plan_enabled"] is True
    assert provider["standard_base_url"] == "https://example.invalid/standard"
    assert provider["coding_plan_base_url"] == "https://example.invalid/coding"
    assert provider["base_url"] == "https://example.invalid/coding"


def test_paratera_catalog_exposes_cogview_as_image_only():
    provider = next(item for item in provider_presets() if item["id"] == "paratera")
    model = provider["models"][0]

    assert provider["base_url"] == "https://llmapi.paratera.com/v1"
    assert provider["model_listing"] is False
    assert model["id"] == "GLM-CogView3-Flash"
    assert model["capabilities"] == ["image"]


def test_paratera_catalog_adds_wanx_without_overwriting_user_choices(tmp_path):
    workspace = tmp_path / "workspace.sqlite"
    settings = load_settings(workspace)
    provider = next(item for item in settings["providers"] if item["id"] == "paratera")
    wanx = next(item for item in provider["models"] if item["id"] == "WanX2.1-T2I-Plus")
    assert wanx["capabilities"] == ["image"] and wanx["group"] == "通义万相"
    provider["base_url"] = "https://example.org/v1"
    provider["enabled"] = False
    provider["models"] = [{"id": "custom-model", "name": "用户模型", "capabilities": ["image"]},
                          {"id": "GLM-CogView3-Flash", "name": "旧模型", "capabilities": ["image"]}]
    original_active = settings["active_model"].copy()
    original_roles = settings["model_roles"].copy()
    save_settings(workspace, settings)
    restored = load_settings(workspace)
    provider = next(item for item in restored["providers"] if item["id"] == "paratera")
    assert provider["base_url"] == "https://example.org/v1" and provider["enabled"] is False
    assert {m["id"] for m in provider["models"]} == {"custom-model", "GLM-CogView3-Flash", "WanX2.1-T2I-Plus"}
    assert next(m for m in provider["models"] if m["id"] == "GLM-CogView3-Flash")["name"] == "旧模型"
    assert restored["active_model"] == original_active and restored["model_roles"] == original_roles


@pytest.mark.skip(reason="Qwen removed; WIP")
def test_managed_glm_flash_setup_selects_the_scansci_service_without_persisting_a_key(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"

    settings = configure_managed_glm_4_7_flash(workspace)
    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    managed = next(item for item in settings["providers"] if item["id"] == "scansci-managed")

    assert settings["active_model"] == {"provider_id": "scansci-managed", "model_id": "glm-4.7-flash"}
    assert {settings["model_roles"][role] for role in ("reasoning", "writing", "slides")} == {"provider:scansci-managed:glm-4.7-flash"}
    assert settings["model_roles"]["retrieval"] == "local:builtin-evidence"
    assert managed["enabled"] is True
    assert managed["auth_mode"] == "managed"
    assert managed["api_key_configured"] is True
    assert {model["id"] for model in managed["models"]} == {
        "glm-4.7-flash",
        "Qwen/Qwen2.5-7B-Instruct",
    }
    assert "api_key" not in json.dumps(persisted)


@pytest.mark.skip(reason="Qwen removed; WIP")
def test_existing_managed_workspace_migrates_standby_qwen_selection(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "active_model": {
                "provider_id": "scansci-managed",
                "model_id": "Qwen/Qwen2.5-7B-Instruct",
            },
            "model_roles": {
                "reasoning": "provider:scansci-managed:Qwen/Qwen2.5-7B-Instruct",
                "writing": "provider:scansci-managed:Qwen/Qwen2.5-7B-Instruct",
                "slides": "provider:scansci-managed:Qwen/Qwen2.5-7B-Instruct",
            },
            "providers": [{
                "id": "scansci-managed",
                "name": "ScanSciAI",
                "kind": "openai-compatible",
                "base_url": "https://scansci-glm-gateway.932196440.workers.dev/v1",
                "auth_mode": "managed",
                "models": [{
                    "id": "glm-4.7-flash",
                    "name": "GLM-4.7 Flash",
                }],
            }],
        },
    )

    managed = next(item for item in settings["providers"] if item["id"] == "scansci-managed")
    assert settings["active_model"] == {
        "provider_id": "scansci-managed",
        "model_id": "glm-4.7-flash",
    }
    assert {settings["model_roles"][role] for role in ("reasoning", "writing", "slides")} == {
        "provider:scansci-managed:glm-4.7-flash"
    }
    assert {model["id"] for model in managed["models"]} == {
        "glm-4.7-flash",
        "Qwen/Qwen2.5-7B-Instruct",
    }


def test_selected_non_default_model_survives_a_fresh_settings_load(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    save_settings(
        workspace,
        {
            "active_model": {
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-pro",
            },
            "providers": [{
                "id": "deepseek",
                "name": "DeepSeek",
                "kind": "openai-compatible",
                "base_url": "https://api.deepseek.com",
                "enabled": True,
                "models": [{
                    "id": "deepseek-v4-pro",
                    "name": "DeepSeek V4 Pro",
                    "capabilities": ["reasoning", "tool"],
                }],
            }],
        },
    )

    reloaded = load_settings(workspace)

    assert reloaded["active_model"] == {
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-pro",
    }
    managed = next(item for item in reloaded["providers"] if item["id"] == "scansci-managed")
    assert managed["name"] == "ScanSci"


@pytest.mark.skip(reason="Qwen removed; WIP")
def test_legacy_managed_provider_label_migrates_standby_qwen_selection(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings_path(workspace).write_text(
        json.dumps(
            {
                "active_model": {
                    "provider_id": "scansci-managed",
                    "model_id": "Qwen/Qwen2.5-7B-Instruct",
                },
                "providers": [{
                    "id": "scansci-managed",
                    "name": "ScanSciAI",
                    "kind": "openai-compatible",
                    "base_url": "https://old.invalid/v1",
                    "enabled": True,
                    "models": [{
                        "id": "Qwen/Qwen2.5-7B-Instruct",
                        "name": "Qwen2.5 7B Instruct",
                    }],
                }],
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(workspace)
    managed = next(item for item in settings["providers"] if item["id"] == "scansci-managed")

    assert managed["id"] == "scansci-managed"
    assert managed["name"] == "ScanSci"
    assert managed["category"] == "ScanSci"
    assert managed["base_url"] == "https://scansci-glm-gateway.932196440.workers.dev/v1"
    assert managed["models"][0]["id"] == "glm-4.7-flash"
    assert managed["models"][0]["context_window"] == "200K"
    assert managed["models"][1]["id"] == "Qwen/Qwen2.5-7B-Instruct"
    assert managed["models"][1]["context_window"] == "32K"
    assert settings["active_model"] == {
        "provider_id": "scansci-managed",
        "model_id": "glm-4.7-flash",
    }


def test_legacy_settings_without_an_active_model_receive_glm_flash_default(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings_path(workspace).write_text(
        json.dumps({
            "providers": [{
                "id": "custom",
                "name": "Custom",
                "kind": "openai-compatible",
                "enabled": True,
                "models": [{"id": "custom-chat", "name": "Custom Chat"}],
            }],
        }),
        encoding="utf-8",
    )

    settings = load_settings(workspace)

    assert settings["active_model"] == {
        "provider_id": "scansci-managed",
        "model_id": "glm-4.7-flash",
    }
    managed = next(item for item in settings["providers"] if item["id"] == "scansci-managed")
    assert managed["enabled"] is True


def test_model_groups_and_capabilities_are_saved_and_normalized(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "providers": [{
                "id": "custom",
                "name": "Capability test",
                "kind": "openai-compatible",
                "logo": "custom-logo",
                "models": [{
                    "id": "paper-vision-reranker",
                    "name": "Paper vision reranker",
                    "group": "Research",
                    "capabilities": ["vision", "reranking", "not-supported", "vision"],
                }],
            }],
        },
    )

    model = settings["providers"][0]["models"][0]
    assert settings["providers"][0]["logo"] == "custom-logo"
    assert model["group"] == "Research"
    assert model["capabilities"] == ["vision", "reranking"]


def test_model_ids_keep_provider_namespaces(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "active_model": {"provider_id": "openrouter", "model_id": "openai/gpt-5.2"},
            "providers": [
                {
                    "id": "openrouter",
                    "name": "OpenRouter",
                    "kind": "openai-compatible",
                    "base_url": "https://openrouter.ai/api/v1",
                    "models": [{"id": "openai/gpt-5.2", "name": "GPT-5.2"}],
                }
            ],
        },
    )

    assert settings["active_model"]["model_id"] == "openai/gpt-5.2"
    assert settings["providers"][0]["models"][0]["id"] == "openai/gpt-5.2"


def test_document_processing_configuration_is_persisted_without_keys(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "document_processing": {
                "ocr": {
                    "provider": "custom",
                    "base_url": "https://ocr.example.invalid/v1",
                    "languages": ["en", "zh", "en", "unsupported"],
                    "enabled": True,
                    "api_key": "must-not-be-written",
                },
                "mineru": {
                    "provider": "mineru",
                    "base_url": "https://mineru.example.invalid",
                    "enabled": True,
                    "api_key": "must-not-be-written",
                },
            },
        },
    )

    persisted = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    assert settings["document_processing"]["ocr"]["languages"] == ["en", "zh"]
    assert settings["document_processing"]["ocr"]["api_key_configured"] is False
    assert settings["document_processing"]["mineru"]["api_key_configured"] is False
    assert persisted["document_processing"]["mineru"]["base_url"] == "https://mineru.example.invalid"
    assert "api_key" not in json.dumps(persisted)


def test_document_processing_preserves_paddle_ocr_provider(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "document_processing": {
                "ocr": {
                    "provider": "paddle",
                    "languages": ["zh", "en"],
                    "enabled": True,
                },
            },
        },
    )

    assert settings["document_processing"]["ocr"]["provider"] == "paddle"
    assert settings["document_processing"]["ocr"]["base_url"] == ""


def test_document_processing_preserves_deepseek_ocr_provider(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    settings = save_settings(
        workspace,
        {
            "document_processing": {
                "ocr": {
                    "provider": "deepseek",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "languages": ["zh", "en"],
                    "enabled": True,
                },
            },
        },
    )

    assert settings["document_processing"]["ocr"]["provider"] == "deepseek"
    assert settings["document_processing"]["ocr"]["base_url"] == "https://api.siliconflow.cn/v1"
