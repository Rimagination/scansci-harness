"""ScanSci-native Agent Reach capability layer.

This module adapts the routing idea from Panniantong/Agent-Reach to the
ScanSci runtime.  It deliberately exposes a small, read-only API instead of
installing the upstream CLI or running arbitrary shell commands.  The
zero-install paths use dependencies already shipped with ScanSci plus public
HTTP endpoints; optional tools are reported by ``status`` and never required
for startup.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import ipaddress
import json
import os
from pathlib import PurePosixPath
import re
import shutil
import socket
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .web_search import search_public_web


AGENT_REACH_SCHEMA_VERSION = "scansci.agent-reach.v1"
_USER_AGENT = "ScanSci-Harness/0.2 Agent-Reach-compatible reader"
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_CONTENT_CHARS = 60_000
_DEFAULT_TIMEOUT = 30.0
_SUPPORTED_CHANNELS = {
    "auto",
    "web",
    "rss",
    "github",
    "youtube",
    "bilibili",
    "v2ex",
    "xueqiu",
    "twitter",
    "reddit",
    "xiaohongshu",
    "facebook",
    "instagram",
    "linkedin",
}


class AgentReachError(ValueError):
    """Raised when an Agent Reach request is invalid or cannot be fulfilled."""


def agent_reach_status() -> dict[str, Any]:
    """Return a no-network capability report for the built-in routes."""

    optional = {
        "yt-dlp": bool(importlib.util.find_spec("yt_dlp")),
        "gh": shutil.which("gh") is not None,
        "opencli": "probe_on_demand",
    }
    channels = [
        _channel_status("web", "Jina Reader", "ready", "任意公开网页，无需额外安装"),
        _channel_status("rss", "stdlib XML parser", "ready", "RSS/Atom，无需额外安装"),
        _channel_status("github", "GitHub public REST API", "ready", "公开仓库和公开 Issue/PR，无需 gh CLI"),
        _channel_status("bilibili", "B站公开搜索 API", "ready", "搜索无需登录；视频正文按网页兜底"),
        _channel_status("v2ex", "V2EX public API", "ready", "公开主题和网页阅读，无需额外安装"),
        _channel_status("youtube", "optional yt-dlp / Jina Reader", "fallback", "视频元数据和公开网页无需安装；完整字幕转写仍需额外音频转写能力"),
        _channel_status("xueqiu", "Jina Reader", "fallback", "公开页面读取；平台接口可能要求登录"),
        _channel_status("twitter", "Jina Reader", "fallback", "公开链接读取；搜索和时间线需要登录态后端"),
        _channel_status("reddit", "Jina Reader", "fallback", "公开链接读取；搜索和评论可能需要登录态"),
        _channel_status("xiaohongshu", "Jina Reader", "fallback", "公开链接尝试读取；不注入 Cookie、不代替登录"),
        _channel_status("facebook", "Jina Reader", "fallback", "公开链接尝试读取；登录态需用户自行配置浏览器"),
        _channel_status("instagram", "Jina Reader", "fallback", "公开链接尝试读取；登录态需用户自行配置浏览器"),
        _channel_status("linkedin", "Jina Reader", "fallback", "公开页面读取；登录态能力按现有浏览器连接情况决定"),
    ]
    return {
        "ok": True,
        "schema_version": AGENT_REACH_SCHEMA_VERSION,
        "source": "ScanSci built-in adaptation of Agent-Reach",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "zero_install": True,
        "optional_backends": optional,
        "channels": channels,
        "security": {
            "read_only": True,
            "public_url_only": True,
            "cookie_injection": False,
            "shell_execution": False,
        },
    }


def run_agent_reach(
    operation: str,
    *,
    target: str = "",
    query: str = "",
    channel: str = "auto",
    limit: int = 8,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Execute one bounded Agent Reach operation for the Pi bridge."""

    normalized_operation = str(operation or "").strip().lower()
    normalized_channel = str(channel or "auto").strip().lower()
    if normalized_operation not in {"status", "read", "search"}:
        raise AgentReachError("operation must be status, read, or search")
    if normalized_channel not in _SUPPORTED_CHANNELS:
        raise AgentReachError(f"unsupported Agent Reach channel: {normalized_channel}")
    bounded_limit = max(1, min(12, int(limit or 8)))
    bounded_timeout = max(3.0, min(60.0, float(timeout or _DEFAULT_TIMEOUT)))
    if normalized_operation == "status":
        return agent_reach_status()
    if normalized_operation == "read":
        if not str(target or "").strip():
            raise AgentReachError("read requires target")
        return agent_reach_read(
            str(target),
            channel=normalized_channel,
            limit=bounded_limit,
            timeout=bounded_timeout,
        )
    if not str(query or "").strip():
        raise AgentReachError("search requires query")
    return agent_reach_search(
        str(query),
        channel=normalized_channel,
        limit=bounded_limit,
        timeout=bounded_timeout,
    )


def agent_reach_read(
    target: str,
    *,
    channel: str = "auto",
    limit: int = 8,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read a public URL through the most suitable built-in channel."""

    url = _normalize_public_url(target)
    selected = _route_channel(url, channel)
    if selected == "rss":
        return _read_rss(url, limit=limit, timeout=timeout)
    if selected == "github":
        return _read_github(url, timeout=timeout)
    if selected == "youtube":
        return _read_youtube(url, timeout=timeout)
    if selected == "v2ex" and _is_v2ex_api_url(url):
        return _read_json_endpoint(url, channel=selected, timeout=timeout)
    return _read_web(url, channel=selected, timeout=timeout)


def agent_reach_search(
    query: str,
    *,
    channel: str = "auto",
    limit: int = 8,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Search a public channel, falling back to ScanSci's web search."""

    clean_query = " ".join(str(query or "").split())[:1_000]
    selected = _route_search_channel(clean_query, channel)
    if selected == "github":
        try:
            return _search_github(clean_query, limit=limit, timeout=timeout)
        except (AgentReachError, requests.RequestException) as error:
            # The unauthenticated GitHub search API is heavily rate limited;
            # do not fail the whole search when it responds 403/429.
            result = search_public_web(clean_query, limit=limit, timeout=timeout)
            return {
                **result,
                "source": "agent-reach:web-search-fallback",
                "channel": "github",
                "evidence_level": "web-discovery",
                "fallback_reason": f"{type(error).__name__}: {error}"[:240],
            }
    if selected == "bilibili":
        return _search_bilibili(clean_query, limit=limit, timeout=timeout)
    if selected == "v2ex":
        return _search_v2ex(clean_query, limit=limit, timeout=timeout)
    search_query = clean_query
    if selected not in {"auto", "web"}:
        search_query = f"site:{_search_domain(selected)} {clean_query}"
    result = search_public_web(search_query, limit=limit, timeout=timeout)
    return {
        **result,
        "source": "agent-reach:web-search-fallback",
        "channel": selected,
        "evidence_level": "web-discovery",
    }


def _channel_status(channel: str, backend: str, status: str, message: str) -> dict[str, str]:
    return {"channel": channel, "backend": backend, "status": status, "message": message}


def _normalize_public_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise AgentReachError("URL is required")
    if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise AgentReachError("only public http(s) URLs are supported")
    if parsed.username or parsed.password:
        raise AgentReachError("URLs containing credentials are not allowed")
    if _is_private_host(host):
        raise AgentReachError("private, loopback, and local URLs are not allowed")
    try:
        netloc = host
        if parsed.port:
            netloc = f"{host}:{parsed.port}"
    except ValueError as exc:
        raise AgentReachError("invalid URL port") from exc
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _is_private_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.endswith((".local", ".internal")):
            return True
        # Resolve DNS and require every address to be public.  This also
        # catches DNS-rebinding style names such as 127.0.0.1.nip.io whose
        # literal hostname looks public but resolves to a local address.
        try:
            resolved = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError):
            # Unresolvable right now: let the fetch fail naturally rather
            # than misclassifying an unresolvable name as private.
            return False
        return any(_is_private_address(str(item[4][0])) for item in resolved)
    return _is_private_address(str(address))


def _is_private_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _validated_get(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    max_bytes: int = _MAX_RESPONSE_BYTES,
) -> tuple[bytes, list[str]]:
    """GET with per-hop private-host validation and a streaming byte cap.

    Redirects are followed manually so that every hop (including the final
    target) is checked against the same public-URL rules; a public URL that
    redirects to a loopback or cloud-metadata address is rejected instead of
    fetched.  The response body is streamed so the safety limit applies while
    downloading rather than after the whole body is buffered.

    Each hop is issued with ``requests.get`` (no session) so tests and callers
    that patch ``requests.get`` keep working.
    """

    current = url
    hops = [url]
    for _ in range(6):
        response = requests.get(
            current,
            headers=headers,
            params=params,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                response.close()
                raise AgentReachError("redirect response did not include a Location header")
            current = _normalize_public_url(urljoin(current, location))
            hops.append(current)
            response.close()
            continue
        try:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise AgentReachError(
                        f"response exceeded the {max_bytes // (1024 * 1024)} MB safety limit"
                    )
                chunks.append(chunk)
        finally:
            response.close()
        return b"".join(chunks), hops
    raise AgentReachError("too many redirects while fetching URL")


def _route_channel(url: str, requested: str) -> str:
    if requested != "auto":
        return requested
    host = str(urlsplit(url).hostname or "").lower()
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
        return "youtube"
    if host == "bilibili.com" or host.endswith(".bilibili.com") or host == "b23.tv":
        return "bilibili"
    if host == "v2ex.com" or host.endswith(".v2ex.com"):
        return "v2ex"
    if host == "xueqiu.com" or host.endswith(".xueqiu.com"):
        return "xueqiu"
    if host in {"x.com", "twitter.com", "mobile.twitter.com"} or host.endswith(".twitter.com"):
        return "twitter"
    if host == "reddit.com" or host.endswith(".reddit.com"):
        return "reddit"
    if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"):
        return "xiaohongshu"
    if host == "facebook.com" or host.endswith(".facebook.com"):
        return "facebook"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    return "web"


def _route_search_channel(query: str, requested: str) -> str:
    if requested != "auto":
        return requested
    lowered = query.casefold()
    # Keep GitHub routing tied to explicit GitHub intent.  The bare word
    # "issue" is far too common ("climate change issues", "sleep issues") to
    # send every such query to the unauthenticated GitHub search API.
    if re.search(r"(?:github|gh\s+repo|pull\s+request|issue\s+tracker|issues?\s+(?:on|in|at)\s+github)", lowered):
        return "github"
    if re.search(r"(?:bilibili|b站|b23\.tv)", lowered):
        return "bilibili"
    if re.search(r"(?:v2ex)", lowered):
        return "v2ex"
    return "web"


def _search_domain(channel: str) -> str:
    return {
        "youtube": "youtube.com",
        "xueqiu": "xueqiu.com",
        "twitter": "x.com",
        "reddit": "reddit.com",
        "xiaohongshu": "xiaohongshu.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "linkedin": "linkedin.com",
        "v2ex": "v2ex.com",
        "bilibili": "bilibili.com",
    }.get(channel, channel)


def _read_web(url: str, *, channel: str, timeout: float) -> dict[str, Any]:
    # Prefer the actual public endpoint.  Apart from avoiding an unnecessary
    # third-party hop, this is essential for scholarly JSON APIs (OpenAlex,
    # Crossref, etc.): sending an encoded API URL through a reader service can
    # fail even while the API itself is healthy.  Every direct redirect is
    # still validated by ``_validated_get`` before any body is returned.
    direct_errors: list[str] = []
    direct_headers = {
        "Accept": "application/json, text/html;q=0.9, text/plain;q=0.8, */*;q=0.5",
        "User-Agent": _USER_AGENT,
    }
    # Public scholarly APIs occasionally reset a keep-alive connection on
    # Windows. Retry transient transport failures inside this tool call so the
    # agent does not need another model/tool round just to repeat the same URL.
    # Policy and content-safety errors are deliberately not retried.
    for _attempt in range(3):
        try:
            body, hops = _validated_get(
                url,
                headers=direct_headers,
                timeout=timeout,
            )
            direct = _public_body_payload(body)
            content = str(direct.get("content", "") or "")
            sample = content[:4_096].casefold()
            if content and not _looks_like_block_page(sample):
                return {
                    "ok": True,
                    "operation": "read",
                    "channel": channel,
                    "backend": "direct public HTTP",
                    "source": "agent-reach:direct",
                    "evidence_level": "page-content",
                    "url": url,
                    "final_url": hops[-1],
                    "redirect_chain": hops,
                    **direct,
                    "content": content[:_MAX_CONTENT_CHARS],
                    "truncated": len(content) > _MAX_CONTENT_CHARS,
                }
            direct_errors.append("direct response was empty or an anti-bot page")
            break
        except AgentReachError as error:
            message = str(error)
            if "private" in message or "credentials" in message:
                raise
            direct_errors.append(f"{type(error).__name__}: {error}"[:500])
            break
        except (requests.ConnectionError, requests.Timeout, OSError) as error:
            direct_errors.append(f"{type(error).__name__}: {error}"[:500])
            continue
        except requests.RequestException as error:
            direct_errors.append(f"{type(error).__name__}: {error}"[:500])
            break

    direct_error = "; ".join(direct_errors)[-1_000:]

    # The reader endpoint receives the target as a single path segment.  The
    # target's own query string must be percent-encoded; otherwise it would be
    # parsed as the reader's query and the page fetched without its query.
    jina_url = f"https://r.jina.ai/{quote(url, safe='')}"
    body, hops = _validated_get(
        jina_url,
        headers={"Accept": "text/plain", "User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    text = body.decode("utf-8", errors="replace")
    sample = text[:4_096].casefold()
    if _looks_like_block_page(sample):
        raise AgentReachError("reader returned an anti-bot page; use a permitted browser session or another source")
    return {
        "ok": True,
        "operation": "read",
        "channel": channel,
        "backend": "Jina Reader",
        "source": "agent-reach:web",
        "evidence_level": "page-content",
        "url": url,
        "reader_url": jina_url,
        "content": text[:_MAX_CONTENT_CHARS],
        "truncated": len(text) > _MAX_CONTENT_CHARS,
        "fallback_reason": direct_error,
    }


def _public_body_payload(body: bytes) -> dict[str, Any]:
    """Turn a bounded public response into model-readable text and metadata."""

    text = body.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
    if not text:
        return {"content": "", "content_type": "empty"}
    if text[:1] in {"{", "["}:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            rendered = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            return {
                "content": rendered,
                "content_type": "application/json",
                "data": data,
            }
    lowered = text[:1_000].casefold()
    if "<html" in lowered or "<!doctype html" in lowered or "<body" in lowered:
        soup = BeautifulSoup(text, "html.parser")
        for node in soup(["script", "style", "noscript", "template"]):
            node.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        readable = "\n".join(
            line.strip()
            for line in soup.get_text("\n", strip=True).splitlines()
            if line.strip()
        )
        return {
            "content": readable,
            "content_type": "text/html",
            "title": title,
        }
    return {"content": text, "content_type": "text/plain"}


def _looks_like_block_page(sample: str) -> bool:
    normalized = str(sample or "").casefold()
    return bool(
        ("requiring captcha" in normalized and "just a moment" in normalized)
        or "attention required! | cloudflare" in normalized
        or ("verify you are human" in normalized and "cloudflare" in normalized)
    )


def _read_json_endpoint(url: str, *, channel: str, timeout: float) -> dict[str, Any]:
    payload = _get_json(url, timeout=timeout)
    return {
        "ok": True,
        "operation": "read",
        "channel": channel,
        "backend": "public JSON API",
        "source": "agent-reach:public-api",
        "evidence_level": "page-content",
        "url": url,
        "data": payload,
    }


def _read_youtube(url: str, *, timeout: float) -> dict[str, Any]:
    """Use an already-installed yt-dlp for metadata, with a Jina fallback."""

    try:
        import yt_dlp  # type: ignore[import-not-found]
    except ImportError:
        return _read_web(url, channel="youtube", timeout=timeout)
    try:
        options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
    except Exception:
        return _read_web(url, channel="youtube", timeout=timeout)
    if not isinstance(info, dict):
        return _read_web(url, channel="youtube", timeout=timeout)
    return {
        "ok": True,
        "operation": "read",
        "channel": "youtube",
        "backend": "yt-dlp" if importlib.util.find_spec("yt_dlp") else "Jina Reader",
        "source": "agent-reach:youtube",
        "evidence_level": "page-content",
        "url": url,
        "data": {
            key: info.get(key)
            for key in ("id", "title", "description", "uploader", "channel", "upload_date", "duration", "webpage_url")
            if info.get(key) not in {None, ""}
        },
        "subtitle_languages": sorted(set(
            list(dict(info.get("subtitles", {}) or {}).keys())
            + list(dict(info.get("automatic_captions", {}) or {}).keys())
        )),
        "note": "Metadata and available subtitle languages were read; no audio was downloaded or transcribed.",
    }


def _read_rss(url: str, *, limit: int, timeout: float) -> dict[str, Any]:
    body, _hops = _validated_get(
        url,
        headers={"Accept": "application/rss+xml, application/atom+xml, text/xml", "User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise AgentReachError("the target is not valid RSS or Atom XML") from exc
    entries: list[dict[str, str]] = []
    for node in root.iter():
        if _xml_name(node.tag) not in {"item", "entry"}:
            continue
        entry = {
            "title": _xml_child_text(node, "title"),
            "url": _xml_child_link(node),
            "summary": _xml_child_text(node, "description") or _xml_child_text(node, "summary") or _xml_child_text(node, "content"),
            "published_at": _xml_child_text(node, "pubDate") or _xml_child_text(node, "published") or _xml_child_text(node, "updated"),
        }
        if entry["title"] or entry["url"]:
            entries.append({key: value[:4_000] for key, value in entry.items()})
        if len(entries) >= max(1, min(12, int(limit or 8))):
            break
    return {
        "ok": True,
        "operation": "read",
        "channel": "rss",
        "backend": "stdlib XML parser",
        "source": "agent-reach:rss",
        "evidence_level": "page-content",
        "url": url,
        "feed_title": _xml_child_text(root, "title"),
        "count": len(entries),
        "items": entries,
    }


def _read_github(url: str, *, timeout: float) -> dict[str, Any]:
    parsed = urlsplit(url)
    parts = [part for part in PurePosixPath(parsed.path).parts if part not in {"/", ""}]
    if len(parts) < 2:
        raise AgentReachError("a GitHub URL must include owner and repository")
    owner, repository = parts[0], parts[1].removesuffix(".git")
    api_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}"
    kind = "repository"
    if len(parts) >= 4 and parts[2] in {"issues", "pull", "pulls"} and parts[3].isdigit():
        api_url = f"{api_url}/issues/{parts[3]}"
        kind = "issue_or_pull_request"
    payload = _get_json(api_url, timeout=timeout, headers=_github_headers())
    return {
        "ok": True,
        "operation": "read",
        "channel": "github",
        "backend": "GitHub public REST API",
        "source": "agent-reach:github",
        "evidence_level": "page-content",
        "url": url,
        "api_url": api_url,
        "resource_kind": kind,
        "data": _compact_github_payload(payload),
    }


def _search_github(query: str, *, limit: int, timeout: float) -> dict[str, Any]:
    api_url = "https://api.github.com/search/repositories"
    payload = _get_json(api_url, timeout=timeout, headers=_github_headers(), params={"q": query, "per_page": min(12, limit)})
    items = []
    for item in list(payload.get("items", []) or [])[:limit]:
        if not isinstance(item, dict):
            continue
        items.append({
            "name": str(item.get("full_name", "")),
            "title": str(item.get("name", "")),
            "url": str(item.get("html_url", "")),
            "description": str(item.get("description", ""))[:1_200],
            "stars": int(item.get("stargazers_count", 0) or 0),
            "language": str(item.get("language", "") or ""),
        })
    return _search_result("github", "GitHub public REST API", api_url, query, items)


def _search_bilibili(query: str, *, limit: int, timeout: float) -> dict[str, Any]:
    api_url = "https://api.bilibili.com/x/web-interface/search/type"
    payload = _get_json(api_url, timeout=timeout, params={"search_type": "video", "keyword": query, "page": 1})
    raw_items = list(dict(payload.get("data", {}) or {}).get("result", []) or [])
    items = []
    for item in raw_items[:limit]:
        if not isinstance(item, dict):
            continue
        items.append({
            "title": _strip_html(str(item.get("title", ""))),
            "url": str(item.get("arcurl", "") or "https://www.bilibili.com/video/" + str(item.get("bvid", ""))),
            "author": str(item.get("author", "")),
            "description": _strip_html(str(item.get("description", "")))[:1_200],
            "duration": str(item.get("duration", "")),
        })
    return _search_result("bilibili", "B站公开搜索 API", api_url, query, items)


def _search_v2ex(query: str, *, limit: int, timeout: float) -> dict[str, Any]:
    api_url = "https://www.v2ex.com/api/topics/hot.json"
    payload = _get_json(api_url, timeout=timeout)
    items = []
    needle = str(query or "").casefold()
    candidates = [
        item for item in list(payload or [])
        if not needle or needle in str(item.get("title", "")).casefold()
    ]
    for item in candidates[:limit]:
        if not isinstance(item, dict):
            continue
        topic_id = str(item.get("id", ""))
        items.append({
            "title": str(item.get("title", "")),
            "url": f"https://www.v2ex.com/t/{topic_id}" if topic_id else "",
            "replies": int(item.get("replies", 0) or 0),
            "node": str(dict(item.get("node", {}) or {}).get("title", "")),
        })
    return _search_result("v2ex", "V2EX public API", api_url, query, items)


def _search_result(channel: str, backend: str, url: str, query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": "search",
        "channel": channel,
        "backend": backend,
        "source": "agent-reach:public-api",
        "evidence_level": "web-discovery",
        "query": query,
        "url": url,
        "count": len(items),
        "items": items,
    }


def _get_json(url: str, *, timeout: float, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
    body, _hops = _validated_get(
        url,
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT, **(headers or {})},
        params=params,
        timeout=timeout,
    )
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AgentReachError("public API returned invalid JSON") from exc


def _github_headers() -> dict[str, str]:
    token = str(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _compact_github_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    keep = (
        "full_name", "name", "html_url", "description", "homepage", "default_branch",
        "language", "stargazers_count", "forks_count", "open_issues_count", "topics",
        "title", "body", "state", "user", "created_at", "updated_at", "comments",
        "pull_request", "labels",
    )
    result = {key: payload[key] for key in keep if key in payload}
    if isinstance(result.get("body"), str):
        result["body"] = result["body"][:12_000]
    return result


def _xml_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _xml_child_text(node: ElementTree.Element, name: str) -> str:
    for child in node.iter():
        if child is node:
            continue
        if _xml_name(child.tag) == name.lower():
            return " ".join("".join(child.itertext()).split())
    return ""


def _xml_child_link(node: ElementTree.Element) -> str:
    for child in list(node):
        if _xml_name(child.tag) != "link":
            continue
        href = str(child.attrib.get("href", "") or "").strip()
        text = " ".join("".join(child.itertext()).split())
        return href or text
    return ""


def _strip_html(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", value).replace("&amp;", "&").split())


def _is_v2ex_api_url(url: str) -> bool:
    return "/api/" in urlsplit(url).path.lower()


__all__ = ["AGENT_REACH_SCHEMA_VERSION", "AgentReachError", "agent_reach_read", "agent_reach_search", "agent_reach_status", "run_agent_reach"]
