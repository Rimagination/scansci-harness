"""Private image attachments used by ScanSci conversation runs.

Pasted images are kept outside the notebook evidence library: they are user
inputs, not scholarly sources.  The run stores only small, validated metadata
and the bytes remain local to the workspace until a vision-capable provider is
called for that request.
"""

from __future__ import annotations

import base64
import binascii
import math
from pathlib import Path
import re
import uuid
from typing import Any


_ATTACHMENTS_DIR = ".scansci-attachments"
_MAX_IMAGES = 4
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_DIMENSION = 16_384
_MAX_IMAGE_PIXELS = 40_000_000
_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_DATA_URL = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=]+)$")
_ATTACHMENT_ID = re.compile(r"^image-[a-f0-9]{32}$")


def persist_image_attachments(workspace: str | Path, images: object) -> list[dict[str, Any]]:
    """Validate clipboard data URLs and persist them under the local workspace."""

    if images in (None, ""):
        return []
    if not isinstance(images, list):
        raise ValueError("images must be a list")
    if len(images) > _MAX_IMAGES:
        raise ValueError(f"一次最多可发送 {_MAX_IMAGES} 张图片")

    # A retry loaded from durable history contains only the attachment
    # metadata.  Reuse the already-persisted local asset instead of requiring
    # the browser to retain the original data URL.
    validated: list[tuple[str, bytes, str, str]] = []
    total = 0
    for index, item in enumerate(images, start=1):
        if not isinstance(item, dict):
            raise ValueError("图片附件格式无效")
        attachment_id = str(item.get("id", "")).strip()
        if attachment_id and not str(item.get("data_url", "")).strip():
            path, mime_type = attachment_asset(workspace, attachment_id)
            if path.stat().st_size > _MAX_IMAGE_BYTES:
                raise ValueError("单张图片不能超过 4 MB")
            raw = path.read_bytes()
            _validate_image_bytes(mime_type, raw)
            reused_id = attachment_id
        else:
            mime_type, raw = _decode_data_url(str(item.get("data_url", "")))
            reused_id = ""
        if len(raw) > _MAX_IMAGE_BYTES:
            raise ValueError("单张图片不能超过 4 MB")
        total += len(raw)
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("本次图片总大小不能超过 10 MB")
        name = _display_name(str(item.get("name", "")), index, _IMAGE_TYPES[mime_type])
        validated.append((mime_type, raw, name, reused_id))

    if not validated:
        return []

    root = _attachments_root(workspace)
    root_existed = root.exists()
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    staged: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    try:
        for mime_type, raw, name, reused_id in validated:
            attachment_id = reused_id or f"image-{uuid.uuid4().hex}"
            if reused_id:
                records.append(
                    {
                        "id": attachment_id,
                        "name": name,
                        "mime_type": mime_type,
                        "size": len(raw),
                        "preview_url": f"/api/attachments/{attachment_id}",
                    }
                )
                continue
            target = root / f"{attachment_id}{_IMAGE_TYPES[mime_type]}"
            staging = root / f".{attachment_id}.{uuid.uuid4().hex}.tmp"
            staged.append((staging, target))
            staging.write_bytes(raw)
            records.append(
                {
                    "id": attachment_id,
                    "name": name,
                    "mime_type": mime_type,
                    "size": len(raw),
                    "preview_url": f"/api/attachments/{attachment_id}",
                }
            )

        for staging, target in staged:
            staging.replace(target)
            committed.append(target)
        return records
    except Exception:
        for staging, _target in staged:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
        for target in committed:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        if not root_existed:
            try:
                root.rmdir()
            except OSError:
                pass
        raise


def vision_image_blocks(workspace: str | Path, attachments: object) -> list[dict[str, str]]:
    """Read persisted attachment bytes as provider-neutral base64 blocks."""

    if not isinstance(attachments, list) or not attachments:
        return []
    blocks: list[dict[str, str]] = []
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("图片附件格式无效")
        attachment_id = str(item.get("id", ""))
        mime_type = str(item.get("mime_type", ""))
        path, detected_type = attachment_asset(workspace, attachment_id)
        if mime_type and mime_type != detected_type:
            raise ValueError("图片附件类型不匹配")
        raw = path.read_bytes()
        _validate_image_bytes(detected_type, raw)
        blocks.append(
            {
                "mime_type": detected_type,
                "data": base64.b64encode(raw).decode("ascii"),
            }
        )
    return blocks


def pi_image_blocks(workspace: str | Path, attachments: object) -> list[dict[str, str]]:
    """Return the only image shape accepted by the Pi JSONL protocol."""

    return validate_pi_image_blocks(
        [
            {"type": "image", "data": block["data"], "mimeType": block["mime_type"]}
            for block in vision_image_blocks(workspace, attachments)
        ]
    )


def validate_pi_image_blocks(images: object) -> list[dict[str, str]]:
    """Validate bounded canonical base64 image blocks without accepting URLs."""

    if images in (None, ""):
        return []
    if not isinstance(images, list):
        raise ValueError("Pi images must be a list")
    if len(images) > _MAX_IMAGES:
        raise ValueError(f"一次最多可发送 {_MAX_IMAGES} 张图片")
    validated: list[dict[str, str]] = []
    total = 0
    encoded_limit = 4 * ((_MAX_IMAGE_BYTES + 2) // 3)
    for item in images:
        if not isinstance(item, dict) or set(item) != {"type", "data", "mimeType"}:
            raise ValueError("Pi image blocks require exactly type, data, and mimeType")
        if item.get("type") != "image":
            raise ValueError("Pi image block type must be image")
        mime_type = str(item.get("mimeType", "")).strip().lower()
        if mime_type not in _IMAGE_TYPES:
            raise ValueError("仅支持 PNG、JPG、WebP 或 GIF 图片")
        encoded = item.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("图片内容为空")
        # Reject before decode so an attacker cannot allocate an oversized
        # temporary buffer from a JSONL line.
        if len(encoded) > encoded_limit:
            raise ValueError("单张图片不能超过 4 MB")
        if len(encoded) % 4 != 0 or re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", encoded) is None:
            raise ValueError("图片数据损坏")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("图片数据损坏") from error
        if not raw:
            raise ValueError("图片内容为空")
        if len(raw) > _MAX_IMAGE_BYTES:
            raise ValueError("单张图片不能超过 4 MB")
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError("图片必须使用规范 base64 编码")
        total += len(raw)
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("本次图片总大小不能超过 10 MB")
        _validate_image_bytes(mime_type, raw)
        validated.append({"type": "image", "data": encoded, "mimeType": mime_type})
    return validated


def estimate_pi_image_tokens(images: object) -> int:
    """Conservatively reserve provider context for validated visual inputs.

    Provider tokenizers differ, so ScanSci uses a deliberately generous
    1,024-token charge per 512px tile. Tiny images still reserve one tile;
    high-resolution inputs cannot pass the provider gate on a flat fee.
    """

    validated = validate_pi_image_blocks(images)
    total = 0
    for item in validated:
        raw = base64.b64decode(item["data"], validate=True)
        dimensions = _image_dimensions(item["mimeType"], raw)
        if dimensions is None:  # Defensive: validation above already rejects this.
            raise ValueError("图片尺寸无效或无法解析")
        width, height = dimensions
        total += max(1, math.ceil(width / 512)) * max(1, math.ceil(height / 512)) * 1024
    return total


def attachment_asset(workspace: str | Path, attachment_id: str) -> tuple[Path, str]:
    """Resolve a single stored image without allowing workspace traversal."""

    if not _ATTACHMENT_ID.fullmatch(str(attachment_id or "")):
        raise FileNotFoundError("图片附件不存在")
    root = _attachments_root(workspace)
    matches = [path for path in root.glob(f"{attachment_id}.*") if path.is_file()]
    if len(matches) != 1:
        raise FileNotFoundError("图片附件不存在")
    path = matches[0].resolve()
    if root.resolve() not in path.parents:
        raise FileNotFoundError("图片附件不存在")
    mime_type = next((kind for kind, extension in _IMAGE_TYPES.items() if path.suffix.lower() == extension), "")
    if not mime_type:
        raise FileNotFoundError("图片附件不存在")
    return path, mime_type


def _attachments_root(workspace: str | Path) -> Path:
    # ``workspace`` is the SQLite workspace file; keep transient user inputs
    # beside it, never inside the database path itself.
    return Path(workspace).resolve().parent / _ATTACHMENTS_DIR


def _decode_data_url(value: str) -> tuple[str, bytes]:
    match = _DATA_URL.fullmatch(value.strip())
    if match is None:
        raise ValueError("仅支持 PNG、JPG、WebP 或 GIF 图片")
    mime_type = match.group(1).lower()
    if mime_type not in _IMAGE_TYPES:
        raise ValueError("仅支持 PNG、JPG、WebP 或 GIF 图片")
    encoded = match.group(2)
    encoded_limit = 4 * ((_MAX_IMAGE_BYTES + 2) // 3)
    if len(encoded) > encoded_limit:
        raise ValueError("单张图片不能超过 4 MB")
    if len(encoded) % 4 != 0 or re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", encoded) is None:
        raise ValueError("图片数据损坏")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("图片数据损坏") from error
    if not raw:
        raise ValueError("图片内容为空")
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("图片必须使用规范 base64 编码")
    _validate_image_bytes(mime_type, raw)
    return mime_type, raw


def _validate_image_bytes(mime_type: str, raw: bytes) -> None:
    if not _matches_magic(mime_type, raw):
        raise ValueError("图片附件类型与内容不匹配")
    dimensions = _image_dimensions(mime_type, raw)
    if dimensions is None:
        raise ValueError("图片尺寸无效或无法解析")
    width, height = dimensions
    if (
        width <= 0
        or height <= 0
        or width > _MAX_IMAGE_DIMENSION
        or height > _MAX_IMAGE_DIMENSION
        or width * height > _MAX_IMAGE_PIXELS
    ):
        raise ValueError("图片尺寸过大或无效")


def _matches_magic(mime_type: str, raw: bytes) -> bool:
    if mime_type == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime_type == "image/gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if mime_type == "image/webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def _image_dimensions(mime_type: str, raw: bytes) -> tuple[int, int] | None:
    if mime_type == "image/png" and len(raw) >= 24 and raw[12:16] == b"IHDR":
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if mime_type == "image/gif" and len(raw) >= 10:
        return int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")
    if mime_type == "image/jpeg":
        offset = 2
        while offset + 9 <= len(raw):
            if raw[offset] != 0xFF:
                offset += 1
                continue
            marker = raw[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA or offset + 2 > len(raw):
                break
            segment = int.from_bytes(raw[offset : offset + 2], "big")
            if segment < 2 or offset + segment > len(raw):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if segment >= 7:
                    return (
                        int.from_bytes(raw[offset + 5 : offset + 7], "big"),
                        int.from_bytes(raw[offset + 3 : offset + 5], "big"),
                    )
                break
            offset += segment
    if mime_type == "image/webp" and len(raw) >= 30:
        kind = raw[12:16]
        if kind == b"VP8X":
            return (
                1 + int.from_bytes(raw[24:27], "little"),
                1 + int.from_bytes(raw[27:30], "little"),
            )
        if kind == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
            bits = int.from_bytes(raw[21:25], "little")
            return (1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF))
        if kind == b"VP8 " and raw[23:26] == b"\x9d\x01\x2a":
            return (
                int.from_bytes(raw[26:28], "little") & 0x3FFF,
                int.from_bytes(raw[28:30], "little") & 0x3FFF,
            )
    return None


def _display_name(value: str, index: int, extension: str) -> str:
    clean = " ".join(value.replace("\x00", "").split()).strip()
    if clean:
        return clean[:120]
    return f"粘贴图片 {index}{extension}"
