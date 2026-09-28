from __future__ import annotations

import json
from pathlib import Path

from scansci_html.app_settings import load_settings, save_settings
from scansci_html.capability_ledger import CapabilityLedger
from scansci_html.external_capabilities import (
    host_policy_defaults,
    normalize_external_tool_result,
    provider_manifest_for_record,
    semantic_capability_for_tool,
    semantic_tool_aliases,
)
from scansci_html.research_agent import _required_pi_tool_groups


def test_literature_provider_has_stable_semantic_aliases_and_host_effects() -> None:
    record = {"id": "scansci-pdf", "connector_kind": "literature"}

    manifest = provider_manifest_for_record(record)
    defaults = host_policy_defaults(record)

    assert manifest["schema_version"] == "scansci.external-capability.v1"
    assert "scansci_pdf_download" in manifest["capability_aliases"]["download_papers"]
    assert defaults["tool_effects"]["scansci_pdf_download"] == "reversible"
    assert defaults["tool_effects"]["scansci_pdf_search"] == "read"

    aliases = semantic_tool_aliases([record])
    assert aliases["mcp__scansci_pdf__scansci_pdf_download"] == "download_papers"
    assert semantic_capability_for_tool(
        "mcp__scansci_pdf__call",
        aliases,
        {"tool": "scansci_pdf_download", "arguments": {"identifier": "10.1000/example"}},
    ) == "download_papers"


def test_external_result_preview_keeps_workspace_artifacts_only(tmp_path: Path) -> None:
    inside = tmp_path / "papers" / "paper.pdf"
    outside = tmp_path.parent / "outside.pdf"
    payload = {
        "details": {
            "ok": True,
            "files": [str(inside), str(outside)],
            "identifiers": ["10.1000/example"],
        }
    }

    preview = normalize_external_tool_result(payload, workspace=tmp_path)

    assert preview["ok"] is True
    assert preview["identifiers"] == ["10.1000/example"]
    assert preview["artifact_paths"] == [str(inside.resolve())]


def test_literature_mcp_settings_persist_manifest_and_reversible_permission(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    saved = save_settings(workspace, {
        "mcp_servers": [{
            "id": "papers",
            "name": "ScanSci PDF",
            "connector_kind": "literature",
            "command": "scansci-pdf",
            "args": "run",
            "allow_reversible": True,
        }],
    })
    loaded = load_settings(workspace)
    record = loaded["mcp_servers"][0]

    assert record["provider_kind"] == "literature"
    assert record["allow_reversible"] is True
    assert record["tool_effects"]["scansci_pdf_download"] == "reversible"
    assert "download_papers" in record["provider_manifest"]["capability_aliases"]
    assert json.loads((workspace.parent / ".scansci-notebook.json").read_text(encoding="utf-8"))["mcp_servers"][0]["allow_reversible"] is True
    assert saved["mcp_servers"][0]["tool_policies"]


def test_semantic_provider_tool_satisfies_download_ledger_group() -> None:
    ledger = CapabilityLedger.from_tool_calls(
        [{"download_and_index", "download_papers"}],
        [{
            "name": "mcp__scansci_pdf__scansci_pdf_download",
            "capability_id": "download_papers",
            "status": "completed",
        }],
    )

    assert ledger.ready
    assert ledger.outcomes["download_papers"]["status"] == "succeeded"


def test_external_download_satisfies_research_download_requirement_without_claiming_indexing() -> None:
    groups = _required_pi_tool_groups("research", "下载这篇论文")

    assert {"download_and_index", "download_papers"} in groups
