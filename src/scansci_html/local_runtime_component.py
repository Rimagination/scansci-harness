"""Install and locate ScanSci's versioned local-model runtime component.

The desktop core deliberately does not bundle PyTorch or Transformers.  Those
packages live in a separately versioned sidecar that is downloaded once and
reused by later ScanSci desktop releases.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from importlib.util import find_spec
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from .build_info import current_build_info
from .manifest_trust import verify_manifest_signature
from .release_transport import open_release_url, validate_release_url
from .local_runtime_contract import (
    COMPONENT_ID,
    COMPONENT_VERSION,
    EXECUTABLE_NAME,
    MINIMUM_COMPONENT_VERSION,
)


RUNTIME_MANIFEST_ENV = "SCANSCI_LOCAL_RUNTIME_MANIFEST_URL"
RUNTIME_MANIFEST_FALLBACKS_ENV = "SCANSCI_LOCAL_RUNTIME_MANIFEST_FALLBACKS"
RUNTIME_EXECUTABLE_ENV = "SCANSCI_LOCAL_RUNTIME_EXECUTABLE"
RUNTIME_ROOT_ENV = "SCANSCI_LOCAL_RUNTIME_ROOT"
UPDATE_MANIFEST_ENV = "SCANSCI_UPDATE_MANIFEST_URL"
RELEASE_PUBLIC_KEY_ENV = "SCANSCI_RELEASE_PUBLIC_KEY"
DEFAULT_RUNTIME_MANIFEST_URL = "https://github.com/Rimagination/scansci-portal/releases/download/local-runtime-v1.0.0/local-transformers.json"
DEFAULT_RUNTIME_RELEASE_URL = "https://github.com/Rimagination/scansci-portal/releases/tag/local-runtime-v1.0.0"
DEFAULT_RUNTIME_MANIFEST_FALLBACK_URLS = (
    "https://github.com/Rimagination/scansci-portal/releases/download/local-runtime-v1.0.0/local-transformers.json",
)
_RUNTIME_RETRY_DELAYS_SECONDS = (0.0, 1.0, 3.0, 8.0)
_MAX_RUNTIME_ARCHIVE_FILES = 200_000
_MAX_UNPACKED_RUNTIME_BYTES = 16 * 1024**3
_MAX_MANIFEST_BYTES = 1_000_000
_MAX_RUNTIME_DOWNLOAD_BYTES = 16 * 1024**3
# Loading a downloaded vision/audio model can take longer than the old fixed
# 45-second window, especially on the first CUDA initialization.  Do not kill
# a healthy component merely because the model needs time to load.
_RUNTIME_START_TIMEOUT_SECONDS = 180.0


class LocalRuntimeInstallPaused(RuntimeError):
    """Raised when the user pauses the optional runtime installation."""


class LocalRuntimeInstallCancelled(RuntimeError):
    """Raised when the user cancels the optional runtime installation."""


class _RetryableRuntimeDownloadError(RuntimeError):
    """A runtime download failed transiently and may safely resume."""


def _semantic_version_less(current: str, required: str) -> bool:
    """Compare managed component versions without guessing manual labels."""

    pattern = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
    current_match = pattern.fullmatch(str(current or "").strip())
    required_match = pattern.fullmatch(str(required or "").strip())
    if current_match is None or required_match is None:
        return False
    return tuple(int(value) for value in current_match.groups()) < tuple(
        int(value) for value in required_match.groups()
    )


class LocalRuntimeComponent:
    """Manage ScanSci's optional local Transformers sidecar after user approval."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        manifest_url: str | None = None,
        fallback_manifest_url: str | None = None,
        component_id: str = COMPONENT_ID,
        executable_name: str = EXECUTABLE_NAME,
        component_version: str = COMPONENT_VERSION,
        minimum_component_version: str | None = None,
        default_manifest_url: str = DEFAULT_RUNTIME_MANIFEST_URL,
        default_release_url: str = DEFAULT_RUNTIME_RELEASE_URL,
        manifest_env: str = RUNTIME_MANIFEST_ENV,
        fallbacks_env: str = RUNTIME_MANIFEST_FALLBACKS_ENV,
        executable_env: str = RUNTIME_EXECUTABLE_ENV,
        build_manifest_key: str = "runtime_manifest_url",
        install_job_id: str | None = None,
        display_name: str = "ScanSci 本地运行能力",
        system_executable_names: tuple[str, ...] = (),
        embedded_profiles: frozenset[str] = frozenset({"full"}),
        source_dependency_modules: tuple[str, ...] = ("torch", "transformers", "sentence_transformers"),
        release_public_key: str | None = None,
    ) -> None:
        self.component_id = str(component_id or COMPONENT_ID)
        self.executable_name = str(executable_name or EXECUTABLE_NAME)
        self.component_version = str(component_version or COMPONENT_VERSION)
        self.minimum_component_version = str(
            minimum_component_version
            or (MINIMUM_COMPONENT_VERSION if self.component_id == COMPONENT_ID else self.component_version)
        )
        self.default_manifest_url = str(default_manifest_url or DEFAULT_RUNTIME_MANIFEST_URL)
        self.default_release_url = str(default_release_url or DEFAULT_RUNTIME_RELEASE_URL)
        self.executable_env = str(executable_env or RUNTIME_EXECUTABLE_ENV)
        self.build_manifest_key = str(build_manifest_key or "runtime_manifest_url")
        self.install_job_id = str(
            install_job_id
            or ("local-runtime" if self.component_id == COMPONENT_ID else f"runtime:{self.component_id}")
        )
        self.display_name = str(display_name or "ScanSci 本地运行能力")
        self._system_executable_names = tuple(system_executable_names or ())
        self._embedded_profiles = frozenset(embedded_profiles or ())
        self._source_dependency_modules = tuple(source_dependency_modules or ())
        local_app_data = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        configured_root = os.getenv(RUNTIME_ROOT_ENV, "").strip()
        self.root = Path(root or configured_root or Path(local_app_data) / "ScanSci" / "runtimes" / self.component_id).resolve()
        build = current_build_info()
        if release_public_key is None:
            release_public_key = os.getenv(RELEASE_PUBLIC_KEY_ENV, "") or build.get("release_public_key", "")
        self.release_public_key = str(release_public_key or "").strip()
        packaged_core_fallback = (
            self.default_manifest_url
            if bool(build.get("frozen")) and str(build.get("package_profile", "")) == "core"
            else ""
        )
        if manifest_url is not None:
            # An explicit empty value is an intentional opt-out used by source
            # builds and tests.  Do not silently resurrect a release URL in
            # that case.
            configured_urls = [manifest_url]
        else:
            configured_urls = [
                os.getenv(manifest_env),
                build.get(self.build_manifest_key),
                packaged_core_fallback,
                fallback_manifest_url,
                os.getenv(UPDATE_MANIFEST_ENV),
            ]
            if self.component_id == COMPONENT_ID:
                configured_urls.extend(DEFAULT_RUNTIME_MANIFEST_FALLBACK_URLS)
            configured_urls.extend(
                value
                for value in re.split(r"[;\r\n]+", os.getenv(fallbacks_env, ""))
                if value.strip()
            )
        self.manifest_urls = self._unique_urls(configured_urls)
        self.manifest_url = self.manifest_urls[0] if self.manifest_urls else ""
        self._install_lock = threading.RLock()
        self._install_job: dict[str, Any] = {
            "job_id": self.install_job_id,
            "state": "idle",
            "phase": "",
            "progress": 0.0,
            "message": "",
            "error": "",
            "current_file": "",
            "completed_bytes": 0,
            "total_bytes": 0,
            "speed_bytes_per_second": 0.0,
            "eta_seconds": None,
            "updated_at": int(time.time()),
        }
        self._install_thread: threading.Thread | None = None
        self._install_control = ""
        self._download_sample: tuple[int, float, float] | None = None
        self._last_install_persist_at = 0.0
        self._process: subprocess.Popen[Any] | None = None
        self._process_model_id = ""
        self._process_base_url = ""
        self._last_manifest_source = ""
        self._last_manifest_failures: list[dict[str, str]] = []
        self._manual_install_paths: tuple[str, ...] = ()
        # Runtime components are versioned independently from the desktop
        # core.  Keep a small per-process capability cache so a newly
        # installed core can still start an older sidecar whose CLI predates
        # optional flags such as ``--state-dir``.
        self._cli_argument_support: dict[str, bool] = {}
        self._load_install_job()

    @property
    def active_path(self) -> Path:
        return self.root / "active.json"

    def executable(self) -> Path | None:
        override = os.getenv(self.executable_env, "").strip()
        if override:
            candidate = Path(override).expanduser().resolve()
            if candidate.is_file():
                return candidate
        try:
            active = json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            active = {}
        if isinstance(active, dict):
            relative = str(active.get("executable", "")).strip()
            if relative:
                candidate = (self.root / relative).resolve()
                try:
                    candidate.relative_to(self.root)
                except ValueError:
                    candidate = None
                if candidate is not None and candidate.is_file():
                    return candidate
        for name in self._system_executable_names:
            resolved = shutil.which(name)
            if resolved:
                system_candidate = Path(resolved).resolve()
                if system_candidate.is_file():
                    return system_candidate
        return None

    def ensure_process(self, model_id: str) -> str:
        """Start the isolated runtime and return its URL only after it is usable.

        A daemon can answer ``/health`` while its model worker is still loading.
        Returning that URL early made the UI report an opaque exit-code error on
        the first image request.  New runtimes therefore have to report
        ``available=true`` (or ``status=ok``); legacy runtimes that only expose
        ``model`` remain supported.
        """

        wanted = str(model_id or "").strip()
        if not wanted:
            raise ValueError("本地运行组件缺少模型 ID")
        executable = self.executable()
        if executable is None:
            raise RuntimeError("本地运行组件尚未安装，请先在设置 → 本地模型中安装")
        with self._install_lock:
            base_url = self._component_base_url()
            health = self._health_snapshot(base_url)
            health_model = self._health_model_from_payload(health)
            if health_model == wanted:
                if self._health_needs_probe(health, wanted) or self._compatibility_probe_needed(wanted):
                    self._probe_generation(base_url, wanted)
                    self._process_model_id = wanted
                    self._process_base_url = base_url
                    return base_url
                readiness = self._health_readiness(health)
                if readiness == "ready":
                    self._process_model_id = wanted
                    self._process_base_url = base_url
                    return base_url
                if readiness == "failed":
                    raise RuntimeError(self._health_failure_message(health, wanted))
                if self._wait_for_health_locked(base_url, wanted):
                    self._process_model_id = wanted
                    self._process_base_url = base_url
                    return base_url
                if self._process is None:
                    raise RuntimeError(
                        f"本地模型 {wanted} 启动超时（已等待 {int(_RUNTIME_START_TIMEOUT_SECONDS)} 秒）。"
                        "请打开设置 → 本地模型检查显存、模型文件和运行组件。"
                    )
                health_model = ""
            if health_model and health_model != wanted:
                if self._process is not None and self._process.poll() is None:
                    self._stop_process_locked()
                else:
                    raise RuntimeError(
                        f"本地运行组件正在服务 {health_model}，请等待当前本地模型请求完成后再切换。"
                    )
            if self._process is not None and self._process.poll() is None:
                if self._process_model_id == wanted and self._wait_for_health_locked(base_url, wanted):
                    self._process_base_url = base_url
                    return base_url
                self._stop_process_locked()
            command = [
                str(executable),
                "--model-id",
                wanted,
                "--host",
                "127.0.0.1",
                "--port",
                str(self._component_port()),
                "--parent-pid",
                str(os.getpid()),
            ]
            if self._supports_cli_argument(executable, "--state-dir"):
                command.extend(
                    [
                        "--state-dir",
                        str((self.root / "state").resolve()),
                    ]
                )
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # The bundled runtime executable reads its state directory from the
            # environment; passing the unsupported --state-dir flag exits 2.
            worker_environment = os.environ.copy()
            worker_environment["SCANSCI_LOCAL_RUNTIME_STATE_DIR"] = str(
                (self.root / "state").resolve()
            )
            try:
                self._process = subprocess.Popen(
                    command,
                    cwd=str(executable.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                    env=worker_environment,
                )
            except OSError as error:
                raise RuntimeError(f"无法启动本地运行组件：{error}") from error
            deadline = time.monotonic() + _RUNTIME_START_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None
                    raise RuntimeError(
                        f"本地运行组件启动失败（退出码 {code}）。"
                        "请打开设置 → 本地模型查看运行组件和模型状态。"
                    )
                health = self._health_snapshot(base_url)
                if self._health_model_from_payload(health) == wanted:
                    if self._health_needs_probe(health, wanted):
                        self._probe_generation(base_url, wanted)
                        self._process_model_id = wanted
                        self._process_base_url = base_url
                        return base_url
                    readiness = self._health_readiness(health)
                    if readiness == "failed":
                        self._stop_process_locked()
                        raise RuntimeError(self._health_failure_message(health, wanted))
                    if readiness == "ready":
                        self._process_model_id = wanted
                        self._process_base_url = base_url
                        return base_url
                time.sleep(0.25)
            self._stop_process_locked()
            raise RuntimeError(
                f"本地运行组件启动超时（已等待 {int(_RUNTIME_START_TIMEOUT_SECONDS)} 秒），"
                "请重试或打开本地模型查看组件状态"
            )

    def _component_port(self) -> int:
        try:
            value = int(os.getenv("SCANSCI_LOCAL_RUNTIME_PORT", "17863"))
        except ValueError:
            value = 17863
        return value if 1024 <= value <= 65535 else 17863

    def _component_base_url(self) -> str:
        return f"http://127.0.0.1:{self._component_port()}/v1"

    def _supports_cli_argument(self, executable: Path, argument: str) -> bool:
        """Return whether an installed sidecar advertises an optional flag.

        The local runtime is a separately downloaded executable.  In the
        field, an older runtime may remain installed while the core starts
        sending a newer optional argument; argparse then exits with code 2
        before the model server can start.  A bounded ``--help`` probe keeps
        the launch command compatible in both directions and is cached for
        the lifetime of this component manager.
        """

        key = str(executable.resolve())
        cached = self._cli_argument_support.get(key)
        if cached is not None:
            return cached
        try:
            completed = subprocess.run(
                [str(executable), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            output = f"{completed.stdout}\n{completed.stderr}"
            supported = str(argument) in output if completed.returncode == 0 else True
        except (OSError, subprocess.SubprocessError, TypeError):
            # If the probe cannot run, omit the optional flag and rely on the
            # state-directory environment variable.  This is safer for an
            # older or locked-down sidecar than sending an argument it may
            # reject before the daemon can start.
            supported = False
        self._cli_argument_support[key] = supported
        return supported

    @staticmethod
    def _health_payload(base_url: str) -> dict[str, Any] | None:
        try:
            root = base_url[:-3] if base_url.endswith("/v1") else base_url.rstrip("/")
            with urlopen(f"{root}/health", timeout=1.5) as response:  # noqa: S310 - loopback only
                payload = json.loads(response.read(256_000).decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _health_snapshot(self, base_url: str) -> dict[str, Any] | None:
        payload = self._health_payload(base_url)
        if payload is not None:
            if not any(key in payload for key in ("available", "status", "error")):
                payload = dict(payload)
                payload["_legacy_health"] = True
            return payload
        # Older components expose only {model: ...}; preserve compatibility.
        model = self._health_model(base_url)
        return {"model": model, "_legacy_health": True} if model else None

    @staticmethod
    def _health_model_from_payload(payload: dict[str, Any] | None) -> str:
        return str(payload.get("model", "")).strip() if isinstance(payload, dict) else ""

    @staticmethod
    def _compatibility_probe_needed(wanted: str) -> bool:
        """Retry a previously failed probe when the runtime is healthy again."""

        try:
            from .local_model_market import installed_models

            return any(
                str(item.get("id", "")).strip() == wanted
                and str(item.get("runtime_probe_state", "")).strip().lower() == "failed"
                for item in installed_models()
                if isinstance(item, dict)
            )
        except (OSError, ValueError):
            return False

    @staticmethod
    def _health_readiness(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict) or payload.get("_legacy_health"):
            return "ready" if isinstance(payload, dict) else "unknown"
        # The bundled lazy server reports ``status=ok`` before it loads
        # a snapshot.  Vision/chat models are probed separately above; other
        # model kinds do not necessarily implement chat generation, so their
        # healthy daemon status must remain sufficient for compatibility.
        if payload.get("available") is True or str(payload.get("status", "")).lower() == "ok":
            return "ready"
        if str(payload.get("status", "")).lower() == "degraded" or isinstance(payload.get("error"), dict):
            return "failed"
        return "starting"

    @classmethod
    def _health_failure_message(cls, payload: dict[str, Any] | None, wanted: str) -> str:
        error = payload.get("error") if isinstance(payload, dict) else None
        code = str(error.get("code", "")) if isinstance(error, dict) else ""
        message = str(error.get("message", "")) if isinstance(error, dict) else ""
        message = " ".join(message.split())[:320]
        detail = f"（{code}）" if code else ""
        if message:
            return f"本地模型 {wanted} 未能就绪{detail}：{cls._actionable_runtime_message(message)}"
        return f"本地模型 {wanted} 未能就绪。请打开设置 → 本地模型检查模型文件、显存和运行组件。"

    @staticmethod
    def _actionable_runtime_message(message: str) -> str:
        """Turn common native loader failures into a next step users can follow."""

        normalized = " ".join(str(message or "").split())
        lowered = normalized.casefold()
        if "1455" in lowered or "页面文件太小" in normalized or "page file" in lowered:
            return (
                f"{normalized}。请在 Windows 系统设置中增大虚拟内存（页面文件）或释放系统内存后重试；"
                "也可改用云端视觉模型或 OCR。"
            )
        if "out of memory" in lowered or "显存不足" in normalized or "cuda" in lowered and "memory" in lowered:
            return (
                f"{normalized}。请关闭其他 GPU 程序、选择更小的 4-bit 模型，"
                "或改用云端视觉模型/OCR。"
            )
        return normalized

    @staticmethod
    def _health_needs_probe(payload: dict[str, Any] | None, wanted: str) -> bool:
        """Identify the lazy-loading runtime used by the published binary."""

        if not isinstance(payload, dict) or payload.get("available") is not None:
            return False
        if payload.get("loaded") is not False:
            return False
        lowered = str(wanted or "").casefold()
        return any(token in lowered for token in ("minicpm", "qwen3.5", "qwen3-vl", "llava", "internvl"))

    def _probe_generation(self, base_url: str, wanted: str) -> None:
        """Force lazy runtimes to load a chat/vision model before UI requests."""

        request = Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(
                {
                    "model": wanted,
                    "messages": [{"role": "user", "content": "Reply with one word: ready"}],
                    "stream": False,
                    "max_tokens": 1,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=min(_RUNTIME_START_TIMEOUT_SECONDS, 180.0)) as response:  # noqa: S310 - loopback only
                payload = json.loads(response.read(256_000).decode("utf-8"))
            generated_text = self._probe_response_text(payload)
            if not isinstance(payload, dict) or not payload.get("choices") or not generated_text:
                raise RuntimeError("最小生成没有返回有效文本")
            self._record_probe_success(wanted, generated_text)
        except Exception as error:  # noqa: BLE001 - normalize sidecar errors for the UI
            message = str(error).strip()
            if hasattr(error, "read"):
                try:
                    raw = error.read(256_000).decode("utf-8", errors="replace")
                    detail = json.loads(raw)
                    nested = detail.get("error") if isinstance(detail, dict) else None
                    if isinstance(nested, dict):
                        message = str(nested.get("message") or nested.get("code") or message).strip()
                except Exception:
                    pass
            message = self._actionable_runtime_message(message[:500])
            self._record_probe_failure(wanted, message)
            raise RuntimeError(f"本地模型 {wanted} 无法完成最小生成：{message}") from error

    @staticmethod
    def _probe_response_text(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ""
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content", choice.get("text", ""))
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", item.get("content", "")) or ""))
                elif item is not None:
                    parts.append(str(item))
            return "".join(parts).strip()
        return str(content or "").strip()

    def _record_probe_success(self, wanted: str, generated_text: str) -> None:
        """Replace a stale failure after a later isolated probe succeeds."""

        try:
            from .local_model_market import installed_models
            from .local_runtime_compatibility import ModelCompatibilityStore

            record = next(
                (item for item in installed_models() if str(item.get("id", "")) == wanted),
                None,
            )
            if not isinstance(record, dict) or not str(record.get("path", "")).strip():
                return
            ModelCompatibilityStore(self.root / "state" / "model-compatibility.json").record_success(
                record,
                component_version=self.component_version,
                runtime_versions={},
                generated_text=generated_text,
            )
        except (OSError, ValueError):
            # Runtime availability must remain authoritative even if a diagnostic
            # state file cannot be updated on a locked-down machine.
            return

    def _record_probe_failure(self, wanted: str, message: str) -> None:
        """Persist a model-specific failure so routing stops retrying it blindly."""

        try:
            from .local_model_market import installed_models
            from .local_runtime_compatibility import ModelCompatibilityStore

            record = next(
                (item for item in installed_models() if str(item.get("id", "")) == wanted),
                None,
            )
            if not isinstance(record, dict) or not str(record.get("path", "")).strip():
                return
            ModelCompatibilityStore(self.root / "state" / "model-compatibility.json").record_failure(
                record,
                component_version=self.component_version,
                error={
                    "code": "model_probe_failed",
                    "message": message[:500],
                    "model": wanted,
                    "phase": "load_or_generate",
                },
            )
        except Exception:
            # Diagnostics must never hide the original model error.
            return

    def _wait_for_health_locked(self, base_url: str, wanted: str) -> bool:
        deadline = time.monotonic() + _RUNTIME_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                return False
            health = self._health_snapshot(base_url)
            if self._health_model_from_payload(health) == wanted:
                if self._health_needs_probe(health, wanted):
                    self._probe_generation(base_url, wanted)
                    return True
                readiness = self._health_readiness(health)
                if readiness == "ready":
                    return True
                if readiness == "failed":
                    raise RuntimeError(self._health_failure_message(health, wanted))
            elif self._health_model_from_payload(health):
                return False
            time.sleep(0.25)
        return False

    @staticmethod
    def _health_model(base_url: str) -> str:
        try:
            root = base_url[:-3] if base_url.endswith("/v1") else base_url.rstrip("/")
            with urlopen(f"{root}/health", timeout=1.5) as response:  # noqa: S310 - loopback only
                payload = json.loads(response.read(256_000).decode("utf-8"))
            return str(payload.get("model", "")).strip() if isinstance(payload, dict) else ""
        except (OSError, ValueError, json.JSONDecodeError):
            return ""

    def _stop_process_locked(self) -> None:
        process = self._process
        self._process = None
        self._process_model_id = ""
        self._process_base_url = ""
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def status(self) -> dict[str, Any]:
        executable = self.executable()
        build = current_build_info()
        source_runtime = not bool(build.get("frozen"))
        embedded_runtime = bool(build.get("frozen")) and str(build.get("package_profile", "")) in self._embedded_profiles
        source_dependencies_ready = source_runtime and bool(self._source_dependency_modules) and all(
            find_spec(module) is not None
            for module in self._source_dependency_modules
        )
        installed = executable is not None
        managed_component = False
        if executable is not None:
            try:
                executable.relative_to(self.root)
                managed_component = True
            except ValueError:
                managed_component = False
        version = ""
        if managed_component:
            try:
                active = json.loads(self.active_path.read_text(encoding="utf-8"))
                version = str(active.get("version", ""))
            except (OSError, json.JSONDecodeError):
                version = ""
        elif installed:
            version = "external"
        mode = (
            "component"
            if managed_component
            else "system"
            if installed and self._system_executable_names
            else "external"
            if installed
            else "embedded"
            if embedded_runtime
            else "source"
            if source_dependencies_ready
            else "missing"
        )
        update_available = bool(
            managed_component
            and version
            and _semantic_version_less(version, self.component_version)
        )
        update_required = bool(
            managed_component
            and version
            and _semantic_version_less(version, self.minimum_component_version)
        )
        return {
            "id": self.component_id,
            "name": self.display_name,
            "version": version or (self.component_version if source_runtime or embedded_runtime else ""),
            "required_version": self.minimum_component_version,
            "recommended_version": self.component_version,
            "minimum_version": self.minimum_component_version,
            "update_available": bool(update_available and self.manifest_urls),
            "update_required": update_required,
            "installed": bool(installed or source_dependencies_ready or embedded_runtime),
            "mode": mode,
            "executable": str(executable or ""),
            "install_available": bool(self.manifest_urls),
            "manifest_configured": bool(self.manifest_urls),
            "manifest_url": self.manifest_url,
            "manifest_urls": list(self.manifest_urls),
            "manifest_channel_count": len(self.manifest_urls),
            "manifest_release_url": self.default_release_url,
            "manual_install_available": True,
            "last_manifest_source": self._last_manifest_source,
            "last_manifest_failures": list(self._last_manifest_failures),
            "root": str(self.root),
            "install_job": self.install_status(),
        }

    def install_status(self) -> dict[str, Any]:
        with self._install_lock:
            snapshot = dict(self._install_job)
            updated_at = float(snapshot.get("updated_at", 0) or 0)
            age = max(0, round(time.time() - updated_at)) if updated_at else 0
            snapshot["last_update_seconds"] = age
            snapshot["stalled"] = bool(snapshot.get("state") in {"queued", "installing", "pausing", "cancelling"} and age >= 90)
            return snapshot

    def start_install(self) -> dict[str, Any]:
        with self._install_lock:
            if self._install_thread is not None and self._install_thread.is_alive():
                return dict(self._install_job)
            if not self.manifest_urls:
                now = int(time.time())
                self._install_job = {
                    "job_id": self.install_job_id,
                    "state": "failed",
                    "phase": "unavailable",
                    "progress": 0.0,
                    "message": "自动下载清单不可用，可选择本地组件安装",
                    "error": f"{self.display_name}尚未安装；自动下载清单不可用。请选择官方 ZIP 后从本地文件安装。",
                    "current_file": "",
                    "completed_bytes": 0,
                    "total_bytes": 0,
                    "speed_bytes_per_second": 0.0,
                    "eta_seconds": None,
                    "updated_at": now,
                }
                self._persist_install_job(force=True)
                return dict(self._install_job)
            self._install_job = {
                "job_id": self.install_job_id,
                "state": "queued",
                "phase": "preparing",
                "progress": 0.0,
                "message": f"准备安装{self.display_name}",
                "error": "",
                "started_at": time.time(),
                "updated_at": int(time.time()),
                "current_file": "",
                "completed_bytes": 0,
                "total_bytes": 0,
                "speed_bytes_per_second": 0.0,
                "eta_seconds": None,
                "source": "automatic",
            }
            self._manual_install_paths = ()
            self._download_sample = None
            self._install_control = ""
            self._persist_install_job(force=True)
            self._install_thread = threading.Thread(
                target=self._install_in_background,
                daemon=True,
                name=f"scansci-{self.component_id}-install",
            )
            self._install_thread.start()
            return dict(self._install_job)

    def start_local_install(self, paths: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Install a runtime selected from local files in the native dialog.

        The user may select a single ZIP, or the ZIP parts plus the matching
        ``local-transformers.json`` manifest from the official release.  The
        same hash, size, ZIP path and diagnostic checks used by automatic
        downloads are applied before activation.
        """

        normalized = self._normalize_manual_paths(paths)
        with self._install_lock:
            if self._install_thread is not None and self._install_thread.is_alive():
                return dict(self._install_job)
            self._manual_install_paths = tuple(str(path) for path in normalized)
            self._install_job = {
                "job_id": self.install_job_id,
                "state": "queued",
                "phase": "preparing",
                "progress": 0.0,
                "message": f"准备校验{self.display_name}",
                "error": "",
                "started_at": time.time(),
                "updated_at": int(time.time()),
                "current_file": "",
                "completed_bytes": 0,
                "total_bytes": 0,
                "speed_bytes_per_second": 0.0,
                "eta_seconds": None,
                "source": "manual",
            }
            self._download_sample = None
            self._install_control = ""
            self._persist_install_job(force=True)
            self._install_thread = threading.Thread(
                target=self._install_in_background,
                daemon=True,
                name=f"scansci-{self.component_id}-manual-install",
            )
            self._install_thread.start()
            return dict(self._install_job)

    def pause_install(self) -> dict[str, Any]:
        return self._request_install_control("pause")

    def cancel_install(self) -> dict[str, Any]:
        return self._request_install_control("cancel")

    def resume_install(self) -> dict[str, Any]:
        return self._restart_install("resume")

    def retry_install(self) -> dict[str, Any]:
        return self._restart_install("retry")

    def _request_install_control(self, action: str) -> dict[str, Any]:
        with self._install_lock:
            state = str(self._install_job.get("state", "idle"))
            if state == "ready":
                raise ValueError(f"本地运行组件当前状态不支持{('暂停' if action == 'pause' else '取消')}：{state}")
            if state in {"failed", "cancelled"}:
                if action == "cancel" and state == "cancelled":
                    return self.install_status()
                raise ValueError(f"本地运行组件当前状态不支持{('暂停' if action == 'pause' else '取消')}：{state}")
            thread = self._install_thread
            if thread is None or not thread.is_alive():
                self._install_job.update(
                    {
                        "state": "paused" if action == "pause" else "cancelled",
                        "phase": "paused" if action == "pause" else "cancelled",
                        "message": (
                            "安装已暂停；恢复时会继续使用已下载的组件归档"
                            if action == "pause"
                            else "安装已取消；已有组件归档会保留，重试时可续传"
                        ),
                        "error": "",
                        "updated_at": int(time.time()),
                    }
                )
                self._persist_install_job(force=True)
                return self.install_status()
            self._install_control = action
            self._install_job.update(
                {
                    "state": "pausing" if action == "pause" else "cancelling",
                    "message": "正在暂停组件安装…" if action == "pause" else "正在取消组件安装…",
                    "updated_at": int(time.time()),
                }
            )
            self._persist_install_job(force=True)
            return self.install_status()

    def _restart_install(self, action: str) -> dict[str, Any]:
        with self._install_lock:
            thread = self._install_thread
            if thread is not None and thread.is_alive():
                return self.install_status()
            state = str(self._install_job.get("state", "idle"))
            if state == "ready":
                return self.install_status()
            if state not in {"paused", "interrupted", "failed", "cancelled", "queued", "installing"}:
                raise ValueError(f"本地运行组件当前状态不支持{action}：{state}")
            manual = str(self._install_job.get("source", "automatic")) == "manual"
            paths = self._manual_install_paths
            if manual and not paths:
                raise ValueError("手动安装任务需要重新选择本地组件文件")
        return self.start_local_install(paths) if manual else self.start_install()

    def _check_install_control(self) -> None:
        with self._install_lock:
            action = self._install_control
        if action == "pause":
            raise LocalRuntimeInstallPaused("用户已暂停本地运行组件安装")
        if action == "cancel":
            raise LocalRuntimeInstallCancelled("用户已取消本地运行组件安装")

    def _install_in_background(self) -> None:
        try:
            if self._manual_install_paths:
                result = self.install_local(self._manual_install_paths, progress_callback=self._update_install_progress)
            else:
                result = self.install(progress_callback=self._update_install_progress)
            self._check_install_control()
            with self._install_lock:
                self._install_job.update(
                    {
                        "state": "ready",
                        "phase": "ready",
                        "progress": 1.0,
                        "message": f"{self.display_name}已就绪",
                        "error": "",
                        "result": result,
                        "finished_at": time.time(),
                        "updated_at": int(time.time()),
                        "speed_bytes_per_second": 0.0,
                        "eta_seconds": 0,
                        "source": str(self._install_job.get("source", "automatic")),
                    }
                )
                self._persist_install_job(force=True)
        except LocalRuntimeInstallPaused as exc:
            self._finish_controlled_install("paused", str(exc))
        except LocalRuntimeInstallCancelled as exc:
            self._finish_controlled_install("cancelled", str(exc))
        except Exception as exc:
            with self._install_lock:
                self._install_job.update(
                    {
                        "state": "failed",
                        "phase": "failed",
                        "message": "安装未完成",
                        "error": str(exc),
                        "finished_at": time.time(),
                        "updated_at": int(time.time()),
                        "source": str(self._install_job.get("source", "automatic")),
                    }
                )
                self._persist_install_job(force=True)

    def _finish_controlled_install(self, state: str, detail: str) -> None:
        with self._install_lock:
            self._install_control = ""
            self._install_job.update(
                {
                    "state": state,
                    "phase": state,
                    "message": (
                        "安装已暂停；恢复时会继续使用已下载的组件归档"
                        if state == "paused"
                        else "安装已取消；已有组件归档会保留，重试时可续传"
                    ),
                    "error": "" if state == "paused" else detail[:1200],
                    "updated_at": int(time.time()),
                }
            )
            self._persist_install_job(force=True)

    def _update_install_progress(
        self,
        phase: str,
        progress: float,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._check_install_control()
        with self._install_lock:
            details = details or {}
            now = time.time()
            completed = int(details.get("completed_bytes", self._install_job.get("completed_bytes", 0)) or 0)
            total = int(details.get("total_bytes", self._install_job.get("total_bytes", 0)) or 0)
            speed = float(self._install_job.get("speed_bytes_per_second", 0.0) or 0.0)
            if phase == "download":
                previous = self._download_sample
                if previous and completed >= previous[0] and now > previous[1]:
                    instant = (completed - previous[0]) / (now - previous[1])
                    if instant > 0:
                        speed = instant if previous[2] <= 0 else previous[2] * 0.72 + instant * 0.28
                self._download_sample = (completed, now, speed)
            eta = (total - completed) / speed if total > completed and speed > 0 else None
            self._install_job.update(
                {
                    "state": "installing",
                    "phase": phase,
                    "progress": max(0.0, min(1.0, float(progress))),
                    "message": message,
                    "current_file": str(details.get("current_file", self._install_job.get("current_file", ""))),
                    "completed_bytes": completed,
                    "total_bytes": total,
                    "speed_bytes_per_second": round(speed, 2),
                    "eta_seconds": round(eta) if eta is not None else None,
                    "updated_at": int(now),
                }
            )
            self._persist_install_job()

    @property
    def install_job_path(self) -> Path:
        return self.root / "install-job.json"

    def _load_install_job(self) -> None:
        try:
            payload = json.loads(self.install_job_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("job_id") != self.install_job_id:
            return
        if payload.get("state") in {"queued", "installing"}:
            payload.update(
                {
                    "state": "interrupted",
                    "message": "上次安装因应用退出而中断，可继续安装并断点续传；已下载内容会自动复用",
                    "error": "",
                    "updated_at": int(time.time()),
                }
            )
        self._install_job.update(payload)
        self._persist_install_job(force=True)

    def _persist_install_job(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_install_persist_at < 0.75:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        # Each installer thread gets its own temporary file.  A shared
        # ``install-job.json.tmp`` can be replaced or removed by a concurrent
        # cancellation/status writer on Windows, producing a noisy background
        # PermissionError even though the install itself is still recoverable.
        temporary = self.install_job_path.with_name(
            f"{self.install_job_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(json.dumps(self._install_job, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.install_job_path)
            self._last_install_persist_at = now
        finally:
            temporary.unlink(missing_ok=True)

    def ensure_installed(self) -> Path | None:
        """Return an active component without starting a download.

        ``install()`` is deliberately the only operation that may fetch the
        optional runtime. Importing a library, opening a conversation, or
        checking an index must never become an unannounced installation.
        """

        executable = self.executable()
        if executable is not None:
            status = self.status()
            if status.get("update_required"):
                raise RuntimeError(
                    f"{self.display_name} {status.get('version') or '旧版本'} 需要更新到 "
                    f"{status.get('minimum_version') or self.minimum_component_version} 或更高版本；"
                    "已下载的模型文件会继续复用。"
                    "请前往设置 → 本地模型更新本地运行组件。"
                )
            return executable
        build = current_build_info()
        if not bool(build.get("frozen")):
            if self.status().get("installed"):
                return None
            raise RuntimeError(f"{self.display_name}不可用。请前往设置 → 本地模型查看组件状态。")
        if build.get("package_profile") == "full":
            return None
        if not self.manifest_url:
            raise RuntimeError(f"{self.display_name}尚未安装，当前发行渠道也没有提供组件下载清单。")
        raise RuntimeError(f"{self.display_name}尚未安装。请前往设置 → 本地模型确认安装。")

    def install(self, progress_callback=None) -> dict[str, Any]:
        """Download, verify, and atomically activate one runtime version."""

        if not self.manifest_urls:
            raise RuntimeError(f"当前发行渠道没有配置{self.display_name}下载清单。")
        with self._process_install_lock():
            return self._install_component(progress_callback=progress_callback)

    def install_local(self, paths: list[str] | tuple[str, ...], progress_callback=None) -> dict[str, Any]:
        """Install a verified runtime package selected from local disk."""

        normalized = self._normalize_manual_paths(paths)
        with self._process_install_lock():
            component, package = self._manual_component_package(normalized)
            return self._install_component_package(
                component,
                package,
                progress_callback=progress_callback,
                manifest_source="manual",
            )

    def _install_component(self, progress_callback=None) -> dict[str, Any]:
        self._emit_progress(progress_callback, "manifest", 0.02, "读取官方组件清单（自动切换下载通道）")
        failures: list[dict[str, str]] = []
        for index, source in enumerate(self.manifest_urls):
            component: dict[str, Any] | None = None
            try:
                manifest = self._read_json(source)
                component = self._component_record(manifest)
                package = component.get("windows", {})
                if not isinstance(package, dict):
                    raise RuntimeError(f"{self.display_name}清单缺少 Windows 包")
                self._validate_component_package(
                    package,
                    allow_file=urlparse(source).scheme.casefold() == "file",
                    require_metadata=bool(self.release_public_key) and urlparse(source).scheme.casefold() != "file",
                )
                result = self._install_component_package(
                    component,
                    dict(package),
                    progress_callback=progress_callback,
                    manifest_source=source,
                    manifest_failures=failures,
                )
            except (LocalRuntimeInstallPaused, LocalRuntimeInstallCancelled):
                raise
            except Exception as error:
                if component is not None:
                    self._quarantine_failed_inactive_version(component)
                failures.append(
                    {
                        "url": source,
                        "label": self._manifest_channel_label(source, index),
                        "error": str(error)[:500],
                    }
                )
                self._last_manifest_source = ""
                self._last_manifest_failures = list(failures)
                continue
            self._last_manifest_source = source
            self._last_manifest_failures = list(failures)
            return result
        detail = "；".join(f"{item['label']}：{item['error']}" for item in failures)
        raise RuntimeError(f"{self.display_name}的所有自动下载通道均不可用。{detail}")

    def _quarantine_failed_inactive_version(self, component: dict[str, Any]) -> None:
        """Keep a failed candidate from poisoning a same-version fallback source."""

        version = str(component.get("version", "")).strip()
        if not version:
            return
        try:
            active = json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            active = {}
        if isinstance(active, dict) and str(active.get("version", "")).strip() == version:
            return
        target = self.root / "versions" / self._safe_segment(version)
        if not target.exists():
            return
        quarantine = target.with_name(f"{target.name}.invalid-{uuid4().hex[:8]}")
        try:
            target.replace(quarantine)
        except OSError:
            return

    def _install_component_package(
        self,
        component: dict[str, Any],
        package: dict[str, Any],
        *,
        progress_callback=None,
        manifest_source: str = "",
        manifest_failures: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        version = str(component.get("version", "")).strip()
        if not version or not isinstance(package, dict):
            raise RuntimeError(f"{self.display_name}清单缺少版本或 Windows 包。")
        expected = str(package.get("sha256", "")).strip().lower()
        if not self._is_sha256(expected):
            raise RuntimeError(f"{self.display_name}清单缺少有效的 SHA256。")

        self.root.mkdir(parents=True, exist_ok=True)
        downloads = self.root / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        archive = downloads / f"{self._safe_segment(self.component_id)}-{self._safe_segment(version)}.zip"
        if not archive.is_file() or self._sha256(archive) != expected:
            archive.unlink(missing_ok=True)
            self._acquire_package(package, archive, expected, progress_callback=progress_callback)
        else:
            self._emit_progress(progress_callback, "download", 0.78, "复用已校验的组件归档")

        versions = self.root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        target = versions / self._safe_segment(version)
        self._emit_progress(progress_callback, "extract", 0.86, "解压本地运行能力")
        staging = Path(tempfile.mkdtemp(prefix="install-", dir=self.root)).resolve()
        previous_target: Path | None = None
        failed_target: Path | None = None
        candidate_activated = False
        try:
            matches = self._extract_verified(archive, staging)
            if len(matches) != 1:
                raise RuntimeError(f"{self.display_name}包必须只包含一个 {self.executable_name}。")
            payload = matches[0].parent
            diagnostics = package.get("diagnostics")
            self._emit_progress(progress_callback, "diagnostics", 0.95, "验证运行依赖")
            diagnostic_result = self._run_diagnostics(payload / self.executable_name, diagnostics)

            # A same-version reinstall is a repair operation. Validate the
            # staged tree first, then swap it in so a corrupt dependency is
            # restored without sacrificing the currently active runtime.
            if target.exists():
                previous_target = versions / f"{target.name}.previous-{uuid4().hex[:8]}"
                target.replace(previous_target)
            shutil.move(str(payload), str(target))
            candidate_activated = True
            active_payload = {
                "id": self.component_id,
                "version": version,
                "executable": str((target / self.executable_name).relative_to(self.root)),
                "sha256": expected,
                "diagnostics": diagnostic_result,
            }
            temporary = self.active_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(active_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.active_path)
        except Exception:
            if candidate_activated and target.exists():
                failed_target = versions / f"{target.name}.failed-{uuid4().hex[:8]}"
                target.replace(failed_target)
            if previous_target is not None and previous_target.exists():
                previous_target.replace(target)
            raise
        finally:
            self._remove_private_tree(staging)
            if failed_target is not None:
                shutil.rmtree(self._extended_windows_path(failed_target), ignore_errors=True)
        if previous_target is not None:
            shutil.rmtree(self._extended_windows_path(previous_target), ignore_errors=True)
        self._emit_progress(progress_callback, "ready", 1.0, f"{self.display_name}已就绪")
        result = {**self.status(), "installed": True, "source": manifest_source or "manual"}
        if manifest_failures:
            result["manifest_failures"] = list(manifest_failures)
        return result

    def check_manifest_channels(self) -> dict[str, Any]:
        """Probe configured manifest channels only when the user asks to."""

        checked_at = int(time.time())
        channels: list[dict[str, Any]] = []
        for index, url in enumerate(self.manifest_urls):
            record: dict[str, Any] = {
                "url": url,
                "label": self._manifest_channel_label(url, index),
                "reachable": False,
                "valid": False,
                "version": "",
                "error": "",
            }
            try:
                manifest = self._read_json(url)
                component = self._component_record(manifest)
                record.update(
                    {
                        "reachable": True,
                        "valid": True,
                        "version": str(component.get("version", "")).strip(),
                    }
                )
            except Exception as error:
                record["error"] = str(error)[:500]
            channels.append(record)
        available = next((item for item in channels if item["valid"]), None)
        return {
            "checked_at": checked_at,
            "channels": channels,
            "available": bool(available),
            "preferred_url": str(available.get("url", "")) if available else "",
            "manual_fallback": {
                "available": True,
                "release_url": self.default_release_url,
                "accepted_files": [".zip", ".json", ".zip.part001 … .zip.part999"],
            },
        }

    @staticmethod
    def _unique_urls(values: list[object]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for value in values:
            url = str(value or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    def _manifest_channel_label(self, url: str, index: int) -> str:
        if url == self.default_manifest_url:
            return "官方固定清单"
        if "stable.json" in url:
            return "应用更新清单备用"
        return "首选清单" if index == 0 else f"备用清单 {index}"

    @classmethod
    def _normalize_manual_paths(cls, paths: list[str] | tuple[str, ...]) -> list[Path]:
        if isinstance(paths, (str, bytes)) or not isinstance(paths, (list, tuple)):
            raise ValueError("请选择本地运行组件 ZIP，以及可选的 JSON 清单或分片文件。")
        normalized: list[Path] = []
        seen: set[str] = set()
        for raw in paths:
            candidate = Path(str(raw or "")).expanduser()
            if not str(candidate).strip():
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as error:
                raise ValueError(f"找不到本地文件：{candidate}") from error
            if not resolved.is_file():
                raise ValueError(f"本地运行组件必须是文件：{resolved}")
            key = str(resolved).casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(resolved)
        if not normalized:
            raise ValueError("请至少选择一个本地运行组件文件。")
        if len(normalized) > 128:
            raise ValueError("一次选择的本地组件文件过多。")
        return normalized

    def _manual_component_package(self, paths: list[Path]) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest_path = next((path for path in paths if path.suffix.casefold() == ".json"), None)
        archive_path = next((path for path in paths if path.suffix.casefold() == ".zip"), None)
        if manifest_path is None and archive_path is None:
            raise ValueError("没有找到 ZIP 组件包。请从官方发布页下载 ZIP，或同时选择 ZIP 分片。")

        if manifest_path is not None:
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError("所选本地组件清单不是有效的 UTF-8 JSON 文件。") from error
            if not isinstance(payload, dict):
                raise ValueError("本地组件清单必须是 JSON 对象。")
            component = self._component_record(payload)
            package = component.get("windows", {})
            if not isinstance(package, dict):
                raise ValueError("本地组件清单缺少 Windows 包。")
            package = dict(package)
            parts = package.get("parts")
            if isinstance(parts, list) and parts:
                selected = {path.name.casefold(): path for path in paths if path != manifest_path}
                local_parts: list[dict[str, Any]] = []
                missing: list[str] = []
                for record in parts:
                    if not isinstance(record, dict):
                        raise ValueError("本地组件分片清单格式无效。")
                    raw_url = str(record.get("url", "")).strip()
                    name = Path(unquote(urlparse(raw_url).path)).name.casefold()
                    match = selected.get(name)
                    if match is None:
                        match = next((path for key, path in selected.items() if name and (name in key or key in name)), None)
                    if match is None:
                        missing.append(name or "未命名分片")
                    else:
                        local_parts.append({**record, "url": match.as_uri()})
                if missing:
                    raise ValueError(f"本地组件还缺少分片：{', '.join(missing[:8])}")
                package["parts"] = local_parts
            else:
                if archive_path is None:
                    raise ValueError("所选清单对应单个 ZIP 包；请同时选择该 ZIP 文件。")
                package["url"] = archive_path.as_uri()
            return dict(component), package

        # A ZIP without a manifest is still accepted as a deliberate local
        # fallback, but its hash is calculated locally and the archive must
        # pass the same structural and diagnostic checks before activation.
        assert archive_path is not None
        digest = self._sha256(archive_path)
        return {
            "id": self.component_id,
            "version": f"manual-{int(archive_path.stat().st_mtime)}",
            "windows": {
                "url": archive_path.as_uri(),
                "size": archive_path.stat().st_size,
                "sha256": digest,
            },
        }, {
            "url": archive_path.as_uri(),
            "size": archive_path.stat().st_size,
            "sha256": digest,
        }

    def _acquire_package(
        self,
        package: dict[str, Any],
        archive: Path,
        expected: str,
        *,
        progress_callback=None,
    ) -> None:
        parts = package.get("parts")
        if isinstance(parts, list) and parts:
            total_bytes = self._validated_multipart_size(package)
            assert total_bytes is not None
            downloaded: list[Path] = []
            completed_bytes = 0
            for index, record in enumerate(parts, start=1):
                if not isinstance(record, dict):
                    raise RuntimeError("本地 AI 组件分片清单格式无效。")
                url = str(record.get("url", "")).strip()
                checksum = str(record.get("sha256", "")).strip().lower()
                size = int(record.get("size", 0) or 0)
                if not url or not self._is_sha256(checksum) or size < 1:
                    raise RuntimeError("本地 AI 组件分片缺少有效的地址、大小或 SHA256。")
                destination = archive.with_suffix(f".zip.part{index:03d}")
                if (
                    not destination.is_file()
                    or destination.stat().st_size != size
                    or self._sha256(destination) != checksum
                ):
                    destination.unlink(missing_ok=True)
                    for attempt in range(2):
                        try:
                            self._download(
                                url,
                                destination,
                                expected_size=size,
                                progress_callback=lambda received, _total, before=completed_bytes: self._emit_download_progress(
                                    progress_callback,
                                    before + received,
                                    total_bytes,
                                    current_file=destination.name,
                                ),
                            )
                        except RuntimeError as error:
                            raise RuntimeError(f"本地 AI 组件第 {index} 个分片校验失败：{error}") from error
                        if destination.stat().st_size == size and self._sha256(destination) == checksum:
                            break
                        destination.unlink(missing_ok=True)
                        if attempt == 1:
                            raise RuntimeError(f"本地 AI 组件第 {index} 个分片校验失败，已重试后取消安装。")
                if destination.stat().st_size != size or self._sha256(destination) != checksum:
                    destination.unlink(missing_ok=True)
                    raise RuntimeError(f"本地 AI 组件第 {index} 个分片校验失败，已取消安装。")
                downloaded.append(destination)
                completed_bytes += size
                self._emit_download_progress(progress_callback, completed_bytes, total_bytes)
            temporary = archive.with_suffix(".zip.assembling")
            temporary.unlink(missing_ok=True)
            with temporary.open("wb") as output:
                for part in downloaded:
                    with part.open("rb") as source:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
            temporary.replace(archive)
        else:
            url = str(package.get("url", "")).strip()
            if not url:
                raise RuntimeError("本地 AI 组件清单缺少有效的下载地址。")
            expected_size = int(package.get("size", 0) or 0)
            for attempt in range(2):
                self._download(
                    url,
                    archive,
                    expected_size=expected_size or None,
                    progress_callback=lambda received, total: self._emit_download_progress(
                        progress_callback,
                        received,
                        expected_size or total,
                        current_file=archive.name,
                    ),
                )
                size_ok = not expected_size or archive.stat().st_size == expected_size
                hash_ok = self._sha256(archive) == expected
                if size_ok and hash_ok:
                    break
                archive.unlink(missing_ok=True)
                if attempt == 1:
                    raise RuntimeError("本地 AI 组件校验失败，已重试后取消安装。")

        self._emit_progress(progress_callback, "verify", 0.82, "校验组件完整性")
        expected_size = int(package.get("size", 0) or 0)
        if expected_size and archive.stat().st_size != expected_size:
            archive.unlink(missing_ok=True)
            raise RuntimeError("本地 AI 组件大小校验失败，已取消安装。")
        if self._sha256(archive) != expected:
            archive.unlink(missing_ok=True)
            raise RuntimeError("本地 AI 组件校验失败，已取消安装。")

    def _run_diagnostics(self, executable: Path, diagnostics: object) -> dict[str, Any]:
        if not isinstance(diagnostics, dict):
            return {"status": "not_requested"}
        raw_args = diagnostics.get("args")
        if not isinstance(raw_args, list) or not raw_args or not all(isinstance(arg, str) for arg in raw_args):
            raise RuntimeError("本地 AI 组件自检配置无效。")
        timeout = max(10, min(int(diagnostics.get("timeout_seconds", 180) or 180), 600))
        report = executable.parent / "diagnostics.json"
        args = [arg.replace("{output}", str(report)) for arg in raw_args]
        try:
            completed = subprocess.run(
                [str(executable), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"本地 AI 组件自检未能完成：{exc}") from exc
        try:
            result = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"本地 AI 组件没有生成有效的自检报告（退出码 {completed.returncode}）。"
            ) from exc
        if completed.returncode != 0 or not isinstance(result, dict) or not result.get("ok"):
            detail = str(result.get("error", "") if isinstance(result, dict) else "").strip()
            raise RuntimeError(f"本地 AI 组件自检失败。{detail}".rstrip())
        return result

    def _component_record(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if str(manifest.get("id", "")) == self.component_id:
            return manifest
        components = manifest.get("components", {})
        if isinstance(components, dict) and isinstance(components.get(self.component_id), dict):
            return dict(components[self.component_id])
        raise RuntimeError(f"发行清单中没有 {self.component_id} 组件。")

    @staticmethod
    def _validate_url(url: str, *, allow_file: bool = False) -> None:
        validate_release_url(url, allow_file=allow_file)

    def _read_json(self, url: str) -> dict[str, Any]:
        allow_file = urlparse(url).scheme.casefold() == "file"
        self._validate_url(url, allow_file=allow_file)
        req = Request(url, headers={"Accept": "application/json", "User-Agent": "ScanSci local runtime"})
        with open_release_url(req, timeout=12, allow_file=allow_file, opener=urlopen) as response:
            payload = response.read(_MAX_MANIFEST_BYTES + 1)
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise ValueError("组件清单过大。")
        # GitHub's JSON assets may include a UTF-8 BOM (notably the stable
        # update manifest used as a compatibility fallback).  Accept both
        # forms so a valid channel is not reported as unavailable.
        result = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError("组件清单必须是 JSON 对象。")
        schema_version = result.get("schema_version")
        signature_required = bool(self.release_public_key) and not allow_file
        if schema_version is not None and schema_version != 1:
            raise ValueError("组件清单 schema_version 必须为 1。")
        if signature_required and schema_version != 1:
            raise ValueError("已签名组件清单 schema_version 必须为 1。")
        verify_manifest_signature(result, self.release_public_key, required=signature_required)
        return result

    def _validate_component_package(
        self,
        package: dict[str, Any],
        *,
        allow_file: bool,
        require_metadata: bool,
    ) -> None:
        records = package.get("parts")
        downloads = records if isinstance(records, list) and records else [package]
        if not all(isinstance(record, dict) for record in downloads):
            raise ValueError("组件清单分片格式无效。")
        self._validated_multipart_size(package)
        for record in downloads:
            url = str(record.get("url", "")).strip()
            if not url:
                raise ValueError("组件清单缺少下载地址。")
            self._validate_url(url, allow_file=allow_file)
            if require_metadata:
                checksum = str(record.get("sha256", "")).strip().casefold()
                size = record.get("size")
                if (
                    not self._is_sha256(checksum)
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or size <= 0
                    or size > _MAX_RUNTIME_DOWNLOAD_BYTES
                ):
                    raise ValueError("已签名组件清单缺少有效的下载大小或 SHA256。")
        declared_size = package.get("size")
        if require_metadata and (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size <= 0
            or declared_size > _MAX_RUNTIME_DOWNLOAD_BYTES
        ):
            raise ValueError("已签名组件清单缺少有效的组件总大小。")

    @staticmethod
    def _validated_multipart_size(package: dict[str, Any]) -> int | None:
        records = package.get("parts")
        if not isinstance(records, list) or not records:
            return None
        if len(records) > 128 or not all(isinstance(record, dict) for record in records):
            raise ValueError("本地 AI 组件分片数量或格式异常。")
        declared_size = package.get("size")
        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size <= 0
            or declared_size > _MAX_RUNTIME_DOWNLOAD_BYTES
        ):
            raise ValueError("组件清单缺少有效的组件总大小。")
        sizes = [record.get("size") for record in records]
        if any(
            isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size > _MAX_RUNTIME_DOWNLOAD_BYTES
            for size in sizes
        ):
            raise ValueError("组件清单分片大小无效。")
        total_size = sum(sizes)
        if total_size != declared_size or total_size > _MAX_RUNTIME_DOWNLOAD_BYTES:
            raise ValueError("组件分片总大小与清单声明不一致或超出允许范围。")
        return total_size

    @classmethod
    def _download(
        cls,
        url: str,
        destination: Path,
        progress_callback=None,
        *,
        expected_size: int | None = None,
    ) -> None:
        """Download a runtime archive with bounded retries and safe resume.

        A connection can disappear after hundreds of megabytes. The partial
        file is retained, but it is only promoted after the advertised size is
        complete. If a server ignores ``Range``, the partial is replaced
        instead of being accidentally concatenated with a second full response.
        """

        allow_file = urlparse(url).scheme.casefold() == "file"
        cls._validate_url(url, allow_file=allow_file)
        if expected_size is not None and (
            isinstance(expected_size, bool)
            or expected_size <= 0
            or expected_size > _MAX_RUNTIME_DOWNLOAD_BYTES
        ):
            raise ValueError("组件下载大小超出允许范围。")
        temporary = Path(f"{destination}.download")
        last_error: Exception | None = None
        for delay in _RUNTIME_RETRY_DELAYS_SECONDS:
            if delay:
                time.sleep(delay)
            try:
                resumed = temporary.stat().st_size if temporary.is_file() else 0
                if expected_size and resumed == expected_size:
                    if progress_callback is not None:
                        progress_callback(expected_size, expected_size)
                    temporary.replace(destination)
                    return
                if expected_size and resumed > expected_size:
                    temporary.unlink(missing_ok=True)
                    resumed = 0
                headers = {"User-Agent": "ScanSci local runtime"}
                if resumed:
                    headers["Range"] = f"bytes={resumed}-"
                req = Request(url, headers=headers)
                with open_release_url(req, timeout=300, allow_file=allow_file, opener=urlopen) as response:
                    content_range = str(response.headers.get("Content-Range", "") or "")
                    accepted_range = resumed > 0 and content_range.lower().startswith(f"bytes {resumed}-")
                    if resumed and not accepted_range:
                        resumed = 0
                    content_type = str(response.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
                    if urlparse(url).scheme.lower() in {"http", "https"} and content_type in {
                        "text/html",
                        "application/json",
                        "application/xml",
                        "text/plain",
                    }:
                        raise RuntimeError(
                            f"组件下载源返回了 {content_type or '文本'} 错误页，而不是运行组件归档。"
                        )
                    remaining = int(response.headers.get("Content-Length", 0) or 0)
                    total = resumed + remaining if remaining else 0
                    if "/" in content_range:
                        advertised_total = content_range.rsplit("/", 1)[-1].strip()
                        if advertised_total.isdigit():
                            total = int(advertised_total)
                    if expected_size and total and total != expected_size and urlparse(url).scheme.lower() in {"http", "https"}:
                        raise _RetryableRuntimeDownloadError(
                            f"组件响应大小异常：清单为 {expected_size} 字节，服务器报告 {total} 字节。"
                        )
                    if expected_size:
                        total = expected_size
                    if total > _MAX_RUNTIME_DOWNLOAD_BYTES:
                        raise ValueError("组件下载大小超出允许范围。")
                    received = resumed
                    mode = "ab" if accepted_range else "wb"
                    with temporary.open(mode) as output:
                        if progress_callback is not None:
                            progress_callback(received, total)
                        while chunk := response.read(1024 * 1024):
                            output.write(chunk)
                            received += len(chunk)
                            if received > _MAX_RUNTIME_DOWNLOAD_BYTES:
                                raise ValueError("组件下载大小超出允许范围。")
                            if progress_callback is not None:
                                progress_callback(received, total)
                if expected_size and received != expected_size:
                    detail = f"组件下载未完成：预期 {expected_size} 字节，当前只有 {received} 字节。"
                    if urlparse(url).scheme.lower() in {"http", "https"}:
                        raise _RetryableRuntimeDownloadError(detail)
                    raise RuntimeError(detail)
                temporary.replace(destination)
                return
            except (OSError, TimeoutError, _RetryableRuntimeDownloadError) as error:
                last_error = error
                continue
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        detail = str(last_error or "未知网络错误").strip()
        raise RuntimeError(f"本地 AI 组件下载失败；已自动重试并保留断点：{detail}") from last_error

    @staticmethod
    def _is_sha256(value: str) -> bool:
        return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)

    @staticmethod
    def _emit_progress(
        callback,
        phase: str,
        progress: float,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if callback is not None:
            try:
                callback(phase, progress, message, details or {})
            except TypeError:
                callback(phase, progress, message)

    @classmethod
    def _emit_download_progress(
        cls,
        callback,
        received: int,
        total: int,
        *,
        current_file: str = "",
    ) -> None:
        ratio = min(1.0, received / total) if total > 0 else 0.0
        percent = max(0, min(100, round(ratio * 100)))
        cls._emit_progress(
            callback,
            "download",
            0.05 + 0.73 * ratio,
            f"下载官方组件 {percent}%",
            {
                "current_file": current_file,
                "completed_bytes": max(0, int(received)),
                "total_bytes": max(0, int(total)),
            },
        )

    def _extract_verified(self, archive_path: Path, destination: Path) -> list[Path]:
        executable_paths: list[Path] = []
        destination = destination.resolve()
        try:
            with ZipFile(archive_path) as archive:
                infos = archive.infolist()
                if len(infos) > _MAX_RUNTIME_ARCHIVE_FILES:
                    raise RuntimeError("本地 AI 组件包文件数量过多。")
                if sum(info.file_size for info in infos) > _MAX_UNPACKED_RUNTIME_BYTES:
                    raise RuntimeError("本地 AI 组件解压后体积过大。")
                seen: set[str] = set()
                for info in infos:
                    name = _safe_component_path(info.filename)
                    normal = name.as_posix().casefold()
                    if normal in seen:
                        raise RuntimeError("本地 AI 组件包包含重复路径。")
                    seen.add(normal)
                    unix_mode = info.external_attr >> 16
                    if (unix_mode and stat.S_ISLNK(unix_mode)) or info.flag_bits & 0x1:
                        raise RuntimeError("本地 AI 组件包不能包含符号链接或加密文件。")
                    # ``destination`` is a fresh private staging directory and
                    # every member already passed the lexical path guard above.
                    # Resolving a not-yet-created deep path breaks before the
                    # extended Windows path prefix can take effect.
                    target = destination.joinpath(*name.parts)
                    if info.is_dir():
                        os.makedirs(self._extended_windows_path(target), exist_ok=True)
                        continue
                    os.makedirs(self._extended_windows_path(target.parent), exist_ok=True)
                    with archive.open(info, "r") as source, open(self._extended_windows_path(target), "wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    if os.path.getsize(self._extended_windows_path(target)) != info.file_size:
                        raise RuntimeError("本地 AI 组件文件解压不完整。")
                    if name.name == self.executable_name:
                        executable_paths.append(target)
        except BadZipFile as exc:
            raise RuntimeError("本地 AI 组件包不是有效的 ZIP 文件。") from exc
        return executable_paths

    @staticmethod
    def _extended_windows_path(path: Path) -> str:
        resolved = str(path.resolve())
        if os.name != "nt" or resolved.startswith("\\\\?\\"):
            return resolved
        if resolved.startswith("\\\\"):
            return "\\\\?\\UNC\\" + resolved.lstrip("\\")
        return "\\\\?\\" + resolved

    @contextmanager
    def _process_install_lock(self):
        """Prevent two ScanSci processes from mutating one runtime root."""

        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / "install.lock"
        stream = lock_path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise RuntimeError("另一个 ScanSci 窗口正在安装本地运行组件，请等待当前安装完成。") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise RuntimeError("另一个 ScanSci 进程正在安装本地运行组件，请等待当前安装完成。") from exc
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def _remove_private_tree(self, path: Path) -> None:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeError("拒绝清理运行时目录之外的临时路径。") from exc
        if resolved.name.startswith("install-"):
            shutil.rmtree(self._extended_windows_path(resolved), ignore_errors=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_segment(value: str) -> str:
        clean = "".join(ch for ch in value if ch.isalnum() or ch in ".-_").strip(".-")
        clean = re.sub(r"\.{2,}", ".", clean)
        return clean or uuid4().hex


def _safe_component_path(value: str) -> PurePosixPath:
    raw = str(value or "").replace("\\", "/").rstrip("/")
    path = PurePosixPath(raw)
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if (
        not raw
        or "\x00" in raw
        or path.is_absolute()
        or any(
            part in {"", ".", ".."}
            or ":" in part
            or part.rstrip(" .") != part
            or part.split(".", 1)[0].casefold() in reserved
            for part in path.parts
        )
    ):
        raise RuntimeError("本地 AI 组件包包含不安全路径。")
    return path


_DEFAULT_RUNTIME_COMPONENT: LocalRuntimeComponent | None = None


def default_local_runtime_component() -> LocalRuntimeComponent:
    """Return the process-wide component manager used by chat and audio."""

    global _DEFAULT_RUNTIME_COMPONENT
    if _DEFAULT_RUNTIME_COMPONENT is None:
        _DEFAULT_RUNTIME_COMPONENT = LocalRuntimeComponent()
    return _DEFAULT_RUNTIME_COMPONENT
