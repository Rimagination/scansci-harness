"""Blockmap-based differential downloads for desktop release archives."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request

from .release_transport import open_release_url


DEFAULT_BLOCK_SIZE = 64 * 1024
BLOCKMAP_SCHEMA_VERSION = 1


class DifferentialDownloadUnavailable(RuntimeError):
    """The remote package cannot be reconstructed safely by block ranges."""


def build_blockmap(path: str | Path, *, block_size: int = DEFAULT_BLOCK_SIZE) -> dict[str, Any]:
    """Build a JSON-serialisable SHA256 blockmap for a local file."""

    source = Path(path)
    if not source.is_file():
        raise ValueError(f"blockmap source is not a file: {source}")
    _validate_block_size(block_size)
    return _build_blockmap_from_chunks(_read_chunks(source, block_size), block_size=block_size)


def build_blockmap_from_bytes(payload: bytes, *, block_size: int = DEFAULT_BLOCK_SIZE) -> dict[str, Any]:
    """Build a blockmap from bytes; primarily useful for deterministic tests."""

    _validate_block_size(block_size)
    return _build_blockmap_from_chunks(_iter_bytes_chunks(payload, block_size), block_size=block_size)


def load_blockmap(path: str | Path) -> dict[str, Any]:
    """Read and validate a blockmap JSON file."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid blockmap: {path}") from error
    _validate_blockmap(payload)
    return payload


def plan_differential_operations(
    old_file: str | Path,
    old_blockmap: dict[str, Any],
    new_blockmap: dict[str, Any],
) -> list[dict[str, int | str]]:
    """Plan copy/download ranges for a new file using an old local file."""

    _validate_blockmap(old_blockmap)
    _validate_blockmap(new_blockmap)
    old_path = Path(old_file)
    if not old_path.is_file() or old_path.stat().st_size != int(old_blockmap["size"]):
        raise DifferentialDownloadUnavailable("cached base package is missing or has the wrong size")
    if build_blockmap(old_path, block_size=int(old_blockmap["block_size"])) != old_blockmap:
        raise DifferentialDownloadUnavailable("cached base package does not match its blockmap")

    old_blocks = list(old_blockmap["blocks"])
    new_blocks = list(new_blockmap["blocks"])
    block_size = int(new_blockmap["block_size"])
    operations: list[dict[str, int | str]] = []
    pending_download: dict[str, int | str] | None = None
    old_size = int(old_blockmap["size"])

    for index, new_hash in enumerate(new_blocks):
        start = index * block_size
        end = min(int(new_blockmap["size"]), start + block_size)
        can_copy = (
            int(old_blockmap["block_size"]) == block_size
            and index < len(old_blocks)
            and end <= old_size
            and old_blocks[index] == new_hash
        )
        kind = "copy" if can_copy else "download"
        if kind == "download":
            if pending_download is not None and int(pending_download["end"]) == start:
                pending_download["end"] = end
            else:
                pending_download = {"kind": "download", "start": start, "end": end}
                operations.append(pending_download)
        else:
            pending_download = None
            operations.append({"kind": "copy", "start": start, "end": end})
    return operations


def download_differential(
    old_file: str | Path,
    old_blockmap: dict[str, Any],
    new_url: str,
    new_blockmap: dict[str, Any],
    destination: str | Path,
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Reconstruct a new file by copying old blocks and fetching changed ranges."""

    destination_path = Path(destination)
    temporary = destination_path.with_name(destination_path.name + ".partial")
    temporary.unlink(missing_ok=True)
    operations = plan_differential_operations(old_file, old_blockmap, new_blockmap)
    bytes_downloaded = 0

    try:
        with Path(old_file).open("rb") as source, temporary.open("wb") as output:
            for operation in operations:
                start = int(operation["start"])
                end = int(operation["end"])
                length = end - start
                if operation["kind"] == "copy":
                    source.seek(start)
                    data = source.read(length)
                    if len(data) != length:
                        raise DifferentialDownloadUnavailable("cached base package ended before a copied range")
                else:
                    data = _download_range(new_url, start, end, opener=opener)
                    bytes_downloaded += len(data)
                output.write(data)
            output.flush()
            os.fsync(output.fileno())

        if temporary.stat().st_size != int(new_blockmap["size"]):
            raise DifferentialDownloadUnavailable("reconstructed package has the wrong size")
        if _sha256(temporary) != str(new_blockmap["sha256"]).lower():
            raise DifferentialDownloadUnavailable("reconstructed package failed SHA256 validation")
        temporary.replace(destination_path)
    except DifferentialDownloadUnavailable:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        raise DifferentialDownloadUnavailable(str(error)) from error

    return {
        "used_differential": True,
        "bytes_downloaded": bytes_downloaded,
        "total_size": int(new_blockmap["size"]),
    }


def _build_blockmap_from_chunks(chunks: Iterable[bytes], *, block_size: int) -> dict[str, Any]:
    block_hashes: list[str] = []
    digest = hashlib.sha256()
    size = 0
    for chunk in chunks:
        digest.update(chunk)
        size += len(chunk)
        block_hashes.append(hashlib.sha256(chunk).hexdigest())
    return {
        "schema_version": BLOCKMAP_SCHEMA_VERSION,
        "algorithm": "sha256",
        "block_size": block_size,
        "size": size,
        "sha256": digest.hexdigest(),
        "blocks": block_hashes,
    }


def _read_chunks(path: Path, block_size: int) -> Iterable[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(block_size):
            yield chunk


def _iter_bytes_chunks(payload: bytes, block_size: int) -> Iterable[bytes]:
    for start in range(0, len(payload), block_size):
        yield payload[start : start + block_size]


def _validate_block_size(value: int) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError("block size must be a positive integer")


def _validate_blockmap(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("blockmap must be a JSON object")
    if payload.get("schema_version") != BLOCKMAP_SCHEMA_VERSION or payload.get("algorithm") != "sha256":
        raise ValueError("unsupported blockmap schema")
    block_size = payload.get("block_size")
    size = payload.get("size")
    blocks = payload.get("blocks")
    checksum = payload.get("sha256")
    _validate_block_size(block_size)
    if not isinstance(size, int) or size < 0:
        raise ValueError("blockmap size must be a non-negative integer")
    if not isinstance(blocks, list) or not all(isinstance(item, str) and len(item) == 64 for item in blocks):
        raise ValueError("blockmap blocks must contain SHA256 strings")
    expected_count = (size + block_size - 1) // block_size if size else 0
    if len(blocks) != expected_count:
        raise ValueError("blockmap block count does not match size")
    if not isinstance(checksum, str) or len(checksum) != 64:
        raise ValueError("blockmap sha256 must be a SHA256 string")


def _download_range(
    url: str,
    start: int,
    end: int,
    *,
    opener: Callable[..., Any] | None = None,
) -> bytes:
    request = Request(url, headers={"Accept": "*/*", "Range": f"bytes={start}-{end - 1}"})
    response = (
        open_release_url(
            request,
            timeout=90,
            allow_file=urlparse(url).scheme.casefold() == "file",
        )
        if opener is None
        else opener(request, timeout=90)
    )
    with response:
        status = int(getattr(response, "status", response.getcode()))
        if status != 206:
            raise DifferentialDownloadUnavailable("release server does not support HTTP Range")
        data = response.read(end - start + 1)
    if len(data) != end - start:
        raise DifferentialDownloadUnavailable("release server returned an incomplete range")
    return data


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
