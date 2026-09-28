"""Sign a ScanSci release manifest with an offline Ed25519 private key."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scansci_html.manifest_trust import canonical_manifest_bytes


def load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        raw = base64.b64decode(path.read_text(encoding="utf-8").strip(), validate=True)
    except (OSError, ValueError) as error:
        raise ValueError("私钥文件必须包含 Base64 编码的 32 字节 Ed25519 私钥。") from error
    if len(raw) != 32:
        raise ValueError("Ed25519 私钥必须是 32 字节。")
    return Ed25519PrivateKey.from_private_bytes(raw)


def sign_manifest(path: Path, private_key: Ed25519PrivateKey, *, key_id: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("清单必须是 JSON 对象。")
    payload["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "value": base64.b64encode(private_key.sign(canonical_manifest_bytes(payload))).decode("ascii"),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--private-key", required=True, help="Path to an offline Base64 Ed25519 private key")
    parser.add_argument("--key-id", default="scansci-release-v1")
    parser.add_argument("--print-public-key", action="store_true")
    args = parser.parse_args(argv)
    key = load_private_key(Path(args.private_key).expanduser().resolve())
    sign_manifest(Path(args.manifest).expanduser().resolve(), key, key_id=args.key_id)
    if args.print_public_key:
        public = key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        print(base64.b64encode(public).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
