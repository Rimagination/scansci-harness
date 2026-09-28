from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from zipfile import ZipFile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scansci_html.update_blockmap import build_blockmap
from scripts.verify_scansci_agent import (
    _mostly_chinese,
    _mostly_english,
    _numbered_section_bullet_counts,
    _numbered_section_count,
    verify_model,
)


SCRIPT = Path(__file__).parents[1] / "scripts" / "release_gate.py"
SPEC = importlib.util.spec_from_file_location("scansci_release_gate", SCRIPT)
assert SPEC and SPEC.loader
release_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_gate)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _release_signing_material(tmp_path: Path) -> tuple[Path, str]:
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "release-manifest.key"
    private_path.write_text(
        base64.b64encode(
            private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("ascii"),
        encoding="utf-8",
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_path, base64.b64encode(public).decode("ascii")


def _scope() -> dict:
    return {
        "schema_version": 1,
        "p0_objective": "验证发布门禁",
        "acceptance": [{"id": "gate", "requirement": "门禁留下证据"}],
        "non_goals": ["不改产品功能"],
    }


def _contract(command: list[str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "product": "ScanSci",
        "version": "0.2.0-test",
        "targeted_commands": (
            [{"id": "fake-targeted", "title": "快速命令", "command": command}]
            if command
            else []
        ),
        "full_commands": [],
        "real_verifications": [],
        "package": {"name": "ScanSci", "mode": "onedir", "required_resources": []},
        "visual_acceptance": {
            "evidence_file": "visual-evidence.json",
            "required_checks": ["enter_send"],
            "required_screenshots": ["home.png"],
        },
    }


def _valid_pi_report(tmp_path: Path, *, source_sha256: str, bundle_path: Path) -> Path:
    manifest = _write_json(
        tmp_path / "run-manifest.json",
        {"run_id": "run-test", "status": "completed", "metrics": {"tool_calls": 1}},
    )
    matrix = Path(release_gate.PROJECT_ROOT) / "bench" / "pi_capability_tasks.json"
    matrix_payload = json.loads(matrix.read_text(encoding="utf-8"))
    axes = []
    for matrix_axis in matrix_payload["axes"]:
        case_ids = [str(case["id"]) for case in matrix_axis["cases"]]
        axes.append({
            "id": str(matrix_axis["id"]),
            "cases": len(case_ids),
            "threshold": int(matrix_axis["threshold"]),
            "passed": len(case_ids),
            "status": "passed",
            "evidence_matches": 0,
            "verified_case_ids": case_ids,
        })
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
    junit = tmp_path / "pi-targeted-junit.xml"
    testcases = "".join(
        f'<testcase classname="{node_id.split("::", 1)[0]}" name="{node_id.split("::", 1)[1]}" time="0.1"/>'
        for node_id in node_ids
    )
    junit.write_text(
        f'<testsuite tests="100" failures="0" errors="0" skipped="0">{testcases}</testsuite>',
        encoding="utf-8",
    )
    batch_matches: dict[str, dict[str, list[str]]] = {}
    selector_matches: dict[str, int] = {}
    matched_timings: dict[str, float] = {}
    for matrix_axis, report_axis in zip(matrix_payload["axes"], axes, strict=True):
        axis_batches: dict[str, list[str]] = {}
        axis_nodes: set[str] = set()
        proof_count = 0
        for batch in matrix_axis["requirements"]["proof_batches"]:
            if batch["source"] == "runtime":
                proof_count += 10
                continue
            matches = [node_id for node_id in node_ids if batch["selector"] in node_id]
            axis_batches[batch["id"]] = matches
            axis_nodes.update(matches)
            proof_count += len(matches)
            matched_timings.update({node_id: 0.1 for node_id in matches})
        batch_matches[matrix_axis["id"]] = axis_batches
        selector_matches[matrix_axis["id"]] = len(axis_nodes)
        report_axis["evidence_matches"] = proof_count
    observability_axis = next(axis for axis in matrix_payload["axes"] if axis["id"] == "observability")
    observability_matches = {
        kind: sum(1 for node_id in node_ids if any(selector in node_id for selector in selectors))
        for kind, selectors in observability_axis["requirements"]["kind_selectors"].items()
    }
    mutation_proofs = [
        {
            "turn": index + 1,
            "session_id": f"mutation-{index + 1}",
            "target_tool": "inspect_workspace" if index % 2 == 0 else "self_assess",
            "search_tools_completed": True,
            "target_activated": True,
            "target_completed": True,
        }
        for index in range(10)
    ]
    base_loop = {
        "ok": True,
        "tool_calls": 1,
        "done": True,
        "marker_seen": True,
        "fallback_count": 0,
        "provider_requests": 2,
        "dynamic_mutations": 0,
        "mutation_evidence": [],
        "image_tool_tasks": 0,
        "image_serialized_tasks": 0,
        "duration_seconds": 0.1,
    }
    dynamic_loop = {
        **base_loop,
        "tool_calls": 10,
        "provider_requests": 30,
        "dynamic_mutations": 10,
        "mutation_evidence": mutation_proofs,
    }
    image_loop = {
        **base_loop,
        "tool_calls": 10,
        "provider_requests": 20,
        "image_tool_tasks": 10,
        "image_serialized_tasks": 10,
    }
    required_features = sorted(release_gate._PI_REQUIRED_RUNTIME_FEATURES)
    observability_records = []
    observability_cases = {case["id"]: case for case in observability_axis["cases"]}
    for batch in observability_axis["requirements"]["proof_batches"]:
        matches = batch_matches["observability"][batch["id"]]
        selected = [case for case in observability_axis["cases"] if case["probe"] == batch["case_probe"]]
        for index, case in enumerate(selected):
            node_id = matches[index % len(matches)]
            observability_records.append({
                "id": case["id"],
                "kind": observability_cases[case["id"]]["probe"],
                "timing": {"source": "junit", "duration_seconds": 0.1},
                "decision": "passed",
                "result_reference": {
                    "junit_sha256": release_gate._sha256(junit),
                    "node_id": node_id,
                },
            })
    report = {
        "schema_version": 2,
        "report_kind": "scansci.pi-capabilities",
        "mode": "deterministic",
        "status": "passed",
        "protocol_version": 7,
        "sdk_version": "0.84.3",
        "source_sha256": source_sha256,
        "bundle": {"path": str(bundle_path), "sha256": release_gate._sha256(bundle_path), "bytes": bundle_path.stat().st_size},
        "matrix": {
            "path": str(matrix),
            "sha256": release_gate._sha256(matrix),
            "bytes": matrix.stat().st_size,
            "schema_version": 2,
        },
        "fallback_count": 0,
        "run_manifests": [{
            "path": str(manifest),
            "sha256": release_gate._sha256(manifest),
            "bytes": manifest.stat().st_size,
            "run_id": "run-test",
        }],
        "axes": axes,
        "provider": {"configured": False, "provider_id": "deterministic-loopback"},
        "evidence": {
            "ping": {"ready": True, "protocol": 7},
            "protocol_probe": {
                "ready": True,
                "protocol": 7,
                "required_features": required_features,
                "capabilities": required_features,
            },
            "tool_loop": base_loop,
            "targeted_tests": {
                "path": str(junit),
                "sha256": release_gate._sha256(junit),
                "bytes": junit.stat().st_size,
                "tests": 100,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "axis_selector_matches": selector_matches,
                "observability_selector_matches": observability_matches,
                "batch_matches": batch_matches,
                "matched_testcase_timings": matched_timings,
            },
            "dynamic_mutations": dynamic_loop,
            "image_tool_tasks": image_loop,
            "observability": {
                "required_fields": ["id", "timing", "decision", "result_reference"],
                "kind_matches": observability_matches,
                "run_manifest_count": 1,
                "records": observability_records,
            },
        },
    }
    return _write_json(tmp_path / "pi-capabilities.json", report)


def test_release_contract_requires_one_p0_with_acceptance_and_non_goals() -> None:
    scope = _scope()
    release_gate.validate_release_inputs(_contract(), scope)
    scope["non_goals"] = []
    with pytest.raises(release_gate.GateFailure, match="non-empty acceptance and non_goals"):
        release_gate.validate_release_inputs(_contract(), scope)


def test_release_contract_requires_scope_version_to_match_the_gate() -> None:
    contract = _contract()
    scope = _scope()
    scope["schema_version"] = 2
    scope["version"] = "0.4.0"
    contract["version"] = "0.3.1"

    with pytest.raises(release_gate.GateFailure, match="scope version must match"):
        release_gate.validate_release_inputs(contract, scope)


def test_v040_release_contract_rejects_schema_v1_scope_downgrade() -> None:
    contract = _contract()
    contract["version"] = "0.4.0"

    with pytest.raises(release_gate.GateFailure, match="schema v2"):
        release_gate.validate_release_inputs(contract, _scope())


@pytest.mark.parametrize("value", [True, 2.0, "2"])
def test_release_contract_rejects_non_integer_schema_numbers(value: object) -> None:
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "config" / "release-gate.json").read_text(encoding="utf-8"))
    scope = json.loads((root / "config" / "release-scope.json").read_text(encoding="utf-8"))
    scope["schema_version"] = value

    with pytest.raises(release_gate.GateFailure, match="schema_version must be an integer"):
        release_gate.validate_release_inputs(contract, scope)


@pytest.mark.parametrize("value", [True, 40.0, "40"])
def test_release_contract_rejects_non_integer_axis_thresholds(value: object) -> None:
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "config" / "release-gate.json").read_text(encoding="utf-8"))
    scope = json.loads((root / "config" / "release-scope.json").read_text(encoding="utf-8"))
    scope["acceptance"][0]["threshold"] = value

    with pytest.raises(release_gate.GateFailure, match="threshold must be an integer"):
        release_gate.validate_release_inputs(contract, scope)


def test_v2_scope_acceptance_must_match_the_pi_matrix() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "config" / "release-gate.json").read_text(encoding="utf-8"))
    scope = json.loads((root / "config" / "release-scope.json").read_text(encoding="utf-8"))
    release_gate.validate_release_inputs(contract, scope)

    scope["acceptance"][0]["threshold"] = 39
    with pytest.raises(release_gate.GateFailure, match="Pi acceptance mapping"):
        release_gate.validate_release_inputs(contract, scope)

    scope = json.loads((root / "config" / "release-scope.json").read_text(encoding="utf-8"))
    scope["acceptance"][0]["threshold"] = "not-an-integer"
    with pytest.raises(release_gate.GateFailure, match="threshold"):
        release_gate.validate_release_inputs(contract, scope)


def test_v2_scope_rejects_mutated_frozen_v031_history() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "config" / "release-gate.json").read_text(encoding="utf-8"))
    scope = json.loads((root / "config" / "release-scope.json").read_text(encoding="utf-8"))
    scope["release_history"][0]["acceptance"][0]["id"] = "rewritten-history"

    with pytest.raises(release_gate.GateFailure, match="v0.3.1 history"):
        release_gate.validate_release_inputs(contract, scope)


def test_release_gate_accepts_the_real_matrix_validation_artifact(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "pi-capability-matrix.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "verify_pi_capabilities.py"),
            "--validate-matrix-only",
            "--output",
            str(report),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    gate = object.__new__(release_gate.ReleaseGate)
    payload = gate._validate_required_result(report, result_kind="pi_matrix_validation")

    assert payload and payload["matrix"]["schema_version"] == 2


def test_release_gate_console_output_survives_legacy_windows_codepages(capsys: pytest.CaptureFixture[str]) -> None:
    release_gate._emit_console("证据 \ufffd", encoding="gbk")

    output = capsys.readouterr().out
    assert "证据" in output
    assert "\\ufffd" in output


def test_release_gate_accepts_semver_build_metadata_but_rejects_path_separators() -> None:
    assert release_gate.validate_build_id("0.2.0+formal-r4-20260728")
    assert release_gate.validate_build_id("20260728-120000")
    assert not release_gate.validate_build_id("../escape")
    assert not release_gate.validate_build_id("release/name")


def test_agent_release_check_accepts_six_numbered_sections_without_an_arbitrary_long_answer() -> None:
    text = "\n".join(f"### {index}. 第{index}项\n- 检查一\n- 检查二\n- 检查三" for index in range(1, 7))
    assert _numbered_section_count(text) == 6
    assert _numbered_section_bullet_counts(text) == [3, 3, 3, 3, 3, 3]


def test_agent_release_check_detects_the_user_language() -> None:
    assert _mostly_chinese("你好，我可以帮助你梳理科研证据。")
    assert not _mostly_chinese("Hello, I can help with research evidence.")
    assert _mostly_english("Hello, I can help a researcher compare evidence and draft a clear answer.")
    assert not _mostly_english("你好，我可以帮助你梳理科研证据。")


def test_agent_release_check_exercises_language_and_real_multi_turn_history(tmp_path: Path) -> None:
    class Runtime:
        def chat_stream(self, payload: dict):
            messages = list(payload.get("messages", []))
            prompt = str(messages[-1]["content"])
            trace = []
            if prompt.startswith("你好，请记住校验码"):
                answer = "你好，我已经记住这个校验码。"
            elif "刚才让你记住" in prompt:
                answer = re.search(r"SCN-[A-F0-9]+", str(messages[0]["content"])).group(0)
            elif prompt.startswith("Hello"):
                answer = "I can help a researcher compare evidence and draft a clear answer."
            elif prompt.startswith("你是谁"):
                answer = "我是 ScanSci，当前模型 glm-4.7-flash，并可加载 good-question Skill。"
                trace = [{"title": "运行时身份", "detail": "fixture"}]
            elif "六个编号部分" in prompt:
                answer = "\n".join(
                    f"### {index}. 检查部分\n- 检查一\n- 检查二\n- 检查三"
                    for index in range(1, 7)
                ) + "\n【回答完毕】"
            else:
                answer = "核心研究问题：在合理假设下比较植被与微气候响应。"
            yield {"type": "RUN_STARTED"}
            yield {"type": "TEXT_MESSAGE_CONTENT", "content": answer}
            yield {
                "type": "RUN_FINISHED",
                "result": {"message": {"role": "assistant", "content": answer, "trace": trace}},
            }

    result = verify_model(Runtime(), tmp_path / "workspace.sqlite", "glm-4.7-flash")

    assert result["passed"] is True
    assert result["checks"]["chinese_prompt_gets_chinese_answer"] is True
    assert result["checks"]["multi_turn_history_is_recalled"] is True
    assert result["checks"]["english_prompt_gets_english_answer"] is True


def test_real_release_contract_defaults_to_a_lightweight_core_package() -> None:
    contract = json.loads((Path(__file__).parents[1] / "config" / "release-gate.json").read_text(encoding="utf-8"))

    assert contract["package"]["profile"] == "core"
    assert contract["package"]["exclude_runtimes"] is True
    assert contract["package"]["signature_required"] is True
    assert int(contract["package"]["max_package_bytes"]) <= 450_000_000
    assert "{version}" in contract["package"]["update_package_url"]


def test_release_contract_accepts_only_https_runtime_component_manifests() -> None:
    contract = _contract()
    contract["package"]["runtime_manifest_url"] = "https://downloads.example.com/runtime.json"
    release_gate.validate_release_inputs(contract, _scope())

    contract["package"]["runtime_manifest_url"] = "file:///C:/runtime.json"
    with pytest.raises(release_gate.GateFailure, match="runtime_manifest_url must use HTTPS"):
        release_gate.validate_release_inputs(contract, _scope())

    contract = _contract()
    contract["package"].update(
        {
            "profile": "core",
            "exclude_runtimes": True,
            "node_component_manifest_url": "http://downloads.example.com/node.json",
            "tectonic_component_manifest_url": "https://downloads.example.com/tectonic.json",
        }
    )
    with pytest.raises(release_gate.GateFailure, match="node_component_manifest_url must use HTTPS"):
        release_gate.validate_release_inputs(contract, _scope())

    contract["package"]["node_component_manifest_url"] = "https://downloads.example.com/node.json"
    del contract["package"]["tectonic_component_manifest_url"]
    with pytest.raises(release_gate.GateFailure, match="separately downloadable runtime components"):
        release_gate.validate_release_inputs(contract, _scope())


def test_release_contract_requires_a_versioned_https_update_package_url() -> None:
    contract = _contract()
    contract["package"]["update_manifest_url"] = "https://downloads.example.com/stable.json"

    with pytest.raises(release_gate.GateFailure, match="update_package_url"):
        release_gate.validate_release_inputs(contract, _scope())

    contract["package"]["update_package_url"] = "http://downloads.example.com/ScanSci-{version}.zip"
    with pytest.raises(release_gate.GateFailure, match="update_package_url"):
        release_gate.validate_release_inputs(contract, _scope())

    contract["package"]["update_package_url"] = "https://downloads.example.com/ScanSci.zip"
    with pytest.raises(release_gate.GateFailure, match="{version}"):
        release_gate.validate_release_inputs(contract, _scope())

    contract["package"]["update_package_url"] = "https://downloads.example.com/ScanSci-{version}.zip"
    release_gate.validate_release_inputs(contract, _scope())


def test_release_gate_probes_external_runtime_manifests_and_assets(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    contract["package"].update(
        {
            "profile": "core",
            "exclude_runtimes": True,
            "runtime_manifest_url": "https://downloads.example.com/local-transformers.json",
            "node_component_manifest_url": "https://downloads.example.com/node.json",
            "tectonic_component_manifest_url": "https://downloads.example.com/tectonic.json",
        }
    )
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", contract),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="beta",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="component-channels",
    )
    requested: list[tuple[str, str]] = []

    class _Response:
        def __init__(self, payload: bytes = b"", status: int = 200) -> None:
            self.payload = payload
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

        def getcode(self) -> int:
            return self.status

    def fake_urlopen(request, timeout: int):
        del timeout
        url = request.full_url
        method = request.get_method()
        requested.append((method, url))
        if method == "HEAD":
            return _Response()
        component_id = (
            "node"
            if url.endswith("node.json")
            else "local-transformers"
            if url.endswith("local-transformers.json")
            else "tectonic"
        )
        payload = {
            "id": component_id,
            "version": "1.0.0",
            "windows": {
                "url": f"https://downloads.example.com/{component_id}.zip",
                "sha256": "a" * 64,
                "size": 42,
            },
        }
        return _Response(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(release_gate, "urlopen", fake_urlopen)

    result = gate.verify_runtime_component_channels()

    assert set(result["components"]) == {"node", "tectonic", "local-transformers"}
    assert requested == [
        ("GET", "https://downloads.example.com/node.json"),
        ("HEAD", "https://downloads.example.com/node.zip"),
        ("GET", "https://downloads.example.com/tectonic.json"),
        ("HEAD", "https://downloads.example.com/tectonic.zip"),
        ("GET", "https://downloads.example.com/local-transformers.json"),
        ("HEAD", "https://downloads.example.com/local-transformers.zip"),
    ]
    snapshot = Path(result["components"]["local-transformers"]["snapshot"])
    assert json.loads(snapshot.read_text(encoding="utf-8"))["id"] == "local-transformers"


def test_release_gate_rejects_a_missing_external_runtime_manifest(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    contract["package"].update(
        {
            "profile": "core",
            "exclude_runtimes": True,
            "runtime_manifest_url": "https://downloads.example.com/local-transformers.json",
            "node_component_manifest_url": "https://downloads.example.com/node.json",
            "tectonic_component_manifest_url": "https://downloads.example.com/tectonic.json",
        }
    )
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", contract),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="beta",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="missing-component-channel",
    )
    monkeypatch.setattr(release_gate, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("404")))

    with pytest.raises(release_gate.GateFailure, match="manifest is unavailable for node"):
        gate.verify_runtime_component_channels()


def test_release_gate_requires_an_overall_checksum_for_multipart_components() -> None:
    with pytest.raises(release_gate.GateFailure, match="package is missing SHA256 or size"):
        release_gate.ReleaseGate._validate_component_asset(
            "node",
            {
                "version": "22.14.0",
                "windows": {
                    "parts": [
                        {
                            "url": "https://downloads.example.com/node.zip.part001",
                            "sha256": "b" * 64,
                            "size": 42,
                        }
                    ]
                },
            },
        )


def test_release_gate_rejects_multipart_sizes_that_do_not_match_package() -> None:
    with pytest.raises(release_gate.GateFailure, match="part sizes do not match"):
        release_gate.ReleaseGate._validate_component_asset(
            "node",
            {
                "version": "22.14.0",
                "windows": {
                    "sha256": "a" * 64,
                    "size": 1,
                    "parts": [
                        {
                            "url": "https://downloads.example.com/node.zip.part001",
                            "sha256": "b" * 64,
                            "size": 8,
                        },
                        {
                            "url": "https://downloads.example.com/node.zip.part002",
                            "sha256": "c" * 64,
                            "size": 8,
                        },
                    ],
                },
            },
        )


def test_plan_only_writes_machine_readable_report_without_running_commands(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract(["{python}", "-c", "raise SystemExit(9)"]))
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="plan-test",
    )
    report = gate.plan()
    persisted = json.loads(gate.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "planned"
    assert persisted["planned_steps"] == ["scope-contract", "fake-targeted"]
    assert not (gate.diagnostics_dir / "fake-targeted.log").exists()


def test_release_plan_default_never_creates_an_empty_release_candidate() -> None:
    plan_root = release_gate.default_output_root(profile="release", plan_only=True)
    real_release_root = release_gate.default_output_root(profile="release", plan_only=False)
    assert plan_root.name == "release-gate-plans"
    assert real_release_root.name == "releases"
    assert plan_root != real_release_root


def test_source_gate_resumes_passed_steps_without_repeating_commands(tmp_path: Path) -> None:
    marker = tmp_path / "command-count.txt"
    code = (
        "from pathlib import Path; "
        f"p=Path({str(marker)!r}); "
        "p.write_text((p.read_text() if p.exists() else '') + 'x', encoding='utf-8')"
    )
    contract_path = _write_json(tmp_path / "contract.json", _contract(["{python}", "-c", code]))
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    first = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="resume-test",
    )
    assert first.run() == 0
    second = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "ignored",
        build_id="ignored",
        resume_report=first.report_path,
    )
    assert second.run() == 0
    assert marker.read_text(encoding="utf-8") == "x"
    report = json.loads(first.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert {step["id"] for step in report["steps"]} == {"scope-contract", "fake-targeted"}


def test_real_verification_retries_only_an_explicit_rate_limit_result(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="rate-retry-test",
    )
    marker = tmp_path / "attempts.txt"
    result = gate.diagnostics_dir / "agent.json"
    code = (
        "from pathlib import Path; import json; "
        f"marker=Path({str(marker)!r}); result=Path({str(result)!r}); "
        "count=len(marker.read_text() if marker.exists() else '')+1; "
        "marker.write_text('x'*count); "
        "result.write_text(json.dumps({'failure_reason': 'provider_rate_limited' if count == 1 else ''})); "
        "raise SystemExit(1 if count == 1 else 0)"
    )

    step = gate.command_step(
        step_id="rate-limited-model",
        title="rate limited model verification",
        command=[sys.executable, "-c", code],
        required_result=result,
        echo_output=False,
        retry={"max_attempts": 2, "delay_seconds": 0, "failure_reason": "provider_rate_limited"},
    )

    assert step["status"] == "passed"
    assert marker.read_text(encoding="utf-8") == "xx"
    assert [attempt["exit_code"] for attempt in step["attempts"]] == [1, 0]
    assert step["attempts"][0]["failure_reason"] == "provider_rate_limited"


def test_command_retry_can_match_failure_text_in_the_command_log(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="log-retry-test",
    )
    marker = tmp_path / "attempts.txt"
    code = (
        "from pathlib import Path; "
        f"marker=Path({str(marker)!r}); "
        "count=len(marker.read_text() if marker.exists() else '')+1; "
        "marker.write_text('x'*count); "
        "print('test_notebook_webapp_tests_saved_mcp_connection_and_reports_tools' if count == 1 else 'ok'); "
        "raise SystemExit(1 if count == 1 else 0)"
    )

    step = gate.command_step(
        step_id="log-retry",
        title="log retry",
        command=[sys.executable, "-c", code],
        echo_output=False,
        retry={
            "max_attempts": 2,
            "delay_seconds": 0,
            "failure_reason": "test_notebook_webapp_tests_saved_mcp_connection_and_reports_tools",
        },
    )

    assert step["status"] == "passed"
    assert marker.read_text(encoding="utf-8") == "xx"
    assert [attempt["exit_code"] for attempt in step["attempts"]] == [1, 0]
    assert step["attempts"][0]["failure_reason"] == "test_notebook_webapp_tests_saved_mcp_connection_and_reports_tools"


def test_command_step_can_write_nested_process_output_directly_to_its_log(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="file-output-test",
    )

    step = gate.command_step(
        step_id="file-output",
        title="file output",
        command=[sys.executable, "-c", "print('nested output')"],
        echo_output=False,
        output_to_file=True,
    )

    assert step["status"] == "passed"
    assert "nested output" in (gate.diagnostics_dir / "file-output.log").read_text(encoding="utf-8")


def test_release_gate_stops_a_hung_verification_at_the_declared_timeout(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="command-timeout-test",
    )

    with pytest.raises(release_gate.GateFailure, match="hung provider verification"):
        gate.command_step(
            step_id="hung-provider",
            title="hung provider verification",
            command=[sys.executable, "-c", "import time; time.sleep(3)"],
            echo_output=False,
            timeout_seconds=0.1,
        )

    step = gate.report["steps"][-1]

    assert step["status"] == "failed"
    assert step["exit_code"] == 124
    assert step["failure_reason"] == "command_timeout"
    assert step["attempts"][0]["failure_reason"] == "command_timeout"
    assert step["attempts"][0]["timeout_seconds"] == 0.1


def test_higher_profile_can_promote_unchanged_passed_evidence(tmp_path: Path) -> None:
    marker = tmp_path / "promoted-command-count.txt"
    code = (
        "from pathlib import Path; "
        f"p=Path({str(marker)!r}); "
        "p.write_text((p.read_text() if p.exists() else '') + 'x', encoding='utf-8')"
    )
    contract_path = _write_json(tmp_path / "contract.json", _contract(["{python}", "-c", code]))
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    targeted = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="targeted",
        knowledge_source=tmp_path,
        output_root=tmp_path / "targeted-reports",
        build_id="targeted",
    )
    assert targeted.run() == 0
    promoted = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "source-reports",
        build_id="source",
        promote_report=targeted.report_path,
    )
    assert promoted.run() == 0
    assert marker.read_text(encoding="utf-8") == "x"
    report = json.loads(promoted.report_path.read_text(encoding="utf-8"))
    assert report["promoted_from"]["profile"] == "targeted"
    assert report["promoted_from"]["step_ids"] == ["scope-contract", "fake-targeted"]


def test_build_script_reuses_dependency_cache_and_only_cleans_on_request() -> None:
    build_script = (Path(__file__).parents[1] / "scripts" / "build_desktop.ps1").read_text(encoding="utf-8")
    assert 'build\\desktop-cache\\$cacheName' in build_script
    assert 'build\\desktop-spec\\$cacheName' in build_script
    assert 'build\\desktop\\$resolvedBuildId' not in build_script
    assert '$metadataPath = Join-Path $projectRoot "build\\release-metadata\\$resolvedBuildId"' in build_script
    assert "if ($Clean)" in build_script
    assert '$pyInstallerArgs = @("--clean") + $pyInstallerArgs' in build_script
    assert '"--clean",' not in build_script
    assert '[string]$ReleaseSourceSha256 = ""' in build_script
    assert 'release_source_sha256 = $ReleaseSourceSha256.ToLowerInvariant()' in build_script


def test_release_package_is_bound_to_the_source_fingerprint_that_passed_the_gate(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="release-source-binding",
    )
    package = gate.package_dir
    internal = package / "_internal" / "scansci_html"
    internal.mkdir(parents=True)
    gate.executable.write_bytes(b"scan-sci")
    _write_json(internal / "build-info.json", {
        "build_id": gate.build_id,
        "package_profile": "core",
        "release_source_sha256": "0" * 64,
    })

    with pytest.raises(release_gate.GateFailure, match="source fingerprint"):
        gate.verify_package()


def test_release_package_rejects_executable_changed_after_signing(tmp_path: Path) -> None:
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", _contract()),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="signed-package-binding",
    )
    internal = gate.package_dir / "_internal" / "scansci_html"
    internal.mkdir(parents=True)
    gate.executable.write_bytes(b"signed-executable")
    gate.report["artifacts"]["signed_executable_sha256"] = release_gate._sha256(gate.executable)
    gate.executable.write_bytes(b"changed-after-signing")
    _write_json(
        internal / "build-info.json",
        {
            "build_id": gate.build_id,
            "package_profile": "core",
            "release_source_sha256": gate.source_sha256,
        },
    )

    with pytest.raises(release_gate.GateFailure, match="changed after Authenticode signing"):
        gate.verify_package()


@pytest.mark.parametrize(
    "forbidden_relative_path",
    [
        Path("torch/lib/torch_cpu.dll"),
        Path("third-party/node.exe"),
        Path("latex/bin/tectonic.exe"),
        Path("cache/models--openbmb--MiniCPM/config.json"),
    ],
)
def test_core_package_rejects_bundled_model_runtime_or_huggingface_snapshot(
    tmp_path: Path,
    forbidden_relative_path: Path,
) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="core-forbidden-runtime",
    )
    internal = gate.package_dir / "_internal" / "scansci_html"
    internal.mkdir(parents=True)
    gate.executable.write_bytes(b"scan-sci")
    _write_json(internal / "build-info.json", {
        "build_id": gate.build_id,
        "package_profile": "core",
        "release_source_sha256": gate.source_sha256,
    })
    forbidden = gate.package_dir / "_internal" / forbidden_relative_path
    forbidden.parent.mkdir(parents=True)
    forbidden.write_bytes(b"not-a-runtime")

    with pytest.raises(release_gate.GateFailure, match="optional runtime resources"):
        gate.verify_package()


def test_release_package_requires_pi_bundle_and_matching_build_info_hash(tmp_path: Path) -> None:
    contract = _contract()
    contract["package"]["required_resources"] = ["pi_runtime/main.mjs"]
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", contract),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="pi-bundle-binding",
    )
    internal = gate.package_dir / "_internal"
    build_info = internal / "scansci_html" / "build-info.json"
    build_info.parent.mkdir(parents=True)
    gate.executable.write_bytes(b"scan-sci")
    _write_json(build_info, {
        "build_id": gate.build_id,
        "package_profile": "core",
        "release_source_sha256": gate.source_sha256,
        "pi_bundle_sha256": "0" * 64,
    })

    with pytest.raises(release_gate.GateFailure, match="pi_runtime/main.mjs"):
        gate.verify_package()

    bundle = internal / "pi_runtime" / "main.mjs"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("export {};", encoding="utf-8")
    with pytest.raises(release_gate.GateFailure, match="Pi bundle SHA256"):
        gate.verify_package()


def test_packaged_diagnostics_requires_a_completed_pi_tool_loop(tmp_path: Path, monkeypatch) -> None:
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", _contract()),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="diagnostic-tool-loop",
    )
    output = gate.diagnostics_dir / "packaged-runtime.json"

    def fake_command_step(**_kwargs):
        _write_json(output, {"ok": True, "pi_tool_loop": {"ok": False, "tool_calls": 0, "done": False}})
        return {"status": "passed"}

    monkeypatch.setattr(gate, "command_step", fake_command_step)

    with pytest.raises(release_gate.GateFailure, match="tool loop"):
        gate.packaged_diagnostics()


def test_release_build_keeps_long_pyinstaller_output_in_its_artifact_log(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    _private_path, public_key = _release_signing_material(tmp_path)
    contract["package"]["release_public_key"] = public_key
    contract["package"]["update_manifest_url"] = "https://downloads.example.com/stable.json"
    contract["package"]["update_package_url"] = "https://downloads.example.com/ScanSci-{version}.zip"
    contract["package"]["runtime_manifest_url"] = "https://downloads.example.com/runtime.json"
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="quiet-package-build",
    )
    captured: dict[str, object] = {}

    def fake_command_step(**kwargs):
        captured.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(gate, "command_step", fake_command_step)
    monkeypatch.setattr(release_gate.shutil, "which", lambda _name: "powershell.exe")
    gate.build_desktop()

    assert captured["step_id"] == "build-desktop"
    assert captured["echo_output"] is False
    command = list(captured["command"])
    output_dir_index = command.index("-OutputDir") + 1
    assert Path(command[output_dir_index]) == gate.package_staging_root
    update_manifest_index = command.index("-UpdateManifestUrl") + 1
    assert command[update_manifest_index] == "https://downloads.example.com/stable.json"
    release_public_key_index = command.index("-ReleasePublicKey") + 1
    assert command[release_public_key_index] == public_key
    runtime_manifest_index = command.index("-RuntimeManifestUrl") + 1
    assert command[runtime_manifest_index] == "https://downloads.example.com/runtime.json"
    assert "-ExcludeRuntimes" not in command
    assert gate.package_dir == gate.package_staging_root / "ScanSci"
    assert len(str(gate.package_dir)) < len(str(gate.release_dir / "ScanSci"))


def test_windows_installer_build_script_supports_standard_and_per_user_inno_locations() -> None:
    installer_script = (Path(__file__).parents[1] / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")
    signer_script = (Path(__file__).parents[1] / "scripts" / "sign_windows_executable.ps1").read_text(encoding="utf-8")

    assert "Inno Setup 6\\ISCC.exe" in installer_script
    assert "$env:LOCALAPPDATA\\Programs\\Inno Setup 6\\ISCC.exe" in installer_script
    assert "Get-AuthenticodeSignature" in installer_script
    assert "Assert-ScanSciArtifactSignature -Artifact $exe" in installer_script
    assert "Invoke-ScanSciArtifactSigning -Artifact $installer" in installer_script
    assert "Invoke-ScanSciArtifactSigning -Artifact $exe" not in installer_script
    assert "Invoke-ScanSciArtifactSigning -Artifact $resolvedArtifact" in signer_script


def test_formal_release_signs_the_executable_before_package_identity(tmp_path: Path, monkeypatch) -> None:
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", _contract()),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="sign-before-package",
    )
    gate.executable.parent.mkdir(parents=True)
    gate.executable.write_bytes(b"unsigned")
    captured: dict[str, object] = {}

    def fake_command_step(**kwargs):
        captured.update(kwargs)
        gate.executable.write_bytes(b"signed")
        return {"status": "passed", "result_sha256": release_gate._sha256(gate.executable)}

    monkeypatch.setattr(gate, "command_step", fake_command_step)
    monkeypatch.setattr(release_gate.shutil, "which", lambda _name: "powershell.exe")

    details = gate.sign_desktop_executable()

    assert captured["step_id"] == "sign-desktop-executable"
    assert Path(str(captured["required_result"])) == gate.executable
    assert details["signed_executable_sha256"] == release_gate._sha256(gate.executable)


def test_formal_release_requests_a_signed_installer(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    contract["package"]["signature_required"] = True
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="signed-installer",
    )
    captured: dict[str, object] = {}

    def fake_command_step(**kwargs):
        captured.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(gate, "command_step", fake_command_step)
    monkeypatch.setattr(release_gate.shutil, "which", lambda _name: "powershell.exe")
    gate.build_installer()

    assert "-RequireSignature" in captured["command"]


def test_formal_release_cannot_disable_signing_through_contract_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = _contract()
    contract["package"]["signature_required"] = False
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="mandatory-signing",
    )
    captured: dict[str, object] = {}

    def fake_command_step(**kwargs):
        captured.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(gate, "command_step", fake_command_step)
    monkeypatch.setattr(release_gate.shutil, "which", lambda _name: "powershell.exe")

    gate.build_installer()

    assert gate.signature_required is True
    assert "-RequireSignature" in captured["command"]


def test_formal_release_signing_preflight_rejects_missing_configuration(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    contract["package"]["signature_required"] = True
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="signing-preflight",
    )
    monkeypatch.delenv("SCANSCI_SIGNING_CERT_THUMBPRINT", raising=False)
    monkeypatch.delenv("SCANSCI_TIMESTAMP_URL", raising=False)

    with pytest.raises(release_gate.GateFailure, match="signing is not configured"):
        gate.signing_environment()


def test_formal_release_signing_preflight_records_a_configured_release_machine(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    contract["package"]["signature_required"] = True
    private_path, public_key = _release_signing_material(tmp_path)
    contract["package"]["release_public_key"] = public_key
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="configured-signing-preflight",
    )
    monkeypatch.setenv("SCANSCI_SIGNING_CERT_THUMBPRINT", "A" * 40)
    monkeypatch.setenv("SCANSCI_TIMESTAMP_URL", "https://timestamp.example.test/rfc3161")
    monkeypatch.setenv("SCANSCI_RELEASE_MANIFEST_PRIVATE_KEY_FILE", str(private_path))
    monkeypatch.setattr(release_gate, "_find_sign_tool", lambda: tmp_path / "signtool.exe")

    assert gate.signing_environment() == {
        "signature_required": True,
        "certificate_reference_present": True,
        "timestamp_endpoint_present": True,
        "sign_tool_available": True,
        "private_key_validation": "deferred_to_build_script",
        "manifest_private_key_present": True,
        "manifest_public_key_matches": True,
    }


def test_formal_release_installer_verification_requires_an_installed_signature(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    contract["package"]["signature_required"] = True
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="signed-installation",
    )
    gate.report["artifacts"]["installer_path"] = str(tmp_path / "installer.exe")
    captured: dict[str, object] = {}

    def fake_command_step(**kwargs):
        captured.update(kwargs)
        Path(str(kwargs["required_result"])).write_text('{"ok": true}', encoding="utf-8")
        return {"status": "passed"}

    monkeypatch.setattr(gate, "command_step", fake_command_step)
    gate.verify_installer_installation()

    assert "--require-signature" in captured["command"]
    assert captured["command"][captured["command"].index("--expected-version") + 1] == contract["version"]


def test_formal_release_rejects_an_unsigned_installer_manifest(tmp_path: Path) -> None:
    contract = _contract()
    contract["package"]["signature_required"] = True
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="unsigned-installer",
    )
    installer = gate.installer_dir / "ScanSci-0.2.0-test-windows-x64-setup.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"installer")
    digest = release_gate._sha256(installer)
    gate.report["artifacts"]["executable_sha256"] = digest
    _write_json(
        gate.installer_manifest,
        {
            "version": "0.2.0-test",
            "build_id": gate.build_id,
            "installer_path": str(installer),
            "installer_sha256": digest,
            "source_executable_sha256": digest,
            "signature_required": False,
            "authenticode_status": "NotSigned",
        },
    )

    with pytest.raises(release_gate.GateFailure, match="both the installer and ScanSci.exe"):
        gate.verify_installer_manifest()


def test_formal_release_rejects_a_manifest_with_an_unsigned_packaged_executable(tmp_path: Path) -> None:
    contract = _contract()
    contract["package"]["signature_required"] = True
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="unsigned-executable",
    )
    installer = gate.installer_dir / "ScanSci-0.2.0-test-windows-x64-setup.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"installer")
    digest = release_gate._sha256(installer)
    gate.report["artifacts"]["executable_sha256"] = digest
    _write_json(
        gate.installer_manifest,
        {
            "version": "0.2.0-test",
            "build_id": gate.build_id,
            "installer_path": str(installer),
            "installer_sha256": digest,
            "source_executable_sha256": digest,
            "signature_required": True,
            "authenticode_status": "Valid",
            "source_executable_authenticode_status": "NotSigned",
        },
    )

    with pytest.raises(release_gate.GateFailure, match="both the installer and ScanSci.exe"):
        gate.verify_installer_manifest()


def test_release_source_fingerprint_includes_the_installer_and_gateway_definitions() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"pi-runtime"' in source
    assert '"bench"' in source
    assert '"AGENTS.md"' in source
    assert '"docs/project-governance.zh.md"' in source


def test_release_source_fingerprint_changes_for_pi_runtime_bench_and_architecture_docs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for relative in (
        "pi-runtime/src/main.ts",
        "pi-runtime/package-lock.json",
        "bench/pi_capability_tasks.json",
        "AGENTS.md",
        "docs/project-governance.zh.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before", encoding="utf-8")
    monkeypatch.setattr(release_gate, "PROJECT_ROOT", tmp_path)
    before = release_gate._source_fingerprint()

    for relative in (
        "pi-runtime/src/main.ts",
        "bench/pi_capability_tasks.json",
        "AGENTS.md",
        "docs/project-governance.zh.md",
    ):
        path = tmp_path / relative
        path.write_text(path.read_text(encoding="utf-8") + "-after", encoding="utf-8")
        after = release_gate._source_fingerprint()
        assert after != before, relative
        path.write_text("before", encoding="utf-8")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda report: report.update(schema_version=1), "schema_version"),
        (lambda report: report["axes"].pop(), "axes"),
        (lambda report: report["axes"][0].update(status="failed"), "axis"),
        (lambda report: report.update(fallback_count=1), "fallback"),
        (lambda report: report.update(source_sha256="0" * 64), "source"),
        (lambda report: report["bundle"].update(sha256="0" * 64), "bundle"),
        (lambda report: report["run_manifests"][0].update(sha256="0" * 64), "manifest"),
        (lambda report: report["run_manifests"][0].update(run_id="invented-run"), "run_id"),
        (lambda report: report["axes"][0].update(threshold=39), "matrix|threshold"),
        (lambda report: report.update(unexpected="not allowed"), "unknown|unexpected|schema"),
        (lambda report: report["axes"][0].update(unexpected="not allowed"), "unknown"),
        (lambda report: report["provider"].update(unexpected="not allowed"), "unknown"),
        (lambda report: report["evidence"].update(unexpected="not allowed"), "unknown"),
    ],
)
def test_release_gate_strictly_validates_pi_capability_report(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    bundle = tmp_path / "main.mjs"
    bundle.write_text("export {};", encoding="utf-8")
    report_path = _valid_pi_report(tmp_path, source_sha256="a" * 64, bundle_path=bundle)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    mutation(report)
    _write_json(report_path, report)

    with pytest.raises(release_gate.GateFailure, match=message):
        release_gate.validate_pi_capability_report(
            report_path,
            expected_source_sha256="a" * 64,
            expected_bundle_path=bundle,
            required_mode="deterministic",
        )


def test_release_gate_parses_manifest_status_and_tool_call_evidence(tmp_path: Path) -> None:
    bundle = tmp_path / "main.mjs"
    bundle.write_text("export {};", encoding="utf-8")
    report_path = _valid_pi_report(tmp_path, source_sha256="a" * 64, bundle_path=bundle)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path = Path(report["run_manifests"][0]["path"])
    _write_json(manifest_path, {"run_id": "run-test", "status": "failed", "metrics": {"tool_calls": 0}})
    report["run_manifests"][0]["sha256"] = release_gate._sha256(manifest_path)
    report["run_manifests"][0]["bytes"] = manifest_path.stat().st_size
    _write_json(report_path, report)

    with pytest.raises(release_gate.GateFailure, match="completed|tool call|status"):
        release_gate.validate_pi_capability_report(
            report_path,
            expected_source_sha256="a" * 64,
            expected_bundle_path=bundle,
            required_mode="deterministic",
        )


def test_release_gate_rejects_manifest_outside_current_diagnostics(tmp_path: Path) -> None:
    report_dir = tmp_path / "diagnostics"
    report_dir.mkdir()
    bundle = tmp_path / "main.mjs"
    bundle.write_text("export {};", encoding="utf-8")
    report_path = _valid_pi_report(report_dir, source_sha256="a" * 64, bundle_path=bundle)
    outside = _write_json(
        tmp_path / "outside-manifest.json",
        {"run_id": "outside", "status": "completed", "metrics": {"tool_calls": 1}},
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["run_manifests"] = [{
        "path": str(outside),
        "sha256": release_gate._sha256(outside),
        "bytes": outside.stat().st_size,
        "run_id": "outside",
    }]
    _write_json(report_path, report)

    with pytest.raises(release_gate.GateFailure, match="outside"):
        release_gate.validate_pi_capability_report(
            report_path,
            expected_source_sha256="a" * 64,
            expected_bundle_path=bundle,
            required_mode="deterministic",
        )


def test_release_gate_recomputes_case_proofs_from_concrete_junit_nodes(tmp_path: Path) -> None:
    bundle = tmp_path / "main.mjs"
    bundle.write_text("export {};", encoding="utf-8")
    report_path = _valid_pi_report(tmp_path, source_sha256="a" * 64, bundle_path=bundle)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    junit = Path(report["evidence"]["targeted_tests"]["path"])
    unrelated = "".join(
        f'<testcase classname="tests.test_unrelated" name="test_case_{index}" time="0.1"/>'
        for index in range(100)
    )
    junit.write_text(
        f'<testsuite tests="100" failures="0" errors="0" skipped="0">{unrelated}</testsuite>',
        encoding="utf-8",
    )
    report["evidence"]["targeted_tests"].update(
        sha256=release_gate._sha256(junit),
        bytes=junit.stat().st_size,
    )
    _write_json(report_path, report)

    with pytest.raises(release_gate.GateFailure, match="proof batch|selector|JUnit"):
        release_gate.validate_pi_capability_report(
            report_path,
            expected_source_sha256="a" * 64,
            expected_bundle_path=bundle,
            required_mode="deterministic",
        )


def test_release_gate_requires_observability_records_bound_to_passed_nodes(tmp_path: Path) -> None:
    bundle = tmp_path / "main.mjs"
    bundle.write_text("export {};", encoding="utf-8")
    report_path = _valid_pi_report(tmp_path, source_sha256="a" * 64, bundle_path=bundle)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["evidence"]["observability"]["records"][0]["result_reference"]["node_id"] = (
        "tests.test_unrelated::test_not_a_proof"
    )
    _write_json(report_path, report)

    with pytest.raises(release_gate.GateFailure, match="observability record"):
        release_gate.validate_pi_capability_report(
            report_path,
            expected_source_sha256="a" * 64,
            expected_bundle_path=bundle,
            required_mode="deterministic",
        )


def test_command_step_rejects_success_with_an_invalid_pi_report(tmp_path: Path) -> None:
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", _contract()),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="targeted",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="invalid-pi-report",
    )
    output = gate.diagnostics_dir / "pi-capabilities.json"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(output)!r}).write_text('{{}}')"]

    with pytest.raises(release_gate.GateFailure, match="capability report"):
        gate.command_step(
            step_id="pi-capabilities-deterministic",
            title="Pi capability report",
            command=command,
            required_result=output,
            result_kind="pi_capability_report",
        )


def test_promotion_revalidates_tampered_pi_capability_evidence(tmp_path: Path) -> None:
    contract = _contract()
    contract["targeted_commands"] = [{
        "id": "pi-capabilities-deterministic",
        "title": "Pi deterministic",
        "command": [sys.executable, "-c", "pass"],
        "result_file": "pi-capabilities.json",
        "result_kind": "pi_capability_report",
    }]
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    targeted = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="targeted",
        knowledge_source=tmp_path,
        output_root=tmp_path / "targeted",
        build_id="targeted",
    )
    bundle = Path(release_gate.PROJECT_ROOT) / "pi-runtime" / "dist" / "main.mjs"
    report_path = _valid_pi_report(targeted.diagnostics_dir, source_sha256=targeted.source_sha256, bundle_path=bundle)
    targeted._record({
        "id": "pi-capabilities-deterministic",
        "title": "Pi deterministic",
        "status": "passed",
        "result_file": str(report_path),
        "result_sha256": release_gate._sha256(report_path),
    })
    targeted.report["status"] = "passed"
    targeted._persist()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["fallback_count"] = 1
    _write_json(report_path, report)

    with pytest.raises(release_gate.GateFailure, match="evidence|fallback"):
        release_gate.ReleaseGate(
            contract_path=contract_path,
            scope_path=scope_path,
            profile="source",
            knowledge_source=tmp_path,
            output_root=tmp_path / "source",
            build_id="source",
            promote_report=targeted.report_path,
        )


def test_promotion_rewrites_and_revalidates_internal_pi_evidence_paths(tmp_path: Path) -> None:
    contract = _contract()
    contract["targeted_commands"] = [{
        "id": "pi-capabilities-deterministic",
        "title": "Pi deterministic",
        "command": [sys.executable, "-c", "pass"],
        "result_file": "pi-capabilities.json",
        "result_kind": "pi_capability_report",
    }]
    contract_path = _write_json(tmp_path / "contract.json", contract)
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    targeted = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="targeted",
        knowledge_source=tmp_path,
        output_root=tmp_path / "targeted",
        build_id="targeted-remap",
    )
    bundle = Path(release_gate.PROJECT_ROOT) / "pi-runtime" / "dist" / "main.mjs"
    report_path = _valid_pi_report(
        targeted.diagnostics_dir,
        source_sha256=targeted.source_sha256,
        bundle_path=bundle,
    )
    targeted._record({
        "id": "pi-capabilities-deterministic",
        "title": "Pi deterministic",
        "status": "passed",
        "result_file": str(report_path),
        "result_sha256": release_gate._sha256(report_path),
    })
    targeted.report["status"] = "passed"
    targeted._persist()

    promoted = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "source",
        build_id="source-remap",
        promote_report=targeted.report_path,
    )

    step = next(item for item in promoted.report["steps"] if item["id"] == "pi-capabilities-deterministic")
    promoted_report = json.loads(Path(step["result_file"]).read_text(encoding="utf-8"))
    manifest_path = Path(promoted_report["run_manifests"][0]["path"])
    junit_path = Path(promoted_report["evidence"]["targeted_tests"]["path"])
    assert manifest_path.is_relative_to(promoted.diagnostics_dir)
    assert junit_path.is_relative_to(promoted.diagnostics_dir)
    assert not manifest_path.is_relative_to(targeted.diagnostics_dir)
    assert not junit_path.is_relative_to(targeted.diagnostics_dir)
    assert step["result_sha256"] == release_gate._sha256(Path(step["result_file"]))
    promoted_summary = promoted.report["artifacts"]["pi_capabilities"]["deterministic"]
    assert Path(promoted_summary["report"]) == Path(step["result_file"])
    assert promoted_summary["report_sha256"] == step["result_sha256"]


def test_release_gate_timeout_terminates_the_nested_process_tree(tmp_path: Path) -> None:
    marker = tmp_path / "orphan-marker.txt"
    child_code = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text('orphan')"
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        "time.sleep(10)"
    )
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract-tree.json", _contract()),
        scope_path=_write_json(tmp_path / "scope-tree.json", _scope()),
        profile="source",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports-tree",
        build_id="process-tree-timeout",
    )

    with pytest.raises(release_gate.GateFailure, match="process tree"):
        gate.command_step(
            step_id="process-tree",
            title="process tree",
            command=[sys.executable, "-c", parent_code],
            echo_output=False,
            timeout_seconds=0.2,
        )

    time.sleep(1.4)
    assert not marker.exists()


def test_release_plan_requires_a_built_and_exercised_windows_installer(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="installer-plan",
    )

    plan = gate.plan()

    assert "build-installer" in plan["planned_steps"]
    assert "installer-integrity" in plan["planned_steps"]
    assert "installer-installation" in plan["planned_steps"]
    assert "build-update-bundle" in plan["planned_steps"]
    assert "update-bundle-integrity" in plan["planned_steps"]
    assert "signing-environment" in plan["planned_steps"]
    assert plan["planned_steps"].index("build-desktop") < plan["planned_steps"].index("sign-desktop-executable")
    assert plan["planned_steps"].index("sign-desktop-executable") < plan["planned_steps"].index("package-integrity")
    assert plan["planned_steps"].index("package-integrity") < plan["planned_steps"].index("build-update-bundle")


def test_release_update_bundle_receives_the_offline_manifest_key(tmp_path: Path, monkeypatch) -> None:
    contract = _contract()
    private_path, public_key = _release_signing_material(tmp_path)
    contract["package"].update(
        {
            "release_public_key": public_key,
            "release_key_id": "release-test",
            "update_manifest_url": "https://downloads.example.com/stable.json",
            "update_package_url": "https://downloads.example.com/ScanSci-{version}.zip",
        }
    )
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", contract),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="signed-update-command",
    )
    captured: dict[str, object] = {}

    monkeypatch.setenv("SCANSCI_RELEASE_MANIFEST_PRIVATE_KEY_FILE", str(private_path))
    monkeypatch.setattr(release_gate.shutil, "which", lambda _name: "powershell.exe")
    monkeypatch.setattr(
        gate,
        "command_step",
        lambda **kwargs: captured.update(kwargs) or {"status": "passed"},
    )

    gate.build_update_bundle()

    command = list(captured["command"])
    assert command[command.index("-ManifestPrivateKey") + 1] == str(private_path.resolve())
    assert command[command.index("-ManifestKeyId") + 1] == "release-test"


def test_release_gate_binds_update_assets_to_the_verified_package_identity(tmp_path: Path) -> None:
    contract = _contract()
    contract["package"].update(
        {
            "profile": "core",
            "exclude_runtimes": True,
            "update_manifest_url": "https://downloads.example.com/stable.json",
            "update_package_url": "https://downloads.example.com/v{version}/ScanSci-{version}-windows-x64.zip",
            "runtime_manifest_url": "https://downloads.example.com/local-transformers.json",
            "node_component_manifest_url": "https://downloads.example.com/node.json",
            "tectonic_component_manifest_url": "https://downloads.example.com/tectonic.json",
        }
    )
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", contract),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="update-assets",
    )
    identity = {
        "version": contract["version"],
        "build_id": gate.build_id,
        "package_profile": "core",
        "release_source_sha256": gate.source_sha256,
    }
    gate.report["artifacts"]["build_info"] = identity
    gate.update_bundle_dir.mkdir(parents=True)
    archive = gate.update_bundle_dir / f"ScanSci-{contract['version']}-windows-x64.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("ScanSci.exe", b"desktop")
        zipped.writestr("_internal/scansci_html/build-info.json", json.dumps(identity))
    blockmap_path = archive.with_suffix(archive.suffix + ".blockmap")
    _write_json(blockmap_path, build_blockmap(archive))
    runtime_component = {
        "id": "local-transformers",
        "version": "1.0.4",
        "windows": {
            "url": "https://downloads.example.com/local-transformers.zip",
            "sha256": "a" * 64,
            "size": 42,
        },
    }
    _write_json(gate.diagnostics_dir / "runtime-component-local-transformers.json", runtime_component)
    _write_json(
        gate.update_manifest,
        {
            "schema_version": 1,
            "version": contract["version"],
            "channel": "stable",
            "windows": {
                "url": gate._update_package_url(),
                "sha256": release_gate._sha256(archive),
                "size": archive.stat().st_size,
                "blockmap": {
                    "url": gate._update_package_url() + ".blockmap",
                    "sha256": release_gate._sha256(blockmap_path),
                    "size": blockmap_path.stat().st_size,
                    "block_size": 65536,
                },
            },
            "components": {
                "local-transformers": {
                    "version": runtime_component["version"],
                    "windows": runtime_component["windows"],
                }
            },
        },
    )

    details = gate.verify_update_bundle()

    assert details["update_archive_sha256"] == release_gate._sha256(archive)
    assert details["update_package_url"].endswith(f"/ScanSci-{contract['version']}-windows-x64.zip")
    assert details["runtime_component_version"] == "1.0.4"


def test_release_gate_rejects_unsigned_update_manifest_when_public_key_is_configured(tmp_path: Path) -> None:
    contract = _contract()
    _private_path, public_key = _release_signing_material(tmp_path)
    contract["package"]["release_public_key"] = public_key
    gate = release_gate.ReleaseGate(
        contract_path=_write_json(tmp_path / "contract.json", contract),
        scope_path=_write_json(tmp_path / "scope.json", _scope()),
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="unsigned-update-manifest",
    )
    gate.update_bundle_dir.mkdir(parents=True)
    _write_json(
        gate.update_manifest,
        {"schema_version": 1, "version": contract["version"], "channel": "stable"},
    )

    with pytest.raises(release_gate.GateFailure, match="signature is invalid"):
        gate.verify_update_bundle()


def test_beta_plan_builds_and_exercises_an_unsigned_installer_without_formal_signing(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="beta",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="invited-beta-plan",
    )

    plan = gate.plan()

    assert "signing-environment" not in plan["planned_steps"]
    assert "build-installer" in plan["planned_steps"]
    assert "installer-installation" in plan["planned_steps"]
    assert "beta-delivery" in plan["planned_steps"]
    assert "visual-acceptance" not in plan["planned_steps"]


def test_beta_delivery_materials_bind_to_the_unsigned_verified_installer(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="beta",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="invited-beta-materials",
    )
    installer = gate.installer_dir / "ScanSci-0.2.0-test-windows-x64-setup.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"unsigned installer")
    gate.report["artifacts"].update(
        {
            "installer_path": str(installer),
            "installer_sha256": release_gate._sha256(installer),
            "installer_authenticode_status": "NotSigned",
            "executable_sha256": "a" * 64,
            "source_executable_authenticode_status": "NotSigned",
        }
    )

    details = gate.prepare_beta_delivery()
    delivery = json.loads(Path(details["distribution_manifest"]).read_text(encoding="utf-8"))

    assert delivery["channel"] == "invited-internal-beta"
    assert delivery["installer"]["sha256"] == release_gate._sha256(installer)
    assert delivery["installer"]["unsigned_acknowledgement_required"] is True
    assert "未知发布者" in Path(details["readme"]).read_text(encoding="utf-8")
    assert "API 密钥" in Path(details["feedback_template"]).read_text(encoding="utf-8")


def test_beta_outputs_are_isolated_from_formal_releases() -> None:
    assert release_gate.default_output_root(profile="beta", plan_only=False).name == "internal-beta-releases"


def test_installer_manifest_must_bind_to_the_verified_packaged_executable(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "contract.json", _contract())
    scope_path = _write_json(tmp_path / "scope.json", _scope())
    gate = release_gate.ReleaseGate(
        contract_path=contract_path,
        scope_path=scope_path,
        profile="release",
        knowledge_source=tmp_path,
        output_root=tmp_path / "reports",
        build_id="installer-binding",
    )
    installer = gate.installer_dir / "ScanSci-0.2.0-test-windows-x64-setup.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"installer")
    gate.report["artifacts"]["executable_sha256"] = "a" * 64
    _write_json(
        gate.installer_manifest,
        {
            "version": "0.2.0-test",
            "build_id": gate.build_id,
            "installer_path": str(installer),
            "installer_sha256": release_gate._sha256(installer),
            "source_executable_sha256": "b" * 64,
        },
    )

    with pytest.raises(release_gate.GateFailure, match="verified packaged executable"):
        gate.verify_installer_manifest()
