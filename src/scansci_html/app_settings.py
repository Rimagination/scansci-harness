"""Local, redacted configuration for the ScanSci desktop workbench.

The configuration file deliberately contains no credentials.  Provider API keys
are resolved from a launch environment variable first and the operating-system
keyring second; the web API exposes only redacted availability metadata.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from .local_model_market import (
    QWEN3_ASR_LEGACY_MODEL_ID,
    QWEN3_ASR_NATIVE_MODEL_ID,
    installed_models,
    missing_configured_model_roots,
)

from .builtin_skills import default_skill_records
from .artifact_plugins import default_plugin_records, enrich_builtin_plugins
from .external_capabilities import host_policy_defaults, normalize_provider_manifest
from .jev_client import DEFAULT_ENDPOINT as JEV_DEFAULT_ENDPOINT
from .vector_storage import configure_vector_index_root


_CONFIG_NAME = ".scansci-notebook.json"
_SERVICE_NAME = "scansci-html-notebook"
_CREDENTIAL_SCOPE_RE = re.compile(r"[0-9a-f]{32}\Z")
_CREDENTIAL_SCOPE_LOCK = threading.RLock()
_SAFE_ID = re.compile(r"[^a-z0-9._-]+")
_MAX_ITEMS = 128
_MAX_MCP_TOOL_POLICIES = 64
_MAX_MCP_TOOL_NAME_LENGTH = 160
_MCP_TOOL_EFFECTS = frozenset({"read", "reversible", "write", "high"})
_MANAGED_GATEWAY_BASE_URL = "https://scansci-glm-gateway.932196440.workers.dev/v1"
_MANAGED_PROVIDER_ID = "scansci-managed"
_MANAGED_PROVIDER_NAME = "ScanSci"
_DEFAULT_CHAT_MODEL_ID = "glm-4.7-flash"
_DEEPSEEK_PROVIDER_ID = "deepseek"
_DEEPSEEK_FLASH_MODEL_ID = "deepseek-v4-flash"
_DEEPSEEK_PRO_MODEL_ID = "deepseek-v4-pro"
_DEEPSEEK_LEGACY_MODEL_IDS = frozenset({"deepseek-chat", "deepseek-reasoner"})
_OPENCODE_GO_PROVIDER_ID = "opencode-go"
_OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
_JEV_PROVIDER_ID = "jev"
_ZHIPU_PROVIDER_ID = "zhipu"
_ZHIPU_STANDARD_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
_ZHIPU_CODING_PLAN_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
_PROVIDER_API_KEY_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "opencode-go": ("OPENCODE_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "dashscope": ("DASHSCOPE_API_KEY",),
    "zai": ("ZAI_API_KEY",),
    "zhipu": ("ZHIPU_API_KEY",),
    "moonshot": ("MOONSHOT_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "siliconflow": ("SILICONFLOW_API_KEY",),
    "modelscope": ("MODELSCOPE_API_TOKEN", "MODELSCOPE_API_KEY"),
    "volcengine": ("VOLCENGINE_API_KEY",),
    "jev": ("TYPESAFE_API_KEY",),
}
_STORAGE_ENV_BASELINE: dict[str, str | None] = {}
_STORAGE_ENV_LAST_APPLIED: dict[str, str | None] = {}


def _provider_preset(
    identifier: str,
    name: str,
    *,
    category: str,
    logo: str | None = None,
    base_url: str = "",
    kind: str = "openai-compatible",
    summary: str = "填写 API 地址与密钥后可读取服务商的模型列表。",
    models: list[dict[str, Any]] | None = None,
    auth_mode: str = "key",
    model_listing: bool = True,
    api_surface: str = "chat_completions",
    standard_base_url: str | None = None,
    coding_plan_base_url: str = "",
) -> dict[str, Any]:
    """Describe a credential-free provider card shown by Model Services.

    Endpoints are intentionally supplied only for broadly documented, stable
    OpenAI/Anthropic-compatible APIs.  Aggregators and account-linked services
    start blank so an outdated endpoint can never silently send a key to the
    wrong host.
    """

    result = {
        "id": identifier,
        "name": name,
        "logo": logo or identifier,
        "kind": kind,
        "base_url": base_url,
        "api_surface": api_surface,
        "responses_enabled": False,
        "category": category,
        "summary": summary,
        "auth_mode": auth_mode,
        "model_listing": model_listing,
        "models": models or [_model_preset("chat-model", "通用对话模型", group=name, capabilities=["reasoning"])],
    }
    if coding_plan_base_url:
        result.update(
            {
                "standard_base_url": standard_base_url or base_url,
                "coding_plan_base_url": coding_plan_base_url,
                "coding_plan_enabled": False,
            }
        )
    return result


def _model_preset(
    identifier: str,
    name: str,
    *,
    group: str,
    capabilities: list[str],
    context_window: str = "",
    readiness: str = "production",
    automatic_fallback: bool = False,
) -> dict[str, Any]:
    """Build a visible, editable model record without credentials."""

    return {
        "id": identifier,
        "name": name,
        "group": group,
        "context_window": context_window,
        "capabilities": capabilities,
        # A routed model is not automatically a production fallback.  The
        # release probe must verify its actual task quality before an operator
        # grants it that role.
        "readiness": readiness,
        "automatic_fallback": bool(automatic_fallback),
    }


# The catalog mirrors Cherry Studio's documented providers and the providers
# currently exposed in its desktop catalog.  It is a discovery/configuration
# catalog, never a list of bundled or shared credentials.
_MANAGED_MODEL_PRESETS = [
    _model_preset("glm-4.7-flash", "GLM-4.7 Flash", group="GLM", context_window="200K", capabilities=["reasoning", "tool", "coding"]),
]


_DEEPSEEK_MODEL_PRESETS = [
    _model_preset(_DEEPSEEK_FLASH_MODEL_ID, "DeepSeek V4 Flash", group="DeepSeek", capabilities=["reasoning", "tool", "coding"]),
    _model_preset(_DEEPSEEK_PRO_MODEL_ID, "DeepSeek V4 Pro", group="DeepSeek", capabilities=["reasoning", "tool", "coding"]),
]


# OpenCode Go exposes one subscription endpoint while routing different model
# families through the API surface documented by OpenCode.  Keep the catalog
# here so a new installation is immediately usable after the user saves a key;
# the transport layer resolves the endpoint from the selected model ID.
_OPENCODE_GO_MODEL_PRESETS = [
    _model_preset("grok-4.6", "Grok 4.6", group="Grok", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("glm-5.3-flash", "GLM-5.3 Flash", group="GLM", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("glm-5.3", "GLM-5.3", group="GLM", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("glm-5.2", "GLM-5.2", group="GLM", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("glm-5.1", "GLM-5.1", group="GLM", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("gpt-5.6-luna", "GPT-5.6 Luna", group="GPT", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("kimi-k3", "Kimi K3", group="Kimi", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("kimi-k2.7-code", "Kimi K2.7 Code", group="Kimi", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("kimi-k2.6", "Kimi K2.6", group="Kimi", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("longcat-2.0", "LongCat 2.0", group="LongCat", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("mimo-v2.5", "MiMo V2.5", group="MiMo", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("mimo-v2.5-pro", "MiMo V2.5 Pro", group="MiMo", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("minimax-m3", "MiniMax M3", group="MiniMax", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("minimax-m2.7", "MiniMax M2.7", group="MiniMax", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("minimax-m2.5", "MiniMax M2.5", group="MiniMax", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("muse-spark-1.2-contributor", "Muse Spark 1.2 Contributor", group="Muse", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("qwen3.8-max", "Qwen3.8 Max", group="Qwen", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("qwen3.8-flash", "Qwen3.8 Flash", group="Qwen", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("qwen3.7-max", "Qwen3.7 Max", group="Qwen", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("qwen3.7-plus", "Qwen3.7 Plus", group="Qwen", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("qwen3.6-plus", "Qwen3.6 Plus", group="Qwen", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("deepseek-v4-pro", "DeepSeek V4 Pro", group="DeepSeek", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("deepseek-v4-flash", "DeepSeek V4 Flash", group="DeepSeek", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("deepseek-v4-flash-vision-exp", "DeepSeek V4 Flash Vision Exp", group="DeepSeek", capabilities=["reasoning", "vision", "tool", "coding"]),
    _model_preset("hy4-preview", "HY4 Preview", group="HY", capabilities=["reasoning", "tool", "coding"]),
    _model_preset("hy3", "HY3", group="HY", capabilities=["reasoning", "tool", "coding"]),
]


def managed_model_ids() -> tuple[str, ...]:
    """Return every managed route shown in the product catalog."""

    return tuple(str(model["id"]) for model in _MANAGED_MODEL_PRESETS)


def managed_probe_model_ids() -> tuple[str, ...]:
    """Return the managed routes that must pass the release health probe."""

    return tuple(
        str(model["id"])
        for model in _MANAGED_MODEL_PRESETS
        if str(model.get("readiness", "production")) == "production"
    )


def _managed_production_model_ids() -> set[str]:
    """Return managed routes that may serve user-visible answers."""

    return {
        str(model["id"])
        for model in _MANAGED_MODEL_PRESETS
        if str(model.get("readiness", "production")) == "production"
    }


def managed_fallback_model_ids(model_id: str) -> tuple[str, ...]:
    """Return only release-approved automatic fallbacks.

    Merely being reachable through the Worker is deliberately insufficient:
    a fallback must also pass ScanSci's structured research-writing probe.
    """

    selected = str(model_id or "").strip()
    return tuple(
        str(model["id"])
        for model in _MANAGED_MODEL_PRESETS
        if bool(model.get("automatic_fallback")) and str(model["id"]) != selected
    )


_PROVIDER_PRESETS: list[dict[str, Any]] = [
    _provider_preset(_MANAGED_PROVIDER_ID, _MANAGED_PROVIDER_NAME, category="ScanSci", base_url=_MANAGED_GATEWAY_BASE_URL, summary="Built-in managed models. Upstream credentials are held only by ScanSci's gateway.", auth_mode="managed", model_listing=False, models=_MANAGED_MODEL_PRESETS),
    _provider_preset(_JEV_PROVIDER_ID, "JEV", category="Pi 决策层", logo="typesafe", base_url=JEV_DEFAULT_ENDPOINT, api_surface="jev_systemone", summary="JEV 作为 Pi 的结构化决策层，按需参与工具与 Skill 流程。", model_listing=False, models=[_model_preset("jev-decision-layer", "JEV Decision Layer", group="JEV", capabilities=["reasoning", "tool"])]),
    _provider_preset("openai", "OpenAI", category="国际模型", base_url="https://api.openai.com/v1", models=[_model_preset("gpt-5.2", "GPT-5.2", group="GPT", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("gpt-5.2-mini", "GPT-5.2 mini", group="GPT", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("text-embedding-3-large", "text-embedding-3-large", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset("anthropic", "Anthropic", category="国际模型", kind="anthropic-compatible", base_url="https://api.anthropic.com/v1", models=[_model_preset("claude-sonnet-4-6", "Claude Sonnet 4.6", group="Claude", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("claude-opus-4-6", "Claude Opus 4.6", group="Claude", capabilities=["reasoning", "vision", "tool", "coding"])]),
    _provider_preset("gemini", "Google Gemini", category="国际模型", base_url="https://generativelanguage.googleapis.com/v1beta/openai", models=[_model_preset("gemini-3.1-pro-preview", "Gemini 3.1 Pro", group="Gemini", capabilities=["reasoning", "vision", "tool", "coding", "audio"]), _model_preset("gemini-3.5-flash", "Gemini 3.5 Flash", group="Gemini", capabilities=["reasoning", "vision", "tool"]), _model_preset("gemini-embedding-001", "Gemini Embedding", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset("vertex-ai", "Google Vertex AI", category="国际模型", summary="Vertex AI 通常需要网关或企业项目凭据；请粘贴其兼容 API 地址。", models=[_model_preset("gemini-3.1-pro-preview", "Gemini 3.1 Pro", group="Gemini", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("text-embedding-005", "Text Embedding", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset("openrouter", "OpenRouter", category="模型聚合", base_url="https://openrouter.ai/api/v1", models=[_model_preset("openai/gpt-5.2", "OpenAI GPT-5.2", group="OpenAI", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro", group="Google", capabilities=["reasoning", "vision", "tool"]), _model_preset("deepseek/deepseek-r1", "DeepSeek R1", group="DeepSeek", capabilities=["reasoning", "coding"])]),
    _provider_preset("l0veyou", "l0veyou", category="模型聚合", base_url="https://l0veyou.com/v1", summary="公益站 OpenAI 兼容 API；免费额度和模型由站点调整，密钥需由用户自行创建。", models=[_model_preset("openai/gpt-6-luna", "GPT-6 Luna", group="l0veyou", capabilities=["reasoning"]), _model_preset("deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash", group="l0veyou", capabilities=["reasoning"]), _model_preset("muse-spark-1.3", "Muse Spark 1.3", group="l0veyou", capabilities=["reasoning"])]),
    _provider_preset(_OPENCODE_GO_PROVIDER_ID, "OpenCode Go", category="订阅模型", base_url=_OPENCODE_GO_BASE_URL, api_surface="auto", summary="OpenCode Go 订阅接口；在 OpenCode Zen 开通套餐后粘贴 API Key，ScanSci 会按模型自动选择接口。", models=_OPENCODE_GO_MODEL_PRESETS),
    _provider_preset("nvidia", "NVIDIA NIM", category="国际模型", base_url="https://integrate.api.nvidia.com/v1", models=[_model_preset("meta/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct", group="Meta", capabilities=["reasoning", "tool", "coding"]), _model_preset("nvidia/llama-3.2-nv-embedqa-1b-v2", "NV-EmbedQA", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset(_DEEPSEEK_PROVIDER_ID, "DeepSeek", category="国内直连", base_url="https://api.deepseek.com", models=_DEEPSEEK_MODEL_PRESETS),
    _provider_preset("dashscope", "阿里云百炼", category="国内直连", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", models=[_model_preset("qwen-plus", "Qwen Plus", group="Qwen", capabilities=["reasoning", "tool", "coding"]), _model_preset("qwen-vl-plus", "Qwen VL Plus", group="Qwen", capabilities=["reasoning", "vision"]), _model_preset("text-embedding-v3", "Text Embedding V3", group="Embeddings", capabilities=["embedding"]), _model_preset("gte-rerank-v2", "GTE Rerank V2", group="Rerankers", capabilities=["reranking"])]),
    _provider_preset("zai", "Z.ai", category="国内直连", base_url="https://api.z.ai/api/paas/v4", models=[_model_preset("glm-4.7", "GLM-4.7", group="GLM", capabilities=["reasoning", "tool", "coding"]), _model_preset("glm-4.6v", "GLM-4.6V", group="GLM", capabilities=["reasoning", "vision"]), _model_preset("embedding-3", "Embedding-3", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset("zhipu", "智谱开放平台", category="国内直连", base_url=_ZHIPU_STANDARD_BASE_URL, standard_base_url=_ZHIPU_STANDARD_BASE_URL, coding_plan_base_url=_ZHIPU_CODING_PLAN_BASE_URL, models=[_model_preset("glm-4.7-flash", "GLM-4.7 Flash（免费）", group="GLM", context_window="200K", capabilities=["reasoning", "tool", "coding"]), _model_preset("glm-4-plus", "GLM-4 Plus", group="GLM", capabilities=["reasoning", "tool", "coding"]), _model_preset("glm-4v-plus", "GLM-4V Plus", group="GLM", capabilities=["reasoning", "vision"]), _model_preset("embedding-3", "Embedding-3", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset("paratera", "并行智算云（Paratera）", category="国内直连", base_url="https://llmapi.paratera.com/v1", summary="并行智算云模型服务；支持通义万相与 GLM CogView 文生图。", model_listing=False, models=[_model_preset("GLM-CogView3-Flash", "GLM-CogView3-Flash（限时免费）", group="GLM CogView", capabilities=["image"]), _model_preset("WanX2.1-T2I-Plus", "WanX2.1-T2I-Plus", group="通义万相", capabilities=["image"])]),
    _provider_preset("moonshot", "Kimi", category="国内直连", base_url="https://api.moonshot.ai/v1", models=[_model_preset("kimi-k2.5", "Kimi K2.5", group="Kimi", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("moonshot-v1-128k", "Moonshot 128K", group="Kimi", capabilities=["reasoning", "tool"])]),
    _provider_preset("minimax", "MiniMax", category="国内直连", base_url="https://api.minimaxi.com/v1", models=[_model_preset("MiniMax-M2.7", "MiniMax M2.7", group="MiniMax", capabilities=["reasoning", "tool", "coding"]), _model_preset("MiniMax-VL-01", "MiniMax VL", group="MiniMax", capabilities=["reasoning", "vision"])]),
    _provider_preset("xiaomi-mimo", "Xiaomi MiMo", category="国内直连", models=[_model_preset("mimo-v2-flash", "MiMo V2 Flash", group="MiMo", capabilities=["reasoning", "tool", "coding"])]),
    _provider_preset("siliconflow", "硅基流动", category="云端推理", base_url="https://api.siliconflow.cn/v1", models=[_model_preset("deepseek-ai/DeepSeek-V3", "DeepSeek V3", group="DeepSeek", capabilities=["reasoning", "tool", "coding"]), _model_preset("Qwen/Qwen2.5-VL-72B-Instruct", "Qwen VL 72B", group="Qwen", capabilities=["reasoning", "vision"]), _model_preset("Qwen/Qwen3-VL-8B-Instruct", "Qwen3 VL 8B", group="Qwen", capabilities=["reasoning", "vision"]), _model_preset("PaddlePaddle/PaddleOCR-VL-1.5", "PaddleOCR VL 1.5", group="Paddle", capabilities=["vision"]), _model_preset("BAAI/bge-m3", "BGE-M3", group="BAAI", capabilities=["embedding"]), _model_preset("Qwen/Qwen3-Embedding-8B", "Qwen3 Embedding 8B", group="Qwen", capabilities=["embedding"]), _model_preset("BAAI/bge-reranker-v2-m3", "BGE Reranker V2 M3", group="BAAI", capabilities=["reranking"]), _model_preset("Qwen/Qwen3-Reranker-8B", "Qwen3 Reranker 8B", group="Qwen", capabilities=["reranking"])]),
    _provider_preset("modelscope", "ModelScope", category="云端推理", base_url="https://api-inference.modelscope.cn/v1", models=[_model_preset("Qwen/Qwen2.5-72B-Instruct", "Qwen 2.5 72B", group="Qwen", capabilities=["reasoning", "tool", "coding"]), _model_preset("Qwen/Qwen2.5-VL-72B-Instruct", "Qwen VL 72B", group="Qwen", capabilities=["reasoning", "vision"]), _model_preset("iic/nlp_gte-rerank", "GTE Rerank", group="Rerankers", capabilities=["reranking"])]),
    _provider_preset("ppio", "PPIO Cloud", category="云端推理", models=[_model_preset("deepseek-ai/DeepSeek-V3", "DeepSeek V3", group="DeepSeek", capabilities=["reasoning", "tool", "coding"]), _model_preset("Qwen/Qwen2.5-72B-Instruct", "Qwen 2.5 72B", group="Qwen", capabilities=["reasoning", "tool", "coding"])]),
    _provider_preset("volcengine", "火山引擎", category="云端推理", base_url="https://ark.cn-beijing.volces.com/api/v3", models=[_model_preset("doubao-seed-1-6-thinking", "Doubao Seed Thinking", group="Doubao", capabilities=["reasoning", "tool", "coding"]), _model_preset("doubao-1-5-vision-pro", "Doubao Vision Pro", group="Doubao", capabilities=["reasoning", "vision"]), _model_preset("doubao-embedding-large", "Doubao Embedding", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset("huawei-cloud", "华为云", category="云端推理", models=[_model_preset("DeepSeek-R1", "DeepSeek R1", group="DeepSeek", capabilities=["reasoning", "coding"]), _model_preset("bge-m3", "BGE-M3", group="Embeddings", capabilities=["embedding"])]),
    _provider_preset("infinigence", "无问芯穹", category="云端推理", models=[_model_preset("Qwen2.5-72B-Instruct", "Qwen 2.5 72B", group="Qwen", capabilities=["reasoning", "tool", "coding"]), _model_preset("DeepSeek-R1", "DeepSeek R1", group="DeepSeek", capabilities=["reasoning", "coding"])]),
    _provider_preset("qiniu-ai", "七牛云 AI 推理", category="云端推理", models=[_model_preset("DeepSeek-V3", "DeepSeek V3", group="DeepSeek", capabilities=["reasoning", "tool", "coding"]), _model_preset("Qwen2.5-VL-72B-Instruct", "Qwen VL", group="Qwen", capabilities=["reasoning", "vision"])]),
    _provider_preset("modal", "Modal", category="云端推理", models=[_model_preset("zai-org/GLM-5.1-FP8", "GLM-5.1-FP8", group="zai-org", capabilities=["reasoning", "tool", "coding"])]),
    _provider_preset("new-api", "NewAPI", category="模型聚合", summary="自建 NewAPI 实例；请填入管理员提供的 OpenAI 兼容地址。", models=[_model_preset("gpt-5.2", "GPT-5.2", group="OpenAI", capabilities=["reasoning", "vision", "tool", "coding"])]),
    _provider_preset("one-api", "OneAPI", category="模型聚合", summary="自建 OneAPI 实例；请填入管理员提供的 OpenAI 兼容地址。", models=[_model_preset("gpt-5.2", "GPT-5.2", group="OpenAI", capabilities=["reasoning", "vision", "tool", "coding"])]),
    _provider_preset("aihubmix", "AiHubMix", category="模型聚合", models=[_model_preset("gpt-5.2", "GPT-5.2", group="OpenAI", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("deepseek-r1", "DeepSeek R1", group="DeepSeek", capabilities=["reasoning", "coding"])]),
    _provider_preset("ocoolai", "ocoolAI", category="模型聚合", models=[_model_preset("gpt-5.2", "GPT-5.2", group="OpenAI", capabilities=["reasoning", "vision", "tool", "coding"]), _model_preset("claude-sonnet-4-6", "Claude Sonnet", group="Anthropic", capabilities=["reasoning", "vision", "tool", "coding"])]),
    _provider_preset("alaya", "Alaya NeW", category="模型聚合", models=[_model_preset("deepseek-chat", "DeepSeek Chat", group="DeepSeek", capabilities=["reasoning", "tool", "coding"])]),
    _provider_preset("dmxapi", "DMXAPI", category="模型聚合", models=[_model_preset("gemini-3.1-pro-preview", "Gemini 3.1 Pro", group="Google", capabilities=["reasoning", "vision", "tool"])]),
    _provider_preset("aionly", "唯一AI (AiOnly)", category="模型聚合", models=[_model_preset("deepseek-chat", "DeepSeek Chat", group="DeepSeek", capabilities=["reasoning", "tool", "coding"])]),
    _provider_preset("burncloud", "BurnCloud", category="模型聚合", models=[_model_preset("gpt-5.2", "GPT-5.2", group="OpenAI", capabilities=["reasoning", "vision", "tool", "coding"])]),
    _provider_preset("cherryai", "CherryAI", category="Cherry 生态", summary="Cherry Studio 的账户入口；在 ScanSci 中请填写该账户提供的兼容地址和密钥。", auth_mode="account_or_key", models=[_model_preset("cherry-model", "Cherry 模型", group="CherryAI", capabilities=["reasoning", "tool"])]),
    _provider_preset("cherryin", "CherryIN", category="Cherry 生态", models=[_model_preset("gpt-5.2", "GPT-5.2", group="OpenAI", capabilities=["reasoning", "vision", "tool", "coding"])]),
    _provider_preset("github-copilot", "GitHub Copilot", category="Cherry 生态", summary="Copilot 通常使用账户授权或专用令牌；请填写可用的兼容网关地址。", auth_mode="account_or_token", models=[_model_preset("gpt-5.2", "GPT-5.2", group="Copilot", capabilities=["reasoning", "vision", "tool", "coding"])]),
    _provider_preset("wuwen", "无问", category="Cherry 生态", models=[_model_preset("deepseek-r1", "DeepSeek R1", group="DeepSeek", capabilities=["reasoning", "coding"])]),
]


_LOCAL_MODEL_PRESETS: list[dict[str, Any]] = [
    {
        "id": "ollama",
        "name": "Ollama",
        "runtime": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "",
        "enabled": False,
    },
    {
        "id": "ollama-minicpm-v4.6",
        "name": "Ollama · MiniCPM-V 4.6（视觉）",
        "runtime": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "minicpm-v4.6",
        "context_window": "32K",
        "capabilities": ["vision"],
        # Transformers is ScanSci's default local route for native HF vision,
        # ASR, embeddings, and reranking. Keep this as an opt-in external
        # connection so a second MiniCPM checkpoint is never pulled silently.
        "enabled": False,
    },
    {
        "id": "ollama-qwen3.5-4b",
        "name": "Ollama · Qwen3.5 4B（工具推理）",
        "runtime": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen3.5:4b",
        "context_window": "32K",
        "capabilities": ["reasoning", "tool", "coding", "vision"],
        # Qwen3.5 is an optional user-managed Ollama model.  It is enabled
        # after the user selects it or an existing workspace explicitly opts
        # into the route; ScanSci never pulls it implicitly.
        "enabled": False,
    },
    {
        "id": "ollama-qwen3.5-9b",
        "name": "Ollama · Qwen3.5 9B",
        "runtime": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen3.5:9b",
        "context_window": "16K",
        "capabilities": ["reasoning", "tool", "coding", "vision"],
        "enabled": False,
    },
    {
        "id": "ollama-qwen3.5-2b",
        "name": "Ollama · Qwen3.5 2B（轻量）",
        "runtime": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen3.5:2b",
        "context_window": "16K",
        "capabilities": ["reasoning", "tool", "coding", "vision"],
        "enabled": False,
    },
    {
        "id": "qwen3-asr-0.6b",
        "name": "Qwen3 ASR 0.6B（语音识别）",
        "runtime": "local-huggingface",
        "base_url": "",
        "model_id": QWEN3_ASR_NATIVE_MODEL_ID,
        "capabilities": ["audio"],
        "enabled": True,
    },
    {
        "id": "lm-studio",
        "name": "LM Studio",
        "runtime": "lm-studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "model_id": "",
        "enabled": False,
    },
    {
        "id": "llama-cpp",
        "name": "llama.cpp Server",
        "runtime": "llama.cpp",
        "base_url": "http://127.0.0.1:8080/v1",
        "model_id": "",
        "enabled": False,
    },
    {
        "id": "strata",
        "name": "Strata",
        "runtime": "strata",
        "base_url": "http://127.0.0.1:8080/v1",
        "model_id": "",
        "enabled": False,
    },
]


class SettingsError(ValueError):
    """Raised when a local workbench setting cannot be read or stored."""


_DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": 2,
    "credential_scope_id": "",
    "active_model": {"provider_id": _MANAGED_PROVIDER_ID, "model_id": _DEFAULT_CHAT_MODEL_ID},
    "providers": [
        {
            "id": _MANAGED_PROVIDER_ID,
            "name": _MANAGED_PROVIDER_NAME,
            "kind": "openai-compatible",
            "base_url": _MANAGED_GATEWAY_BASE_URL,
            "enabled": True,
            "auth_mode": "managed",
            "models": [
                {"id": _DEFAULT_CHAT_MODEL_ID, "name": "GLM-4.7 Flash", "group": "GLM", "context_window": "200K", "capabilities": ["reasoning", "tool", "coding"]},
            ],
        },
        {
            "id": "local-evidence",
            "name": "离线基础检索（非模型）",
            "kind": "local",
            "base_url": "",
            "enabled": True,
            "models": [
                {"id": "evidence-retrieval", "name": "关键词检索与词法重排（非模型）", "group": "离线回退", "context_window": "本地", "capabilities": ["embedding", "reranking"]},
            ],
        },
    ],
    "local_models": [
        {
            "id": "builtin-evidence",
            "name": "离线基础检索（非模型）",
            "runtime": "builtin",
            "base_url": "",
            "model_id": "local-hash-v1 / local-lexical-v1",
            "enabled": True,
        }
    ],
    "model_roles": {
        "reasoning": "provider:scansci-managed:glm-4.7-flash",
        "writing": "provider:scansci-managed:glm-4.7-flash",
        "retrieval": "local:builtin-evidence",
        "embedding": "auto",
        "reranking": "auto",
        "vision": "",
        "audio": "",
        "image": "auto",
        "slides": "provider:scansci-managed:glm-4.7-flash",
    },
    "document_processing": {
        "ocr": {
            "provider": "tesseract",
            "base_url": "",
            "languages": ["zh", "en"],
            "enabled": True,
        },
        "mineru": {
            "provider": "mineru",
            "base_url": "https://mineru.net",
            "enabled": False,
        },
    },
    "onboarding": {
        "welcome_dismissed": False,
        "resource_setup_completed": False,
        # Kept separately from the optional model download.  A user can start
        # work without any local files, then resume the data-source walkthrough
        # from Settings when they are ready to connect private material.
        "data_setup_completed": False,
    },
    "appearance": {
        "locale": "zh-CN",
        "theme": "system",
        "accent": "jade",
        "font_scale": "medium",
    },
    "general": {
        "conversation": {
            "send_shortcut": "enter",
            "completion_notifications": True,
            "agent_completion_notifications": True,
            "subagent_completion_notifications": False,
        },
        "directories": {
            "default_workspace": "",
            "conversation_workspace": "",
            # These storage paths are optional overrides.  Empty values preserve
            # the platform defaults and existing environment configuration.
            "model_cache": "",
            "local_runtime": "",
            # Vector indexes are isolated per knowledge base.  Empty keeps the
            # legacy location next to the application evidence database.
            "vector_index": "",
        },
        "updates": {
            "auto_check": True,
        },
    },
    "skills": default_skill_records(),
    "mcp_servers": [],
    "plugins": default_plugin_records(),
}


def provider_presets() -> list[dict[str, Any]]:
    """Return credential-free API provider presets for the settings UI."""

    return deepcopy(_PROVIDER_PRESETS)


def local_model_presets() -> list[dict[str, Any]]:
    """Return common loopback model runtime presets."""

    return deepcopy(_LOCAL_MODEL_PRESETS)


def ensure_local_model_preset(
    workspace: str | Path,
    preset_id: str,
    *,
    make_primary: bool = False,
    base_url: str = "",
) -> dict[str, Any]:
    """Persist one approved local-model preset after an explicit install.

    Model downloads are deliberately separate from settings: a user can browse
    the catalog without changing routing.  Once a download has completed, the
    installer calls this helper so the new capability becomes visible and can
    be selected without asking the user to copy an endpoint or model id.
    """

    wanted = str(preset_id or "").strip()
    preset = next((item for item in _LOCAL_MODEL_PRESETS if str(item.get("id", "")) == wanted), None)
    if preset is None:
        raise SettingsError("未知的本地模型预设")
    settings = _normalize_settings(_read_raw_settings(workspace))
    rows = list(settings.get("local_models", []) or [])
    existing = next((item for item in rows if str(item.get("id", "")) == wanted), None)
    endpoint = f"{str(base_url).rstrip('/')}/v1" if base_url else ""
    if endpoint and preset.get("runtime") != "ollama":
        raise SettingsError("只有 Ollama 预设可以指定运行时地址")
    if existing is None:
        rows.append({**deepcopy(preset), "enabled": True, **({"base_url": endpoint} if endpoint else {})})
    else:
        # Keep a user's custom endpoint/name, but make the installed model
        # routable again if a previous setup left the preset disabled.
        existing["model_id"] = str(preset.get("model_id", "") or existing.get("model_id", ""))
        existing["runtime"] = str(preset.get("runtime", existing.get("runtime", "")))
        existing["base_url"] = endpoint or str(existing.get("base_url") or preset.get("base_url", ""))
        existing["capabilities"] = list(preset.get("capabilities", existing.get("capabilities", [])) or [])
        existing["enabled"] = True
    settings["local_models"] = rows
    if wanted == "qwen3-asr-0.6b" and not str(settings.get("model_roles", {}).get("audio", "")):
        settings.setdefault("model_roles", {})["audio"] = f"local:{wanted}"
    if make_primary:
        if preset.get("runtime") != "ollama" or "tool" not in preset.get("capabilities", []):
            raise SettingsError("该本地模型不能设为默认助手")
        provider_id = f"local-runtime-{_safe_id(wanted, fallback='model')}"
        model_id = str(preset["model_id"])
        settings["active_model"] = {"provider_id": provider_id, "model_id": model_id}
        reference = f"provider:{provider_id}:{model_id}"
        settings["model_roles"].update({role: reference for role in ("reasoning", "writing", "slides")})
    return save_settings(workspace, settings)


def settings_path(workspace: str | Path) -> Path:
    """Return the per-workspace config file path without creating it."""

    return Path(workspace).resolve().parent / _CONFIG_NAME


def _quarantine_settings_file(path: Path) -> Path | None:
    """Move an unreadable settings file aside without deleting user data.

    A partially written or legacy non-UTF-8 settings file must not prevent the
    desktop from starting.  Keeping the original as a timestamped sidecar
    gives support and the user a recovery point while allowing the normalized
    defaults to load immediately.
    """

    if not path.is_file():
        return None
    try:
        stamp = str(path.stat().st_mtime_ns)
    except OSError:
        stamp = "unknown"
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    suffix = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{suffix}")
        suffix += 1
    try:
        os.replace(path, candidate)
    except OSError:
        return None
    return candidate


def _read_raw_settings_file(workspace: str | Path) -> dict[str, Any]:
    """Read the public settings file, recovering safely from bad snapshots."""

    path = settings_path(workspace)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _quarantine_settings_file(path)
        return {}
    if not isinstance(payload, dict):
        _quarantine_settings_file(path)
        return {}
    return payload


def load_settings(workspace: str | Path, *, include_secret_status: bool = True) -> dict[str, Any]:
    """Load a safe public configuration and redact all provider credentials."""

    path = settings_path(workspace)
    raw = _read_raw_settings_file(workspace)
    try:
        normalized = _normalize_settings(raw)
    except SettingsError:
        # A valid JSON object from an older release can still be structurally
        # unusable after a migration. Preserve it and start from defaults.
        _quarantine_settings_file(path)
        normalized = _normalize_settings({})
    if not include_secret_status:
        return normalized
    normalized = _ensure_credential_scope(workspace, normalized, create=False)
    return _with_secret_status(normalized, workspace)


def save_settings(workspace: str | Path, payload: object) -> dict[str, Any]:
    """Validate and atomically persist public settings (never provider keys)."""

    previous = _normalize_settings(_read_raw_settings_file(workspace))
    normalized = _normalize_settings(payload)
    if _strata_in_use(normalized) and not _verified_strata_model(normalized):
        raise SettingsError("Strata 地址或模型尚未通过 Agent 验证；请在运行时页面验证后切换")
    previous_providers = {provider["id"]: provider for provider in previous["providers"]}
    next_providers = {provider["id"]: provider for provider in normalized["providers"]}
    changed_prior_ids = {
        provider_id
        for provider_id, prior in previous_providers.items()
        if provider_id not in next_providers
        or (
            str(prior["base_url"]).strip().rstrip("/"), prior["kind"], prior.get("auth_mode")
        ) != (
            str(next_providers[provider_id]["base_url"]).strip().rstrip("/"),
            next_providers[provider_id]["kind"],
            next_providers[provider_id].get("auth_mode"),
        )
    }
    added_ids = set(next_providers) - set(previous_providers)
    for provider in normalized["providers"]:
        if provider["id"] in changed_prior_ids or provider["id"] in added_ids:
            provider["remote_rag_allowed"] = False
    _ensure_credential_scope(workspace, previous, create=bool(changed_prior_ids or added_ids))
    if changed_prior_ids or added_ids:
        try:
            scope = _credential_scope_id(_read_raw_settings_file(workspace))
            if changed_prior_ids and not scope:
                raise SettingsError("更改 API 地址前无法确认凭据归属")
            if scope:
                import keyring

                for provider_id in changed_prior_ids | added_ids:
                    scoped_key = f"model-provider:v2:{scope}:{provider_id}"
                    marker = _provider_scoped_endpoint_marker(scope, provider_id)
                    if _get_saved_secret(keyring, scoped_key) and not _get_saved_secret(keyring, marker):
                        # A re-added provider has no trustworthy prior endpoint.
                        old_endpoint = _provider_endpoint_digest(workspace, provider_id) if provider_id in changed_prior_ids else "unbound"
                        keyring.set_password(_SERVICE_NAME, marker, old_endpoint)
                        if _get_saved_secret(keyring, marker) != old_endpoint:
                            raise SettingsError("无法固定已有密钥的 API 地址")
        except Exception as exc:
            raise SettingsError(f"更改 API 地址前无法检查系统凭据：{exc}") from exc
    # The browser can update public settings, but cannot select a keyring scope.
    normalized["credential_scope_id"] = _credential_scope_id(_read_raw_settings_file(workspace))
    _write_settings(workspace, normalized)
    return _with_secret_status(normalized, workspace)


def remote_retrieval_granted(
    workspace: str | Path,
    provider_id: str,
    role: str,
    model_id: str,
    base_url: str,
) -> bool:
    """Check the persisted consent immediately before sending retrieval text."""

    if role not in {"embedding", "reranking"}:
        return False
    raw = _read_raw_settings_file(workspace)
    if not isinstance(raw, dict):
        return False
    roles = raw.get("model_roles")
    if not isinstance(roles, dict) or roles.get(role) != f"provider:{provider_id}:{model_id}":
        return False
    providers = raw.get("providers")
    if not isinstance(providers, list):
        return False
    return any(
        isinstance(provider, dict)
        and provider.get("id") == provider_id
        and provider.get("enabled", True) is True
        and provider.get("remote_rag_allowed") is True
        and str(provider.get("base_url", "")).strip().rstrip("/") == base_url.strip().rstrip("/")
        for provider in providers
    )


def _write_settings(workspace: str | Path, normalized: dict[str, Any]) -> None:
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise SettingsError(f"无法保存本地设置：{exc}") from exc


def _credential_scope_id(settings: object) -> str:
    value = str(settings.get("credential_scope_id", "") or "") if isinstance(settings, dict) else ""
    return value if _CREDENTIAL_SCOPE_RE.fullmatch(value) else ""


def _ensure_credential_scope(
    workspace: str | Path,
    settings: dict[str, Any],
    *,
    create: bool,
    keyring_module: Any | None = None,
) -> dict[str, Any]:
    """Move all existing workspace credentials to a stable, path-independent ID."""

    with _CREDENTIAL_SCOPE_LOCK:
        current_scope = _credential_scope_id(_read_raw_settings_file(workspace))
        return _ensure_credential_scope_unlocked(
            workspace,
            {**settings, "credential_scope_id": current_scope},
            create=create,
            keyring_module=keyring_module,
        )


def _ensure_credential_scope_unlocked(
    workspace: str | Path,
    settings: dict[str, Any],
    *,
    create: bool,
    keyring_module: Any | None = None,
) -> dict[str, Any]:
    if _credential_scope_id(settings):
        return settings
    try:
        if keyring_module is None:
            import keyring as keyring_module
        legacy: list[tuple[str, str]] = []
        for provider in settings["providers"]:
            if provider["kind"] == "local" or provider.get("auth_mode") == "managed":
                continue
            provider_id = str(provider["id"])
            value = keyring_module.get_password(_SERVICE_NAME, _legacy_provider_secret_name(workspace, provider_id))
            if value:
                legacy.append((f"model-provider:{provider_id}", str(value)))
        for service_id in ("ocr", "mineru"):
            value = keyring_module.get_password(_SERVICE_NAME, _legacy_document_service_secret_name(workspace, service_id))
            if value:
                legacy.append((f"document-service:{service_id}", str(value)))
        notion = keyring_module.get_password(_SERVICE_NAME, _legacy_notion_secret_name(workspace))
        if notion:
            legacy.append(("notion-integration", str(notion)))
        if not create and not legacy:
            return settings
        scope = uuid4().hex
        for identity, value in legacy:
            category, _, item_id = identity.partition(":")
            username = f"{category}:v2:{scope}{f':{item_id}' if item_id else ''}"
            if category == "model-provider":
                account_username = _provider_account_secret_name(workspace, item_id)
                account_key = keyring_module.get_password(_SERVICE_NAME, account_username)
                deleted = keyring_module.get_password(_SERVICE_NAME, _provider_account_deleted_name(workspace, item_id))
                if not deleted and account_key == value:
                    continue
                if not deleted and not account_key:
                    username = account_username
                else:
                    marker = _provider_scoped_endpoint_marker(scope, item_id)
                    endpoint_digest = _provider_endpoint_digest(workspace, item_id)
                    keyring_module.set_password(_SERVICE_NAME, marker, endpoint_digest)
                    if keyring_module.get_password(_SERVICE_NAME, marker) != endpoint_digest:
                        raise SettingsError("工作区凭据地址校验失败")
            keyring_module.set_password(_SERVICE_NAME, username, value)
            if keyring_module.get_password(_SERVICE_NAME, username) != value:
                raise SettingsError("系统凭据迁移后校验失败")
        updated = {**settings, "credential_scope_id": scope}
        _write_settings(workspace, updated)
        for identity, _ in legacy:
            category, _, item_id = identity.partition(":")
            old_username = (
                _legacy_provider_secret_name(workspace, item_id)
                if category == "model-provider"
                else _legacy_document_service_secret_name(workspace, item_id)
                if category == "document-service"
                else _legacy_notion_secret_name(workspace)
            )
            try:
                keyring_module.delete_password(_SERVICE_NAME, old_username)
            except Exception:
                # The new entry is verified and active; an unavailable delete
                # must not make the migrated credential unusable.
                pass
        return updated
    except Exception as exc:
        if create:
            raise SettingsError(f"无法迁移系统凭据：{exc}") from exc
        return settings


def _get_saved_secret(keyring_module: Any, username: str) -> str:
    return str(keyring_module.get_password(_SERVICE_NAME, username) or "")


def _delete_saved_secret(keyring_module: Any, *usernames: str) -> None:
    for username in dict.fromkeys(usernames):
        if keyring_module.get_password(_SERVICE_NAME, username):
            keyring_module.delete_password(_SERVICE_NAME, username)
        if keyring_module.get_password(_SERVICE_NAME, username):
            raise SettingsError("系统凭据删除后仍可读取")


def configure_managed_glm_4_7_flash(workspace: str | Path) -> dict[str, Any]:
    """Enable ScanSci's zero-configuration GLM service for one workspace."""

    settings = _normalize_settings(_read_raw_settings(workspace))
    provider = next((item for item in settings["providers"] if item["id"] == _MANAGED_PROVIDER_ID), None)
    if provider is None:  # Defensive: the common catalog normally guarantees this.
        raise SettingsError("Managed ScanSci provider preset is unavailable")

    provider["kind"] = "openai-compatible"
    provider["base_url"] = _MANAGED_GATEWAY_BASE_URL
    provider["enabled"] = True
    provider["auth_mode"] = "managed"
    preset = next(item for item in _PROVIDER_PRESETS if item["id"] == _MANAGED_PROVIDER_ID)
    available_model_ids = {str(model.get("id", "")) for model in provider["models"]}
    for model in reversed(preset["models"]):
        if model["id"] not in available_model_ids:
            provider["models"].insert(0, deepcopy(model))

    # Keep the built-in service at the top of an existing workspace too, so
    # people upgrading ScanSci immediately see the ready-to-use default.
    settings["providers"] = [
        provider,
        *(item for item in settings["providers"] if item["id"] != _MANAGED_PROVIDER_ID),
    ]

    reference = f"provider:{_MANAGED_PROVIDER_ID}:{_DEFAULT_CHAT_MODEL_ID}"
    settings["active_model"] = {"provider_id": _MANAGED_PROVIDER_ID, "model_id": _DEFAULT_CHAT_MODEL_ID}
    settings["model_roles"].update({"reasoning": reference, "writing": reference, "slides": reference, "vision": ""})
    return save_settings(workspace, settings)


def set_provider_api_key(workspace: str | Path, provider_id: str, value: str) -> dict[str, Any]:
    """Store a provider key in the OS credential manager, not the JSON file.

    A non-empty provider environment variable is an explicit, read-only
    override.  Refuse writes and deletes while it is active so the settings UI
    cannot create a misleading keyring value that will never be used.
    """

    settings = _normalize_settings(_read_raw_settings(workspace))
    provider = next((item for item in settings["providers"] if item["id"] == provider_id), None)
    if provider is None:
        raise SettingsError("找不到模型提供商")
    if provider["kind"] == "local":
        raise SettingsError("本地证据引擎不需要 API Key")
    if provider.get("auth_mode") == "managed":
        raise SettingsError("ScanSci 托管模型不接受本机 API Key")
    environment_value, environment_name = _provider_environment_api_key(provider_id, workspace=workspace)
    if environment_value:
        raise SettingsError(
            f"环境变量 {environment_name} 已提供该服务商 API Key；请先清除环境变量后再通过设置页保存或移除本机凭据"
        )
    secret = str(value or "").strip()
    try:
        import keyring

        settings = _ensure_credential_scope(workspace, settings, create=True, keyring_module=keyring)
        username = _provider_account_secret_name(workspace, provider_id)
        deleted_username = _provider_account_deleted_name(workspace, provider_id)
        if secret:
            keyring.set_password(_SERVICE_NAME, username, secret)
            if keyring.get_password(_SERVICE_NAME, username) != secret:
                raise SettingsError("系统凭据保存后校验失败")
            _delete_saved_secret(keyring, deleted_username)
            try:
                _delete_saved_secret(keyring, _provider_secret_name(workspace, provider_id), _legacy_provider_secret_name(workspace, provider_id))
            except Exception:
                pass
        else:
            keyring.set_password(_SERVICE_NAME, deleted_username, "1")
            _delete_saved_secret(keyring, username, _provider_secret_name(workspace, provider_id), _legacy_provider_secret_name(workspace, provider_id))
    except Exception as exc:  # pragma: no cover - desktop keyring differs by OS
        raise SettingsError(f"无法访问系统凭据管理器：{exc}") from exc
    return _with_secret_status(settings, workspace)


def get_provider_api_key(workspace: str | Path, provider_id: str) -> str:
    """Resolve one provider key for a request without exposing it to settings.

    Resolution is performed per call. A saved provider key is shared by this
    OS account only when the provider ID and API endpoint match.
    """

    environment_value, _ = _provider_environment_api_key(provider_id, workspace=workspace)
    if environment_value:
        return environment_value
    try:
        import keyring

        _ensure_credential_scope(workspace, _normalize_settings(_read_raw_settings_file(workspace)), create=False, keyring_module=keyring)
        return _provider_saved_key(keyring, workspace, provider_id).strip()
    except Exception:
        return ""


def provider_request_api_key(
    workspace: str | Path,
    provider: dict[str, Any],
    *,
    key_lookup: Callable[[str | Path, str], str] = get_provider_api_key,
) -> str:
    """Return a request credential while keeping keyless local routes usable."""

    mode = str(provider.get("auth_mode", "")).strip().lower()
    if provider.get("runtime") == "strata" and not _verified_strata_model(load_settings(workspace, include_secret_status=False), provider):
        raise SettingsError("Strata 当前地址或模型未通过 Agent 验证")
    if mode == "managed":
        return "scansci-managed-gateway"
    if mode == "local" or str(provider.get("kind", "")).strip().lower() == "local":
        return "local"
    return key_lookup(workspace, str(provider.get("id", "")))


def set_document_service_api_key(workspace: str | Path, service_id: str, value: str) -> dict[str, Any]:
    """Store a document-service key in the OS credential manager.

    The public settings file contains the selected provider, endpoint, and
    language preferences only.  OCR and MinerU keys follow the exact same
    redaction boundary as chat-model credentials.
    """

    service = _document_service_id(service_id)
    settings = _normalize_settings(_read_raw_settings(workspace))
    secret = str(value or "").strip()
    try:
        import keyring

        settings = _ensure_credential_scope(workspace, settings, create=True, keyring_module=keyring)
        username = _document_service_secret_name(workspace, service)
        if secret:
            keyring.set_password(_SERVICE_NAME, username, secret)
        else:
            _delete_saved_secret(keyring, username, _legacy_document_service_secret_name(workspace, service))
    except Exception as exc:  # pragma: no cover - desktop keyring differs by OS
        raise SettingsError(f"无法访问系统凭据管理器：{exc}") from exc
    return _with_secret_status(settings, workspace)


def get_document_service_api_key(workspace: str | Path, service_id: str) -> str:
    """Read one document-service secret for in-process parsing only."""

    try:
        import keyring

        _ensure_credential_scope(workspace, _normalize_settings(_read_raw_settings_file(workspace)), create=False, keyring_module=keyring)
        service = _document_service_id(service_id)
        return _get_saved_secret(keyring, _document_service_secret_name(workspace, service))
    except Exception:
        return ""


def set_notion_api_token(workspace: str | Path, value: str) -> dict[str, Any]:
    """Store the Notion integration token in the OS credential manager."""

    secret = str(value or "").strip()
    try:
        import keyring

        _ensure_credential_scope(workspace, _normalize_settings(_read_raw_settings_file(workspace)), create=True, keyring_module=keyring)
        username = _notion_secret_name(workspace)
        if secret:
            keyring.set_password(_SERVICE_NAME, username, secret)
        else:
            _delete_saved_secret(keyring, username, _legacy_notion_secret_name(workspace))
    except Exception as exc:  # pragma: no cover - desktop keyring differs by OS
        raise SettingsError(f"无法访问系统凭据管理器：{exc}") from exc
    return load_settings(workspace)


def get_notion_api_token(workspace: str | Path) -> str:
    """Read the Notion token for an in-process API request only."""

    try:
        import keyring

        _ensure_credential_scope(workspace, _normalize_settings(_read_raw_settings_file(workspace)), create=False, keyring_module=keyring)
        return _get_saved_secret(keyring, _notion_secret_name(workspace))
    except Exception:
        return ""


def notion_api_token_configured(workspace: str | Path) -> bool:
    return bool(get_notion_api_token(workspace))


def _read_raw_settings(workspace: str | Path) -> object:
    return _read_raw_settings_file(workspace)


def _normalize_settings(payload: object) -> dict[str, Any]:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise SettingsError("设置必须是一个对象")
    defaults = deepcopy(_DEFAULT_SETTINGS)
    local_models = _normalize_local_models(payload.get("local_models", defaults["local_models"]))
    local_models = _with_discovered_local_models(local_models)
    providers = _with_local_runtime_providers(
        _with_local_huggingface_provider(
            _with_common_provider_catalog(_normalize_providers(payload.get("providers", defaults["providers"])))
        ),
        local_models,
    )
    document_processing = _normalize_document_processing(payload.get("document_processing", defaults["document_processing"]))
    onboarding = _normalize_onboarding(payload.get("onboarding", defaults["onboarding"]))
    appearance = _normalize_appearance(payload.get("appearance", defaults["appearance"]))
    general = _normalize_general(payload.get("general", defaults["general"]))
    skills = _with_builtin_skills(_normalize_records(payload.get("skills", defaults["skills"]), kind="skill"))
    mcp_servers = _normalize_records(payload.get("mcp_servers", defaults["mcp_servers"]), kind="mcp")
    plugins = _with_builtin_plugins(_normalize_records(payload.get("plugins", defaults["plugins"]), kind="plugin"))
    active = payload.get("active_model", defaults["active_model"])
    if not isinstance(active, dict):
        active = {}
    provider_id = _safe_id(active.get("provider_id"), fallback=providers[0]["id"])
    provider = next((item for item in providers if item["id"] == provider_id), providers[0])
    if provider["id"] == _JEV_PROVIDER_ID:
        # JEV is a typed decision service and must never become the ordinary
        # chat model selected by the conversation model picker.
        provider = next((item for item in providers if item["id"] == _MANAGED_PROVIDER_ID), providers[0])
    model_id = _model_id(active.get("model_id"), fallback=provider["models"][0]["id"])
    if not any(model["id"] == model_id for model in provider["models"]):
        model_id = provider["models"][0]["id"]
    # Managed standby routes remain visible for diagnostics, but they are not
    # release-approved conversation models.  Migrate stale workspace choices
    # to the production default every time settings are loaded or saved so an
    # old selection cannot silently serve a user-visible answer.
    if provider["id"] == _MANAGED_PROVIDER_ID and model_id not in _managed_production_model_ids():
        model_id = _DEFAULT_CHAT_MODEL_ID
    model_roles = _normalize_model_roles(payload.get("model_roles", defaults["model_roles"]))
    if not model_roles.get("audio"):
        audio_model = next(
            (
                item
                for item in local_models
                if item.get("runtime") == "local-huggingface"
                and item.get("enabled")
                and "audio" in set(item.get("capabilities", []) or [])
                and item.get("runtime_compatible", True)
            ),
            None,
        )
        if audio_model:
            model_roles["audio"] = f"local:{audio_model['id']}"
    for role, reference in list(model_roles.items()):
        if reference.startswith("provider:local-runtime-"):
            if not any(
                reference == f"provider:{provider['id']}:{model['id']}"
                for provider in providers for model in provider.get("models", [])
            ):
                model_roles[role] = ""
                continue
        prefix = f"provider:{_MANAGED_PROVIDER_ID}:"
        if reference.startswith(prefix) and reference.removeprefix(prefix) not in _managed_production_model_ids():
            model_roles[role] = f"{prefix}{_DEFAULT_CHAT_MODEL_ID}"
        deepseek_prefix = f"provider:{_DEEPSEEK_PROVIDER_ID}:"
        if reference.startswith(deepseek_prefix) and reference.removeprefix(deepseek_prefix) in _DEEPSEEK_LEGACY_MODEL_IDS:
            model_roles[role] = f"{deepseek_prefix}{_DEEPSEEK_FLASH_MODEL_ID}"
    return {
        "schema_version": 2,
        "credential_scope_id": _credential_scope_id(payload),
        "active_model": {"provider_id": provider["id"], "model_id": model_id},
        "providers": providers,
        "local_models": local_models,
        "model_roles": model_roles,
        "document_processing": document_processing,
        "onboarding": onboarding,
        "appearance": appearance,
        "general": general,
        "skills": skills,
        "mcp_servers": mcp_servers,
        "plugins": plugins,
    }


def _verified_strata_model(settings: dict[str, Any], provider: dict[str, Any] | None = None) -> bool:
    local = next((item for item in settings.get("local_models", []) if item.get("id") == "strata" and item.get("runtime") == "strata"), None)
    if not local or not local.get("enabled"):
        return False
    base_url = str(local.get("base_url", "")).strip().rstrip("/")
    model_id = str(local.get("model_id", "")).strip()
    if provider and (
        str(provider.get("base_url", "")).strip().rstrip("/") != base_url
        or not any(str(model.get("id", "")) == model_id for model in provider.get("models", []))
    ):
        return False
    return bool(model_id and _is_strata_loopback_url(base_url) and local.get("agent_verified_for") == f"{base_url}\n{model_id}")


def _strata_in_use(settings: dict[str, Any]) -> bool:
    if settings.get("active_model", {}).get("provider_id") == "local-runtime-strata":
        return True
    return any(str(reference).startswith("provider:local-runtime-strata:") for reference in settings.get("model_roles", {}).values())


def _normalize_local_models(value: object) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(source[:_MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        identifier = _unique_id(_safe_id(item.get("id"), fallback=f"local-model-{index + 1}"), used)
        runtime = _text(item.get("runtime"), fallback="openai-compatible", limit=40).lower()
        if runtime not in {"builtin", "ollama", "lm-studio", "llama.cpp", "strata", "openai-compatible", "local-huggingface"}:
            runtime = "openai-compatible"
        if identifier == "builtin-evidence" and runtime == "builtin":
            name = "离线基础检索（非模型）"
            model_id = "local-hash-v1 / local-lexical-v1"
        else:
            name = _text(item.get("name"), fallback=identifier, limit=100)
            model_id = _text(item.get("model_id"), limit=160)
        capabilities = _normalize_capabilities(item.get("capabilities"), fallback=model_id or identifier)
        runtime_compatible = bool(item.get("runtime_compatible", True))
        # A legacy ASR snapshot can still be present in the Hugging Face cache,
        # but the current in-process loader cannot execute it.  Keep it visible
        # in the installed-model diagnostics; do not expose it as a selectable
        # default or a runnable connection.
        if runtime == "local-huggingface" and model_id == QWEN3_ASR_LEGACY_MODEL_ID:
            continue
        if runtime == "local-huggingface" and "audio" in capabilities and not runtime_compatible:
            continue
        rows.append(
            {
                "id": identifier,
                "name": name,
                "runtime": runtime,
                "base_url": _text(item.get("base_url"), limit=500),
                "model_id": model_id,
                "context_window": _text(item.get("context_window"), limit=24),
                "enabled": bool(item.get("enabled", True)),
                "capabilities": capabilities,
                # Runtime diagnostics are facts discovered by the installer;
                # preserving them lets the UI explain an incompatible model
                # instead of presenting a misleading download/select action.
                "runtime_compatible": runtime_compatible,
                "runtime_backend": _text(item.get("runtime_backend"), limit=80),
                "runtime_message": _text(item.get("runtime_message"), limit=500),
                "agent_verified_for": _text(item.get("agent_verified_for"), limit=700) if runtime == "strata" else "",
            }
        )
    return rows or deepcopy(_DEFAULT_SETTINGS["local_models"])


def _with_discovered_local_models(local_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose downloaded local models that can be selected by capability.

    Chat and vision snapshots are exposed by the local Hugging Face provider;
    embedding and reranking models are used by the evidence stack directly,
    so they need a small role-routable record of their own.  Audio keeps the
    same treatment because it is executed by the native ASR loader.
    """

    rows = [dict(item) for item in local_models]
    known = {str(item.get("model_id", "")) for item in rows}
    for item in installed_models():
        model_id = str(item.get("id", "")).strip()
        kind = str(item.get("kind", "")).strip().lower()
        # Keep compatibility with older discovery records that predate the
        # explicit ``kind`` field.  Qwen3-ASR is the only audio snapshot we
        # currently execute in-process.
        if not kind and ("asr" in model_id.casefold() or "whisper" in model_id.casefold()):
            kind = "audio"
        if not item.get("ready") or kind not in {"audio", "embedding", "reranking"}:
            continue
        if kind == "audio" and item.get("runtime_compatible") is False:
            continue
        if not model_id or model_id in known:
            continue
        identifier = _safe_id(f"{kind}-{model_id}", fallback=f"local-{kind}")
        existing = next((row for row in rows if str(row.get("id", "")) == identifier), None)
        if existing is not None:
            if existing.get("runtime") == "local-huggingface" and not existing.get("model_id"):
                existing["model_id"] = model_id
                known.add(model_id)
            continue
        rows.append(
            {
                "id": identifier,
                "name": (
                    "Qwen3 ASR 0.6B（语音识别）"
                    if model_id == QWEN3_ASR_NATIVE_MODEL_ID
                    else str(item.get("name") or model_id)
                ),
                "runtime": "local-huggingface",
                "base_url": "",
                "model_id": model_id,
                "enabled": True,
                "capabilities": [kind],
                "runtime_compatible": bool(item.get("runtime_compatible", True)),
                "runtime_backend": str(item.get("runtime_backend", "unsupported")),
                "runtime_message": str(item.get("runtime_message", "")),
            }
        )
        known.add(model_id)
    return rows


def _with_local_runtime_providers(providers: list[dict[str, Any]], local_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn configured Ollama/loopback entries into selectable model providers."""

    rows = [
        item
        for item in providers
        if not str(item.get("id", "")).startswith("local-runtime-")
    ]
    for local in local_models:
        runtime = str(local.get("runtime", "")).strip().lower()
        model_id = str(local.get("model_id", "")).strip()
        base_url = str(local.get("base_url", "")).strip()
        if runtime in {"builtin", "local-huggingface"} or not local.get("enabled") or not model_id or not base_url:
            continue
        if runtime == "strata" and not _is_strata_loopback_url(base_url):
            continue
        provider_id = f"local-runtime-{_safe_id(local.get('id'), fallback='model')}"
        capabilities = _normalize_capabilities(local.get("capabilities"), fallback=model_id)
        rows.append(
            {
                "id": provider_id,
                "name": f"本地 · {local.get('name') or model_id}",
                "kind": "openai-compatible",
                "base_url": base_url,
                "api_surface": "chat_completions",
                "responses_enabled": False,
                "enabled": True,
                "logo": "ollama" if runtime == "ollama" else "local",
                "category": "本地模型",
                "summary": "通过本机运行时提供，不会上传本地文件。",
                "auth_mode": "local",
                "model_listing": False,
                "local_model_id": str(local.get("id", "")),
                "runtime": runtime,
                "models": [
                    {
                        "id": model_id,
                        "name": str(local.get("name") or model_id),
                        "group": f"本地 {runtime}",
                        "context_window": str(local.get("context_window") or "本机"),
                        "capabilities": capabilities,
                    }
                ],
            }
        )
    return rows


def _is_strata_loopback_url(value: str) -> bool:
    try:
        endpoint = urlsplit(str(value or "").strip().rstrip("/"))
        return (
            endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "::1"}
            and endpoint.port is not None and endpoint.path == "/v1"
            and not (endpoint.username or endpoint.password or endpoint.query or endpoint.fragment)
        )
    except ValueError:
        return False


def with_discovered_local_runtime_models(
    settings: dict[str, Any],
    inventories: dict[str, list[object]],
) -> dict[str, Any]:
    """Merge models reported by configured loopback runtimes into the catalog.

    Runtime discovery stays ephemeral: callers can expose the richer catalog
    without turning a health-check request into a settings write. Existing
    rows remain authoritative, while ``:latest`` aliases are de-duplicated.
    """

    result = deepcopy(settings)
    rows = [dict(item) for item in list(result.get("local_models", []) or []) if isinstance(item, dict)]
    sources = list(rows)
    used_ids = {str(item.get("id", "")) for item in rows if str(item.get("id", "")).strip()}

    def runtime_key(item: dict[str, Any], model_id: str) -> tuple[str, str, str]:
        runtime = str(item.get("runtime", "")).strip().lower()
        base_url = str(item.get("base_url", "")).strip().rstrip("/").casefold()
        alias = str(model_id or "").strip().casefold()
        if alias.endswith(":latest"):
            alias = alias[:-7]
        return runtime, base_url, alias

    known = {
        runtime_key(item, str(item.get("model_id", "")))
        for item in rows
        if str(item.get("model_id", "")).strip()
    }
    for source in sources:
        runtime = str(source.get("runtime", "")).strip().lower()
        base_url = str(source.get("base_url", "")).strip().rstrip("/")
        source_id = str(source.get("id", "")).strip()
        if runtime in {"builtin", "local-huggingface"} or not source.get("enabled") or not base_url:
            continue
        for raw_model in list(inventories.get(source_id, []) or [])[:_MAX_ITEMS]:
            item = raw_model if isinstance(raw_model, dict) else {}
            model_id = str(
                item.get("id")
                or item.get("model")
                or item.get("name")
                or (raw_model if isinstance(raw_model, str) else "")
            ).strip()
            key = runtime_key(source, model_id)
            if not model_id or key in known:
                continue
            declared = item.get("capabilities") if isinstance(item.get("capabilities"), list) else []
            declared = ["tool" if str(value).strip().lower() in {"tools", "tool_calling"} else value for value in declared]
            capabilities = _normalize_capabilities(declared, fallback=model_id)
            display_name = str(item.get("display_name") or item.get("name") or model_id).strip()
            if display_name.endswith(":latest"):
                display_name = display_name[:-7]
            identifier = _unique_id(
                _safe_id(f"{runtime}-{model_id}", fallback=f"{runtime}-model"),
                used_ids,
            )
            rows.append(
                {
                    "id": identifier,
                    "name": display_name or model_id,
                    "runtime": runtime,
                    "base_url": base_url,
                    "model_id": model_id,
                    "context_window": str(item.get("context_window") or item.get("context_length") or "").strip()[:24],
                    "enabled": True,
                    "capabilities": capabilities,
                    "runtime_compatible": True,
                    "runtime_backend": runtime,
                    "runtime_message": "",
                    "discovered": runtime == "strata",
                }
            )
            known.add(key)
            if len(rows) >= _MAX_ITEMS:
                break
        if len(rows) >= _MAX_ITEMS:
            break

    result["local_models"] = rows
    result["providers"] = _with_local_runtime_providers(
        list(result.get("providers", []) or []),
        rows,
    )
    return result


def _with_local_huggingface_provider(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose complete HF snapshots as selectable local text/vision models.

    This row is derived from the local cache each time settings are normalized.
    It intentionally contains no token, endpoint configuration or duplicated
    download metadata: the runtime is loopback-only and starts lazily when a
    user actually sends a message.
    """

    def is_chat_model(item: dict[str, Any]) -> bool:
        marker = " ".join(
            [
                str(item.get("id", "")),
                str(item.get("model_type", "")),
            ]
        ).casefold()
        architecture = str(item.get("architecture", "")).casefold()
        is_generation_architecture = "forcausallm" in architecture or "forconditionalgeneration" in architecture
        discovered = {
            str(value).strip().casefold()
            for value in item.get("capabilities", [])
            if str(value).strip()
        }
        return (
            bool(item.get("model_files_present", item.get("ready")))
            and str(item.get("format", "")) == "transformers"
            and is_generation_architecture
            and "audio" not in discovered
            and not any(word in marker for word in ("embedding", "reranker", "asr", "whisper", "reward"))
            and not any(word in marker for word in ("tts", "text-to-speech", "vocoder"))
        )

    def capabilities_for(item: dict[str, Any]) -> list[str]:
        # Preserve every capability discovered from the local snapshot.  A
        # vision-language model is usually also a conversation model; do not
        # collapse it to a single exclusive ``kind`` when building the local
        # provider catalog.
        discovered = item.get("capabilities") if isinstance(item.get("capabilities"), list) else []
        capabilities = [
            str(value).strip().lower()
            for value in discovered
            if str(value).strip().lower() in {"chat", "vision", "audio", "embedding", "reranking"}
        ]
        if not any(item in capabilities for item in ("audio", "embedding", "reranking")):
            capabilities.extend(["reasoning", "coding"])
        marker = " ".join(
            [
                str(item.get("id", "")),
                str(item.get("model_type", "")),
                str(item.get("kind", "")),
            ]
        ).casefold()
        architecture = str(item.get("architecture", "")).casefold()
        if str(item.get("kind", "")).casefold() == "vision" or "minicpmv" in marker or "vision" in marker or "image" in architecture:
            capabilities.append("vision")
        return list(dict.fromkeys(capabilities))

    previous = next((item for item in providers if str(item.get("id", "")) == "local-huggingface"), None)
    models: list[dict[str, Any]] = []
    for item in installed_models():
        if not is_chat_model(item):
            continue
        model = {
            "id": str(item["id"]),
            "name": str(item.get("name") or item["id"]),
            "group": "本地 Hugging Face",
            "context_window": "本机",
            "capabilities": capabilities_for(item),
        }
        if not bool(item.get("ready")) or item.get("runtime_compatible") is False:
            model["available"] = False
            model["unavailable_reason"] = str(
                item.get("runtime_message")
                or item.get("runtime_compatibility_message")
                or "模型文件已存在，当前本地运行组件尚未完成可用性验证。"
            )[:160]
        source_root = str(item.get("source_root", "")).strip()
        if source_root:
            model["source_root"] = source_root
        models.append(model)
    discovered_ids = {str(model["id"]).casefold() for model in models}
    missing_roots = missing_configured_model_roots()
    missing_keys = {item.casefold() for item in missing_roots}
    if previous:
        for model in previous.get("models", []):
            source_root = str(model.get("source_root", "")).strip()
            unavailable = source_root.casefold() in missing_keys if source_root else False
            if source_root:
                if not unavailable:
                    try:
                        unavailable = not Path(source_root).expanduser().exists()
                    except OSError:
                        unavailable = True
                if unavailable and source_root.casefold() not in missing_keys:
                    missing_roots.append(source_root)
                    missing_keys.add(source_root.casefold())
            elif missing_roots:
                unavailable = True
            model_id = str(model.get("id", "")).casefold()
            if unavailable and model_id not in discovered_ids:
                models.append(
                    {
                        **model,
                        "available": False,
                        "unavailable_reason": "模型目录当前不可访问",
                    }
                )
    rows = [item for item in providers if str(item.get("id", "")) != "local-huggingface"]
    if models:
        rows.append(
            {
                "id": "local-huggingface",
                "name": "本地 Hugging Face",
                "kind": "local",
                "base_url": "http://127.0.0.1:17863/v1",
                "enabled": bool(previous.get("enabled", True)) if previous else True,
                "logo": "local-huggingface",
                "category": "本地模型",
                "summary": "由 ScanSci 在本机按需启动；模型权重始终保留在本地。",
                "auth_mode": "local",
                "model_listing": False,
                "models": models,
                "missing_roots": missing_roots,
            }
        )
    return rows


def _normalize_model_roles(value: object) -> dict[str, str]:
    defaults = deepcopy(_DEFAULT_SETTINGS["model_roles"])
    if not isinstance(value, dict):
        return defaults
    return {
        role: _text(value.get(role), fallback=str(default), limit=240)
        for role, default in defaults.items()
    }


def _normalize_onboarding(value: object) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    return {
        "welcome_dismissed": bool(source.get("welcome_dismissed", False)),
        "resource_setup_completed": bool(source.get("resource_setup_completed", False)),
        "data_setup_completed": bool(source.get("data_setup_completed", False)),
    }


def _normalize_appearance(value: object) -> dict[str, str]:
    """Keep desktop presentation preferences small, portable, and predictable."""

    source = value if isinstance(value, dict) else {}
    locale = _text(source.get("locale"), fallback="zh-CN", limit=16)
    theme = _text(source.get("theme"), fallback="system", limit=16).lower()
    accent = _text(source.get("accent"), fallback="jade", limit=16).lower()
    font_scale = _text(source.get("font_scale"), fallback="medium", limit=16).lower()
    return {
        "locale": locale if locale in {"zh-CN", "en"} else "zh-CN",
        "theme": theme if theme in {"system", "light", "dark"} else "system",
        "accent": accent if accent in {"jade", "ocean", "plum", "amber"} else "jade",
        "font_scale": font_scale if font_scale in {"small", "medium", "large"} else "medium",
    }


def _normalize_general(value: object) -> dict[str, Any]:
    """Normalize behavior and directory preferences shown under General settings."""

    defaults = deepcopy(_DEFAULT_SETTINGS["general"])
    source = value if isinstance(value, dict) else {}
    conversation_source = source.get("conversation") if isinstance(source.get("conversation"), dict) else {}
    directories_source = source.get("directories") if isinstance(source.get("directories"), dict) else {}
    updates_source = source.get("updates") if isinstance(source.get("updates"), dict) else {}
    send_shortcut = _text(conversation_source.get("send_shortcut"), fallback=defaults["conversation"]["send_shortcut"], limit=24).lower()
    if send_shortcut not in {"enter", "shift-enter"}:
        send_shortcut = defaults["conversation"]["send_shortcut"]
    return {
        "conversation": {
            "send_shortcut": send_shortcut,
            "completion_notifications": bool(conversation_source.get("completion_notifications", defaults["conversation"]["completion_notifications"])),
            "agent_completion_notifications": bool(conversation_source.get("agent_completion_notifications", defaults["conversation"]["agent_completion_notifications"])),
            "subagent_completion_notifications": bool(conversation_source.get("subagent_completion_notifications", defaults["conversation"]["subagent_completion_notifications"])),
        },
        "directories": {
            "default_workspace": _text(directories_source.get("default_workspace"), limit=1000),
            "conversation_workspace": _text(directories_source.get("conversation_workspace"), limit=1000),
            "model_cache": _normalize_storage_path(directories_source.get("model_cache")),
            "local_runtime": _normalize_storage_path(directories_source.get("local_runtime")),
            "vector_index": _normalize_storage_path(directories_source.get("vector_index")),
        },
        "updates": {
            "auto_check": bool(updates_source.get("auto_check", defaults["updates"]["auto_check"])),
        },
    }


def _normalize_storage_path(value: object) -> str:
    """Normalize an optional absolute directory without creating it.

    A selected location may not exist yet (for example, a new empty drive),
    so validation is intentionally lexical and does not require ``is_dir``.
    Relative paths are ignored rather than interpreted relative to the
    workspace, avoiding an unexpected C: drive allocation.
    """

    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or any(ord(char) < 32 for char in text):
        return ""
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            return ""
        # ``absolute`` normalizes ``.``/``..`` without case-folding a Windows
        # drive path; retaining user-visible casing makes the setting easier
        # to recognize in the UI and in support reports.
        return str(path.absolute())
    except (OSError, RuntimeError, ValueError):
        return ""


def vector_index_directory(settings: object) -> str:
    """Return the normalized vector-index directory override from settings."""

    source = settings if isinstance(settings, dict) else {}
    general = source.get("general") if isinstance(source.get("general"), dict) else {}
    directories = general.get("directories") if isinstance(general.get("directories"), dict) else {}
    return _normalize_storage_path(directories.get("vector_index"))


def apply_storage_directories(settings: object) -> dict[str, str]:
    """Apply persisted model/runtime/vector locations to the current process.

    The desktop creates download/runtime managers after loading settings, so
    this function is called during startup and after a settings save.  The
    original environment is restored when a user resets a location, which is
    important for developer shells and packaged upgrades.  Vector indexes use
    a process-local resolver instead of an environment variable.
    """

    source = settings if isinstance(settings, dict) else {}
    general = source.get("general") if isinstance(source.get("general"), dict) else {}
    directories = general.get("directories") if isinstance(general.get("directories"), dict) else {}
    values = {
        "HF_HUB_CACHE": _normalize_storage_path(directories.get("model_cache")),
        "SCANSCI_LOCAL_RUNTIME_ROOT": _normalize_storage_path(directories.get("local_runtime")),
    }
    vector_index = vector_index_directory(settings)
    configure_vector_index_root(vector_index or None)
    for name, value in values.items():
        current = os.environ.get(name)
        # A test harness, shell, or host application can change the process
        # environment after ScanSci has applied a setting.  Treat that as a
        # new baseline; otherwise clearing a setting would restore the value
        # from an earlier startup instead of the host's current value.
        if name not in _STORAGE_ENV_BASELINE or (
            name in _STORAGE_ENV_LAST_APPLIED and current != _STORAGE_ENV_LAST_APPLIED[name]
        ):
            _STORAGE_ENV_BASELINE[name] = current
        baseline = _STORAGE_ENV_BASELINE[name]
        if value:
            os.environ[name] = value
        elif baseline is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = baseline
        _STORAGE_ENV_LAST_APPLIED[name] = os.environ.get(name)
    return {
        "model_cache": values["HF_HUB_CACHE"],
        "local_runtime": values["SCANSCI_LOCAL_RUNTIME_ROOT"],
        "vector_index": vector_index,
    }


def _normalize_document_processing(value: object) -> dict[str, Any]:
    """Validate the small public surface for OCR and scientific PDF parsing."""

    defaults = deepcopy(_DEFAULT_SETTINGS["document_processing"])
    source = value if isinstance(value, dict) else {}
    ocr_source = source.get("ocr") if isinstance(source.get("ocr"), dict) else {}
    mineru_source = source.get("mineru") if isinstance(source.get("mineru"), dict) else {}

    ocr_provider = _text(ocr_source.get("provider"), fallback=defaults["ocr"]["provider"], limit=32).lower()
    if ocr_provider not in {"tesseract", "system", "paddle", "deepseek", "custom"}:
        ocr_provider = "tesseract"
    language_source = ocr_source.get("languages") if isinstance(ocr_source.get("languages"), list) else []
    languages = [
        code
        for code in (_text(item, limit=12).lower() for item in language_source)
        if code in {"zh", "en"}
    ]
    if not languages:
        languages = list(defaults["ocr"]["languages"])

    mineru_provider = _text(mineru_source.get("provider"), fallback=defaults["mineru"]["provider"], limit=32).lower()
    if mineru_provider not in {"mineru", "custom"}:
        mineru_provider = "mineru"
    mineru_base_url = _text(mineru_source.get("base_url"), fallback=defaults["mineru"]["base_url"], limit=500)

    return {
        "ocr": {
            "provider": ocr_provider,
            "base_url": _text(ocr_source.get("base_url"), limit=500),
            "languages": list(dict.fromkeys(languages)),
            "enabled": bool(ocr_source.get("enabled", defaults["ocr"]["enabled"])),
        },
        "mineru": {
            "provider": mineru_provider,
            "base_url": mineru_base_url,
            "enabled": bool(mineru_source.get("enabled", defaults["mineru"]["enabled"])),
        },
    }


def _normalize_providers(value: object) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(source[:_MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "openai-compatible") or "openai-compatible").strip().lower()
        if kind not in {"local", "openai-compatible", "anthropic-compatible"}:
            kind = "openai-compatible"
        fallback = "local-evidence" if kind == "local" else f"provider-{index + 1}"
        identifier = _unique_id(_safe_id(item.get("id"), fallback=fallback), used)
        models_source = item.get("models") if isinstance(item.get("models"), list) else []
        models = _normalize_models(models_source, fallback="evidence-retrieval" if kind == "local" else "model")
        if identifier == "local-evidence" and kind == "local":
            name = "离线基础检索（非模型）"
            models = [
                {
                    **model,
                    "name": "关键词检索与词法重排（非模型）",
                    "group": "离线回退",
                }
                if str(model.get("id", "")) == "evidence-retrieval"
                else model
                for model in models
            ]
        else:
            name = _text(item.get("name"), fallback="本地证据引擎" if kind == "local" else "未命名提供商", limit=80)
        base_url = _text(item.get("base_url"), limit=500)
        zhipu_endpoints: dict[str, Any] = {}
        if identifier == _ZHIPU_PROVIDER_ID:
            coding_plan_enabled = bool(item.get("coding_plan_enabled", False))
            if "standard_base_url" in item:
                standard_base_url = _text(item.get("standard_base_url"), limit=500)
            elif coding_plan_enabled:
                standard_base_url = _ZHIPU_STANDARD_BASE_URL
            else:
                standard_base_url = base_url
            if "coding_plan_base_url" in item:
                coding_plan_base_url = _text(item.get("coding_plan_base_url"), fallback=_ZHIPU_CODING_PLAN_BASE_URL, limit=500)
            elif coding_plan_enabled and base_url:
                coding_plan_base_url = base_url
            else:
                coding_plan_base_url = _ZHIPU_CODING_PLAN_BASE_URL
            base_url = coding_plan_base_url if coding_plan_enabled else standard_base_url
            zhipu_endpoints = {
                "standard_base_url": standard_base_url,
                "coding_plan_base_url": coding_plan_base_url,
                "coding_plan_enabled": coding_plan_enabled,
            }
        rows.append(
            {
                "id": identifier,
                "name": name,
                "kind": kind,
                "base_url": base_url,
                **zhipu_endpoints,
                "api_surface": (
                    _text(item.get("api_surface"), fallback="chat_completions", limit=32).lower()
                    if _text(item.get("api_surface"), fallback="chat_completions", limit=32).lower()
                    in {"auto", "chat_completions", "responses", "anthropic_messages", "jev_systemone"}
                    else "chat_completions"
                ),
                "responses_enabled": bool(item.get("responses_enabled", False)),
                "enabled": bool(item.get("enabled", True)),
                "remote_rag_allowed": item.get("remote_rag_allowed") is True,
                "logo": _text(item.get("logo"), fallback=identifier, limit=80),
                "category": _text(item.get("category"), fallback="自定义提供商", limit=48),
                "summary": _text(item.get("summary"), limit=280),
                "auth_mode": _text(item.get("auth_mode"), fallback="key", limit=32).lower().replace("api_key", "key"),
                "model_listing": bool(item.get("model_listing", True)),
                "models": models,
            }
        )
    if rows:
        return rows
    return deepcopy(_DEFAULT_SETTINGS["providers"])


def _with_common_provider_catalog(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the common cloud catalog visible without enabling unconfigured APIs.

    Existing user records win over a preset with the same id.  New catalog rows
    are deliberately disabled until a user saves an API key or explicitly turns
    the provider on, so they never appear as accidentally selectable models.
    """

    rows = list(providers)
    catalog = {preset["id"]: preset for preset in _PROVIDER_PRESETS}
    for index, row in enumerate(rows):
        preset = catalog.get(str(row.get("id", "")))
        if not preset:
            continue
        # Preserve the user's endpoint, enablement, and selected models while
        # refreshing presentation and adapter metadata from the catalog.
        refreshed = {
            **row,
            "category": preset["category"],
            "logo": preset["logo"],
            "summary": preset["summary"],
            "auth_mode": preset["auth_mode"],
            "model_listing": preset["model_listing"],
        }
        if row.get("id") == _MANAGED_PROVIDER_ID:
            # ScanSci is product-owned catalog data rather than a user-named
            # custom provider. Refresh stale labels and model metadata from
            # earlier workspaces without touching their active-model choice.
            refreshed["name"] = _MANAGED_PROVIDER_NAME
            refreshed["kind"] = preset["kind"]
            refreshed["base_url"] = preset["base_url"]
            existing_models = {
                str(model.get("id", "")): model
                for model in refreshed["models"]
                if str(model.get("id", ""))
            }
            refreshed["models"] = [
                {
                    **existing_models.get(str(model["id"]), {}),
                    **deepcopy(model),
                }
                for model in preset["models"]
            ]
        elif row.get("id") == _JEV_PROVIDER_ID:
            # JEV uses TypeSafe's typed evaluation endpoint rather than the
            # chat-completions protocol. Keep the official endpoint visible
            # for older workspaces that received the provider before the
            # transport adapter was added, while retaining an explicit custom
            # proxy endpoint if the user chose one.
            refreshed["name"] = preset["name"]
            refreshed["kind"] = preset["kind"]
            refreshed["api_surface"] = preset["api_surface"]
            refreshed["responses_enabled"] = preset["responses_enabled"]
            if not str(refreshed.get("base_url", "")).strip():
                refreshed["base_url"] = preset["base_url"]
            existing_models = {
                str(model.get("id", "")): model
                for model in refreshed["models"]
                if str(model.get("id", ""))
            }
            preset_ids = {str(model["id"]) for model in preset["models"]}
            refreshed["models"] = [
                {
                    **existing_models.get(str(model["id"]), {}),
                    **deepcopy(model),
                }
                for model in preset["models"]
            ] + [
                model
                for model in refreshed["models"]
                if str(model.get("id", "")) not in preset_ids
            ]
        elif row.get("id") == "paratera":
            # Add newly supported transports without replacing user model edits,
            # endpoint, enablement or default role choices.
            known_models = {str(model.get("id", "")) for model in refreshed["models"]}
            refreshed["models"] = list(refreshed["models"]) + [
                deepcopy(model) for model in preset["models"] if model["id"] not in known_models
            ]
        elif row.get("id") == "siliconflow":
            # SiliconFlow's evidence models are product catalog entries too.
            # Merge newly shipped presets into an existing workspace while
            # retaining any user-added model IDs that are not in the catalog.
            existing_models = {
                str(model.get("id", "")): model
                for model in refreshed["models"]
                if str(model.get("id", ""))
            }
            preset_ids = {str(model["id"]) for model in preset["models"]}
            refreshed["models"] = [
                {
                    **existing_models.get(str(model["id"]), {}),
                    **deepcopy(model),
                }
                for model in preset["models"]
            ] + [
                model
                for model in refreshed["models"]
                if str(model.get("id", "")) not in preset_ids
            ]
        elif row.get("id") == _DEEPSEEK_PROVIDER_ID:
            # DeepSeek's old Chat/Reasoner aliases are no longer offered by
            # the product. Replace those presets during normalization so an
            # existing workspace gets the same two exact choices as a fresh
            # install, while preserving any model the user added manually.
            existing_models = {
                str(model.get("id", "")): model
                for model in refreshed["models"]
                if str(model.get("id", ""))
            }
            preset_ids = {str(model["id"]) for model in preset["models"]}
            refreshed["models"] = [
                {
                    **existing_models.get(str(model["id"]), {}),
                    **deepcopy(model),
                }
                for model in preset["models"]
            ] + [
                model
                for model in refreshed["models"]
                if str(model.get("id", "")) not in _DEEPSEEK_LEGACY_MODEL_IDS
                and str(model.get("id", "")) not in preset_ids
            ]
        elif row.get("id") == _OPENCODE_GO_PROVIDER_ID:
            # OpenCode Go has one stable catalog endpoint but its model families
            # use different protocol surfaces. Refresh the built-in route when
            # an existing workspace first sees this provider, while retaining
            # a custom endpoint and any model IDs the user added manually.
            refreshed["name"] = preset["name"]
            refreshed["kind"] = preset["kind"]
            refreshed["api_surface"] = preset["api_surface"]
            refreshed["responses_enabled"] = preset["responses_enabled"]
            if not str(refreshed.get("base_url", "")).strip():
                refreshed["base_url"] = preset["base_url"]
            existing_models = {
                str(model.get("id", "")): model
                for model in refreshed["models"]
                if str(model.get("id", ""))
            }
            preset_ids = {str(model["id"]) for model in preset["models"]}
            refreshed["models"] = [
                {
                    **existing_models.get(str(model["id"]), {}),
                    **deepcopy(model),
                }
                for model in preset["models"]
            ] + [
                model
                for model in refreshed["models"]
                if str(model.get("id", "")) not in preset_ids
            ]
        rows[index] = refreshed
    known_ids = {str(provider.get("id", "")) for provider in rows}
    for preset in _PROVIDER_PRESETS:
        if preset["id"] in known_ids:
            continue
        row = deepcopy(preset)
        row["enabled"] = preset["id"] == _MANAGED_PROVIDER_ID
        if preset["id"] == _JEV_PROVIDER_ID:
            managed_index = next((index for index, item in enumerate(rows) if item.get("id") == _MANAGED_PROVIDER_ID), -1)
            rows.insert(managed_index + 1 if managed_index >= 0 else len(rows), row)
        else:
            rows.append(row)
    return rows


_MODEL_CAPABILITIES = frozenset({"reasoning", "vision", "embedding", "reranking", "tool", "coding", "image", "audio"})


def _infer_model_capabilities(identifier: str) -> list[str]:
    """Give imported OpenAI-compatible model IDs a useful initial profile."""

    value = identifier.lower()
    capabilities: list[str] = []
    if any(token in value for token in ("embed", "bge-", "gte-")):
        capabilities.append("embedding")
    if any(token in value for token in ("rerank", "reranker")):
        capabilities.append("reranking")
    if any(token in value for token in ("vision", "-vl", "4v", "omni", "image")):
        capabilities.append("vision")
    if any(token in value for token in ("reasoner", "-r1", "thinking", "o1")):
        capabilities.append("reasoning")
    if "code" in value:
        capabilities.append("coding")
    if any(token in value for token in ("audio", "tts", "speech", "whisper", "asr", "vocoder")):
        capabilities.append("audio")
    return capabilities or ["reasoning"]


def _normalize_capabilities(value: object, *, fallback: str) -> list[str]:
    marker = str(fallback or "").casefold()
    if any(token in marker for token in ("audio", "tts", "speech", "whisper", "asr", "vocoder")):
        return ["audio"]
    source = value if isinstance(value, list) else []
    capabilities = [
        _text(item, limit=24).lower()
        for item in source[:16]
        if _text(item, limit=24).lower() in _MODEL_CAPABILITIES
    ]
    return list(dict.fromkeys(capabilities)) or _infer_model_capabilities(fallback)


def _normalize_models(value: object, *, fallback: str) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(source[:_MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        identifier = _unique_id(_model_id(item.get("id"), fallback=f"model-{index + 1}"), used)
        row = {
            "id": identifier,
            "name": _text(item.get("name"), fallback=identifier, limit=120),
            "group": _text(item.get("group"), fallback="默认模型", limit=80),
            "context_window": _text(item.get("context_window"), fallback="", limit=40),
            "capabilities": _normalize_capabilities(item.get("capabilities"), fallback=identifier),
            "show_in_composer": item.get("show_in_composer") is not False,
            "readiness": _text(item.get("readiness"), fallback="production", limit=24)
            if _text(item.get("readiness"), fallback="production", limit=24) in {"production", "standby"}
            else "production",
            "automatic_fallback": bool(item.get("automatic_fallback")),
        }
        source_root = _text(item.get("source_root"), limit=1_000)
        unavailable_reason = _text(item.get("unavailable_reason"), limit=160)
        if source_root:
            row["source_root"] = source_root
        if item.get("available") is False:
            row["available"] = False
        if unavailable_reason:
            row["unavailable_reason"] = unavailable_reason
        rows.append(row)
    if rows:
        return rows
    return [{
        "id": fallback,
        "name": "证据检索" if fallback == "evidence-retrieval" else fallback,
        "group": "ScanSci" if fallback == "evidence-retrieval" else "默认模型",
        "context_window": "本地" if fallback == "evidence-retrieval" else "",
        "capabilities": ["embedding", "reranking"] if fallback == "evidence-retrieval" else ["reasoning"],
        "show_in_composer": True,
    }]


def _mcp_tool_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not name or len(name) > _MAX_MCP_TOOL_NAME_LENGTH:
        return ""
    if any(not character.isprintable() or character.isspace() for character in name):
        return ""
    return name


def _mcp_tool_effect(value: object) -> str:
    if not isinstance(value, str):
        return ""
    effect = value.strip().lower()
    return effect if effect in _MCP_TOOL_EFFECTS else ""


def _normalize_mcp_tool_effects(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    effects: dict[str, str] = {}
    for raw_name, raw_effect in list(value.items())[:_MAX_ITEMS]:
        name = _mcp_tool_name(raw_name)
        effect = _mcp_tool_effect(raw_effect)
        if name and effect and name not in effects:
            effects[name] = effect
        if len(effects) >= _MAX_MCP_TOOL_POLICIES:
            break
    return effects


def _normalize_mcp_tool_policies(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        source: list[object] = [
            {**raw_policy, "name": raw_name}
            if isinstance(raw_policy, dict)
            else {"name": raw_name, "effect": raw_policy}
            for raw_name, raw_policy in list(value.items())[:_MAX_ITEMS]
        ]
    elif isinstance(value, list):
        source = value[:_MAX_ITEMS]
    else:
        return []
    policies: list[dict[str, Any]] = []
    used: set[str] = set()
    for raw_policy in source:
        if not isinstance(raw_policy, dict):
            continue
        name = _mcp_tool_name(raw_policy.get("name") or raw_policy.get("tool"))
        effect = _mcp_tool_effect(raw_policy.get("effect"))
        if not name or not effect or name in used:
            continue
        policy: dict[str, Any] = {"name": name, "effect": effect}
        if isinstance(raw_policy.get("idempotent"), bool):
            policy["idempotent"] = raw_policy["idempotent"]
        freshness = str(raw_policy.get("freshness", "") or "").strip().lower()
        if freshness in {"volatile", "turn", "run"}:
            policy["freshness"] = freshness
        policies.append(policy)
        used.add(name)
        if len(policies) >= _MAX_MCP_TOOL_POLICIES:
            break
    return policies


def _normalize_records(value: object, *, kind: str) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(source[:_MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        identifier = _unique_id(_safe_id(item.get("id"), fallback=f"{kind}-{index + 1}"), used)
        row = {
            "id": identifier,
            "name": _text(item.get("name"), fallback=f"未命名{kind}", limit=100),
            "description": _text(item.get("description"), limit=400),
            "enabled": bool(item.get("enabled", True)),
            "uninstalled": bool(item.get("uninstalled", False)),
        }
        if kind == "skill":
            row["path"] = _text(item.get("path"), limit=500)
            # Installed Skill packages carry their provenance so the extension
            # hub can distinguish built-ins from local, Git, archive, and
            # marketplace imports.  These fields are deliberately metadata
            # only: no executable command is persisted or launched here.
            row["source_type"] = _text(item.get("source_type"), limit=32)
            row["source"] = _text(item.get("source"), limit=1_000)
            row["installed_at"] = _text(item.get("installed_at"), limit=48)
            row["updated_at"] = _text(item.get("updated_at"), limit=48)
            security_scan = item.get("security_scan")
            if isinstance(security_scan, dict):
                row["security_scan"] = {
                    "version": _text(security_scan.get("version"), limit=80),
                    "verdict": _text(security_scan.get("verdict"), limit=16).upper(),
                    "scanned_at": _text(security_scan.get("scanned_at"), limit=48),
                    "fingerprint": _text(security_scan.get("fingerprint"), limit=100),
                    "package_count": max(0, int(security_scan.get("package_count", 0) or 0)),
                    "file_count": max(0, int(security_scan.get("file_count", 0) or 0)),
                    "byte_count": max(0, int(security_scan.get("byte_count", 0) or 0)),
                    "counts": dict(security_scan.get("counts", {}) or {}) if isinstance(security_scan.get("counts"), dict) else {},
                    "scanners": list(security_scan.get("scanners", []) or [])[:32],
                    "findings": list(security_scan.get("findings", []) or [])[:80],
                    "recommendation": _text(security_scan.get("recommendation"), limit=500),
                }
        elif kind == "mcp":
            row["command"] = _text(item.get("command"), limit=500)
            row["args"] = _text(item.get("args"), limit=1_000)
            # Marketplace imports add provenance and connection metadata.  It
            # remains configuration only: persisting a server never executes
            # its command or contacts its endpoint.
            row["catalog_id"] = _text(item.get("catalog_id"), limit=200)
            row["source"] = _text(item.get("source"), limit=120)
            row["source_url"] = _text(item.get("source_url"), limit=500)
            row["discipline"] = _text(item.get("discipline"), limit=48)
            row["transport"] = _text(item.get("transport"), fallback="stdio", limit=40).lower()
            if row["transport"] not in {"stdio", "streamable-http", "sse"}:
                row["transport"] = "stdio"
            row["endpoint"] = _text(item.get("endpoint"), limit=500)
            row["version"] = _text(item.get("version"), limit=80)
            row["updated_at"] = _text(item.get("updated_at"), limit=48)
            connector_kind = _text(item.get("connector_kind"), limit=32).lower()
            row["connector_kind"] = connector_kind if connector_kind in {"", "zotero", "obsidian", "general", "literature"} else "general"
            provider_kind = _text(item.get("provider_kind"), limit=48).lower()
            if not provider_kind and row["connector_kind"] == "literature":
                provider_kind = "literature"
            row["provider_kind"] = provider_kind
            row["provider_manifest"] = normalize_provider_manifest(
                item.get("provider_manifest"),
                provider_kind=provider_kind or row["connector_kind"],
            )
            # Read-only is the product default.  A saved MCP cannot expose
            # write-like tools to Pi until the user explicitly opts in.
            row["allow_write"] = bool(item.get("allow_write", False))
            row["allow_reversible"] = bool(item.get("allow_reversible", False))
            # Tool effect classifications are host-owned policy.  Remote MCP
            # annotations are advisory and must never manufacture read access.
            row["tool_effects"] = _normalize_mcp_tool_effects(item.get("tool_effects"))
            row["tool_policies"] = _normalize_mcp_tool_policies(item.get("tool_policies"))
            provider_defaults = host_policy_defaults(row)
            row["provider_manifest"] = provider_defaults.get("provider_manifest", row["provider_manifest"])
            for tool_name, effect in dict(provider_defaults.get("tool_effects", {}) or {}).items():
                row["tool_effects"].setdefault(tool_name, effect)
            existing_policy_names = {
                str(policy.get("name", ""))
                for policy in row["tool_policies"]
                if isinstance(policy, dict)
            }
            for policy in list(provider_defaults.get("tool_policies", []) or []):
                if not isinstance(policy, dict) or str(policy.get("name", "")) in existing_policy_names:
                    continue
                if len(row["tool_policies"]) >= _MAX_MCP_TOOL_POLICIES:
                    break
                row["tool_policies"].append(policy)
                existing_policy_names.add(str(policy.get("name", "")))
            call_timeout = item.get("call_timeout_ms")
            if isinstance(call_timeout, (int, float)) and not isinstance(call_timeout, bool):
                row["call_timeout_ms"] = max(250, min(120_000, int(call_timeout)))
            # Deferred servers expose a compact search/call surface and only
            # start the MCP process when the agent actually needs a tool.
            # Keep direct mode as the migration-safe default for existing
            # workspaces and connector integrations.
            row["deferred"] = bool(item.get("deferred", False))
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            row["tags"] = list(dict.fromkeys(_text(tag, limit=48) for tag in tags if _text(tag, limit=48)))[:12]
        elif kind == "plugin":
            row["source"] = _text(item.get("source"), limit=500)
            row["builtin"] = bool(item.get("builtin", False))
            row["icon"] = _text(item.get("icon"), limit=40)
            skills = item.get("skills") if isinstance(item.get("skills"), list) else []
            row["skills"] = list(dict.fromkeys(_text(value, limit=80) for value in skills if _text(value, limit=80)))[:12]
            tool_names = item.get("tool_names") if isinstance(item.get("tool_names"), list) else []
            row["tool_names"] = list(dict.fromkeys(_text(value, limit=80) for value in tool_names if _text(value, limit=80)))[:12]
            row["update_mode"] = _text(item.get("update_mode"), fallback="manual", limit=24)
            row["version"] = _text(item.get("version"), limit=40)
        rows.append(row)
    if rows or kind not in {"skill"}:
        return rows
    return deepcopy(_DEFAULT_SETTINGS["skills"])


def _with_builtin_skills(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refresh shipped Skill metadata without overriding user choices.

    The desktop app has already written settings files for earlier releases.
    Canonical metadata therefore needs to replace stale translated labels while
    preserving per-workspace enablement.  Zotero used to be misclassified as a
    Skill and is removed only when the record is the retired built-in package.
    """

    canonical = {record["id"]: record for record in default_skill_records()}
    refreshed: list[dict[str, Any]] = []
    for record in records:
        identifier = str(record.get("id", ""))
        is_builtin = str(record.get("path", "")).startswith("builtin:") or record.get("source_type") == "builtin"
        if identifier == "zotero" and is_builtin:
            continue
        shipped = canonical.get(identifier)
        if shipped:
            refreshed.append({**shipped, "enabled": bool(record.get("enabled", shipped["enabled"])), "uninstalled": bool(record.get("uninstalled", False))})
        else:
            refreshed.append(record)
    known = {str(record.get("id", "")) for record in refreshed}
    return [*refreshed, *(record for record in default_skill_records() if record["id"] not in known)]


def _with_builtin_plugins(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refresh built-in plugin metadata while retaining user enablement."""

    canonical = {record["id"]: record for record in default_plugin_records()}
    refreshed: list[dict[str, Any]] = []
    for record in records:
        identifier = str(record.get("id", ""))
        shipped = canonical.get(identifier)
        if shipped and record.get("builtin"):
            refreshed.append({**shipped, "enabled": bool(record.get("enabled", shipped["enabled"])), "uninstalled": bool(record.get("uninstalled", False))})
        else:
            refreshed.append(record)
    known = {str(record.get("id", "")) for record in refreshed}
    return [*refreshed, *(record for record in default_plugin_records() if record["id"] not in known)]


def _with_secret_status(settings: dict[str, Any], workspace: str | Path) -> dict[str, Any]:
    public = deepcopy(settings)
    public.pop("credential_scope_id", None)
    public["plugins"] = enrich_builtin_plugins(list(public.get("plugins", []) or []))
    public["notion_api_token_configured"] = notion_api_token_configured(workspace)
    public["workspace_profile"] = _workspace_profile(workspace)
    for provider in public["providers"]:
        if provider.get("auth_mode") == "managed":
            provider["api_key_configured"] = True
            provider["api_key_source"] = "managed"
            provider["api_key_env"] = ""
        elif provider["kind"] == "local":
            provider["api_key_configured"] = False
            provider["api_key_source"] = "missing"
            provider["api_key_env"] = ""
        else:
            source, environment_name = _provider_credential_status(workspace, provider["id"])
            provider["api_key_configured"] = source in {"environment", "keyring"}
            provider["api_key_source"] = source
            provider["api_key_env"] = environment_name or _provider_api_key_env(provider["id"])
    document_processing = public["document_processing"]
    document_processing["ocr"]["api_key_configured"] = _has_document_service_api_key(workspace, "ocr")
    document_processing["mineru"]["api_key_configured"] = _has_document_service_api_key(workspace, "mineru")
    return public


def _workspace_profile(workspace: str | Path) -> str:
    workspace_path = Path(workspace).expanduser().resolve()
    temporary_root = Path(tempfile.gettempdir()).expanduser().resolve()
    try:
        workspace_path.relative_to(temporary_root)
    except ValueError:
        return "persistent"
    return "temporary"


def _has_provider_api_key(workspace: str | Path, provider_id: str) -> bool:
    return _provider_credential_status(workspace, provider_id)[0] in {"environment", "keyring"}


def _provider_credential_status(workspace: str | Path, provider_id: str) -> tuple[str, str]:
    environment_value, environment_name = _provider_environment_api_key(provider_id, workspace=workspace)
    if environment_value:
        return "environment", environment_name
    try:
        import keyring

        if _provider_saved_key(keyring, workspace, provider_id):
            return "keyring", ""
    except Exception:
        pass
    if environment_name:
        return "endpoint_mismatch", environment_name
    return "missing", ""


def _provider_environment_api_key(provider_id: str, *, workspace: str | Path | None = None) -> tuple[str, str]:
    for environment_name in _provider_api_key_envs(provider_id):
        value = str(os.environ.get(environment_name) or "").strip()
        if value:
            if workspace is not None and not _provider_environment_endpoint_allowed(workspace, provider_id):
                return "", environment_name
            return value, environment_name
    return "", ""


def _provider_environment_endpoint_allowed(workspace: str | Path, provider_id: str) -> bool:
    preset = next((item for item in _PROVIDER_PRESETS if item["id"] == provider_id), None)
    if preset is None:
        return True
    shipped_endpoint = str(preset.get("base_url", "") or "").strip().rstrip("/")
    shipped_digest = hashlib.sha256(shipped_endpoint.encode("utf-8")).hexdigest()[:16]
    return _provider_endpoint_digest(workspace, provider_id) == shipped_digest


def _provider_api_key_env(provider_id: str) -> str:
    names = _provider_api_key_envs(provider_id)
    return names[0] if names else ""


def _provider_api_key_envs(provider_id: str) -> tuple[str, ...]:
    identifier = str(provider_id or "").strip().lower()
    if not identifier or identifier in {_MANAGED_PROVIDER_ID, "local", "local-evidence"}:
        return ()
    known = _PROVIDER_API_KEY_ENV_ALIASES.get(identifier)
    if known:
        return known
    normalized = re.sub(r"[^a-z0-9]+", "_", identifier).strip("_").upper()
    return (f"{normalized}_API_KEY",) if normalized else ()


def _provider_secret_name(workspace: str | Path, provider_id: str) -> str:
    scope = _credential_scope_id(_read_raw_settings_file(workspace))
    if scope:
        return f"model-provider:v2:{scope}:{provider_id}"
    return _legacy_provider_secret_name(workspace, provider_id)


def _provider_account_secret_name(workspace: str | Path, provider_id: str) -> str:
    return f"model-provider:v3:{provider_id}:{_provider_endpoint_digest(workspace, provider_id)}"


def _provider_account_deleted_name(workspace: str | Path, provider_id: str) -> str:
    return f"model-provider:v3-deleted:{provider_id}:{_provider_endpoint_digest(workspace, provider_id)}"


def _provider_scoped_endpoint_marker(scope: str, provider_id: str) -> str:
    return f"model-provider:v2-endpoint:{scope}:{provider_id}"


def _provider_endpoint_digest(workspace: str | Path, provider_id: str) -> str:
    raw = _read_raw_settings_file(workspace)
    configured = raw.get("providers") if isinstance(raw.get("providers"), list) else []
    provider = next((item for item in configured if isinstance(item, dict) and item.get("id") == provider_id), None)
    if provider is None:
        provider = next((item for item in _PROVIDER_PRESETS if item["id"] == provider_id), {})
    endpoint = str(provider.get("base_url", "") or "").strip().rstrip("/")
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]


def _provider_saved_key(keyring_module: Any, workspace: str | Path, provider_id: str) -> str:
    username = _provider_account_secret_name(workspace, provider_id)
    if keyring_module.get_password(_SERVICE_NAME, _provider_account_deleted_name(workspace, provider_id)):
        return ""
    account_key = _get_saved_secret(keyring_module, username)
    scope = _credential_scope_id(_read_raw_settings_file(workspace))
    if not scope:
        return account_key
    scoped_username = f"model-provider:v2:{scope}:{provider_id}"
    scoped_key = _get_saved_secret(keyring_module, scoped_username)
    if not scoped_key:
        return account_key
    marker = _provider_scoped_endpoint_marker(scope, provider_id)
    endpoint_digest = _provider_endpoint_digest(workspace, provider_id)
    bound_endpoint = _get_saved_secret(keyring_module, marker)
    if bound_endpoint and bound_endpoint != endpoint_digest:
        return account_key
    try:
        if not bound_endpoint:
            keyring_module.set_password(_SERVICE_NAME, marker, endpoint_digest)
            if _get_saved_secret(keyring_module, marker) != endpoint_digest:
                return account_key
        if account_key and account_key != scoped_key:
            return scoped_key
        if not account_key:
            keyring_module.set_password(_SERVICE_NAME, username, scoped_key)
            if _get_saved_secret(keyring_module, username) != scoped_key:
                return ""
        try:
            _delete_saved_secret(keyring_module, scoped_username)
        except Exception:
            pass
        return scoped_key
    except Exception:
        return account_key


def _legacy_provider_secret_name(workspace: str | Path, provider_id: str) -> str:
    digest = hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"model-provider:{digest}:{provider_id}"


def _document_service_id(value: object) -> str:
    identifier = _safe_id(value, fallback="")
    if identifier not in {"ocr", "mineru"}:
        raise SettingsError("不支持的文档处理服务")
    return identifier


def _document_service_secret_name(workspace: str | Path, service_id: str) -> str:
    scope = _credential_scope_id(_read_raw_settings_file(workspace))
    if scope:
        return f"document-service:v2:{scope}:{service_id}"
    return _legacy_document_service_secret_name(workspace, service_id)


def _legacy_document_service_secret_name(workspace: str | Path, service_id: str) -> str:
    digest = hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"document-service:{digest}:{service_id}"


def _notion_secret_name(workspace: str | Path) -> str:
    scope = _credential_scope_id(_read_raw_settings_file(workspace))
    if scope:
        return f"notion-integration:v2:{scope}"
    return _legacy_notion_secret_name(workspace)


def _legacy_notion_secret_name(workspace: str | Path) -> str:
    digest = hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"notion-integration:{digest}"


def _has_document_service_api_key(workspace: str | Path, service_id: str) -> bool:
    try:
        import keyring

        return bool(_get_saved_secret(keyring, _document_service_secret_name(workspace, service_id)))
    except Exception:
        return False


def _text(value: object, *, fallback: str = "", limit: int = 200) -> str:
    text = str(value or "").strip()
    return (text[:limit] or fallback).strip()


def _safe_id(value: object, *, fallback: str) -> str:
    text = _SAFE_ID.sub("-", str(value or "").strip().lower()).strip(".-_")
    return text[:64] or fallback


def _model_id(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = "".join(character for character in text if character.isprintable() and not character.isspace())
    return text[:160] or fallback


def _unique_id(identifier: str, used: set[str]) -> str:
    candidate = identifier
    suffix = 2
    while candidate in used:
        candidate = f"{identifier[:56]}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate
