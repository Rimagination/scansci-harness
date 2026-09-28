from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scansci_html.manifest_trust import canonical_manifest_bytes, verify_manifest_signature
from scripts.sign_release_manifest import load_private_key, sign_manifest


def _key_pair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, base64.b64encode(public).decode("ascii")


def test_manifest_signature_survives_json_formatting_and_rejects_tampering() -> None:
    private, public = _key_pair()
    manifest = {"schema_version": 1, "version": "0.4.2", "notes": ["中文"]}
    manifest["signature"] = {
        "algorithm": "ed25519",
        "key_id": "test",
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode("ascii"),
    }

    reformatted = json.loads(json.dumps(manifest, ensure_ascii=False, indent=4))
    assert verify_manifest_signature(reformatted, public, required=True) is True

    reformatted["version"] = "9.9.9"
    with pytest.raises(ValueError, match="签名校验失败"):
        verify_manifest_signature(reformatted, public, required=True)


def test_signing_script_reads_raw_private_key_and_replaces_manifest_atomically(tmp_path: Path) -> None:
    private, public = _key_pair()
    private_bytes = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "release.key"
    key_path.write_text(base64.b64encode(private_bytes).decode("ascii"), encoding="utf-8")
    manifest_path = tmp_path / "stable.json"
    manifest_path.write_text('{"version":"0.4.2","schema_version":1}', encoding="utf-8")

    signed = sign_manifest(manifest_path, load_private_key(key_path), key_id="test-key")

    assert signed["signature"]["key_id"] == "test-key"
    assert verify_manifest_signature(
        json.loads(manifest_path.read_text(encoding="utf-8")),
        public,
        required=True,
    ) is True
    assert not manifest_path.with_suffix(".json.tmp").exists()
