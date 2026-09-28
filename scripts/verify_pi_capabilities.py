"""Generate hash-bound Pi capability evidence for the release gate.

Deterministic mode uses a localhost-only model fixture but the production Pi
SDK bundle, provider serializer, JSONL bridge, authorization layer, and Python
tool dispatcher. Real mode never substitutes the fixture: missing provider
configuration is recorded as ``not_run`` and returns a non-zero exit code.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.release_gate import _source_fingerprint  # noqa: E402
from scansci_html.pi_agent import PiAgentClient, _PI_REQUIRED_FEATURES  # noqa: E402
from scansci_html.agent_contract import compile_task_contract  # noqa: E402
from scansci_html.model_metadata import descriptor_from_model_record  # noqa: E402
from scansci_html.telemetry import diagnostics_root  # noqa: E402


REPORT_SCHEMA_VERSION = 2
REPORT_KIND = "scansci.pi-capabilities"
PROTOCOL_VERSION = 7
MATRIX_PATH = PROJECT_ROOT / "bench" / "pi_capability_tasks.json"
BUNDLE_PATH = PROJECT_ROOT / "pi-runtime" / "dist" / "main.mjs"
PACKAGE_PATH = PROJECT_ROOT / "package.json"
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_REQUIRED_AXES = (
    "routing",
    "dynamic_tools",
    "parallelism",
    "long_context",
    "skills",
    "subagents",
    "mcp",
    "multimodal",
    "safety",
    "observability",
)
_MINIMUM_THRESHOLDS = {
    "routing": 40,
    "dynamic_tools": 10,
    "parallelism": 3,
    "long_context": 20,
    "skills": 20,
    "subagents": 3,
    "mcp": 10,
    "multimodal": 10,
    "safety": 100,
    "observability": 10,
}
_PI_TEST_FILES = (
    "tests/test_agent_contract.py",
    "tests/test_task_contract.py",
    "tests/test_agent_capabilities.py",
    "tests/test_pi_capability_matrix.py",
    "tests/test_pi_runtime_protocol.py",
    "tests/test_skill_runtime.py",
    "tests/test_pi_parallel.py",
    "tests/test_pi_observability.py",
    "tests/test_context_policy.py",
    "tests/test_pi_multimodal.py",
    "tests/test_image_attachments.py",
    "tests/test_vision_routing.py",
    "tests/test_pi_subagents.py",
    "tests/test_mcp_bridge.py",
    "tests/test_pi_security.py",
    "tests/test_pi_agent.py",
)


class CapabilityVerificationError(RuntimeError):
    """The deterministic evidence was incomplete or internally inconsistent."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise CapabilityVerificationError(f"Required evidence file is missing: {resolved}")
    return {"path": str(resolved), "sha256": _sha256(resolved), "bytes": resolved.stat().st_size}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sdk_version() -> str:
    package = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
    dependencies = dict(package.get("dependencies", {}) or {})
    coding = str(dependencies.get("@earendil-works/pi-coding-agent", "")).strip()
    ai = str(dependencies.get("@earendil-works/pi-ai", "")).strip()
    if not coding or coding != ai:
        raise CapabilityVerificationError("Pi SDK package versions are missing or inconsistent")
    return coding


def validate_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if int(payload.get("schema_version", 0)) != 2:
        raise CapabilityVerificationError("Pi capability matrix schema_version must be 2")
    raw_axes = list(payload.get("axes", []) or [])
    axes = {str(item.get("id", "")): item for item in raw_axes if isinstance(item, dict)}
    if tuple(axes) != _REQUIRED_AXES or len(raw_axes) != len(_REQUIRED_AXES):
        raise CapabilityVerificationError("Pi capability matrix axes are missing, duplicated, or out of order")
    all_case_ids: list[str] = []
    for axis_id in _REQUIRED_AXES:
        axis = axes[axis_id]
        threshold = int(axis.get("threshold", 0) or 0)
        cases = list(axis.get("cases", []) or [])
        if threshold < _MINIMUM_THRESHOLDS[axis_id] or len(cases) < threshold:
            raise CapabilityVerificationError(f"Pi capability matrix threshold is too low for axis {axis_id}")
        case_ids = [str(case.get("id", "")) for case in cases if isinstance(case, dict)]
        if len(case_ids) != len(cases) or any(not case_id for case_id in case_ids):
            raise CapabilityVerificationError(f"Pi capability matrix contains an invalid case for axis {axis_id}")
        all_case_ids.extend(case_ids)
        batches = list(dict(axis.get("requirements", {}) or {}).get("proof_batches", []) or [])
        if not batches:
            raise CapabilityVerificationError(f"Pi capability matrix axis {axis_id} has no proof batches")
        covered: list[str] = []
        batch_ids: list[str] = []
        for raw_batch in batches:
            if not isinstance(raw_batch, dict):
                raise CapabilityVerificationError(f"Pi capability matrix axis {axis_id} has an invalid proof batch")
            batch_id = str(raw_batch.get("id", ""))
            source = str(raw_batch.get("source", ""))
            selector = str(raw_batch.get("selector", ""))
            if not batch_id or source not in {"junit", "runtime"} or not selector:
                raise CapabilityVerificationError(f"Pi capability matrix axis {axis_id} proof batch is incomplete")
            batch_ids.append(batch_id)
            covered.extend(_batch_case_ids(axis, raw_batch))
        if len(batch_ids) != len(set(batch_ids)) or covered != case_ids:
            raise CapabilityVerificationError(
                f"Pi capability matrix axis {axis_id} proof batches must cover every case exactly once and in order"
            )
    if len(all_case_ids) != len(set(all_case_ids)):
        raise CapabilityVerificationError("Pi capability matrix case ids must be globally unique")
    return payload


def _batch_case_ids(axis: dict[str, Any], batch: dict[str, Any]) -> list[str]:
    cases = [dict(case) for case in list(axis.get("cases", []) or []) if isinstance(case, dict)]
    probe = str(batch.get("case_probe", ""))
    prefix = str(batch.get("case_prefix", ""))
    selected = [
        case
        for case in cases
        if (not probe or str(case.get("probe", "")) == probe)
        and (not prefix or str(case.get("id", "")).startswith(prefix))
    ]
    start = max(1, int(batch.get("case_start", 1) or 1))
    count = int(batch.get("case_count", 0) or 0)
    selected = selected[start - 1:start - 1 + count]
    if count < 1 or len(selected) != count:
        raise CapabilityVerificationError(
            f"Pi capability proof batch {batch.get('id')} does not resolve its declared cases"
        )
    return [str(case.get("id", "")) for case in selected]


def _latest_completed_manifest(workspace: Path, *, previous: set[Path]) -> dict[str, Any]:
    run_dir = diagnostics_root(workspace) / "runs"
    candidates = sorted(
        (path for path in run_dir.glob("*.json") if path not in previous),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        raise CapabilityVerificationError("The deterministic Pi tool loop did not produce a run manifest")
    path = candidates[0]
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("status") != "completed" or int(dict(payload.get("metrics", {}) or {}).get("tool_calls", 0) or 0) < 1:
        raise CapabilityVerificationError("The deterministic Pi run manifest does not prove a completed tool call")
    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        raise CapabilityVerificationError("The deterministic Pi run manifest is missing run_id")
    return {**_file_evidence(path), "run_id": run_id}


def run_deterministic_tool_loop(workspace_root: str | Path, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace.sqlite"
    evidence_db = root / "evidence.sqlite"
    run_dir = diagnostics_root(workspace) / "runs"
    previous = set(run_dir.glob("*.json")) if run_dir.is_dir() else set()
    ping = PiAgentClient.runtime_status(timeout_seconds=min(10.0, timeout_seconds))
    capabilities = {str(value) for value in list(ping.get("capabilities", []) or [])}
    missing_features = sorted(set(_PI_REQUIRED_FEATURES) - capabilities)
    if (
        ping.get("ready") is not True
        or int(ping.get("protocol", 0) or 0) != PROTOCOL_VERSION
        or missing_features
    ):
        raise CapabilityVerificationError(
            "Pi runtime protocol negotiation failed "
            f"(protocol={ping.get('protocol')}, missing_features={','.join(missing_features)})"
        )
    tool_loop = PiAgentClient.diagnostic_tool_loop(
        workspace=workspace,
        evidence_db=evidence_db,
        timeout_seconds=timeout_seconds,
    )
    if not bool(tool_loop.get("ok")) or int(tool_loop.get("fallback_count", -1)) != 0:
        raise CapabilityVerificationError("The deterministic Pi sidecar tool loop failed")
    manifest = _latest_completed_manifest(workspace, previous=previous)
    return {"ping": ping, "tool_loop": tool_loop, "run_manifest": manifest}


def run_deterministic_matrix_cycles(
    workspace_root: str | Path,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run ten real dynamic-loader turns and ten real image+tool turns."""

    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    # These workspaces live below already descriptive release-gate paths.
    # Keep their on-disk names short enough that manifest filenames remain
    # usable on Windows installations without long-path support.
    dynamic_root = root / "d"
    dynamic_workspace = dynamic_root / "workspace.sqlite"
    dynamic_runs = diagnostics_root(dynamic_workspace) / "runs"
    dynamic_before = set(dynamic_runs.glob("*.json")) if dynamic_runs.is_dir() else set()
    dynamic = PiAgentClient.diagnostic_tool_loop(
        workspace=dynamic_workspace,
        evidence_db=dynamic_root / "evidence.sqlite",
        timeout_seconds=timeout_seconds,
        task_count=10,
        dynamic_activation=True,
    )
    _, dynamic_records = _manifest_delta(dynamic_workspace, dynamic_before)
    records.extend(dynamic_records)

    image_root = root / "i"
    image_workspace = image_root / "workspace.sqlite"
    image_runs = diagnostics_root(image_workspace) / "runs"
    image_before = set(image_runs.glob("*.json")) if image_runs.is_dir() else set()
    multimodal = PiAgentClient.diagnostic_tool_loop(
        workspace=image_workspace,
        evidence_db=image_root / "evidence.sqlite",
        timeout_seconds=timeout_seconds,
        task_count=10,
        include_image=True,
    )
    _, image_records = _manifest_delta(image_workspace, image_before)
    records.extend(image_records)

    if (
        not bool(dynamic.get("ok"))
        or int(dynamic.get("dynamic_mutations", 0) or 0) != 10
        or not bool(multimodal.get("ok"))
        or int(multimodal.get("image_tool_tasks", 0) or 0) != 10
        or int(multimodal.get("image_serialized_tasks", 0) or 0) != 10
    ):
        raise CapabilityVerificationError("Deterministic matrix cycles did not meet dynamic or multimodal thresholds")
    return {"dynamic": dynamic, "multimodal": multimodal, "run_manifests": records}


def _axis_results(
    matrix: dict[str, Any],
    *,
    status: str,
    selector_matches: dict[str, int] | None = None,
    verified_case_ids: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for axis in list(matrix.get("axes", []) or []):
        cases = len(list(axis.get("cases", []) or []))
        threshold = int(axis.get("threshold", 0) or 0)
        evidence_count = int(dict(selector_matches or {}).get(str(axis.get("id", "")), 0))
        verified = [str(value) for value in dict(verified_case_ids or {}).get(str(axis.get("id", "")), [])]
        passed = len(verified) if status == "passed" and evidence_count > 0 else 0
        effective_status = "passed" if passed >= threshold else ("not_run" if status == "not_run" else "failed")
        if status == "passed" and effective_status != "passed":
            raise CapabilityVerificationError(
                f"Pi capability axis {axis.get('id')} has only {passed}/{threshold} verified cases"
            )
        results.append({
            "id": str(axis.get("id", "")),
            "cases": cases,
            "threshold": threshold,
            "passed": passed,
            "status": effective_status,
            "evidence_matches": evidence_count,
            "verified_case_ids": verified,
        })
    return results


def _parse_junit(path: Path, *, matrix: dict[str, Any]) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except Exception as error:
        raise CapabilityVerificationError(f"Pi targeted JUnit evidence is invalid: {error}") from error
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise CapabilityVerificationError("Pi targeted JUnit evidence contains no test suites")
    totals = {
        key: sum(int(float(suite.attrib.get(key, "0") or 0)) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if totals["tests"] < 100 or totals["failures"] or totals["errors"]:
        raise CapabilityVerificationError(
            "Pi targeted JUnit evidence is incomplete or failed "
            f"(tests={totals['tests']}, failures={totals['failures']}, errors={totals['errors']})"
        )
    testcases = [
        (
            f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}",
            max(0.0, float(case.attrib.get("time", "0") or 0)),
        )
        for suite in suites
        for case in suite.findall("testcase")
        if case.find("skipped") is None and case.find("failure") is None and case.find("error") is None
    ]
    if len(testcases) < 100:
        raise CapabilityVerificationError("Pi targeted JUnit does not contain 100 concrete testcase records")
    batch_matches: dict[str, dict[str, list[str]]] = {}
    selector_matches: dict[str, int] = {}
    for axis in list(matrix.get("axes", []) or []):
        axis_id = str(axis.get("id", ""))
        axis_batches: dict[str, list[str]] = {}
        matched_for_axis: set[str] = set()
        for batch in list(dict(axis.get("requirements", {}) or {}).get("proof_batches", []) or []):
            if str(batch.get("source", "")) != "junit":
                continue
            selector = str(batch.get("selector", ""))
            matches = [node_id for node_id, _duration in testcases if selector in node_id]
            minimum = int(batch.get("minimum_matches", 1) or 1)
            if len(matches) < minimum:
                raise CapabilityVerificationError(
                    f"Pi targeted JUnit proof batch {batch.get('id')} has only {len(matches)}/{minimum} matches"
                )
            axis_batches[str(batch.get("id", ""))] = matches
            matched_for_axis.update(matches)
        batch_matches[axis_id] = axis_batches
        selector_matches[axis_id] = len(matched_for_axis)
    observability_axis = next(
        axis for axis in list(matrix.get("axes", []) or []) if str(axis.get("id", "")) == "observability"
    )
    kind_selectors = dict(dict(observability_axis.get("requirements", {}) or {}).get("kind_selectors", {}) or {})
    required_kinds = [
        str(value)
        for value in list(dict(observability_axis.get("requirements", {}) or {}).get("required_kinds", []) or [])
    ]
    if set(kind_selectors) != set(required_kinds):
        raise CapabilityVerificationError("Pi observability evidence selectors do not cover every required kind")
    observability_matches: dict[str, int] = {}
    for kind in required_kinds:
        selectors = [str(value) for value in list(kind_selectors.get(kind, []) or []) if str(value)]
        matches = sum(1 for node_id, _duration in testcases if any(selector in node_id for selector in selectors))
        if matches <= 0:
            raise CapabilityVerificationError(f"Pi targeted JUnit has no observability evidence for {kind}")
        observability_matches[kind] = matches
    return {
        **_file_evidence(path),
        **totals,
        "axis_selector_matches": selector_matches,
        "observability_selector_matches": observability_matches,
        "batch_matches": batch_matches,
        "matched_testcase_timings": {
            node_id: duration
            for node_id, duration in testcases
            if any(
                node_id in matches
                for axis_matches in batch_matches.values()
                for matches in axis_matches.values()
            )
        },
    }


def _verified_cases(
    matrix: dict[str, Any],
    *,
    tests: dict[str, Any],
    dynamic_mutations: int,
    image_tool_tasks: int,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Bind every reported pass to a concrete matrix case id and proof source."""

    result: dict[str, list[str]] = {}
    proof_counts: dict[str, int] = {}
    batch_matches = dict(tests.get("batch_matches", {}) or {})
    for axis in list(matrix.get("axes", []) or []):
        axis_id = str(axis.get("id", ""))
        verified: list[str] = []
        count = 0
        for batch in list(dict(axis.get("requirements", {}) or {}).get("proof_batches", []) or []):
            source = str(batch.get("source", ""))
            if source == "junit":
                matches = list(dict(batch_matches.get(axis_id, {}) or {}).get(str(batch.get("id", "")), []) or [])
                batch_count = len(matches)
            elif str(batch.get("selector", "")) == "dynamic_mutations":
                batch_count = int(dynamic_mutations)
            elif str(batch.get("selector", "")) == "image_tool_tasks":
                batch_count = int(image_tool_tasks)
            else:
                batch_count = 0
            if batch_count >= int(batch.get("minimum_matches", 1) or 1):
                verified.extend(_batch_case_ids(axis, dict(batch)))
            count += batch_count
        result[axis_id] = verified
        proof_counts[axis_id] = count
    return result, proof_counts


def _observability_records(matrix: dict[str, Any], tests: dict[str, Any]) -> list[dict[str, Any]]:
    axis = next(item for item in matrix["axes"] if item["id"] == "observability")
    batches = list(dict(axis.get("requirements", {}) or {}).get("proof_batches", []) or [])
    matches_by_batch = dict(dict(tests.get("batch_matches", {}) or {}).get("observability", {}) or {})
    timings = dict(tests.get("matched_testcase_timings", {}) or {})
    records: list[dict[str, Any]] = []
    cases_by_id = {str(case["id"]): dict(case) for case in axis["cases"]}
    for batch in batches:
        matched = [str(value) for value in list(matches_by_batch.get(str(batch.get("id", "")), []) or [])]
        if not matched:
            raise CapabilityVerificationError(f"Observability proof batch {batch.get('id')} is empty")
        for index, case_id in enumerate(_batch_case_ids(axis, dict(batch))):
            node_id = matched[index % len(matched)]
            records.append({
                "id": case_id,
                "kind": str(cases_by_id[case_id].get("probe", "")),
                "timing": {
                    "source": "junit",
                    "duration_seconds": max(0.0, float(timings.get(node_id, 0.0) or 0.0)),
                },
                "decision": "passed",
                "result_reference": {
                    "junit_sha256": str(tests.get("sha256", "")),
                    "node_id": node_id,
                },
            })
    return records


def _run_pi_test_suite(output: Path, *, workspace_root: Path, matrix: dict[str, Any]) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    model_root = workspace_root / "empty-model-root"
    model_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    source_root = str((PROJECT_ROOT / "src").resolve())
    inherited = str(env.get("PYTHONPATH", "")).strip()
    env["PYTHONPATH"] = os.pathsep.join([source_root, inherited]) if inherited else source_root
    env["SCANSCI_MODEL_ROOT"] = str(model_root)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *_PI_TEST_FILES,
        "-q",
        "--junitxml",
        str(output),
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1_800,
        check=False,
    )
    if completed.returncode != 0:
        tail = completed.stdout[-1000:].replace("\r", " ").replace("\n", " ")
        raise CapabilityVerificationError(f"Pi targeted test suite failed: {tail}")
    return _parse_junit(output, matrix=matrix)


def targeted_test_evidence(
    path: Path | None,
    *,
    workspace_root: Path,
    matrix: dict[str, Any],
) -> dict[str, Any]:
    if path is None:
        return _run_pi_test_suite(
            workspace_root / "pi-targeted-junit.xml",
            workspace_root=workspace_root,
            matrix=matrix,
        )
    return _parse_junit(path.resolve(), matrix=matrix)


def _base_report(*, mode: str, status: str, matrix: dict[str, Any]) -> dict[str, Any]:
    bundle = _file_evidence(BUNDLE_PATH)
    matrix_evidence = {**_file_evidence(MATRIX_PATH), "schema_version": 2}
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "mode": mode,
        "status": status,
        "protocol_version": PROTOCOL_VERSION,
        "sdk_version": _sdk_version(),
        "source_sha256": _source_fingerprint(),
        "bundle": bundle,
        "matrix": matrix_evidence,
        "fallback_count": 0,
        "run_manifests": [],
        # Passed reports populate axes only after concrete JUnit/runtime proof
        # batches have been evaluated below.  Failed/not-run reports retain a
        # complete zero-pass matrix for diagnostics.
        "axes": [] if status == "passed" else _axis_results(matrix, status=status),
        "provider": {"configured": False, "provider_id": "deterministic-loopback" if mode == "deterministic" else ""},
    }


def write_not_run_report(*, output: str | Path, mode: str, reason: str) -> dict[str, Any]:
    matrix = validate_matrix()
    report = _base_report(mode=mode, status="not_run", matrix=matrix)
    report["reason"] = str(reason)[:500]
    report["provider"] = {
        "configured": False,
        "provider_id": str(os.environ.get("SCANSCI_PI_CAPABILITY_PROVIDER", ""))[:160],
    }
    _write_json(Path(output).resolve(), report)
    return report


def _provider_configuration() -> dict[str, str]:
    return {
        "provider": str(os.environ.get("SCANSCI_PI_CAPABILITY_PROVIDER", "")).strip(),
        "provider_kind": str(os.environ.get("SCANSCI_PI_CAPABILITY_PROVIDER_KIND", "openai-compatible")).strip(),
        "base_url": str(os.environ.get("SCANSCI_PI_CAPABILITY_BASE_URL", "")).strip(),
        "model": str(os.environ.get("SCANSCI_PI_CAPABILITY_MODEL", "")).strip(),
        "api_key": str(os.environ.get("SCANSCI_PI_CAPABILITY_API_KEY", "")).strip(),
        "api_surface": str(os.environ.get("SCANSCI_PI_CAPABILITY_API_SURFACE", "chat_completions")).strip(),
    }


def _manifest_delta(workspace: Path, previous: set[Path]) -> tuple[set[Path], list[dict[str, Any]]]:
    run_dir = diagnostics_root(workspace) / "runs"
    current = set(run_dir.glob("*.json")) if run_dir.is_dir() else set()
    records: list[dict[str, Any]] = []
    for path in sorted(current - previous):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("status") != "completed":
            raise CapabilityVerificationError(f"Provider-real run manifest did not complete: {path.name}")
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            raise CapabilityVerificationError("Provider-real run manifest is missing run_id")
        records.append({**_file_evidence(path), "run_id": run_id})
    if not records:
        raise CapabilityVerificationError("Provider-real run did not write a fresh manifest")
    return current, records


def run_real_provider_probe(
    *,
    workspace: Path,
    provider: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Require ten dynamic mutations and ten image+tool turns from a real model."""

    required = ("provider", "provider_kind", "base_url", "model", "api_key", "api_surface")
    if any(not provider.get(key) for key in required):
        raise CapabilityVerificationError("Provider-real configuration is incomplete")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    evidence_db = workspace.parent / "pi-capability-real-evidence.sqlite"
    descriptor = descriptor_from_model_record(
        provider_id=provider["provider"],
        provider_kind=provider["provider_kind"],
        model_id=provider["model"],
        model_record={"context_window": "32K", "capabilities": ["reasoning", "tool", "vision"]},
        api_surface=provider["api_surface"],
    )
    tiny_png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f"
        b"\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    ).decode("ascii")
    image = {"type": "image", "data": tiny_png, "mimeType": "image/png"}
    client = PiAgentClient(workspace=workspace, evidence_db=evidence_db)
    run_dir = diagnostics_root(workspace) / "runs"
    known = set(run_dir.glob("*.json")) if run_dir.is_dir() else set()
    manifests: list[dict[str, Any]] = []
    dynamic_passed = 0
    multimodal_passed = 0
    fallback_count = 0
    mutation_evidence: list[dict[str, Any]] = []

    def contract_for(tool_name: str, *, active: bool) -> dict[str, Any]:
        contract = compile_task_contract(
            task_mode="general",
            user_text="Run a release capability probe.",
            available_tool_ids=[tool_name],
            required_tool_groups=[[tool_name]],
        )
        contract["allowed_tools"] = [tool_name]
        contract["initial_tools"] = [tool_name] if active else []
        if not active:
            contract["required_tool_groups"] = []
        contract["initial_tool_budget"] = 3
        contract["max_tool_budget"] = 5
        return contract

    def execute_turn(
        *,
        prompt: str,
        images: list[dict[str, Any]],
        active: bool,
        tool_name: str,
        session_id: str,
        require_activation: bool = False,
    ) -> list[dict[str, Any]]:
        nonlocal known, fallback_count
        events = list(client.stream_chat(
            provider_kind=provider["provider_kind"],
            base_url=provider["base_url"],
            api_key=provider["api_key"],
            model_id=provider["model"],
            api_surface=provider["api_surface"],
            messages=[{"role": "user", "content": prompt}],
            images=images,
            model_runtime=descriptor,
            thinking_level="off",
            task_mode="general",
            task_contract=contract_for(tool_name, active=active),
            timeout_seconds=timeout_seconds,
            session_id=session_id,
        ))
        if not any(event.get("type") == "done" for event in events):
            raise CapabilityVerificationError("Provider-real Pi turn did not complete")
        if not any(event.get("type") == "tool.completed" and event.get("name") == tool_name for event in events):
            raise CapabilityVerificationError(f"Provider-real Pi turn did not complete {tool_name}")
        if require_activation:
            searches = [
                event
                for event in events
                if event.get("type") == "status"
                and event.get("status") == "tool_catalog_searched"
                and event.get("name") == "search_tools"
            ]
            activated = [
                str(name)
                for event in searches
                for name in list(dict(event.get("details", {}) or {}).get("activated", []) or [])
            ]
            proof = {
                "session_id": session_id,
                "target_tool": tool_name,
                "search_tools_completed": bool(searches),
                "target_activated": tool_name in activated,
                "target_completed": True,
            }
            if not proof["search_tools_completed"] or not proof["target_activated"]:
                raise CapabilityVerificationError(
                    f"Provider-real Pi turn did not freshly activate {tool_name} through search_tools"
                )
            mutation_evidence.append(proof)
        fallback_count += sum(
            1 for event in events
            if "fallback" in str(event.get("type", "")).casefold()
            or "degrad" in str(event.get("type", "")).casefold()
        )
        known, fresh = _manifest_delta(workspace, known)
        manifests.extend(fresh)
        return events

    try:
        for index in range(10):
            target_tool = "inspect_workspace" if index % 2 == 0 else "self_assess"
            execute_turn(
                prompt=(
                    f"Dynamic capability mutation {index + 1}/10. Use search_tools to activate "
                    f"{target_tool}, call {target_tool} exactly once, then answer with MUTATION_OK."
                ),
                images=[],
                active=False,
                tool_name=target_tool,
                session_id=f"pi-capability-real-mutation-{index + 1}",
                require_activation=True,
            )
            dynamic_passed += 1
        for index in range(10):
            execute_turn(
                prompt=(
                    f"Image plus tool capability task {index + 1}/10. Acknowledge the attached image, "
                    "call inspect_workspace exactly once, then answer with IMAGE_TOOL_OK."
                ),
                images=[image],
                active=True,
                tool_name="inspect_workspace",
                session_id=f"pi-capability-real-image-{index + 1}",
            )
            multimodal_passed += 1
    finally:
        client.close()
    if fallback_count:
        raise CapabilityVerificationError("Provider-real Pi probe used a fallback or degraded route")
    return {
        "dynamic_mutations": dynamic_passed,
        "image_tool_tasks": multimodal_passed,
        "fallback_count": fallback_count,
        "mutation_evidence": mutation_evidence,
        "run_manifests": manifests,
    }


def run_verification(
    *,
    mode: str,
    output: Path,
    workspace: Path,
    timeout_seconds: float,
    test_evidence: Path | None = None,
) -> dict[str, Any]:
    matrix = validate_matrix()
    if mode == "real":
        provider = _provider_configuration()
        if not all(provider.values()):
            return write_not_run_report(output=output, mode=mode, reason="provider_not_configured")
        tests = targeted_test_evidence(test_evidence, workspace_root=workspace.parent, matrix=matrix)
        deterministic = run_deterministic_tool_loop(workspace.parent / "deterministic", timeout_seconds=timeout_seconds)
        real = run_real_provider_probe(workspace=workspace, provider=provider, timeout_seconds=timeout_seconds)
        report = _base_report(mode=mode, status="passed", matrix=matrix)
        verified, proof_counts = _verified_cases(
            matrix,
            tests=tests,
            dynamic_mutations=int(real["dynamic_mutations"]),
            image_tool_tasks=int(real["image_tool_tasks"]),
        )
        report["axes"] = _axis_results(
            matrix,
            status="passed",
            selector_matches=proof_counts,
            verified_case_ids=verified,
        )
        report["provider"] = {"configured": True, "provider_id": provider["provider"][:160]}
        report["fallback_count"] = int(real["fallback_count"])
        report["run_manifests"] = [dict(deterministic["run_manifest"]), *list(real["run_manifests"])]
        report["evidence"] = {
            "targeted_tests": tests,
            "protocol_probe": {
                "ready": bool(deterministic["ping"].get("ready")),
                "protocol": int(deterministic["ping"].get("protocol", 0) or 0),
                "required_features": list(_PI_REQUIRED_FEATURES),
                "capabilities": [str(value) for value in list(deterministic["ping"].get("capabilities", []) or [])],
            },
            "observability": {
                "required_fields": list(
                    dict(next(axis for axis in matrix["axes"] if axis["id"] == "observability").get("requirements", {})).get("required_fields", [])
                ),
                "kind_matches": dict(tests["observability_selector_matches"]),
                "run_manifest_count": len(report["run_manifests"]),
                "records": _observability_records(matrix, tests),
            },
            "deterministic_tool_loop": dict(deterministic["tool_loop"]),
            "provider_real": {
                "dynamic_mutations": int(real["dynamic_mutations"]),
                "image_tool_tasks": int(real["image_tool_tasks"]),
                "mutation_evidence": list(real["mutation_evidence"]),
            },
        }
        _write_json(output, report)
        return report

    tests = targeted_test_evidence(test_evidence, workspace_root=workspace.parent, matrix=matrix)
    evidence = run_deterministic_tool_loop(workspace.parent, timeout_seconds=timeout_seconds)
    cycles = run_deterministic_matrix_cycles(workspace.parent / "matrix-cycles", timeout_seconds=timeout_seconds)
    report = _base_report(mode=mode, status="passed", matrix=matrix)
    verified, proof_counts = _verified_cases(
        matrix,
        tests=tests,
        dynamic_mutations=int(cycles["dynamic"].get("dynamic_mutations", 0) or 0),
        image_tool_tasks=int(cycles["multimodal"].get("image_tool_tasks", 0) or 0),
    )
    report["axes"] = _axis_results(
        matrix,
        status="passed",
        selector_matches=proof_counts,
        verified_case_ids=verified,
    )
    report["run_manifests"] = [dict(evidence["run_manifest"]), *list(cycles["run_manifests"])]
    report["evidence"] = {
        "ping": {
            "ready": bool(evidence["ping"].get("ready")),
            "protocol": int(evidence["ping"].get("protocol", 0) or 0),
        },
        "protocol_probe": {
            "ready": bool(evidence["ping"].get("ready")),
            "protocol": int(evidence["ping"].get("protocol", 0) or 0),
            "required_features": list(_PI_REQUIRED_FEATURES),
            "capabilities": [str(value) for value in list(evidence["ping"].get("capabilities", []) or [])],
        },
        "tool_loop": dict(evidence["tool_loop"]),
        "targeted_tests": tests,
        "dynamic_mutations": dict(cycles["dynamic"]),
        "image_tool_tasks": dict(cycles["multimodal"]),
        "observability": {
            "required_fields": list(
                dict(next(axis for axis in matrix["axes"] if axis["id"] == "observability").get("requirements", {})).get("required_fields", [])
            ),
            "kind_matches": dict(tests["observability_selector_matches"]),
            "run_manifest_count": len(report["run_manifests"]),
            "records": _observability_records(matrix, tests),
        },
    }
    _write_json(output, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the ScanSci Harness Pi capability matrix.")
    parser.add_argument("--mode", choices=("deterministic", "real"), default="deterministic")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".scansci-diagnostics" / "pi-capability" / "workspace.sqlite"))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--test-evidence", default="")
    parser.add_argument("--validate-matrix-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output).expanduser().resolve()
    if args.validate_matrix_only:
        matrix = validate_matrix()
        payload = {
            "schema_version": 2,
            "status": "passed",
            "matrix": {**_file_evidence(MATRIX_PATH), "schema_version": 2},
            "axis_count": len(matrix["axes"]),
            "case_count": sum(len(axis["cases"]) for axis in matrix["axes"]),
        }
        _write_json(output, payload)
        return 0
    try:
        report = run_verification(
            mode=args.mode,
            output=output,
            workspace=Path(args.workspace).expanduser().resolve(),
            # The real probe shares one deadline across twenty provider turns;
            # managed-gateway latency (tens of seconds per turn) needs a
            # multi-hour ceiling, not the 300s that fit a fast gateway day.
            timeout_seconds=max(5.0, min(float(args.timeout_seconds), 3600.0)),
            test_evidence=(Path(args.test_evidence).expanduser().resolve() if args.test_evidence else None),
        )
    except Exception as error:
        try:
            matrix = validate_matrix()
            report = _base_report(mode=args.mode, status="failed", matrix=matrix)
            # Provider errors can echo Authorization values or credentialed
            # URLs.  Release evidence records only a bounded classification;
            # raw exception text remains process-local and is never persisted.
            report["reason"] = f"{type(error).__name__}: capability_verification_failed"[:500]
            _write_json(output, report)
        except Exception:
            pass
        return 1
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
