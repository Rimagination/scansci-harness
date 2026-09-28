from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_update_channel.py"
SPEC = importlib.util.spec_from_file_location("scansci_verify_update_channel", SCRIPT)
assert SPEC and SPEC.loader
verify_update_channel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_update_channel)


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.stream = io.BytesIO(payload)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self.stream.read(_limit)

    def getcode(self) -> int:
        return self.status


def test_update_channel_audit_rejects_unsigned_manifest_when_public_key_is_pinned() -> None:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    manifest = json.dumps(
        {"schema_version": 1, "version": "0.4.2", "channel": "stable"}
    ).encode("utf-8")

    with pytest.raises(verify_update_channel.UpdateChannelFailure, match="signature is invalid"):
        verify_update_channel.verify_update_channel(
            "https://downloads.example.com/stable.json",
            release_public_key=public,
            opener=lambda *_args, **_kwargs: _Response(manifest),
        )


def test_update_channel_audit_checks_manifest_assets_and_http_range() -> None:
    package = b"desktop update payload"
    package_sha = hashlib.sha256(package).hexdigest()
    blockmap = json.dumps(
        {
            "schema_version": 1,
            "algorithm": "sha256",
            "block_size": 65536,
            "size": len(package),
            "sha256": package_sha,
            "blocks": [package_sha],
        }
    ).encode("utf-8")
    blockmap_sha = hashlib.sha256(blockmap).hexdigest()
    manifest = json.dumps(
        {
            "schema_version": 1,
            "version": "0.3.1",
            "channel": "stable",
            "windows": {
                "url": "https://downloads.example.com/ScanSci-0.3.1.zip",
                "sha256": package_sha,
                "size": len(package),
                "blockmap": {
                    "url": "https://downloads.example.com/ScanSci-0.3.1.zip.blockmap",
                    "sha256": blockmap_sha,
                    "size": len(blockmap),
                    "block_size": 65536,
                },
            },
        }
    ).encode("utf-8")
    requests: list[tuple[str, str, str]] = []

    def opener(request, timeout: int):
        assert timeout > 0
        method = request.get_method()
        range_header = request.headers.get("Range", "")
        requests.append((method, request.full_url, range_header))
        if request.full_url.endswith("stable.json"):
            return _Response(manifest)
        if request.full_url.endswith(".blockmap"):
            return _Response(blockmap)
        if method == "HEAD":
            return _Response(b"", headers={"Content-Length": str(len(package))})
        if range_header:
            return _Response(
                package[:1],
                status=206,
                headers={"Content-Range": f"bytes 0-0/{len(package)}"},
            )
        return _Response(package)

    result = verify_update_channel.verify_update_channel(
        "https://downloads.example.com/stable.json",
        expected_version="0.3.1",
        verify_downloads=True,
        opener=opener,
    )

    assert result["ok"] is True
    assert result["version"] == "0.3.1"
    assert result["range_supported"] is True
    assert result["content_verified"] is True
    assert result["verified_bytes"] == len(package)
    assert requests[-1] == ("GET", "https://downloads.example.com/ScanSci-0.3.1.zip", "")


def test_update_channel_audit_keeps_full_package_fallback_when_range_is_unavailable() -> None:
    package = b"desktop update payload"
    package_sha = hashlib.sha256(package).hexdigest()
    blockmap = json.dumps(
        {
            "schema_version": 1,
            "algorithm": "sha256",
            "block_size": 65536,
            "size": len(package),
            "sha256": package_sha,
            "blocks": [package_sha],
        }
    ).encode("utf-8")
    blockmap_sha = hashlib.sha256(blockmap).hexdigest()
    manifest = json.dumps(
        {
            "schema_version": 1,
            "version": "0.3.1",
            "channel": "stable",
            "windows": {
                "url": "https://downloads.example.com/ScanSci-0.3.1.zip",
                "sha256": package_sha,
                "size": len(package),
                "blockmap": {
                    "url": "https://downloads.example.com/ScanSci-0.3.1.zip.blockmap",
                    "sha256": blockmap_sha,
                    "size": len(blockmap),
                    "block_size": 65536,
                },
            },
        }
    ).encode("utf-8")

    def opener(request, timeout: int):
        assert timeout > 0
        if request.full_url.endswith("stable.json"):
            return _Response(manifest)
        if request.full_url.endswith(".blockmap"):
            return _Response(blockmap)
        if request.get_method() == "HEAD":
            return _Response(b"", headers={"Content-Length": str(len(package))})
        return _Response(package, status=200, headers={"Content-Length": str(len(package))})

    result = verify_update_channel.verify_update_channel(
        "https://downloads.example.com/stable.json",
        expected_version="0.3.1",
        opener=opener,
    )

    assert result["ok"] is True
    assert result["range_supported"] is False
    assert result["download_strategy"] == "full-package-fallback"


def test_update_channel_audit_requires_reachable_runtime_component_assets() -> None:
    package = b"desktop update payload"
    package_sha = hashlib.sha256(package).hexdigest()
    blockmap = json.dumps(
        {
            "schema_version": 1,
            "algorithm": "sha256",
            "block_size": 65536,
            "size": len(package),
            "sha256": package_sha,
            "blocks": [package_sha],
        }
    ).encode("utf-8")
    blockmap_sha = hashlib.sha256(blockmap).hexdigest()
    runtime_part = b"runtime-part"
    runtime_sha = hashlib.sha256(runtime_part).hexdigest()
    manifest = json.dumps(
        {
            "schema_version": 1,
            "version": "0.4.2",
            "channel": "stable",
            "windows": {
                "url": "https://downloads.example.com/ScanSci-0.4.2.zip",
                "sha256": package_sha,
                "size": len(package),
                "blockmap": {
                    "url": "https://downloads.example.com/ScanSci-0.4.2.zip.blockmap",
                    "sha256": blockmap_sha,
                    "size": len(blockmap),
                    "block_size": 65536,
                },
            },
            "components": {
                "local-transformers": {
                    "version": "1.0.4",
                    "windows": {
                        "sha256": runtime_sha,
                        "size": len(runtime_part),
                        "parts": [
                            {
                                "url": "https://downloads.example.com/ScanSciLocalRuntime.zip.part001",
                                "sha256": runtime_sha,
                                "size": len(runtime_part),
                            }
                        ],
                    },
                }
            },
        }
    ).encode("utf-8")

    def opener(request, timeout: int):
        assert timeout > 0
        url = request.full_url
        if url.endswith("stable.json"):
            return _Response(manifest)
        if url.endswith(".blockmap"):
            return _Response(blockmap)
        payload = runtime_part if url.endswith("part001") else package
        if request.get_method() == "HEAD":
            return _Response(b"", headers={"Content-Length": str(len(payload))})
        if request.headers.get("Range", ""):
            return _Response(
                payload[:1],
                status=206,
                headers={"Content-Range": f"bytes 0-0/{len(payload)}"},
            )
        return _Response(payload)

    result = verify_update_channel.verify_update_channel(
        "https://downloads.example.com/stable.json",
        expected_version="0.4.2",
        required_components=("local-transformers",),
        verify_downloads=True,
        opener=opener,
    )

    runtime = result["components"]["local-transformers"]
    assert runtime["version"] == "1.0.4"
    assert runtime["asset_count"] == 1
    assert runtime["download_strategy"] == "resumable"
    assert runtime["content_verified"] is True
    assert runtime["verified_bytes"] == len(runtime_part)


def test_stream_verification_rejects_same_size_corruption() -> None:
    expected = b"expected-bytes"
    corrupted = b"corrupt!-bytes"
    assert len(corrupted) == len(expected)

    with pytest.raises(verify_update_channel.UpdateChannelFailure, match="SHA256"):
        verify_update_channel._stream_verified_response(
            _Response(corrupted),
            expected_size=len(expected),
            expected_sha256=hashlib.sha256(expected).hexdigest(),
            label="test asset",
        )
