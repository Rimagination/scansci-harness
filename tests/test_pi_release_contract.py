"""Contracts for the v0.4.2 release candidate and its v0.4.0 Pi baseline."""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ACCEPTANCE = {
    "pi-routing": ("routing", 40),
    "pi-dynamic-tools": ("dynamic_tools", 10),
    "pi-parallelism": ("parallelism", 3),
    "pi-long-context": ("long_context", 20),
    "pi-skills": ("skills", 20),
    "pi-subagents": ("subagents", 3),
    "pi-mcp": ("mcp", 10),
    "pi-multimodal": ("multimodal", 10),
    "pi-safety": ("safety", 128),
    "pi-observability": ("observability", 10),
}


def _json(path: str) -> dict:
    return json.loads((PROJECT_ROOT / path).read_text(encoding="utf-8"))


def test_v041_is_the_single_p0_and_maps_exactly_to_the_task8_matrix() -> None:
    scope = _json("config/release-scope.json")
    matrix = _json("bench/pi_capability_tasks.json")

    assert scope["schema_version"] == 2
    assert scope["version"] == "0.4.2"
    assert "v0.4.2" in scope["p0_objective"]
    assert {
        item["id"]: (item["report_axis"], item["threshold"])
        for item in scope["acceptance"]
    } == EXPECTED_ACCEPTANCE
    assert {
        axis["id"]: axis["threshold"] for axis in matrix["axes"]
    } == {axis: threshold for axis, threshold in EXPECTED_ACCEPTANCE.values()}


def test_v031_scope_is_preserved_as_superseded_frozen_unverified_history() -> None:
    scope = _json("config/release-scope.json")
    history = scope["release_history"]

    assert len(history) == 1
    previous = history[0]
    assert previous["version"] == "0.3.1"
    assert previous["state"] == "superseded"
    assert previous["verification"] == "frozen_unverified"
    assert len(previous["acceptance"]) == 13
    assert previous["non_goals"]
    assert "发布 v0.3.1" in previous["p0_objective"]
    assert "released" not in json.dumps(previous, ensure_ascii=False).lower()


def test_architecture_docs_define_the_full_pi_boundary_and_evidence_contract() -> None:
    required_by_document = {
        "AGENTS.md": ["Pi 与 Host 权责", "direct fallback", "protocol v7"],
        "docs/agent-startup.zh.md": ["10 轴", "fallback_count", "bundle.sha256"],
        "docs/project-governance.zh.md": [
            "Pi 编排层",
            "Host 权威层",
            "fail-closed",
            "最小内核与插件优先约束",
            "唯一编排面",
        ],
        "docs/research-agent-architecture.zh.md": ["scansci.pi-capabilities", "100%", "frozen_unverified"],
        "docs/agent-harness-p0-p2.zh.md": ["protocol v7", "report schema v2", "not_run"],
        "docs/implementation-plan.md": ["v0.4.0", "TaskContract v2", "research_runs schema v4"],
        "docs/release-workflow.zh.md": ["scansci.pi-capabilities", "fallback_count = 0", "provider-real"],
        "docs/desktop-packaging.zh.md": ["pi_runtime/main.mjs", "Node executable", "完整 Windows ZIP"],
    }
    for relative, required in required_by_document.items():
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert all(token in text for token in required), (relative, required)


def test_full_pi_contract_does_not_promise_unbounded_or_unsafe_authority() -> None:
    architecture = (PROJECT_ROOT / "docs/research-agent-architecture.zh.md").read_text(encoding="utf-8")
    for required in (
        "不等于无限资源",
        "不开放任意 shell",
        "Skill 只改变指令",
        "未知 effect 拒绝",
        "最多 3 个",
        "abort + resume",
        "unsupported",
    ):
        assert required in architecture


def test_targeted_release_gate_runs_the_v040_contract_tests() -> None:
    gate = _json("config/release-gate.json")
    targeted_python = next(item for item in gate["targeted_commands"] if item["id"] == "targeted-python")

    assert "tests/test_pi_release_contract.py" in targeted_python["command"]


def test_mcp_p0_distinguishes_deferred_evidence_from_legacy_direct_compatibility() -> None:
    scope = _json("config/release-scope.json")
    mcp = next(item for item in scope["acceptance"] if item["id"] == "pi-mcp")
    runtime_source = (PROJECT_ROOT / "pi-runtime" / "src" / "main.ts").read_text(encoding="utf-8")

    assert "deferred stdio" in mcp["requirement"]
    assert "不增加 Harness 分支" in mcp["requirement"]
    assert "旧 direct 配置只作显式兼容" in mcp["requirement"]
    assert "if (raw.deferred === true)" in runtime_source
    assert "ensureDirectConnected" in runtime_source


def test_routing_p0_keeps_harness_thin_and_pi_as_the_only_model_orchestrator() -> None:
    scope = _json("config/release-scope.json")
    routing = next(item for item in scope["acceptance"] if item["id"] == "pi-routing")

    assert "Harness 只做 turn/contract/lease 绑定和事件透传" in routing["requirement"]
    assert "模型规划与工具循环唯一归 Pi" in routing["requirement"]
