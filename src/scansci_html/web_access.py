"""Read-only bridge for the bundled web-access CDP proxy.

The upstream web-access Skill describes a browser workflow, while the Pi
runtime exposes only host-owned tools.  This module closes that gap with a
narrow read operation: it can create a background tab, read rendered page
text, and close that tab.  It deliberately does not expose arbitrary
JavaScript, clicks, navigation, uploads, or writes to the model.
"""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests


WEB_ACCESS_SCHEMA_VERSION = "scansci.web-access.v1"
_DEFAULT_PROXY_PORT = 3456
_DEFAULT_TIMEOUT = 30.0
_MAX_CONTENT_CHARS = 60_000
_USER_AGENT = "ScanSci-Harness/0.2 web-access reader"
_RISK_NOTICE = (
    "浏览器读取会使用当前 Chrome 的登录态；部分站点可能检测自动化并触发账号风控，"
    "本次操作仅读取公开或用户已授权页面，不执行点击、提交、上传或其他写入操作。"
)


class WebAccessError(RuntimeError):
    """Raised when the local web-access browser bridge is unavailable."""


def browser_access_status(*, timeout: float = 3.0) -> dict[str, Any]:
    """Return local CDP proxy readiness without starting any process."""

    health = _proxy_json("/health", timeout=timeout, tolerate_errors=True)
    if not isinstance(health, dict):
        return {
            "ok": True,
            "schema_version": WEB_ACCESS_SCHEMA_VERSION,
            "backend": "web-access CDP proxy",
            "ready": False,
            "proxy_ready": False,
            "chrome_connected": False,
            "requires_user_setup": True,
            "message": "CDP proxy is not running; browser access will perform the bundled preflight on demand.",
            "read_only": True,
        }
    connected = bool(health.get("connected"))
    return {
        "ok": True,
        "schema_version": WEB_ACCESS_SCHEMA_VERSION,
        "backend": "web-access CDP proxy",
        "ready": connected,
        "proxy_ready": True,
        "chrome_connected": connected,
        "requires_user_setup": not connected,
        "proxy_port": _proxy_port(),
        "sessions": int(health.get("sessions", 0) or 0),
        "read_only": True,
        "message": "Chrome CDP is ready." if connected else "CDP proxy is running but Chrome is not connected.",
    }


def browser_access_read(
    target: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read rendered text from one public URL through the user's Chrome."""

    url = _normalize_public_url(target)
    bounded_timeout = max(5.0, min(60.0, float(timeout or _DEFAULT_TIMEOUT)))
    _ensure_proxy(timeout=bounded_timeout)
    created = _proxy_json(
        "/new",
        params={"url": url},
        timeout=bounded_timeout,
    )
    if not isinstance(created, dict) or not str(created.get("targetId", "")).strip():
        raise WebAccessError(f"web-access could not create a background tab: {created!r}")
    target_id = str(created["targetId"])
    try:
        info = _proxy_json("/info", params={"target": target_id}, timeout=bounded_timeout)
        page = _proxy_json(
            "/eval",
            params={"target": target_id},
            method="POST",
            body=(
                "JSON.stringify({title: document.title, url: location.href, ready: document.readyState, "
                "text: (document.body && document.body.innerText || '').slice(0, 60000)})"
            ),
            timeout=bounded_timeout,
        )
        value = page.get("value") if isinstance(page, dict) else None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = {"text": value}
        if not isinstance(value, dict):
            raise WebAccessError(f"web-access returned no readable page content: {page!r}")
        final_url = str(value.get("url") or (info or {}).get("url") or url)
        if final_url and final_url != url:
            # Chrome may follow redirects before we can inspect them.  Even
            # then, refuse to extract content from a landing page on a
            # private/loopback address so local services never leak into the
            # model context through an open redirect.
            try:
                _normalize_public_url(final_url)
            except WebAccessError as exc:
                raise WebAccessError(
                    "the page redirected to a non-public address; refusing to read its content"
                ) from exc
        content = " ".join(str(value.get("text", "") or "").split())
        if not content:
            raise WebAccessError("browser page loaded but contained no readable text")
        return {
            "ok": True,
            "operation": "read",
            "channel": "browser",
            "backend": "web-access CDP proxy",
            "source": "web-access:cdp",
            "evidence_level": "rendered-page",
            "url": url,
            "final_url": final_url,
            "title": str(value.get("title") or (info or {}).get("title") or ""),
            "ready_state": str(value.get("ready") or ""),
            "content": content[:_MAX_CONTENT_CHARS],
            "truncated": len(content) > _MAX_CONTENT_CHARS,
            "read_only": True,
            "risk_notice": _RISK_NOTICE,
        }
    finally:
        _proxy_json("/close", params={"target": target_id}, timeout=bounded_timeout, tolerate_errors=True)


def _ensure_proxy(*, timeout: float) -> None:
    status = browser_access_status(timeout=min(3.0, timeout))
    if status.get("ready"):
        return
    node = shutil.which("node")
    script = Path(__file__).with_name("builtin_skill_assets") / "web-access" / "scripts" / "check-deps.mjs"
    if not node:
        raise WebAccessError("web-access requires Node.js to start its bundled CDP preflight")
    if not script.is_file():
        raise WebAccessError("the bundled web-access CDP preflight script is missing")
    try:
        completed = subprocess.run(
            [node, str(script)],
            capture_output=True,
            text=True,
            timeout=min(45.0, max(10.0, timeout)),
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WebAccessError(f"web-access CDP preflight failed: {exc}") from exc
    if completed.returncode != 0 or not browser_access_status(timeout=3.0).get("ready"):
        detail = " ".join((completed.stdout or "").split())[-600:]
        raise WebAccessError(
            "Chrome CDP is unavailable. Open chrome://inspect/#remote-debugging and enable "
            f"remote debugging, then retry. {detail}"
        )


def _proxy_port() -> int:
    try:
        return max(1, min(65535, int(os.environ.get("CDP_PROXY_PORT", _DEFAULT_PROXY_PORT))))
    except ValueError:
        return _DEFAULT_PROXY_PORT


def _proxy_json(
    path: str,
    *,
    params: dict[str, str] | None = None,
    method: str = "GET",
    body: str = "",
    timeout: float = 3.0,
    tolerate_errors: bool = False,
) -> Any:
    url = f"http://127.0.0.1:{_proxy_port()}{path}"
    try:
        if method == "POST":
            response = requests.post(url, params=params or {}, data=body.encode("utf-8"), timeout=timeout)
        else:
            response = requests.get(url, params=params or {}, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (OSError, requests.RequestException, ValueError) as exc:
        if tolerate_errors:
            return None
        raise WebAccessError(f"web-access proxy request failed for {path}: {exc}") from exc


def _normalize_public_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise WebAccessError("URL is required")
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise WebAccessError("only public http(s) URLs are supported")
    if parsed.username or parsed.password:
        raise WebAccessError("URLs containing credentials are not allowed")
    if _is_private_host(host):
        raise WebAccessError("private, loopback, and local URLs are not allowed")
    try:
        netloc = host if not parsed.port else f"{host}:{parsed.port}"
    except ValueError as exc:
        raise WebAccessError("invalid URL port") from exc
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
            # Unresolvable right now: let the navigation fail naturally.
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


__all__ = ["WEB_ACCESS_SCHEMA_VERSION", "WebAccessError", "browser_access_read", "browser_access_status"]
