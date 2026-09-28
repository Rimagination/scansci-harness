"""Restricted transport for release manifests and immutable download assets."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen as _stdlib_urlopen


MAX_RELEASE_REDIRECTS = 5


def validate_release_url(url: str, *, allow_file: bool = False) -> None:
    """Validate release URL syntax without issuing DNS or network requests."""

    parsed = urlparse(str(url or ""))
    scheme = parsed.scheme.casefold()
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("下载地址端口无效。") from error
    if scheme == "file" and allow_file:
        if parsed.netloc.casefold() not in {"", "localhost"} or parsed.query or parsed.fragment:
            raise ValueError("本地下载地址格式无效。")
        return
    if (
        scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port is not None and not 1 <= port <= 65535
    ):
        raise ValueError("下载地址必须使用无凭据的 HTTPS。")


def validate_public_release_url(url: str, *, allow_file: bool = False) -> None:
    """Reject loopback/private destinations before a network request is sent."""

    validate_release_url(url, allow_file=allow_file)
    parsed = urlparse(url)
    if parsed.scheme.casefold() == "file":
        return
    host = str(parsed.hostname or "").rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("下载地址不能指向本机或私有网络。")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            resolved = {
                str(record[4][0]).split("%", 1)[0]
                for record in socket.getaddrinfo(
                    host,
                    parsed.port or 443,
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as error:
            raise ValueError(f"下载地址无法解析：{host}") from error
        if not resolved:
            raise ValueError(f"下载地址无法解析：{host}")
        addresses = [ipaddress.ip_address(value) for value in resolved]
    else:
        addresses = [literal]
    if any(not address.is_global for address in addresses):
        raise ValueError("下载地址不能指向本机或私有网络。")


class ValidatedRedirectHandler(HTTPRedirectHandler):
    """Validate every redirect target before urllib follows it."""

    max_redirections = MAX_RELEASE_REDIRECTS

    def __init__(self, *, allow_file: bool = False) -> None:
        super().__init__()
        self.allow_file = allow_file

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        destination = urljoin(req.full_url, newurl)
        validate_public_release_url(destination, allow_file=self.allow_file)
        return super().redirect_request(req, fp, code, msg, headers, destination)


def open_release_url(
    request: Request | str,
    *,
    timeout: float,
    allow_file: bool = False,
    opener: Callable[..., Any] | None = None,
) -> Any:
    """Open a release URL with pre-request target and redirect validation.

    An injected opener is reserved for deterministic tests. Production calls
    use urllib's standard function (or omit ``opener``) and receive the
    restricted redirect handler plus DNS/IP checks before every request.
    """

    initial_url = request.full_url if isinstance(request, Request) else str(request)
    validate_release_url(initial_url, allow_file=allow_file)
    production_transport = opener is None or opener is _stdlib_urlopen
    if production_transport:
        validate_public_release_url(initial_url, allow_file=allow_file)
        response = build_opener(ValidatedRedirectHandler(allow_file=allow_file)).open(
            request,
            timeout=timeout,
        )
    else:
        response = opener(request, timeout=timeout)
    final_url = str(response.geturl()) if hasattr(response, "geturl") else initial_url
    validate_release_url(final_url, allow_file=allow_file)
    if production_transport:
        validate_public_release_url(final_url, allow_file=allow_file)
    return response
