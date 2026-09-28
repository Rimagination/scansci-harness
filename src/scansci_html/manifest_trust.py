"""Pinned Ed25519 trust for desktop update and runtime manifests."""

from __future__ import annotations

import base64
from copy import deepcopy
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return deterministic bytes covered by a manifest signature."""

    payload = deepcopy(manifest)
    payload.pop("signature", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decode_public_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(str(value or "").strip(), validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("发布签名公钥不是有效的 Base64。") from error
    if len(decoded) != 32:
        raise ValueError("发布签名公钥必须是 32 字节 Ed25519 公钥。")
    return decoded


def verify_manifest_signature(
    manifest: dict[str, Any],
    public_key: str,
    *,
    required: bool,
) -> bool:
    """Verify a manifest against the public key pinned into the application."""

    signature = manifest.get("signature")
    if not public_key:
        if required:
            raise ValueError("正式下载清单缺少内置 Ed25519 发布公钥。")
        return False
    if not isinstance(signature, dict):
        if required:
            raise ValueError("正式下载清单缺少 Ed25519 签名。")
        return False
    if str(signature.get("algorithm", "")).casefold() != "ed25519":
        raise ValueError("下载清单签名算法必须是 Ed25519。")
    try:
        signed = base64.b64decode(str(signature.get("value", "")).strip(), validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("下载清单签名不是有效的 Base64。") from error
    if len(signed) != 64:
        raise ValueError("下载清单 Ed25519 签名长度无效。")
    try:
        Ed25519PublicKey.from_public_bytes(decode_public_key(public_key)).verify(
            signed,
            canonical_manifest_bytes(manifest),
        )
    except InvalidSignature as error:
        raise ValueError("下载清单签名校验失败。") from error
    return True
