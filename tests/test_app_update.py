from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZipInfo

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scansci_html import app_update
from scansci_html import build_info
from scansci_html.app_update import APP_VERSION, AppUpdateService
from scansci_html.manifest_trust import canonical_manifest_bytes
from scansci_html.update_blockmap import build_blockmap


def test_update_status_is_honest_without_release_channel(tmp_path: Path) -> None:
    service = AppUpdateService(manifest_url="", updates_root=tmp_path)

    status = service.check()

    assert status["state"] == "idle"
    assert status["available"] is False
    assert status["current_version"] == APP_VERSION
    assert status["message"] == f"当前版本 v{APP_VERSION}"


def test_packaged_update_service_uses_the_public_channel_when_environment_is_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    service = AppUpdateService(updates_root=tmp_path)

    assert service.manifest_url == app_update.DEFAULT_UPDATE_MANIFEST_URL


def test_update_service_uses_channel_and_trust_key_embedded_in_build_info(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SCANSCI_UPDATE_MANIFEST_URL", raising=False)
    monkeypatch.delenv("SCANSCI_RELEASE_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(
        build_info,
        "current_build_info",
        lambda: {
            "version": "0.4.2",
            "update_manifest_url": "https://downloads.example.test/stable.json",
            "release_public_key": "embedded-key",
        },
    )

    service = AppUpdateService(updates_root=tmp_path)

    assert service.manifest_url == "https://downloads.example.test/stable.json"
    assert service.release_public_key == "embedded-key"


def test_update_service_reads_verified_windows_release_manifest(tmp_path: Path) -> None:
    archive = tmp_path / "ScanSci-0.3.0.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("ScanSci/ScanSci.exe", b"desktop-binary")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = tmp_path / "stable.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "0.3.0",
                "title": "ScanSci 0.3.0",
                "channel": "稳定版",
                "notes": [{"title": "新功能", "items": ["新增更新中心。"]}],
                "windows": {"url": archive.as_uri(), "sha256": checksum, "size": archive.stat().st_size},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    status = AppUpdateService(
        manifest_url=manifest.as_uri(),
        current_version="0.2.3",
        updates_root=tmp_path / "updates",
    ).check()

    assert status["state"] == "available"
    assert status["latest_version"] == "0.3.0"
    assert status["can_install"] is True
    assert status["release_notes"][0]["items"] == ["新增更新中心。"]


def test_update_status_marks_blockmap_capable_release(tmp_path: Path) -> None:
    archive = tmp_path / "ScanSci-0.3.0.zip"
    archive.write_bytes(_zip_bytes(b"new-desktop-binary"))
    blockmap = tmp_path / "ScanSci-0.3.0.zip.blockmap"
    blockmap.write_text(json.dumps(build_blockmap(archive)), encoding="utf-8")
    manifest = tmp_path / "stable.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "0.3.0",
                "windows": {
                    "url": archive.as_uri(),
                    "sha256": _sha256(archive),
                    "size": archive.stat().st_size,
                    "blockmap": {
                        "url": blockmap.as_uri(),
                        "sha256": _sha256(blockmap),
                        "size": blockmap.stat().st_size,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    status = AppUpdateService(manifest_url=manifest.as_uri(), updates_root=tmp_path / "updates").check()

    assert status["update_mode"] == "differential-capable"
    assert status["blockmap_size"] == blockmap.stat().st_size


def test_update_status_ignores_blockmap_with_wrong_declared_size(tmp_path: Path) -> None:
    archive = tmp_path / "ScanSci-0.3.0.zip"
    archive.write_bytes(_zip_bytes(b"new-desktop-binary"))
    blockmap = tmp_path / "ScanSci-0.3.0.zip.blockmap"
    blockmap.write_text(json.dumps(build_blockmap(archive)), encoding="utf-8")
    manifest = tmp_path / "stable.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "0.3.0",
                "windows": {
                    "url": archive.as_uri(),
                    "sha256": _sha256(archive),
                    "size": archive.stat().st_size,
                    "blockmap": {
                        "url": blockmap.as_uri(),
                        "sha256": _sha256(blockmap),
                        "size": blockmap.stat().st_size + 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    service = AppUpdateService(manifest_url=manifest.as_uri(), updates_root=tmp_path / "updates")
    service.check()
    destination = tmp_path / "updates" / "ScanSci-update.zip.blockmap"
    destination.parent.mkdir(parents=True)

    assert service._prepare_blockmap(service._windows_package(service._manifest or {}), destination) is None
    assert not destination.exists()


def test_install_prefers_differential_update_from_cached_current_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    current_version = "0.2.3"
    latest_version = "0.3.0"
    updates_root = tmp_path / "updates"
    base_dir = updates_root / current_version
    base_dir.mkdir(parents=True)
    base_archive = base_dir / "ScanSci-update.zip"
    base_archive.write_bytes(_zip_bytes(b"old-desktop-binary"))
    base_blockmap = base_dir / "ScanSci-update.zip.blockmap"
    base_blockmap.write_text(json.dumps(build_blockmap(base_archive)), encoding="utf-8")

    new_archive = tmp_path / "ScanSci-0.3.0.zip"
    new_archive.write_bytes(_zip_bytes(b"new-desktop-binary"))
    new_blockmap = tmp_path / "ScanSci-0.3.0.zip.blockmap"
    new_blockmap.write_text(json.dumps(build_blockmap(new_archive)), encoding="utf-8")
    manifest = tmp_path / "stable.json"
    manifest.write_text(
        json.dumps(
            {
                "version": latest_version,
                "windows": {
                    "url": new_archive.as_uri(),
                    "sha256": _sha256(new_archive),
                    "size": new_archive.stat().st_size,
                    "blockmap": {
                        "url": new_blockmap.as_uri(),
                        "sha256": _sha256(new_blockmap),
                        "size": new_blockmap.stat().st_size,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    executable = install_dir / "ScanSci.exe"
    executable.write_bytes(b"running")
    calls: list[Path] = []

    def fake_differential(old_file, old_map, new_url, new_map, destination):
        calls.append(Path(old_file))
        assert new_url == new_archive.as_uri()
        destination.write_bytes(new_archive.read_bytes())
        return {"used_differential": True, "bytes_downloaded": 17, "total_size": new_archive.stat().st_size}

    monkeypatch.setattr(app_update, "download_differential", fake_differential)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    monkeypatch.setattr(sys, "executable", str(executable), raising=False)
    launched: list[list[str]] = []
    monkeypatch.setattr(app_update.subprocess, "Popen", lambda command, **_kwargs: launched.append(list(command)))

    service = AppUpdateService(
        manifest_url=manifest.as_uri(),
        current_version=current_version,
        updates_root=updates_root,
    )
    service.check()
    result = service.install()

    assert result["update_mode"] == "differential"
    assert result["bytes_downloaded"] == 17
    assert calls == [base_archive]
    assert launched
    command = launched[0]
    assert command[command.index("-TargetVersion") + 1] == latest_version
    assert len(command[command.index("-ReadyToken") + 1]) == 32


def _zip_bytes(executable_payload: bytes) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w") as output:
        output.writestr("ScanSci.exe", executable_payload)
    return stream.getvalue()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_update_service_does_not_treat_equivalent_versions_as_new(tmp_path: Path) -> None:
    manifest = tmp_path / "stable.json"
    manifest.write_text(json.dumps({"version": "0.2", "windows": {}}), encoding="utf-8")

    status = AppUpdateService(
        manifest_url=manifest.as_uri(),
        current_version="0.2.0",
        updates_root=tmp_path / "updates",
    ).check()

    assert status["state"] == "current"
    assert status["available"] is False


class _MemoryResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, final_url: str) -> None:
        super().__init__(payload)
        self.headers: dict[str, str] = {}
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def geturl(self) -> str:
        return self._final_url


def _signed_update_manifest() -> tuple[dict[str, object], str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "version": "9.9.9",
        "windows": {
            "url": "https://downloads.example.test/ScanSci.zip",
            "sha256": "a" * 64,
            "size": 123,
        },
    }
    manifest["signature"] = {
        "algorithm": "ed25519",
        "key_id": "test",
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode("ascii"),
    }
    return manifest, base64.b64encode(public).decode("ascii")


def test_remote_update_manifest_requires_valid_signature_when_public_key_is_pinned(tmp_path: Path, monkeypatch) -> None:
    manifest, public = _signed_update_manifest()

    monkeypatch.setattr(
        app_update,
        "urlopen",
        lambda *_args, **_kwargs: _MemoryResponse(
            json.dumps(manifest).encode("utf-8"),
            final_url="https://downloads.example.test/stable.json",
        ),
    )
    service = AppUpdateService(
        manifest_url="https://downloads.example.test/stable.json",
        current_version="0.4.2",
        updates_root=tmp_path,
        release_public_key=public,
    )

    assert service.check()["state"] == "available"

    manifest["version"] = "9.9.8"
    status = service.check()
    assert status["state"] == "error"
    assert "签名校验失败" in status["error"]


def test_update_manifest_rejects_https_redirect_to_local_file(tmp_path: Path, monkeypatch) -> None:
    manifest, public = _signed_update_manifest()
    monkeypatch.setattr(
        app_update,
        "urlopen",
        lambda *_args, **_kwargs: _MemoryResponse(
            json.dumps(manifest).encode("utf-8"),
            final_url=(tmp_path / "redirected.json").as_uri(),
        ),
    )

    status = AppUpdateService(
        manifest_url="https://downloads.example.test/stable.json",
        current_version="0.4.2",
        updates_root=tmp_path,
        release_public_key=public,
    ).check()

    assert status["state"] == "error"
    assert "HTTPS" in status["error"]


def test_local_unsigned_manifest_remains_available_for_development_with_a_pinned_key(tmp_path: Path) -> None:
    archive = tmp_path / "ScanSci.zip"
    archive.write_bytes(_zip_bytes(b"desktop"))
    manifest = tmp_path / "stable.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "9.9.9",
                "windows": {
                    "url": archive.as_uri(),
                    "sha256": _sha256(archive),
                    "size": archive.stat().st_size,
                },
            }
        ),
        encoding="utf-8",
    )

    status = AppUpdateService(
        manifest_url=manifest.as_uri(),
        current_version="0.4.2",
        updates_root=tmp_path / "updates",
        release_public_key="unused-for-local-files",
    ).check()

    assert status["state"] == "available"


def test_update_download_rejects_wrong_size_and_removes_partial_file(tmp_path: Path) -> None:
    source = tmp_path / "payload.zip"
    source.write_bytes(b"abc")
    destination = tmp_path / "downloaded.zip"

    with pytest.raises(ValueError, match="大小|完整"):
        AppUpdateService._download(source.as_uri(), destination, expected_size=4, allow_file=True)

    assert not destination.exists()
    assert not destination.with_name(destination.name + ".download").exists()


def test_update_archive_rejects_duplicate_casefolded_paths(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("ScanSci.exe", b"first")
        output.writestr("SCANSCI.EXE", b"second")

    with pytest.raises(ValueError, match="重复路径"):
        app_update._validate_release_archive(archive)


def test_update_archive_rejects_symlinks_and_nested_executable(tmp_path: Path) -> None:
    symlink_archive = tmp_path / "symlink.zip"
    link = ZipInfo("runtime-link")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with ZipFile(symlink_archive, "w") as output:
        output.writestr("ScanSci.exe", b"desktop")
        output.writestr(link, "target")
    with pytest.raises(ValueError, match="符号链接"):
        app_update._validate_release_archive(symlink_archive)

    nested_archive = tmp_path / "nested.zip"
    with ZipFile(nested_archive, "w") as output:
        output.writestr("unexpected/depth/ScanSci.exe", b"desktop")
    with pytest.raises(ValueError, match="发行根目录"):
        app_update._validate_release_archive(nested_archive)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell updater is Windows-only")
def test_windows_updater_restores_previous_install_when_new_executable_cannot_start(tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    install_dir = tmp_path / "ScanSci"
    install_dir.mkdir()
    executable = install_dir / "ScanSci.exe"
    executable.write_bytes(b"previous-version")
    archive = tmp_path / "update.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("ScanSci.exe", b"invalid-new-executable")
    relaunch_args = tmp_path / "relaunch-args.json"
    relaunch_args.write_text("[]", encoding="utf-8")
    helper = tmp_path / "apply-update.ps1"
    helper.write_text(app_update._UPDATER_SCRIPT, encoding="utf-8-sig")

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-ParentPid",
            "2147483647",
            "-Archive",
            str(archive),
            "-InstallDir",
            str(install_dir),
            "-ExecutableName",
            "ScanSci.exe",
            "-RelaunchArgsPath",
            str(relaunch_args),
            "-TargetVersion",
            "9.9.9",
            "-ReadyToken",
            "a" * 32,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode != 0
    assert executable.read_bytes() == b"previous-version"
    failed_candidates = list(tmp_path.glob("ScanSci.failed-*"))
    assert len(failed_candidates) == 1
    assert (failed_candidates[0] / "ScanSci.exe").read_bytes() == b"invalid-new-executable"
    assert "Update error" in (tmp_path / "apply-update-error.log").read_text(encoding="utf-8-sig")


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell updater is Windows-only")
def test_windows_updater_rolls_back_when_new_process_exits_before_startup_confirmation(tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    short_lived = shutil.which("where.exe")
    if not powershell or not short_lived:
        pytest.skip("PowerShell or where.exe is unavailable")
    install_dir = tmp_path / "ScanSci"
    install_dir.mkdir()
    executable = install_dir / "ScanSci.exe"
    shutil.copyfile(powershell, executable)
    previous_digest = _sha256(executable)
    archive = tmp_path / "update.zip"
    with ZipFile(archive, "w") as output:
        output.write(short_lived, "ScanSci.exe")
    relaunch_args = tmp_path / "relaunch-args.json"
    relaunch_args.write_text(
        json.dumps(["-NoProfile", "-Command", "Start-Sleep -Seconds 3"]),
        encoding="utf-8",
    )
    helper = tmp_path / "apply-update.ps1"
    helper.write_text(app_update._UPDATER_SCRIPT, encoding="utf-8-sig")

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-ParentPid",
            "2147483647",
            "-Archive",
            str(archive),
            "-InstallDir",
            str(install_dir),
            "-ExecutableName",
            "ScanSci.exe",
            "-RelaunchArgsPath",
            str(relaunch_args),
            "-TargetVersion",
            "9.9.9",
            "-ReadyToken",
            "b" * 32,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode != 0
    assert _sha256(executable) == previous_digest
    assert len(list(tmp_path.glob("ScanSci.failed-*"))) == 1
    assert "exited during startup" in (tmp_path / "apply-update-error.log").read_text(encoding="utf-8-sig")
