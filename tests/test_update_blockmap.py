from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.request import urlopen

import pytest

from scansci_html.update_blockmap import (
    DifferentialDownloadUnavailable,
    build_blockmap,
    build_blockmap_from_bytes,
    download_differential,
    load_blockmap,
    plan_differential_operations,
)


def test_build_blockmap_records_fixed_sha256_blocks(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"abcdefghi")

    blockmap = build_blockmap(source, block_size=4)

    assert blockmap["schema_version"] == 1
    assert blockmap["algorithm"] == "sha256"
    assert blockmap["block_size"] == 4
    assert blockmap["size"] == 9
    assert len(blockmap["blocks"]) == 3
    assert blockmap["sha256"]


def test_load_blockmap_accepts_windows_powershell_utf8_bom(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"abcdefghi")
    blockmap_path = tmp_path / "payload.blockmap"
    blockmap_path.write_text(json.dumps(build_blockmap(source, block_size=4)), encoding="utf-8-sig")

    loaded = load_blockmap(blockmap_path)

    assert loaded["size"] == 9


def test_plan_copies_unchanged_blocks_and_downloads_changed_ranges(tmp_path: Path) -> None:
    old = tmp_path / "old.bin"
    new = tmp_path / "new.bin"
    old.write_bytes(b"abcdefghij")
    new.write_bytes(b"abcd1234ij")

    old_map = build_blockmap(old, block_size=4)
    new_map = build_blockmap(new, block_size=4)

    operations = plan_differential_operations(old, old_map, new_map)

    assert operations == [
        {"kind": "copy", "start": 0, "end": 4},
        {"kind": "download", "start": 4, "end": 8},
        {"kind": "copy", "start": 8, "end": 10},
    ]


class _RangeHandler(BaseHTTPRequestHandler):
    payload = b""
    ranges: list[str] = []
    support_ranges = True

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        range_header = self.headers.get("Range", "")
        if range_header:
            type(self).ranges.append(range_header)
        if range_header and type(self).support_ranges:
            start, end = range_header.removeprefix("bytes=").split("-")
            start_index = int(start)
            end_index = int(end)
            body = type(self).payload[start_index : end_index + 1]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Range", f"bytes {start_index}-{end_index}/{len(type(self).payload)}")
        else:
            body = type(self).payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture()
def range_server() -> tuple[str, type[_RangeHandler], ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/payload.bin", _RangeHandler, server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_download_differential_requests_only_changed_range(
    tmp_path: Path,
    range_server: tuple[str, type[_RangeHandler], ThreadingHTTPServer],
) -> None:
    url, handler, _server = range_server
    old = tmp_path / "old.bin"
    destination = tmp_path / "new.bin"
    old.write_bytes(b"abcdefghij")
    handler.payload = b"abcd1234ij"
    handler.ranges = []
    old_map = build_blockmap(old, block_size=4)
    new_map = build_blockmap_from_bytes(handler.payload, block_size=4)

    result = download_differential(old, old_map, url, new_map, destination, opener=urlopen)

    assert result["used_differential"] is True
    assert result["bytes_downloaded"] == 4
    assert handler.ranges == ["bytes=4-7"]
    assert destination.read_bytes() == handler.payload


def test_download_differential_rejects_server_without_range_support(
    tmp_path: Path,
    range_server: tuple[str, type[_RangeHandler], ThreadingHTTPServer],
) -> None:
    url, handler, _server = range_server
    old = tmp_path / "old.bin"
    destination = tmp_path / "new.bin"
    old.write_bytes(b"abcdefghij")
    handler.payload = b"abcd1234ij"
    handler.ranges = []
    handler.support_ranges = False
    try:
        with pytest.raises(DifferentialDownloadUnavailable):
            download_differential(
                old,
                build_blockmap(old, block_size=4),
                url,
                build_blockmap_from_bytes(handler.payload, block_size=4),
                destination,
                opener=urlopen,
            )
    finally:
        handler.support_ranges = True

    assert not destination.exists()
