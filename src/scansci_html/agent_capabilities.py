"""Canonical capability and research-resource protocol for ScanSci agents.

The desktop host owns this catalogue.  Models, MCP servers and child agents
receive a projection of it, but none of them can use the projection to expand
their own authority.  Keeping the descriptors JSON-shaped makes the protocol
safe to pass across the Python host, Pi sidecar and durable run records.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote, urlsplit

from .external_capabilities import provider_manifest_for_record

CAPABILITY_SCHEMA_VERSION = "scansci.capability.v1"
CAPABILITY_LEASE_SCHEMA_VERSION = "scansci.capability-lease.v1"
RESOURCE_URI_SCHEME = "scansci"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """A host-owned, serialisable description of one callable capability."""

    id: str
    kind: str
    label: str
    description: str
    source: str = "scansci"
    version: str = CAPABILITY_SCHEMA_VERSION
    status: str = "ready"
    risk_level: str = "read_only"
    requires_approval: bool = False
    subagent_allowed: bool = True
    evidence_policy: str = "assist"
    output_kinds: tuple[str, ...] = ()
    timeout_seconds: int = 120
    idempotent: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["output_kinds"] = list(self.output_kinds)
        return payload


_BUILTIN_CAPABILITIES: tuple[CapabilityDescriptor, ...] = (
    CapabilityDescriptor(
        "inspect_workspace", "tool", "工作区概览", "读取当前工作区、知识库和任务状态。",
        evidence_policy="off", output_kinds=("workspace_snapshot",),
    ),
    CapabilityDescriptor(
        "inspect_available_tools", "tool", "能力目录", "返回当前可用能力、状态、风险和资源协议。",
        evidence_policy="off", output_kinds=("capability_catalog",),
    ),
    CapabilityDescriptor(
        "discover_papers", "tool", "多源论文发现", "从学术数据源发现候选论文；结果必须标记为发现线索。",
        output_kinds=("paper_candidates",), timeout_seconds=180,
    ),
    CapabilityDescriptor(
        "verify_doi", "tool", "DOI 核验", "核对 DOI 与书目信息。",
        output_kinds=("paper_metadata",),
    ),
    CapabilityDescriptor(
        "search_local_evidence", "tool", "本地证据检索", "从当前证据库检索命中句、前后句和可定位的原文锚点。",
        evidence_policy="required", output_kinds=("evidence_findings",),
    ),
    CapabilityDescriptor(
        "build_verified_answer", "tool", "证据回答", "生成带已核验引文的科研回答。",
        evidence_policy="required", output_kinds=("verified_answer", "evidence_links"),
        timeout_seconds=300,
    ),
    CapabilityDescriptor(
        "download_and_index", "tool", "下载并建立索引", "在工作区内合法获取并索引文献。",
        risk_level="reversible", evidence_policy="required", output_kinds=("paper", "index"),
        timeout_seconds=900, idempotent=False,
    ),
    CapabilityDescriptor(
        "summarize_documents", "tool", "全文分析", "提取研究问题、方法、发现、局限和证据锚点。",
        evidence_policy="required", output_kinds=("document_map", "evidence_findings"),
        timeout_seconds=300,
    ),
    CapabilityDescriptor(
        "check_task_completion", "tool", "任务完成核验", "检查持久化阶段、产物、文献和索引是否满足任务要求。",
        evidence_policy="required", output_kinds=("completion_check",),
    ),
    CapabilityDescriptor(
        "create_document", "artifact", "文档产物", "创建可编辑文档。",
        risk_level="reversible", evidence_policy="required", output_kinds=("artifact",),
        timeout_seconds=300, idempotent=False,
    ),
    CapabilityDescriptor(
        "create_presentation", "artifact", "演示文稿产物", "创建可编辑科研演示文稿。",
        risk_level="reversible", evidence_policy="required", output_kinds=("artifact",),
        timeout_seconds=300, idempotent=False,
    ),
    CapabilityDescriptor(
        "generate_image", "artifact", "图像生成", "使用已配置的图像模型生成并保存图片。",
        risk_level="reversible", evidence_policy="assist", output_kinds=("image", "artifact"),
        timeout_seconds=300, idempotent=False, subagent_allowed=False,
    ),
    CapabilityDescriptor(
        "search_web", "tool", "公开网络检索", "查询公开网页；结果需要区分线索与已验证证据。",
        output_kinds=("web_candidates",), timeout_seconds=180,
    ),
    CapabilityDescriptor(
        "agent_reach", "tool", "Agent Reach Internet Router", "Read or search public internet channels through ScanSci-owned zero-install backends.",
        source="Panniantong/Agent-Reach / MIT / ScanSci adaptation",
        output_kinds=("web_candidates", "page_content", "channel_status"), timeout_seconds=180,
    ),
    CapabilityDescriptor(
        "browser_access", "tool", "Rendered Browser Access", "Read a public page through the bundled web-access CDP bridge when static readers cannot provide sufficient content.",
        source="eze-is/web-access / MIT / ScanSci read-only bridge",
        output_kinds=("page_content", "browser_status"), timeout_seconds=180,
    ),
    CapabilityDescriptor(
        "delegate_scientific_agents", "control", "Delegate scientific agents",
        "Atomically reserve and start bounded, read-only scientific child runs owned by the active durable parent.",
        status="legacy", risk_level="reversible", subagent_allowed=False, evidence_policy="off",
        output_kinds=("scientific_children",), idempotent=False,
    ),
    CapabilityDescriptor(
        "list_scientific_agents", "control", "List scientific agents",
        "List durable scientific child runs owned by the active durable parent.",
        status="legacy", subagent_allowed=False, evidence_policy="off", output_kinds=("scientific_children",),
    ),
    CapabilityDescriptor(
        "collect_scientific_agents", "control", "Collect scientific agents",
        "Validate and collect partial structured handoffs from owned scientific child runs.",
        status="legacy", subagent_allowed=False, evidence_policy="off", output_kinds=("scientific_handoffs",),
    ),
    CapabilityDescriptor(
        "cancel_scientific_agents", "control", "Cancel scientific agents",
        "Cancel only scientific child runs owned by the active durable parent.",
        status="legacy", risk_level="reversible", subagent_allowed=False, evidence_policy="off",
        output_kinds=("scientific_children",), idempotent=False,
    ),
    # Generic Pi-native child-session controls. Legacy scientific ids above
    # remain readable for durable-run migration, but are not in the default
    # model contract.
    CapabilityDescriptor(
        "subagent", "control", "Pi child session",
        "Start bounded native Pi child sessions with a task-defined prompt and a host-owned read-only tool subset.",
        risk_level="reversible", subagent_allowed=False, evidence_policy="off",
        output_kinds=("subagent_children",), idempotent=False,
    ),
    CapabilityDescriptor(
        "list_subagents", "control", "List Pi child sessions",
        "List native Pi child sessions owned by the current parent session.",
        subagent_allowed=False, evidence_policy="off", output_kinds=("subagent_children",),
    ),
    CapabilityDescriptor(
        "collect_subagents", "control", "Collect Pi child sessions",
        "Collect bounded handoffs from native Pi child sessions, optionally waiting for completion.",
        subagent_allowed=False, evidence_policy="off", output_kinds=("subagent_handoffs",),
    ),
    CapabilityDescriptor(
        "cancel_subagents", "control", "Cancel Pi child sessions",
        "Cancel native Pi child sessions owned by the current parent session.",
        risk_level="reversible", subagent_allowed=False, evidence_policy="off",
        output_kinds=("subagent_children",), idempotent=False,
    ),
    CapabilityDescriptor(
        "self_assess", "control", "执行自评", "基于当前轮次的工具记录检查缺口和下一步。",
        evidence_policy="off", output_kinds=("self_assessment",),
    ),
)

# ``agent_contract`` and the Pi sidecar both refer to these stable ids.  The
# catalogue is deliberately the only place that says whether an id is
# installed and ready for a particular workspace.  A descriptor is supplied
# for every bridge tool, including less common integrations, so a capability
# lease never has to fall back to an undocumented implicit allow-list.
_CANONICAL_TOOL_IDS: tuple[str, ...] = (
    "inspect_workspace",
    "inspect_available_tools",
    "read_task_documents",
    "download_and_index",
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
    "agent_reach",
    "browser_access",
    "search_journal",
    "audit_references",
    "build_presentation_outline",
    "create_document",
    "create_pdf",
    "create_spreadsheet",
    "create_presentation",
    "generate_image",
    "compile_latex",
    "edit_section",
    "edit_slide",
    "subagent",
    "list_subagents",
    "collect_subagents",
    "cancel_subagents",
    "self_assess",
)
_REVERSIBLE_TOOL_IDS = {
    "download_and_index",
    "create_document",
    "create_pdf",
    "create_spreadsheet",
    "create_presentation",
    "generate_image",
    "compile_latex",
    "edit_section",
    "edit_slide",
    "subagent",
    "cancel_subagents",
}
_SUBAGENT_DENIED_TOOL_IDS = {
    "create_document",
    "create_pdf",
    "create_spreadsheet",
    "create_presentation",
    "generate_image",
    "compile_latex",
    "edit_section",
    "edit_slide",
    "subagent",
    "list_subagents",
    "collect_subagents",
    "cancel_subagents",
}
_LEGACY_TOOL_IDS = {
    "delegate_scientific_agents",
    "list_scientific_agents",
    "collect_scientific_agents",
    "cancel_scientific_agents",
}
_PLUGIN_TOOL_IDS = {
    "zotero": {
        "zotero_search",
        "zotero_fulltext",
        "zotero_attachment",
        "zotero_export_bibtex",
        "zotero_citations",
    },
    "documents": {"create_document"},
    "pdf": {"create_pdf"},
    "spreadsheets": {"create_spreadsheet"},
    "presentations": {"create_presentation"},
    "latex": {"compile_latex"},
}


def _default_tool_descriptor(capability_id: str) -> CapabilityDescriptor:
    """Describe a bridge tool that does not need bespoke UI copy yet."""

    reversible = capability_id in _REVERSIBLE_TOOL_IDS
    return CapabilityDescriptor(
        id=capability_id,
        kind="tool",
        label=capability_id.replace("_", " "),
        description=f"ScanSci bridge capability: {capability_id}.",
        risk_level="reversible" if reversible else "read_only",
        subagent_allowed=capability_id not in _SUBAGENT_DENIED_TOOL_IDS,
        evidence_policy="required" if capability_id in {"build_verified_answer", "search_local_evidence"} else "assist",
        output_kinds=("tool_result",),
        idempotent=not reversible,
    )


def make_resource_uri(kind: str, *segments: object) -> str:
    """Build a stable opaque URI without leaking filesystem paths or secrets."""

    normalized_kind = str(kind or "resource").strip().lower().replace("_", "-") or "resource"
    normalized = [quote(str(segment or "").strip(), safe="._-:") for segment in segments if str(segment or "").strip()]
    return f"{RESOURCE_URI_SCHEME}://{normalized_kind}" + (f"/{'/'.join(normalized)}" if normalized else "")


def parse_resource_uri(value: object) -> dict[str, Any] | None:
    """Parse only ScanSci-owned opaque URIs; unrelated URIs are rejected."""

    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme != RESOURCE_URI_SCHEME or not parsed.netloc:
        return None
    return {
        "uri": str(value),
        "kind": parsed.netloc,
        "segments": [unquote(part) for part in parsed.path.split("/") if part],
    }


def paper_uri(*, doi: object = "", paper_id: object = "", arxiv_id: object = "") -> str:
    identifier = str(doi or paper_id or arxiv_id or "unknown").strip()
    return make_resource_uri("paper", identifier)


def evidence_uri(*, doc_id: object, evidence_id: object = "", anchor: object = "") -> str:
    return make_resource_uri("evidence", doc_id, evidence_id or anchor)


def artifact_uri(*, run_id: object, artifact_id: object) -> str:
    return make_resource_uri("artifact", run_id, artifact_id)


def run_uri(run_id: object) -> str:
    return make_resource_uri("run", run_id)


def builtin_capability_catalog() -> list[dict[str, Any]]:
    descriptors = [item.to_dict() for item in _BUILTIN_CAPABILITIES]
    declared = {str(item["id"]) for item in descriptors}
    descriptors.extend(
        _default_tool_descriptor(capability_id).to_dict()
        for capability_id in _CANONICAL_TOOL_IDS
        if capability_id not in declared
    )
    return descriptors


def builtin_capability_descriptor(capability_id: object) -> dict[str, Any] | None:
    """Return one host-owned bridge descriptor without inventing metadata."""

    normalized = str(capability_id or "").strip()
    if not normalized:
        return None
    return next(
        (dict(item) for item in builtin_capability_catalog() if str(item.get("id", "")) == normalized),
        None,
    )


def capability_catalog(
    *,
    workspace: str | Path,
    evidence_db: str | Path,
    mcp_servers: Iterable[dict[str, Any]] = (),
    plugins: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Return the host-visible catalogue used by UI, Pi and child agents."""

    descriptors = builtin_capability_catalog()
    plugin_records = {str(item.get("id", "")): dict(item) for item in plugins if isinstance(item, dict)}
    for plugin_id, tool_ids in _PLUGIN_TOOL_IDS.items():
        plugin = plugin_records.get(plugin_id)
        if plugin and plugin.get("enabled") and not plugin.get("uninstalled"):
            continue
        for descriptor in descriptors:
            if str(descriptor.get("id", "")) in tool_ids:
                descriptor["status"] = "disabled"
    for raw in mcp_servers:
        if not isinstance(raw, dict):
            continue
        server_id = str(raw.get("id") or raw.get("name") or "mcp").strip()
        if not server_id:
            continue
        deferred = bool(raw.get("deferred", False))
        enabled = bool(raw.get("enabled", False)) and not bool(raw.get("uninstalled", False))
        literature = str(raw.get("provider_kind") or raw.get("connector_kind") or "").strip().lower() == "literature"
        reversible_authorized = bool(raw.get("allow_reversible", False)) or bool(raw.get("allow_write", False))
        manifest = provider_manifest_for_record(raw)
        descriptors.append(
            CapabilityDescriptor(
                id=f"mcp:{server_id}",
                kind="mcp_server",
                label=str(raw.get("name") or server_id),
                description=str(raw.get("description") or "External MCP capability."),
                source="mcp",
                status="ready" if enabled else "disabled",
                risk_level="reversible" if reversible_authorized else "read_only",
                requires_approval=bool(raw.get("allow_write", False)),
                subagent_allowed=not bool(raw.get("allow_write", False)),
                evidence_policy="assist",
                output_kinds=("mcp_result", "paper", "paper_metadata", "artifact") if literature else ("mcp_result",),
                timeout_seconds=120,
                idempotent=not reversible_authorized,
            ).to_dict()
        )
        descriptors[-1]["activation_mode"] = "deferred" if deferred else "direct"
        descriptors[-1]["transport"] = str(raw.get("transport") or "stdio")
        descriptors[-1]["provider_kind"] = str(raw.get("provider_kind") or raw.get("connector_kind") or "general")
        descriptors[-1]["semantic_capabilities"] = sorted(
            str(key) for key in dict(manifest.get("capability_aliases", {}) or {})
        )
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "workspace": str(Path(workspace).resolve()),
        "evidence_store_ready": Path(evidence_db).is_file(),
        "resource_uri_scheme": f"{RESOURCE_URI_SCHEME}://",
        "capabilities": descriptors,
    }


def allowed_capability_projection(
    catalog: dict[str, Any],
    allowed_ids: Iterable[object],
) -> list[dict[str, Any]]:
    """Project a catalogue through a task lease without inventing capability."""

    allowed = {str(value) for value in allowed_ids}
    return [
        dict(item)
        for item in list(catalog.get("capabilities", []) or [])
        if isinstance(item, dict) and str(item.get("id", "")) in allowed
    ]


def compile_capability_lease(
    catalog: dict[str, Any],
    requested_tool_ids: Iterable[object],
    *,
    requested_mcp_server_ids: Iterable[object] = (),
    subagent: bool = False,
) -> dict[str, Any]:
    """Compile an auditable authority projection from a host catalogue.

    Callers may request capabilities, but only ``ready`` descriptors may be
    leased.  The return value retains denied ids for diagnostics instead of
    silently reintroducing a second authority list in a caller.
    """

    descriptors = {
        str(item.get("id", "")): dict(item)
        for item in list(catalog.get("capabilities", []) or [])
        if isinstance(item, dict) and str(item.get("id", ""))
    }
    requested_tools = tuple(dict.fromkeys(str(value).strip() for value in requested_tool_ids if str(value).strip()))
    requested_mcp = tuple(
        dict.fromkeys(
            f"mcp:{str(value).strip().removeprefix('mcp:')}"
            for value in requested_mcp_server_ids
            if str(value).strip()
        )
    )

    def eligible(capability_id: str) -> bool:
        descriptor = descriptors.get(capability_id, {})
        status = descriptor.get("status")
        return bool(descriptor) and (status == "ready" or (
            status == "legacy" and capability_id in _LEGACY_TOOL_IDS
        )) and (
            not subagent or bool(descriptor.get("subagent_allowed", False))
        )

    allowed_tools = [capability_id for capability_id in requested_tools if eligible(capability_id)]
    allowed_mcp_ids = [capability_id for capability_id in requested_mcp if eligible(capability_id)]
    return {
        "schema_version": CAPABILITY_LEASE_SCHEMA_VERSION,
        "catalog_schema_version": str(catalog.get("schema_version", "")),
        "subagent": bool(subagent),
        "requested_tools": list(requested_tools),
        "allowed_tools": allowed_tools,
        "unavailable_tools": [capability_id for capability_id in requested_tools if capability_id not in allowed_tools],
        "requested_mcp_servers": [capability_id.removeprefix("mcp:") for capability_id in requested_mcp],
        "allowed_mcp_servers": [capability_id.removeprefix("mcp:") for capability_id in allowed_mcp_ids],
        "unavailable_mcp_servers": [
            capability_id.removeprefix("mcp:") for capability_id in requested_mcp if capability_id not in allowed_mcp_ids
        ],
    }


__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "CAPABILITY_LEASE_SCHEMA_VERSION",
    "CapabilityDescriptor",
    "RESOURCE_URI_SCHEME",
    "allowed_capability_projection",
    "artifact_uri",
    "builtin_capability_catalog",
    "builtin_capability_descriptor",
    "capability_catalog",
    "compile_capability_lease",
    "evidence_uri",
    "make_resource_uri",
    "paper_uri",
    "parse_resource_uri",
    "run_uri",
]
