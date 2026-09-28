"""Host-owned autonomy contracts for ScanSci agent turns.

The language model may propose a next action, but it must not decide its own
permission envelope.  This module compiles a small, deterministic contract
from the product mode and the user's explicit request.  The contract is safe
to persist with a research run and is also understood by the Pi sidecar.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable, Sequence
from uuid import uuid4

from .task_contract import TASK_CONTRACT_SCHEMA, TASK_CONTRACT_VERSION


_READ_ONLY_TOOLS = {
    "inspect_workspace",
    "inspect_available_tools",
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
    "agent_reach",
    "browser_access",
    "search_journal",
    "audit_references",
    "build_presentation_outline",
    "list_subagents",
    "collect_subagents",
    "self_assess",
}
_REVERSIBLE_LOCAL_TOOLS = {
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
_MODE_TOOLS = {
    "workspace-status": {"inspect_workspace"},
    "zotero-status": {"zotero_status"},
    "zotero-search": {"zotero_search"},
    "task-documents": {
        "read_task_documents",
        "summarize_documents",
        "check_task_completion",
        "self_assess",
    },
    # Explicit web access also covers scholarly discovery.  A user should not
    # lose ``discover_papers`` merely because the web toggle is ON instead of
    # AUTO; the host still keeps this lease read-only.
    "web": {"search_web", "agent_reach", "browser_access", "discover_papers", "verify_doi", "self_assess"},
    "web-auto": {"search_web", "agent_reach", "browser_access", "discover_papers", "verify_doi", "self_assess"},
    "knowledge": {
        "inspect_workspace",
        "inspect_available_tools",
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
        "subagent",
        "list_subagents",
        "collect_subagents",
        "cancel_subagents",
        "self_assess",
    },
    "research": {
        "inspect_workspace",
        "inspect_available_tools",
        "search_web",
        "agent_reach",
        "browser_access",
        "discover_papers",
        "download_and_index",
        "summarize_documents",
        "check_task_completion",
        "verify_doi",
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
        "subagent",
        "list_subagents",
        "collect_subagents",
        "cancel_subagents",
        "self_assess",
    },
    "verified-answer": {"build_verified_answer"},
    "image": {"generate_image", "self_assess"},
    "slides": {
        "inspect_workspace",
        "build_presentation_outline",
        "create_document",
        "create_pdf",
        "create_spreadsheet",
        "create_presentation",
        "compile_latex",
        "edit_section",
        "edit_slide",
        "self_assess",
    },
}
_MODE_INITIAL_TOOLS = {
    "workspace-status": {"inspect_workspace"},
    "zotero-status": {"zotero_status"},
    "zotero-search": {"zotero_search"},
    "task-documents": {"read_task_documents"},
    "web": {"search_web", "agent_reach", "browser_access", "discover_papers"},
    # AUTO is a cognition hint, not a reason to pay the schema cost for every
    # public reader. Pi starts with search_tools and activates a reader only
    # when the current question actually depends on public information.
    "web-auto": set(),
    "knowledge": {"inspect_available_tools"},
    "research": {"discover_papers"},
    "verified-answer": {"build_verified_answer"},
    "image": {"generate_image"},
    "slides": {"build_presentation_outline"},
}
_WORKFLOW_REVERSIBLE = {
    "paper_download",
    "paper_download_batch",
    "paper_search_download",
    "ppt_project",
    "pdf_to_ppt",
}
_MODE_TOOLS["benchmark"] = set(_READ_ONLY_TOOLS) | set(_REVERSIBLE_LOCAL_TOOLS)
_HIGH_RISK_PATTERN = re.compile(
    r"(?:"
    r"\b(?:delete|erase|remove|wipe|overwrite|replace)\b.{0,30}\b(?:file|folder|database|library|account)\b"
    r"|\b(?:send|email|publish|post|upload|share)\b.{0,40}\b(?:public|externally|online|to\s+\w+)"
    r"|\b(?:purchase|pay|subscribe|charge)\b"
    r"|删除.{0,20}(?:文件|文件夹|数据库|知识库|账户)"
    r"|覆盖.{0,20}(?:原文件|现有文件|数据库|知识库)"
    r"|(?:发送|发邮件|发布|公开|上传|分享).{0,30}(?:给|到|至|网络|网站|平台|公众)"
    r"|(?:购买|付款|付费|订阅|扣费)"
    r"|(?:修改|重置).{0,20}(?:权限|密码|密钥|凭据|安全设置)"
    r")",
    re.IGNORECASE,
)
_BULK_PATTERN = re.compile(
    r"(?:\b(?:[2-9]\d|1\d{2,})\s*(?:papers?|files?|documents?|items?)\b"
    r"|(?:[2-9]\d|1\d{2,})\s*(?:篇|个|份)(?:文献|论文|文件|条目))",
    re.IGNORECASE,
)
_GREETING_PATTERN = re.compile(
    r"^\s*(?:你?好|您好|哈喽|嗨|早上好|上午好|下午好|晚上好|"
    r"谢谢|感谢|好的|好[的呀啊]?|知道了|明白了|"
    r"hello|hi|hey|thanks?|thank\s+you|ok(?:ay)?|got\s+it|good\s+(?:morning|afternoon|evening))"
    r"(?:[!！。,.，\s]*(?:呀|啊|哦|么|吗|啦)?)?\s*$",
    re.IGNORECASE,
)
_HIGH_COGNITIVE_PATTERN = re.compile(
    r"(?:综述|系统评价|元分析|研究设计|方法比较|证据综合|跨文献|批量|工作流|完整索引|"
    r"literature\s+review|systematic\s+review|meta[- ]analysis|research\s+design|"
    r"synthesi[sz]e|cross[- ]paper|batch|workflow)",
    re.IGNORECASE,
)
_MEDIUM_COGNITIVE_PATTERN = re.compile(
    r"(?:解释|分析|比较|对比|总结|归纳|评估|论证|改写|润色|翻译|写作|"
    r"explain|analy[sz]e|compare|summari[sz]e|evaluate|rewrite|polish|translate|draft)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskProfile:
    """Deterministic host classification for one user turn.

    The profile is deliberately model-independent.  It tells the transport
    whether this is direct conversation, a bounded tool turn, a resumable
    workflow, or an approval-gated action before any model is called.
    """

    route: str
    capability_profile: str
    cognitive_complexity: str
    execution_complexity: str
    evidence_policy: str
    risk_level: str
    confidence: float
    requires_tools: bool
    requires_plan: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


@dataclass(frozen=True)
class TaskContract:
    """Serializable host policy for one task or conversational action."""

    contract_id: str
    version: int
    schema_version: str
    goal: str
    workflow_type: str
    task_mode: str
    autonomy: str
    risk_level: str
    confidence: float
    requires_plan: bool
    allowed_tools: tuple[str, ...]
    initial_tools: tuple[str, ...]
    required_tool_groups: tuple[tuple[str, ...], ...]
    success_criteria: tuple[str, ...]
    initial_tool_budget: int
    max_tool_budget: int
    recovery_budget: int
    model_token_budget: int
    max_model_token_budget: int
    allow_external_write: bool
    task_profile: TaskProfile
    unavailable_tools: tuple[str, ...] = ()
    allowed_mcp_servers: tuple[str, ...] = ()
    capability_lease: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        payload["initial_tools"] = list(self.initial_tools)
        payload["required_tool_groups"] = [list(group) for group in self.required_tool_groups]
        payload["success_criteria"] = list(self.success_criteria)
        payload["task_profile"] = self.task_profile.to_dict()
        payload["capability_profile"] = self.task_profile.capability_profile
        payload["unavailable_tools"] = list(self.unavailable_tools)
        payload["allowed_mcp_servers"] = list(self.allowed_mcp_servers)
        payload["capability_lease"] = dict(self.capability_lease or {})
        return payload


def _mode_parts(task_mode: str) -> set[str]:
    return {part for part in str(task_mode or "general").strip().lower().split("+") if part}


def _budgets(parts: set[str], *, risk_level: str) -> tuple[int, int, int]:
    if parts & {"research", "slides"}:
        initial, maximum = 6, 12
    elif parts & {"knowledge", "task-documents"}:
        initial, maximum = 5, 10
    elif parts & {"web", "web-auto"}:
        initial, maximum = 4, 8
    else:
        initial, maximum = 3, 6
    if risk_level == "high":
        maximum = min(maximum, initial + 2)
    return initial, maximum, 3 if maximum > initial else 2


def _model_token_budgets(parts: set[str]) -> tuple[int, int]:
    """Return a soft token lease and a generous runaway guard.

    Pi already compacts sessions against the active model context window.
    Counting every repeated prompt token against a small hard per-turn ceiling
    made healthy multi-step research fail even when the visible answer was
    tiny.  The first value may be extended automatically; the second only
    protects against a genuinely runaway run.
    """

    if parts & {"research", "slides"}:
        return 192_000, 768_000
    if parts & {"knowledge", "task-documents"}:
        return 128_000, 512_000
    if parts & {"web", "web-auto"}:
        return 96_000, 384_000
    return 48_000, 192_000


def _goal_text(user_text: str, workflow_type: str) -> str:
    normalized = re.sub(r"\s+", " ", str(user_text or "")).strip()
    return normalized[:1200] or f"Complete the {workflow_type or 'current'} ScanSci task"


def classify_task_profile(
    *,
    task_mode: str = "general",
    user_text: str = "",
    workflow_type: str = "",
    required_tool_groups: Sequence[Iterable[str]] | None = None,
) -> TaskProfile:
    """Classify the turn before a model sees it.

    Product mode and explicit user verbs are stronger signals than a model's
    self-reported intent.  This is intentionally conservative: uncertainty
    can reduce confidence, but can never expand the capability lease.
    """

    normalized_mode = str(task_mode or "general").strip().lower() or "general"
    normalized_workflow = str(workflow_type or "").strip().lower()
    normalized_text = re.sub(r"\s+", " ", str(user_text or "")).strip()
    parts = _mode_parts(normalized_mode)
    required = [
        {str(name) for name in group if str(name)}
        for group in list(required_tool_groups or [])
        if group
    ]

    social_turn = bool(_GREETING_PATTERN.fullmatch(normalized_text))
    high_risk = bool(_HIGH_RISK_PATTERN.search(normalized_text))
    reversible = bool(
        parts & {"research", "slides", "image"}
        or normalized_workflow in _WORKFLOW_REVERSIBLE
    )
    if social_turn and not required:
        risk_level = "none"
        reversible = False
    elif high_risk:
        risk_level = "high"
    elif reversible:
        risk_level = "reversible"
    elif required or parts & {
        "knowledge",
        "task-documents",
        "web",
        "workspace-status",
        "zotero-status",
        "zotero-search",
        "verified-answer",
        "benchmark",
    }:
        risk_level = "read_only"
    else:
        # A plain direct_chat turn stays on the Host fast lane.  Capability-
        # bearing profiles still receive the read-only baseline so Pi can
        # discover an allowed capability even when no single tool is mandatory.
        risk_level = "read_only"

    bulk = bool(_BULK_PATTERN.search(normalized_text))
    durable_workflow = not social_turn and bool(
        normalized_workflow and normalized_workflow != "ask"
        or parts & {"research", "slides"}
        or len(required) > 1
        or bulk
    )
    requires_tools = not social_turn and bool(
        required
        or parts & {
            "knowledge",
            "task-documents",
            "web",
            "workspace-status",
            "zotero-status",
            "zotero-search",
            "verified-answer",
            "research",
            "slides",
            "benchmark",
        }
    )
    if durable_workflow:
        execution_complexity = "workflow"
    elif requires_tools:
        execution_complexity = "tool"
    else:
        execution_complexity = "none"

    if social_turn:
        evidence_policy = "off"
    elif parts & {"knowledge", "research", "verified-answer", "benchmark"}:
        evidence_policy = "strict"
    elif parts & {
        "workspace-status",
        "task-documents",
        "zotero-search",
        "web",
        "web-auto",
        "slides",
    }:
        evidence_policy = "assist"
    else:
        evidence_policy = "off"

    if (
        durable_workflow
        or len(normalized_text) > 800
        or _HIGH_COGNITIVE_PATTERN.search(normalized_text)
    ):
        cognitive_complexity = "high"
    elif (
        requires_tools
        or len(normalized_text) > 180
        or _MEDIUM_COGNITIVE_PATTERN.search(normalized_text)
    ):
        cognitive_complexity = "medium"
    else:
        cognitive_complexity = "low"

    requires_plan = high_risk or bulk
    if high_risk or requires_plan:
        route = "approval_gate"
    elif execution_complexity == "workflow":
        route = "resumable_workflow"
    elif execution_complexity == "tool":
        route = "tool_agent"
    else:
        route = "direct_chat"

    capability_profile = {
        "direct_chat": "minimal",
        "tool_agent": "focused",
        "resumable_workflow": "workflow",
        "approval_gate": "approval",
    }[route]

    reasons: list[str] = []
    if social_turn:
        reasons.append("greeting")
    if required:
        reasons.append("required_tool_contract")
    if parts - {"general", "web-auto"}:
        reasons.append("explicit_product_mode")
    if durable_workflow:
        reasons.append("durable_workflow")
    if bulk:
        reasons.append("large_batch")
    if high_risk:
        reasons.append("high_risk_action")
    if not reasons:
        reasons.append("ordinary_conversation")

    confidence = 1.0
    if normalized_mode == "general" and not normalized_text:
        confidence = 0.6
    elif normalized_mode == "general" and execution_complexity == "none":
        confidence = 0.95
    elif normalized_mode == "web-auto" and not required:
        confidence = 0.9
    return TaskProfile(
        route=route,
        capability_profile=capability_profile,
        cognitive_complexity=cognitive_complexity,
        execution_complexity=execution_complexity,
        evidence_policy=evidence_policy,
        risk_level=risk_level,
        confidence=confidence,
        requires_tools=requires_tools,
        requires_plan=requires_plan,
        reasons=tuple(reasons),
    )


def compile_task_contract(
    *,
    task_mode: str = "general",
    user_text: str = "",
    workflow_type: str = "",
    required_tool_groups: Sequence[Iterable[str]] | None = None,
    available_tool_ids: Iterable[object] | None = None,
    allowed_mcp_servers: Iterable[object] = (),
    capability_lease: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a conservative capability lease without asking the model.

    Ambiguous turns receive only read capability.  Reversible writes are
    granted only when the selected product mode or workflow already implies
    them.  External/destructive actions require an approved plan.
    """

    normalized_mode = str(task_mode or "general").strip().lower() or "general"
    normalized_workflow = str(workflow_type or "").strip().lower()
    parts = _mode_parts(normalized_mode)
    task_profile = classify_task_profile(
        task_mode=normalized_mode,
        user_text=user_text,
        workflow_type=normalized_workflow,
        required_tool_groups=required_tool_groups,
    )
    social_turn = "greeting" in task_profile.reasons
    # Pi owns research strategy.  Every ready read-only builtin is inside the
    # hard envelope for a non-social turn; product modes only hint which tools
    # should be active first.  Reversible tools remain tied to an explicit
    # product workflow and are never granted by the broad read inventory.
    allowed: set[str] = set() if social_turn else set(_READ_ONLY_TOOLS)
    mode_authorized: set[str] = set()
    for part in parts:
        mode_authorized.update(_MODE_TOOLS.get(part, set()))
    high_risk = task_profile.risk_level == "high"
    reversible = task_profile.risk_level == "reversible"
    if high_risk:
        autonomy, risk_level = "approval_required", "high"
    elif reversible:
        autonomy, risk_level = "reversible", "reversible"
    elif task_profile.risk_level == "read_only":
        autonomy, risk_level = "read_only", "read_only"
    else:
        autonomy, risk_level = "direct", "none"

    if risk_level in {"none", "read_only"}:
        allowed.difference_update(_REVERSIBLE_LOCAL_TOOLS)
    elif risk_level in {"reversible", "high"}:
        allowed.update(
            tool
            for tool in _REVERSIBLE_LOCAL_TOOLS
            if tool in mode_authorized
        )

    unavailable_tools: tuple[str, ...] = ()
    if available_tool_ids is not None:
        available = {str(item).strip() for item in available_tool_ids if str(item).strip()}
        unavailable_tools = tuple(sorted(allowed - available))
        allowed.intersection_update(available)

    required = tuple(
        tuple(sorted({str(name) for name in group if str(name)}))
        for group in list(required_tool_groups or [])
        if group
    )
    initial: set[str] = set()
    for part in parts:
        initial.update(_MODE_INITIAL_TOOLS.get(part, set()))
    for group in required:
        initial.update(group)
    initial.intersection_update(allowed)
    success = ["Return a direct answer to the user's final request"]
    if required:
        success.append("Complete every required tool group before claiming success")
    if reversible:
        success.append("Verify persisted files or indexed records after local writes")
    if parts & {"knowledge", "research", "verified-answer"}:
        success.append("Distinguish metadata, discovery snippets, and verified full text")
    if unavailable_tools:
        success.append("Report any capability unavailable in this workspace instead of claiming it was used")

    initial_budget, max_budget, recovery_budget = _budgets(parts, risk_level=risk_level)
    model_token_budget, max_model_token_budget = _model_token_budgets(parts)
    requires_plan = task_profile.requires_plan
    return TaskContract(
        contract_id=f"contract-{uuid4().hex}",
        version=TASK_CONTRACT_VERSION,
        schema_version=TASK_CONTRACT_SCHEMA,
        goal=_goal_text(user_text, normalized_workflow),
        workflow_type=normalized_workflow,
        task_mode=normalized_mode,
        autonomy=autonomy,
        risk_level=risk_level,
        confidence=task_profile.confidence,
        requires_plan=requires_plan,
        allowed_tools=tuple(sorted(allowed)),
        initial_tools=tuple(sorted(initial)),
        required_tool_groups=required,
        success_criteria=tuple(success),
        initial_tool_budget=initial_budget,
        max_tool_budget=max_budget,
        recovery_budget=recovery_budget,
        model_token_budget=model_token_budget,
        max_model_token_budget=max_model_token_budget,
        allow_external_write=high_risk,
        task_profile=task_profile,
        unavailable_tools=unavailable_tools,
        allowed_mcp_servers=tuple(
            dict.fromkeys(str(item).strip().removeprefix("mcp:") for item in allowed_mcp_servers if str(item).strip())
        ),
        capability_lease=dict(capability_lease or {}),
    ).to_dict()


__all__ = [
    "TaskContract",
    "TaskProfile",
    "classify_task_profile",
    "compile_task_contract",
]
