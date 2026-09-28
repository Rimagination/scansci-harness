"""Small, credential-free model capability and readiness snapshots.

The settings file describes what a user wants to use.  This module describes
what can actually be selected *right now*.  Keeping the two contracts apart
prevents a downloaded-but-unrunnable model, a stopped loopback server, or a
disabled provider from appearing as a healthy chat route.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


READY_STATUSES = frozenset({"ready", "connected", "configured"})


def model_health_key(provider_id: str, model_id: str) -> str:
    """Return the stable public key used by the browser for one model."""

    return f"{str(provider_id or '').strip()}::{str(model_id or '').strip()}"


def _capabilities(model: dict[str, Any]) -> list[str]:
    values = model.get("capabilities") if isinstance(model.get("capabilities"), list) else []
    return list(dict.fromkeys(str(value).strip().lower() for value in values if str(value).strip()))


def _entry(
    status: str,
    *,
    detail: str = "",
    capabilities: list[str] | None = None,
    runtime: str = "",
) -> dict[str, Any]:
    return {
        "status": str(status),
        "detail": str(detail),
        "capabilities": list(capabilities or []),
        "runtime": str(runtime),
    }


def _installed_lookup(installed_models: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id", "")): dict(item)
        for item in installed_models
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }


def _local_model_health(
    local_model: dict[str, Any],
    *,
    installed: dict[str, dict[str, Any]],
    ollama: dict[str, Any],
    runtime_checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    runtime = str(local_model.get("runtime", "")).strip().lower()
    model_id = str(local_model.get("model_id", "")).strip()
    capabilities = _capabilities(local_model)
    if runtime == "builtin":
        return _entry("ready", detail="离线基础能力可用。", capabilities=capabilities, runtime=runtime)
    if not bool(local_model.get("enabled", True)):
        return _entry("disabled", detail="本地模型已停用。", capabilities=capabilities, runtime=runtime)
    if runtime == "local-huggingface":
        snapshot = installed.get(model_id)
        if not snapshot or not bool(snapshot.get("ready")):
            return _entry("not_installed", detail="尚未发现已完成校验的本地模型。", capabilities=capabilities, runtime=runtime)
        if snapshot.get("runtime_compatible") is False or local_model.get("runtime_compatible") is False:
            return _entry(
                "incompatible",
                detail=str(snapshot.get("runtime_message") or local_model.get("runtime_message") or "当前运行组件不能执行这个模型格式。"),
                capabilities=capabilities,
                runtime=runtime,
            )
        return _entry("ready", detail="模型已下载并通过本地格式检查。", capabilities=capabilities, runtime=runtime)
    if runtime == "ollama":
        if not bool(ollama.get("reachable")):
            return _entry("unavailable", detail=str(ollama.get("error") or "Ollama 未运行。"), capabilities=capabilities, runtime=runtime)
        names = {
            str(item.get("name", "")).strip()
            for item in list(ollama.get("models", []) or [])
            if isinstance(item, dict)
        }
        if model_id and model_id not in names and f"{model_id}:latest" not in names:
            return _entry("not_installed", detail="Ollama 已启动，但尚未安装指定模型。", capabilities=capabilities, runtime=runtime)
        return _entry("ready", detail="Ollama 已运行，指定模型可用。", capabilities=capabilities, runtime=runtime)
    check = runtime_checks.get(str(local_model.get("id", "")), {})
    if check.get("ok"):
        returned_models = {str(value).strip() for value in list(check.get("models", []) or []) if str(value).strip()}
        if model_id and returned_models and model_id not in returned_models:
            return _entry("not_installed", detail=f"本地服务已连接，但没有返回模型 {model_id}。", capabilities=capabilities, runtime=runtime)
        return _entry("ready", detail="本地模型服务已连接。", capabilities=capabilities, runtime=runtime)
    return _entry(
        "unavailable",
        detail=str(check.get("error") or "本地模型服务未响应。"),
        capabilities=capabilities,
        runtime=runtime,
    )


def build_model_health(
    settings: dict[str, Any],
    *,
    installed_models: list[dict[str, Any]] | None = None,
    ollama: dict[str, Any] | None = None,
    runtime_checks: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a safe model/provider readiness snapshot for the local UI.

    Remote providers intentionally use ``configured`` rather than making a
    network request on every page load.  Their explicit “检测” button remains
    the authoritative connectivity test.  Local runtimes are checked from
    local facts and bounded loopback probes supplied by the caller.
    """

    installed = _installed_lookup(list(installed_models or []))
    local_rows = {
        str(item.get("id", "")): dict(item)
        for item in list(settings.get("local_models", []) or [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    ollama_status = dict(ollama or {})
    checks = {
        str(key): dict(value)
        for key, value in dict(runtime_checks or {}).items()
        if isinstance(value, dict)
    }
    providers: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}

    for provider in list(settings.get("providers", []) or []):
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id", "")).strip()
        if not provider_id:
            continue
        provider_kind = str(provider.get("kind", "")).strip().lower()
        provider_models = [item for item in list(provider.get("models", []) or []) if isinstance(item, dict)]
        provider_status = "configured"
        provider_detail = "已配置；发送消息时会按服务商协议连接。"
        local_health: dict[str, dict[str, Any]] = {}

        if not bool(provider.get("enabled", True)):
            provider_status = "disabled"
            provider_detail = "服务商已停用。"
        elif provider_kind == "local" or str(provider.get("auth_mode", "")).strip().lower() == "local":
            local_id = str(provider.get("local_model_id", "")).strip()
            if local_id and local_id in local_rows:
                runtime_health = _local_model_health(
                    local_rows[local_id],
                    installed=installed,
                    ollama=ollama_status,
                    runtime_checks=checks,
                )
                local_health = {
                    str(model.get("id", "")).strip(): runtime_health
                    for model in provider_models
                    if str(model.get("id", "")).strip()
                }
            elif provider_id == "local-huggingface":
                for model in provider_models:
                    model_id = str(model.get("id", "")).strip()
                    snapshot = installed.get(model_id)
                    unavailable_reason = str(model.get("unavailable_reason") or "").strip()
                    if snapshot and (snapshot.get("runtime_compatible") is False or model.get("available") is False):
                        local_health[model_id] = _entry(
                            "incompatible",
                            detail=str(unavailable_reason or snapshot.get("runtime_message") or "当前运行组件不能执行这个模型格式。"),
                            capabilities=_capabilities(model),
                            runtime="local-huggingface",
                        )
                    elif snapshot and bool(snapshot.get("ready")):
                        local_health[model_id] = _entry("ready", detail="模型已下载并通过本地格式检查。", capabilities=_capabilities(model), runtime="local-huggingface")
                    elif unavailable_reason:
                        local_health[model_id] = _entry("unavailable", detail=unavailable_reason, capabilities=_capabilities(model), runtime="local-huggingface")
                    else:
                        local_health[model_id] = _entry("not_installed", detail="尚未发现已完成校验的本地模型。", capabilities=_capabilities(model), runtime="local-huggingface")
            else:
                local_health = {
                    str(model.get("id", "")): _entry("ready", detail="离线基础能力可用。", capabilities=_capabilities(model), runtime="builtin")
                    for model in provider_models
                }
            if local_health:
                statuses = [str(item.get("status", "unknown")) for item in local_health.values()]
                if all(status in READY_STATUSES for status in statuses):
                    provider_status = "ready"
                    provider_detail = "本地模型已就绪。"
                elif any(status in READY_STATUSES for status in statuses):
                    provider_status = "partial"
                    provider_detail = "部分本地模型可用；未就绪模型会在选择器中显示原因。"
                else:
                    # local_health is guaranteed non-empty here, so all
                    # statuses are non-ready: the provider is unavailable.
                    provider_status = "unavailable"
                    provider_detail = str(next(iter(local_health.values())).get("detail", "本地模型尚未就绪"))
        elif str(provider.get("auth_mode", "")).strip().lower() == "managed":
            provider_status = "configured"
            provider_detail = "ScanSci 托管服务；无需在本机保存 API 密钥。"
        elif bool(provider.get("api_key_configured")):
            provider_status = "configured"
        else:
            provider_status = "needs_key"
            provider_detail = "尚未配置 API 密钥。"

        providers[provider_id] = {
            "status": provider_status,
            "detail": provider_detail,
            "kind": provider_kind,
            "model_count": len(provider_models),
        }
        for model in provider_models:
            model_id = str(model.get("id", "")).strip()
            if not model_id:
                continue
            entry = local_health.get(model_id)
            if entry is None:
                entry = _entry(
                    provider_status if provider_status in {"configured", "ready", "connected", "disabled", "needs_key"} else "unknown",
                    detail=provider_detail,
                    capabilities=_capabilities(model),
                    runtime="remote" if provider_kind != "local" else "local",
                )
            models[model_health_key(provider_id, model_id)] = entry

    # The browser can use this timestamp to distinguish “not checked yet” from
    # an explicit local failure without persisting transient health state.
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"checked_at": checked_at, "providers": providers, "models": models}


def ready_for_selection(status: str) -> bool:
    """Return whether a provider/model status is safe to expose as selectable."""

    return str(status or "") in READY_STATUSES
