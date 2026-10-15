from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import threading

import pytest

import scansci_html.run_manifest as run_manifest_module
from scansci_html.harness_adapters import (
    OptionalHarnessUnavailable,
    build_openai_agents_agent,
    build_pydanticai_agent,
    probe_optional_harnesses,
)
from scansci_html.run_manifest import RunManifest, load_manifest
from scansci_html.pi_agent import PiAgentClient


VERIFY_SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_pi_capabilities.py"
FRONTEND_VERIFY_SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_scansci_frontend.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("scansci_verify_pi_capabilities", VERIFY_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_frontend_verifier():
    spec = importlib.util.spec_from_file_location("scansci_verify_frontend", FRONTEND_VERIFY_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_complete_pi_junit(path: Path) -> Path:
    node_ids = [
        *[
            f"tests.test_pi_capability_matrix::test_bilingual_turns_use_pi_only_when_the_task_profile_requires_it[case-{index}]"
            for index in range(40)
        ],
        *[
            f"tests.test_pi_parallel::test_three_two_second_safe_reads_overlap_end_to_end[round-{index}]"
            for index in range(1, 4)
        ],
        "tests.test_context_policy::test_long_context_twenty_turn_batch_recovers_every_sentinel",
        *[f"tests.test_skill_runtime::test_skill_case_{index}" for index in range(20)],
        "tests.test_pi_subagents::test_atomic_delegation_reserves_at_most_three_under_ten_concurrent_requests",
        "tests.test_mcp_bridge::test_deferred_mcp_registers_and_calls_native_remote_schema_after_search",
        "tests.test_mcp_bridge::test_deferred_streamable_http_stays_disconnected_until_search_then_calls_native_schema",
        "tests.test_pi_security::test_mcp_policy_endpoint_audit_and_cache_are_fail_closed",
        "tests.test_pi_observability::test_runtime_hooks_emit_bounded_ordered_current_turn_audit",
        "tests.test_pi_agent::test_pi_manual_compaction_is_persisted",
        *[f"tests.test_other::test_unrelated_{index}" for index in range(30)],
    ]
    assert len(node_ids) == 100
    cases = "".join(
        f'<testcase classname="{node_id.split("::", 1)[0]}" name="{node_id.split("::", 1)[1]}" time="0.1"/>'
        for node_id in node_ids
    )
    path.write_text(
        f'<testsuite tests="100" failures="0" errors="0" skipped="0">{cases}</testsuite>',
        encoding="utf-8",
    )
    return path


def test_run_manifest_is_durable_and_redacted(tmp_path):
    full_tool_lease = [f"tool_{index:02d}" for index in range(60)]
    manifest = RunManifest.start(
        tmp_path / "workspace.sqlite",
        harness="pi",
        provider="gateway",
        model="model",
        api_surface="chat_completions",
        session_id="session-1",
        prompt="do not persist this prompt",
        tool_set=["search_local_evidence"],
        task_contract={
            "schema_version": "scansci.task-contract.v2",
            "version": 2,
            "goal": "PRIVATE_GOAL_SENTINEL api_key=goal-secret",
            "Question": "CASE_PRIVATE_SENTINEL",
            "metadata": {
                "prompt": "NESTED_PRIVATE_SENTINEL",
                "question": "NESTED_QUESTION_SENTINEL",
            },
            "allowed_tools": full_tool_lease,
            "capability_lease": {
                "requested_tools": full_tool_lease,
                "allowed_tools": full_tool_lease,
            },
        },
        timeout_seconds=12,
    )
    manifest.record("model.request.started", api_key="secret-value", input_chars=120)
    manifest.finish(status="completed", total_tokens=3)

    payload = load_manifest(manifest.path)
    raw = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "completed"
    assert payload["api_surface"] == "chat_completions"
    assert payload["prompt_version"]
    assert "secret-value" not in raw
    assert "PRIVATE_GOAL_SENTINEL" not in raw
    assert "CASE_PRIVATE_SENTINEL" not in raw
    assert "NESTED_PRIVATE_SENTINEL" not in raw
    assert "NESTED_QUESTION_SENTINEL" not in raw
    assert "goal-secret" not in raw
    assert "api_key" not in raw
    assert payload["task_contract"]["projection_schema"] == "scansci.task-contract-audit.v1"
    assert payload["task_contract"]["goal_reference"]["chars"] > 0
    assert len(payload["task_contract"]["goal_reference"]["sha256"]) == 64
    assert payload["task_contract"]["allowed_tools"] == full_tool_lease
    assert payload["task_contract"]["capability_lease"]["requested_tools"] == full_tool_lease
    assert payload["task_contract"]["capability_lease"]["allowed_tools"] == full_tool_lease
    assert payload["metrics"]["total_tokens"] == 3


@pytest.mark.skipif(os.name != "nt", reason="Windows path-length regression")
def test_run_manifest_atomic_write_survives_a_near_limit_windows_path(tmp_path: Path) -> None:
    workspace_root = tmp_path
    placeholder = "run-" + ("0" * 32) + ".json"
    final_length = len(str(workspace_root / ".scansci-diagnostics" / "runs" / placeholder))
    padding = 250 - final_length - 1
    assert 0 < padding < 240
    workspace_root /= "x" * padding
    assert len(str(workspace_root / ".scansci-diagnostics" / "runs" / placeholder)) == 250

    manifest = RunManifest.start(
        workspace_root / "workspace.sqlite",
        harness="pi",
        provider="gateway",
        model="model",
        api_surface="chat_completions",
    )
    manifest.finish(status="completed", tool_calls=1)

    assert manifest.path.exists()
    assert load_manifest(manifest.path)["status"] == "completed"
    assert not list(manifest.path.parent.glob("*.tmp"))


def test_run_manifest_atomic_staging_does_not_overwrite_a_peer_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = RunManifest(
        workspace=tmp_path / "workspace.sqlite",
        harness="pi",
        provider="gateway",
        model="model",
        api_surface="chat_completions",
        run_id="run-collision",
    )
    manifest.path.parent.mkdir(parents=True, exist_ok=True)
    peer = manifest.path.parent / ".deadbeef.tmp"
    peer.write_text("peer-owned", encoding="utf-8")
    monkeypatch.setattr(
        run_manifest_module,
        "uuid4",
        lambda: type("FixedUuid", (), {"hex": "deadbeef" + ("0" * 24)})(),
    )

    manifest.persist()

    assert peer.read_text(encoding="utf-8") == "peer-owned"
    assert load_manifest(manifest.path)["run_id"] == "run-collision"


def test_run_manifest_atomic_staging_is_cleaned_when_serialization_fails(tmp_path: Path) -> None:
    manifest = RunManifest(
        workspace=tmp_path / "workspace.sqlite",
        harness="pi",
        provider="gateway",
        model="model",
        api_surface="chat_completions",
        run_id="run-serialization-error",
    )
    manifest.metrics["not-json"] = object()

    with pytest.raises(TypeError):
        manifest.persist()

    assert not list(manifest.path.parent.glob("*.tmp"))


def test_optional_harness_probe_is_lazy_and_serializable():
    probes = probe_optional_harnesses()
    assert {item.name for item in probes} == {"pydanticai", "openai-agents", "langgraph"}
    assert all(item.api_surfaces for item in probes)
    assert all(isinstance(item.to_dict(), dict) for item in probes)


def test_optional_adapters_fail_closed_when_not_installed():
    installed = {item.name: item.installed for item in probe_optional_harnesses()}
    if not installed["pydanticai"]:
        try:
            build_pydanticai_agent(name="probe", instructions="probe", model="gpt")
        except OptionalHarnessUnavailable:
            pass
        else:  # pragma: no cover - protects the optional dependency contract
            raise AssertionError("PydanticAI adapter unexpectedly ran without its optional dependency")
    if not installed["openai-agents"]:
        try:
            build_openai_agents_agent(name="probe", instructions="probe", model="gpt")
        except OptionalHarnessUnavailable:
            pass
        else:  # pragma: no cover - protects the optional dependency contract
            raise AssertionError("OpenAI Agents adapter unexpectedly ran without its optional dependency")


def test_pi_capability_verifier_runs_a_real_sidecar_tool_loop(tmp_path: Path) -> None:
    verifier = _load_verifier()

    evidence = verifier.run_deterministic_tool_loop(tmp_path, timeout_seconds=30)

    assert evidence["ping"]["ready"] is True
    assert evidence["tool_loop"]["ok"] is True
    assert evidence["tool_loop"]["tool_calls"] >= 1
    assert evidence["tool_loop"]["done"] is True
    assert evidence["tool_loop"]["fallback_count"] == 0
    assert Path(evidence["run_manifest"]["path"]).is_file()
    assert len(evidence["run_manifest"]["sha256"]) == 64


def test_pi_capability_verifier_builds_a_complete_deterministic_report(tmp_path: Path) -> None:
    verifier = _load_verifier()
    report_root = tmp_path
    if os.name == "nt":
        placeholder = (
            report_root
            / "matrix-cycles"
            / "multimodal"
            / ".scansci-diagnostics"
            / "runs"
            / ("run-" + ("0" * 32) + ".json")
        )
        padding = 262 - len(str(placeholder)) - 1
        assert 0 < padding < 240
        report_root /= "x" * padding
    report_root.mkdir(parents=True, exist_ok=True)
    output = report_root / "pi-capabilities-deterministic.json"

    report = verifier.run_verification(
        mode="deterministic",
        output=output,
        workspace=report_root / "workspace.sqlite",
        timeout_seconds=30,
        test_evidence=_write_complete_pi_junit(report_root / "pi-targeted-junit.xml"),
    )

    assert report["status"] == "passed"
    assert len(report["run_manifests"]) == 21
    assert report["fallback_count"] == 0
    assert all(axis["status"] == "passed" for axis in report["axes"])
    assert output.is_file()


def test_frontend_release_verifier_rejects_serialized_parallel_streams() -> None:
    pytest.importorskip("playwright.sync_api")
    verifier = _load_frontend_verifier()
    real_stream = verifier._fake_chat_stream
    serialization_lock = threading.Lock()

    def serialized_stream(runtime, payload):
        with serialization_lock:
            yield from real_stream(runtime, payload)

    verifier._fake_chat_stream = serialized_stream

    # The serialized stream can trip the earlier queue check before the overlap check.
    with pytest.raises(AssertionError, match="overlap|FIFO order"):
        verifier.verify()


def test_frontend_release_verifier_uses_an_installed_browser_channel_when_bundle_is_missing() -> None:
    verifier = _load_frontend_verifier()

    class FakeChromium:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def launch(self, **options: object) -> str:
            self.calls.append(dict(options))
            if "channel" not in options:
                raise RuntimeError("Executable doesn't exist at managed browser path")
            return str(options["channel"])

    chromium = FakeChromium()

    assert verifier._launch_chromium(chromium) == "chrome"
    assert chromium.calls == [{"headless": True}, {"headless": True, "channel": "chrome"}]


def test_frontend_release_verifier_does_not_hide_a_real_channel_launch_error() -> None:
    verifier = _load_frontend_verifier()

    class FakeChromium:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def launch(self, **options: object) -> str:
            self.calls.append(dict(options))
            if "channel" not in options:
                raise RuntimeError("Executable doesn't exist at managed browser path")
            raise RuntimeError("Browser launch blocked by enterprise policy")

    chromium = FakeChromium()

    with pytest.raises(RuntimeError, match="enterprise policy"):
        verifier._launch_chromium(chromium)
    assert chromium.calls == [{"headless": True}, {"headless": True, "channel": "chrome"}]


def test_frontend_release_verifier_rejects_non_fifo_follow_up_events() -> None:
    verifier = _load_frontend_verifier()

    with pytest.raises(AssertionError, match="FIFO"):
        verifier._assert_follow_up_sequence(
            [
                ("start", "parent"),
                ("start", "child-2"),
                ("finish", "parent"),
                ("finish", "child-2"),
                ("start", "child-1"),
                ("finish", "child-1"),
            ],
            ["parent", "child-1", "child-2"],
        )


def test_pi_capability_verifier_rejects_a_stale_runtime_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    monkeypatch.setattr(
        verifier.PiAgentClient,
        "runtime_status",
        lambda **_kwargs: {"ready": True, "protocol": 6, "capabilities": []},
    )

    with pytest.raises(verifier.CapabilityVerificationError, match="protocol"):
        verifier.run_deterministic_tool_loop(tmp_path, timeout_seconds=30)


def test_pi_diagnostic_loop_exercises_repeated_dynamic_and_image_tool_turns(tmp_path: Path) -> None:
    dynamic = PiAgentClient.diagnostic_tool_loop(
        workspace=tmp_path / "dynamic" / "workspace.sqlite",
        evidence_db=tmp_path / "dynamic" / "evidence.sqlite",
        task_count=2,
        dynamic_activation=True,
    )
    multimodal = PiAgentClient.diagnostic_tool_loop(
        workspace=tmp_path / "multimodal" / "workspace.sqlite",
        evidence_db=tmp_path / "multimodal" / "evidence.sqlite",
        task_count=2,
        include_image=True,
    )

    assert dynamic["ok"] is True
    assert dynamic["tool_calls"] == 2
    assert dynamic["dynamic_mutations"] == 2
    assert dynamic["provider_requests"] == 6
    assert len(dynamic["mutation_evidence"]) == 2
    assert all(item["search_tools_completed"] for item in dynamic["mutation_evidence"])
    assert all(item["target_activated"] for item in dynamic["mutation_evidence"])
    assert len({item["session_id"] for item in dynamic["mutation_evidence"]}) == 2
    assert multimodal["ok"] is True
    assert multimodal["tool_calls"] == 2
    assert multimodal["image_tool_tasks"] == 2
    assert multimodal["image_serialized_tasks"] == 2
    assert multimodal["provider_requests"] == 4


def test_pi_capability_real_mode_without_provider_is_not_run_and_nonzero(tmp_path: Path) -> None:
    verifier = _load_verifier()
    output = tmp_path / "real-report.json"

    exit_code = verifier.main([
        "--mode", "real",
        "--output", str(output),
        "--workspace", str(tmp_path / "workspace.sqlite"),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code != 0
    assert report["schema_version"] == 2
    assert report["mode"] == "real"
    assert report["status"] == "not_run"
    assert report["provider"]["configured"] is False
    assert report["fallback_count"] == 0


def test_pi_capability_report_contains_no_provider_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _load_verifier()
    monkeypatch.setenv("SCANSCI_PI_CAPABILITY_API_KEY", "pi-capability-super-secret")
    output = tmp_path / "report.json"

    verifier.write_not_run_report(output=output, mode="real", reason="provider_not_configured")

    assert "pi-capability-super-secret" not in output.read_text(encoding="utf-8")


def test_pi_capability_failure_report_redacts_provider_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    secret = "pi-capability-super-secret"
    base_url = f"https://user:{secret}@provider.invalid/v1?token={secret}"
    monkeypatch.setenv("SCANSCI_PI_CAPABILITY_API_KEY", secret)
    monkeypatch.setenv("SCANSCI_PI_CAPABILITY_BASE_URL", base_url)
    monkeypatch.setattr(
        verifier,
        "run_verification",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(f"provider failed: {base_url} key={secret}")),
    )
    output = tmp_path / "failed-report.json"

    assert verifier.main(["--mode", "real", "--output", str(output)]) != 0

    encoded = output.read_text(encoding="utf-8")
    assert secret not in encoded
    assert "user:" not in encoded
    assert "?token=" not in encoded
