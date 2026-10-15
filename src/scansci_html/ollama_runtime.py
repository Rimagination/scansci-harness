"""Small, resumable Ollama model management for the local desktop app."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urlparse


OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
OLLAMA_VISION_MODEL = "minicpm-v4.6"
OLLAMA_CHAT_MODEL_SPECS = {
    "qwen3.5:9b": {"name": "Qwen3.5 9B · Q4_K_M", "download_gb": 6.6, "context_tokens": 16384},
    "qwen3.5:4b": {"name": "Qwen3.5 4B · Q4_K_M", "download_gb": 3.4, "context_tokens": 32768},
    "qwen3.5:2b": {"name": "Qwen3.5 2B · Q4_K_M", "download_gb": 2.7, "context_tokens": 16384},
}
OLLAMA_CHAT_CATALOG_ITEMS = tuple(
    {
        "id": model_id,
        "name": spec["name"],
        "kind": "chat",
        "runtime": "ollama",
        "source": "ollama",
        "size_hint": f"约 {spec['download_gb']} GB",
        "description": "可作为本地助手模型；安装后验证生成、工具调用和证据引用。",
        "icon_url": "https://ollama.com/public/ollama.png",
    }
    for model_id, spec in OLLAMA_CHAT_MODEL_SPECS.items()
)
OLLAMA_VISION_CATALOG_ITEM = {
    "id": OLLAMA_VISION_MODEL,
    "name": "MiniCPM-V 4.6（Ollama）",
    "kind": "vision",
    "runtime": "ollama",
    "source": "ollama",
    "size_hint": "约 1.6 GB",
    "description": "轻量本地视觉模型；安装 Ollama 后可识别图片、图表和扫描页面。",
    "icon_url": "https://ollama.com/public/ollama.png",
}


def ollama_base_url(value: str | None = None) -> str:
    clean = str(value or OLLAMA_DEFAULT_URL).strip().rstrip("/")
    if clean.endswith("/v1"):
        clean = clean[:-3]
    return clean or OLLAMA_DEFAULT_URL


def is_local_ollama_url(value: str) -> bool:
    try:
        endpoint = urlparse(value)
        return endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "localhost", "::1"} and endpoint.port is not None
    except ValueError:
        return False


def chat_thinking_parameters(base_url: str, model_id: str, thinking_mode: str | None) -> dict[str, Any]:
    """Use the parameter Ollama's OpenAI endpoint actually honors for Qwen3.5."""

    # A user may configure Ollama on another loopback port.  Ollama's v1
    # endpoint requires reasoning_effort to keep visible answers out of thinking.
    local_ollama = is_local_ollama_url(base_url)
    if local_ollama and model_id.startswith("qwen3.5:"):
        return {"reasoning_effort": "medium" if thinking_mode == "enabled" else "none"}
    return {"thinking": {"type": thinking_mode}} if thinking_mode in {"enabled", "disabled"} else {}


def _request_json(base_url: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 3.0) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(f"{ollama_base_url(base_url)}{path}", data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout) as response:  # nosec B310 - URL is local runtime configuration
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Ollama 响应过大")
    value = json.loads(raw.decode("utf-8")) if raw else {}
    return value if isinstance(value, dict) else {}


def warm_ollama_chat_model(base_url: str, model_id: str):
    """Report and complete a cold Ollama load before sending the real prompt."""

    if not is_local_ollama_url(base_url):
        raise ValueError("仅允许预加载本机 Ollama 模型")
    try:
        running = _request_json(base_url, "/api/ps", timeout=3.0)
        names = {
            str(item.get("name", "")).strip()
            for item in list(running.get("models", []) or [])
            if isinstance(item, dict)
        }
        if model_id in names or f"{model_id}:latest" in names:
            return
        yield {"type": "status", "status": "local_model_loading", "name": model_id}
        _request_json(
            base_url,
            "/api/chat",
            method="POST",
            payload={"model": model_id, "messages": [], "stream": False, "keep_alive": "5m"},
            timeout=120.0,
        )
        yield {"type": "status", "status": "local_model_ready", "name": model_id}
    except error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(2048).decode("utf-8", errors="replace"))
        except ValueError:
            payload = {}
        finally:
            exc.close()
        detail = str(payload.get("error") or exc.reason) if isinstance(payload, dict) else str(exc.reason)
        raise RuntimeError(f"Ollama 加载 {model_id} 失败（HTTP {exc.code}）：{detail[:500]}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Ollama 加载 {model_id} 超过 120 秒；请检查模型占用和 Ollama 日志。") from exc
    except error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise RuntimeError(f"Ollama 加载 {model_id} 超过 120 秒；请检查模型占用和 Ollama 日志。") from exc
        raise RuntimeError(f"无法连接 Ollama 以加载 {model_id}；请检查服务状态。") from exc


def ollama_status(
    base_url: str | None = None,
    *,
    model_id: str = OLLAMA_VISION_MODEL,
) -> dict[str, Any]:
    """Return a bounded local status; an unavailable Ollama is not fatal."""

    root = ollama_base_url(base_url)
    wanted_model = str(model_id or OLLAMA_VISION_MODEL).strip()
    try:
        version = _request_json(root, "/api/version", timeout=2.5)
        tags = _request_json(root, "/api/tags", timeout=3.0)
        models = [dict(item) for item in list(tags.get("models", []) or []) if isinstance(item, dict)]
        names = {str(item.get("name", "")).strip() for item in models}
        ready = wanted_model in names or f"{wanted_model}:latest" in names
        return {
            "reachable": True,
            "installed": True,
            "base_url": root,
            "version": str(version.get("version", "") or ""),
            "models": models[:200],
            "model_ready": ready,
            "model_id": wanted_model,
            "error": "",
        }
    except (OSError, ValueError, json.JSONDecodeError, error.URLError) as exc:
        if isinstance(exc, error.HTTPError):
            try:
                payload = json.loads(exc.read(2048).decode("utf-8", errors="replace"))
            except ValueError:
                payload = {}
            finally:
                exc.close()
            detail = str(payload.get("error") or exc.reason) if isinstance(payload, dict) else str(exc.reason)
            message = f"Ollama 已响应，但接口返回 HTTP {exc.code}：{detail}"
        elif isinstance(exc, (OSError, error.URLError)):
            message = "无法连接 Ollama，请检查服务状态和连接地址。"
        else:
            message = str(exc)
        return {
            "reachable": False,
            "installed": False,
            "base_url": root,
            "version": "",
            "models": [],
            "model_ready": False,
            "model_id": wanted_model,
            "error": message,
        }


def probe_ollama_chat_model(base_url: str, model_id: str) -> dict[str, Any]:
    """Check direct chat and the native 16K route used by Pi."""

    if model_id not in OLLAMA_CHAT_MODEL_SPECS:
        raise ValueError("该模型不在已审核的本地助手推荐目录中")
    status = ollama_status(base_url, model_id=model_id)
    if not status.get("model_ready"):
        raise RuntimeError("推荐模型尚未完整安装到 Ollama")
    options = {
        "model": model_id,
        "stream": False,
        "reasoning_effort": "none",
        "max_tokens": 128,
        "temperature": 0,
    }
    answer = _request_json(
        base_url,
        "/v1/chat/completions",
        method="POST",
        payload={
            **options,
            "messages": [
                {"role": "system", "content": "只根据给定证据作答，并保留证据编号。"},
                {"role": "user", "content": "证据 [E1]：样地 A 有 12 株乔木。样地 A 有多少株乔木？请简短回答并引用 [E1]。"},
            ],
        },
        timeout=180,
    )
    content = str(((answer.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    if "12" not in content or "[E1]" not in content:
        raise RuntimeError("模型已加载，但证据引用探测未通过")
    tool = _request_json(
        base_url,
        "/v1/chat/completions",
        method="POST",
        payload={
            **options,
            "messages": [
                {"role": "system", "content": "回答前必须先调用 lookup_evidence 工具。"},
                {"role": "user", "content": "请调用 lookup_evidence 查询编号 E1。"},
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "lookup_evidence",
                    "description": "按编号读取证据。",
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                },
            }],
        },
        timeout=180,
    )
    calls = ((tool.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    if not any(isinstance(call, dict) and (call.get("function") or {}).get("name") == "lookup_evidence" for call in calls):
        raise RuntimeError("模型已加载，但工具调用探测未通过")
    native = _request_json(
        base_url,
        "/api/chat",
        method="POST",
        payload={
            "model": model_id,
            "stream": False,
            "think": False,
            "options": {"num_ctx": OLLAMA_CHAT_MODEL_SPECS[model_id]["context_tokens"], "num_predict": 128},
            "messages": [
                {"role": "system", "content": "只根据给定证据作答，并保留证据编号。"},
                {"role": "user", "content": "证据 [E1]：样地 A 有 12 株乔木。样地 A 有多少株乔木？请简短回答并引用 [E1]。"},
            ],
        },
        timeout=180,
    )
    native_content = str((native.get("message") or {}).get("content") or "")
    if "12" not in native_content or "[E1]" not in native_content:
        raise RuntimeError("模型已加载，但 Pi 16K 上下文探测未通过")
    return {
        "model_id": model_id,
        "loaded": True,
        "citation_verified": True,
        "tool_call_verified": True,
        "pi_context_verified": True,
        "completion_tokens": int((answer.get("usage") or {}).get("completion_tokens") or 0),
    }


class OllamaInstallManager:
    """Track ``ollama pull`` progress while Ollama handles layer resumption."""

    def __init__(self, *, base_url: str | None = None, state_path: str | Path | None = None) -> None:
        self.base_url = ollama_base_url(base_url)
        self.state_path = Path(state_path) if state_path else None
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._controls: dict[str, str] = {}
        self._callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._last_persist_at = 0.0
        self._load()

    def start(self, model_id: str = OLLAMA_VISION_MODEL, *, job_id: str = "", on_complete: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        model = str(model_id or "").strip()
        if model not in {OLLAMA_VISION_MODEL, *OLLAMA_CHAT_MODEL_SPECS}:
            raise ValueError("当前 Ollama 下载器不支持该模型")
        identifier = str(job_id or f"ollama:{model}").strip()[:180]
        current_status = ollama_status(self.base_url, model_id=model)
        with self._lock:
            if on_complete is not None:
                self._callbacks[identifier] = on_complete
            callback = on_complete or self._callbacks.get(identifier)
            existing = self._jobs.get(identifier)
            worker = self._threads.get(identifier)
            if worker and worker.is_alive():
                return deepcopy(existing or {})
            self._controls.pop(identifier, None)
            ready = bool(current_status.get("model_ready"))
            job = {
                "job_id": identifier,
                "state": "ready" if ready else "queued",
                "models": [model],
                "current_model": model,
                "completed_models": [model] if ready else [],
                "total_models": 1,
                "current_model_index": 0,
                "current_model_progress": 1.0 if ready else 0.0,
                "completed_bytes": 0,
                "total_bytes": 0,
                "current_file": "",
                "speed_bytes_per_second": 0.0,
                "eta_seconds": None,
                "progress": 1.0 if ready else 0.0,
                "source": "ollama",
                "message": "模型已在 Ollama 中，无需重复下载" if ready else "正在准备 Ollama 下载",
                "error": "" if ready else str(current_status.get("error", "") or ""),
                "started_at": int(time.time()),
                "updated_at": int(time.time()),
            }
            self._jobs[identifier] = job
            self._persist_locked()
            if ready:
                callback_job = deepcopy(job)
            else:
                callback_job = None
                worker = threading.Thread(target=self._run, args=(identifier, model, callback), daemon=True, name="scansci-ollama-pull")
                self._threads[identifier] = worker
                worker.start()
        if callback_job is not None and callback:
            callback(callback_job)
        return deepcopy(callback_job or job)

    def status(self, job_id: str = "") -> dict[str, Any]:
        with self._lock:
            if job_id:
                return deepcopy(self._jobs.get(job_id, {"job_id": job_id, "state": "idle"}))
            rows = sorted((deepcopy(item) for item in self._jobs.values()), key=lambda item: int(item.get("updated_at", 0) or 0), reverse=True)
            return {"jobs": rows, "active": next((item for item in rows if item.get("state") in {"queued", "downloading", "pausing", "cancelling"}), None)}

    def pause(self, job_id: str) -> dict[str, Any]:
        return self._control(job_id, "pause")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._control(job_id, "cancel")

    def resume(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            model = str(self._jobs.get(job_id, {}).get("current_model") or "")
        return self.start(model, job_id=job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        return self.resume(job_id)

    def _control(self, job_id: str, action: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if job is None:
                raise ValueError("Ollama 下载任务不存在")
            if job.get("state") in {"ready", "failed", "cancelled"}:
                return deepcopy(job)
            self._controls[str(job_id)] = action
            job["state"] = "pausing" if action == "pause" else "cancelling"
            job["message"] = "正在暂停 Ollama 下载" if action == "pause" else "正在取消 Ollama 下载"
            job["updated_at"] = int(time.time())
            self._persist_locked()
            return deepcopy(job)

    def _run(self, job_id: str, model: str, on_complete: Callable[[dict[str, Any]], None] | None) -> None:
        try:
            req = request.Request(
                f"{self.base_url}/api/pull",
                data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
                headers={"Accept": "application/x-ndjson", "Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=300) as response:  # nosec B310 - local Ollama endpoint
                self._update(job_id, state="downloading", message="Ollama 正在下载模型")
                for raw_line in response:
                    with self._lock:
                        control = self._controls.get(job_id, "")
                    if control:
                        self._update(job_id, state="paused" if control == "pause" else "cancelled", message="已暂停；继续时 Ollama 会复用已下载层" if control == "pause" else "已取消；重试时 Ollama 会复用已下载层")
                        return
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    total = int(event.get("total", 0) or 0)
                    completed = int(event.get("completed", 0) or 0)
                    progress = min(1.0, max(0.0, completed / total)) if total else 0.0
                    self._update(job_id, state="downloading", progress=progress, completed_bytes=completed, total_bytes=total, message=str(event.get("status", "正在下载模型") or "正在下载模型"))
            status = ollama_status(self.base_url, model_id=model)
            if not status.get("model_ready"):
                raise RuntimeError(str(status.get("error", "Ollama 未确认模型已安装")))
            self._update(job_id, state="ready", progress=1.0, completed_models=[model], current_model_progress=1.0, message="模型已安装并通过 Ollama 清单校验", error="")
            if on_complete:
                on_complete(self.status(job_id))
        except Exception as exc:
            self._update(job_id, state="failed", message="Ollama 模型下载未完成", error=f"{type(exc).__name__}: {exc}"[:1200])

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(changes)
            job["updated_at"] = int(time.time())
            # NDJSON progress events can arrive many times per second while a
            # model downloads.  Persisting each line thrashes the disk and
            # blocks concurrent status() reads behind the file write, so
            # transient progress is throttled to once per second while
            # terminal states always persist.
            state = str(job.get("state", ""))
            now = time.monotonic()
            terminal = state in {"ready", "failed", "cancelled", "paused", "interrupted"}
            if terminal or now - self._last_persist_at >= 1.0:
                self._persist_locked()
                self._last_persist_at = now

    def _load(self) -> None:
        if not self.state_path or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            rows = payload.get("jobs", []) if isinstance(payload, dict) else []
            now = int(time.time())
            restored: dict[str, dict[str, Any]] = {}
            for item in rows:
                if not isinstance(item, dict) or not item.get("job_id"):
                    continue
                job = dict(item)
                if str(job.get("state", "")) in {"queued", "downloading", "pausing", "cancelling"}:
                    # The HTTP stream belongs to the previous desktop
                    # process. Do not leave the UI spinning forever after a
                    # restart; Ollama keeps completed layers and can resume
                    # safely when the user presses Continue.
                    job["state"] = "interrupted"
                    job["message"] = "ScanSci 已重启；可以继续下载，Ollama 会复用已完成的模型分层"
                    job["updated_at"] = now
                restored[str(job.get("job_id"))] = job
            self._jobs = restored
        except (OSError, json.JSONDecodeError):
            self._jobs = {}

    def _persist_locked(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "jobs": list(self._jobs.values())[-16:]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)
