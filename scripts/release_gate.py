"""Run ScanSci's staged source and Windows release acceptance gates.

The gate deliberately separates automated evidence from desktop visual evidence.
It never reads or prints provider secrets, never reuses an anonymous ``dist``
directory, and can resume an already-built release without rebuilding it.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from queue import Empty, Queue
import re
import shutil
import socket
import subprocess
import sys
from threading import Thread
import time
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
for import_root in (PROJECT_ROOT, SOURCE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from cryptography.hazmat.primitives import serialization

from scansci_html.manifest_trust import decode_public_key, verify_manifest_signature
from scansci_html.release_transport import open_release_url
from scripts.sign_release_manifest import load_private_key

CORE_RUNTIME_FORBIDDEN_SEGMENTS = frozenset({
    "torch",
    "transformers",
    "sentence_transformers",
    "tensorflow",
    "models--",
    # The public core keeps the Pi sidecar and LaTeX engine as separately
    # managed components.  Catch a packaging regression even when the files
    # are placed below an otherwise innocuous directory.
    "node.exe",
    "tectonic.exe",
})
MAX_RUNTIME_COMPONENT_BYTES = 16 * 1024**3
FROZEN_V031_SCOPE_SHA256 = "a253baca5ae32f0edaba4baf5b2a77e1023d84db714014b5032768c8fb16acc4"


class GateFailure(RuntimeError):
    """A mandatory acceptance gate failed."""


class GatePending(RuntimeError):
    """Automated gates passed but human desktop evidence is still missing."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _emit_console(
    value: object = "",
    *,
    end: str = "\n",
    file: Any | None = None,
    encoding: str | None = None,
) -> None:
    """Write diagnostic output without letting a Windows console codepage abort a gate.

    Subprocess output and release titles may contain Unicode outside a legacy
    GBK console. The complete UTF-8 log remains the source of truth; the
    console receives an escaped representation for characters it cannot show.
    """

    stream = file or sys.stdout
    target_encoding = encoding or getattr(stream, "encoding", None) or "utf-8"
    text = str(value)
    try:
        safe_text = text.encode(target_encoding, errors="backslashreplace").decode(target_encoding, errors="replace")
    except LookupError:
        safe_text = text
    print(safe_text, end=end, file=stream)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint() -> str:
    """Hash release-relevant source without build outputs or user data."""

    files: set[Path] = set()
    # The desktop client and its managed Worker are released as one service
    # contract. A gateway change must invalidate an otherwise reusable
    # desktop release candidate.
    for directory in (
        "src", "scripts", "tests", "config", "installer", "services", "pi-runtime", "bench", "docs",
    ):
        root = PROJECT_ROOT / directory
        if root.is_dir():
            files.update(
                path
                for path in root.rglob("*")
                if path.is_file()
                and not (
                    directory == "pi-runtime"
                    and any(part in {"dist", "node_modules"} for part in path.relative_to(root).parts)
                )
            )
    for name in (
        "AGENTS.md",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "package-lock.json",
        "docs/agent-startup.zh.md",
        "docs/project-governance.zh.md",
        "docs/desktop-packaging.zh.md",
        "docs/release-workflow.zh.md",
        "docs/research-agent-architecture.zh.md",
        "docs/agent-harness-p0-p2.zh.md",
    ):
        path = PROJECT_ROOT / name
        if path.is_file():
            files.add(path)
    digest = hashlib.sha256()
    for path in sorted(
        (path for path in files if "__pycache__" not in path.parts and path.suffix != ".pyc"),
        key=lambda item: item.relative_to(PROJECT_ROOT).as_posix(),
    ):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _required_int(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise GateFailure(f"{label} must be an integer")
    return value


_PI_AXIS_THRESHOLDS = {
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

_PI_REQUIRED_RUNTIME_FEATURES = {
    "deferred_mcp_v2",
    "dynamic_tools",
    "lifecycle_hooks_v1",
    "mcp_effect_audit_v1",
    "mcp_run_cache_v1",
    "multimodal_turns",
    "parallel_tool_dispatch",
    "progressive_skills",
    "scientific_subagents_v1",
}


def _pi_batch_case_ids(axis: dict[str, Any], batch: dict[str, Any]) -> list[str]:
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
        raise GateFailure(f"Pi capability matrix proof batch {batch.get('id')} has invalid case coverage")
    return [str(case.get("id", "")) for case in selected]


def _strict_object(
    raw: object,
    *,
    label: str,
    required: set[str],
    allowed: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GateFailure(f"Pi capability report {label} must be an object")
    payload = dict(raw)
    allowed_keys = set(allowed if allowed is not None else required)
    missing = required - set(payload)
    unknown = set(payload) - allowed_keys
    if missing:
        raise GateFailure(f"Pi capability report {label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise GateFailure(f"Pi capability report {label} has unknown fields: {', '.join(sorted(unknown))}")
    return payload


def _validated_file_evidence(
    raw: object,
    *,
    label: str,
    extra_required: set[str] | None = None,
    extra_allowed: set[str] | None = None,
) -> Path:
    extra_required = set(extra_required or set())
    extra_allowed = set(extra_allowed or extra_required)
    payload = _strict_object(
        raw,
        label=f"{label} evidence",
        required={"path", "sha256", "bytes", *extra_required},
        allowed={"path", "sha256", "bytes", *extra_allowed},
    )
    path = Path(str(payload.get("path", ""))).expanduser().resolve()
    digest = str(payload.get("sha256", "")).strip().casefold()
    if not path.is_file():
        raise GateFailure(f"Pi capability report {label} evidence is missing: {path}")
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or _sha256(path).casefold() != digest:
        raise GateFailure(f"Pi capability report {label} SHA256 does not match its file")
    if int(payload.get("bytes", -1)) != path.stat().st_size:
        raise GateFailure(f"Pi capability report {label} byte size does not match its file")
    return path


def _validate_tool_loop(raw: object, *, label: str, require_mutations: bool = False) -> dict[str, Any]:
    keys = {
        "ok", "tool_calls", "done", "marker_seen", "fallback_count", "provider_requests",
        "dynamic_mutations", "mutation_evidence", "image_tool_tasks", "duration_seconds",
        "image_serialized_tasks",
    }
    payload = _strict_object(raw, label=label, required=keys)
    if (
        payload.get("ok") is not True
        or payload.get("done") is not True
        or payload.get("marker_seen") is not True
        or int(payload.get("tool_calls", 0) or 0) < 1
        or int(payload.get("provider_requests", 0) or 0) < 1
        or int(payload.get("fallback_count", -1)) != 0
    ):
        raise GateFailure(f"Pi capability report {label} did not prove a complete zero-fallback tool loop")
    proofs = list(payload.get("mutation_evidence", []) or [])
    if require_mutations and (
        int(payload.get("dynamic_mutations", 0) or 0) != 10 or len(proofs) != 10
    ):
        raise GateFailure(f"Pi capability report {label} did not prove ten dynamic mutations")
    for proof in proofs:
        record = _strict_object(
            proof,
            label=f"{label} mutation",
            required={
                "turn", "session_id", "target_tool", "search_tools_completed",
                "target_activated", "target_completed",
            },
        )
        if not all(record.get(key) is True for key in ("search_tools_completed", "target_activated", "target_completed")):
            raise GateFailure(f"Pi capability report {label} contains an unverified mutation")
    if require_mutations and len({str(item.get("session_id", "")) for item in proofs}) != 10:
        raise GateFailure(f"Pi capability report {label} mutation sessions are not isolated")
    return payload


def validate_pi_capability_report(
    report_path: Path,
    *,
    expected_source_sha256: str,
    expected_bundle_path: Path,
    required_mode: str,
) -> dict[str, Any]:
    """Strictly validate capability evidence; file existence never suffices."""

    try:
        report = _read_json(report_path)
    except Exception as error:
        raise GateFailure(f"Pi capability report is not valid JSON: {error}") from error
    report = _strict_object(
        report,
        label="root",
        required={
            "schema_version", "report_kind", "mode", "status", "protocol_version", "sdk_version",
            "source_sha256", "bundle", "matrix", "fallback_count", "run_manifests", "axes",
            "provider", "evidence",
        },
        allowed={
            "schema_version", "report_kind", "mode", "status", "reason", "protocol_version",
            "sdk_version", "source_sha256", "bundle", "matrix", "fallback_count", "run_manifests",
            "axes", "provider", "evidence",
        },
    )
    if int(report.get("schema_version", 0)) != 2:
        raise GateFailure("Pi capability report schema_version must be 2")
    if str(report.get("report_kind", "")) != "scansci.pi-capabilities":
        raise GateFailure("Pi capability report_kind is invalid")
    if str(report.get("mode", "")) != required_mode:
        raise GateFailure(f"Pi capability report mode must be {required_mode}")
    status = str(report.get("status", ""))
    if status != "passed":
        reason = str(report.get("reason", "")).strip()
        raise GateFailure(f"Pi capability report is {status or 'invalid'}{f': {reason}' if reason else ''}")
    if int(report.get("protocol_version", 0) or 0) != 7:
        raise GateFailure("Pi capability report protocol_version must be 7")
    if not str(report.get("sdk_version", "")).strip():
        raise GateFailure("Pi capability report SDK version is missing")
    source = str(report.get("source_sha256", "")).strip().casefold()
    if source != str(expected_source_sha256).strip().casefold():
        raise GateFailure("Pi capability report source SHA256 does not match the current gate")
    if int(report.get("fallback_count", -1)) != 0:
        raise GateFailure("Pi capability report fallback_count must be zero")

    bundle_path = _validated_file_evidence(report.get("bundle"), label="bundle")
    if bundle_path != expected_bundle_path.resolve():
        raise GateFailure("Pi capability report bundle path does not match the current worktree")
    matrix_record = _strict_object(
        report.get("matrix"),
        label="matrix evidence",
        required={"path", "sha256", "bytes", "schema_version"},
    )
    matrix_path = _validated_file_evidence(
        matrix_record,
        label="matrix",
        extra_required={"schema_version"},
    )
    if int(dict(report.get("matrix", {}) or {}).get("schema_version", 0)) != 2:
        raise GateFailure("Pi capability report matrix schema_version must be 2")
    expected_matrix = (PROJECT_ROOT / "bench" / "pi_capability_tasks.json").resolve()
    if matrix_path != expected_matrix and expected_bundle_path.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise GateFailure("Pi capability report matrix path does not match the current worktree")

    try:
        matrix_payload = _read_json(matrix_path)
    except Exception as error:
        raise GateFailure(f"Pi capability report matrix is invalid: {error}") from error
    matrix_axes = list(matrix_payload.get("axes", []) or [])
    if int(matrix_payload.get("schema_version", 0) or 0) != 2 or len(matrix_axes) != len(_PI_AXIS_THRESHOLDS):
        raise GateFailure("Pi capability report matrix is incomplete")
    expected_axes = {str(axis.get("id", "")): dict(axis) for axis in matrix_axes if isinstance(axis, dict)}
    if list(expected_axes) != list(_PI_AXIS_THRESHOLDS):
        raise GateFailure("Pi capability report matrix axes are invalid")
    for axis_id, matrix_axis in expected_axes.items():
        matrix_case_ids = [str(case.get("id", "")) for case in list(matrix_axis.get("cases", []) or [])]
        batches = list(dict(matrix_axis.get("requirements", {}) or {}).get("proof_batches", []) or [])
        covered = [
            case_id
            for batch in batches
            for case_id in _pi_batch_case_ids(matrix_axis, dict(batch))
        ]
        if not batches or covered != matrix_case_ids:
            raise GateFailure(f"Pi capability report matrix proof batches do not cover axis {axis_id}")

    axes = list(report.get("axes", []) or [])
    if len(axes) != len(_PI_AXIS_THRESHOLDS):
        raise GateFailure("Pi capability report axes are missing or duplicated")
    axis_ids = [str(axis.get("id", "")) for axis in axes if isinstance(axis, dict)]
    if axis_ids != list(_PI_AXIS_THRESHOLDS):
        raise GateFailure("Pi capability report axes are missing, duplicated, or out of order")
    reported_axes: dict[str, dict[str, Any]] = {}
    for axis in axes:
        axis = _strict_object(
            axis,
            label="axis",
            required={
                "id", "cases", "threshold", "passed", "status", "evidence_matches",
                "verified_case_ids",
            },
        )
        axis_id = str(axis["id"])
        reported_axes[axis_id] = axis
        cases = int(axis.get("cases", 0) or 0)
        threshold = int(axis.get("threshold", 0) or 0)
        passed = int(axis.get("passed", 0) or 0)
        matrix_axis = expected_axes[axis_id]
        matrix_cases = [
            str(case.get("id", ""))
            for case in list(matrix_axis.get("cases", []) or [])
            if isinstance(case, dict)
        ]
        verified_case_ids = [str(value) for value in list(axis.get("verified_case_ids", []) or [])]
        if (
            threshold != int(matrix_axis.get("threshold", 0) or 0)
            or threshold < _PI_AXIS_THRESHOLDS[axis_id]
            or cases != len(matrix_cases)
            or len(verified_case_ids) != len(set(verified_case_ids))
            or not set(verified_case_ids).issubset(set(matrix_cases))
            or passed != len(verified_case_ids)
            or passed < threshold
            or int(axis.get("evidence_matches", 0) or 0) < 1
            or str(axis.get("status", "")) != "passed"
        ):
            raise GateFailure(f"Pi capability report axis {axis_id} did not match the hashed matrix threshold and cases")

    manifests = list(report.get("run_manifests", []) or [])
    if not manifests:
        raise GateFailure("Pi capability report run manifest references are missing")
    report_root = report_path.resolve().parent
    seen_manifest_paths: set[Path] = set()
    seen_run_ids: set[str] = set()
    for raw in manifests:
        manifest_record = _strict_object(
            raw,
            label="manifest evidence",
            required={"path", "sha256", "bytes", "run_id"},
        )
        manifest_path = _validated_file_evidence(
            manifest_record,
            label="manifest",
            extra_required={"run_id"},
        )
        if not manifest_path.is_relative_to(report_root):
            raise GateFailure("Pi capability report manifest path is outside the current diagnostics directory")
        manifest_payload = _read_json(manifest_path)
        run_id = str(manifest_record.get("run_id", "")).strip()
        if (
            not run_id
            or str(manifest_payload.get("run_id", "")).strip() != run_id
            or manifest_payload.get("status") != "completed"
            or int(dict(manifest_payload.get("metrics", {}) or {}).get("tool_calls", 0) or 0) < 1
        ):
            raise GateFailure("Pi capability report manifest run_id/status/tool call evidence is invalid")
        if manifest_path in seen_manifest_paths or run_id in seen_run_ids:
            raise GateFailure("Pi capability report manifest references are duplicated")
        seen_manifest_paths.add(manifest_path)
        seen_run_ids.add(run_id)
    provider = _strict_object(
        report.get("provider"),
        label="provider",
        required={"configured", "provider_id"},
    )
    if not str(provider.get("provider_id", "")).strip():
        raise GateFailure("Pi capability report provider identity is missing")
    if required_mode == "real" and provider.get("configured") is not True:
        raise GateFailure("Pi capability report provider-real run is not configured")
    deterministic_keys = {
        "ping", "protocol_probe", "tool_loop", "targeted_tests", "dynamic_mutations",
        "image_tool_tasks", "observability",
    }
    real_keys = {
        "targeted_tests", "protocol_probe", "observability", "deterministic_tool_loop", "provider_real",
    }
    evidence = _strict_object(
        report.get("evidence"),
        label="evidence",
        required=deterministic_keys if required_mode == "deterministic" else real_keys,
    )
    targeted = _strict_object(
        evidence.get("targeted_tests"),
        label="targeted tests",
        required={
            "path", "sha256", "bytes", "tests", "failures", "errors", "skipped",
            "axis_selector_matches", "observability_selector_matches", "batch_matches",
            "matched_testcase_timings",
        },
    )
    targeted_path = _validated_file_evidence(
        targeted,
        label="targeted tests",
        extra_required={
            "tests", "failures", "errors", "skipped", "axis_selector_matches",
            "observability_selector_matches", "batch_matches", "matched_testcase_timings",
        },
    )
    if not targeted_path.is_relative_to(report_root):
        raise GateFailure("Pi capability report targeted test path is outside the current diagnostics directory")
    try:
        junit_root = ET.parse(targeted_path).getroot()
    except Exception as error:
        raise GateFailure(f"Pi capability report targeted JUnit is invalid: {error}") from error
    junit_suites = [junit_root] if junit_root.tag == "testsuite" else list(junit_root.findall("testsuite"))
    junit_totals = {
        key: sum(int(float(suite.attrib.get(key, "0") or 0)) for suite in junit_suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if any(int(targeted.get(key, -1)) != value for key, value in junit_totals.items()):
        raise GateFailure("Pi capability report targeted JUnit totals do not match the report")
    passed_testcases = [
        (
            f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}",
            max(0.0, float(case.attrib.get("time", "0") or 0)),
        )
        for suite in junit_suites
        for case in suite.findall("testcase")
        if case.find("skipped") is None and case.find("failure") is None and case.find("error") is None
    ]
    if len(passed_testcases) < 100:
        raise GateFailure("Pi capability report targeted JUnit has fewer than 100 concrete passed testcases")
    recomputed_batches: dict[str, dict[str, list[str]]] = {}
    recomputed_axis_matches: dict[str, int] = {}
    recomputed_proof_counts: dict[str, int] = {}
    junit_verified_cases: dict[str, list[str]] = {}
    matched_timings: dict[str, float] = {}
    for axis_id, matrix_axis in expected_axes.items():
        axis_batches: dict[str, list[str]] = {}
        axis_nodes: set[str] = set()
        verified: list[str] = []
        proof_count = 0
        for batch in list(dict(matrix_axis.get("requirements", {}) or {}).get("proof_batches", []) or []):
            if str(batch.get("source", "")) != "junit":
                continue
            selector = str(batch.get("selector", ""))
            matches = [node_id for node_id, _duration in passed_testcases if selector in node_id]
            if len(matches) < int(batch.get("minimum_matches", 1) or 1):
                raise GateFailure(f"Pi capability report JUnit proof batch {batch.get('id')} is incomplete")
            axis_batches[str(batch.get("id", ""))] = matches
            axis_nodes.update(matches)
            proof_count += len(matches)
            verified.extend(_pi_batch_case_ids(matrix_axis, dict(batch)))
            for node_id, duration in passed_testcases:
                if node_id in matches:
                    matched_timings[node_id] = duration
        recomputed_batches[axis_id] = axis_batches
        recomputed_axis_matches[axis_id] = len(axis_nodes)
        recomputed_proof_counts[axis_id] = proof_count
        junit_verified_cases[axis_id] = verified
    selector_matches = dict(targeted.get("axis_selector_matches", {}) or {})
    reported_batches = dict(targeted.get("batch_matches", {}) or {})
    reported_timings = dict(targeted.get("matched_testcase_timings", {}) or {})
    if (
        int(targeted.get("tests", 0) or 0) < 100
        or int(targeted.get("failures", -1)) != 0
        or int(targeted.get("errors", -1)) != 0
        or set(selector_matches) != set(_PI_AXIS_THRESHOLDS)
        or {key: int(value or 0) for key, value in selector_matches.items()} != recomputed_axis_matches
        or reported_batches != recomputed_batches
        or {key: float(value or 0) for key, value in reported_timings.items()} != matched_timings
    ):
        raise GateFailure("Pi capability report targeted selector/node evidence does not match JUnit")

    protocol = _strict_object(
        evidence.get("protocol_probe"),
        label="protocol probe",
        required={"ready", "protocol", "required_features", "capabilities"},
    )
    required_features = {str(value) for value in list(protocol.get("required_features", []) or [])}
    capabilities = {str(value) for value in list(protocol.get("capabilities", []) or [])}
    if (
        protocol.get("ready") is not True
        or int(protocol.get("protocol", 0) or 0) != 7
        or not _PI_REQUIRED_RUNTIME_FEATURES.issubset(required_features)
        or not required_features.issubset(capabilities)
    ):
        raise GateFailure("Pi capability report protocol probe is stale or incomplete")

    observability_axis = expected_axes["observability"]
    observability_requirements = dict(observability_axis.get("requirements", {}) or {})
    observability = _strict_object(
        evidence.get("observability"),
        label="observability",
        required={"required_fields", "kind_matches", "run_manifest_count", "records"},
    )
    required_kinds = {str(value) for value in list(observability_requirements.get("required_kinds", []) or [])}
    required_fields = {str(value) for value in list(observability_requirements.get("required_fields", []) or [])}
    kind_matches = dict(observability.get("kind_matches", {}) or {})
    kind_selectors = dict(observability_requirements.get("kind_selectors", {}) or {})
    recomputed_kind_matches = {
        kind: sum(
            1
            for node_id, _duration in passed_testcases
            if any(str(selector) in node_id for selector in list(kind_selectors.get(kind, []) or []))
        )
        for kind in required_kinds
    }
    if (
        set(kind_matches) != required_kinds
        or {key: int(value or 0) for key, value in kind_matches.items()} != recomputed_kind_matches
        or any(value < 1 for value in recomputed_kind_matches.values())
        or set(str(value) for value in list(observability.get("required_fields", []) or [])) != required_fields
        or int(observability.get("run_manifest_count", 0) or 0) != len(manifests)
        or dict(targeted.get("observability_selector_matches", {}) or {}) != kind_matches
    ):
        raise GateFailure("Pi capability report observability run/effect/subagent/compaction evidence is incomplete")
    observability_cases = {
        str(case.get("id", "")): dict(case)
        for case in list(observability_axis.get("cases", []) or [])
        if isinstance(case, dict)
    }
    allowed_nodes_by_kind: dict[str, set[str]] = {kind: set() for kind in required_kinds}
    for batch in list(observability_requirements.get("proof_batches", []) or []):
        kind = str(batch.get("case_probe", ""))
        allowed_nodes_by_kind.setdefault(kind, set()).update(
            recomputed_batches["observability"].get(str(batch.get("id", "")), [])
        )
    records = list(observability.get("records", []) or [])
    record_ids: list[str] = []
    for raw_record in records:
        record = _strict_object(
            raw_record,
            label="observability record",
            required={"id", "kind", "timing", "decision", "result_reference"},
        )
        record_id = str(record.get("id", ""))
        kind = str(record.get("kind", ""))
        timing = _strict_object(
            record.get("timing"),
            label="observability timing",
            required={"source", "duration_seconds"},
        )
        reference = _strict_object(
            record.get("result_reference"),
            label="observability result reference",
            required={"junit_sha256", "node_id"},
        )
        node_id = str(reference.get("node_id", ""))
        expected_case = observability_cases.get(record_id)
        if (
            expected_case is None
            or kind != str(expected_case.get("probe", ""))
            or record.get("decision") != "passed"
            or timing.get("source") != "junit"
            or float(timing.get("duration_seconds", -1) or 0) != float(matched_timings.get(node_id, -1))
            or str(reference.get("junit_sha256", "")) != str(targeted.get("sha256", ""))
            or node_id not in allowed_nodes_by_kind.get(kind, set())
            or not required_fields.issubset(set(record))
        ):
            raise GateFailure("Pi capability report observability record is not bound to a passed JUnit proof")
        record_ids.append(record_id)
    if record_ids != list(observability_cases):
        raise GateFailure("Pi capability report observability records do not cover every case exactly once")

    if required_mode == "deterministic":
        ping = _strict_object(evidence.get("ping"), label="ping", required={"ready", "protocol"})
        if ping.get("ready") is not True or int(ping.get("protocol", 0) or 0) != 7:
            raise GateFailure("Pi capability report ping evidence is stale")
        _validate_tool_loop(evidence.get("tool_loop"), label="tool loop")
        dynamic = _validate_tool_loop(
            evidence.get("dynamic_mutations"),
            label="dynamic mutations",
            require_mutations=True,
        )
        images = _validate_tool_loop(evidence.get("image_tool_tasks"), label="image+tool tasks")
        if int(images.get("image_tool_tasks", 0) or 0) != 10:
            raise GateFailure("Pi capability report image+tool evidence is incomplete")
        if int(images.get("image_serialized_tasks", 0) or 0) != 10:
            raise GateFailure("Pi capability report image serialization evidence is incomplete")
        runtime_matches = {
            "dynamic_mutations": int(dynamic.get("dynamic_mutations", 0) or 0),
            "image_tool_tasks": int(images.get("image_tool_tasks", 0) or 0),
        }
    else:
        _validate_tool_loop(evidence.get("deterministic_tool_loop"), label="deterministic tool loop")
        provider_real = _strict_object(
            evidence.get("provider_real"),
            label="provider-real",
            required={"dynamic_mutations", "image_tool_tasks", "mutation_evidence"},
        )
        proofs = list(provider_real.get("mutation_evidence", []) or [])
        if (
            int(provider_real.get("dynamic_mutations", 0) or 0) != 10
            or int(provider_real.get("image_tool_tasks", 0) or 0) != 10
            or len(proofs) != 10
        ):
            raise GateFailure("Pi capability report provider-real evidence is incomplete")
        for proof in proofs:
            record = _strict_object(
                proof,
                label="provider-real mutation",
                required={
                    "session_id", "target_tool", "search_tools_completed", "target_activated",
                    "target_completed",
                },
                allowed={
                    "turn", "session_id", "target_tool", "search_tools_completed", "target_activated",
                    "target_completed",
                },
            )
            if not all(record.get(key) is True for key in ("search_tools_completed", "target_activated", "target_completed")):
                raise GateFailure("Pi capability report provider-real mutation is unverified")
        if len({str(item.get("session_id", "")) for item in proofs}) != 10:
            raise GateFailure("Pi capability report provider-real mutation sessions are not isolated")
        runtime_matches = {
            "dynamic_mutations": int(provider_real.get("dynamic_mutations", 0) or 0),
            "image_tool_tasks": int(provider_real.get("image_tool_tasks", 0) or 0),
        }
    for axis_id, matrix_axis in expected_axes.items():
        expected_verified = list(junit_verified_cases.get(axis_id, []))
        expected_proof_count = int(recomputed_proof_counts.get(axis_id, 0) or 0)
        for batch in list(dict(matrix_axis.get("requirements", {}) or {}).get("proof_batches", []) or []):
            if str(batch.get("source", "")) != "runtime":
                continue
            match_count = int(runtime_matches.get(str(batch.get("selector", "")), 0) or 0)
            expected_proof_count += match_count
            if match_count >= int(batch.get("minimum_matches", 1) or 1):
                expected_verified.extend(_pi_batch_case_ids(matrix_axis, dict(batch)))
        reported_axis = reported_axes[axis_id]
        if (
            list(reported_axis.get("verified_case_ids", []) or []) != expected_verified
            or int(reported_axis.get("passed", 0) or 0) != len(expected_verified)
            or int(reported_axis.get("evidence_matches", 0) or 0) != expected_proof_count
        ):
            raise GateFailure(
                f"Pi capability report axis {axis_id} verified cases do not match recomputed JUnit/runtime proof batches"
            )
    return report


def _resolve_from_root(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def validate_release_inputs(contract: dict[str, Any], scope: dict[str, Any]) -> None:
    if _required_int(contract.get("schema_version", 0), label="release-gate.json schema_version") != 1:
        raise GateFailure("release-gate.json schema_version must be 1")
    contract_version = str(contract.get("version", "")).strip()
    if not contract_version:
        raise GateFailure("release-gate.json must define version")
    scope_schema = _required_int(scope.get("schema_version", 0), label="release-scope.json schema_version")
    if scope_schema not in {1, 2}:
        raise GateFailure("release-scope.json schema_version must be 1 or 2")
    version_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", contract_version)
    if version_match and tuple(int(part) for part in version_match.groups()) >= (0, 4, 0) and scope_schema != 2:
        raise GateFailure("release-scope.json schema v2 is required for v0.4.0 and newer release contracts")
    if scope_schema == 2:
        scope_version = str(scope.get("version", "")).strip()
        if not scope_version:
            raise GateFailure("release-scope.json schema v2 must define version")
        if scope_version != str(contract.get("version", "")).strip():
            raise GateFailure("release-scope.json scope version must match release-gate.json version")
    if not str(scope.get("p0_objective", "")).strip():
        raise GateFailure("release-scope.json must contain exactly one non-empty p0_objective")
    acceptance = list(scope.get("acceptance", []) or [])
    non_goals = list(scope.get("non_goals", []) or [])
    if not acceptance or not non_goals:
        raise GateFailure("release-scope.json requires non-empty acceptance and non_goals")
    if not all(isinstance(item, dict) for item in acceptance):
        raise GateFailure("release-scope.json acceptance entries must be objects")
    acceptance_ids = [str(item.get("id", "")).strip() for item in acceptance]
    if any(not item for item in acceptance_ids) or len(set(acceptance_ids)) != len(acceptance_ids):
        raise GateFailure("release-scope.json acceptance ids must be non-empty and unique")
    if scope_schema == 2:
        matrix_path = PROJECT_ROOT / "bench" / "pi_capability_tasks.json"
        matrix = _read_json(matrix_path)
        acceptance_id_by_axis = {
            "routing": "pi-routing",
            "dynamic_tools": "pi-dynamic-tools",
            "parallelism": "pi-parallelism",
            "long_context": "pi-long-context",
            "skills": "pi-skills",
            "subagents": "pi-subagents",
            "mcp": "pi-mcp",
            "multimodal": "pi-multimodal",
            "safety": "pi-safety",
            "observability": "pi-observability",
        }
        expected_mapping = {
            acceptance_id_by_axis[str(axis.get("id", ""))]: (
                str(axis.get("id", "")),
                _required_int(axis.get("threshold", 0), label="Pi matrix threshold"),
            )
            for axis in list(matrix.get("axes", []) or [])
            if str(axis.get("id", "")) in acceptance_id_by_axis
        }
        actual_mapping = {
            str(item.get("id", "")).strip(): (
                str(item.get("report_axis", "")).strip(),
                _required_int(item.get("threshold", 0), label="Pi acceptance threshold"),
            )
            for item in acceptance
        }
        if actual_mapping != expected_mapping or len(expected_mapping) != 10:
            raise GateFailure("release-scope.json Pi acceptance mapping must match the 10-axis capability matrix")
        history = list(scope.get("release_history", []) or [])
        if not all(isinstance(item, dict) for item in history):
            raise GateFailure("release-scope.json release_history entries must be objects")
        previous_v031 = [item for item in history if str(item.get("version", "")).strip() == "0.3.1"]
        if len(previous_v031) != 1:
            raise GateFailure("release-scope.json must preserve one v0.3.1 history entry")
        previous = previous_v031[0]
        if (
            str(previous.get("state", "")) != "superseded"
            or str(previous.get("verification", "")) != "frozen_unverified"
            or not list(previous.get("acceptance", []) or [])
            or not list(previous.get("non_goals", []) or [])
        ):
            raise GateFailure("release-scope.json v0.3.1 history must remain superseded/frozen_unverified and complete")
        snapshot_digest = str(previous.get("snapshot_sha256", "")).strip().casefold()
        canonical_previous = dict(previous)
        canonical_previous.pop("snapshot_sha256", None)
        actual_snapshot_digest = hashlib.sha256(
            json.dumps(
                canonical_previous,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if snapshot_digest != FROZEN_V031_SCOPE_SHA256 or actual_snapshot_digest != FROZEN_V031_SCOPE_SHA256:
            raise GateFailure("release-scope.json v0.3.1 history snapshot is not the frozen original")
    command_ids: list[str] = []
    allowed_result_kinds = {"", "pi_capability_report", "pi_matrix_validation", "pytest_junit"}
    for group in ("targeted_commands", "full_commands", "real_verifications"):
        for item in list(contract.get(group, []) or []):
            step_id = str(item.get("id", "")).strip()
            command = list(item.get("command", []) or [])
            if not step_id or not command or not all(isinstance(part, str) and part for part in command):
                raise GateFailure(f"{group} entries require a non-empty id and command array")
            result_kind = str(item.get("result_kind", "")).strip()
            if result_kind not in allowed_result_kinds:
                raise GateFailure(f"{group} contains an unknown result_kind: {result_kind}")
            if result_kind and not str(item.get("result_file", "")).strip():
                raise GateFailure(f"{group} result_kind requires result_file")
            retry = item.get("retry", {})
            if retry is not None and not isinstance(retry, dict):
                raise GateFailure(f"{group} retry configuration must be an object")
            if isinstance(retry, dict) and retry:
                try:
                    max_attempts = int(retry.get("max_attempts", 1))
                    delay_seconds = float(retry.get("delay_seconds", 0))
                except (TypeError, ValueError) as error:
                    raise GateFailure(f"{group} retry configuration must use numeric limits") from error
                if not 1 <= max_attempts <= 3:
                    raise GateFailure(f"{group} retry max_attempts must be between 1 and 3")
                if not 0 <= delay_seconds <= 120:
                    raise GateFailure(f"{group} retry delay_seconds must be between 0 and 120")
                if not str(retry.get("failure_reason", "")).strip():
                    raise GateFailure(f"{group} retry configuration must name one failure_reason")
            timeout_seconds = item.get("timeout_seconds")
            if timeout_seconds is not None:
                try:
                    timeout_value = float(timeout_seconds)
                except (TypeError, ValueError) as error:
                    raise GateFailure(f"{group} timeout_seconds must be numeric") from error
                if not 15 <= timeout_value <= 1_800:
                    raise GateFailure(f"{group} timeout_seconds must be between 15 and 1800")
            command_ids.append(step_id)
    if len(set(command_ids)) != len(command_ids):
        raise GateFailure("release gate command ids must be unique")
    pi_targeted_order = [
        "pi-runtime-build",
        "pi-targeted-python",
        "pi-capability-matrix",
        "pi-capabilities-deterministic",
    ]
    present_pi_steps = [step_id for step_id in pi_targeted_order if step_id in command_ids]
    declares_pi_pipeline = any(step_id in command_ids for step_id in ("pi-runtime-build", "pi-targeted-python"))
    if declares_pi_pipeline and present_pi_steps != pi_targeted_order:
        raise GateFailure("Pi targeted release steps must include build, tests, matrix, and deterministic evidence")
    if declares_pi_pipeline:
        positions = [command_ids.index(step_id) for step_id in pi_targeted_order]
        if positions != sorted(positions):
            raise GateFailure("Pi targeted release steps are out of order")
        if "pi-capabilities-real" not in command_ids:
            raise GateFailure("Pi provider-real capability evidence is missing from the release contract")
    visual = dict(contract.get("visual_acceptance", {}) or {})
    if not list(visual.get("required_checks", []) or []) or not list(visual.get("required_screenshots", []) or []):
        raise GateFailure("visual_acceptance must define required checks and screenshots")
    package = dict(contract.get("package", {}) or {})
    if "signature_required" in package and not isinstance(package["signature_required"], bool):
        raise GateFailure("package signature_required must be a boolean")
    runtime_manifest_url = str(package.get("runtime_manifest_url", "")).strip()
    if runtime_manifest_url and urlparse(runtime_manifest_url).scheme.lower() != "https":
        raise GateFailure("package runtime_manifest_url must use HTTPS")
    for key in ("node_component_manifest_url", "tectonic_component_manifest_url"):
        component_manifest_url = str(package.get(key, "")).strip()
        parsed_component_manifest = urlparse(component_manifest_url)
        if component_manifest_url and (
            parsed_component_manifest.scheme.lower() != "https" or not parsed_component_manifest.netloc
        ):
            raise GateFailure(f"package {key} must use HTTPS")
    if (
        str(package.get("profile", "")).strip().casefold() == "core"
        and bool(package.get("exclude_runtimes", False))
    ):
        missing_component_channels = [
            key
            for key in (
                "runtime_manifest_url",
                "node_component_manifest_url",
                "tectonic_component_manifest_url",
            )
            if not str(package.get(key, "")).strip()
        ]
        if missing_component_channels:
            raise GateFailure(
                "A lightweight core package requires separately downloadable runtime components: "
                + ", ".join(missing_component_channels)
            )
    update_manifest_url = str(package.get("update_manifest_url", "")).strip()
    if update_manifest_url and urlparse(update_manifest_url).scheme.lower() != "https":
        raise GateFailure("package update_manifest_url must use HTTPS")
    update_package_url = str(package.get("update_package_url", "")).strip()
    if update_manifest_url and not update_package_url:
        raise GateFailure("package update_package_url is required when automatic updates are enabled")
    if update_package_url:
        parsed_update_package = urlparse(update_package_url)
        if parsed_update_package.scheme.lower() != "https" or not parsed_update_package.netloc:
            raise GateFailure("package update_package_url must use HTTPS")
        if "{version}" not in update_package_url:
            raise GateFailure("package update_package_url must contain {version} for immutable release assets")
    release_public_key = str(package.get("release_public_key", "")).strip()
    if release_public_key:
        try:
            decode_public_key(release_public_key)
        except ValueError as error:
            raise GateFailure(f"package release_public_key is invalid: {error}") from error


def _template_command(parts: list[str], variables: dict[str, str]) -> list[str]:
    rendered: list[str] = []
    for part in parts:
        value = part
        for key, replacement in variables.items():
            value = value.replace("{" + key + "}", replacement)
        rendered.append(value)
    return rendered


def _find_available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _find_sign_tool() -> Path | None:
    """Find the Windows SDK signer without recording machine-specific paths."""

    direct = shutil.which("signtool.exe") or shutil.which("signtool")
    if direct:
        candidate = Path(direct)
        if candidate.is_file():
            return candidate
    kit_roots = [
        Path(value) / "Windows Kits" / "10" / "bin"
        for value in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
        if value
    ]
    for kit_root in kit_roots:
        if not kit_root.is_dir():
            continue
        try:
            versions = sorted((path for path in kit_root.iterdir() if path.is_dir()), reverse=True)
        except OSError:
            continue
        for version in versions:
            candidate = version / "x64" / "signtool.exe"
            if candidate.is_file():
                return candidate
    return None


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _verification_popen_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))}
    return {"start_new_session": True}


def _stop_verification_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate a timed-out verifier and every Node/MCP descendant."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, 15)
        except (OSError, ProcessLookupError):
            process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, 9)
            except (OSError, ProcessLookupError):
                process.kill()
        else:
            process.kill()
        process.wait(timeout=5)


def _rewrite_promoted_pi_report_paths(
    report_path: Path,
    *,
    source_diagnostics: Path,
    target_diagnostics: Path,
) -> None:
    """Make copied capability evidence self-contained in its promoted report."""

    payload = _read_json(report_path)

    def remap(record: object) -> None:
        if not isinstance(record, dict):
            return
        source = Path(str(record.get("path", ""))).resolve()
        try:
            relative = source.relative_to(source_diagnostics)
        except ValueError:
            return
        target = (target_diagnostics / relative).resolve()
        if not target.is_file():
            raise GateFailure(f"Cannot promote because copied Pi evidence is missing: {target}")
        record["path"] = str(target)
        record["sha256"] = _sha256(target)
        record["bytes"] = target.stat().st_size

    for manifest in list(payload.get("run_manifests", []) or []):
        remap(manifest)
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        remap(evidence.get("targeted_tests"))
    _write_json(report_path, payload)


def _pi_capability_summary(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    return {
        "report": str(report_path),
        "report_sha256": _sha256(report_path),
        "mode": str(report.get("mode", "")),
        "protocol_version": int(report.get("protocol_version", 0) or 0),
        "sdk_version": str(report.get("sdk_version", "")),
        "source_sha256": str(report.get("source_sha256", "")),
        "bundle_sha256": str(dict(report.get("bundle", {}) or {}).get("sha256", "")),
        "matrix_sha256": str(dict(report.get("matrix", {}) or {}).get("sha256", "")),
        "fallback_count": int(report.get("fallback_count", -1)),
        "run_manifest_count": len(list(report.get("run_manifests", []) or [])),
        "axes": [
            {
                "id": str(axis.get("id", "")),
                "passed": int(axis.get("passed", 0) or 0),
                "threshold": int(axis.get("threshold", 0) or 0),
            }
            for axis in list(report.get("axes", []) or [])
            if isinstance(axis, dict)
        ],
    }


class ReleaseGate:
    def __init__(
        self,
        *,
        contract_path: Path,
        scope_path: Path,
        profile: str,
        knowledge_source: Path,
        output_root: Path,
        build_id: str,
        resume_report: Path | None = None,
        promote_report: Path | None = None,
        visual_evidence_dir: Path | None = None,
    ) -> None:
        self.contract_path = contract_path
        self.scope_path = scope_path
        self.contract = _read_json(contract_path)
        self.scope = _read_json(scope_path)
        validate_release_inputs(self.contract, self.scope)
        self.profile = profile
        self.knowledge_source = knowledge_source
        self.contract_sha256 = _sha256(contract_path)
        self.scope_sha256 = _sha256(scope_path)
        self.source_sha256 = _source_fingerprint()
        self.resumed = resume_report is not None

        if resume_report is not None:
            self.report_path = resume_report.resolve()
            self.report = _read_json(self.report_path)
            if str(self.report.get("contract_sha256")) != self.contract_sha256:
                raise GateFailure("The release contract changed; start a new build instead of resuming stale evidence")
            if str(self.report.get("scope_sha256")) != self.scope_sha256:
                raise GateFailure("The P0 scope changed; start a new build instead of resuming stale evidence")
            if str(self.report.get("source_sha256")) != self.source_sha256:
                raise GateFailure("Release-relevant source changed; start a new gate instead of resuming stale evidence")
            if str(self.report.get("profile")) != profile:
                raise GateFailure("A report can only be resumed with its original profile")
            self.build_id = str(self.report["build_id"])
            self.release_dir = Path(str(self.report["release_dir"])).resolve()
            self.diagnostics_dir = self.release_dir / "diagnostics"
        else:
            self.build_id = build_id
            version = str(self.contract["version"])
            self.release_dir = (output_root / f"{version}+{build_id}").resolve()
            self.report_path = self.release_dir / "release-report.json"
            if self.report_path.exists():
                raise GateFailure(f"Release report already exists; use --resume-report: {self.report_path}")
            self.diagnostics_dir = self.release_dir / "diagnostics"
            self.report = {
                "schema_version": 1,
                "product": str(self.contract.get("product", "ScanSci")),
                "version": version,
                "build_id": build_id,
                "profile": profile,
                "status": "running",
                "started_at": _utc_now(),
                "finished_at": None,
                "repo_root": str(PROJECT_ROOT),
                "release_dir": str(self.release_dir),
                "contract_file": str(contract_path),
                "contract_sha256": self.contract_sha256,
                "scope_file": str(scope_path),
                "scope_sha256": self.scope_sha256,
                "source_sha256": self.source_sha256,
                "knowledge_source": str(knowledge_source),
                "steps": [],
                "artifacts": {},
            }
        self.visual_evidence_dir = (visual_evidence_dir or self.release_dir / "visual-evidence").resolve()
        self.release_dir.mkdir(parents=True, exist_ok=True)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        if promote_report is not None:
            self._promote_from(promote_report.resolve())
        self._persist()

    def _promote_from(self, source_report_path: Path) -> None:
        source = _read_json(source_report_path)
        source_profile = str(source.get("profile", ""))
        rank = {"targeted": 0, "source": 1, "beta": 2, "release": 3}
        if str(source.get("status")) != "passed":
            raise GateFailure("Only a passed report can be promoted")
        if source_profile not in rank or rank[source_profile] >= rank[self.profile]:
            raise GateFailure("Promotion requires a passed lower-profile report")
        for field, expected in (
            ("contract_sha256", self.contract_sha256),
            ("scope_sha256", self.scope_sha256),
            ("source_sha256", self.source_sha256),
        ):
            if str(source.get(field, "")) != expected:
                raise GateFailure(f"Cannot promote because {field} changed")
        allowed = {"scope-contract"}
        allowed.update(str(item["id"]) for item in list(self.contract.get("targeted_commands", []) or []))
        if source_profile in {"source", "beta", "release"}:
            for group in ("full_commands", "real_verifications"):
                allowed.update(str(item["id"]) for item in list(self.contract.get(group, []) or []))
        imported = [
            dict(step)
            for step in list(source.get("steps", []) or [])
            if step.get("id") in allowed and step.get("status") == "passed"
        ]
        source_diagnostics = Path(str(source.get("release_dir", source_report_path.parent))).resolve() / "diagnostics"
        configured = {
            str(item.get("id", "")): dict(item)
            for group in ("targeted_commands", "full_commands", "real_verifications")
            for item in list(self.contract.get(group, []) or [])
        }
        for step in imported:
            raw_result = str(step.get("result_file", "")).strip()
            if not raw_result:
                continue
            source_result = Path(raw_result).resolve()
            if not source_result.is_file():
                raise GateFailure(f"Cannot promote because evidence is missing for {step.get('id')}")
            recorded_hash = str(step.get("result_sha256", "")).strip().casefold()
            if not recorded_hash or _sha256(source_result).casefold() != recorded_hash:
                raise GateFailure(f"Cannot promote because evidence changed for {step.get('id')}")
            result_kind = str(configured.get(str(step.get("id", "")), {}).get("result_kind", ""))
            self._validate_required_result(source_result, result_kind=result_kind)
        if source_diagnostics.is_dir():
            for source_file in source_diagnostics.rglob("*"):
                if not source_file.is_file():
                    continue
                target = self.diagnostics_dir / source_file.relative_to(source_diagnostics)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
        for step in imported:
            raw_result = str(step.get("result_file", "")).strip()
            if not raw_result:
                continue
            source_result = Path(raw_result).resolve()
            try:
                relative = source_result.relative_to(source_diagnostics)
            except ValueError:
                continue
            target_result = self.diagnostics_dir / relative
            step["result_file"] = str(target_result)
            result_kind = str(configured.get(str(step.get("id", "")), {}).get("result_kind", ""))
            if result_kind == "pi_capability_report":
                _rewrite_promoted_pi_report_paths(
                    target_result,
                    source_diagnostics=source_diagnostics,
                    target_diagnostics=self.diagnostics_dir,
                )
            step["result_sha256"] = _sha256(target_result)
            validated_result = self._validate_required_result(target_result, result_kind=result_kind)
            if result_kind == "pi_capability_report" and isinstance(validated_result, dict):
                capability_summary = _pi_capability_summary(validated_result, target_result)
                step["capability_summary"] = capability_summary
                self.report["artifacts"].setdefault("pi_capabilities", {})[
                    capability_summary["mode"]
                ] = capability_summary
        self.report["steps"] = imported
        self.report["promoted_from"] = {
            "report": str(source_report_path),
            "profile": source_profile,
            "step_ids": [str(step["id"]) for step in imported],
        }

    def _persist(self) -> None:
        _write_json(self.report_path, self.report)

    def _previous(self, step_id: str) -> dict[str, Any] | None:
        for item in reversed(list(self.report.get("steps", []) or [])):
            if item.get("id") == step_id:
                return item
        return None

    def _record(self, step: dict[str, Any]) -> None:
        steps = [item for item in list(self.report.get("steps", []) or []) if item.get("id") != step["id"]]
        steps.append(step)
        self.report["steps"] = steps
        self._persist()

    def internal_step(self, step_id: str, title: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        previous = self._previous(step_id)
        if previous and previous.get("status") == "passed":
            _emit_console(f"[reuse] {title}")
            return previous
        _emit_console(f"[gate] {title}")
        started = time.monotonic()
        step = {"id": step_id, "title": title, "status": "running", "started_at": _utc_now()}
        self._record(step)
        try:
            details = action()
        except GatePending:
            step.update(status="pending", finished_at=_utc_now(), duration_seconds=round(time.monotonic() - started, 2))
            self._record(step)
            raise
        except Exception as error:
            step.update(
                status="failed",
                finished_at=_utc_now(),
                duration_seconds=round(time.monotonic() - started, 2),
                error=str(error),
            )
            self._record(step)
            raise GateFailure(f"{title}: {error}") from error
        step.update(
            status="passed",
            finished_at=_utc_now(),
            duration_seconds=round(time.monotonic() - started, 2),
            details=details,
        )
        self._record(step)
        return step

    def command_step(
        self,
        *,
        step_id: str,
        title: str,
        command: list[str],
        required_result: Path | None = None,
        echo_output: bool = True,
        retry: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        output_to_file: bool = False,
        result_kind: str = "",
    ) -> dict[str, Any]:
        previous = self._previous(step_id)
        if previous and previous.get("status") == "passed":
            if required_result is None:
                _emit_console(f"[reuse] {title}")
                return previous
            if required_result.is_file():
                recorded_hash = str(previous.get("result_sha256", "")).strip().casefold()
                if recorded_hash and _sha256(required_result).casefold() == recorded_hash:
                    self._validate_required_result(required_result, result_kind=result_kind)
                    _emit_console(f"[reuse] {title}")
                    return previous
        log_path = self.diagnostics_dir / f"{step_id}.log"
        _emit_console(f"[run] {title}")
        _emit_console("      " + subprocess.list2cmdline(command))
        started = time.monotonic()
        step = {
            "id": step_id,
            "title": title,
            "status": "running",
            "started_at": _utc_now(),
            "command": command,
            "log": str(log_path),
        }
        self._record(step)
        env = self._command_environment(command)
        retry = dict(retry or {})
        max_attempts = max(1, int(retry.get("max_attempts", 1) or 1))
        retry_delay_seconds = max(0.0, float(retry.get("delay_seconds", 0) or 0))
        retry_failure_reason = str(retry.get("failure_reason", "")).strip()
        command_timeout = float(timeout_seconds) if timeout_seconds is not None else None
        attempts: list[dict[str, Any]] = []
        exit_code = 1
        try:
            with log_path.open("w", encoding="utf-8") as log:
                for attempt_number in range(1, max_attempts + 1):
                    attempt_started = time.monotonic()
                    if attempt_number > 1:
                        log.write(f"\n[retry] attempt {attempt_number}/{max_attempts}\n")
                    timed_out = False
                    if output_to_file:
                        # A verification that launches nested Windows
                        # subprocesses must not inherit the gate runner's
                        # captured stdout pipe. Direct the child output to its
                        # artifact log instead; this preserves evidence while
                        # avoiding handle-inheritance failures such as
                        # ``spawn EPERM`` in stdio MCP probes.
                        process = subprocess.Popen(
                            command,
                            cwd=PROJECT_ROOT,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            **_verification_popen_options(),
                        )
                        try:
                            exit_code = process.wait(timeout=command_timeout)
                        except subprocess.TimeoutExpired:
                            timed_out = True
                            _stop_verification_process_tree(process)
                            exit_code = process.returncode if process.returncode is not None else 124
                    else:
                        process = subprocess.Popen(
                            command,
                            cwd=PROJECT_ROOT,
                            env=env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            **_verification_popen_options(),
                        )
                        assert process.stdout is not None
                        output_queue: Queue[str] = Queue()

                        def drain_output() -> None:
                            for line in process.stdout:
                                output_queue.put(line)

                        output_thread = Thread(target=drain_output, daemon=True)
                        output_thread.start()
                        deadline = (
                            time.monotonic() + command_timeout
                            if command_timeout is not None
                            else None
                        )
                        while process.poll() is None:
                            try:
                                line = output_queue.get(timeout=0.1)
                            except Empty:
                                if deadline is not None and time.monotonic() >= deadline:
                                    timed_out = True
                                    _stop_verification_process_tree(process)
                                    break
                                continue
                            if echo_output:
                                _emit_console(line, end="")
                            log.write(line)
                        exit_code = process.wait()
                        output_thread.join(timeout=1.0)
                        while not output_queue.empty():
                            line = output_queue.get_nowait()
                            if echo_output:
                                _emit_console(line, end="")
                            log.write(line)
                    if timed_out:
                        exit_code = 124
                        timeout_message = (
                            f"[timeout] {title}: exceeded {command_timeout:g}s; "
                            "terminated the verification process\n"
                        )
                        _emit_console(timeout_message, end="")
                        log.write(timeout_message)
                    failure_reason = ""
                    if timed_out:
                        failure_reason = "command_timeout"
                    elif retry_failure_reason:
                        try:
                            log.flush()
                            log_text = log_path.read_text(encoding="utf-8", errors="replace")
                            if retry_failure_reason in log_text:
                                failure_reason = retry_failure_reason
                        except OSError:
                            pass
                        if not failure_reason and required_result is not None and required_result.is_file():
                            try:
                                result_text = required_result.read_text(encoding="utf-8-sig")
                                if retry_failure_reason in result_text:
                                    failure_reason = retry_failure_reason
                            except OSError:
                                pass
                    attempts.append(
                        {
                            "attempt": attempt_number,
                            "exit_code": exit_code,
                            "duration_seconds": round(time.monotonic() - attempt_started, 2),
                            **({"timeout_seconds": command_timeout} if command_timeout is not None else {}),
                            **({"failure_reason": failure_reason} if failure_reason else {}),
                        }
                    )
                    if exit_code == 0:
                        break
                    if (
                        attempt_number < max_attempts
                        and retry_failure_reason
                        and failure_reason == retry_failure_reason
                    ):
                        message = (
                            f"[retry] {title}: {retry_failure_reason}; "
                            f"waiting {retry_delay_seconds:g}s before retry {attempt_number + 1}/{max_attempts}\n"
                        )
                        _emit_console(message, end="")
                        log.write(message)
                        time.sleep(retry_delay_seconds)
                        continue
                    break
        except Exception as error:
            step.update(status="failed", finished_at=_utc_now(), error=str(error))
            self._record(step)
            raise GateFailure(f"{title}: {error}") from error
        step.update(
            status="passed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            finished_at=_utc_now(),
            duration_seconds=round(time.monotonic() - started, 2),
            attempts=attempts,
        )
        if exit_code != 0:
            # Keep the final report useful without making a reviewer inspect
            # every nested attempt record.  The detailed attempt list remains
            # the source of timing and retry evidence.
            final_failure_reason = next(
                (
                    str(attempt.get("failure_reason", "")).strip()
                    for attempt in reversed(attempts)
                    if str(attempt.get("failure_reason", "")).strip()
                ),
                "",
            )
            if final_failure_reason:
                step["failure_reason"] = final_failure_reason
        if required_result is not None:
            step["result_file"] = str(required_result)
            if exit_code == 0 and not required_result.is_file():
                step["status"] = "failed"
                step["error"] = "Command returned success but its required result file is missing"
            elif exit_code == 0:
                try:
                    validated_result = self._validate_required_result(required_result, result_kind=result_kind)
                except Exception as error:
                    step["status"] = "failed"
                    step["error"] = str(error)
                else:
                    step["result_sha256"] = _sha256(required_result)
                    if result_kind == "pi_capability_report" and isinstance(validated_result, dict):
                        capability_summary = _pi_capability_summary(validated_result, required_result)
                        step["capability_summary"] = capability_summary
                        self.report["artifacts"].setdefault("pi_capabilities", {})[
                            capability_summary["mode"]
                        ] = capability_summary
        self._record(step)
        if step["status"] != "passed":
            raise GateFailure(f"{title} failed; see {log_path}")
        if not echo_output:
            _emit_console(f"[ok] {title} ({step['duration_seconds']}s; log: {log_path})")
        return step

    @staticmethod
    def _command_environment(command: list[str]) -> dict[str, str]:
        env = dict(os.environ)
        executable = Path(str(command[0] if command else "")).name.casefold()
        is_python = executable.startswith("python") or (
            command and os.path.normcase(str(Path(command[0]).resolve())) == os.path.normcase(str(Path(sys.executable).resolve()))
        )
        if is_python:
            source_root = str((PROJECT_ROOT / "src").resolve())
            inherited = str(env.get("PYTHONPATH", "")).strip()
            env["PYTHONPATH"] = os.pathsep.join([source_root, inherited]) if inherited else source_root
            env["PYTHONUTF8"] = "1"
        else:
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONUTF8", None)
        return env

    def _validate_required_result(self, path: Path, *, result_kind: str) -> dict[str, Any] | None:
        kind = str(result_kind or "").strip()
        if not kind:
            return None
        if kind == "pi_capability_report":
            mode = "real" if "real" in path.stem.casefold() else "deterministic"
            return validate_pi_capability_report(
                path,
                expected_source_sha256=self.source_sha256,
                expected_bundle_path=PROJECT_ROOT / "pi-runtime" / "dist" / "main.mjs",
                required_mode=mode,
            )
        if kind == "pi_matrix_validation":
            payload = _read_json(path)
            if int(payload.get("schema_version", 0)) != 2 or payload.get("status") != "passed":
                raise GateFailure("Pi capability matrix validation report is invalid")
            if int(payload.get("axis_count", 0) or 0) != 10 or int(payload.get("case_count", 0) or 0) < 226:
                raise GateFailure("Pi capability matrix validation report is incomplete")
            matrix_evidence = payload.get("matrix")
            _validated_file_evidence(
                matrix_evidence,
                label="matrix",
                extra_required={"schema_version"},
            )
            if int(dict(matrix_evidence or {}).get("schema_version", 0)) != 2:
                raise GateFailure("Pi capability matrix evidence schema_version must be 2")
            return payload
        if kind == "pytest_junit":
            try:
                root = ET.parse(path).getroot()
            except Exception as error:
                raise GateFailure(f"Pytest JUnit evidence is invalid: {error}") from error
            suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
            totals = {
                key: sum(int(float(suite.attrib.get(key, "0") or 0)) for suite in suites)
                for key in ("tests", "failures", "errors")
            }
            if totals["tests"] < 100 or totals["failures"] or totals["errors"]:
                raise GateFailure("Pytest JUnit evidence is incomplete or failed")
            return totals
        raise GateFailure(f"Unknown release result_kind: {kind}")

    def _variables(self) -> dict[str, str]:
        return {
            "python": sys.executable,
            "project_root": str(PROJECT_ROOT),
            "diagnostics": str(self.diagnostics_dir),
            "knowledge_source": str(self.knowledge_source),
        }

    def run_configured_commands(self, group: str) -> None:
        variables = self._variables()
        for item in list(self.contract.get(group, []) or []):
            required_result = None
            if item.get("result_file"):
                required_result = self.diagnostics_dir / str(item["result_file"])
            self.command_step(
                step_id=str(item["id"]),
                title=str(item["title"]),
                command=_template_command(list(item["command"]), variables),
                required_result=required_result,
                echo_output=str(item.get("console", "stream")) != "log",
                retry=dict(item.get("retry", {}) or {}),
                timeout_seconds=(float(item["timeout_seconds"]) if item.get("timeout_seconds") is not None else None),
                output_to_file=str(item.get("output", "pipe")) == "file",
                result_kind=str(item.get("result_kind", "")),
            )

    @property
    def package_staging_root(self) -> Path:
        """Return a short, deterministic staging root for Windows onedir builds.

        PyInstaller preserves package-internal paths.  A delivery directory such
        as ``internal-beta-releases/<version>+<build-id>/ScanSci`` can therefore
        push otherwise valid LiteLLM resources beyond Windows' traditional
        MAX_PATH limit during ``COLLECT``.  The onedir tree is only an input to
        installer creation and runtime verification, so stage it below a short
        project-local build path while keeping the installer and release report
        in the auditable release directory.
        """

        material = f"{self.release_dir.resolve()}\0{self.build_id}"
        token = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
        return PROJECT_ROOT / "build" / "p" / token

    @property
    def package_dir(self) -> Path:
        return self.package_staging_root / str(self.contract["package"]["name"])

    @property
    def executable(self) -> Path:
        return self.package_dir / f"{self.contract['package']['name']}.exe"

    @property
    def installer_dir(self) -> Path:
        return self.release_dir / "installer"

    @property
    def update_bundle_dir(self) -> Path:
        return self.release_dir / "update"

    @property
    def update_manifest(self) -> Path:
        return self.update_bundle_dir / "stable.json"

    @property
    def installer_manifest(self) -> Path:
        return self.installer_dir / "installer-manifest.json"

    @property
    def is_packaged_profile(self) -> bool:
        """Whether this gate must create and exercise a Windows delivery package."""

        return self.profile in {"beta", "release"}

    @property
    def signature_required(self) -> bool:
        """Require signatures for every formal release regardless of mutable config."""

        return self.profile == "release"

    @property
    def requires_runtime_component_channels(self) -> bool:
        """Whether a slim desktop release depends on external binary components."""

        package = dict(self.contract.get("package", {}) or {})
        return (
            self.is_packaged_profile
            and str(package.get("profile", "")).strip().casefold() == "core"
            and bool(package.get("exclude_runtimes", False))
        )

    @staticmethod
    def _runtime_component_record(manifest: dict[str, Any], component_id: str) -> dict[str, Any]:
        if str(manifest.get("id", "")).strip() == component_id:
            source = manifest
        else:
            components = manifest.get("components")
            if not isinstance(components, dict) or not isinstance(components.get(component_id), dict):
                raise GateFailure(f"Runtime component manifest does not contain {component_id!r}")
            source = dict(components[component_id])
        return {
            "version": source.get("version"),
            "windows": source.get("windows"),
        }

    @staticmethod
    def _validate_component_asset(component_id: str, record: dict[str, Any]) -> list[str]:
        version = str(record.get("version", "")).strip()
        windows = record.get("windows")
        if not version or not isinstance(windows, dict):
            raise GateFailure(f"Runtime component {component_id!r} is missing version or Windows package metadata")
        overall_checksum = str(windows.get("sha256", "")).strip()
        try:
            overall_size = int(windows.get("size", 0))
        except (TypeError, ValueError) as error:
            raise GateFailure(f"Runtime component {component_id!r} package has an invalid size") from error
        if (
            not re.fullmatch(r"[A-Fa-f0-9]{64}", overall_checksum)
            or overall_size <= 0
            or overall_size > MAX_RUNTIME_COMPONENT_BYTES
        ):
            raise GateFailure(f"Runtime component {component_id!r} package is missing SHA256 or size")

        def validate_download(download: dict[str, Any], label: str) -> str:
            url = str(download.get("url", "")).strip()
            parsed = urlparse(url)
            checksum = str(download.get("sha256", "")).strip()
            try:
                size = int(download.get("size", 0))
            except (TypeError, ValueError) as error:
                raise GateFailure(f"Runtime component {component_id!r} {label} has an invalid size") from error
            if parsed.scheme.lower() != "https" or not parsed.netloc:
                raise GateFailure(f"Runtime component {component_id!r} {label} must use HTTPS")
            if not re.fullmatch(r"[A-Fa-f0-9]{64}", checksum) or size <= 0:
                raise GateFailure(f"Runtime component {component_id!r} {label} is missing SHA256 or size")
            return url

        parts = windows.get("parts")
        if isinstance(parts, list) and parts:
            if not all(isinstance(part, dict) for part in parts):
                raise GateFailure(f"Runtime component {component_id!r} has an invalid multipart package")
            urls = [validate_download(dict(part), f"part {index}") for index, part in enumerate(parts, start=1)]
            total_size = sum(int(dict(part).get("size", 0) or 0) for part in parts)
            if total_size != overall_size or total_size > MAX_RUNTIME_COMPONENT_BYTES:
                raise GateFailure(
                    f"Runtime component {component_id!r} part sizes do not match its package size"
                )
            return urls
        return [validate_download(windows, "package")]

    def verify_runtime_component_channels(self) -> dict[str, Any]:
        """Prove that every binary removed from the core has a usable release channel."""

        package = dict(self.contract.get("package", {}) or {})
        manifests = {
            "node": str(package.get("node_component_manifest_url", "")).strip(),
            "tectonic": str(package.get("tectonic_component_manifest_url", "")).strip(),
            "local-transformers": str(package.get("runtime_manifest_url", "")).strip(),
        }
        verified: dict[str, Any] = {}
        for component_id, manifest_url in manifests.items():
            try:
                request = Request(manifest_url, headers={"User-Agent": "ScanSci-release-gate/1"})
                with open_release_url(request, timeout=20, opener=urlopen) as response:
                    payload = json.loads(response.read().decode("utf-8-sig"))
            except Exception as error:
                raise GateFailure(
                    f"Runtime component manifest is unavailable for {component_id}: {manifest_url} ({error})"
                ) from error
            if not isinstance(payload, dict):
                raise GateFailure(f"Runtime component manifest for {component_id} must be a JSON object")
            if self.profile == "release":
                if payload.get("schema_version") != 1:
                    raise GateFailure(f"Runtime component manifest for {component_id} must use schema_version 1")
                try:
                    verify_manifest_signature(
                        payload,
                        str(package.get("release_public_key", "")).strip(),
                        required=True,
                    )
                except ValueError as error:
                    raise GateFailure(
                        f"Runtime component manifest signature is invalid for {component_id}: {error}"
                    ) from error
            record = self._runtime_component_record(payload, component_id)
            asset_urls = self._validate_component_asset(component_id, record)
            # Probe each immutable asset without downloading it. A valid JSON
            # file that points to a missing ZIP is still a broken release.
            for asset_url in asset_urls:
                try:
                    request = Request(
                        asset_url,
                        method="HEAD",
                        headers={"User-Agent": "ScanSci-release-gate/1"},
                    )
                    with open_release_url(request, timeout=20, opener=urlopen) as response:
                        status = int(response.getcode() or 200)
                    if status >= 400:
                        raise GateFailure(f"HTTP {status}")
                except Exception as error:
                    raise GateFailure(
                        f"Runtime component asset is unavailable for {component_id}: {asset_url} ({error})"
                    ) from error
            verified[component_id] = {
                "manifest_url": manifest_url,
                "version": str(record.get("version", "")).strip(),
                "asset_count": len(asset_urls),
            }
            if component_id == "local-transformers":
                snapshot = self.diagnostics_dir / "runtime-component-local-transformers.json"
                _write_json(snapshot, {"id": component_id, **record})
                verified[component_id]["snapshot"] = str(snapshot)
        return {"components": verified}

    def build_desktop(self) -> None:
        previous = self._previous("build-desktop")
        if self.package_dir.exists() and not (previous and previous.get("status") == "passed"):
            raise GateFailure(
                f"Untrusted package directory already exists: {self.package_dir}. "
                "Use a new build id; the gate will not delete or overwrite it."
            )
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            raise GateFailure("PowerShell is required to build the Windows desktop package")
        package = dict(self.contract["package"])
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "build_desktop.ps1"),
            "-Mode",
            str(package.get("mode", "onedir")),
            "-PackageProfile",
            str(package.get("profile", "core")),
            "-OutputDir",
            str(self.package_staging_root),
            "-Name",
            str(package["name"]),
            "-Version",
            str(self.contract["version"]),
            "-BuildId",
            self.build_id,
            "-ReleaseSourceSha256",
            self.source_sha256,
            "-Clean",
        ]
        update_manifest_url = str(package.get("update_manifest_url", "")).strip()
        if update_manifest_url:
            command.extend(["-UpdateManifestUrl", update_manifest_url])
        release_public_key = str(package.get("release_public_key", "")).strip()
        if release_public_key:
            command.extend(["-ReleasePublicKey", release_public_key])
        runtime_manifest_url = str(package.get("runtime_manifest_url", "")).strip()
        if runtime_manifest_url:
            command.extend(["-RuntimeManifestUrl", runtime_manifest_url])
        node_component_manifest_url = str(package.get("node_component_manifest_url", "")).strip()
        if node_component_manifest_url:
            command.extend(["-NodeComponentManifestUrl", node_component_manifest_url])
        tectonic_component_manifest_url = str(package.get("tectonic_component_manifest_url", "")).strip()
        if tectonic_component_manifest_url:
            command.extend(["-TectonicComponentManifestUrl", tectonic_component_manifest_url])
        if bool(package.get("exclude_runtimes", False)):
            command.append("-ExcludeRuntimes")
        # PyInstaller's full profile emits a long analysis log. Keep it in the
        # release artifact instead of streaming it through a caller-owned
        # console pipe: desktop automation and CI runners may detach that pipe
        # while the build is still healthy, which used to turn a valid build
        # into a spurious ``Invalid argument`` gate failure.
        self.command_step(
            step_id="build-desktop",
            title="唯一目录中的正式 Windows 构建",
            command=command,
            echo_output=False,
        )

    def sign_desktop_executable(self) -> dict[str, Any]:
        """Sign the formal executable before any package identity is computed."""

        if not self.signature_required:
            return {"signature_required": False}
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            raise GateFailure("PowerShell is required to sign the Windows desktop executable")
        step = self.command_step(
            step_id="sign-desktop-executable",
            title="正式 Windows 主程序预签名",
            command=[
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PROJECT_ROOT / "scripts" / "sign_windows_executable.ps1"),
                "-Artifact",
                str(self.executable),
            ],
            required_result=self.executable,
            echo_output=False,
        )
        details = {
            "signature_required": True,
            "source_executable_authenticode_status": "Valid",
            "signed_executable_sha256": str(step.get("result_sha256", "")),
        }
        self.report["artifacts"].update(details)
        self._persist()
        return details

    def verify_package(self) -> dict[str, Any]:
        if not self.executable.is_file() or self.executable.stat().st_size <= 0:
            raise GateFailure(f"Packaged executable is missing or empty: {self.executable}")
        internal = self.package_dir / "_internal"
        missing = [
            relative
            for relative in list(self.contract["package"].get("required_resources", []) or [])
            if not (internal / str(relative)).is_file()
        ]
        if missing:
            raise GateFailure("Packaged resources are missing: " + ", ".join(missing))
        build_info_path = internal / "scansci_html" / "build-info.json"
        build_info = _read_json(build_info_path)
        if str(build_info.get("build_id")) != self.build_id:
            raise GateFailure("Packaged build-info.json does not match the release build id")
        expected_profile = str(self.contract["package"].get("profile", "core"))
        if str(build_info.get("package_profile", "")) != expected_profile:
            raise GateFailure("Packaged build-info.json does not match the package profile")
        if bool(self.contract["package"].get("exclude_runtimes", False)) and not bool(build_info.get("exclude_runtimes", False)):
            raise GateFailure("Packaged build-info.json does not record the slim runtime-component build")
        expected_runtime_manifest = str(self.contract["package"].get("runtime_manifest_url", "")).strip()
        if expected_runtime_manifest and str(build_info.get("runtime_manifest_url", "")).strip() != expected_runtime_manifest:
            raise GateFailure("Packaged build-info.json does not match the runtime component manifest")
        expected_update_manifest = str(self.contract["package"].get("update_manifest_url", "")).strip()
        if expected_update_manifest and str(build_info.get("update_manifest_url", "")).strip() != expected_update_manifest:
            raise GateFailure("Packaged build-info.json does not match the update manifest")
        expected_release_public_key = str(self.contract["package"].get("release_public_key", "")).strip()
        if str(build_info.get("release_public_key", "")).strip() != expected_release_public_key:
            raise GateFailure("Packaged build-info.json does not match the release manifest public key")
        for key in ("node_component_manifest_url", "tectonic_component_manifest_url"):
            expected_component_manifest = str(self.contract["package"].get(key, "")).strip()
            if expected_component_manifest and str(build_info.get(key, "")).strip() != expected_component_manifest:
                raise GateFailure(f"Packaged build-info.json does not match {key}")
        if str(build_info.get("release_source_sha256", "")).casefold() != self.source_sha256.casefold():
            raise GateFailure("Packaged build-info.json is not bound to the source fingerprint that passed this gate")
        if "pi_runtime/main.mjs" in list(self.contract["package"].get("required_resources", []) or []):
            pi_bundle_path = internal / "pi_runtime" / "main.mjs"
            expected_pi_bundle_sha256 = str(build_info.get("pi_bundle_sha256", "")).strip().casefold()
            if not pi_bundle_path.is_file():
                raise GateFailure("Packaged resources are missing: pi_runtime/main.mjs")
            if (
                not re.fullmatch(r"[a-f0-9]{64}", expected_pi_bundle_sha256)
                or _sha256(pi_bundle_path).casefold() != expected_pi_bundle_sha256
            ):
                raise GateFailure("Packaged Pi bundle SHA256 does not match build-info.json")
        package_bytes = sum(path.stat().st_size for path in self.package_dir.rglob("*") if path.is_file())
        maximum_bytes = int(self.contract["package"].get("max_package_bytes", 0) or 0)
        if maximum_bytes and package_bytes > maximum_bytes:
            raise GateFailure(f"Package is too large: {package_bytes} bytes exceeds {maximum_bytes}")
        forbidden = [
            name
            for name in list(self.contract["package"].get("forbidden_top_level_resources", []) or [])
            if (internal / str(name)).exists()
        ]
        if expected_profile == "core":
            bundled_runtime = sorted(
                {
                    path.relative_to(internal).as_posix()
                    for path in internal.rglob("*")
                    if any(
                        segment.casefold() in CORE_RUNTIME_FORBIDDEN_SEGMENTS
                        or segment.casefold().startswith("models--")
                        for segment in path.relative_to(internal).parts
                    )
                }
            )
            forbidden.extend(bundled_runtime)
        if forbidden:
            raise GateFailure("Core package contains optional runtime resources: " + ", ".join(forbidden))
        executable_sha256 = _sha256(self.executable)
        if self.signature_required:
            signed_sha256 = str(self.report.get("artifacts", {}).get("signed_executable_sha256", ""))
            if signed_sha256.casefold() != executable_sha256.casefold():
                raise GateFailure("Packaged executable changed after Authenticode signing")
        details = {
            "package_staging_dir": str(self.package_staging_root),
            "executable_path": str(self.executable),
            "executable_sha256": executable_sha256,
            "executable_bytes": self.executable.stat().st_size,
            "package_file_count": sum(1 for path in self.package_dir.rglob("*") if path.is_file()),
            "package_bytes": package_bytes,
            "package_profile": expected_profile,
            "build_info": build_info,
        }
        self.report["artifacts"].update(details)
        self._persist()
        return details

    def _update_package_url(self) -> str:
        template = str(self.contract["package"].get("update_package_url", "")).strip()
        return template.replace("{version}", str(self.contract["version"]))

    def build_update_bundle(self) -> None:
        """Build the full ZIP, blockmap, and stable manifest as one release set."""

        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            raise GateFailure("PowerShell is required to build the Windows update bundle")
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "package_desktop_release.ps1"),
            "-BuildDir",
            str(self.package_dir),
            "-Version",
            str(self.contract["version"]),
            "-PackageUrl",
            self._update_package_url(),
            "-OutputDir",
            str(self.update_bundle_dir),
            "-Channel",
            "stable",
        ]
        runtime_snapshot = self.diagnostics_dir / "runtime-component-local-transformers.json"
        if self.requires_runtime_component_channels:
            if not runtime_snapshot.is_file():
                raise GateFailure("Verified local-transformers manifest snapshot is missing")
            command.extend(["-LocalRuntimeManifest", str(runtime_snapshot)])
        manifest_private_key = os.environ.get("SCANSCI_RELEASE_MANIFEST_PRIVATE_KEY_FILE", "").strip()
        if manifest_private_key:
            command.extend(
                [
                    "-ManifestPrivateKey",
                    str(Path(manifest_private_key).expanduser().resolve()),
                    "-ManifestKeyId",
                    str(self.contract["package"].get("release_key_id", "scansci-release-v1")),
                ]
            )
        self.command_step(
            step_id="build-update-bundle",
            title="Windows 完整更新包、blockmap 与 stable.json 构建",
            command=command,
            required_result=self.update_manifest,
            echo_output=False,
        )

    def verify_update_bundle(self) -> dict[str, Any]:
        """Bind every updater asset to the package identity that passed this gate."""

        if not self.update_manifest.is_file():
            raise GateFailure(f"Update manifest is missing: {self.update_manifest}")
        manifest = _read_json(self.update_manifest)
        version = str(self.contract["version"])
        if manifest.get("schema_version") != 1:
            raise GateFailure("Update manifest must use schema_version 1")
        release_public_key = str(self.contract["package"].get("release_public_key", "")).strip()
        if release_public_key:
            try:
                verify_manifest_signature(manifest, release_public_key, required=True)
            except ValueError as error:
                raise GateFailure(f"Update manifest signature is invalid: {error}") from error
        if str(manifest.get("version", "")) != version:
            raise GateFailure("Update manifest does not match the release version")
        if str(manifest.get("channel", "")).casefold() != "stable":
            raise GateFailure("Formal update manifest must use the stable channel")
        windows = manifest.get("windows")
        if not isinstance(windows, dict):
            raise GateFailure("Update manifest is missing the Windows package")
        archive = self.update_bundle_dir / f"ScanSci-{version}-windows-x64.zip"
        blockmap_path = archive.with_suffix(archive.suffix + ".blockmap")
        for path, label in ((archive, "full ZIP"), (blockmap_path, "blockmap")):
            if not path.is_file() or path.stat().st_size <= 0:
                raise GateFailure(f"Update {label} is missing or empty: {path}")
        archive_sha256 = _sha256(archive)
        if str(windows.get("url", "")) != self._update_package_url():
            raise GateFailure("Update manifest package URL does not match the immutable release URL")
        if str(windows.get("sha256", "")).casefold() != archive_sha256.casefold():
            raise GateFailure("Update manifest ZIP SHA256 does not match the full package")
        if int(windows.get("size", 0) or 0) != archive.stat().st_size:
            raise GateFailure("Update manifest ZIP size does not match the full package")
        blockmap_info = windows.get("blockmap")
        if not isinstance(blockmap_info, dict):
            raise GateFailure("Update manifest is missing blockmap metadata")
        if str(blockmap_info.get("url", "")) != self._update_package_url() + ".blockmap":
            raise GateFailure("Update manifest blockmap URL does not match the full package URL")
        if str(blockmap_info.get("sha256", "")).casefold() != _sha256(blockmap_path).casefold():
            raise GateFailure("Update manifest blockmap SHA256 does not match the published blockmap")
        if int(blockmap_info.get("size", 0) or 0) != blockmap_path.stat().st_size:
            raise GateFailure("Update manifest blockmap size does not match the published blockmap")

        runtime_component_version = ""
        if self.requires_runtime_component_channels:
            runtime_snapshot = self.diagnostics_dir / "runtime-component-local-transformers.json"
            if not runtime_snapshot.is_file():
                raise GateFailure("Verified local-transformers manifest snapshot is missing")
            expected_runtime = self._runtime_component_record(
                _read_json(runtime_snapshot),
                "local-transformers",
            )
            actual_runtime = self._runtime_component_record(manifest, "local-transformers")
            if actual_runtime != expected_runtime:
                raise GateFailure("Update manifest local-transformers component differs from the verified channel")
            runtime_component_version = str(actual_runtime.get("version", "")).strip()

        blockmap = _read_json(blockmap_path)
        block_size = int(blockmap.get("block_size", 0) or 0)
        blocks = blockmap.get("blocks")
        if (
            int(blockmap.get("schema_version", 0) or 0) != 1
            or str(blockmap.get("algorithm", "")).casefold() != "sha256"
            or block_size != 64 * 1024
            or int(blockmap.get("size", 0) or 0) != archive.stat().st_size
            or str(blockmap.get("sha256", "")).casefold() != archive_sha256.casefold()
            or not isinstance(blocks, list)
            or len(blocks) != (archive.stat().st_size + block_size - 1) // block_size
            or any(not re.fullmatch(r"[a-fA-F0-9]{64}", str(item)) for item in blocks)
        ):
            raise GateFailure("Update blockmap does not describe the verified full ZIP")

        expected_build_info = dict(self.report.get("artifacts", {}).get("build_info", {}) or {})
        try:
            with ZipFile(archive) as zipped:
                members = [PurePosixPath(name.replace("\\", "/")) for name in zipped.namelist()]
                if not members or any(path.is_absolute() or ".." in path.parts for path in members):
                    raise GateFailure("Update ZIP contains an unsafe path")
                if not any(path.name.casefold() == "scansci.exe" for path in members):
                    raise GateFailure("Update ZIP does not contain ScanSci.exe")
                build_info_member = next(
                    (
                        name
                        for name in zipped.namelist()
                        if name.replace("\\", "/").casefold().endswith("_internal/scansci_html/build-info.json")
                    ),
                    None,
                )
                if not build_info_member:
                    raise GateFailure("Update ZIP does not contain build-info.json")
                archived_build_info = json.loads(zipped.read(build_info_member).decode("utf-8-sig"))
        except BadZipFile as error:
            raise GateFailure("Update ZIP is not a valid archive") from error
        for key in ("version", "build_id", "package_profile", "release_source_sha256"):
            if str(archived_build_info.get(key, "")) != str(expected_build_info.get(key, "")):
                raise GateFailure(f"Update ZIP build-info.json does not match verified package field {key}")

        details = {
            "update_manifest_path": str(self.update_manifest),
            "update_archive_path": str(archive),
            "update_archive_sha256": archive_sha256,
            "update_archive_bytes": archive.stat().st_size,
            "update_blockmap_path": str(blockmap_path),
            "update_blockmap_sha256": _sha256(blockmap_path),
            "update_blockmap_bytes": blockmap_path.stat().st_size,
            "update_package_url": self._update_package_url(),
            "runtime_component_version": runtime_component_version,
            "manifest_signature_verified": bool(release_public_key),
        }
        self.report["artifacts"].update(details)
        self._persist()
        return details

    def build_installer(self) -> None:
        """Produce a per-user Windows installer tied to the verified onedir package."""

        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            raise GateFailure("PowerShell is required to build the Windows installer")
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "build_windows_installer.ps1"),
            "-BuildDir",
            str(self.package_dir),
            "-Version",
            str(self.contract["version"]),
            "-BuildId",
            self.build_id,
            "-OutputDir",
            str(self.installer_dir),
        ]
        update_manifest_url = str(self.contract["package"].get("update_manifest_url", "")).strip()
        if update_manifest_url:
            command.extend(["-UpdateManifestUrl", update_manifest_url])
        if self.signature_required:
            command.append("-RequireSignature")
        self.command_step(
            step_id="build-installer",
            title="正式 Windows 安装包构建",
            command=command,
            required_result=self.installer_manifest,
            echo_output=False,
        )

    def signing_environment(self) -> dict[str, Any]:
        """Fail formal builds before expensive gates when signing is unavailable.

        The actual certificate/private-key validation remains inside the build
        script on the release machine. This early check deliberately records
        capability only, never a certificate thumbprint or timestamp endpoint.
        """

        if not self.signature_required:
            return {"signature_required": False}
        thumbprint = os.environ.get("SCANSCI_SIGNING_CERT_THUMBPRINT", "").replace(" ", "").strip()
        timestamp_url = os.environ.get("SCANSCI_TIMESTAMP_URL", "").strip()
        if not re.fullmatch(r"[A-Fa-f0-9]{40}", thumbprint):
            raise GateFailure(
                "Formal release signing is not configured: set SCANSCI_SIGNING_CERT_THUMBPRINT "
                "for the release certificate in CurrentUser\\My."
            )
        parsed_timestamp = urlparse(timestamp_url)
        if parsed_timestamp.scheme != "https" or not parsed_timestamp.netloc:
            raise GateFailure(
                "Formal release signing is not configured: SCANSCI_TIMESTAMP_URL must be an absolute HTTPS RFC 3161 endpoint."
            )
        if _find_sign_tool() is None:
            raise GateFailure(
                "Formal release signing is not configured: SignTool.exe from the Windows SDK or Visual Studio is unavailable."
            )
        public_key = str(self.contract.get("package", {}).get("release_public_key", "")).strip()
        try:
            expected_public_key = decode_public_key(public_key)
        except ValueError as error:
            raise GateFailure(f"Formal release manifest signing is not configured: {error}") from error
        private_key_value = os.environ.get("SCANSCI_RELEASE_MANIFEST_PRIVATE_KEY_FILE", "").strip()
        if not private_key_value:
            raise GateFailure(
                "Formal release manifest signing is not configured: set SCANSCI_RELEASE_MANIFEST_PRIVATE_KEY_FILE."
            )
        private_key_path = Path(private_key_value).expanduser().resolve()
        try:
            private_key = load_private_key(private_key_path)
        except ValueError as error:
            raise GateFailure(f"Formal release manifest signing is not configured: {error}") from error
        actual_public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if actual_public_key != expected_public_key:
            raise GateFailure("Formal release manifest private key does not match package release_public_key")
        return {
            "signature_required": True,
            "certificate_reference_present": True,
            "timestamp_endpoint_present": True,
            "sign_tool_available": True,
            "private_key_validation": "deferred_to_build_script",
            "manifest_private_key_present": True,
            "manifest_public_key_matches": True,
        }

    def verify_installer_manifest(self) -> dict[str, Any]:
        if not self.installer_manifest.is_file():
            raise GateFailure(f"Installer manifest is missing: {self.installer_manifest}")
        manifest = _read_json(self.installer_manifest)
        installer = Path(str(manifest.get("installer_path", ""))).resolve()
        if not installer.is_file() or installer.stat().st_size <= 0:
            raise GateFailure("Installer executable is missing or empty")
        if str(manifest.get("version", "")) != str(self.contract["version"]):
            raise GateFailure("Installer manifest does not match the release version")
        if str(manifest.get("build_id", "")) != self.build_id:
            raise GateFailure("Installer manifest does not match the release build id")
        expected_executable_sha256 = str(self.report.get("artifacts", {}).get("executable_sha256", ""))
        if str(manifest.get("source_executable_sha256", "")).casefold() != expected_executable_sha256.casefold():
            raise GateFailure("Installer is not bound to the verified packaged executable")
        actual_installer_sha256 = _sha256(installer)
        if str(manifest.get("installer_sha256", "")).casefold() != actual_installer_sha256.casefold():
            raise GateFailure("Installer SHA256 does not match its manifest")
        signature_required = self.signature_required
        signature_status = str(manifest.get("authenticode_status", "Unknown"))
        source_signature_status = str(manifest.get("source_executable_authenticode_status", "Unknown"))
        if signature_required and (
            not bool(manifest.get("signature_required"))
            or signature_status.casefold() != "valid"
            or source_signature_status.casefold() != "valid"
        ):
            raise GateFailure(
                "Formal release requires valid Authenticode signatures for both the installer and ScanSci.exe; "
                f"installer={signature_status}, executable={source_signature_status}"
            )
        details = {
            "installer_path": str(installer),
            "installer_sha256": actual_installer_sha256,
            "installer_bytes": installer.stat().st_size,
            "installer_authenticode_status": signature_status,
            "source_executable_authenticode_status": source_signature_status,
            "installer_signature_required": signature_required,
            "installer_manifest": str(self.installer_manifest),
        }
        self.report["artifacts"].update(details)
        self._persist()
        return details

    def verify_installer_installation(self) -> None:
        installer = Path(str(self.report.get("artifacts", {}).get("installer_path", ""))).resolve()
        output = self.diagnostics_dir / "installer-installation.json"
        signature_required = self.signature_required
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_windows_installer.py"),
            "--installer",
            str(installer),
            "--expected-build-id",
            self.build_id,
            "--expected-version",
            str(self.contract["version"]),
            "--output",
            str(output),
        ]
        if signature_required:
            command.append("--require-signature")
        self.command_step(
            step_id="installer-installation",
            title="正式安装包安装、运行与卸载验收",
            command=command,
            required_result=output,
            echo_output=False,
        )
        result = _read_json(output)
        if not bool(result.get("ok")):
            raise GateFailure("Installer verification reported ok=false")

    def prepare_beta_delivery(self) -> dict[str, Any]:
        """Write the exact hand-off materials for an invited, unsigned beta.

        The beta profile intentionally never looks like a formal public
        release: it records the unsigned state, binds every tester-facing
        value to the exercised installer, and provides a safe feedback
        template that does not invite users to paste secrets or documents.
        """

        if self.profile != "beta":
            raise GateFailure("Beta delivery material can only be written by the beta profile")
        installer = Path(str(self.report.get("artifacts", {}).get("installer_path", ""))).resolve()
        if not installer.is_file():
            raise GateFailure("Cannot prepare beta delivery material without a verified installer")
        installer_sha256 = str(self.report.get("artifacts", {}).get("installer_sha256", "")).casefold()
        if not re.fullmatch(r"[a-f0-9]{64}", installer_sha256):
            raise GateFailure("Cannot prepare beta delivery material without an installer SHA256")
        executable_sha256 = str(self.report.get("artifacts", {}).get("executable_sha256", "")).casefold()
        if not re.fullmatch(r"[a-f0-9]{64}", executable_sha256):
            raise GateFailure("Cannot prepare beta delivery material without an executable SHA256")

        installer_status = str(self.report.get("artifacts", {}).get("installer_authenticode_status", "Unknown"))
        executable_status = str(self.report.get("artifacts", {}).get("source_executable_authenticode_status", "Unknown"))
        if installer_status.casefold() == "valid" or executable_status.casefold() == "valid":
            raise GateFailure("The unsigned beta profile must not mix signed and unsigned delivery artifacts")

        delivery_dir = self.release_dir / "beta-delivery"
        delivery_dir.mkdir(parents=True, exist_ok=True)
        checksum_path = delivery_dir / "SHA256SUMS.txt"
        readme_path = delivery_dir / "BETA-README.zh-CN.md"
        feedback_path = delivery_dir / "BETA-FEEDBACK-TEMPLATE.md"
        manifest_path = delivery_dir / "beta-distribution.json"
        checksum_path.write_text(
            f"{installer_sha256}  {installer.name}\n",
            encoding="utf-8",
        )
        readme_path.write_text(
            "\n".join(
                [
                    f"# ScanSci {self.contract['version']} 受邀内测",
                    "",
                    f"构建编号：`{self.build_id}`",
                    "",
                    "这是受邀内测包，不是公开正式版。此安装包尚未使用公开信任的代码签名证书签名；Windows 可能显示“未知发布者”或 SmartScreen 提示。仅在你从 ScanSci 官方内测渠道收到本文件时继续安装。",
                    "",
                    "## 安装前核验",
                    "",
                    f"安装包：`{installer.name}`",
                    f"SHA-256：`{installer_sha256}`",
                    "",
                    "在 PowerShell 中运行：",
                    "",
                    "```powershell",
                    f"Get-FileHash .\\{installer.name} -Algorithm SHA256",
                    "```",
                    "",
                    "输出必须与上面的 SHA-256 完全一致。若不一致，请勿安装，并从提供内测包的渠道重新获取。",
                    "",
                    "## 反馈范围",
                    "",
                    "请优先反馈：安装或启动失败、资料库绑定/索引、证据引用、联网学术搜索、长任务恢复和界面异常。请使用同目录的 `BETA-FEEDBACK-TEMPLATE.md`。",
                    "",
                    "请不要在反馈中粘贴 API 密钥、Access Token、私有文档全文、未脱敏的个人资料或聊天记录。",
                    "",
                    "## 卸载与回退",
                    "",
                    "Windows 设置 → 应用 → 已安装的应用 → ScanSci → 卸载。卸载 ScanSci 不会删除你原始资料文件；若要保留问题现场，请先导出日志后再卸载。",
                    "",
                    "## 已知边界",
                    "",
                    "- 本包未签名，仅供受邀测试者使用，不应公开转发。",
                    "- 联网模型、学术搜索、Notion 等功能依赖各自服务的网络与账号状态。",
                    "- 首次建立资料索引可能消耗 CPU；应用会复用未变化资料的既有索引。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        feedback_path.write_text(
            "\n".join(
                [
                    "# ScanSci 内测反馈",
                    "",
                    "- 构建编号：",
                    "- Windows 版本：",
                    "- 发生时间：",
                    "- 操作步骤：",
                    "- 实际结果：",
                    "- 期望结果：",
                    "- 是否可以稳定复现：是 / 否",
                    "- 截图或日志文件：",
                    "",
                    "请勿提交 API 密钥、Access Token、私有文档全文或未脱敏个人信息。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        _write_json(
            manifest_path,
            {
                "schema_version": 1,
                "product": str(self.contract.get("product", "ScanSci")),
                "channel": "invited-internal-beta",
                "version": str(self.contract["version"]),
                "build_id": self.build_id,
                "installer": {
                    "path": str(installer),
                    "file_name": installer.name,
                    "sha256": installer_sha256,
                    "authenticode_status": installer_status,
                    "unsigned_acknowledgement_required": True,
                },
                "packaged_executable": {
                    "sha256": executable_sha256,
                    "authenticode_status": executable_status,
                },
                "materials": {
                    "checksums": str(checksum_path),
                    "readme": str(readme_path),
                    "feedback_template": str(feedback_path),
                },
                "distribution_guardrails": [
                    "invited_testers_only",
                    "do_not_publish_to_a_public_download_page",
                    "verify_sha256_before_installing",
                    "do_not_collect_or_share_secrets_in_feedback",
                ],
            },
        )
        details = {
            "delivery_dir": str(delivery_dir),
            "distribution_manifest": str(manifest_path),
            "checksums": str(checksum_path),
            "readme": str(readme_path),
            "feedback_template": str(feedback_path),
            "installer_sha256": installer_sha256,
            "unsigned": True,
        }
        self.report["artifacts"]["beta_delivery"] = details
        self._persist()
        return details

    def packaged_diagnostics(self) -> None:
        output = self.diagnostics_dir / "packaged-runtime.json"
        runtime_dir = self.diagnostics_dir / "packaged-runtime-state"
        command = [
            str(self.executable),
            "--workspace",
            str(runtime_dir / "workspace.sqlite"),
            "--evidence-db",
            str(runtime_dir / "evidence.sqlite"),
            "--diagnose",
            "--diagnostics-output",
            str(output),
        ]
        self.command_step(
            step_id="packaged-diagnostics",
            title="正式 EXE 运行时依赖诊断",
            command=command,
            required_result=output,
        )
        payload = _read_json(output)
        if not bool(payload.get("ok")):
            raise GateFailure("Packaged runtime diagnostics reported ok=false")
        tool_loop = payload.get("pi_tool_loop")
        if (
            not isinstance(tool_loop, dict)
            or not bool(tool_loop.get("ok"))
            or int(tool_loop.get("tool_calls", 0) or 0) < 1
            or not bool(tool_loop.get("done"))
            or int(tool_loop.get("fallback_count", -1)) != 0
        ):
            raise GateFailure("Packaged runtime diagnostics did not complete a Pi tool loop")

    def packaged_health(self) -> dict[str, Any]:
        port = _find_available_port()
        runtime_dir = self.diagnostics_dir / "packaged-health-state"
        command = [
            str(self.executable),
            "--workspace",
            str(runtime_dir / "workspace.sqlite"),
            "--evidence-db",
            str(runtime_dir / "evidence.sqlite"),
            "--serve-only",
            "--port",
            str(port),
        ]
        process = subprocess.Popen(command, cwd=self.package_dir)
        try:
            deadline = time.monotonic() + 45
            health: dict[str, Any] | None = None
            last_error = ""
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise GateFailure(f"Packaged server exited early with code {process.returncode}")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    break
                except Exception as error:
                    last_error = str(error)
                    time.sleep(0.5)
            if not health or health.get("status") != "ok":
                raise GateFailure(f"Packaged /api/health did not become ready: {last_error}")
            if str(health.get("build_id")) != self.build_id:
                raise GateFailure("Packaged health endpoint reported a different build id")
            return {"port": port, "health": health, "process_id": process.pid}
        finally:
            _stop_process(process)

    def packaged_liveness(self) -> dict[str, Any]:
        runtime_dir = self.diagnostics_dir / "packaged-desktop-state"
        command = [
            str(self.executable),
            "--workspace",
            str(runtime_dir / "workspace.sqlite"),
            "--evidence-db",
            str(runtime_dir / "evidence.sqlite"),
            "--title",
            f"ScanSci release verification {self.build_id}",
        ]
        process = subprocess.Popen(command, cwd=self.package_dir)
        try:
            time.sleep(15)
            if process.poll() is not None:
                raise GateFailure(f"Packaged desktop exited early with code {process.returncode}")
            return {"process_id": process.pid, "executable_path": str(self.executable), "survived_seconds": 15}
        finally:
            _stop_process(process)

    def visual_acceptance(self) -> dict[str, Any]:
        visual = dict(self.contract["visual_acceptance"])
        evidence_path = self.visual_evidence_dir / str(visual["evidence_file"])
        if not evidence_path.is_file():
            self.write_visual_template(evidence_path)
            raise GatePending(f"Desktop visual evidence is required: {evidence_path}")
        evidence = _read_json(evidence_path)
        expected_path = os.path.normcase(str(self.executable.resolve()))
        actual_path = os.path.normcase(str(Path(str(evidence.get("executable_path", ""))).resolve()))
        if actual_path != expected_path:
            raise GateFailure("Visual evidence executable_path does not match this release")
        if str(evidence.get("build_id")) != self.build_id:
            raise GateFailure("Visual evidence build_id does not match this release")
        expected_hash = str(self.report["artifacts"].get("executable_sha256", ""))
        if str(evidence.get("executable_sha256", "")).casefold() != expected_hash.casefold():
            raise GateFailure("Visual evidence executable_sha256 does not match this release")
        checks = dict(evidence.get("checks", {}) or {})
        failed = [name for name in list(visual["required_checks"]) if checks.get(name) is not True]
        if failed:
            raise GatePending("Desktop checks are not confirmed: " + ", ".join(failed))
        screenshots: dict[str, str] = {}
        for name in list(visual["required_screenshots"]):
            path = self.visual_evidence_dir / str(name)
            if not path.is_file() or path.stat().st_size <= 0:
                raise GatePending(f"Required desktop screenshot is missing: {path}")
            screenshots[str(name)] = str(path)
        return {"evidence_file": str(evidence_path), "screenshots": screenshots, "checks": checks}

    def write_visual_template(self, evidence_path: Path) -> None:
        visual = dict(self.contract["visual_acceptance"])
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        if evidence_path.exists():
            return
        payload = {
            "build_id": self.build_id,
            "executable_path": str(self.executable),
            "executable_sha256": str(self.report.get("artifacts", {}).get("executable_sha256", "")),
            "checked_at": None,
            "checks": {name: False for name in list(visual["required_checks"])},
            "screenshots": list(visual["required_screenshots"]),
            "notes": "只在真实桌面 EXE 中逐项验收后改为 true；截图必须来自同一 build_id。",
        }
        _write_json(evidence_path, payload)

    def plan(self) -> dict[str, Any]:
        steps = ["scope-contract"]
        if self.profile == "release":
            steps.append("signing-environment")
        if self.requires_runtime_component_channels:
            steps.append("runtime-component-channels")
        groups = ["targeted_commands"]
        if self.profile in {"source", "beta", "release"}:
            groups.extend(["full_commands", "real_verifications"])
        for group in groups:
            steps.extend(str(item["id"]) for item in list(self.contract.get(group, []) or []))
        if self.is_packaged_profile:
            steps.extend(
                [
                    "build-desktop",
                    "package-integrity",
                    "build-installer",
                    "installer-integrity",
                    "installer-installation",
                    "packaged-diagnostics",
                    "packaged-health",
                    "packaged-liveness",
                ]
            )
            if self.profile == "release":
                steps.insert(steps.index("package-integrity"), "sign-desktop-executable")
                steps[steps.index("build-installer"):steps.index("build-installer")] = [
                    "build-update-bundle",
                    "update-bundle-integrity",
                ]
                steps.append("visual-acceptance")
            else:
                steps.append("beta-delivery")
        self.report.update(status="planned", finished_at=_utc_now(), planned_steps=steps)
        self._persist()
        return self.report

    def run(self) -> int:
        self.report.update(status="running", finished_at=None)
        self._persist()
        try:
            self.internal_step(
                "scope-contract",
                "单一 P0、验收标准与非目标契约",
                lambda: {
                    "p0_objective": self.scope["p0_objective"],
                    "acceptance_ids": [item["id"] for item in self.scope["acceptance"]],
                    "non_goals": self.scope["non_goals"],
                },
            )
            if self.profile == "release":
                self.internal_step(
                    "signing-environment",
                    "正式版签名环境预检",
                    self.signing_environment,
                )
            if self.requires_runtime_component_channels:
                self.internal_step(
                    "runtime-component-channels",
                    "独立运行组件下载通道",
                    self.verify_runtime_component_channels,
                )
            self.run_configured_commands("targeted_commands")
            if self.profile in {"source", "beta", "release"}:
                self.run_configured_commands("full_commands")
                self.run_configured_commands("real_verifications")
            if self.is_packaged_profile:
                self.build_desktop()
                if self.profile == "release":
                    self.sign_desktop_executable()
                self.internal_step("package-integrity", "包完整性、build_id 与 SHA256", self.verify_package)
                if self.profile == "release":
                    self.build_update_bundle()
                    self.internal_step(
                        "update-bundle-integrity",
                        "完整 ZIP、blockmap、stable.json 与包身份一致性",
                        self.verify_update_bundle,
                    )
                self.build_installer()
                self.internal_step("installer-integrity", "安装包、来源 EXE 与 SHA256", self.verify_installer_manifest)
                self.verify_installer_installation()
                self.packaged_diagnostics()
                self.internal_step("packaged-health", "打包 EXE 本地服务健康检查", self.packaged_health)
                self.internal_step("packaged-liveness", "打包桌面进程存活检查", self.packaged_liveness)
                if self.profile == "release":
                    self.internal_step("visual-acceptance", "正式 EXE 人工交互与视觉证据", self.visual_acceptance)
                else:
                    self.internal_step("beta-delivery", "受邀内测交付清单、校验值与反馈模板", self.prepare_beta_delivery)
        except GatePending as error:
            self.report.update(status="awaiting_visual_evidence", finished_at=_utc_now(), message=str(error))
            self._persist()
            _emit_console(f"[pending] {error}")
            _emit_console(f"Resume without rebuilding: .\\scripts\\release_gate.ps1 -Profile release -ResumeReport \"{self.report_path}\"")
            return 2
        except Exception as error:
            self.report.update(status="failed", finished_at=_utc_now(), message=str(error))
            self._persist()
            _emit_console(f"[failed] {error}", file=sys.stderr)
            return 1
        self.report.update(status="passed", finished_at=_utc_now(), message="All mandatory gates passed")
        self._persist()
        _emit_console(f"[passed] {self.report_path}")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run staged ScanSci source or Windows release gates.")
    parser.add_argument("--profile", choices=("targeted", "source", "beta", "release"), default="release")
    parser.add_argument("--contract", default="config/release-gate.json")
    parser.add_argument("--scope", default="")
    parser.add_argument("--knowledge-source", default=r"D:\光伏生态文献")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--build-id", default="")
    parser.add_argument("--resume-report", default="")
    parser.add_argument("--promote-report", default="")
    parser.add_argument("--visual-evidence-dir", default="")
    parser.add_argument("--plan-only", action="store_true")
    return parser


def default_output_root(*, profile: str, plan_only: bool) -> Path:
    if plan_only:
        return PROJECT_ROOT / ".scansci-diagnostics" / "release-gate-plans"
    if profile == "release":
        return PROJECT_ROOT / "releases"
    if profile == "beta":
        return PROJECT_ROOT / "internal-beta-releases"
    return PROJECT_ROOT / ".scansci-diagnostics" / "release-gates"


def validate_build_id(build_id: str) -> bool:
    """Accept a filesystem-safe build token, including SemVer build metadata."""

    return bool(build_id) and all(character.isalnum() or character in "._+-" for character in build_id)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.resume_report and args.promote_report:
        _emit_console("[failed] --resume-report and --promote-report are mutually exclusive", file=sys.stderr)
        return 1
    contract_path = _resolve_from_root(args.contract)
    contract = _read_json(contract_path)
    scope_path = _resolve_from_root(args.scope or str(contract.get("scope_file", "config/release-scope.json")))
    build_id = args.build_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    if not validate_build_id(build_id):
        _emit_console(
            "[failed] build-id may contain only letters, numbers, dots, underscores, plus signs, and hyphens",
            file=sys.stderr,
        )
        return 1
    if args.output_root:
        output_root = _resolve_from_root(args.output_root)
    else:
        output_root = default_output_root(profile=args.profile, plan_only=bool(args.plan_only))
    try:
        gate = ReleaseGate(
            contract_path=contract_path,
            scope_path=scope_path,
            profile=args.profile,
            knowledge_source=_resolve_from_root(args.knowledge_source),
            output_root=output_root,
            build_id=build_id,
            resume_report=_resolve_from_root(args.resume_report) if args.resume_report else None,
            promote_report=_resolve_from_root(args.promote_report) if args.promote_report else None,
            visual_evidence_dir=_resolve_from_root(args.visual_evidence_dir) if args.visual_evidence_dir else None,
        )
    except Exception as error:
        _emit_console(f"[failed] {error}", file=sys.stderr)
        return 1
    if args.plan_only:
        report = gate.plan()
        _emit_console(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    return gate.run()


if __name__ == "__main__":
    raise SystemExit(main())
