"""Small, resumable Ollama model management for the local desktop app."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib import error, request


OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
OLLAMA_VISION_MODEL = "minicpm-v4.6"
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
        return {
            "reachable": False,
            "installed": False,
            "base_url": root,
            "version": "",
            "models": [],
            "model_ready": False,
            "model_id": wanted_model,
            "error": "Ollama 未运行，请先安装并启动 Ollama" if isinstance(exc, (OSError, error.URLError)) else str(exc),
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
        if model != OLLAMA_VISION_MODEL:
            raise ValueError("当前 Ollama 视觉下载器只支持 MiniCPM-V 4.6")
        identifier = str(job_id or f"ollama:{model}").strip()[:180]
        current_status = ollama_status(self.base_url)
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
        return self.start(job_id=job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        return self.start(job_id=job_id)

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
                self._update(job_id, state="downloading", message="Ollama 正在下载视觉模型")
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
                    self._update(job_id, state="downloading", progress=progress, completed_bytes=completed, total_bytes=total, message=str(event.get("status", "正在下载视觉模型") or "正在下载视觉模型"))
            status = ollama_status(self.base_url)
            if not status.get("model_ready"):
                raise RuntimeError(str(status.get("error", "Ollama 未确认模型已安装")))
            self._update(job_id, state="ready", progress=1.0, completed_models=[model], current_model_progress=1.0, message="视觉模型已安装并通过 Ollama 校验", error="")
            if on_complete:
                on_complete(self.status(job_id))
        except Exception as exc:
            self._update(job_id, state="failed", message="Ollama 视觉模型下载未完成", error=f"{type(exc).__name__}: {exc}"[:1200])

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
