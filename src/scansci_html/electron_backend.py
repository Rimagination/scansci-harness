"""Loopback service owned by the Electron desktop process."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from threading import Thread

from .webapp import create_notebook_server


READY_PREFIX = "SCANSCI_BACKEND_READY "


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ScanSci Electron backend")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence-db", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    evidence_db = args.evidence_db.resolve()
    workspace.parent.mkdir(parents=True, exist_ok=True)
    evidence_db.parent.mkdir(parents=True, exist_ok=True)
    server = create_notebook_server(
        workspace=workspace, evidence_db=evidence_db,
        host="127.0.0.1", port=0, allowed_origin="scansci://app",
    )

    def stop_when_parent_closes() -> None:
        try:
            sys.stdin.buffer.read()
        finally:
            server.shutdown()

    Thread(target=stop_when_parent_closes, name="ScanSci-electron-parent", daemon=True).start()
    print(f"{READY_PREFIX}{json.dumps({'port': server.server_port})}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
