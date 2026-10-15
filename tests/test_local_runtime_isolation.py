from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def _required_module(name: str):
    spec = importlib.util.find_spec(name)
    assert spec is not None, f"missing runtime isolation module: {name}"
    return importlib.import_module(name)


def _model_snapshot(tmp_path: Path, model_id: str = "Qwen/Qwen3.5-2B") -> dict[str, object]:
    snapshot = tmp_path / "models--Qwen--Qwen3.5-2B" / "snapshots" / "revision-a"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5", "architectures": ["Qwen3_5ForConditionalGeneration"]}),
        encoding="utf-8",
    )
    (snapshot / "model-00001-of-00001.safetensors").write_bytes(b"already-downloaded-model")
    return {
        "id": model_id,
        "path": str(snapshot),
        "kind": "vision",
        "ready": True,
        "format": "huggingface",
    }


def test_model_worker_native_exit_is_normalized_to_structured_failure() -> None:
    daemon = _required_module("scansci_html.local_runtime_daemon")

    failure = daemon.classify_worker_exit(
        model_id="Qwen/Qwen3.5-2B",
        phase="load_or_generate",
        returncode=-1073741819,
    )

    assert failure.code == "model_worker_crashed"
    assert failure.exit_code == 0xC0000005
    assert failure.native_crash is True
    assert failure.phase == "load_or_generate"
    assert failure.as_error()["error"]["model"] == "Qwen/Qwen3.5-2B"


def test_worker_crash_returns_structured_error_while_daemon_stays_healthy(tmp_path: Path) -> None:
    daemon = _required_module("scansci_html.local_runtime_daemon")
    record = _model_snapshot(tmp_path)
    worker_script = tmp_path / "crashing_worker.py"
    worker_script.write_text("import os\nos._exit(23)\n", encoding="utf-8")
    supervisor = daemon.ModelWorkerSupervisor(
        model_record=record,
        state_dir=tmp_path / "state",
        command_prefix=[sys.executable, str(worker_script)],
        startup_timeout=1.0,
        auto_start=False,
    )
    service = daemon.LocalRuntimeDaemon(
        host="127.0.0.1",
        port=0,
        model_record=record,
        supervisor=supervisor,
    )
    service.start()
    try:
        request = Request(
            service.base_url + "/v1/chat/completions",
            data=json.dumps({"messages": [{"role": "user", "content": "hello"}], "stream": False}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as captured:
            urlopen(request, timeout=5)
        payload = json.loads(captured.value.read().decode("utf-8"))

        assert captured.value.code == 503
        assert payload["error"]["code"] == "model_worker_crashed"
        assert payload["error"]["phase"] == "startup"
        assert payload["error"]["exit_code"] == 23
        with urlopen(service.base_url + "/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert health["status"] == "degraded"
        assert health["available"] is False
        assert health["error"]["code"] == "model_worker_crashed"
    finally:
        service.shutdown()


def test_daemon_health_remains_responsive_during_slow_model_preflight(tmp_path: Path) -> None:
    daemon = _required_module("scansci_html.local_runtime_daemon")
    record = _model_snapshot(tmp_path)
    worker_script = tmp_path / "slow_worker.py"
    worker_script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    supervisor = daemon.ModelWorkerSupervisor(
        model_record=record,
        state_dir=tmp_path / "state",
        command_prefix=[sys.executable, str(worker_script)],
        startup_timeout=1.0,
        auto_start=True,
    )
    service = daemon.LocalRuntimeDaemon(
        host="127.0.0.1",
        port=0,
        model_record=record,
        supervisor=supervisor,
    )
    service.start()
    started = __import__("time").monotonic()
    try:
        with urlopen(service.base_url + "/health", timeout=1) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert __import__("time").monotonic() - started < 1
        assert health["status"] == "starting"
        assert health["available"] is False
    finally:
        service.shutdown()


def test_mid_stream_worker_exit_emits_sse_error_done_and_degrades_daemon(tmp_path: Path) -> None:
    daemon = _required_module("scansci_html.local_runtime_daemon")
    record = _model_snapshot(tmp_path)
    worker_script = tmp_path / "mid_stream_crash_worker.py"
    worker_script.write_text(
        r"""
import argparse
import json
import os
import socket
import time

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--worker-model-id')
parser.add_argument('--port', type=int)
args, _ = parser.parse_known_args()
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('127.0.0.1', args.port))
server.listen(8)
while True:
    connection, _address = server.accept()
    request = b''
    while b'\r\n\r\n' not in request:
        chunk = connection.recv(65536)
        if not chunk:
            break
        request += chunk
    header, separator, request_body = request.partition(b'\r\n\r\n')
    content_length = 0
    for line in header.split(b'\r\n')[1:]:
        name, marker, value = line.partition(b':')
        if marker and name.strip().lower() == b'content-length':
            content_length = int(value.strip() or b'0')
    while separator and len(request_body) < content_length:
        chunk = connection.recv(65536)
        if not chunk:
            break
        request_body += chunk
    if not request.startswith(b'POST '):
        payload = json.dumps({
            'status': 'ok',
            'model': args.worker_model_id,
            'probe': {'generated': True, 'generated_text': 'ok'},
            'runtime_versions': {'torch': 'test', 'transformers': 'test'},
        }).encode()
        connection.sendall(
            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
            + str(len(payload)).encode() + b'\r\nConnection: close\r\n\r\n' + payload
        )
        connection.close()
        continue
    normal = b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
    connection.sendall(
        b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n' + normal
    )
    connection.close()
    time.sleep(0.2)
    os._exit(31)
""".strip(),
        encoding="utf-8",
    )
    supervisor = daemon.ModelWorkerSupervisor(
        model_record=record,
        state_dir=tmp_path / "state",
        command_prefix=[sys.executable, str(worker_script)],
        startup_timeout=3.0,
        auto_start=False,
    )
    service = daemon.LocalRuntimeDaemon(
        host="127.0.0.1",
        port=0,
        model_record=record,
        supervisor=supervisor,
    )
    service.start()
    try:
        request = Request(
            service.base_url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": record["id"],
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                stream = response.read().decode("utf-8")
        except HTTPError as error:
            pytest.fail(f"expected streaming 200, received {error.code}: {error.read().decode('utf-8')}")

        assert response.status == 200
        assert '"content":"first"' in stream
        assert "event: error" in stream
        assert '"code": "model_worker_crashed"' in stream
        assert stream.rstrip().endswith("data: [DONE]")
        with urlopen(service.base_url + "/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["status"] == "degraded"
        assert health["error"]["code"] == "model_worker_crashed"
        assert health["error"]["phase"] == "generation"
        assert supervisor.latest_failure is not None
        assert supervisor.latest_failure.phase == "generation"
    finally:
        service.shutdown()


def test_normal_sse_stream_is_forwarded_without_a_synthetic_error(tmp_path: Path) -> None:
    daemon = _required_module("scansci_html.local_runtime_daemon")
    record = _model_snapshot(tmp_path)
    worker_script = tmp_path / "normal_stream_worker.py"
    worker_script.write_text(
        r"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--worker-model-id')
parser.add_argument('--port', type=int)
args, _ = parser.parse_known_args()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        payload = json.dumps({
            'status': 'ok',
            'model': args.worker_model_id,
            'probe': {'generated': True, 'generated_text': 'ok'},
            'runtime_versions': {'torch': 'test', 'transformers': 'test'},
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0') or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"complete"}}]}\n\n')
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()

ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
""".strip(),
        encoding="utf-8",
    )
    supervisor = daemon.ModelWorkerSupervisor(
        model_record=record,
        state_dir=tmp_path / "state",
        command_prefix=[sys.executable, str(worker_script)],
        startup_timeout=3.0,
        auto_start=False,
    )
    service = daemon.LocalRuntimeDaemon(
        host="127.0.0.1",
        port=0,
        model_record=record,
        supervisor=supervisor,
    )
    service.start()
    try:
        request = Request(
            service.base_url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": record["id"],
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            status = response.status
            stream = response.read().decode("utf-8")

        assert status == 200
        assert '"content":"complete"' in stream
        assert stream.rstrip().endswith("data: [DONE]")
        assert "event: error" not in stream
        with urlopen(service.base_url + "/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["status"] == "ok"
        assert health["available"] is True
        assert health["error"] is None
        assert supervisor.latest_failure is None
    finally:
        service.shutdown()


def test_worker_environment_is_strictly_offline_and_preserves_existing_cache_paths(tmp_path: Path) -> None:
    daemon = _required_module("scansci_html.local_runtime_daemon")
    hf_home = tmp_path / "existing-hf-home"
    hub_cache = tmp_path / "existing-hub-cache"
    base = {
        "HF_HOME": str(hf_home),
        "HF_HUB_CACHE": str(hub_cache),
        "TRANSFORMERS_CACHE": str(tmp_path / "existing-transformers-cache"),
    }

    isolated = daemon.isolated_worker_environment(base)

    assert isolated["HF_HOME"] == str(hf_home)
    assert isolated["HF_HUB_CACHE"] == str(hub_cache)
    assert isolated["TRANSFORMERS_CACHE"] == base["TRANSFORMERS_CACHE"]
    assert isolated["HF_HUB_OFFLINE"] == "1"
    assert isolated["TRANSFORMERS_OFFLINE"] == "1"
    assert isolated["HF_DATASETS_OFFLINE"] == "1"
    assert isolated["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_qwen35_requires_matching_load_and_generation_probe_before_compatibility(tmp_path: Path) -> None:
    compatibility = _required_module("scansci_html.local_runtime_compatibility")
    record = _model_snapshot(tmp_path)
    store = compatibility.ModelCompatibilityStore(tmp_path / "compatibility.json")
    fingerprint = compatibility.snapshot_fingerprint(record)

    pending = store.apply(record, component_version="1.0.4")
    assert pending["runtime_verified"] is False
    assert pending["runtime_compatible"] is False
    assert pending["ready"] is False
    assert pending["model_files_present"] is True
    assert pending["installed"] is True
    assert Path(str(record["path"])).is_dir()

    with pytest.raises(ValueError, match="generation"):
        store.record_success(
            record,
            component_version="1.0.4",
            runtime_versions={"torch": "2.13.0+cu130", "transformers": "5.14.1"},
            generated_text="",
        )

    store.record_success(
        record,
        component_version="1.0.4",
        runtime_versions={"torch": "2.13.0+cu130", "transformers": "5.14.1"},
        generated_text="好",
    )
    verified = store.apply(record, component_version="1.0.4")
    assert verified["runtime_verified"] is True
    assert verified["runtime_compatible"] is True
    assert verified["ready"] is True
    assert verified["model_files_present"] is True
    assert verified["runtime_probe"]["fingerprint"] == fingerprint
    assert verified["runtime_probe"]["generated"] is True

    config = Path(str(record["path"])) / "config.json"
    config.write_text('{"model_type":"qwen3_5","changed":true}', encoding="utf-8")
    stale = store.apply(record, component_version="1.0.4")
    assert stale["runtime_verified"] is False
    assert stale["runtime_compatible"] is False
    assert stale["ready"] is False
    assert stale["model_files_present"] is True


def test_failed_qwen35_probe_marks_unavailable_without_modifying_model_cache(tmp_path: Path) -> None:
    compatibility = _required_module("scansci_html.local_runtime_compatibility")
    record = _model_snapshot(tmp_path)
    weight = Path(str(record["path"])) / "model-00001-of-00001.safetensors"
    before = (weight.read_bytes(), weight.stat().st_mtime_ns)
    store = compatibility.ModelCompatibilityStore(tmp_path / "compatibility.json")

    store.record_failure(
        record,
        component_version="1.0.4",
        error={
            "code": "model_worker_crashed",
            "message": "模型子进程在加载或生成时原生崩溃。",
            "phase": "load_or_generate",
            "exit_code": 0xC0000005,
            "native_crash": True,
        },
    )
    unavailable = store.apply(record, component_version="1.0.4")

    assert unavailable["runtime_verified"] is True
    assert unavailable["runtime_compatible"] is False
    assert unavailable["ready"] is False
    assert unavailable["model_files_present"] is True
    assert unavailable["installed"] is True
    assert unavailable["runtime_error"]["code"] == "model_worker_crashed"
    assert "原生崩溃" in unavailable["runtime_compatibility_message"]
    assert (weight.read_bytes(), weight.stat().st_mtime_ns) == before


def test_model_preflight_must_load_and_return_generated_text(tmp_path: Path) -> None:
    runtime_server = _required_module("scansci_html.local_runtime_server")
    calls: list[str] = []

    def load_model(record):
        calls.append("load")
        return object()

    def generate(model, record, messages, *, max_new_tokens):
        calls.append("generate")
        assert max_new_tokens >= 1
        yield "好"

    result = runtime_server.run_model_preflight(
        _model_snapshot(tmp_path),
        load_model=load_model,
        generate_stream=generate,
    )

    assert calls == ["load", "generate"]
    assert result["generated"] is True
    assert result["generated_text"] == "好"


def test_model_preflight_rejects_an_empty_generation(tmp_path: Path) -> None:
    runtime_server = _required_module("scansci_html.local_runtime_server")

    with pytest.raises(RuntimeError, match="没有生成任何文本"):
        runtime_server.run_model_preflight(
            _model_snapshot(tmp_path),
            load_model=lambda _record: object(),
            generate_stream=lambda *_args, **_kwargs: iter([""]),
        )


def test_public_runtime_entry_builds_a_daemon_not_an_in_process_model_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_server = _required_module("scansci_html.local_runtime_server")
    daemon_module = _required_module("scansci_html.local_runtime_daemon")
    record = _model_snapshot(tmp_path)
    monkeypatch.setattr(runtime_server, "installed_models", lambda: [record])

    service = runtime_server.create_runtime_daemon(
        model_id=str(record["id"]),
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path / "state",
        auto_start_worker=False,
    )

    assert isinstance(service, daemon_module.LocalRuntimeDaemon)
    assert isinstance(service.supervisor, daemon_module.ModelWorkerSupervisor)
    assert service.supervisor.model_record["path"] == record["path"]


def test_desktop_component_passes_a_persistent_state_directory_to_the_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component_module = _required_module("scansci_html.local_runtime_component")
    component = component_module.LocalRuntimeComponent(root=tmp_path / "runtime", manifest_url="")
    executable = tmp_path / "ScanSciLocalRuntime.exe"
    executable.write_bytes(b"fake")
    monkeypatch.setattr(component, "executable", lambda: executable)
    health = iter(["", "Qwen/Qwen3.5-2B"])
    monkeypatch.setattr(component, "_health_payload", lambda _base_url: None)
    monkeypatch.setattr(component, "_health_model", lambda _base_url: next(health))
    captured: dict[str, object] = {}

    class Process:
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(component_module.subprocess, "Popen", popen)

    component.ensure_process("Qwen/Qwen3.5-2B")

    command = list(captured["command"])
    assert "--state-dir" not in command
    environment = dict(captured["kwargs"]["env"])
    assert environment["SCANSCI_LOCAL_RUNTIME_STATE_DIR"] == str((component.root / "state").resolve())


def test_local_runtime_pagefile_failure_has_an_actionable_message() -> None:
    component_module = _required_module("scansci_html.local_runtime_component")

    message = component_module.LocalRuntimeComponent._actionable_runtime_message(
        "页面文件太小，无法完成操作。 (os error 1455)"
    )

    assert "增大虚拟内存" in message
    assert "云端视觉模型或 OCR" in message


def test_lazy_runtime_status_ok_is_ready_for_non_vision_models() -> None:
    component_module = _required_module("scansci_html.local_runtime_component")

    assert component_module.LocalRuntimeComponent._health_readiness(
        {"model": "Qwen/Qwen3-Embedding-0.6B", "status": "ok", "loaded": False}
    ) == "ready"
    assert component_module.LocalRuntimeComponent._health_needs_probe(
        {"model": "openbmb/MiniCPM-V-4.6-BNB", "status": "ok", "loaded": False},
        "openbmb/MiniCPM-V-4.6-BNB",
    ) is True


def test_single_component_entry_dispatches_daemon_and_worker_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_server = _required_module("scansci_html.local_runtime_server")
    calls: list[tuple[str, object]] = []

    class Worker:
        def __init__(self, *, host, port):
            calls.append(("worker-init", (host, port)))

        def start(self, model_id, *, preflight=False):
            calls.append(("worker-start", (model_id, preflight)))

        def wait(self):
            calls.append(("worker-wait", None))

        def shutdown(self):
            calls.append(("worker-shutdown", None))

    class Daemon:
        def start(self):
            calls.append(("daemon-start", None))

        def wait(self):
            calls.append(("daemon-wait", None))

        def shutdown(self):
            calls.append(("daemon-shutdown", None))

    monkeypatch.setattr(runtime_server, "LocalRuntimeServer", Worker)
    monkeypatch.setattr(
        runtime_server,
        "create_runtime_daemon",
        lambda **kwargs: calls.append(("daemon-create", kwargs)) or Daemon(),
    )

    assert runtime_server.main(
        ["--worker-model-id", "Qwen/Qwen3.5-2B", "--host", "127.0.0.1", "--port", "19001"]
    ) == 0
    assert runtime_server.main(
        [
            "--model-id",
            "Qwen/Qwen3.5-2B",
            "--host",
            "127.0.0.1",
            "--port",
            "19002",
            "--state-dir",
            str(tmp_path / "state"),
        ]
    ) == 0

    assert ("worker-start", ("Qwen/Qwen3.5-2B", True)) in calls
    assert any(name == "daemon-create" for name, _value in calls)
    assert ("daemon-start", None) in calls


def test_frozen_daemon_relaunches_the_same_executable_as_its_model_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = _required_module("scansci_html.local_runtime_daemon")
    monkeypatch.setattr(daemon.sys, "frozen", True, raising=False)
    monkeypatch.setattr(daemon.sys, "executable", r"C:\Program Files\ScanSci\ScanSciLocalRuntime.exe")

    assert daemon._default_command_prefix() == [r"C:\Program Files\ScanSci\ScanSciLocalRuntime.exe"]


def test_local_runtime_build_tracks_and_collects_daemon_modules() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "build_local_runtime.ps1").read_text(encoding="utf-8")

    assert "local_runtime_daemon.py" in script
    assert "local_runtime_compatibility.py" in script
    assert '"scansci_html.local_runtime_daemon"' in script
    assert '"scansci_html.local_runtime_compatibility"' in script


def test_installed_model_discovery_exposes_qwen35_as_unavailable_until_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market = _required_module("scansci_html.local_model_market")
    compatibility = _required_module("scansci_html.local_runtime_compatibility")
    record = _model_snapshot(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    monkeypatch.setenv("SCANSCI_LOCAL_RUNTIME_STATE_DIR", str(state_dir))

    pending = next(item for item in market.installed_models() if item["id"] == record["id"])
    assert pending["installed"] is True
    assert pending["model_files_present"] is True
    assert pending["ready"] is False
    assert pending["runtime_compatible"] is False

    store = compatibility.ModelCompatibilityStore(state_dir / "model-compatibility.json")
    store.record_success(
        record,
        component_version=market.COMPONENT_VERSION,
        runtime_versions={"torch": "2.13.0+cu130", "transformers": "5.14.1"},
        generated_text="好",
    )
    verified = next(item for item in market.installed_models() if item["id"] == record["id"])
    assert verified["ready"] is True
    assert verified["runtime_compatible"] is True

    store.record_failure(
        record,
        component_version=market.COMPONENT_VERSION,
        error={"code": "model_worker_crashed", "message": "原生崩溃", "phase": "generation"},
    )
    failed = next(item for item in market.installed_models() if item["id"] == record["id"])
    assert failed["ready"] is False
    assert failed["runtime_compatible"] is False
    assert failed["model_files_present"] is True
