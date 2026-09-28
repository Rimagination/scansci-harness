"""Local, evidence-first Notebook web application.

The web application deliberately exposes only local ScanSci contracts.  Public
settings are stored next to the workspace while model credentials remain in the
operating-system credential manager.  The browser talks to a small same-origin
JSON API backed by the existing SQLite workspace and evidence store.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape as html_escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import io
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
import webbrowser
import zipfile

from .app_settings import (
    apply_storage_directories,
    ensure_local_model_preset,
    get_provider_api_key,
    load_settings,
    local_model_presets,
    provider_presets,
    save_settings,
    set_notion_api_token,
    set_document_service_api_key,
    set_provider_api_key,
    with_discovered_local_runtime_models,
    vector_index_directory,
)
from .app_update import AppUpdateService
from .build_info import current_build_info
from .deep_research_evidence import task_evidence_reader_path
from .direct_chat_history import DirectChatHistoryStore
from .audio_attachments import audio_attachment_asset
from .image_attachments import attachment_asset
from .image_generation import generated_image_asset
from .ingestion import ingest_sources, ingestion_source_path, ingestion_source_text
from .library_manager import (
    connect_local_zotero,
    import_library_files,
    import_library_folder,
    index_zotero_attachments,
    notebook_evidence_db,
    register_zotero_library,
)
from .zotero_integration import zotero_status
from .knowledge_connectors import connector_catalog, test_connector
from .notion_integration import sync_notion_library, test_notion_connection
from .local_evidence_runtime import (
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    DEFAULT_LOCAL_RERANKER_MODEL,
    default_vector_cache_identity,
)
from .local_model_market import (
    QWEN3_ASR_NATIVE_MODEL_ID,
    create_install_manager,
    delete_installed_model,
    installed_models,
    market_catalog,
)
from .local_runtime_component import LocalRuntimeComponent
from .local_transformers_runtime import ensure_local_transformers_runtime
from .runtime_components import default_node_component, default_tectonic_component
from .model_health import build_model_health
from .ollama_runtime import OLLAMA_VISION_MODEL, OllamaInstallManager, ollama_status
from .mcp_marketplace import (
    install_marketplace_server,
    marketplace_catalog,
    marketplace_updates,
    sync_official_registry,
    update_marketplace_server,
)
from .pi_agent import PiAgentClient
from .research_agent import ResearchAgentRuntime
from .research_tools import (
    analyze_references,
    build_ppt_outline,
    capability_snapshot,
    create_ppt_project,
    download_paper,
    fetch_provider_models,
    search_journals,
    search_paper_atlas,
    test_local_model_connection,
    test_provider_connection,
    verify_doi_metadata,
)
from .skill_manager import (
    cancel_skill_scan,
    check_skill_updates,
    install_skill,
    installed_skills,
    marketplace_skills,
    scan_skill_source,
    scan_skill_update,
    skill_library_path,
    update_skill,
)
from .slides_templates import list_slide_templates, slide_template_asset
from .slide_studio import save_browser_rendered_deck
from .tesseract_installer import TesseractInstallManager
from .telemetry import diagnostic_span, diagnostics_summary, export_diagnostics_bundle
from .vector_index import vector_cache_status
from .vector_storage import migrate_vector_indexes
from .vision_routing import system_ocr_status, tesseract_status
from .workspace import (
    add_note_to_notebook,
    delete_notebook,
    initialize_notebook,
    load_workspace_summary,
    record_citation_audit,
    set_notebook_root_path,
    update_notebook_metadata,
)


# Pasted images are capped at 10 MiB and presentation sources at 30 MiB after
# decoding.  Base64 expansion needs room in the local JSON request before the
# per-file validation runs.
_MAX_REQUEST_BYTES = 170 * 1024 * 1024
_ASSET_DIR = Path(__file__).with_name("web")
_DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data: blob:; "
    "style-src 'self'; script-src 'self'; worker-src 'self' blob:; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'"
)
_EMBEDDED_SOURCE_CONTENT_SECURITY_POLICY = (
    "default-src 'self' data: blob:; connect-src 'none'; img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'none'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'self'"
)

# Site icons are resolved by the loopback app instead of directly by the
# browser. This keeps the desktop CSP tight and gives us one place to enforce
# timeouts, size limits, redirect checks, and a small in-process cache.
_SITE_ICON_MAX_BYTES = 512 * 1024
_SITE_PAGE_MAX_BYTES = 384 * 1024
_SITE_ICON_TIMEOUT_SECONDS = 3.0
_SITE_ICON_CACHE_TTL_SECONDS = 24 * 60 * 60
_SITE_ICON_CONTENT_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
}

_NOTE_FILE_NAME_MAX_CHARS = 120


def _safe_note_file_stem(title: str) -> str:
    """Turn a note title into a safe, readable Markdown file stem."""

    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", str(title or "").strip())
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:_NOTE_FILE_NAME_MAX_CHARS].strip(" .") or "ScanSci-笔记"


def _safe_note_folder_name(value: str) -> str:
    """Validate one user-entered child-folder name without accepting paths."""

    name = str(value or "").strip()
    if not name:
        return ""
    if name in {".", ".."} or any(char in name for char in ('/', '\\', ':')):
        raise ValueError("新建文件夹名称只能是单个文件夹名")
    if any(ord(char) < 32 for char in name) or any(char in name for char in '*?"<>|'):
        raise ValueError("新建文件夹名称包含不支持的字符")
    return name.strip(" .")[:_NOTE_FILE_NAME_MAX_CHARS]
_SITE_ICON_LOCAL_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}

_EVIDENCE_READER_PALETTES = {
    ("light", "jade"): {"highlight": "#caff4d", "ink": "#17210b", "surface": "#ffffff", "text": "#242b20"},
    ("light", "ocean"): {"highlight": "#8eeeff", "ink": "#06242b", "surface": "#ffffff", "text": "#1d292c"},
    ("light", "plum"): {"highlight": "#e7bdff", "ink": "#2d1237", "surface": "#ffffff", "text": "#2b242e"},
    ("light", "amber"): {"highlight": "#ffe66a", "ink": "#2b2100", "surface": "#ffffff", "text": "#302916"},
    ("dark", "jade"): {"highlight": "#b9ef45", "ink": "#162005", "surface": "#171a18", "text": "#edf1ec"},
    ("dark", "ocean"): {"highlight": "#7bdbf0", "ink": "#052128", "surface": "#171a1c", "text": "#edf1f1"},
    ("dark", "plum"): {"highlight": "#deb0ff", "ink": "#261132", "surface": "#1b181c", "text": "#f0ecf2"},
    ("dark", "amber"): {"highlight": "#f5d65c", "ink": "#2b2100", "surface": "#1c1a16", "text": "#f2efe8"},
}


def _decorate_evidence_reader_html(text: str, query: str = "") -> str:
    """Apply the current app appearance to evidence readers, including old indexes."""

    params = parse_qs(query)
    theme = str(params.get("theme", ["light"])[0] or "light").casefold()
    accent = str(params.get("accent", ["jade"])[0] or "jade").casefold()
    if theme not in {"light", "dark"}:
        theme = "light"
    if accent not in {"jade", "ocean", "plum", "amber"}:
        accent = "jade"
    palette = _EVIDENCE_READER_PALETTES[(theme, accent)]
    style = f'''<style data-scansci-reader-theme="true">
:root {{ color-scheme: {theme}; --scansci-evidence-highlight: {palette["highlight"]}; --scansci-evidence-ink: {palette["ink"]}; }}
html, body {{ background: {palette["surface"]}; color: {palette["text"]}; }}
[data-evidence-id] {{ scroll-margin-top: 6rem; }}
[data-evidence-id]:target {{
  background: var(--scansci-evidence-highlight) !important;
  color: var(--scansci-evidence-ink) !important;
  outline: none !important;
  box-shadow: none !important;
  border-color: transparent !important;
  border-radius: 0 !important;
  -webkit-box-decoration-break: clone;
  box-decoration-break: clone;
}}
</style>'''
    for marker in ("data-scansci-evidence-style", "data-scansci-reader-theme"):
        text = re.sub(
            rf"<style\b[^>]*\b{marker}\b[^>]*>.*?</style\s*>",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if re.search(r"</head\s*>", text, flags=re.IGNORECASE):
        return re.sub(r"</head\s*>", lambda match: f"{style}{match.group(0)}", text, count=1, flags=re.IGNORECASE)
    if re.search(r"<html\b[^>]*>", text, flags=re.IGNORECASE):
        return re.sub(r"<html\b[^>]*>", lambda match: f"{match.group(0)}<head>{style}</head>", text, count=1, flags=re.IGNORECASE)
    return f"<!doctype html><html><head>{style}</head><body>{text}</body></html>"


def _diagnostic_route(path: str) -> str:
    """Keep route shape while omitting document, task, and attachment IDs."""

    normalized = re.sub(r"/ing-[a-f0-9]{32}(?=/|$)", "/{ingestion_id}", str(path))
    normalized = re.sub(r"/[a-f0-9]{20,}(?=/|$)", "/{id}", normalized)
    if normalized.startswith("/api/presentations/"):
        return "/api/presentations/{file}"
    return normalized[:240]


@dataclass(frozen=True)
class WebResponse:
    status: int
    content_type: str
    body: bytes
    content_security_policy: str = _DEFAULT_CONTENT_SECURITY_POLICY
    cache_control: str = "no-store"


class LibraryImportManager:
    """Run local document imports in the background with inspectable milestones.

    The original source is never moved or altered.  The manager only makes the
    already-rebuildable import pipeline observable to the first-run experience.
    """

    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("library_kind", "folder") or "folder").strip().lower()
        if kind not in {"folder", "obsidian", "zotero"}:
            raise ValueError("后台资料接入当前支持本地文件夹、Obsidian Vault 和 Zotero")
        path = str(payload.get("path", "") or "").strip()
        if not path and kind != "zotero":
            raise ValueError("请选择要接入的文件夹")
        job_id = f"import-{uuid4().hex}"
        now = int(time.time())
        job = {
            "job_id": job_id,
            "state": "queued",
            "library_kind": kind,
            "path": path,
            "notebook_id": str(payload.get("notebook_id", "") or "").strip(),
            "phase": "准备接入资料",
            "detail": "等待本地解析任务启动",
            "progress": 0.0,
            "error": "",
            "result": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job_id] = job
            thread = threading.Thread(target=self._run, args=(job_id, dict(payload)), daemon=True)
            self._threads[job_id] = thread
            thread.start()
            return deepcopy(job)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise FileNotFoundError("资料接入任务不存在或已过期")
            return deepcopy(self._jobs[job_id])

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if "progress" in changes:
                # Milestones are monotonic, so the bar cannot jump backwards
                # while a nested PDF/Markdown import starts its next stage.
                changes["progress"] = max(float(job.get("progress", 0.0) or 0.0), float(changes["progress"] or 0.0))
            job.update(changes)
            job["updated_at"] = int(time.time())

    def _run(self, job_id: str, payload: dict[str, Any]) -> None:
        self._update(job_id, state="running", phase="检查资料", detail="正在建立安全的只读连接", progress=0.01)

        def report(event: dict[str, Any]) -> None:
            self._update(
                job_id,
                state="running",
                phase=str(event.get("phase", "正在处理资料") or "正在处理资料"),
                detail=str(event.get("detail", "") or ""),
                progress=float(event.get("progress", 0.0) or 0.0),
            )

        try:
            result = self._runner(payload, report)
            self._update(
                job_id,
                state="completed",
                phase="资料已可检索",
                detail="文档、章节与原文证据片段已建立",
                progress=1.0,
                result=result,
                error="",
            )
        except Exception as error:  # keep the original source untouched and expose an actionable failure
            message = str(error).strip() or type(error).__name__
            lowered = message.casefold()
            if "ocr" in lowered or "扫描" in message:
                guidance = "这批资料可能是扫描件。请在 设置 · 文档处理 中启用 OCR 后重试。"
            elif "可提取正文" in message or "没有成功解析" in message:
                guidance = "没有读到可用正文。请检查文件是否损坏、加密，或启用 OCR 后重试。"
            elif "权限" in message or "access" in lowered or "permission" in lowered:
                guidance = "ScanSci 没有读取该目录的权限。请换一个可访问的文件夹后重试。"
            else:
                guidance = "原文件没有被修改；检查提示后可直接重试。"
            self._update(
                job_id,
                state="failed",
                phase="资料接入未完成",
                detail=guidance,
                error=f"{type(error).__name__}: {message}"[:1200],
            )


class NotebookWebApp:
    """Routes the local Notebook UI and its small evidence API."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        evidence_db: str | Path,
        slides_root: str | Path | None = None,
        update_service: AppUpdateService | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.evidence_db = Path(evidence_db).resolve()
        self.slides_root = Path(slides_root).resolve() if slides_root is not None else None
        self.update_service = update_service or AppUpdateService()
        # Apply persisted storage choices before constructing any model or
        # runtime manager.  A second load lets model discovery include a
        # newly selected Hugging Face cache in the normalized catalog.
        apply_storage_directories(load_settings(self.workspace))
        load_settings(self.workspace)
        # A dedicated component URL embedded in the lightweight package takes
        # precedence over the application-update channel. The latter remains
        # a compatibility fallback for combined release manifests.
        self.local_runtime = LocalRuntimeComponent(
            fallback_manifest_url=self.update_service.manifest_url or None
        )
        self.runtime_components = {
            "node": default_node_component(),
            "tectonic": default_tectonic_component(),
        }
        self.tesseract_installs = TesseractInstallManager()
        self.model_installs = create_install_manager()
        self.ollama_installs = OllamaInstallManager(
            state_path=self.workspace.parent / ".scansci-ollama-download-jobs.json",
        )
        self.library_imports = LibraryImportManager(self._run_library_import_job)
        self.direct_chat_history = DirectChatHistoryStore(self.workspace)
        self._retrieval_setup_errors: dict[str, str] = {}
        self._retrieval_setup_lock = threading.RLock()
        self._local_runtime_activation_lock = threading.RLock()
        self._local_runtime_connection_lock = threading.Lock()
        self._local_runtime_activation_models: set[str] = set()
        self._site_icon_cache: dict[str, tuple[float, str, bytes]] = {}
        self._site_icon_cache_lock = threading.RLock()
        self.research_agent = ResearchAgentRuntime(
            workspace=self.workspace,
            evidence_db=self.evidence_db,
            runtime_facts_provider=self._local_resource_facts,
        )

    def _runtime_component(self, component_id: str) -> LocalRuntimeComponent:
        identifier = str(component_id or "").strip().lower()
        component = self.runtime_components.get(identifier)
        if component is None:
            raise ValueError("运行组件 id 必须是 node 或 tectonic")
        return component

    def _runtime_components_status(self) -> dict[str, Any]:
        return {
            "components": {
                identifier: component.status()
                for identifier, component in self.runtime_components.items()
            }
        }

    def _local_resource_facts(self) -> dict[str, Any]:
        """Return a lazy, read-only snapshot for deterministic chat answers."""

        return {
            "runtime": self.local_runtime.status(),
            "model_installs": self.model_installs.status(),
            "ollama": self._ollama_status(),
            "installed_models": installed_models(),
        }

    def _ollama_base_url(self) -> str:
        settings = load_settings(self.workspace)
        local = next(
            (
                item
                for item in list(settings.get("local_models", []) or [])
                if str(item.get("runtime", "")).strip().lower() == "ollama"
                and str(item.get("base_url", "")).strip()
            ),
            None,
        )
        return str(local.get("base_url", "")) if local else ""

    def _ollama_status(self, *, register_ready: bool = False) -> dict[str, Any]:
        status = ollama_status(self._ollama_base_url() or None)
        if register_ready and status.get("model_ready"):
            # A model installed directly with ``ollama pull`` should become
            # routable without requiring the user to copy an endpoint into
            # ScanSci.  Registration is a settings write, so it runs only on
            # the model-market page (an explicit model-management surface),
            # never on a background status poll; and only when the preset is
            # absent/disabled so it stays a one-time side effect.
            try:
                settings = load_settings(self.workspace)
                preset = next(
                    (
                        item
                        for item in list(settings.get("local_models", []) or [])
                        if str(item.get("id", "")) == "ollama-minicpm-v4.6"
                    ),
                    None,
                )
                if not preset or not preset.get("enabled"):
                    ensure_local_model_preset(self.workspace, "ollama-minicpm-v4.6")
            except Exception:
                # Status reporting must remain available even if a stale or
                # read-only settings file prevents automatic registration.
                pass
        return status

    def _model_health(self) -> dict[str, Any]:
        """Return transient provider/model readiness without exposing secrets."""

        settings = load_settings(self.workspace)
        checks: dict[str, dict[str, Any]] = {}
        inventories: dict[str, list[object]] = {}
        endpoint_checks: dict[tuple[str, str], dict[str, Any]] = {}
        ollama = self._ollama_status()
        catalog_settings = settings
        discovery_seed_id = ""
        configured_ollama = any(
            str(item.get("runtime", "")).strip().lower() == "ollama"
            for item in list(settings.get("local_models", []) or [])
            if isinstance(item, dict)
        )
        if ollama.get("reachable") and ollama.get("models") and not configured_ollama:
            discovery_seed_id = "ollama-runtime-discovery"
            catalog_settings = deepcopy(settings)
            catalog_settings["local_models"] = [
                *list(settings.get("local_models", []) or []),
                {
                    "id": discovery_seed_id,
                    "name": "Ollama",
                    "runtime": "ollama",
                    "base_url": f'{str(ollama.get("base_url") or "http://127.0.0.1:11434").rstrip("/")}/v1',
                    "model_id": "",
                    "enabled": True,
                    "capabilities": ["reasoning"],
                },
            ]
        # Local loopback providers are cheap to probe, but an unavailable
        # endpoint must never delay the rest of the UI for the full request
        # timeout.  Ollama has its own bounded status probe above; Hugging Face
        # snapshots are checked from the local cache instead of starting a
        # Transformers server during page load.
        for local_model in list(catalog_settings.get("local_models", []) or []):
            runtime = str(local_model.get("runtime", "")).strip().lower()
            identifier = str(local_model.get("id", "")).strip()
            if runtime == "ollama":
                inventories[identifier] = list(ollama.get("models", []) or []) if ollama.get("reachable") else []
                continue
            if runtime in {"builtin", "local-huggingface"}:
                continue
            if not bool(local_model.get("enabled", True)) or not str(local_model.get("base_url", "")).strip():
                continue
            endpoint = (runtime, str(local_model.get("base_url", "")).strip().rstrip("/"))
            if endpoint not in endpoint_checks:
                try:
                    endpoint_checks[endpoint] = test_local_model_connection(dict(local_model), timeout=1.5)
                except Exception as error:
                    endpoint_checks[endpoint] = {"ok": False, "error": f"{type(error).__name__}: {error}"[:500]}
            checks[identifier] = endpoint_checks[endpoint]
            inventories[identifier] = list(checks[identifier].get("models", []) or []) if checks[identifier].get("ok") else []
        catalog = with_discovered_local_runtime_models(catalog_settings, inventories)
        if discovery_seed_id:
            catalog["local_models"] = [
                item
                for item in list(catalog.get("local_models", []) or [])
                if str(item.get("id", "")) != discovery_seed_id
            ]
        for local_model in list(catalog.get("local_models", []) or []):
            runtime = str(local_model.get("runtime", "")).strip().lower()
            if runtime in {"builtin", "ollama", "local-huggingface"}:
                continue
            endpoint = (runtime, str(local_model.get("base_url", "")).strip().rstrip("/"))
            if endpoint in endpoint_checks:
                checks[str(local_model.get("id", "")).strip()] = endpoint_checks[endpoint]
        health = build_model_health(
            catalog,
            installed_models=installed_models(),
            ollama=ollama,
            runtime_checks=checks,
        )
        health["selection_catalog"] = {
            "providers": [
                {
                    key: deepcopy(value)
                    for key, value in provider.items()
                    if not any(marker in str(key).casefold() for marker in ("api_key", "token", "secret"))
                }
                for provider in list(catalog.get("providers", []) or [])
                if str(provider.get("id", "")) == "local-huggingface"
                or str(provider.get("id", "")).startswith("local-runtime-")
            ],
            "local_models": deepcopy(list(catalog.get("local_models", []) or [])),
        }
        return health

    def _extension_updates(self) -> dict[str, Any]:
        """Return one user-facing update snapshot for all extension types.

        Built-in plugins are part of the signed ScanSci application package,
        so their update is intentionally coupled to the app update channel.
        They are never downloaded or executed as independent archives.
        """

        app_status = self.update_service.status()
        settings = load_settings(self.workspace)
        plugins: list[dict[str, Any]] = []
        for plugin in list(settings.get("plugins", []) or []):
            if not isinstance(plugin, dict) or plugin.get("uninstalled"):
                continue
            bundled = bool(plugin.get("builtin")) or str(plugin.get("update_mode", "")) == "app"
            if bundled and app_status.get("available"):
                state = "available"
                message = f"随 ScanSci v{app_status.get('latest_version', '')} 一起更新"
            elif bundled:
                state = "bundled"
                message = "随 ScanSci 应用更新"
            else:
                state = "manual"
                message = "外部插件需要手动维护"
            plugins.append({
                "id": str(plugin.get("id", "")),
                "name": str(plugin.get("name", "") or plugin.get("id", "")),
                "state": state,
                "available": state == "available",
                "can_install": bool(app_status.get("can_install")) if state == "available" else False,
                "message": message,
            })
        return {
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "app": app_status,
            "skills": check_skill_updates(self.workspace),
            "mcp": marketplace_updates(self.workspace),
            "plugins": plugins,
        }

    def _local_model_install_status(self, job_id: str = "") -> dict[str, Any]:
        requested = str(job_id or "").strip()
        if requested.startswith("ollama:"):
            return self.ollama_installs.status(requested)
        model_status = self.model_installs.status(requested)
        if requested:
            return model_status
        ollama = self.ollama_installs.status()
        jobs = list(model_status.get("jobs", []) or []) + list(ollama.get("jobs", []) or [])
        jobs.sort(key=lambda item: int(item.get("updated_at", 0) or 0), reverse=True)
        active = next(
            (
                item
                for item in jobs
                if str(item.get("state", "")) in {"queued", "downloading", "pausing", "cancelling"}
            ),
            None,
        )
        return {"jobs": jobs, "active": active}

    def _enable_installed_local_model(self, preset_id: str) -> None:
        try:
            ensure_local_model_preset(self.workspace, preset_id)
        except Exception:
            # The model itself is already installed.  A settings write failure
            # must not turn a successful download into a false download error.
            return

    def _connect_downloaded_local_models(self, job: dict[str, Any]) -> None:
        """Start Transformers for downloaded chat/vision snapshots in the background.

        The download job only establishes that the snapshot files are present.
        The isolated runtime remains the authority for loading and probing the
        model, which is especially important for Qwen3.5 compatibility records.
        """

        requested = [
            str(item).strip()
            for item in list(job.get("models", []) or [])
            if str(item).strip()
        ]
        if not requested:
            return
        records = {
            str(item.get("id", "")): item
            for item in installed_models()
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        if not self._local_runtime_connection_lock.acquire(blocking=False):
            return

        def connect() -> None:
            try:
                for model_id in requested:
                    record = records.get(model_id)
                    if not record:
                        continue
                    kind = str(record.get("kind", "")).strip().lower()
                    model_format = str(record.get("format", "")).strip().lower()
                    architecture = str(record.get("architecture", "")).lower().replace("_", "")
                    generation_architecture = (
                        "forcausallm" in architecture or "forconditionalgeneration" in architecture
                    )
                    files_present = bool(record.get("model_files_present", record.get("ready")))
                    if (
                        not files_present
                        or model_format not in {"transformers", "huggingface"}
                        or kind not in {"chat", "vision"}
                        or not (generation_architecture or kind == "vision")
                    ):
                        continue
                    with self._local_runtime_activation_lock:
                        if model_id in self._local_runtime_activation_models:
                            continue
                        self._local_runtime_activation_models.add(model_id)
                    try:
                        ensure_local_transformers_runtime(model_id)
                    except Exception:
                        # The compatibility store records the isolated failure
                        # where applicable.  A later explicit retry can attempt it
                        # again, so failed starts are not latched in memory.
                        pass
                    finally:
                        with self._local_runtime_activation_lock:
                            self._local_runtime_activation_models.discard(model_id)
            finally:
                self._local_runtime_connection_lock.release()

        threading.Thread(
            target=connect,
            name="scansci-local-model-auto-connect",
            daemon=True,
        ).start()

    def dispatch(self, method: str, target: str, body: bytes = b"") -> WebResponse:
        parsed_target = urlparse(target)
        path = parsed_target.path
        with diagnostic_span(
            self.workspace,
            "scansci.http.request",
            {"http.method": method, "http.route": _diagnostic_route(path), "http.request_bytes": len(body)},
        ) as span:
            try:
                if method == "GET":
                    response = self._get(path, parsed_target.query)
                elif method == "POST":
                    response = self._post(path, body)
                else:
                    response = self._json_error(
                        HTTPStatus.METHOD_NOT_ALLOWED,
                        "method_not_allowed",
                        "Only GET and POST are supported.",
                    )
            except FileNotFoundError as error:
                response = self._json_error(HTTPStatus.NOT_FOUND, "not_found", str(error))
            except ValueError as error:
                response = self._json_error(HTTPStatus.BAD_REQUEST, "invalid_request", str(error))
            except RuntimeError as error:
                response = self._json_error(HTTPStatus.BAD_GATEWAY, "tool_failed", str(error))
            except Exception as error:  # keep local API failures visible to the UI
                message = str(error).strip() or type(error).__name__
                response = self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    f"{type(error).__name__}: {message[:1000]}",
                )
            span.set_attribute("http.status_code", int(response.status))
            span.set_attribute("http.response_bytes", len(response.body))
            return response

    def _get(self, path: str, query: str = "") -> WebResponse:
        if path in {"/", "/index.html"}:
            return self._static_asset("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._static_asset("app.js", "application/javascript; charset=utf-8")
        if path == "/pdf-viewer.js":
            return self._static_asset("pdf-viewer.js", "application/javascript; charset=utf-8")
        if path == "/pptx-export.js":
            return self._static_asset("pptx-export.js", "application/javascript; charset=utf-8")
        if path == "/styles.css":
            return self._static_asset("styles.css", "text/css; charset=utf-8")
        if path.startswith("/vendor/"):
            relative = unquote(path.removeprefix("/"))
            content_type = {
                ".mjs": "application/javascript; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }.get(Path(relative).suffix.lower(), "application/octet-stream")
            return self._static_tree_asset(relative, content_type)
        if path.startswith("/provider-icons/"):
            relative = unquote(path.removeprefix("/"))
            content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            if content_type == "image/svg+xml":
                content_type = "image/svg+xml; charset=utf-8"
            return self._static_tree_asset(relative, content_type)
        if path == "/scansci-mark.png":
            return self._static_asset("scansci-mark.png", "image/png")
        if path == "/avatar-panda-male.png":
            return self._static_asset("avatar-panda-male.png", "image/png")
        if path == "/avatar-panda-female.png":
            return self._static_asset("avatar-panda-female.png", "image/png")
        if path == "/avatar-panda-male-duo.png":
            return self._static_asset("avatar-panda-male-duo.png", "image/png")
        if path == "/avatar-panda-female-duo.png":
            return self._static_asset("avatar-panda-female-duo.png", "image/png")
        if path == "/avatar-panda-male-tight.png":
            return self._static_asset("avatar-panda-male-tight.png", "image/png")
        if path == "/avatar-panda-female-tight.png":
            return self._static_asset("avatar-panda-female-tight.png", "image/png")
        if path in {
            "/knowledge-personal.svg",
            "/zotero-logo.svg",
            "/obsidian-logo.svg",
            "/pdf-document.svg",
        }:
            return self._static_asset(path.removeprefix("/"), "image/svg+xml; charset=utf-8")
        if path in {
            "/codex-plugin-documents.png",
            "/codex-plugin-pdf.png",
            "/codex-plugin-spreadsheets.png",
            "/codex-plugin-presentations.png",
            "/codex-plugin-latex.png",
        }:
            return self._static_asset(path.removeprefix("/"), "image/png")
        if path == "/notion-logo.png":
            return self._static_asset("notion-logo.png", "image/png")
        if path == "/api/health":
            build = current_build_info()
            return self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "workspace_exists": self.workspace.exists(),
                    "evidence_store_exists": self.evidence_db.exists(),
                    "version": build["version"],
                    "build_id": build["build_id"],
                    "built_at": build["built_at"],
                    "frozen": build["frozen"],
                    "executable": build["executable"],
                    "runtime_kind": build["runtime_kind"],
                    "package_root": build["package_root"],
                    "source_root": build["source_root"],
                },
            )
        if path == "/api/site-icon":
            return self._site_icon(query)
        if path == "/api/diagnostics":
            summary = diagnostics_summary(self.workspace)
            vector_identity = default_vector_cache_identity()
            summary["vector_cache"] = vector_cache_status(
                self.evidence_db,
                provider=str(vector_identity["provider"]),
                dimensions=int(vector_identity["dimensions"]),
            )
            return self._json(HTTPStatus.OK, summary)
        if path == "/api/diagnostics/bundle":
            bundle = export_diagnostics_bundle(self.workspace)
            return WebResponse(HTTPStatus.OK, "application/zip", bundle.read_bytes())
        if path == "/api/workspace":
            return self._json(HTTPStatus.OK, load_workspace_summary(self.workspace))
        if path == "/api/connectors":
            return self._json(HTTPStatus.OK, connector_catalog(self.workspace))
        if path == "/api/settings/document-processing/ocr/status":
            requested = [
                part.strip()
                for value in parse_qs(query).get("languages", [])
                for part in str(value).split(",")
                if part.strip()
            ]
            provider = str(parse_qs(query).get("provider", ["system"])[0] or "system").strip().lower()
            status = tesseract_status(requested) if provider == "tesseract" else system_ocr_status(requested)
            status["provider"] = provider
            if provider == "tesseract":
                status["install"] = self.tesseract_installs.status()
            return self._json(HTTPStatus.OK, status)
        if path == "/api/settings":
            return self._json(HTTPStatus.OK, load_settings(self.workspace))
        if path == "/api/model-health":
            return self._json(HTTPStatus.OK, self._model_health())
        if path == "/api/settings/presets":
            return self._json(
                HTTPStatus.OK,
                {"providers": provider_presets(), "local_models": local_model_presets()},
            )
        if path == "/api/local-models/installed":
            models = installed_models()
            pending_runtime_models = [
                str(item.get("id", "")).strip()
                for item in models
                if isinstance(item, dict)
                and bool(item.get("model_files_present"))
                and str(item.get("runtime_probe_state", "")) == "pending"
                and str(item.get("id", "")).strip()
            ]
            if pending_runtime_models:
                # This also covers snapshots downloaded before the current
                # app session; the connector itself remains asynchronous.
                self._connect_downloaded_local_models({"models": pending_runtime_models})
            return self._json(HTTPStatus.OK, {"models": models})
        if path == "/api/local-models/market":
            search = str(parse_qs(query).get("q", [""])[0] or "")
            catalog = market_catalog(search)
            ollama = self._ollama_status(register_ready=True)
            for item in list(catalog.get("items", []) or []):
                if str(item.get("runtime", "")).strip().lower() != "ollama":
                    continue
                item["installed"] = bool(ollama.get("model_ready"))
                item["ready"] = bool(ollama.get("model_ready"))
                item["runtime_reachable"] = bool(ollama.get("reachable"))
                item["runtime_error"] = str(ollama.get("error", "") or "")
            return self._json(HTTPStatus.OK, catalog)
        if path == "/api/local-models/install-status":
            job_id = str(parse_qs(query).get("job_id", [""])[0] or "")
            return self._json(HTTPStatus.OK, self._local_model_install_status(job_id))
        if path == "/api/ollama/status":
            return self._json(HTTPStatus.OK, self._ollama_status())
        if path == "/api/local-runtime":
            return self._json(HTTPStatus.OK, self.local_runtime.status())
        if path == "/api/local-runtime/channels":
            return self._json(HTTPStatus.OK, self.local_runtime.check_manifest_channels())
        if path == "/api/local-runtime/install-status":
            return self._json(HTTPStatus.OK, self.local_runtime.install_status())
        if path == "/api/runtime-components":
            return self._json(HTTPStatus.OK, self._runtime_components_status())
        if path == "/api/runtime-components/install-status":
            component_id = str(parse_qs(query).get("component", [""])[0] or "")
            return self._json(HTTPStatus.OK, self._runtime_component(component_id).install_status())
        if path == "/api/runtime-components/channels":
            component_id = str(parse_qs(query).get("component", [""])[0] or "")
            return self._json(HTTPStatus.OK, self._runtime_component(component_id).check_manifest_channels())
        if path == "/api/cuda-status":
            from .local_retrieval_runtime import cuda_status

            return self._json(HTTPStatus.OK, cuda_status())
        if path == "/api/skills":
            return self._json(
                HTTPStatus.OK,
                {"skills": installed_skills(self.workspace), "library_path": str(skill_library_path(self.workspace))},
            )
        if path == "/api/skills/updates":
            return self._json(HTTPStatus.OK, check_skill_updates(self.workspace))
        if path == "/api/skills/market":
            return self._json(HTTPStatus.OK, marketplace_skills())
        if path == "/api/mcp/marketplace":
            return self._json(HTTPStatus.OK, marketplace_catalog(self.workspace))
        if path == "/api/extension-updates":
            return self._json(HTTPStatus.OK, self._extension_updates())
        if path == "/api/capabilities":
            return self._json(
                HTTPStatus.OK,
                capability_snapshot(workspace=self.workspace, evidence_db=self.evidence_db),
            )
        if path == "/api/pi/status":
            return self._json(HTTPStatus.OK, self.research_agent.pi_status())
        if path == "/api/chat/history":
            params = parse_qs(query)
            limit = min(200, max(1, int(params.get("limit", [200])[0] or 200)))
            view = str(params.get("view", ["active"])[0] or "active").strip().lower()
            return self._json(HTTPStatus.OK, self.direct_chat_history.list(limit=limit, view=view))
        if path == "/api/chat/sessions":
            return self._json(HTTPStatus.OK, self.research_agent.list_chat_sessions())
        if path == "/api/chat/stats":
            session_id = str(parse_qs(query).get("session_id", [""])[0] or "")
            return self._json(HTTPStatus.OK, self.research_agent.chat_session_stats({"session_id": session_id}))
        if path == "/api/runs":
            params = parse_qs(query)
            view = str(params.get("view", ["active"])[0] or "active").strip().lower()
            archived = True if view == "archived" else None if view == "all" else False
            limit = min(200, max(1, int(params.get("limit", [50])[0] or 50)))
            return self._json(
                HTTPStatus.OK,
                {"runs": self.research_agent.store.list_runs(limit=limit, archived=archived), "view": view},
            )
        if path == "/api/runs/catalog":
            return self._json(HTTPStatus.OK, {"workflows": self.research_agent.workflow_catalog()})
        if path == "/api/tasks/registry":
            return self._json(HTTPStatus.OK, self.research_agent.task_registry())
        if path == "/api/agents/scientific":
            return self._json(HTTPStatus.OK, self.research_agent.scientific_agent_catalog())
        if path == "/api/slides/templates":
            return self._json(HTTPStatus.OK, list_slide_templates(self.slides_root))
        if path == "/api/app/update":
            return self._json(HTTPStatus.OK, self.update_service.status())

        parts = self._path_parts(path)
        if len(parts) == 4 and parts[:3] == ["api", "chat", "history"]:
            return self._json(HTTPStatus.OK, self.direct_chat_history.get(parts[3]))
        if len(parts) == 6 and parts[:3] == ["api", "slides", "templates"] and parts[4] == "pages":
            asset = slide_template_asset(parts[3], parts[5], self.slides_root)
            return WebResponse(HTTPStatus.OK, "image/svg+xml; charset=utf-8", asset.read_bytes())
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "events":
            params = parse_qs(query)
            after_sequence = max(0, int(params.get("after_sequence", [0])[0] or 0))
            limit = min(1000, max(1, int(params.get("limit", [200])[0] or 200)))
            return self._json(
                HTTPStatus.OK,
                self.research_agent.store.snapshot(parts[2], after_sequence=after_sequence, event_limit=limit),
            )
        if len(parts) == 3 and parts[:2] == ["api", "runs"]:
            params = parse_qs(query)
            if "after_sequence" in params:
                after_sequence = max(0, int(params.get("after_sequence", [0])[0] or 0))
                limit = min(1000, max(1, int(params.get("limit", [200])[0] or 200)))
                return self._json(
                    HTTPStatus.OK,
                    self.research_agent.store.snapshot(parts[2], after_sequence=after_sequence, event_limit=limit),
                )
            return self._json(HTTPStatus.OK, self.research_agent.store.get_run(parts[2]))
        if len(parts) == 6 and parts[:2] == ["api", "runs"] and parts[3] == "sources" and parts[5] == "reader":
            return self._task_evidence_reader(parts[2], parts[4], query=query)
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "download":
            return self._download_run_artifact(parts[2])
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "preview":
            return self._presentation_preview(parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "notebooks"]:
            return self._json(HTTPStatus.OK, self._notebook(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "citations":
            notebook = self._notebook(parts[2])
            return self._json(HTTPStatus.OK, {"notebook_id": notebook["notebook_id"], "citations": notebook["citations"]})
        if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "evidence-index":
            return self._json(
                HTTPStatus.OK,
                self._evidence_index_status(parts[2]),
            )
        if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "local-ai-status":
            return self._json(
                HTTPStatus.OK,
                self.research_agent.local_evidence_status(
                    notebook_id=parts[2],
                    quality_profile="precision",
                ),
            )
        if len(parts) == 4 and parts[:3] == ["api", "library", "import-jobs"]:
            return self._json(HTTPStatus.OK, self.library_imports.status(parts[3]))
        if len(parts) == 4 and parts[:2] == ["api", "sources"] and parts[3] == "reader":
            return self._source_reader(parts[2], query=query)
        if len(parts) == 4 and parts[:2] == ["api", "sources"] and parts[3] == "original":
            return self._source_original(parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "attachments"]:
            return self._attachment_asset(parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "audio-attachments"]:
            return self._audio_attachment_asset(parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "generated-images"]:
            image_path, content_type = generated_image_asset(self.workspace, parts[2])
            return WebResponse(HTTPStatus.OK, content_type, image_path.read_bytes())
        if len(parts) == 3 and parts[:2] == ["api", "presentations"]:
            requested = Path(parts[2]).name
            root = (self.workspace.parent / "presentations").resolve()
            candidate = (root / requested).resolve()
            if root not in candidate.parents or candidate.suffix.lower() != ".pptx" or not candidate.is_file():
                raise FileNotFoundError("Presentation does not exist")
            return WebResponse(
                HTTPStatus.OK,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                candidate.read_bytes(),
            )
        if len(parts) == 6 and parts[:2] == ["api", "ingestions"] and parts[3] == "sources":
            if parts[5] == "file":
                source_path = ingestion_source_path(self.workspace, parts[2], parts[4])
                content_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
                return WebResponse(HTTPStatus.OK, content_type, source_path.read_bytes())
            if parts[5] == "text":
                text = ingestion_source_text(self.workspace, parts[2], parts[4])
                return WebResponse(HTTPStatus.OK, "text/markdown; charset=utf-8", text.encode("utf-8"))
        if len(parts) >= 5 and parts[:2] == ["api", "sources"] and parts[3] == "files":
            return self._source_asset(parts[2], parts[4:])
        return self._json_error(HTTPStatus.NOT_FOUND, "not_found", "No route matches this path.")

    def _post(self, path: str, body: bytes) -> WebResponse:
        payload = self._json_body(body)
        parts = self._path_parts(path)
        if path == "/api/library/import-jobs":
            return self._json(HTTPStatus.ACCEPTED, self.library_imports.start(payload))
        if path == "/api/library/bind-folder":
            return self._json(HTTPStatus.CREATED, self._bind_library_folder(payload))
        if path == "/api/ask":
            return self._ask(payload)
        if path == "/api/audio/transcribe":
            return self._json(HTTPStatus.OK, self.research_agent.transcribe_audio(payload))
        if path == "/api/chat/attachments/stage":
            return self._json(HTTPStatus.OK, self.research_agent.stage_direct_attachments(payload))
        if path == "/api/chat/title":
            return self._json(HTTPStatus.OK, self.research_agent.generate_conversation_title(payload))
        if path == "/api/chat":
            return self._json(HTTPStatus.OK, self.research_agent.chat(payload))
        if path == "/api/chat/history":
            return self._json(HTTPStatus.OK, self.direct_chat_history.upsert(payload))
        if len(parts) == 5 and parts[:3] == ["api", "chat", "history"]:
            conversation_id, action = parts[3], parts[4]
            if action == "title":
                return self._json(
                    HTTPStatus.OK,
                    self.direct_chat_history.set_title(
                        conversation_id,
                        str(payload.get("title", "") or ""),
                        source=str(payload.get("title_source", "") or ""),
                    ),
                )
            if action == "archive":
                return self._json(HTTPStatus.OK, self.direct_chat_history.set_archived(conversation_id, True))
            if action == "restore":
                return self._json(HTTPStatus.OK, self.direct_chat_history.set_archived(conversation_id, False))
            if action == "delete":
                return self._json(HTTPStatus.OK, self.direct_chat_history.delete(conversation_id))
        if path == "/api/chat/compact":
            return self._json(HTTPStatus.OK, self.research_agent.compact_chat(payload))
        if path == "/api/chat/cancel":
            return self._json(HTTPStatus.OK, {"ok": self.research_agent.cancel_chat(payload)})
        if path == "/api/chat/pause":
            return self._json(HTTPStatus.OK, {"ok": self.research_agent.pause_chat(payload)})
        if path == "/api/chat/steer":
            return self._json(HTTPStatus.OK, self.research_agent.steer_chat(payload))
        if path == "/api/chat/follow-up":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.follow_up_chat(payload))
        if path == "/api/chat/interactions/respond":
            return self._json(HTTPStatus.OK, self.research_agent.respond_chat_interaction(payload))
        if len(parts) == 4 and parts[:2] == ["api", "chat"] and parts[2] == "session":
            self.research_agent.close_chat_session(parts[3])
            return self._json(HTTPStatus.OK, {"ok": True})
        if path == "/api/connectors/test":
            return self._json(HTTPStatus.OK, test_connector(self.workspace, str(payload.get("connector_kind", ""))))
        if path == "/api/notion/test":
            return self._json(HTTPStatus.OK, test_notion_connection(self.workspace, token=str(payload.get("token", "")).strip() or None))
        if path == "/api/notion/token":
            return self._json(HTTPStatus.OK, set_notion_api_token(self.workspace, str(payload.get("token", ""))))
        if path == "/api/ingestions":
            return self._json(
                HTTPStatus.CREATED,
                ingest_sources(
                    self.workspace,
                    payload.get("source_files", []),
                    parser=str(payload.get("parser", "auto")),
                ),
            )
        if path == "/api/diagnostics/export":
            bundle = export_diagnostics_bundle(self.workspace)
            return self._json(
                HTTPStatus.CREATED,
                {
                    "ok": True,
                    "local_only": True,
                    "download_url": "/api/diagnostics/bundle",
                    "download_name": bundle.name,
                },
            )
        if path == "/api/academic-search/plan":
            return self._json(HTTPStatus.OK, self.research_agent.preview_academic_search_plan(payload))
        if path == "/api/task-routing/preview":
            return self._json(HTTPStatus.OK, self.research_agent.preview_task_route(payload))
        if path == "/api/runs":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.start(payload))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "archive":
            return self._json(HTTPStatus.OK, self.research_agent.store.archive_run(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "restore":
            return self._json(HTTPStatus.OK, self.research_agent.store.restore_run(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "delete":
            return self._json(HTTPStatus.OK, self.research_agent.store.delete_run(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "messages":
            return self._json(HTTPStatus.OK, self.research_agent.continue_run_conversation(parts[2], payload))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "branch":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.branch_run(parts[2], payload))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "recover":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.recover_run(parts[2], payload))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "advisor-action":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.apply_advisor_action(parts[2], payload))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "interaction":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.respond_run_interaction(parts[2], payload))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "delegate":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.delegate_scientific_agents(parts[2], payload))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "collect-agents":
            return self._json(HTTPStatus.OK, self.research_agent.collect_scientific_agents(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "evidence-index":
            notebook_id = parts[2]
            install = self._ensure_retrieval_models(notebook_id)
            # Treat an omitted flag as the safe automatic path too.  Older
            # desktop clients posted an empty JSON body here; an empty body
            # must not unexpectedly turn a reusable cache into a GPU build.
            requested_mode = payload.get("auto", payload.get("automatic", True))
            automatic = bool(requested_mode) and not bool(payload.get("force", False))
            local_ai = (
                self.research_agent.local_evidence_status(notebook_id=notebook_id)
                if automatic
                else
                self.research_agent.prepare_local_evidence(
                    notebook_id=notebook_id,
                    quality_profile="precision",
                )
                if install.get("state") == "ready"
                else {
                    "notebook_id": str(notebook_id),
                    "state": "idle",
                    "phase": "idle",
                    "progress": 0.0,
                    "message": "检索组件尚未就绪，暂不加载本地 AI 模型",
                    "error": "",
                }
            )
            run = (
                self.research_agent.start_evidence_index(notebook_id, automatic=automatic)
                if install.get("state") == "ready" and local_ai.get("state") != "preparing"
                else None
            )
            return self._json(
                HTTPStatus.ACCEPTED,
                {
                    "run": run,
                    "model_install": install,
                    "local_ai": local_ai,
                    "status": self._evidence_index_status(notebook_id, install=install),
                },
            )
        if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "local-ai-status":
            install = self._ensure_retrieval_models(parts[2])
            result = (
                self.research_agent.prepare_local_evidence(
                    notebook_id=parts[2],
                    quality_profile=str(payload.get("quality_profile", "precision") or "precision"),
                    force=bool(payload.get("force", False)),
                )
                if install.get("state") == "ready"
                else {
                    "notebook_id": str(parts[2]),
                    "state": "idle",
                    "phase": "idle",
                    "progress": 0.0,
                    "message": "检索组件尚未就绪，暂不加载本地 AI 模型",
                    "error": "",
                }
            )
            return self._json(HTTPStatus.ACCEPTED, result)
        if path == "/api/settings":
            return self._save_settings(payload)
        if path == "/api/app/update/check":
            return self._json(HTTPStatus.OK, self.update_service.check())
        if path == "/api/skills/scan":
            return self._json(HTTPStatus.OK, scan_skill_source(self.workspace, payload))
        if path == "/api/skills/install":
            return self._json(HTTPStatus.CREATED, install_skill(self.workspace, payload))
        if path == "/api/skills/update/scan":
            return self._json(HTTPStatus.OK, scan_skill_update(self.workspace, payload))
        if path == "/api/skills/update":
            return self._json(HTTPStatus.OK, update_skill(self.workspace, payload))
        if path == "/api/skills/scan/cancel":
            return self._json(HTTPStatus.OK, cancel_skill_scan(self.workspace, str(payload.get("scan_id", ""))))
        if path == "/api/mcp/marketplace/sync":
            return self._json(HTTPStatus.OK, sync_official_registry(self.workspace))
        if path == "/api/mcp/marketplace/install":
            result = install_marketplace_server(self.workspace, str(payload.get("id", "")))
            return self._json(HTTPStatus.CREATED if result["created"] else HTTPStatus.OK, result)
        if path == "/api/mcp/marketplace/update":
            return self._json(HTTPStatus.OK, update_marketplace_server(self.workspace, str(payload.get("id", ""))))
        if path == "/api/mcp/test":
            server_id = str(payload.get("server_id", "")).strip()
            server = next(
                (
                    record
                    for record in list(load_settings(self.workspace).get("mcp_servers", []) or [])
                    if isinstance(record, dict) and str(record.get("id", "")) == server_id and not record.get("uninstalled")
                ),
                None,
            )
            if server is None:
                raise FileNotFoundError(f"MCP server does not exist: {server_id}")
            tested = PiAgentClient.probe_mcp_server(workspace=self.workspace, server=dict(server))
            # A fresh Node sidecar can occasionally finish its handshake with
            # an empty client list while the stdio child is still starting.
            # Retry once so the explicit connection test is useful instead of
            # reporting a transient "0 servers" result to the user.
            if not int(tested.get("server_count", 0) or 0):
                retry = PiAgentClient.probe_mcp_server(workspace=self.workspace, server=dict(server))
                if int(retry.get("server_count", 0) or 0) or int(retry.get("tool_count", 0) or 0):
                    tested = retry
            return self._json(
                HTTPStatus.OK,
                tested,
            )
        if path == "/api/library":
            title = str(payload.get("title", "")).strip()
            if not title:
                raise ValueError("知识库名称不能为空")
            if len(title) > 80:
                raise ValueError("知识库名称最多 80 个字符")
            created = initialize_notebook(
                self.workspace,
                notebook_id=f"kb_{uuid4().hex[:12]}",
                title=title,
                root_path=self.workspace.parent,
                metadata={"library_kind": "empty", "created_by": "user"},
            )
            summary = load_workspace_summary(self.workspace, notebook_id=str(created["notebook_id"]))
            notebook = next(iter(list(summary.get("notebooks", []) or [])), {})
            return self._json(
                HTTPStatus.CREATED,
                {"ok": True, "notebook": notebook, "workspace": load_workspace_summary(self.workspace)},
            )
        if len(parts) == 4 and parts[:2] == ["api", "library"] and parts[3] == "delete":
            notebook = self._notebook(parts[2])
            library_kind = str(dict(notebook.get("metadata", {}) or {}).get("library_kind", "folder"))
            if library_kind in {"zotero", "obsidian", "notion"}:
                raise ValueError("外部数据源请在其连接设置中管理，不能从个人知识库中移除")
            removed = delete_notebook(self.workspace, notebook_id=parts[2])
            index_removed = False
            index_path = notebook_evidence_db(self.evidence_db, parts[2])
            for candidate in (index_path, Path(f"{index_path}-wal"), Path(f"{index_path}-shm")):
                try:
                    if candidate.is_file():
                        candidate.unlink()
                        index_removed = True
                except OSError:
                    # The index is rebuildable.  Keep a locked cache for a later
                    # cleanup instead of failing a user-requested library removal.
                    pass
            return self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "removed": removed,
                    "index_removed": index_removed,
                    "workspace": load_workspace_summary(self.workspace),
                },
            )
        if path == "/api/library/folder":
            folder_path = str(payload.get("path", ""))
            notebook = self._requested_or_created_notebook(
                payload,
                title=Path(folder_path).name or "我的知识库",
                root_path=folder_path,
                library_kind=str(payload.get("library_kind", "folder")),
            )
            result = import_library_folder(
                self.workspace,
                self.evidence_db,
                notebook_id=str(notebook["notebook_id"]),
                folder_path=str(payload.get("path", "")),
                library_kind=str(payload.get("library_kind", "folder")),
            )
            return self._json(
                HTTPStatus.OK,
                self._with_evidence_index_run(result, notebook_id=str(notebook["notebook_id"])),
            )
        if path == "/api/library/zotero":
            zotero_path = str(payload.get("path", ""))
            notebook = self._requested_or_created_notebook(
                payload,
                title="Zotero 文献库",
                root_path=zotero_path,
                library_kind="zotero",
            )
            return self._json(
                HTTPStatus.OK,
                register_zotero_library(
                    self.workspace,
                    notebook_id=str(notebook["notebook_id"]),
                    folder_path=str(payload.get("path", "")),
                    evidence_db=self.evidence_db,
                ),
            )
        if path == "/api/library/zotero/status":
            data_dir = str(payload.get("data_dir", "") or "").strip()
            return self._json(
                HTTPStatus.OK,
                {"ok": True, "zotero": zotero_status(data_dir=data_dir or None, timeout=0.8)},
            )
        if path == "/api/library/zotero/local":
            notebook = self._requested_or_created_notebook(
                payload,
                title="Zotero 文献库",
                root_path=self.workspace.parent,
                library_kind="zotero",
            )
            data_dir = str(payload.get("data_dir", "") or "").strip()
            if not data_dir:
                data_dir = str(
                    dict(dict(notebook.get("metadata", {}) or {}).get("zotero", {}) or {}).get(
                        "configured_data_dir", ""
                    )
                    or ""
                ).strip()
            connect_kwargs = {
                "notebook_id": str(notebook["notebook_id"]),
                "evidence_db": self.evidence_db,
                "index_attachments": False,
            }
            if data_dir:
                connect_kwargs["data_dir"] = data_dir
            try:
                result = connect_local_zotero(self.workspace, **connect_kwargs)
            except RuntimeError as error:
                return self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "ok": False,
                        "error": {"code": "tool_failed", "message": str(error)},
                        "notebook": self._notebook(str(notebook["notebook_id"])),
                        "workspace": load_workspace_summary(self.workspace),
                    },
                )
            import_payload = {
                "library_kind": "zotero",
                "notebook_id": str(notebook["notebook_id"]),
            }
            if data_dir:
                import_payload["data_dir"] = data_dir
            result["import_job"] = self.library_imports.start(import_payload)
            return self._json(HTTPStatus.ACCEPTED, result)
        if path == "/api/library/notion":
            root_page_id = str(payload.get("root_page_id", "")).strip()
            notebook = self._requested_or_created_notebook(
                payload,
                title=str(payload.get("title", "Notion 知识库")).strip() or "Notion 知识库",
                root_path=self.workspace.parent,
                library_kind="notion",
            )
            token = str(payload.get("token", "")).strip() or None
            if token:
                set_notion_api_token(self.workspace, token)
            result = sync_notion_library(
                self.workspace,
                self.evidence_db,
                notebook_id=str(notebook["notebook_id"]),
                root_page_id=root_page_id,
                title=str(payload.get("title", "Notion 知识库")),
                token=token,
            )
            return self._json(
                HTTPStatus.OK,
                self._with_evidence_index_run(result, notebook_id=str(notebook["notebook_id"])),
            )
        if path == "/api/library/files":
            paths = payload.get("paths")
            if not isinstance(paths, list):
                raise ValueError("paths must be a list")
            first_parent = Path(str(paths[0])).parent if paths else self.workspace.parent
            notebook = self._requested_or_created_notebook(
                payload,
                title="我的知识库",
                root_path=first_parent,
                library_kind="files",
            )
            notebook_id = str(notebook["notebook_id"])
            normalized_paths = [str(path) for path in paths]
            first_link = not self._local_binding_paths(notebook) and not int(
                dict(notebook.get("counts", {}) or {}).get("sources", 0) or 0
            )
            display_root = first_parent if first_link else notebook.get("root_path")
            if first_link:
                set_notebook_root_path(
                    self.workspace,
                    notebook_id=notebook_id,
                    root_path=first_parent,
                    metadata={"library_kind": "files"},
                )
            else:
                update_notebook_metadata(
                    self.workspace,
                    notebook_id=notebook_id,
                    metadata={"library_kind": "files"},
                )
            self._set_local_bindings(
                notebook_id,
                normalized_paths,
                library_kind="files",
                index_state="queued",
                binding_kind="file",
            )
            try:
                result = import_library_files(
                    self.workspace,
                    self.evidence_db,
                    notebook_id=notebook_id,
                    file_paths=normalized_paths,
                    root_path=display_root,
                )
            except Exception as error:
                self._set_local_bindings(
                    notebook_id,
                    normalized_paths,
                    library_kind="files",
                    index_state="failed",
                    error=str(error)[:500],
                    binding_kind="file",
                )
                raise
            self._set_local_bindings(
                notebook_id,
                normalized_paths,
                library_kind="files",
                index_state="ready",
                binding_kind="file",
            )
            result["workspace"] = load_workspace_summary(self.workspace)
            result["notebook"] = self._notebook(notebook_id)
            return self._json(
                HTTPStatus.OK,
                self._with_evidence_index_run(result, notebook_id=notebook_id),
            )
        if path == "/api/library/uploads":
            files = payload.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("files must be a non-empty list")
            if len(files) > 12:
                raise ValueError("单次最多拖入 12 个文件")
            notebook_id = str(payload.get("notebook_id", "")).strip()
            if not notebook_id:
                raise ValueError("请先创建或选择一个知识库")
            notebook = self._notebook(notebook_id)
            total_bytes = 0
            with TemporaryDirectory(prefix="scansci-library-drop-") as temporary:
                temporary_root = Path(temporary)
                paths: list[str] = []
                for index, item in enumerate(files):
                    record = dict(item or {}) if isinstance(item, dict) else {}
                    name = Path(str(record.get("name", ""))).name
                    data_url = str(record.get("data_url", ""))
                    if not name or "," not in data_url:
                        raise ValueError("拖入的文件数据不完整")
                    try:
                        content = base64.b64decode(data_url.split(",", 1)[1], validate=True)
                    except Exception as error:
                        raise ValueError(f"无法读取文件：{name}") from error
                    total_bytes += len(content)
                    if total_bytes > 120 * 1024 * 1024:
                        raise ValueError("拖入文件总大小不能超过 120 MB")
                    target = temporary_root / name
                    if target.exists():
                        target = temporary_root / f"{target.stem}-{index + 1}{target.suffix}"
                    target.write_bytes(content)
                    paths.append(str(target))
                result = import_library_files(
                    self.workspace,
                    self.evidence_db,
                    notebook_id=str(notebook["notebook_id"]),
                    file_paths=paths,
                )
            return self._json(
                HTTPStatus.OK,
                self._with_evidence_index_run(result, notebook_id=str(notebook["notebook_id"])),
            )
        if len(parts) == 5 and parts[:3] == ["api", "settings", "providers"] and parts[4] == "api-key":
            return self._set_provider_api_key(parts[3], payload)
        if len(parts) == 6 and parts[:3] == ["api", "settings", "providers"] and parts[4:] == ["api-key", "reveal"]:
            return self._reveal_provider_api_key(parts[3], payload)
        if len(parts) == 5 and parts[:3] == ["api", "settings", "providers"] and parts[4] == "test":
            return self._test_provider(parts[3])
        if len(parts) == 5 and parts[:3] == ["api", "settings", "providers"] and parts[4] == "models":
            return self._fetch_provider_models(parts[3])
        if len(parts) == 5 and parts[:3] == ["api", "settings", "local-models"] and parts[4] == "test":
            return self._test_local_model(parts[3])
        if len(parts) == 5 and parts[:3] == ["api", "settings", "document-processing"] and parts[4] == "api-key":
            return self._set_document_service_api_key(parts[3], payload)
        if path == "/api/tools/journals/search":
            return self._json(HTTPStatus.OK, search_journals(str(payload.get("query", "")), limit=int(payload.get("limit", 8))))
        if path == "/api/tools/references/analyze":
            return self._json(HTTPStatus.OK, analyze_references(str(payload.get("text", "")), mode=str(payload.get("mode", "full"))))
        if path == "/api/tools/references/verify-doi":
            return self._json(
                HTTPStatus.OK,
                verify_doi_metadata(str(payload.get("doi", "")), expected_title=str(payload.get("title", ""))),
            )
        if path == "/api/local-models/delete":
            model_id = str(payload.get("id", "")).strip()
            return self._json(HTTPStatus.OK, delete_installed_model(model_id))
        if path == "/api/local-models/recheck":
            return self._recheck_local_model(str(payload.get("id", "")).strip())
        if path == "/api/local-models/download":
            repo_id = str(payload.get("id", ""))
            runtime = str(payload.get("runtime", "") or "").strip().lower()
            if runtime == "ollama" or repo_id == OLLAMA_VISION_MODEL:
                ollama = self._ollama_status()
                if not ollama.get("reachable"):
                    return self._json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": {
                                "code": "ollama_unavailable",
                                "message": "请先安装并启动 Ollama；ScanSci 不会替用户静默安装第三方运行时。",
                            },
                            "ollama": ollama,
                        },
                    )
                install = self.ollama_installs.start(
                    OLLAMA_VISION_MODEL,
                    job_id=f"ollama:{OLLAMA_VISION_MODEL}",
                    on_complete=lambda _job: self._enable_installed_local_model("ollama-minicpm-v4.6"),
                )
                return self._json(HTTPStatus.ACCEPTED, install)
            blocked = self._audio_model_download_requires_runtime() if repo_id == QWEN3_ASR_NATIVE_MODEL_ID else self._model_download_requires_runtime()
            if blocked is not None:
                return blocked
            on_complete = self._connect_downloaded_local_models
            if repo_id == QWEN3_ASR_NATIVE_MODEL_ID:
                previous = on_complete

                def on_complete(job: dict[str, Any]) -> None:
                    previous(job)
                    self._enable_installed_local_model("qwen3-asr-0.6b")

            elif repo_id in {DEFAULT_LOCAL_EMBEDDING_MODEL, DEFAULT_LOCAL_RERANKER_MODEL}:
                # Either model may be downloaded independently from the new
                # resource page. Once both are present, start any pending
                # semantic indexes just as the legacy combined download did.
                previous = on_complete

                def on_complete(job: dict[str, Any]) -> None:
                    previous(job)
                    self._after_retrieval_models_ready()
            install = self.model_installs.start(
                [repo_id],
                job_id=f"model:{repo_id}",
                source=str(payload.get("source", "auto") or "auto"),
                on_complete=on_complete,
            )
            return self._json(HTTPStatus.ACCEPTED, install)
        if path == "/api/settings/document-processing/ocr/install":
            languages = payload.get("languages")
            if not isinstance(languages, list):
                languages = ["zh", "en"]
            return self._json(HTTPStatus.ACCEPTED, self.tesseract_installs.start(languages))
        if path == "/api/local-models/install-control":
            job_id = str(payload.get("job_id", "") or "").strip()
            action = str(payload.get("action", "") or "").strip().lower()
            actions = (
                {
                    "pause": self.ollama_installs.pause,
                    "resume": self.ollama_installs.resume,
                    "retry": self.ollama_installs.retry,
                    "cancel": self.ollama_installs.cancel,
                }
                if job_id.startswith("ollama:")
                else {
                    "pause": self.model_installs.pause,
                    "resume": self.model_installs.resume,
                    "retry": self.model_installs.retry,
                    "cancel": self.model_installs.cancel,
                }
            )
            if action not in actions:
                raise ValueError("下载控制 action 必须是 pause、resume、retry 或 cancel")
            return self._json(HTTPStatus.ACCEPTED, actions[action](job_id))
        if path == "/api/resources/retrieval/download":
            blocked = self._model_download_requires_runtime()
            if blocked is not None:
                return blocked
            install = self.model_installs.start(
                [DEFAULT_LOCAL_EMBEDDING_MODEL, DEFAULT_LOCAL_RERANKER_MODEL],
                job_id="retrieval-core",
                source="auto",
                on_complete=lambda _job: self._after_retrieval_models_ready(),
            )
            return self._json(HTTPStatus.ACCEPTED, install)
        if path == "/api/local-runtime/install-control":
            action = str(payload.get("action", "") or "").strip().lower()
            actions = {
                "pause": self.local_runtime.pause_install,
                "resume": self.local_runtime.resume_install,
                "retry": self.local_runtime.retry_install,
                "cancel": self.local_runtime.cancel_install,
            }
            if action not in actions:
                raise ValueError("本地运行组件控制 action 必须是 pause、resume、retry 或 cancel")
            return self._json(HTTPStatus.ACCEPTED, actions[action]())
        if path == "/api/local-runtime/install":
            return self._json(HTTPStatus.ACCEPTED, self.local_runtime.start_install())
        if path == "/api/local-runtime/install-local":
            paths = payload.get("paths")
            if not isinstance(paths, list) or not paths:
                raise ValueError("请选择本地运行组件 ZIP，以及可选的 JSON 清单或分片文件。")
            return self._json(HTTPStatus.ACCEPTED, self.local_runtime.start_local_install(paths))
        if path == "/api/runtime-components/install":
            component = self._runtime_component(str(payload.get("component", "")))
            return self._json(HTTPStatus.ACCEPTED, component.start_install())
        if path == "/api/runtime-components/install-control":
            component = self._runtime_component(str(payload.get("component", "")))
            action = str(payload.get("action", "") or "").strip().lower()
            actions = {
                "pause": component.pause_install,
                "resume": component.resume_install,
                "retry": component.retry_install,
                "cancel": component.cancel_install,
            }
            if action not in actions:
                raise ValueError("运行组件控制 action 必须是 pause、resume、retry 或 cancel")
            return self._json(HTTPStatus.ACCEPTED, actions[action]())
        if path == "/api/runtime-components/install-local":
            component = self._runtime_component(str(payload.get("component", "")))
            paths = payload.get("paths")
            if not isinstance(paths, list) or not paths:
                raise ValueError("请选择运行组件 ZIP，以及可选的 JSON 清单或分片文件。")
            return self._json(HTTPStatus.ACCEPTED, component.start_local_install(paths))
        if path == "/api/tools/paper-atlas/search":
            return self._json(HTTPStatus.OK, search_paper_atlas(str(payload.get("query", ""))))
        if path == "/api/tools/papers/download":
            result = download_paper(
                str(payload.get("identifier", "")),
                workspace=self.workspace,
                strategy=str(payload.get("strategy", "oa_first")),
            )
            files = [str(path) for path in list(result.get("files", []) or []) if str(path).strip()]
            notebook_id = str(payload.get("notebook_id", "")).strip()
            if not notebook_id:
                notebooks = list(load_workspace_summary(self.workspace).get("notebooks", []) or [])
                notebook_id = str(dict(notebooks[0]).get("notebook_id", "")) if notebooks else ""
            if files and notebook_id:
                try:
                    result["imported"] = import_library_files(
                        self.workspace,
                        self.evidence_db,
                        notebook_id=notebook_id,
                        file_paths=files,
                    )
                    result["evidence_status"] = "indexed_fulltext"
                    result = self._with_evidence_index_run(result, notebook_id=notebook_id)
                except Exception as error:  # keep a successful download usable even if indexing fails
                    result["evidence_status"] = "downloaded_unindexed"
                    result["evidence_error"] = f"{type(error).__name__}: {error}"[:500]
            elif files:
                result["evidence_status"] = "downloaded_unindexed"
            return self._json(HTTPStatus.OK, result)
        if path == "/api/studio/ppt/outline":
            notebook = self._requested_notebook(payload)
            return self._json(
                HTTPStatus.OK,
                build_ppt_outline(
                    notebook,
                    topic=str(payload.get("topic", "")),
                    template_id=str(payload.get("template_id", "")),
                ),
            )
        if path == "/api/studio/ppt/project":
            notebook = self._requested_notebook(payload)
            return self._json(
                HTTPStatus.CREATED,
                create_ppt_project(
                    notebook,
                    workspace=self.workspace,
                    topic=str(payload.get("topic", "")),
                    template_id=str(payload.get("template_id", "")),
                ),
            )
        if path == "/api/studio/ppt/rendered":
            return self._json(
                HTTPStatus.CREATED,
                save_browser_rendered_deck(
                    self.workspace,
                    file_name=str(payload.get("file_name", "")),
                    base64_data=str(payload.get("base64", "")),
                ),
            )
        if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "notes":
            return self._add_note(parts[2], payload)
        if len(parts) == 4 and parts[:2] == ["api", "citations"] and parts[3] == "audits":
            return self._record_audit(parts[2], payload)
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "cancel":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.cancel(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "pause":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.pause(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "resume":
            return self._json(HTTPStatus.ACCEPTED, self.research_agent.resume(parts[2]))
        return self._json_error(HTTPStatus.NOT_FOUND, "not_found", "No route matches this path.")

    def _ask(self, payload: dict[str, Any]) -> WebResponse:
        return self._json(HTTPStatus.OK, self.research_agent.answer_sync(payload))

    def stream_chat(self, payload: dict[str, Any]):
        """Expose direct-chat deltas to the loopback HTTP handler."""

        return self.research_agent.chat_stream(payload)

    def _attachment_asset(self, attachment_id: str) -> WebResponse:
        path, content_type = attachment_asset(self.workspace, attachment_id)
        return WebResponse(HTTPStatus.OK, content_type, path.read_bytes())

    def _audio_attachment_asset(self, attachment_id: str) -> WebResponse:
        path, content_type = audio_attachment_asset(self.workspace, attachment_id)
        return WebResponse(HTTPStatus.OK, content_type, path.read_bytes())

    def _download_run_artifact(self, run_id: str) -> WebResponse:
        """Serve a PPTX or project archive produced by a durable local run."""

        run = self.research_agent.store.get_run(run_id)
        artifact = dict(run.get("output_artifact", {}) or {})
        payload = dict(artifact.get("payload", {}) or {})
        presentations = (self.workspace.parent / "presentations").resolve()
        candidate = Path(str(artifact.get("file_path", "") or "")).resolve()
        if candidate.suffix.casefold() == ".pptx" and presentations in candidate.parents and candidate.is_file():
            return WebResponse(
                HTTPStatus.OK,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                candidate.read_bytes(),
            )
        if artifact.get("artifact_type") != "slide_deck_project":
            raise FileNotFoundError("这个任务没有可下载的文件")

        project = Path(str(payload.get("project_path") or artifact.get("file_path") or "")).resolve()
        if presentations not in project.parents or not project.is_dir():
            raise FileNotFoundError("这个任务没有可下载的演示项目")
        files = [
            path for path in project.rglob("*")
            if not path.is_symlink() and path.is_file() and project in path.resolve().parents
        ]
        if not files:
            raise FileNotFoundError("演示项目中没有可下载的文件")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in files:
                bundle.write(path, (Path(project.name) / path.relative_to(project)).as_posix())
        return WebResponse(HTTPStatus.OK, "application/zip", archive.getvalue())

    def _presentation_preview(self, run_id: str) -> WebResponse:
        """Return a lightweight cover preview for a completed source-to-PPT run.

        The cover uses the same title and colours as the generated deck.  This
        is intentionally local SVG rather than an untrusted document preview,
        so it works consistently in both the browser preview and the packaged
        desktop WebView.
        """

        run = self.research_agent.store.get_run(run_id)
        artifact = dict(run.get("output_artifact", {}) or {})
        payload = dict(artifact.get("payload", {}) or {})
        candidate = Path(str(artifact.get("file_path", "") or "")).resolve()
        presentations = (self.workspace.parent / "presentations").resolve()
        if candidate.suffix.casefold() != ".pptx" or presentations not in candidate.parents or not candidate.is_file():
            raise FileNotFoundError("这个任务没有可预览的 PPTX 文件")
        outline = dict(payload.get("outline", {}) or {})
        plan = dict(payload.get("slide_plan", {}) or {})
        theme = dict(plan.get("theme", {}) or {})
        cover = str(theme.get("cover") or "091B2A").lstrip("#")[:6]
        accent = str(theme.get("accent") or "14897D").lstrip("#")[:6]
        title = html_escape(str(outline.get("title") or run.get("title") or "科研幻灯片")[:80])
        subtitle = html_escape(str(outline.get("central_question") or "基于上传材料生成的可编辑演示文稿")[:112])
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" role="img" aria-label="{title}">
<rect width="1280" height="720" fill="#{cover}"/><rect x="82" y="80" width="154" height="8" rx="4" fill="#{accent}"/>
<circle cx="1124" cy="125" r="76" fill="none" stroke="#{accent}" stroke-width="5" opacity=".85"/>
<text x="84" y="250" fill="#ffffff" font-family="Microsoft YaHei, Arial, sans-serif" font-size="54" font-weight="700">{title}</text>
<text x="86" y="350" fill="#cfe8e6" font-family="Microsoft YaHei, Arial, sans-serif" font-size="25">{subtitle}</text>
<text x="86" y="650" fill="#a7bec7" font-family="Microsoft YaHei, Arial, sans-serif" font-size="18">ScanSci Presentation Studio · 可编辑 PPTX</text></svg>'''
        return WebResponse(HTTPStatus.OK, "image/svg+xml; charset=utf-8", svg.encode("utf-8"))

    def _save_settings(self, payload: dict[str, Any]) -> WebResponse:
        settings = payload.get("settings", payload)
        previous_settings = load_settings(self.workspace)
        previous_runtime_root = self.local_runtime.root
        saved = save_settings(self.workspace, settings)
        previous_vector_root = vector_index_directory(previous_settings)
        next_vector_root = vector_index_directory(saved)
        vector_migration: dict[str, Any] = {
            "migrated": 0,
            "source_root": "",
            "destination_root": "",
            "updated_workspace_paths": 0,
        }
        if previous_vector_root.casefold() != next_vector_root.casefold():
            try:
                vector_migration = migrate_vector_indexes(
                    workspace=self.workspace,
                    evidence_db=self.evidence_db,
                    old_root=previous_vector_root or None,
                    new_root=next_vector_root or None,
                )
            except Exception as error:
                # The old index files were never removed, so restoring the
                # settings file is enough to keep the running app consistent.
                save_settings(self.workspace, previous_settings)
                apply_storage_directories(previous_settings)
                raise ValueError(f"向量索引迁移失败，已保留原目录：{error}") from error
        applied = apply_storage_directories(saved)
        # Managers are intentionally recreated only while idle.  This makes a
        # directory selected during first-run immediately useful, without
        # interrupting an in-progress download; the next app launch always
        # applies the persisted paths before creating managers.
        try:
            runtime_job = self.local_runtime.install_status()
            if (
                previous_runtime_root != LocalRuntimeComponent(
                    fallback_manifest_url=self.update_service.manifest_url or None
                ).root
                and not runtime_job.get("active")
                and self.local_runtime.executable() is None
            ):
                self.local_runtime = LocalRuntimeComponent(
                    fallback_manifest_url=self.update_service.manifest_url or None
                )
            install_status = self.model_installs.status()
            if not install_status.get("active"):
                self.model_installs = create_install_manager()
        except Exception:
            pass
        return self._json(
            HTTPStatus.OK,
            {
                **saved,
                "storage": {
                    "applied": applied,
                    "vector_index_migration": vector_migration,
                    "restart_required_for_running_process": bool(self.local_runtime.executable()),
                },
            },
        )

    def _set_provider_api_key(self, provider_id: str, payload: dict[str, Any]) -> WebResponse:
        settings = set_provider_api_key(self.workspace, provider_id, str(payload.get("api_key", "")))
        return self._json(HTTPStatus.OK, settings)

    def _reveal_provider_api_key(self, provider_id: str, payload: dict[str, Any]) -> WebResponse:
        """Return a saved provider key only after an explicit local UI action.

        Provider secrets remain absent from the normal settings contract.  The
        desktop UI calls this POST route only when the person clicks the eye
        control; all HTTP responses are already marked ``Cache-Control:
        no-store`` by the loopback server.
        """

        if payload.get("reveal") is not True:
            raise ValueError("需要明确确认后才能显示 API 密钥")
        settings = load_settings(self.workspace)
        provider = next((item for item in settings.get("providers", []) if item.get("id") == provider_id), None)
        if provider is None:
            raise FileNotFoundError("找不到模型提供商")
        if provider.get("kind") == "local" or provider.get("auth_mode") == "managed":
            raise ValueError("这个模型服务没有可显示的本机 API 密钥")
        secret = get_provider_api_key(self.workspace, provider_id)
        if not secret:
            raise FileNotFoundError("尚未保存 API 密钥")
        return self._json(HTTPStatus.OK, {"api_key": secret})

    def _set_document_service_api_key(self, service_id: str, payload: dict[str, Any]) -> WebResponse:
        settings = set_document_service_api_key(self.workspace, service_id, str(payload.get("api_key", "")))
        return self._json(HTTPStatus.OK, settings)

    def _test_provider(self, provider_id: str) -> WebResponse:
        settings = load_settings(self.workspace)
        provider = next((item for item in settings.get("providers", []) if item.get("id") == provider_id), None)
        if provider is None:
            raise FileNotFoundError("找不到模型提供商")
        return self._json(
            HTTPStatus.OK,
            test_provider_connection(workspace=self.workspace, provider=dict(provider)),
        )

    def _fetch_provider_models(self, provider_id: str) -> WebResponse:
        settings = load_settings(self.workspace)
        provider = next((item for item in settings.get("providers", []) if item.get("id") == provider_id), None)
        if provider is None:
            raise FileNotFoundError("找不到模型提供商")
        return self._json(
            HTTPStatus.OK,
            fetch_provider_models(workspace=self.workspace, provider=dict(provider)),
        )

    def _test_local_model(self, local_model_id: str) -> WebResponse:
        settings = load_settings(self.workspace)
        local_model = next((item for item in settings.get("local_models", []) if item.get("id") == local_model_id), None)
        if local_model is None:
            raise FileNotFoundError("找不到本地模型")
        return self._json(HTTPStatus.OK, test_local_model_connection(dict(local_model)))

    def _requested_notebook(self, payload: dict[str, Any]) -> dict[str, Any]:
        notebook_id = str(payload.get("notebook_id", "")).strip()
        if notebook_id:
            return self._notebook(notebook_id)
        summary = load_workspace_summary(self.workspace)
        notebooks = list(summary.get("notebooks", []) or [])
        if not notebooks:
            raise FileNotFoundError("当前工作区没有可用资料库")
        return dict(notebooks[0])

    def _run_library_import_job(self, payload: dict[str, Any], report: Any) -> dict[str, Any]:
        """Create or reuse a source container, then run its local import task."""

        folder_path = str(payload.get("path", "") or "").strip()
        library_kind = str(payload.get("library_kind", "folder") or "folder").strip().lower()
        merge_existing = bool(payload.get("merge_existing"))
        if library_kind == "zotero":
            notebook = self._requested_or_created_notebook(
                payload,
                title="Zotero 文献库",
                root_path=self.workspace.parent,
                library_kind="zotero",
            )
            notebook_id = str(notebook["notebook_id"])
            report(
                {
                    "phase": "正在读取 Zotero 文献",
                    "detail": "元数据已显示，正在后台建立可检索全文",
                    "progress": 0.08,
                }
            )
            data_dir = str(payload.get("data_dir", "") or "").strip()
            connect_kwargs = {
                "notebook_id": notebook_id,
                "evidence_db": self.evidence_db,
                "index_attachments": False,
            }
            if data_dir:
                connect_kwargs["data_dir"] = data_dir
            metadata_result = connect_local_zotero(self.workspace, **connect_kwargs)
            zotero_state = dict(metadata_result.get("zotero", {}) or {})
            evidence_index = index_zotero_attachments(
                self.workspace,
                self.evidence_db,
                notebook_id=notebook_id,
                zotero_state=zotero_state,
                progress=report,
            )
            zotero_state["evidence_index"] = evidence_index
            update_notebook_metadata(
                self.workspace,
                notebook_id=notebook_id,
                metadata={"library_kind": "zotero", "zotero": zotero_state},
            )
            summary = load_workspace_summary(self.workspace, notebook_id=notebook_id)
            refreshed_notebook = next(iter(list(summary.get("notebooks", []) or [])), {})
            report(
                {
                    "phase": "已完成 Zotero 全文索引",
                    "detail": "文献元数据与可追溯证据已同步到 ScanSci",
                    "progress": 0.93,
                }
            )
            return self._with_evidence_index_run(
                {
                    "ok": True,
                    "zotero": zotero_state,
                    "notebook": refreshed_notebook,
                    "workspace": load_workspace_summary(self.workspace),
                },
                notebook_id=notebook_id,
            )
        notebook = self._requested_or_created_notebook(
            payload,
            title=Path(folder_path).name or ("Obsidian 知识库" if library_kind == "obsidian" else "我的知识库"),
            root_path=folder_path,
            library_kind=library_kind,
        )
        notebook_id = str(notebook["notebook_id"])
        try:
            result = import_library_folder(
                self.workspace,
                self.evidence_db,
                notebook_id=notebook_id,
                folder_path=folder_path,
                library_kind=library_kind,
                progress=report,
                merge_existing=merge_existing,
                root_path_override=notebook.get("root_path") if merge_existing else None,
            )
        except Exception as error:
            # Binding is intentionally durable even when indexing is not.  A
            # user should never have to select the same folder again merely
            # because one parser or OCR pass failed.
            self._set_local_bindings(
                notebook_id,
                [folder_path],
                library_kind=library_kind,
                index_state="failed",
                error=str(error)[:500],
                binding_kind="folder",
            )
            raise
        self._set_local_bindings(
            notebook_id,
            [folder_path],
            library_kind=library_kind,
            index_state="ready",
            binding_kind="folder",
        )
        result["workspace"] = load_workspace_summary(self.workspace)
        result["notebook"] = self._notebook(notebook_id)
        return self._with_evidence_index_run(result, notebook_id=notebook_id)

    def _bind_library_folder(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a local-folder connection before its expensive scan begins.

        Folder selection is a connection decision, while parsing, evidence
        materialisation and semantic indexing are recoverable background work.
        Keeping those concerns apart prevents a long import from making the
        selected source look as if it was never connected.
        """

        library_kind = str(payload.get("library_kind", "folder") or "folder").strip().lower()
        if library_kind not in {"folder", "obsidian"}:
            raise ValueError("文件夹绑定仅支持本地文件夹或 Obsidian Vault")
        raw_path = str(payload.get("path", "") or "").strip()
        if not raw_path:
            raise ValueError("请选择要绑定的文件夹")
        folder = Path(raw_path).expanduser().resolve()
        if not folder.is_dir():
            raise FileNotFoundError(f"文件夹不存在：{folder}")

        title = folder.name or ("Obsidian 知识库" if library_kind == "obsidian" else "我的知识库")
        notebook = self._requested_or_created_notebook(
            payload,
            title=title,
            root_path=folder,
            library_kind=library_kind,
        )
        notebook_id = str(notebook["notebook_id"])
        explicit_notebook = bool(str(payload.get("notebook_id", "") or "").strip())
        first_link = not self._local_binding_paths(notebook) and not int(
            dict(notebook.get("counts", {}) or {}).get("sources", 0) or 0
        )
        if first_link:
            set_notebook_root_path(
                self.workspace,
                notebook_id=notebook_id,
                root_path=folder,
                metadata={"library_kind": library_kind},
            )
        else:
            update_notebook_metadata(
                self.workspace,
                notebook_id=notebook_id,
                metadata={"library_kind": library_kind},
            )
        self._set_local_bindings(
            notebook_id,
            [folder],
            library_kind=library_kind,
            index_state="queued",
            binding_kind="folder",
        )
        import_job = self.library_imports.start(
            {
                **dict(payload),
                "path": str(folder),
                "library_kind": library_kind,
                "notebook_id": notebook_id,
                # Supplying a notebook id means this is an addition to the
                # selected personal knowledge base, not a new library card.
                "merge_existing": explicit_notebook,
            }
        )
        summary = load_workspace_summary(self.workspace)
        return {
            "ok": True,
            "bound": True,
            "notebook": self._notebook(notebook_id),
            "workspace": summary,
            "import_job": import_job,
            # Keep the immediate binding response easy to consume by both the
            # desktop UI and integrations that poll jobs directly.
            "job_id": import_job["job_id"],
            "job_state": import_job["state"],
            "progress": import_job["progress"],
        }

    def _with_evidence_index_run(self, result: dict[str, Any], *, notebook_id: str) -> dict[str, Any]:
        """Report import readiness without loading optional retrieval models."""

        payload = dict(result)
        install = self._ensure_retrieval_models(notebook_id)
        payload["model_install"] = install
        if install.get("state") == "ready":
            try:
                local_ai = self.research_agent.local_evidence_status(
                    notebook_id=notebook_id,
                    quality_profile="precision",
                )
            except FileNotFoundError:
                # A stale import response may refer to a notebook removed by
                # another session. Do not turn that bookkeeping race into a
                # failed library response.
                local_ai = {
                    "notebook_id": str(notebook_id),
                    "state": "idle",
                    "phase": "idle",
                    "progress": 0.0,
                    "message": "知识库尚未准备本地 AI 模型",
                    "error": "",
                }
        else:
            local_ai = {
                "notebook_id": str(notebook_id),
                "state": "idle",
                "phase": "idle",
                "progress": 0.0,
                "message": "检索组件尚未就绪，暂不加载本地 AI 模型",
                "error": "",
            }
        payload["local_ai"] = local_ai
        run = (
            self.research_agent.start_evidence_index(notebook_id, automatic=True)
            if install.get("state") == "ready" and local_ai.get("state") != "preparing"
            else None
        )
        if run is not None:
            payload["index_run"] = run
        return payload

    def _ensure_retrieval_models(self, notebook_id: str) -> dict[str, Any]:
        """Describe retrieval readiness; never download models as an import side effect."""

        current = self.research_agent.evidence_index_status(str(notebook_id))
        if int(current.get("total", 0) or 0) <= 0:
            return {
                "job_id": "retrieval-core",
                "state": "idle",
                "reason": "empty_library",
                "progress": 0.0,
                "models": [
                    DEFAULT_LOCAL_EMBEDDING_MODEL,
                    DEFAULT_LOCAL_RERANKER_MODEL,
                ],
            }
        runtime = self.local_runtime.status()
        models = [DEFAULT_LOCAL_EMBEDDING_MODEL, DEFAULT_LOCAL_RERANKER_MODEL]
        if not bool(runtime.get("installed")):
            return {
                "job_id": "retrieval-core",
                "state": "blocked",
                "reason": "runtime_required",
                "progress": 0.0,
                "models": models,
                "local_runtime": runtime,
            }
        ready = {str(item.get("id", "")) for item in installed_models() if bool(item.get("ready"))}
        if set(models).issubset(ready):
            return {
                "job_id": "retrieval-core",
                "state": "ready",
                "reason": "installed",
                "progress": 1.0,
                "models": models,
            }
        current_job = self.model_installs.status("retrieval-core")
        if str(current_job.get("state", "idle")) in {"queued", "downloading", "failed"}:
            return current_job
        return {
            "job_id": "retrieval-core",
            "state": "idle",
            "reason": "not_requested",
            "progress": 0.0,
            "models": models,
        }

    def _model_download_requires_runtime(self) -> WebResponse | None:
        """Refuse model downloads that the current lightweight app cannot execute."""

        runtime = self.local_runtime.status()
        if bool(runtime.get("installed")):
            return None
        next_action = "install_runtime_component" if bool(runtime.get("install_available")) else "configure_local_runtime"
        return self._json(
            HTTPStatus.CONFLICT,
            {
                "error": {
                    "code": "local_runtime_required",
                    "message": "ScanSci 未开始下载模型。本地模型市场的权重需要 ScanSci 运行组件；请先安装受信任的运行组件。自动清单不可用时，可从官方发布页下载组件 ZIP 并在本地安装；也可连接外部运行时使用已有模型。",
                },
                "local_runtime": runtime,
                "next_action": next_action,
                "fallback_action": "manual_install_runtime" if not bool(runtime.get("install_available")) else "",
            },
        )

    def _recheck_local_model(self, model_id: str) -> WebResponse:
        """Retry an installed chat/vision snapshot after a transient probe failure."""

        wanted = str(model_id or "").strip()
        record = next(
            (item for item in installed_models() if str(item.get("id", "")).strip() == wanted),
            None,
        )
        if not isinstance(record, dict):
            return self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "local_model_not_found", "message": "没有找到这个本地模型。"}},
            )
        capabilities = {str(value).strip().lower() for value in list(record.get("capabilities", []) or [])}
        kind = str(record.get("kind", "")).strip().lower()
        if str(record.get("format", "")).strip().lower() != "transformers" or not (
            kind == "vision" or capabilities.intersection({"chat", "vision"})
        ):
            return self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "local_model_not_probeable", "message": "这个本地模型不支持对话/视觉预检。"}},
            )
        if not bool(record.get("model_files_present", record.get("ready"))):
            return self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "local_model_incomplete", "message": "模型文件尚未完整下载，暂时不能检测。"}},
            )
        try:
            ensure_local_transformers_runtime(wanted)
        except Exception as error:  # noqa: BLE001 - surface the actionable loader error to the UI
            return self._json(
                HTTPStatus.CONFLICT,
                {
                    "error": {
                        "code": "model_probe_failed",
                        "message": str(error)[:1200],
                    },
                    "model": next(
                        (item for item in installed_models() if str(item.get("id", "")).strip() == wanted),
                        record,
                    ),
                },
            )
        refreshed = next(
            (item for item in installed_models() if str(item.get("id", "")).strip() == wanted),
            record,
        )
        return self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "model": refreshed,
                "message": "本地模型已重新检测并可用。",
            },
        )

    def _audio_model_download_requires_runtime(self) -> WebResponse | None:
        """Do not offer Qwen ASR where the desktop cannot execute it."""

        runtime = self.local_runtime.status()
        if str(runtime.get("mode", "")) in {"source", "embedded", "component"}:
            return None
        return self._json(
            HTTPStatus.CONFLICT,
            {
                "error": {
                    "code": "local_asr_runtime_required",
                    "message": "Qwen3 ASR 需要带 Transformers/PyTorch 的 ScanSci 完整本地运行能力；当前轻量版不会下载无法执行的语音模型。",
                },
                "local_runtime": runtime,
                "next_action": "install_full_package",
            },
        )

    def _after_retrieval_models_ready(self, notebook_id: str = "") -> None:
        """Build pending semantic indexes only after an explicit, verified install."""

        if not bool(self.local_runtime.status().get("installed")):
            return
        ready_ids = {
            str(item.get("id", ""))
            for item in installed_models()
            if bool(item.get("ready"))
        }
        if not {DEFAULT_LOCAL_EMBEDDING_MODEL, DEFAULT_LOCAL_RERANKER_MODEL}.issubset(ready_ids):
            return
        if notebook_id:
            notebook_ids = [str(notebook_id)]
        else:
            notebook_ids = [
                str(row.get("notebook_id", ""))
                for row in list(load_workspace_summary(self.workspace).get("notebooks", []) or [])
                if str(row.get("notebook_id", ""))
            ]
        for target_id in notebook_ids:
            try:
                current = self.research_agent.evidence_index_status(target_id)
                if int(current.get("total", 0) or 0) <= 0:
                    continue
                with self._retrieval_setup_lock:
                    self._retrieval_setup_errors.pop(target_id, None)
                self.research_agent.start_evidence_index(target_id)
            except Exception as error:  # one library must not block the others
                with self._retrieval_setup_lock:
                    self._retrieval_setup_errors[target_id] = f"{type(error).__name__}: {error}"[:1200]

    def _evidence_index_status(
        self,
        notebook_id: str,
        *,
        install: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Combine vector, model-download, and runtime readiness truthfully."""

        status = dict(self.research_agent.evidence_index_status(notebook_id))
        status["local_ai"] = self.research_agent.local_evidence_status(
            notebook_id=notebook_id,
            quality_profile="precision",
        )
        model_install = dict(install or self.model_installs.status("retrieval-core"))
        runtime = self.local_runtime.status()
        with self._retrieval_setup_lock:
            setup_error = self._retrieval_setup_errors.get(str(notebook_id), "")
        status["model_install"] = model_install
        status["local_runtime"] = runtime
        install_state = str(model_install.get("state", "idle"))
        if bool(status.get("ready")):
            return status
        if install_state in {"queued", "downloading"}:
            status.update(
                {
                    "state": "installing",
                    "progress": float(model_install.get("progress", 0.0) or 0.0),
                    "error": "",
                    "message": "正在通过国内 ModelScope 安装高质量检索组件",
                }
            )
        elif install_state == "failed":
            status.update(
                {
                    "state": "degraded",
                    "error": str(model_install.get("error", "")),
                    "message": "高质量检索组件安装失败，基础关键词检索仍可使用",
                }
            )
        elif install_state == "blocked":
            status.update(
                {
                    "state": "waiting_for_runtime",
                    "progress": 0.0,
                    "error": "",
                    "message": "请先配置本地运行时；模型尚未下载，基础关键词检索仍可使用",
                }
            )
        elif setup_error:
            status.update(
                {
                    "state": "degraded",
                    "error": setup_error,
                    "message": "模型已下载，但本地推理运行时尚未就绪",
                }
            )
        elif install_state == "ready" and not bool(runtime.get("installed")):
            status.update(
                {
                    "state": "degraded",
                    "error": "本地推理运行时尚未安装",
                    "message": "模型已下载，但本地推理运行时尚未就绪",
                }
            )
        elif install_state == "idle":
            status.update(
                {
                    "state": "optional",
                    "progress": 0.0,
                    "error": "",
                    "message": "基础关键词检索可用；配置运行时后可按需安装语义检索组件",
                }
            )
        return status

    def _requested_or_created_notebook(
        self,
        payload: dict[str, Any],
        *,
        title: str,
        root_path: str | Path,
        library_kind: str,
    ) -> dict[str, Any]:
        """Resolve an explicit library or create a separate library card."""

        notebook_id = str(payload.get("notebook_id", "")).strip()
        if notebook_id:
            return self._notebook(notebook_id)
        created = initialize_notebook(
            self.workspace,
            title=title or "我的知识库",
            root_path=root_path or self.workspace.parent,
            metadata={"library_kind": library_kind, "created_by": "library_import"},
        )
        return self._notebook(str(created["notebook_id"]))

    def _local_binding_paths(self, notebook: dict[str, Any]) -> list[str]:
        metadata = dict(notebook.get("metadata", {}) or {})
        raw_bindings = metadata.get("local_bindings")
        bindings = list(raw_bindings) if isinstance(raw_bindings, list) else []
        if not bindings and isinstance(metadata.get("local_binding"), dict):
            bindings = [metadata["local_binding"]]
        return [
            str(dict(item).get("source_path", "") or "").strip()
            for item in bindings
            if isinstance(item, dict) and str(dict(item).get("source_path", "") or "").strip()
        ]

    def _set_local_bindings(
        self,
        notebook_id: str,
        paths: list[str | Path],
        *,
        library_kind: str,
        index_state: str,
        error: str = "",
        binding_kind: str | None = None,
    ) -> dict[str, Any]:
        """Append or update source links without replacing the other links."""

        notebook = self._notebook(notebook_id)
        metadata = dict(notebook.get("metadata", {}) or {})
        raw_bindings = metadata.get("local_bindings")
        bindings = [dict(item) for item in raw_bindings if isinstance(item, dict)] if isinstance(raw_bindings, list) else []
        legacy = metadata.get("local_binding")
        if not bindings and isinstance(legacy, dict) and legacy.get("source_path"):
            bindings = [{**dict(legacy), "kind": "folder"}]
        last_record: dict[str, Any] | None = None
        for raw_path in paths:
            source_path = str(Path(str(raw_path)).expanduser().resolve())
            record = {
                "state": "bound",
                "index_state": index_state,
                "source_path": source_path,
                "error": error,
            }
            entry = {**record, "kind": binding_kind or ("file" if Path(source_path).suffix else "folder")}
            bindings = [
                item
                for item in bindings
                if str(item.get("source_path", "") or "") != source_path
            ]
            bindings.append(entry)
            last_record = record
        if last_record is None:
            return notebook
        metadata["local_bindings"] = bindings
        # Keep the old single-binding field for existing clients and old
        # workspaces; the list is the authoritative multi-source view.
        metadata["local_binding"] = last_record
        metadata["library_kind"] = library_kind
        update_notebook_metadata(self.workspace, notebook_id=notebook_id, metadata=metadata)
        return self._notebook(notebook_id)

    def _add_note(self, notebook_id: str, payload: dict[str, Any]) -> WebResponse:
        title = str(payload.get("title", "")).strip()
        body = str(payload.get("body", "")).strip()
        if not title:
            raise ValueError("title is required")
        if not body:
            raise ValueError("body is required")
        folder_path = str(payload.get("folder_path", "") or "").strip()
        new_folder_name = _safe_note_folder_name(str(payload.get("new_folder_name", "") or ""))
        destination_folder: Path | None = None
        destination_file: Path | None = None
        note_metadata = dict(payload.get("metadata", {}) or {}) if isinstance(payload.get("metadata"), dict) else {}
        if new_folder_name and not folder_path:
            raise ValueError("新建文件夹前请先选择上级文件夹")
        if folder_path:
            destination_folder = Path(folder_path).expanduser()
            if not destination_folder.is_absolute():
                raise ValueError("保存文件夹必须使用绝对路径")
            destination_folder = destination_folder.resolve()
            if not destination_folder.is_dir():
                raise FileNotFoundError(f"保存文件夹不存在：{destination_folder}")
            if new_folder_name:
                destination_folder = destination_folder / new_folder_name
                destination_folder.mkdir(parents=False, exist_ok=True)
                if not destination_folder.is_dir():
                    raise ValueError(f"无法创建保存文件夹：{destination_folder}")
            stem = _safe_note_file_stem(title)
            destination_file = destination_folder / f"{stem}.md"
            if destination_file.exists():
                index = 2
                while True:
                    candidate = destination_folder / f"{stem}-{index}.md"
                    if not candidate.exists():
                        destination_file = candidate
                        break
                    index += 1
            destination_file.write_text(body + "\n", encoding="utf-8")
            note_metadata.update({
                "storage": "markdown",
                "folder_path": str(destination_folder),
                "file_path": str(destination_file),
            })
        note = add_note_to_notebook(
            self.workspace,
            notebook_id=notebook_id,
            title=title,
            body=body,
            note_type=str(payload.get("note_type", "research_note") or "research_note"),
            source_path=str(destination_file or ""),
            metadata=note_metadata,
        )
        result: dict[str, Any] = {"note": note, "notebook": self._notebook(notebook_id)}
        if destination_folder is not None and destination_file is not None:
            result["destination"] = {
                "folder_path": str(destination_folder),
                "file_path": str(destination_file),
            }
        return self._json(HTTPStatus.CREATED, result)

    def _record_audit(self, citation_record_id: str, payload: dict[str, Any]) -> WebResponse:
        verdict = str(payload.get("verdict", "")).strip()
        if verdict not in {"supported", "partial_support", "unsupported", "needs_human_review"}:
            raise ValueError("verdict must be supported, partial_support, unsupported, or needs_human_review")
        confidence = payload.get("confidence")
        if confidence not in {None, ""}:
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError) as error:
                raise ValueError("confidence must be a number between 0 and 1") from error
        else:
            confidence = None
        audit = record_citation_audit(
            self.workspace,
            citation_record_id=citation_record_id,
            provider="human-notebook-review",
            verdict=verdict,
            reasoning=str(payload.get("reasoning", "")).strip(),
            confidence=confidence,
        )
        return self._json(HTTPStatus.CREATED, {"audit": audit})

    def _notebook(self, notebook_id: str) -> dict[str, Any]:
        summary = load_workspace_summary(self.workspace, notebook_id=notebook_id)
        notebooks = list(summary.get("notebooks", []) or [])
        if not notebooks:
            raise FileNotFoundError(f"Notebook does not exist: {notebook_id}")
        return dict(notebooks[0])

    def _source_reader(self, doc_id: str, *, query: str = "") -> WebResponse:
        source = self._source(doc_id)
        source_path = self._source_html_path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"Source HTML does not exist: {source_path}")
        text = source_path.read_text(encoding="utf-8", errors="replace")
        if source_path.suffix.lower() in {".md", ".markdown", ".txt"}:
            return WebResponse(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                _decorate_evidence_reader_html(self._markdown_source_reader(doc_id, source, text), query).encode("utf-8"),
                _EMBEDDED_SOURCE_CONTENT_SECURITY_POLICY,
            )
        base = f'<base href="/api/sources/{doc_id}/files/">'
        if re.search(r"<head\b[^>]*>", text, flags=re.IGNORECASE):
            text = re.sub(r"(<head\b[^>]*>)", r"\1" + base, text, count=1, flags=re.IGNORECASE)
        else:
            text = base + text
        return WebResponse(
            HTTPStatus.OK,
            "text/html; charset=utf-8",
            _decorate_evidence_reader_html(text, query).encode("utf-8"),
            _EMBEDDED_SOURCE_CONTENT_SECURITY_POLICY,
        )

    def _task_evidence_reader(self, run_id: str, doc_id: str, *, query: str = "") -> WebResponse:
        """Serve only task-scoped Deep Research evidence, never a user library."""

        run = self.research_agent.store.get_run(run_id)
        if str(run.get("workflow_type", "")) != "deep_research":
            raise FileNotFoundError("Task evidence is only available for Deep Research runs")
        acquisition = next(
            (
                dict(stage.get("output", {}) or {})
                for stage in list(run.get("stages", []) or [])
                if str(dict(stage).get("key", "")) == "acquire"
            ),
            {},
        )
        task_evidence = dict(acquisition.get("task_evidence", {}) or {})
        source_path = task_evidence_reader_path(
            self.workspace,
            run_id,
            str(task_evidence.get("evidence_db", "") or ""),
            doc_id,
        )
        text = source_path.read_text(encoding="utf-8", errors="replace")
        return WebResponse(
            HTTPStatus.OK,
            "text/html; charset=utf-8",
            _decorate_evidence_reader_html(text, query).encode("utf-8"),
            _EMBEDDED_SOURCE_CONTENT_SECURITY_POLICY,
        )

    def _markdown_source_reader(self, doc_id: str, source: dict[str, Any], full_text: str) -> str:
        """Render exact evidence anchors plus the complete converted source text."""

        rows: list[sqlite3.Row] = []
        source_db = Path(str(source.get("evidence_db_path", "") or ""))
        if not source_db.is_file():
            source_db = self.evidence_db
        if source_db.is_file():
            try:
                with sqlite3.connect(source_db) as connection:
                    connection.row_factory = sqlite3.Row
                    rows = list(
                        connection.execute(
                            """
                            select evidence_id, html_anchor, section, block_id, text
                            from evidence_spans
                            where doc_id = ?
                            order by sentence_index
                            """,
                            (doc_id,),
                        )
                    )
            except sqlite3.Error:
                rows = []
        blocks: list[str] = []
        last_section = None
        for row in rows:
            section = str(row["section"] or "正文")
            if section != last_section:
                blocks.append(f"<h2>{html_escape(section)}</h2>")
                last_section = section
            blocks.append(
                '<article class="evidence-block" '
                f'id="{html_escape(str(row["html_anchor"] or ""))}" '
                f'data-evidence-id="{html_escape(str(row["evidence_id"] or ""))}">'
                f'<span>{html_escape(str(row["evidence_id"] or ""))}</span>'
                f'<p>{html_escape(str(row["text"] or ""))}</p></article>'
            )
        title = html_escape(str(source.get("title") or doc_id))
        evidence = "".join(blocks) or '<p class="empty">这份来源尚未生成可定位证据块。</p>'
        return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
:root{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#2d3035;background:#fff}}body{{margin:0}}header{{position:sticky;top:0;z-index:3;padding:18px 28px 15px;border-bottom:1px solid #e7e8eb;background:rgba(255,255,255,.96);backdrop-filter:blur(14px)}}header span{{color:#96989f;font-size:11px;letter-spacing:.08em}}header h1{{margin:4px 0 0;font-size:20px;line-height:1.4}}main{{max-width:920px;margin:auto;padding:24px 30px 70px}}h2{{margin:28px 0 10px;color:#565960;font-size:14px}}.evidence-block{{scroll-margin-top:94px;margin:0 0 8px;padding:13px 16px;border:1px solid transparent;border-radius:10px;transition:background .18s,border-color .18s,box-shadow .18s}}.evidence-block>span{{display:block;margin-bottom:4px;color:#a0a2a8;font:10px ui-monospace,SFMono-Regular,Consolas,monospace}}.evidence-block p{{margin:0;font-size:15px;line-height:1.85}}.evidence-block:target{{border-color:#e7cb68;background:#fff8cf;box-shadow:0 0 0 4px rgba(225,190,66,.14)}}details{{margin-top:34px;border-top:1px solid #e7e8eb;padding-top:18px}}summary{{color:#707279;font-size:13px;cursor:pointer}}pre{{padding:18px;overflow:auto;border-radius:10px;background:#f7f7f8;white-space:pre-wrap;font:13px/1.75 ui-monospace,SFMono-Regular,Consolas,monospace}}.empty{{color:#92949a}}</style></head><body><header><span>SCANSCI · EVIDENCE READER</span><h1>{title}</h1></header><main>{evidence}<details><summary>查看完整转换文本</summary><pre>{html_escape(full_text)}</pre></details></main></body></html>'''

    def _source_asset(self, doc_id: str, asset_parts: list[str]) -> WebResponse:
        source = self._source(doc_id)
        root = self._source_html_path(source).parent.resolve()
        requested = root.joinpath(*asset_parts).resolve()
        try:
            requested.relative_to(root)
        except ValueError as error:
            raise ValueError("Source asset path escapes the source directory") from error
        if not requested.is_file():
            raise FileNotFoundError(f"Source asset does not exist: {requested.name}")
        content_type, _ = mimetypes.guess_type(str(requested))
        return WebResponse(HTTPStatus.OK, content_type or "application/octet-stream", requested.read_bytes())

    def _source_original(self, doc_id: str) -> WebResponse:
        """Serve only the exact local source path already registered in the workspace."""

        source = self._source(doc_id)
        original = Path(str(source.get("source_url", "") or ""))
        if not original.is_absolute() or not original.is_file():
            raise FileNotFoundError("This source does not have a readable original file")
        content_type, _ = mimetypes.guess_type(str(original))
        return WebResponse(
            HTTPStatus.OK,
            content_type or "application/octet-stream",
            original.read_bytes(),
            _EMBEDDED_SOURCE_CONTENT_SECURITY_POLICY,
        )

    def _source(self, doc_id: str) -> dict[str, Any]:
        summary = load_workspace_summary(self.workspace)
        for notebook in list(summary.get("notebooks", []) or []):
            for source in list(dict(notebook).get("sources", []) or []):
                if str(dict(source).get("doc_id", "")) == doc_id:
                    return dict(source)
        raise FileNotFoundError(f"Source does not exist in the workspace: {doc_id}")

    @staticmethod
    def _source_html_path(source: dict[str, Any]) -> Path:
        evidence_value = str(source.get("evidence_html_path", "") or "")
        evidence_path = Path(evidence_value) if evidence_value else None
        if evidence_path is not None and evidence_path.exists():
            return evidence_path
        return Path(str(source.get("html_path", "") or ""))

    @staticmethod
    def _path_parts(path: str) -> list[str]:
        return [unquote(part) for part in path.split("/") if part]

    @staticmethod
    def _reader_url(doc_id: str, anchor: str) -> str:
        suffix = f"#{anchor}" if anchor else ""
        return f"/api/sources/{quote(doc_id, safe='')}/reader{suffix}"

    @staticmethod
    def _validated_site_url(value: str) -> str:
        """Accept only public HTTP(S) URLs for the favicon resolver."""

        parsed = urlparse(str(value or "").strip())
        hostname = (parsed.hostname or "").strip().casefold()
        if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
            raise ValueError("Site icon URLs must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("Site icon URLs cannot contain credentials")
        if hostname in _SITE_ICON_LOCAL_HOSTS or hostname.endswith((".local", ".localhost", ".internal")):
            raise ValueError("Local and internal site icon URLs are not allowed")
        try:
            address = ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("Private and loopback site icon URLs are not allowed")
        return parsed.geturl()

    @staticmethod
    def _site_icon_origin(value: str) -> str:
        parsed = urlparse(value)
        port = parsed.port
        default_port = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
        netloc = parsed.hostname or ""
        if ":" in netloc and not netloc.startswith("["):
            netloc = f"[{netloc}]"
        if port and not default_port:
            netloc = f"{netloc}:{port}"
        return f"{parsed.scheme}://{netloc}"

    @staticmethod
    def _site_icon_type(url: str, content_type: str, body: bytes) -> str | None:
        normalized = str(content_type or "").split(";", 1)[0].strip().casefold()
        if normalized in _SITE_ICON_CONTENT_TYPES:
            return normalized
        guessed = mimetypes.guess_type(url)[0]
        if guessed in _SITE_ICON_CONTENT_TYPES:
            return guessed
        sample = body.lstrip()[:32]
        if body.startswith(b"\\x89PNG"):
            return "image/png"
        if body.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if body.startswith(b"\\xff\\xd8\\xff"):
            return "image/jpeg"
        if body.startswith((b"RIFF",)) and body[8:12] == b"WEBP":
            return "image/webp"
        if body.startswith(b"\\x00\\x00\\x01\\x00"):
            return "image/x-icon"
        if sample.startswith(b"<svg") or sample.startswith(b"<?xml") and b"<svg" in body[:256]:
            return "image/svg+xml"
        return None

    @staticmethod
    def _site_icon_links(html: bytes, page_url: str) -> list[str]:
        """Extract icon candidates without executing or fully parsing a page."""

        text = html.decode("utf-8", errors="ignore")
        candidates: list[str] = []
        for tag in re.findall(r"<link\b[^>]*>", text, flags=re.IGNORECASE):
            rel_match = re.search(r"\brel\s*=\s*(['\"])(.*?)\1", tag, flags=re.IGNORECASE | re.DOTALL)
            href_match = re.search(r"\bhref\s*=\s*(['\"])(.*?)\1", tag, flags=re.IGNORECASE | re.DOTALL)
            if not rel_match or not href_match:
                continue
            rel = {part.casefold() for part in re.split(r"\s+", rel_match.group(2).strip())}
            if "icon" not in rel and "shortcut" not in rel:
                continue
            href = href_match.group(2).strip()
            if not href or href.startswith("data:"):
                continue
            try:
                candidate = NotebookWebApp._validated_site_url(urljoin(page_url, href))
            except ValueError:
                continue
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    @classmethod
    def _read_site_resource(cls, url: str, max_bytes: int) -> tuple[str, str, bytes] | None:
        validated = cls._validated_site_url(url)
        request = Request(
            validated,
            headers={
                "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/apng,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
                "User-Agent": "ScanSci/0.2 (+local site icon resolver)",
            },
        )
        with urlopen(request, timeout=_SITE_ICON_TIMEOUT_SECONDS) as response:
            final_url = cls._validated_site_url(response.geturl() or validated)
            content_type = str(response.headers.get("Content-Type", "") or "")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                return None
            return final_url, content_type, body

    def _site_icon(self, query: str) -> WebResponse:
        requested = str(parse_qs(query).get("url", [""])[0] or "").strip()
        validated = self._validated_site_url(requested)
        cache_key = self._site_icon_origin(validated)
        now = time.monotonic()
        with self._site_icon_cache_lock:
            cached = self._site_icon_cache.get(cache_key)
            if cached and now - cached[0] < _SITE_ICON_CACHE_TTL_SECONDS:
                return WebResponse(
                    HTTPStatus.OK,
                    cached[1],
                    cached[2],
                    cache_control="public, max-age=86400",
                )

        origin = cache_key
        candidates = [validated]
        try:
            page = self._read_site_resource(validated, _SITE_PAGE_MAX_BYTES)
        except Exception:
            page = None
        if page is not None:
            page_url, page_content_type, page_body = page
            if page_content_type.casefold().split(";", 1)[0].strip() in {"text/html", "application/xhtml+xml", ""}:
                candidates = self._site_icon_links(page_body, page_url) + candidates
        candidates.extend(
            [
                f"{origin}/favicon.ico",
                f"{origin}/favicon.png",
                f"{origin}/apple-touch-icon.png",
            ]
        )

        seen: set[str] = set()
        for candidate in candidates:
            try:
                candidate = self._validated_site_url(candidate)
            except ValueError:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                fetched = self._read_site_resource(candidate, _SITE_ICON_MAX_BYTES)
            except Exception:
                continue
            if fetched is None:
                continue
            final_url, content_type, body = fetched
            icon_type = self._site_icon_type(final_url, content_type, body)
            if not icon_type:
                continue
            with self._site_icon_cache_lock:
                self._site_icon_cache[cache_key] = (time.monotonic(), icon_type, body)
            return WebResponse(
                HTTPStatus.OK,
                icon_type,
                body,
                cache_control="public, max-age=86400",
            )
        raise FileNotFoundError("Site icon could not be resolved")

    @staticmethod
    def _json_body(body: bytes) -> dict[str, Any]:
        if len(body) > _MAX_REQUEST_BYTES:
            raise ValueError("Request body is too large")
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be UTF-8 JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    @staticmethod
    def _json(status: int | HTTPStatus, payload: dict[str, Any]) -> WebResponse:
        return WebResponse(int(status), "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))

    @classmethod
    def _json_error(cls, status: int | HTTPStatus, code: str, message: str) -> WebResponse:
        return cls._json(status, {"error": {"code": code, "message": message}})

    @staticmethod
    def _static_asset(name: str, content_type: str) -> WebResponse:
        path = _ASSET_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"Notebook application asset does not exist: {name}")
        return WebResponse(HTTPStatus.OK, content_type, path.read_bytes())

    @staticmethod
    def _static_tree_asset(name: str, content_type: str) -> WebResponse:
        root = _ASSET_DIR.resolve()
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(f"Notebook application asset does not exist: {name}")
        return WebResponse(HTTPStatus.OK, content_type, path.read_bytes())


def serve_notebook(
    *,
    workspace: str | Path,
    evidence_db: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """Start the local Notebook UI until interrupted by the user."""

    server = create_notebook_server(
        workspace=workspace,
        evidence_db=evidence_db,
        host=host,
        port=port,
    )
    url = f"http://{host}:{server.server_port}"
    print(f"ScanSci Notebook is running at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return None
    finally:
        server.server_close()


def create_notebook_server(
    *,
    workspace: str | Path,
    evidence_db: str | Path,
    host: str = "127.0.0.1",
    port: int = 0,
    update_service: AppUpdateService | None = None,
) -> ThreadingHTTPServer:
    """Create a loopback-only HTTP server for the Notebook UI.

    The desktop shell uses this factory with an ephemeral port and owns the
    server lifecycle.  Keeping this boundary explicit means the same UI works
    in a browser for development and in a native desktop window for daily use.
    """

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ScanSci Notebook only binds to a loopback host; this local UI has no multi-user authentication.")
    app = NotebookWebApp(workspace=workspace, evidence_db=evidence_db, update_service=update_service)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            self._dispatch()

        def _dispatch(self) -> None:
            rejection = self._request_boundary_rejection()
            if rejection is not None:
                self._write_response(rejection)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > _MAX_REQUEST_BYTES:
                response = NotebookWebApp._json_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large", "Request body is too large")
                self._write_response(response)
                return
            body = self.rfile.read(max(0, length))
            if self.command == "POST" and urlparse(self.path).path == "/api/chat/stream":
                self._stream_chat(body)
                return
            response = app.dispatch(self.command, self.path, body)
            self._write_response(response)

        def _request_boundary_rejection(self) -> WebResponse | None:
            """Block DNS rebinding and cross-site POSTs against the loopback API."""

            host_header = str(self.headers.get("Host", "") or "").strip()
            if host_header:
                host_name = (urlparse(f"//{host_header}").hostname or "").casefold()
                if host_name not in {"127.0.0.1", "localhost", "::1"}:
                    return NotebookWebApp._json_error(
                        HTTPStatus.FORBIDDEN,
                        "invalid_host",
                        "The ScanSci local API accepts only loopback Host headers.",
                    )
            if self.command != "POST":
                return None
            origin = str(self.headers.get("Origin", "") or "").strip()
            if not origin:
                # Native desktop and local automation clients do not always
                # send Origin. A browser cross-site request does.
                return None
            try:
                parsed = urlparse(origin)
                origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError:
                parsed = urlparse("")
                origin_port = -1
            if (
                parsed.scheme != "http"
                or (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}
                or origin_port != int(self.server.server_port)
            ):
                return NotebookWebApp._json_error(
                    HTTPStatus.FORBIDDEN,
                    "cross_origin_request",
                    "Cross-origin writes to the ScanSci local API are not allowed.",
                )
            return None

        def _write_response(self, response: WebResponse) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                response.content_security_policy,
            )
            self.send_header("Cache-Control", response.cache_control)
            self.end_headers()
            self.wfile.write(response.body)

        def _stream_chat(self, body: bytes) -> None:
            try:
                payload = NotebookWebApp._json_body(body)
            except ValueError as error:
                self._write_response(NotebookWebApp._json_error(HTTPStatus.BAD_REQUEST, "invalid_request", str(error)))
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.close_connection = True
            with diagnostic_span(
                app.workspace,
                "scansci.chat.stream",
                {
                    "chat.message_count": len(list(payload.get("messages", []) or [])),
                    "chat.attachment_count": len(list(payload.get("source_files", []) or [])),
                    "chat.image_count": len(list(payload.get("images", []) or [])),
                },
            ) as span:
                try:
                    self._write_sse("ready", {})
                    last_event = "ready"
                    for event in app.stream_chat(payload):
                        event_type = str(event.get("type", "message"))
                        event_payload = {str(key): value for key, value in event.items() if key != "type"}
                        self._write_sse(event_type, event_payload)
                        last_event = event_type
                    span.set_attribute("chat.terminal_event", last_event)
                except (BrokenPipeError, ConnectionResetError):
                    span.set_attribute("chat.client_disconnected", True)
                    return
                except (ValueError, RuntimeError) as error:
                    span.record_exception(error)
                    payload = {"message": str(error)}
                    failure = getattr(error, "failure", None)
                    if isinstance(failure, dict) and failure:
                        payload["failure"] = dict(failure)
                    self._write_sse("error", payload)
                except Exception as error:  # noqa: BLE001 - prevent closing an SSE response without a client event
                    span.record_exception(error)
                    self._write_sse("error", {"message": "The streaming response could not be completed."})

        def _write_sse(self, event_type: str, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
            frame = f"event: {event_type}\ndata: {encoded}\n\n".encode("utf-8")
            self.wfile.write(frame)
            self.wfile.flush()

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    return ThreadingHTTPServer((host, int(port)), Handler)
