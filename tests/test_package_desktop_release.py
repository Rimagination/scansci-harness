from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scansci_html.manifest_trust import verify_manifest_signature


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_release_preserves_multipart_runtime_contract(tmp_path: Path) -> None:
    build = tmp_path / "build" / "ScanSci"
    build.mkdir(parents=True)
    (build / "ScanSci.exe").write_bytes(b"desktop")
    runtime = tmp_path / "local-transformers.json"
    runtime.write_text(
        json.dumps(
            {
                "id": "local-transformers",
                "version": "1.0.0",
                "windows": {
                    "sha256": "a" * 64,
                    "size": 12,
                    "parts": [
                        {
                            "url": "https://example.test/runtime.zip.part001",
                            "sha256": "b" * 64,
                            "size": 12,
                        }
                    ],
                    "diagnostics": {
                        "args": ["--diagnose-output", "{output}"],
                        "timeout_seconds": 180,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "release"
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "release.key"
    private_path.write_text(
        base64.b64encode(
            private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("ascii"),
        encoding="utf-8",
    )
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "package_desktop_release.ps1"),
            "-BuildDir",
            str(build),
            "-Version",
            "0.2.0-beta.1",
            "-PackageUrl",
            "https://example.test/ScanSci.zip",
            "-OutputDir",
            str(output),
            "-Channel",
            "beta",
            "-LocalRuntimeManifest",
            str(runtime),
            "-ManifestPrivateKey",
            str(private_path),
            "-ManifestKeyId",
            "package-test",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    stable = json.loads((output / "stable.json").read_text(encoding="utf-8-sig"))
    archive = output / "ScanSci-0.2.0-beta.1-windows-x64.zip"
    blockmap_path = output / "ScanSci-0.2.0-beta.1-windows-x64.zip.blockmap"
    blockmap = json.loads(blockmap_path.read_text(encoding="utf-8-sig"))
    windows = stable["windows"]
    assert stable["schema_version"] == 1
    assert stable["signature"]["key_id"] == "package-test"
    assert verify_manifest_signature(stable, public, required=True) is True
    assert archive.exists()
    assert blockmap["schema_version"] == 1
    assert blockmap["algorithm"] == "sha256"
    assert blockmap["block_size"] == 65536
    assert blockmap["size"] == archive.stat().st_size
    assert len(blockmap["blocks"]) == (archive.stat().st_size + 65535) // 65536
    assert windows["blockmap"]["url"] == "https://example.test/ScanSci.zip.blockmap"
    assert windows["size"] == archive.stat().st_size
    assert windows["blockmap"]["size"] == blockmap_path.stat().st_size
    assert windows["blockmap"]["sha256"]
    windows = stable["components"]["local-transformers"]["windows"]
    assert windows["sha256"] == "a" * 64
    assert windows["size"] == 12
    assert windows["parts"][0]["url"].endswith(".part001")
    assert windows["parts"][0]["sha256"] == "b" * 64
    assert windows["diagnostics"]["timeout_seconds"] == 180
