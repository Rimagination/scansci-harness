"""Adapters that turn the existing ScanSci suite into research-agent tools.

The adapters are intentionally narrow: they expose status and explicit user
actions, keep downloads inside the current workspace, and never persist API
credentials outside the operating-system credential manager.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Callable
from urllib import error, parse, request
from uuid import uuid4

from .academic_search import (
    FederatedAcademicSearch,
    _apply_topic_quality_gate,
    _normalized_author_name,
    build_academic_provider,
    search_academic_papers,
    search_openalex_author_works,
)
from .agent_capabilities import capability_catalog
from .app_settings import _is_strata_loopback_url, get_provider_api_key, load_settings
from .jev_client import DEFAULT_ENDPOINT as JEV_DEFAULT_ENDPOINT, DEFAULT_MODEL as JEV_DEFAULT_MODEL, JEVError, ask_jev
from .retrieval_intent import compile_retrieval_intent
from .slides_templates import get_slide_template, resolve_slide_template_dir


_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_PATTERN = re.compile(r"^(?:arxiv:)?(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?$", re.IGNORECASE)
_SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")
_SCANSCI_SUITE_ROOT = Path(os.getenv("SCANSCI_SUITE_ROOT", r"D:\scansci"))
_JOURNAL_API = "https://www.scansci.com/api/journals/search"
_CITATION_API = "https://www.scansci.com/api/citation/analyze"
# The public frontend currently serves its SPA shell for /api/* requests.
# Use the upstream Space API that the maintained Cloudflare proxy targets.
_PAPER_ATLAS_API = "https://rimagination-paper-atlas.hf.space/api"
_CLI_ENCODING_FAILURE_HINTS = (
    "unicodeencodeerror",
    "'gbk' codec can't encode",
    "'cp936' codec can't encode",
)
_DOWNLOAD_COMMIT_LOCK = threading.Lock()
_PUBLIC_SCORE_ALPHA = 0.1
_PUBLIC_DEFAULT_SCORE = 0.5
_PUBLIC_DEFAULT_LATENCY_MS = 5000.0
_PUBLIC_RACE_WORKERS = max(2, min(6, int(os.getenv("SCANSCI_PUBLIC_RACE_WORKERS", "4") or "4")))


def _scansci_pdf_environment(
    *,
    fallback_errors: bool = False,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment that makes the Python CLI emit UTF-8 on Windows."""

    environment = os.environ.copy()
    environment.update(overrides or {})
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8:backslashreplace" if fallback_errors else "utf-8"
    return environment


def _is_cli_encoding_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    detail = f"{completed.stderr or ''}\n{completed.stdout or ''}".casefold()
    return any(hint in detail for hint in _CLI_ENCODING_FAILURE_HINTS)


def _run_scansci_pdf(
    command: list[str],
    *,
    timeout: float,
    retry_encoding_failure: bool = False,
    env_overrides: dict[str, str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``scansci-pdf`` with UTF-8 output and one encoding-safe retry."""

    run_options: dict[str, Any] = {
        "check": False,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "shell": False,
    }
    try:
        completed = _run_download_cli(
            command,
            cancel_check=cancel_check,
            env=_scansci_pdf_environment(overrides=env_overrides),
            **run_options,
        )
    except subprocess.TimeoutExpired:
        # Keep timeout failures inside the same structured tool contract as a
        # non-zero CLI exit.  Letting TimeoutExpired escape used to become an
        # opaque worker crash with no safe retry path in the UI.
        return subprocess.CompletedProcess(
            command,
            124,
            stdout="",
            stderr=f"scansci-pdf timed out after {timeout:g}s",
        )
    if retry_encoding_failure and completed.returncode != 0 and _is_cli_encoding_failure(completed):
        try:
            completed = _run_download_cli(
                command,
                cancel_check=cancel_check,
                env=_scansci_pdf_environment(fallback_errors=True, overrides=env_overrides),
                **run_options,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                command,
                124,
                stdout="",
                stderr=f"scansci-pdf timed out after {timeout:g}s",
            )
    return completed


def _start_windows_download(command: list[str], **options: Any):
    """Own descendants even if the CLI launcher exits before its children.

    Start suspended, assign a Windows Job, then resume: no child can escape
    between process creation and assignment. Only this invocation is affected.
    """
    import ctypes
    from ctypes import wintypes

    class ThreadEntry(ctypes.Structure):
        _fields_ = [(name, kind) for name, kind in (
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD), ("process_id", wintypes.DWORD),
            ("priority", wintypes.LONG), ("delta", wintypes.LONG),
            ("flags", wintypes.DWORD),
        )]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
        "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
        "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())

    def stop_tree():
        try:
            if not kernel.TerminateJobObject(job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(job)

    process = None
    try:
        process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW | 0x4, **options)  # CREATE_SUSPENDED
        if not kernel.AssignProcessToJobObject(job, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.process_id == process.pid:
                    thread = kernel.OpenThread(0x2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        kernel.CloseHandle(thread)
                    return process, stop_tree
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Cannot find the download process's suspended thread")
        finally:
            kernel.CloseHandle(snapshot)
    except BaseException:
        try:
            stop_tree()
        finally:
            if process is not None:
                process.kill()
                process.communicate(timeout=2)
        raise


def _run_download_cli(command: list[str], *, cancel_check: Callable[[], bool] | None, **options: Any) -> subprocess.CompletedProcess[str]:
    if cancel_check is None:
        return subprocess.run(command, **options)
    if cancel_check():
        raise _BatchCancelled("文献任务已停止")
    timeout = float(options.pop("timeout"))
    options.pop("check", None)
    options.pop("capture_output", None)
    options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if os.name == "nt":
        process, stop_tree = _start_windows_download(command, **options)
    else:
        process = subprocess.Popen(command, start_new_session=True, **options)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel_check():
                raise _BatchCancelled("文献任务已停止")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        # The parent may already have exited while a child still holds stdout.
        if os.name == "nt":
            stop_tree()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            # Closing a stream with an active Windows reader can itself block.
            # Its daemon reader will close it when the terminated tree exits.
            pass


def _parse_scansci_pdf_json(output: str) -> dict[str, Any]:
    """Read the JSON object from ``scansci-pdf`` even if it logs a preamble.

    The CLI prints a short author-resolution line before its JSON payload for
    author searches.  Treating stdout as a JSON document made valid searches
    fail after the tool had already returned usable records.
    """

    text = str(output or "").lstrip("\ufeff")
    decoder = json.JSONDecoder()
    starts = [0, *[index for index, character in enumerate(text) if character in "{["]]
    visited: set[int] = set()
    for start in starts:
        if start in visited:
            continue
        visited.add(start)
        candidate = text[start:].lstrip()
        if not candidate or candidate[0] not in "{[":
            continue
        try:
            payload, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        # Some CLI versions emit JSON progress events before the final JSON
        # document.  A progress object is valid JSON but is not a search
        # response; only accept the object that carries the result collection.
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            return payload
    raise ValueError("scansci-pdf did not emit a JSON object")


def _parse_scansci_pdf_payload(output: str) -> dict[str, Any]:
    """Parse one JSON object from a CLI stream that may contain log lines."""

    text = str(output or "").lstrip("\ufeff")
    decoder = json.JSONDecoder()
    starts = [0, *[index for index, character in enumerate(text) if character == "{"]]
    for start in starts:
        try:
            payload, _ = decoder.raw_decode(text[start:].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("scansci-pdf did not emit a JSON payload")


def _run_scansci_pdf_fetch(
    executable: str,
    identifier: str,
    *,
    output_dir: Path,
    timeout: float,
    env_overrides: dict[str, str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Use the latest structured institutional cascade as a safe fallback."""

    command = [
        executable,
        "fetch",
        identifier,
        "--output",
        str(output_dir),
        "--format",
        "json",
    ]
    completed = _run_scansci_pdf(
        command,
        timeout=timeout,
        retry_encoding_failure=True,
        env_overrides=env_overrides,
        cancel_check=cancel_check,
    )
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    try:
        payload = _parse_scansci_pdf_payload(stdout)
    except ValueError:
        return {
            "ok": False,
            "provider": "scansci-pdf-institutional",
            "status": "unavailable",
            "message": (stderr or stdout or "structured fetch returned no JSON")[-800:],
        }

    paper = payload.get("paper") if isinstance(payload.get("paper"), dict) else {}
    raw_file = paper.get("pdf_path") or payload.get("file") or payload.get("pdf_path") or ""
    if raw_file:
        file_path = Path(str(raw_file)).expanduser()
        if not file_path.is_absolute():
            file_path = output_dir / file_path
        file_path = file_path.resolve()
        if _pdf_file(file_path):
            return {
                "ok": True,
                "identifier": identifier,
                "output_dir": str(output_dir),
                "files": [str(file_path)],
                "source": str(paper.get("source") or "institutional_cascade"),
                "provider": "scansci-pdf-institutional",
                "status": str(payload.get("status") or "success"),
                "quality": str(payload.get("quality") or ""),
                "reason": str(payload.get("reason") or ""),
                "next_action": payload.get("next_action"),
                "attempts": payload.get("attempts") or [],
            }

    return {
        "ok": False,
        "identifier": identifier,
        "output_dir": str(output_dir),
        "provider": "scansci-pdf-institutional",
        "status": str(payload.get("status") or "unavailable"),
        "quality": str(payload.get("quality") or "none"),
        "reason": str(payload.get("reason") or "no_pdf"),
        "next_action": payload.get("next_action"),
        "attempts": payload.get("attempts") or [],
    }


SCANSCI_APPS: tuple[dict[str, str], ...] = (
    {
        "id": "journal-scout",
        "name": "Journal Scout",
        "description": "期刊分区、JCR、影响因子、CiteScore 与预警信息。",
        "url": "https://journal.scansci.com",
        "local_path": "journal-scout",
    },
    {
        "id": "citation-lab",
        "name": "Citation Lab",
        "description": "参考文献元数据、语境相关性与断言支持度核查。",
        "url": "https://citation.scansci.com",
        "local_path": "citation-lab",
    },
    {
        "id": "paper-atlas",
        "name": "Paper Atlas",
        "description": "论文引用关系、相似论文与主题网络。",
        "url": "https://paperatlas.scansci.com",
        "local_path": "paper-atlas",
    },
    {
        "id": "easyslides",
        "name": "EasySlides",
        "description": "从论文和研究资料生成可编辑学术 PPTX 的本地工具链。",
        "url": "https://easyslides.scansci.com",
        "local_path": "easyslides",
    },
    {
        "id": "paper-deck",
        "name": "PaperDeck",
        "description": "基于研究兴趣的每日论文发现。",
        "url": "https://paperdeck.scansci.com",
        "local_path": "paper-deck",
    },
)


def capability_snapshot(*, workspace: str | Path, evidence_db: str | Path) -> dict[str, Any]:
    """Return a truthful capability map for the desktop UI."""

    pdf_cli = shutil.which("scansci-pdf")
    apps = []
    for app in SCANSCI_APPS:
        local_path = _SCANSCI_SUITE_ROOT / app["local_path"]
        apps.append({**app, "local_available": local_path.is_dir(), "local_path": str(local_path)})
    settings = load_settings(workspace)
    catalog = capability_catalog(
        workspace=workspace,
        evidence_db=evidence_db,
        mcp_servers=list(settings.get("mcp_servers", []) or []),
        plugins=list(settings.get("plugins", []) or []),
    )
    return {
        "workspace": str(Path(workspace).resolve()),
        "evidence_store_ready": Path(evidence_db).is_file(),
        "capability_catalog": catalog,
        "download_directory": str(_download_directory(workspace)),
        "presentation_directory": str(_presentation_directory(workspace)),
        "apps": apps,
        "tools": [
            {
                "id": "evidence-trace",
                "name": "证据追溯",
                "status": "ready" if Path(evidence_db).is_file() else "needs-data",
                "description": "句子级证据、原文锚点与引用核验。",
            },
            {
                "id": "paper-download",
                "name": "论文获取",
                "status": "ready" if pdf_cli else "unavailable",
                "description": "通过本地 scansci-pdf 并行获取 DOI 或 arXiv 文献。",
            },
            {
                "id": "journal-scout",
                "name": "期刊分区",
                "status": "ready",
                "description": "调用 ScanSci Journal Scout 在线检索接口。",
            },
            {
                "id": "citation-lab",
                "name": "引文真伪核查",
                "status": "ready",
                "description": "调用 ScanSci Citation Lab 与 Crossref/OpenAlex 双源核验。",
            },
            {
                "id": "academic-search",
                "name": "多源学术搜索",
                "status": "ready",
                "description": "并行检索 OpenAlex、Semantic Scholar、Crossref、PubMed、Europe PMC 与 arXiv，去重后用本地模型重排。",
            },
            {
                "id": "deep-research",
                "name": "学术深度研究",
                "status": "ready",
                "description": "从多轮检索、合法全文获取到句级证据写作与引用核验；题录线索不会冒充证据。",
            },
            {
                "id": "paper-atlas",
                "name": "Paper Atlas",
                "status": "external",
                "description": "保留原有论文图谱入口，供兼容旧任务和关系探索使用。",
            },
            {
                "id": "ppt-studio",
                "name": "PPT Studio",
                "status": "ready" if (_SCANSCI_SUITE_ROOT / "easyslides" / "scripts" / "project_manager.py").is_file() else "unavailable",
                "description": "创建 EasySlides 项目、导入当前来源并生成可追溯大纲。",
            },
        ],
    }


def _jev_api_key(workspace: str | Path, provider_id: str) -> str:
    """Resolve the first configured JEV key without returning it to callers."""

    stored = get_provider_api_key(workspace, provider_id)
    return next((value.strip() for value in stored.split(",") if value.strip()), "")


def _test_jev_connection(
    *,
    workspace: str | Path,
    provider: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    api_key = _jev_api_key(workspace, str(provider.get("id", "jev")))
    endpoint = str(provider.get("base_url") or JEV_DEFAULT_ENDPOINT).strip()
    try:
        payload = ask_jev(
            api_key=api_key,
            endpoint=endpoint,
            model=JEV_DEFAULT_MODEL,
            state="ScanSci connection check",
            questions={
                "connection_check": {
                    "type": "noul",
                    "instructions": "Return a typed result confirming that this connection check is reachable.",
                }
            },
            timeout=timeout,
        )
    except JEVError as exc:
        raise RuntimeError(str(exc)) from exc
    answer = payload.get("answers", {}).get("connection_check")
    if not isinstance(answer, dict) or not str(answer.get("type", "")).strip():
        raise RuntimeError("JEV API 返回了无法识别的结构化判断结果")
    return {
        "ok": True,
        "status": "connected",
        "checked_scope": "jev_evaluation",
        "generation_verified": False,
        "message": "JEV 结构化判断接口可访问；最小连接检测已完成。",
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "model": str(payload.get("model") or JEV_DEFAULT_MODEL),
    }


def test_provider_connection(
    *,
    workspace: str | Path,
    provider: dict[str, Any],
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Test a configured provider without returning credentials or response bodies."""

    started = time.perf_counter()
    kind = str(provider.get("kind", ""))
    if kind == "local":
        return {"ok": True, "status": "ready", "latency_ms": 0, "model_count": len(provider.get("models", []))}
    if str(provider.get("id", "")).strip().lower() == "jev" or str(provider.get("api_surface", "")).strip().lower() == "jev_systemone":
        return _test_jev_connection(workspace=workspace, provider=provider, timeout=timeout)
    base_url = str(provider.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("Base URL 尚未配置")
    api_key = get_provider_api_key(workspace, str(provider.get("id", "")))
    api_key = next((value.strip() for value in api_key.split(",") if value.strip()), "")
    if not api_key:
        raise ValueError("请先保存 API Key")
    endpoint = f"{base_url}/models"
    headers = {"Accept": "application/json", "User-Agent": "ScanSci-Desktop/0.1"}
    if kind == "anthropic-compatible":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = _request_json(endpoint, headers=headers, timeout=timeout)
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list) or payload.get("error"):
        raise RuntimeError("模型列表接口未返回有效列表，连接检测未通过；请检查 API 地址及服务商状态。")
    has_image = any("image" in model.get("capabilities", []) for model in provider.get("models", []) if isinstance(model, dict))
    return {
        "ok": True,
        "status": "connected",
        "checked_scope": "model_catalog",
        "generation_verified": False,
        "message": "模型列表接口可访问；生图权限尚未验证，未发起生图。" if has_image else "模型列表接口可访问；模型调用权限尚未验证。",
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "model_count": len(models),
    }


def fetch_provider_models(
    *,
    workspace: str | Path,
    provider: dict[str, Any],
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Read a provider's public model catalog without exposing its API key.

    The Model Services screen uses the OpenAI-compatible ``/models`` contract
    whenever a provider exposes it.  A provider that does not offer that
    contract can still be configured by adding model IDs manually.
    """

    kind = str(provider.get("kind", ""))
    if kind == "local":
        models = [dict(item) for item in provider.get("models", []) if isinstance(item, dict)]
        return {"provider": str(provider.get("name", "")), "models": models, "count": len(models)}
    if str(provider.get("id", "")).strip().lower() == "jev" or str(provider.get("api_surface", "")).strip().lower() == "jev_systemone":
        models = [dict(item) for item in provider.get("models", []) if isinstance(item, dict)]
        return {"provider": str(provider.get("name", "JEV")), "models": models, "count": len(models), "source": "fixed"}
    base_url = str(provider.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("请先填写 API 地址")
    api_key = get_provider_api_key(workspace, str(provider.get("id", "")))
    if not api_key:
        raise ValueError("请先保存 API Key")
    headers = {"Accept": "application/json", "User-Agent": "ScanSci-Desktop/0.1"}
    if kind == "anthropic-compatible":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = _request_json(f"{base_url}/models", headers=headers, timeout=timeout)
    raw_models = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(raw_models, list) and isinstance(payload, dict):
        raw_models = payload.get("models", [])
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_models[:128] if isinstance(raw_models, list) else []:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        name = str(item.get("display_name") or item.get("name") or identifier).strip() or identifier
        context_window = str(item.get("context_window") or item.get("context_length") or "").strip()
        group = str(item.get("owned_by") or item.get("organization") or item.get("provider") or "默认模型").strip() or "默认模型"
        models.append({"id": identifier, "name": name, "group": group, "context_window": context_window})
    return {"provider": str(provider.get("name", "")), "models": models, "count": len(models)}


def test_local_model_connection(local_model: dict[str, Any], *, timeout: float = 4.0) -> dict[str, Any]:
    runtime = str(local_model.get("runtime", "")).strip().lower()
    if runtime == "builtin":
        return {"ok": True, "status": "ready", "latency_ms": 0, "models": [str(local_model.get("model_id", ""))]}
    base_url = str(local_model.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("本地服务地址尚未配置")
    if runtime == "strata":
        base_url = _strata_api_base(base_url)
    started = time.perf_counter()
    try:
        if runtime == "strata":
            health = _request_json(f"{base_url[:-3]}/health", timeout=timeout)
            if not isinstance(health, dict) or "loaded" not in health:
                return {"ok": False, "status": "unavailable", "models": [], "error": "该地址没有返回 Strata 健康信息；请确认端口。"}
            if health.get("loaded") is not True:
                return {"ok": False, "status": "loading", "models": [], "error": "Strata 已响应，但模型尚未加载完成。"}
        payload = _request_json(f"{base_url}/models", timeout=timeout)
    except RuntimeError as exc:
        if runtime != "strata":
            raise
        reason = str(exc).casefold()
        if "timed out" in reason or "timeout" in reason:
            message = "Strata 本机连接超时；请确认服务已启动、模型已加载且端口正确。"
        elif "refused" in reason or "拒绝" in reason:
            message = "Strata 本机端口拒绝连接；请先启动 Strata。"
        elif "http 401" in reason or "http 403" in reason:
            message = "Strata 要求 API Key；当前本机连接尚未配置该密钥。"
        else:
            message = f"Strata 本机连接失败：{str(exc).replace('远程服务', '本机服务')[:300]}"
        return {"ok": False, "status": "unavailable", "models": [], "error": message}
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    model_ids = [str(item.get("id", "")).strip() for item in rows[:20] if isinstance(item, dict) and str(item.get("id", "")).strip()] if isinstance(rows, list) else []
    if runtime == "strata" and not model_ids:
        return {"ok": False, "status": "unavailable", "models": [], "error": "Strata 已加载，但没有返回可用模型。"}
    return {
        "ok": True, "status": "connected", "latency_ms": round((time.perf_counter() - started) * 1000),
        "models": model_ids, **({"max_context": health.get("max_context")} if runtime == "strata" else {}),
    }


def _strata_api_base(value: str) -> str:
    """Strata is a keyless local route; never send its probes to a remote host."""

    if not _is_strata_loopback_url(value):
        raise ValueError("Strata 地址须为本机 HTTP 地址，例如 http://127.0.0.1:8080/v1")
    return str(value).strip().rstrip("/")


def probe_strata_agent_model(base_url: str, model_id: str, *, timeout: float = 180.0) -> dict[str, Any]:
    """Exercise the streaming evidence and tool contracts before routing Pi."""

    base_url = _strata_api_base(base_url)
    model_id = str(model_id or "").strip()
    if not model_id:
        raise ValueError("请选择 Strata 返回的模型")
    evidence_request = {
        "model": model_id, "stream": True, "reasoning_effort": "none", "temperature": 0, "max_tokens": 128,
        "messages": [
            {"role": "system", "content": "只根据给定证据作答，并保留证据编号。"},
            {"role": "user", "content": "证据 [E1]：样地 A 有 12 株乔木。样地 A 有多少株乔木？请简短回答并引用 [E1]。"},
        ],
    }
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(evidence_request, ensure_ascii=False).encode("utf-8"),
        headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        method="POST",
    )
    chunks: list[str] = []
    complete = False
    try:
        with request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    complete = True
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Strata 流式响应格式无效") from exc
                if isinstance(event, dict) and event.get("error"):
                    raise RuntimeError("Strata 流式回答返回错误")
                choices = event.get("choices") if isinstance(event, dict) else None
                delta = choices[0].get("delta") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                delta = delta if isinstance(delta, dict) else {}
                chunks.append(str(delta.get("content") or ""))
                if len(chunks) > 2048:
                    raise RuntimeError("Strata 流式回答超过预检上限")
    except error.HTTPError as exc:
        raise RuntimeError(f"Strata 流式接口返回 HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"无法连接 Strata 流式接口：{exc.reason}") from exc
    if not complete:
        raise RuntimeError("Strata 流式回答未正常结束")
    answer = "".join(chunks)
    if not re.search(r"(?<!\d)12(?!\d)", answer) or "[E1]" not in answer:
        raise RuntimeError("Strata 已响应，但证据引用预检未通过")
    tool = _request_json(
        f"{base_url}/chat/completions",
        method="POST",
        data={
            "model": model_id, "stream": False, "reasoning_effort": "none", "max_tokens": 128,
            "messages": [
                {"role": "system", "content": "回答前必须先调用 lookup_evidence 工具。"},
                {"role": "user", "content": "请调用 lookup_evidence 查询编号 E1。"},
            ],
            "tools": [{"type": "function", "function": {
                "name": "lookup_evidence", "description": "按编号读取证据。",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            }}],
        },
        timeout=timeout,
    )
    choices = tool.get("choices") if isinstance(tool, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    calls = message.get("tool_calls") or [] if isinstance(message, dict) else []
    valid_call = False
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or function.get("name") != "lookup_evidence":
            continue
        try:
            arguments = json.loads(function.get("arguments") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(arguments, dict) and arguments.get("id") == "E1":
            valid_call = True
            break
    if not valid_call:
        raise RuntimeError("Strata 已响应，但工具调用预检未通过")
    return {"model_id": model_id, "stream_verified": True, "citation_verified": True, "tool_call_verified": True}


def search_journals(query: str, *, limit: int = 8, timeout: float = 15.0) -> dict[str, Any]:
    try:
        intent = compile_retrieval_intent(query, kind="journal")
    except ValueError as error:
        raise ValueError("请输入期刊名、ISSN 或 CN 号") from error
    clean = str(intent["subject"])
    endpoint = f"{_JOURNAL_API}?{parse.urlencode({'q': clean, 'limit': max(1, min(20, int(limit)))})}"
    payload = _request_json(
        endpoint,
        headers={
            "Origin": "https://journal.scansci.com",
            "Referer": "https://journal.scansci.com/",
        },
        timeout=timeout,
    )
    rows = payload.get("items", []) if isinstance(payload, dict) else []
    items = []
    for row in rows[:20] if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "id": row.get("id"),
                "title": str(row.get("title", "")),
                "issn": str(row.get("issn", "")),
                "eissn": str(row.get("eissn", "")),
                "impact_factor": row.get("if_2023"),
                "impact_factor_year": row.get("if_year"),
                "jcr_quartile": str(row.get("jcr_quartile", "")),
                "cas_partition": str(row.get("cas_2025", "")),
                "top": bool(row.get("is_top")),
                "warning": bool(row.get("warning_latest") or row.get("xuankan_warning")),
                "tags": list(row.get("tags", []) or [])[:12],
                "detail_url": f"https://journal.scansci.com/journal.html?id={parse.quote(str(row.get('id', '')))}&q={parse.quote(clean)}",
            }
        )
    return {"query": clean, "search_intent": intent, "items": items, "source": "ScanSci Journal Scout"}


def analyze_references(text: str, *, mode: str = "full", timeout: float = 35.0) -> dict[str, Any]:
    clean = str(text or "").strip()
    if not clean:
        raise ValueError("请粘贴正文与参考文献，或仅粘贴参考文献")
    if len(clean) > 200_000:
        raise ValueError("单次核查文本不能超过 200,000 字符")
    normalized_mode = "references" if mode == "references" else "full"
    return _request_json(
        _CITATION_API,
        method="POST",
        data={"text": clean, "mode": normalized_mode},
        timeout=timeout,
    )


def search_paper_atlas(query: str, *, timeout: float = 30.0) -> dict[str, Any]:
    try:
        intent = compile_retrieval_intent(query, kind="paper_atlas")
    except ValueError as error:
        raise ValueError("请输入论文主题、标题、DOI 或 arXiv ID") from error
    clean = str(intent["subject"])
    external_url = "https://paperatlas.scansci.com/"
    try:
        payload = _request_json(
            f"{_PAPER_ATLAS_API}/search?{parse.urlencode({'q': clean})}",
            headers={"Referer": external_url},
            timeout=timeout,
        )
    except RuntimeError as exc:
        return {
            "query": clean,
            "search_intent": intent,
            "items": [],
            "source": "ScanSci Paper Atlas",
            "status": "external",
            "external_url": external_url,
            "message": f"图谱计算服务暂不可用，可在 Paper Atlas 网页继续搜索。{exc}",
        }
    items = payload if isinstance(payload, list) else []
    return {
        "query": clean,
        "search_intent": intent,
        "items": [dict(item) for item in items[:10] if isinstance(item, dict)],
        "source": "ScanSci Paper Atlas",
        "status": "ready",
        "external_url": external_url,
    }


def download_paper(
    identifier: str,
    *,
    workspace: str | Path,
    strategy: str = "oa_first",
    timeout: float = 180.0,
    env_overrides: dict[str, str] | None = None,
    metadata_is_oa: bool | None = None,
    _output_dir: str | Path | None = None,
    _source_health_dir: str | Path | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    _check_download_cancelled(cancel_check)
    clean = _normalize_download_identifier(identifier)
    if not (_DOI_PATTERN.fullmatch(clean) or _ARXIV_PATTERN.fullmatch(clean)):
        raise ValueError("请输入有效 DOI 或 arXiv ID")
    normalized_strategy = strategy if strategy in {"legal_only", "oa_first", "gray_oa"} else "oa_first"
    # Search metadata that says a work is closed-access does not justify
    # skipping it (metadata can lag), but it does justify not spending the
    # full per-item budget inside the multi-source CLI race before reporting
    # the predictable failure.
    cli_timeout = min(timeout, 45.0) if metadata_is_oa is False else timeout
    executable = shutil.which("scansci-pdf")
    output_dir = Path(_output_dir).resolve() if _output_dir else _download_directory(workspace)
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = _identifier_already_downloaded(clean, output_dir)
    if existing:
        return _finalize_download_result(
            {
                "ok": True,
                "identifier": clean,
                "strategy": normalized_strategy,
                "output_dir": str(output_dir),
                "files": existing,
                "source": "local_cache",
                "cached": True,
            },
            clean,
            workspace=workspace,
            timeout=timeout,
            output_dir=output_dir,
            cancel_check=cancel_check,
        )

    # Prefer one canonical open-access location before invoking the CLI race.
    # This keeps ordinary OA downloads single-file and leaves the multi-source
    # cascade as a fallback for papers whose best public location is unavailable.
    if normalized_strategy in {"oa_first", "gray_oa"} and not env_overrides:
        try:
            preferred = _download_from_public_archives(
                clean,
                workspace=workspace,
                strategy=normalized_strategy,
                timeout=timeout,
                output_dir=output_dir,
                source_health_dir=_source_health_dir,
                cancel_check=cancel_check,
            )
        except RuntimeError:
            preferred = None
        if preferred:
            return _finalize_download_result(
                preferred,
                clean,
                workspace=workspace,
                timeout=timeout,
                output_dir=output_dir,
                cancel_check=cancel_check,
            )

    # arXiv is itself the authoritative public archive.  Routing it through an
    # optional account-aware downloader can produce a misleading zero exit
    # status with only a login hint and no file.
    if _ARXIV_PATTERN.fullmatch(clean) or normalized_strategy == "gray_oa" or not executable:
        result = _download_from_public_archives(
            clean,
            workspace=workspace,
            strategy=normalized_strategy,
            timeout=timeout,
            output_dir=output_dir,
            source_health_dir=_source_health_dir,
            cancel_check=cancel_check,
        )
        return _finalize_download_result(
            result,
            clean,
            workspace=workspace,
            timeout=timeout,
            output_dir=output_dir,
            cancel_check=cancel_check,
        )
    before = {
        path.resolve(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in output_dir.glob("**/*")
        if path.is_file()
    }
    command = [executable, "get", clean, "--output", str(output_dir), "--strategy", normalized_strategy]
    completed = _run_scansci_pdf(command, timeout=cli_timeout, env_overrides=env_overrides, cancel_check=cancel_check)
    _check_download_cancelled(cancel_check)
    changed_pdfs: list[Path] = []
    for path in output_dir.glob("**/*"):
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            continue
        resolved = path.resolve()
        signature = (path.stat().st_size, path.stat().st_mtime_ns)
        if before.get(resolved) == signature:
            continue
        try:
            valid_pdf = path.read_bytes()[:4] == b"%PDF"
        except OSError:
            valid_pdf = False
        if valid_pdf:
            changed_pdfs.append(resolved)
    created = sorted(str(path) for path in changed_pdfs)
    if completed.returncode != 0 or not created:
        # The current scansci-pdf release exposes a structured institutional
        # cascade (OA, Elsevier, CARSI, publisher, browser, gateway). Try it
        # after the racing ``get`` command and accept it only when its JSON
        # points to a real PDF on disk.
        institutional = _run_scansci_pdf_fetch(
            executable,
            clean,
            output_dir=output_dir,
            timeout=cli_timeout,
            env_overrides=env_overrides,
            cancel_check=cancel_check,
        )
        if institutional and institutional.get("files"):
            return _finalize_download_result(
                institutional,
                clean,
                workspace=workspace,
                timeout=timeout,
                output_dir=output_dir,
                cancel_check=cancel_check,
            )

        # A few optional downloader versions report success while printing a
        # failure/login hint and creating no file.  Fall back to lawful public
        # archives and only report success after an actual PDF is present.
        try:
            result = _download_from_public_archives(
                clean,
                workspace=workspace,
                strategy=normalized_strategy,
                timeout=timeout,
                output_dir=output_dir,
                source_health_dir=_source_health_dir,
                cancel_check=cancel_check,
            )
            return _finalize_download_result(
                result,
                clean,
                workspace=workspace,
                timeout=timeout,
                output_dir=output_dir,
                cancel_check=cancel_check,
            )
        except RuntimeError as fallback_error:
            detail = (completed.stderr or completed.stdout or "下载器没有生成有效 PDF").strip()
            raise RuntimeError(f"{detail[-800:]}；公开存档回退失败：{fallback_error}") from fallback_error
    return _finalize_download_result({
        "ok": True,
        "identifier": clean,
        "strategy": normalized_strategy,
        "output_dir": str(output_dir),
        "files": created,
        "message": (completed.stdout or "下载完成").strip()[-1200:],
    }, clean, workspace=workspace, timeout=timeout, output_dir=output_dir, cancel_check=cancel_check)


def search_papers_for_download(
    query: str = "",
    *,
    author: str = "",
    search_plan: dict[str, Any] | None = None,
    limit: int = 20,
    sort: str = "relevance",
    year_from: int | None = None,
    year_to: int | None = None,
    timeout: float = 60.0,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Search ``scansci-pdf`` and return records that can enter download flow."""

    intent = compile_retrieval_intent(query, kind="paper_download") if str(query or "").strip() else None
    clean_query = str(intent["subject"]) if intent else ""
    clean_author = str(author or "").strip() or str((intent or {}).get("author", "")).strip()
    if intent and intent.get("author_only"):
        clean_query = ""
    search_query = clean_query
    search_intent = dict(intent or {})
    if clean_query and isinstance(search_plan, dict) and search_plan.get("query_variants"):
        variants = list(dict.fromkeys(
            str(value).strip()
            for value in list(search_plan.get("query_variants", []) or [])
            if str(value).strip()
        ))[:3]
        english_variants = [value for value in variants if re.search(r"[A-Za-z]{2,}", value)]
        if str(search_plan.get("planner", "")) != "deterministic":
            english_variants = english_variants[:1]
        proposed_search_query = str(search_plan.get("search_query", "")).strip()
        if proposed_search_query not in english_variants:
            proposed_search_query = next(
                iter(english_variants),
                "",
            )
        if proposed_search_query:
            search_query = proposed_search_query
        search_intent["query_variants"] = english_variants[:3]
        if re.search(r"[A-Za-z]{2,}", search_query):
            # Variants are complete scholarly queries, so they provide
            # alternative full-topic matches without trusting loose concepts.
            search_intent["required_terms"] = english_variants[:3] or [search_query]

    def preserve_query_trace(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is not None:
            result["query"] = clean_query
            result["search_query"] = search_query
            result["search_intent"] = search_intent
        return result

    if not clean_query and not clean_author:
        raise ValueError("请给出检索主题或作者姓名")
    normalized_limit = max(1, min(50, int(limit or 20)))
    normalized_sort = sort if sort in {"relevance", "cited_by_count", "publication_date"} else "relevance"
    inferred_limit = (intent or {}).get("limit")
    if inferred_limit is not None:
        normalized_limit = max(1, min(50, int(inferred_limit)))
    # Delivery count is not a recall budget: a title's usable record may rank
    # below metadata-only copies or unrelated provider hits.
    candidate_limit = max(6, normalized_limit)
    # High-citation author lists are dominated by paywalled classics, so an
    # author search pulls a wider pool before OA-first selection.
    author_pool_limit = min(50, max(normalized_limit * 4, normalized_limit + 8))
    if cancel_check is not None and cancel_check():
        raise _BatchCancelled("文献检索已停止")
    inferred_sort = str((intent or {}).get("sort", "")).strip()
    if inferred_sort and normalized_sort == "relevance":
        normalized_sort = inferred_sort
    deterministic_cross_language = (
        isinstance(search_plan, dict)
        and str(search_plan.get("planner", "")) == "deterministic"
        and len(search_intent.get("query_variants", [])) > 1
        and any("\u3400" <= character <= "\u9fff" for character in clean_query)
        and not clean_author
    )
    if deterministic_cross_language:
        # Known bilingual topics use the bounded public federation directly;
        # the keyword CLI can spend minutes on a broad/noisy query before it
        # reaches the same sources as a fallback.
        fallback = _public_download_search_fallback(
            search_query,
            author=clean_author,
            search_intent=search_intent,
            sort=normalized_sort,
            limit=normalized_limit,
            year_from=year_from,
            year_to=year_to,
            provider_timeout=8.0,
            cancel_check=cancel_check,
        )
        if fallback is not None and fallback.get("identifiers"):
            return preserve_query_trace(fallback) or fallback
        raise RuntimeError("公开学术检索未找到带 DOI 或 arXiv 标识的相关论文；请补充题名、作者或 DOI 后重试")
    # Author-only searches must be resolved as authors, not treated as an
    # unqualified keyword query by the download CLI.  The registry-first path
    # avoids homonyms and remains usable when a local scansci-pdf build is
    # outdated or returns an empty author result.
    if clean_author and not clean_query:
        oa_preferred = normalized_sort == "cited_by_count"
        author_pool = ThreadPoolExecutor(max_workers=1)
        try:
            author_future = author_pool.submit(
                search_openalex_author_works,
                clean_author,
                limit=author_pool_limit,
                sort=normalized_sort,
                year_from=year_from,
                year_to=year_to,
            )
            author_result = next(_completed_downloads([author_future], cancel_check, timeout=timeout)).result()
            resolved = _download_search_result(
                list(author_result.get("items") or []),
                query="",
                search_intent={},
                author=clean_author,
                sort=normalized_sort,
                limit=normalized_limit,
                source="openalex_author_works",
                year_from=year_from,
                year_to=year_to,
                author_verified=True,
                oa_preferred=oa_preferred,
            )
            resolved["author_resolution"] = dict(author_result.get("author_resolution") or {})
            if resolved["items"]:
                return preserve_query_trace(resolved) or resolved
        except _BatchCancelled:
            raise
        except Exception:
            # Keep the local CLI as a compatibility fallback for unusual
            # names or transient public API failures.  Its result is still
            # validated below before download begins.
            pass
        finally:
            author_pool.shutdown(wait=False, cancel_futures=True)
    executable = shutil.which("scansci-pdf")
    if not executable:
        fallback = _public_download_search_fallback(
            search_query,
            author=clean_author,
            search_intent=search_intent,
            sort=normalized_sort,
            limit=normalized_limit,
            year_from=year_from,
            year_to=year_to,
            cancel_check=cancel_check,
        )
        if fallback is not None:
            return preserve_query_trace(fallback) or fallback
        raise RuntimeError("未找到 scansci-pdf，且公开学术检索未返回可下载文献")
    cli_candidate_limit = candidate_limit
    cli_oa_preferred = False
    if clean_author and normalized_sort == "cited_by_count":
        cli_candidate_limit = min(50, max(candidate_limit, author_pool_limit))
        cli_oa_preferred = True
    command = [executable, "search", search_query, "--limit", str(cli_candidate_limit), "--sort", normalized_sort]
    if clean_author:
        command.extend(["--author", clean_author])
    if year_from is not None:
        command.extend(["--year-from", str(int(year_from))])
    if year_to is not None:
        command.extend(["--year-to", str(int(year_to))])
    completed = _run_scansci_pdf(command, timeout=timeout, retry_encoding_failure=True, cancel_check=cancel_check)
    if completed.returncode != 0:
        fallback = _public_download_search_fallback(
            search_query, author=clean_author, search_intent=search_intent,
            sort=normalized_sort, limit=normalized_limit, year_from=year_from, year_to=year_to,
            cancel_check=cancel_check,
        )
        if fallback is not None and fallback["identifiers"]:
            return preserve_query_trace(fallback) or fallback
        detail = (completed.stderr or completed.stdout or "检索失败").strip()
        raise RuntimeError(detail[-1200:])
    try:
        payload = _parse_scansci_pdf_json(completed.stdout or "")
    except ValueError as exc:
        fallback = _public_download_search_fallback(
            search_query,
            author=clean_author,
            search_intent=search_intent,
            sort=normalized_sort,
            limit=normalized_limit,
            year_from=year_from,
            year_to=year_to,
            cancel_check=cancel_check,
        )
        if fallback is not None:
            return preserve_query_trace(fallback) or fallback
        raise RuntimeError("scansci-pdf 未返回有效的 JSON 检索结果") from exc
    rows = payload.get("results", []) if isinstance(payload, dict) else []
    result = _download_search_result(
        list(rows or []),
        query=clean_query,
        search_intent=search_intent,
        author=clean_author,
        sort=normalized_sort,
        limit=normalized_limit,
        source="scansci-pdf",
        year_from=year_from,
        year_to=year_to,
        oa_preferred=cli_oa_preferred,
    )
    if result["identifiers"]:
        return preserve_query_trace(result) or result
    fallback = _public_download_search_fallback(
        search_query,
        author=clean_author,
        search_intent=search_intent,
        sort=normalized_sort,
        limit=normalized_limit,
        year_from=year_from,
        year_to=year_to,
        cancel_check=cancel_check,
    )
    return preserve_query_trace(fallback if fallback is not None else result) or result


def _download_search_result(
    rows: list[Any],
    *,
    query: str,
    search_intent: dict[str, Any],
    author: str,
    sort: str,
    limit: int,
    source: str,
    year_from: int | None = None,
    year_to: int | None = None,
    author_verified: bool = False,
    oa_preferred: bool = False,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    identifiers: list[str] = []
    seen: set[str] = set()
    all_rows = [dict(row) for row in list(rows or []) if isinstance(row, dict)]
    article_rows: list[dict[str, Any]] = []
    non_article_dropped: list[dict[str, Any]] = []
    for row in all_rows:
        if _is_non_article_record(row):
            non_article_dropped.append(
                {
                    "title": str(row.get("title", "")),
                    "doi": str(row.get("doi", "")),
                    "reason": "non_article_record",
                }
            )
            continue
        article_rows.append(row)
    candidates, quality_gate = _apply_topic_quality_gate(
        article_rows,
        list(search_intent.get("required_terms") or []),
    )
    if oa_preferred:
        # Author high-citation lists are dominated by paywalled classics.  A
        # stable sort keeps citation order inside each group while letting
        # openly accessible works enter the download flow first, so the
        # requested count can actually deliver PDFs.
        candidates = sorted(candidates, key=lambda row: 0 if _row_is_open_access(row) else 1)
    for raw in candidates:
        # Every provider/fallback must retain the user's explicit constraints.
        # Resolved OpenAlex works already carry an author-identity filter.
        if author and not author_verified and not any(
            _normalized_author_name(value) == _normalized_author_name(author)
            for value in (raw.get("authors") or [])
        ):
            continue
        if year_from is not None or year_to is not None:
            try:
                year = int(raw.get("year"))
            except (ValueError, TypeError):
                continue
            if (year_from is not None and year < year_from) or (year_to is not None and year > year_to):
                continue
        doi = _normalize_download_identifier(str(raw.get("doi") or ""))
        arxiv_id = _normalize_download_identifier(str(raw.get("arxiv_id") or raw.get("arxiv") or ""))
        if not arxiv_id:
            for url in (raw.get("url"), raw.get("oa_url")):
                value = _normalize_download_identifier(str(url or ""))
                if _ARXIV_PATTERN.fullmatch(value):
                    arxiv_id = value
                    break
        identifier = arxiv_id if _ARXIV_PATTERN.fullmatch(arxiv_id) else doi if _DOI_PATTERN.fullmatch(doi) else ""
        item = {
            "title": str(raw.get("title") or "未命名论文"),
            "identifier": identifier,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "authors": [str(value) for value in list(raw.get("authors") or [])],
            "year": raw.get("year"),
            "cited_by_count": int(raw.get("cited_by_count", raw.get("citation_count", 0)) or 0),
            "is_oa": bool(raw.get("is_oa") or raw.get("oa_url")),
            "oa_url": str(raw.get("oa_url") or ""),
            "url": str(raw.get("url") or ""),
            "source": str(raw.get("source") or source),
        }
        folded = _identifier_key(identifier)
        if identifier and folded not in seen:
            seen.add(folded)
            items.append(item)
            identifiers.append(identifier)
            if len(identifiers) >= limit:
                break
    return {
        "query": query,
        "search_intent": search_intent,
        "author": author,
        "sort": sort,
        "limit": limit,
        "items": items,
        "identifiers": identifiers,
        "total": len(items),
        "downloadable": len(identifiers),
        "source": source,
        "quality_gate": quality_gate,
        "non_article_dropped": {
            "count": len(non_article_dropped),
            "preview": non_article_dropped[:5],
        },
    }


_COMPONENT_DOI_SEGMENT_RE = re.compile(
    r"(?:^|\.)(?:(?:g|t|f|s|p|r|e)\d{2,4}|tno|tn\d+|app\d*|eapp|supp?\d*|supplementary|"
    r"metajpg|jpe?g|png|gif|pdf|epub|xml|html|video\d*|audio\d*|poster|slides?|chart|dataset)$",
    re.IGNORECASE,
)
_PEER_REVIEW_DOI_RE = re.compile(r"/(?:v\d+(?:/|$)|review|response|decision|rating)", re.IGNORECASE)
# DOI component records are numbered against a parent article: eLife appends
# “.016” to “10.7554/elife.33423”, while a real article suffix never ends in a
# 1–3 digit segment directly after a long numeric one (PLOS/eLife article IDs
# and “science.277.5330.1300” all keep 4+ digits in the final segment).
_COMPONENT_NUMERIC_DOI_RE = re.compile(r"\.(\d{3,})\.(\d{1,3})$")
_NON_ARTICLE_TYPES = frozenset(
    {"component", "peer-review", "reference-entry", "report-component", "abstract", "dataset"}
)
_NON_ARTICLE_TITLE_PREFIXES = (
    "appendix",
    "supplementary",
    "supplement",
    "table ",
    "figure ",
    "review for ",
    "review of ",
    "author's response",
    "authors response",
    "author response",
    "decision letter",
    "erratum",
    "corrigendum",
    "back matter",
    "front matter",
    "cover ",
    "meeting abstract",
    "conference abstract",
)


def _is_non_article_record(row: dict[str, Any]) -> bool:
    """Reject appendix, peer-review, and figure-component records.

    Keyword searches over Crossref legitimately return appendix tables,
    reviewer reports, and figure components whose titles phrase-match any
    topic.  They have no article PDF to download, so admitting them into the
    download flow only burns a per-item timeout before failing.
    """

    doi = str(row.get("doi") or "").strip().lower()
    if doi:
        suffix = doi.split("/", 1)[-1]
        if _PEER_REVIEW_DOI_RE.search(suffix):
            return True
        segments = suffix.split(".")
        if len(segments) > 1 and _COMPONENT_DOI_SEGMENT_RE.search(segments[-1]):
            return True
        if _COMPONENT_NUMERIC_DOI_RE.search(suffix):
            return True
    for value in row.get("publication_types") or []:
        if str(value or "").strip().lower() in _NON_ARTICLE_TYPES:
            return True
    title = " ".join(str(row.get("title") or "").split()).strip().lower()
    if any(title.startswith(prefix) for prefix in _NON_ARTICLE_TITLE_PREFIXES):
        return True
    return False


def _row_is_open_access(row: dict[str, Any]) -> bool:
    return bool(row.get("is_oa") or row.get("oa_url") or row.get("arxiv_id") or row.get("arxiv"))


def _public_download_search_fallback(
    query: str,
    *,
    author: str,
    search_intent: dict[str, Any],
    sort: str,
    limit: int,
    year_from: int | None,
    year_to: int | None = None,
    provider_timeout: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Use the existing public federation when scansci-pdf cannot supply IDs."""

    clean_query = str(query or "").strip()
    # An unresolved author name must not silently become a loose text query:
    # that would return homonyms and papers merely mentioning the person.
    if not clean_query:
        return None
    try:
        search_kwargs = {
            "query_variants": list(search_intent.get("query_variants") or []) or None,
            "required_terms": list(search_intent.get("required_terms") or []),
            "limit": max(12, limit),
            "per_source": max(6, min(20, limit)),
            "year_from": year_from,
            "cancel_requested": cancel_check,
        }
        if provider_timeout is None:
            public_result = search_academic_papers(
                clean_query,
                provider_names=("openalex", "crossref", "arxiv"),
                **search_kwargs,
            )
        else:
            public_result = FederatedAcademicSearch(
                providers=[
                    build_academic_provider(name, timeout=provider_timeout)
                    for name in ("openalex", "crossref", "arxiv")
                ],
            ).search(clean_query, **search_kwargs)
    except InterruptedError:
        raise _BatchCancelled("文献检索已停止") from None
    except Exception:
        return None
    fallback = _download_search_result(
        list(public_result.get("items") or []),
        query=clean_query,
        search_intent=search_intent,
        author=author,
        sort=sort,
        limit=limit,
        source="public_academic_fallback",
        year_from=year_from,
        year_to=year_to,
    )
    if not fallback["items"]:
        return None
    fallback["provider_errors"] = dict(public_result.get("provider_errors") or {})
    fallback["fallback_reason"] = "download_cli_unavailable_or_unusable"
    return fallback


class _BatchCancelled(Exception):
    """Raised by :func:`download_papers` when ``cancel_check`` requests a stop.

    The caller (the research-agent worker) catches this and translates it into
    a cooperative run cancellation, mirroring ``_RunCancelled``.
    """


def _check_download_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise _BatchCancelled("文献任务已停止")


def _completed_downloads(futures, cancel_check, *, timeout: float | None = None):
    """Poll even when every provider is blocked; as_completed alone cannot."""
    pending = set(futures)
    deadline = time.monotonic() + timeout if timeout is not None else None
    while pending:
        _check_download_cancelled(cancel_check)
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("public source race timed out")
        completed, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
        yield from completed


# Lightweight IP protection for batch downloads. Downloading dozens of papers
# from one IP at full speed triggers rate-limiting and bans on most sources.
# These defaults keep the batch polite: a randomized gap between items spreads
# requests over time, and the rate-limit detector retries transient 429/503
# responses with exponential backoff instead of recording a hard failure.
_BATCH_DELAY_MIN = float(os.getenv("SCANSCI_BATCH_DELAY_MIN", "3") or "3")
_BATCH_DELAY_MAX = float(os.getenv("SCANSCI_BATCH_DELAY_MAX", "8") or "8")
_BATCH_MAX_RETRIES = int(os.getenv("SCANSCI_BATCH_MAX_RETRIES", "2") or "2")
_BATCH_WORKERS = int(os.getenv("SCANSCI_BATCH_WORKERS", "4") or "4")

# Substrings that, when present in a download error, suggest the source pushed
# back on rate/frequency rather than the paper genuinely being unavailable.
# A retry after backoff is worthwhile in exactly these cases.
_RATE_LIMIT_HINTS = (
    "429", "503", "rate limit", "rate-limit", "too many requests",
    "temporarily", "retry", "throttle", "blocked", "cloudflare",
    "captcha", "challenge", "frequent",
)


def _looks_rate_limited(error: Exception) -> bool:
    message = f"{type(error).__name__} {error}".lower()
    return any(hint in message for hint in _RATE_LIMIT_HINTS)


def _normalize_download_identifier(value: str) -> str:
    """Normalize DOI/arXiv inputs before deduplication and cache lookup."""

    clean = str(value or "").strip()
    clean = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^doi:\s*", "", clean, flags=re.IGNORECASE)
    if re.match(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", clean, re.IGNORECASE):
        clean = re.sub(r"^/(?:abs|pdf)/", "", parse.urlparse(clean).path, flags=re.IGNORECASE)
        clean = re.sub(r"\.pdf$", "", clean, flags=re.IGNORECASE)
    if _ARXIV_PATTERN.fullmatch(clean):
        clean = re.sub(r"^arxiv:", "", clean, flags=re.IGNORECASE)
    return clean.strip()


def _identifier_key(value: str) -> str:
    """Return a case-insensitive key, folding arXiv's DOI alias to its ID."""

    clean = _normalize_download_identifier(value)
    arxiv_doi = re.fullmatch(r"10\.48550/arxiv\.(.+)", clean, flags=re.IGNORECASE)
    if arxiv_doi:
        clean = arxiv_doi.group(1)
    return clean.casefold()


def _scansci_pdf_safe_filename(identifier: str) -> str:
    """Match scansci-pdf's ``safe_filename`` fallback exactly."""

    return re.sub(r"[^A-Za-z0-9._-]+", "_", identifier).strip("_") or "paper"


def _scansci_pdf_filename(metadata: dict[str, Any], max_len: int = 80) -> str:
    """Match scansci-pdf's ``LastNameYear_TitleWord`` naming rule."""

    year = ""
    for key in ("year", "published-print", "published-online", "created"):
        value = metadata.get(key)
        if isinstance(value, str) and value.isdigit():
            year = value
            break
        if isinstance(value, dict):
            parts = value.get("date-parts", [[]])
            if parts and parts[0]:
                year = str(parts[0][0])
                break
        if isinstance(value, (int, float)):
            year = str(int(value))
            break

    authors = metadata.get("author", metadata.get("authors", []))
    first_author = ""
    if isinstance(authors, list) and authors:
        author = authors[0]
        if isinstance(author, dict):
            first_author = str(author.get("family") or author.get("given") or author.get("name") or "")
        elif isinstance(author, str):
            first_author = author.split(",", 1)[0].strip().split()[-1] if author.strip() else ""

    titles = metadata.get("title", metadata.get("titles", []))
    title = titles[0] if isinstance(titles, list) and titles else titles
    title_word = ""
    if isinstance(title, str):
        title = re.sub(r"<[^>]+>", "", title)
        stop_words = {"a", "an", "the", "on", "in", "of", "for", "to", "and", "with", "from"}
        for word in re.findall(r"[A-Za-z]+", title):
            if word.lower() not in stop_words and len(word) > 2:
                title_word = word.capitalize()
                break

    first_author = re.sub(r"[^A-Za-z]", "", first_author)[:20]
    year = re.sub(r"[^0-9]", "", year)[:4]
    title_word = re.sub(r"[^A-Za-z]", "", title_word)[:20]
    parts = [part for part in (first_author, year, title_word) if part]
    if not parts:
        return ""
    name = f"{parts[0]}{parts[1]}_{parts[2]}" if len(parts) >= 3 else "".join(parts[:2])
    return name[:max_len]


def _crossref_filename_metadata(identifier: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch the small Crossref metadata subset needed for canonical filenames."""

    clean = _normalize_download_identifier(identifier)
    if _ARXIV_PATTERN.fullmatch(clean):
        try:
            with request.urlopen(
                request.Request(
                    f"https://export.arxiv.org/api/query?id_list={parse.quote(clean, safe='.')}",
                    headers={"User-Agent": "ScanSci-Desktop/0.2"},
                ),
                timeout=timeout,
            ) as response:
                import xml.etree.ElementTree as ET

                root = ET.fromstring(response.read())
                atom = "{http://www.w3.org/2005/Atom}"
                entry = root.find(f"{atom}entry")
                title = " ".join((entry.findtext(f"{atom}title", default="") if entry is not None else "").split())
                published = (entry.findtext(f"{atom}published", default="") if entry is not None else "")[:4]
                authors = []
                if entry is not None:
                    for author in list(entry.findall(f"{atom}author"))[:1]:
                        name = " ".join((author.findtext(f"{atom}name", default="") or "").split())
                        if name:
                            authors.append({"family": name.split()[-1]})
                if title:
                    return {"title": [title], "year": published, "author": authors}
        except Exception:
            pass
        return {}
    if not _DOI_PATTERN.fullmatch(clean):
        return {}
    try:
        payload = _request_json(
            f"https://api.crossref.org/works/{parse.quote(clean, safe='')}",
            timeout=timeout,
        )
    except Exception:
        return {}
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    return dict(message) if isinstance(message, dict) else {}


def _pdf_file(path: Path) -> bool:
    try:
        return path.is_file() and path.read_bytes()[:4] == b"%PDF"
    except OSError:
        return False


def _public_source_scores_path(download_dir: Path) -> Path:
    """Return the local, non-secret source-health file for public downloads."""

    return download_dir / ".source_scores.json"


def _load_public_source_scores(download_dir: Path) -> dict[str, dict[str, Any]]:
    path = _public_source_scores_path(download_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(source): dict(entry)
        for source, entry in payload.items()
        if isinstance(entry, dict)
    }


def _write_public_source_scores(download_dir: Path, scores: dict[str, dict[str, Any]]) -> None:
    path = _public_source_scores_path(download_dir)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(scores, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _record_public_source_result(
    download_dir: Path,
    source: str,
    *,
    success: bool,
    latency_ms: float,
    error_type: str = "",
) -> None:
    """Persist a small EMA for public sources, mirroring scansci-pdf scoring."""

    source = str(source or "unknown").strip() or "unknown"
    with _DOWNLOAD_COMMIT_LOCK:
        scores = _load_public_source_scores(download_dir)
        entry = scores.get(
            source,
            {
                "success_ema": _PUBLIC_DEFAULT_SCORE,
                "latency_ema": _PUBLIC_DEFAULT_LATENCY_MS,
                "attempts": 0,
                "last_error": "",
                "last_update": 0,
            },
        )
        value = 1.0 if success else 0.0
        entry["success_ema"] = (
            _PUBLIC_SCORE_ALPHA * value
            + (1.0 - _PUBLIC_SCORE_ALPHA) * float(entry.get("success_ema", _PUBLIC_DEFAULT_SCORE))
        )
        if success and latency_ms > 0:
            entry["latency_ema"] = (
                _PUBLIC_SCORE_ALPHA * latency_ms
                + (1.0 - _PUBLIC_SCORE_ALPHA) * float(entry.get("latency_ema", _PUBLIC_DEFAULT_LATENCY_MS))
            )
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_error"] = str(error_type or "") if not success else ""
        entry["last_update"] = int(time.time())
        scores[source] = entry
        try:
            _write_public_source_scores(download_dir, scores)
        except OSError:
            # Source health is an optimization, never a reason to fail a PDF.
            return


def _rank_public_candidates(candidates: list[dict[str, str]], download_dir: Path) -> list[dict[str, str]]:
    """Order candidates by recent success, then by observed latency."""

    scores = _load_public_source_scores(download_dir)
    indexed = list(enumerate(candidates))

    def sort_key(item: tuple[int, dict[str, str]]) -> tuple[float, float, int]:
        index, candidate = item
        entry = scores.get(str(candidate.get("source", "")), {})
        success = float(entry.get("success_ema", _PUBLIC_DEFAULT_SCORE))
        latency = float(entry.get("latency_ema", _PUBLIC_DEFAULT_LATENCY_MS))
        return (-success, latency, index)

    return [candidate for _, candidate in sorted(indexed, key=sort_key)]


def _public_download_error_type(error_value: Exception | str) -> str:
    """Classify common public-source failures for UI diagnostics and scoring."""

    detail = str(error_value).casefold()
    if "404" in detail or "not found" in detail:
        return "not_found"
    if "403" in detail or "forbidden" in detail or "access denied" in detail:
        return "forbidden"
    if "429" in detail or "rate" in detail or "too many" in detail:
        return "rate_limited"
    if "timeout" in detail or "timed out" in detail:
        return "timeout"
    if "ssl" in detail or "certificate" in detail:
        return "ssl_error"
    if "captcha" in detail or "challenge" in detail or "cloudflare" in detail:
        return "captcha"
    return "unknown"


def _is_suspicious_public_pdf(path: Path) -> bool:
    """Reject tiny one-page preview PDFs when pypdf can inspect the file.

    Some repositories return a cover/preview page with a valid ``%PDF`` header.
    Parsing is deliberately best-effort: a malformed test fixture or a PDF that
    pypdf cannot parse is left to the existing header validation path.
    """

    try:
        from pypdf import PdfReader

        if path.stat().st_size >= 100_000:
            return False
        reader = PdfReader(str(path), strict=False)
        if len(reader.pages) > 1:
            return False
        if not reader.pages:
            return True
        text = (reader.pages[0].extract_text() or "").strip()
        return path.stat().st_size < 16_384 and len(text) < 80
    except Exception:
        return False


def _download_public_candidate_race(
    candidate: dict[str, str],
    *,
    output_dir: Path,
    identifier: str,
    timeout: float,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Download one public candidate into an isolated staging directory."""

    started = time.perf_counter()
    staging = TemporaryDirectory(prefix=".source-race-", dir=str(output_dir))
    try:
        destination = _download_public_pdf(
            candidate,
            output_dir=Path(staging.name),
            identifier=identifier,
            timeout=timeout,
            cancel_check=cancel_check,
        )
    except Exception as exc:
        staging.cleanup()
        return {
            "candidate": candidate,
            "error": str(exc),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "staging": None,
        }
    return {
        "candidate": candidate,
        "path": Path(destination),
        "latency_ms": (time.perf_counter() - started) * 1000,
        "staging": staging,
    }


def _cleanup_public_race_outcome(outcome: dict[str, Any]) -> None:
    staging = outcome.get("staging")
    if staging is not None:
        try:
            staging.cleanup()
        except OSError:
            pass


def _race_public_archives(
    candidates: list[dict[str, str]],
    *,
    identifier: str,
    strategy: str,
    output_dir: Path,
    score_dir: Path | None = None,
    timeout: float,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Race public locations and converge the result to one canonical PDF."""

    pool = ThreadPoolExecutor(max_workers=min(_PUBLIC_RACE_WORKERS, len(candidates)))
    futures = {
        pool.submit(
            _download_public_candidate_race,
            candidate,
            output_dir=output_dir,
            identifier=identifier,
            timeout=timeout,
            cancel_check=cancel_check,
        ): candidate
        for candidate in candidates
    }
    outcomes: dict[Any, dict[str, Any]] = {}
    winner: Path | None = None
    errors: list[str] = []
    health_dir = score_dir or output_dir

    def evaluate(outcome: dict[str, Any], *, select: bool) -> None:
        nonlocal winner
        path = outcome.get("path")
        if path is None or not _pdf_file(Path(path)):
            outcome["success"] = False
            outcome.setdefault("error", "source did not produce a valid PDF")
            return
        if _is_suspicious_public_pdf(Path(path)):
            outcome["success"] = False
            outcome["error"] = "public source returned a suspicious preview PDF"
            outcome["error_type"] = "suspicious_pdf"
            return
        outcome["success"] = True
        if select and winner is None:
            destination = output_dir / f"{_safe_project_name(identifier.replace('/', '_'))}.pdf"
            try:
                with _DOWNLOAD_COMMIT_LOCK:
                    if destination.exists() and destination.resolve() != Path(path).resolve():
                        destination.unlink(missing_ok=True)
                    if destination.resolve() != Path(path).resolve():
                        Path(path).replace(destination)
                winner = destination.resolve()
                outcome["selected"] = True
            except OSError as exc:
                outcome["success"] = False
                outcome["error"] = f"could not commit selected PDF: {exc}"
                outcome["error_type"] = "commit_error"

    try:
        try:
            for future in _completed_downloads(futures, cancel_check, timeout=max(1.0, float(timeout))):
                try:
                    outcome = future.result()
                except Exception as exc:
                    outcome = {
                        "candidate": futures[future],
                        "error": str(exc),
                        "latency_ms": 0.0,
                        "staging": None,
                    }
                outcomes[future] = outcome
                evaluate(outcome, select=True)
                if winner is not None:
                    break
        except TimeoutError:
            errors.append("public source race timed out")
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

        # Collect late results so source health is updated and all staging
        # directories are cleaned, including sources that lost the race.
        for future, candidate in futures.items():
            if future in outcomes:
                continue
            if not future.done():
                # Read-only late requests may finish after pause/winner selection.
                # They only own staging data, which is discarded on completion.
                future.add_done_callback(lambda finished: _cleanup_public_race_outcome(finished.result()) if not finished.cancelled() else None)
                continue
            if future.cancelled():
                outcomes[future] = {
                    "candidate": candidate,
                    "error": "cancelled after another source succeeded",
                    "latency_ms": 0.0,
                    "staging": None,
                }
                continue
            try:
                outcome = future.result()
            except Exception as exc:
                outcome = {
                    "candidate": candidate,
                    "error": str(exc),
                    "latency_ms": 0.0,
                    "staging": None,
                }
            outcomes[future] = outcome
            evaluate(outcome, select=False)

        attempts: list[dict[str, Any]] = []
        for outcome in outcomes.values():
            candidate = dict(outcome.get("candidate") or {})
            source = str(candidate.get("source") or "unknown")
            success = bool(outcome.get("success"))
            error_value = str(outcome.get("error") or "")
            error_type = str(
                outcome.get("error_type")
                or (_public_download_error_type(error_value) if not success else "")
            )
            latency_ms = float(outcome.get("latency_ms") or 0.0)
            _record_public_source_result(
                health_dir,
                source,
                success=success,
                latency_ms=latency_ms,
                error_type=error_type,
            )
            attempts.append(
                {
                    "source": source,
                    "status": "success" if success else "failed",
                    "selected": bool(outcome.get("selected")),
                    "latency_ms": round(latency_ms),
                    **({"error_type": error_type} if error_type else {}),
                    **({"error": error_value[:300]} if error_value else {}),
                }
            )
            if not success and error_value:
                errors.append(f"{source}: {error_value}")
            _cleanup_public_race_outcome(outcome)

    if winner is not None and _pdf_file(winner):
        selected_source = next(
            (str(item["source"]) for item in attempts if item.get("selected")),
            "public archive",
        )
        return {
            "ok": True,
            "identifier": identifier,
            "strategy": strategy,
            "output_dir": str(output_dir),
            "files": [str(winner)],
            "source": selected_source,
            "source_race": True,
            "attempts": attempts,
            "message": f"downloaded from {selected_source}",
        }
    raise RuntimeError("public source download failed: " + " | ".join(errors[-3:]))


def _doi_index_path(download_dir: Path) -> Path:
    return download_dir / ".doi_index.json"


def _prune_download_index(index: dict[str, Any]) -> tuple[dict[str, str], bool]:
    """Drop stale or non-PDF cache entries before they can fake a hit."""

    cleaned: dict[str, str] = {}
    changed = False
    for raw_key, raw_value in index.items():
        key = str(raw_key).strip()
        value = str(raw_value).strip()
        if not key or not value:
            changed = True
            continue
        path = Path(value).expanduser().resolve()
        if not _pdf_file(path):
            changed = True
            continue
        cleaned[key] = str(path)
        if value != str(path):
            changed = True
    return cleaned, changed


def _write_download_index(index_path: Path, index: dict[str, str]) -> None:
    temporary = index_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, index_path)


def _update_download_index(download_dir: Path, identifier: str, file_path: Path) -> None:
    """Keep a DOI→canonical file map compatible with scansci-pdf."""

    index_path = _doi_index_path(download_dir)
    try:
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        if not isinstance(index, dict):
            index = {}
        index, _ = _prune_download_index(index)
        # DOI matching is case-insensitive, and arXiv DOI aliases should share
        # the same cache entry as the bare arXiv identifier.
        index[_identifier_key(identifier)] = str(file_path.resolve())
        _write_download_index(index_path, index)
    except (OSError, TypeError, ValueError):
        return


def _download_marker(identifier: str) -> str:
    return _scansci_pdf_safe_filename(_normalize_download_identifier(identifier)).casefold()


def _matching_download_pdfs(identifier: str, download_dir: Path) -> list[Path]:
    marker = _download_marker(identifier)
    if not marker or not download_dir.is_dir():
        return []
    matches: list[Path] = []
    for path in download_dir.glob("*.pdf"):
        lowered = path.name.casefold()
        if lowered == f"{marker}.pdf" or lowered.startswith(f"{marker}_") or lowered.startswith(f"{marker}-"):
            if _pdf_file(path):
                matches.append(path.resolve())
    return matches


def _canonicalize_download_files(
    identifier: str,
    files: list[str],
    *,
    download_dir: Path,
    timeout: float,
    cancel_check: Callable[[], bool] | None = None,
) -> list[str]:
    """Collapse source-race leftovers into one scansci-pdf-named PDF."""

    candidates: set[Path] = {Path(value).resolve() for value in files if value}
    candidates.update(_matching_download_pdfs(identifier, download_dir))
    candidates = {path for path in candidates if _pdf_file(path)}
    if not candidates:
        return [str(value) for value in files if value]

    chosen = max(
        candidates,
        key=lambda path: (
            path.stat().st_size,
            path.name.casefold() == f"{_download_marker(identifier)}.pdf",
        ),
    )
    _check_download_cancelled(cancel_check)
    metadata = _crossref_filename_metadata(identifier, timeout=min(timeout, 10.0))
    _check_download_cancelled(cancel_check)
    canonical_stem = _scansci_pdf_filename(metadata) or _scansci_pdf_safe_filename(_normalize_download_identifier(identifier))
    target = (download_dir / f"{canonical_stem}.pdf").resolve()
    if chosen != target:
        try:
            if target.exists() and _pdf_file(target):
                if target.stat().st_size >= chosen.stat().st_size:
                    chosen.unlink(missing_ok=True)
                    chosen = target
                else:
                    target.unlink(missing_ok=True)
                    chosen.rename(target)
                    chosen = target
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                chosen.rename(target)
                chosen = target
        except OSError:
            pass

    for duplicate in candidates:
        if duplicate != chosen and duplicate.exists():
            try:
                duplicate.unlink(missing_ok=True)
            except OSError:
                pass
    _update_download_index(download_dir, identifier, chosen)
    return [str(chosen)]


def _finalize_download_result(
    result: dict[str, Any],
    identifier: str,
    *,
    workspace: str | Path,
    timeout: float,
    output_dir: str | Path | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    download_dir = Path(output_dir).resolve() if output_dir else _download_directory(workspace)
    raw_files = [str(path) for path in (result.get("files") or []) if path]
    if result.get("file"):
        raw_files.append(str(result["file"]))
    files = _canonicalize_download_files(
        identifier,
        raw_files,
        download_dir=download_dir,
        timeout=timeout,
        cancel_check=cancel_check,
    )
    if files:
        result["files"] = files
        result["file"] = files[0]
    return result


def _cancelable_sleep(seconds: float, cancel_check: "Callable[[], bool] | None") -> None:
    """Sleep in short slices so a cancel is noticed within ~0.5s."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if cancel_check is not None and cancel_check():
            return
        time.sleep(min(0.5, remaining))


def _identifier_already_downloaded(identifier: str, download_dir: Path) -> list[str]:
    """Return PDFs already in ``download_dir`` whose filename mentions identifier.

    Used to skip re-downloading on batch resume. Matching is intentionally
    fuzzy on filename so it works with the varied naming the CLI/archive
    fallbacks produce; a false negative simply triggers a re-download.
    """

    if not download_dir.is_dir():
        return []
    index_path = _doi_index_path(download_dir)
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(index, dict):
                index = {}
            index, changed = _prune_download_index(index)
            if changed:
                _write_download_index(index_path, index)
            lookup_keys = [_normalize_download_identifier(identifier), _identifier_key(identifier)]
            for key in dict.fromkeys(lookup_keys):
                indexed = Path(str(index.get(key, ""))).resolve()
                if indexed.exists() and _pdf_file(indexed):
                    return [str(indexed)]
        except (OSError, TypeError, ValueError):
            pass
    return sorted(str(path) for path in _matching_download_pdfs(identifier, download_dir))


def _build_result(
    items: list[dict[str, Any]],
    download_dir: Path,
    strategy: str,
    rotation_state: dict[str, Any],
) -> dict[str, Any]:
    """Build the final result dict from the shared items list."""
    completed_files: list[str] = []
    for item in items:
        completed_files.extend(item.get("files") or [])
    completed_count = sum(1 for item in items if item["status"] == "completed")
    failed_count = sum(1 for item in items if item["status"] == "failed")
    result: dict[str, Any] = {
        "ok": failed_count == 0,
        "strategy": strategy,
        "output_dir": str(download_dir),
        "items": items,
        "completed": completed_count,
        "failed": failed_count,
        "total": len(items),
        "files": completed_files,
        "message": f"批量完成：成功 {completed_count}/{len(items)}，失败 {failed_count}",
    }
    if rotation_state.get("count", 0) > 0:
        result["tor_rotations"] = rotation_state["count"]
        result["tor_last_rotation_ok"] = rotation_state["last_ok"]
    return result


def download_papers(
    identifiers: list[str],
    *,
    workspace: str | Path,
    strategy: str = "oa_first",
    timeout: float = 180.0,
    on_progress: "Callable[[dict[str, Any]], None] | None" = None,
    cancel_check: "Callable[[], bool] | None" = None,
    env_overrides: dict[str, str] | None = None,
    rotate_circuit: "Callable[[], bool] | None" = None,
    rotate_every: int = 0,
    oa_hints: "dict[str, bool] | None" = None,
) -> dict[str, Any]:
    """Download a list of DOI/arXiv identifiers one by one.

    Each item is resolved through :func:`download_paper`; a single failure
    never aborts the batch. ``on_progress`` receives the live item-state dict
    after every item (and once at the start), and ``cancel_check`` is polled
    during network waits so a cooperative cancel stops the loop promptly. Items whose
    PDF already exists in the download directory are marked completed without
    a network request, which makes batch resume cheap and idempotent.

    ``env_overrides`` is merged into each scansci-pdf subprocess environment
    (e.g. ``{"TOR_PROXY": "socks5h://..."}`` to route through a managed Tor).
    ``rotate_circuit`` is called every ``rotate_every`` real downloads so an
    upstream Tor can switch circuits to spread requests over multiple exit
    IPs; its return value (whether rotation succeeded) is folded into the
    progress output for the UI. ``oa_hints`` carries the search stage's
    ``is_oa`` knowledge keyed by identifier so works known to be closed-access
    fail fast instead of spending the full per-item timeout in the CLI race.
    """

    normalized_strategy = strategy if strategy in {"legal_only", "oa_first", "gray_oa"} else "oa_first"
    download_dir = _download_directory(workspace)
    download_dir.mkdir(parents=True, exist_ok=True)
    requested_stop = cancel_check
    stopped = threading.Event()

    def stop_requested() -> bool:
        # Latch this invocation's stop: a later resume must not let old workers
        # publish late files after the Host clears the persisted pause flag.
        if requested_stop is not None and requested_stop():
            stopped.set()
        return stopped.is_set()

    cancel_check = stop_requested
    _check_download_cancelled(cancel_check)

    # Validate up front so a malformed list fails fast with a clear error,
    # rather than reporting every item as failed.
    cleaned: list[str] = []
    seen_identifiers: set[str] = set()
    identifier_oa_hints: dict[str, bool] = {}
    hint_source = dict(oa_hints or {})
    for raw in identifiers:
        clean = _normalize_download_identifier(raw)
        if _DOI_PATTERN.fullmatch(clean) or _ARXIV_PATTERN.fullmatch(clean):
            key = _identifier_key(clean)
            if key not in seen_identifiers:
                seen_identifiers.add(key)
                cleaned.append(clean)
            for hint_key in (clean, key, _normalize_download_identifier(str(raw)), _identifier_key(str(raw))):
                if hint_key and hint_key in hint_source:
                    identifier_oa_hints[key] = bool(hint_source[hint_key])
                    break
    if not cleaned:
        raise ValueError("未识别到有效的 DOI 或 arXiv ID")

    items: list[dict[str, Any]] = []
    for identifier in cleaned:
        existing = _identifier_already_downloaded(identifier, download_dir)
        marker = _download_marker(identifier)
        if existing and (
            len(existing) > 1
            or any(Path(path).name.casefold().startswith((f"{marker}_", f"{marker}-")) for path in existing)
        ):
            existing = _canonicalize_download_files(
                identifier,
                existing,
                download_dir=download_dir,
                timeout=timeout,
                cancel_check=cancel_check,
            )
        items.append(
            {
                "identifier": identifier,
                "status": "completed" if existing else "pending",
                "files": existing,
                "error": "",
                "source": "local_cache" if existing else "",
                "attempts": [],
            }
        )

    rotation_state = {"count": 0, "last_ok": False}

    def emit() -> None:
        if on_progress is not None:
            completed = sum(1 for item in items if item["status"] == "completed")
            failed = sum(1 for item in items if item["status"] == "failed")
            payload: dict[str, Any] = {
                "items": [dict(item) for item in items],
                "completed": completed,
                "failed": failed,
                "total": len(items),
            }
            if rotate_circuit is not None and rotate_every > 0:
                payload["tor_rotations"] = rotation_state["count"]
                payload["tor_last_rotation_ok"] = rotation_state["last_ok"]
            on_progress(payload)

    emit()

    # Build the work queue — only items that need downloading.
    pending = [item for item in items if item["status"] != "completed"]
    if not pending:
        return _build_result(items, download_dir, normalized_strategy, rotation_state)

    # Worker: call download_paper with retries, return the file plus source
    # diagnostics so batch progress can explain which version won the race.
    def _download_one(item: dict[str, Any]) -> tuple[str, list[str], str, dict[str, Any]]:
        identifier = str(item["identifier"])
        attempt = 0
        while True:
            try:
                with TemporaryDirectory(prefix=".batch-", dir=download_dir) as staging_directory:
                    staging_dir = Path(staging_directory).resolve()
                    result = download_paper(
                        identifier,
                        workspace=workspace,
                        strategy=normalized_strategy,
                        timeout=timeout,
                        env_overrides=env_overrides,
                        metadata_is_oa=identifier_oa_hints.get(_identifier_key(identifier)),
                        _output_dir=staging_dir,
                        _source_health_dir=download_dir,
                        cancel_check=cancel_check,
                    )
                    files = [str(path) for path in (result.get("files") or [])]
                    staged_files = [
                        Path(path).resolve()
                        for path in files
                        if staging_dir == Path(path).resolve().parent or staging_dir in Path(path).resolve().parents
                    ]
                    if staged_files:
                        if cancel_check is not None and cancel_check():
                            raise _BatchCancelled("Batch download cancelled before commit")
                        with _DOWNLOAD_COMMIT_LOCK:
                            if cancel_check is not None and cancel_check():
                                raise _BatchCancelled("Batch download cancelled before commit")
                            files = _canonicalize_download_files(
                                identifier,
                                [str(path) for path in staged_files],
                                download_dir=download_dir,
                                timeout=timeout,
                                cancel_check=cancel_check,
                            )
                return identifier, files, "", {
                    "source": str(result.get("source") or ""),
                    "provider": str(result.get("provider") or ""),
                    "attempts": list(result.get("attempts") or []),
                }
            except _BatchCancelled:
                raise
            except Exception as error:
                if attempt < _BATCH_MAX_RETRIES and _looks_rate_limited(error):
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    _cancelable_sleep(backoff, cancel_check)
                    if cancel_check is not None and cancel_check():
                        raise _BatchCancelled("批量下载已取消") from None
                    attempt += 1
                    continue
                return identifier, [], f"{type(error).__name__}: {error}"[:500], {}

    # Main loop: throttle submissions and collect results.
    workers = max(1, min(_BATCH_WORKERS, len(pending)))
    first_submission = True
    submitted_since_rotation = 0
    submitted = 0
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Any, dict[str, Any]] = {}
    try:
        for item in pending:
            # Co-operative cancel check between submissions.
            if cancel_check is not None and cancel_check():
                for remaining in pending:
                    if remaining["status"] in {"pending", "downloading"}:
                        remaining["status"] = "cancelled"
                emit()
                raise _BatchCancelled("批量下载已取消")
            # Circuit rotation (main thread only — Tor control is single-connection).
            # Rotate on submission count so parallel workers that are still running
            # don't block the rotation gate: every Nth submitted download gets the new
            # circuit regardless of completion timing.
            if (
                rotate_circuit is not None
                and rotate_every > 0
                and not first_submission
                and submitted_since_rotation >= rotate_every
            ):
                rotation_state["last_ok"] = bool(rotate_circuit())
                if rotation_state["last_ok"]:
                    rotation_state["count"] += 1
                submitted_since_rotation = 0
                if cancel_check is not None and cancel_check():
                    raise _BatchCancelled("批量下载已取消")
            # Polite gap between submissions — skipped for the very first item.
            if not first_submission and _BATCH_DELAY_MAX > 0:
                _cancelable_sleep(random.uniform(_BATCH_DELAY_MIN, _BATCH_DELAY_MAX), cancel_check)
                if cancel_check is not None and cancel_check():
                    item["status"] = "cancelled"
                    emit()
                    raise _BatchCancelled("批量下载已取消")
            first_submission = False
            item["status"] = "downloading"
            submitted += 1
            submitted_since_rotation += 1
            future = executor.submit(_download_one, item)
            futures[future] = item

        # Collect results as they complete.
        try:
            for future in _completed_downloads(futures, cancel_check):
                if cancel_check is not None and cancel_check():
                    for f in futures:
                        f.cancel()
                    for remaining in pending:
                        if remaining["status"] in {"pending", "downloading"}:
                            remaining["status"] = "cancelled"
                    emit()
                    raise _BatchCancelled("批量下载已取消")
                item = futures[future]
                try:
                    identifier, files, error, details = future.result()
                except _BatchCancelled:
                    item["status"] = "cancelled"
                    emit()
                    raise
                item["files"] = files
                item["source"] = str(details.get("source") or "")
                item["provider"] = str(details.get("provider") or "")
                item["attempts"] = list(details.get("attempts") or [])
                if error:
                    item["status"] = "failed"
                    item["error"] = error
                elif files:
                    item["status"] = "completed"
                else:
                    item["status"] = "failed"
                    item["error"] = "下载器未生成有效 PDF"
                emit()
        except _BatchCancelled:
            # Clean cancellation: cancel any still-running futures.
            for f in futures:
                f.cancel()
            raise
    except BaseException:
        stopped.set()
        for item in pending:
            if item["status"] in {"pending", "downloading"}:
                item["status"] = "cancelled"
        emit()
        raise
    finally:
        executor.shutdown(wait=not stopped.is_set(), cancel_futures=stopped.is_set())

    return _build_result(items, download_dir, normalized_strategy, rotation_state)


def _download_from_public_archives(
    identifier: str,
    *,
    workspace: str | Path,
    strategy: str,
    timeout: float,
    output_dir: str | Path | None = None,
    source_health_dir: str | Path | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Download from public archives when the optional CLI is not packaged.

    ``gray_oa`` covers lawful grey-literature locations such as institutional
    repositories and preprint archives; it never attempts to bypass a paid
    publisher page.
    """

    _check_download_cancelled(cancel_check)
    candidates = _public_fulltext_candidates(identifier, timeout=min(timeout, 10.0), cancel_check=cancel_check)
    _check_download_cancelled(cancel_check)
    if not candidates:
        raise RuntimeError("未找到可直接获取的开放全文或灰色文献存档版本。")
    resolved_output_dir = Path(output_dir).resolve() if output_dir else _download_directory(workspace)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_health_dir = Path(source_health_dir).resolve() if source_health_dir else resolved_output_dir
    resolved_health_dir.mkdir(parents=True, exist_ok=True)
    candidates = _rank_public_candidates(candidates, resolved_health_dir)
    if len(candidates) > 1:
        return _race_public_archives(
            candidates,
            identifier=identifier,
            strategy=strategy,
            output_dir=resolved_output_dir,
            score_dir=resolved_health_dir,
            timeout=timeout,
            cancel_check=cancel_check,
        )
    errors: list[str] = []
    for candidate in candidates:
        started = time.perf_counter()
        try:
            destination = _download_public_pdf(
                candidate,
                output_dir=resolved_output_dir,
                identifier=identifier,
                timeout=timeout,
                cancel_check=cancel_check,
            )
        except RuntimeError as exc:
            errors.append(str(exc))
            _record_public_source_result(
                resolved_health_dir,
                candidate["source"],
                success=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_type=_public_download_error_type(exc),
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000
        _record_public_source_result(
            resolved_health_dir,
            candidate["source"],
            success=True,
            latency_ms=latency_ms,
        )
        return {
            "ok": True,
            "identifier": identifier,
            "strategy": strategy,
            "output_dir": str(resolved_output_dir),
            "files": [str(destination)],
            "source": candidate["source"],
            "source_race": False,
            "attempts": [
                {
                    "source": candidate["source"],
                    "status": "success",
                    "selected": True,
                    "latency_ms": round(latency_ms),
                }
            ],
            "message": f"已从 {candidate['source']} 保存全文。",
        }
    raise RuntimeError("已找到全文入口，但下载失败：" + "；".join(errors[-3:]))


def _public_fulltext_candidates(identifier: str, *, timeout: float, cancel_check: Callable[[], bool] | None = None) -> list[dict[str, str]]:
    _check_download_cancelled(cancel_check)
    if _ARXIV_PATTERN.fullmatch(identifier):
        arxiv_id = identifier.removeprefix("arXiv:").removeprefix("ARXIV:")
        return [{"url": f"https://arxiv.org/pdf/{parse.quote(arxiv_id, safe='./')}.pdf", "source": "arXiv"}]
    if identifier.casefold().startswith("10.48550/arxiv."):
        arxiv_id = re.sub(r"^10\.48550/arxiv\.", "", identifier, flags=re.IGNORECASE)
        return [{"url": f"https://arxiv.org/pdf/{parse.quote(arxiv_id, safe='./')}.pdf", "source": "arXiv"}]
    encoded = parse.quote(identifier, safe="")
    candidates: list[dict[str, str]] = []
    unpaywall_email = os.getenv("SCANSCI_UNPAYWALL_EMAIL", "").strip()
    if unpaywall_email:
        try:
            unpaywall = _request_json(
                f"https://api.unpaywall.org/v2/{encoded}?email={parse.quote(unpaywall_email, safe='@')}",
                timeout=timeout,
            )
        except RuntimeError:
            unpaywall = {}
        if isinstance(unpaywall, dict):
            locations = [unpaywall.get("best_oa_location"), *(unpaywall.get("oa_locations") or [])]
            for location in locations:
                if not isinstance(location, dict):
                    continue
                url = str(location.get("url_for_pdf") or "").strip()
                if not url:
                    continue
                host_type = str(location.get("host_type") or "open-access location").replace("_", " ")
                candidates.append({"url": url, "source": f"Unpaywall {host_type}"})
    _check_download_cancelled(cancel_check)
    europe_query = parse.quote(f"DOI:{identifier}", safe="")
    try:
        europe_pmc = _request_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={europe_query}&format=json&resultType=core&pageSize=1",
            timeout=timeout,
        )
    except RuntimeError:
        europe_pmc = {}
    europe_results = (
        dict(europe_pmc.get("resultList") or {}).get("result", [])
        if isinstance(europe_pmc, dict)
        else []
    )
    for record in list(europe_results or [])[:1]:
        if not isinstance(record, dict):
            continue
        fulltext_urls = dict(record.get("fullTextUrlList") or {}).get("fullTextUrl", [])
        added_pdf = False
        ordered_links = sorted(
            list(fulltext_urls or []),
            key=lambda link: (
                0 if isinstance(link, dict) and "europepmc.org" in str(link.get("url", "")).lower() else 1
            ),
        )
        for link in ordered_links:
            if not isinstance(link, dict):
                continue
            if str(link.get("documentStyle", "")).lower() != "pdf":
                continue
            if "open" not in str(link.get("availability", "open access")).lower():
                continue
            url = str(link.get("url") or "").strip()
            if url:
                candidates.append({"url": url, "source": "Europe PMC open access"})
                added_pdf = True
        pmcid = str(record.get("pmcid") or "").strip()
        if pmcid and not added_pdf:
            candidates.append(
                {
                    "url": f"https://europepmc.org/articles/{parse.quote(pmcid, safe='')}/?pdf=render",
                    "source": "Europe PMC",
                }
            )
    _check_download_cancelled(cancel_check)
    try:
        work = _request_json(f"https://api.openalex.org/works/https://doi.org/{encoded}", timeout=timeout)
    except RuntimeError:
        work = {}
    if isinstance(work, dict):
        locations = [work.get("best_oa_location"), *(work.get("locations") or [])]
        for location in locations:
            if not isinstance(location, dict):
                continue
            url = str(location.get("pdf_url") or "").strip()
            if not url:
                continue
            source = dict(location.get("source") or {}).get("display_name") or "OpenAlex open-access archive"
            candidates.append({"url": url, "source": str(source)})
    _check_download_cancelled(cancel_check)
    try:
        crossref = _request_json(f"https://api.crossref.org/works/{encoded}", timeout=timeout)
    except RuntimeError:
        crossref = {}
    message = dict(crossref.get("message") or {}) if isinstance(crossref, dict) else {}
    for link in list(message.get("link") or []):
        if not isinstance(link, dict) or "pdf" not in str(link.get("content-type", "")).lower():
            continue
        url = str(link.get("URL") or "").strip()
        if url:
            candidates.append({"url": url, "source": "Publisher-declared PDF"})
    seen: set[str] = set()
    return [item for item in candidates if item["url"].startswith("https://") and not (item["url"] in seen or seen.add(item["url"]))]


def _download_public_pdf(candidate: dict[str, str], *, output_dir: Path, identifier: str, timeout: float, cancel_check: Callable[[], bool] | None = None) -> Path:
    _check_download_cancelled(cancel_check)
    req = request.Request(candidate["url"], headers={"Accept": "application/pdf,*/*;q=0.8", "User-Agent": "ScanSci-Desktop/0.2"})
    try:
        with request.urlopen(req, timeout=min(timeout, 10.0)) as response:
            chunks: list[bytes] = []
            size = 0
            while size <= 200 * 1024 * 1024:
                _check_download_cancelled(cancel_check)
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            data = b"".join(chunks)
    except (error.HTTPError, error.URLError, OSError) as exc:
        raise RuntimeError(f"{candidate['source']} 不可访问") from exc
    if len(data) > 200 * 1024 * 1024:
        raise RuntimeError("全文文件超过 200 MB 上限")
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"{candidate['source']} 未返回 PDF")
    _check_download_cancelled(cancel_check)
    destination = output_dir / f"{_safe_project_name(identifier.replace('/', '_'))}.pdf"
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def verify_doi_metadata(doi: str, *, expected_title: str = "", timeout: float = 15.0) -> dict[str, Any]:
    clean = str(doi or "").strip()
    if not _DOI_PATTERN.fullmatch(clean):
        raise ValueError("请输入有效 DOI")
    endpoint = f"https://api.crossref.org/works/{parse.quote(clean, safe='')}"
    payload = _request_json(endpoint, headers={"User-Agent": "ScanSci-Desktop/0.1"}, timeout=timeout)
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    titles = message.get("title", []) if isinstance(message, dict) else []
    official_title = str(titles[0] if isinstance(titles, list) and titles else "")
    similarity = SequenceMatcher(None, expected_title.casefold().strip(), official_title.casefold().strip()).ratio() if expected_title.strip() and official_title else None
    return {
        "doi": str(message.get("DOI", clean)) if isinstance(message, dict) else clean,
        "status": "verified" if similarity is None or similarity >= 0.78 else "metadata-mismatch",
        "official_title": official_title,
        "title_similarity": round(similarity, 3) if similarity is not None else None,
        "publisher": str(message.get("publisher", "")) if isinstance(message, dict) else "",
        "journal": str((message.get("container-title") or [""])[0]) if isinstance(message, dict) else "",
        "type": str(message.get("type", "")) if isinstance(message, dict) else "",
        "url": str(message.get("URL", f"https://doi.org/{clean}")) if isinstance(message, dict) else f"https://doi.org/{clean}",
        "source": "Crossref REST API",
    }


def build_ppt_outline(
    notebook: dict[str, Any],
    *,
    topic: str = "",
    template_id: str = "",
) -> dict[str, Any]:
    sources = [dict(item) for item in list(notebook.get("sources", []) or [])]
    title = str(topic or notebook.get("title") or notebook.get("notebook_id") or "研究汇报").strip()
    slides: list[dict[str, Any]] = [
        {"index": 1, "title": title, "purpose": "封面与研究问题", "source_ids": []},
        {"index": 2, "title": "研究范围与证据边界", "purpose": "说明资料库覆盖范围、来源数量与限制", "source_ids": []},
        {"index": 3, "title": "证据版图", "purpose": "按主题、年份与来源组织证据", "source_ids": [str(item.get("doc_id", "")) for item in sources[:8]]},
    ]
    for source in sources[:4]:
        slides.append(
            {
                "index": len(slides) + 1,
                "title": str(source.get("title") or source.get("doc_id") or "核心文献"),
                "purpose": "提炼核心结论、方法与可核验原文",
                "source_ids": [str(source.get("doc_id", ""))],
                "doi": str(source.get("doi", "")),
            }
        )
    slides.extend(
        [
            {"index": len(slides) + 1, "title": "综合发现", "purpose": "汇总跨来源一致结论与冲突证据", "source_ids": [str(item.get("doc_id", "")) for item in sources[:12]]},
            {"index": len(slides) + 2, "title": "局限与下一步", "purpose": "标出证据不足、待核验断言与后续研究", "source_ids": []},
            {"index": len(slides) + 3, "title": "参考文献", "purpose": "保留 DOI 与原文跳转", "source_ids": [str(item.get("doc_id", "")) for item in sources]},
        ]
    )
    template = get_slide_template(template_id) if str(template_id).strip() else None
    return {
        "title": title,
        "format": "ppt169",
        "slide_count": len(slides),
        "slides": slides,
        "evidence_linked": True,
        "template": template,
        "template_id": str(template.get("id", "")) if template else "",
    }


def create_ppt_project(
    notebook: dict[str, Any],
    *,
    workspace: str | Path,
    topic: str = "",
    template_id: str = "",
) -> dict[str, Any]:
    script = _SCANSCI_SUITE_ROOT / "easyslides" / "scripts" / "project_manager.py"
    if not script.is_file():
        raise FileNotFoundError(f"未找到 EasySlides：{script}")
    outline = build_ppt_outline(notebook, topic=topic, template_id=template_id)
    project_root = _presentation_directory(workspace)
    project_root.mkdir(parents=True, exist_ok=True)
    base_name = _safe_project_name(str(outline["title"]))
    dated_name = f"{base_name}_ppt169_{datetime.now().strftime('%Y%m%d')}"
    if (project_root / dated_name).exists():
        base_name = f"{base_name}_{datetime.now().strftime('%H%M%S')}"
        dated_name = f"{base_name}_ppt169_{datetime.now().strftime('%Y%m%d')}"
    _run_command([sys.executable, str(script), "init", base_name, "--format", "ppt169", "--dir", str(project_root)], timeout=30)
    project_path = project_root / dated_name
    sources = []
    for item in list(notebook.get("sources", []) or []):
        source = dict(item)
        candidate = Path(str(source.get("evidence_html_path") or source.get("html_path") or ""))
        if candidate.is_file():
            sources.append(str(candidate.resolve()))
    if sources:
        _run_command([sys.executable, str(script), "import-sources", str(project_path), *sources, "--copy"], timeout=120)
    installed_template_path = ""
    template = dict(outline.get("template", {}) or {})
    if template:
        source_template = resolve_slide_template_dir(str(template["id"]))
        target_template = project_path / "templates" / str(template["id"])
        shutil.copytree(source_template, target_template, dirs_exist_ok=True)
        installed_template_path = str(target_template)
        (project_path / "template_selection.json").write_text(
            json.dumps(
                {
                    "provider": "EasySlides",
                    "template_id": template["id"],
                    "template_name": template["name"],
                    "template_path": f"templates/{template['id']}",
                    "format": template["format"],
                    "replication_mode": template["replication_mode"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    notes_dir = project_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "deck-plan.json").write_text(json.dumps(outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    message = "EasySlides 项目已创建"
    if template:
        message += f"，已应用模板「{template.get('name', '')}」"
    return {
        "ok": True,
        "project_path": str(project_path),
        "imported_sources": len(sources),
        "outline": outline,
        "template": template or None,
        "template_id": str(template.get("id", "")),
        "installed_template_path": installed_template_path,
        "message": message,
    }


def _download_directory(workspace: str | Path) -> Path:
    return Path(workspace).resolve().parent / "downloads"


def _presentation_directory(workspace: str | Path) -> Path:
    return Path(workspace).resolve().parent / "presentations"


def _safe_project_name(value: str) -> str:
    clean = _SAFE_NAME.sub("_", value).strip("._-")[:64]
    return clean or "scansci_deck"


def _run_command(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "命令执行失败").strip()
        raise RuntimeError(detail[-1600:])
    return completed


def _request_json(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> Any:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ScanSci-Desktop/0.1",
        **(headers or {}),
    }
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise RuntimeError(f"远程服务返回 HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"无法连接远程服务：{exc.reason}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"远程服务响应无效：{exc}") from exc
