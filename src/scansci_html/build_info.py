"""Runtime identity for distinguishing source, preview, and packaged builds."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from .app_update import APP_VERSION


_PACKAGED_BUILD_INFO_KEYS = {
    "version",
    "build_id",
    "built_at",
    "commit",
    "package_profile",
    "exclude_runtimes",
    "update_manifest_url",
    "runtime_manifest_url",
    "node_component_manifest_url",
    "tectonic_component_manifest_url",
    "release_public_key",
    "cache_key",
    "source_tree_sha256",
    "release_source_sha256",
}


def current_build_info() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    source_root = package_root.parent
    payload: dict[str, Any] = {
        "version": APP_VERSION,
        "build_id": "source",
        "built_at": "",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": Path(sys.executable).name,
        # The loopback health endpoint exposes this on purpose: it lets a
        # preview, a diagnostic bundle, and the release gate prove which copy
        # of ScanSci is actually serving the UI.  A version string alone does
        # not distinguish two editable checkouts of the same version.
        "package_root": str(package_root),
        "runtime_kind": "packaged" if getattr(sys, "frozen", False) else "source",
        "source_root": str(source_root) if not getattr(sys, "frozen", False) else "",
    }
    candidates = [
        Path(__file__).with_name("build-info.json"),
        Path(getattr(sys, "_MEIPASS", "")) / "scansci_html" / "build-info.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            stored = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(stored, dict):
            payload.update(
                {
                    str(key): value
                    for key, value in stored.items()
                    if key in _PACKAGED_BUILD_INFO_KEYS
                }
            )
            break
    return payload
