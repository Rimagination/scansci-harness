from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.request import urlopen
from zipfile import ZipFile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scansci_html import local_runtime_component
import scansci_html.local_transformers_runtime as local_transformers_runtime
from scansci_html.app_update import AppUpdateService
from scansci_html.local_runtime_component import LocalRuntimeComponent
from scansci_html.local_runtime_compatibility import ModelCompatibilityStore
from scansci_html.local_runtime_server import LocalRuntimeServer
from scansci_html.manifest_trust import canonical_manifest_bytes
from scansci_html.webapp import NotebookWebApp


def test_local_runtime_component_uses_configured_root(monkeypatch, tmp_path):
    configured = tmp_path / "selected-runtime"
    monkeypatch.setenv("SCANSCI_LOCAL_RUNTIME_ROOT", str(configured))

    component = LocalRuntimeComponent(manifest_url="")

    assert component.root == configured.resolve()


def test_pagefile_error_guidance_does_not_recommend_freeing_gpu_vram():
    message = LocalRuntimeComponent._actionable_runtime_message(
        "页面文件太小，无法完成操作。 (os error 1455)"
    )

    assert "虚拟内存（页面文件）" in message
    assert "显存" not in message


def test_installed_component_is_preferred_even_when_source_has_torch(tmp_path: Path, monkeypatch) -> None:
    class InstalledComponent:
        def executable(self):
            return tmp_path / "ScanSciLocalRuntime.exe"

        def ensure_process(self, model_id: str) -> str:
            assert model_id == "Qwen/Qwen3.5-2B"
            return "http://127.0.0.1:17863/v1"

    monkeypatch.setattr(local_transformers_runtime, "default_local_runtime_component", InstalledComponent)
    monkeypatch.setattr(
        local_transformers_runtime._RUNTIME,
        "ensure",
        lambda _model_id: (_ for _ in ()).throw(AssertionError("in-process runtime must not start")),
    )

    assert local_transformers_runtime.ensure_local_transformers_runtime("Qwen/Qwen3.5-2B") == (
        "http://127.0.0.1:17863/v1"
    )


def test_runtime_component_omits_new_state_flag_for_legacy_sidecar_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 1.0.4 sidecar must not exit argparse(2) on the newer optional flag."""

    executable = tmp_path / "ScanSciLocalRuntime.exe"
    executable.write_bytes(b"runtime-binary")
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url="")
    monkeypatch.setattr(component, "executable", lambda: executable)

    health_values = iter(("", "Qwen/Test"))
    monkeypatch.setattr(component, "_health_model", lambda _url: next(health_values, "Qwen/Test"))
    probe = {}

    def fake_run(command, **_kwargs):
        probe["command"] = list(command)
        return SimpleNamespace(stdout="--model-id MODEL --parent-pid PID", stderr="", returncode=0)

    class FakeProcess:
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **_kwargs):
        probe["launch"] = list(command)
        return FakeProcess()

    monkeypatch.setattr(local_runtime_component.subprocess, "run", fake_run)
    monkeypatch.setattr(local_runtime_component.subprocess, "Popen", fake_popen)

    assert component.ensure_process("Qwen/Test") == "http://127.0.0.1:17863/v1"
    assert probe["command"][-1] == "--help"
    assert "--state-dir" not in probe["launch"]


def test_runtime_component_clears_stale_probe_failure_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openbmb/MiniCPM-V-4.6-BNB"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"weights")
    record = {
        "id": model_id,
        "path": str(snapshot),
        "model_files_present": True,
    }
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url="")
    compatibility = ModelCompatibilityStore(
        component.root / "state" / "model-compatibility.json"
    )
    compatibility.record_failure(
        record,
        component_version=component.component_version,
        error={"code": "model_probe_failed", "message": "temporary pagefile error"},
    )
    monkeypatch.setattr("scansci_html.local_model_market.installed_models", lambda: [record])

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(
                {"choices": [{"message": {"content": "ready"}}]}
            ).encode("utf-8")

    monkeypatch.setattr(local_runtime_component, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    component._probe_generation("http://127.0.0.1:17863/v1", model_id)

    entry = json.loads(
        (component.root / "state" / "model-compatibility.json").read_text(encoding="utf-8")
    )["models"][model_id]
    assert entry["compatible"] is True
    assert entry["generated"] is True
    assert entry["generated_preview"] == "ready"


def _component_manifest(tmp_path: Path) -> Path:
    archive = tmp_path / "runtime.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("ScanSciLocalRuntime/ScanSciLocalRuntime.exe", b"runtime-binary")
        output.writestr("ScanSciLocalRuntime/_internal/runtime.dat", b"dependency")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "local-transformers",
                "version": "1.2.0",
                "windows": {
                    "url": archive.as_uri(),
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_remote_runtime_manifest_requires_signature_when_release_key_is_pinned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    manifest = {
        "schema_version": 1,
        "id": "local-transformers",
        "version": "1.2.0",
        "windows": {
            "url": "https://downloads.example.test/runtime.zip",
            "sha256": "a" * 64,
            "size": 42,
        },
    }
    manifest["signature"] = {
        "algorithm": "ed25519",
        "key_id": "test",
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode("ascii"),
    }

    class _ManifestResponse(io.BytesIO):
        def __init__(self, payload: bytes) -> None:
            super().__init__(payload)
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            self.close()

        def geturl(self) -> str:
            return "https://downloads.example.test/runtime.json"

    payload = json.dumps(manifest).encode("utf-8")
    monkeypatch.setattr(
        local_runtime_component,
        "urlopen",
        lambda *_args, **_kwargs: _ManifestResponse(payload),
    )
    component = LocalRuntimeComponent(
        root=tmp_path / "installed",
        manifest_url="https://downloads.example.test/runtime.json",
        release_public_key=public,
    )

    assert component._read_json(component.manifest_url)["version"] == "1.2.0"

    del manifest["signature"]
    payload = json.dumps(manifest).encode("utf-8")
    with pytest.raises(ValueError, match="缺少 Ed25519 签名"):
        component._read_json(component.manifest_url)


def test_remote_runtime_manifest_cannot_select_a_local_package(tmp_path: Path) -> None:
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url="")

    with pytest.raises(ValueError, match="HTTPS"):
        component._validate_component_package(
            {
                "url": (tmp_path / "runtime.zip").as_uri(),
                "sha256": "a" * 64,
                "size": 42,
            },
            allow_file=False,
            require_metadata=False,
        )


def test_runtime_component_sanitizes_manifest_version_before_building_download_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url="")
    observed: dict[str, Path] = {}

    def stop_before_download(_package, archive, _expected, **_kwargs):
        observed["archive"] = archive
        raise RuntimeError("stop after path selection")

    monkeypatch.setattr(component, "_acquire_package", stop_before_download)
    with pytest.raises(RuntimeError, match="path selection"):
        component._install_component_package(
            {"version": "x/../../../../outside"},
            {"sha256": "a" * 64},
        )

    archive = observed["archive"].resolve()
    assert archive.parent == (component.root / "downloads").resolve()
    assert ".." not in archive.name


def test_runtime_component_rejects_multipart_aggregate_before_downloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url="")
    monkeypatch.setattr(
        component,
        "_download",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download must not start")),
    )
    package = {
        "sha256": "a" * 64,
        "size": 1,
        "parts": [
            {"url": "https://downloads.example.test/part1", "sha256": "b" * 64, "size": 8},
            {"url": "https://downloads.example.test/part2", "sha256": "c" * 64, "size": 8},
        ],
    }

    with pytest.raises(ValueError, match="总大小"):
        component._acquire_package(package, tmp_path / "runtime.zip", "a" * 64)


def _multipart_component_manifest(tmp_path: Path) -> Path:
    manifest = _component_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    archive = tmp_path / "runtime.zip"
    content = archive.read_bytes()
    split = max(1, len(content) // 2)
    parts = []
    for index, chunk in enumerate((content[:split], content[split:]), start=1):
        path = tmp_path / f"runtime.zip.part{index:03d}"
        path.write_bytes(chunk)
        parts.append(
            {
                "url": path.as_uri(),
                "sha256": hashlib.sha256(chunk).hexdigest(),
                "size": len(chunk),
            }
        )
    payload["windows"].pop("url")
    payload["windows"]["size"] = len(content)
    payload["windows"]["parts"] = parts
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    archive.unlink()
    return manifest


def test_runtime_component_installs_once_and_uses_active_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url=_component_manifest(tmp_path).as_uri())

    first = component.install()
    second = component.ensure_installed()

    assert first["installed"] is True
    assert first["version"] == "1.2.0"
    assert second == tmp_path / "installed" / "versions" / "1.2.0" / "ScanSciLocalRuntime.exe"
    assert second.read_bytes() == b"runtime-binary"


def test_same_version_install_repairs_a_missing_runtime_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url=_component_manifest(tmp_path).as_uri())
    component.install()
    dependency = component.root / "versions" / "1.2.0" / "_internal" / "runtime.dat"
    dependency.unlink()

    result = component.install()

    assert result["installed"] is True
    assert dependency.read_bytes() == b"dependency"


def test_failed_staged_runtime_validation_keeps_the_active_same_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url=_component_manifest(tmp_path).as_uri())
    component.install()
    executable = component.ensure_installed()
    assert executable is not None
    executable.write_bytes(b"known-good-active-runtime")
    monkeypatch.setattr(
        component,
        "_run_diagnostics",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("staged diagnostics failed")),
    )

    with pytest.raises(RuntimeError, match="所有自动下载通道均不可用"):
        component.install()

    assert component.ensure_installed() == executable
    assert executable.read_bytes() == b"known-good-active-runtime"


def test_runtime_component_rejects_casefolded_duplicate_archive_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    archive = tmp_path / "runtime.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("ScanSciLocalRuntime/ScanSciLocalRuntime.exe", b"runtime-binary")
        output.writestr("ScanSciLocalRuntime/runtime.dat", b"first")
        output.writestr("ScanSciLocalRuntime/RUNTIME.DAT", b"second")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "local-transformers",
                "version": "1.2.0",
                "windows": {
                    "url": archive.as_uri(),
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url=manifest.as_uri())

    with pytest.raises(RuntimeError, match="重复路径"):
        component.install()


def test_runtime_component_installs_from_verified_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    component = LocalRuntimeComponent(
        root=tmp_path / "installed",
        manifest_url=_multipart_component_manifest(tmp_path).as_uri(),
    )

    result = component.install()

    assert result["installed"] is True
    assert component.ensure_installed().read_bytes() == b"runtime-binary"


def test_runtime_component_manual_zip_fallback_installs_without_a_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    manifest = _component_manifest(tmp_path)
    archive = tmp_path / "runtime.zip"
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url="")

    result = component.install_local([str(archive)])

    assert result["installed"] is True
    assert result["source"] == "manual"
    assert component.ensure_installed().read_bytes() == b"runtime-binary"
    assert manifest.is_file()


def test_runtime_component_switches_to_a_healthy_manifest_fallback_and_reports_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.json"
    healthy = _component_manifest(tmp_path)
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {
            "frozen": True,
            "package_profile": "core-without-embedded-fallback",
            "runtime_manifest_url": missing.as_uri(),
        },
    )
    component = LocalRuntimeComponent(
        root=tmp_path / "installed",
        fallback_manifest_url=healthy.as_uri(),
    )

    result = component.install()
    report = component.check_manifest_channels()

    assert result["installed"] is True
    assert result["source"] == healthy.as_uri()
    assert result["manifest_failures"][0]["url"] == missing.as_uri()
    assert report["available"] is True
    assert report["preferred_url"] == healthy.as_uri()
    assert report["channels"][0]["valid"] is False
    assert report["channels"][1]["valid"] is True


def test_runtime_component_switches_source_when_preferred_manifest_asset_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy = _component_manifest(tmp_path)
    broken = tmp_path / "broken-manifest.json"
    broken_payload = json.loads(healthy.read_text(encoding="utf-8"))
    broken_payload["windows"]["url"] = (tmp_path / "missing-runtime.zip").as_uri()
    broken.write_text(json.dumps(broken_payload), encoding="utf-8")
    monkeypatch.setattr(local_runtime_component, "_RUNTIME_RETRY_DELAYS_SECONDS", (0.0,))
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {
            "frozen": True,
            "package_profile": "core-without-embedded-fallback",
            "runtime_manifest_url": broken.as_uri(),
        },
    )
    component = LocalRuntimeComponent(
        root=tmp_path / "installed",
        fallback_manifest_url=healthy.as_uri(),
    )

    result = component.install()

    assert result["installed"] is True
    assert result["source"] == healthy.as_uri()
    assert result["manifest_failures"][0]["url"] == broken.as_uri()
    assert "missing-runtime.zip" in result["manifest_failures"][0]["error"]


def test_runtime_component_extracts_deep_windows_paths_without_zip_extractall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    archive = tmp_path / "runtime-long-path.zip"
    deep_member = "ScanSciLocalRuntime/_internal/" + "/".join(["third_party_component"] * 16) + "/LICENSE.txt"
    with ZipFile(archive, "w") as output:
        output.writestr("ScanSciLocalRuntime/ScanSciLocalRuntime.exe", b"runtime-binary")
        output.writestr(deep_member, b"license")
    manifest = tmp_path / "runtime-long-path.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "local-transformers",
                "version": "1.2.1",
                "windows": {
                    "url": archive.as_uri(),
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url=manifest.as_uri())

    result = component.install()

    assert result["installed"] is True
    assert component.ensure_installed().read_bytes() == b"runtime-binary"


def test_runtime_component_rejects_a_second_process_install_for_the_same_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    root = tmp_path / "installed"
    first = LocalRuntimeComponent(root=root, manifest_url=_component_manifest(tmp_path).as_uri())
    second = LocalRuntimeComponent(root=root, manifest_url=_component_manifest(tmp_path).as_uri())

    with first._process_install_lock():
        with pytest.raises(RuntimeError, match="正在安装本地运行组件"):
            second.install()


def test_runtime_component_installs_in_background_with_visible_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    component = LocalRuntimeComponent(
        root=tmp_path / "installed",
        manifest_url=_multipart_component_manifest(tmp_path).as_uri(),
    )

    started = component.start_install()
    deadline = time.time() + 5
    while component.install_status()["state"] in {"queued", "installing"} and time.time() < deadline:
        time.sleep(0.02)
    finished = component.install_status()

    assert started["state"] in {"queued", "installing"}
    assert finished["state"] == "ready"
    assert finished["progress"] == 1.0
    assert component.status()["installed"] is True


def test_runtime_component_pause_resume_cancel_and_retry_are_persistent_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )

    def build_fake(component: LocalRuntimeComponent, *, job_name: str) -> tuple[threading.Event, dict[str, int]]:
        entered = threading.Event()
        calls = {"count": 0}

        def fake_install(progress_callback=None):
            calls["count"] += 1
            for index in range(20):
                entered.set()
                if progress_callback:
                    progress_callback("download", index / 20, "下载官方组件", {"completed_bytes": index, "total_bytes": 20})
                time.sleep(0.01)
            return {"job_id": job_name, "installed": True}

        monkeypatch.setattr(component, "install", fake_install)
        return entered, calls

    component = LocalRuntimeComponent(root=tmp_path / "paused", manifest_url="https://example.test/runtime.json")
    entered, calls = build_fake(component, job_name="paused")
    component.start_install()
    assert entered.wait(1)
    requested = component.pause_install()
    assert requested["state"] in {"pausing", "paused"}
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and component.install_status()["state"] != "paused":
        time.sleep(0.01)
    assert component.install_status()["state"] == "paused"
    resumed = component.resume_install()
    assert resumed["state"] in {"queued", "installing"}
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and component.install_status()["state"] != "ready":
        time.sleep(0.01)
    assert component.install_status()["state"] == "ready"
    assert calls["count"] == 2

    cancelled = LocalRuntimeComponent(root=tmp_path / "cancelled", manifest_url="https://example.test/runtime.json")
    entered_cancel, cancel_calls = build_fake(cancelled, job_name="cancelled")
    cancelled.start_install()
    assert entered_cancel.wait(1)
    requested = cancelled.cancel_install()
    assert requested["state"] in {"cancelling", "cancelled"}
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and cancelled.install_status()["state"] != "cancelled":
        time.sleep(0.01)
    assert cancelled.install_status()["state"] == "cancelled"
    retried = cancelled.retry_install()
    assert retried["state"] in {"queued", "installing"}
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and cancelled.install_status()["state"] != "ready":
        time.sleep(0.01)
    assert cancelled.install_status()["state"] == "ready"
    assert cancel_calls["count"] == 2


def test_runtime_component_download_resumes_an_existing_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "runtime.zip"
    partial = Path(f"{destination}.download")
    partial.write_bytes(b"abcd")
    observed_ranges: list[str | None] = []

    class _Response:
        headers = {"Content-Range": "bytes 4-9/10", "Content-Length": "6"}

        def __init__(self) -> None:
            self._chunks = iter((b"efghij", b""))

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self, _size: int = -1) -> bytes:
            return next(self._chunks)

        def geturl(self) -> str:
            return "https://downloads.example.test/runtime.zip"

    def _urlopen(request, timeout=0):
        observed_ranges.append(request.get_header("Range"))
        assert timeout == 300
        return _Response()

    monkeypatch.setattr(local_runtime_component, "urlopen", _urlopen)
    progress: list[tuple[int, int]] = []

    LocalRuntimeComponent._download(
        "https://downloads.example.test/runtime.zip",
        destination,
        progress_callback=lambda received, total: progress.append((received, total)),
    )

    assert observed_ranges == ["bytes=4-"]
    assert progress[0] == (4, 10)
    assert progress[-1] == (10, 10)
    assert destination.read_bytes() == b"abcdefghij"
    assert not partial.exists()


def test_runtime_component_retries_a_connection_drop_and_resumes_the_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "runtime.zip"
    calls: list[str | None] = []

    class _Response:
        def __init__(self, payload: bytes, headers: dict[str, str], *, fail_after: bool = False) -> None:
            self._payload = payload
            self.headers = headers
            self._fail_after = fail_after
            self._read = False

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self, _size: int = -1) -> bytes:
            if self._read:
                if self._fail_after:
                    raise OSError("connection reset")
                return b""
            self._read = True
            return self._payload

        def geturl(self) -> str:
            return "https://downloads.example.test/runtime.zip"

    def _urlopen(request, timeout=0):
        calls.append(request.get_header("Range"))
        assert timeout == 300
        if len(calls) == 1:
            return _Response(b"abcd", {"Content-Length": "10"}, fail_after=True)
        assert calls[-1] == "bytes=4-"
        return _Response(
            b"efghij",
            {"Content-Range": "bytes 4-9/10", "Content-Length": "6"},
        )

    monkeypatch.setattr(local_runtime_component, "urlopen", _urlopen)
    monkeypatch.setattr(local_runtime_component, "_RUNTIME_RETRY_DELAYS_SECONDS", (0.0, 0.0))

    LocalRuntimeComponent._download(
        "https://downloads.example.test/runtime.zip",
        destination,
        expected_size=10,
    )

    assert calls == [None, "bytes=4-"]
    assert destination.read_bytes() == b"abcdefghij"
    assert not Path(f"{destination}.download").exists()


def test_runtime_component_download_removes_a_permanently_oversized_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "runtime.zip"

    class _Response:
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self, _size: int = -1) -> bytes:
            if getattr(self, "_read", False):
                return b""
            self._read = True
            return b"12345"

        def geturl(self) -> str:
            return "https://downloads.example.test/runtime.zip"

    monkeypatch.setattr(local_runtime_component, "urlopen", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(local_runtime_component, "_MAX_RUNTIME_DOWNLOAD_BYTES", 4)

    with pytest.raises(ValueError, match="超出允许范围"):
        LocalRuntimeComponent._download("https://downloads.example.test/runtime.zip", destination)

    assert not Path(f"{destination}.download").exists()


def test_runtime_component_restores_interrupted_install_with_download_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    root = tmp_path / "installed"
    root.mkdir()
    (root / "install-job.json").write_text(
        json.dumps(
            {
                "job_id": "local-runtime",
                "state": "installing",
                "phase": "download",
                "progress": 0.4,
                "message": "下载官方组件 40%",
                "current_file": "runtime.zip",
                "completed_bytes": 40,
                "total_bytes": 100,
                "updated_at": int(time.time()) - 20,
            }
        ),
        encoding="utf-8",
    )

    component = LocalRuntimeComponent(root=root, manifest_url=_component_manifest(tmp_path).as_uri())
    restored = component.install_status()

    assert restored["state"] == "interrupted"
    assert restored["current_file"] == "runtime.zip"
    assert restored["completed_bytes"] == 40
    assert "续传" in restored["message"]
    persisted = json.loads((root / "install-job.json").read_text(encoding="utf-8"))
    assert persisted["state"] == "interrupted"


def test_interrupted_replacement_does_not_hide_the_active_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    root = tmp_path / "installed"
    executable = root / "versions" / "1.2.0" / "ScanSciLocalRuntime.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"stable-runtime")
    (root / "active.json").write_text(
        json.dumps(
            {
                "id": "local-transformers",
                "version": "1.2.0",
                "executable": str(executable.relative_to(root)),
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    (root / "install-job.json").write_text(
        json.dumps(
            {
                "job_id": "local-runtime",
                "state": "installing",
                "phase": "download",
                "progress": 0.37,
                "message": "正在下载替换版本",
                "updated_at": int(time.time()) - 10,
            }
        ),
        encoding="utf-8",
    )

    component = LocalRuntimeComponent(root=root, manifest_url="")
    status = component.status()

    assert status["installed"] is True
    assert status["mode"] == "component"
    assert status["version"] == "1.2.0"
    assert Path(status["executable"]) == executable.resolve()
    assert status["install_job"]["state"] == "interrupted"


def test_outdated_managed_runtime_stays_visible_but_requires_component_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    root = tmp_path / "installed"
    executable = root / "versions" / "1.0.3" / "ScanSciLocalRuntime.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"old-runtime")
    (root / "active.json").write_text(
        json.dumps(
            {
                "id": "local-transformers",
                "version": "1.0.3",
                "executable": str(executable.relative_to(root)),
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    component = LocalRuntimeComponent(
        root=root,
        manifest_url="https://downloads.example.com/local-transformers.json",
        component_version="1.0.4",
        minimum_component_version="1.0.4",
    )

    status = component.status()

    assert status["installed"] is True
    assert status["version"] == "1.0.3"
    assert status["required_version"] == "1.0.4"
    assert status["recommended_version"] == "1.0.4"
    assert status["update_available"] is True
    assert status["update_required"] is True
    assert Path(status["executable"]) == executable.resolve()
    with pytest.raises(RuntimeError, match="1.0.4"):
        component.ensure_installed()


def test_compatible_runtime_can_run_while_a_newer_component_is_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    root = tmp_path / "installed"
    executable = root / "versions" / "1.0.0" / "ScanSciLocalRuntime.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"compatible-runtime")
    (root / "active.json").write_text(
        json.dumps(
            {
                "id": "local-transformers",
                "version": "1.0.0",
                "executable": str(executable.relative_to(root)),
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    component = LocalRuntimeComponent(
        root=root,
        manifest_url="https://downloads.example.com/local-transformers.json",
    )

    status = component.status()

    assert status["required_version"] == "1.0.0"
    assert status["recommended_version"] == "1.0.4"
    assert status["minimum_version"] == "1.0.0"
    assert status["update_available"] is True
    assert status["update_required"] is False
    assert component.ensure_installed() == executable.resolve()


def test_runtime_component_rejects_corrupt_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    manifest = _multipart_component_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    (tmp_path / "runtime.zip.part001").write_bytes(b"corrupt")
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url=manifest.as_uri())

    with pytest.raises(RuntimeError, match="分片校验失败"):
        component.install()


def test_core_runtime_requires_a_release_component_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    component = LocalRuntimeComponent(root=tmp_path, manifest_url="")

    with pytest.raises(RuntimeError, match="下载清单"):
        component.ensure_installed()


def test_packaged_core_uses_public_runtime_manifest_fallback_when_build_info_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core", "runtime_manifest_url": ""},
    )

    component = LocalRuntimeComponent(root=tmp_path)

    assert component.manifest_url == local_runtime_component.DEFAULT_RUNTIME_MANIFEST_URL
    assert local_runtime_component.DEFAULT_RUNTIME_MANIFEST_FALLBACK_URLS[0] in component.manifest_urls
    assert "local-runtime-v1.0.0" in component.status()["manifest_release_url"]
    assert component.status()["install_available"] is True


def test_core_runtime_never_installs_from_a_manifest_while_only_checking_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core"},
    )
    component = LocalRuntimeComponent(root=tmp_path / "installed", manifest_url=_component_manifest(tmp_path).as_uri())
    monkeypatch.setattr(component, "install", lambda: (_ for _ in ()).throw(AssertionError("must not download")))

    with pytest.raises(RuntimeError, match="设置 → 本地模型确认安装"):
        component.ensure_installed()


def test_source_runtime_is_not_marked_ready_without_its_actual_inference_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_runtime_component, "current_build_info", lambda: {"frozen": False})
    monkeypatch.setattr(local_runtime_component, "find_spec", lambda _module: None)
    component = LocalRuntimeComponent(root=tmp_path / "installed")

    assert component.status()["installed"] is False
    assert component.status()["mode"] == "missing"
    with pytest.raises(RuntimeError, match="设置 → 本地模型查看组件状态"):
        component.ensure_installed()


def test_source_runtime_server_exposes_health_without_loading_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    monkeypatch.setattr(
        "scansci_html.local_runtime_server.installed_models",
        lambda: [{"id": "Qwen/Test", "path": str(snapshot), "ready": True, "format": "transformers"}],
    )
    runtime = LocalRuntimeServer(port=0)
    runtime.start("Qwen/Test")
    try:
        with urlopen(f"http://127.0.0.1:{runtime.port}/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["status"] == "ok"
        assert payload["component"] == "local-transformers"
        assert payload["version"] == local_runtime_component.COMPONENT_VERSION
        assert payload["model"] == "Qwen/Test"
        assert payload["loaded"] is False
        assert "cuda_available" in payload
        assert payload["device"] in {"cpu", "cuda:0", "pending", "unavailable"}
    finally:
        runtime.shutdown()


def test_webapp_does_not_mask_embedded_runtime_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _component_manifest(tmp_path).as_uri()
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {"frozen": True, "package_profile": "core", "runtime_manifest_url": manifest},
    )

    app = NotebookWebApp(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")

    assert app.local_runtime.manifest_url == manifest
    assert app.local_runtime.status()["install_available"] is True


def test_webapp_prefers_embedded_runtime_manifest_over_app_update_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = _component_manifest(tmp_path).as_uri()
    app_update_manifest = (tmp_path / "app-update.json").as_uri()
    monkeypatch.setattr(
        local_runtime_component,
        "current_build_info",
        lambda: {
            "frozen": True,
            "package_profile": "core",
            "runtime_manifest_url": runtime_manifest,
        },
    )

    app = NotebookWebApp(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
        update_service=AppUpdateService(manifest_url=app_update_manifest),
    )

    assert app.local_runtime.manifest_url == runtime_manifest
