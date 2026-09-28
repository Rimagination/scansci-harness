"""Provider-neutral image generation for the ScanSci Host.

Pi receives one stable ``generate_image`` tool.  This module resolves the
user-selected image model, calls an OpenAI-compatible images endpoint, and
commits the returned bytes inside the workspace artifact directory.  Provider
credentials never enter Pi messages, tool results, or the settings JSON.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse
from uuid import uuid4

import requests

from .app_settings import get_provider_api_key, load_settings
from .artifact_plugins import _artifact_path, _result


SUPPORTED_IMAGE_SIZES = frozenset(
    {
        "1024x1024",
        "768x1344",
        "864x1152",
        "1344x768",
        "1152x864",
        "1440x720",
        "720x1440",
    }
)
_MAX_PROMPT_CHARS = 4_000
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_RESPONSE_BYTES = 36 * 1024 * 1024
_MAX_REDIRECTS = 3


def _model_record(provider: dict[str, Any], model_id: str) -> dict[str, Any]:
    return next(
        (
            dict(item)
            for item in list(provider.get("models", []) or [])
            if isinstance(item, dict) and str(item.get("id", "")).strip() == model_id
        ),
        {},
    )


def _image_capable(model: dict[str, Any]) -> bool:
    return "image" in {
        str(value).strip().lower()
        for value in list(model.get("capabilities", []) or [])
    }


def _first_provider_key(workspace: str | Path, provider: dict[str, Any]) -> str:
    auth_mode = str(provider.get("auth_mode", "")).strip().lower()
    if auth_mode == "managed":
        return "scansci-managed-gateway"
    if auth_mode == "local" or str(provider.get("kind", "")).strip().lower() == "local":
        return "scansci-local-runtime"
    secret = get_provider_api_key(workspace, str(provider.get("id", "")))
    return next((value.strip() for value in secret.split(",") if value.strip()), "")


def _provider_route(
    workspace: str | Path,
    provider: dict[str, Any],
    model_id: str,
) -> dict[str, str] | None:
    model = _model_record(provider, model_id)
    if (
        not provider.get("enabled", True)
        or not model.get("enabled", True)
        or model.get("available") is False
        or str(model.get("readiness") or "production") != "production"
        or not _image_capable(model)
        or not str(provider.get("base_url", "")).strip()
    ):
        return None
    api_key = _first_provider_key(workspace, provider)
    if not api_key:
        return None
    return {
        "provider_id": str(provider.get("id", "")),
        "provider_name": str(provider.get("name") or provider.get("id") or "图像服务"),
        "base_url": str(provider.get("base_url", "")).strip(),
        "api_key": api_key,
        "model_id": model_id,
    }


def select_image_generation_route(
    workspace: str | Path,
    settings: dict[str, Any] | None = None,
    *,
    image_model: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve the explicit image role, then a configured image-capable provider."""

    current = settings or load_settings(workspace)
    providers = [
        dict(item)
        for item in list(current.get("providers", []) or [])
        if isinstance(item, dict)
    ]
    if image_model is not None:
        if not isinstance(image_model, dict):
            raise ValueError("image_model 必须指定 provider_id 和 model_id")
        provider_id = str(image_model.get("provider_id", "") or "").strip()
        model_id = str(image_model.get("model_id", "") or "").strip()
        provider = next((item for item in providers if item.get("id") == provider_id), {})
        if not provider_id or not model_id:
            raise ValueError("image_model 必须指定 provider_id 和 model_id")
        route = _provider_route(workspace, provider, model_id)
        if route is None:
            raise ValueError("所选生图模型不可用：请确认服务和模型已启用、具备 image 能力、状态为 production，并满足服务的认证要求。")
        return route
    reference = str(dict(current.get("model_roles", {}) or {}).get("image", "auto")).strip()
    if reference.startswith("provider:"):
        provider_id, separator, model_id = reference.removeprefix("provider:").partition(":")
        provider = next(
            (item for item in providers if str(item.get("id", "")) == provider_id),
            None,
        )
        route = _provider_route(workspace, provider or {}, model_id) if separator else None
        if route is None:
            raise ValueError(
                "指定的图像生成模型尚不可用。请在设置 → 模型服务启用服务商并保存 API Key。"
            )
        return route
    if reference.startswith("local:"):
        local_id = reference.removeprefix("local:")
        local_model = next(
            (
                dict(item)
                for item in list(current.get("local_models", []) or [])
                if isinstance(item, dict) and str(item.get("id", "")) == local_id
            ),
            {},
        )
        if (
            local_model.get("enabled", True)
            and _image_capable(local_model)
            and str(local_model.get("base_url", "")).strip()
            and str(local_model.get("model_id", "")).strip()
        ):
            return {
                "provider_id": f"local:{local_id}",
                "provider_name": str(local_model.get("name") or "本地图像模型"),
                "base_url": str(local_model.get("base_url", "")).strip(),
                "api_key": "scansci-local-runtime",
                "model_id": str(local_model.get("model_id", "")).strip(),
            }
        raise ValueError("指定的本地图像生成模型尚不可用，请先启动对应运行时。")
    if reference not in {"", "auto"}:
        raise ValueError("图像生成模型配置无效，请在设置 → 默认能力中重新选择。")

    for provider in providers:
        for model in list(provider.get("models", []) or []):
            if not isinstance(model, dict) or not _image_capable(model):
                continue
            route = _provider_route(workspace, provider, str(model.get("id", "")).strip())
            if route is not None:
                return route
    raise ValueError(
        "尚未配置可用的图像生成模型。请先在设置 → 模型服务连接服务商，再到默认能力选择图像生成模型。"
    )


def _images_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/images/generations"):
        if value.lower().endswith(suffix):
            value = value[: -len(suffix)]
            break
    parsed = urlparse(value)
    hostname = str(parsed.hostname or "").strip().lower()
    local_http = parsed.scheme == "http" and hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        (parsed.scheme != "https" and not local_http)
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("图像服务 API 地址必须使用 HTTPS；本机回环运行时可以使用 HTTP。")
    return f"{value}/images/generations"


def _public_https_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    hostname = str(parsed.hostname or "").strip().lower()
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise RuntimeError("图像服务返回了不安全的下载地址")
    try:
        addresses = {ip_address(hostname)}
    except ValueError:
        try:
            addresses = {
                ip_address(record[4][0])
                for record in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise RuntimeError("无法解析图像服务返回的下载地址") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise RuntimeError("图像服务返回的下载地址指向本地或保留网络")
    return parsed.geturl()


def _download_image_url(url: str, *, timeout: float) -> bytes:
    current = str(url or "").strip()
    for _redirect in range(_MAX_REDIRECTS + 1):
        safe_url = _public_https_url(current)
        try:
            response = requests.get(
                safe_url,
                headers={"Accept": "image/*", "User-Agent": "ScanSci-Desktop/0.4"},
                timeout=(10, timeout),
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError("无法从图像服务下载生成结果") from exc
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = str(response.headers.get("Location", "")).strip()
                if not location:
                    raise RuntimeError("图像下载重定向缺少目标地址")
                current = urljoin(safe_url, location)
                continue
            if response.status_code != 200:
                raise RuntimeError(f"图像下载服务返回 HTTP {response.status_code}")
            try:
                declared = int(response.headers.get("Content-Length", "0") or 0)
            except (TypeError, ValueError):
                declared = 0
            if declared > _MAX_IMAGE_BYTES:
                raise RuntimeError("生成图像超过 25 MB 上限")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > _MAX_IMAGE_BYTES:
                    raise RuntimeError("生成图像超过 25 MB 上限")
                chunks.append(chunk)
            return b"".join(chunks)
        except requests.RequestException as exc:
            raise RuntimeError("生成图像下载中断，请稍后重试。") from exc
        finally:
            response.close()
    raise RuntimeError("图像下载重定向次数过多")


def _decode_image_result(payload: Any, *, timeout: float) -> bytes:
    data = payload.get("data") if isinstance(payload, dict) else None
    item = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    encoded = str(item.get("b64_json", "") or "").strip()
    if encoded:
        if encoded.startswith("data:"):
            encoded = encoded.partition(",")[2]
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("图像服务返回了无效的 Base64 数据") from exc
        if len(image) > _MAX_IMAGE_BYTES:
            raise RuntimeError("生成图像超过 25 MB 上限")
        return image
    image_url = str(item.get("url", "") or "").strip()
    if image_url:
        return _download_image_url(image_url, timeout=timeout)
    raise RuntimeError("图像服务响应中没有可用图像")


def _bounded_json_response(response: requests.Response, *, max_bytes: int = _MAX_RESPONSE_BYTES) -> Any:
    try:
        declared = int(response.headers.get("Content-Length", "0") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > max_bytes:
        raise RuntimeError("图像服务响应超过大小上限")
    content = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        content.extend(chunk)
        if len(content) > max_bytes:
            raise RuntimeError("图像服务响应超过大小上限")
    try:
        return json.loads(bytes(content))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("图像服务返回了无效 JSON") from exc


def _image_service_error(response: requests.Response, *, api_key: str, payload: Any = None) -> RuntimeError:
    """Expose bounded diagnostic codes, never provider prose, prompts or credentials."""
    if payload is None:
        try:
            payload = _bounded_json_response(response, max_bytes=16 * 1024)
        except (RuntimeError, requests.RequestException):
            payload = {}
    details = payload.get("error") if isinstance(payload, dict) else None
    details = details if isinstance(details, dict) else {}
    code = str(details.get("code") or details.get("type") or "").lower()
    # Only recognized machine codes may cross the Host boundary. Unknown response
    # messages can echo the prompt, keys or HTML; the HTTP status remains useful.
    reasons = {
        "invalid_api_key": "密钥未通过验证，请在模型服务中检查 API Key。",
        "authentication_error": "密钥未通过验证，请在模型服务中检查 API Key。",
        "permission_denied": "当前账户或密钥没有调用权限，请在服务商控制台检查模型授权。",
        "model_not_found": "所选模型不存在或当前账户不可访问，请核对模型 ID 和授权。",
        "insufficient_quota": "当前账户配额不足，请在服务商控制台检查额度。",
        "insufficient_balance": "当前账户余额不足，请在服务商控制台检查余额。",
        "content_policy_violation": "服务商的内容策略拒绝了本次请求，请调整图片描述。",
        "rate_limit_exceeded": "请求触发限流，请稍后重试。",
    }
    status = response.status_code
    reason = reasons.get(code) or {
        400: "请求参数未被接受，请核对模型、尺寸和图片描述。",
        401: "密钥未通过验证，请在模型服务中检查 API Key 和服务地址。",
        402: "账户支付或额度要求未满足，请在服务商控制台检查。",
        403: "服务商拒绝访问，请检查模型授权、账户状态或访问限制；仅凭此状态无法确定密钥是否有误。",
        404: "生图接口或模型不可用，请核对服务地址和模型 ID。",
        429: "请求触发限流或额度限制，请检查账户额度或稍后重试。",
    }.get(status, "服务商暂时无法完成生图，请稍后检查服务状态。" if status >= 500 else "服务商未完成生图，请检查模型服务配置。")
    diagnostics = [f"HTTP {status}"]
    if code in reasons and (not api_key or api_key not in code):
        diagnostics.append(f"错误码：{code}")
    return RuntimeError(f"图像服务：{reason}（{'；'.join(diagnostics)}）")


def _image_format(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise RuntimeError("图像服务返回的内容不是受支持的 PNG、JPEG、GIF 或 WebP")


def _wanx_task_info(output: dict[str, Any], task_id: str, *, api_key: str) -> dict[str, str]:
    """Keep typed diagnostics only; provider prose may echo private input."""
    reasons = {
        "DataInspectionFailed": "服务商内容审核未通过，输入描述或生成结果被拦截。请调整描述后自行决定是否重新生成。",
        "IPInfringementSuspect": "服务商判定输入可能涉及知识产权侵权，已拒绝生成。可改用原创角色描述；是否涉及侵权需另行判断。",
        "InvalidParameter": "服务商未接受生成参数，请核对模型和图片尺寸。",
        "InvalidApiKey": "服务商密钥验证失败，请检查模型服务配置。",
        "Arrearage": "服务商账户余额不足，请检查账户余额。",
        "Throttling": "服务商请求限流，请稍后再试。",
        "InternalError": "服务商内部执行失败，请联系服务商核查。",
    }
    status = output.get("task_status")
    status = status if status in ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN") else "UNKNOWN"
    info = {"status": status}
    # Only canonical UUID task identifiers cross the diagnostic boundary.
    if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", task_id) and (not api_key or api_key not in task_id):
        info["task_id"] = task_id
    code = output.get("code")
    if isinstance(code, str) and code in reasons and (not api_key or api_key not in code):
        info["error_code"] = code
    if status in ("FAILED", "CANCELED", "UNKNOWN") or info.get("error_code"):
        reason = reasons.get(info.get("error_code", "")) or {
            "CANCELED": "服务商已取消生成任务。",
            "UNKNOWN": "服务商任务不存在或状态无法确认。",
        }.get(status, "服务商生成失败，尚无可识别的错误原因；请联系服务商核查。")
        details = []
        if info.get("error_code"):
            details.append(f"错误码：{info['error_code']}")
        if info.get("task_id"):
            details.append(f"任务编号：{info['task_id']}")
        info["error"] = reason + (f"（{'；'.join(details)}）" if details else "") + " 未重新提交生图。"
    return info


def _write_image_receipt(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _wanx_image_payload(route: dict[str, str], *, prompt: str, size: str, timeout: float,
                        on_task: Callable[[dict[str, str]], None] | None = None) -> dict[str, Any]:
    """Submit once, then poll read-only; failures never restart the paid operation."""

    if size != "1024x1024":
        raise ValueError("所选生图模型目前仅支持 1024x1024 尺寸")
    base = route["base_url"].rstrip("/")
    _images_endpoint(base)  # Reuse API URL validation, retaining this transport's base path.
    headers = {
        "Authorization": f"Bearer {route['api_key']}",
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "ScanSci-Desktop/0.4",
    }
    deadline = time.monotonic() + max(1.0, min(300.0, float(timeout)))

    def read(response: requests.Response) -> dict[str, Any]:
        try:
            if not 200 <= response.status_code < 300:
                raise _image_service_error(response, api_key=route["api_key"])
            payload = _bounded_json_response(response, max_bytes=64 * 1024)
            if not isinstance(payload, dict):
                raise RuntimeError("异步生图服务返回了无效任务响应")
            if payload.get("error") or payload.get("code"):
                raise _image_service_error(response, api_key=route["api_key"], payload=payload)
            output = payload.get("output")
            if not isinstance(output, dict):
                raise RuntimeError("异步生图服务响应缺少任务信息")
            return output
        finally:
            response.close()

    try:
        output = read(requests.post(
            base + "/p003/text2image", headers=headers,
            json={"model": route["model_id"], "input": {"prompt": prompt},
                  "parameters": {"size": "1024*1024", "n": 1}},
            timeout=(15, min(60.0, max(1.0, deadline - time.monotonic()))),
            stream=True, allow_redirects=False,
        ))
        task_id = output.get("task_id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise RuntimeError("异步生图服务返回了无效任务 ID；未重新提交生图")
        if on_task:
            on_task(_wanx_task_info({"task_status": "PENDING"}, task_id, api_key=route["api_key"]))
        # Once submitted, caller cancellation only stops foreground waiting.
        # Keep polling and saving so a later receipt replay can return the artifact.
        for _ in range(75):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            output = read(requests.get(
                base + "/p003/tasks/" + task_id, headers=headers,
                timeout=(min(15.0, remaining), min(30.0, remaining)),
                stream=True, allow_redirects=False,
            ))
            status = output.get("task_status")
            info = _wanx_task_info(output, task_id, api_key=route["api_key"])
            if on_task:
                on_task(info)
            if status == "SUCCEEDED":
                results = output.get("results")
                item = results[0] if isinstance(results, list) and results else None
                url = item.get("url") if isinstance(item, dict) else None
                if not isinstance(url, str) or not url.strip():
                    if isinstance(item, dict) and item.get("code"):
                        info = _wanx_task_info({**item, "task_status": "FAILED"}, task_id, api_key=route["api_key"])
                        if on_task:
                            on_task(info)
                        raise RuntimeError(info["error"])
                    raise RuntimeError("异步生图任务成功但缺少图片地址；未重新提交生图")
                return {"data": [{"url": url}]}
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise RuntimeError(info["error"])
            if status not in ("PENDING", "RUNNING"):
                raise RuntimeError("异步生图服务返回了无法识别的任务状态；未重新提交生图")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(4.0, remaining))
    except requests.RequestException as exc:
        raise RuntimeError("异步生图提交或查询连接中断，结果尚未确定；未重新提交生图。") from exc
    raise RuntimeError("异步生图查询超时，任务可能仍在服务商执行；未重新提交生图。")


def generate_image_artifact(
    arguments: dict[str, Any],
    *,
    workspace: str | Path,
    timeout: float = 180.0,
    image_model: dict[str, Any] | None = None,
    request_id: str = "",
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Persist explicit paid submissions before sending; never replay an uncertain call."""

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("生图已取消，未向服务商提交请求。")
    if not request_id:
        return _generate_image_artifact(
            arguments, workspace=workspace, timeout=timeout,
            image_model=image_model, cancel_event=cancel_event,
        )
    material = json.dumps({"arguments": arguments, "image_model": image_model}, sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
    record_id = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    root = Path(workspace).resolve().parent / "artifacts" / "images" / ".requests"
    root.mkdir(parents=True, exist_ok=True)
    record = root / f"{record_id}.json"
    try:
        with record.open("x", encoding="utf-8") as stream:
            json.dump({"fingerprint": fingerprint, "status": "submitted"}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        try:
            previous = json.loads(record.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise RuntimeError("生图请求已登记，状态尚未确定；已阻止重复付费请求。") from exc
        if previous.get("fingerprint") != fingerprint:
            raise ValueError("生图请求 ID 已用于不同内容，请使用新的提交 ID。")
        if previous.get("status") == "completed":
            result = dict(previous["result"])
            if not Path(result["file_path"]).is_file():
                raise RuntimeError("此前生成的图像文件已不可用；已阻止自动重新生图。")
            return result
        if previous.get("provider_task", {}).get("error"):
            raise RuntimeError(previous["provider_task"]["error"] + " 已阻止重复付费请求。")
        # ponytail: no provider reconciliation for interrupted submissions;
        # keep them blocked until a future recovery API can prove their outcome.
        raise RuntimeError("生图请求已提交或结果尚未确定；已阻止重复付费请求。")
    receipt: dict[str, Any] = {"fingerprint": fingerprint, "status": "submitted"}

    def save_task(info: dict[str, str]) -> None:
        if receipt.get("provider_task") != info:
            receipt["provider_task"] = info
            try:
                _write_image_receipt(record, receipt)
            except OSError:
                # The initial durable duplicate guard remains. Finish the paid
                # operation and retry checkpointing on the next poll/completion.
                receipt.pop("provider_task", None)

    result = _generate_image_artifact(
        arguments, workspace=workspace, timeout=timeout,
        image_model=image_model, cancel_event=cancel_event, on_task=save_task,
    )
    _write_image_receipt(record, {**receipt, "status": "completed", "result": result})
    return result


def _generate_image_artifact(
    arguments: dict[str, Any],
    *,
    workspace: str | Path,
    timeout: float,
    image_model: dict[str, Any] | None,
    cancel_event: threading.Event | None,
    on_task: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, Any]:
    """Generate one image and atomically persist it as a local artifact."""

    prompt = str(arguments.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise ValueError(f"prompt must not exceed {_MAX_PROMPT_CHARS} characters")
    size = str(arguments.get("size", "1024x1024") or "1024x1024").strip().lower()
    if size not in SUPPORTED_IMAGE_SIZES:
        raise ValueError(f"不支持的图像尺寸：{size}")
    route = (
        select_image_generation_route(workspace, image_model=image_model)
        if image_model is not None else select_image_generation_route(workspace)
    )
    request_payload = {"model": route["model_id"], "prompt": prompt, "size": size}
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("生图已取消，未向服务商提交请求。")
    if route.get("provider_id") == "paratera" and route["model_id"] == "WanX2.1-T2I-Plus":
        payload = _wanx_image_payload(route, prompt=prompt, size=size, timeout=timeout, on_task=on_task)
    else:
        try:
            response = requests.post(
                _images_endpoint(route["base_url"]),
                headers={
                    "Authorization": f"Bearer {route['api_key']}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "ScanSci-Desktop/0.4",
                },
                json=request_payload,
                timeout=(15, max(30.0, min(300.0, float(timeout)))),
                stream=True,
                allow_redirects=False,
            )
            try:
                if response.status_code < 200 or response.status_code >= 300:
                    raise _image_service_error(response, api_key=route["api_key"])
                payload = _bounded_json_response(response)
                if isinstance(payload, dict) and payload.get("error"):
                    raise _image_service_error(response, api_key=route["api_key"], payload=payload)
            finally:
                response.close()
        except requests.RequestException as exc:
            raise RuntimeError("图像服务连接失败，请检查网络和 API 地址。") from exc
    image = _decode_image_result(payload, timeout=max(20.0, min(120.0, float(timeout))))
    suffix, mime_type = _image_format(image)
    title = str(arguments.get("title", "") or "").strip() or prompt[:48] or "AI 生成图像"
    path = _artifact_path(
        workspace,
        "images",
        arguments.get("output_name") or f"image-{uuid4().hex}",
        title,
        suffix,
    )
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(image)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return _result(
        "image",
        path,
        title,
        provider_id=route["provider_id"],
        provider_name=route["provider_name"],
        model_id=route["model_id"],
        size=size,
        mime_type=mime_type,
        asset_url=f"/api/generated-images/{quote(path.name, safe='')}",
    )


def generated_image_asset(workspace: str | Path, filename: str) -> tuple[Path, str]:
    """Resolve one generated image by basename without exposing arbitrary files."""

    requested = str(filename or "").strip()
    if not requested or Path(requested).name != requested:
        raise FileNotFoundError("Generated image does not exist")
    root = (Path(workspace).resolve().parent / "artifacts" / "images").resolve()
    candidate = (root / requested).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FileNotFoundError("Generated image does not exist")
    with candidate.open("rb") as stream:
        header = stream.read(16)
    _suffix, mime_type = _image_format(header)
    return candidate, mime_type


__all__ = [
    "SUPPORTED_IMAGE_SIZES",
    "generate_image_artifact",
    "generated_image_asset",
    "select_image_generation_route",
]
