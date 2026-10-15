"""Fail-closed host authorization for Pi bridge tool calls.

The Node sidecar filters model-visible schemas, but it is not the final
authority boundary.  Every call that crosses into Python is checked again
against the current request's hard lease and the host-owned capability
descriptor before any dispatcher code runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4


class ToolAuthorizationError(PermissionError):
    """Raised before dispatch when a Pi tool call lacks current authority."""

    def __init__(self, message: str, *, code: str = "authorization_denied") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ApprovalToken:
    """An in-memory, request-scoped proof of an explicit plan approval."""

    request_id: str
    token_id: str


@dataclass(frozen=True)
class ToolAuthorizationDecision:
    allowed: bool
    tool_name: str
    request_id: str
    risk_level: str
    idempotent: bool
    approval_required: bool = False


_RISK_RANK = {
    "none": 0,
    "direct": 0,
    "read_only": 1,
    "reversible": 2,
    "high": 3,
    "approval_required": 3,
}
_LEGACY_COMPAT_TOOL_IDS = frozenset({
    "delegate_scientific_agents",
    "list_scientific_agents",
    "collect_scientific_agents",
    "cancel_scientific_agents",
})


def _explicit_decision(response: Mapping[str, Any] | None) -> str:
    raw = dict(response or {})
    for key in ("decision", "action", "value"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def approval_token_from_response(
    request_id: str,
    response: Mapping[str, Any] | None,
) -> ApprovalToken | None:
    """Mint a token only for the literal, explicit decision ``approve``."""

    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id or _explicit_decision(response) != "approve":
        return None
    return ApprovalToken(request_id=normalized_request_id, token_id=uuid4().hex)


def authorize_tool_call(
    *,
    tool_name: str,
    contract: Mapping[str, Any] | None,
    descriptor: Mapping[str, Any] | None,
    request_id: str,
    active_request_id: str,
    approval_token: ApprovalToken | None = None,
    call_count: int = 0,
) -> ToolAuthorizationDecision:
    """Authorize one bridge call without deriving authority from its name.

    Missing and explicit-empty tool leases are intentionally distinct for
    diagnostics, but both deny executable domain tools.  The descriptor must
    come from the host capability catalogue; model-supplied risk metadata is
    never accepted.
    """

    normalized_name = str(tool_name or "").strip()
    event_request_id = str(request_id or "").strip()
    current_request_id = str(active_request_id or "").strip()
    if not event_request_id or event_request_id != current_request_id:
        raise ToolAuthorizationError("Tool call belongs to another request", code="request_mismatch")
    if not normalized_name:
        raise ToolAuthorizationError("Tool call has no capability id", code="missing_capability_id")

    raw_contract = dict(contract or {})
    if "allowed_tools" not in raw_contract:
        raise ToolAuthorizationError("Task contract has a missing tool capability lease", code="missing_lease")
    raw_allowed = raw_contract.get("allowed_tools")
    if not isinstance(raw_allowed, (list, tuple, set, frozenset)):
        raise ToolAuthorizationError("Task contract tool capability lease is malformed", code="malformed_lease")
    allowed = {str(value).strip() for value in raw_allowed if str(value).strip()}
    if not allowed:
        raise ToolAuthorizationError("Task contract has an explicit empty tool capability lease", code="empty_lease")
    if normalized_name not in allowed:
        raise ToolAuthorizationError(
            f"Capability lease denied tool {normalized_name}; it is outside the current task contract",
            code="outside_lease",
        )

    raw_descriptor = dict(descriptor or {})
    if not raw_descriptor or str(raw_descriptor.get("id", "")).strip() != normalized_name:
        raise ToolAuthorizationError(f"Host capability descriptor is unavailable for {normalized_name}", code="descriptor_unavailable")
    descriptor_status = str(raw_descriptor.get("status", "ready"))
    if descriptor_status != "ready" and not (
        descriptor_status == "legacy" and normalized_name in _LEGACY_COMPAT_TOOL_IDS
    ):
        raise ToolAuthorizationError(f"Host capability {normalized_name} is not ready", code="capability_unready")
    risk_level = str(raw_descriptor.get("risk_level", "")).strip()
    if risk_level not in _RISK_RANK:
        raise ToolAuthorizationError(f"Host capability {normalized_name} has an unknown risk effect", code="unknown_risk")
    risk_ceiling = str(raw_contract.get("risk_level", "none") or "none").strip()
    if _RISK_RANK[risk_level] > _RISK_RANK.get(risk_ceiling, -1):
        raise ToolAuthorizationError(
            f"Capability lease denied {risk_level} tool {normalized_name}; risk ceiling is {risk_ceiling}",
            code="risk_ceiling",
        )

    max_calls = raw_contract.get("max_tool_budget", 32)
    try:
        normalized_max_calls = max(0, min(32, int(max_calls)))
    except (TypeError, ValueError) as error:
        raise ToolAuthorizationError("Task contract tool-call budget is malformed", code="malformed_budget") from error
    if int(call_count) >= normalized_max_calls:
        raise ToolAuthorizationError("Task contract tool-call budget is exhausted", code="budget_exhausted")

    approval_required = bool(raw_contract.get("requires_plan", False)) and risk_level != "read_only"
    approval_required = approval_required or risk_level == "high"
    if approval_required and (
        approval_token is None or approval_token.request_id != current_request_id
    ):
        raise ToolAuthorizationError(f"Tool {normalized_name} requires an approved plan for this request", code="approval_required")

    return ToolAuthorizationDecision(
        allowed=True,
        tool_name=normalized_name,
        request_id=current_request_id,
        risk_level=risk_level,
        idempotent=bool(raw_descriptor.get("idempotent", False)),
        approval_required=approval_required,
    )


__all__ = [
    "ApprovalToken",
    "ToolAuthorizationDecision",
    "ToolAuthorizationError",
    "approval_token_from_response",
    "authorize_tool_call",
]
