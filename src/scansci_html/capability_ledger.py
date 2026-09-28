"""Turn-scoped capability accounting for ScanSci agent runs.

Reasonix treats a capability as a host-owned fact rather than a claim made by
the model.  This module keeps the same boundary deliberately small: a ledger
is created for one user turn, records the observed tool outcomes, and decides
whether the turn is ready to deliver or needs a safe recovery.

The ledger is intentionally independent from Pi and from the web UI.  It can
therefore be used by the synchronous path, the streaming path, and tests
without creating a second tool registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping, Sequence


CAPABILITY_LEDGER_SCHEMA = "scansci.capability-ledger.v1"

PENDING = "pending"
INVOKED = "invoked"
SUCCEEDED = "succeeded"
FAILED = "failed"
UNAVAILABLE = "unavailable"
DECLINED = "declined"

# Repeating a read-only query cannot create a duplicate file or external
# write.  This set is intentionally explicit; unknown tools are not retried
# automatically because a future plugin may mutate state.
_READ_ONLY_TOOLS = frozenset(
    {
        "inspect_workspace",
        "inspect_available_tools",
        "search_tools",
        "read_task_documents",
        "summarize_documents",
        "check_task_completion",
        "search_local_evidence",
        "kb_search",
        "zotero_search",
        "zotero_status",
        "zotero_fulltext",
        "zotero_attachment",
        "zotero_export_bibtex",
        "zotero_citations",
        "obsidian_status",
        "obsidian_search",
        "obsidian_read",
        "obsidian_backlinks",
        "build_verified_answer",
        "verify_doi",
        "discover_papers",
        "search_web",
        "search_journal",
        "audit_references",
        "build_presentation_outline",
        "self_assess",
    }
)

_TRANSIENT_FAILURE_MARKERS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "temporary failure",
    "connection reset",
    "connection refused",
    "connection aborted",
    "eof",
    "rate limit",
    "rate_limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "5xx",
    "connection error",
    "network error",
    "empty response",
    "invalid protocol",
    # Note: bare substrings like "json" or "ssl" are deliberately absent.
    # A permanent provider bug ("invalid JSON response", "SSL certificate
    # error") must not be classified as transient, or retries burn model
    # calls and latency on a failure that cannot succeed.
)
_UNAVAILABLE_MARKERS = (
    "unknown tool",
    "tool is unavailable",
    "capability lease denied",
    "not installed",
    "not found",
    "missing",
    "disabled",
    "unavailable",
    "does not exist",
    "no such file",
)


def _clean_name(value: object) -> str:
    return str(value or "").strip()


def _normalize_groups(groups: Iterable[Iterable[object]] | None) -> tuple[tuple[str, ...], ...]:
    normalized: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups or ():
        names = tuple(sorted({_clean_name(name) for name in group if _clean_name(name)}))
        if names and names not in seen:
            normalized.append(names)
            seen.add(names)
    return tuple(normalized)


def classify_failure(error: object) -> dict[str, Any]:
    """Classify a tool failure without exposing the provider's raw details.

    This is not a promise that every failure is recoverable.  It is a
    conservative hint used only for read-only retries and diagnostics.
    """

    raw = re.sub(r"\s+", " ", str(error or "")).strip()[:500]
    normalized = raw.casefold()
    unavailable = any(marker in normalized for marker in _UNAVAILABLE_MARKERS)
    retryable = not unavailable and any(marker in normalized for marker in _TRANSIENT_FAILURE_MARKERS)
    return {
        "error": raw,
        "retryable": retryable,
        "unavailable": unavailable,
        "code": "capability_unavailable" if unavailable else "capability_transient_failure" if retryable else "capability_failed",
    }


@dataclass
class CapabilityLedger:
    """Observed capability outcomes for one host-controlled turn."""

    required_groups: tuple[tuple[str, ...], ...] = ()
    unavailable_tools: frozenset[str] = frozenset()
    outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)
    recovery_attempts: int = 0
    reminder_sent: bool = False

    def __init__(
        self,
        required_groups: Sequence[Iterable[object]] | None = None,
        *,
        unavailable_tools: Iterable[object] = (),
    ) -> None:
        self.required_groups = _normalize_groups(required_groups)
        self.unavailable_tools = frozenset(
            _clean_name(name) for name in unavailable_tools if _clean_name(name)
        )
        self.outcomes = {
            name: {"status": UNAVAILABLE, "reason": "host preflight marked unavailable"}
            for name in sorted(self.unavailable_tools)
        }
        self.recovery_attempts = 0
        self.reminder_sent = False

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any] | None) -> "CapabilityLedger":
        payload = dict(contract or {})
        return cls(
            payload.get("required_tool_groups", []),
            unavailable_tools=payload.get("unavailable_tools", []),
        )

    @classmethod
    def from_tool_calls(
        cls,
        required_groups: Sequence[Iterable[object]] | None,
        tool_calls: Iterable[Mapping[str, Any]] | None,
    ) -> "CapabilityLedger":
        ledger = cls(required_groups)
        for item in tool_calls or ():
            if not isinstance(item, Mapping):
                continue
            name = _clean_name(item.get("name"))
            if not name:
                continue
            names = [name]
            semantic_name = _clean_name(item.get("capability_id") or item.get("capability"))
            if semantic_name and semantic_name not in names:
                names.append(semantic_name)
            status = _clean_name(item.get("status")).casefold()
            if status in {"completed", "succeeded", "ok", "success"}:
                for target in names:
                    ledger.mark_completed(target, result=item.get("result"))
            elif status in {"failed", "error"}:
                for target in names:
                    ledger.mark_failed(target, item.get("error", "tool failed"))
            elif status in {"unavailable", "disabled"}:
                for target in names:
                    ledger.mark_unavailable(target, item.get("error", "tool unavailable"))
            elif status in {"declined", "denied"}:
                for target in names:
                    ledger.mark_declined(target, item.get("error", "tool declined"))
            else:
                for target in names:
                    ledger.mark_invoked(target)
        return ledger

    def _set_outcome(self, name: str, status: str, **details: Any) -> None:
        if not name:
            return
        previous = dict(self.outcomes.get(name, {}))
        # A later success is authoritative.  A stale failure from a previous
        # recovery attempt must never make a successful turn look incomplete.
        if previous.get("status") == SUCCEEDED and status != SUCCEEDED:
            return
        previous.update({"status": status, **details})
        self.outcomes[name] = previous

    def mark_invoked(self, name: object) -> None:
        normalized = _clean_name(name)
        if normalized and self.outcomes.get(normalized, {}).get("status") != SUCCEEDED:
            self._set_outcome(normalized, INVOKED)

    def mark_completed(self, name: object, *, result: Any = None) -> None:
        normalized = _clean_name(name)
        if normalized:
            self._set_outcome(normalized, SUCCEEDED, result_observed=result is not None)

    def mark_failed(self, name: object, error: object = "") -> None:
        normalized = _clean_name(name)
        if not normalized:
            return
        classification = classify_failure(error)
        if classification["unavailable"]:
            self.mark_unavailable(normalized, classification["error"])
            return
        self._set_outcome(normalized, FAILED, **classification)

    def mark_unavailable(self, name: object, reason: object = "") -> None:
        normalized = _clean_name(name)
        if normalized:
            self._set_outcome(normalized, UNAVAILABLE, reason=str(reason or "tool unavailable")[:500])

    def mark_declined(self, name: object, reason: object = "") -> None:
        normalized = _clean_name(name)
        if normalized:
            self._set_outcome(normalized, DECLINED, reason=str(reason or "tool declined")[:500])

    def record_event(self, event: Mapping[str, Any] | None) -> None:
        payload = dict(event or {})
        event_type = _clean_name(payload.get("type")).casefold()
        name = _clean_name(payload.get("name"))
        semantic_name = _clean_name(payload.get("capability_id") or payload.get("capability"))
        names = [name]
        if semantic_name and semantic_name not in names:
            names.append(semantic_name)
        if event_type in {"status", "status.update"}:
            status = _clean_name(payload.get("status")).casefold()
            if status in {"tool_started", "tool_call_started"}:
                for target in names:
                    self.mark_invoked(target)
            elif status in {"tool_failed", "tool_call_failed"}:
                for target in names:
                    self.mark_failed(target, payload.get("error", "tool failed"))
            elif status in {"tool_completed", "tool_call_completed"}:
                for target in names:
                    self.mark_completed(target, result=payload.get("result"))
        elif event_type == "tool.completed":
            for target in names:
                self.mark_completed(target, result=payload.get("result"))
        elif event_type == "tool.failed":
            for target in names:
                self.mark_failed(target, payload.get("error", "tool failed"))

    def mark_recovery_attempt(self) -> None:
        self.recovery_attempts += 1
        self.reminder_sent = True

    def _group_satisfied(self, group: tuple[str, ...]) -> bool:
        return any(self.outcomes.get(name, {}).get("status") == SUCCEEDED for name in group)

    def missing_groups(self) -> list[tuple[str, ...]]:
        return [group for group in self.required_groups if not self._group_satisfied(group)]

    def unavailable_groups(self) -> list[tuple[str, ...]]:
        return [
            group
            for group in self.missing_groups()
            if all(self.outcomes.get(name, {}).get("status") == UNAVAILABLE for name in group)
        ]

    def failed_groups(self) -> list[tuple[str, ...]]:
        return [
            group
            for group in self.missing_groups()
            if any(self.outcomes.get(name, {}).get("status") == FAILED for name in group)
        ]

    def pending_groups(self) -> list[tuple[str, ...]]:
        return [group for group in self.missing_groups() if group not in self.failed_groups() and group not in self.unavailable_groups()]

    @property
    def ready(self) -> bool:
        return not self.missing_groups()

    @property
    def has_non_idempotent_effect(self) -> bool:
        return any(
            name not in _READ_ONLY_TOOLS
            and outcome.get("status") in {INVOKED, SUCCEEDED, FAILED}
            for name, outcome in self.outcomes.items()
        )

    @property
    def has_observed_failure(self) -> bool:
        return any(outcome.get("status") == FAILED for outcome in self.outcomes.values())

    def safe_retry_allowed(self) -> bool:
        """Return whether one fresh attempt is safe for this ledger."""

        if (
            self.ready
            or self.recovery_attempts >= 1
            or self.has_non_idempotent_effect
            or bool(self.unavailable_groups())
        ):
            return False
        failed = [
            (name, outcome)
            for name, outcome in self.outcomes.items()
            if outcome.get("status") == FAILED
        ]
        if failed and not all(
            name in _READ_ONLY_TOOLS and bool(outcome.get("retryable", False))
            for name, outcome in failed
        ):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        missing = self.missing_groups()
        status = "ready" if not missing else "blocked" if self.unavailable_groups() or self.failed_groups() else "pending"
        return {
            "schema_version": CAPABILITY_LEDGER_SCHEMA,
            "status": status,
            "required_groups": [list(group) for group in self.required_groups],
            "missing_groups": [list(group) for group in missing],
            "failed_groups": [list(group) for group in self.failed_groups()],
            "unavailable_groups": [list(group) for group in self.unavailable_groups()],
            "pending_groups": [list(group) for group in self.pending_groups()],
            "outcomes": {name: dict(value) for name, value in sorted(self.outcomes.items())},
            "recovery_attempts": self.recovery_attempts,
            "reminder_sent": self.reminder_sent,
            "safe_retry_allowed": self.safe_retry_allowed(),
        }

    def delivery_error(self, *, cause: object = "") -> "CapabilityDeliveryError":
        missing = self.missing_groups()
        rendered = " AND ".join("/".join(group) for group in missing) or "unknown capability"
        unavailable = bool(self.unavailable_groups()) and len(self.unavailable_groups()) == len(missing)
        code = "capability_unavailable" if unavailable else "required_capability_failed" if self.failed_groups() else "required_capability_missing"
        if unavailable:
            message = f"ScanSci required tool loop failed because the capability is unavailable: {rendered}."
            actions = [{"id": "open_settings", "label": "检查能力配置", "kind": "settings"}]
        elif self.safe_retry_allowed():
            message = f"ScanSci required tool loop failed: {rendered}. A safe recovery is available."
            actions = [
                {"id": "retry", "label": "自动重试", "kind": "retry"},
                {"id": "branch", "label": "保留当前结果并分支", "kind": "branch"},
            ]
        else:
            message = f"ScanSci required tool loop failed: {rendered}."
            actions = [{"id": "branch", "label": "保留当前结果并分支", "kind": "branch"}]
        failure = {
            "code": code,
            "message": message,
            "detail": str(cause or "").strip()[:1000],
            "retryable": bool(self.safe_retry_allowed()),
            "recovery_actions": actions,
            "capability_ledger": self.to_dict(),
        }
        return CapabilityDeliveryError(message, failure=failure)


class CapabilityDeliveryError(RuntimeError):
    """Raised when host verification finds an unmet required capability."""

    def __init__(self, message: str, *, failure: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.failure = dict(failure or {})


__all__ = [
    "CAPABILITY_LEDGER_SCHEMA",
    "CapabilityDeliveryError",
    "CapabilityLedger",
    "classify_failure",
]
