from __future__ import annotations

import ctypes
import json
from pathlib import Path
from urllib.request import urlopen

from scansci_html import desktop
from scansci_html.desktop import ScanSciDesktopApi, desktop_diagnostics, launch_desktop
from scansci_html.evidence_store import index_evidence_library
from scansci_html.workspace import sync_sources_from_evidence_store


class _FakeWebView:
    def __init__(self) -> None:
        self.window: dict[str, object] | None = None

    def create_window(self, title: str, url: str, **options: object) -> None:
        self.window = {"title": title, "url": url, "options": options}

    def start(self) -> None:
        assert self.window is not None
        with urlopen(str(self.window["url"]), timeout=5) as response:
            assert response.status == 200
            assert "搜索科学".encode("utf-8") in response.read()


def test_desktop_launches_native_shell_against_ephemeral_loopback_server(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text("<article><h1>Desktop evidence</h1><p>Evidence stays local.</p></article>", encoding="utf-8")
    evidence = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=evidence, inject_evidence_html=True, min_sentence_length=10)
    workspace = tmp_path / "workspace.sqlite"
    sync_sources_from_evidence_store(workspace, evidence, notebook_id="desktop")

    fake = _FakeWebView()
    launch_desktop(workspace=workspace, evidence_db=evidence, title="ScanSci Test", webview_module=fake)

    assert fake.window is not None
    assert fake.window["title"] == "ScanSci Test"
    assert str(fake.window["url"]).startswith("http://127.0.0.1:")
    assert fake.window["options"]["min_size"] == (800, 560)
    assert fake.window["options"]["resizable"] is True
    assert fake.window["options"]["frameless"] is True
    assert fake.window["options"]["easy_drag"] is False
    assert fake.window["options"]["js_api"].__class__.__name__ == "ScanSciDesktopApi"


def test_desktop_confirms_transactional_update_after_window_creation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    marker = local_app_data / "ScanSciPi" / "updates" / "0.4.2" / "startup-ready.json"
    expected_version = str(desktop.current_build_info()["version"])
    token = "a" * 32
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("SCANSCI_UPDATE_READY_FILE", str(marker))
    monkeypatch.setenv("SCANSCI_UPDATE_READY_TOKEN", token)
    monkeypatch.setenv("SCANSCI_UPDATE_EXPECTED_VERSION", expected_version)
    fake = _FakeWebView()

    launch_desktop(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
        title="ScanSci Update Check",
        webview_module=fake,
    )

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["pid"] > 0
    assert payload["token"] == token
    assert payload["version"] == expected_version


def test_desktop_rejects_update_marker_outside_private_update_root(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "outside" / "startup-ready.json"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("SCANSCI_UPDATE_READY_FILE", str(marker))
    monkeypatch.setenv("SCANSCI_UPDATE_READY_TOKEN", "b" * 32)
    monkeypatch.setenv("SCANSCI_UPDATE_EXPECTED_VERSION", str(desktop.current_build_info()["version"]))

    launch_desktop(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
        title="ScanSci Update Check",
        webview_module=_FakeWebView(),
    )

    assert not marker.exists()


class _FakeUser32:
    def __init__(self) -> None:
        self.style = 0
        self.calls: list[tuple[object, ...]] = []

    def GetWindowLongPtrW(self, hwnd: int, index: int) -> int:
        self.calls.append(("get", hwnd, index))
        return self.style

    def SetWindowLongPtrW(self, hwnd: int, index: int, style: int) -> int:
        self.calls.append(("set", hwnd, index, style))
        self.style = style
        return style

    def SetWindowPos(self, *args: object) -> int:
        self.calls.append(("frame-changed", *args))
        return 1


class _FakeDwmApi:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int, int]] = []

    def DwmSetWindowAttribute(self, hwnd: int, attribute: int, value: object, size: int) -> int:
        decoded = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32)).contents.value
        self.calls.append((hwnd, attribute, decoded, size))
        return 0


def test_borderless_window_gets_native_resize_style() -> None:
    user32 = _FakeUser32()

    assert desktop._apply_borderless_resize_style(1234, user32) is True
    assert user32.style & 0x00040000
    assert user32.calls[0] == ("get", 1234, -16)
    assert user32.calls[1][0:3] == ("set", 1234, -16)
    assert user32.calls[2][0] == "frame-changed"


def test_maximized_native_frame_can_be_removed_and_bounds_are_exact() -> None:
    user32 = _FakeUser32()
    user32.style = 0x00040000

    assert desktop._set_resize_frame_style(1234, enabled=False, user32=user32) is True
    assert user32.style & 0x00040000 == 0
    assert desktop._set_native_window_bounds(1234, (0, 0, 1920, 1040), user32=user32) is True
    assert user32.calls[-1] == ("frame-changed", 1234, 0, 0, 0, 1920, 1040, 52)


def test_windows_window_chrome_removes_maximized_rounding_and_border(monkeypatch) -> None:
    monkeypatch.setattr(desktop.os, "name", "nt")
    dwmapi = _FakeDwmApi()

    assert desktop._set_windows_window_chrome(1234, maximized=True, dwmapi=dwmapi) is True
    assert dwmapi.calls == [
        (1234, 33, 1, ctypes.sizeof(ctypes.c_uint32)),
        (1234, 34, 0xFFFFFFFE, ctypes.sizeof(ctypes.c_uint32)),
    ]

    dwmapi.calls.clear()
    assert desktop._set_windows_window_chrome(1234, maximized=False, dwmapi=dwmapi) is True
    assert dwmapi.calls[0][0:3] == (1234, 33, 2)


def test_packaged_desktop_uses_per_user_data_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    workspace, evidence_db = desktop._default_desktop_paths()

    assert workspace == tmp_path / "LocalAppData" / "ScanSciPi" / "workspace.sqlite"
    assert evidence_db == tmp_path / "LocalAppData" / "ScanSciPi" / "evidence.sqlite"


class _FakeWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def minimize(self) -> None:
        self.calls.append("minimize")

    def maximize(self) -> None:
        self.calls.append("maximize")

    def restore(self) -> None:
        self.calls.append("restore")

    def destroy(self) -> None:
        self.calls.append("destroy")


class _FakeWindowModule:
    def __init__(self) -> None:
        self.window = _FakeWindow()
        self.windows = [self.window]


class _FakeSaveWindow(_FakeWindow):
    def __init__(self, destination: Path) -> None:
        super().__init__()
        self.destination = destination
        self.dialog_calls: list[dict[str, object]] = []

    def create_file_dialog(self, _dialog_type: int, **kwargs: object) -> tuple[str, ...]:
        self.dialog_calls.append(kwargs)
        return (str(self.destination),)


class _FakeSaveWindowModule:
    SAVE_DIALOG = 1

    def __init__(self, destination: Path) -> None:
        self.window = _FakeSaveWindow(destination)
        self.windows = [self.window]


def test_desktop_save_presentation_copy_uses_native_save_dialog(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    presentations = tmp_path / "presentations"
    presentations.mkdir()
    source = presentations / "source.pptx"
    source.write_bytes(b"pptx-data")
    target = tmp_path / "exports" / "Research deck"
    target.parent.mkdir()
    webview = _FakeSaveWindowModule(target)

    result = ScanSciDesktopApi(webview, workspace=workspace).save_presentation_copy(str(source), "Research deck.pptx")

    assert result["ok"] is True
    assert Path(result["path"]).name == "Research deck.pptx"
    assert Path(result["path"]).read_bytes() == b"pptx-data"
    assert webview.window.dialog_calls[0]["save_filename"] == "Research deck.pptx"


def test_desktop_local_runtime_file_picker_allows_manifest_and_parts(tmp_path: Path) -> None:
    archive = tmp_path / "local-runtime.zip"
    manifest = tmp_path / "local-transformers.json"
    archive.write_bytes(b"zip")
    manifest.write_text("{}", encoding="utf-8")
    webview = _FakeSaveWindowModule(archive)
    webview.window.create_file_dialog = lambda _dialog_type, **kwargs: (str(archive), str(manifest))

    selected = ScanSciDesktopApi(webview, workspace=tmp_path / "workspace.sqlite").choose_local_runtime_files()

    assert selected == [str(archive), str(manifest)]


def test_desktop_managed_component_picker_is_shared_by_node_and_tectonic(tmp_path: Path) -> None:
    archive = tmp_path / "node-runtime.zip"
    manifest = tmp_path / "node.json"
    archive.write_bytes(b"zip")
    manifest.write_text("{}", encoding="utf-8")
    webview = _FakeSaveWindowModule(archive)
    captured: dict[str, object] = {}

    def pick(_dialog_type, **kwargs):
        captured.update(kwargs)
        return str(archive), str(manifest)

    webview.window.create_file_dialog = pick

    selected = ScanSciDesktopApi(webview, workspace=tmp_path / "workspace.sqlite").choose_runtime_component_files("node")

    assert selected == [str(archive), str(manifest)]
    assert "Agent 运行组件" in str(captured["file_types"][0])


def test_desktop_local_artifact_bridge_opens_and_reveals_existing_file(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "paper.pdf"
    artifact.write_bytes(b"%PDF-1.7")
    opened: list[str] = []
    revealed: list[list[str]] = []
    monkeypatch.setattr(desktop.os, "name", "nt")
    monkeypatch.setattr(desktop.os, "startfile", lambda path: opened.append(path), raising=False)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args: revealed.append(list(args)))
    api = ScanSciDesktopApi(_FakeWindowModule())

    opened_result = api.open_local_path(str(artifact))
    revealed_result = api.reveal_local_path(str(artifact))

    assert opened_result == {"ok": True, "path": str(artifact.resolve()), "kind": "file"}
    assert revealed_result == {"ok": True, "path": str(artifact.resolve())}
    assert opened == [str(artifact.resolve())]
    assert revealed == [["explorer.exe", f"/select,{artifact.resolve()}"]]


def test_desktop_local_artifact_bridge_rejects_missing_path(tmp_path: Path) -> None:
    result = ScanSciDesktopApi(_FakeWindowModule()).open_local_path(str(tmp_path / "missing.pdf"))

    assert result["ok"] is False
    assert "missing.pdf" in result["message"]


def test_desktop_titlebar_bridge_controls_the_native_window() -> None:
    webview = _FakeWindowModule()
    api = ScanSciDesktopApi(webview)

    assert api.minimize_window() == {"ok": True}
    assert api.toggle_maximize_window() == {"ok": False, "maximized": False}
    assert api.close_window() == {"ok": True}
    assert webview.window.calls == ["minimize", "destroy"]


class _FakeResizableWindow(_FakeWindow):
    def __init__(self) -> None:
        super().__init__()
        self.x, self.y = 120, 96
        self.width, self.height = 1240, 820

    def resize(self, width: int, height: int) -> None:
        self.calls.append(f"resize:{width}x{height}")
        self.width, self.height = width, height

    def move(self, x: int, y: int) -> None:
        self.calls.append(f"move:{x},{y}")
        self.x, self.y = x, y


class _FakeWorkArea:
    X, Y, Width, Height = 0, 0, 1920, 1040


class _FakeScreen:
    x, y, width, height = 0, 0, 1920, 1080
    frame = _FakeWorkArea()


class _FakeWindowModuleWithWorkArea:
    def __init__(self) -> None:
        self.window = _FakeResizableWindow()
        self.windows = [self.window]
        self.screens = [_FakeScreen()]


def test_frameless_maximize_uses_monitor_work_area_and_restores_bounds() -> None:
    webview = _FakeWindowModuleWithWorkArea()
    api = ScanSciDesktopApi(webview)

    assert api.toggle_maximize_window() == {"ok": True, "maximized": True}
    assert api.toggle_maximize_window() == {"ok": True, "maximized": False}
    assert webview.window.calls == [
        "resize:1920x1040",
        "move:0,0",
        "resize:1240x820",
        "move:120,96",
    ]


class _ScaledFakeWorkArea:
    X, Y, Width, Height = 0, 0, 2880, 1560


class _ScaledFakeScreen:
    x, y, width, height = 0, 0, 2880, 1620
    frame = _ScaledFakeWorkArea()


class _ScaledNativeWindow:
    _scale = 1.5


class _ScaledFakeResizableWindow(_FakeResizableWindow):
    native = _ScaledNativeWindow()


class _ScaledFakeWindowModule:
    def __init__(self) -> None:
        self.window = _ScaledFakeResizableWindow()
        self.windows = [self.window]
        self.screens = [_ScaledFakeScreen()]


def test_frameless_maximize_converts_native_work_area_to_logical_pixels() -> None:
    webview = _ScaledFakeWindowModule()
    api = ScanSciDesktopApi(webview)

    assert api.toggle_maximize_window() == {"ok": True, "maximized": True}
    assert webview.window.calls == ["resize:1920x1040", "move:0,0"]


class _FullFrameWorkArea:
    X, Y, Width, Height = 0, 0, 1280, 800


class _FullFrameScreen:
    x, y, width, height = 0, 0, 1280, 800
    frame = _FullFrameWorkArea()


class _FullFrameWindowModule:
    def __init__(self) -> None:
        self.window = _FakeResizableWindow()
        self.windows = [self.window]
        self.screens = [_FullFrameScreen()]


def test_frameless_maximize_prefers_native_work_area_over_full_screen_frame() -> None:
    webview = _FullFrameWindowModule()
    api = ScanSciDesktopApi(webview)
    api._native_working_area_for_window = lambda _window, _scale: (0, 0, 1280, 752)  # type: ignore[method-assign]

    assert api.toggle_maximize_window() == {"ok": True, "maximized": True}
    assert webview.window.calls == ["resize:1280x752", "move:0,0"]


def test_desktop_diagnostics_verify_packaged_runtime_dependencies(tmp_path: Path) -> None:
    report = desktop_diagnostics(tmp_path / "workspace.sqlite", tmp_path / "evidence.sqlite")

    assert report["ok"] is True
    assert all(report["assets"].values())
    assert all(report["modules"].values())
    assert report["build"]["build_id"] == "source"
    assert report["pi_tool_loop"]["ok"] is True
    assert report["pi_tool_loop"]["tool_calls"] >= 1
    assert report["pi_tool_loop"]["done"] is True
    assert report["pi_tool_loop"]["fallback_count"] == 0
