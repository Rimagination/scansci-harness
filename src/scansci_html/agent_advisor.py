"""Deterministic completion advisor and run metrics for scientific agents.

The advisor deliberately does not call another model and cannot execute tools.
It reviews the durable task record after delivery, reports structured gaps, and
leaves the host responsible for deciding whether a follow-up task is needed.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


ADVISOR_VERSION = "scansci.contract-advisor.v1"


def classify_failure(error: object) -> str:
    """Map a persisted error into a product-level recovery category."""

    text = str(error or "").casefold()
    if not text:
        return "none"
    if any(token in text for token in ("permission", "approval", "forbidden", "unauthor", "disabled")):
        return "permission_denied"
    if any(token in text for token in ("not found", "no usable", "does not exist", "needs-data")):
        return "capability_or_data_missing"
    if any(token in text for token in ("timeout", "timed out", "connection", "network", "429", "502", "503", "504")):
        return "source_or_transport_unavailable"
    if any(token in text for token in ("evidence", "citation", "full text", "insufficient")):
        return "evidence_insufficient"
    return "execution_or_model_failure"


def run_metrics(run: dict[str, Any]) -> dict[str, Any]:
    """Produce redaction-safe outcome metrics from a durable task record."""

    calls = [dict(item) for item in list(run.get("tool_calls", []) or []) if isinstance(item, dict)]
    events = [dict(item) for item in list(run.get("events", []) or []) if isinstance(item, dict)]
    statuses = Counter(str(item.get("status", "")) for item in calls)
    tool_names = Counter(str(item.get("tool_name", "")) for item in calls)
    failures = [str(item.get("error_message", "")) for item in calls if str(item.get("status", "")) == "failed"]
    run_error = dict(run.get("error", {}) or {}).get("message", "")
    failure_categories = Counter(classify_failure(item) for item in [*failures, run_error])
    failure_categories.pop("none", None)
    evidence_links = list(dict(run.get("output_artifact", {}) or {}).get("evidence_links", []) or [])
    return {
        "schema_version": "scansci.run-metrics.v1",
        "run_id": str(run.get("run_id", "")),
        "status": str(run.get("status", "")),
        "stage_completion_ratio": _stage_ratio(run),
        "tool_calls": {
            "total": len(calls),
            "completed": int(statuses.get("completed", 0)),
            "failed": int(statuses.get("failed", 0)),
            "cancelled": int(statuses.get("cancelled", 0)),
            "duration_ms": sum(max(0, int(item.get("duration_ms", 0) or 0)) for item in calls),
            "by_name": {name: count for name, count in tool_names.items() if name},
        },
        "evidence_link_count": len(evidence_links),
        "event_count": len(events),
        "failure_categories": dict(failure_categories),
    }


def review_research_run(run: dict[str, Any]) -> dict[str, Any]:
    """Assess durable completion against the task contract without side effects."""

    contract = dict(run.get("task_contract", {}) or {})
    artifact = dict(run.get("output_artifact", {}) or {})
    completed_tools = {
        str(item.get("tool_name", ""))
        for item in list(run.get("tool_calls", []) or [])
        if isinstance(item, dict) and str(item.get("status", "")) == "completed"
    }
    findings: list[dict[str, str]] = []
    if str(run.get("status", "")) != "completed":
        findings.append({"code": "run_not_completed", "severity": "high", "message": "任务未处于已完成状态。"})
    if not artifact:
        findings.append({"code": "output_missing", "severity": "high", "message": "任务没有可追溯的最终产物。"})
    required_groups = [list(group) for group in list(contract.get("required_tool_groups", []) or []) if group]
    for group in required_groups:
        if not any(_tool_requirement_met(str(name), completed_tools, artifact) for name in group):
            findings.append(
                {
                    "code": "required_tool_group_missing",
                    "severity": "medium",
                    "message": f"未观察到必需工具组的完成记录：{' / '.join(str(name) for name in group)}。",
                }
            )
    profile = dict(contract.get("task_profile", {}) or {})
    evidence_required = str(profile.get("evidence_policy", "")) in {"required", "strict"}
    if evidence_required and not list(artifact.get("evidence_links", []) or []):
        findings.append(
            {
                "code": "evidence_links_missing",
                "severity": "high",
                "message": "证据型任务的最终产物没有可定位的证据链接。",
            }
        )
    if any(str(item.get("status", "")) == "failed" for item in list(run.get("tool_calls", []) or []) if isinstance(item, dict)):
        findings.append(
            {
                "code": "tool_failures_present",
                "severity": "medium",
                "message": "任务存在失败工具调用；交付前应确认失败不影响结论。",
            }
        )
    high = any(item["severity"] == "high" for item in findings)
    medium = any(item["severity"] == "medium" for item in findings)
    return {
        "schema_version": ADVISOR_VERSION,
        "run_id": str(run.get("run_id", "")),
        "verdict": "insufficient" if high else "needs_review" if medium else "passed",
        "findings": findings,
        "metrics": run_metrics(run),
        "recommended_next_action": _next_action(findings),
    }


def _tool_requirement_met(
    name: str, completed_tools: set[str], artifact: dict[str, Any]
) -> bool:
    """Match contract semantics to actual completed calls, including Host calls."""

    if name in completed_tools:
        return True
    # The background academic search performs paper discovery and persists
    # its result under the Host tool name. It proves only that capability;
    # it does not prove browser access, evidence verification or Pi routing.
    if name == "discover_papers":
        return "scansci.academic.search" in completed_tools
    if name not in {"search_local_evidence", "build_verified_answer"}:
        return False
    # Background evidence pipelines persist one Host call. Require both the
    # completed call and navigable evidence before accepting its internal stages.
    if not completed_tools.intersection({"scansci.evidence.ask", "scansci.review.write"}):
        return False

    artifact_type = str(artifact.get("artifact_type", "")).strip().casefold()
    if artifact_type not in {"evidence_answer", "literature_review", "review"}:
        return False
    evidence_links = [
        item for item in list(artifact.get("evidence_links", []) or []) if isinstance(item, dict)
    ]
    if not evidence_links:
        return False
    payload = dict(artifact.get("payload", {}) or {})
    verification = dict(payload.get("citation_verification", {}) or {})
    # A non-empty link list is already the persisted source-navigation gate;
    # honour an explicit failed verification and leave the contract unmet.
    if verification and verification.get("passed") is False:
        return False
    return True


def _stage_ratio(run: dict[str, Any]) -> float:
    counts = dict(run.get("stage_counts", {}) or {})
    total = max(0, int(counts.get("total", 0) or 0))
    completed = max(0, int(counts.get("completed", 0) or 0))
    return round(completed / total, 4) if total else 0.0


def _next_action(findings: Iterable[dict[str, str]]) -> str:
    codes = {str(item.get("code", "")) for item in findings}
    if "evidence_links_missing" in codes:
        return "run_evidence_verification"
    if "required_tool_group_missing" in codes:
        return "resume_required_tools"
    if "tool_failures_present" in codes:
        return "review_failed_tools"
    if "output_missing" in codes:
        return "rebuild_delivery"
    return "none"


__all__ = ["ADVISOR_VERSION", "classify_failure", "review_research_run", "run_metrics"]
