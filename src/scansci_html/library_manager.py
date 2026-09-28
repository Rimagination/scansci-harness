"""Manage user-selected local literature folders for the desktop workbench."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from functools import wraps
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from statistics import median
import time
from tempfile import TemporaryDirectory
from threading import RLock
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from .evidence_store import (
    _identity_key_from_values,
    _initialize_schema as _initialize_evidence_schema,
    _is_page_heading,
    _record_document_identity_alias,
    _split_markdown_front_matter,
    build_library_overview,
    index_evidence_library,
    index_markdown_library,
)
from .evidence_doctor import assess_evidence_structure
from .ingestion import SUPPORTED_INGESTION_SUFFIXES, extract_local_document
from .source_filters import is_ignored_library_path
from .vector_storage import vector_library_root
from .vector_index import migrate_embedding_caches
from .workspace import (
    load_workspace_summary,
    set_notebook_root_path,
    sync_sources_from_evidence_store,
    update_notebook_metadata,
)
from .zotero_scope import sync_zotero_document_tags


_MAX_LIBRARY_DOCUMENTS = 2_000
_MIN_EVIDENCE_DB_BYTES = 1_000_000
_HTML_SUFFIXES = {".html"}
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_SUPPORTED_SUFFIXES = _HTML_SUFFIXES | _MARKDOWN_SUFFIXES
_IMPORTABLE_SUFFIXES = set(SUPPORTED_INGESTION_SUFFIXES)
_LIBRARY_IMPORTABLE_SUFFIXES = {
    ".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".markdown", ".txt", ".rtf", ".epub",
}

ImportProgress = Callable[[dict[str, Any]], None]

# ponytail: imports still serialize process-wide; use per-library RLocks if
# parallel imports of different notebooks become necessary.
_IMPORT_LOCK = RLock()
_ACTIVE_IMPORTS: set[Path] = set()


def _serial_library_import(function):
    @wraps(function)
    def import_serially(workspace, evidence_db, *, notebook_id, **kwargs):
        with _IMPORT_LOCK:
            root = Path(workspace).resolve().parent / ".scansci-library" / _safe_folder_name(notebook_id)
            # Folder/file imports call each other while building one generation.
            if root in _ACTIVE_IMPORTS:
                return function(workspace, evidence_db, notebook_id=notebook_id, **kwargs)
            with _library_process_lock(root):
                _ACTIVE_IMPORTS.add(root)
                try:
                    return function(workspace, evidence_db, notebook_id=notebook_id, **kwargs)
                finally:
                    _ACTIVE_IMPORTS.remove(root)
    return import_serially


@contextmanager
def _library_process_lock(root: Path):
    """Use the runtime installer's OS-lock pattern, scoped to one library.

    Keep the lock file in place: unlinking it can let another process lock a
    different inode. The OS releases the lock even if an importing process dies.
    """

    root.mkdir(parents=True, exist_ok=True)
    with (root / ".import.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("另一个 ScanSci 进程正在导入此知识库，请等待导入完成后重试。") from error
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _report_import_progress(
    callback: ImportProgress | None,
    *,
    phase: str,
    progress: float,
    detail: str = "",
) -> None:
    """Report an honest import milestone without coupling parsing to the UI."""

    if callback is None:
        return
    try:
        callback(
            {
                "phase": phase,
                "progress": max(0.0, min(1.0, float(progress))),
                "detail": detail,
            }
        )
    except Exception:
        # Progress is informative only. A stale browser must never prevent the
        # source documents from being indexed.
        return


def _refresh_zotero_tag_index(
    evidence_db: str | Path,
    zotero_items: Iterable[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Sync Zotero tags without invalidating parsed evidence or vectors."""

    tag_index = sync_zotero_document_tags(evidence_db, zotero_items)
    if tag_index.get("status") == "ready":
        try:
            tag_index["library_overview"] = build_library_overview(evidence_db)
        except (OSError, sqlite3.Error, ValueError) as error:
            tag_index["overview_error"] = f"{type(error).__name__}: {error}"[:300]
    return tag_index


def _quality_snapshot(quality: dict[str, Any]) -> dict[str, Any]:
    """Keep the durable workspace summary compact and free of source excerpts."""

    return {
        key: quality.get(key)
        for key in (
            "passed",
            "documents",
            "sections",
            "spans",
            "missing_structure_spans",
            "oversized_spans",
            "source_text_mismatches",
            "orphan_sections",
            "reference_spans",
        )
    } | {"warning_count": len(list(quality.get("warnings", []) or []))}


def _count_reusable_vectors(db_path: str | Path) -> int:
    """Count existing cache rows whose digest still matches current evidence.

    This is deliberately independent of sqlite-vec: importing a library must
    be able to report reuse even when the vector extension is unavailable at
    that moment.  A changed source row fails the digest check and is excluded.
    """

    path = Path(db_path)
    if not path.is_file():
        return 0
    try:
        with sqlite3.connect(path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table' and name in ('evidence_spans', 'scansci_vector_cache_meta')"
                )
            }
            if {"evidence_spans", "scansci_vector_cache_meta"} - tables:
                return 0
            reusable_ids: set[str] = set()
            for evidence_id, text, stored_digest in connection.execute(
                """
                select evidence_id, text, text_sha256
                from evidence_spans
                join scansci_vector_cache_meta using (evidence_id)
                """
            ):
                digest = sha256(str(text or "").encode("utf-8")).hexdigest()
                if digest == str(stored_digest or ""):
                    reusable_ids.add(str(evidence_id))
            return len(reusable_ids)
    except sqlite3.Error:
        return 0


def notebook_evidence_db(evidence_db: str | Path, notebook_id: str) -> Path:
    """Return the isolated evidence index owned by one knowledge library."""

    root = vector_library_root(evidence_db)
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{_safe_folder_name(notebook_id)}.sqlite"


@_serial_library_import
def import_library_folder(
    workspace: str | Path,
    evidence_db: str | Path,
    *,
    notebook_id: str,
    folder_path: str | Path,
    library_kind: str = "folder",
    progress: ImportProgress | None = None,
    merge_existing: bool = False,
    root_path_override: str | Path | None = None,
) -> dict[str, Any]:
    """Index a selected folder and switch the notebook only after validation."""

    _report_import_progress(progress, phase="扫描资料目录", progress=0.04, detail="正在确认访问权限与支持的文件类型")
    folder = _resolved_directory(folder_path)
    normalized_kind = _library_kind(library_kind)
    html_files = [] if normalized_kind == "obsidian" else _candidate_files(folder, _HTML_SUFFIXES)
    markdown_files = _candidate_files(folder, _MARKDOWN_SUFFIXES)
    if merge_existing:
        # A personal knowledge base is a container for multiple linked
        # locations.  Importing the new location directly would make the
        # incremental indexer prune documents from the previous location.
        # Route the addition through the managed-file importer so the old
        # sources are copied into the next generation before the new folder
        # is indexed.
        merge_files = (
            markdown_files
            if normalized_kind == "obsidian"
            else _candidate_files(folder, _LIBRARY_IMPORTABLE_SUFFIXES)
        )
        if not merge_files:
            pdf_count = sum(1 for path in folder.rglob("*.pdf") if path.is_file())
            if pdf_count:
                raise ValueError(
                    "所选文件夹只有 PDF。请先用 scansci-html 转换为 HTML，或通过“添加论文文件”导入已转换文献。"
                )
            raise ValueError("所选文件夹没有可索引的 HTML 或 Markdown 文献")
        existing_summary = load_workspace_summary(workspace, notebook_id=notebook_id)
        existing_notebooks = list(existing_summary.get("notebooks", []) or [])
        existing_root = str(dict(existing_notebooks[0]).get("root_path", "") or "") if existing_notebooks else ""
        display_root = root_path_override or existing_root or folder
        result = import_library_files(
            workspace,
            evidence_db,
            notebook_id=notebook_id,
            file_paths=merge_files,
            progress=progress,
            root_path=display_root,
        )
        update_notebook_metadata(
            workspace,
            notebook_id=notebook_id,
            metadata={"imported_from_folder": str(folder), "library_kind": normalized_kind},
        )
        result["workspace"] = load_workspace_summary(workspace)
        result["notebook"] = load_workspace_summary(workspace, notebook_id=notebook_id)["notebooks"][0]
        result["linked_folder"] = str(folder)
        result["merged_existing"] = True
        return result
    candidates = html_files or markdown_files
    importable_files = (
        markdown_files
        if normalized_kind == "obsidian"
        else _candidate_files(folder, _LIBRARY_IMPORTABLE_SUFFIXES)
    )
    requires_conversion = normalized_kind != "obsidian" and bool(importable_files) and (
        not candidates
        or len(importable_files) > len(html_files) + len(markdown_files)
        or bool(html_files and markdown_files)
    )
    if requires_conversion:
        _report_import_progress(
            progress,
            phase="解析原始文档",
            progress=0.14,
            detail=f"发现 {len(importable_files)} 个可解析文件",
        )
        result = import_library_files(
            workspace,
            evidence_db,
            notebook_id=notebook_id,
            file_paths=importable_files,
            progress=progress,
            root_path=root_path_override or folder,
        )
        update_notebook_metadata(
            workspace,
            notebook_id=notebook_id,
            metadata={"imported_from_folder": str(folder), "library_kind": _library_kind(library_kind)},
        )
        result["workspace"] = load_workspace_summary(workspace)
        result["notebook"] = load_workspace_summary(workspace, notebook_id=notebook_id)["notebooks"][0]
        return result
    if not candidates:
        pdf_count = sum(1 for path in folder.rglob("*.pdf") if path.is_file())
        if pdf_count:
            raise ValueError(
                "所选文件夹只有 PDF。请先用 scansci-html 转换为 HTML，或通过“添加论文文件”导入已转换文献。"
            )
        raise ValueError("所选文件夹没有可索引的 HTML 或 Markdown 文献")
    if len(candidates) > _MAX_LIBRARY_DOCUMENTS:
        raise ValueError(f"资料文件过多（{len(candidates)}），单次最多 {_MAX_LIBRARY_DOCUMENTS} 篇")

    target_db = notebook_evidence_db(evidence_db, notebook_id)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    temporary_db = target_db.with_name(f".{target_db.stem}.{uuid4().hex}.importing.sqlite")
    source_format = "html" if html_files else "markdown"
    try:
        # Start from the previous isolated store when it exists.  The indexer
        # then updates only changed source documents; unchanged document cards
        # and digest-matched vectors remain reusable after the atomic swap.
        if target_db.is_file():
            shutil.copy2(target_db, temporary_db)
        _report_import_progress(
            progress,
            phase="建立文档与章节结构",
            progress=0.32,
            detail=f"正在解析 {len(candidates)} 篇{ '网页文献' if source_format == 'html' else '笔记或文档'}",
        )
        if source_format == "html":
            indexed = index_evidence_library(
                folder,
                db_path=temporary_db,
                inject_evidence_html=True,
                min_sentence_length=10,
                incremental=True,
            )
        else:
            _preserve_imported_document_ids(temporary_db, markdown_files)
            indexed = index_markdown_library(
                folder,
                db_path=temporary_db,
                min_sentence_length=10,
                include_support_directories=normalized_kind == "obsidian",
                include_title_only_notes=normalized_kind == "obsidian",
                incremental=True,
            )
        if int(indexed.get("documents", 0) or 0) <= 0:
            raise ValueError("所选文件夹中的文献没有可提取正文")
        # Incremental imports begin with a copy of the previous isolated
        # store. Count digest-matched vectors already present there so the
        # progress report reflects actual reuse instead of a full rebuild.
        reused_in_place_vectors = _count_reusable_vectors(temporary_db)
        # The evidence database is rebuilt in a temporary file before the
        # notebook switches to it. Preserve vectors whose original evidence
        # text still matches either the notebook's preceding database or the
        # pre-isolation shared evidence store. The migration is digest-gated,
        # so an unrelated library can never contribute vectors merely because
        # it happens to use the same embedding model.
        _report_import_progress(
            progress,
            phase="复用已有语义索引",
            progress=0.76,
            detail="正在校验可复用的原文证据向量",
        )
        cache_migration = migrate_embedding_caches(
            temporary_db,
            source_dbs=[target_db, Path(evidence_db).resolve()],
        )
        if reused_in_place_vectors:
            cache_migration["reused_in_place_vectors"] = reused_in_place_vectors
            cache_migration["migrated_vectors"] = int(cache_migration.get("migrated_vectors", 0) or 0) + reused_in_place_vectors
            cache_migration["matched_evidence"] = int(cache_migration.get("matched_evidence", 0) or 0) + reused_in_place_vectors
            prior_name = target_db.name
            sources_used = list(cache_migration.get("sources_used", []) or [])
            if prior_name not in sources_used:
                sources_used.append(prior_name)
            cache_migration["sources_used"] = sources_used
            cache_migration["reason"] = "reused_in_place"
        indexed["vector_cache_migration"] = cache_migration
        _replace_evidence_database(temporary_db, target_db)
        indexed["db_path"] = str(target_db)
        _report_import_progress(
            progress,
            phase="核验原文证据定位",
            progress=0.82,
            detail="正在检查章节层级、证据片段和原文对应关系",
        )
        indexed["quality"] = assess_evidence_structure(target_db, verify_source_text=True)
        _report_import_progress(
            progress,
            phase="生成资料目录与知识图谱",
            progress=0.88,
            detail=f"正在为 {int(indexed.get('documents', 0) or 0)} 份资料生成结构摘要，并连接文档、章节与主题",
        )
        indexed["library_overview"] = build_library_overview(target_db)
    finally:
        _cleanup_temporary_database(temporary_db)

    effective_root = Path(root_path_override).expanduser().resolve() if root_path_override else folder
    set_notebook_root_path(
        workspace,
        notebook_id=notebook_id,
        root_path=effective_root,
        metadata={
            "library_kind": normalized_kind,
            "library_root": str(effective_root),
            "evidence_quality": _quality_snapshot(dict(indexed.get("quality", {}) or {})),
            "vector_cache_migration": {
                "migrated_vectors": int(cache_migration.get("migrated_vectors", 0) or 0),
                "sources": list(cache_migration.get("sources_used", []) or []),
                "reason": str(cache_migration.get("reason", "") or ""),
            },
            "library_overview": {
                key: int(value or 0)
                for key, value in dict(indexed.get("library_overview", {}) or {}).items()
                if key in {"documents", "document_cards", "sections", "graph_nodes", "graph_edges", "evidence_spans"}
            },
        },
    )
    _report_import_progress(progress, phase="同步知识库目录", progress=0.93, detail="正在更新本地资料清单")
    synced = sync_sources_from_evidence_store(
        workspace,
        target_db,
        notebook_id=notebook_id,
        metadata={"library_root": str(folder), "source_format": source_format},
        replace=True,
    )
    summary = load_workspace_summary(workspace, notebook_id=notebook_id)
    notebook = next(iter(list(summary.get("notebooks", []) or [])), {})
    _report_import_progress(
        progress,
        phase="资料已可检索",
        progress=1.0,
        detail="文档摘要、章节目录与原文证据片段均已建立；细节查询会按需访问原文证据",
    )
    return {
        "ok": True,
        "folder_path": str(folder),
        "source_format": source_format,
        "library_kind": normalized_kind,
        "indexed": indexed,
        "synced": synced,
        "notebook": notebook,
        "workspace": load_workspace_summary(workspace),
        "ignored_markdown_files": len(markdown_files) if html_files else 0,
    }


@_serial_library_import
def import_library_files(
    workspace: str | Path,
    evidence_db: str | Path,
    *,
    notebook_id: str,
    file_paths: Iterable[str | Path],
    progress: ImportProgress | None = None,
    replace_existing: bool = False,
    source_metadata: dict[str, dict[str, Any]] | None = None,
    root_path: str | Path | None = None,
) -> dict[str, Any]:
    """Index local source documents while leaving the originals in place.

    ``replace_existing`` is used by integrations whose supplied file list is
    authoritative (currently Zotero).  In that mode the next managed
    generation contains exactly that list instead of carrying forward old
    extracted Markdown, so removed attachments and partial prior imports do
    not remain silently searchable or get duplicated.
    """

    _report_import_progress(progress, phase="准备解析文件", progress=0.05, detail="正在确认文件格式和读取权限")
    supplied = [_resolved_file(path) for path in file_paths]
    supported = list(dict.fromkeys(path for path in supplied if path.suffix.lower() in _IMPORTABLE_SUFFIXES))
    if not supported:
        raise ValueError("请选择 HTML 或 Markdown 文献文件")
    if len(supported) > _MAX_LIBRARY_DOCUMENTS:
        raise ValueError(f"单次最多添加 {_MAX_LIBRARY_DOCUMENTS} 篇文献")

    workspace_path = Path(workspace).resolve()
    current_summary = load_workspace_summary(workspace_path, notebook_id=notebook_id)
    current_notebooks = list(current_summary.get("notebooks", []) or [])
    current_kind = str(dict(current_notebooks[0]).get("metadata", {}).get("library_kind", "files")) if current_notebooks else "files"
    current_sources = list(dict(current_notebooks[0]).get("sources", []) or []) if current_notebooks else []
    sources_by_path: dict[Path, list[dict[str, Any]]] = {}
    for source in current_sources:
        paths = {_local_source_path(source.get(key)) for key in ("source_url", "html_path")}
        for path in paths - {None}:
            sources_by_path.setdefault(path, []).append(source)
    for path in supported:
        if len(sources_by_path.get(path, [])) > 1:
            raise ValueError(f"原文 {path.name} 已关联多个资料记录；为保留已有引用，请先核对重复记录后重试。")
    display_root = Path(root_path).expanduser().resolve() if root_path else None
    if display_root is None and current_notebooks:
        current_root = str(dict(current_notebooks[0]).get("root_path", "") or "").strip()
        if current_root:
            display_root = Path(current_root).expanduser().resolve()
    if display_root is None:
        display_root = supported[0].parent
    managed = (
        workspace_path.parent
        / ".scansci-library"
        / _safe_folder_name(notebook_id)
        / f"generation-{uuid4().hex}"
    )
    managed.mkdir(parents=True, exist_ok=True)
    legacy_sources: list[Path] = []
    copied_sources: dict[Path, Path] = {}
    if not replace_existing:
        for existing in _existing_source_paths(workspace_path, notebook_id=notebook_id):
            if not existing.is_file():
                continue
            if existing.suffix.lower() in _MARKDOWN_SUFFIXES:
                copied = _unique_path(managed, _safe_folder_name(existing.stem), existing.suffix or ".md")
                shutil.copy2(existing, copied)
                copied_sources[existing] = copied
            elif existing not in supported:
                legacy_sources.append(existing)

    extracted_sources: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    added = 0
    work_items = [*((path, False) for path in legacy_sources), *((path, True) for path in supported)]

    def extract_one(item: tuple[Path, bool]) -> tuple[Path, bool, dict[str, Any] | None, str, str]:
        original, is_new = item
        try:
            with TemporaryDirectory(prefix=".extract-", dir=managed) as extraction_dir:
                source = extract_local_document(
                    original,
                    output_dir=extraction_dir,
                    workspace=workspace_path,
                )
                text = Path(str(source["text_path"])).read_text(encoding="utf-8")
            return original, is_new, source, text, ""
        except Exception as error:
            return original, is_new, None, "", f"{type(error).__name__}: {error}"

    # MinerU is an asynchronous cloud conversion service.  Keep its queue
    # deliberately small so a large folder import does not exhaust the user's
    # quota or create a burst of signed-upload requests.  Local parsers retain
    # the faster eight-worker path.
    mineru_enabled = False
    if any(path.suffix.lower() == ".pdf" for path, _is_new in work_items):
        try:
            from .app_settings import get_document_service_api_key, load_settings

            mineru_config = dict(load_settings(workspace_path).get("document_processing", {}).get("mineru", {}) or {})
            mineru_enabled = bool(mineru_config.get("enabled")) and bool(
                get_document_service_api_key(workspace_path, "mineru")
            )
        except Exception:
            mineru_enabled = False
    workers = min(2 if mineru_enabled else 8, max(1, len(work_items)))
    extracted_items: list[tuple[Path, bool, dict[str, Any] | None, str, str] | None] = [None] * len(work_items)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(extract_one, item): index
            for index, item in enumerate(work_items)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            extracted_items[futures[future]] = future.result()
            _report_import_progress(
                progress,
                phase="提取原文内容",
                progress=0.08 + 0.52 * completed / max(1, len(work_items)),
                detail=f"已完成 {completed}/{len(work_items)} 个文件",
            )
    for item in extracted_items:
        if item is None:
            continue
        original, is_new, source, text, error_message = item
        if source is None:
            failures.append({"name": original.name, "error": error_message[:300]})
            continue
        try:
            stem = _safe_folder_name(original.stem)
            previous = next(iter(sources_by_path.get(original, [])), {})
            previous_path = _local_source_path(previous.get("html_path"))
            target = copied_sources.get(previous_path) or _unique_path(managed, stem, ".md")
            previous_metadata = {}
            if previous_path and previous_path.is_file() and previous_path.suffix.lower() in _MARKDOWN_SUFFIXES:
                previous_metadata, _ = _split_markdown_front_matter(previous_path.read_text(encoding="utf-8-sig"))
            supplied_metadata = dict((source_metadata or {}).get(str(original.resolve()), {}) or {})
            embedded_metadata = dict(source.get("metadata", {}) or {})
            # Preserve catalog and reliable PDF titles. Only enrich the
            # metadata helper's low-confidence filename fallback.
            if original.suffix.lower() == ".pdf" and not supplied_metadata.get("title") and (
                not embedded_metadata.get("title") or embedded_metadata.get("metadata_confidence") == "low"
            ):
                page_title = _pdf_first_page_title(original) or _title_from_extracted_text(text)
                if page_title:
                    embedded_metadata["title"] = page_title
                    embedded_metadata["metadata_source"] = f"{embedded_metadata.get('metadata_source', 'filename')}+pdf-first-page"
                    embedded_metadata["metadata_confidence"] = "medium"
            metadata = {
                "title": str(
                    supplied_metadata.get("title")
                    or embedded_metadata.get("title")
                    or _title_from_extracted_text(text)
                    or original.stem
                ),
                "source_file": original.name,
                "source_url": str(original),
                "source_suffix": original.suffix.lower(),
                "parser": str(source.get("parser", "")),
                "source_storage": "external-reference",
            }
            if previous.get("doc_id"):
                # A better title must keep the source and citation identity.
                # The temporary index binds this hint to the existing original.
                metadata["doc_id"] = str(previous["doc_id"])
            for key in ("doi", "date", "year", "author", "metadata_source", "metadata_confidence"):
                value = _scalar_metadata_value(
                    supplied_metadata.get(key)
                    or (supplied_metadata.get("DOI") if key == "doi" else "")
                    or embedded_metadata.get(key, "")
                    or previous_metadata.get(key, "")
                )
                if value:
                    metadata[key] = value
            if not metadata.get("author"):
                creators = _scalar_metadata_value(supplied_metadata.get("creators"))
                if creators:
                    metadata["author"] = creators
            pending = target.with_suffix(".md.importing")
            pending.write_text(_markdown_document(metadata, text), encoding="utf-8")
            pending.replace(target)
            extracted_sources.append(
                {
                    "source_id": str(source.get("source_id", "")),
                    "name": original.name,
                    "parser": str(source.get("parser", "")),
                    "page_count": int(source.get("page_count", 0) or 0),
                    "character_count": int(source.get("character_count", 0) or 0),
                    "original_path": str(original),
                    "metadata": {
                        key: str(value)
                        for key, value in metadata.items()
                        if key in {"title", "author", "doi", "date", "year", "metadata_source", "metadata_confidence"}
                    },
                }
            )
            if is_new:
                added += 1
        except Exception as error:
            failures.append({"name": original.name, "error": str(error)[:300]})

    if added <= 0 and not any(managed.iterdir()):
        detail = failures[0]["error"] if failures else "没有可提取的文献"
        raise ValueError(f"知识库中没有成功解析的文件：{detail}")
    _report_import_progress(
        progress,
        phase="生成文档、章节与证据片段",
        progress=0.66,
        detail="正在将已提取原文切分为可引用的证据单元",
    )
    result = import_library_folder(
        workspace_path,
        evidence_db,
        notebook_id=notebook_id,
        folder_path=managed,
        library_kind=current_kind,
        root_path_override=display_root,
    )
    result["added_files"] = added
    result["skipped_files"] = failures
    result["managed"] = True
    result["source_storage"] = "external-reference"
    result["ingestion"] = {
        "sources": extracted_sources,
        "summary": {
            "requested": len(supported),
            "completed": added,
            "skipped": len(failures),
            "characters": sum(int(item.get("character_count", 0) or 0) for item in extracted_sources),
            "pages": sum(int(item.get("page_count", 0) or 0) for item in extracted_sources),
        },
    }
    _cleanup_managed_generations(workspace_path, notebook_id=notebook_id, keep=managed)
    return result


def register_zotero_library(
    workspace: str | Path,
    *,
    notebook_id: str,
    folder_path: str | Path,
    evidence_db: str | Path | None = None,
) -> dict[str, Any]:
    """Remember a local Zotero PDF store as a read-only literature shelf.

    The evidence engine only indexes extractable HTML and Markdown today, so
    this deliberately does not pretend that raw PDFs have been parsed.  The
    connection is still useful: users see the real local literature shelf and
    can enable document processing before importing searchable full text.
    """

    folder = _resolved_directory(folder_path)
    pdf_files = sorted(path for path in folder.rglob("*.pdf") if path.is_file())
    if not pdf_files:
        raise ValueError("所选 Zotero 文件夹没有找到 PDF 文献")
    preview_titles = [path.stem.replace("_", " ").strip() for path in pdf_files[:18]]
    zotero = {
        "path": str(folder),
        "pdf_count": len(pdf_files),
        "sample_titles": preview_titles,
        "items": [
            {
                "title": path.stem.replace("_", " ").strip(),
                "attachments": [{"path": str(path), "exists": True}],
            }
            for path in pdf_files
        ],
    }
    update_notebook_metadata(
        workspace,
        notebook_id=notebook_id,
        metadata={"library_kind": "zotero", "zotero": zotero},
    )
    if evidence_db is not None:
        zotero["evidence_index"] = index_zotero_attachments(
            workspace,
            evidence_db,
            notebook_id=notebook_id,
            zotero_state=zotero,
        )
        update_notebook_metadata(
            workspace,
            notebook_id=notebook_id,
            metadata={"library_kind": "zotero", "zotero": zotero},
        )
    summary = load_workspace_summary(workspace, notebook_id=notebook_id)
    notebook = next(iter(list(summary.get("notebooks", []) or [])), {})
    return {
        "ok": True,
        "zotero": zotero,
        "notebook": notebook,
        "workspace": load_workspace_summary(workspace),
    }


def index_zotero_attachments(
    workspace: str | Path,
    evidence_db: str | Path,
    *,
    notebook_id: str,
    zotero_state: dict[str, Any],
    progress: ImportProgress | None = None,
) -> dict[str, Any]:
    """Materialize readable Zotero PDFs into the notebook evidence store once.

    Zotero remains the source of truth; ScanSci stores extracted Markdown in its
    managed generation directory and never changes the Zotero library itself.
    """

    target_db = notebook_evidence_db(evidence_db, notebook_id)
    paths: list[Path] = []
    source_metadata: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in list(zotero_state.get("items", []) or []):
        if not isinstance(item, dict):
            continue
        for attachment in list(item.get("attachments", []) or []):
            if not isinstance(attachment, dict) or not bool(attachment.get("exists", True)):
                continue
            path = Path(str(attachment.get("path", ""))).expanduser()
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            resolved = str(path.resolve())
            source_metadata.setdefault(
                resolved,
                {
                    "title": str(item.get("title", "") or "").strip(),
                    "doi": str(item.get("doi", "") or "").strip(),
                    "date": str(item.get("date", "") or "").strip(),
                },
            )
            if resolved not in seen:
                seen.add(resolved)
                paths.append(Path(resolved))
    if not paths:
        return {"status": "unavailable", "reason": "no_readable_pdf_attachments", "db_path": str(target_db)}
    expected_documents = min(len(paths), _MAX_LIBRARY_DOCUMENTS)
    summary = load_workspace_summary(workspace, notebook_id=notebook_id)
    notebook = next(iter(list(summary.get("notebooks", []) or [])), {})
    indexed_documents = int(dict(notebook.get("counts", {}) or {}).get("sources", 0) or 0)
    # A nonempty SQLite file only proves that *some* earlier import ran.  A
    # large Zotero library can gain hundreds of attachments after that, so only
    # reuse it when the notebook already contains at least this many sources.
    if (
        target_db.is_file()
        and target_db.stat().st_size >= _MIN_EVIDENCE_DB_BYTES
        and indexed_documents >= expected_documents
    ):
        tag_index = _refresh_zotero_tag_index(target_db, zotero_state.get("items", []))
        return {
            "status": "ready",
            "db_path": str(target_db),
            "reused": True,
            "requested": expected_documents,
            "indexed_documents": indexed_documents,
            "tag_index": tag_index,
        }
    _report_import_progress(
        progress,
        phase="正在读取 Zotero 全文",
        progress=0.12,
        detail=f"已找到 {expected_documents} 篇可读取的 PDF，正在建立证据索引",
    )
    try:
        result = import_library_files(
            workspace,
            evidence_db,
            notebook_id=notebook_id,
            file_paths=paths[:expected_documents],
            replace_existing=True,
            source_metadata=source_metadata,
            progress=lambda event: _report_import_progress(
                progress,
                phase=str(event.get("phase", "正在读取 Zotero 全文")),
                progress=0.12 + 0.80 * float(event.get("progress", 0.0) or 0.0),
                detail=str(event.get("detail", "") or ""),
            ),
        )
    except Exception as error:
        return {
            "status": "failed",
            "reason": f"{type(error).__name__}: {error}"[:500],
            "db_path": str(target_db),
            "requested": len(paths),
        }
    indexed_db = Path(str(result.get("db_path", target_db)))
    tag_index = _refresh_zotero_tag_index(indexed_db, zotero_state.get("items", []))
    return {
        "status": "indexed",
        "db_path": str(indexed_db),
        "requested": len(paths),
        "indexed_documents": int(dict(result.get("indexed", {}) or {}).get("documents", 0) or 0),
        "skipped": len(list(result.get("skipped_files", []) or [])),
        "tag_index": tag_index,
    }


def discover_local_zotero(preferred_data_dir: str | Path | None = None) -> dict[str, str]:
    """Find a Zotero data directory without starting or modifying Zotero.

    A user-selected directory is authoritative.  This is important for the
    manual recovery flow: an invalid selection must not silently fall back to
    another profile and make the UI report a connection to the wrong library.
    """

    candidates: list[Path] = []
    preferred = str(preferred_data_dir or "").strip()
    if preferred:
        candidates.append(Path(preferred).expanduser())
    else:
        configured = str(os.environ.get("ZOTERO_DATA_DIR", "")).strip()
        if configured:
            candidates.append(Path(configured).expanduser())

        home = Path.home()
        appdata = Path(str(os.environ.get("APPDATA", home / "AppData" / "Roaming")))
        profile_root = appdata / "Zotero" / "Zotero" / "Profiles"
        # Zotero's profile preference is authoritative when it exists.  A
        # stale ``%USERPROFILE%\\Zotero`` folder can otherwise win first and
        # make ScanSci connect to an empty or obsolete library.
        for prefs_path in sorted(profile_root.glob("*/prefs.js")) if profile_root.is_dir() else []:
            try:
                text = prefs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            match = re.search(r'user_pref\("extensions\.zotero\.dataDir",\s*("(?:[^"\\]|\\.)*")\s*\);', text)
            if not match:
                continue
            try:
                value = str(json.loads(match.group(1))).strip()
            except (ValueError, TypeError):
                continue
            if value:
                candidates.append(Path(value).expanduser())

        candidates.extend([home / "Zotero", home / "Documents" / "Zotero"])

    checked: list[str] = []
    for candidate in candidates:
        data_dir = candidate.parent if candidate.name.lower() == "zotero.sqlite" else candidate
        try:
            resolved = data_dir.resolve()
        except OSError:
            resolved = data_dir.absolute()
        key = os.path.normcase(str(resolved))
        if key in checked:
            continue
        checked.append(key)
        database_path = resolved / "zotero.sqlite"
        if database_path.is_file():
            return {
                "data_dir": str(resolved),
                "database_path": str(database_path),
                "storage_path": str(resolved / "storage"),
            }
    raise FileNotFoundError("未在本机发现 Zotero 数据目录")


def _read_local_zotero_database(
    *,
    limit: int,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Read Zotero metadata and attachment links through a read-only SQLite connection."""

    discovered = discover_local_zotero(data_dir)
    data_dir = Path(discovered["data_dir"])
    database_path = Path(discovered["database_path"])
    # Zotero keeps a write transaction open while it refreshes its local API.
    # A normal read-only connection can therefore report ``database is locked``
    # even though the database is healthy.  Immutable mode reads the last
    # committed snapshot without competing for Zotero's writer lock.
    connection = sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    try:
        collection_rows = connection.execute(
            """
            SELECT c.collectionID, c.key, c.collectionName, c.parentCollectionID,
                   COUNT(ci.itemID) AS itemCount
            FROM collections c
            LEFT JOIN collectionItems ci ON ci.collectionID = c.collectionID
            GROUP BY c.collectionID, c.key, c.collectionName, c.parentCollectionID
            ORDER BY lower(c.collectionName), c.collectionID
            """
        ).fetchall()
        collection_keys = {int(row["collectionID"]): str(row["key"] or "") for row in collection_rows}
        collections = [
            {
                "key": str(row["key"] or ""),
                "name": str(row["collectionName"] or "").strip(),
                "parent": collection_keys.get(int(row["parentCollectionID"]), "")
                if row["parentCollectionID"] is not None
                else "",
                "item_count": int(row["itemCount"] or 0),
            }
            for row in collection_rows
            if str(row["key"] or "").strip() and str(row["collectionName"] or "").strip()
        ]

        excluded_types = ("attachment", "note", "annotation")
        item_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM items i
                JOIN itemTypes t ON t.itemTypeID = i.itemTypeID
                LEFT JOIN deletedItems d ON d.itemID = i.itemID
                WHERE d.itemID IS NULL AND t.typeName NOT IN (?, ?, ?)
                """,
                excluded_types,
            ).fetchone()[0]
            or 0
        )
        normalized_limit = max(1, min(20_000, int(limit)))
        candidate_limit = normalized_limit
        item_rows = connection.execute(
            """
            SELECT i.itemID, i.key, i.dateModified, t.typeName
            FROM items i
            JOIN itemTypes t ON t.itemTypeID = i.itemTypeID
            LEFT JOIN deletedItems d ON d.itemID = i.itemID
            WHERE d.itemID IS NULL AND t.typeName NOT IN (?, ?, ?)
            ORDER BY i.dateModified DESC, i.itemID DESC
            LIMIT ?
            """,
            (*excluded_types, candidate_limit),
        ).fetchall()
        item_ids = [int(row["itemID"]) for row in item_rows]
        placeholders = ",".join("?" for _ in item_ids)
        fields_by_item: dict[int, dict[str, str]] = {item_id: {} for item_id in item_ids}
        creators_by_item: dict[int, list[str]] = {item_id: [] for item_id in item_ids}
        collections_by_item: dict[int, list[str]] = {item_id: [] for item_id in item_ids}
        tags_by_item: dict[int, list[str]] = {item_id: [] for item_id in item_ids}
        attachments_by_item: dict[int, list[dict[str, Any]]] = {item_id: [] for item_id in item_ids}
        if item_ids:
            for row in connection.execute(
                f"""
                SELECT d.itemID, f.fieldName, v.value
                FROM itemData d
                JOIN fields f ON f.fieldID = d.fieldID
                JOIN itemDataValues v ON v.valueID = d.valueID
                WHERE d.itemID IN ({placeholders})
                """,
                item_ids,
            ):
                fields_by_item[int(row["itemID"])][str(row["fieldName"])] = str(row["value"] or "")
            for row in connection.execute(
                f"""
                SELECT ic.itemID, c.firstName, c.lastName
                FROM itemCreators ic
                JOIN creators c ON c.creatorID = ic.creatorID
                WHERE ic.itemID IN ({placeholders})
                ORDER BY ic.itemID, ic.orderIndex
                """,
                item_ids,
            ):
                name = " ".join(part for part in [str(row["firstName"] or "").strip(), str(row["lastName"] or "").strip()] if part)
                if name and len(creators_by_item[int(row["itemID"])]) < 4:
                    creators_by_item[int(row["itemID"])].append(name)
            for row in connection.execute(
                f"""
                SELECT ci.itemID, c.key
                FROM collectionItems ci
                JOIN collections c ON c.collectionID = ci.collectionID
                WHERE ci.itemID IN ({placeholders})
                """,
                item_ids,
            ):
                key = str(row["key"] or "")
                if key:
                    collections_by_item[int(row["itemID"])].append(key)
            # ``tags``/``itemTags`` are present in current Zotero databases,
            # but older or minimal test profiles may not have them yet.
            try:
                for row in connection.execute(
                    f"""
                    SELECT it.itemID, t.name
                    FROM itemTags it
                    JOIN tags t ON t.tagID = it.tagID
                    WHERE it.itemID IN ({placeholders})
                    ORDER BY it.itemID, lower(t.name), t.tagID
                    """,
                    item_ids,
                ):
                    tag = str(row["name"] or "").strip()
                    if tag and tag not in tags_by_item[int(row["itemID"])]:
                        tags_by_item[int(row["itemID"])].append(tag)
            except sqlite3.Error:
                pass
            for row in connection.execute(
                f"""
                SELECT ia.parentItemID, ia.path, ia.linkMode, attachment.key AS attachmentKey
                FROM itemAttachments ia
                JOIN items attachment ON attachment.itemID = ia.itemID
                WHERE lower(coalesce(ia.contentType, '')) = 'application/pdf'
                  AND ia.parentItemID IN ({placeholders})
                """,
                item_ids,
            ):
                raw_path = str(row["path"] or "")
                attachment_path = _resolve_zotero_attachment_path(
                    raw_path,
                    data_dir=data_dir,
                    attachment_key=str(row["attachmentKey"] or ""),
                )
                attachments_by_item[int(row["parentItemID"])].append(
                    {
                        "name": Path(raw_path.removeprefix("storage:")).name,
                        "path": str(attachment_path),
                        "exists": attachment_path.is_file(),
                        "link_mode": int(row["linkMode"] or 0),
                    }
                )

        pdf_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM itemAttachments WHERE lower(coalesce(contentType, '')) = 'application/pdf'"
            ).fetchone()[0]
            or 0
        )
        items: list[dict[str, Any]] = []
        for row in item_rows:
            item_id = int(row["itemID"])
            fields = fields_by_item[item_id]
            title = str(fields.get("title", "") or fields.get("caseName", "") or fields.get("nameOfAct", "")).strip()
            if not title:
                title = f"未命名文献（{str(row['typeName'] or 'item')} · {str(row['key'] or item_id)}）"
            attachments = attachments_by_item[item_id]
            publication = str(
                fields.get("publicationTitle", "")
                or fields.get("bookTitle", "")
                or fields.get("proceedingsTitle", "")
                or fields.get("websiteTitle", "")
            )
            items.append(
                {
                    "key": str(row["key"] or ""),
                    "title": title,
                    "item_type": str(row["typeName"] or ""),
                    "doi": str(fields.get("DOI", "")),
                    "date": str(fields.get("date", "")),
                    "publication": publication,
                    "creators": creators_by_item[item_id],
                    "url": str(fields.get("url", "")),
                    "collections": collections_by_item[item_id],
                    "tags": tags_by_item[item_id],
                    "attachments": attachments,
                    "pdf_path": str(attachments[0]["path"]) if attachments else "",
                }
            )
            if len(items) >= normalized_limit:
                break
    finally:
        connection.close()

    return {
        "connection": "local-database",
        "connected": True,
        "auto_discovered": True,
        "read_only": True,
        "source_storage": "external-reference",
        "data_dir": discovered["data_dir"],
        "database_path": discovered["database_path"],
        "storage_path": discovered["storage_path"],
        "item_count": item_count,
        "pdf_count": pdf_count,
        "collections": collections,
        "collection_count": len(collections),
        "items": items,
        "sample_titles": [item["title"] for item in items[:18]],
    }


def _resolve_zotero_attachment_path(raw_path: str, *, data_dir: Path, attachment_key: str) -> Path:
    if raw_path.startswith("storage:"):
        return (data_dir / "storage" / attachment_key / raw_path.removeprefix("storage:")).resolve()
    return Path(raw_path).expanduser().resolve()


def connect_local_zotero(
    workspace: str | Path,
    *,
    notebook_id: str,
    client: Any | None = None,
    limit: int = 10_000,
    evidence_db: str | Path | None = None,
    index_attachments: bool = True,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Read bibliographic metadata from Zotero 7's loopback API.

    No Zotero cloud key is requested or stored.  The user remains in control of
    the local API through Zotero's "Allow other applications" preference.
    """

    database_error: Exception | None = None
    configured_data_dir = str(data_dir or "").strip()
    if client is None:
        try:
            zotero_state = _read_local_zotero_database(limit=limit, data_dir=data_dir)
        except (OSError, sqlite3.Error, ValueError) as error:
            database_error = error
        else:
            if configured_data_dir:
                zotero_state["auto_discovered"] = False
                zotero_state["configured_data_dir"] = str(zotero_state["data_dir"])
            set_notebook_root_path(
                workspace,
                notebook_id=notebook_id,
                root_path=zotero_state["data_dir"],
                metadata={"library_kind": "zotero", "zotero": zotero_state},
            )
            if evidence_db is not None and index_attachments:
                zotero_state["evidence_index"] = index_zotero_attachments(
                    workspace,
                    evidence_db,
                    notebook_id=notebook_id,
                    zotero_state=zotero_state,
                )
            elif evidence_db is not None:
                zotero_state["evidence_index"] = {
                    "status": "queued",
                    "reason": "background_import",
                }
            update_notebook_metadata(
                workspace,
                notebook_id=notebook_id,
                metadata={"library_kind": "zotero", "zotero": zotero_state},
            )
            summary = load_workspace_summary(workspace, notebook_id=notebook_id)
            notebook = next(iter(list(summary.get("notebooks", []) or [])), {})
            return {
                "ok": True,
                "zotero": zotero_state,
                "notebook": notebook,
                "workspace": load_workspace_summary(workspace),
            }
        try:
            from pyzotero import zotero

            client = zotero.Zotero("0", "user", local=True)
        except Exception as error:  # optional connector boundary
            raise RuntimeError("本机 Zotero 连接器不可用，请重新安装 ScanSci") from error
    normalized_limit = max(1, min(20_000, int(limit)))
    try:
        top_reader = getattr(client, "top", None)
        if callable(top_reader):
            raw_items = (
                _read_local_zotero_endpoint(client, "items/top", limit=normalized_limit)
                if bool(getattr(client, "local", False))
                else _read_all_zotero_pages(client, top_reader, limit=normalized_limit)
            )
        else:
            # Compatibility with older pyzotero clients and injected test
            # clients. Modern clients use /items/top so child attachments and
            # notes cannot crowd literature records out of the first page.
            raw_items = list(client.items(limit=normalized_limit))
    except Exception as error:
        detail = f"；自动发现失败：{database_error}" if database_error else ""
        raise RuntimeError(
            "无法自动连接本机 Zotero。请确认 Zotero 数据目录可读，或打开 Zotero → 设置 → 高级，启用“允许本机其他应用与 Zotero 通信”后重试"
            + detail
        ) from error

    raw_collections: list[dict[str, Any]] = []
    collection_reader = getattr(client, "collections", None)
    if callable(collection_reader):
        try:
            raw_collections = [
                item
                for item in (
                    _read_local_zotero_endpoint(client, "collections", limit=20_000)
                    if bool(getattr(client, "local", False))
                    else _read_all_zotero_pages(client, collection_reader, limit=20_000)
                )
                if isinstance(item, dict)
            ]
        except Exception:
            raw_collections = []
    collections: list[dict[str, Any]] = []
    for raw in raw_collections:
        data = dict(raw.get("data", {}) or {})
        name = str(data.get("name", "")).strip()
        key = str(raw.get("key", data.get("key", ""))).strip()
        if not name or not key:
            continue
        meta = dict(raw.get("meta", {}) or {})
        collections.append(
            {
                "key": key,
                "name": name,
                "parent": str(data.get("parentCollection", "") or "").strip(),
                "item_count": int(meta.get("numItems", 0) or 0),
            }
        )

    items: list[dict[str, Any]] = []
    attachment_count = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        data = dict(raw.get("data", {}) or {})
        item_type = str(data.get("itemType", ""))
        if item_type in {"attachment", "note", "annotation"}:
            if item_type == "attachment" and str(data.get("contentType", "")) == "application/pdf":
                attachment_count += 1
            continue
        creators = []
        for creator in list(data.get("creators", []) or [])[:4]:
            if not isinstance(creator, dict):
                continue
            name = str(creator.get("name", "")).strip() or " ".join(
                part for part in [str(creator.get("firstName", "")).strip(), str(creator.get("lastName", "")).strip()] if part
            )
            if name:
                creators.append(name)
        tags: list[str] = []
        for raw_tag in list(data.get("tags", []) or []):
            tag = str(raw_tag.get("tag", "") if isinstance(raw_tag, dict) else raw_tag).strip()
            if tag and tag not in tags:
                tags.append(tag)
        key = str(raw.get("key", data.get("key", "")))
        title = str(data.get("title", "")).strip() or str(data.get("shortTitle", "")).strip()
        if not title:
            title = f"未命名文献（{item_type or 'item'} · {key or len(items) + 1}）"
        items.append(
            {
                "key": key,
                "title": title,
                "item_type": item_type,
                "doi": str(data.get("DOI", "")),
                "date": str(data.get("date", "")),
                "publication": str(data.get("publicationTitle", data.get("proceedingsTitle", ""))),
                "creators": creators,
                "url": str(data.get("url", "")),
                "collections": [str(key) for key in list(data.get("collections", []) or []) if key],
                "tags": tags,
            }
        )

    # /items/top deliberately excludes child attachments. Read attachment
    # records separately so the status card still reports the real PDF count.
    if callable(getattr(client, "top", None)):
        item_reader = getattr(client, "items", None)
        if callable(item_reader):
            try:
                raw_attachments = (
                    _read_local_zotero_endpoint(
                        client,
                        "items",
                        limit=20_000,
                        itemType="attachment",
                    )
                    if bool(getattr(client, "local", False))
                    else _read_all_zotero_pages(
                        client,
                        item_reader,
                        limit=20_000,
                        itemType="attachment",
                    )
                )
            except Exception:
                raw_attachments = []
            attachment_count = sum(
                1
                for raw in raw_attachments
                if isinstance(raw, dict)
                and str(dict(raw.get("data", {}) or {}).get("contentType", "")).lower() == "application/pdf"
            )

    zotero_state = {
        "connection": "local-api",
        "connected": True,
        "item_count": len(items),
        "pdf_count": attachment_count,
        "collections": collections,
        "collection_count": len(collections),
        "items": items,
        "sample_titles": [item["title"] for item in items[:18]],
    }
    if configured_data_dir:
        zotero_state["configured_data_dir"] = configured_data_dir
    if evidence_db is not None and index_attachments:
        zotero_state["evidence_index"] = index_zotero_attachments(
            workspace,
            evidence_db,
            notebook_id=notebook_id,
            zotero_state=zotero_state,
        )
    elif evidence_db is not None:
        zotero_state["evidence_index"] = {
            "status": "queued",
            "reason": "background_import",
        }
    update_notebook_metadata(
        workspace,
        notebook_id=notebook_id,
        metadata={"library_kind": "zotero", "zotero": zotero_state},
    )
    summary = load_workspace_summary(workspace, notebook_id=notebook_id)
    notebook = next(iter(list(summary.get("notebooks", []) or [])), {})
    return {
        "ok": True,
        "zotero": zotero_state,
        "notebook": notebook,
        "workspace": load_workspace_summary(workspace),
    }


def _resolved_directory(value: str | Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请选择资料文件夹")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("资料文件夹必须使用绝对路径")
    resolved = path.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"资料文件夹不存在：{resolved}")
    return resolved


def _resolved_file(value: str | Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("文件路径不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("文件必须使用绝对路径")
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"文件不存在：{resolved}")
    return resolved


def _candidate_files(folder: Path, suffixes: set[str]) -> list[Path]:
    return [
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in suffixes
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
        and not is_ignored_library_path(path, folder)
        and not path.name.endswith(".evidence.html")
        and not path.name.endswith(".raw.html")
    ]


def _read_all_zotero_pages(
    client: Any,
    reader: Any,
    *,
    limit: int,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Read a complete local Zotero result set without a one-page cap."""

    normalized_limit = max(1, min(20_000, int(limit)))
    page_size = min(100, normalized_limit)
    try:
        first_page = list(reader(limit=page_size, **kwargs))
    except TypeError:
        # Small injected clients may not expose pyzotero's keyword surface.
        first_page = list(reader())
    everything = getattr(client, "everything", None)
    if not callable(everything) or len(first_page) >= normalized_limit:
        return [item for item in first_page[:normalized_limit] if isinstance(item, dict)]
    return [
        item
        for item in list(everything(first_page))[:normalized_limit]
        if isinstance(item, dict)
    ]


def _read_local_zotero_endpoint(
    client: Any,
    resource: str,
    *,
    limit: int,
    **params: Any,
) -> list[dict[str, Any]]:
    """Read local Zotero pages concurrently.

    Zotero's loopback API takes roughly the same fixed time per request as the
    cloud API. Independent offset pages are safe to read in parallel and keep
    a full multi-thousand-item refresh interactive.
    """

    normalized_limit = max(1, min(20_000, int(limit)))
    page_size = min(100, normalized_limit)
    endpoint = str(getattr(client, "endpoint", "")).rstrip("/")
    library_type = str(getattr(client, "library_type", "users")).strip("/") or "users"
    library_id = str(getattr(client, "library_id", "0")).strip("/") or "0"
    http_client = getattr(client, "client", None)
    if not endpoint or http_client is None:
        raise RuntimeError("本机 Zotero 客户端缺少分页接口")
    url = f"{endpoint}/{library_type}/{library_id}/{resource.strip('/')}"

    def read_page(start: int) -> tuple[list[dict[str, Any]], int]:
        response = http_client.get(
            url,
            params={**params, "limit": page_size, "start": start},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        page = [item for item in list(payload or []) if isinstance(item, dict)]
        total = int(response.headers.get("Total-Results", len(page)) or len(page))
        return page, total

    first_page, total = read_page(0)
    bounded_total = min(normalized_limit, total)
    starts = list(range(page_size, bounded_total, page_size))
    if not starts:
        return first_page[:normalized_limit]
    workers = min(8, len(starts))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        remaining_pages = list(executor.map(read_page, starts))
    items = list(first_page)
    for page, _total in remaining_pages:
        items.extend(page)
    return items[:normalized_limit]


def _copy_existing_sources(workspace: Path, *, notebook_id: str, destination: Path) -> None:
    summary = load_workspace_summary(workspace, notebook_id=notebook_id)
    notebooks = list(summary.get("notebooks", []) or [])
    for source in list(dict(notebooks[0]).get("sources", []) or []) if notebooks else []:
        path = Path(str(dict(source).get("html_path", ""))).resolve()
        if path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES:
            _copy_unique(path, destination)


def _existing_source_paths(workspace: Path, *, notebook_id: str) -> list[Path]:
    summary = load_workspace_summary(workspace, notebook_id=notebook_id)
    notebooks = list(summary.get("notebooks", []) or [])
    paths: list[Path] = []
    for source in list(dict(notebooks[0]).get("sources", []) or []) if notebooks else []:
        raw = str(dict(source).get("html_path", ""))
        if not raw:
            continue
        path = Path(raw).resolve()
        if path.is_file() and path.suffix.lower() in _IMPORTABLE_SUFFIXES:
            paths.append(path)
    return paths


def _local_source_path(value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw or re.match(r"^https?://", raw, re.IGNORECASE):
        return None
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else None


def _preserve_imported_document_ids(db_path: Path, markdown_files: list[Path]) -> None:
    """Bind regenerated text to its original document in the staged index only."""

    if not db_path.is_file():
        return
    with sqlite3.connect(db_path) as connection:
        _initialize_evidence_schema(connection)
        for path in markdown_files:
            content = path.read_bytes()
            metadata, _ = _split_markdown_front_matter(content.decode("utf-8-sig"))
            doc_id = metadata.get("doc_id", "")
            original = _local_source_path(metadata.get("source_url"))
            if not doc_id or original is None or metadata.get("source_storage") != "external-reference":
                continue
            row = connection.execute(
                "select source_url, html_path from source_documents where doc_id = ?", (doc_id,),
            ).fetchone()
            if not row or original not in {_local_source_path(value) for value in row}:
                continue
            content_hash = sha256(content).hexdigest()
            identity_key = _identity_key_from_values(
                doi=metadata.get("doi", ""), source_url=metadata.get("source_url", ""),
                content_hash=content_hash, html_path=str(path),
            )
            owner = connection.execute(
                "select doc_id from document_identity_aliases where identity_key = ?", (identity_key,),
            ).fetchone()
            if owner and owner[0] != doc_id:
                raise ValueError("文献身份与已有记录冲突，已保留原索引，请核对重复资料后重试。")
            _record_document_identity_alias(
                connection, identity_key=identity_key, doc_id=doc_id,
                content_hash=content_hash, source_path=path,
            )


def _copy_unique(source: Path, destination: Path) -> Path:
    candidate = destination / source.name
    if candidate.exists() and candidate.read_bytes() == source.read_bytes():
        return candidate
    counter = 2
    while candidate.exists():
        candidate = destination / f"{source.stem}-{counter}{source.suffix.lower()}"
        counter += 1
    shutil.copy2(source, candidate)
    return candidate


def _unique_path(destination: Path, stem: str, suffix: str) -> Path:
    candidate = destination / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = destination / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _scalar_metadata_value(value: Any) -> str:
    """Serialize common Zotero/PDF metadata into a stable front-matter value."""

    if isinstance(value, Mapping):
        name = " ".join(
            part
            for part in (
                str(value.get("name", "")).strip(),
                str(value.get("firstName", "")).strip(),
                str(value.get("lastName", "")).strip(),
            )
            if part
        )
        return name or str(value.get("value", "")).strip()
    if isinstance(value, (list, tuple, set)):
        return "; ".join(item for item in (_scalar_metadata_value(raw) for raw in value) if item)
    return str(value or "").replace("\n", " ").strip()


def _title_from_extracted_text(text: str) -> str:
    """Use an explicit Markdown/HTML heading when no catalog title exists."""

    for raw_line in str(text or "").splitlines()[:40]:
        line = raw_line.strip()
        if line.startswith("#"):
            candidate = line.lstrip("#").strip()
            if candidate and len(candidate) <= 300 and not _is_page_heading(candidate):
                return candidate
    return ""


def _pdf_first_page_title(path: Path) -> str:
    """Recover a prominent heading with the existing optional local PDF reader.

    Ambiguous layouts retain the existing filename fallback; no OCR, model or
    network lookup is started to improve a title.
    """

    try:
        import fitz

        with fitz.open(str(path)) as document:
            if not document.page_count:
                return ""
            page = document[0]
            rows = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    if tuple(line.get("dir", (1, 0))) != (1, 0):
                        continue  # Ignore rotated arXiv stamps.
                    spans = [span for span in line.get("spans", []) if str(span.get("text", "")).strip()]
                    text = " ".join(str(span["text"]).strip() for span in spans).strip()
                    if text:
                        rows.append((line["bbox"], max(float(span["size"]) for span in spans), text))
            upper_rows = [row for row in rows if row[0][1] < page.rect.height * 0.55]
            if not upper_rows:
                return ""
            largest = max(row[1] for row in upper_rows)
            if largest < median(row[1] for row in rows) * 1.15:
                return ""  # Uniform-sized text has no reliable title signal.
            heading = sorted((row for row in upper_rows if row[1] >= largest * 0.96), key=lambda row: row[0][1])
            lines = [heading[0][2]]
            bottom = heading[0][0][3]
            for bbox, _size, text in heading[1:]:
                if bbox[1] - bottom > largest:
                    break
                lines.append(text)
                bottom = bbox[3]
            candidate = " ".join(lines)
            if 10 <= len(candidate) <= 300 and not re.match(r"^(?:arxiv\b|abstract\b|proceedings\b|journal\b)", candidate, re.I):
                return candidate
    except Exception:
        # Metadata enrichment must never prevent an otherwise readable import.
        pass
    return ""


def _markdown_document(metadata: dict[str, str], text: str) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        normalized = str(value or "").replace("\n", " ").replace('"', '\\"')
        lines.append(f'{key}: "{normalized}"')
    lines.extend(["---", "", str(text or "").strip(), ""])
    return "\n".join(lines)


def _cleanup_managed_generations(workspace: Path, *, notebook_id: str, keep: Path) -> None:
    """Remove superseded generated text after a replacement index is durable."""

    root = (workspace.parent / ".scansci-library" / _safe_folder_name(notebook_id)).resolve()
    kept = keep.resolve()
    if kept.parent != root or not kept.name.startswith("generation-"):
        return
    for candidate in (root.iterdir() if root.is_dir() else []):
        resolved = candidate.resolve()
        if resolved == kept or resolved.parent != root or not resolved.name.startswith("generation-"):
            continue
        if resolved.is_dir():
            shutil.rmtree(resolved)


def _safe_folder_name(value: str) -> str:
    normalized = "".join(character if character.isalnum() or character in "-_." else "-" for character in value)
    return normalized.strip(".-")[:80] or "library"


def _library_kind(value: str) -> str:
    normalized = str(value or "folder").strip().lower()
    return normalized if normalized in {"folder", "obsidian", "zotero", "notion", "files", "empty"} else "folder"


def _replace_evidence_database(source: Path, destination: Path) -> None:
    """Replace the evidence store, including when Windows keeps it open for reading."""

    try:
        os.replace(source, destination)
        return
    except PermissionError:
        destination.parent.mkdir(parents=True, exist_ok=True)

    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _cleanup_temporary_database(path: Path) -> None:
    """Best-effort cleanup for a replaced SQLite file on Windows.

    sqlite extensions can retain a short-lived handle after a transactional
    backup. The user-visible import has already succeeded at this point, so a
    delayed cleanup must not turn that success into a failed import.
    """

    for _attempt in range(3):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.05)
