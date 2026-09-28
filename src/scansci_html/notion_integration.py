"""Read-only Notion synchronization for local ScanSci knowledge bases."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable
from urllib import error, parse, request
from uuid import uuid4

from .app_settings import get_notion_api_token


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_COMPACT_UUID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.I)


class NotionIntegrationError(RuntimeError):
    """A user-facing Notion connection or synchronization failure."""


@dataclass
class NotionPage:
    page_id: str
    title: str
    url: str
    markdown: str
    last_edited_time: str = ""
    parent_id: str = ""
    parent_type: str = "workspace"
    parent_title: str = ""


class NotionClient:
    def __init__(self, token: str, *, timeout: float = 20.0, opener: Any = request.urlopen) -> None:
        self.token = str(token or "").strip()
        if not self.token:
            raise NotionIntegrationError("尚未配置 Notion Integration Token")
        self.timeout = timeout
        self.opener = opener

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = NOTION_API_BASE + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "ScanSci-Harness/NotionConnector",
        }
        for attempt in range(4):
            try:
                with self.opener(request.Request(url, data=data, method=method, headers=headers), timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    result = json.loads(raw or "{}")
                    if not isinstance(result, dict):
                        raise NotionIntegrationError("Notion 返回了无效响应")
                    return result
            except error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(raw).get("message", raw)
                except Exception:
                    detail = raw
                if exc.code == 429 and attempt < 3:
                    retry_after = int(exc.headers.get("Retry-After", "1") or 1)
                    time.sleep(min(max(retry_after, 1), 8))
                    continue
                if exc.code in {401, 403}:
                    raise NotionIntegrationError("Notion Token 无效，或 Integration 尚未获得页面访问权限") from exc
                if exc.code == 404:
                    raise NotionIntegrationError("找不到 Notion 页面，或该页面尚未分享给 Integration") from exc
                raise NotionIntegrationError(f"Notion API 请求失败（HTTP {exc.code}）：{detail}") from exc
            except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < 3:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise NotionIntegrationError(f"无法连接 Notion：{exc}") from exc
        raise NotionIntegrationError("Notion API 请求失败")

    def test(self) -> dict[str, Any]:
        result = self.request_json("GET", "/users/me")
        return {"ok": True, "bot_id": str(result.get("id", "")), "name": _person_name(result)}

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self.request_json("GET", f"/pages/{_clean_id(page_id)}")

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self.request_json("GET", f"/databases/{_clean_id(database_id)}")

    def children(self, block_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = ""
        while True:
            suffix = f"?page_size=100{parse.quote(cursor)}" if cursor else "?page_size=100"
            if cursor:
                suffix = f"?page_size=100&start_cursor={parse.quote(cursor)}"
            response = self.request_json("GET", f"/blocks/{_clean_id(block_id)}/children{suffix}")
            results.extend(item for item in response.get("results", []) if isinstance(item, dict))
            if not response.get("has_more") or not response.get("next_cursor"):
                return results
            cursor = str(response["next_cursor"])

    def query_data_source(self, data_source_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = ""
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            response = self.request_json("POST", f"/data_sources/{_clean_id(data_source_id)}/query", payload)
            results.extend(item for item in response.get("results", []) if isinstance(item, dict))
            if not response.get("has_more") or not response.get("next_cursor"):
                return results
            cursor = str(response["next_cursor"])

    def search_all(self, *, max_results: int = 5000) -> list[dict[str, Any]]:
        """Return every page/database the Integration is allowed to see."""
        results: list[dict[str, Any]] = []
        cursor = ""
        while len(results) < max_results:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            response = self.request_json("POST", "/search", payload)
            results.extend(item for item in response.get("results", []) if isinstance(item, dict))
            if not response.get("has_more") or not response.get("next_cursor"):
                break
            cursor = str(response["next_cursor"])
        return results[:max_results]

    def _complete_page(self, page: dict[str, Any]) -> dict[str, Any]:
        """Retrieve a child-page block before relying on its title or parent."""

        page_id = _clean_id(page.get("id", ""))
        if not page_id:
            return page
        parent = page.get("parent")
        if _page_title(page) and page.get("url") and isinstance(parent, dict):
            return page
        detail = self.retrieve_page(page_id)
        return {**page, **detail}

    def export_tree(self, root_page_id: str, *, max_pages: int = 2000) -> list[NotionPage]:
        root = self.retrieve_page(root_page_id)
        pages: list[NotionPage] = []
        seen: set[str] = set()

        def visit(
            page: dict[str, Any],
            *,
            parent_id: str = "",
            parent_type: str = "",
            parent_title: str = "",
        ) -> None:
            page_id = _clean_id(page.get("id", ""))
            if not page_id or page_id in seen or len(pages) >= max_pages:
                return
            page = self._complete_page(page)
            seen.add(page_id)
            title = _page_title(page) or parent_title or "未命名页面"
            actual_parent_id, actual_parent_type = _parent_reference(page)
            resolved_parent_id = actual_parent_id or _clean_id(parent_id)
            resolved_parent_type = actual_parent_type or parent_type or "workspace"
            blocks = self.children(page_id)
            body = _blocks_to_markdown(
                blocks,
                self,
                depth=0,
                on_page=visit,
                parent_page_id=page_id,
                parent_page_title=title,
                seen=seen,
                max_pages=max_pages,
                pages=pages,
            )
            pages.append(
                NotionPage(
                    page_id,
                    title,
                    str(page.get("url", "")),
                    f"# {title}\n\n{body}".strip() + "\n",
                    str(page.get("last_edited_time", "")),
                    resolved_parent_id,
                    resolved_parent_type,
                    _notion_title_text(parent_title),
                )
            )

        visit(root)
        return pages

    def export_all(self, *, max_pages: int = 5000) -> list[NotionPage]:
        """Export all pages and database rows visible to this Integration."""
        results = self.search_all(max_results=max_pages * 2)
        pages: list[NotionPage] = []
        seen: set[str] = set()

        def visit(
            page: dict[str, Any],
            *,
            parent_id: str = "",
            parent_type: str = "",
            parent_title: str = "",
        ) -> None:
            page_id = _clean_id(page.get("id", ""))
            if not page_id or page_id in seen or len(pages) >= max_pages:
                return
            page = self._complete_page(page)
            seen.add(page_id)
            title = _page_title(page) or parent_title or "未命名页面"
            actual_parent_id, actual_parent_type = _parent_reference(page)
            resolved_parent_id = actual_parent_id or _clean_id(parent_id)
            resolved_parent_type = actual_parent_type or parent_type or "workspace"
            blocks = self.children(page_id)
            body = _blocks_to_markdown(
                blocks,
                self,
                depth=0,
                on_page=visit,
                parent_page_id=page_id,
                parent_page_title=title,
                seen=seen,
                max_pages=max_pages,
                pages=pages,
            )
            pages.append(
                NotionPage(
                    page_id,
                    title,
                    str(page.get("url", "")),
                    f"# {title}\n\n{body}".strip() + "\n",
                    str(page.get("last_edited_time", "")),
                    resolved_parent_id,
                    resolved_parent_type,
                    _notion_title_text(parent_title),
                )
            )

        # Query database rows before plain search results.  Search can return a
        # row before its database; this preserves a meaningful virtual parent
        # label for data-source pages even when that row is already seen later.
        for item in results:
            if item.get("object") in {"database", "data_source"}:
                data_sources = item.get("data_sources") or []
                if item.get("object") == "data_source" and not data_sources:
                    data_sources = [item]
                for source in data_sources:
                    source_id = str(source.get("id", "")).strip() if isinstance(source, dict) else ""
                    if not source_id:
                        continue
                    for row in self.query_data_source(source_id):
                        visit(
                            row,
                            parent_id=source_id,
                            parent_type="data_source_id",
                            parent_title=_page_title(item) or "Notion 数据库",
                        )
        for item in results:
            if item.get("object") == "page":
                visit(item)
        return pages


def test_notion_connection(workspace: str | Path, *, token: str | None = None) -> dict[str, Any]:
    return NotionClient(token or get_notion_api_token(workspace)).test()


def sync_notion_library(
    workspace: str | Path,
    evidence_db: str | Path,
    *,
    notebook_id: str,
    root_page_id: str = "",
    title: str = "Notion 知识库",
    token: str | None = None,
) -> dict[str, Any]:
    """Fetch a Notion tree into managed Markdown and rebuild its evidence index."""

    from .library_manager import import_library_folder
    from .workspace import update_notebook_metadata

    workspace_path = Path(workspace).resolve()
    root_id = _clean_id(root_page_id)
    cache_root = workspace_path.parent / ".scansci-library" / "notion" / notebook_id
    client = NotionClient(token or get_notion_api_token(workspace_path))
    pages = client.export_tree(root_id) if root_id else client.export_all()
    if not pages:
        raise NotionIntegrationError("没有找到可同步的 Notion 内容。请确认页面已通过 Add connections 授权给 ScanSci。")
    manifest = {"root_page_id": root_id, "sync_scope": "root_page" if root_id else "all_accessible", "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pages": []}
    pages_by_id = {page.page_id: page for page in pages}
    cache_root.parent.mkdir(parents=True, exist_ok=True)
    backup_root = cache_root.with_name(f".{cache_root.name}.backup-{uuid4().hex}")
    if cache_root.exists():
        cache_root.rename(backup_root)
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        for page in pages:
            relative_path = _notion_cache_relative_path(page, pages_by_id)
            path = cache_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\ntitle: {_yaml(page.title)}\nsource_url: {_yaml(page.url)}\nnotion_page_id: {_yaml(page.page_id)}\nnotion_parent_id: {_yaml(page.parent_id)}\nnotion_parent_type: {_yaml(page.parent_type)}\nnotion_parent_title: {_yaml(page.parent_title)}\nlast_edited_time: {_yaml(page.last_edited_time)}\nsource_storage: external-reference\n---\n\n{page.markdown}",
                encoding="utf-8",
            )
            manifest["pages"].append(
                {
                    "id": page.page_id,
                    "title": page.title,
                    "url": page.url,
                    "last_edited_time": page.last_edited_time,
                    "parent_id": page.parent_id,
                    "parent_type": page.parent_type,
                    "parent_title": page.parent_title,
                    "path": relative_path.as_posix(),
                }
            )
        (cache_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result = import_library_folder(workspace_path, evidence_db, notebook_id=notebook_id, folder_path=cache_root, library_kind="notion")
    except Exception:
        if cache_root.exists():
            shutil.rmtree(cache_root)
        if backup_root.exists():
            backup_root.rename(cache_root)
        raise
    else:
        if backup_root.exists():
            shutil.rmtree(backup_root)
    update_notebook_metadata(workspace_path, notebook_id=notebook_id, metadata={"library_kind": "notion", "notion": {"root_page_id": root_id, "sync_scope": manifest["sync_scope"], "page_count": len(pages), "last_sync": manifest["synced_at"], "cache_path": str(cache_root)}})
    result.update({"notion": manifest, "page_count": len(pages), "cache_path": str(cache_root)})
    return result


def _blocks_to_markdown(
    blocks: list[dict[str, Any]],
    client: NotionClient,
    *,
    depth: int,
    on_page: Callable[..., None],
    parent_page_id: str,
    parent_page_title: str,
    seen: set[str],
    max_pages: int,
    pages: list[NotionPage],
) -> str:
    lines: list[str] = []
    for block in blocks:
        kind = str(block.get("type", ""))
        data = block.get(kind, {}) if isinstance(block.get(kind), dict) else {}
        text = _rich_text(data.get("rich_text") or data.get("caption") or [])
        if kind == "paragraph": lines.append(text)
        elif kind.startswith("heading_"): lines.append(f"{'#' * min(int(kind[-1]) + 1, 6)} {text}".rstrip())
        elif kind == "bulleted_list_item": lines.append(f"- {text}")
        elif kind == "numbered_list_item": lines.append(f"1. {text}")
        elif kind == "to_do": lines.append(f"- [{'x' if data.get('checked') else ' '}] {text}")
        elif kind == "quote": lines.append(f"> {text}")
        elif kind == "code": lines.append(f"```{data.get('language', '')}\n{text}\n```")
        elif kind == "divider": lines.append("---")
        elif kind in {"child_page", "child_database"}: lines.append(f"## {text or data.get('title', '嵌套内容')}")
        elif text: lines.append(text)
        if block.get("has_children"):
            child_id = str(block.get("id", ""))
            nested = client.children(child_id)
            if kind == "child_page":
                on_page(
                    {"id": child_id},
                    parent_id=parent_page_id,
                    parent_type="page_id",
                    parent_title=parent_page_title,
                )
            elif kind == "child_database":
                database = client.retrieve_database(child_id)
                data_sources = database.get("data_sources") or []
                if not data_sources:
                    data_sources = [{"id": child_id}]
                for source in data_sources:
                    source_id = str(source.get("id", "")) if isinstance(source, dict) else ""
                    for row in client.query_data_source(source_id):
                        on_page(
                            row,
                            parent_id=source_id,
                            parent_type="data_source_id",
                            parent_title=_page_title(database) or str(data.get("title", "Notion 数据库")),
                        )
            elif nested:
                lines.append(
                    _blocks_to_markdown(
                        nested,
                        client,
                        depth=depth + 1,
                        on_page=on_page,
                        parent_page_id=parent_page_id,
                        parent_page_title=parent_page_title,
                        seen=seen,
                        max_pages=max_pages,
                        pages=pages,
                    )
                )
    return "\n\n".join(line for line in lines if line.strip()).strip()


def _rich_text(items: list[dict[str, Any]]) -> str:
    return "".join(str(item.get("plain_text") or item.get("text", {}).get("content") or "") for item in items if isinstance(item, dict))


def _page_title(page: dict[str, Any]) -> str:
    properties = page.get("properties", {})
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and (prop.get("type") == "title" or "title" in prop):
                title = _rich_text(prop.get("title", []))
                if title:
                    return title
    return _notion_title_text(page.get("title", ""))


def _notion_title_text(value: object) -> str:
    if isinstance(value, list):
        title = _rich_text(value)
        if title:
            return title
    if isinstance(value, dict):
        title = _rich_text(value.get("title") or value.get("rich_text") or [])
        if title:
            return title
    text = str(value or "").strip()
    # Some database-list responses expose title rich text after an adapter has
    # stringified it. Recover the human title rather than using that repr as a
    # cache folder name.
    if text.startswith("[{"):
        try:
            return _notion_title_text(ast.literal_eval(text)) or text
        except (SyntaxError, ValueError):
            return text
    return text


def _parent_reference(page: dict[str, Any]) -> tuple[str, str]:
    parent = page.get("parent")
    if not isinstance(parent, dict):
        return "", ""
    parent_type = str(parent.get("type", "") or "")
    key = {
        "page_id": "page_id",
        "database_id": "database_id",
        "data_source_id": "data_source_id",
        "block_id": "block_id",
    }.get(parent_type, "")
    return (_clean_id(parent.get(key, "")) if key else "", parent_type)


def _notion_cache_stem(title: str, identifier: str) -> str:
    digest = hashlib.sha1(str(identifier).encode("utf-8")).hexdigest()[:10]
    return f"{_safe_filename(title)}--{digest}"


def _notion_cache_relative_path(page: NotionPage, pages_by_id: dict[str, NotionPage]) -> Path:
    """Create a stable local path following Notion's known parent chain."""

    ancestors: list[str] = []
    cursor = page
    seen = {page.page_id}
    while cursor.parent_id:
        parent = pages_by_id.get(cursor.parent_id)
        if parent and parent.page_id not in seen:
            ancestors.append(_notion_cache_stem(parent.title, parent.page_id))
            seen.add(parent.page_id)
            cursor = parent
            continue
        if cursor.parent_title:
            ancestors.append(_notion_cache_stem(cursor.parent_title, cursor.parent_id or cursor.parent_type))
        break
    ancestors.reverse()
    return Path(*ancestors, f"{_notion_cache_stem(page.title, page.page_id)}.md")


def _person_name(data: dict[str, Any]) -> str:
    person = data.get("name") or data.get("bot", {}).get("owner", {}).get("user", {}).get("name")
    return str(person or "Notion Integration")


def _clean_id(value: object) -> str:
    raw = str(value or "")
    match = _UUID_RE.search(raw)
    if match:
        return match.group(0)
    compact = _COMPACT_UUID_RE.search(raw)
    if not compact:
        return ""
    value = compact.group(1).lower()
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"


def _safe_filename(value: str) -> str:
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", value).strip(" .") or "notion-page"
    return text[:100]


def _yaml(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)
