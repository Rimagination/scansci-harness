"""Academic MCP marketplace backed by the Official MCP Registry.

The public MCP Registry is the canonical supply-side directory.  It does not
attempt to classify servers by scholarly discipline, so ScanSci keeps a small,
reviewable academic taxonomy on top of the official server metadata.  Remote
catalogue data is fetched only when the user explicitly requests a sync, then
cached next to the local workspace for fast, offline browsing.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .app_settings import load_settings, save_settings
from .external_capabilities import host_policy_defaults


OFFICIAL_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0.1/servers"
OFFICIAL_REGISTRY_HOME = "https://registry.modelcontextprotocol.io/"
_CACHE_NAME = ".scansci-mcp-marketplace.json"
_SYNC_QUERIES = (
    "pubmed",
    "crossref",
    "openalex",
    "arxiv",
    "uniprot",
    "chembl",
    "nasa",
    "usgs",
    "zotero",
    "ncbi",
)
_MAX_CACHED_ITEMS = 120


DISCIPLINES = (
    {"id": "all", "label": "全部学科"},
    {"id": "life", "label": "生命科学"},
    {"id": "medicine", "label": "医学与健康"},
    {"id": "chemistry", "label": "化学与材料"},
    {"id": "math", "label": "数理科学"},
    {"id": "earth", "label": "地球与环境"},
    {"id": "information", "label": "信息科学"},
    {"id": "social", "label": "社会科学"},
    {"id": "general", "label": "科研通用"},
)


def _catalogue_item(
    *,
    identifier: str,
    title: str,
    description: str,
    version: str,
    disciplines: list[str],
    tags: list[str],
    command: str,
    args: str,
    transport: str = "stdio",
    endpoint: str = "",
    repository_url: str = "",
    rank: int = 0,
    provider_kind: str = "",
    connector_kind: str = "general",
    allow_reversible: bool = False,
    deferred: bool = True,
    provider_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": title,
        "description": description,
        "version": version,
        "disciplines": disciplines,
        "tags": tags,
        "command": command,
        "args": args,
        "transport": transport,
        "endpoint": endpoint,
        "source": "Official MCP Registry",
        "source_url": repository_url or OFFICIAL_REGISTRY_HOME,
        "rank": rank,
        "updated_at": "",
        "curated": True,
        "provider_kind": provider_kind,
        "connector_kind": connector_kind,
        "allow_reversible": bool(allow_reversible),
        "deferred": bool(deferred),
        "provider_manifest": deepcopy(provider_manifest or {}),
    }


# These records are deliberately compact, yet fully installable configurations
# obtained from published official-registry entries.  They provide an offline
# research-first catalogue before a user asks to synchronise current data.
_CURATED_ITEMS: tuple[dict[str, Any], ...] = (
    _catalogue_item(
        identifier="io.github.Rimagination/scansci-pdf",
        title="ScanSci PDF · 文献下载",
        description="连接独立维护的 scansci-pdf MCP 插件，提供论文检索、标识符核验和可审计全文下载。",
        version="1.16.0",
        disciplines=["general", "information"],
        tags=["文献下载", "PDF", "DOI", "可插拔 provider"],
        command="scansci-pdf",
        args="run",
        repository_url="https://github.com/Rimagination/scansci-pdf",
        rank=110,
        provider_kind="literature",
        connector_kind="literature",
        allow_reversible=True,
        deferred=True,
        provider_manifest=host_policy_defaults({"provider_kind": "literature"})["provider_manifest"],
    ),
    _catalogue_item(
        identifier="io.github.cyanheads/pubmed-mcp-server",
        title="PubMed / Europe PMC",
        description="检索 PubMed、Europe PMC 与全文可得性，并读取 MeSH、引文和文章详情。",
        version="2.9.8",
        disciplines=["life", "medicine", "general"],
        tags=["文献检索", "生物医学", "全文获取"],
        command="bun",
        args="run @cyanheads/pubmed-mcp-server start:stdio",
        repository_url="https://github.com/cyanheads/pubmed-mcp-server",
        rank=100,
    ),
    _catalogue_item(
        identifier="io.github.cyanheads/openalex-mcp-server",
        title="OpenAlex Academic Catalog",
        description="访问 OpenAlex 学术目录，检索论文、作者、机构、主题与引用关系。",
        version="0.7.4",
        disciplines=["general", "information", "social"],
        tags=["论文元数据", "引文网络", "开放学术"],
        command="node",
        args="run @cyanheads/openalex-mcp-server start:stdio",
        repository_url="https://github.com/cyanheads/openalex-mcp-server",
        rank=98,
    ),
    _catalogue_item(
        identifier="io.github.cyanheads/crossref-mcp-server",
        title="Crossref Scholarly Metadata",
        description="按 DOI 或关键词检索 Crossref 学术元数据、期刊与参考文献。",
        version="0.2.0",
        disciplines=["general", "information"],
        tags=["DOI", "文献检索", "引用"],
        command="bun",
        args="run @cyanheads/crossref-mcp-server start:stdio",
        repository_url="https://github.com/cyanheads/crossref-mcp-server",
        rank=96,
    ),
    _catalogue_item(
        identifier="io.github.cyanheads/arxiv-mcp-server",
        title="arXiv Paper Reader",
        description="检索 arXiv 预印本、获取论文元数据并读取正文内容。",
        version="1.2.15",
        disciplines=["math", "information", "general"],
        tags=["预印本", "AI", "论文检索"],
        command="bun",
        args="run @cyanheads/arxiv-mcp-server start:stdio",
        repository_url="https://github.com/cyanheads/arxiv-mcp-server",
        rank=94,
    ),
    _catalogue_item(
        identifier="io.github.cyanheads/uniprot-mcp-server",
        title="UniProt Protein Knowledgebase",
        description="搜索 UniProtKB，读取蛋白功能、序列、注释、蛋白组和 ID 映射。",
        version="0.2.1",
        disciplines=["life"],
        tags=["蛋白质组", "序列", "生物信息学"],
        command="bun",
        args="run @cyanheads/uniprot-mcp-server start:stdio",
        repository_url="https://github.com/cyanheads/uniprot-mcp-server",
        rank=91,
    ),
    _catalogue_item(
        identifier="io.github.cyanheads/chembl-mcp-server",
        title="ChEMBL Drug Discovery",
        description="连接化合物、蛋白靶点与生物活性，查询药物机制和适应症。",
        version="0.2.0",
        disciplines=["chemistry", "medicine", "life"],
        tags=["化学信息学", "药物发现", "生物活性"],
        command="bun",
        args="run @cyanheads/chembl-mcp-server start:stdio",
        repository_url="https://github.com/cyanheads/chembl-mcp-server",
        rank=89,
    ),
    _catalogue_item(
        identifier="io.github.cyanheads/usgs-water-mcp-server",
        title="USGS Water Data",
        description="查询美国地质调查局的实时和历史水文、河流与地下水观测数据。",
        version="0.2.2",
        disciplines=["earth", "general"],
        tags=["水文", "地学数据", "时间序列"],
        command="bun",
        args="run @cyanheads/usgs-water-mcp-server start:stdio",
        repository_url="https://github.com/cyanheads/usgs-water-mcp-server",
        rank=84,
    ),
    _catalogue_item(
        identifier="io.github.SMABoundless/crossref",
        title="Crossref Metadata (Python)",
        description="使用 Crossref REST API 检索和读取学术出版物元数据。",
        version="1.0.0",
        disciplines=["general", "information"],
        tags=["DOI", "Python", "学术元数据"],
        command="uvx",
        args="crossref-mcp-server",
        repository_url="https://github.com/SMABoundless/crossref-mcp-server",
        rank=79,
    ),
)


def marketplace_cache_path(workspace: str | Path) -> Path:
    return Path(workspace).resolve().parent / _CACHE_NAME


def marketplace_catalog(workspace: str | Path) -> dict[str, Any]:
    """Return the offline-first scholarly catalogue and cache metadata."""

    cache = _read_cache(workspace)
    items_by_id = {str(item["id"]): deepcopy(item) for item in _CURATED_ITEMS}
    for item in cache.get("items", []):
        if isinstance(item, dict) and item.get("id"):
            items_by_id[str(item["id"])] = item
    items = sorted(items_by_id.values(), key=lambda item: (-int(item.get("rank", 0)), str(item.get("title", "")).casefold()))
    result = {
        "items": items,
        "disciplines": list(DISCIPLINES),
        "source": {
            "name": "Official MCP Registry",
            "url": OFFICIAL_REGISTRY_HOME,
            "api_version": "v0.1",
            "description": "统一的公开 MCP 目录；搜索科学在其原始元数据上补充科研学科标签。",
        },
        "synced_at": str(cache.get("synced_at", "")),
        "cached_count": len(cache.get("items", [])),
    }
    result["updates"] = _marketplace_update_records(workspace, items)
    return result


def sync_official_registry(workspace: str | Path, *, timeout: float = 7.0) -> dict[str, Any]:
    """Refresh a science-focused subset from the official, public registry."""

    discovered: list[dict[str, Any]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_registry_query, query, timeout): query for query in _SYNC_QUERIES}
        for future in as_completed(futures):
            query = futures[future]
            try:
                discovered.extend(future.result())
            except (OSError, ValueError, TimeoutError) as exc:
                failures.append(f"{query}: {exc}")

    by_id: dict[str, dict[str, Any]] = {}
    for raw in discovered:
        item = _normalise_registry_item(raw)
        if item is not None:
            prior = by_id.get(item["id"])
            if prior is None or int(item.get("rank", 0)) > int(prior.get("rank", 0)):
                by_id[item["id"]] = item
    cached_items = sorted(by_id.values(), key=lambda item: (-int(item.get("rank", 0)), item["title"].casefold()))[:_MAX_CACHED_ITEMS]
    payload = {
        "schema_version": 2,
        "synced_at": datetime.now(UTC).isoformat(),
        "items": cached_items,
    }
    path = marketplace_cache_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    catalog = marketplace_catalog(workspace)
    catalog["sync"] = {
        "fetched": len(cached_items),
        "queries": len(_SYNC_QUERIES),
        "failures": failures,
    }
    return catalog


def install_marketplace_server(workspace: str | Path, identifier: str) -> dict[str, Any]:
    """Save one marketplace record as a local MCP configuration, never run it."""

    item = next((row for row in marketplace_catalog(workspace)["items"] if row.get("id") == identifier), None)
    if item is None:
        raise ValueError("未在 MCP 广场中找到此服务器，请先刷新目录。")
    settings = load_settings(workspace)
    servers = list(settings.get("mcp_servers", []))
    existing = next((server for server in servers if server.get("catalog_id") == item["id"]), None)
    if existing is not None:
        return {"settings": settings, "record": existing, "created": False}
    record = {
        "id": _record_id(str(item["id"]), servers),
        "catalog_id": item["id"],
        "name": item["title"],
        "description": item["description"],
        "command": item.get("command", ""),
        "args": item.get("args", ""),
        "transport": item.get("transport", "stdio"),
        "endpoint": item.get("endpoint", ""),
        "source": item.get("source", "Official MCP Registry"),
        "source_url": item.get("source_url", OFFICIAL_REGISTRY_HOME),
        "discipline": (item.get("disciplines") or ["general"])[0],
        "tags": item.get("tags", []),
        "version": item.get("version", ""),
        "provider_kind": item.get("provider_kind", ""),
        "connector_kind": item.get("connector_kind", "general"),
        "allow_reversible": bool(item.get("allow_reversible", False)),
        "deferred": bool(item.get("deferred", True)),
        "provider_manifest": item.get("provider_manifest", {}),
        "enabled": True,
    }
    servers.append(record)
    settings["mcp_servers"] = servers
    return {"settings": save_settings(workspace, settings), "record": record, "created": True}


def marketplace_updates(workspace: str | Path) -> dict[str, Any]:
    """Return version comparisons for MCP servers installed from the catalog."""

    catalog = marketplace_catalog(workspace)
    updates = list(catalog.get("updates", []) or [])
    return {
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "updates": updates,
        "available_count": sum(1 for item in updates if item.get("available")),
    }


def update_marketplace_server(workspace: str | Path, identifier: str) -> dict[str, Any]:
    """Apply the latest catalog configuration without starting the MCP process."""

    requested = str(identifier or "").strip()
    if not requested:
        raise ValueError("缺少 MCP 服务器标识。")
    settings = load_settings(workspace)
    servers = list(settings.get("mcp_servers", []) or [])
    index = next(
        (
            position
            for position, server in enumerate(servers)
            if str(server.get("id", "")) == requested or str(server.get("catalog_id", "")) == requested
        ),
        None,
    )
    if index is None:
        raise ValueError("找不到要更新的 MCP 服务器。")
    server = dict(servers[index])
    catalog_id = str(server.get("catalog_id", "") or "")
    item = next((row for row in marketplace_catalog(workspace)["items"] if str(row.get("id", "")) == catalog_id), None)
    if item is None:
        raise ValueError("这个 MCP 服务器没有对应的官方目录记录，请手动维护。")
    current_version = str(server.get("version", "") or "").strip()
    latest_version = str(item.get("version", "") or "").strip()
    if not _is_newer_version(latest_version, current_version):
        return {"settings": settings, "record": server, "updated": False, "update": _update_record(server, item)}

    # Keep user-owned switches and connector permissions.  Only registry-owned
    # connection metadata is replaced, and the process is never launched here.
    replacement = {
        **server,
        "name": item.get("title", server.get("name", "")),
        "description": item.get("description", server.get("description", "")),
        "command": item.get("command", server.get("command", "")),
        "args": item.get("args", server.get("args", "")),
        "transport": item.get("transport", server.get("transport", "stdio")),
        "endpoint": item.get("endpoint", server.get("endpoint", "")),
        "source": item.get("source", server.get("source", "Official MCP Registry")),
        "source_url": item.get("source_url", server.get("source_url", OFFICIAL_REGISTRY_HOME)),
        "discipline": (item.get("disciplines") or [server.get("discipline", "general")])[0],
        "tags": item.get("tags", server.get("tags", [])),
        "version": latest_version,
        "provider_kind": item.get("provider_kind", server.get("provider_kind", "")),
        "connector_kind": item.get("connector_kind", server.get("connector_kind", "general")),
        "allow_reversible": bool(item.get("allow_reversible", server.get("allow_reversible", False))),
        "deferred": bool(item.get("deferred", server.get("deferred", True))),
        "provider_manifest": item.get("provider_manifest", server.get("provider_manifest", {})),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    servers[index] = replacement
    settings["mcp_servers"] = servers
    saved = save_settings(workspace, settings)
    record = next((row for row in saved.get("mcp_servers", []) if str(row.get("id", "")) == str(replacement.get("id", ""))), replacement)
    return {
        "settings": saved,
        "record": record,
        "updated": True,
        "update": _update_record(record, item),
    }


def _marketplace_update_records(workspace: str | Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = load_settings(workspace)
    catalogue = {str(item.get("id", "")): item for item in items if isinstance(item, dict) and item.get("id")}
    updates: list[dict[str, Any]] = []
    for server in list(settings.get("mcp_servers", []) or []):
        catalog_id = str(server.get("catalog_id", "") or "")
        if not catalog_id:
            continue
        item = catalogue.get(catalog_id)
        if item is None:
            updates.append({
                "id": str(server.get("id", "")),
                "catalog_id": catalog_id,
                "state": "unavailable",
                "available": False,
                "message": "官方目录暂未找到此服务器",
            })
            continue
        updates.append(_update_record(server, item))
    return updates


def _update_record(server: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    current = str(server.get("version", "") or "").strip()
    latest = str(item.get("version", "") or "").strip()
    if not current or not latest:
        state = "unknown"
        available = False
        message = "版本信息不完整，请手动核对"
    else:
        try:
            available = _is_newer_version(latest, current)
            state = "available" if available else "current"
            message = "发现可更新版本" if available else "当前已是最新版本"
        except ValueError:
            state = "unknown"
            available = False
            message = "版本格式无法比较，请手动核对"
    return {
        "id": str(server.get("id", "")),
        "catalog_id": str(server.get("catalog_id", "")),
        "name": str(server.get("name", "") or item.get("title", "")),
        "current_version": current,
        "latest_version": latest,
        "state": state,
        "available": available,
        "message": message,
        "source_url": item.get("source_url", OFFICIAL_REGISTRY_HOME),
        "updated_at": item.get("updated_at", ""),
    }


def _is_newer_version(latest: str, current: str) -> bool:
    if not latest or not current:
        return False
    return _version_key(latest) > _version_key(current)


def _version_key(value: str) -> tuple[tuple[int, ...], int, str]:
    clean = str(value).strip().lstrip("vV")
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:[-+]?([0-9A-Za-z.-]+))?", clean)
    if not match:
        raise ValueError(f"无效版本号：{value}")
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    prerelease = match.group(2) or ""
    return tuple(parts), 1 if not prerelease else 0, prerelease


def _read_cache(workspace: str | Path) -> dict[str, Any]:
    path = marketplace_cache_path(workspace)
    if not path.is_file():
        return {"items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return {"items": []}
    return payload


def _fetch_registry_query(query: str, timeout: float) -> list[dict[str, Any]]:
    url = f"{OFFICIAL_REGISTRY_URL}?{urlencode({'limit': 24, 'version': 'latest', 'search': query})}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ScanSci/0.4 MCP Marketplace"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official HTTPS registry URL
        payload = json.loads(response.read().decode("utf-8"))
    servers = payload.get("servers") if isinstance(payload, dict) else []
    return [item for item in servers if isinstance(item, dict)]


def _normalise_registry_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    server = raw.get("server") if isinstance(raw.get("server"), dict) else raw
    identifier = str(server.get("name", "")).strip()
    description = str(server.get("description", "")).strip()
    if not identifier or not description:
        return None
    title = str(server.get("title") or identifier.rsplit("/", 1)[-1]).strip()
    package = _preferred_package(server.get("packages"))
    remote = _preferred_remote(server.get("remotes"))
    command, args, transport, endpoint = _connection_from_official(package, remote)
    disciplines, tags = _academic_taxonomy(" ".join((identifier, title, description)))
    metadata = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    official_meta = metadata.get("io.modelcontextprotocol.registry/official", {}) if isinstance(metadata, dict) else {}
    repository = server.get("repository") if isinstance(server.get("repository"), dict) else {}
    source_url = str(repository.get("url") or server.get("websiteUrl") or OFFICIAL_REGISTRY_HOME)
    updated_at = str(official_meta.get("updatedAt") or official_meta.get("publishedAt") or "")
    return {
        "id": identifier,
        "title": title[:100],
        "description": description[:400],
        "version": str(server.get("version", ""))[:80],
        "disciplines": disciplines,
        "tags": tags,
        "command": command,
        "args": args,
        "transport": transport,
        "endpoint": endpoint,
        "source": "Official MCP Registry",
        "source_url": source_url[:500],
        "rank": _rank_item(disciplines, tags, package, remote),
        "updated_at": updated_at[:48],
        "curated": False,
    }


def _preferred_package(value: object) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    packages = [item for item in value if isinstance(item, dict)]
    return next((item for item in packages if str((item.get("transport") or {}).get("type", "")) == "stdio"), packages[0] if packages else None)


def _preferred_remote(value: object) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    remotes = [item for item in value if isinstance(item, dict)]
    return next((item for item in remotes if str(item.get("type", "")) == "streamable-http"), remotes[0] if remotes else None)


def _connection_from_official(package: dict[str, Any] | None, remote: dict[str, Any] | None) -> tuple[str, str, str, str]:
    if package is not None:
        runtime = str(package.get("runtimeHint") or _runtime_for_registry(str(package.get("registryType", "")))).strip()
        identifier = str(package.get("identifier", "")).strip()
        arguments = _argument_text(package.get("runtimeArguments")) + _argument_text(package.get("packageArguments"))
        args = " ".join(part for part in (identifier, arguments) if part).strip()
        transport_info = package.get("transport") if isinstance(package.get("transport"), dict) else {}
        transport = str(transport_info.get("type") or "stdio")
        endpoint = str(transport_info.get("url") or "")
        return runtime, args, transport, endpoint
    if remote is not None:
        return "", "", str(remote.get("type") or "streamable-http"), str(remote.get("url") or "")
    return "", "", "stdio", ""


def _argument_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for argument in value:
        if not isinstance(argument, dict):
            continue
        name = str(argument.get("name", "")).strip()
        raw_value = str(argument.get("value", "")).strip()
        if name and raw_value:
            parts.append(f"{name} {raw_value}")
        elif raw_value:
            parts.append(raw_value)
    return " ".join(parts)


def _runtime_for_registry(registry_type: str) -> str:
    return {"npm": "npx", "pypi": "uvx", "cargo": "cargo", "oci": "docker"}.get(registry_type.lower(), "")


def _academic_taxonomy(text: str) -> tuple[list[str], list[str]]:
    value = text.casefold()
    labels: list[str] = []
    tags: list[str] = []

    def matches(words: tuple[str, ...]) -> bool:
        return any(word in value for word in words)

    if matches(("pubmed", "medline", "clinical", "drug", "disease", "patient", "health", "chembl")):
        labels.append("medicine")
        tags.append("医学健康")
    if matches(("uniprot", "protein", "genome", "gene", "sequence", "omics", "biolog", "ncbi", "pubmed", "chembl")):
        labels.append("life")
        tags.append("生命科学")
    if matches(("chembl", "compound", "chemistry", "molecule", "material", "reaction", "drug")):
        labels.append("chemistry")
        tags.append("化学信息")
    if matches(("nasa", "usgs", "earth", "climate", "water", "geolog", "weather", "ocean", "satellite", "astronom")):
        labels.append("earth")
        tags.append("地球观测")
    if matches(("arxiv", "math", "physics", "statistic", "astronom", "quantum")):
        labels.append("math")
        tags.append("数理研究")
    if matches(("arxiv", "openalex", "crossref", "zotero", "citation", "doi", "code", "data", "machine learning", "ai")):
        labels.append("information")
        tags.append("科研数据")
    if matches(("econom", "policy", "social", "survey", "census", "law", "humanities")):
        labels.append("social")
        tags.append("社会科学")
    if matches(("openalex", "crossref", "zotero", "pubmed", "arxiv", "citation", "paper", "research", "scholar")):
        labels.append("general")
        tags.append("文献检索")
    if not labels:
        labels.append("general")
        tags.append("科研通用")
    return list(dict.fromkeys(labels)), list(dict.fromkeys(tags))[:4]


def _rank_item(disciplines: list[str], tags: list[str], package: dict[str, Any] | None, remote: dict[str, Any] | None) -> int:
    rank = 30 + len(disciplines) * 5 + len(tags) * 2
    if package is not None:
        rank += 9
    if remote is not None:
        rank += 4
    if "文献检索" in tags:
        rank += 10
    return rank


def _record_id(identifier: str, existing: list[dict[str, Any]]) -> str:
    base = "mcp-" + "".join(char if char.isalnum() else "-" for char in identifier.casefold()).strip("-")[:48]
    used = {str(item.get("id", "")) for item in existing}
    candidate = base or "mcp-server"
    suffix = 2
    while candidate in used:
        candidate = f"{base[:55]}-{suffix}"
        suffix += 1
    return candidate
