from __future__ import annotations

import socket
from urllib.request import Request

import pytest

from scansci_html import release_transport


def test_release_url_rejects_downgrades_credentials_and_private_literals() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        release_transport.validate_release_url("http://downloads.example.com/file.zip")
    with pytest.raises(ValueError, match="HTTPS"):
        release_transport.validate_release_url("https://user:secret@downloads.example.com/file.zip")
    with pytest.raises(ValueError, match="私有网络"):
        release_transport.validate_public_release_url("https://127.0.0.1/file.zip")


def test_release_url_rejects_hostnames_that_resolve_to_private_addresses(monkeypatch) -> None:
    monkeypatch.setattr(
        release_transport.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443)),
        ],
    )

    with pytest.raises(ValueError, match="私有网络"):
        release_transport.validate_public_release_url("https://updates.example.test/stable.json")


def test_redirect_target_is_rejected_before_urllib_builds_the_next_request() -> None:
    handler = release_transport.ValidatedRedirectHandler()
    request = Request("https://downloads.example.com/stable.json")

    with pytest.raises(ValueError, match="HTTPS|私有网络"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {"Location": "http://127.0.0.1/private"},
            "http://127.0.0.1/private",
        )

    assert handler.max_redirections == release_transport.MAX_RELEASE_REDIRECTS == 5
