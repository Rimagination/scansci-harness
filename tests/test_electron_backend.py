"""The Electron-owned Python service must start and stop with its parent pipe."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scansci_html.electron_backend import READY_PREFIX


def test_electron_backend_serves_loopback_and_stops_with_parent(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [
            sys.executable,
            str(root / "scripts" / "scansci_electron_backend_entry.py"),
            "--workspace", str(tmp_path / "workspace.sqlite"),
            "--evidence-db", str(tmp_path / "evidence.sqlite"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=root,
    )
    try:
        assert process.stdout is not None
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            line = executor.submit(process.stdout.readline).result(timeout=60)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        assert line.startswith(READY_PREFIX), line
        port = json.loads(line[len(READY_PREFIX):])["port"]
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:
            assert response.status == 200
        probe = Request(
            f"http://127.0.0.1:{port}/api/electron-origin-probe",
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Origin": "scansci://app"},
        )
        try:
            with urlopen(probe, timeout=15) as response:
                status = response.status
        except HTTPError as error:
            status = error.code
        assert status != 403
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            assert process.wait(timeout=15) == 0
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise
