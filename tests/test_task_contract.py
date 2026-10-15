from __future__ import annotations

import json
from pathlib import Path

import pytest

from scansci_html.task_contract import TASK_CONTRACT_SCHEMA, TaskContract
from scansci_html.tool_authorization import (
    ToolAuthorizationError,
    approval_token_from_response,
    authorize_tool_call,
)


def test_task_contract_reads_legacy_v1_and_writes_only_v2() -> None:
    contract = TaskContract.from_payload(
        {
            "schema_version": "scansci.task-contract.v1",
            "version": 1,
            "goal": "legacy",
            "allowed_tools": ["inspect_workspace"],
        }
    )

    payload = contract.to_dict()

    assert TASK_CONTRACT_SCHEMA == "scansci.task-contract.v2"
    assert payload["schema_version"] == TASK_CONTRACT_SCHEMA
    assert payload["version"] == 2
    assert payload["allowed_tools"] == ["inspect_workspace"]


def test_task_contract_preserves_missing_versus_explicit_empty_tool_lease() -> None:
    version = {"schema_version": TASK_CONTRACT_SCHEMA, "version": 2}
    omitted = TaskContract.from_payload({**version, "goal": "omitted"}).to_dict()
    explicit = TaskContract.from_payload({**version, "goal": "empty", "allowed_tools": []}).to_dict()

    assert "allowed_tools" not in omitted
    assert explicit["allowed_tools"] == []


def test_v2_contract_fixture_round_trips_without_expanding_initial_tools() -> None:
    fixture = Path(__file__).parent / "fixtures" / "pi_task_contract_v2.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    round_tripped = TaskContract.from_payload(payload).to_dict()

    assert round_tripped["schema_version"] == TASK_CONTRACT_SCHEMA
    assert round_tripped["allowed_tools"] == ["inspect_workspace", "kb_search"]
    assert round_tripped["initial_tools"] == ["inspect_workspace"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": "scansci.task-contract.v999"},
        {"schema_version": "scansci.task-contract.v999", "version": 999},
        {"version": 999},
        {"version": "999"},
        {"schema_version": "scansci.task-contract.v1", "version": 2},
        {"schema_version": "scansci.task-contract.v2", "version": "1"},
        {"schema_version": "scansci.task-contract.v2", "version": {"major": 2}},
    ],
)
def test_unknown_conflicting_or_malformed_contract_versions_drop_authority(
    payload: dict[str, object],
) -> None:
    migrated = TaskContract.from_payload({
        **payload,
        "goal": "malformed authority",
        "allowed_tools": ["download_and_index"],
        "initial_tools": ["download_and_index"],
        "allowed_mcp_servers": ["writer"],
        "allow_external_write": True,
        "requires_plan": False,
        "risk_level": "high",
    }).to_dict()

    assert migrated["schema_version"] == TASK_CONTRACT_SCHEMA
    assert migrated["source_contract_valid"] is False
    assert "allowed_tools" not in migrated
    assert "initial_tools" not in migrated
    assert "allowed_mcp_servers" not in migrated
    assert "allow_external_write" not in migrated
    assert "requires_plan" not in migrated
    assert "risk_level" not in migrated


@pytest.mark.parametrize(
    ("schema_version", "version"),
    [
        ("scansci.task-contract.v1", 1),
        ("scansci.task-contract.v1", "1"),
        ("scansci.task-contract.v2", 2),
        ("scansci.task-contract.v2", "2"),
    ],
)
def test_matching_v1_v2_schema_and_integer_or_string_version_preserve_lease(
    schema_version: str,
    version: int | str,
) -> None:
    migrated = TaskContract.from_payload({
        "schema_version": schema_version,
        "version": version,
        "allowed_tools": ["inspect_workspace"],
    }).to_dict()

    assert migrated["allowed_tools"] == ["inspect_workspace"]
    assert migrated["source_contract_valid"] is True


@pytest.mark.parametrize("version", [1, "1", 2, "2"])
def test_integer_only_legacy_versions_remain_readable(version: int | str) -> None:
    migrated = TaskContract.from_payload({
        "version": version,
        "allowed_tools": ["inspect_workspace"],
    }).to_dict()

    assert migrated["allowed_tools"] == ["inspect_workspace"]
    assert migrated["source_contract_valid"] is True


def test_tool_authorization_denies_missing_empty_spoofed_and_over_budget_calls() -> None:
    descriptor = {"id": "inspect_workspace", "risk_level": "read_only", "idempotent": True}
    base = {
        "request_id": "request-current",
        "active_request_id": "request-current",
        "descriptor": descriptor,
        "call_count": 0,
    }

    with pytest.raises(ToolAuthorizationError, match="missing") as missing:
        authorize_tool_call(tool_name="inspect_workspace", contract={}, **base)
    assert missing.value.code == "missing_lease"
    with pytest.raises(ToolAuthorizationError, match="empty") as empty:
        authorize_tool_call(tool_name="inspect_workspace", contract={"allowed_tools": []}, **base)
    assert empty.value.code == "empty_lease"
    with pytest.raises(ToolAuthorizationError, match="another request") as stale:
        authorize_tool_call(
            tool_name="inspect_workspace",
            contract={"allowed_tools": ["inspect_workspace"], "max_tool_budget": 2},
            **{**base, "request_id": "request-stale"},
        )
    assert stale.value.code == "request_mismatch"
    with pytest.raises(ToolAuthorizationError, match="budget") as exhausted:
        authorize_tool_call(
            tool_name="inspect_workspace",
            contract={
                "allowed_tools": ["inspect_workspace"],
                "risk_level": "read_only",
                "max_tool_budget": 1,
            },
            **{**base, "call_count": 1},
        )
    assert exhausted.value.code == "budget_exhausted"


def test_legacy_scientific_tool_contracts_remain_authorizable_for_migration() -> None:
    decision = authorize_tool_call(
        tool_name="list_scientific_agents",
        contract={
            "allowed_tools": ["list_scientific_agents"],
            "risk_level": "read_only",
            "max_tool_budget": 2,
        },
        descriptor={
            "id": "list_scientific_agents",
            "status": "legacy",
            "risk_level": "read_only",
            "idempotent": True,
        },
        request_id="legacy-request",
        active_request_id="legacy-request",
        call_count=0,
    )
    assert decision.allowed is True
    assert decision.approval_required is False


def test_approval_token_is_created_only_for_explicit_request_scoped_approve() -> None:
    assert approval_token_from_response("request-a", {}) is None
    assert approval_token_from_response("request-a", {"decision": "approved"}) is None
    assert approval_token_from_response("request-a", {"decision": "continue"}) is None

    token = approval_token_from_response("request-a", {"decision": "approve"})

    assert token is not None
    assert token.request_id == "request-a"
    with pytest.raises(ToolAuthorizationError, match="approved plan") as unapproved:
        authorize_tool_call(
            tool_name="download_and_index",
            contract={
                "allowed_tools": ["download_and_index"],
                "risk_level": "reversible",
                "requires_plan": True,
                "max_tool_budget": 2,
            },
            descriptor={"id": "download_and_index", "risk_level": "reversible", "idempotent": False},
            request_id="request-b",
            active_request_id="request-b",
            approval_token=token,
            call_count=0,
        )
    assert unapproved.value.code == "approval_required"

    decision = authorize_tool_call(
        tool_name="download_and_index",
        contract={
            "allowed_tools": ["download_and_index"],
            "risk_level": "reversible",
            "requires_plan": True,
            "max_tool_budget": 2,
        },
        descriptor={"id": "download_and_index", "risk_level": "reversible", "idempotent": False},
        request_id="request-a",
        active_request_id="request-a",
        approval_token=token,
        call_count=0,
    )
    assert decision.allowed is True
    assert decision.approval_required is True
