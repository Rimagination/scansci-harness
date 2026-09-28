from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from zipfile import ZipFile

from scripts.archive_runtime_component import archive_component


def test_archives_runtime_with_installable_root_and_checksum(tmp_path: Path) -> None:
    package = tmp_path / "ScanSciLocalRuntime"
    (package / "_internal").mkdir(parents=True)
    (package / "ScanSciLocalRuntime.exe").write_bytes(b"exe")
    (package / "_internal" / "runtime.dat").write_bytes(b"runtime")
    notice = tmp_path / "NOTICE.md"
    notice.write_text("licenses", encoding="utf-8")

    archive, manifest_path = archive_component(
        package,
        tmp_path / "release",
        "1.2.3",
        notice_file=notice,
    )

    with ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {
            "ScanSciLocalRuntime/ScanSciLocalRuntime.exe",
            "ScanSciLocalRuntime/THIRD_PARTY_NOTICES.md",
            "ScanSciLocalRuntime/_internal/runtime.dat",
        }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["version"] == "1.2.3"
    assert manifest["windows"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert manifest["windows"]["size"] == archive.stat().st_size
    assert manifest["windows"]["diagnostics"]["args"] == ["--diagnose-output", "{output}"]


def test_archives_runtime_as_checksums_parts(tmp_path: Path) -> None:
    package = tmp_path / "ScanSciLocalRuntime"
    package.mkdir()
    (package / "ScanSciLocalRuntime.exe").write_bytes(os.urandom(2 * 1024 * 1024 + 9))

    archive, manifest_path = archive_component(
        package,
        tmp_path / "release",
        "2.0.0",
        package_url="https://downloads.example/runtime/",
        part_size_mb=1,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parts = manifest["windows"]["parts"]
    assert len(parts) >= 2
    assert "url" not in manifest["windows"]
    assert all(part["url"].startswith("https://downloads.example/runtime/") for part in parts)
    assert sum(part["size"] for part in parts) == archive.stat().st_size
    assert all(
        hashlib.sha256((archive.parent / Path(part["url"]).name).read_bytes()).hexdigest()
        == part["sha256"]
        for part in parts
    )
