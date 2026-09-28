"""Install, exercise, and uninstall a ScanSci Windows installer in isolation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any
from uuid import uuid4


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(
    command: list[str], *, timeout: float, environment: dict[str, str] | None = None
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=environment,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def _authenticode_status(path: Path) -> str:
    """Read Windows' embedded-signature status without echoing certificate data."""

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell is required to verify the installed Authenticode signature")
    environment = os.environ.copy()
    environment["SCANSCI_VERIFY_ARTIFACT"] = str(path)
    result = _run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$signature = Get-AuthenticodeSignature -LiteralPath $env:SCANSCI_VERIFY_ARTIFACT; [Console]::Write($signature.Status)",
        ],
        timeout=45,
        environment=environment,
    )
    if result["exit_code"] != 0:
        raise RuntimeError(f"Could not verify installed Authenticode signature: {result['stderr_tail']}")
    return str(result["stdout_tail"]).strip()


def _log_tail(path: Path, *, limit: int = 3000) -> str:
    """Return the useful end of an Inno log before its temp directory is removed."""

    if not path.is_file():
        return "<installer did not create a log>"
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError as error:
        return f"<could not read installer log: {type(error).__name__}: {error}>"


def _verification_install_dir(temporary_root: Path) -> Path:
    """Use a realistic, bounded user installation path for Windows installer checks.

    An onedir ScanSci release contains vendor paths close to Windows' legacy
    ``MAX_PATH`` boundary. Installing below the already-long system temporary
    directory turns an otherwise valid per-user installer into a false
    negative. The public default lives below ``LOCALAPPDATA``; mirror that
    location here while keeping each verification isolated.
    """

    local_app_data = Path(os.environ.get("LOCALAPPDATA", temporary_root))
    return local_app_data / "ScanSciVerify" / uuid4().hex[:10]


def _wait_for_uninstall(install_dir: Path, *, timeout: float = 45.0) -> bool:
    """Wait for Inno's deferred cleanup after its launcher has returned.

    Inno Setup can return from the uninstaller launcher before the worker has
    released the uninstaller executable and deleted its directory. A release
    gate must observe the final filesystem state instead of racing that
    documented cleanup phase.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not install_dir.exists():
            return True
        time.sleep(0.25)
    return not install_dir.exists()


def verify_installer(
    *,
    installer: Path,
    expected_build_id: str,
    expected_version: str,
    require_signature: bool = False,
) -> dict[str, Any]:
    if not installer.is_file():
        raise RuntimeError(f"Installer is missing: {installer}")
    installer_signature_status = _authenticode_status(installer) if require_signature else "not_required"
    if require_signature and installer_signature_status.casefold() != "valid":
        raise RuntimeError(
            "Installer is not Authenticode-valid and will not be executed; "
            f"status: {installer_signature_status}"
        )

    with tempfile.TemporaryDirectory(prefix="scansci-installer-verify-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        install_dir = _verification_install_dir(root)
        installer_log = root / "install.log"
        install = _run(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
                f"/DIR={install_dir}",
                f"/LOG={installer_log}",
            ],
            timeout=15 * 60,
        )
        if install["exit_code"] != 0:
            raise RuntimeError(
                f"Installer exited with {install['exit_code']}: {install['stderr_tail']}\n"
                f"Inno install log tail:\n{_log_tail(installer_log)}"
            )

        executable = install_dir / "ScanSci.exe"
        if not executable.is_file():
            raise RuntimeError(f"Installed ScanSci.exe is missing: {executable}")
        installed_signature_status = _authenticode_status(executable) if require_signature else "not_required"
        if require_signature and installed_signature_status.casefold() != "valid":
            raise RuntimeError(
                "Installed ScanSci.exe is not Authenticode-valid after installation; "
                f"status: {installed_signature_status}"
            )
        diagnostics = root / "installed-runtime.json"
        runtime = _run(
            [
                str(executable),
                "--workspace",
                str(root / "workspace.sqlite"),
                "--evidence-db",
                str(root / "evidence.sqlite"),
                "--diagnose",
                "--diagnostics-output",
                str(diagnostics),
            ],
            timeout=3 * 60,
        )
        if runtime["exit_code"] != 0 or not diagnostics.is_file():
            raise RuntimeError(
                f"Installed ScanSci diagnostics failed: {runtime['stderr_tail']}\n"
                f"Inno install log tail:\n{_log_tail(installer_log)}"
            )
        diagnostics_payload = json.loads(diagnostics.read_text(encoding="utf-8-sig"))
        if not bool(diagnostics_payload.get("ok")):
            raise RuntimeError("Installed ScanSci diagnostics reported ok=false")
        if str(diagnostics_payload.get("build", {}).get("build_id", "")) != expected_build_id:
            raise RuntimeError("Installed ScanSci reports a different build id")
        if str(diagnostics_payload.get("build", {}).get("version", "")) != expected_version:
            raise RuntimeError("Installed ScanSci reports a different release version")

        uninstaller = install_dir / "unins000.exe"
        if not uninstaller.is_file():
            raise RuntimeError("Installer did not provide an uninstaller")
        uninstall_log = root / "uninstall.log"
        uninstall = _run(
            [
                str(uninstaller),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/LOG={uninstall_log}",
            ],
            timeout=5 * 60,
        )
        if uninstall["exit_code"] != 0:
            raise RuntimeError(
                f"Uninstaller exited with {uninstall['exit_code']}: {uninstall['stderr_tail']}\n"
                f"Inno uninstall log tail:\n{_log_tail(uninstall_log)}"
            )
        if not _wait_for_uninstall(install_dir):
            remaining = [str(path.relative_to(install_dir)) for path in install_dir.rglob("*")][:20] if install_dir.exists() else []
            raise RuntimeError(f"Uninstall left files behind after deferred cleanup: {remaining}")

        try:
            install_dir.parent.rmdir()
        except OSError:
            # A concurrent validation or user file must never be removed.
            pass

        return {
            "ok": True,
            "installer": str(installer),
            "expected_build_id": expected_build_id,
            "expected_version": expected_version,
            "installer_authenticode_status": installer_signature_status,
            "installed_executable": str(executable),
            "installed_executable_authenticode_status": installed_signature_status,
            "runtime_build_id": diagnostics_payload.get("build", {}).get("build_id"),
            "install": install,
            "runtime": runtime,
            "uninstall": uninstall,
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--expected-build-id", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-signature", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    try:
        result = verify_installer(
            installer=Path(args.installer).resolve(),
            expected_build_id=args.expected_build_id,
            expected_version=args.expected_version,
            require_signature=bool(args.require_signature),
        )
    except Exception as error:
        _write_json(output, {"ok": False, "error": str(error)})
        raise
    _write_json(output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
