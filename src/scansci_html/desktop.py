"""Native Windows desktop shell for the local ScanSci Notebook workbench."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from threading import Thread
import time
from typing import Any
from urllib.request import urlopen

from .app_update import AppUpdateService
from .app_settings import load_settings
from .llm import warm_managed_gateway_connection
from .local_evidence_runtime import default_vector_cache_identity
from .pi_agent import PiAgentClient
from .webapp import create_notebook_server
from .build_info import current_build_info
from .telemetry import diagnostics_summary
from .vector_index import vector_cache_status


# Do not reuse the identifier of the retired Research Agent shell. Windows
# otherwise groups and may reactivate that executable when a ScanSci shortcut
# or an explicit package path is launched.
WINDOWS_APP_USER_MODEL_ID = "ScanSci.Pi.Desktop"

# Windows 11 applies rounded corners and a DWM border to borderless windows
# unless the app opts out.  ScanSci uses a custom title bar, so the native
# chrome must follow the same state as the HTML shell: rounded in a normal
# window, square and borderless when the window fills the monitor work area.
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWCP_DONOTROUND = 1
_DWMWCP_ROUND = 2
_DWMWA_COLOR_NONE = 0xFFFFFFFE


def _desktop_data_directory() -> Path | None:
    """Return the durable per-user location used by packaged Windows builds."""

    if not getattr(sys, "frozen", False):
        return None
    local_app_data = os.environ.get("LOCALAPPDATA")
    base_directory = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base_directory / "ScanSciPi"


def _default_desktop_paths() -> tuple[Path, Path]:
    """Choose launch-location-independent defaults for the packaged app."""

    data_directory = _desktop_data_directory()
    if data_directory is None:
        return Path("workspace.sqlite"), Path("html-papers") / "evidence.sqlite"
    return data_directory / "workspace.sqlite", data_directory / "evidence.sqlite"


def _signal_update_startup_ready(server: Any | None = None) -> bool:
    """Confirm a replacement only after its exact build and loopback API are healthy."""

    raw_target = os.environ.get("SCANSCI_UPDATE_READY_FILE", "").strip()
    token = os.environ.get("SCANSCI_UPDATE_READY_TOKEN", "").strip()
    expected_version = os.environ.get("SCANSCI_UPDATE_EXPECTED_VERSION", "").strip()
    if not raw_target and not token and not expected_version:
        return False
    if server is None or not re.fullmatch(r"[0-9a-f]{32}", token):
        return False
    build_version = str(current_build_info().get("version", "")).strip()
    if not expected_version or build_version != expected_version:
        return False

    local_app_data = os.environ.get("LOCALAPPDATA")
    local_root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    updates_root = (local_root / "ScanSciPi" / "updates").resolve()
    target = Path(raw_target).expanduser().resolve()
    try:
        target.relative_to(updates_root)
    except ValueError:
        return False

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/health", timeout=2) as response:
                health = json.loads(response.read(1_000_001).decode("utf-8"))
            if health.get("status") == "ok" and str(health.get("version", "")) == expected_version:
                break
        except (OSError, ValueError, json.JSONDecodeError):
            time.sleep(0.1)
    else:
        return False

    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "token": token,
            "version": expected_version,
            "ready_at": int(time.time()),
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def _warm_active_managed_model(workspace: Path) -> None:
    """Prepare the public managed gateway while the desktop shell is loading."""

    settings = load_settings(workspace)
    active = dict(settings.get("active_model", {}) or {})
    if active.get("provider_id") != "scansci-managed":
        return
    provider = next((item for item in settings.get("providers", []) if item.get("id") == "scansci-managed"), None)
    if not provider:
        return
    warm_managed_gateway_connection(str(provider.get("base_url", "")))


def _windows_app_user_model_id() -> str:
    """Keep release builds grouped while making named developer builds distinct."""

    if not getattr(sys, "frozen", False):
        return WINDOWS_APP_USER_MODEL_ID
    executable_stem = Path(sys.executable).stem
    if executable_stem.casefold() == "scansci":
        return WINDOWS_APP_USER_MODEL_ID
    safe_stem = "".join(character for character in executable_stem if character.isalnum())
    return f"{WINDOWS_APP_USER_MODEL_ID}.{safe_stem or 'Preview'}"


def _set_resize_frame_style(
    hwnd: int,
    *,
    enabled: bool,
    user32: Any | None = None,
) -> bool:
    """Toggle the native resize frame without restoring a visible title bar."""

    if user32 is None:
        if os.name != "nt":
            return False
        try:
            import ctypes

            user32 = ctypes.windll.user32
        except (AttributeError, ImportError, OSError):
            return False

    get_window_long = getattr(user32, "GetWindowLongPtrW", None) or getattr(
        user32, "GetWindowLongW", None
    )
    set_window_long = getattr(user32, "SetWindowLongPtrW", None) or getattr(
        user32, "SetWindowLongW", None
    )
    set_window_pos = getattr(user32, "SetWindowPos", None)
    if not all((get_window_long, set_window_long, set_window_pos)):
        return False

    # WS_THICKFRAME makes DefWindowProc report HTLEFT/HTRIGHT/HTTOP and corner
    # hit-test results even though pywebview removed the visible title bar.
    gwl_style = -16
    ws_thickframe = 0x00040000
    swp_nosize = 0x0001
    swp_nomove = 0x0002
    swp_nozorder = 0x0004
    swp_noactivate = 0x0010
    swp_framechanged = 0x0020
    try:
        style = int(get_window_long(int(hwnd), gwl_style))
        next_style = style | ws_thickframe if enabled else style & ~ws_thickframe
        if next_style == style:
            return True
        set_window_long(int(hwnd), gwl_style, next_style)
        set_window_pos(
            int(hwnd),
            0,
            0,
            0,
            0,
            0,
            swp_nosize | swp_nomove | swp_nozorder | swp_noactivate | swp_framechanged,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def _apply_borderless_resize_style(hwnd: int, user32: Any | None = None) -> bool:
    """Give a frameless Windows form native edge and corner resize hit areas."""

    return _set_resize_frame_style(hwnd, enabled=True, user32=user32)


def _set_native_window_bounds(
    hwnd: int,
    bounds: tuple[int, int, int, int],
    user32: Any | None = None,
) -> bool:
    """Place a native window on exact physical monitor-work-area bounds."""

    if user32 is None:
        if os.name != "nt":
            return False
        try:
            import ctypes

            user32 = ctypes.windll.user32
        except (AttributeError, ImportError, OSError):
            return False
    set_window_pos = getattr(user32, "SetWindowPos", None)
    if not callable(set_window_pos):
        return False
    try:
        x, y, width, height = (int(value) for value in bounds)
        # SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        flags = 0x0004 | 0x0010 | 0x0020
        return bool(set_window_pos(int(hwnd), 0, x, y, width, height, flags))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _set_windows_window_chrome(
    hwnd: int,
    *,
    maximized: bool,
    dwmapi: Any | None = None,
) -> bool:
    """Synchronize native corners with ScanSci's custom window state."""

    if os.name != "nt":
        return False
    if dwmapi is None:
        try:
            import ctypes

            dwmapi = ctypes.windll.dwmapi
        except (AttributeError, ImportError, OSError):
            return False

    set_window_attribute = getattr(dwmapi, "DwmSetWindowAttribute", None)
    if not callable(set_window_attribute):
        return False

    try:
        import ctypes

        corner_preference = ctypes.c_uint32(
            _DWMWCP_DONOTROUND if maximized else _DWMWCP_ROUND
        )
        border_color = ctypes.c_uint32(_DWMWA_COLOR_NONE)
        corner_result = int(
            set_window_attribute(
                int(hwnd),
                _DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner_preference),
                ctypes.sizeof(corner_preference),
            )
        )
        border_result = int(
            set_window_attribute(
                int(hwnd),
                _DWMWA_BORDER_COLOR,
                ctypes.byref(border_color),
                ctypes.sizeof(border_color),
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return corner_result == 0 and border_result == 0


def _native_window_handle(window: Any) -> int | None:
    """Return a WinForms/pywebview HWND when the backend exposes one."""

    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is None:
        return None
    for method_name in ("ToInt64", "ToInt32"):
        converter = getattr(handle, method_name, None)
        if callable(converter):
            try:
                hwnd = int(converter())
            except (OSError, TypeError, ValueError):
                continue
            if hwnd:
                return hwnd
    try:
        hwnd = int(handle)
    except (TypeError, ValueError):
        return None
    return hwnd or None


def _physical_working_area_for_window(window: Any) -> tuple[int, int, int, int] | None:
    """Return the native physical work area used to remove outer frame gaps."""

    if os.name != "nt":
        return None
    native = getattr(window, "native", None)
    if native is None:
        return None
    try:
        from System.Windows.Forms import Screen  # type: ignore[import-not-found]

        rectangle = Screen.FromHandle(native.Handle).WorkingArea
        return tuple(
            int(getattr(rectangle, field))
            for field in ("X", "Y", "Width", "Height")
        )
    except Exception:
        return None


def _enable_borderless_window_resize(window: Any) -> bool:
    """Install native resizing after pywebview has created the Windows form."""

    if os.name != "nt" or window is None:
        return False
    events = getattr(window, "events", None)
    shown = getattr(events, "shown", None)
    if shown is not None and not shown.wait(15):
        return False
    hwnd = _native_window_handle(window)
    if hwnd is None:
        return False
    _apply_borderless_resize_style(hwnd)
    _set_windows_window_chrome(hwnd, maximized=False)
    return True


def _confirm_desktop_started(window: Any, server: Any) -> bool:
    """Signal updater readiness only after pywebview has initialized its GUI loop."""

    resized = _enable_borderless_window_resize(window)
    _signal_update_startup_ready(server)
    return resized


class ScanSciDesktopApi:
    """Small native-dialog bridge exposed only inside the desktop WebView."""

    def __init__(
        self,
        webview_module: Any,
        *,
        workspace: str | Path | None = None,
        update_service: AppUpdateService | None = None,
        relaunch_args: list[str] | None = None,
    ) -> None:
        self.webview = webview_module
        self.workspace = Path(workspace).resolve() if workspace is not None else None
        self.update_service = update_service or AppUpdateService()
        self.relaunch_args = list(relaunch_args or [])
        self._maximized = False
        self._restore_bounds: tuple[int, int, int, int] | None = None

    def _window(self) -> Any | None:
        windows = list(getattr(self.webview, "windows", []) or [])
        return windows[0] if windows else None

    def minimize_window(self) -> dict[str, bool]:
        window = self._window()
        if window is None:
            return {"ok": False}
        window.minimize()
        return {"ok": True}

    @staticmethod
    def _window_bounds(window: Any) -> tuple[int, int, int, int] | None:
        """Return the current public pywebview bounds, if the backend exposes them."""

        try:
            return (int(window.x), int(window.y), int(window.width), int(window.height))
        except (AttributeError, TypeError, ValueError):
            return None

    def _working_area_for_window(self, window: Any) -> tuple[int, int, int, int] | None:
        """Return the monitor work area so a frameless window does not cover the taskbar."""

        bounds = self._window_bounds(window)
        if bounds is None:
            return None
        x, y, width, height = bounds
        center_x, center_y = x + width // 2, y + height // 2
        scale = self._window_scale(window)
        # pywebview exposes the public window bounds in logical pixels, while
        # WinForms exposes Screen.Bounds and Screen.WorkingArea in physical
        # pixels. Select the monitor in its native coordinate space first.
        physical_center_x = int(center_x * scale)
        physical_center_y = int(center_y * scale)
        try:
            screens = list(getattr(self.webview, "screens", []) or [])
        except Exception:
            screens = []
        selected = next(
            (
                screen
                for screen in screens
                if int(screen.x) <= physical_center_x < int(screen.x) + int(screen.width)
                and int(screen.y) <= physical_center_y < int(screen.y) + int(screen.height)
            ),
            next(
                (
                    screen
                    for screen in screens
                    if int(screen.x) <= center_x < int(screen.x) + int(screen.width)
                    and int(screen.y) <= center_y < int(screen.y) + int(screen.height)
                ),
                screens[0] if screens else None,
            ),
        )
        native_work_area = self._native_working_area_for_window(window, scale)
        if native_work_area is not None:
            return native_work_area
        frame = getattr(selected, "frame", None)
        return self._logical_rectangle(frame, scale)

    @staticmethod
    def _window_scale(window: Any) -> float:
        """Return the native logical-to-physical scale for the active window."""

        native = getattr(window, "native", None)
        try:
            scale = float(getattr(native, "_scale"))
        except (AttributeError, TypeError, ValueError):
            return 1.0
        return scale if scale > 0 else 1.0

    @staticmethod
    def _logical_rectangle(rectangle: Any, scale: float) -> tuple[int, int, int, int] | None:
        """Convert a native WinForms rectangle to pywebview logical pixels."""

        if rectangle is None:
            return None
        try:
            return tuple(
                int(round(value / scale))
                for value in (
                    float(rectangle.X),
                    float(rectangle.Y),
                    float(rectangle.Width),
                    float(rectangle.Height),
                )
            )
        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
            return None

    def _native_working_area_for_window(self, window: Any, scale: float) -> tuple[int, int, int, int] | None:
        """Use the native monitor as a Windows fallback when pywebview has no screens."""

        native = getattr(window, "native", None)
        if native is None:
            return None
        try:
            from System.Windows.Forms import Screen  # type: ignore[import-not-found]

            rectangle = Screen.FromHandle(native.Handle).WorkingArea
        except Exception:
            return None
        return self._logical_rectangle(rectangle, scale)

    def toggle_maximize_window(self) -> dict[str, bool]:
        window = self._window()
        if window is None:
            return {"ok": False, "maximized": False}
        if not self._maximized:
            self._restore_bounds = self._window_bounds(window)
            work_area = self._working_area_for_window(window)
            if work_area is None:
                # Frameless native maximize can cover the taskbar. Do not turn
                # an unavailable work area into a fullscreen-sized window.
                return {"ok": False, "maximized": False}
            work_x, work_y, work_width, work_height = work_area
            window.resize(work_width, work_height)
            window.move(work_x, work_y)
            self._maximized = True
        else:
            if self._restore_bounds is None:
                window.restore()
            else:
                restore_x, restore_y, restore_width, restore_height = self._restore_bounds
                window.resize(restore_width, restore_height)
                window.move(restore_x, restore_y)
            self._maximized = False
        hwnd = _native_window_handle(window)
        if hwnd is not None:
            _set_resize_frame_style(hwnd, enabled=not self._maximized)
            _set_windows_window_chrome(hwnd, maximized=self._maximized)
            if self._maximized:
                native_work_area = _physical_working_area_for_window(window)
                if native_work_area is not None:
                    _set_native_window_bounds(hwnd, native_work_area)
        return {"ok": True, "maximized": self._maximized}

    def close_window(self) -> dict[str, bool]:
        window = self._window()
        if window is None:
            return {"ok": False}
        window.destroy()
        return {"ok": True}

    def choose_library_folder(self) -> str:
        windows = list(getattr(self.webview, "windows", []) or [])
        if not windows:
            return ""
        result = windows[0].create_file_dialog(getattr(self.webview, "FOLDER_DIALOG", 3))
        return str(result[0]) if result else ""

    def choose_library_files(self) -> list[str]:
        windows = list(getattr(self.webview, "windows", []) or [])
        if not windows:
            return []
        result = windows[0].create_file_dialog(
            getattr(self.webview, "OPEN_DIALOG", 0),
            allow_multiple=True,
            file_types=(
                "研究资料 (*.pdf;*.docx;*.pptx;*.xlsx;*.xls;*.csv;*.json;*.xml;*.html;*.htm;*.md;*.markdown;*.txt;*.rtf;*.epub;*.zip;*.png;*.jpg;*.jpeg;*.webp;*.tif;*.tiff)",
                "所有文件 (*.*)",
            ),
        )
        return [str(path) for path in list(result or [])]

    def choose_local_runtime_files(self) -> list[str]:
        """Pick a runtime ZIP, manifest, or multipart runtime assets."""

        windows = list(getattr(self.webview, "windows", []) or [])
        if not windows:
            return []
        result = windows[0].create_file_dialog(
            getattr(self.webview, "OPEN_DIALOG", 0),
            allow_multiple=True,
            file_types=(
                "ScanSci 本地运行组件 (*.zip;*.json;*.part*)",
                "所有文件 (*.*)",
            ),
        )
        return [str(path) for path in list(result or [])]

    def choose_runtime_component_files(self, component_id: str = "") -> list[str]:
        """Pick a managed Node/Tectonic component without exposing paths to web code."""

        windows = list(getattr(self.webview, "windows", []) or [])
        if not windows:
            return []
        identifier = str(component_id or "").strip().lower()
        label = "Agent 运行组件" if identifier == "node" else "LaTeX 排版组件" if identifier == "tectonic" else "ScanSci 运行组件"
        result = windows[0].create_file_dialog(
            getattr(self.webview, "OPEN_DIALOG", 0),
            allow_multiple=True,
            file_types=(
                f"{label} (*.zip;*.json;*.part*)",
                "所有文件 (*.*)",
            ),
        )
        return [str(path) for path in list(result or [])]

    @staticmethod
    def _existing_local_path(raw_path: str) -> Path:
        """Resolve a path supplied by a trusted local artifact record."""

        path = Path(str(raw_path or "")).expanduser()
        if not str(path).strip():
            raise FileNotFoundError("缺少本地文件路径")
        resolved = path.resolve(strict=True)
        if resolved == Path(resolved.anchor):
            raise ValueError("不能直接打开磁盘根目录")
        return resolved

    def open_local_path(self, raw_path: str) -> dict[str, Any]:
        """Open a generated file or folder with the Windows default app."""

        try:
            target = self._existing_local_path(raw_path)
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return {"ok": True, "path": str(target), "kind": "folder" if target.is_dir() else "file"}
        except (FileNotFoundError, OSError, ValueError) as error:
            return {"ok": False, "message": str(error)}

    def reveal_local_path(self, raw_path: str) -> dict[str, Any]:
        """Reveal a generated file in Explorer, or open the folder itself."""

        try:
            target = self._existing_local_path(raw_path)
            if os.name == "nt":
                if target.is_dir():
                    os.startfile(str(target))  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["explorer.exe", f"/select,{target}"])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
            return {"ok": True, "path": str(target)}
        except (FileNotFoundError, OSError, ValueError) as error:
            return {"ok": False, "message": str(error)}

    def choose_presentation_sources(self) -> list[str]:
        """Pick source material for a standalone document-to-slides task."""

        windows = list(getattr(self.webview, "windows", []) or [])
        if not windows:
            return []
        result = windows[0].create_file_dialog(
            getattr(self.webview, "OPEN_DIALOG", 0),
            allow_multiple=True,
            file_types=(
                "演示材料 (*.pdf;*.docx;*.pptx;*.xlsx;*.xls;*.csv;*.json;*.xml;*.html;*.htm;*.md;*.markdown;*.txt;*.rtf;*.epub;*.png;*.jpg;*.jpeg;*.webp;*.tif;*.tiff)",
                "所有文件 (*.*)",
            ),
        )
        return [str(path) for path in list(result or [])]

    def save_presentation_copy(self, source_path: str, suggested_name: str = "") -> dict[str, Any]:
        """Save a generated PPTX through a native Windows dialog.

        Embedded WebView downloads are inconsistent across backends.  Copying a
        previously validated local deck through the native save dialog is both
        predictable for users and avoids exposing a browser download prompt.
        """

        window = self._window()
        source = Path(str(source_path or "")).resolve()
        if window is None or not source.is_file() or source.suffix.casefold() != ".pptx":
            return {"ok": False, "cancelled": False, "message": "没有可保存的 PPTX 文件"}
        if self.workspace is not None:
            presentations = (self.workspace.parent / "presentations").resolve()
            if source.parent != presentations:
                return {"ok": False, "cancelled": False, "message": "PPTX 文件不在当前工作区"}
        safe_name = Path(str(suggested_name or source.name)).name
        if not safe_name.casefold().endswith(".pptx"):
            safe_name = f"{safe_name}.pptx"
        try:
            selected = window.create_file_dialog(
                getattr(self.webview, "SAVE_DIALOG", 1),
                save_filename=safe_name,
                file_types=("PowerPoint 演示文稿 (*.pptx)",),
            )
        except TypeError:
            selected = window.create_file_dialog(getattr(self.webview, "SAVE_DIALOG", 1), file_types=("PowerPoint 演示文稿 (*.pptx)",))
        if not selected:
            return {"ok": False, "cancelled": True}
        target = Path(selected[0] if isinstance(selected, (list, tuple)) else selected)
        if target.suffix.casefold() != ".pptx":
            target = target.with_suffix(".pptx")
        if target.resolve() != source:
            shutil.copyfile(source, target)
        return {"ok": True, "cancelled": False, "path": str(target)}

    def install_update(self) -> dict[str, Any]:
        result = self.update_service.install(relaunch_args=self.relaunch_args)
        Thread(target=self._close_for_update, name="ScanSci-update-restart", daemon=True).start()
        return result

    def _close_for_update(self) -> None:
        time.sleep(0.8)
        windows = list(getattr(self.webview, "windows", []) or [])
        if windows:
            windows[0].destroy()


def launch_desktop(
    *,
    workspace: str | Path,
    evidence_db: str | Path,
    title: str = "搜索科学 Pi",
    update_manifest_url: str | None = None,
    webview_module: Any | None = None,
) -> None:
    """Open the local evidence workbench in a native WebView window.

    The server is deliberately bound to an operating-system selected loopback
    port.  It starts before the window and is stopped immediately after the
    user closes the window.
    """

    if webview_module is None:
        _set_windows_app_user_model_id()
    webview = webview_module or _load_webview()
    workspace_path = Path(workspace).resolve()
    evidence_db_path = Path(evidence_db).resolve()
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_db_path.parent.mkdir(parents=True, exist_ok=True)
    updates = AppUpdateService(manifest_url=update_manifest_url)
    relaunch_args = [
        "--workspace",
        str(workspace_path),
        "--evidence-db",
        str(evidence_db_path),
    ]
    if title != "搜索科学 Pi":
        relaunch_args.extend(["--title", title])
    if update_manifest_url:
        relaunch_args.extend(["--update-manifest-url", update_manifest_url])
    server = create_notebook_server(
        workspace=workspace_path,
        evidence_db=evidence_db_path,
        host="127.0.0.1",
        port=0,
        update_service=updates,
    )
    server_thread = Thread(
        target=server.serve_forever,
        name="ScanSci-local-notebook",
        daemon=True,
    )
    server_thread.start()
    if webview_module is None:
        Thread(
            target=_warm_active_managed_model,
            args=(workspace_path,),
            name="ScanSci-managed-gateway-warmup",
            daemon=True,
        ).start()
    try:
        native_window = webview.create_window(
            title,
            f"http://127.0.0.1:{server.server_port}",
            width=1440,
            height=960,
            # Keep the desktop usable on 14-inch displays and at higher OS
            # scaling factors; the web layout collapses the sidebar when the
            # initial viewport is narrow.
            min_size=(800, 560),
            resizable=True,
            frameless=True,
            easy_drag=False,
            # Keep the native surface identical to --canvas so no 1 px
            # Windows/DWM strip becomes visible at the custom frame edges.
            background_color="#f4f4f6",
            js_api=ScanSciDesktopApi(webview, workspace=workspace, update_service=updates, relaunch_args=relaunch_args),
        )
        if webview_module is None:
            webview.start(_confirm_desktop_started, args=(native_window, server))
        else:
            _signal_update_startup_ready(server)
            webview.start()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    """Run the standalone ``scansci-desktop`` entry point."""

    parser = argparse.ArgumentParser(description="Open the local ScanSci evidence workbench in a desktop window.")
    default_workspace, default_evidence_db = _default_desktop_paths()
    parser.add_argument("--workspace", default=str(default_workspace), help="Path to the Notebook workspace SQLite store.")
    parser.add_argument("--evidence-db", default=str(default_evidence_db), help="Path to the SQLite evidence store.")
    parser.add_argument("--title", default="ScanSci | 搜索科学", help="Window title.")
    parser.add_argument(
        "--update-manifest-url",
        default=None,
        help="HTTPS URL for the ScanSci desktop stable release manifest.",
    )
    parser.add_argument("--diagnose", action="store_true", help="Run packaged-runtime diagnostics and exit.")
    parser.add_argument("--diagnostics-output", default="", help="Optional JSON file for --diagnose results.")
    parser.add_argument("--serve-only", action="store_true", help="Run the local HTTP app without opening a desktop window.")
    parser.add_argument("--port", type=int, default=0, help="Loopback port for --serve-only. Zero selects an available port.")
    args = parser.parse_args(argv)
    if args.diagnose:
        report = desktop_diagnostics(args.workspace, args.evidence_db)
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if args.diagnostics_output:
            output = Path(args.diagnostics_output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded + "\n", encoding="utf-8")
        else:
            print(encoded)
        return 0 if report["ok"] else 1
    if args.serve_only:
        server = create_notebook_server(
            workspace=Path(args.workspace).resolve(),
            evidence_db=Path(args.evidence_db).resolve(),
            host="127.0.0.1",
            port=max(0, int(args.port)),
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return 0
    launch_desktop(
        workspace=args.workspace,
        evidence_db=args.evidence_db,
        title=args.title,
        update_manifest_url=args.update_manifest_url,
    )
    return 0


def desktop_diagnostics(workspace: str | Path, evidence_db: str | Path) -> dict[str, Any]:
    """Verify resources and dynamic dependencies needed by the packaged app."""

    required_assets = [
        "index.html",
        "styles.css",
        "app.js",
        "pdf-viewer.js",
        "pptx-export.js",
        "vendor/pdfjs/pdf.mjs",
        "vendor/pdfjs/pdf.worker.mjs",
        "vendor/pptxgen/pptxgen.bundle.js",
    ]
    asset_root = Path(__file__).with_name("web")
    assets = {relative: (asset_root / relative).is_file() for relative in required_assets}
    modules: dict[str, bool] = {}
    module_errors: dict[str, str] = {}
    for module_name in ["litellm", "markitdown", "opentelemetry.sdk", "pyzotero", "sqlite_vec", "webview"]:
        try:
            __import__(module_name)
        except Exception as error:
            modules[module_name] = False
            module_errors[module_name] = f"{type(error).__name__}: {error}"[:500]
        else:
            modules[module_name] = True
    workspace_path = Path(workspace).resolve()
    evidence_path = Path(evidence_db).resolve()
    try:
        pi_runtime = PiAgentClient.runtime_status()
    except Exception as error:
        pi_runtime = {"ready": False, "error": f"{type(error).__name__}: {error}"[:500]}
    try:
        pi_tool_loop = PiAgentClient.diagnostic_tool_loop(
            workspace=workspace_path,
            evidence_db=evidence_path,
            timeout_seconds=30.0,
        )
    except Exception as error:
        pi_tool_loop = {
            "ok": False,
            "tool_calls": 0,
            "done": False,
            "fallback_count": 0,
            "error": f"{type(error).__name__}: {error}"[:500],
        }
    vector_identity = default_vector_cache_identity()
    report = {
        "ok": (
            all(assets.values())
            and all(modules.values())
            and bool(pi_runtime.get("ready"))
            and bool(pi_tool_loop.get("ok"))
        ),
        "build": current_build_info(),
        "assets": assets,
        "modules": modules,
        "module_errors": module_errors,
        "pi_runtime": pi_runtime,
        "pi_tool_loop": pi_tool_loop,
        "telemetry": diagnostics_summary(workspace_path),
        "vector_cache": vector_cache_status(
            evidence_path,
            provider=str(vector_identity["provider"]),
            dimensions=int(vector_identity["dimensions"]),
        ),
    }
    return report


def _load_webview() -> Any:
    try:
        import webview
    except ImportError as error:  # pragma: no cover - depends on optional desktop extra
        raise RuntimeError(
            "Desktop support is not installed. Run `python -m pip install -e \".[desktop]\"` first."
        ) from error
    return webview


def _set_windows_app_user_model_id(app_id: str | None = None) -> bool:
    """Give Windows a stable identity for taskbar grouping and icon selection."""

    if sys.platform != "win32":
        return False
    resolved_app_id = app_id or _windows_app_user_model_id()
    try:
        import ctypes

        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(resolved_app_id)
    except (AttributeError, OSError):
        return False
    return result == 0


if __name__ == "__main__":  # pragma: no cover - exercised through the installed entry point
    raise SystemExit(main())
