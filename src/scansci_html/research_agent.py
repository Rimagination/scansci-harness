"""Research workflow runtime for the ScanSci desktop application."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from html import escape as html_escape
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from .agent_context import build_agent_system_context, runtime_self_description
from .agent_skill_tools import ProgressiveSkillRuntime, SKILL_STATE_SCHEMA, SkillAccessError
from .agent_advisor import review_research_run
from .agent_capabilities import capability_catalog, compile_capability_lease, make_resource_uri, parse_resource_uri
from .agent_contract import compile_task_contract
from .agent_reasoning import evidence_budget_for_thinking, managed_glm_thinking_mode, normalize_thinking_level
from .academic_search import DEFAULT_PROVIDER_NAMES, FederatedAcademicSearch, build_academic_provider, search_academic_papers
from .academic_planning import plan_academic_search, review_academic_search_plan
from .app_settings import (
    get_provider_api_key,
    load_settings,
    managed_fallback_model_ids,
)
from .deep_research import (
    discovery_only_result,
    enrich_deep_research_result,
    plan_deep_research,
    run_discovery_loop,
)
from .deep_research_evidence import (
    build_task_fulltext_evidence,
    task_evidence_claim_ready,
    task_evidence_root,
)
from .deep_agent import ScanSciDeepAgent, build_deep_agent_model
from .evidence_store import ensure_library_overview, knowledge_base_snapshot
from .image_attachments import persist_image_attachments, pi_image_blocks, vision_image_blocks
from .audio_attachments import audio_attachment_asset, persist_audio_attachments
from .capability_ledger import CapabilityDeliveryError, CapabilityLedger
from .ingestion import ingest_sources, ingestion_context
from .library_manager import import_library_files, notebook_evidence_db
from .literature_review import _concise_review_title, retrieve_review_evidence, synthesize_literature_review
from .local_evidence_runtime import (
    BUILTIN_MODEL_MARKER,
    LocalEvidenceStack,
    build_local_evidence_stack,
    default_vector_cache_identity,
)
from .novelty_check import assess_novelty_evidence, plan_novelty_check
from .research_ideation import (
    assemble_research_idea_card,
    audit_candidate_coherence,
    audit_candidate_falsifiability,
    audit_candidate_implementability,
    diagnose_research_bottleneck,
    generate_research_candidate,
    plan_research_idea,
)
from .embeddings import HashingEmbeddingProvider, build_embedding_provider
from .rerankers import LexicalReranker, build_reranker
from .llm import (
    CascadingChatJsonClient,
    _parse_and_validate_structured_content,
    analyze_vision_images,
    # Retained as a compatibility seam for downstream harnesses.  Production
    # model-mediated workflow calls below use the Pi-backed adapter exclusively.
    build_chat_json_client,
    complete_chat_text,
    managed_gateway_session,
    stream_chat_text,
)
from .model_transport import select_api_surface
from .local_transformers_runtime import ensure_local_transformers_runtime
from .local_asr import transcribe_local_audio
from .local_model_market import QWEN3_ASR_LEGACY_MODEL_ID, installed_models
from .ollama_runtime import ollama_status
from .model_metadata import ModelRuntimeDescriptor, descriptor_from_model_record
from .pi_agent import PiAgentClient, PiAgentRunError, PiMultimodalUnavailable
from .run_manifest import RunManifest
from .qa.agent import answer_question
from .research_runs import ResearchRunStore, StageSpec, classify_error, redact_sensitive_text
from .research_subagents import (
    MAX_CONCURRENT_SCIENTIFIC_SUBAGENTS,
    delegation_prompt,
    public_role_catalog,
    select_scientific_roles,
    structured_output_schema,
    validate_scientific_resource_uri,
    validate_subagent_result,
)
from .vector_index import (
    DEFAULT_NEURAL_CACHE_BATCH_SIZE,
    load_embedding_cache_rows,
    prewarm_embedding_cache,
    vector_cache_status,
)
from .run_events import (
    CUSTOM,
    RUN_ERROR,
    RUN_FINISHED,
    RUN_STARTED,
    STEP_FINISHED,
    STEP_STARTED,
    TEXT_MESSAGE_CONTENT,
    TEXT_MESSAGE_END,
    TEXT_MESSAGE_START,
    event as run_event,
    new_message_id,
    new_run_id,
)
from .research_tools import (
    analyze_references,
    build_ppt_outline,
    create_ppt_project,
    download_paper,
    download_papers,
    _BatchCancelled,
    search_papers_for_download,
    search_journals,
    search_paper_atlas,
    verify_doi_metadata,
)
from .slide_studio import create_source_slide_deck, persist_slide_sources
from .task_routing import route_freeform_task
from .skill_runtime import resolve_skill_selection
from .text_tokenization import lexical_tokens
from .workspace import load_workspace_summary
from .vision_routing import ocr_image_blocks, select_text_route, select_vision_route
from .zotero_integration import zotero_status
from .zotero_scope import resolve_zotero_tag_scope, sync_zotero_document_tags


_GOOD_QUESTION_FIELDS = (
    "**暂定题目：**",
    "**核心研究问题：**",
    "**为什么值得做：**",
    "**它挑战了什么默认假设：**",
    "**竞争性解释：**",
    "**关键判别证据或实验：**",
    "**什么结果会推翻它：**",
    "**两周内可做的 pilot：**",
    "**所需数据与资源：**",
    "**最强评审质疑：**",
    "**下一步：**",
)

_NEURAL_LIBRARY_MIN_BYTES = 1_000_000
_SKILL_MENTION_PATTERN = re.compile(r"(?<![A-Za-z0-9._-])\$[A-Za-z0-9._-]+")


def _owned_scientific_evidence_uris(
    tool_name: str,
    result: object,
    evidence_dbs: list[Path],
) -> set[str]:
    """Derive evidence URIs only from host evidence rows, never result prose."""

    if not isinstance(result, dict) or tool_name not in {
        "search_local_evidence",
        "build_verified_answer",
    }:
        return set()
    candidates: list[dict[str, Any]] = []
    if tool_name == "search_local_evidence":
        candidates = [item for item in list(result.get("hits", []) or []) if isinstance(item, dict)]
    else:
        reader = dict(result.get("reader_answer", {}) or {})
        candidates = [item for item in list(reader.get("citations", []) or []) if isinstance(item, dict)]
    owned: set[str] = set()
    for candidate in candidates[:80]:
        evidence_id = str(candidate.get("evidence_id", "") or "").strip()
        doc_id = str(candidate.get("doc_id", "") or "").strip()
        try:
            library_index = int(candidate.get("library_index", 0) or 0)
        except (TypeError, ValueError):
            continue
        if (
            not evidence_id
            or not doc_id
            or len(evidence_id) > 300
            or len(doc_id) > 300
            or library_index < 0
            or library_index >= len(evidence_dbs)
        ):
            continue
        evidence_db = evidence_dbs[library_index]
        if not evidence_db.is_file():
            continue
        try:
            with sqlite3.connect(evidence_db) as connection:
                exists = connection.execute(
                    "select 1 from evidence_spans where evidence_id = ? and doc_id = ? limit 1",
                    (evidence_id, doc_id),
                ).fetchone()
        except sqlite3.Error:
            continue
        if exists:
            owned.add(make_resource_uri("evidence", doc_id, evidence_id))
    return owned


def _intent_classification_text(text: object) -> str:
    """Remove UI selectors that are instructions, never task intent."""

    without_library_labels = re.sub(r"@[^\s,，;；。]+", " ", str(text or ""))
    return _SKILL_MENTION_PATTERN.sub(" ", without_library_labels)

# A library chat is not always a question-answering task.  Counting and
# inventory requests need a complete, de-duplicated document query instead of
# a top-k evidence retrieval.  Keep the aliases conservative: they make a
# user's natural-language concept usable without quietly widening it to a
# different research topic.
_CATALOG_COUNT_INTENT_RE = re.compile(
    r"(?:多少|几|总共|一共|合计|数量|统计|count|how\s+many|total)"
    r".{0,18}(?:篇|条|份|个|文献|论文|资料|文章|papers?|articles?|documents?)"
    r"|(?:文献|论文|资料|文章|papers?|articles?|documents?).{0,18}"
    r"(?:多少|几|总共|一共|合计|数量|统计|count|how\s+many|total)",
    re.IGNORECASE,
)
_CATALOG_LIST_INTENT_RE = re.compile(
    r"(?:有哪些|哪些|列出|罗列|显示|找出|题录|目录|清单|列表|list|show|find)"
    r".{0,18}(?:文献|论文|资料|文章|papers?|articles?|documents?|records?)"
    r"|(?:文献|论文|资料|文章|papers?|articles?|documents?|records?).{0,18}"
    r"(?:有哪些|哪些|列出|罗列|显示|找出|题录|目录|清单|列表|list|show|find)"
    r"|(?:这个|当前|该|我的|我们(?:的)?)?(?:知识库|资料库|文献库).{0,12}"
    r"(?:有什么|有哪些|包含什么|收录什么)",
    re.IGNORECASE,
)
_CATALOG_TOPIC_PATTERNS = (
    re.compile(
        r"(?:和|与)\s*(?P<topic>[^，。！？?；;、]{1,40}?)\s*(?:有关|相关)(?:的)?"
        r"(?:文献|论文|资料|文章|篇|条|份)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:关于|有关)\s*(?P<topic>[^，。！？?；;、]{1,40}?)\s*(?:的)?"
        r"(?:文献|论文|资料|文章|篇|条|份)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<topic>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 ._/-]{0,38}?)"
        r"\s*(?:相关|有关)(?:的)?(?:文献|论文|资料|文章|篇|条|份)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<topic>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 ._/-]{0,38}?)"
        r"\s*(?:有|共|一共|总共)?\s*(?:多少|几)\s*(?:篇|条|份|个)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:how\s+many|count)\s+(?:[\w-]+\s+){0,5}?(?:about|on|related\s+to)\s+"
        r"(?P<topic>[A-Za-z0-9 ._/-]{2,48})",
        re.IGNORECASE,
    ),
)
_CATALOG_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "光伏": ("光伏", "photovoltaic", "photovoltaics", "solar photovoltaic", "solar pv"),
    "机器学习": ("机器学习", "machine learning"),
    "检索增强生成": ("检索增强生成", "retrieval-augmented generation", "rag"),
}


_RESTART_REQUEST_RE = re.compile(
    r"(?:^|\s|[\u3400-\u9fff])(?:重来|重新来|从头(?:开始)?|重新开始|再做一次|再来一次|重做|重新执行|重试|"
    r"start\s+over|restart|redo|retry(?:\s+from\s+scratch)?)(?:$|\s|[，。！？,.!?])",
    re.IGNORECASE,
)
_TOOL_MODE_PARTS = {
    "knowledge",
    "research",
    "slides",
    "task-documents",
    "web",
    "web-auto",
    "workspace-status",
    "zotero-status",
    "zotero-search",
    "verified-answer",
    "benchmark",
}
_EVIDENCE_TOOL_NAMES = {
    "search_local_evidence",
    "kb_search",
    "zotero_search",
    "zotero_fulltext",
    "obsidian_search",
    "obsidian_read",
    "build_verified_answer",
}
_ARTIFACT_TOOL_NAMES = {
    "create_document",
    "create_pdf",
    "create_spreadsheet",
    "create_presentation",
    "compile_latex",
}


def _pi_mode_parts(task_mode: str | None) -> set[str]:
    """Return the independently enforceable capabilities in a Pi task mode."""

    normalized = str(task_mode or "general").strip().lower()
    return {part for part in normalized.split("+") if part}


def _pi_requires_tools(task_mode: str | None, user_text: str = "") -> bool:
    """Return whether this turn must complete at least one concrete tool call.

    A mode can expose tools without requiring them.  In particular, ``web-auto``
    lets Pi decide whether retrieval is useful for ordinary conversation, while
    an explicit request to search current/online information remains mandatory.
    """

    return bool(_required_pi_tool_groups(task_mode, user_text))


def _pi_should_run(
    task_mode: str | None,
    user_text: str = "",
    task_profile: dict[str, Any] | None = None,
) -> bool:
    """Route every model-mediated text turn through Pi.

    ``task_mode`` and the deterministic profile remain useful for evidence,
    risk, budget and initial-tool hints.  They no longer select a cheaper
    direct-provider cognition path: one session abstraction is what gives the
    model continuity, progressive tools and auditable lifecycle semantics.
    Deterministic local facts bypass this helper at the caller.
    """

    del task_mode, user_text, task_profile
    return True


def _is_managed_service_fallback_error(error: BaseException) -> bool:
    """Return whether a clean managed-model failover is safe.

    A fallback is intentionally narrow: only rate limits and short-lived
    upstream/gateway availability failures qualify, and callers must still
    ensure that no text, tool, or other visible effect has escaped.  Bad
    prompts, malformed output, and quality failures must never silently switch
    models because that would hide a real product defect from the user.
    """

    failure = getattr(error, "failure", None)
    details = failure if isinstance(failure, dict) else {}
    parts = [str(error)]
    for key in ("code", "reason", "message", "detail", "error_class"):
        value = details.get(key)
        if value:
            parts.append(str(value))
    normalized = " ".join(parts).lower()
    return bool(
        re.search(
            r"(?:\b429\b|rate[ _-]?limit|provider_rate_limited|"
            r"upstream_rate_limited|gateway_rate_limit|managed_model_not_configured|"
            r"\b(?:502|503|504)\b|temporar(?:y|ily).{0,32}(?:unavailable|busy))",
            normalized,
        )
    )


def _direct_output_budget(user_text: str, selected_skills: list[dict[str, Any]] | None = None) -> int:
    """Return an output budget that follows the user's requested scope.

    Ordinary conversation stays compact, but an explicitly structured long-form
    request must never be forced through the same small response window.  In
    particular, a request for several sections with required checks and an end
    marker is an instruction about completeness, not an invitation to truncate
    it and ask the user to continue in another turn.
    """

    text = str(user_text or "")
    skill_ids = {str(item.get("id", "")).strip().lower() for item in list(selected_skills or [])}
    if "good-question" in skill_ids:
        return 1_600
    if re.search(
        r"(?:不超过|至多|限(?:制)?在?)\s*\d+\s*(?:字|词|tokens?)"
        r"|(?:一句|两句|二句|一段|简短|简洁|精简)"
        r"|(?:one|two|[12])\s+sentences?\b|\bbrief(?:ly)?\b|\bconcise(?:ly)?\b",
        text,
        re.IGNORECASE,
    ):
        return 384
    if re.search(
        r"(?:[四五六七八九十\d]+\s*个(?:编号)?(?:部分|章节)|第\s*[四五六七八九十\d]+\s*(?:部分|章))"
        r"|(?:每(?:个|一)?(?:部分|章节).{0,24}(?:至少|不少于|包含).{0,16}\d+\s*条)"
        r"|(?:不要省略|完整结尾|最后一行|末尾必须|end\s+with)"
        r"|(?:\b(?:[4-9]|[1-9]\d+)\s+(?:numbered\s+)?(?:sections?|parts?|chapters?)\b)",
        text,
        re.IGNORECASE,
    ):
        return 4_096
    if skill_ids & {"literature-review", "nature-writing", "nature-reviewer", "nature-response"}:
        return 4_096
    if re.search(
        r"(?:详细|全面|系统|完整|深入|长文|综述|教程|报告)"
        r"|(?:detailed|comprehensive|in[- ]depth|tutorial|report|review)",
        text,
        re.IGNORECASE,
    ):
        return 3_072
    if skill_ids & {"academic-research-suite", "nature-polishing", "nature-paper2ppt"}:
        return 3_072
    if skill_ids & {
        "scientific-brainstorming",
        "nature-statistics",
        "scientific-visualization",
        "nature-figure",
        "nature-data",
    }:
        return 2_048
    return 1_024


def _direct_max_continuations(
    user_text: str,
    selected_skills: list[dict[str, Any]] | None = None,
) -> int:
    """Allow one safe continuation only for a user-requested long response."""

    return 1 if _direct_output_budget(user_text, selected_skills) >= 3_072 else 0


def _requested_completion_marker_from_text(user_text: str) -> str:
    """Return a user-requested terminal marker, without inventing one.

    Markers such as ``【回答完毕】`` are a completion contract for structured
    writing.  They are not part of the research content and can safely be
    restored after ScanSci has verified that the requested structure is already
    present.
    """

    text = str(user_text or "")
    if not re.search(r"(?:最后|末尾|结尾|last\s+line|end\s+with)", text, re.IGNORECASE):
        return ""
    markers = re.findall(r"【[^】\r\n]{1,40}】", text)
    return markers[-1] if markers else ""


def _requested_numbered_section_count(user_text: str) -> int:
    """Read an explicit top-level section count from a structured request."""

    text = str(user_text or "")
    match = re.search(
        r"(?:共|总共|合计|分为)\s*([一二三四五六七八九十\d]+)\s*(?:个)?(?:编号)?(?:部分|章节|章)",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
            r"(?:numbered\s+)?(?:sections?|parts?|chapters?)\b",
            text,
            re.IGNORECASE,
        )
    if not match:
        return 0
    raw = match.group(1)
    if raw.isdigit():
        return max(0, min(int(raw), 50))
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if raw in digits:
        return digits[raw]
    english_digits = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    if raw.casefold() in english_digits:
        return english_digits[raw.casefold()]
    if raw.startswith("十") and len(raw) == 2 and raw[1] in digits:
        return 10 + digits[raw[1]]
    if raw.endswith("十") and len(raw) == 2 and raw[0] in digits:
        return digits[raw[0]] * 10
    return 0


def _requested_section_check_count(user_text: str) -> int:
    """Read an explicit per-section actionable-check requirement.

    A request such as "每部分至少包含三条可执行检查" is not merely
    stylistic.  It defines a verifiable delivery contract: checks must be
    individually inspectable rather than hidden in prose paragraphs.
    """

    text = str(user_text or "")
    match = re.search(
        r"每(?:个|一)?(?:部分|章节|节)[^。；\n]{0,32}?"
        r"(?:至少|不少于|至少要|需|需要|包含|有)\s*(?:包含)?\s*"
        r"([一二三四五六七八九十\d]+)\s*(?:条|项|个)?(?:可执行)?"
        r"(?:检查|步骤|要点|清单|项目)",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"(?:each|every)\s+(?:section|part|chapter)[^.\n]{0,80}?"
            r"(?:at\s+least|no\s+fewer\s+than|contain(?:s)?|include(?:s)?)\s*"
            r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
            r"(?:actionable\s+)?(?:checks?|steps?|items?|bullets?)",
            text,
            re.IGNORECASE,
        )
    if not match:
        return 0
    raw = str(match.group(1))
    if raw.isdigit():
        return max(0, min(int(raw), 50))
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if raw in digits:
        return digits[raw]
    english_digits = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    if raw.casefold() in english_digits:
        return english_digits[raw.casefold()]
    if raw.startswith("十") and len(raw) == 2 and raw[1] in digits:
        return 10 + digits[raw[1]]
    if raw.endswith("十") and len(raw) == 2 and raw[0] in digits:
        return digits[raw[0]] * 10
    return 0


def _numbered_heading_count(text: str) -> int:
    """Count top-level numbered headings without treating ordinary bullets as sections."""

    return len(re.findall(
        r"(?m)^\s*(?:#{1,6}\s*)?(?:第\s*)?(?:[一二三四五六七八九十]|[1-9]\d*)\s*(?:部分|章(?:节)?|[、.．:：])\s*[^\s]",
        str(text or ""),
    ))


def _numbered_section_check_counts(text: str) -> list[int]:
    """Count explicit Markdown checklist items in each numbered section.

    The direct-writing contract asks for independently actionable checks.
    Requiring a Markdown list keeps that contract clear for readers and lets
    ScanSci validate it without guessing whether a prose sentence was a check.
    """

    headings = list(re.finditer(
        r"(?m)^\s*(?:#{1,6}\s*)?(?:第\s*)?(?:[一二三四五六七八九十]|[1-9]\d*)\s*(?:部分|章(?:节)?|节|[、.．:：])\s*[^\s]",
        str(text or ""),
    ))
    counts: list[int] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(str(text or ""))
        section_body = str(text or "")[heading.end():end]
        counts.append(len(re.findall(r"(?m)^\s*[-*•]\s+(?:\[[ xX]\]\s*)?\S", section_body)))
    return counts


def _trim_terminal_word_loop(text: str) -> tuple[str, bool]:
    """Remove an obviously corrupted repeated-word suffix from a model reply.

    This deliberately only accepts a long whitespace-separated repetition at
    the *very end* of a reply.  It cannot remove normal rhetorical repetition
    inside a paragraph, and it is used only before completion validation.
    """

    value = str(text or "")
    repeated = re.search(
        r"(?is)(?:^|\s)(?P<word>[a-z\u4e00-\u9fff][a-z0-9_\-\u4e00-\u9fff]{0,23})"
        r"(?:\s+(?P=word)){7,}\s*(?P<marker>【[^】\r\n]{1,40}】)?\s*$",
        value,
    )
    if not repeated:
        return value, False
    prefix = value[:repeated.start()].rstrip()
    marker = str(repeated.group("marker") or "").strip()
    if prefix and marker:
        return prefix + f"\n\n{marker}", True
    return prefix, True


def _settle_structured_direct_output(
    user_text: str,
    text: str,
    *,
    truncated: bool,
) -> tuple[str, bool, bool]:
    """Finish a structurally complete direct answer without hiding real gaps.

    A provider can hit its generation limit after producing every requested
    section, yet miss a terminal marker or enter a pathological word loop.
    ScanSci may restore only the user's marker after checking the requested
    top-level section count.  If the structure is incomplete, the caller keeps
    the response marked as truncated instead of calling it complete.
    """

    value, removed_loop = _trim_terminal_word_loop(text)
    marker = _requested_completion_marker_from_text(user_text)
    required_sections = _requested_numbered_section_count(user_text)
    required_checks = _requested_section_check_count(user_text)
    check_counts = _numbered_section_check_counts(value)
    checks_complete = not required_checks or (
        len(check_counts) >= required_sections
        and all(count >= required_checks for count in check_counts[:required_sections])
    )
    complete = bool(
        truncated
        and marker
        and required_sections > 0
        and _numbered_heading_count(value) >= required_sections
        and checks_complete
    )
    if complete and not value.rstrip().endswith(marker):
        value = value.rstrip() + f"\n\n{marker}"
    return value, complete, removed_loop


def _structured_output_contract_gap(user_text: str, text: str) -> str:
    """Describe a missing explicit writing constraint, if there is one.

    Direct completion can occasionally return a valid protocol response that
    is unrelated to a constrained writing request (for example, a short
    gateway greeting after a transient retry). It is not safe to display such
    a response merely because it is non-empty. This helper only enforces
    constraints the user actually stated: numbered top-level sections,
    explicit checklist items, and an explicit terminal marker.
    """

    required_sections = _requested_numbered_section_count(user_text)
    required_checks = _requested_section_check_count(user_text)
    marker = _requested_completion_marker_from_text(user_text)
    gaps: list[str] = []
    if required_sections:
        actual_sections = _numbered_heading_count(text)
        if actual_sections < required_sections:
            gaps.append(f"需要 {required_sections} 个编号部分，实际只有 {actual_sections} 个")
    if marker and not str(text or "").rstrip().endswith(marker):
        gaps.append(f"缺少结尾标记 {marker}")
    if required_checks and required_sections:
        check_counts = _numbered_section_check_counts(text)
        short_sections = [
            str(index + 1)
            for index, count in enumerate(check_counts[:required_sections])
            if count < required_checks
        ]
        if len(check_counts) < required_sections:
            short_sections.extend(str(index) for index in range(len(check_counts) + 1, required_sections + 1))
        if short_sections:
            gaps.append(
                f"第 {', '.join(dict.fromkeys(short_sections))} 部分缺少逐条列出的可执行检查（每部分至少 {required_checks} 条）"
            )
    return "；".join(gaps)


def _structured_retry_messages(
    messages: list[dict[str, Any]],
    user_text: str,
    *,
    previous_gap: str = "",
    attempt: int = 1,
) -> list[dict[str, Any]]:
    """Return an isolated retry prompt for a failed structured delivery.

    The original assistant output is deliberately not included: it may be a
    stale gateway greeting, and feeding it back increases the chance of the
    model continuing that unrelated response. The retry remains a pure model
    read, so it is only used before text has been emitted to the user.
    """

    marker = _requested_completion_marker_from_text(user_text)
    required_sections = _requested_numbered_section_count(user_text)
    required_checks = _requested_section_check_count(user_text)
    instructions = [
        "Return the answer to the user's final request now.",
        "Do not introduce yourself, describe ScanSci, or return a generic greeting.",
        "Output only the requested deliverable and honor every explicit format constraint.",
    ]
    if required_sections:
        instructions.append(
            f"Use at least {required_sections} clearly numbered top-level sections."
        )
    if required_checks:
        instructions.append(
            f"Inside every numbered section, render at least {required_checks} actionable checks as separate Markdown list lines beginning with '- '. Do not bury checks inside prose paragraphs."
        )
    if marker:
        instructions.append(f"End the final line exactly with {marker}.")
    if previous_gap:
        instructions.append(
            "The previous draft was not delivered because its objective format "
            f"checks failed: {previous_gap}. Rebuild the full answer from scratch."
        )
    if attempt > 1:
        instructions.append(
            "This is the final automatic repair attempt. Do not explain the failure; "
            "return only the complete requested deliverable."
        )
    retry_context = {"role": "system", "content": " ".join(instructions)}
    if not messages:
        return [retry_context]
    return [*messages[:-1], retry_context, messages[-1]]


def _direct_thinking_mode(chat_request: "_DirectChatRequest") -> str | None:
    """Disable paid hidden reasoning for the lightweight managed-chat path."""

    if chat_request.provider_id == "scansci-managed" and chat_request.model_id == "glm-4.7-flash":
        return "disabled"
    return chat_request.thinking_mode


def _model_transport_kwargs(chat_request: "_DirectChatRequest") -> dict[str, Any]:
    """Forward the selected provider capability contract to model calls."""

    return {
        "api_surface": chat_request.api_surface,
        "provider_id": chat_request.provider_id,
        "responses_enabled": chat_request.responses_enabled,
        "previous_response_id": chat_request.previous_response_id,
    }


def _repair_scientific_rewrite(user_text: str, answer: str) -> str:
    """Narrowly remove causal absolutes that survive a requested rewrite."""

    request = str(user_text or "")
    text = str(answer or "").strip()
    if not re.search(r"(?:改写|润色|重写|严谨|rewrite|revise|polish)", request, re.IGNORECASE):
        return text
    if not re.search(r"(?:证明|肯定|所有|唯一(?:原因|因素|决定因素)|prove|certain|only cause)", request, re.IGNORECASE):
        return text
    replacements = (
        ("非常清楚地", ""),
        ("明确地证明了", "提示"),
        ("明确证明了", "提示"),
        ("证明了", "提示"),
        ("证实了", "提示"),
        ("证实", "提示"),
        ("明确表明", "提示"),
        ("肯定", ""),
        ("所有", "所观察到的"),
        ("唯一决定因素", "可能影响因素之一"),
        ("唯一原因", "可能影响因素之一"),
        ("唯一因素", "可能影响因素之一"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _guard_temporal_delivery(user_text: str, answer: str) -> tuple[str, bool]:
    """Prevent stale search results from being labelled as today's news."""

    request = str(user_text or "")
    text = str(answer or "").strip()
    if not re.search(
        r"(?:今天|今日|当天|最新|当前|近期|today|today'?s|latest|current|recent)",
        request,
        re.IGNORECASE,
    ):
        return text, False

    current = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    labelled_dates = [
        (int(month), int(day))
        for month, day in re.findall(
            r"(?:今天|今日)\s*[（(]?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
            text,
        )
    ]
    mismatched_label = any(
        month != current.month or day != current.day
        for month, day in labelled_dates
    )
    url_years = [
        int(value)
        for value in re.findall(r"https?://[^\s)\]]+?/((?:19|20)\d{2})(?:/|[-_])", text)
    ]
    stale_link_set = (
        len(url_years) >= 2
        and max(url_years) < current.year
        and str(current.year) not in text
    )
    if not mismatched_label and not stale_link_set:
        return text, False

    guarded = re.sub(
        r"(?:今天|今日)\s*[（(]?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[）)]?",
        r"搜索结果标注日期（\1月\2日）",
        text,
    )
    notice = (
        f"未能从本轮搜索结果中验证 {current.isoformat()} 当天的有效内容。"
        "下面是搜索服务返回的较早资料，不能视为“今天/最新”的新闻。"
    )
    return f"{notice}\n\n{guarded}".strip(), True


def _requested_artifact_tools(text: str) -> set[str]:
    normalized = str(text or "").strip().lower()
    requested: set[str] = set()
    if re.search(r"(?:pptx?|powerpoint|slides?|幻灯片|演示文稿)", normalized, re.IGNORECASE):
        requested.add("create_presentation")
    if re.search(r"(?:docx?|word\s+document|word文档|文档)", normalized, re.IGNORECASE):
        requested.add("create_document")
    if re.search(r"(?:xlsx?|excel|spreadsheet|电子表格)", normalized, re.IGNORECASE):
        requested.add("create_spreadsheet")
    if re.search(r"(?:latex|\.tex\b|tex\s+source)", normalized, re.IGNORECASE):
        requested.add("compile_latex")
    if re.search(r"(?:pdf|便携式文档)", normalized, re.IGNORECASE):
        requested.add("create_pdf")
    return requested


def _required_pi_tool_groups(task_mode: str | None, user_text: str = "") -> list[set[str]]:
    """Build an AND-of-ORs execution contract for the current request."""

    user_text = _intent_classification_text(user_text)
    parts = _pi_mode_parts(task_mode)
    groups: list[set[str]] = []
    if "zotero-status" in parts:
        groups.append({"zotero_status"})
    if "workspace-status" in parts:
        groups.append({"inspect_workspace"})
    if "zotero-search" in parts:
        groups.append({"zotero_search"})
    if "web" in parts:
        # Any successful read-only web route satisfies explicit web access.
        # Requiring only discovery search made direct-URL reads call an extra,
        # irrelevant search after the requested page had already been read.
        groups.append({"search_web", "agent_reach", "browser_access", "discover_papers"})
    if "web-auto" in parts and re.search(
        r"(?:web|internet|online|联网|网上|网络).{0,24}(?:search|find|look\s*up|检索|搜索|查找|查询|看看)"
        r"|(?:search|find|look\s*up|检索|搜索|查找|查询).{0,24}(?:recent|latest|current|today|news|近期|最新|当前|今天)",
        str(user_text or ""),
        re.IGNORECASE,
    ):
        groups.append({"search_web", "agent_reach", "browser_access", "discover_papers"})
    if "task-documents" in parts:
        groups.append({"read_task_documents", "summarize_documents"})
    explicit_knowledge = bool(
        re.search(
            r"(?:knowledge\s*base|zotero|obsidian|知识库|本地库|文献库|资料库|向量库)"
            r"|(?:这些|这批|已连接|已链接|当前).{0,12}(?:文献|论文|资料|知识库)"
            r"|(?:linked|local|selected|current).{0,18}(?:library|documents?|papers?)",
            str(user_text or ""),
            re.IGNORECASE,
        )
    )
    if "knowledge" in parts and explicit_knowledge:
        groups.append(set(_EVIDENCE_TOOL_NAMES))
    if "verified-answer" in parts:
        groups.append({"build_verified_answer"})
    if "research" in parts:
        normalized = str(user_text or "").lower()
        wants_download = bool(
            re.search(
                r"(?:download|acquire|fetch|full\s*text|下载|获取(?:论文|文献)|全文|索引)",
                normalized,
                re.IGNORECASE,
            )
        )
        groups.append({"download_and_index"} if wants_download else {"discover_papers", "search_web"})
        if wants_download and re.search(
            r"(?:summari[sz]e|synthesi[sz]e|review|compare|analy[sz]e|总结|综述|比较|对比|分析|归纳)",
            normalized,
            re.IGNORECASE,
        ):
            groups.append({"summarize_documents"})
        if wants_download:
            groups.append({"check_task_completion"})
    explicit_artifact = bool(
        re.search(
            r"(?:create|generate|make|build|export|save|produce|downloadable|创建|生成|制作|导出|保存|产出|可下载|实际文件)",
            str(user_text or ""),
            re.IGNORECASE,
        )
    )
    if "slides" in parts and explicit_artifact:
        requested = _requested_artifact_tools(user_text)
        groups.append(requested or {"build_presentation_outline", "create_presentation"})
    return groups


def _missing_pi_tool_groups(
    task_mode: str | None,
    completed_tool_names: set[str],
    user_text: str = "",
) -> list[set[str]]:
    return [
        group
        for group in _required_pi_tool_groups(task_mode, user_text)
        if completed_tool_names.isdisjoint(group)
    ]


def _validate_pi_delivery(
    text: str,
    *,
    task_mode: str | None,
    user_text: str,
    tool_calls: list[dict[str, Any]] | None,
) -> CapabilityLedger:
    """Reject plans and unsupported completion claims at the delivery boundary."""

    ledger = CapabilityLedger.from_tool_calls(
        _required_pi_tool_groups(task_mode, user_text),
        tool_calls,
    )
    if not ledger.ready:
        raise ledger.delivery_error()
    if not _pi_requires_tools(task_mode, user_text):
        return ledger
    tail = str(text or "").strip()[-1_200:]
    if re.search(
        r"(?:\bI (?:will|shall)(?: now)?\b|\bLet me (?:now )?\b|\bNext,? I will\b)"
        r".{0,80}(?:search|retrieve|download|summari[sz]e|review|create|generate|continue)"
        r"|(?:接下来|现在|下一步|随后)(?:我|将|会|准备|开始).{0,40}(?:检索|搜索|下载|总结|综述|创建|生成|继续)"
        r"|<SCANSCI_TOOL_CALL>|\{\s*[\"']name[\"']\s*:\s*[\"'][A-Za-z0-9_-]+[\"']",
        tail,
        re.IGNORECASE | re.DOTALL,
    ):
        raise RuntimeError("Pi stopped at an execution plan instead of returning a completed result")
    return ledger


def _required_capability_preflight(task_contract: dict[str, Any]) -> CapabilityLedger:
    """Fail early only when the host has already proven a required tool unavailable."""

    ledger = CapabilityLedger.from_contract(task_contract)
    if ledger.required_groups and ledger.unavailable_groups():
        raise ledger.delivery_error(cause="Host capability preflight marked every candidate unavailable")
    return ledger


def _is_restart_request(text: str) -> bool:
    """Return whether a short follow-up asks to repeat the original task."""

    normalized = re.sub(r"[\u200b\ufeff]+", "", str(text or "")).strip()
    if not normalized or re.search(r"(?:不要|别|无需|不必)\s*(?:重来|重做|重试|restart|redo|retry)", normalized, re.I):
        return False
    return bool(_RESTART_REQUEST_RE.search(normalized))
_REPEATED_CJK_PHRASE = re.compile(r"(?P<phrase>[\u3400-\u9fff]{2,6})(?P=phrase)")


class _RunCancelled(RuntimeError):
    """Internal control flow for a cooperatively cancelled long-running tool."""


def _structured_recovery(error: BaseException, *, stage_key: str = "") -> dict[str, Any]:
    """Classify a failure into safe user actions instead of exposing a stack trace."""

    raw = redact_sensitive_text(f"{type(error).__name__}: {error}")
    upstream = getattr(error, "failure", None)
    if isinstance(upstream, dict) and upstream.get("code"):
        upstream_actions = upstream.get("recovery_actions", upstream.get("actions", []))
        return {
            "code": str(upstream.get("code", "stage_failed")),
            "message": redact_sensitive_text(upstream.get("message", str(error))),
            "detail": redact_sensitive_text(upstream.get("detail", raw))[:1000],
            "stage_key": str(stage_key),
            "retryable": bool(upstream.get("retryable", False)),
            "actions": [
                dict(item)
                for item in list(upstream_actions or [])
                if isinstance(item, dict)
            ][:8],
        }
    text = raw.lower()
    if "scansci-pdf" in text and "json" in text and ("检索" in raw or "search" in text):
        code, message, retryable = (
            "paper_search_response_invalid",
            "文献检索服务返回了无法读取的结果，下载尚未开始。",
            True,
        )
        actions = [
            {"id": "retry", "label": "重新检索", "kind": "resume"},
        ]
    elif "未找到可下载的 doi 或 arxiv" in raw.lower() or "no downloadable doi or arxiv" in text:
        code, message, retryable = (
            "paper_search_no_downloadable_identifier",
            "已完成检索，但候选文献没有可用于合法获取的 DOI 或 arXiv 标识；尚未开始下载。",
            True,
        )
        actions = [
            {"id": "retry", "label": "调整条件后重试", "kind": "resume"},
            {"id": "branch", "label": "改用学术搜索查看候选", "kind": "branch"},
        ]
    elif "http 402" in text or "insufficient balance" in text or "余额不足" in text:
        code, message, retryable = (
            "provider_balance_exhausted",
            "当前模型服务余额不足，ScanSci 已停止继续请求。请充值该服务商账户，或切换到其他可用模型。",
            False,
        )
        actions = [
            {"id": "change_model", "label": "切换模型", "kind": "branch"},
            {"id": "open_settings", "label": "检查模型服务", "kind": "settings"},
        ]
    elif (
        "tool loop failed" in text
        or "required tool" in text
        or "不会退化" in text
        or "必需的工具调用" in text
    ):
        code, message, retryable = (
            "required_tool_failed",
            redact_sensitive_text(str(error)),
            True,
        )
        actions = [
            {"id": "retry", "label": "重试工具流程", "kind": "resume"},
            {"id": "branch", "label": "换模型后分支", "kind": "branch"},
        ]
    elif "rate limit" in text or "rate_limit" in text or "429" in text:
        code, message, retryable = (
            "provider_rate_limited",
            "模型服务暂时限流，已完成阶段与本地文件均已保留。",
            True,
        )
        actions = [
            {"id": "retry", "label": "自动重试", "kind": "resume"},
            {"id": "branch", "label": "换模型后分支", "kind": "branch"},
        ]
    elif "timeout" in text or "timed out" in text:
        code, message, retryable = (
            "stage_timeout",
            "当前步骤超时，ScanSci 可以从这个阶段重试或更换获取策略。",
            True,
        )
        actions = [
            {"id": "retry", "label": "重试当前阶段", "kind": "resume"},
            {"id": "change_strategy", "label": "更换策略", "kind": "branch"},
        ]
    elif "context" in text and ("limit" in text or "length" in text):
        code, message, retryable = (
            "context_limit",
            "会话上下文超过模型上限；任务记录、证据和产物没有丢失。",
            True,
        )
        actions = [
            {"id": "compact_retry", "label": "压缩并继续", "kind": "compact_retry"},
            {"id": "branch", "label": "从此处分支", "kind": "branch"},
        ]
    elif "not found" in text or "不存在" in text or "unavailable" in text:
        code, message, retryable = (
            "resource_unavailable",
            "目标资源当前不可用，可以切换来源或保留已有结果继续。",
            True,
        )
        actions = [
            {"id": "change_source", "label": "切换来源", "kind": "branch"},
            {"id": "retry", "label": "重试", "kind": "resume"},
        ]
    else:
        code, message, retryable = (
            "stage_failed",
            "当前步骤未完成。ScanSci 已保留此前成功阶段，可重试或从这里建立分支。",
            False,
        )
        actions = [
            {"id": "retry", "label": "重试当前阶段", "kind": "resume"},
            {"id": "branch", "label": "保留现场并分支", "kind": "branch"},
        ]
    return {
        "code": code,
        "message": message,
        "detail": raw[:1000],
        "stage_key": str(stage_key),
        "retryable": retryable,
        "actions": actions,
    }


def _tor_failure_hint(error_name: str) -> str:
    """Return a concise user-facing suggestion when Tor cannot start.

    The exact hint depends on what went wrong and whether Tor itself is
    installed, making the message actionable rather than opaque.
    """
    name = error_name.lower() if error_name else ""
    if "filenotfound" in name or "module" in name:
        return "stem 或 PySocks 未安装，请运行 pip install scansci[tor]"
    if "timeout" in name or "bootstrap" in name:
        return "Tor 无法连接中继——请确保本地代理（Clash/V2Ray）已启动，或前端选择 obfs4/snowflake 传输方式"
    if "port" in name or "address" in name or "occupied" in name:
        return "Tor 端口被占用，请关闭已有 Tor 进程后重试"
    return "请确保本地代理已运行，或在下载设置中调整传输方式后重试"


def _has_selected_skill(selected_skills: list[dict[str, Any]], identifier: str) -> bool:
    return any(
        str(item.get("id", "")).strip().lower() == identifier
        and str(item.get("status", "loaded") or "loaded") == "loaded"
        for item in selected_skills
    )


def _normalize_direct_chat_output(text: str, selected_skills: list[dict[str, Any]]) -> str:
    """Apply narrow presentational cleanup required by formal Skill outputs."""

    normalized = str(text or "").strip()
    if not _has_selected_skill(selected_skills, "good-question"):
        return normalized
    # Small managed models occasionally repeat a whole Chinese phrase at a
    # token boundary (for example, ``线性线性``).  This is safe to collapse in
    # the formal card because intentional rhetorical repetition is forbidden
    # by the Skill contract.
    for _ in range(3):
        cleaned = _REPEATED_CJK_PHRASE.sub(r"\g<phrase>", normalized)
        if cleaned == normalized:
            break
        normalized = cleaned
    return normalized


def _validate_direct_chat_output(text: str, selected_skills: list[dict[str, Any]]) -> None:
    """Refuse to mark an incomplete formal Skill artifact as delivered."""

    if not _has_selected_skill(selected_skills, "good-question"):
        return
    missing = [field for field in _GOOD_QUESTION_FIELDS if field not in text]
    for hypothesis in ("H1", "H2", "H3"):
        if hypothesis not in text:
            missing.append(hypothesis)
    if len(text) < 320:
        missing.append("完整卡片正文")
    if "�" in text:
        missing.append("无乱码正文")
    if "基于用户信息的暂定判断：" not in text:
        missing.append("显式的证据边界")
    if re.search(r"(?:[A-Za-z][A-Za-z0-9_]*\s*[=~]\s*|[α-ωΑ-Ω]|系数)", text):
        missing.append("不含未审定公式的判别规则")
    if missing:
        labels = "、".join(dict.fromkeys(item.replace("**", "") for item in missing))
        raise RuntimeError(
            f"科学问题卡未通过完整性校验（缺少：{labels}）。"
            "ScanSci 没有把不完整草稿标记为完成，请重试或切换内置 GLM 模型。"
        )


def _merge_usage(primary: dict[str, int], secondary: dict[str, int]) -> dict[str, int]:
    keys = set(primary) | set(secondary)
    return {
        key: int(primary.get(key, 0)) + int(secondary.get(key, 0))
        for key in keys
        if isinstance(primary.get(key, 0), int) and isinstance(secondary.get(key, 0), int)
    }


def _card_value(text: str, label: str, fallback: str) -> str:
    match = re.search(
        rf"\*\*{re.escape(label)}：\*\*\s*(.+?)(?=\n\s*\n?\*\*|\Z)",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return fallback
    value = re.sub(r"\s+", " ", match.group(1)).strip(" -*\n")
    if re.search(r"(?:[A-Za-z][A-Za-z0-9_]*\s*[=~]\s*|[α-ωΑ-Ω]|系数)", value):
        return fallback
    return value[:320] if value else fallback


def _safe_good_question_fallback(chat_request: _DirectChatRequest, draft: str) -> str:
    """Keep a model-shaped card useful when its statistical notation is unsafe.

    The model still supplies the topic and core question.  The application
    replaces only the untrusted hypothesis/test scaffold with a conservative,
    domain-neutral strong-inference design that makes its assumptions explicit.
    """

    title = _card_value(draft, "暂定题目", "可证伪研究问题（待用户确认）")
    core = _card_value(
        draft,
        "核心研究问题",
        "在用户描述的对象、时间与条件下，主要暴露与主要结局是否存在达到预设最小有意义效应的稳定关系？",
    )
    user_request = next(
        (
            str(item.get("content", ""))
            for item in reversed(chat_request.messages)
            if item.get("role") == "user" and item.get("content")
        ),
        "用户已提供初步研究方向，具体变量名待确认。",
    )
    user_request = re.sub(r"(?<!\S)\$good-question\b", "", user_request, flags=re.IGNORECASE)
    user_request = re.sub(r"\s+", " ", user_request).strip()[:260]
    if re.search(r"(?:[A-Za-z][A-Za-z0-9_]*\s*[=~]\s*|[α-ωΑ-Ω]|系数)", user_request):
        user_request = "用户已提供初步研究方向；原始描述中的统计表达式需在后续分析方案中单独审定。"
    return f"""## 好问题卡
**暂定题目：** {title}

**核心研究问题：** {core}

**为什么值得做：** 基于用户信息的暂定判断：回答这一问题将决定是否值得进入完整研究，并明确下一步应优先补数据、控制混杂还是检验目标关系；当前没有把任何文献空白当作既定事实。

**它挑战了什么默认假设：** 用户提出的主要关系在不同时间、地点或样本层中都稳定存在，而且不是由共同变化的背景因素或测量方式造成。

**竞争性解释：** H1（目标解释）：对核心问题的回答为“是”，目标关系在预先划分的样本层和留出数据中方向一致；H2（替代解释）：表面关系主要由共同变化的背景因素解释，匹配或分层后明显减弱；H3（零效应或测量解释）：目标关系小于最小有意义效应，或在重复测量、负对照与留出数据中不能复现。

**关键判别证据或实验：** 先固定研究对象、主要暴露、主要结局、关键对照和最小有意义效应，再预先划分探索集与留出集。H1 预期目标模式跨样本层复现且明显强于负对照；H2 预期加入关键背景因素或匹配对照后目标模式大幅减弱；H3 预期重复测量不稳定、留出集不复现，或负对照出现同等强度的模式。观察性数据只解释为关联，不直接声称因果。

**什么结果会推翻它：** 若留出数据中的效应方向与探索集相反，至少半数预设样本层不能复现，效应低于事先定义的最小有意义门槛，或负对照达到与目标关系相近的强度，则推翻目标解释。

**两周内可做的 pilot：** 第1—2天完成变量字典、纳入标准和最小有意义效应预设；第3—5天检查缺失、异常值、时间与空间对齐；第6—9天在不超过全量20%的分层样本上完成探索；第10—12天做留出集、负对照和敏感性检查；第13—14天形成决策记录。继续门槛：关键变量缺失低于10%、至少70%的预设样本层方向一致、目标效应超过预设门槛且负对照小于目标效应的三分之一；数据质量合格但只满足两项则修改问题，少于两项则停止当前路线。

**所需数据与资源：** 已有信息：{user_request or '用户已提供初步研究方向。'} 待补内容：主要暴露、主要结局、分析单位、对照、最小有意义效应及负对照的精确定义。最大依赖是数据在时间、空间和测量口径上的可比性。

**最强评审质疑：** 这是观察性关联，选择偏差、混杂或测量误差足以产生同样模式。最低成本应对是预先固定变量与排除规则，保留独立留出集，并加入一个结果负对照或暴露负对照；若这些检查失败，就降低主张而不是补写因果故事。

**下一步：** 现在填写一行变量字典：研究对象｜主要暴露｜主要结局｜分析单位｜关键对照｜最小有意义效应。
""".strip()


_WORKFLOWS: dict[str, dict[str, Any]] = {
    "evidence_index": {
        "label": "语义检索优化",
        "artifact_type": "evidence_index",
        "stages": [
            StageSpec("build", "建立本地语义检索", "tool", "scansci.evidence.index"),
        ],
    },
    "ask": {
        "label": "证据问答",
        "artifact_type": "evidence_answer",
        "stages": [
            StageSpec("plan", "理解问题", "planner"),
            StageSpec("research", "检索与综合", "tool", "scansci.evidence.ask"),
            StageSpec("verify", "证据核验", "verification", "scansci.evidence.verify"),
            StageSpec("deliver", "交付回答", "delivery"),
        ],
    },
    "literature_review": {
        "label": "证据综述",
        "artifact_type": "literature_review",
        "stages": [
            StageSpec("plan", "确定综述边界", "planner"),
            StageSpec("research", "按章节检索证据", "tool", "scansci.review.retrieve"),
            StageSpec("synthesize", "跨论文比较与写作", "tool", "scansci.review.write"),
            StageSpec("verify", "核验段落引用", "verification", "scansci.evidence.verify"),
            StageSpec("deliver", "生成综述产物", "delivery"),
        ],
    },
    "academic_search": {
        "label": "学术搜索",
        "artifact_type": "academic_search_result",
        "stages": [
            StageSpec("plan", "制定可核验检索计划", "planner"),
            StageSpec("execute", "多源检索与相关性筛选", "tool", "scansci.academic.search"),
            StageSpec("deliver", "交付高相关候选文献", "delivery"),
        ],
    },
    "deep_research": {
        "label": "学术深度研究",
        "artifact_type": "deep_research_report",
        "stages": [
            StageSpec("plan", "拆解问题与研究视角", "planner"),
            StageSpec("discover", "多源搜索与证据缺口追踪", "tool", "scansci.academic.discover"),
            StageSpec("acquire", "获取可公开访问的全文", "tool", "scansci.academic.acquire"),
            StageSpec("research", "整理外部来源与可核验摘录", "tool", "scansci.review.retrieve"),
            StageSpec("synthesize", "多视角写作与证据绑定", "tool", "scansci.review.write"),
            StageSpec("verify", "逐句核验引用", "verification", "scansci.evidence.verify"),
            StageSpec("deliver", "交付深度研究报告", "delivery"),
        ],
    },
    "novelty_check": {
        "label": "证据驱动查新",
        "artifact_type": "novelty_assessment",
        "stages": [
            StageSpec("plan", "拆解主张与四轴查新问题", "planner"),
            StageSpec("discover", "多源检索潜在重合工作", "tool", "scansci.novelty.discover"),
            StageSpec("acquire", "获取合法全文并建立索引", "tool", "scansci.academic.acquire"),
            StageSpec("research", "检索逐论文全文证据", "tool", "scansci.novelty.retrieve"),
            StageSpec("assess", "执行四轴重合审查", "tool", "scansci.novelty.assess"),
            StageSpec("verify", "核验查新结论与引用", "verification", "scansci.evidence.verify"),
            StageSpec("deliver", "交付查新报告", "delivery"),
        ],
    },
    "research_idea": {
        "label": "证据驱动研究构思",
        "artifact_type": "research_idea_card",
        "stages": [
            StageSpec("plan", "界定研究方向与证据问题", "planner"),
            StageSpec("discover", "多源检索现有方法与失败模式", "tool", "scansci.idea.discover"),
            StageSpec("acquire", "获取合法全文并建立索引", "tool", "scansci.academic.acquire"),
            StageSpec("evidence", "检索瓶颈与方法全文证据", "tool", "scansci.idea.retrieve"),
            StageSpec("diagnose", "诊断承重研究瓶颈", "tool", "scansci.idea.diagnose"),
            StageSpec("generate", "生成单一候选方案", "tool", "scansci.idea.generate"),
            StageSpec("coherence", "执行隔离连贯性演算", "tool", "scansci.idea.coherence"),
            StageSpec("falsify", "核验可证伪结构", "tool", "scansci.idea.falsify"),
            StageSpec("implement", "执行可实现性审查", "tool", "scansci.idea.implement"),
            StageSpec("assemble", "组装研究 Idea Card", "tool", "scansci.idea.assemble"),
            StageSpec("verify", "核验证据与质量门", "verification", "scansci.evidence.verify"),
            StageSpec("deliver", "交付研究候选", "delivery"),
        ],
    },
    "journal_search": {
        "label": "期刊查询",
        "artifact_type": "journal_search_result",
        "stages": [
            StageSpec("plan", "识别期刊查询", "planner"),
            StageSpec("execute", "查询分区与指标", "tool", "scansci.journal.search"),
            StageSpec("deliver", "整理期刊结果", "delivery"),
        ],
    },
    "citation_analysis": {
        "label": "参考文献核验",
        "artifact_type": "citation_audit",
        "stages": [
            StageSpec("plan", "解析参考文献", "planner"),
            StageSpec("execute", "核验引用与元数据", "tool", "scansci.citation.analyze"),
            StageSpec("deliver", "生成核验报告", "delivery"),
        ],
    },
    "doi_verification": {
        "label": "DOI 核验",
        "artifact_type": "doi_verification",
        "stages": [
            StageSpec("plan", "识别 DOI 与题名", "planner"),
            StageSpec("execute", "查询权威元数据", "tool", "scansci.citation.verify_doi"),
            StageSpec("deliver", "生成 DOI 结论", "delivery"),
        ],
    },
    "paper_atlas": {
        "label": "Paper Atlas",
        "artifact_type": "paper_atlas_result",
        "stages": [
            StageSpec("plan", "确定图谱种子", "planner"),
            StageSpec("execute", "检索论文关系", "tool", "scansci.paper_atlas.search"),
            StageSpec("deliver", "生成图谱入口", "delivery"),
        ],
    },
    "paper_download": {
        "label": "文献下载",
        "artifact_type": "downloaded_paper",
        "stages": [
            StageSpec("plan", "解析论文标识", "planner"),
            StageSpec("execute", "查找合法全文", "tool", "scansci.paper.download"),
            StageSpec("deliver", "登记下载产物", "delivery"),
        ],
    },
    "paper_download_batch": {
        "label": "批量文献下载",
        "artifact_type": "downloaded_paper",
        "stages": [
            StageSpec("plan", "解析标识符列表", "planner"),
            StageSpec("execute", "逐条获取全文", "tool", "scansci.paper.download.batch"),
            StageSpec("deliver", "登记批量产物", "delivery"),
        ],
    },
    "paper_search_download": {
        "label": "检索并下载文献",
        "artifact_type": "downloaded_paper",
        "stages": [
            StageSpec("plan", "确认检索与下载条件", "planner"),
            StageSpec("search", "检索并筛选论文", "tool", "scansci.paper.search"),
            StageSpec("execute", "逐条获取可用全文", "tool", "scansci.paper.download.batch"),
            StageSpec("deliver", "登记批量产物", "delivery"),
        ],
    },
    "ppt_outline": {
        "label": "PPT 大纲",
        "artifact_type": "slide_outline",
        "stages": [
            StageSpec("plan", "规划汇报叙事", "planner"),
            StageSpec("execute", "生成证据化大纲", "tool", "scansci.ppt.outline"),
            StageSpec("deliver", "交付演示大纲", "delivery"),
        ],
    },
    "ppt_project": {
        "label": "PPT 项目",
        "artifact_type": "slide_deck_project",
        "stages": [
            StageSpec("plan", "规划演示项目", "planner"),
            StageSpec("execute", "创建 EasySlides 项目", "tool", "scansci.ppt.create_project"),
            StageSpec("deliver", "登记演示产物", "delivery"),
        ],
    },
    "pdf_to_ppt": {
        "label": "PDF 制作幻灯片",
        "artifact_type": "presentation_deck",
        "stages": [
            StageSpec("plan", "解析材料与演示主线", "planner"),
            StageSpec("execute", "生成可编辑 PPTX", "tool", "scansci.ppt.from_source"),
            StageSpec("deliver", "交付演示文稿", "delivery"),
        ],
    },
}


# Workflows that pause for approval after the planner produces a structured
# proposal. Deep research is intentionally absent: choosing that mode already
# authorizes its end-to-end public-source search, verification, and delivery.
_NEEDS_CONFIRMATION_WORKFLOWS: set[str] = {"novelty_check", "research_idea"}


@dataclass(frozen=True)
class _DirectChatRequest:
    messages: list[dict[str, Any]]
    provider_id: str
    provider_name: str
    provider_kind: str
    base_url: str
    api_key: str
    model_id: str
    api_surface: str
    responses_enabled: bool
    previous_response_id: str | None
    thinking_mode: str | None
    session: Any | None
    chat_mode: str
    thinking_level: str
    selected_skills: list[dict[str, Any]]
    notebook_id: str
    notebook_ids: list[str]
    knowledge_scope: dict[str, Any]
    manifest: RunManifest | None = None
    vision_route: dict[str, Any] = field(default_factory=dict)
    image_attachments: list[dict[str, Any]] = field(default_factory=list)
    pi_images: list[dict[str, str]] = field(default_factory=list)
    model_runtime: dict[str, Any] = field(default_factory=dict)


class _DurableModelClient:
    """Record model request lifecycle events for one durable research stage.

    Prompts and model output are intentionally not persisted in the event
    payload. Only hashes and structural metadata are retained, so the trace is
    useful for recovery/audit without becoming a credential or source-text log.
    """

    def __init__(
        self,
        client: Any,
        *,
        store: ResearchRunStore,
        run_id: str,
        stage_id: str,
        stage_key: str,
    ) -> None:
        self._client = client
        self._store = store
        self._run_id = str(run_id)
        self._stage_id = str(stage_id)
        self._stage_key = str(stage_key)

    def complete_json(self, messages: list[dict[str, Any]], *, schema_name: str, **kwargs: Any) -> Any:
        request_id = f"model-{uuid4().hex}"
        input_shape = [
            {
                "role": str(message.get("role", "")),
                "content_length": len(str(message.get("content", ""))),
            }
            for message in list(messages or [])
        ]
        input_hash = hashlib.sha256(
            json.dumps(
                {"schema_name": str(schema_name), "messages": input_shape},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        common = {
            "request_id": request_id,
            "schema_name": str(schema_name),
            "stage_key": self._stage_key,
        }
        self._store.append_event(
            self._run_id,
            event_type="model.request.started",
            summary=f"模型请求开始：{str(schema_name)[:120]}",
            actor="worker",
            stage_id=self._stage_id,
            request_id=request_id,
            payload={**common, "input_shape": input_shape},
            input_hash=input_hash,
            idempotency_key=f"{request_id}:started",
        )
        try:
            result = self._client.complete_json(messages, schema_name=schema_name, **kwargs)
        except Exception as error:  # noqa: BLE001 - lifecycle event must precede re-raise
            self._store.append_event(
                self._run_id,
                event_type="model.request.failed",
                summary=f"模型请求失败：{str(schema_name)[:120]}",
                actor="worker",
                stage_id=self._stage_id,
                request_id=request_id,
                payload={**common, "error": redact_sensitive_text(str(error))[:240]},
                input_hash=input_hash,
                error_category=classify_error(error, operation="model.request"),
                idempotency_key=f"{request_id}:failed",
            )
            raise
        output_hash = hashlib.sha256(
            json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self._store.append_event(
            self._run_id,
            event_type="model.request.completed",
            summary=f"模型请求完成：{str(schema_name)[:120]}",
            actor="worker",
            stage_id=self._stage_id,
            request_id=request_id,
            payload={
                **common,
                "output_type": type(result).__name__,
                "output_keys": sorted(str(key) for key in result) if isinstance(result, dict) else [],
            },
            input_hash=input_hash,
            output_hash=output_hash,
            idempotency_key=f"{request_id}:completed",
        )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _PiBackedChatJsonClient:
    """Run one bounded role-model request through an isolated Pi session.

    Durable workflow models are nested inside a host-owned stage or tool call.
    Reusing the parent Pi client would deadlock its single active request, so
    every logical JSON request owns a fresh sidecar/client and a transient
    session.  The explicit empty v2 lease keeps this synthesis-only adapter
    from recursively discovering domain or MCP tools.
    """

    session = None
    thinking_mode = "off"
    logical_request_limit = 1

    def __init__(
        self,
        *,
        workspace: Path,
        evidence_db: Path,
        provider_kind: str,
        provider_id: str,
        base_url: str,
        api_key: str,
        model: str,
        api_surface: str,
        responses_enabled: bool,
        role: str,
        timeout_seconds: float,
        model_runtime: dict[str, Any] | ModelRuntimeDescriptor | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.evidence_db = Path(evidence_db)
        self.provider_kind = str(provider_kind)
        self.provider_id = str(provider_id)
        self.base_url = str(base_url)
        self.api_key = str(api_key)
        self.model = str(model)
        self.api_surface = str(api_surface or "chat_completions")
        self.responses_enabled = bool(responses_enabled)
        self.role = str(role or "workflow")
        self.timeout_seconds = float(timeout_seconds)
        if isinstance(model_runtime, ModelRuntimeDescriptor):
            descriptor = model_runtime
        elif isinstance(model_runtime, dict):
            descriptor = ModelRuntimeDescriptor.from_payload(model_runtime)
        else:
            descriptor = descriptor_from_model_record(
                provider_id=self.provider_id,
                provider_kind=self.provider_kind,
                model_id=self.model,
                model_record=None,
                api_surface=self.api_surface,
            )
        self.model_runtime = descriptor.to_dict()

    def _contract(self, *, schema_name: str, output_format: str) -> dict[str, Any]:
        return {
            "schema_version": "scansci.task-contract.v2",
            "version": 2,
            "contract_id": f"nested-{self.role}-{uuid4().hex[:12]}",
            "goal": f"Produce the bounded {self.role} model result",
            "request": str(schema_name),
            "output_format": output_format,
            "constraints": [
                "Do not call tools or request interactions",
                "Do not expand the parent workflow authority",
            ],
            "required_evidence": [],
            "allowed_tools": [],
            "initial_tools": [],
            "allowed_mcp_servers": [],
            "required_tool_groups": [],
            "risk_level": "none",
            "autonomy": "none",
            "requires_plan": False,
            "allow_external_write": False,
            "initial_tool_budget": 1,
            "max_tool_budget": 1,
            "model_token_budget": 16_000,
            "max_model_token_budget": 32_000,
            "success_criteria": [f"Return one valid {output_format} result"],
        }

    def _complete(self, messages: list[dict[str, Any]], *, schema_name: str, json_only: bool) -> str:
        role_contract = (
            f"Nested ScanSci model role: {self.role}. "
            "This is a synthesis-only Pi turn with no domain or MCP authority. "
            "Do not call search_tools, ask_user, submit_plan, or any other tool. "
        )
        if json_only:
            role_contract += (
                f"Return exactly one JSON object for schema `{schema_name}`; "
                "do not use Markdown fences or surrounding prose."
            )
        else:
            role_contract += "Return only the requested text."
        bounded_messages = [
            {"role": "system", "content": role_contract},
            *[
                {"role": str(message.get("role", "user")), "content": str(message.get("content", ""))}
                for message in list(messages or [])
            ],
        ]
        client = PiAgentClient(workspace=self.workspace, evidence_db=self.evidence_db)
        fragments: list[str] = []
        try:
            for event in client.stream_chat(
                provider_kind=self.provider_kind,
                base_url=self.base_url,
                api_key=self.api_key,
                model_id=self.model,
                api_surface=self.api_surface,
                responses_enabled=self.responses_enabled,
                messages=bounded_messages,
                thinking_level="off",
                task_mode="model-json",
                task_contract=self._contract(
                    schema_name=schema_name,
                    output_format="strict JSON object" if json_only else "text",
                ),
                model_runtime=self.model_runtime,
                timeout_seconds=self.timeout_seconds,
                # Transient and independent by construction: never wait on or
                # mutate a parent durable session that may currently own a
                # Python bridge tool call.
                session_id=None,
            ):
                event_type = str(event.get("type", ""))
                if event_type == "delta":
                    fragments.append(str(event.get("content", "")))
                elif event_type in {"tool.completed", "tool.failed", "interaction"}:
                    raise RuntimeError("Nested Pi role model attempted to expand its empty authority lease")
                elif event_type == "cancelled":
                    raise RuntimeError("Nested Pi role model was cancelled")
        finally:
            client.close()
        text = "".join(fragments).strip()
        if not text:
            raise RuntimeError("Nested Pi role model returned an empty response")
        return text

    def complete_json(self, messages: list[dict[str, Any]], *, schema_name: str, **_kwargs: Any) -> Any:
        text = self._complete(messages, schema_name=schema_name, json_only=True)
        return _parse_and_validate_structured_content(text, schema_name=schema_name)

    def complete_text(self, messages: list[dict[str, Any]], *, max_tokens: int = 700) -> str:
        del max_tokens
        return self._complete(messages, schema_name="plain_text", json_only=False)


def _apply_direct_chat_profile(
    chat_request: _DirectChatRequest,
    task_profile: dict[str, Any] | None,
) -> _DirectChatRequest:
    """Use a compact prompt when the host has ruled out tools and workflows."""

    profile = dict(task_profile or {})
    if str(profile.get("route", "")) != "direct_chat":
        return chat_request
    if chat_request.selected_skills or chat_request.notebook_id or chat_request.notebook_ids:
        return chat_request
    # Multimodal direct turns necessarily carry a list of content blocks on
    # the final user message.  The old guard treated that list as a reason to
    # keep the long scientific-agent system prompt, which made vision models
    # reject perfectly valid image questions as "not a scientific task".
    # Keep the guard for other structured messages (tool payloads or an
    # unexpected assistant block), but allow the standard user image blocks.
    for index, item in enumerate(chat_request.messages):
        content = item.get("content")
        if isinstance(content, str):
            continue
        if (
            index == len(chat_request.messages) - 1
            and str(item.get("role", "")).strip().lower() == "user"
            and isinstance(content, list)
            and all(isinstance(part, dict) for part in content)
        ):
            continue
        return chat_request
    system_indexes = [
        index
        for index, item in enumerate(chat_request.messages)
        if str(item.get("role", "")).strip().lower() == "system"
    ]
    # A second system message carries attachment or other turn-local context
    # and must not be compressed away.
    if system_indexes != [0]:
        return chat_request

    compact_system = (
        "You are ScanSci, a scientific assistant. Reply directly in the user's language and "
        "follow the requested length and format. For greetings and ordinary questions, do not "
        "narrate planning, processing, tools, or capabilities. Never claim that a tool or local "
        "file action occurred on this direct-chat route. Preserve scientific uncertainty; do not "
        "turn correlation into causation. When explaining a frequentist confidence interval, "
        "describe long-run procedure coverage rather than a probability assigned to the fixed "
        "parameter. Do not add tutorials or alternatives the user did not ask for."
    )
    if chat_request.image_attachments:
        compact_system += (
            " For image questions, identify the image type first, then describe the visible "
            "interface or objects and transcribe the most important readable text. Do not answer "
            "with only one isolated phrase unless the user asks for a single label, and never "
            "guess details that are not visible."
        )
    messages = [dict(item) for item in chat_request.messages]
    messages[0] = {"role": "system", "content": compact_system}
    return replace(chat_request, messages=messages)


class ResearchAgentRuntime:
    # A verified-answer turn has exactly one required action
    # (``build_verified_answer``).  Do not let a gateway's general-purpose
    # agent timeout hold the interactive evidence surface hostage when that
    # action cannot be scheduled (for example during transient rate limiting).
    # The deterministic, citation-checked workflow below is the safe fallback.
    _MANAGED_VERIFIED_ANSWER_TIMEOUT_SECONDS = 45.0
    _EXTERNAL_VERIFIED_ANSWER_TIMEOUT_SECONDS = 90.0

    """Run ScanSci tools behind a persistent, resumable research contract."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        evidence_db: str | Path,
        runtime_facts_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.evidence_db = Path(evidence_db).resolve()
        self._runtime_facts_provider = runtime_facts_provider
        self.store = ResearchRunStore(self.workspace)
        self.store.recover_interrupted_runs()
        self._backfill_task_contracts()
        self._threads: dict[str, threading.Thread] = {}
        self._thread_lock = threading.Lock()
        self._model_lock = threading.Lock()
        # If True, workflows in _NEEDS_CONFIRMATION_WORKFLOWS pause after the
        # planner stage for user review (ZCode EnterPlanMode pattern). Tests
        # monkeypatch this to False so _execute_run completes in one pass.
        self._pause_for_plan_review = True
        self._stage_retry_counts: dict[str, int] = {}
        self._model_event_context = threading.local()
        self._active_pi_lock = threading.Lock()
        self._active_pi_clients: dict[str, PiAgentClient] = {}
        self._managed_writing_clients: dict[tuple[str, str, str], Any] = {}
        self._local_evidence_models: dict[str, LocalEvidenceStack] = {}
        self._academic_discovery_models: dict[str, LocalEvidenceStack] = {}
        self._task_evidence_models: dict[str, LocalEvidenceStack] = {}
        self._zotero_tag_sync_cache: set[str] = set()
        self._background_warm_lock = threading.Lock()
        self._background_warming: set[str] = set()
        # Neural embedding/reranker weights can take minutes to initialise on
        # a first run. Keep that work outside the request thread and expose a
        # small status snapshot so the desktop can show progress truthfully.
        self._local_evidence_prepare_lock = threading.Lock()
        self._local_evidence_preparations: dict[str, dict[str, Any]] = {}

    def _maybe_warm_cache_in_background(
        self,
        evidence_db: Path,
        stack: LocalEvidenceStack,
    ) -> None:
        """Start a background thread to build the neural vector cache.

        Only one thread per evidence database runs at a time.  The first
        user query to a library with a cold cache will use the fast
        (hashing) path while this thread warms the neural index; subsequent
        queries switch to the dense route automatically.
        """

        key = str(evidence_db.resolve())
        with self._background_warm_lock:
            if key in self._background_warming:
                return
            self._background_warming.add(key)
        provider = stack.embedding_provider
        if isinstance(provider, HashingEmbeddingProvider):
            with self._background_warm_lock:
                self._background_warming.discard(key)
            return

        def _warm() -> None:
            try:
                from .vector_index import prewarm_embedding_cache
                from .retrieval import _load_evidence_spans

                rows = _load_evidence_spans(evidence_db)
                if not rows:
                    return
                prewarm_embedding_cache(
                    evidence_db,
                    rows,
                    provider=provider,
                    cache_batch_size=DEFAULT_NEURAL_CACHE_BATCH_SIZE,
                )
            except Exception:
                pass
            finally:
                with self._background_warm_lock:
                    self._background_warming.discard(key)

        threading.Thread(target=_warm, daemon=True, name=f"scansci-warm-{evidence_db.name}").start()

    @staticmethod
    def _local_evidence_preparation_key(evidence_db: Path, quality_profile: str) -> str:
        profile = "precision" if str(quality_profile).lower() == "precision" else "balanced"
        return f"{evidence_db.resolve()}|{profile}"

    @staticmethod
    def _local_evidence_model_snapshot(stack: Any) -> dict[str, Any]:
        metadata = dict(getattr(stack, "metadata", {}) or {})
        keys = (
            "embedding",
            "reranker",
            "requested_embedding_model",
            "requested_reranker_model",
            "effective_quality_profile",
            "embedding_device",
            "reranker_device",
            "local_neural_embedding",
            "local_neural_reranker",
            "remote_embedding_active",
            "remote_reranker_active",
            "qwen_embedding_active",
            "qwen_reranker_active",
            "fallback",
        )
        return {key: metadata[key] for key in keys if key in metadata}

    def _local_evidence_status_snapshot(
        self,
        evidence_db: Path,
        *,
        quality_profile: str,
        notebook_id: str = "",
    ) -> dict[str, Any]:
        key = self._local_evidence_preparation_key(evidence_db, quality_profile)
        with self._local_evidence_prepare_lock:
            current = dict(self._local_evidence_preparations.get(key, {}) or {})
        if not current:
            # A query may have loaded the stack before the UI asked for its
            # status. Reflect that immediately instead of showing "idle".
            with self._model_lock:
                existing = next(
                    (
                        stack
                        for stack in self._local_evidence_models.values()
                        if any(
                            bool(dict(getattr(stack, "metadata", {}) or {}).get(key))
                            for key in (
                                "local_neural_embedding",
                                "remote_embedding_active",
                                "remote_reranker_active",
                            )
                        )
                    ),
                    None,
                )
            if existing is not None:
                current = {
                    "state": "ready",
                    "phase": "ready",
                    "progress": 1.0,
                    "message": (
                        "远程嵌入与重排已就绪，无需预热本地 GPU"
                        if dict(getattr(existing, "metadata", {}) or {}).get("remote_embedding_active")
                        and dict(getattr(existing, "metadata", {}) or {}).get("remote_reranker_active")
                        else "本地 AI 模型已就绪"
                    ),
                    "error": "",
                    "model": self._local_evidence_model_snapshot(existing),
                    "started_at": "",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            else:
                current = {
                    "state": "idle",
                    "phase": "idle",
                    "progress": 0.0,
                    "message": "尚未准备本地 AI 模型",
                    "error": "",
                    "model": {},
                    "started_at": "",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
        current["notebook_id"] = str(notebook_id or "")
        current["quality_profile"] = "precision" if str(quality_profile).lower() == "precision" else "balanced"
        current["cache_warming"] = False
        current["cache_ready"] = False
        if evidence_db.is_file():
            try:
                identity = default_vector_cache_identity()
                cache = vector_cache_status(
                    evidence_db,
                    provider=str(identity["provider"]),
                    dimensions=int(identity["dimensions"]),
                )
                current["cache_ready"] = bool(cache.get("ready"))
            except Exception:
                # Status reporting must never turn an otherwise usable library
                # into an error just because an old cache is malformed.
                current["cache_ready"] = False
            with self._background_warm_lock:
                current["cache_warming"] = str(evidence_db.resolve()) in self._background_warming
        return current

    def local_evidence_status(
        self,
        evidence_db: Path | None = None,
        *,
        notebook_id: str = "",
        quality_profile: str = "precision",
    ) -> dict[str, Any]:
        """Return non-blocking local embedding/reranker preparation status."""

        if notebook_id and evidence_db is None:
            notebook = self._notebook(str(notebook_id))
            evidence_db = self._evidence_db_for_notebook(notebook)
        target = Path(evidence_db or self.evidence_db).resolve()
        return self._local_evidence_status_snapshot(
            target,
            quality_profile=quality_profile,
            notebook_id=notebook_id,
        )

    def _remote_evidence_models_configured(self) -> bool:
        """Return whether both evidence roles are configured for SiliconFlow.

        This is deliberately a credential-presence check only.  It does not
        call either remote endpoint, so selecting API-backed embedding and
        reranking never triggers a local model warm-up or a network probe.
        """

        try:
            settings = load_settings(self.workspace)
            providers = list(settings.get("providers", []) or [])
            provider = next(
                (item for item in providers if str(item.get("id", "")) == "siliconflow"),
                None,
            )
            if provider is None or not provider.get("enabled", True):
                return False
            models = {
                str(item.get("id", "")): {
                    str(capability).casefold()
                    for capability in list(item.get("capabilities", []) or [])
                }
                for item in list(provider.get("models", []) or [])
                if isinstance(item, dict)
            }
            roles = dict(settings.get("model_roles", {}) or {})
            for role, capability in (("embedding", "embedding"), ("reranking", "reranking")):
                reference = str(roles.get(role, "") or "").strip()
                provider_id, separator, model_id = (
                    reference.removeprefix("provider:").partition(":")
                    if reference.startswith("provider:")
                    else ("", "", "")
                )
                if (
                    not separator
                    or provider_id != "siliconflow"
                    or capability not in models.get(model_id.strip(), set())
                    or not get_provider_api_key(self.workspace, provider_id)
                ):
                    return False
            return True
        except Exception:
            return False

    def prepare_local_evidence(
        self,
        evidence_db: Path | None = None,
        *,
        notebook_id: str = "",
        quality_profile: str = "precision",
        force: bool = False,
    ) -> dict[str, Any]:
        """Schedule local model preparation without blocking the caller.

        The fast hashing/lexical stack remains available while weights load.
        A later request automatically uses the neural stack once the worker
        has completed, so the desktop never needs to spin or retry blindly.
        """

        if notebook_id and evidence_db is None:
            notebook = self._notebook(str(notebook_id))
            evidence_db = self._evidence_db_for_notebook(notebook)
        target = Path(evidence_db or self.evidence_db).resolve()
        profile = "precision" if str(quality_profile).lower() == "precision" else "balanced"
        key = self._local_evidence_preparation_key(target, profile)
        if not target.is_file():
            with self._local_evidence_prepare_lock:
                self._local_evidence_preparations[key] = {
                    "state": "idle",
                    "phase": "idle",
                    "progress": 0.0,
                    "message": "暂无可检索资料，暂不加载本地 AI 模型",
                    "error": "",
                    "model": {},
                    "started_at": "",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            return self._local_evidence_status_snapshot(
                target,
                quality_profile=profile,
                notebook_id=notebook_id,
            )
        with self._local_evidence_prepare_lock:
            current = dict(self._local_evidence_preparations.get(key, {}) or {})
        if current.get("state") == "preparing":
            return self._local_evidence_status_snapshot(
                target,
                quality_profile=profile,
                notebook_id=notebook_id,
            )
        if current.get("state") in {"ready", "fallback"} and not force:
            return self._local_evidence_status_snapshot(
                target,
                quality_profile=profile,
                notebook_id=notebook_id,
            )
        if self._remote_evidence_models_configured():
            try:
                stack = self._local_evidence_stack(
                    target,
                    quality_profile=profile,
                    _allow_neural_build=True,
                )
                metadata = dict(getattr(stack, "metadata", {}) or {})
                remote_ready = bool(
                    metadata.get("remote_embedding_active")
                    and metadata.get("remote_reranker_active")
                )
                if remote_ready:
                    now = datetime.now().isoformat(timespec="seconds")
                    with self._local_evidence_prepare_lock:
                        self._local_evidence_preparations[key] = {
                            "state": "ready",
                            "phase": "remote",
                            "progress": 1.0,
                            "message": "远程嵌入与重排已就绪，无需预热本地 GPU",
                            "error": "",
                            "model": self._local_evidence_model_snapshot(stack),
                            "started_at": now,
                            "updated_at": now,
                        }
                    return self._local_evidence_status_snapshot(
                        target,
                        quality_profile=profile,
                        notebook_id=notebook_id,
                    )
            except Exception:
                # Fall through to the existing background local preparation
                # path so a failed remote adapter cannot block retrieval.
                pass
        with self._local_evidence_prepare_lock:
            now = datetime.now().isoformat(timespec="seconds")
            self._local_evidence_preparations[key] = {
                "state": "preparing",
                "phase": "loading_models",
                "progress": 0.05,
                "message": "正在加载本地 AI 模型；首次加载可能需要几分钟",
                "error": "",
                "model": {},
                "started_at": now,
                "updated_at": now,
            }

        def _prepare() -> None:
            try:
                stack = self._local_evidence_stack(
                    target,
                    quality_profile=profile,
                    _allow_neural_build=True,
                )
                metadata = dict(getattr(stack, "metadata", {}) or {})
                neural = bool(
                    metadata.get("local_neural_embedding")
                    or metadata.get("remote_embedding_active")
                    or metadata.get("remote_reranker_active")
                )
                intentionally_fast = str(metadata.get("effective_quality_profile", "")) == "fast"
                fallback = (bool(metadata.get("fallback")) or not neural) and not intentionally_fast
                reasons = [
                    str(item).strip()
                    for item in list(metadata.get("fallback_reasons", []) or [])
                    if str(item).strip()
                ]
                if fallback and not reasons:
                    reasons.append("local_neural_stack_unavailable")
                state = "fallback" if fallback else "ready"
                message = (
                    "资料量较少，已使用快速检索"
                    if intentionally_fast
                    else "远程嵌入与重排已就绪，无需预热本地 GPU"
                    if metadata.get("remote_embedding_active") and metadata.get("remote_reranker_active")
                    else "本地 AI 模型已就绪，正在后台优化索引"
                    if state == "ready"
                    else "本地 AI 模型暂不可用，已保留基础检索；可稍后重试"
                )
                with self._local_evidence_prepare_lock:
                    self._local_evidence_preparations[key] = {
                        "state": state,
                        "phase": "ready" if state == "ready" else "fallback",
                        "progress": 1.0,
                        "message": message,
                        "error": "; ".join(reasons)[:800] if fallback else "",
                        "model": self._local_evidence_model_snapshot(stack),
                        "started_at": self._local_evidence_preparations.get(key, {}).get("started_at", ""),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
            except Exception as error:  # optional local-model boundary
                with self._local_evidence_prepare_lock:
                    self._local_evidence_preparations[key] = {
                        "state": "error",
                        "phase": "error",
                        "progress": 0.0,
                        "message": "本地 AI 模型准备失败，已保留基础检索；可重试",
                        "error": f"{type(error).__name__}: {str(error)[:700]}",
                        "model": {},
                        "started_at": self._local_evidence_preparations.get(key, {}).get("started_at", ""),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }

        threading.Thread(
            target=_prepare,
            daemon=True,
            name=f"scansci-local-ai-{target.stem or 'evidence'}",
        ).start()
        return self._local_evidence_status_snapshot(
            target,
            quality_profile=profile,
            notebook_id=notebook_id,
        )

    def _fast_preparing_evidence_stack(self, selector_key: str, requested_profile: str) -> LocalEvidenceStack:
        # Do not take ``_model_lock`` here: the background neural build holds
        # it while PyTorch initialises. A fresh lightweight stack is cheap and
        # keeps the foreground query genuinely non-blocking.
        del selector_key
        return LocalEvidenceStack(
            embedding_provider=HashingEmbeddingProvider(),
            reranker=LexicalReranker(),
            metadata={
                "embedding": "local-hash-v1",
                "reranker": "local-lexical-v1",
                "local_neural_embedding": False,
                "local_neural_reranker": False,
                "qwen_embedding_active": False,
                "qwen_reranker_active": False,
                "embedding_device": "cpu",
                "reranker_device": "cpu",
                "fallback": True,
                "fallback_reasons": ["local_neural_models_preparing"],
                "selection_reason": "neural-model-preparing",
                "requested_quality_profile": requested_profile,
                "effective_quality_profile": "fast-preparing",
                "precision_reranker_active": False,
                "preparing": True,
            },
        )

    def _wrap_model_client(self, client: Any) -> Any:
        context = getattr(self._model_event_context, "value", None)
        if not isinstance(context, dict) or not context.get("run_id"):
            return client
        return _DurableModelClient(
            client,
            store=self.store,
            run_id=str(context["run_id"]),
            stage_id=str(context.get("stage_id", "")),
            stage_key=str(context.get("stage_key", "")),
        )

    @classmethod
    def verified_answer_pi_timeout_seconds(cls, *, managed: bool) -> float:
        """Bound the one-tool Pi compatibility probe before safe fallback."""

        return (
            cls._MANAGED_VERIFIED_ANSWER_TIMEOUT_SECONDS
            if managed
            else cls._EXTERNAL_VERIFIED_ANSWER_TIMEOUT_SECONDS
        )

    def _backfill_task_contracts(self) -> None:
        """Give pre-contract history a conservative durable policy envelope."""

        for summary in self.store.list_runs(limit=200, archived=None):
            if dict(summary.get("task_contract", {}) or {}):
                continue
            run = self.store.get_run(str(summary["run_id"]))
            workflow_type = str(run.get("workflow_type", ""))
            payload = dict(run.get("input", {}) or {})
            task_mode = self._workflow_task_mode(workflow_type, payload)
            self.store.set_task_contract(
                str(run["run_id"]),
                self._compile_contract(
                    task_mode=task_mode,
                    user_text=self._task_request_text(payload) or str(run.get("title", "")),
                    workflow_type=workflow_type,
                    mcp_scope=(
                        "selected-library"
                        if str(run.get("notebook_id", "")).strip()
                        else "default"
                    ),
                ),
            )

    def workflow_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "id": workflow_id,
                "label": spec["label"],
                "artifact_type": spec["artifact_type"],
                "stages": [stage.title for stage in spec["stages"]],
            }
            for workflow_id, spec in _WORKFLOWS.items()
        ]

    @staticmethod
    def _workflow_evidence_policy(workflow_type: str, payload: dict[str, Any]) -> str:
        if workflow_type == "ask":
            task_mode = str(payload.get("task_mode", "evidence") or "evidence").strip().lower()
            return "strict" if task_mode in {"auto", "evidence", "knowledge", "research", "verified-answer"} else "off"
        if workflow_type in {
            "literature_review",
            "deep_research",
            "novelty_check",
            "citation_analysis",
            "doi_verification",
        }:
            return "strict"
        if workflow_type in {
            "academic_search",
            "journal_search",
            "paper_atlas",
            "paper_download",
            "paper_download_batch",
            "paper_search_download",
            "research_idea",
            "ppt_outline",
            "ppt_project",
            "pdf_to_ppt",
        }:
            return "assist"
        return "off"

    @staticmethod
    def _workflow_task_mode(workflow_type: str, payload: dict[str, Any]) -> str:
        """Map a durable product workflow to its host-owned capability lease."""

        if workflow_type == "ask":
            requested = str(payload.get("task_mode", "verified-answer") or "verified-answer").strip().lower()
            return "verified-answer" if requested in {"auto", "evidence"} else requested
        if workflow_type in {"paper_download", "paper_download_batch", "paper_search_download"}:
            return "research"
        if workflow_type in {"ppt_outline", "ppt_project", "pdf_to_ppt"}:
            return "slides"
        if workflow_type == "deep_research":
            # Deep research is a standalone, web-backed task.  It may read
            # publicly traceable scholarly sources, but it must not silently
            # turn the user's selected knowledge library into its corpus.
            return "web"
        if workflow_type == "literature_review":
            # Evidence reviews are the long-form form of local evidence Q&A.
            # A writing model must not broaden their corpus into web research.
            return "knowledge"
        if workflow_type in {
            "novelty_check",
            "citation_analysis",
            "doi_verification",
            "research_idea",
        }:
            return "knowledge+web"
        if workflow_type in {
            "academic_search",
            "journal_search",
            "paper_atlas",
        }:
            return "web"
        return "general"

    @staticmethod
    def _task_request_text(payload: dict[str, Any]) -> str:
        for key in ("question", "content", "query", "topic", "title", "instruction"):
            value = str(payload.get(key, "") or "").strip()
            if value:
                return value
        values = payload.get("identifiers")
        if isinstance(values, list) and values:
            return "Download and process " + ", ".join(str(value) for value in values[:20])
        return ""

    def _compile_contract(
        self,
        *,
        task_mode: str,
        user_text: str,
        workflow_type: str = "",
        mcp_scope: str = "default",
    ) -> dict[str, Any]:
        user_text = _intent_classification_text(user_text)
        required_groups = _required_pi_tool_groups(task_mode, user_text)
        preliminary = compile_task_contract(
            task_mode=task_mode,
            user_text=user_text,
            workflow_type=workflow_type,
            required_tool_groups=required_groups,
        )
        settings = load_settings(self.workspace)
        catalog = capability_catalog(
            workspace=self.workspace,
            evidence_db=self.evidence_db,
            mcp_servers=list(settings.get("mcp_servers", []) or []),
            plugins=list(settings.get("plugins", []) or []),
        )
        profile = dict(preliminary.get("task_profile", {}) or {})
        normalized_mcp_scope = str(mcp_scope or "default").strip().lower()
        if normalized_mcp_scope == "default" and self._local_only_intent(user_text):
            normalized_mcp_scope = "local-only"
        social_turn = "greeting" in set(profile.get("reasons", []) or [])
        allow_unrelated_mcp = (
            not social_turn
            and normalized_mcp_scope not in {"disabled", "local-only", "selected-library"}
        )

        def read_capable_mcp(item: dict[str, Any]) -> bool:
            # A read-only connection is eligible even before its deferred
            # catalog is loaded.  A write-enabled server must carry at least
            # one host-owned read classification; remote annotations cannot
            # create this lease.
            if not bool(item.get("allow_write", False)):
                return True
            effects = [
                str(effect).strip().lower()
                for effect in dict(item.get("tool_effects", {}) or {}).values()
            ]
            policies = [
                str(policy.get("effect", "")).strip().lower()
                for policy in list(item.get("tool_policies", []) or [])
                if isinstance(policy, dict)
            ]
            return any(effect in {"read", "read_only", "readonly"} for effect in [*effects, *policies])

        requested_mcp_servers = [
            str(item.get("id") or item.get("name") or "").strip()
            for item in list(settings.get("mcp_servers", []) or [])
            if (
                allow_unrelated_mcp
                and isinstance(item, dict)
                and item.get("enabled")
                and not item.get("uninstalled")
                and read_capable_mcp(item)
            )
        ]
        lease = compile_capability_lease(
            catalog,
            preliminary.get("allowed_tools", []),
            requested_mcp_server_ids=requested_mcp_servers,
        )
        return compile_task_contract(
            task_mode=task_mode,
            user_text=user_text,
            workflow_type=workflow_type,
            required_tool_groups=required_groups,
            available_tool_ids=lease["allowed_tools"],
            allowed_mcp_servers=lease["allowed_mcp_servers"],
            capability_lease=lease,
        )

    def _contract_for_active_run(
        self,
        active_run_id: str,
        *,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        """Use the durable host-issued child lease for its actual Pi run."""

        if not str(active_run_id or "").strip():
            return json.loads(json.dumps(dict(fallback or {})))
        try:
            run = self.store.get_run(str(active_run_id))
        except (KeyError, FileNotFoundError):
            # Direct-chat AG-UI ids are host correlation ids, not durable
            # ResearchStore runs. They retain the freshly compiled contract.
            return json.loads(json.dumps(dict(fallback or {})))
        if str(dict(run.get("metadata", {}) or {}).get("runtime", "")) != "scansci-scientific-subagent.v1":
            return json.loads(json.dumps(dict(fallback or {})))
        persisted = dict(run.get("task_contract", {}) or {})
        if not persisted:
            raise PermissionError("Scientific child run is missing its durable capability contract")
        return json.loads(json.dumps(persisted))

    def preview_academic_search_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a bounded public-source search plan before a run is created.

        This deliberately performs no remote search and does not open a local
        notebook.  The UI can therefore let a researcher inspect and amend the
        plan before any provider receives a query.
        """

        raw_query = str(payload.get("raw_query") or payload.get("query") or "").strip()
        if not raw_query:
            raise ValueError("query is required")
        explicit_providers = (
            self._academic_provider_names(payload)
            if payload.get("providers") is not None
            else None
        )
        try:
            chat_client = self._writing_chat_client()
        except Exception:
            chat_client = None
        plan = plan_academic_search(
            raw_query,
            explicit_providers=explicit_providers,
            # The model may only propose bounded aliases.  The deterministic
            # host plan remains the fallback and the user sees every query
            # before any provider call is made.
            chat_client=chat_client,
        )
        if isinstance(payload.get("search_plan"), dict):
            plan = review_academic_search_plan(plan, dict(payload["search_plan"]))
        plan["query"] = str(plan["topic"])
        plan["plan_mode"] = "confirm_before_search"
        plan["scope_notice"] = "仅查询公开学术来源；不会读取、上传或写入知识库。"
        return plan

    def preview_task_route(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Classify a free-form request without calling a model or provider.

        The web client uses this as a convenience preview.  ``start`` repeats
        the same decision for ``workflow_type=auto`` so a stale or modified
        client cannot choose a different durable workflow.
        """

        request = str(payload.get("question") or payload.get("input") or "").strip()
        notebook_id = str(payload.get("notebook_id", "")).strip()
        has_knowledge = False
        if notebook_id:
            try:
                notebook = self._notebook(notebook_id)
                has_knowledge = bool(list(notebook.get("sources", []) or []))
            except (FileNotFoundError, sqlite3.Error):
                has_knowledge = False
        selection = resolve_skill_selection(
            payload,
            [{"role": "user", "content": request}],
        )
        decision = route_freeform_task(
            request,
            has_knowledge=has_knowledge,
            skill_ids=selection.explicit_ids,
        ).to_dict()
        decision["host_owned"] = True
        decision["has_selected_knowledge"] = has_knowledge
        decision["skill_selection"] = selection.to_dict()
        return decision

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested_workflow_type = str(payload.get("workflow_type", "ask")).strip()
        routing: dict[str, Any] = {}
        if requested_workflow_type == "auto":
            routing = self.preview_task_route(payload)
            if not bool(routing.get("durable")):
                raise ValueError("这个请求适合直接在通用输入中回答，无需建立后台任务")
            workflow_type = str(routing.get("workflow_type", "")).strip()
            payload = {
                **dict(payload),
                **dict(routing.get("input", {}) or {}),
                "workflow_type": workflow_type,
            }
        else:
            workflow_type = requested_workflow_type
        spec = _WORKFLOWS.get(workflow_type)
        if spec is None:
            raise ValueError(f"Unsupported research workflow: {workflow_type}")
        if workflow_type in {"pdf_to_ppt", "academic_search", "deep_research"}:
            notebook = {"notebook_id": ""}
        elif workflow_type in {"paper_download", "paper_download_batch", "paper_search_download"}:
            # Downloads are standalone only when no library is selected.  The
            # desktop UI still sends its active notebook id, so downloaded
            # PDFs can be indexed immediately and summarized in the same task.
            notebook = (
                self._requested_notebook(payload)
                if str(payload.get("notebook_id", "")).strip()
                else {"notebook_id": ""}
            )
        else:
            notebook = self._requested_notebook(payload)
        normalized = self._normalize_input(workflow_type, payload)
        if workflow_type == "academic_search":
            # Reject hostile or malformed public-search input before a task is
            # persisted.  The validation is local-only and never calls an
            # academic provider or opens a knowledge library.
            self.preview_academic_search_plan(normalized)
            self._optional_year(normalized.get("year_from"))
        thinking_level = normalize_thinking_level(normalized.get("thinking_level"))
        normalized["thinking_level"] = thinking_level
        if normalized.get("images"):
            if workflow_type != "ask":
                raise ValueError("图片提问目前仅支持“通用”模式")
            normalized["images"] = persist_image_attachments(self.workspace, normalized["images"])
        if workflow_type == "pdf_to_ppt":
            normalized["source_files"] = persist_slide_sources(self.workspace, normalized.get("source_files"))
        settings = load_settings(self.workspace)
        active = dict(settings.get("active_model", {}) or {})
        if workflow_type in {"literature_review", "deep_research", "novelty_check", "research_idea"}:
            provider_id, model_id = self._role_identity(settings, "writing")
        elif workflow_type == "pdf_to_ppt":
            provider_id, model_id = self._role_identity(settings, "slides")
        else:
            provider_id, model_id = str(active.get("provider_id", "")), str(active.get("model_id", ""))
        contract_mode = self._workflow_task_mode(workflow_type, normalized)
        task_contract = self._compile_contract(
            task_mode=contract_mode,
            user_text=self._task_request_text(normalized),
            workflow_type=workflow_type,
            mcp_scope="selected-library" if str(notebook.get("notebook_id", "")).strip() else "default",
        )
        knowledge_metadata = self._knowledge_run_metadata(notebook)
        routing_metadata = (
            {
                "origin": "freeform",
                "requested_workflow": "auto",
                "workflow_type": workflow_type,
                "reason": str(routing.get("reason", "")),
                "scope": str(routing.get("scope", "")),
                "host_owned": True,
            }
            if routing
            else {
                "origin": str(payload.get("task_origin", "shortcut") or "shortcut"),
                "requested_workflow": workflow_type,
                "workflow_type": workflow_type,
                "host_owned": False,
            }
        )
        run = self.store.create_run(
            notebook_id=str(notebook["notebook_id"]),
            workflow_type=workflow_type,
            title=self._run_title(workflow_type, normalized),
            input_payload=normalized,
            stages=spec["stages"],
            model_provider_id=provider_id,
            model_id=model_id,
            metadata={
                "artifact_contract": spec["artifact_type"],
                "runtime": "scansci-research-agent.v1",
                "workflow_orchestration": "durable-stage-machine",
                "interactive_agent": "pi-agent-sdk",
                "model_tool_loop": "pi-agent-sdk" if workflow_type == "ask" else "product-owned-stages",
                "thinking_level": thinking_level,
                "evidence_budget": evidence_budget_for_thinking(thinking_level),
                "evidence_policy": self._workflow_evidence_policy(workflow_type, normalized),
                "routing": routing_metadata,
                **knowledge_metadata,
            },
            task_contract=task_contract,
            idempotency_key=str(payload.get("idempotency_key") or payload.get("request_id") or "").strip(),
        )
        self._submit(str(run["run_id"]))
        return self.store.get_run(str(run["run_id"]))

    def start_evidence_index(
        self,
        notebook_id: str,
        *,
        automatic: bool = False,
    ) -> dict[str, Any] | None:
        """Start a neural-cache build, reusing compatible vectors first.

        Automatic callers must not turn a partially reusable library into a
        surprise GPU workload.  When ``automatic`` is true, an existing
        serving generation or any matching cache rows are enough to keep the
        current lexical/stale retrieval path available; the missing vectors
        are left for an explicit user-triggered retry.  Explicit callers keep
        the previous behaviour and may complete the incremental build.
        """

        notebook = self._notebook(str(notebook_id))
        evidence_db = self._evidence_db_for_notebook(notebook)
        if not evidence_db.is_file():
            return None
        vector_identity = default_vector_cache_identity()
        cache_status = vector_cache_status(
            evidence_db,
            provider=str(vector_identity["provider"]),
            dimensions=int(vector_identity["dimensions"]),
        )
        migration = dict(
            dict(notebook.get("metadata", {}) or {}).get("vector_cache_migration", {}) or {}
        )
        migrated_vectors = max(0, int(migration.get("migrated_vectors", 0) or 0))
        migration_sources = [
            str(value)
            for value in list(migration.get("sources", []) or [])
            if str(value).strip()
        ]
        if bool(cache_status.get("available")) and int(cache_status.get("total", 0) or 0) <= 0:
            return None
        if bool(cache_status.get("ready")):
            return None
        existing = next(
            (
                run
                for run in self.store.list_runs(notebook_id=str(notebook_id), limit=50)
                if str(run.get("workflow_type", "")) == "evidence_index"
                and str(run.get("status", "")) in {"queued", "running", "paused"}
            ),
            None,
        )
        if existing is not None:
            if str(existing.get("status", "")) == "paused":
                # A paused legacy build may have been interrupted by an app
                # restart. Automatic startup must not silently resume it;
                # the user can retry explicitly when the machine is cool
                # and the timing is acceptable.
                if automatic:
                    return existing
                return self.resume(str(existing["run_id"]))
            return existing
        reusable_vectors = max(
            0,
            int(cache_status.get("reusable_vectors", 0) or 0),
            int(cache_status.get("serving_vectors", 0) or 0),
            int(cache_status.get("completed", 0) or 0),
            migrated_vectors,
        )
        if automatic and reusable_vectors > 0:
            # Keep the previous generation/cache as the low-temperature path.
            # An explicit POST/retry can still call this method with the
            # default ``automatic=False`` to finish only the missing rows.
            return None
        return self.start(
            {
                "workflow_type": "evidence_index",
                "notebook_id": str(notebook_id),
                "notebook_title": str(notebook.get("title") or "当前知识库"),
                "evidence_db": str(evidence_db),
                "evidence_db_size": evidence_db.stat().st_size,
                "vector_provider": str(vector_identity["provider"]),
                "vector_dimensions": int(vector_identity["dimensions"]),
                "cached_vectors": int(cache_status.get("completed", 0) or 0),
                "total_vectors": int(cache_status.get("total", 0) or 0),
                "cache_migration": bool(cache_status.get("migration_required")),
                "migrated_vectors": migrated_vectors,
                "migration_sources": migration_sources,
            }
        )

    def evidence_index_status(self, notebook_id: str) -> dict[str, Any]:
        """Return the user-facing readiness of one notebook's retrieval index."""

        notebook = self._notebook(str(notebook_id))
        evidence_db = self._evidence_db_for_notebook(notebook)
        vector_identity = default_vector_cache_identity()
        status = vector_cache_status(
            evidence_db,
            provider=str(vector_identity["provider"]),
            dimensions=int(vector_identity["dimensions"]),
        )
        latest_run = next(
            (
                run
                for run in self.store.list_runs(notebook_id=str(notebook_id), limit=50)
                if str(run.get("workflow_type", "")) == "evidence_index"
            ),
            None,
        )
        status["notebook_id"] = str(notebook_id)
        status["run"] = latest_run
        return status

    def resume(self, run_id: str) -> dict[str, Any]:
        run = self.store.prepare_resume(run_id)
        self._submit(run_id)
        return run

    def task_registry(self) -> dict[str, Any]:
        """Expose the durable multi-session control plane in one snapshot."""

        snapshot = self.store.task_registry()
        with self._thread_lock:
            worker_ids = {
                run_id for run_id, thread in self._threads.items() if thread.is_alive()
            }
        with self._active_pi_lock:
            pi_ids = set(self._active_pi_clients)
        for task in list(snapshot.get("tasks", []) or []):
            run_id = str(task.get("run_id", ""))
            task["worker_active"] = run_id in worker_ids
            task["pi_active"] = run_id in pi_ids
        snapshot["active_worker_ids"] = sorted(worker_ids)
        snapshot["active_pi_ids"] = sorted(pi_ids)
        return snapshot

    @staticmethod
    def scientific_agent_catalog() -> dict[str, Any]:
        return {
            "roles": public_role_catalog(),
            "max_concurrent": MAX_CONCURRENT_SCIENTIFIC_SUBAGENTS,
            "shared_state": "parent notebook, evidence store, artifacts and task registry",
        }

    def _legacy_delegate_scientific_agents(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Start bounded scientific child runs that share the parent's evidence store."""

        parent = self.store.get_run(run_id)
        roles = select_scientific_roles(payload.get("roles"))
        existing_children = [
            item
            for item in self.store.list_runs(limit=200, archived=None)
            if str(item.get("parent_run_id", "")) == run_id
            and str(item.get("status", "")) in {"queued", "planning", "running", "verifying"}
        ]
        capacity = max(0, MAX_CONCURRENT_SCIENTIFIC_SUBAGENTS - len(existing_children))
        if capacity <= 0:
            return {
                "parent_run_id": run_id,
                "children": existing_children,
                "accepted": 0,
                "message": "已有科研子 Agent 正在运行；等待任一完成后再委派。",
            }
        roles = roles[:capacity]
        question = self._original_request_summary(
            dict(parent.get("input", {}) or {}),
            parent.get("workflow_type", ""),
        )
        instruction = str(payload.get("instruction", "") or "")
        children: list[dict[str, Any]] = []
        for role in roles:
            prompt = delegation_prompt(
                role,
                parent_title=str(parent.get("title", "")),
                parent_question=question,
                instruction=instruction,
            )
            compiled = compile_task_contract(
                task_mode=role.task_mode,
                user_text=prompt,
                workflow_type="scientific_subagent",
            )
            role_tools = set(role.allowed_capabilities)
            # Child roles are a read-only evidence-finding surface.  Even a
            # role that may recommend an artifact cannot create or mutate one
            # inside the parent workspace; its only handoff is a durable
            # result artifact and evidence/resource references.
            child_allowed = [
                name
                for name in list(compiled.get("allowed_tools", []) or [])
                if str(name) in role_tools
            ]
            parent_lease = dict(compiled.get("capability_lease", {}) or {})
            child_lease = {
                **parent_lease,
                "subagent": True,
                "requested_tools": sorted(role_tools),
                "allowed_tools": child_allowed,
                "unavailable_tools": sorted(role_tools - set(child_allowed)),
                "requested_mcp_servers": [],
                "allowed_mcp_servers": [],
                "unavailable_mcp_servers": [],
            }
            child_contract = {
                **compiled,
                "autonomy": "read_only",
                "risk_level": "read_only",
                "requires_plan": False,
                "allow_external_write": False,
                "allowed_tools": child_allowed,
                "allowed_mcp_servers": [],
                "capability_lease": child_lease,
                "success_criteria": [
                    "Return the required structured sub-agent handoff",
                    "Distinguish metadata, discovery snippets, and verified full text",
                    "Do not modify the parent workspace or external systems",
                ],
                "subagent": {
                    "role": role.role_id,
                    "parent_run_id": run_id,
                    "output_schema": structured_output_schema(role),
                },
            }
            child = self.store.create_run(
                notebook_id=str(parent.get("notebook_id", "")),
                workflow_type="ask",
                title=f"{role.label} · {str(parent.get('title', '科研任务'))}",
                input_payload={
                    "question": prompt,
                    "task_mode": role.task_mode,
                    "thinking_level": str(dict(parent.get("metadata", {}) or {}).get("thinking_level", "medium")),
                    "subagent_role": role.role_id,
                    "parent_run_id": run_id,
                    "output_schema": structured_output_schema(role),
                },
                stages=_WORKFLOWS["ask"]["stages"],
                model_provider_id=str(dict(parent.get("model", {}) or {}).get("provider_id", "")),
                model_id=str(dict(parent.get("model", {}) or {}).get("model_id", "")),
                metadata={
                    "runtime": "scansci-scientific-subagent.v1",
                    "subagent": {
                        "role": role.role_id,
                        "label": role.label,
                        "allowed_capabilities": list(role.allowed_capabilities),
                        "output_contract": role.output_contract,
                        "output_schema": structured_output_schema(role),
                        "write_access": False,
                    },
                    "shared_evidence": {
                        "notebook_id": str(parent.get("notebook_id", "")),
                        "parent_run_id": run_id,
                    },
                },
                parent_run_id=run_id,
                task_contract=child_contract,
                background=True,
            )
            self._submit(str(child["run_id"]))
            children.append(self.store.get_run(str(child["run_id"])))
        return {
            "parent_run_id": run_id,
            "children": children,
            "accepted": len(children),
            "max_concurrent": MAX_CONCURRENT_SCIENTIFIC_SUBAGENTS,
        }

    def _legacy_collect_scientific_agents(self, run_id: str) -> dict[str, Any]:
        """Collect auditable child results without pretending unfinished roles succeeded."""

        parent = self.store.get_run(run_id)
        children = [
            self.store.get_run(str(item["run_id"]))
            for item in self.store.list_runs(limit=200, archived=None)
            if str(item.get("parent_run_id", "")) == run_id
            and str(dict(self.store.get_run(str(item["run_id"])).get("metadata", {}) or {}).get("runtime", ""))
            == "scansci-scientific-subagent.v1"
        ]
        results: list[dict[str, Any]] = []
        aggregated_findings: list[dict[str, Any]] = []
        aggregated_evidence_uris: list[str] = []
        for child in children:
            artifact = dict(child.get("output_artifact") or {})
            role = dict(dict(child.get("metadata", {}) or {}).get("subagent", {}) or {})
            role_id = str(role.get("role", ""))
            validation = (
                validate_subagent_result(dict(artifact.get("payload", {}) or {}), role_id=role_id)
                if artifact and str(child.get("status", "")) == "completed" and role_id
                else {"valid": False, "errors": ["child_not_completed_or_artifact_missing"], "result": None}
            )
            handoff = dict(validation.get("result") or {})
            artifact_evidence_uris = [
                str(item.get("uri", ""))
                for item in list(artifact.get("evidence_links", []) or [])
                if isinstance(item, dict) and str(item.get("uri", ""))
            ]
            evidence_uris = list(dict.fromkeys([
                *[str(item) for item in list(handoff.get("evidence_uris", []) or [])],
                *artifact_evidence_uris,
            ]))
            if validation["valid"]:
                aggregated_findings.append({
                    "run_uri": str(child.get("uri", "")),
                    "artifact_uri": str(artifact.get("uri", "")),
                    "role": role_id,
                    "handoff": handoff,
                })
                aggregated_evidence_uris.extend(evidence_uris)
            results.append(
                {
                    "run_id": str(child.get("run_id", "")),
                    "run_uri": str(child.get("uri", "")),
                    "role": str(role.get("role", "")),
                    "label": str(role.get("label", "")),
                    "status": str(child.get("status", "")),
                    "summary": str(artifact.get("summary", "") or child.get("error", {}).get("message", "")),
                    "artifact_id": str(artifact.get("artifact_id", "")),
                    "artifact_uri": str(artifact.get("uri", "")),
                    "handoff_status": "valid" if validation["valid"] else "invalid",
                    "handoff": handoff if validation["valid"] else None,
                    "handoff_errors": list(validation.get("errors", []) or []),
                    "evidence_uris": evidence_uris,
                    "recovery": dict(child.get("recovery", {}) or {}),
                }
            )
        return {
            "parent_run_id": run_id,
            "parent_run_uri": str(parent.get("uri", "")),
            "children": results,
            "aggregated_findings": aggregated_findings,
            "evidence_uris": list(dict.fromkeys(aggregated_evidence_uris)),
            "complete": bool(results) and all(item["status"] == "completed" and item["handoff_status"] == "valid" for item in results),
            "completed": sum(item["status"] == "completed" and item["handoff_status"] == "valid" for item in results),
            "total": len(results),
        }

    def delegate_scientific_agents(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Atomically reserve and start bounded, read-only scientific children."""

        parent = self.store.get_run(run_id)
        if str(dict(parent.get("metadata", {}) or {}).get("runtime", "")) == "scansci-scientific-subagent.v1":
            raise PermissionError("Scientific child runs cannot delegate recursively")
        roles = select_scientific_roles(payload.get("roles"))
        question = self._original_request_summary(
            dict(parent.get("input", {}) or {}),
            parent.get("workflow_type", ""),
        )
        instruction = str(payload.get("instruction", "") or "")
        batch_key = str(payload.get("idempotency_key", "") or "").strip() or f"delegate-{uuid4().hex}"
        settings = load_settings(self.workspace)
        catalog = capability_catalog(
            workspace=self.workspace,
            evidence_db=self.evidence_db,
            mcp_servers=list(settings.get("mcp_servers", []) or []),
            plugins=list(settings.get("plugins", []) or []),
        )
        descriptors = {
            str(item.get("id", "")): dict(item)
            for item in list(catalog.get("capabilities", []) or [])
            if isinstance(item, dict)
        }
        parent_contract = dict(parent.get("task_contract", {}) or {})
        parent_allowed = set(str(item) for item in list(parent_contract.get("allowed_tools", []) or []))
        parent_initial = set(str(item) for item in list(parent_contract.get("initial_tools", []) or []))
        parent_max_tools = max(1, int(parent_contract.get("max_tool_budget", 6) or 6))
        parent_max_tokens = max(2, int(parent_contract.get("max_model_token_budget", 48000) or 48000))
        child_specs: list[dict[str, Any]] = []
        for role in roles:
            prompt = delegation_prompt(
                role,
                parent_title=str(parent.get("title", "")),
                parent_question=question,
                instruction=instruction,
            )
            role_tools = set(str(item) for item in role.allowed_capabilities)
            child_allowed = [
                name
                for name in role.allowed_capabilities
                if name in parent_allowed
                and str(descriptors.get(name, {}).get("status", "")) == "ready"
                and bool(descriptors.get(name, {}).get("subagent_allowed", False))
                and str(descriptors.get(name, {}).get("risk_level", "")) == "read_only"
            ]
            child_initial = [name for name in child_allowed if name in parent_initial]
            child_max_tools = max(1, min(4, parent_max_tools - 1)) if parent_max_tools > 1 else 1
            child_max_tokens = max(1, min(48000, parent_max_tokens - 1, parent_max_tokens // 2))
            compiled = compile_task_contract(
                task_mode=role.task_mode,
                user_text=prompt,
                workflow_type="scientific_subagent",
                available_tool_ids=child_allowed,
                allowed_mcp_servers=[],
            )
            compiled_lease = dict(compiled.get("capability_lease", {}) or {})
            child_lease = {
                **compiled_lease,
                "subagent": True,
                "requested_tools": list(role.allowed_capabilities),
                "allowed_tools": list(child_allowed),
                "unavailable_tools": sorted(role_tools - set(child_allowed)),
                "requested_mcp_servers": [],
                "allowed_mcp_servers": [],
                "unavailable_mcp_servers": [],
            }
            child_contract = {
                **compiled,
                "autonomy": "read_only",
                "risk_level": "read_only",
                "requires_plan": False,
                "allow_external_write": False,
                "allowed_tools": list(child_allowed),
                "initial_tools": child_initial,
                "allowed_mcp_servers": [],
                "capability_lease": child_lease,
                "initial_tool_budget": min(child_max_tools, max(1, len(child_initial))),
                "max_tool_budget": child_max_tools,
                "initial_model_token_budget": min(child_max_tokens, max(1, child_max_tokens // 2)),
                "max_model_token_budget": child_max_tokens,
                "success_criteria": [
                    "Return the required structured sub-agent handoff",
                    "Distinguish metadata, discovery snippets, and verified full text",
                    "Do not modify the parent workspace or external systems",
                ],
                "subagent": {
                    "role": role.role_id,
                    "parent_run_id": run_id,
                    "output_schema": structured_output_schema(role),
                    "recursive_delegation": False,
                    "trace_id": f"subagent-{uuid4().hex}",
                },
            }
            child_specs.append(
                {
                    "role_id": role.role_id,
                    "workflow_type": "ask",
                    "title": f"{role.label} · {str(parent.get('title', 'Scientific task'))}",
                    "input_payload": {
                        "question": prompt,
                        "task_mode": role.task_mode,
                        "thinking_level": str(dict(parent.get("metadata", {}) or {}).get("thinking_level", "medium")),
                        "subagent_role": role.role_id,
                        "parent_run_id": run_id,
                        "output_schema": structured_output_schema(role),
                    },
                    "stages": _WORKFLOWS["ask"]["stages"],
                    "model_provider_id": str(dict(parent.get("model", {}) or {}).get("provider_id", "")),
                    "model_id": str(dict(parent.get("model", {}) or {}).get("model_id", "")),
                    "metadata": {
                        "runtime": "scansci-scientific-subagent.v1",
                        "subagent": {
                            "role": role.role_id,
                            "label": role.label,
                            "allowed_capabilities": list(role.allowed_capabilities),
                            "output_contract": role.output_contract,
                            "output_schema": structured_output_schema(role),
                            "write_access": False,
                        },
                        "shared_evidence": {
                            "notebook_id": str(parent.get("notebook_id", "")),
                            "parent_run_id": run_id,
                        },
                    },
                    "task_contract": child_contract,
                }
            )
        reservation = self.store.reserve_scientific_children(
            run_id,
            idempotency_key=batch_key,
            child_specs=child_specs,
            max_children=MAX_CONCURRENT_SCIENTIFIC_SUBAGENTS,
        )
        replayed = bool(reservation.get("replayed", False))
        for reserved_child in list(reservation.get("children", []) or []):
            child_run_id = str(reserved_child.get("run_id", ""))
            child = self.store.get_run(child_run_id)
            if replayed:
                error = dict(child.get("error", {}) or {})
                if not (
                    str(child.get("status", "")) == "paused"
                    and str(error.get("code", "")) == "app_restarted"
                ):
                    # Same-process idempotent replay must never submit a second
                    # worker.  Only the durable crash marker proves that the
                    # commit-before-submit window was interrupted.
                    continue
            try:
                if replayed:
                    child = self.store.prepare_resume(child_run_id)
                self._submit(child_run_id)
            except Exception as exc:
                # One failed resume/submit is isolated to that child; reserved
                # siblings remain independently runnable and collectable.
                try:
                    self.store.begin_run(child_run_id)
                    self.store.fail_stage(
                        child_run_id,
                        str(child.get("current_stage", "answer")),
                        exc,
                    )
                except Exception:
                    pass
        children = [
            self.store.get_run(str(child["run_id"]))
            for child in list(reservation.get("children", []) or [])
        ]
        return {
            "parent_run_id": run_id,
            "children": children,
            "accepted": int(reservation.get("accepted", 0) or 0),
            "replayed": bool(reservation.get("replayed", False)),
            "max_concurrent": MAX_CONCURRENT_SCIENTIFIC_SUBAGENTS,
        }

    def list_scientific_agents(self, run_id: str) -> dict[str, Any]:
        """Idempotently list only durable children owned by one parent."""

        parent = self.store.get_run(run_id)
        return {
            "parent_run_id": run_id,
            "parent_run_uri": str(parent.get("uri", "")),
            "children": self.store.list_scientific_children(run_id),
            "max_concurrent": MAX_CONCURRENT_SCIENTIFIC_SUBAGENTS,
        }

    def cancel_scientific_agents(
        self,
        run_id: str,
        child_run_ids: Iterable[object] | None = None,
    ) -> dict[str, Any]:
        """Cancel selected owned children without trusting a model parent id."""

        owned = self.store.list_scientific_children(run_id)
        by_id = {str(child.get("run_id", "")): child for child in owned}
        requested = by_id.keys() if child_run_ids is None else child_run_ids
        selected = [str(item or "").strip() for item in list(requested)]
        if any(child_id not in by_id for child_id in selected):
            raise PermissionError("A scientific child must be owned by the active parent run")
        results: list[dict[str, Any]] = []
        cancelled = 0
        for child_id in dict.fromkeys(selected):
            child = self.store.get_run(child_id)
            if str(child.get("status", "")) not in {"completed", "failed", "cancelled"}:
                child = self.cancel(child_id)
                if str(child.get("status", "")) == "cancelled":
                    cancelled += 1
            results.append(child)
        return {
            "parent_run_id": run_id,
            "children": results,
            "selected": len(results),
            "cancelled": cancelled,
        }

    def collect_scientific_agents(
        self,
        run_id: str,
        *,
        wait_seconds: float = 0.0,
        poll_interval_ms: int = 50,
    ) -> dict[str, Any]:
        """Collect partial, membership-validated structured child handoffs."""

        try:
            bounded_wait = min(30.0, max(0.0, float(wait_seconds or 0.0)))
            bounded_poll_ms = min(1_000, max(10, int(poll_interval_ms or 50)))
        except (TypeError, ValueError) as error:
            raise ValueError("Scientific collect wait controls must be numeric") from error
        wait_started = time.monotonic()
        deadline = wait_started + bounded_wait
        wait_timed_out = False
        wait_interrupted = False
        if bounded_wait > 0:
            while True:
                snapshot = self.store.list_scientific_children(run_id)
                if not snapshot or all(
                    str(child.get("status", "")) in {"completed", "failed", "cancelled"}
                    for child in snapshot
                ):
                    break
                if self.store.stop_requested(run_id):
                    wait_interrupted = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    wait_timed_out = True
                    break
                time.sleep(min(bounded_poll_ms / 1000.0, remaining))
        parent = self.store.get_run(run_id)
        children = self.store.list_scientific_children(run_id)
        allowed_uris = {str(parent.get("uri", ""))}
        for owned_run in [parent, *children]:
            allowed_uris.add(str(owned_run.get("uri", "")))
            for artifact in list(owned_run.get("artifacts", []) or []):
                if not isinstance(artifact, dict):
                    continue
                allowed_uris.add(str(artifact.get("uri", "")))
                allowed_uris.update(
                    str(link.get("uri", ""))
                    for link in list(artifact.get("evidence_links", []) or [])
                    if isinstance(link, dict) and str(link.get("uri", ""))
                )
        allowed_uris.discard("")
        results: list[dict[str, Any]] = []
        aggregated_findings: list[dict[str, Any]] = []
        aggregated_evidence_uris: list[str] = []
        counts = {"valid": 0, "running": 0, "failed": 0, "cancelled": 0, "invalid": 0, "total": len(children)}
        for child in children:
            artifact = dict(child.get("output_artifact") or {})
            role = dict(dict(child.get("metadata", {}) or {}).get("subagent", {}) or {})
            role_id = str(role.get("role", ""))
            status = str(child.get("status", ""))
            validation = (
                validate_subagent_result(
                    dict(artifact.get("payload", {}) or {}),
                    role_id=role_id,
                    allowed_uris=allowed_uris,
                )
                if artifact and status == "completed" and role_id
                else {"valid": False, "errors": ["child_not_completed_or_artifact_missing"], "result": None}
            )
            handoff = dict(validation.get("result") or {})
            artifact_evidence_uris = [
                str(item.get("uri", ""))
                for item in list(artifact.get("evidence_links", []) or [])
                if isinstance(item, dict) and str(item.get("uri", "")) in allowed_uris
            ]
            evidence_uris = list(dict.fromkeys([
                *[str(item) for item in list(handoff.get("evidence_uris", []) or [])],
                *artifact_evidence_uris,
            ]))
            if status == "completed" and validation["valid"]:
                counts["valid"] += 1
                aggregated_findings.append(
                    {
                        "run_uri": str(child.get("uri", "")),
                        "artifact_uri": str(artifact.get("uri", "")),
                        "role": role_id,
                        "handoff": handoff,
                    }
                )
                aggregated_evidence_uris.extend(evidence_uris)
            elif status == "failed":
                counts["failed"] += 1
            elif status == "cancelled":
                counts["cancelled"] += 1
            elif status == "completed":
                counts["invalid"] += 1
            else:
                counts["running"] += 1
            handoff_status = "valid" if validation["valid"] else ("invalid" if status == "completed" else status)
            results.append(
                {
                    "run_id": str(child.get("run_id", "")),
                    "run_uri": str(child.get("uri", "")),
                    "role": role_id,
                    "label": str(role.get("label", "")),
                    "status": status,
                    "summary": str(artifact.get("summary", "") or child.get("error", {}).get("message", "")),
                    "artifact_id": str(artifact.get("artifact_id", "")),
                    "artifact_uri": str(artifact.get("uri", "")),
                    "handoff_status": handoff_status,
                    "handoff": handoff if validation["valid"] else None,
                    "handoff_errors": list(validation.get("errors", []) or []),
                    "evidence_uris": evidence_uris,
                    "recovery": dict(child.get("recovery", {}) or {}),
                }
            )
        return {
            "parent_run_id": run_id,
            "parent_run_uri": str(parent.get("uri", "")),
            "children": results,
            "aggregated_findings": aggregated_findings,
            "evidence_uris": list(dict.fromkeys(aggregated_evidence_uris)),
            "complete": bool(results) and counts["valid"] == counts["total"],
            "counts": counts,
            "completed": counts["valid"],
            "total": len(results),
            "wait": {
                "requested_seconds": bounded_wait,
                "waited_seconds": round(time.monotonic() - wait_started, 6),
                "timed_out": wait_timed_out,
                "interrupted": wait_interrupted,
                "poll_interval_ms": bounded_poll_ms,
            },
        }

    def _scientific_agent_control(
        self,
        name: str,
        active_run_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a Pi scientific control under the host-bound active run."""

        parent = self.store.get_run(active_run_id)
        if str(dict(parent.get("metadata", {}) or {}).get("runtime", "")) == "scansci-scientific-subagent.v1":
            raise PermissionError("Scientific child runs cannot control or delegate scientific agents")
        if str(parent.get("status", "")) not in {
            "queued", "planning", "running", "verifying", "paused", "waiting_input", "needs_confirmation"
        }:
            raise PermissionError("Scientific agent controls require an active durable parent run")
        if name == "delegate_scientific_agents":
            return self.delegate_scientific_agents(active_run_id, dict(arguments or {}))
        if name == "list_scientific_agents":
            return self.list_scientific_agents(active_run_id)
        if name == "collect_scientific_agents":
            return self.collect_scientific_agents(
                active_run_id,
                wait_seconds=arguments.get("wait_seconds", 0.0),
                poll_interval_ms=arguments.get("poll_interval_ms", 50),
            )
        if name == "cancel_scientific_agents":
            child_run_ids = (
                list(arguments.get("child_run_ids") or [])
                if "child_run_ids" in arguments
                else None
            )
            return self.cancel_scientific_agents(
                active_run_id,
                child_run_ids,
            )
        raise ValueError(f"Unsupported scientific agent control: {name}")

    def branch_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a durable conversation/task branch and optionally execute it."""

        overrides = dict(payload.get("input_overrides", {}) or {})
        instruction = str(payload.get("instruction", "") or "").strip()
        if instruction:
            overrides["branch_instruction"] = instruction
        branch = self.store.fork_run(
            run_id,
            branch_from_message_id=str(payload.get("message_id", "") or ""),
            title=str(payload.get("title", "") or ""),
            input_overrides=overrides,
            background=bool(payload.get("background", True)),
        )
        if bool(payload.get("execute", True)):
            self._submit(str(branch["run_id"]))
        return self.store.get_run(str(branch["run_id"]))

    def recover_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one advertised recovery action without discarding prior work."""

        action = str(payload.get("action", "retry") or "retry").strip()
        if action in {"branch", "change_strategy", "change_source"}:
            return self.branch_run(
                run_id,
                {
                    "instruction": str(payload.get("instruction", "") or ""),
                    "input_overrides": dict(payload.get("input_overrides", {}) or {}),
                    "background": True,
                    "execute": True,
                },
            )
        if action == "compact_retry":
            session_id = f"research-run-{run_id}"
            compacted = self.compact_chat({"session_id": session_id})
            if not compacted.get("ok"):
                self._forget_pi_session(session_id)
        return self.resume(run_id)

    def advisor_report(self, run_id: str) -> dict[str, Any]:
        """Return a fresh deterministic advisor report for a durable task."""

        return review_research_run(self.store.get_run(run_id))

    def apply_advisor_action(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Turn a deterministic advisor recommendation into one auditable follow-up.

        The advisor cannot expand authority: every non-trivial recommendation
        forks the existing contract into a separate task, preserving the
        original result and its evidence trail.
        """

        report = self.advisor_report(run_id)
        action = str(payload.get("action") or report.get("recommended_next_action") or "none").strip()
        recommended = str(report.get("recommended_next_action", "none"))
        if action != recommended:
            raise ValueError("Advisor action does not match the current durable report")
        if action == "none":
            return self.store.get_run(run_id)
        instructions = {
            "run_evidence_verification": "Re-check the prior answer against locatable evidence. Do not make claims without evidence links.",
            "resume_required_tools": "Complete the task's required tool group before delivering a conclusion.",
            "review_failed_tools": "Review failed tool calls, use only the existing capability lease, and state any remaining gap.",
            "rebuild_delivery": "Rebuild the delivery artifact from already durable work; do not claim missing output exists.",
        }
        instruction = instructions.get(action)
        if not instruction:
            raise ValueError(f"Unsupported advisor action: {action}")
        self.store.append_event(
            run_id,
            event_type="advisor.action_requested",
            summary=f"Advisor follow-up requested: {action}",
            payload={"action": action, "verdict": report["verdict"], "findings": report["findings"]},
        )
        return self.branch_run(
            run_id,
            {
                "instruction": instruction,
                "background": True,
                "execute": True,
            },
        )

    def respond_run_interaction(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Resolve a stored plan/AskUser gate and continue the same task."""

        interaction_id = str(payload.get("interaction_id", "") or "")
        response = dict(payload.get("response", {}) or {})
        with self._active_pi_lock:
            client = self._active_pi_clients.get(run_id)
        if client is not None and client.active_request_id:
            client.respond_interaction(interaction_id, response, request_id=client.active_request_id)
        resolved = self.store.resolve_interaction(
            run_id,
            interaction_id=interaction_id,
            response=response,
        )
        decision = str(response.get("decision", response.get("action", "approve")) or "approve")
        if decision not in {"cancel", "reject"}:
            return self.resume(run_id)
        return resolved

    def restart(self, run_id: str) -> dict[str, Any]:
        """Restart a finished task without creating a second conversation."""

        run = self.store.prepare_restart(run_id)
        self._submit(run_id)
        return run

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.store.request_cancel(run_id)
        with self._active_pi_lock:
            active_pi = self._active_pi_clients.get(str(run_id))
        if active_pi is not None:
            active_pi.cancel()
        if run["status"] in {"queued", "paused"}:
            return self.store.mark_cancelled(run_id)
        return run

    def pause(self, run_id: str) -> dict[str, Any]:
        """Cooperatively pause a durable run so its completed stages remain resumable."""
        run = self.store.request_pause(run_id)
        with self._active_pi_lock:
            active_pi = self._active_pi_clients.get(str(run_id))
        if active_pi is not None:
            active_pi.pause()
        if run["status"] == "queued":
            return self.store.mark_paused(run_id)
        return run

    def _pi_image_analysis(
        self,
        *,
        question: str,
        evidence_db: Path,
        provider: dict[str, Any],
        model_id: str,
        api_key: str,
        image_attachments: list[dict[str, Any]],
    ) -> str:
        """Analyze evidence images through one bounded native Pi turn."""

        provider_id = str(provider.get("id", ""))
        provider_kind = str(provider.get("kind", ""))
        base_url = str(provider.get("base_url", ""))
        if provider_id == "local-huggingface":
            base_url = ensure_local_transformers_runtime(model_id)
            provider_kind = "openai-compatible"
        responses_enabled = bool(provider.get("responses_enabled", False))
        api_surface = select_api_surface(
            str(provider.get("api_surface", "chat_completions")),
            provider_kind=provider_kind,
            provider_id=provider_id,
            model=model_id,
            responses_enabled=responses_enabled,
        )
        model_record = next(
            (
                dict(item)
                for item in list(provider.get("models", []) or [])
                if isinstance(item, dict) and str(item.get("id", "")) == model_id
            ),
            {},
        )
        descriptor = descriptor_from_model_record(
            provider_id=provider_id,
            provider_kind=provider_kind,
            model_id=model_id,
            model_record=model_record,
            api_surface=api_surface,
        )
        images = pi_image_blocks(self.workspace, image_attachments)
        if not images or not descriptor.supports_images:
            raise PiMultimodalUnavailable(
                "The selected evidence vision route does not declare native image input"
            )
        contract = {
            "schema_version": "scansci.task-contract.v2",
            "version": 2,
            "contract_id": f"evidence-vision-{uuid4().hex[:12]}",
            "goal": "Analyze only the attached evidence images for the user's question",
            "request": question,
            "constraints": [
                "Do not call tools or request interactions",
                "Do not infer facts that are not visible in the supplied images",
            ],
            "required_evidence": [],
            "allowed_tools": [],
            "initial_tools": [],
            "allowed_mcp_servers": [],
            "required_tool_groups": [],
            "risk_level": "none",
            "autonomy": "none",
            "requires_plan": False,
            "allow_external_write": False,
            "initial_tool_budget": 1,
            "max_tool_budget": 1,
            "model_token_budget": min(16_000, descriptor.max_output_tokens * 2),
            "max_model_token_budget": min(32_000, descriptor.context_window_tokens),
            "success_criteria": ["Return a concise image-grounded analysis"],
        }
        client = PiAgentClient(workspace=self.workspace, evidence_db=evidence_db)
        fragments: list[str] = []
        try:
            for event in client.stream_chat(
                provider_kind=provider_kind,
                base_url=base_url,
                api_key=api_key,
                model_id=model_id,
                api_surface=api_surface,
                responses_enabled=responses_enabled,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Analyze the attached images for the final user question. "
                            "Describe only visible evidence, preserve uncertainty, and do not call tools."
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                images=images,
                model_runtime=descriptor.to_dict(),
                thinking_level="off",
                task_mode="vision",
                task_contract=contract,
                timeout_seconds=120.0,
                session_id=None,
            ):
                event_type = str(event.get("type", ""))
                if event_type == "delta":
                    fragments.append(str(event.get("content", "")))
                elif event_type in {"tool.completed", "tool.failed", "interaction"}:
                    raise RuntimeError("Evidence vision Pi turn attempted to expand its empty authority lease")
                elif event_type == "cancelled":
                    raise RuntimeError("Evidence vision Pi turn was cancelled")
        finally:
            client.close()
        text = "".join(fragments).strip()
        if not text:
            raise RuntimeError("Evidence vision Pi turn returned an empty response")
        return text

    def answer_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValueError("question is required")
        notebook = self._requested_notebook(payload)
        evidence_db = self._evidence_db_for_notebook(notebook)
        requested_notebook_ids = list(dict.fromkeys([
            str(notebook.get("notebook_id", "")).strip(),
            *[
                str(value).strip()
                for value in list(payload.get("notebook_ids", []) or [])
                if str(value).strip()
            ],
        ]))
        requested_notebook_ids = [value for value in requested_notebook_ids if value]
        evidence_dbs: list[Path] = [evidence_db]
        for notebook_id in requested_notebook_ids:
            candidate = self._evidence_db_for_notebook(self._notebook(notebook_id))
            if candidate not in evidence_dbs:
                evidence_dbs.append(candidate)
        thinking_level = normalize_thinking_level(payload.get("thinking_level"))
        try:
            requested_limit = payload.get("limit", evidence_budget_for_thinking(thinking_level))
            limit = min(20, max(1, int(requested_limit)))
        except (TypeError, ValueError) as error:
            raise ValueError("limit must be an integer") from error
        try:
            requested_max_quotes = payload.get("max_quotes", min(8, limit))
            max_quotes = min(16, max(1, int(requested_max_quotes)))
            min_quotes = max(1, int(payload.get("min_quotes", 1)))
            min_documents = max(1, int(payload.get("min_documents", 1)))
            per_document_limit = max(0, int(payload.get("per_document_limit", 5)))
            query_variants = max(1, int(payload.get("query_variants", 2)))
            max_followup_queries = max(0, int(payload.get("max_followup_queries", 1)))
        except (TypeError, ValueError) as error:
            raise ValueError("evidence retrieval controls must be integers") from error
        settings = load_settings(self.workspace)
        quality_profile = self._retrieval_quality(payload, default="balanced")
        # Do not let the first evidence question synchronously load local
        # embedding/reranker weights on the conversation worker. The existing
        # preparation path keeps retrieval responsive with its lexical fallback
        # while the neural stack warms in the background.
        self.prepare_local_evidence(evidence_db, quality_profile=quality_profile)
        local_evidence = self._local_evidence_stack(
            evidence_db,
            quality_profile=quality_profile,
        )
        active = dict(settings.get("active_model", {}) or {})
        provider = next(
            (item for item in settings.get("providers", []) if item.get("id") == active.get("provider_id")),
            None,
        )
        answer_options: dict[str, Any] = {}
        result: dict[str, Any] | None = None
        agent_harness = "local-evidence"
        agent_fallback_error = ""
        agent_fallback_failure: dict[str, Any] = {}
        # Article drafting already makes one final generation call after
        # retrieval.  Keeping the intermediate evidence workflow local avoids
        # serial model calls (query rewrite, synthesis, verification, then
        # drafting) that can leave the direct-chat surface waiting for minutes.
        force_local_evidence = bool(payload.get("force_local_evidence", False))
        research_run_id = str(payload.get("_research_run_id", "") or "")
        durable_task_contract: dict[str, Any] | None = None
        scientific_child_role = ""
        scientific_child_schema: dict[str, Any] = {}
        scientific_allowed_uris: set[str] = set()
        if research_run_id:
            durable_run = self.store.get_run(research_run_id)
            persisted_contract = dict(durable_run.get("task_contract", {}) or {})
            is_scientific_child = (
                str(dict(durable_run.get("metadata", {}) or {}).get("runtime", ""))
                == "scansci-scientific-subagent.v1"
            )
            if is_scientific_child and not persisted_contract:
                raise PermissionError("Scientific child run is missing its durable capability contract")
            durable_task_contract = persisted_contract or None
            if is_scientific_child:
                subagent_contract = dict(persisted_contract.get("subagent", {}) or {})
                scientific_child_role = str(subagent_contract.get("role", "")).strip()
                scientific_child_schema = dict(subagent_contract.get("output_schema", {}) or {})
                if not scientific_child_role or not scientific_child_schema:
                    raise PermissionError("Scientific child run is missing its durable output contract")
                scientific_allowed_uris.add(str(durable_run.get("uri", "")))
                parent_run_id = str(durable_run.get("parent_run_id", "")).strip()
                if parent_run_id:
                    scientific_allowed_uris.add(str(self.store.get_run(parent_run_id).get("uri", "")))
                scientific_allowed_uris.discard("")
        image_attachments = list(payload.get("images", []) or [])
        image_analysis: dict[str, Any] | None = None
        if image_attachments:
            blocks = vision_image_blocks(self.workspace, image_attachments)
            active_model_id = str(active.get("model_id", "")).strip()
            active_model_record = next(
                (
                    item
                    for item in list(provider.get("models", []) or [])
                    if str(item.get("id", "")) == active_model_id
                ),
                {},
            ) if provider is not None else {}
            active_has_vision = "vision" in {
                str(value).strip().lower()
                for value in list(active_model_record.get("capabilities", []) or [])
            }
            vision_provider = provider if active_has_vision else None
            vision_model_id = active_model_id if active_has_vision else ""
            vision_api_key = ""
            if vision_provider is not None:
                managed_vision = str(vision_provider.get("auth_mode", "")) == "managed"
                vision_api_key = (
                    "scansci-managed-gateway"
                    if managed_vision
                    else "local"
                    if str(vision_provider.get("auth_mode", "")).strip().lower() == "local"
                    or str(vision_provider.get("kind", "")).strip().lower() == "local"
                    else get_provider_api_key(self.workspace, str(vision_provider.get("id", "")))
                )
                if not vision_api_key:
                    vision_provider = None
            if vision_provider is None:
                try:
                    selected_vision = select_vision_route(
                        self.workspace,
                        settings,
                        active_provider_id=str(active.get("provider_id", "")),
                        active_model_id=active_model_id,
                        # Evidence answers must not silently ship user images
                        # to a cloud vision provider the user never selected.
                        # Only the explicitly selected model or a local vision
                        # runtime qualifies; otherwise the OCR fallback below
                        # keeps the image local.
                        allow_cloud=False,
                    )
                except Exception:
                    selected_vision = None
                if selected_vision is not None:
                    vision_provider = dict(selected_vision["provider"])
                    vision_model_id = str(selected_vision["model_id"])
                    vision_api_key = str(selected_vision.get("api_key", ""))
            if vision_provider is not None and vision_api_key:
                try:
                    image_analysis = {
                        "text": self._pi_image_analysis(
                            question=question,
                            evidence_db=evidence_db,
                            provider=vision_provider,
                            model_id=vision_model_id,
                            api_key=vision_api_key,
                            image_attachments=image_attachments,
                        ),
                        "image_count": len(image_attachments),
                        "model": vision_model_id,
                    }
                except Exception as error:
                    ocr = ocr_image_blocks(blocks, workspace=self.workspace, settings=settings)
                    image_analysis = {
                        "text": str(ocr.get("text", "")).strip()
                        or "视觉模型本轮未能读取图片，且 OCR 未提取到可用文字。请不要根据图片猜测事实。",
                        "image_count": len(image_attachments),
                        "model": "本地 OCR（视觉模型失败后兜底）",
                        "fallback_reason": f"{type(error).__name__}: {error}"[:240],
                    }
            else:
                ocr = ocr_image_blocks(blocks, workspace=self.workspace, settings=settings)
                image_analysis = {
                    "text": str(ocr.get("text", "")).strip()
                    or "当前没有可用的视觉模型，且 OCR 未提取到可用文字。请不要根据图片猜测事实。",
                    "image_count": len(image_attachments),
                    "model": "本地 OCR（视觉模型不可用时兜底）",
                }
        if image_attachments and image_analysis is not None:
            analysis_text = str(image_analysis.get("text", "")).strip()
            if analysis_text:
                question = f"{question}\n\n【图片内容（{str(image_analysis.get('model', ''))}）】\n{analysis_text}"
        if scientific_child_role and provider is None:
            raise RuntimeError("Scientific child run has no configured Pi model route")
        if scientific_child_role or (
            provider is not None and provider.get("kind") != "local" and not force_local_evidence
        ):
            assert provider is not None
            managed = str(provider.get("auth_mode", "")) == "managed"
            local_provider = str(provider.get("kind", "")).strip().lower() == "local"
            api_key = (
                "scansci-managed-gateway"
                if managed
                else "local"
                if local_provider
                else get_provider_api_key(self.workspace, str(provider.get("id", "")))
            )
            pi_provider_kind = str(provider.get("kind", ""))
            pi_base_url = str(provider.get("base_url", ""))
            if str(provider.get("id", "")) == "local-huggingface":
                pi_base_url = ensure_local_transformers_runtime(str(active.get("model_id", "")))
                pi_provider_kind = "openai-compatible"
            if not api_key and not image_attachments:
                raise ValueError("当前生成模型尚未配置 API Key")
            task_mode = str(payload.get("task_mode", "evidence")).strip().lower() or "evidence"
            requested_harness = str(payload.get("agent_harness", "")).strip().lower()
            # Evidence answers have a product-owned retrieval, citation and
            # verification pipeline below.  Running an additional model
            # tool-planning loop first delays the answer and makes a
            # gateway-side tool incompatibility look like a retrieval
            # failure.  Keep Pi available when it is explicitly requested,
            # but make the deterministic evidence workflow the default.
            harness = requested_harness or (
                "fixed-workflow"
                if task_mode in {"auto", "evidence", "verified-answer"}
                else "pi"
            )
            tool_calls: list[dict[str, Any]] = []
            # PiAgentClient owns a local compatibility adapter for ScanSci's
            # text-only managed gateway, so managed models must traverse the
            # same real Pi session/tool loop as other production providers.
            use_native_tool_loop = harness not in {"legacy", "fixed-workflow"}
            if harness in {"deep", "deep-agent", "deep-agents"}:
                try:
                    model = build_deep_agent_model(
                        provider_id=str(provider.get("id", "")),
                        provider_kind=str(provider.get("kind", "")),
                        base_url=str(provider.get("base_url", "")),
                        api_key=api_key,
                        model=str(active.get("model_id", "")),
                        thinking_level=thinking_level,
                    )
                    deep_agent = ScanSciDeepAgent(
                        evidence_db=evidence_db,
                        workspace=self.workspace,
                        model=model,
                    )
                    deep_agent.embedding_provider = local_evidence.embedding_provider
                    deep_agent.reranker = local_evidence.reranker
                    result = deep_agent.answer(
                        question,
                        limit=limit,
                        thread_id=str(payload.get("thread_id", "")),
                        task_mode=str(payload.get("task_mode", "auto")),
                    )
                    agent_harness = "deep-agents"
                except Exception as error:  # optional compatibility harness
                    agent_fallback_error = f"{type(error).__name__}: {error}"
            elif use_native_tool_loop:
                try:
                    active_model_id = str(active.get("model_id", ""))
                    native_api_surface = select_api_surface(
                        str(provider.get("api_surface", "chat_completions")),
                        provider_kind=pi_provider_kind,
                        provider_id=str(provider.get("id", "")),
                        model=active_model_id,
                        responses_enabled=bool(provider.get("responses_enabled", False)),
                    )
                    native_model_record = next(
                        (
                            dict(item)
                            for item in list(provider.get("models", []) or [])
                            if isinstance(item, dict) and str(item.get("id", "")) == active_model_id
                        ),
                        {},
                    )
                    native_model_runtime = descriptor_from_model_record(
                        provider_id=str(provider.get("id", "")),
                        provider_kind=pi_provider_kind,
                        model_id=active_model_id,
                        model_record=native_model_record,
                        api_surface=native_api_surface,
                    )
                    pi_agent = PiAgentClient(
                        workspace=self.workspace,
                        evidence_db=evidence_db,
                        root_evidence_db=self.evidence_db,
                        additional_evidence_dbs=evidence_dbs[1:],
                        notebook_ids=requested_notebook_ids,
                        knowledge_scope=self._scope_for_notebook(payload, notebook),
                        active_run_id=research_run_id,
                        scientific_agent_control=(
                            self._scientific_agent_control if research_run_id else None
                        ),
                    )
                    pi_agent.embedding_provider = local_evidence.embedding_provider
                    pi_agent.reranker = local_evidence.reranker
                    if research_run_id:
                        with self._active_pi_lock:
                            self._active_pi_clients[research_run_id] = pi_agent
                    try:
                        if scientific_child_role:
                            system_content = (
                                "You are a bounded ScanSci scientific sub-agent. Execute only tools in the "
                                "host-issued lease. Return exactly one JSON object matching this schema; do not "
                                "wrap it in Markdown or add prose: "
                                + json.dumps(scientific_child_schema, ensure_ascii=False, separators=(",", ":"))
                            )
                        else:
                            system_content = (
                                "You are ScanSci. This endpoint requires a source-grounded answer. "
                                "Call build_verified_answer and use its verified result for delivery."
                            )
                        child_fragments: list[str] = []
                        pi_events = pi_agent.stream_chat(
                            provider_kind=pi_provider_kind,
                            base_url=pi_base_url,
                            api_key=api_key,
                            model_id=active_model_id,
                            api_surface=native_api_surface,
                            responses_enabled=bool(provider.get("responses_enabled", False)),
                            model_runtime=native_model_runtime,
                            messages=[
                                {
                                    "role": "system",
                                    "content": system_content,
                                },
                                {"role": "user", "content": question},
                            ],
                            # The managed text gateway does not expose native
                            # tool calls or a separate reasoning budget. Keep
                            # this one-action protocol non-reasoning so the
                            # bounded output window contains the actual intent
                            # instead of being exhausted by hidden thoughts.
                            # Scientific children inherit the parent's durable
                            # request.  Pi must see max/xhigh so its provider-
                            # specific clamp emits an explicit degradation;
                            # silently forcing managed children to off would
                            # discard that contract.
                            thinking_level=(
                                thinking_level
                                if scientific_child_role
                                else "off"
                                if managed
                                else thinking_level
                            ),
                            task_mode="verified-answer" if task_mode in {"auto", "evidence"} else task_mode,
                            task_contract=durable_task_contract,
                            timeout_seconds=self.verified_answer_pi_timeout_seconds(managed=managed),
                        )
                        for pi_event in pi_events:
                            event_type = str(pi_event.get("type", ""))
                            if event_type == "delta" and scientific_child_role:
                                child_fragments.append(str(pi_event.get("content", "")))
                            elif event_type == "tool.completed":
                                tool_name = str(pi_event.get("name", ""))
                                tool_calls.append({"name": tool_name, "status": "completed"})
                                if scientific_child_role:
                                    scientific_allowed_uris.update(
                                        _owned_scientific_evidence_uris(
                                            tool_name,
                                            pi_event.get("result", {}),
                                            evidence_dbs,
                                        )
                                    )
                                if not scientific_child_role and tool_name == "build_verified_answer":
                                    result = dict(pi_event.get("result", {}) or {})
                            elif event_type == "cancelled":
                                raise _RunCancelled("Pi Agent run was cancelled")
                        if scientific_child_role:
                            raw_handoff = "".join(child_fragments).strip()
                            try:
                                parsed_handoff = json.loads(raw_handoff)
                            except (json.JSONDecodeError, TypeError) as error:
                                raise RuntimeError(
                                    "Scientific child Pi turn did not return one valid JSON object"
                                ) from error
                            validation = validate_subagent_result(
                                {"subagent_handoff": parsed_handoff},
                                role_id=scientific_child_role,
                                allowed_uris=scientific_allowed_uris,
                            )
                            if not validation["valid"]:
                                raise RuntimeError(
                                    "Scientific child Pi handoff failed its durable schema: "
                                    + ", ".join(str(item) for item in validation.get("errors", []))
                                )
                            result = {
                                "subagent_handoff": dict(validation["result"] or {}),
                                "_scientific_allowed_uris": sorted(scientific_allowed_uris),
                            }
                    finally:
                        if research_run_id:
                            with self._active_pi_lock:
                                if self._active_pi_clients.get(research_run_id) is pi_agent:
                                    self._active_pi_clients.pop(research_run_id, None)
                        pi_agent.close()
                    agent_harness = "pi-agent-sdk"
                    if result is None:
                        agent_fallback_error = "Pi Agent did not finalize through build_verified_answer"
                except _RunCancelled:
                    raise
                except Exception as error:  # provider tool compatibility boundary
                    if scientific_child_role:
                        # A child may not escape its persisted lease by falling
                        # into the broader fixed verified-answer workflow.
                        raise
                    # Do not fail the user's evidence task merely because a
                    # nominally OpenAI-compatible endpoint implements a
                    # different tool/message schema.  The fixed workflow
                    # below retains the same retrieval and citation gates.
                    agent_fallback_error = f"{type(error).__name__}: {error}"
                    if isinstance(error, PiAgentRunError):
                        agent_fallback_failure = dict(error.failure or {})
            if result is None:
                rag_client = self._pi_role_chat_client(
                    provider_kind=str(provider.get("kind", "")),
                    provider_id=str(provider.get("id", "")),
                    base_url=str(provider.get("base_url", "")),
                    api_key=api_key,
                    model_id=str(active.get("model_id", "")),
                    role="evidence",
                    timeout_seconds=35.0 if managed else 60.0,
                    api_surface=str(provider.get("api_surface", "chat_completions")),
                    responses_enabled=bool(provider.get("responses_enabled", False)),
                )
                answer_options = {
                    "answer_provider": "llm",
                    # Retrieval already expands queries locally, and citation
                    # verification is deterministic; keep model use to one synthesis call.
                    "verification_provider": "local",
                    "query_rewrite_provider": "local",
                    "chat_client": rag_client,
                }
                agent_harness = "pi-fixed-workflow"
        if result is None:
            if not evidence_db.exists():
                raise FileNotFoundError(f"Evidence store does not exist: {evidence_db}")
            available_evidence_dbs = [path for path in evidence_dbs if path.is_file()]
            if len(available_evidence_dbs) > 1:
                federated_client = PiAgentClient(
                    workspace=self.workspace,
                    evidence_db=available_evidence_dbs[0],
                    root_evidence_db=self.evidence_db,
                    additional_evidence_dbs=available_evidence_dbs[1:],
                    notebook_ids=requested_notebook_ids,
                    knowledge_scope=self._scope_for_notebook(payload, notebook),
                    embedding_provider=local_evidence.embedding_provider,
                    reranker=local_evidence.reranker,
                )
                try:
                    result = federated_client._build_verified_answer(
                        {"question": question, "result_limit": limit}
                    )
                finally:
                    federated_client.close()
            else:
                result = answer_question(
                    evidence_db,
                    question,
                    limit=limit,
                    max_quotes=max_quotes,
                    min_quotes=min_quotes,
                    min_documents=min_documents,
                    adequacy_profile="manual",
                    agentic_profile="custom",
                    query_variants=query_variants,
                    max_followup_queries=max_followup_queries,
                    per_document_limit=per_document_limit,
                    embedding_provider=local_evidence.embedding_provider,
                    reranker=local_evidence.reranker,
                    filters=self._knowledge_filters_for_notebook(
                        notebook,
                        evidence_db,
                        self._scope_for_notebook(payload, notebook),
                        question,
                    ),
                    **answer_options,
                )
            steps = list(dict(result.get("agentic_trace", {}) or {}).get("steps", []) or [])
            result["pi_agent"] = {
                "harness": agent_harness,
                "task_mode": str(payload.get("task_mode", "evidence") or "evidence"),
                "evidence_policy": "strict",
                "finalization": "verified-workflow",
                "tool_calls": _provider_neutral_tool_calls(result, steps=steps),
                "compatibility_fallback": bool(agent_fallback_error),
                "compatibility_error": agent_fallback_error[:500],
                "compatibility_failure": {
                    "reason": str(
                        agent_fallback_failure.get("reason")
                        or agent_fallback_failure.get("code")
                        or ""
                    ),
                    "retryable": bool(agent_fallback_failure.get("retryable")),
                },
            }
        else:
            result["pi_agent"] = {
                "harness": agent_harness,
                "task_mode": str(payload.get("task_mode", "evidence") or "evidence"),
                "evidence_policy": "strict",
                "finalization": "build_verified_answer",
                "tool_calls": tool_calls,
                "compatibility_fallback": False,
                "compatibility_error": "",
                "compatibility_failure": {"reason": "", "retryable": False},
            }
        scope_resolution = self._knowledge_scope_resolution(
            notebook,
            evidence_db,
            self._scope_for_notebook(payload, notebook),
            question,
        )
        if scope_resolution.get("active") and str(scope_resolution.get("type", "")) != "zotero-tag":
            result["knowledge_scope"] = scope_resolution
        reader_answer = dict(result.get("reader_answer", {}) or {})
        citations = []
        for citation in list(reader_answer.get("citations", []) or []):
            item = dict(citation)
            doc_id = str(item.get("doc_id", ""))
            anchor = str(item.get("html_anchor", ""))
            item["reader_url"] = self._reader_url(doc_id, anchor) if doc_id else ""
            item["original_url"] = self._original_url(doc_id) if doc_id else ""
            citations.append(item)
        reader_answer["citations"] = citations
        result["reader_answer"] = reader_answer
        if image_analysis is not None:
            result["image_analysis"] = image_analysis
        runtime_meta = dict(result.get("agent_runtime", {}) or {})
        runtime_meta["thinking_level"] = thinking_level
        runtime_meta["evidence_budget"] = limit
        result["agent_runtime"] = runtime_meta
        result["retrieval_runtime"] = dict(local_evidence.metadata)
        return result

    @staticmethod
    def _pi_eligible(chat_request: _DirectChatRequest, payload: dict[str, Any]) -> bool:
        del payload
        # Harness selection is a host architecture decision, not a client-side
        # escape hatch.  Task 5 extends this boundary to validated image blocks;
        # for now every text-only request is Pi eligible, including local
        # OpenAI-compatible models.
        return all(isinstance(item.get("content"), str) for item in chat_request.messages)

    @staticmethod
    def _pi_task_mode(chat_mode: str) -> str:
        return chat_mode if chat_mode in {"knowledge", "slides"} else "general"

    @staticmethod
    def _evidence_policy(task_mode: str) -> str:
        parts = _pi_mode_parts(task_mode)
        if parts & {"knowledge", "research", "verified-answer", "benchmark"}:
            return "strict"
        if parts & {"workspace-status", "task-documents", "zotero-search", "web", "web-auto", "slides"}:
            return "assist"
        return "off"

    @staticmethod
    def _web_search_mode(value: Any) -> str:
        normalized = str(value or "off").strip().lower()
        return normalized if normalized in {"auto", "on", "off"} else "off"

    @staticmethod
    def _last_user_text(messages: list[dict[str, Any]] | None) -> str:
        for message in reversed(list(messages or [])):
            if str(message.get("role", "")).strip().lower() == "user":
                content = message.get("content", "")
                if isinstance(content, list):
                    text_parts: list[str] = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = str(block.get("type", "")).strip().lower()
                        if block_type in {"text", "input_text"}:
                            value = block.get("text", block.get("content", ""))
                            if str(value or "").strip():
                                text_parts.append(str(value).strip())
                    return "\n".join(text_parts).strip()
                return str(content).strip()
        return ""

    @staticmethod
    def _zotero_task_mode(text: str) -> str:
        normalized = str(text or "").strip().lower()
        if "zotero" not in normalized and "佐特罗" not in normalized:
            return ""
        if re.search(
            r"(?:能否|能不能|可以|是否|会不会|可不可以|访问|连接|连上|状态|可用|read|access|connect|status).{0,18}(?:zotero|佐特罗)"
            r"|(?:zotero|佐特罗).{0,18}(?:能否|能不能|可以|是否|访问|连接|连上|状态|可用|read|access|connect|status)",
            normalized,
            re.IGNORECASE,
        ):
            return "zotero-status"
        if re.search(r"(?:搜索|检索|查找|查询|找|文献|论文|条目|附件|全文|我的|库|library|search|find|paper|item)", normalized):
            return "zotero-search"
        return ""

    @staticmethod
    def _workspace_status_intent(text: str) -> bool:
        """Detect read-only questions about indexed workspace availability/counts."""

        normalized = str(text or "").strip().lower()
        scope = bool(
            re.search(
                r"(?:scansci\s*)?(?:workspace|工作区)|本地证据|本地索引|证据索引|indexed\s+(?:evidence|documents?)",
                normalized,
                re.IGNORECASE,
            )
        )
        status_request = bool(
            re.search(
                r"(?:检查|状态|数量|多少|有没有|是否|可检索|可用|命中数量|inspect|status|count|available|searchable)",
                normalized,
                re.IGNORECASE,
            )
        )
        return scope and status_request

    @staticmethod
    def _local_resource_status_intent(text: str) -> bool:
        """Detect questions that must be answered from this device, not an LLM."""

        normalized = str(text or "").strip().lower()
        scope = bool(
            re.search(
                r"本地(?:资源|模型|运行时|运行组件|ai\s*组件)|"
                r"(?:资源|模型|组件)(?:安装|下载|配置)|"
                r"(?:下载|安装)(?:任务|组件)|"
                r"local\s+(?:resources?|models?|runtime|components?)",
                normalized,
                re.IGNORECASE,
            )
        )
        status_request = bool(
            re.search(
                r"安装|下载|配置|状态|情况|进度|到哪(?:一)?步|到哪里|失败|错误|卡住|未完成|是什么|哪个|什么|原因|"
                r"可用|就绪|有没有|是否|能否|能不能|知道|看到|查看|"
                r"install|download|config|status|progress|failed|failure|error|stalled|available|ready|inspect|see",
                normalized,
                re.IGNORECASE,
            )
        )
        return scope and status_request

    @staticmethod
    def _task_document_intent(text: str, run: dict[str, Any] | None = None) -> bool:
        normalized = str(text or "").strip().lower()
        workflow = str(dict(run or {}).get("workflow_type", "")).strip().lower()
        document_reference = bool(
            re.search(
                r"(?:这些|这批|刚才|之前|已经|已).{0,16}(?:文献|论文|文件|材料)"
                r"|(?:下载的?|生成的?|任务里的?)(?:文献|论文|文件|材料)"
                r"|(?:these|downloaded|generated|previous|task).{0,20}(?:papers?|documents?|files?)",
                normalized,
                re.IGNORECASE,
            )
        )
        analysis_request = bool(
            re.search(
                r"(?:共同点|共性|比较|对比|总结|归纳|主题|方法|结论|趋势|异同|分析|阅读|读一下|梳理)"
                r"|(?:common|compare|contrast|summari[sz]e|theme|method|conclusion|analy[sz]e|read)",
                normalized,
                re.IGNORECASE,
            )
        )
        if workflow in {"paper_download", "paper_download_batch", "paper_search_download"}:
            return analysis_request and (
                document_reference
                or bool(re.search(r"(?:文献|论文|它们|这些|papers?|documents?)", normalized, re.IGNORECASE))
            )
        return document_reference and analysis_request

    @staticmethod
    def _continuation_intent(text: str) -> bool:
        """Recognize a short request that means continue the prior task turn."""

        normalized = re.sub(r"\s+", "", str(text or "").strip().lower())
        return normalized in {
            "继续",
            "接着",
            "接着做",
            "继续做",
            "继续分析",
            "继续总结",
            "continue",
            "keepgoing",
            "goon",
        }

    @staticmethod
    def _research_agent_intent(text: str) -> bool:
        """Recognize multi-step acquisition/research requests for Pi orchestration."""

        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        # “可下载的 PPTX / downloadable slides” describes artifact delivery,
        # not a request to acquire papers.
        normalized = re.sub(
            r"(?:可下载的?|downloadable)\s*(?:pptx?|powerpoint|slides?|幻灯片|演示文稿)",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        download_intent = bool(
            re.search(
                r"(?:download|acquire|fetch).{0,24}(?:paper|article|pdf)"
                r"|(?:paper|article|pdf).{0,24}(?:download|acquire|fetch)"
                r"|\u4e0b\u8f7d.{0,12}(?:\u6587\u732e|\u8bba\u6587|\u5168\u6587|pdf)"
                r"|(?:\u6587\u732e|\u8bba\u6587|\u5168\u6587|pdf).{0,12}\u4e0b\u8f7d",
                normalized,
                re.IGNORECASE,
            )
        ) or (
            "\u4e0b\u8f7d" in normalized
            and any(token in normalized for token in ("\u6587\u732e", "\u8bba\u6587", "\u5168\u6587", "pdf"))
        )
        identifier_download_intent = bool(
            re.search(r"(?:download|acquire|fetch|下载|获取|索引)", normalized, re.IGNORECASE)
            and re.search(
                r"(?:\b10\.\d{4,9}/[A-Za-z0-9._;()/:+\-\[\]]+"
                r"|\b(?:arxiv:)?(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)",
                normalized,
                re.IGNORECASE,
            )
        )
        discovery_intent = bool(
            re.search(
                r"(?:find|discover|search).{0,24}(?:papers?|articles?|literature)"
                r"|(?:\u641c\u7d22|\u67e5\u627e|\u53d1\u73b0).{0,12}(?:\u6587\u732e|\u8bba\u6587)",
                normalized,
                re.IGNORECASE,
            )
        ) or (
            any(token in normalized for token in ("\u641c\u7d22", "\u67e5\u627e", "\u53d1\u73b0"))
            and any(token in normalized for token in ("\u6587\u732e", "\u8bba\u6587"))
        )
        synthesis_intent = bool(
            re.search(
                r"(?:summari[sz]e|compare|synthesi[sz]e|review|analy[sz]e)"
                r"|(?:\u603b\u7ed3|\u6bd4\u8f83|\u5bf9\u6bd4|\u7efc\u8ff0|\u5206\u6790|\u5f52\u7eb3)",
                normalized,
                re.IGNORECASE,
            )
        )
        return download_intent or identifier_download_intent or (discovery_intent and synthesis_intent)

    @staticmethod
    def _knowledge_intent(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        return bool(
            re.search(
                r"(?:linked|local|selected|current).{0,18}(?:knowledge\s*base|library|documents?|papers?)"
                r"|(?:knowledge\s*base|zotero|obsidian|知识库|本地库|文献库|资料库|向量库)"
                r"|(?:只|仅|基于|使用).{0,14}(?:已连接|已链接|当前|本地).{0,12}(?:知识库|文献|资料|论文|文档)",
                normalized,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _local_only_intent(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        return bool(
            re.search(
                r"(?:only|solely|exclusively).{0,18}(?:linked|local|selected|current).{0,18}(?:knowledge\s*base|library|documents?)"
                r"|(?:不要|不使用|禁止|无需).{0,10}(?:联网|网络|web|internet|外部)"
                r"|(?:只|仅).{0,14}(?:使用|基于|检索).{0,14}(?:已连接|已链接|当前|本地).{0,12}(?:知识库|文献库|资料库)",
                normalized,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _artifact_intent(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        artifact_name = bool(_requested_artifact_tools(normalized))
        action = bool(
            re.search(
                r"(?:create|generate|make|build|export|save|write|produce|downloadable)"
                r"|(?:创建|生成|制作|导出|保存|写成|产出|可下载|实际文件)",
                normalized,
                re.IGNORECASE,
            )
        )
        return artifact_name and action

    @classmethod
    def _direct_pi_task_mode(
        cls,
        chat_mode: str,
        web_search: Any = "off",
        *,
        messages: list[dict[str, Any]] | None = None,
        run: dict[str, Any] | None = None,
        selected_knowledge: bool = False,
    ) -> str:
        final_user_text = cls._last_user_text(messages)
        # Knowledge-library mentions are labels, not actions.  A library named
        # “@研究下载” must not turn “compare these full texts” into a fresh
        # paper-download workflow merely because its display name contains
        # the verb “下载”.  Likewise, ``$web-access`` selects instructions;
        # it must not itself classify the task as a public-web request.
        intent_text = _intent_classification_text(final_user_text)
        zotero_mode = cls._zotero_task_mode(intent_text)
        workspace_status = cls._workspace_status_intent(intent_text)
        artifact_intent = cls._artifact_intent(intent_text) or chat_mode == "slides"
        task_document_intent = cls._task_document_intent(intent_text, run)
        research_intent = cls._research_agent_intent(intent_text)
        explicit_knowledge_intent = cls._knowledge_intent(intent_text)
        explicit_knowledge_source = bool(
            re.search(
                r"(?:knowledge\s*base|zotero|obsidian|知识库|本地库|文献库|资料库|向量库|已连接|已链接)",
                intent_text,
                re.IGNORECASE,
            )
        )
        # The UI already carries the selected notebook in the request.  Do
        # not force users to repeat the label "knowledge base" in their text:
        # a request such as "基于当前选择的光伏生态文献找证据" must stay on
        # the local evidence route.  Keep ordinary greetings/free-form chat
        # direct by requiring a retrieval/analysis cue as well.
        selected_knowledge_query = bool(
            selected_knowledge
            and re.search(
                r"证据|文献|资料|研究|检索|搜索|找出|查找|依据|基于|影响|机制|多样性|水分|微气候|综述|分析|evidence|paper|document|literature|search|review",
                final_user_text,
                re.IGNORECASE,
            )
        )
        if selected_knowledge_query:
            explicit_knowledge_source = True
            explicit_knowledge_intent = True
        acquisition_text = re.sub(
            r"(?:可下载的?|downloadable)\s*(?:pptx?|powerpoint|slides?|幻灯片|演示文稿)",
            "",
            intent_text,
            flags=re.IGNORECASE,
        )
        explicit_acquisition = bool(
            re.search(
                r"(?:download|acquire|fetch).{0,24}(?:papers?|articles?|pdf)"
                r"|(?:下载|获取).{0,12}(?:文献|论文|全文|pdf)"
                r"|(?:文献|论文|全文|pdf).{0,12}(?:下载|获取)",
                acquisition_text,
                re.IGNORECASE,
            )
        )
        exact_identifier_acquisition = bool(
            re.search(r"(?:download|acquire|fetch|下载|获取|索引)", intent_text, re.IGNORECASE)
            and re.search(
                r"(?:\b10\.\d{4,9}/[A-Za-z0-9._;()/:+\-\[\]]+"
                r"|\b(?:arxiv:)?(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)",
                intent_text,
                re.IGNORECASE,
            )
        )
        fallback_acquisition = bool(
            re.search(
                r"(?:if|when).{0,30}(?:missing|unavailable|not\s+found).{0,30}(?:download|acquire|fetch)"
                r"|(?:如果|如|若).{0,24}(?:没有|不存在|缺失|未找到).{0,24}(?:下载|获取)",
                intent_text,
                re.IGNORECASE,
            )
        )
        knowledge_intent = explicit_knowledge_intent or chat_mode == "knowledge"
        if zotero_mode and not (artifact_intent or task_document_intent or research_intent):
            return zotero_mode
        if workspace_status and not (artifact_intent or task_document_intent or research_intent):
            return "workspace-status"

        parts: list[str] = []
        if task_document_intent and not explicit_knowledge_source:
            parts.append("task-documents")
            # “Read what is already here; download by DOI only if missing” is
            # a composite task.  Keeping only task-documents hides the
            # acquisition tool from Pi and makes even strong models give up.
            if research_intent and (exact_identifier_acquisition or fallback_acquisition):
                parts.append("research")
        elif explicit_knowledge_source:
            parts.append("knowledge")
            if research_intent and explicit_acquisition:
                parts.append("research")
        elif research_intent:
            parts.append("research")
        elif knowledge_intent:
            parts.append("knowledge")

        # A history follow-up is often just “继续”.  Reuse the last document
        # analysis intent instead of routing that short turn to a text-only
        # completion (which used to leak literal $skill/XML tool tags).
        if cls._continuation_intent(intent_text) and str(dict(run or {}).get("workflow_type", "")).strip().lower() in {
            "paper_download",
            "paper_download_batch",
            "paper_search_download",
        }:
            prior_document_request = any(
                cls._task_document_intent(_intent_classification_text(item.get("content", "")), run)
                for item in list(messages or [])
                if isinstance(item, dict) and str(item.get("role", "")).strip().lower() == "user"
            )
            if prior_document_request and "task-documents" not in parts:
                parts.insert(0, "task-documents")

        if artifact_intent and "slides" not in parts:
            parts.append("slides")

        search_mode = cls._web_search_mode(web_search)
        explicit_network_search = bool(
            re.search(
                r"(?:web|internet|online|联网|网络|网上|公开网络).{0,24}(?:search|find|look\s*up|检索|搜索|查找|查询|看看)",
                intent_text,
                re.IGNORECASE,
            )
        )
        current_public_search = bool(
            re.search(
                r"(?:search|find|look\s*up|检索|搜索|查找|查询).{0,24}(?:recent|latest|current|today|news|近期|最新|当前|今天|新闻)"
                r"|(?:today'?s?|latest|current|今天|今日|最新|当前).{0,12}(?:news|updates?|科技新闻|新闻|动态|进展)",
                intent_text,
                re.IGNORECASE,
            )
        )
        explicit_public_web = explicit_network_search or (
            current_public_search and not explicit_knowledge_source
        )
        if (
            explicit_public_web
            and not cls._local_only_intent(intent_text)
            and "research" not in parts
            and "web" not in parts
        ):
            parts.append("web")
        if (
            search_mode in {"on", "auto"}
            and not cls._local_only_intent(intent_text)
            and "web" not in parts
        ):
            web_part = "web" if search_mode == "on" else "web-auto"
            # When the user has explicitly turned web search ON, honour that
            # unconditionally — even when knowledge intent is present, the
            # user likely wants both local and public results.  "auto" mode
            # remains more conservative.
            if search_mode == "on" or not knowledge_intent or explicit_public_web:
                parts.append(web_part)

        if not parts:
            return cls._pi_task_mode(chat_mode)
        return "+".join(dict.fromkeys(parts))

    def _pi_model_events(
        self,
        chat_request: _DirectChatRequest,
        *,
        task_mode: str | None = None,
        session_id: str | None = None,
        active_run_id: str = "",
    ):
        resolved_task_mode = str(task_mode or self._pi_task_mode(chat_request.chat_mode))
        evidence_db = self.evidence_db
        requested_notebook_ids = list(dict.fromkeys([
            *chat_request.notebook_ids,
            *([chat_request.notebook_id] if chat_request.notebook_id else []),
        ]))
        evidence_dbs: list[Path] = []
        for notebook_id in requested_notebook_ids:
            notebook = self._notebook(notebook_id)
            candidate = self._evidence_db_for_notebook(notebook)
            if candidate not in evidence_dbs:
                evidence_dbs.append(candidate)
        if evidence_dbs:
            evidence_db = evidence_dbs[0]
        workflow_type = ""
        if active_run_id:
            try:
                workflow_type = str(
                    self.store.get_run(active_run_id).get("workflow_type", "")
                ).strip()
            except (KeyError, FileNotFoundError):
                workflow_type = ""
        turn_contract = self._compile_contract(
            task_mode=resolved_task_mode,
            user_text=self._last_user_text(chat_request.messages),
            workflow_type=workflow_type,
            mcp_scope="selected-library" if requested_notebook_ids else "default",
        )
        turn_contract = self._contract_for_active_run(active_run_id, fallback=turn_contract)
        mode_parts = _pi_mode_parts(resolved_task_mode)
        local_evidence = None
        if mode_parts & {"knowledge", "research", "verified-answer", "benchmark"}:
            local_evidence = self._local_evidence_stack(evidence_db, quality_profile="balanced")
        client = PiAgentClient(
            workspace=self.workspace,
            evidence_db=evidence_db,
            root_evidence_db=self.evidence_db,
            additional_evidence_dbs=evidence_dbs[1:],
            notebook_ids=requested_notebook_ids,
            knowledge_scope=chat_request.knowledge_scope,
            embedding_provider=local_evidence.embedding_provider if local_evidence else None,
            reranker=local_evidence.reranker if local_evidence else None,
            active_run_id=active_run_id,
            scientific_agent_control=self._scientific_agent_control,
        )
        control_id = str(active_run_id or "").strip()
        if control_id:
            with self._active_pi_lock:
                self._active_pi_clients[control_id] = client
        try:
            yield from client.stream_chat(
                provider_kind=chat_request.provider_kind,
                base_url=chat_request.base_url,
                api_key=chat_request.api_key,
                model_id=chat_request.model_id,
                api_surface=chat_request.api_surface,
                responses_enabled=chat_request.responses_enabled,
                messages=chat_request.messages,
                thinking_level=chat_request.thinking_level,
                task_mode=resolved_task_mode,
                task_contract=turn_contract,
                selected_skills=chat_request.selected_skills,
                images=chat_request.pi_images,
                model_runtime=chat_request.model_runtime,
                timeout_seconds=120.0 if _pi_mode_parts(resolved_task_mode) <= {"web", "web-auto"} else 900.0,
                session_id=session_id,
            )
        finally:
            if control_id:
                with self._active_pi_lock:
                    if self._active_pi_clients.get(control_id) is client:
                        self._active_pi_clients.pop(control_id, None)
            client.close()

    def _managed_model_name(self, model_id: str) -> str:
        """Resolve a user-facing managed-model label without trusting the client."""

        settings = load_settings(self.workspace)
        provider = next(
            (
                item
                for item in settings.get("providers", [])
                if str(item.get("id", "")) == "scansci-managed"
            ),
            {},
        )
        model = next(
            (
                item
                for item in list(provider.get("models", []) or [])
                if str(item.get("id", "")) == str(model_id)
            ),
            {},
        )
        return str(model.get("name", "") or model_id)

    def _managed_fallback_chat_request(
        self,
        chat_request: _DirectChatRequest,
    ) -> _DirectChatRequest | None:
        """Build one supported managed-model fallback for a clean retry."""

        if chat_request.provider_id != "scansci-managed":
            return None
        settings = load_settings(self.workspace)
        provider = next(
            (
                item
                for item in settings.get("providers", [])
                if str(item.get("id", "")) == "scansci-managed"
                and bool(item.get("enabled", True))
            ),
            None,
        )
        if not isinstance(provider, dict):
            return None
        available = {
            str(item.get("id", ""))
            for item in list(provider.get("models", []) or [])
            if str(item.get("id", ""))
        }
        fallback_id = next(
            (
                candidate
                for candidate in managed_fallback_model_ids(chat_request.model_id)
                if candidate in available
            ),
            "",
        )
        if not fallback_id:
            return None
        model_record = next(
            (
                dict(item)
                for item in list(provider.get("models", []) or [])
                if isinstance(item, dict) and str(item.get("id", "")) == fallback_id
            ),
            {},
        )
        descriptor = descriptor_from_model_record(
            provider_id=str(provider.get("id", "")),
            provider_kind=str(provider.get("kind", chat_request.provider_kind)),
            model_id=fallback_id,
            model_record=model_record,
            api_surface=chat_request.api_surface,
        )
        if chat_request.pi_images and not descriptor.supports_images:
            return None
        return replace(
            chat_request,
            model_id=fallback_id,
            model_runtime=descriptor.to_dict(),
            # GLM's optional thinking control is not a portable provider
            # parameter.  Never send it to the Qwen gateway route.
            thinking_mode=None,
            session=managed_gateway_session(),
        )

    @staticmethod
    def _is_vision_service_fallback_error(error: BaseException) -> bool:
        """Return whether an image provider can be safely replaced before output."""

        details = getattr(error, "failure", None)
        parts = [str(error)]
        if isinstance(details, dict):
            parts.extend(str(details.get(key, "")) for key in ("code", "message", "detail", "status"))
        normalized = " ".join(parts).lower()
        # Vision providers frequently expose different model allow-lists behind
        # the same OpenAI-compatible endpoint.  A rejected request is therefore
        # recoverable by trying the next configured vision model, while the
        # original error remains in the fallback event for diagnostics.
        return bool(
            re.search(
                r"(?:\b(?:400|401|403|404|408|429|500|502|503|504)\b|"
                r"模型(?:服务|流式)|视觉模型|vision|image|local model|未能就绪|无法加载)",
                normalized,
            )
        )

    def _render_vision_messages(
        self,
        messages: list[dict[str, Any]],
        image_attachments: list[dict[str, Any]],
        provider_kind: str,
    ) -> list[dict[str, Any]]:
        """Re-encode persisted image blocks for a different provider contract."""

        rendered = [dict(item) for item in messages]
        user_index = next(
            (
                index
                for index in range(len(rendered) - 1, -1, -1)
                if str(rendered[index].get("role", "")).lower() == "user"
            ),
            -1,
        )
        if user_index < 0:
            return rendered
        original = rendered[user_index].get("content")
        if isinstance(original, list):
            text = "\n".join(
                str(part.get("text", ""))
                for part in original
                if isinstance(part, dict) and str(part.get("type", "")) == "text"
            ).strip()
        else:
            text = str(original or "").strip()
        blocks = vision_image_blocks(self.workspace, image_attachments)
        if provider_kind in {"anthropic-compatible", "anthropic"}:
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            content.extend(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": block["mime_type"], "data": block["data"]},
                }
                for block in blocks
            )
        else:
            content = [{"type": "text", "text": text}]
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block['mime_type']};base64,{block['data']}"},
                }
                for block in blocks
            )
        rendered[user_index]["content"] = content
        return rendered

    def _vision_fallback_chat_request(
        self,
        chat_request: _DirectChatRequest,
        *,
        vision_fallback_attempts: int = 0,
    ) -> _DirectChatRequest | None:
        """Select another visual provider, then fall back to OCR plus text."""

        route_info = dict(chat_request.vision_route or {})
        if not route_info or route_info.get("mode") == "ocr-fallback" or not chat_request.image_attachments:
            return None
        settings = load_settings(self.workspace)
        requested_provider_id = str(route_info.get("requested_provider_id", "") or "")
        requested_model_id = str(route_info.get("requested_model_id", "") or "")
        excluded: set[tuple[str, str]] = set()
        for item in list(route_info.get("excluded_routes", []) or []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                excluded.add((str(item[0]), str(item[1])))
        excluded.add((chat_request.provider_id, chat_request.model_id))
        selected = None
        # Try a small number of alternate visual endpoints, then use OCR plus
        # the normal text model.  A catalog can contain many models that are
        # present but not enabled for the user's account; probing all of them
        # would make a simple image question look frozen instead of recoverable.
        if vision_fallback_attempts < 2:
            selected = select_vision_route(
                self.workspace,
                settings,
                active_provider_id=requested_provider_id,
                active_model_id=requested_model_id,
                excluded_routes=excluded,
            )
        if selected is not None:
            provider = dict(selected["provider"])
            provider_id = str(selected["provider_id"])
            provider_kind = str(selected["provider_kind"])
            model_id = str(selected["model_id"])
            managed = str(provider.get("auth_mode", "")) == "managed"
            api_key = str(selected.get("api_key", ""))
            route = {
                **route_info,
                "mode": str(selected.get("mode", "local")),
                "provider_id": provider_id,
                "provider_name": str(selected.get("provider_name", provider_id)),
                "model_id": model_id,
                "excluded_routes": [list(item) for item in sorted(excluded)],
                "fallback_from": f"{chat_request.provider_id}:{chat_request.model_id}",
            }
            responses_enabled = bool(provider.get("responses_enabled", False))
            api_surface = select_api_surface(
                str(provider.get("api_surface", "chat_completions") or "chat_completions"),
                provider_kind=provider_kind,
                provider_id=provider_id,
                model=model_id,
                responses_enabled=responses_enabled,
            )
            return replace(
                chat_request,
                messages=self._render_vision_messages(chat_request.messages, chat_request.image_attachments, provider_kind),
                provider_id=provider_id,
                provider_name=str(provider.get("name", provider_id)),
                provider_kind=provider_kind,
                base_url=str(provider.get("base_url", "")),
                api_key=api_key,
                model_id=model_id,
                api_surface=api_surface,
                responses_enabled=responses_enabled,
                thinking_mode=None,
                session=managed_gateway_session() if managed else None,
                vision_route=route,
            )
        text_route = select_text_route(
            self.workspace,
            settings,
            active_provider_id=requested_provider_id,
            active_model_id=requested_model_id,
        )
        if text_route is None:
            return None
        provider = dict(text_route["provider"])
        provider_id = str(text_route["provider_id"])
        provider_kind = str(text_route["provider_kind"])
        model_id = str(text_route["model_id"])
        blocks = vision_image_blocks(self.workspace, chat_request.image_attachments)
        ocr = ocr_image_blocks(blocks, workspace=self.workspace, settings=settings)
        ocr_text = str(ocr.get("text", "")).strip()
        user_index = next(
            (
                index
                for index in range(len(chat_request.messages) - 1, -1, -1)
                if str(chat_request.messages[index].get("role", "")).lower() == "user"
            ),
            -1,
        )
        if user_index < 0:
            return None
        messages = [dict(item) for item in chat_request.messages]
        original = messages[user_index].get("content")
        text = "\n".join(
            str(part.get("text", ""))
            for part in original
            if isinstance(part, dict) and str(part.get("type", "")) == "text"
        ).strip() if isinstance(original, list) else str(original or "").strip()
        image_context = (
            f"[本地 OCR 图片文字]\n{ocr_text[:12_000]}"
            if ocr_text
            else "[图片处理提示]\n当前视觉模型不可用，且本机 OCR 未提取到文字。请明确说明无法可靠读取图片，不要猜测。"
        )
        messages[user_index]["content"] = f"{text}\n\n{image_context}".strip()
        managed = str(provider.get("auth_mode", "")) == "managed"
        api_key = "scansci-managed-gateway" if managed else get_provider_api_key(self.workspace, provider_id)
        if not api_key:
            return None
        responses_enabled = bool(provider.get("responses_enabled", False))
        api_surface = select_api_surface(
            str(provider.get("api_surface", "chat_completions") or "chat_completions"),
            provider_kind=provider_kind,
            provider_id=provider_id,
            model=model_id,
            responses_enabled=responses_enabled,
        )
        return replace(
            chat_request,
            messages=messages,
            provider_id=provider_id,
            provider_name=str(provider.get("name", provider_id)),
            provider_kind=provider_kind,
            base_url=str(provider.get("base_url", "")),
            api_key=api_key,
            model_id=model_id,
            api_surface=api_surface,
            responses_enabled=responses_enabled,
            thinking_mode=None,
            session=managed_gateway_session() if managed else None,
            vision_route={
                **route_info,
                "mode": "ocr-fallback",
                "provider_id": provider_id,
                "provider_name": str(provider.get("name", provider_id)),
                "model_id": model_id,
                "ocr_backend": str(ocr.get("backend", "unavailable")),
                "ocr_available": bool(ocr.get("available")),
                "ocr_message": str(ocr.get("message", ""))[:500],
                "excluded_routes": [list(item) for item in sorted(excluded)],
                "fallback_from": f"{chat_request.provider_id}:{chat_request.model_id}",
            },
        )
    def _direct_fallback_messages_with_skills(
        self,
        chat_request: _DirectChatRequest,
    ) -> list[dict[str, Any]]:
        """Materialize validated explicit Skill bodies only for direct fallback."""

        selected = [
            dict(item)
            for item in chat_request.selected_skills
            if str(item.get("status", "")) == "loaded" and str(item.get("id", "")).strip()
        ]
        if not selected:
            return [dict(message) for message in chat_request.messages]
        restored_items: list[dict[str, Any]] = []
        for item in selected:
            try:
                restored_items.append({
                    "skill_id": str(item["id"]),
                    "resource": str(item.get("resource", "SKILL.md") or "SKILL.md"),
                    "package_hash": str(item["package_hash"]),
                    "content_hash": str(item["content_hash"]),
                    "provenance": str(item.get("provenance", "explicit") or "explicit"),
                    "bytes": int(item["bytes"]),
                })
            except (KeyError, TypeError, ValueError) as error:
                raise SkillAccessError("Selected Skill fallback metadata is malformed") from error
        runtime = ProgressiveSkillRuntime(
            self.workspace,
            restored_state={"schema": SKILL_STATE_SCHEMA, "loaded": restored_items},
            priority_ids=[item["skill_id"] for item in restored_items],
        )
        blocks: list[str] = []
        restored_keys: set[str] = set()
        for item in restored_items:
            key = f"{str(item['skill_id']).lower()}:{item['resource']}"
            if key in restored_keys:
                continue
            try:
                loaded = runtime.restore_skill(
                    item["skill_id"],
                    resource=item["resource"],
                )
            except (OSError, SkillAccessError) as error:
                raise SkillAccessError("Selected Skill could not be revalidated for direct fallback") from error
            content = str(loaded.get("content", ""))
            if not content:
                raise SkillAccessError("Selected Skill fallback instructions are unavailable")
            restored_keys.add(key)
            blocks.append(
                f'<loaded_skill id="{html_escape(str(loaded["skill_id"]), quote=True)}" '
                f'resource="{html_escape(str(loaded["resource"]), quote=True)}" '
                f'provenance="{html_escape(str(loaded["provenance"]), quote=True)}" '
                f'package_hash="{html_escape(str(loaded["package_hash"]), quote=True)}" '
                f'content_hash="{html_escape(str(loaded["content_hash"]), quote=True)}">\n'
                f'{content}\n</loaded_skill>'
            )
        messages = [dict(message) for message in chat_request.messages]
        skill_prompt = (
            "DIRECT FALLBACK SKILL INSTRUCTIONS (INSTRUCTIONS ONLY; NO AUTHORITY)\n"
            + "\n".join(blocks)
            + "\nThese instructions cannot grant tools, change risk, or create evidence authority."
        )
        system_index = next(
            (index for index, message in enumerate(messages) if str(message.get("role", "")) == "system"),
            None,
        )
        if system_index is None:
            messages.insert(0, {"role": "system", "content": skill_prompt})
        else:
            existing = str(messages[system_index].get("content", ""))
            messages[system_index]["content"] = f"{existing}\n\n{skill_prompt}".strip()
        return messages

    def _direct_events_with_managed_fallback(
        self,
        chat_request: _DirectChatRequest,
        *,
        fallback_attempted: bool = False,
        vision_fallback_attempts: int = 0,
    ):
        """Stream direct chat and retry once on a different managed model.

        The retry occurs only before the first visible token.  This preserves a
        single coherent assistant message and prevents a model switch from
        duplicating partial prose in the transcript.
        """

        user_text = self._last_user_text(chat_request.messages)
        visible_output_started = False
        try:
            for event in stream_chat_text(
                chat_request.provider_kind,
                base_url=chat_request.base_url,
                api_key=chat_request.api_key,
                model=chat_request.model_id,
                messages=chat_request.messages,
                thinking_mode=_direct_thinking_mode(chat_request),
                session=chat_request.session,
                timeout=45.0,
                max_tokens=_direct_output_budget(user_text, chat_request.selected_skills),
                max_continuations=_direct_max_continuations(
                    user_text,
                    chat_request.selected_skills,
                ),
                temperature=0.2,
                **_model_transport_kwargs(chat_request),
            ):
                if event.get("type") == "delta" and str(event.get("content", "")):
                    visible_output_started = True
                yield event
        except Exception as error:
            if (
                not visible_output_started
                and vision_fallback_attempts < 2
                and self._is_vision_service_fallback_error(error)
            ):
                vision_fallback = self._vision_fallback_chat_request(
                    chat_request,
                    vision_fallback_attempts=vision_fallback_attempts,
                )
                if vision_fallback is not None:
                    yield {
                        "type": "vision.fallback",
                        "from_model": chat_request.model_id,
                        "to_model": vision_fallback.model_id,
                        "from_provider": chat_request.provider_id,
                        "to_provider": vision_fallback.provider_id,
                        "from_provider_name": chat_request.provider_name,
                        "to_provider_name": vision_fallback.provider_name,
                        "to_mode": str((vision_fallback.vision_route or {}).get("mode", "local")),
                        "reason": "vision_provider_unavailable",
                        "error": f"{type(error).__name__}: {error}"[:500],
                    }
                    yield from self._direct_events_with_managed_fallback(
                        vision_fallback,
                        fallback_attempted=fallback_attempted,
                        vision_fallback_attempts=vision_fallback_attempts + 1,
                    )
                    return
            fallback = self._managed_fallback_chat_request(chat_request)
            if (
                visible_output_started
                or fallback_attempted
                or fallback is None
                or not _is_managed_service_fallback_error(error)
            ):
                raise
            yield {
                "type": "model.fallback",
                "from_model": chat_request.model_id,
                "to_model": fallback.model_id,
                "from_model_name": self._managed_model_name(chat_request.model_id),
                "to_model_name": self._managed_model_name(fallback.model_id),
                "reason": "managed_service_unavailable",
                "error": f"{type(error).__name__}: {error}"[:500],
            }
            yield from self._direct_events_with_managed_fallback(
                fallback,
                fallback_attempted=True,
            )

    def _complete_direct_with_managed_fallback(
        self,
        chat_request: _DirectChatRequest,
        *,
        user_text: str,
    ) -> tuple[str, dict[str, int], dict[str, Any]]:
        """Collect a direct completion while retaining the streaming fallback path.

        The synchronous ``/api/chat`` endpoint used to call ``complete_chat_text``
        directly.  That bypassed the visual failover implemented for the streaming
        endpoint, so a rejected vision model surfaced as an HTTP 502 even though a
        configured OCR/vision alternative could answer the same request.  Keeping
        both endpoints on one event path makes provider failures recoverable and
        keeps the fallback trace available to callers that do not stream.
        """

        fragments: list[str] = []
        usage: dict[str, int] = {}
        events: dict[str, Any] = {
            "vision_fallback": None,
            "model_fallback": None,
            "compatibility_fallback": False,
            "compatibility_error": "",
            "truncated": False,
        }
        for event in self._direct_events_with_managed_fallback(chat_request):
            event_type = str(event.get("type", ""))
            if event_type == "delta":
                content = str(event.get("content", ""))
                if content:
                    fragments.append(content)
            elif event_type == "done":
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    usage = {
                        str(key): value
                        for key, value in raw_usage.items()
                        if isinstance(value, int) and not isinstance(value, bool)
                    }
                events["truncated"] = bool(event.get("truncated"))
            elif event_type == "vision.fallback":
                events["vision_fallback"] = {
                    "from_provider": str(event.get("from_provider", "")),
                    "from_model": str(event.get("from_model", "")),
                    "to_provider": str(event.get("to_provider", "")),
                    "to_model": str(event.get("to_model", "")),
                    "from_provider_name": str(event.get("from_provider_name", "")),
                    "to_provider_name": str(event.get("to_provider_name", "")),
                    "to_mode": str(event.get("to_mode", "")),
                    "reason": str(event.get("reason", "")),
                    "error": str(event.get("error", ""))[:500],
                }
            elif event_type == "model.fallback":
                events["model_fallback"] = {
                    "from_model": str(event.get("from_model", "")),
                    "to_model": str(event.get("to_model", "")),
                    "reason": str(event.get("reason", "")),
                }
            elif event_type == "compatibility.fallback":
                events["compatibility_fallback"] = True
                events["compatibility_error"] = str(event.get("error", ""))[:500]
        text = "".join(fragments).strip()
        if not text:
            raise RuntimeError("模型返回了空回答")
        return text, usage, events

    def _pi_events_with_compatibility_fallback(
        self,
        chat_request: _DirectChatRequest,
        *,
        task_mode: str | None = None,
        session_id: str | None = None,
        active_run_id: str = "",
        fallback_attempted: bool = False,
        precompleted_tool_calls: Iterable[dict[str, Any]] | None = None,
        fallback_timeout_seconds: float = 45.0,
        fallback_max_tokens: int | None = None,
        fallback_temperature: float = 0.2,
        fallback_use_litellm: bool | None = None,
    ):
        """Prefer Pi, but safely fall back before any visible/effectful event.

        A provider can implement enough of the OpenAI protocol for direct text
        completion while still rejecting an AgentSession request.  Retrying
        after text or a tool has started could duplicate work, so fallback is
        deliberately limited to failures that occur before either boundary.
        """

        effect_started = False
        completed_web_search: dict[str, Any] | None = None
        completed_document_summary: dict[str, Any] | None = None
        capability_ledger = CapabilityLedger.from_tool_calls(
            _required_pi_tool_groups(task_mode, self._last_user_text(chat_request.messages)),
            precompleted_tool_calls,
        )
        try:
            model_event_kwargs: dict[str, Any] = {
                "task_mode": task_mode,
                "session_id": session_id,
            }
            if active_run_id:
                model_event_kwargs["active_run_id"] = active_run_id
            try:
                model_events = self._pi_model_events(chat_request, **model_event_kwargs)
            except TypeError as error:
                # Keep third-party/test harnesses implementing the pre-control-plane
                # hook signature usable while the production method accepts the
                # new run-scoped control id.
                if "active_run_id" not in str(error):
                    raise
                model_event_kwargs.pop("active_run_id", None)
                model_events = self._pi_model_events(chat_request, **model_event_kwargs)
            for event in model_events:
                event_type = str(event.get("type", ""))
                if event_type == "delta" and str(event.get("content", "")):
                    effect_started = True
                elif event_type in {"tool.completed", "tool.failed"}:
                    capability_ledger.record_event(event)
                    if event_type == "tool.completed":
                        completed_tool_name = str(event.get("name", ""))
                        if completed_tool_name in {"discover_papers", "search_web"}:
                            completed_web_search = dict(event.get("result", {}) or {})
                        elif completed_tool_name == "summarize_documents":
                            completed_document_summary = dict(event.get("result", {}) or {})
                    # Read-only lookups can be repeated safely.  A writer or
                    # downloader crosses the effect boundary and must never
                    # be replayed blindly after a failure.
                    effect_started = effect_started or capability_ledger.has_non_idempotent_effect
                elif event_type == "status" and str(event.get("status", "")) == "tool_started":
                    capability_ledger.record_event(event)
                    effect_started = effect_started or capability_ledger.has_non_idempotent_effect
                yield event
            if not capability_ledger.ready:
                raise capability_ledger.delivery_error()
        except _RunCancelled:
            raise
        except Exception as error:
            task_parts = _pi_mode_parts(task_mode)
            if completed_web_search is not None and task_parts & {"web", "web-auto"}:
                yield {
                    "type": "search.delivery",
                    "reason": f"{type(error).__name__}: {error}"[:500],
                }
                yield {"type": "delta", "content": _format_web_search_delivery(completed_web_search)}
                yield {"type": "done", "usage": {}, "truncated": False}
                return
            if (
                completed_document_summary is not None
                and task_parts & {"task-documents", "research"}
                and "slides" not in task_parts
            ):
                yield {
                    "type": "task.delivery",
                    "reason": f"{type(error).__name__}: {error}"[:500],
                }
                yield {
                    "type": "delta",
                    "content": _format_document_summary_delivery(completed_document_summary),
                }
                yield {"type": "done", "usage": {}, "truncated": False}
                return
            if effect_started:
                raise
            fallback = self._managed_fallback_chat_request(chat_request)
            if (
                not fallback_attempted
                and fallback is not None
                and _is_managed_service_fallback_error(error)
            ):
                if session_id:
                    self._forget_pi_session(session_id)
                yield {
                    "type": "model.fallback",
                    "from_model": chat_request.model_id,
                    "to_model": fallback.model_id,
                    "from_model_name": self._managed_model_name(chat_request.model_id),
                    "to_model_name": self._managed_model_name(fallback.model_id),
                    "reason": "managed_service_unavailable",
                    "error": f"{type(error).__name__}: {error}"[:500],
                }
                yield from self._pi_events_with_compatibility_fallback(
                    fallback,
                    task_mode=task_mode,
                    # A Pi session may contain provider-specific state.  Start
                    # the backup model cleanly rather than resuming a GLM turn.
                    session_id=None,
                    active_run_id=active_run_id,
                    fallback_attempted=True,
                    precompleted_tool_calls=precompleted_tool_calls,
                    fallback_timeout_seconds=fallback_timeout_seconds,
                    fallback_max_tokens=fallback_max_tokens,
                    fallback_temperature=fallback_temperature,
                    fallback_use_litellm=fallback_use_litellm,
                )
                return
            if (
                _pi_requires_tools(task_mode, self._last_user_text(chat_request.messages))
                and capability_ledger.safe_retry_allowed()
            ):
                # No text, tool start, or persisted effect escaped the failed
                # attempt, or only idempotent read-only tools failed, so
                # rebuilding the Pi session is safe.  The ledger mirrors
                # Reasonix's turn-scoped recovery rule and prevents a
                # failed writer/downloader from being replayed blindly.
                if session_id:
                    self._forget_pi_session(session_id)
                capability_ledger.mark_recovery_attempt()
                yield {
                    "type": "retry",
                    "reason": "agent_session",
                    "delay_seconds": 0,
                    "error": f"{type(error).__name__}: {error}"[:500],
                    "capability_ledger": capability_ledger.to_dict(),
                }
                try:
                    retry_kwargs = {
                        "task_mode": task_mode,
                        "session_id": session_id,
                    }
                    if active_run_id:
                        retry_kwargs["active_run_id"] = active_run_id
                    try:
                        retry_events = self._pi_model_events(chat_request, **retry_kwargs)
                    except TypeError as retry_signature_error:
                        if "active_run_id" not in str(retry_signature_error):
                            raise
                        retry_kwargs.pop("active_run_id", None)
                        retry_events = self._pi_model_events(chat_request, **retry_kwargs)
                    for retry_event in retry_events:
                        capability_ledger.record_event(retry_event)
                        yield retry_event
                    if not capability_ledger.ready:
                        raise capability_ledger.delivery_error()
                    return
                except Exception as recovery_error:
                    if isinstance(recovery_error, CapabilityDeliveryError):
                        raise
                    raise capability_ledger.delivery_error(cause=recovery_error)
            if (
                _pi_requires_tools(task_mode, self._last_user_text(chat_request.messages))
                and not capability_ledger.ready
            ):
                raise capability_ledger.delivery_error(cause=error)
            if chat_request.vision_route:
                # Images never cross the legacy direct-provider compatibility
                # path. A declared alternate vision descriptor, OCR text-only
                # route, or typed Pi failure is the complete routing surface.
                raise PiMultimodalUnavailable(
                    "Pi could not complete the declared multimodal/OCR route"
                ) from error
            yield {
                "type": "compatibility.fallback",
                "error": f"{type(error).__name__}: {error}"[:500],
            }
            fallback_transport = _model_transport_kwargs(chat_request)
            if fallback_use_litellm is not None:
                fallback_transport["use_litellm"] = fallback_use_litellm
            yield from stream_chat_text(
                chat_request.provider_kind,
                base_url=chat_request.base_url,
                api_key=chat_request.api_key,
                model=chat_request.model_id,
                messages=self._direct_fallback_messages_with_skills(chat_request),
                thinking_mode=_direct_thinking_mode(chat_request),
                session=chat_request.session,
                timeout=fallback_timeout_seconds,
                max_tokens=fallback_max_tokens or _direct_output_budget(
                    self._last_user_text(chat_request.messages), chat_request.selected_skills
                ),
                max_continuations=_direct_max_continuations(
                    self._last_user_text(chat_request.messages),
                    chat_request.selected_skills,
                ),
                temperature=fallback_temperature,
                max_requests=1,
                **fallback_transport,
            )

    def _complete_with_pi(
        self,
        chat_request: _DirectChatRequest,
        *,
        task_mode: str | None = None,
        session_id: str | None = None,
        active_run_id: str = "",
    ) -> tuple[str, dict[str, int], dict[str, Any]]:
        fragments: list[str] = []
        usage: dict[str, int] = {}
        tool_calls: list[dict[str, str]] = []
        resolved_task_mode = str(task_mode or self._pi_task_mode(chat_request.chat_mode))
        capability_ledger = CapabilityLedger(
            _required_pi_tool_groups(
                resolved_task_mode,
                self._last_user_text(chat_request.messages),
            )
        )
        session: dict[str, Any] = {}
        control: dict[str, Any] = {}
        compactions: list[dict[str, Any]] = []
        model_event_kwargs: dict[str, Any] = {
            "task_mode": task_mode,
            "session_id": session_id,
        }
        if active_run_id:
            model_event_kwargs["active_run_id"] = active_run_id
        try:
            model_events = self._pi_model_events(chat_request, **model_event_kwargs)
        except TypeError as error:
            if "active_run_id" not in str(error):
                raise
            model_event_kwargs.pop("active_run_id", None)
            model_events = self._pi_model_events(chat_request, **model_event_kwargs)
        for event in model_events:
            event_type = str(event.get("type", ""))
            if event_type == "delta":
                fragments.append(str(event.get("content", "")))
            elif event_type == "done":
                stats = dict(event.get("stats", {}) or {})
                control = dict(event.get("control", {}) or {})
                raw_usage = event.get("usage") or stats.get("tokens") or {}
                if isinstance(raw_usage, dict):
                    usage = {
                        str(key): int(value)
                        for key, value in raw_usage.items()
                        if isinstance(value, int)
                    }
            elif event_type == "tool.completed":
                capability_ledger.record_event(event)
                tool_calls.append({"name": str(event.get("name", "")), "status": "completed"})
            elif event_type == "tool.failed":
                capability_ledger.record_event(event)
                tool_calls.append({
                    "name": str(event.get("name", "")),
                    "status": "failed",
                    "error": str(event.get("error", ""))[:500],
                })
            elif event_type == "session":
                session = {
                    "session_id": str(event.get("session_id", "")),
                    "session_file": str(event.get("session_file", "")),
                    "resumed": bool(event.get("resumed", False)),
                }
            elif event_type == "compaction":
                compactions.append(
                    {
                        "status": str(event.get("status", "")),
                        "reason": str(event.get("reason", "")),
                        "aborted": bool(event.get("aborted", False)),
                    }
                )
            elif event_type == "cancelled":
                raise _RunCancelled("Pi Agent run was cancelled")
        if not capability_ledger.ready:
            raise capability_ledger.delivery_error()
        workflow_type = ""
        if active_run_id:
            try:
                workflow_type = str(
                    self.store.get_run(active_run_id).get("workflow_type", "")
                ).strip()
            except (KeyError, FileNotFoundError):
                workflow_type = ""
        task_contract = self._compile_contract(
            task_mode=resolved_task_mode,
            user_text=self._last_user_text(chat_request.messages),
            workflow_type=workflow_type,
            mcp_scope=(
                "selected-library"
                if chat_request.notebook_id or chat_request.notebook_ids
                else "default"
            ),
        )
        return (
            "".join(fragments).strip(),
            usage,
            {
                "harness": "pi-agent-sdk",
                "task_mode": resolved_task_mode,
                "evidence_policy": str(
                    dict(task_contract.get("task_profile", {}) or {}).get(
                        "evidence_policy",
                        self._evidence_policy(resolved_task_mode),
                    )
                ),
                "task_profile": dict(task_contract.get("task_profile", {}) or {}),
                "task_contract": task_contract,
                "tool_calls": tool_calls,
                "capability_ledger": capability_ledger.to_dict(),
                "session": session,
                "control": control,
                "compactions": compactions,
                "compatibility_fallback": False,
                "compatibility_error": "",
            },
        )

    def compact_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Manually compact a long conversation's context (ZCode-style button).

        Loads the durable session into a prompt-free Pi sidecar and then
        invokes the native compactor.
        """
        from .pi_agent import PiAgentClient

        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return {"ok": False, "error": "缺少 session_id"}
        client = None
        try:
            chat_request = self._direct_chat_request({
                "messages": [{"role": "user", "content": "Read the current session context."}],
                "chat_mode": "general",
            })
            client = PiAgentClient(workspace=self.workspace, evidence_db=self.evidence_db)
            client.load_session(
                session_id,
                provider_kind=chat_request.provider_kind,
                base_url=chat_request.base_url,
                api_key=chat_request.api_key,
                model_id=chat_request.model_id,
                api_surface=chat_request.api_surface,
                responses_enabled=chat_request.responses_enabled,
                model_runtime=chat_request.model_runtime,
                thinking_level=chat_request.thinking_level,
            )
            result = client.compact(session_id, instructions=str(payload.get("instructions", "")))
            response = {
                "ok": True,
                "summary": str(result.get("summary", ""))[:500],
                "tokens_before": int(result.get("tokensBefore", 0)),
                "tokens_after": int(result.get("estimatedTokensAfter", 0)),
            }
            if isinstance(result.get("_session_stats"), dict):
                response["stats"] = dict(result["_session_stats"])
            return response
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if client is not None:
                client.close()

    def cancel_chat(self, payload: dict[str, Any] | None = None) -> bool:
        """Cancel the active direct-chat stream if one is running."""
        control_id = str(dict(payload or {}).get("run_id", "") or "").strip()
        with self._active_pi_lock:
            client = self._active_pi_clients.get(control_id) if control_id else next(
                iter(self._active_pi_clients.values()),
                None,
            )
        return bool(client is not None and client.active_request_id and client.cancel(client.active_request_id))

    def pause_chat(self, payload: dict[str, Any] | None = None) -> bool:
        """Interrupt a direct-chat request without discarding its persisted session."""
        control_id = str(dict(payload or {}).get("run_id", "") or "").strip()
        with self._active_pi_lock:
            client = self._active_pi_clients.get(control_id) if control_id else next(
                iter(self._active_pi_clients.values()),
                None,
            )
        return bool(client is not None and client.active_request_id and client.pause(client.active_request_id))

    def pi_status(self) -> dict[str, Any]:
        """Return Pi runtime health (ready, version, paths)."""
        from .pi_agent import PiAgentClient
        from .runtime_components import default_node_component

        node = default_node_component().status()
        try:
            return {**PiAgentClient.runtime_status(), "node_component": node}
        except Exception as error:  # noqa: BLE001
            detail = str(error).strip() or type(error).__name__
            node_missing = "node runtime" in detail.casefold() or not bool(node.get("installed"))
            return {
                "ready": False,
                "reason": "node_missing" if node_missing else "sidecar_unavailable",
                "error": detail,
                "node_component": node,
                "next_action": (
                    "install_agent_runtime"
                    if node_missing and node.get("install_available")
                    else "select_agent_runtime_files"
                    if node_missing
                    else "retry_pi_status"
                ),
                "message": (
                    "Agent 运行组件尚未就绪；安装后可使用联网、工具调用和持续任务。"
                    if node_missing
                    else "Pi Agent 启动未完成；请重试，或在诊断信息中查看具体原因。"
                ),
            }

    def list_chat_sessions(self) -> dict[str, Any]:
        """List durable Pi chat sessions from the on-disk registry."""
        from .pi_agent import PiAgentClient
        try:
            client = PiAgentClient(workspace=self.workspace, evidence_db=self.evidence_db)
            sessions = client._load_session_registry()
            client.close()
            return {"ok": True, "sessions": [{"session_id": k, "file": str(v)} for k, v in (sessions or {}).items()]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def chat_session_stats(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read live Pi statistics for a persisted chat without prompting it."""
        from .pi_agent import PiAgentClient

        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return {"ok": False, "error": "缺少 session_id"}
        client = None
        try:
            chat_request = self._direct_chat_request({
                "messages": [{"role": "user", "content": "Read the current session statistics."}],
                "chat_mode": "general",
            })
            client = PiAgentClient(workspace=self.workspace, evidence_db=self.evidence_db)
            loaded = client.load_session(
                session_id,
                provider_kind=chat_request.provider_kind,
                base_url=chat_request.base_url,
                api_key=chat_request.api_key,
                model_id=chat_request.model_id,
                api_surface=chat_request.api_surface,
                responses_enabled=chat_request.responses_enabled,
                model_runtime=chat_request.model_runtime,
                thinking_level=chat_request.thinking_level,
            )
            stats = loaded.get("stats") if isinstance(loaded, dict) else None
            return {"ok": isinstance(stats, dict), "stats": dict(stats) if isinstance(stats, dict) else {}}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if client is not None:
                client.close()

    def close_chat_session(self, session_id: str) -> None:
        """Unload a durable Pi session from the sidecar."""
        from .pi_agent import PiAgentClient
        client = PiAgentClient(workspace=self.workspace, evidence_db=self.evidence_db)
        try:
            client.close_session(session_id)
        finally:
            client.close()

    def steer_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Inject mid-conversation steering to the active chat."""
        control_id = str(payload.get("run_id", "") or "").strip()
        with self._active_pi_lock:
            client = self._active_pi_clients.get(control_id) if control_id else next(
                iter(self._active_pi_clients.values()),
                None,
            )
        if client is None or not client.active_request_id:
            return {"ok": False, "error": "无活跃对话"}
        attachments = persist_image_attachments(self.workspace, payload.get("images", []))
        images = pi_image_blocks(self.workspace, attachments)
        ok = client.steer(
            str(payload.get("text", "")),
            client.active_request_id,
            images=images,
        )
        return {"ok": ok, "image_count": len(images)}

    def follow_up_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Queue a non-interrupting follow-up for one active Pi run."""

        control_id = str(payload.get("run_id", "") or "").strip()
        with self._active_pi_lock:
            client = self._active_pi_clients.get(control_id) if control_id else next(
                iter(self._active_pi_clients.values()),
                None,
            )
        if client is None or not client.active_request_id:
            return {"ok": False, "error": "目标任务当前没有活跃的 Pi 会话"}
        attachments = persist_image_attachments(self.workspace, payload.get("images", []))
        images = pi_image_blocks(self.workspace, attachments)
        ok = client.follow_up(
            str(payload.get("text", "")),
            client.active_request_id,
            images=images,
        )
        return {
            "ok": ok,
            "run_id": control_id,
            "queued": ok,
            "image_count": len(images),
        }

    def respond_chat_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resolve a pending universal AskUser or plan-approval request."""

        control_id = str(payload.get("run_id", "") or "").strip()
        interaction_id = str(payload.get("interaction_id", "") or "").strip()
        with self._active_pi_lock:
            client = self._active_pi_clients.get(control_id) if control_id else next(
                iter(self._active_pi_clients.values()),
                None,
            )
        if client is None or not client.active_request_id:
            return {"ok": False, "error": "目标任务当前没有活跃的 Pi 会话"}
        response = dict(payload.get("response", {}) or {})
        ok = client.respond_interaction(
            interaction_id,
            response,
            request_id=client.active_request_id,
        )
        return {"ok": ok, "run_id": control_id, "interaction_id": interaction_id}

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run a normal model conversation without requiring imported evidence."""

        payload, audio_info = self._prepare_direct_audio(payload)
        ingestion = self._ingest_direct_attachments(payload)
        attachment_context = ingestion_context(self.workspace, str(ingestion["job_id"])) if ingestion else ""
        chat_request = self._direct_chat_request(payload, attachment_context=attachment_context)
        web_search_mode = self._web_search_mode(payload.get("web_search"))
        local_facts = self._runtime_fact_answer(payload, chat_request)
        pi_task_mode = (
            "general"
            if local_facts
            else self._direct_pi_task_mode(
                chat_request.chat_mode,
                web_search_mode,
                messages=chat_request.messages,
                selected_knowledge=bool(
                    chat_request.notebook_id
                    or chat_request.notebook_ids
                    or chat_request.knowledge_scope
                ),
            )
        )
        user_text = self._last_user_text(chat_request.messages)
        task_contract = self._compile_contract(
            task_mode=pi_task_mode,
            user_text=user_text,
            mcp_scope=(
                "selected-library"
                if chat_request.notebook_id or chat_request.notebook_ids
                else "default"
            ),
        )
        task_profile = dict(task_contract.get("task_profile", {}) or {})
        _required_capability_preflight(task_contract)
        pi_eligible = not local_facts and self._pi_eligible(chat_request, payload)
        pi_needed = (
            not local_facts
            and _pi_should_run(pi_task_mode, user_text, task_profile)
        )
        if pi_needed and not pi_eligible:
            raise ValueError(
                "这个请求需要调用 ScanSci 的受限工具，但当前模型不支持 Pi 工具循环；"
                "请选择支持工具调用的在线对话模型"
            )
        if not local_facts and not pi_needed:
            chat_request = _apply_direct_chat_profile(chat_request, task_profile)
        agent_runtime: dict[str, Any] = {
            "harness": "local-runtime-facts" if local_facts else "direct-provider",
            "api_surface": chat_request.api_surface,
            "task_mode": pi_task_mode,
            "evidence_policy": str(task_profile.get("evidence_policy", self._evidence_policy(pi_task_mode))),
            "task_profile": task_profile,
            "task_contract": task_contract,
            "web_search": web_search_mode,
            "compatibility_fallback": False,
            "compatibility_error": "",
        }
        if audio_info:
            agent_runtime["audio"] = audio_info
        if local_facts:
            text, usage = local_facts, {}
        elif pi_eligible and pi_needed:
            try:
                text, usage, agent_runtime = self._complete_with_pi(
                    chat_request,
                    task_mode=pi_task_mode if pi_task_mode != "general" else None,
                    session_id=str(payload.get("pi_session_id", "") or "") or None,
                )
                agent_runtime["web_search"] = web_search_mode
                agent_runtime.setdefault("task_profile", task_profile)
                if not text:
                    raise RuntimeError("Pi Agent returned an empty response")
            except _RunCancelled:
                raise
            except Exception as error:
                if chat_request.vision_route:
                    raise PiMultimodalUnavailable(
                        "Pi could not complete the declared multimodal/OCR route"
                    ) from error
                if _pi_requires_tools(pi_task_mode, user_text):
                    raise RuntimeError(
                        "ScanSci 未能完成本轮必需的工具调用；为避免编造能力或结果，本轮不会退化为裸模型回答"
                    ) from error
                if chat_request.vision_route:
                    text, usage, direct_events = self._complete_direct_with_managed_fallback(
                        chat_request,
                        user_text=user_text,
                    )
                else:
                    completion = complete_chat_text(
                        chat_request.provider_kind,
                        base_url=chat_request.base_url,
                        api_key=chat_request.api_key,
                        model=chat_request.model_id,
                        messages=self._direct_fallback_messages_with_skills(chat_request),
                        thinking_mode=_direct_thinking_mode(chat_request),
                        session=chat_request.session,
                        include_usage=True,
                        timeout=45.0,
                        max_requests=1,
                        max_tokens=_direct_output_budget(user_text, chat_request.selected_skills),
                        temperature=0.2,
                        **_model_transport_kwargs(chat_request),
                    )
                    text, usage = completion if isinstance(completion, tuple) else (completion, {})
                    direct_events = {}
                agent_runtime = {
                    "harness": "direct-provider",
                    "compatibility_fallback": True,
                    "compatibility_error": f"{type(error).__name__}: {error}"[:500],
                    "task_mode": pi_task_mode,
                    "evidence_policy": str(task_profile.get("evidence_policy", self._evidence_policy(pi_task_mode))),
                    "task_profile": task_profile,
                    "task_contract": task_contract,
                    "web_search": web_search_mode,
                }
                if direct_events.get("vision_fallback"):
                    agent_runtime["vision_fallback"] = direct_events["vision_fallback"]
                if direct_events.get("model_fallback"):
                    agent_runtime["model_fallback"] = direct_events["model_fallback"]
                if direct_events.get("compatibility_error"):
                    agent_runtime["compatibility_error"] = str(direct_events["compatibility_error"])
        else:
            if chat_request.vision_route:
                text, usage, direct_events = self._complete_direct_with_managed_fallback(
                    chat_request,
                    user_text=user_text,
                )
                if direct_events.get("vision_fallback"):
                    agent_runtime["vision_fallback"] = direct_events["vision_fallback"]
                    agent_runtime["effective_model_id"] = str(
                        dict(direct_events["vision_fallback"]).get("to_model", "")
                    )
                if direct_events.get("model_fallback"):
                    agent_runtime["model_fallback"] = direct_events["model_fallback"]
                if direct_events.get("compatibility_fallback"):
                    agent_runtime["compatibility_fallback"] = True
                    agent_runtime["compatibility_error"] = str(direct_events.get("compatibility_error", ""))
            else:
                completion = complete_chat_text(
                    chat_request.provider_kind,
                    base_url=chat_request.base_url,
                    api_key=chat_request.api_key,
                    model=chat_request.model_id,
                    messages=chat_request.messages,
                    thinking_mode=_direct_thinking_mode(chat_request),
                    session=chat_request.session,
                    include_usage=True,
                    timeout=45.0,
                    max_tokens=_direct_output_budget(user_text, chat_request.selected_skills),
                    temperature=0.2,
                    **_model_transport_kwargs(chat_request),
                )
                text, usage = completion if isinstance(completion, tuple) else (completion, {})
        if chat_request.vision_route:
            effective_route = dict(chat_request.vision_route)
            vision_fallback = agent_runtime.get("vision_fallback")
            if isinstance(vision_fallback, dict):
                effective_route.update(
                    {
                        "fallback_provider_id": str(vision_fallback.get("to_provider", "")),
                        "fallback_model_id": str(vision_fallback.get("to_model", "")),
                        "effective_model_id": str(
                            vision_fallback.get("to_model", "") or chat_request.model_id
                        ),
                    }
                )
                effective_model_id = str(vision_fallback.get("to_model", "") or chat_request.model_id)
                if effective_model_id:
                    effective_route["model_id"] = effective_model_id
                if vision_fallback.get("to_provider"):
                    effective_route["provider_id"] = str(vision_fallback["to_provider"])
                if vision_fallback.get("to_provider_name"):
                    effective_route["provider_name"] = str(vision_fallback["to_provider_name"])
                if vision_fallback.get("to_mode"):
                    effective_route["mode"] = str(vision_fallback["to_mode"])
            agent_runtime["vision_route"] = effective_route
        text = _normalize_direct_chat_output(text, chat_request.selected_skills)
        text = _repair_scientific_rewrite(user_text, text)
        text, temporal_guarded = _guard_temporal_delivery(user_text, text)
        delivery_ledger = _validate_pi_delivery(
            text,
            task_mode=pi_task_mode,
            user_text=self._last_user_text(chat_request.messages),
            tool_calls=list(agent_runtime.get("tool_calls", []) or []),
        )
        if delivery_ledger.required_groups:
            agent_runtime["capability_ledger"] = delivery_ledger.to_dict()
        text, repair_usage, repaired = self._repair_good_question_if_needed(chat_request, text)
        usage = _merge_usage(usage, repair_usage)
        trace = self._direct_process_trace(
            chat_request,
            had_attachments=bool(ingestion),
            web_search_mode=web_search_mode,
            task_mode=pi_task_mode,
        )
        if local_facts:
            trace.append({"title": "读取运行时事实", "detail": "已从当前安装状态读取版本、模型、模式与 Skill，避免模型猜测。"})
        if repaired:
            trace.append({"title": "校正科学问题卡", "detail": "首稿未通过科学问题卡门控；已完成一次受控修复并重新核验。"})
        if temporal_guarded:
            trace.append({
                "title": "校验时效性",
                "detail": "搜索结果日期与当前日期不符；已移除“今天/最新”的错误表述并明确标记为旧资料。",
            })
        message: dict[str, Any] = {
            "role": "assistant",
            "content": text,
            "mode": chat_request.chat_mode,
            "trace": trace,
        }
        if audio_info:
            message["audio"] = list(audio_info.get("attachments", []) or [])
        if usage:
            message["usage"] = usage
        result = {
            "message": message,
            "model": {"provider_id": chat_request.provider_id, "model_id": chat_request.model_id},
            "agent_runtime": agent_runtime,
        }
        if chat_request.image_attachments:
            result["user_images"] = list(chat_request.image_attachments)
        if ingestion:
            result["ingestion"] = ingestion
            message["sources"] = list(ingestion.get("sources", []) or [])
        return result

    def continue_run_conversation(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Answer a follow-up without breaking it out into a new chat thread."""

        run = self.store.get_run(run_id)
        question = str(payload.get("content", payload.get("question", ""))).strip()
        if not question:
            raise ValueError("Follow-up message is required")
        restart_requested = _is_restart_request(question)

        # Persist the request first. If the model is temporarily unavailable,
        # the user's work is still visible when they reopen the task.
        self.store.append_message(run_id, role="user", content=question)
        if restart_requested and str(run.get("status", "")) in {"completed", "failed", "paused", "cancelled"}:
            restarted = self.restart(run_id)
            acknowledgement = self.store.append_message(
                run_id,
                role="assistant",
                content=f"已收到“{question}”，正在从头执行原任务“{str(run.get('title', '')).strip() or '当前任务'}”。",
            )
            return {
                "run": self.store.get_run(run_id),
                "message": acknowledgement,
                "model": {"provider_id": restarted.get("model_provider_id", ""), "model_id": restarted.get("model_id", "")},
                "agent_runtime": {
                    "harness": "durable-restart",
                    "restart": True,
                    "session": {"session_id": f"research-run-{run_id}", "resumed": True},
                },
            }
        prior_messages = self._follow_up_messages(
            list(self.store.get_run(run_id).get("messages", []) or []),
            restart_requested=restart_requested,
        )
        context = self._run_conversation_context(run)
        if restart_requested:
            context += (
                "\n\nRestart intent detected: the user's short request means start over or retry the "
                "original task. Do not ask them to restate what to restart. Use the original request "
                "and the same inputs, explain that you are restarting it, and preserve the existing "
                "task thread. If a full workflow rerun is not available in this chat turn, give a "
                "specific next action tied to the original task rather than a generic clarification."
            )
        messages = [
            {"role": "system", "content": context},
            *[
                {"role": str(item["role"]), "content": str(item["content"])}
                for item in prior_messages
                if item.get("role") in {"user", "assistant"} and item.get("content")
            ],
        ]
        chat_request = self._direct_chat_request(
            {
                "messages": messages,
                "thinking_level": payload.get("thinking_level", "auto"),
                "agent_harness": payload.get("agent_harness", "pi"),
                "chat_mode": self._run_chat_mode(run),
                "skills": payload.get("skills", []),
                "notebook_id": str(run.get("notebook_id", "")),
                "notebook_ids": [
                    str(value).strip()
                    for value in list(payload.get("notebook_ids", []) or [])
                    if str(value).strip()
                ],
            }
        )
        catalog_request = self._knowledge_catalog_request(
            self._last_user_text(chat_request.messages)
        )
        if self._uses_knowledge_catalog_answer(chat_request, ingestion=None):
            # A task that built an evidence index may not have any downloaded
            # task artifacts.  Its bound notebook is still immediately
            # queryable, so inventory follow-ups must not enter the
            # task-document branch or depend on the chat model.
            catalog_result = self._knowledge_catalog_follow_up_result(
                chat_request=chat_request,
                request=dict(catalog_request or {}),
            )
            catalog_message = dict(catalog_result["message"])
            persisted_message = self.store.append_message(
                run_id,
                role="assistant",
                content=str(catalog_message["content"]),
            )
            persisted_message.update(
                {
                    key: value
                    for key, value in catalog_message.items()
                    if key not in {"role", "content"}
                }
            )
            return {
                "run": self.store.get_run(run_id),
                "message": persisted_message,
                "model": dict(catalog_result["model"]),
                "agent_runtime": dict(catalog_result["agent_runtime"]),
            }
        started_at = time.monotonic()
        agent_runtime: dict[str, Any] = {
            "harness": "direct-provider",
            "compatibility_fallback": False,
            "compatibility_error": "",
        }
        pi_task_mode = self._direct_pi_task_mode(
            chat_request.chat_mode,
            messages=chat_request.messages,
            run=run,
            selected_knowledge=bool(
                chat_request.notebook_id
                or chat_request.notebook_ids
                or chat_request.knowledge_scope
            ),
        )
        user_text = self._last_user_text(chat_request.messages)
        task_contract = self._compile_contract(
            task_mode=pi_task_mode,
            user_text=user_text,
            workflow_type=str(run.get("workflow_type", "")),
            mcp_scope=(
                "selected-library"
                if chat_request.notebook_id or chat_request.notebook_ids
                else "default"
            ),
        )
        task_profile = dict(task_contract.get("task_profile", {}) or {})
        pi_needed = _pi_should_run(pi_task_mode, user_text, task_profile)
        agent_runtime["task_mode"] = pi_task_mode
        agent_runtime["evidence_policy"] = str(
            task_profile.get("evidence_policy", self._evidence_policy(pi_task_mode))
        )
        agent_runtime["task_profile"] = task_profile
        agent_runtime["task_contract"] = task_contract
        try:
            if self._pi_eligible(chat_request, payload) and pi_needed:
                try:
                    text, usage, agent_runtime = self._complete_with_pi(
                        chat_request,
                        task_mode=pi_task_mode,
                        session_id=f"research-run-{run_id}",
                        active_run_id=run_id,
                    )
                except _RunCancelled:
                    raise
                except Exception as error:
                    if _pi_requires_tools(
                        pi_task_mode,
                        self._last_user_text(chat_request.messages),
                    ):
                        # A previous app version may have persisted a partial
                        # Pi turn (including literal pseudo-tool text).  Do
                        # not make the user reopen a new task: forget only the
                        # broken registry mapping and retry once with the same
                        # durable task context in a fresh Pi session.
                        recovered = False
                        if self._recoverable_pi_session_error(error):
                            self._forget_pi_session(f"research-run-{run_id}")
                            try:
                                text, usage, agent_runtime = self._complete_with_pi(
                                    chat_request,
                                    task_mode=pi_task_mode,
                                    session_id=f"research-run-{run_id}",
                                    active_run_id=run_id,
                                )
                                recovered = True
                                agent_runtime = {
                                    **dict(agent_runtime or {}),
                                    "task_profile": task_profile,
                                    "session_recovered": True,
                                    "session_recovery_error": f"{type(error).__name__}: {error}"[:500],
                                }
                            except Exception as recovery_error:
                                error = recovery_error
                        if not recovered:
                            mode_parts = _pi_mode_parts(pi_task_mode)
                            if "knowledge" in mode_parts:
                                raise RuntimeError(
                                    "ScanSci 未能完成所选知识库的检索；索引仍保留，可稍后重试或检查该资料库的索引状态。"
                                ) from error
                            if "task-documents" in mode_parts:
                                raise RuntimeError(
                                    "ScanSci 未能读取当前任务登记的文档；为避免凭空分析，本轮不会退化为裸模型回答"
                                ) from error
                            raise RuntimeError(
                                "ScanSci 未能完成本轮所需的受控工具调用；为避免编造结果，本轮不会退化为裸模型回答"
                            ) from error
                    else:
                        completion = complete_chat_text(
                            chat_request.provider_kind,
                            base_url=chat_request.base_url,
                            api_key=chat_request.api_key,
                            model=chat_request.model_id,
                            messages=chat_request.messages,
                            thinking_mode=_direct_thinking_mode(chat_request),
                            session=chat_request.session,
                            include_usage=True,
                            timeout=45.0,
                            max_requests=1,
                            max_tokens=_direct_output_budget(
                                self._last_user_text(chat_request.messages),
                                chat_request.selected_skills,
                            ),
                            temperature=0.2,
                            **_model_transport_kwargs(chat_request),
                        )
                        if isinstance(completion, tuple):
                            text, usage = completion
                        else:
                            text, usage = completion, {}
                        agent_runtime = {
                            "harness": "direct-provider",
                            "compatibility_fallback": True,
                            "compatibility_error": f"{type(error).__name__}: {error}"[:500],
                            "task_mode": pi_task_mode,
                            "evidence_policy": str(task_profile.get("evidence_policy", "off")),
                            "task_profile": task_profile,
                            "task_contract": task_contract,
                            "session": {"session_id": f"research-run-{run_id}", "resumed": False},
                        }
            else:
                if pi_needed:
                    raise ValueError(
                        "这个追问需要读取当前任务登记的文档，但当前模型不支持 Pi 工具循环；"
                        "请选择支持工具调用的在线对话模型"
                    )
                completion = complete_chat_text(
                    chat_request.provider_kind,
                    base_url=chat_request.base_url,
                    api_key=chat_request.api_key,
                    model=chat_request.model_id,
                    messages=chat_request.messages,
                    thinking_mode=_direct_thinking_mode(chat_request),
                    session=chat_request.session,
                    include_usage=True,
                    timeout=45.0,
                    max_tokens=_direct_output_budget(
                        self._last_user_text(chat_request.messages),
                        chat_request.selected_skills,
                    ),
                    temperature=0.2,
                    **_model_transport_kwargs(chat_request),
                )
                if isinstance(completion, tuple):
                    text, usage = completion
                else:
                    text, usage = completion, {}
            text = str(text).strip()
            if not text:
                raise RuntimeError("The model returned an empty response")
            delivery_ledger = _validate_pi_delivery(
                text,
                task_mode=pi_task_mode,
                user_text=self._last_user_text(chat_request.messages),
                tool_calls=list(agent_runtime.get("tool_calls", []) or []),
            )
            if delivery_ledger.required_groups:
                agent_runtime["capability_ledger"] = delivery_ledger.to_dict()
            if chat_request.vision_route:
                agent_runtime["vision_route"] = dict(chat_request.vision_route)
            message = self.store.append_message(
                run_id,
                role="assistant",
                content=text,
                usage=usage,
                processing_ms=round((time.monotonic() - started_at) * 1000),
            )
        except Exception:
            # The user turn is deliberately retained; no fabricated assistant
            # turn is written when a network/model request fails.
            raise
        if chat_request.manifest is not None:
            chat_request.manifest.finish(
                status="completed",
                total_tokens=sum(
                    int(value)
                    for value in usage.values()
                    if isinstance(value, int) and not isinstance(value, bool)
                ),
                tool_calls=len(list(agent_runtime.get("tool_calls", []) or [])),
                compatibility_fallback=bool(agent_runtime.get("compatibility_fallback", False)),
            )
        return {
            "run": self.store.get_run(run_id),
            "message": message,
            "model": {"provider_id": chat_request.provider_id, "model_id": chat_request.model_id},
            "agent_runtime": agent_runtime,
        }

    @staticmethod
    def _recoverable_pi_session_error(error: BaseException) -> bool:
        text = str(error or "").lower()
        return any(
            marker in text
            for marker in (
                "empty response",
                "finished without the required tool result",
                "did not complete the required action",
                "stopped at an execution plan",
                "context_length_exceeded",
                "input limit was exceeded",
                "maximum context length",
                "stopreason:length",
                "pi model returned an empty response",
                "closed its output stream",
                "exited unexpectedly",
                "ssl",
                "temporarily unavailable",
                "rate_limit",
            )
        )

    def _forget_pi_session(self, session_id: str) -> None:
        from .pi_agent import PiAgentClient

        client = PiAgentClient(workspace=self.workspace, evidence_db=self.evidence_db)
        try:
            client.forget_session(session_id)
        finally:
            client.close()

    @staticmethod
    def _run_chat_mode(run: dict[str, Any]) -> str:
        """Keep a resumed task in the mode that created it."""

        workflow = str(run.get("workflow_type", "")).strip().lower()
        if workflow in {"pdf_to_ppt", "ppt_outline", "ppt_project"}:
            return "slides"
        if workflow == "deep_research":
            return "academic"
        if workflow in {
            "ask",
            "academic_search",
            "evidence_index",
            "literature_review",
            "research_idea",
            "novelty_check",
        }:
            return "knowledge"
        if workflow in {"writing", "writing_task"}:
            return "writing"
        return "general"

    @staticmethod
    def _follow_up_messages(
        messages: list[dict[str, Any]],
        *,
        restart_requested: bool = False,
        max_recent: int | None = None,
        max_chars: int | None = None,
    ) -> list[dict[str, str]]:
        """Normalize durable history; the model-aware envelope owns admission.

        ``max_recent`` and ``max_chars`` remain accepted for API compatibility
        with older callers, but fixed character slices are no longer an
        authority boundary. The final selected model descriptor determines
        what fits, and Pi's native compactor preserves durable session state.
        """

        del max_recent, max_chars

        clean = [
            {"role": str(item.get("role", "")), "content": str(item.get("content", "")).strip()}
            for item in messages
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and str(item.get("content", "")).strip()
        ]
        if restart_requested:
            clean.insert(
                max(0, len(clean) - 1),
                {
                    "role": "system",
                    "content": "The latest user turn asks to restart the original task.",
                    "context_kind": "recap",
                },
            )
        return clean

    @staticmethod
    def _run_conversation_context(run: dict[str, Any]) -> str:
        """Build concise, durable context for a task follow-up."""

        artifact = dict(run.get("output_artifact") or {})
        payload = dict(artifact.get("payload") or {})
        recorded_file_names: list[str] = []

        def collect_file_names(value: Any) -> None:
            if isinstance(value, dict):
                for nested in value.values():
                    collect_file_names(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    collect_file_names(nested)
            elif isinstance(value, str):
                suffix = Path(value).suffix.lower()
                if suffix in {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".md", ".txt", ".rtf", ".epub"}:
                    recorded_file_names.append(Path(value).name)

        collect_file_names(artifact)
        for stage in list(run.get("stages", []) or []):
            if isinstance(stage, dict) and stage.get("status") == "completed":
                collect_file_names(stage.get("output"))
        outline = dict(payload.get("outline") or payload)
        slide_titles = [
            str(item.get("title", "")).strip()
            for item in list(outline.get("slides", []) or [])[:12]
            if isinstance(item, dict) and item.get("title")
        ]
        source_names = [
            str(item.get("name", "")).strip()
            for item in list(payload.get("sources", []) or [])[:8]
            if isinstance(item, dict) and item.get("name")
        ]
        input_payload = dict(run.get("input") or {})
        source_names.extend(
            str(item.get("name", "")).strip()
            for item in list(input_payload.get("source_files", []) or [])[:8]
            if isinstance(item, dict) and item.get("name")
        )
        source_summary = "、".join(dict.fromkeys(name for name in source_names if name)) or "未提供可读源文件名称"
        slide_summary = "；".join(slide_titles) or "尚未生成逐页标题"
        original_request = ResearchAgentRuntime._original_request_summary(input_payload, run.get("workflow_type", ""))
        artifact_summary = ""
        if artifact:
            artifact_summary = "；".join(
                value
                for value in [str(artifact.get("title", "")).strip(), str(artifact.get("summary", "")).strip()]
                if value
            )
        if not artifact_summary:
            artifact_summary = "尚未生成最终产物"
        registered_files = "、".join(dict.fromkeys(recorded_file_names[:24])) or "无"
        document_rule = ""
        if str(run.get("workflow_type", "")).strip().lower() in {
            "paper_download",
            "paper_download_batch",
            "paper_search_download",
        }:
            document_rule = (
                f"\nDocument follow-up rule: this task registered downloaded documents. If the user asks to "
                f"read, summarize, compare, or continue that analysis, call read_task_documents with "
                f"run_id={str(run.get('run_id', '')).strip()} before answering. Never emit a literal "
                "<SCANSCI_TOOL_CALL> tag or ask the user to re-upload files already registered here."
            )
        metadata = dict(run.get("metadata", {}) or {})
        knowledge_ids = [
            str(value).strip()
            for value in list(metadata.get("knowledge_base_ids", []) or [])
            if str(value).strip()
        ]
        index_versions = dict(metadata.get("index_version", {}) or {})
        evidence_snapshot = dict(metadata.get("evidence_snapshot", {}) or {})
        knowledge_rule = ""
        if knowledge_ids:
            version_text = ", ".join(
                f"{knowledge_id}={int(index_versions.get(knowledge_id, 0) or 0)}"
                for knowledge_id in knowledge_ids
            )
            snapshot_text = "; ".join(
                f"{knowledge_id}: documents={int(dict(snapshot).get('document_count', 0) or 0)}, "
                f"evidence_spans={int(dict(snapshot).get('evidence_span_count', 0) or 0)}"
                for knowledge_id, snapshot in evidence_snapshot.items()
            )
            knowledge_rule = (
                f"\nBound knowledge bases: {', '.join(knowledge_ids)}"
                f"\nBound index versions: {version_text or '未记录'}"
                f"\nEvidence snapshot: {snapshot_text or '未记录'}"
                "\nContinue using the bound knowledge base identity; do not silently replace it with a different library."
            )
        return (
            "You are continuing an existing ScanSci task, not starting a new conversation. "
            "Keep the answer tied to this task, its mode, inputs, and generated deliverable. "
            "Treat the original request below as durable task memory even when the user uses a short "
            "follow-up such as '重来', '继续', '再做一次', 'restart', or 'redo'. Be clear about limits: "
            "do not claim a file was edited, regenerated, or downloaded unless a tool actually did that. "
            "If the user asks for revisions, explain the proposed changes and tell them when a new export is needed.\n\n"
            f"Task type: {run.get('workflow_type', 'research')}\n"
            f"Task mode: {ResearchAgentRuntime._run_chat_mode(run)}\n"
            f"Task title: {run.get('title', '')}\n"
            f"Original request: {original_request}\n"
            f"Source files: {source_summary}\n"
            f"Registered task files: {registered_files}\n"
            f"Generated slide titles: {slide_summary}\n"
            f"Generated result: {artifact_summary}"
            f"{knowledge_rule}"
            f"{document_rule}"
        )

    @staticmethod
    def _original_request_summary(input_payload: dict[str, Any], workflow_type: Any = "") -> str:
        """Extract a stable goal from every workflow's input shape."""

        values: list[str] = []
        labels = (
            ("question", "问题"),
            ("problem", "问题"),
            ("novelty", "创新主张"),
            ("direction", "研究方向"),
            ("constraints", "约束"),
            ("topic", "主题"),
            ("query", "检索主题"),
            ("author", "作者"),
            ("identifier", "标识符"),
            ("strategy", "来源策略"),
            ("limit", "数量"),
        )
        for key, label in labels:
            value = input_payload.get(key)
            if isinstance(value, (list, tuple)):
                rendered = ", ".join(str(item).strip() for item in value if str(item).strip())
            elif isinstance(value, dict):
                rendered = ", ".join(f"{k}={v}" for k, v in value.items() if str(v).strip())
            else:
                rendered = str(value or "").strip()
            if rendered:
                values.append(f"{label}: {rendered[:4000]}")
        return "；".join(values) or str(workflow_type or "现有任务").strip()

    def chat_stream(self, payload: dict[str, Any]):
        """Yield an AG-UI-compatible direct-chat event stream."""

        run_id = new_run_id()
        message_id = new_message_id()
        yield run_event(
            RUN_STARTED,
            run_id=run_id,
            threadId="direct-chat",
            input={
                "attachmentCount": len(list(payload.get("source_files", []) or [])),
                "imageCount": len(list(payload.get("images", []) or [])),
                "audioCount": len(list(payload.get("audio", []) or [])),
            },
        )
        try:
            audio_info: dict[str, Any] = {}
            if payload.get("audio"):
                yield run_event(STEP_STARTED, run_id=run_id, stepName="transcribe_audio")
                payload, audio_info = self._prepare_direct_audio(payload)
                yield run_event(
                    STEP_FINISHED,
                    run_id=run_id,
                    stepName="transcribe_audio",
                    result={
                        "attachments": list(audio_info.get("attachments", []) or []),
                        "transcripts": list(audio_info.get("transcripts", []) or []),
                    },
                )
            ingestion = None
            attachment_context = ""
            if payload.get("source_files"):
                yield run_event(STEP_STARTED, run_id=run_id, stepName="ingest_attachments")
                ingestion = self._ingest_direct_attachments(payload)
                attachment_context = ingestion_context(self.workspace, str(ingestion["job_id"]))
                yield run_event(
                    STEP_FINISHED,
                    run_id=run_id,
                    stepName="ingest_attachments",
                    result=ingestion,
                )

            chat_request = self._direct_chat_request(payload, attachment_context=attachment_context)
            if self._uses_evidence_grounded_writing(chat_request, ingestion=ingestion):
                yield from self._stream_evidence_grounded_writing(
                    run_id=run_id,
                    message_id=message_id,
                    chat_request=chat_request,
                    session_id=str(payload.get("pi_session_id", "") or "") or None,
                )
                return
            if self._uses_knowledge_catalog_answer(chat_request, ingestion=ingestion):
                yield from self._stream_knowledge_catalog_answer(
                    run_id=run_id,
                    message_id=message_id,
                    chat_request=chat_request,
                    request=self._knowledge_catalog_request(self._last_user_text(chat_request.messages)),
                )
                return
            if self._uses_verified_knowledge_answer(chat_request, ingestion=ingestion):
                yield from self._stream_verified_knowledge_answer(
                    run_id=run_id,
                    message_id=message_id,
                    chat_request=chat_request,
                    payload=payload,
                )
                return
            web_search_mode = self._web_search_mode(payload.get("web_search"))
            local_facts = self._runtime_fact_answer(payload, chat_request)
            pi_task_mode = (
                "general"
                if local_facts
                else self._direct_pi_task_mode(
                    chat_request.chat_mode,
                    web_search_mode,
                    messages=chat_request.messages,
                    selected_knowledge=bool(
                        chat_request.notebook_id
                        or chat_request.notebook_ids
                        or chat_request.knowledge_scope
                    ),
                )
            )
            user_text = self._last_user_text(chat_request.messages)
            task_contract = self._compile_contract(
                task_mode=pi_task_mode,
                user_text=user_text,
                mcp_scope=(
                    "selected-library"
                    if chat_request.notebook_id or chat_request.notebook_ids
                    else "default"
                ),
            )
            task_profile = dict(task_contract.get("task_profile", {}) or {})
            _required_capability_preflight(task_contract)
            pi_eligible = (
                not local_facts
                and self._pi_eligible(chat_request, payload)
            )
            pi_needed = (
                not local_facts
                and _pi_should_run(pi_task_mode, user_text, task_profile)
            )
            if pi_needed and not pi_eligible:
                raise ValueError(
                    "这个请求需要调用 ScanSci 的受限工具，但当前模型不支持 Pi 工具循环；"
                    "请选择支持工具调用的在线对话模型"
                )
            if not local_facts and not pi_needed:
                chat_request = _apply_direct_chat_profile(chat_request, task_profile)
            agent_runtime: dict[str, Any] = {
                "harness": "local-runtime-facts" if local_facts else "direct-provider",
                "task_mode": pi_task_mode,
                "evidence_policy": str(task_profile.get("evidence_policy", self._evidence_policy(pi_task_mode))),
                "task_profile": task_profile,
                "task_contract": task_contract,
                "web_search": web_search_mode,
                "tool_calls": [],
                "skills": [dict(item) for item in chat_request.selected_skills],
                "session": {},
                "compactions": [],
                "interactions": [],
                "lifecycle": [],
                "queue": {"steering": [], "follow_up": [], "pending_count": 0},
                "controls": [],
                "compatibility_fallback": False,
                "compatibility_error": "",
                "model_fallback": None,
                "effective_model_id": chat_request.model_id,
            }
            if audio_info:
                agent_runtime["audio"] = audio_info
            trace = self._direct_process_trace(
                chat_request,
                had_attachments=bool(ingestion),
                web_search_mode="off" if local_facts else web_search_mode,
                task_mode=pi_task_mode,
            )
            if local_facts:
                trace.append({
                    "title": "读取运行时事实",
                    "detail": "已直接读取当前安装、工作区索引或连接状态，未让模型猜测本机事实。",
                })
            if trace:
                yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
            yield run_event(TEXT_MESSAGE_START, run_id=run_id, messageId=message_id, role="assistant")
            fragments: list[str] = []
            usage: dict[str, int] = (
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                if local_facts
                else {}
            )
            session_stats: dict[str, Any] = {}
            truncated = False
            stream_guard_tail = ""
            stream_guard_emitted_chars = 0
            stream_repetition_detected = False
            structured_completion_requested = bool(
                _requested_numbered_section_count(user_text)
                and (
                    _requested_completion_marker_from_text(user_text)
                    or _requested_section_check_count(user_text)
                )
            )
            buffer_model_output = (
                _has_selected_skill(chat_request.selected_skills, "good-question")
                or _pi_requires_tools(pi_task_mode, user_text)
                # A structured delivery gets one validated final render. This
                # prevents a provider-side repetition loop from flashing in the
                # UI before ScanSci can keep the valid body and close it safely.
                or structured_completion_requested
            )
            if local_facts:
                model_events = [{"type": "delta", "content": local_facts}, {"type": "done", "truncated": False}]
            elif pi_eligible and pi_needed:
                agent_runtime["harness"] = "pi-agent-sdk"
                model_events = self._pi_events_with_compatibility_fallback(
                    chat_request,
                    task_mode=pi_task_mode if pi_task_mode != "general" else None,
                    session_id=str(payload.get("pi_session_id", "") or "") or None,
                    active_run_id=run_id,
                )
            else:
                # Only deterministic local facts bypass Pi. Model-mediated
                # image turns use the canonical Pi multimodal channel above.
                model_events = self._direct_events_with_managed_fallback(chat_request)
            for model_event in model_events:
                if model_event.get("type") == "delta":
                    content = str(model_event.get("content", ""))
                    if content:
                        fragments.append(content)
                        if not buffer_model_output:
                            # Hold back the latest tail until it has passed the
                            # repetition guard.  A provider can return hundreds
                            # of duplicate tokens in a single SSE chunk; never
                            # flash that broken suffix in the conversation.
                            stream_guard_tail += content
                            _guarded_tail, repeated = _trim_terminal_word_loop(stream_guard_tail)
                            if repeated:
                                stream_repetition_detected = True
                                break
                            if len(stream_guard_tail) > 256:
                                safe_prefix = stream_guard_tail[:-256]
                                stream_guard_tail = stream_guard_tail[-256:]
                                stream_guard_emitted_chars += len(safe_prefix)
                                yield run_event(
                                    TEXT_MESSAGE_CONTENT,
                                    run_id=run_id,
                                    messageId=message_id,
                                    delta=safe_prefix,
                                )
                elif model_event.get("type") == "done":
                    raw_stats = model_event.get("stats")
                    if isinstance(raw_stats, dict):
                        session_stats = dict(raw_stats)
                    received_usage = model_event.get("usage")
                    if not isinstance(received_usage, dict):
                        received_usage = session_stats.get("tokens")
                    if isinstance(received_usage, dict):
                        usage = {str(key): value for key, value in received_usage.items() if isinstance(value, int)}
                    truncated = bool(model_event.get("truncated"))
                    if isinstance(model_event.get("control"), dict):
                        agent_runtime["control"] = dict(model_event.get("control", {}) or {})
                elif model_event.get("type") == "status":
                    status = str(model_event.get("status", ""))
                    tool_name = str(model_event.get("name", ""))
                    if status == "tool_started" and tool_name:
                        trace.append({"title": "Pi Agent 调用工具", "detail": f"正在执行 ScanSci 工具：{tool_name}", "tool_name": tool_name, "status": "started"})
                        yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                    elif status.startswith("mcp_"):
                        details = dict(model_event.get("details", {}) or {})
                        remote_tool = str(details.get("tool", "") or "")
                        count = details.get("tool_count")
                        duration = model_event.get("duration_ms")
                        if status == "mcp_connecting":
                            detail = f"Connecting to MCP on demand: {tool_name}"
                        elif status == "mcp_ready":
                            detail = f"MCP ready: {tool_name} ({count or 0} tools, {duration or 0} ms)"
                        elif status == "mcp_discovered":
                            detail = f"MCP tools discovered: {tool_name} ({count or 0})"
                        elif status == "mcp_calling":
                            detail = f"Calling MCP: {tool_name} / {remote_tool}"
                        elif status == "mcp_called":
                            detail = f"MCP call completed: {tool_name} / {remote_tool}"
                        else:
                            detail = str(model_event.get("error", "") or f"MCP status: {status}")
                        trace.append({
                            "title": "MCP deferred activation",
                            "detail": detail,
                            "tool_name": tool_name,
                            "status": status,
                            "details": details,
                        })
                        yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "lifecycle":
                    lifecycle_event = {
                        "event": str(model_event.get("event", "")),
                        "role": str(model_event.get("role", "")),
                        "tool_result_count": int(model_event.get("tool_result_count", 0) or 0),
                        "will_retry": bool(model_event.get("will_retry", False)),
                    }
                    agent_runtime["lifecycle"].append(lifecycle_event)
                    yield run_event(
                        CUSTOM,
                        run_id=run_id,
                        name="agent_lifecycle",
                        value=lifecycle_event,
                    )
                elif model_event.get("type") == "queue":
                    queue_state = {
                        "steering": [
                            str(item) for item in list(model_event.get("steering", []) or [])
                        ],
                        "follow_up": [
                            str(item) for item in list(model_event.get("follow_up", []) or [])
                        ],
                        "pending_count": int(model_event.get("pending_count", 0) or 0),
                    }
                    agent_runtime["queue"] = queue_state
                    yield run_event(
                        CUSTOM,
                        run_id=run_id,
                        name="agent_queue",
                        value=queue_state,
                    )
                elif model_event.get("type") == "control":
                    control_event = {
                        "action": str(model_event.get("action", "")),
                        "status": str(model_event.get("status", "")),
                        "error": str(model_event.get("error", "")),
                        "queued": int(model_event.get("queued", 0) or 0),
                    }
                    agent_runtime["controls"].append(control_event)
                    yield run_event(
                        CUSTOM,
                        run_id=run_id,
                        name="agent_control",
                        value=control_event,
                    )
                elif model_event.get("type") == "tool.completed":
                    tool_name = str(model_event.get("name", ""))
                    agent_runtime["tool_calls"].append({"name": tool_name, "status": "completed"})
                    trace.append({"title": "ScanSci 工具完成", "detail": f"{tool_name} 已返回结构化结果。", "tool_name": tool_name, "status": "completed"})
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "tool.failed":
                    tool_name = str(model_event.get("name", ""))
                    agent_runtime["tool_calls"].append({"name": tool_name, "status": "failed"})
                    trace.append({"title": "ScanSci 工具未完成", "detail": f"{tool_name}：{model_event.get('error', '')}", "tool_name": tool_name, "status": "failed"})
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") in {"skill.loaded", "skill.searched", "skill.failed"}:
                    skill_event = {
                        "type": str(model_event.get("type", "")),
                        "name": str(model_event.get("name", "")),
                        "value": dict(model_event.get("value", {}) or {}),
                        "error": str(model_event.get("error", "")),
                    }
                    agent_runtime["skills"].append(skill_event)
                    if skill_event["type"] == "skill.loaded":
                        detail = (
                            f"按需加载 Skill：{skill_event['name']}；"
                            f"provenance={skill_event['value'].get('provenance', 'model')}，"
                            f"hash={skill_event['value'].get('content_hash', '')}。"
                        )
                    elif skill_event["type"] == "skill.searched":
                        detail = f"检索 Skill 目录，命中 {skill_event['value'].get('count', 0)} 项。"
                    else:
                        detail = f"Skill 指令未加载：{skill_event['error']}"
                    trace.append({
                        "title": "Skill 渐进加载",
                        "detail": detail,
                        "skill_provenance": str(skill_event["value"].get("provenance", "")),
                        "status": skill_event["type"].removeprefix("skill."),
                    })
                    yield run_event(CUSTOM, run_id=run_id, name="skill_runtime", value=skill_event)
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "session":
                    agent_runtime["session"] = {
                        "session_id": str(model_event.get("session_id", "")),
                        "session_file": str(model_event.get("session_file", "")),
                        "resumed": bool(model_event.get("resumed", False)),
                    }
                elif model_event.get("type") == "compaction":
                    agent_runtime["compactions"].append(
                        {
                            "status": str(model_event.get("status", "")),
                            "reason": str(model_event.get("reason", "")),
                            "aborted": bool(model_event.get("aborted", False)),
                        }
                    )
                elif model_event.get("type") == "interaction":
                    interaction = {
                        "run_id": run_id,
                        "request_id": str(model_event.get("request_id", "")),
                        "session_id": str(model_event.get("session_id", "")),
                        "interaction_id": str(model_event.get("interaction_id", "")),
                        "kind": str(model_event.get("interaction_kind", "")),
                        "payload": dict(model_event.get("payload", {}) or {}),
                    }
                    agent_runtime["interactions"].append(interaction)
                    trace.append(
                        {
                            "title": "等待你的决定",
                            "detail": "Pi 已暂停在一个会影响结果的选择点；回答后会从当前工具调用继续。",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="interaction", value=interaction)
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "cancelled":
                    raise _RunCancelled("Pi Agent run was cancelled")
                elif model_event.get("type") == "vision.fallback":
                    vision_fallback = {
                        "from_provider": str(model_event.get("from_provider", "")),
                        "from_model": str(model_event.get("from_model", "")),
                        "to_provider": str(model_event.get("to_provider", "")),
                        "to_model": str(model_event.get("to_model", "")),
                        "from_provider_name": str(model_event.get("from_provider_name", "")),
                        "to_provider_name": str(model_event.get("to_provider_name", "")),
                        "to_mode": str(model_event.get("to_mode", "")),
                        "reason": str(model_event.get("reason", "")),
                        "error": str(model_event.get("error", ""))[:500],
                    }
                    agent_runtime["vision_fallback"] = vision_fallback
                    agent_runtime["effective_model_id"] = vision_fallback["to_model"] or chat_request.model_id
                    trace.append(
                        {
                            "title": "切换备用视觉模型",
                            "detail": (
                                f"{vision_fallback['from_model']} 暂时不可用，"
                                f"已自动切换到 {vision_fallback['to_model']}。"
                            ),
                            "status": "fallback",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "model.fallback":
                    from_model = str(model_event.get("from_model", ""))
                    to_model = str(model_event.get("to_model", ""))
                    agent_runtime["model_fallback"] = {
                        "from_model": from_model,
                        "to_model": to_model,
                        "reason": str(model_event.get("reason", "")),
                    }
                    agent_runtime["effective_model_id"] = to_model or chat_request.model_id
                    trace.append(
                        {
                            "title": "切换备用托管模型",
                            "detail": (
                                f"{model_event.get('from_model_name', from_model)} 暂时不可用，"
                                f"已自动切换到 {model_event.get('to_model_name', to_model)}。"
                            ),
                            "status": "fallback",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "compatibility.fallback":
                    agent_runtime["harness"] = "direct-provider"
                    agent_runtime["compatibility_fallback"] = True
                    agent_runtime["compatibility_error"] = str(model_event.get("error", ""))
                    trace.append(
                        {
                            "title": "Pi 兼容回退",
                            "detail": "Pi 在产生文本或启动工具前失败；本轮已安全切换为直连模型传输。",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "search.delivery":
                    trace.append(
                        {
                            "title": "直接交付检索结果",
                            "detail": "联网工具已成功返回；ScanSci 直接交付 DOI/URL 题录以控制等待时间，未让模型补写未经全文证据支持的结论。",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "task.delivery":
                    trace.append(
                        {
                            "title": "直接交付任务文档结果",
                            "detail": "当前任务的文档已成功解析和结构化；最终措辞模型暂时不可用，ScanSci 直接交付全文映射结果，避免已完成的工作丢失。",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "continuation":
                    trace.append(
                        {
                            "title": "自动续写",
                            "detail": "检测到模型达到单次输出上限，已从断点继续生成，避免答案被截断。",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                elif model_event.get("type") == "retry":
                    delay = int(float(model_event.get("delay_seconds", 0) or 0))
                    reason = "服务繁忙" if model_event.get("reason") == "rate_limit" else "上游暂时不可用"
                    trace.append(
                        {
                            "title": "自动重试",
                            "detail": f"检测到{reason}，将在约 {delay} 秒后继续，本轮内容不会丢失。",
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)

            text = _normalize_direct_chat_output("".join(fragments), chat_request.selected_skills)
            text = _repair_scientific_rewrite(user_text, text)
            text, temporal_guarded = _guard_temporal_delivery(user_text, text)
            text, structurally_completed, removed_terminal_loop = _settle_structured_direct_output(
                user_text,
                text,
                truncated=truncated,
            )
            removed_terminal_loop = removed_terminal_loop or stream_repetition_detected
            structured_gap = _structured_output_contract_gap(user_text, text)
            if structured_completion_requested and structured_gap:
                # Nothing has crossed the buffered delivery boundary yet, so a
                # controlled repair can safely recover a transient greeting or
                # a partially formatted answer.  Two repair attempts give the
                # managed model a deterministic last chance without ever
                # showing a malformed draft to the user.
                last_gap = structured_gap
                repair_attempts: list[dict[str, object]] = [
                    {
                        "attempt": 0,
                        "characters": len(text),
                        "gap": structured_gap,
                    }
                ]
                repaired = False
                for repair_attempt in range(1, 3):
                    trace.append(
                        {
                            "title": "校正写作交付" if repair_attempt == 1 else "再次校正写作交付",
                            "detail": (
                                f"第 {repair_attempt} 次输出未满足明确格式要求（{last_gap}），"
                                "正在自动重试。"
                            ),
                        }
                    )
                    yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                    retry_fragments: list[str] = []
                    retry_usage: dict[str, int] = {}
                    retry_truncated = False
                    repair_messages = _structured_retry_messages(
                        chat_request.messages,
                        user_text,
                        previous_gap=last_gap,
                        attempt=repair_attempt,
                    )
                    if pi_eligible and pi_needed:
                        repair_request = replace(chat_request, messages=repair_messages)
                        repair_session_id = str(
                            dict(agent_runtime.get("session", {}) or {}).get("session_id", "")
                            or payload.get("pi_session_id", "")
                        ).strip() or None
                        retry_events = self._pi_events_with_compatibility_fallback(
                            repair_request,
                            task_mode=pi_task_mode if pi_task_mode != "general" else None,
                            session_id=repair_session_id,
                            active_run_id=run_id,
                        )
                    else:
                        retry_events = stream_chat_text(
                            chat_request.provider_kind,
                            base_url=chat_request.base_url,
                            api_key=chat_request.api_key,
                            model=chat_request.model_id,
                            messages=repair_messages,
                            thinking_mode=_direct_thinking_mode(chat_request),
                            session=chat_request.session,
                            timeout=45.0,
                            max_tokens=_direct_output_budget(user_text, chat_request.selected_skills),
                            max_continuations=_direct_max_continuations(
                                user_text,
                                chat_request.selected_skills,
                            ),
                            temperature=0.2,
                            max_requests=1,
                            **_model_transport_kwargs(chat_request),
                        )
                    for retry_event in retry_events:
                        if retry_event.get("type") == "delta":
                            retry_content = str(retry_event.get("content", ""))
                            if retry_content:
                                retry_fragments.append(retry_content)
                        elif retry_event.get("type") == "done":
                            retry_truncated = bool(retry_event.get("truncated"))
                            received_retry_usage = retry_event.get("usage")
                            raw_retry_stats = retry_event.get("stats")
                            if isinstance(raw_retry_stats, dict):
                                session_stats = dict(raw_retry_stats)
                                if not isinstance(received_retry_usage, dict):
                                    received_retry_usage = raw_retry_stats.get("tokens")
                            if isinstance(received_retry_usage, dict):
                                retry_usage = {
                                    str(key): value
                                    for key, value in received_retry_usage.items()
                                    if isinstance(value, int)
                                }
                        elif retry_event.get("type") == "session":
                            agent_runtime["session"] = {
                                "session_id": str(retry_event.get("session_id", "")),
                                "session_file": str(retry_event.get("session_file", "")),
                                "resumed": bool(retry_event.get("resumed", False)),
                            }
                        elif retry_event.get("type") == "tool.completed":
                            tool_name = str(retry_event.get("name", ""))
                            agent_runtime["tool_calls"].append({"name": tool_name, "status": "completed"})
                        elif retry_event.get("type") == "tool.failed":
                            tool_name = str(retry_event.get("name", ""))
                            agent_runtime["tool_calls"].append({"name": tool_name, "status": "failed"})
                        elif retry_event.get("type") == "compatibility.fallback":
                            agent_runtime["harness"] = "direct-provider"
                            agent_runtime["compatibility_fallback"] = True
                            agent_runtime["compatibility_error"] = str(retry_event.get("error", ""))
                            trace.append(
                                {
                                    "title": "Pi 兼容回退",
                                    "detail": "Pi 在结构化修复产生文本前失败；已显式切换为有界直连兼容传输。",
                                }
                            )
                            yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                        elif retry_event.get("type") == "cancelled":
                            raise _RunCancelled("Pi Agent repair run was cancelled")
                    retry_text = _normalize_direct_chat_output(
                        "".join(retry_fragments),
                        chat_request.selected_skills,
                    )
                    retry_text = _repair_scientific_rewrite(user_text, retry_text)
                    retry_text, retry_temporal_guarded = _guard_temporal_delivery(user_text, retry_text)
                    retry_text, retry_structurally_completed, retry_removed_terminal_loop = (
                        _settle_structured_direct_output(
                            user_text,
                            retry_text,
                            truncated=retry_truncated,
                        )
                    )
                    retry_gap = _structured_output_contract_gap(user_text, retry_text)
                    repair_attempts.append(
                        {
                            "attempt": repair_attempt,
                            "characters": len(retry_text),
                            "gap": retry_gap,
                        }
                    )
                    if retry_gap:
                        last_gap = retry_gap
                        continue
                    text = retry_text
                    usage = _merge_usage(usage, retry_usage)
                    truncated = retry_truncated
                    structurally_completed = retry_structurally_completed
                    removed_terminal_loop = retry_removed_terminal_loop
                    temporal_guarded = temporal_guarded or retry_temporal_guarded
                    repaired = True
                    break
                if not repaired:
                    diagnostics = "; ".join(
                        f"第 {item['attempt']} 次：{item['characters']} 字；{item['gap']}"
                        for item in repair_attempts
                    )
                    raise RuntimeError(
                        "模型连续三次都未满足本轮明确的写作格式要求：" + diagnostics
                    )
            if not text:
                if removed_terminal_loop:
                    raise RuntimeError(
                        "模型输出陷入重复，ScanSci 已撤回该结果；请重试或切换模型。"
                    )
                raise RuntimeError("The model returned an empty response")
            if removed_terminal_loop:
                trace.append({
                    "title": "移除异常重复",
                    "detail": "检测到模型在结尾重复同一词语，已保留完整正文并移除损坏后缀。",
                })
                yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
            if structurally_completed:
                truncated = False
                trace.append({
                    "title": "完成性校验",
                    "detail": "已核对用户要求的编号章节；正文完整，已补齐用户指定的结束标记。",
                })
                yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
            if truncated:
                if pi_needed:
                    raise RuntimeError("模型连续达到输出上限，ScanSci 没有把不完整内容标记为完成；请缩小问题范围后重试。")
                text = text.rstrip() + "\n\n（模型输出达到本轮上限，内容尚未完整；请缩小范围后重试，或继续追问未完成部分。）"
            delivery_ledger = _validate_pi_delivery(
                text,
                task_mode=pi_task_mode,
                user_text=self._last_user_text(chat_request.messages),
                tool_calls=list(agent_runtime.get("tool_calls", []) or []),
            )
            if delivery_ledger.required_groups:
                agent_runtime["capability_ledger"] = delivery_ledger.to_dict()
            if chat_request.vision_route:
                effective_route = dict(chat_request.vision_route)
                vision_fallback = agent_runtime.get("vision_fallback")
                if isinstance(vision_fallback, dict):
                    effective_route.update(
                        {
                            "fallback_provider_id": str(vision_fallback.get("to_provider", "")),
                            "fallback_model_id": str(vision_fallback.get("to_model", "")),
                            "effective_model_id": str(
                                vision_fallback.get("to_model", "") or chat_request.model_id
                            ),
                        }
                    )
                    effective_model_id = str(vision_fallback.get("to_model", "") or chat_request.model_id)
                    if effective_model_id:
                        effective_route["model_id"] = effective_model_id
                    if vision_fallback.get("to_provider"):
                        effective_route["provider_id"] = str(vision_fallback["to_provider"])
                    if vision_fallback.get("to_provider_name"):
                        effective_route["provider_name"] = str(vision_fallback["to_provider_name"])
                    if vision_fallback.get("to_mode"):
                        effective_route["mode"] = str(vision_fallback["to_mode"])
                agent_runtime["vision_route"] = effective_route
            if temporal_guarded:
                trace.append({
                    "title": "校验时效性",
                    "detail": "搜索结果日期与当前日期不符；已移除“今天/最新”的错误表述并明确标记为旧资料。",
                })
                yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
            try:
                _validate_direct_chat_output(text, chat_request.selected_skills)
            except RuntimeError:
                if not _has_selected_skill(chat_request.selected_skills, "good-question"):
                    raise
                trace.append(
                    {
                        "title": "校正科学问题卡",
                        "detail": "首稿未通过格式或逻辑门控，正在进行一次受控修复。",
                    }
                )
                yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
                text, repair_usage, _ = self._repair_good_question_if_needed(chat_request, text)
                usage = _merge_usage(usage, repair_usage)
            if buffer_model_output:
                yield run_event(
                    TEXT_MESSAGE_CONTENT,
                    run_id=run_id,
                    messageId=message_id,
                    delta=text,
                )
            else:
                delivered_prefix = "".join(fragments)[:stream_guard_emitted_chars]
                # The browser replaces the streaming placeholder with the
                # canonical RUN_FINISHED message.  In the common case the
                # normalized answer keeps the already delivered prefix, so we
                # can flush only the validated remainder without duplication.
                if text.startswith(delivered_prefix):
                    validated_remainder = text[stream_guard_emitted_chars:]
                    if validated_remainder:
                        yield run_event(
                            TEXT_MESSAGE_CONTENT,
                            run_id=run_id,
                            messageId=message_id,
                            delta=validated_remainder,
                        )
            message: dict[str, Any] = {
                "role": "assistant",
                "content": text,
                "message_id": message_id,
                "mode": chat_request.chat_mode,
                "trace": trace,
            }
            if audio_info:
                message["audio"] = list(audio_info.get("attachments", []) or [])
            if usage:
                message["usage"] = usage
                yield run_event(CUSTOM, run_id=run_id, name="usage", value=usage)
            if session_stats:
                # Keep the runtime's cumulative session stats alongside the
                # message.  The composer uses this payload for its live
                # context ring; it is intentionally not inferred from the
                # single-turn usage object above.
                agent_runtime["session_stats"] = session_stats
                yield run_event(CUSTOM, run_id=run_id, name="session_stats", value=session_stats)
            if ingestion:
                message["sources"] = list(ingestion.get("sources", []) or [])
            yield run_event(TEXT_MESSAGE_END, run_id=run_id, messageId=message_id)
            yield run_event(
                RUN_FINISHED,
                run_id=run_id,
                threadId="direct-chat",
                result={
                    "message": message,
                    "model": {
                        "provider_id": chat_request.provider_id,
                        "model_id": str(agent_runtime.get("effective_model_id") or chat_request.model_id),
                    },
                    "agent_runtime": agent_runtime,
                    **({"user_images": list(chat_request.image_attachments)} if chat_request.image_attachments else {}),
                    **({"stats": session_stats} if session_stats else {}),
                    **({"ingestion": ingestion} if ingestion else {}),
                },
            )
        except Exception as error:  # terminal events prevent a permanently spinning UI
            failure = getattr(error, "failure", None)
            if not isinstance(failure, dict) or not failure:
                failure = _structured_recovery(error)
            yield run_event(
                RUN_ERROR,
                run_id=run_id,
                message=str(failure.get("message", str(error))),
                code="chat_failed",
                failure=failure,
            )

    @staticmethod
    def _knowledge_writing_intent(text: str) -> bool:
        """Identify requests that need an evidence-grounded *draft*, not a terse answer.

        A scoped knowledge-base question such as ``这篇文献的样本量是多少`` is
        best handled as a compact, per-sentence verified answer.  By contrast,
        asking for a paper, review, or an overview of a research field needs
        coherent prose.  Treating both as the same small-claim interaction was
        the regression that made the knowledge chat feel unable to write.
        """

        normalized = " ".join(str(text or "").strip().lower().split())
        if not normalized:
            return False
        return bool(
            re.search(
                r"(?:写|撰写|起草|生成|完成|帮我写).{0,24}(?:论文|文章|综述|报告|摘要|引言|讨论|结论|章节|初稿|文献回顾)"
                r"|(?:研究进展|研究现状|领域现状|发展脉络|系统综述|文献综述|综述一下|总结一下.{0,18}(?:领域|进展|现状|研究))"
                r"|(?:write|draft|compose|prepare).{0,28}(?:paper|article|review|report|abstract|introduction|discussion|manuscript)"
                r"|(?:research progress|state of the art|literature review|research overview)",
                normalized,
                re.IGNORECASE,
            )
        )

    @classmethod
    def _uses_evidence_grounded_writing(
        cls,
        chat_request: _DirectChatRequest,
        *,
        ingestion: dict[str, Any] | None,
    ) -> bool:
        """Route article-like KB requests through retrieval before drafting."""

        requested = [
            *list(chat_request.notebook_ids or []),
            str(chat_request.notebook_id or "").strip(),
        ]
        return (
            chat_request.chat_mode == "knowledge"
            and any(str(notebook_id).strip() for notebook_id in requested)
            and ingestion is None
            and cls._knowledge_writing_intent(cls._last_user_text(chat_request.messages))
        )

    @staticmethod
    def _catalog_match_terms(topic: str) -> tuple[str, tuple[str, ...]]:
        """Normalize a user concept into conservative, inspectable aliases."""

        normalized_topic = str(topic or "").strip()
        casefolded = normalized_topic.casefold()
        matched_canonical = next(
            (
                canonical
                for canonical, candidates in _CATALOG_TOPIC_ALIASES.items()
                if canonical.casefold() in casefolded
                or any(candidate.casefold() in casefolded for candidate in candidates)
            ),
            "",
        )
        if matched_canonical:
            return matched_canonical, _CATALOG_TOPIC_ALIASES[matched_canonical]
        return normalized_topic, (normalized_topic,) if normalized_topic else ()

    @classmethod
    def _knowledge_catalog_request(cls, text: str) -> dict[str, Any] | None:
        """Parse a complete-library count request without involving an LLM.

        The key product distinction is between a *document inventory* question
        ("how many papers?") and an evidence question ("what does a paper
        say?").  The former must never be answered from the number of top-k
        evidence hits, because a single document can produce many evidence
        spans and a broad topic can match documents that retrieval did not
        return.
        """

        question = str(text or "").strip()
        is_count = bool(_CATALOG_COUNT_INTENT_RE.search(question))
        is_list = bool(_CATALOG_LIST_INTENT_RE.search(question))
        if not question or not (is_count or is_list):
            return None

        topic = ""
        for pattern in _CATALOG_TOPIC_PATTERNS:
            match = pattern.search(question)
            if match:
                topic = str(match.group("topic") or "").strip()
                break
        topic = re.sub(
            r"^(?:当前|这个|该|我们(?:的)?|本)?(?:已选(?:中的)?|当前)?"
            r"(?:知识库|资料库|文献库|库)?(?:里面|中|内)?",
            "",
            topic,
            flags=re.IGNORECASE,
        ).strip(" 。，、；;:：？?!！的")
        topic = re.sub(r"(?:有关|相关|文献|论文|资料|文章)$", "", topic).strip()

        topic, aliases = cls._catalog_match_terms(topic)
        return {
            "operation": "count" if is_count else "list",
            "topic": topic,
            "match_terms": list(dict.fromkeys(term.strip() for term in aliases if term.strip())),
            "planner": "deterministic",
            "confidence": 1.0,
        }

    @classmethod
    def _uses_knowledge_catalog_answer(
        cls,
        chat_request: _DirectChatRequest,
        *,
        ingestion: dict[str, Any] | None,
    ) -> bool:
        requested = [
            *list(chat_request.notebook_ids or []),
            str(chat_request.notebook_id or "").strip(),
        ]
        return (
            chat_request.chat_mode == "knowledge"
            and any(str(notebook_id).strip() for notebook_id in requested)
            and ingestion is None
            and cls._knowledge_catalog_request(cls._last_user_text(chat_request.messages)) is not None
        )

    @staticmethod
    def _uses_verified_knowledge_answer(
        chat_request: _DirectChatRequest,
        *,
        ingestion: dict[str, Any] | None,
    ) -> bool:
        """Keep library-scoped chat on the same citation contract as evidence Q&A.

        A regular direct completion may call a search tool, but it cannot prove
        which exact sentence supported each generated claim.  Once the user has
        selected one or more libraries, the compact chat surface therefore uses
        the verified-answer pipeline instead of attempting to retrofit citations
        onto free-form model text.  New, unindexed attachments retain their own
        ingestion flow until they become part of a searchable library.
        """

        requested = [
            *list(chat_request.notebook_ids or []),
            str(chat_request.notebook_id or "").strip(),
        ]
        return (
            chat_request.chat_mode == "knowledge"
            and any(str(notebook_id).strip() for notebook_id in requested)
            and ingestion is None
        )

    def _article_citations_from_evidence(
        self,
        evidence_rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Expose the complete verified article package to the writer and UI.

        ``reader_answer`` intentionally keeps a concise answer view and may
        cite only a few claims. That is correct for a short Q&A response but
        wrong for a review: it silently reduces a broad retrieval to the
        handful of claims chosen by the concise synthesizer. Article writing
        therefore maps each validated evidence-table row to its own stable
        citation marker.
        """

        citations: list[dict[str, Any]] = []
        seen_evidence_ids: set[str] = set()
        for row in evidence_rows:
            evidence_id = str(row.get("evidence_id", "")).strip()
            exact_quote = str(row.get("exact_quote", "")).strip()
            if not evidence_id or not exact_quote or evidence_id in seen_evidence_ids:
                continue
            seen_evidence_ids.add(evidence_id)
            doc_id = str(row.get("doc_id", "")).strip()
            anchor = str(row.get("html_anchor", "")).strip()
            citations.append(
                {
                    "citation_id": str(len(citations) + 1),
                    "quote_id": str(row.get("quote_id", "")).strip(),
                    "evidence_id": evidence_id,
                    "doc_id": doc_id,
                    "paper": str(row.get("paper", "")).strip(),
                    "doi": str(row.get("doi", "")).strip(),
                    "section": str(row.get("section", "")).strip(),
                    "exact_quote": exact_quote,
                    "source_href": str(row.get("source_href", "")).strip(),
                    "html_path": str(row.get("html_path", "")).strip(),
                    "html_anchor": anchor,
                    "reader_url": self._reader_url(doc_id, anchor) if doc_id else "",
                    "original_url": self._original_url(doc_id) if doc_id else "",
                    "support_status": "supported",
                }
            )
            if len(citations) >= max(1, int(limit)):
                break
        return citations

    @staticmethod
    def _evidence_writing_prompt(
        *,
        request: str,
        citations: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Build the bounded source package used by the article-writing pass."""

        source_blocks: list[str] = []
        for citation in citations:
            citation_id = str(citation.get("citation_id", "")).strip()
            exact_quote = " ".join(str(citation.get("exact_quote", "")).split())
            if not citation_id or not exact_quote:
                continue
            provenance = " · ".join(
                value
                for value in [
                    str(citation.get("paper", "")).strip(),
                    str(citation.get("section", "")).strip(),
                    str(citation.get("doi", "")).strip(),
                ]
                if value
            )
            source_blocks.append(
                f"[{citation_id}] {provenance or '本地资料'}\n{exact_quote[:1_400]}"
            )
        evidence_pack = "\n\n".join(source_blocks)
        return [
            {
                "role": "system",
                "content": (
                    "你是 ScanSci 的科研写作助手。请为中文用户写一份有逻辑、有段落层次的科研文章草稿或研究现状综述。"
                    "下方只有已经从本地知识库检索并核验的原文证据；不得加入这些证据不能支持的事实、数字、论文或因果结论。"
                    "使用自然的简体中文，转述英文证据，不要粘贴英文原句。"
                    "除标题外，每个非空正文段落（包括导语和局限分析）末尾都必须附上对应的方括号脚标，例如 [1]；只能使用已提供的脚标。"
                    "不得将来源作者或其措辞写成“本课题组”“我们”或“本研究”，请使用“所选资料”“有研究”或第三人称表述。"
                    "不要编造参考文献表、DOI 或实验细节。若资料覆盖不足，明确写出资料边界，而不是用通用背景填充。"
                    "除非用户指定格式，否则采用：标题、导语、2—4 个小节、局限与下一步；篇幅约 700—1200 个汉字。"
                ),
            },
            {
                "role": "user",
                "content": f"用户请求：{request}\n\n已核验的本地证据：\n{evidence_pack}",
            },
        ]

    @staticmethod
    def _verified_answer_writing_prompt(
        *,
        request: str,
        citations: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Build a concise Pi synthesis turn from host-verified evidence only."""

        source_blocks: list[str] = []
        for citation in citations:
            citation_id = str(citation.get("citation_id", "")).strip()
            exact_quote = " ".join(str(citation.get("exact_quote", "")).split())
            if not citation_id or not exact_quote:
                continue
            provenance = " · ".join(
                value
                for value in [
                    str(citation.get("paper", "")).strip(),
                    str(citation.get("section", "")).strip(),
                    str(citation.get("doi", "")).strip(),
                ]
                if value
            )
            source_blocks.append(
                f"[{citation_id}] {provenance or '本地资料'}\n{exact_quote[:1_400]}"
            )
        return [
            {
                "role": "system",
                "content": (
                    "你是 ScanSci 的证据问答助手。只使用下方已经由宿主检索并核验的本地原文证据回答问题；"
                    "不得补充证据未支持的事实、数字、论文或因果判断。请使用简洁、自然的简体中文，"
                    "每个包含事实判断的非空段落末尾必须附对应方括号脚标，例如 [1]，且只能使用已提供的脚标。"
                    "资料不足时明确说明边界，不要用常识补齐。不要输出参考文献表。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{request}\n\n已核验的本地证据：\n"
                    + "\n\n".join(source_blocks)
                ),
            },
        ]

    @staticmethod
    def _evidence_article_uses_known_citations(text: str, citations: list[dict[str, Any]]) -> bool:
        known_ids = {
            str(citation.get("citation_id", "")).strip()
            for citation in citations
            if str(citation.get("citation_id", "")).strip()
        }
        cited_ids = set(re.findall(r"\[(\d+)\]", str(text or "")))
        return bool(cited_ids) and cited_ids.issubset(known_ids)

    @staticmethod
    def _normalize_evidence_citation_markers(text: str, citations: list[dict[str, Any]]) -> str:
        """Normalize common model footnote forms to ScanSci's stable ``[n]`` form.

        The article writer is asked to emit square-bracket citations, but models
        routinely return Chinese brackets (``【1】`` / ``［1］``), parenthesised
        markers, or Markdown footnotes (``[^1]``).  Those are semantically
        valid references to the supplied source package, and silently dropping
        a complete draft merely because of punctuation is a bad failure mode.
        Only normalize marker groups whose ids all belong to the verified
        citation package; unknown ids stay untouched and will still fail the
        validation gate below.
        """

        normalized = str(text or "")
        known_ids = {
            str(citation.get("citation_id", "")).strip()
            for citation in citations
            if str(citation.get("citation_id", "")).strip()
        }
        if not normalized or not known_ids:
            return normalized

        marker_pattern = re.compile(
            r"(?:\[|［|【|\(|（)\s*\^?\s*"
            r"((?:\d+\s*[,，、;；]\s*)*\d+)\s*(?:\]|］|】|\)|）)"
        )

        def replace_marker(match: re.Match[str]) -> str:
            identifiers = re.findall(r"\d+", match.group(1))
            if not identifiers or any(identifier not in known_ids for identifier in identifiers):
                return match.group(0)
            return "".join(f"[{identifier}]" for identifier in identifiers)

        return marker_pattern.sub(replace_marker, normalized)

    @staticmethod
    def _verified_evidence_fallback(
        citations: list[dict[str, Any]],
        question: str = "",
    ) -> str:
        """Retain every checked source when the prose-writing pass is unavailable.

        A failed writing provider must not quietly turn a 12-source retrieval
        into the three citations used by the short-answer renderer.  This is a
        deliberately transparent evidence brief: each compact excerpt has its
        own stable citation button, while the click target still opens the full
        exact source span.
        """

        selected_citations = list(citations)
        # When a Chinese request falls back to source excerpts (for example
        # when the configured writing provider returns HTTP 402), prefer
        # Chinese excerpts if the verified package contains them.  Returning
        # unrelated English snippets is technically source-grounded but makes
        # the UI look like the answer was not completed.  If no Chinese source
        # exists we keep the full package and let the scope note be honest.
        question_text = str(question or "")
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", question_text):
            chinese_citations = [
                citation
                for citation in selected_citations
                if re.search(
                    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
                    " ".join(
                        str(citation.get(field, ""))
                        for field in ("paper", "section", "claim_target", "exact_quote")
                    ),
                )
            ]
            if chinese_citations:
                selected_citations = chinese_citations

        entries: list[str] = []
        for citation in selected_citations:
            citation_id = str(citation.get("citation_id", "")).strip()
            quote = " ".join(str(citation.get("exact_quote", "")).split())
            if not citation_id or not quote:
                continue
            paper = str(citation.get("paper", "")).strip() or "本地资料"
            excerpt = quote[:320].rstrip()
            if len(quote) > len(excerpt):
                excerpt += "…"
            entries.append(f"## 证据 {citation_id}：{paper}\n\n{excerpt} [{citation_id}]")

        if not entries:
            return ""
        return (
            "# 已核验的本地证据\n\n"
            "写作模型未能返回可验证的整篇草稿。为避免遗漏已召回的资料，"
            "以下保留本轮全部可回跳的证据摘录；点击每个脚标可查看完整原文。\n\n"
            + "\n\n".join(entries)
        )

    @staticmethod
    def _remove_uncited_article_prose(text: str, citations: list[dict[str, Any]]) -> str:
        """Keep an article's prose inside the verified citation boundary.

        Models occasionally add a polished, generic introduction without a
        source marker.  It reads well but breaks ScanSci's promise that a
        factual paragraph can be traced to an original fragment.  Preserve
        headings and cited paragraphs, replace an uncited introduction with a
        transparent scope statement, and drop any other uncited prose.
        """

        known_ids = [
            str(citation.get("citation_id", "")).strip()
            for citation in citations
            if str(citation.get("citation_id", "")).strip()
        ]
        if not known_ids:
            return ""
        parts = [part.strip() for part in re.split(r"\n\s*\n", str(text or "")) if part.strip()]
        accepted: list[str] = []
        pending_headings: list[str] = []
        scope_markers = "".join(f"[{citation_id}]" for citation_id in known_ids)
        scope_statement = (
            "以下综述仅概括本轮检索并核验到的材料；"
            "未被这些原文片段支持的判断不作延伸。"
            f"{scope_markers}"
        )
        for part in parts:
            if re.match(r"^#{1,6}\s+", part):
                pending_headings.append(part)
                continue
            cited_ids = set(re.findall(r"\[(\d+)\]", part))
            if cited_ids and cited_ids.issubset(set(known_ids)):
                accepted.extend(pending_headings)
                accepted.append(part)
                pending_headings.clear()
                continue
            if pending_headings and any("导语" in heading or "引言" in heading for heading in pending_headings):
                accepted.extend(pending_headings)
                accepted.append(scope_statement)
            pending_headings.clear()
        return "\n\n".join(accepted).strip()

    @staticmethod
    def _ensure_evidence_article_title(text: str) -> str:
        """Give a cited article draft a stable, visible document title.

        The writer is asked to include a title, but generated prose can begin
        directly with a second-level section heading.  That leaves the user
        without a document-level title even when the evidence is sound.  Keep
        a model-supplied H1 when present; otherwise add a neutral title that
        does not claim anything beyond the selected local materials.
        """

        article = str(text or "").strip()
        if not article:
            return ""
        if re.search(r"(?m)^#\s+\S", article):
            return article
        return "# 基于本地资料的研究进展综述\n\n" + article

    def _stream_evidence_grounded_writing(
        self,
        *,
        run_id: str,
        message_id: str,
        chat_request: _DirectChatRequest,
        session_id: str | None = None,
    ):
        """Retrieve verified local evidence first, then draft readable prose from it.

        The article is deliberately a second generation pass: retrieval owns the
        citations and the writer only receives those checked source snippets.
        This keeps NotebookLM-style source jumps while restoring the fluency
        expected from a writing request.
        """

        notebook_ids = list(
            dict.fromkeys(
                notebook_id
                for notebook_id in [
                    *list(chat_request.notebook_ids or []),
                    str(chat_request.notebook_id or "").strip(),
                ]
                if str(notebook_id).strip()
            )
        )
        question = self._last_user_text(chat_request.messages)
        if not question:
            raise ValueError("A knowledge-base writing request is required")

        yield run_event(TEXT_MESSAGE_START, run_id=run_id, messageId=message_id, role="assistant")
        # A review must not inherit the concise-answer budget. Reserve a
        # larger, document-diverse evidence package before asking the writer
        # to synthesize it; the final article still uses only exact, verified
        # fragments from that package.
        result = self.answer_sync(
            {
                "question": question,
                "notebook_id": notebook_ids[0],
                "notebook_ids": notebook_ids,
                "knowledge_scope": dict(chat_request.knowledge_scope or {}),
                "thinking_level": chat_request.thinking_level,
                "limit": 20,
                "max_quotes": 12,
                "min_quotes": 6,
                "min_documents": 3,
                "per_document_limit": 2,
                "query_variants": 2,
                # Do not hide repeated full-library searches behind one chat
                # request. The two planned routes already cover a broad
                # review; an incomplete result remains transparent.
                "max_followup_queries": 0,
                "agent_harness": "fixed-workflow",
                "task_mode": "evidence",
                "force_local_evidence": True,
            }
        )
        source_reader = dict(result.get("reader_answer", {}) or {})
        knowledge_scope = dict(result.get("knowledge_scope", {}) or {})
        citations = self._article_citations_from_evidence(
            list(result.get("evidence_table", []) or []),
            limit=12,
        )
        if not citations:
            # Retain the normal reader answer as a compatibility fallback for
            # older evidence engines that do not expose an evidence table.
            citations = list(source_reader.get("citations", []) or [])
        verification = dict(result.get("citation_verification", {}) or {})
        hits = list(result.get("hits", []) or [])
        fallback_text = str(source_reader.get("text", "")).strip()
        text = ""
        usage: dict[str, int] = {}
        writing_fallback = False
        fixed_tool_calls = [
            {"name": "search_local_evidence", "status": "completed"},
            {"name": "build_verified_answer", "status": "completed"},
        ]
        writer_runtime: dict[str, Any] = {
            "harness": "verified-evidence-fallback",
            "task_mode": "knowledge",
            "evidence_policy": "grounded-writing",
            "tool_calls": list(fixed_tool_calls),
            "session": {},
            "compatibility_fallback": False,
            "compatibility_error": "",
            "pi_attempted": False,
            "pi_success": False,
        }

        if citations and (verification.get("passed") or result.get("evidence_table")):
            writer_request = replace(
                chat_request,
                messages=self._evidence_writing_prompt(request=question, citations=citations),
            )
            writer_contract = self._compile_contract(
                task_mode="knowledge",
                user_text=self._last_user_text(writer_request.messages),
                mcp_scope="selected-library",
            )
            writer_runtime["task_contract"] = writer_contract
            writer_runtime["pi_attempted"] = True
            try:
                fragments: list[str] = []
                for writer_event in self._pi_events_with_compatibility_fallback(
                    writer_request,
                    task_mode="knowledge",
                    session_id=session_id,
                    active_run_id=run_id,
                    precompleted_tool_calls=fixed_tool_calls,
                    fallback_timeout_seconds=90.0,
                    fallback_max_tokens=max(
                        1_200,
                        _direct_output_budget(question, chat_request.selected_skills),
                    ),
                    fallback_temperature=0.35,
                    fallback_use_litellm=False,
                ):
                    event_type = str(writer_event.get("type", ""))
                    if event_type == "delta":
                        fragments.append(str(writer_event.get("content", "")))
                    elif event_type == "done":
                        raw_stats = writer_event.get("stats")
                        received_usage = writer_event.get("usage")
                        if not isinstance(received_usage, dict) and isinstance(raw_stats, dict):
                            received_usage = raw_stats.get("tokens")
                        if isinstance(received_usage, dict):
                            usage = {
                                str(key): int(value)
                                for key, value in received_usage.items()
                                if isinstance(value, int)
                            }
                    elif event_type == "session":
                        writer_runtime["session"] = {
                            "session_id": str(writer_event.get("session_id", "")),
                            "session_file": str(writer_event.get("session_file", "")),
                            "resumed": bool(writer_event.get("resumed", False)),
                        }
                    elif event_type == "tool.completed":
                        writer_runtime["tool_calls"].append({
                            "name": str(writer_event.get("name", "")),
                            "status": "completed",
                        })
                    elif event_type == "tool.failed":
                        writer_runtime["tool_calls"].append({
                            "name": str(writer_event.get("name", "")),
                            "status": "failed",
                            "error": str(writer_event.get("error", ""))[:500],
                        })
                    elif event_type == "compatibility.fallback":
                        writer_runtime["harness"] = "direct-provider"
                        writer_runtime["compatibility_fallback"] = True
                        writer_runtime["compatibility_error"] = str(writer_event.get("error", ""))
                    elif event_type == "cancelled":
                        raise _RunCancelled("Pi evidence writer was cancelled")
                text = "".join(fragments)
                text = _normalize_direct_chat_output(text, chat_request.selected_skills)
                text = self._normalize_evidence_citation_markers(text, citations)
                text = self._remove_uncited_article_prose(text, citations)
                text = self._ensure_evidence_article_title(text)
                if not self._evidence_article_uses_known_citations(text, citations):
                    text = ""
                if text and not writer_runtime["compatibility_fallback"]:
                    writer_runtime["harness"] = "pi-agent-sdk"
                    writer_runtime["pi_success"] = True
            except _RunCancelled:
                raise
            except Exception as error:
                # The verified retrieval result is still useful.  Do not turn a
                # temporary writing-provider failure into a loss of the user's
                # source-grounded answer.
                text = ""
                writer_runtime["compatibility_error"] = (
                    writer_runtime.get("compatibility_error")
                    or f"{type(error).__name__}: {error}"[:500]
                )

        if not text:
            text = (
                self._verified_evidence_fallback(citations, question)
                or fallback_text
                or "当前资料不足以生成带原文脚标的文章草稿。请缩小主题或补充可检索资料。"
            )
            writing_fallback = True
            writer_runtime["pi_success"] = False
            if writer_runtime.get("harness") == "pi-agent-sdk":
                writer_runtime["harness"] = "verified-evidence-fallback"

        citation_count = len(citations)
        scope_note = (
            f"本文草稿仅基于本轮检索并核验的 {citation_count} 条本地证据；"
            "点击脚标可定位到原文片段，未检索到的资料不在归纳范围内。"
        )
        if writing_fallback:
            scope_note = (
                "文章生成暂不可用，以下显示本轮已核验的证据摘要；"
                "脚标仍可定位到原文片段。"
            )
        reader_answer = {
            **source_reader,
            "presentation": "article" if not writing_fallback else source_reader.get("presentation", "answer"),
            "scope_note": scope_note,
            "text": text,
            "citations": citations,
            "citation_count": citation_count,
            "writing_fallback": writing_fallback,
        }
        trace = [
            {
                "title": "检索已选知识库",
                "detail": f"已召回 {len(hits)} 个候选证据片段。",
                "tool_name": "search_local_evidence",
                "status": "completed",
            },
            {
                "title": "核验原文脚标",
                "detail": f"{citation_count} 个脚标均绑定到可回跳的原文片段。",
                "tool_name": "build_verified_answer",
                "status": "completed",
            },
            {
                "title": "基于证据撰写",
                "detail": "已仅使用核验后的来源片段组织文章草稿。" if not writing_fallback else "未能完成文章生成，已保留可核验的证据摘要。",
                "tool_name": "evidence_grounded_writing",
                "status": "completed" if not writing_fallback else "fallback",
            },
        ]

        if writer_runtime.get("compatibility_fallback"):
            trace.append({
                "title": "Pi 兼容回退",
                "detail": "Pi 在产生写作文本或启动新工具前失败；本轮已显式切换为有界直连写作，不计为 Pi 成功。",
                "status": "fallback",
            })
        yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
        yield run_event(TEXT_MESSAGE_CONTENT, run_id=run_id, messageId=message_id, delta=text)
        if usage:
            yield run_event(CUSTOM, run_id=run_id, name="usage", value=usage)

        message: dict[str, Any] = {
            "role": "assistant",
            "content": text,
            "message_id": message_id,
            "mode": "knowledge",
            "trace": trace,
            "evidence_answer": True,
            "reader_answer": reader_answer,
            "citation_verification": verification,
        }
        if knowledge_scope.get("active") and str(knowledge_scope.get("type", "")) != "zotero-tag":
            message["knowledge_scope"] = knowledge_scope
        if usage:
            message["usage"] = usage
        yield run_event(TEXT_MESSAGE_END, run_id=run_id, messageId=message_id)
        yield run_event(
            RUN_FINISHED,
            run_id=run_id,
            threadId="direct-chat",
            result={
                "message": message,
                "model": {"provider_id": chat_request.provider_id, "model_id": chat_request.model_id},
                "agent_runtime": {
                    **writer_runtime,
                    "citation_verification": verification,
                    "writing_fallback": writing_fallback,
                },
            },
        )

    @staticmethod
    def _catalog_rows_from_evidence_db(
        evidence_db: Path,
        match_terms: list[str],
        *,
        preview_limit: int,
        scope_doc_ids: set[str] | None = None,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        """Return distinct source documents matching a catalogue query.

        ``evidence_spans`` is used only as an indexed full-text field here;
        the final unit remains ``source_documents.doc_id``.  This prevents
        evidence segmentation from changing a user's document count.
        """

        if not evidence_db.is_file():
            return 0, 0, []
        normalized_scope_ids = {
            str(doc_id).strip()
            for doc_id in (scope_doc_ids or set())
            if str(doc_id).strip()
        } if scope_doc_ids is not None else None
        if normalized_scope_ids is not None and not normalized_scope_ids:
            return 0, 0, []
        terms = list(dict.fromkeys(
            str(term or "").strip().casefold()
            for term in match_terms
            if str(term or "").strip()
        ))
        try:
            with sqlite3.connect(evidence_db) as connection:
                connection.row_factory = sqlite3.Row
                has_document_cards = bool(
                    connection.execute(
                        "select 1 from sqlite_master where type = 'table' and name = 'document_cards'"
                    ).fetchone()
                )
                card_columns = (
                    "coalesce(dc.summary, '') as document_summary, "
                    "coalesce(dc.anchor_evidence_ids_json, '[]') as card_anchor_evidence_ids_json "
                    if has_document_cards
                    else "'' as document_summary, '[]' as card_anchor_evidence_ids_json "
                )
                card_join = " left join document_cards as dc on dc.doc_id = sd.doc_id" if has_document_cards else ""
                query = (
                    "select sd.doc_id, sd.title, sd.doi, sd.source_url, sd.publication_year, "
                    + card_columns
                    + "from source_documents as sd"
                    + card_join
                )
                scope_clause = ""
                scope_parameters: list[str] = []
                if normalized_scope_ids is not None:
                    scope_values = sorted(normalized_scope_ids)
                    scope_parts: list[str] = []
                    for offset in range(0, len(scope_values), 500):
                        chunk = scope_values[offset:offset + 500]
                        placeholders = ", ".join("?" for _ in chunk)
                        scope_parts.append(f"sd.doc_id in ({placeholders})")
                        scope_parameters.extend(chunk)
                    scope_clause = "(" + " or ".join(scope_parts) + ")"
                term_clauses: list[str] = []
                if terms:
                    for term in terms:
                        term_clauses.append(
                            "(instr(lower(sd.title), ?) > 0 or exists ("
                            "select 1 from evidence_spans as es where es.doc_id = sd.doc_id "
                            "and (instr(lower(es.title), ?) > 0 or instr(lower(es.text), ?) > 0)"
                            "))"
                        )
                where_parts = [part for part in [scope_clause, f"({' or '.join(term_clauses)})" if term_clauses else ""] if part]
                where_clause = " where " + " and ".join(where_parts) if where_parts else ""
                parameters: list[str] = [*scope_parameters]
                for term in terms:
                    parameters.extend([term, term, term])
                total_where_clause = f" where {scope_clause}" if scope_clause else ""
                matched_row = connection.execute(
                    "select count(*) as total from source_documents as sd" + where_clause,
                    parameters,
                ).fetchone()
                matched_documents = int(dict(matched_row or {}).get("total", 0) or 0)
                total_row = connection.execute(
                    "select count(*) as total from source_documents as sd" + total_where_clause,
                    scope_parameters,
                ).fetchone()
                total_documents = int(dict(total_row or {}).get("total", 0) or 0)
                if preview_limit > 0:
                    query += where_clause
                    query += " order by coalesce(sd.publication_year, 0) desc, sd.title collate nocase limit ?"
                    rows = [dict(row) for row in connection.execute(query, [*parameters, preview_limit])]
                else:
                    rows = []
        except sqlite3.Error:
            return 0, 0, []
        return total_documents, matched_documents, rows

    def _knowledge_catalog_summary(
        self,
        *,
        notebook_ids: list[str],
        request: dict[str, Any],
        knowledge_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a transparent, full-library inventory answer for direct chat."""

        terms = [str(term).strip() for term in list(request.get("match_terms", []) or []) if str(term).strip()]
        seen_databases: set[Path] = set()
        seen_documents: set[tuple[Path, str]] = set()
        rows: list[dict[str, Any]] = []
        total_documents = 0
        matched_documents = 0
        overview_totals = {
            "document_cards": 0,
            "sections": 0,
            "graph_nodes": 0,
            "graph_edges": 0,
            "evidence_spans": 0,
        }
        titles: list[str] = []
        applied_scope: dict[str, Any] | None = None
        for notebook_id in notebook_ids:
            notebook = self._notebook(notebook_id)
            metadata = dict(notebook.get("metadata", {}) or {})
            title = str(notebook.get("title") or metadata.get("title") or notebook_id).strip() or "知识库"
            if title not in titles:
                titles.append(title)
            evidence_db = self._evidence_db_for_notebook(notebook)
            resolved_db = evidence_db.resolve() if evidence_db.exists() else evidence_db
            if resolved_db in seen_databases:
                continue
            seen_databases.add(resolved_db)
            overview = ensure_library_overview(evidence_db)
            for key in overview_totals:
                overview_totals[key] += int(overview.get(key, 0) or 0)
            scope_resolution = self._knowledge_scope_resolution(notebook, evidence_db, knowledge_scope)
            scope_doc_ids = (
                {str(doc_id).strip() for doc_id in list(scope_resolution.get("doc_ids", []) or []) if str(doc_id).strip()}
                if scope_resolution.get("active")
                else None
            )
            if scope_resolution.get("active"):
                applied_scope = dict(scope_resolution)
            database_total, database_matched, database_rows = self._catalog_rows_from_evidence_db(
                evidence_db,
                terms,
                preview_limit=max(0, 50 - len(rows)),
                scope_doc_ids=scope_doc_ids,
            )
            total_documents += database_total
            matched_documents += database_matched
            for item in database_rows:
                doc_id = str(item.get("doc_id", "")).strip()
                if not doc_id:
                    continue
                key = (resolved_db, doc_id)
                if key in seen_documents:
                    continue
                seen_documents.add(key)
                item["reader_url"] = self._reader_url(doc_id, "")
                item["original_url"] = self._original_url(doc_id)
                rows.append(item)
        rows.sort(
            key=lambda item: (
                -int(item.get("publication_year") or 0),
                str(item.get("title", "")).casefold(),
            )
        )
        result = {
            "operation": str(request.get("operation", "count") or "count"),
            "planner": str(request.get("planner", "deterministic") or "deterministic"),
            "planner_confidence": float(request.get("confidence", 1.0) or 1.0),
            "topic": str(request.get("topic", "")).strip(),
            "match_terms": terms,
            "library_titles": titles,
            "total_documents": total_documents,
            "document_count": matched_documents,
            "items": rows[:50],
            "hidden_count": max(0, matched_documents - len(rows)),
            "library_overview": overview_totals,
        }
        if applied_scope is not None:
            result["knowledge_scope"] = applied_scope
        return result

    @staticmethod
    def _catalog_scope_label(catalog: dict[str, Any]) -> str:
        # Tag matching is an internal retrieval signal, never a user-facing
        # scope or explanation.
        return ""

    def _knowledge_catalog_follow_up_result(
        self,
        *,
        chat_request: _DirectChatRequest,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Answer a task follow-up from its bound library catalogue.

        Indexing is durable library state, not a temporary attachment of the
        indexing task.  A completed ``evidence_index`` run must therefore be
        able to answer simple inventory questions even when that run has no
        downloaded task artifacts to read.
        """

        notebook_ids = list(
            dict.fromkeys(
                notebook_id
                for notebook_id in [
                    *list(chat_request.notebook_ids or []),
                    str(chat_request.notebook_id or "").strip(),
                ]
                if str(notebook_id).strip()
            )
        )
        try:
            catalog = self._knowledge_catalog_summary(
                notebook_ids=notebook_ids,
                request=request,
                knowledge_scope=chat_request.knowledge_scope,
            )
        except TypeError as error:
            # Keep older task extensions and test doubles usable while the
            # scoped catalogue callback rolls out.  Only retry the legacy
            # signature for the specific unexpected keyword; real callback
            # errors must still surface.
            if "knowledge_scope" not in str(error):
                raise
            catalog = self._knowledge_catalog_summary(
                notebook_ids=notebook_ids,
                request=request,
            )
        titles = list(catalog.get("library_titles", []) or [])
        scope_label = "、".join(str(title) for title in titles if str(title).strip()) or "所选知识库"
        terms = list(catalog.get("match_terms", []) or [])
        rendered_terms = "、".join(f"“{term}”" for term in terms)
        document_count = int(catalog.get("document_count", 0) or 0)
        total_documents = int(catalog.get("total_documents", 0) or 0)
        operation = str(catalog.get("operation", "count") or "count")
        items = list(catalog.get("items", []) or [])
        preview_titles = [str(item.get("title", "")).strip() for item in items if str(item.get("title", "")).strip()][:5]
        catalog_scope_label = self._catalog_scope_label(catalog)
        if terms:
            if operation == "list":
                text = (
                    f"{scope_label} 中已索引的资料里，按 {rendered_terms} 找到 {document_count} 篇"
                    f"（资料库共 {total_documents} 篇）。"
                )
            else:
                text = (
                    f"{scope_label} 中已索引的资料里，按 {rendered_terms} 匹配到 {document_count} 篇"
                    f"（资料库共 {total_documents} 篇）。"
                )
            scope_note = (
                f"按题名与已索引正文匹配 {rendered_terms}，按文献去重；"
                "这不是原文证据片段数量。"
            )
        else:
            text = f"{scope_label} 当前共有 {total_documents} 篇已索引资料。"
            if operation == "list" and preview_titles:
                text += " 例如：" + "；".join(preview_titles) + "。"
            scope_note = "按资料库中的文献记录去重，不以原文证据片段计数。"
        if catalog_scope_label:
            text += f" {catalog_scope_label}。"
            scope_note += f" {catalog_scope_label}；未匹配的条目不会自动回退到全库。"
        trace = [
            {
                "title": "读取已建索引的资料库目录",
                "detail": f"已从 {scope_label} 的 {total_documents} 篇已索引资料中完成目录查询。",
                "tool_name": "catalog_library_documents",
                "status": "completed",
            },
            {
                "title": "说明统计口径",
                "detail": scope_note,
                "tool_name": "catalog_library_documents",
                "status": "completed",
            },
        ]
        reader_answer = {
            "text": text,
            "presentation": "catalog",
            "sentences": [],
            "citations": [],
            "citation_count": 0,
            "scope_note": scope_note,
            "catalog": catalog,
        }
        return {
            "message": {
                "role": "assistant",
                "content": text,
                "mode": "knowledge",
                "trace": trace,
                "catalog_answer": True,
                "reader_answer": reader_answer,
                "citation_verification": {"passed": True, "mode": "catalog"},
            },
            "model": {
                "provider_id": chat_request.provider_id,
                "model_id": chat_request.model_id,
            },
            "agent_runtime": {
                "harness": "knowledge-catalog",
                "task_mode": "knowledge-catalog",
                "evidence_policy": "catalog",
                "tool_calls": [{"name": "catalog_library_documents", "status": "completed"}],
            },
        }

    def _stream_knowledge_catalog_answer(
        self,
        *,
        run_id: str,
        message_id: str,
        chat_request: _DirectChatRequest,
        request: dict[str, Any] | None = None,
    ):
        """Deliver a document-level library statistic, never a top-k RAG guess."""

        notebook_ids = list(
            dict.fromkeys(
                notebook_id
                for notebook_id in [
                    *list(chat_request.notebook_ids or []),
                    str(chat_request.notebook_id or "").strip(),
                ]
                if str(notebook_id).strip()
            )
        )
        question = self._last_user_text(chat_request.messages)
        request = dict(request or self._knowledge_catalog_request(question) or {})
        if not request:
            raise ValueError("A knowledge-base catalogue count question is required")

        yield run_event(TEXT_MESSAGE_START, run_id=run_id, messageId=message_id, role="assistant")
        catalog = self._knowledge_catalog_summary(
            notebook_ids=notebook_ids,
            request=request,
            knowledge_scope=chat_request.knowledge_scope,
        )
        titles = list(catalog.get("library_titles", []) or [])
        scope_label = "、".join(titles) or "已选知识库"
        terms = list(catalog.get("match_terms", []) or [])
        rendered_terms = "、".join(f"“{term}”" for term in terms)
        document_count = int(catalog.get("document_count", 0) or 0)
        total_documents = int(catalog.get("total_documents", 0) or 0)
        operation = str(catalog.get("operation", "count") or "count")
        planner = str(catalog.get("planner", "deterministic") or "deterministic")
        catalog_scope_label = self._catalog_scope_label(catalog)
        if terms:
            if operation == "list":
                text = (
                    f"在 {scope_label} 的已索引文献中，按 {rendered_terms} 找到 "
                    f"{document_count} 篇文献（资料库共 {total_documents} 篇）；题录可在下方展开查看。"
                )
            else:
                text = (
                    f"在 {scope_label} 的已索引文献中，按 {rendered_terms} 匹配到 "
                    f"{document_count} 篇文献（资料库共 {total_documents} 篇）。"
                )
            scope_note = (
                f"{'题录检索' if operation == 'list' else '统计'}口径：题名与已索引正文匹配 {rendered_terms}，按文献去重；"
                "这不是原文证据片段数量。"
            )
        else:
            text = (
                f"{scope_label} 当前共有 {total_documents} 篇已索引文献。"
                if operation == "count"
                else f"{scope_label} 当前共有 {total_documents} 篇已索引文献，题录可在下方展开查看。"
            )
            scope_note = f"{'题录检索' if operation == 'list' else '统计'}口径：按资料库中的文献记录去重，不以原文证据片段计数。"
        if catalog_scope_label:
            text += f" {catalog_scope_label}。"
            scope_note += f" {catalog_scope_label}；未匹配的条目不会自动回退到全库。"
        trace = [
            *([
                {
                    "title": "理解查询目标",
                    "detail": "当前模型仅规划为资料库目录查询；实际统计仍由 ScanSci 本地执行。",
                    "tool_name": "plan_knowledge_catalog",
                    "status": "completed",
                },
            ] if planner == "model" else []),
            {
                "title": "检索资料库目录" if operation == "list" else "统计已选资料库",
                "detail": f"已在 {scope_label} 的 {total_documents} 篇已索引文献中完成去重{'题录检索' if operation == 'list' else '统计'}。",
                "tool_name": "catalog_library_documents",
                "status": "completed",
            },
            {
                "title": "说明匹配口径",
                "detail": scope_note,
                "tool_name": "catalog_library_documents",
                "status": "completed",
            },
        ]
        reader_answer = {
            "text": text,
            "presentation": "catalog",
            "sentences": [],
            "citations": [],
            "citation_count": 0,
            "scope_note": scope_note,
            "catalog": catalog,
        }
        yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
        yield run_event(TEXT_MESSAGE_CONTENT, run_id=run_id, messageId=message_id, delta=text)
        message = {
            "role": "assistant",
            "content": text,
            "message_id": message_id,
            "mode": "knowledge",
            "trace": trace,
            "catalog_answer": True,
            "reader_answer": reader_answer,
            "citation_verification": {"passed": True, "mode": "catalog"},
        }
        yield run_event(TEXT_MESSAGE_END, run_id=run_id, messageId=message_id)
        yield run_event(
            RUN_FINISHED,
            run_id=run_id,
            threadId="direct-chat",
            result={
                "message": message,
                "model": {"provider_id": chat_request.provider_id, "model_id": chat_request.model_id},
                "agent_runtime": {
                    "harness": "knowledge-catalog",
                    "task_mode": "knowledge-catalog",
                    "evidence_policy": "catalog",
                    "tool_calls": [{"name": "catalog_library_documents", "status": "completed"}],
                },
            },
        )

    def _stream_verified_knowledge_answer(
        self,
        *,
        run_id: str,
        message_id: str,
        chat_request: _DirectChatRequest,
        payload: dict[str, Any],
    ):
        """Retrieve locally, then let Pi synthesize the verified answer."""

        notebook_ids = list(
            dict.fromkeys(
                notebook_id
                for notebook_id in [
                    *list(chat_request.notebook_ids or []),
                    str(chat_request.notebook_id or "").strip(),
                ]
                if str(notebook_id).strip()
            )
        )
        question = self._last_user_text(chat_request.messages)
        if not question:
            raise ValueError("A knowledge-base question is required")

        yield run_event(TEXT_MESSAGE_START, run_id=run_id, messageId=message_id, role="assistant")
        result = self.answer_sync(
            {
                "question": question,
                "notebook_id": notebook_ids[0],
                "notebook_ids": notebook_ids,
                "knowledge_scope": dict(chat_request.knowledge_scope or {}),
                "thinking_level": chat_request.thinking_level,
                # The answer_sync fallback still uses the active writing model
                # when it is configured, but always keeps the fixed retrieval,
                # quote and citation-verification gates.
                "agent_harness": "fixed-workflow",
                "task_mode": "evidence",
                # Retrieval and citation verification are deterministic host
                # stages here.  The only model-mediated final generation is
                # the Pi turn below, preserving one visible session lineage.
                "force_local_evidence": True,
            }
        )
        knowledge_scope = dict(result.get("knowledge_scope", {}) or {})
        reader_answer = dict(result.get("reader_answer", {}) or {})
        citations = []
        for index, raw_citation in enumerate(list(reader_answer.get("citations", []) or []), start=1):
            if not isinstance(raw_citation, dict):
                continue
            citation = dict(raw_citation)
            citation["citation_id"] = str(
                citation.get("citation_id") or citation.get("citation_no") or index
            ).strip()
            citations.append(citation)
        verification = dict(result.get("citation_verification", {}) or {})
        hits = list(result.get("hits", []) or [])
        fallback_text = str(reader_answer.get("text", "")).strip()
        if not fallback_text:
            limitations = list(dict(result.get("answer", {}) or {}).get("limitations", []) or [])
            fallback_text = str(limitations[0]).strip() if limitations else "当前资料不足以形成带有原文脚标的回答。请缩小问题范围，或补充可检索资料。"

        fixed_tool_calls = [
            {"name": "search_local_evidence", "status": "completed"},
            {"name": "build_verified_answer", "status": "completed"},
        ]
        writer_runtime: dict[str, Any] = {
            "harness": "verified-evidence-fallback",
            "task_mode": "knowledge",
            "evidence_policy": "strict",
            "tool_calls": list(fixed_tool_calls),
            "session": {},
            "compatibility_fallback": False,
            "compatibility_error": "",
            "pi_attempted": False,
            "pi_success": False,
        }
        text = ""
        usage: dict[str, int] = {}
        if citations and verification.get("passed"):
            writer_request = replace(
                chat_request,
                messages=self._verified_answer_writing_prompt(
                    request=question,
                    citations=citations,
                ),
            )
            writer_contract = self._compile_contract(
                task_mode="knowledge",
                user_text=self._last_user_text(writer_request.messages),
                mcp_scope="selected-library",
            )
            writer_runtime["task_contract"] = writer_contract
            writer_runtime["pi_attempted"] = True
            try:
                fragments: list[str] = []
                for writer_event in self._pi_events_with_compatibility_fallback(
                    writer_request,
                    task_mode="knowledge",
                    session_id=str(payload.get("pi_session_id", "") or "") or None,
                    active_run_id=run_id,
                    precompleted_tool_calls=fixed_tool_calls,
                    fallback_timeout_seconds=60.0,
                    fallback_max_tokens=max(700, _direct_output_budget(question, [])),
                    fallback_temperature=0.2,
                    fallback_use_litellm=False,
                ):
                    event_type = str(writer_event.get("type", ""))
                    if event_type == "delta":
                        fragments.append(str(writer_event.get("content", "")))
                    elif event_type == "done":
                        raw_stats = writer_event.get("stats")
                        received_usage = writer_event.get("usage")
                        if not isinstance(received_usage, dict) and isinstance(raw_stats, dict):
                            received_usage = raw_stats.get("tokens")
                        if isinstance(received_usage, dict):
                            usage = {
                                str(key): int(value)
                                for key, value in received_usage.items()
                                if isinstance(value, int)
                            }
                    elif event_type == "session":
                        writer_runtime["session"] = {
                            "session_id": str(writer_event.get("session_id", "")),
                            "session_file": str(writer_event.get("session_file", "")),
                            "resumed": bool(writer_event.get("resumed", False)),
                        }
                    elif event_type == "tool.completed":
                        writer_runtime["tool_calls"].append({
                            "name": str(writer_event.get("name", "")),
                            "status": "completed",
                        })
                    elif event_type == "tool.failed":
                        writer_runtime["tool_calls"].append({
                            "name": str(writer_event.get("name", "")),
                            "status": "failed",
                            "error": str(writer_event.get("error", ""))[:500],
                        })
                    elif event_type == "compatibility.fallback":
                        writer_runtime["harness"] = "direct-provider"
                        writer_runtime["compatibility_fallback"] = True
                        writer_runtime["compatibility_error"] = str(writer_event.get("error", ""))
                    elif event_type == "cancelled":
                        raise _RunCancelled("Pi verified-answer writer was cancelled")
                text = self._normalize_evidence_citation_markers("".join(fragments), citations)
                text = self._remove_uncited_article_prose(text, citations)
                if not self._evidence_article_uses_known_citations(text, citations):
                    text = ""
                if text and not writer_runtime["compatibility_fallback"]:
                    writer_runtime["harness"] = "pi-agent-sdk"
                    writer_runtime["pi_success"] = True
            except _RunCancelled:
                raise
            except Exception as error:
                writer_runtime["compatibility_error"] = (
                    writer_runtime.get("compatibility_error")
                    or f"{type(error).__name__}: {error}"[:500]
                )
                text = ""

        if not text:
            text = fallback_text
            writer_runtime["pi_success"] = False
            if writer_runtime.get("harness") == "pi-agent-sdk":
                writer_runtime["harness"] = "verified-evidence-fallback"
        reader_answer = {
            **reader_answer,
            "text": text,
            "citations": citations,
            "citation_count": len(citations),
        }

        citation_count = len(citations)
        trace = [
            {
                "title": "检索已选知识库",
                "detail": f"已召回 {len(hits)} 个候选证据片段。",
                "tool_name": "search_local_evidence",
                "status": "completed",
            },
            {
                "title": "核验原文脚标",
                "detail": (
                    f"{citation_count} 个脚标均绑定到可回跳的原文片段。"
                    if verification.get("passed")
                    else "没有形成可回跳的原文证据；本轮不会把普通文本伪装成带引文结论。"
                ),
                "tool_name": "build_verified_answer",
                "status": "completed",
            },
        ]
        if writer_runtime.get("compatibility_fallback"):
            trace.append({
                "title": "Pi 兼容回退",
                "detail": "Pi 在产生文本或启动新工具前失败；本轮显式切换为有界直连生成，不计为 Pi 成功。",
                "status": "fallback",
            })
        yield run_event(CUSTOM, run_id=run_id, name="process_trace", value=trace)
        yield run_event(TEXT_MESSAGE_CONTENT, run_id=run_id, messageId=message_id, delta=text)
        if usage:
            yield run_event(CUSTOM, run_id=run_id, name="usage", value=usage)

        message: dict[str, Any] = {
            "role": "assistant",
            "content": text,
            "message_id": message_id,
            "mode": "knowledge",
            "trace": trace,
            "evidence_answer": True,
            "reader_answer": reader_answer,
            "citation_verification": verification,
        }
        if knowledge_scope.get("active") and str(knowledge_scope.get("type", "")) != "zotero-tag":
            message["knowledge_scope"] = knowledge_scope
        if usage:
            message["usage"] = usage
        yield run_event(TEXT_MESSAGE_END, run_id=run_id, messageId=message_id)
        yield run_event(
            RUN_FINISHED,
            run_id=run_id,
            threadId="direct-chat",
            result={
                "message": message,
                "model": {"provider_id": chat_request.provider_id, "model_id": chat_request.model_id},
                "agent_runtime": {
                    **writer_runtime,
                    "citation_verification": verification,
                },
            },
        )

    @staticmethod
    def _repair_good_question_if_needed(
        chat_request: _DirectChatRequest,
        text: str,
    ) -> tuple[str, dict[str, int], bool]:
        """Replace an unsafe statistical scaffold while preserving model framing."""

        try:
            _validate_direct_chat_output(text, chat_request.selected_skills)
            return text, {}, False
        except RuntimeError:
            if not _has_selected_skill(chat_request.selected_skills, "good-question"):
                raise
        repaired_text = _safe_good_question_fallback(chat_request, text)
        repaired_text = _normalize_direct_chat_output(repaired_text, chat_request.selected_skills)
        _validate_direct_chat_output(repaired_text, chat_request.selected_skills)
        return repaired_text, {}, True

    def _runtime_fact_answer(self, payload: dict[str, Any], chat_request: _DirectChatRequest) -> str:
        raw_messages = [item for item in list(payload.get("messages", []) or []) if isinstance(item, dict)]
        question = next(
            (str(item.get("content", "")) for item in reversed(raw_messages) if item.get("role") == "user"),
            "",
        )
        if self._local_resource_status_intent(question):
            if self._runtime_facts_provider is None:
                return "当前运行入口没有提供本机资源状态读取能力；ScanSci 不会让模型猜测安装情况。"
            try:
                facts = dict(self._runtime_facts_provider() or {})
            except Exception as exc:
                return f"ScanSci 无法读取本机资源状态：{type(exc).__name__}。"
            runtime = dict(facts.get("runtime", {}) or {})
            installs = dict(facts.get("model_installs", {}) or {})
            models = [item for item in list(facts.get("installed_models", []) or []) if isinstance(item, dict)]
            ready_models = [item for item in models if bool(item.get("ready"))]
            active_install = installs.get("active") if isinstance(installs.get("active"), dict) else None
            install_jobs = [
                dict(item)
                for item in list(installs.get("jobs", []) or [])
                if isinstance(item, dict)
            ]
            failed_installs = sorted(
                (item for item in install_jobs if str(item.get("state", "")) == "failed"),
                key=lambda item: int(item.get("updated_at", 0) or 0),
                reverse=True,
            )
            runtime_mode = {
                "component": "独立运行组件",
                "embedded": "安装包内置运行时",
                "source": "当前 Python 环境",
                "missing": "未安装",
            }.get(str(runtime.get("mode", "missing")), str(runtime.get("mode", "missing")) or "未知")
            lines = [
                "可以。以下状态由 ScanSci 直接读取本机，不是模型推测：",
                f"- 本地运行能力：{'可用' if runtime.get('installed') else '未就绪'}（{runtime_mode}）",
                f"- 已发现本地模型：{len(models)} 个，其中 {len(ready_models)} 个权重完整、可启动",
            ]
            if ready_models:
                names = [str(item.get("name") or item.get("id") or "未命名模型") for item in ready_models[:5]]
                suffix = "；另有更多" if len(ready_models) > 5 else ""
                lines.append(f"- 可用模型：{'、'.join(names)}{suffix}")
            if active_install:
                progress = round(float(active_install.get("progress", 0.0) or 0.0) * 100)
                target = str(active_install.get("current_model", "") or "本地模型")
                lines.append(f"- 当前下载：{target}（{progress}%）")
            else:
                lines.append("- 当前下载：没有正在进行的模型安装任务")
            if failed_installs:
                failed = failed_installs[0]
                title = "研究检索组件" if str(failed.get("job_id", "")) == "retrieval-core" else str(
                    failed.get("job_id") or "本地模型组件"
                )
                completed = len(list(failed.get("completed_models", []) or []))
                total = max(1, int(failed.get("total_models", 0) or len(list(failed.get("models", []) or [])) or 1))
                current_model = str(failed.get("current_model", "") or "").strip()
                current_file = Path(str(failed.get("current_file", "") or "")).name
                source = str(failed.get("source", "") or "").strip()
                detail = str(failed.get("error") or failed.get("message") or "下载未完成").strip()
                if len(detail) > 360:
                    detail = detail[:357].rstrip() + "…"
                lines.append(f"- 最近失败任务：{title}（已完成 {completed}/{total} 个模型）")
                if current_model:
                    lines.append(f"  - 失败模型：{current_model}")
                if current_file:
                    lines.append(f"  - 失败文件：{current_file}")
                if source:
                    lines.append(f"  - 当时使用：{source}")
                lines.append(f"  - 原因：{detail}")
                lines.append("  - 操作：打开“设置 → 本地模型 → 下载任务”后点击“重试”")
            install_job = dict(runtime.get("install_job", {}) or {})
            if str(install_job.get("state", "idle")) in {"queued", "installing"}:
                progress = round(float(install_job.get("progress", 0.0) or 0.0) * 100)
                lines.append(f"- 运行组件安装：进行中（{progress}%）")
            return "\n".join(lines)
        if self._workspace_status_intent(question):
            summary = load_workspace_summary(self.workspace)
            notebooks = [
                item for item in list(summary.get("notebooks", []) or [])
                if isinstance(item, dict)
            ]
            indexed_documents = 0
            evidence_spans = 0
            indexed_libraries = 0
            counted_dbs: set[Path] = set()
            for notebook in notebooks:
                notebook_id = str(notebook.get("notebook_id", "")).strip()
                if not notebook_id:
                    continue
                evidence_db = notebook_evidence_db(self.evidence_db, notebook_id)
                if not evidence_db.is_file() and len(notebooks) == 1 and self.evidence_db.is_file():
                    evidence_db = self.evidence_db
                evidence_db = evidence_db.resolve()
                if evidence_db in counted_dbs:
                    continue
                if not evidence_db.is_file():
                    continue
                try:
                    connection = sqlite3.connect(
                        f"{evidence_db.as_uri()}?mode=ro",
                        uri=True,
                        timeout=0.5,
                    )
                    try:
                        document_count = int(
                            connection.execute("SELECT count(*) FROM source_documents").fetchone()[0]
                        )
                        span_count = int(
                            connection.execute("SELECT count(*) FROM evidence_spans").fetchone()[0]
                        )
                    finally:
                        connection.close()
                except (OSError, sqlite3.Error):
                    continue
                if document_count > 0:
                    counted_dbs.add(evidence_db)
                    indexed_libraries += 1
                    indexed_documents += document_count
                    evidence_spans += span_count
            if indexed_documents <= 0:
                return "当前 ScanSci 工作区没有检测到已建立本地证据索引的文档。"
            if re.search(r"(?:只|仅).{0,8}(?:报告|给|显示).{0,8}(?:数量|数字|count)", question, re.IGNORECASE):
                return f"当前 ScanSci 工作区共有 {indexed_documents} 条已索引、可检索的本地文档。"
            return (
                f"当前 ScanSci 工作区共有 {indexed_documents} 条已索引、可检索的本地文档，"
                f"分布在 {indexed_libraries} 个资料库中，并生成了 {evidence_spans} 个证据片段。"
            )
        if self._zotero_task_mode(question) == "zotero-status":
            status = dict(zotero_status())
            plugin = next(
                (
                    item
                    for item in list(load_settings(self.workspace).get("plugins", []) or [])
                    if str(item.get("id", "")) == "zotero"
                ),
                None,
            )
            plugin_ready = bool(plugin and plugin.get("enabled") and not plugin.get("uninstalled"))
            if status.get("database_readable") and (status.get("api_running") or status.get("read_mode") == "local-database"):
                return (
                    "Zotero 当前可访问：本地数据库可读，"
                    f"读取模式为 {status.get('read_mode', 'unknown')}，"
                    f"本地 API {'可用' if status.get('api_running') else '未运行'}，"
                    f"ScanSci Zotero 插件{'已启用' if plugin_ready else '未启用'}。"
                )
            return (
                "Zotero 当前不可访问："
                f"数据库{'可读' if status.get('database_readable') else '不可读'}，"
                f"本地 API {'可用' if status.get('api_running') else '未运行'}，"
                f"ScanSci Zotero 插件{'已启用' if plugin_ready else '未启用'}。"
            )
        return runtime_self_description(
            self.workspace,
            question=question,
            model_id=chat_request.model_id,
            provider_name=chat_request.provider_name,
            chat_mode=chat_request.chat_mode,
        )

    def _ingest_direct_attachments(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        source_files = payload.get("source_files")
        if not source_files:
            return None
        return ingest_sources(
            self.workspace,
            source_files,
            parser=str(payload.get("attachment_parser", "auto")),
        )

    def transcribe_audio(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Transcribe composer audio without starting a chat completion.

        The microphone input is an input method, not a chat attachment.  The
        UI can therefore show the returned text in the composer immediately
        and only send the final text to the selected assistant model.
        """

        _prepared, audio_info = self._prepare_direct_audio(payload, require_message=False)
        return {
            "transcripts": list(audio_info.get("transcripts", []) or []),
            "attachments": list(audio_info.get("attachments", []) or []),
            "model_id": str(audio_info.get("model_id", "")),
        }

    def _prepare_direct_audio(
        self,
        payload: dict[str, Any],
        *,
        require_message: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Persist and transcribe local audio before the chat model is called.

        The answer model remains the user's selected chat model.  Audio is a
        separate capability: Qwen3-ASR turns the recording into text locally,
        and that text is appended to the user's message.  This keeps every
        existing text/vision provider compatible and prevents audio bytes from
        being sent to a remote API by accident.
        """

        raw_audio = list(payload.get("audio", []) or [])
        if not raw_audio:
            return dict(payload), {}
        attachments = persist_audio_attachments(self.workspace, raw_audio)
        if not attachments:
            return dict(payload), {}

        settings = load_settings(self.workspace)
        roles = dict(settings.get("model_roles", {}) or {})
        reference = str(roles.get("audio", "") or "").strip()
        local_models = list(settings.get("local_models", []) or [])
        model_id = ""
        if reference.startswith("local:"):
            local_id = reference.removeprefix("local:")
            selected = next((item for item in local_models if str(item.get("id", "")) == local_id), None)
            if selected and str(selected.get("runtime", "")) == "local-huggingface":
                candidate = str(selected.get("model_id", "") or "").strip()
                if candidate and candidate != QWEN3_ASR_LEGACY_MODEL_ID and selected.get("runtime_compatible", True):
                    model_id = candidate
        if not model_id:
            model_id = next(
                (
                    str(item.get("id", "")).strip()
                    for item in installed_models()
                    if bool(item.get("ready"))
                    and str(item.get("kind", "")) == "audio"
                    and item.get("runtime_compatible", True)
                    and str(item.get("id", "")) != QWEN3_ASR_LEGACY_MODEL_ID
                ),
                "",
            )
        if not model_id:
            legacy_ready = any(
                bool(item.get("ready")) and str(item.get("id", "")) == QWEN3_ASR_LEGACY_MODEL_ID
                for item in installed_models()
            )
            if legacy_ready:
                raise ValueError(
                    "检测到旧版 Qwen3-ASR-0.6B。它需要独立的 qwen-asr/Transformers 4.57 环境；"
                    "请在设置 → 本地模型 → 模型市场下载 Qwen3-ASR-0.6B-hf。"
                )
            raise ValueError("尚未配置本地语音识别模型，请在设置 → 本地模型中下载 Qwen3-ASR-0.6B-hf")

        language = str(payload.get("audio_language", "") or "").strip()
        transcripts: list[dict[str, str]] = []
        for attachment in attachments:
            path, _content_type = audio_attachment_asset(self.workspace, str(attachment.get("id", "")))
            text = transcribe_local_audio(model_id, path, language=language)
            transcripts.append({"name": str(attachment.get("name", "音频")), "text": text})

        if not require_message:
            return dict(payload), {
                "model_id": model_id,
                "attachments": attachments,
                "transcripts": transcripts,
            }

        messages = [dict(item) for item in list(payload.get("messages", []) or []) if isinstance(item, dict)]
        user_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if str(messages[index].get("role", "")).lower() == "user"),
            -1,
        )
        if user_index < 0:
            raise ValueError("语音消息缺少用户问题")
        original = str(messages[user_index].get("content", "") or "").strip()
        transcript_text = "\n\n".join(
            f"[{item['name']}]\n{item['text']}" for item in transcripts if str(item.get("text", "")).strip()
        ).strip()
        if not transcript_text:
            raise ValueError("语音识别没有得到可用文字，请重新录音或选择其他音频文件")
        prefix = f"{original}\n\n" if original else "请根据下面的语音内容回答：\n\n"
        messages[user_index]["content"] = f"{prefix}[本地语音转写]\n{transcript_text}"
        prepared = dict(payload)
        prepared["messages"] = messages
        return prepared, {
            "model_id": model_id,
            "attachments": attachments,
            "transcripts": transcripts,
        }

    def _direct_chat_request(self, payload: dict[str, Any], *, attachment_context: str = "") -> _DirectChatRequest:
        raw_messages = list(payload.get("messages", []) or [])
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            raw_content = item.get("content", "")
            if isinstance(raw_content, str):
                content = raw_content.strip()
            elif isinstance(raw_content, list):
                # Historical provider-specific image blocks are never carried
                # forward. Preserve only their explicit text; current-turn
                # images travel in the separate canonical Pi field.
                content = "\n".join(
                    str(block.get("text", "")).strip()
                    for block in raw_content
                    if isinstance(block, dict)
                    and str(block.get("type", "")) == "text"
                    and str(block.get("text", "")).strip()
                )
            else:
                content = ""
            if role in {"system", "user", "assistant"} and content:
                message = {"role": role, "content": content}
                for key in ("message_id", "id", "context_ref", "context_kind"):
                    if item.get(key):
                        message[key] = item[key]
                messages.append(message)
        if not messages or messages[-1]["role"] != "user":
            raise ValueError("messages must end with a user message")

        if attachment_context:
            messages.insert(
                max(0, len(messages) - 1),
                {
                    "role": "system",
                    "content": (
                        "The user attached the following local material. Answer from it when relevant, "
                        "name the attachment and preserve any page labels. Do not invent missing facts.\n\n"
                        + attachment_context
                    ),
                    "context_kind": "attachment",
                    "context_ref": f"attachment:{hashlib.sha256(attachment_context.encode('utf-8')).hexdigest()}",
                },
            )

        settings = load_settings(self.workspace)
        active = dict(settings.get("active_model", {}) or {})
        provider = next(
            (item for item in settings.get("providers", []) if item.get("id") == active.get("provider_id")),
            None,
        )
        if provider is None or not provider.get("enabled", True):
            raise ValueError("当前没有可用的对话模型")

        provider_id = str(provider.get("id", ""))
        provider_kind = str(provider.get("kind", ""))
        requested_provider_id = provider_id
        requested_model_id = str(active.get("model_id", "")).strip()
        model_id = requested_model_id
        raw_images = list(payload.get("images", []) or [])
        vision_route: dict[str, Any] = {}
        if raw_images:
            # Image input is a capability request, not a requirement that the
            # user manually switch away from their preferred text model.  The
            # selected vision endpoint is used only for this turn; subsequent
            # turns keep the model the user chose in the composer.
            try:
                selected_vision = select_vision_route(
                    self.workspace,
                    settings,
                    active_provider_id=requested_provider_id,
                    active_model_id=requested_model_id,
                )
            except Exception:
                selected_vision = None
            if selected_vision is not None:
                provider = dict(selected_vision["provider"])
                provider_id = str(selected_vision["provider_id"])
                provider_kind = str(selected_vision["provider_kind"])
                model_id = str(selected_vision["model_id"])
                vision_route = {
                    "mode": str(selected_vision.get("mode", "local")),
                    "provider_id": provider_id,
                    "provider_name": str(selected_vision.get("provider_name", provider_id)),
                    "model_id": model_id,
                    "requested_provider_id": requested_provider_id,
                    "requested_model_id": requested_model_id,
                }
            else:
                # No visual endpoint is available.  Keep a usable text route
                # when one exists and let the OCR fallback turn the image into
                # explicit text instead of failing before the model request.
                try:
                    text_route = select_text_route(
                        self.workspace,
                        settings,
                        active_provider_id=requested_provider_id,
                        active_model_id=requested_model_id,
                    )
                except Exception:
                    text_route = None
                if text_route is not None:
                    provider = dict(text_route["provider"])
                    provider_id = str(text_route["provider_id"])
                    provider_kind = str(text_route["provider_kind"])
                    model_id = str(text_route["model_id"])
                vision_route = {
                    "mode": "ocr-fallback",
                    "requested_provider_id": requested_provider_id,
                    "requested_model_id": requested_model_id,
                    "text_provider_id": provider_id,
                    "text_model_id": model_id,
                }
        managed = str(provider.get("auth_mode", "")) == "managed"
        if managed:
            api_key = "scansci-managed-gateway"
        elif provider_kind == "local" or str(provider.get("auth_mode", "")) == "local":
            api_key = str(provider.get("api_key", "")) or "local"
        else:
            api_key = get_provider_api_key(self.workspace, provider_id)
        if not api_key:
            raise ValueError("当前对话模型尚未配置 API Key")

        if provider_id.startswith("local-runtime-") and str(provider.get("logo", "")) == "ollama":
            status = ollama_status(str(provider.get("base_url", "")))
            if not status.get("reachable"):
                raise ValueError("Ollama 尚未运行，请先在设置 → 本地模型中启动 Ollama")
            if not status.get("model_ready") and vision_route.get("mode") != "ocr-fallback":
                raise ValueError("MiniCPM-V 4.6 尚未安装，请先在设置 → 本地模型 → 模型市场下载")
        responses_enabled = bool(provider.get("responses_enabled", False))
        requested_api_surface = str(
            payload.get("api_surface", provider.get("api_surface", "chat_completions"))
            or "chat_completions"
        ).strip().lower()
        api_surface = select_api_surface(
            requested_api_surface,
            provider_kind=provider_kind,
            provider_id=provider_id,
            model=model_id,
            responses_enabled=responses_enabled,
        )
        manifest: RunManifest | None = None
        try:
            manifest = RunManifest.start(
                self.workspace,
                harness="pi",
                provider=provider_id,
                model=model_id,
                api_surface=api_surface,
                session_id=str(payload.get("pi_session_id", "") or ""),
                prompt=str(messages[-1].get("content", "")),
                timeout_seconds=45.0,
            )
        except OSError:
            manifest = None
        chat_mode = str(payload.get("chat_mode", "general") or "general").strip().lower()
        if chat_mode not in {"general", "writing", "knowledge", "slides"}:
            chat_mode = "general"
        selection = resolve_skill_selection(payload, messages)
        system_context, selected_skills = build_agent_system_context(
            self.workspace,
            model_id=model_id,
            provider_name=str(provider.get("name", provider_id)),
            chat_mode=chat_mode,
            selected_ids=list(selection.selected_ids),
            selection=selection,
        )
        system_context += (
            "\n\nConversation budget: answer the final user request directly. "
            "Honor every explicit length, sentence-count, and format constraint. "
            "For an ordinary question, prefer a complete answer under 500 Chinese characters "
            "or 350 English words unless the user explicitly asks for a detailed treatment. "
            "Do not add tutorials, alternative versions, capability descriptions, or background "
            "that the user did not request. Preserve scientific uncertainty and do not strengthen "
            "correlation into causation. In scientific rewriting, remove or explicitly qualify "
            "claims such as 'prove', 'certain', 'all', 'only cause', '证明', '肯定', '所有', and "
            "'唯一原因' unless the user supplied a design that actually identifies that causal claim. "
            "When the user requests an exact number of numbered sections or a terminal marker, "
            "write exactly the requested sections, keep each section focused, then end immediately. "
            "When the user asks for actionable checks in each section, put every check on its own "
            "Markdown list line beginning with '- ' so the checklist is readable and complete. "
            "Never pad an answer with repeated words or filler. "
            "For a frequentist 95% confidence interval, describe long-run procedure coverage: do not "
            "say that a fixed parameter has a 95% probability of lying in the single observed interval."
        )
        messages.insert(0, {"role": "system", "content": system_context})
        image_attachments: list[dict[str, Any]] = []
        pi_images: list[dict[str, str]] = []
        if provider_id == "local-huggingface":
            # Registering the selected snapshot starts only a loopback server;
            # the component now runs a bounded generation probe before returning
            # so model-loading failures can be handled before a user request.
            provider_kind = "openai-compatible"
            provider = dict(provider)
            try:
                provider["base_url"] = ensure_local_transformers_runtime(model_id)
            except (RuntimeError, ValueError) as error:
                # A local model can be present on disk but unusable because of
                # VRAM/page-file pressure.  Interactive image questions should
                # still work through OCR + the selected text model; do not make
                # the user discover this as a generic HTTP 502.
                text_route = select_text_route(
                    self.workspace,
                    settings,
                    active_provider_id=requested_provider_id,
                    active_model_id=requested_model_id,
                )
                if text_route is None:
                    raise RuntimeError(
                        f"本地视觉模型 {model_id} 无法启动：{error}。"
                        "请在设置 → 本地模型检查显存或选择可用的视觉模型。"
                    ) from error
                provider = dict(text_route["provider"])
                provider_id = str(text_route["provider_id"])
                provider_kind = str(text_route["provider_kind"])
                model_id = str(text_route["model_id"])
                vision_route.update(
                    {
                        "mode": "ocr-fallback",
                        "fallback_reason": "local_vision_unavailable",
                        "fallback_message": " ".join(str(error).split())[:500],
                        "text_provider_id": provider_id,
                        "text_model_id": model_id,
                    }
                )
                managed = str(provider.get("auth_mode", "")) == "managed"
                api_key = (
                    "scansci-managed-gateway"
                    if managed
                    else get_provider_api_key(self.workspace, provider_id)
                )
                if not api_key:
                    raise ValueError("本地视觉模型不可用，且文字模型尚未配置 API Key") from error
                responses_enabled = bool(provider.get("responses_enabled", False))
                api_surface = select_api_surface(
                    requested_api_surface,
                    provider_kind=provider_kind,
                    provider_id=provider_id,
                    model=model_id,
                    responses_enabled=responses_enabled,
                )
        if raw_images:
            image_attachments = persist_image_attachments(self.workspace, raw_images)
            text = str(messages[-1].get("content", ""))
            if vision_route.get("mode") == "ocr-fallback":
                blocks = vision_image_blocks(self.workspace, image_attachments)
                ocr = ocr_image_blocks(blocks, workspace=self.workspace, settings=settings)
                ocr_text = str(ocr.get("text", "")).strip()
                vision_route.update(
                    {
                        "route": "pi-ocr-text",
                        "degraded": True,
                        "image_count": len(image_attachments),
                        "ocr_backend": str(ocr.get("backend", "unavailable")),
                        "ocr_available": bool(ocr.get("available")),
                        "ocr_message": str(ocr.get("message", ""))[:500],
                    }
                )
                if ocr_text:
                    image_context = f"[本地 OCR 图片文字]\n{ocr_text}"
                else:
                    image_context = (
                        "[图片处理提示]\n当前没有可用的视觉模型，且本机 OCR 未提取到文字。"
                        "请明确告诉用户无法可靠读取图片内容，不要猜测图像中的事实。"
                    )
                messages[-1]["content"] = f"{text}\n\n{image_context}".strip()
            elif provider_kind in {"anthropic-compatible", "anthropic"}:
                blocks = vision_image_blocks(self.workspace, image_attachments)
                content: list[dict[str, Any]] = [{"type": "text", "text": text}]
                content.extend(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": block["mime_type"], "data": block["data"]},
                    }
                    for block in blocks
                )
                messages[-1]["content"] = content
            else:
                pi_images = pi_image_blocks(self.workspace, image_attachments)
                vision_route.update(
                    {
                        "route": "pi-native",
                        "degraded": str(vision_route.get("mode", "")) != "selected",
                        "image_count": len(pi_images),
                    }
                )
                # Explicit legacy callers still use the direct provider
                # transport. Preserve its provider-native image blocks while
                # the default Pi route receives canonical ``pi_images``.
                if str(payload.get("agent_harness", "")).strip().lower() == "legacy":
                    blocks = vision_image_blocks(self.workspace, image_attachments)
                    content = [{"type": "text", "text": text}]
                    content.extend(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{block['mime_type']};base64,{block['data']}"},
                        }
                        for block in blocks
                    )
                    messages[-1]["content"] = content
        model_record = next(
            (
                dict(item)
                for item in list(provider.get("models", []) or [])
                if isinstance(item, dict) and str(item.get("id", "")) == model_id
            ),
            {},
        )
        model_runtime = descriptor_from_model_record(
            provider_id=provider_id,
            provider_kind=provider_kind,
            model_id=model_id,
            model_record=model_record,
            api_surface=api_surface,
        )
        if bool(vision_route.get("degraded")):
            route_reason = (
                "vision_input_degraded_to_ocr_text"
                if str(vision_route.get("route", "")) == "pi-ocr-text"
                else "vision_input_routed_to_alternate_model"
            )
            model_runtime = replace(
                model_runtime,
                degraded=True,
                degradation_reasons=tuple(dict.fromkeys([
                    *model_runtime.degradation_reasons,
                    route_reason,
                ])),
            )
        if pi_images and not model_runtime.supports_images:
            raise PiMultimodalUnavailable(
                "The declared alternate model descriptor does not support image input"
            )
        thinking_mode = (
            managed_glm_thinking_mode(
                thinking_level=payload.get("thinking_level"),
                messages=messages,
            )
            if provider_id == "scansci-managed" and model_id == "glm-4.7-flash"
            else None
        )
        return _DirectChatRequest(
            messages=messages,
            provider_id=provider_id,
            provider_name=str(provider.get("name", provider_id)),
            provider_kind=provider_kind,
            base_url=str(provider.get("base_url", "")),
            api_key=api_key,
            model_id=model_id,
            api_surface=api_surface,
            responses_enabled=responses_enabled,
            previous_response_id=(str(payload.get("previous_response_id", "")).strip() or None),
            thinking_mode=thinking_mode,
            session=managed_gateway_session() if managed else None,
            chat_mode=chat_mode,
            thinking_level=normalize_thinking_level(payload.get("thinking_level")),
            selected_skills=selected_skills,
            notebook_id=str(payload.get("notebook_id", "")).strip(),
            notebook_ids=[
                str(value).strip()
                for value in list(payload.get("notebook_ids", []) or [])
                if str(value).strip()
            ][:12],
            knowledge_scope=dict(payload.get("knowledge_scope", {}) or {}),
            manifest=manifest,
            vision_route=vision_route,
            image_attachments=image_attachments,
            pi_images=pi_images,
            model_runtime=model_runtime.to_dict(),
        )

    @staticmethod
    def _direct_process_trace(
        chat_request: _DirectChatRequest,
        *,
        had_attachments: bool,
        web_search_mode: str = "off",
        task_mode: str = "general",
    ) -> list[dict[str, str]]:
        """Return only meaningful, user-verifiable work events.

        Routing, prompt construction and ordinary model generation are
        implementation details, not a task progress log.  A greeting or a
        direct explanation therefore gets an empty trace and no expandable
        processing card.
        """

        trace: list[dict[str, str]] = []
        if had_attachments:
            trace.append({"title": "读取附件", "detail": "已将本轮添加的本地材料解析为可引用上下文。"})
        explicit = [
            str(item.get("id", ""))
            for item in chat_request.selected_skills
            if item.get("provenance") == "explicit" and item.get("status") == "loaded"
        ]
        inferred = [
            str(item.get("id", ""))
            for item in chat_request.selected_skills
            if item.get("provenance") == "inferred" and item.get("status") == "hint"
        ]
        suppressed = [
            str(item.get("id", ""))
            for item in chat_request.selected_skills
            if item.get("provenance") == "suppressed"
        ]
        if explicit:
            trace.append({
                "title": "应用 Skill",
                "detail": f"已预载用户显式选择的 Skill：{'、'.join(explicit)}（provenance=explicit）。",
            })
        if inferred:
            trace.append({
                "title": "Skill 候选",
                "detail": f"仅提供推断候选，尚未激活：{'、'.join(inferred)}（provenance=inferred）。",
            })
        if suppressed:
            trace.append({
                "title": "Skill 选择",
                "detail": f"已抑制重复或超限 Skill：{'、'.join(suppressed)}（provenance=suppressed）。",
            })
        return trace

    def _submit(self, run_id: str) -> None:
        with self._thread_lock:
            existing = self._threads.get(run_id)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(target=self._worker_entry, args=(run_id,), daemon=True, name=f"scansci-{run_id}")
            self._threads[run_id] = thread
            thread.start()

    def _worker_entry(self, run_id: str) -> None:
        try:
            self._execute_run(run_id)
        finally:
            with self._thread_lock:
                if self._threads.get(run_id) is threading.current_thread():
                    self._threads.pop(run_id, None)

    @staticmethod
    def _transient_stage_error(error: Exception) -> bool:
        """Retry only errors likely to succeed without changing user intent."""

        if isinstance(error, (ValueError, FileNotFoundError, PermissionError)):
            return False
        if isinstance(error, (TimeoutError, ConnectionError)):
            return True
        normalized = f"{type(error).__name__}: {error}".lower()
        permanent_signals = (
            "401",
            "402",
            "403",
            "authentication",
            "invalid api",
            "insufficient balance",
            "insufficient credit",
            "permission denied",
            "not supported",
            "unsupported",
        )
        if any(signal in normalized for signal in permanent_signals):
            return False
        transient_signals = (
            "timeout",
            "timed out",
            "429",
            "502",
            "503",
            "504",
            "temporar",
            "connection reset",
            "connection aborted",
            "connectionerror",
            "database is locked",
            "resource busy",
        )
        return any(signal in normalized for signal in transient_signals)

    def _retry_stage(self, run_id: str, stage_key: str, error: Exception) -> bool:
        """Retry one transient stage failure in the current durable worker.

        Returns True if the stage was re-queued for retry, False if retries
        are exhausted and the failure should be permanent.
        """
        if not self._transient_stage_error(error):
            return False
        key = f"{run_id}:{stage_key}"
        count = self._stage_retry_counts.get(key, 0) + 1
        self._stage_retry_counts[key] = count
        if count > 1:
            return False
        self.store.prepare_stage_retry(run_id, stage_key, error, attempt=count)
        return True

    def _execute_run(self, run_id: str) -> None:
        try:
            self.store.begin_run(run_id)
            run = self.store.get_run(run_id)
            output_artifact_id = str(run.get("output_artifact_id", ""))
            for stage in run["stages"]:
                if stage["status"] == "completed":
                    output_artifact_id = str(stage.get("output", {}).get("artifact_id", output_artifact_id))
                    continue
                if self.store.stop_requested(run_id):
                    if self.store.pause_requested(run_id):
                        self.store.mark_paused(run_id)
                    else:
                        self.store.mark_cancelled(run_id)
                    return
                stage_key = str(stage["key"])
                self.store.start_stage(run_id, stage_key)
                self._model_event_context.value = {
                    "run_id": run_id,
                    "stage_id": str(stage.get("stage_id", "")),
                    "stage_key": stage_key,
                }
                try:
                    if stage["kind"] == "planner":
                        output = self._plan(self.store.get_run(run_id))
                        summary = str(output["summary"])
                        self.store.complete_stage(run_id, stage_key, summary=summary, output=output)
                        # Pause for user approval on workflows whose LLM-generated
                        # plan drives expensive tool stages (ZCode EnterPlanMode pattern).
                        if self._pause_for_plan_review and str(run["workflow_type"]) in _NEEDS_CONFIRMATION_WORKFLOWS:
                            self.store.set_interaction(
                                run_id,
                                {
                                    "interaction_id": f"plan-{run_id}-{stage_key}",
                                    "kind": "plan",
                                    "summary": summary,
                                    "payload": output,
                                    "actions": [
                                        {"id": "approve", "label": "批准并继续"},
                                        {"id": "revise", "label": "修改计划"},
                                        {"id": "cancel", "label": "取消任务"},
                                    ],
                                },
                            )
                            return
                    elif stage["kind"] == "tool":
                        output = self._run_tool(self.store.get_run(run_id), stage)
                        summary = self._tool_summary(self.store.get_run(run_id), output)
                        self.store.complete_stage(run_id, stage_key, summary=summary, output=output)
                    elif stage["kind"] == "verification":
                        output = self._verify(self.store.get_run(run_id))
                        summary = "证据与引用核验通过" if output.get("passed") else "已标记证据不足或待人工核验"
                        self.store.complete_stage(run_id, stage_key, summary=summary, output=output)
                    else:
                        artifact = self._deliver(self.store.get_run(run_id))
                        output_artifact_id = str(artifact["artifact_id"])
                        output = {"artifact_id": output_artifact_id, "artifact_type": artifact["artifact_type"]}
                        summary = str(artifact["summary"] or "研究产物已保存")
                        self.store.complete_stage(run_id, stage_key, summary=summary, output=output)
                except _RunCancelled:
                    if self.store.pause_requested(run_id):
                        self.store.mark_paused(run_id, summary="任务已暂停；已完成的批次会在继续时复用")
                    else:
                        self.store.mark_cancelled(run_id, summary="本地语义索引已停止；已完成的批次会在恢复时复用")
                    return
                except Exception as error:  # noqa: BLE001 - persisted for resume and UI inspection
                    # L3: auto-retry recoverable stage failures once with a
                    # bounded second attempt before marking the run failed.
                    if self._retry_stage(run_id, stage_key, error):
                        # `_submit` cannot start another worker while this
                        # thread is still alive. Continue in this worker so a
                        # transient failure cannot leave the run stuck queued.
                        self._execute_run(run_id)
                        return
                    self.store.set_recovery(run_id, _structured_recovery(error, stage_key=stage_key))
                    self.store.fail_stage(run_id, stage_key, error)
                    return
                finally:
                    self._model_event_context.value = None
                if self.store.stop_requested(run_id):
                    latest = self.store.get_run(run_id)
                    remaining = [item for item in latest["stages"] if item["status"] != "completed"]
                    if remaining:
                        if self.store.pause_requested(run_id):
                            self.store.mark_paused(run_id)
                        else:
                            self.store.mark_cancelled(run_id)
                        return
            self.store.complete_run(run_id, output_artifact_id=output_artifact_id)
            # The advisor is read-only: it records gaps and outcome metrics
            # after durable delivery, but never retries tools or overrides the
            # host's permission contract.
            advisor = review_research_run(self.store.get_run(run_id))
            self.store.append_event(
                run_id,
                event_type="advisor.reviewed",
                summary=f"Advisor verdict: {advisor['verdict']}",
                payload={
                    "schema_version": advisor["schema_version"],
                    "verdict": advisor["verdict"],
                    "findings": advisor["findings"],
                    "recommended_next_action": advisor["recommended_next_action"],
                },
            )
            self.store.append_event(
                run_id,
                event_type="task.metrics",
                summary="Task metrics recorded",
                payload=dict(advisor["metrics"]),
            )
        except Exception as error:  # noqa: BLE001 - last-resort worker boundary
            try:
                current = self.store.get_run(run_id).get("current_stage", "")
                if current:
                    self.store.set_recovery(run_id, _structured_recovery(error, stage_key=str(current)))
                    self.store.fail_stage(run_id, str(current), error)
            except Exception:
                return

    def _run_tool(self, run: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
        payload = dict(run["input"])
        if run.get("notebook_id"):
            payload["notebook_id"] = str(run["notebook_id"])
        workflow_type = str(run["workflow_type"])
        tool_name = str(stage.get("tool_name", ""))
        call_id = self.store.begin_tool_call(
            str(run["run_id"]), str(stage["key"]), tool_name=tool_name, input_payload=payload
        )
        try:
            if workflow_type == "evidence_index":
                notebook = self._notebook(str(run["notebook_id"]))
                evidence_db = self._evidence_db_for_notebook(notebook)
                rows = load_embedding_cache_rows(evidence_db)
                local_evidence = self._local_evidence_stack(
                    evidence_db,
                    quality_profile=self._retrieval_quality(payload, default="balanced"),
                    embedding_only=True,
                )
                # A lexical/hash fallback keeps ordinary search usable while
                # the optional neural stack is unavailable, but it must never
                # be persisted under the Qwen cache identity.  Doing so would
                # make a low-quality emergency path look like a completed
                # semantic index and prevent the real model from rebuilding it
                # later.  Test doubles and third-party providers that do not
                # declare this flag remain compatible.
                if (
                    local_evidence.metadata.get("qwen_embedding_active") is False
                    and not local_evidence.metadata.get("remote_embedding_active")
                ):
                    reasons = "；".join(
                        str(item)
                        for item in list(local_evidence.metadata.get("fallback_reasons", []) or [])
                        if str(item).strip()
                    )
                    detail = f"：{reasons}" if reasons else ""
                    raise RuntimeError(
                        "Qwen3 嵌入模型或本地推理组件尚未就绪，已保留基础关键词检索，"
                        f"不会写入伪语义索引{detail}"
                    )

                def report_progress(completed: int, total: int) -> None:
                    fraction = completed / total if total else 1.0
                    self.store.update_stage_progress(
                        str(run["run_id"]),
                        str(stage["key"]),
                        fraction=fraction,
                        summary=(
                            f"正在为「{notebook.get('title') or '当前知识库'}」优化语义检索："
                            f"{completed}/{total} 条原文证据"
                        ),
                        output={
                            "completed": completed,
                            "total": total,
                            "notebook_title": str(notebook.get("title") or "当前知识库"),
                        },
                    )

                cache = prewarm_embedding_cache(
                    evidence_db,
                    rows,
                    provider=local_evidence.embedding_provider,
                    cache_batch_size=DEFAULT_NEURAL_CACHE_BATCH_SIZE,
                    progress_callback=report_progress,
                    cancel_requested=lambda: self.store.stop_requested(str(run["run_id"])),
                )
                output = {
                    "message": f"本地语义索引已就绪：{int(cache.get('completed', 0))}/{int(cache.get('total', 0))} 条证据",
                    "vector_cache": cache,
                    "retrieval_runtime": local_evidence.metadata,
                }
                if cache.get("cancelled"):
                    self.store.cancel_tool_call(call_id, output)
                    raise _RunCancelled(output["message"])
            elif workflow_type == "ask":
                payload["_research_run_id"] = str(run["run_id"])
                output = self.answer_sync(payload)
            elif workflow_type == "literature_review" and str(stage.get("key", "")) == "research":
                notebook = self._notebook(str(run["notebook_id"]))
                evidence_db = self._evidence_db_for_notebook(notebook)
                # A literature review over a selected notebook is a local
                # knowledge workflow, not task-acquired full text.  Reuse the
                # notebook's configured local evidence stack so its cached
                # embedding model and neural reranker actually participate in
                # section retrieval.  The stack still records an explicit
                # hashing/lexical fallback when the optional local models are
                # unavailable; generation remains on the writing model.
                local_evidence = self._local_evidence_stack(
                    evidence_db,
                    quality_profile=self._retrieval_quality(payload, default="precision"),
                )
                output = retrieve_review_evidence(
                    evidence_db,
                    str(payload["question"]),
                    chat_client=self._writing_chat_client(),
                    limit=int(payload.get("limit", 14) or 14),
                    embedding_provider=local_evidence.embedding_provider,
                    reranker=local_evidence.reranker,
                    retrieval_runtime=local_evidence.metadata,
                    source_doc_ids=list(payload.get("source_doc_ids", []) or []),
                    writing_brief=dict(payload.get("writing_brief", {}) or {}),
                )
            elif workflow_type == "literature_review" and str(stage.get("key", "")) == "synthesize":
                research = self._stage_output(run, "research")
                output = synthesize_literature_review(
                    research,
                    chat_client=self._writing_chat_client(),
                    reader_url_builder=self._reader_url,
                )
            elif workflow_type == "academic_search":
                plan = self._stage_output(run, "plan")
                local_evidence = self._academic_discovery_stack()
                output = search_academic_papers(
                    str(plan["topic"]),
                    query_variants=list(plan.get("query_variants", []) or []),
                    required_terms=list(plan.get("required_terms", []) or []),
                    limit=int(payload.get("limit", 24) or 24),
                    per_source=int(payload.get("per_source", 10) or 10),
                    provider_names=list(plan.get("providers", []) or []),
                    year_from=(
                        self._optional_year(payload.get("year_from"))
                        or self._optional_year(plan.get("year_from"))
                    ),
                    embedding_provider=local_evidence.embedding_provider,
                    reranker=local_evidence.reranker,
                    cancel_requested=lambda: self.store.stop_requested(str(run["run_id"])),
                )
                output["retrieval_runtime"] = dict(local_evidence.metadata)
                output["search_plan"] = plan
            elif workflow_type == "deep_research" and str(stage.get("key", "")) == "discover":
                plan = self._stage_output(run, "plan")
                # Public academic discovery has its own retrieval stack.  It
                # cannot depend on a selected library being non-empty (or on
                # a library being selected at all).
                local_evidence = self._academic_discovery_stack()
                searcher = FederatedAcademicSearch(
                    providers=[build_academic_provider(name) for name in self._academic_provider_names(payload)],
                    embedding_provider=local_evidence.embedding_provider,
                    reranker=local_evidence.reranker,
                )

                def report_discovery_progress(completed: int, total: int, query_record: dict[str, Any]) -> None:
                    self.store.update_stage_progress(
                        str(run["run_id"]),
                        str(stage["key"]),
                        fraction=min(0.95, completed / max(1, total)),
                        summary=f"已完成 {completed} 个检索式：{str(query_record.get('query', ''))[:80]}",
                        output={"completed_queries": completed, "planned_queries": total, "last_query": query_record},
                    )

                try:
                    output = run_discovery_loop(
                        str(payload["question"]),
                        plan,
                        searcher=searcher,
                        result_limit=int(payload.get("limit", 36) or 36),
                        per_source=int(payload.get("per_source", 10) or 10),
                        year_from=self._optional_year(payload.get("year_from")),
                        max_rounds=int(payload.get("max_search_rounds", 2) or 2),
                        cancel_requested=lambda: self.store.stop_requested(str(run["run_id"])),
                        progress_callback=report_discovery_progress,
                    )
                except InterruptedError as error:
                    self.store.cancel_tool_call(call_id, {"cancelled": True})
                    raise _RunCancelled(str(error)) from error
                output["retrieval_runtime"] = dict(local_evidence.metadata)
            elif workflow_type == "novelty_check" and str(stage.get("key", "")) == "discover":
                plan = self._stage_output(run, "plan")
                notebook = self._notebook(str(run["notebook_id"])) if run.get("notebook_id") else {"notebook_id": ""}
                evidence_db = self._evidence_db_for_notebook(notebook)
                local_evidence = self._local_evidence_stack(evidence_db)
                searcher = FederatedAcademicSearch(
                    providers=[build_academic_provider(name) for name in self._academic_provider_names(payload)],
                    embedding_provider=local_evidence.embedding_provider,
                    reranker=local_evidence.reranker,
                )

                def report_novelty_progress(completed: int, total: int, query_record: dict[str, Any]) -> None:
                    self.store.update_stage_progress(
                        str(run["run_id"]),
                        str(stage["key"]),
                        fraction=min(0.95, completed / max(1, total)),
                        summary=f"已完成 {completed} 个查新检索式：{str(query_record.get('query', ''))[:80]}",
                        output={"completed_queries": completed, "planned_queries": total, "last_query": query_record},
                    )

                novelty_question = f"{payload['problem']}；主张的新颖性：{payload['novelty']}"
                try:
                    output = run_discovery_loop(
                        novelty_question,
                        plan,
                        searcher=searcher,
                        result_limit=int(payload.get("limit", 40) or 40),
                        per_source=int(payload.get("per_source", 10) or 10),
                        year_from=self._optional_year(payload.get("year_from")),
                        max_rounds=int(payload.get("max_search_rounds", 2) or 2),
                        cancel_requested=lambda: self.store.stop_requested(str(run["run_id"])),
                        progress_callback=report_novelty_progress,
                    )
                except InterruptedError as error:
                    self.store.cancel_tool_call(call_id, {"cancelled": True})
                    raise _RunCancelled(str(error)) from error
                output["retrieval_runtime"] = dict(local_evidence.metadata)
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "discover":
                plan = self._stage_output(run, "plan")
                notebook = self._notebook(str(run["notebook_id"])) if run.get("notebook_id") else {"notebook_id": ""}
                evidence_db = self._evidence_db_for_notebook(notebook)
                local_evidence = self._local_evidence_stack(evidence_db)
                searcher = FederatedAcademicSearch(
                    providers=[build_academic_provider(name) for name in self._academic_provider_names(payload)],
                    embedding_provider=local_evidence.embedding_provider,
                    reranker=local_evidence.reranker,
                )

                def report_idea_progress(completed: int, total: int, query_record: dict[str, Any]) -> None:
                    self.store.update_stage_progress(
                        str(run["run_id"]),
                        str(stage["key"]),
                        fraction=min(0.95, completed / max(1, total)),
                        summary=f"已完成 {completed} 个研究构思检索式：{str(query_record.get('query', ''))[:80]}",
                        output={"completed_queries": completed, "planned_queries": total, "last_query": query_record},
                    )

                try:
                    output = run_discovery_loop(
                        str(payload["direction"]),
                        plan,
                        searcher=searcher,
                        result_limit=int(payload.get("limit", 40) or 40),
                        per_source=int(payload.get("per_source", 10) or 10),
                        year_from=self._optional_year(payload.get("year_from")),
                        max_rounds=int(payload.get("max_search_rounds", 2) or 2),
                        cancel_requested=lambda: self.store.stop_requested(str(run["run_id"])),
                        progress_callback=report_idea_progress,
                    )
                except InterruptedError as error:
                    self.store.cancel_tool_call(call_id, {"cancelled": True})
                    raise _RunCancelled(str(error)) from error
                output["retrieval_runtime"] = dict(local_evidence.metadata)
            elif workflow_type == "deep_research" and str(stage.get("key", "")) == "acquire":
                output = self._acquire_deep_research_fulltext(run, stage, call_id=call_id)
            elif workflow_type == "novelty_check" and str(stage.get("key", "")) == "acquire":
                output = self._acquire_deep_research_fulltext(run, stage, call_id=call_id)
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "acquire":
                output = self._acquire_deep_research_fulltext(run, stage, call_id=call_id)
            elif workflow_type == "deep_research" and str(stage.get("key", "")) == "research":
                output = self._task_fulltext_deep_research_evidence(run)
                if str(output.get("phase", "")) != "retrieval":
                    abstract_fallback = self._external_deep_research_evidence(run)
                    abstract_fallback["task_evidence"] = dict(output.get("task_evidence", {}) or {})
                    abstract_fallback["fulltext_fallback_reason"] = str(output.get("error", ""))
                    output = abstract_fallback
            elif workflow_type == "deep_research" and str(stage.get("key", "")) == "synthesize":
                plan = self._stage_output(run, "plan")
                discovery = self._stage_output(run, "discover")
                acquisition = self._stage_output(run, "acquire")
                research = self._stage_output(run, "research")
                external_research = str(research.get("phase", "")) == "external_abstracts"
                task_fulltext_research = (
                    str(research.get("phase", "")) == "retrieval"
                    and str(research.get("evidence_status", "")) == "task_acquired_fulltext"
                )
                if str(research.get("phase", "")) in {"retrieval", "external_abstracts"} and len(list(research.get("evidence", []) or [])) >= 3:
                    try:
                        report = synthesize_literature_review(
                            research,
                            chat_client=self._writing_chat_client(),
                            reader_url_builder=(
                                None
                                if external_research
                                else lambda doc_id, anchor: self._task_evidence_reader_url(
                                    str(run["run_id"]), doc_id, anchor
                                )
                                if task_fulltext_research
                                else self._reader_url(doc_id, anchor)
                            ),
                        )
                        output = enrich_deep_research_result(
                            report,
                            plan=plan,
                            discovery=discovery,
                            acquisition=acquisition,
                        )
                        if external_research:
                            output["evidence_status"] = "external_source_abstracts"
                            output["evidence_notice"] = (
                                "本报告基于本次外部研究任务收集的公开学术来源摘要；"
                                "它不会写入或依赖你的知识库。"
                            )
                        elif task_fulltext_research:
                            output["evidence_status"] = "task_acquired_fulltext"
                            output["evidence_level"] = "fulltext"
                            output["task_evidence"] = dict(research.get("task_evidence", {}) or {})
                            output["evidence_notice"] = str(research.get("evidence_notice", ""))
                    except Exception as error:
                        output = discovery_only_result(
                            str(payload["question"]),
                            plan=plan,
                            discovery=discovery,
                            acquisition=acquisition,
                            reason=f"Evidence-grounded synthesis failed: {type(error).__name__}: {error}"[:1000],
                        )
                else:
                    output = discovery_only_result(
                        str(payload["question"]),
                        plan=plan,
                        discovery=discovery,
                        acquisition=acquisition,
                        reason=str(research.get("error", "Indexed evidence was insufficient.")),
                    )
            elif workflow_type == "novelty_check" and str(stage.get("key", "")) == "research":
                notebook = self._notebook(str(run["notebook_id"])) if run.get("notebook_id") else {"notebook_id": ""}
                evidence_db = self._evidence_db_for_notebook(notebook)
                novelty_question = (
                    f"查找与以下研究主张最接近的既有工作及其问题设定、核心机制、关键洞见和应用领域。"
                    f"问题：{payload['problem']}。主张：{payload['novelty']}。"
                )
                if not evidence_db.is_file():
                    output = {
                        "phase": "retrieval_unavailable",
                        "question": novelty_question,
                        "error": "No indexed full text is available after lawful acquisition.",
                        "evidence": [],
                    }
                else:
                    local_evidence = self._local_evidence_stack(
                        evidence_db,
                        quality_profile=self._retrieval_quality(payload, default="precision"),
                    )
                    try:
                        output = retrieve_review_evidence(
                            evidence_db,
                            novelty_question,
                            chat_client=self._writing_chat_client(),
                            limit=int(payload.get("evidence_limit", payload.get("limit", 20)) or 20),
                            embedding_provider=local_evidence.embedding_provider,
                            reranker=local_evidence.reranker,
                            retrieval_runtime=local_evidence.metadata,
                            source_doc_ids=list(payload.get("source_doc_ids", []) or []),
                            writing_brief={"focus": "逐论文比较问题设定、核心机制、关键洞见和应用领域"},
                        )
                    except Exception as error:
                        output = {
                            "phase": "retrieval_unavailable",
                            "question": novelty_question,
                            "error": f"{type(error).__name__}: {error}"[:1000],
                            "evidence": [],
                        }
            elif workflow_type == "novelty_check" and str(stage.get("key", "")) == "assess":
                try:
                    assessment_client = self._writing_chat_client()
                except Exception:
                    assessment_client = None
                output = assess_novelty_evidence(
                    self._stage_output(run, "plan"),
                    self._stage_output(run, "discover"),
                    self._stage_output(run, "research"),
                    chat_client=assessment_client,
                )
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "evidence":
                notebook = self._notebook(str(run["notebook_id"])) if run.get("notebook_id") else {"notebook_id": ""}
                evidence_db = self._evidence_db_for_notebook(notebook)
                idea_question = (
                    f"围绕研究方向“{payload['direction']}”，检索现有方法、失败模式、承重瓶颈、评价指标、"
                    "负结果和适用边界的逐句全文证据。"
                )
                if not evidence_db.is_file():
                    output = {"phase": "retrieval_unavailable", "question": idea_question, "error": "No indexed full text is available.", "evidence": []}
                else:
                    local_evidence = self._local_evidence_stack(
                        evidence_db,
                        quality_profile=self._retrieval_quality(payload, default="precision"),
                    )
                    try:
                        output = retrieve_review_evidence(
                            evidence_db,
                            idea_question,
                            chat_client=self._writing_chat_client(),
                            limit=int(payload.get("evidence_limit", payload.get("limit", 24)) or 24),
                            embedding_provider=local_evidence.embedding_provider,
                            reranker=local_evidence.reranker,
                            retrieval_runtime=local_evidence.metadata,
                            source_doc_ids=list(payload.get("source_doc_ids", []) or []),
                            writing_brief={"focus": "现有方法、失败模式、承重瓶颈、评价指标和负结果"},
                        )
                    except Exception as error:
                        output = {
                            "phase": "retrieval_unavailable",
                            "question": idea_question,
                            "error": f"{type(error).__name__}: {error}"[:1000],
                            "evidence": [],
                        }
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "diagnose":
                try:
                    idea_client = self._writing_chat_client()
                except Exception:
                    idea_client = None
                output = diagnose_research_bottleneck(
                    self._stage_output(run, "plan"),
                    self._stage_output(run, "evidence"),
                    chat_client=idea_client,
                )
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "generate":
                try:
                    idea_client = self._writing_chat_client()
                except Exception:
                    idea_client = None
                output = generate_research_candidate(
                    self._stage_output(run, "plan"),
                    self._stage_output(run, "diagnose"),
                    self._stage_output(run, "evidence"),
                    chat_client=idea_client,
                )
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "coherence":
                try:
                    idea_client = self._writing_chat_client()
                except Exception:
                    idea_client = None
                output = audit_candidate_coherence(self._stage_output(run, "generate"), chat_client=idea_client)
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "falsify":
                output = audit_candidate_falsifiability(self._stage_output(run, "generate"))
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "implement":
                try:
                    idea_client = self._writing_chat_client()
                except Exception:
                    idea_client = None
                output = audit_candidate_implementability(self._stage_output(run, "generate"), chat_client=idea_client)
            elif workflow_type == "research_idea" and str(stage.get("key", "")) == "assemble":
                output = assemble_research_idea_card(
                    self._stage_output(run, "plan"),
                    self._stage_output(run, "evidence"),
                    self._stage_output(run, "diagnose"),
                    self._stage_output(run, "generate"),
                    self._stage_output(run, "coherence"),
                    self._stage_output(run, "falsify"),
                    self._stage_output(run, "implement"),
                )
            elif workflow_type == "journal_search":
                output = search_journals(str(payload["query"]), limit=int(payload.get("limit", 8)))
            elif workflow_type == "citation_analysis":
                output = analyze_references(str(payload["text"]), mode=str(payload.get("mode", "full")))
            elif workflow_type == "doi_verification":
                output = verify_doi_metadata(str(payload["doi"]), expected_title=str(payload.get("title", "")))
            elif workflow_type == "paper_atlas":
                output = search_paper_atlas(str(payload["query"]))
            elif workflow_type == "paper_search_download" and str(stage.get("key", "")) == "search":
                output = search_papers_for_download(
                    str(payload.get("query", "")),
                    author=str(payload.get("author", "")),
                    limit=int(payload.get("limit", 20) or 20),
                    sort=str(payload.get("sort", "relevance")),
                    year_from=self._optional_year(payload.get("year_from")),
                    year_to=self._optional_year(payload.get("year_to")),
                    timeout=float(payload.get("search_timeout", 60) or 60),
                )
                if not output.get("identifiers"):
                    raise RuntimeError("未找到可下载的 DOI 或 arXiv 文献；请缩小主题范围、确认作者姓名，或改用学术搜索查看全部候选论文")
            elif workflow_type == "paper_search_download" and str(stage.get("key", "")) == "execute":
                run_id = str(run["run_id"])
                stage_key = str(stage["key"])
                search_result = self._stage_output(run, "search")

                def report_search_download_progress(state: dict[str, Any]) -> None:
                    completed = int(state.get("completed", 0))
                    total = int(state.get("total", 0))
                    failed = int(state.get("failed", 0))
                    self.store.update_stage_progress(
                        run_id,
                        stage_key,
                        fraction=min(0.95, completed / max(1, total)),
                        summary=f"检索后下载：成功 {completed}/{total}，失败 {failed}",
                        output={**state, "papers": list(search_result.get("items") or [])},
                    )

                try:
                    output = download_papers(
                        list(search_result.get("identifiers") or []),
                        workspace=self.workspace,
                        strategy=str(payload.get("strategy", "oa_first")),
                        timeout=float(payload.get("download_timeout", 180) or 180),
                        on_progress=report_search_download_progress,
                        cancel_check=lambda: self.store.stop_requested(run_id),
                    )
                except _BatchCancelled:
                    raise _RunCancelled("检索后文献下载已取消")
                output["papers"] = list(search_result.get("items") or [])
                output["search"] = {
                    "query": search_result.get("query", ""),
                    "author": search_result.get("author", ""),
                    "sort": search_result.get("sort", "relevance"),
                    "total": search_result.get("total", 0),
                }
                output = self._index_downloaded_files(output, notebook_id=str(run.get("notebook_id", "")))
            elif workflow_type == "paper_download":
                output = download_paper(
                    str(payload["identifier"]),
                    workspace=self.workspace,
                    strategy=str(payload.get("strategy", "oa_first")),
                )
                output = self._index_downloaded_files(output, notebook_id=str(run.get("notebook_id", "")))
            elif workflow_type == "paper_download_batch":
                run_id = str(run["run_id"])
                stage_key = str(stage["key"])

                def report_batch_progress(state: dict[str, Any]) -> None:
                    completed = int(state.get("completed", 0))
                    total = int(state.get("total", 0))
                    failed = int(state.get("failed", 0))
                    rotations = int(state.get("tor_rotations", 0))
                    summary = f"批量获取：成功 {completed}/{total}，失败 {failed}"
                    if rotations:
                        summary += f"，已轮换 {rotations} 次"
                    self.store.update_stage_progress(
                        run_id,
                        stage_key,
                        fraction=min(0.95, completed / max(1, total)),
                        summary=summary,
                        output=state,
                    )

                # Optional Tor circuit rotation for IP protection. Fail-soft:
                # if Tor cannot start, the batch proceeds over a direct link.
                tor_manager = None
                env_overrides = None
                rotate_circuit = None
                rotate_every = 0
                if payload.get("use_tor"):
                    try:
                        from .tor_runtime import TorCircuitManager

                        # Transport selection: snowflake (CDN domain-fronting) is
                        # the most reliable on restricted networks where both
                        # direct relays AND obfs4 bridge IPs are blocked. The
                        # older tor_bridges flag maps to obfs4 for back-compat.
                        transport = str(payload.get("tor_transport", "")).strip()
                        if not transport:
                            transport = "obfs4" if payload.get("tor_bridges", True) else "none"
                        # Route Tor through the user's local proxy (Clash/V2Ray)
                        # when available — the most reliable path on networks
                        # that block direct Tor relays. tor_proxy="" disables.
                        tor_proxy = payload.get("tor_proxy", True)
                        tor_manager = TorCircuitManager(
                            self.workspace, transport=transport, upstream_proxy=tor_proxy
                        )
                        if tor_manager.ensure_tor():
                            env_overrides = {"TOR_PROXY": tor_manager.socks_proxy_url}
                            rotate_circuit = tor_manager.rotate_circuit
                            rotate_every = int(payload.get("rotate_every", 3) or 3)
                            initial_ip = tor_manager.get_exit_ip()
                            self.store.update_stage_progress(
                                run_id,
                                stage_key,
                                fraction=0.0,
                                summary=f"Tor 已就绪，当前出口 IP：{initial_ip or '未知'}，每 {rotate_every} 篇轮换" if initial_ip else f"Tor 已就绪，每 {rotate_every} 篇轮换",
                                output={"tor_exit_ip": initial_ip},
                            )
                        else:
                            if tor_manager.upstream_proxy:
                                hint = f"Tor 启动失败（已探测代理 {tor_manager.upstream_proxy}），尝试切换传输方式后重试"
                            else:
                                hint = "Tor 启动失败，请确保本地代理（Clash/V2Ray）已运行，或切换 obfs4/snowflake 传输方式"
                            self.store.update_stage_progress(
                                run_id,
                                stage_key,
                                fraction=0.0,
                                summary=hint,
                                output={"tor_available": False, "tor_hint": hint},
                            )
                            tor_manager = None
                    except Exception as tor_error:  # noqa: BLE001 - Tor is optional
                        suggestion = _tor_failure_hint(type(tor_error).__name__)
                        self.store.update_stage_progress(
                            run_id,
                            stage_key,
                            fraction=0.0,
                            summary=f"Tor 不可用（{type(tor_error).__name__}），{suggestion}",
                            output={"tor_available": False, "tor_hint": suggestion},
                        )
                        tor_manager = None

                try:
                    output = download_papers(
                        list(payload.get("identifiers") or []),
                        workspace=self.workspace,
                        strategy=str(payload.get("strategy", "oa_first")),
                        timeout=float(payload.get("download_timeout", 180) or 180),
                        on_progress=report_batch_progress,
                        cancel_check=lambda: self.store.stop_requested(run_id),
                        env_overrides=env_overrides,
                        rotate_circuit=rotate_circuit,
                        rotate_every=rotate_every,
                    )
                except _BatchCancelled:
                    raise _RunCancelled("批量文献下载已取消")
                finally:
                    if tor_manager is not None:
                        tor_manager.stop()
                output = self._index_downloaded_files(output, notebook_id=str(run.get("notebook_id", "")))
            elif workflow_type == "ppt_outline":
                output = build_ppt_outline(
                    self._notebook(str(run["notebook_id"])),
                    topic=str(payload.get("topic", "")),
                    template_id=str(payload.get("template_id", "")),
                )
            elif workflow_type == "ppt_project":
                output = create_ppt_project(
                    self._notebook(str(run["notebook_id"])),
                    workspace=self.workspace,
                    topic=str(payload.get("topic", "")),
                    template_id=str(payload.get("template_id", "")),
                )
            elif workflow_type == "pdf_to_ppt":
                try:
                    slide_client = self._slides_chat_client()
                except ValueError:
                    # Source-first generation remains useful while a user is
                    # offline or has not configured a dedicated slide model.
                    slide_client = None

                def report_slide_progress(fraction: float, summary: str) -> None:
                    self.store.update_stage_progress(
                        str(run["run_id"]),
                        str(stage["key"]),
                        fraction=fraction,
                        summary=summary,
                        output={"phase": "easyslides", "summary": summary},
                    )

                output = create_source_slide_deck(
                    workspace=self.workspace,
                    sources=list(payload.get("source_files", []) or []),
                    topic=str(payload.get("topic", "")),
                    template_id=str(payload.get("template_id", "")),
                    chat_client=slide_client,
                    on_progress=report_slide_progress,
                )
            else:
                raise ValueError(f"No tool executor for workflow: {workflow_type}")
        except _RunCancelled:
            raise
        except Exception as error:
            self.store.fail_tool_call(call_id, error)
            raise
        self.store.complete_tool_call(call_id, output)
        return output

    def _index_downloaded_files(
        self,
        output: dict[str, Any],
        *,
        notebook_id: str,
    ) -> dict[str, Any]:
        """Attach downloaded PDFs to the selected notebook's evidence store."""

        result = dict(output)
        files = [str(path) for path in list(result.get("files", []) or []) if str(path).strip()]
        if not files:
            result.setdefault("evidence_status", "downloaded_without_files")
            return result
        if not str(notebook_id).strip():
            result["evidence_status"] = "downloaded_unindexed"
            result["evidence_reason"] = "未选择知识库；请在打开知识库后重新下载或导入文件"
            return result
        try:
            imported = import_library_files(
                self.workspace,
                self.evidence_db,
                notebook_id=str(notebook_id),
                file_paths=files,
            )
        except Exception as error:  # keep a successful download usable even if indexing fails
            result["evidence_status"] = "downloaded_unindexed"
            result["evidence_error"] = f"{type(error).__name__}: {error}"[:500]
            return result
        result["imported"] = imported
        result["evidence_status"] = "indexed_fulltext"
        try:
            # Full-text import is already useful without dense vectors.  Do
            # not turn a follow-up download into an unexpected GPU rebuild
            # when the notebook has compatible cached vectors to reuse.
            index_run = self.start_evidence_index(str(notebook_id), automatic=True)
        except Exception as error:  # neural prewarm is optional after full-text indexing
            result["evidence_index_error"] = f"{type(error).__name__}: {error}"[:500]
        else:
            if index_run is not None:
                result["index_run"] = index_run
        return result

    def _acquire_deep_research_fulltext(
        self,
        run: dict[str, Any],
        stage: dict[str, Any],
        *,
        call_id: str,
    ) -> dict[str, Any]:
        payload = dict(run.get("input", {}) or {})
        discovery = self._stage_output(run, "discover")
        candidates = list(discovery.get("items", []) or [])
        maximum = max(0, min(12, int(payload.get("max_fulltext", 4) or 0)))
        strategy = str(payload.get("strategy", "oa_first") or "oa_first")
        notebook = self._notebook(str(run["notebook_id"])) if run.get("notebook_id") else {"notebook_id": "", "sources": []}
        known_dois = {
            str(dict(source).get("doi", "")).strip().casefold()
            for source in list(notebook.get("sources", []) or [])
            if str(dict(source).get("doi", "")).strip()
        }
        eligible = sorted(
            candidates,
            key=lambda item: (
                0 if str(item.get("oa_url", "")).strip() or str(item.get("arxiv_id", "")).strip() else 1,
                -len(list(item.get("sources", []) or [])),
                -float(item.get("score", 0.0) or 0.0),
            ),
        )
        acquired: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        existing: list[dict[str, str]] = []
        files: list[str] = []
        task_root = task_evidence_root(self.workspace, str(run["run_id"]))
        download_root = task_root / "downloads"
        attempted = 0
        for item in eligible:
            if len(acquired) >= maximum:
                break
            doi = str(item.get("doi", "")).strip()
            arxiv_id = str(item.get("arxiv_id", "")).strip()
            identifier = arxiv_id or doi
            if not identifier:
                continue
            if doi and doi.casefold() in known_dois:
                existing.append({"doi": doi, "title": str(item.get("title", ""))})
                continue
            if self.store.stop_requested(str(run["run_id"])):
                partial = {
                    "cancelled": True,
                    "attempted": attempted,
                    "acquired": acquired,
                    "failures": failures,
                    "files": files,
                }
                self.store.cancel_tool_call(call_id, partial)
                raise _RunCancelled("Academic full-text acquisition cancelled")
            attempted += 1
            try:
                downloaded = download_paper(
                    identifier,
                    workspace=self.workspace,
                    strategy=strategy,
                    timeout=float(payload.get("download_timeout", 120) or 120),
                    _output_dir=download_root if str(run.get("workflow_type", "")) == "deep_research" else None,
                )
                new_files = [str(path) for path in list(downloaded.get("files", []) or [])]
                files.extend(new_files)
                acquired.append(
                    {
                        "title": str(item.get("title", "")),
                        "doi": doi,
                        "arxiv_id": arxiv_id,
                        "files": new_files,
                        "source": str(downloaded.get("source", "")),
                    }
                )
            except Exception as error:
                failures.append(
                    {
                        "title": str(item.get("title", "")),
                        "identifier": identifier,
                        "error": f"{type(error).__name__}: {error}"[:500],
                    }
                )
            self.store.update_stage_progress(
                str(run["run_id"]),
                str(stage["key"]),
                fraction=min(0.9, attempted / max(1, maximum)),
                summary=f"已尝试 {attempted}/{maximum} 篇开放全文，成功 {len(acquired)} 篇",
                output={"attempted": attempted, "acquired": acquired, "failures": failures, "files": files},
            )

        imported: dict[str, Any] = {}
        if files and str(notebook.get("notebook_id", "")) and str(run.get("workflow_type", "")) != "deep_research":
            imported = import_library_files(
                self.workspace,
                self.evidence_db,
                notebook_id=str(notebook["notebook_id"]),
                file_paths=files,
            )
        task_evidence: dict[str, Any] = {}
        if files and str(run.get("workflow_type", "")) == "deep_research":
            task_evidence = build_task_fulltext_evidence(
                self.workspace,
                str(run["run_id"]),
                acquired,
            )
        evidence_level = str(task_evidence.get("evidence_level", ""))
        return {
            "strategy": strategy,
            "requested_max": maximum,
            "attempted": attempted,
            "acquired": acquired,
            "acquired_count": len(acquired),
            "failed": failures,
            "failed_count": len(failures),
            "existing_fulltext": existing,
            "files": files,
            "imported": imported,
            "task_evidence": task_evidence,
            "evidence_status": (
                "task_fulltext_indexed"
                if evidence_level == "fulltext"
                else "task_fulltext_partial"
                if task_evidence
                else "indexed_fulltext"
                if imported
                else "existing_or_unavailable"
            ),
        }

    def _task_fulltext_deep_research_evidence(self, run: dict[str, Any]) -> dict[str, Any]:
        """Retrieve claim evidence from full texts acquired for this run only."""

        payload = dict(run.get("input", {}) or {})
        question = str(payload.get("question", "")).strip()
        requested_writing_brief = dict(payload.get("writing_brief", {}) or {})
        requested_focus = str(requested_writing_brief.get("focus", "")).strip()
        fulltext_focus = (
            "Prioritize claims supported by the acquired full text. "
            "State evidence limits and disagreements explicitly."
        )
        acquisition = self._stage_output(run, "acquire")
        task_evidence = dict(acquisition.get("task_evidence", {}) or {})
        evidence_db = Path(str(task_evidence.get("evidence_db", "") or ""))
        index = dict(task_evidence.get("index", {}) or {})
        if str(task_evidence.get("evidence_level", "")) != "fulltext" or not evidence_db.is_file():
            return {
                "phase": "task_fulltext_unavailable",
                "question": question,
                "error": "Acquired full text did not produce enough verified task evidence.",
                "evidence": [],
                "task_evidence": task_evidence,
            }
        try:
            local_evidence = self._task_fulltext_evidence_stack()
            output = retrieve_review_evidence(
                evidence_db,
                question,
                chat_client=self._writing_chat_client(),
                limit=int(payload.get("evidence_limit", payload.get("limit", 20)) or 20),
                embedding_provider=local_evidence.embedding_provider,
                reranker=local_evidence.reranker,
                retrieval_runtime=local_evidence.metadata,
                writing_brief={
                    **requested_writing_brief,
                    "focus": " ".join(part for part in (requested_focus, fulltext_focus) if part),
                },
            )
        except Exception as error:
            return {
                "phase": "task_fulltext_unavailable",
                "question": question,
                "error": f"{type(error).__name__}: {error}"[:1000],
                "evidence": [],
                "task_evidence": task_evidence,
            }
        output.update(
            {
                "source_scope": {
                    "kind": "task_acquired_fulltext",
                    "label": "Full text acquired and indexed for this Deep Research task",
                },
                "evidence_status": "task_acquired_fulltext",
                "evidence_level": "fulltext",
                "task_evidence": task_evidence,
                "evidence_notice": (
                    f"This report cites {int(index.get('documents', 0) or 0)} task-acquired full texts and "
                    f"{int(index.get('spans', 0) or 0)} traceable evidence fragments. It does not read or write your personal knowledge libraries."
                ),
            }
        )
        return output

    def _external_deep_research_evidence(self, run: dict[str, Any]) -> dict[str, Any]:
        """Create a temporary, source-linked evidence set for web deep research.

        This deliberately uses only abstracts returned by traceable academic
        providers.  A title, DOI, or search snippet is never promoted to a
        scientific claim.  The resulting source set lives with the research
        run and is not imported into, or retrieved from, a user library.
        """

        payload = dict(run.get("input", {}) or {})
        question = str(payload.get("question", "")).strip()
        requested_writing_brief = dict(payload.get("writing_brief", {}) or {})
        requested_focus = str(requested_writing_brief.get("focus", "")).strip()
        abstract_focus = "只可根据所给公开来源摘要作答；不能把题录信息或检索排序写成研究结论。"
        plan = self._stage_output(run, "plan")
        discovery = self._stage_output(run, "discover")
        evidence: list[dict[str, Any]] = []
        used_documents: set[str] = set()
        for item in list(discovery.get("items", []) or []):
            record = dict(item) if isinstance(item, dict) else {}
            title = " ".join(str(record.get("title", "")).split())
            abstract = " ".join(str(record.get("abstract", "")).split())
            doi = str(record.get("doi", "")).strip()
            if not title or len(abstract) < 80:
                continue
            document_key = doi.casefold() or re.sub(r"\W+", "-", title.casefold()).strip("-")
            if not document_key or document_key in used_documents:
                continue
            used_documents.add(document_key)
            source_url = str(record.get("oa_url") or record.get("url") or "").strip()
            if not re.match(r"https?://", source_url, flags=re.IGNORECASE):
                source_url = f"https://doi.org/{doi}" if doi else ""
            index = len(evidence) + 1
            evidence.append(
                {
                    "citation_id": f"S{index}",
                    "evidence_id": f"external:{run.get('run_id', 'research')}:{index}",
                    "doc_id": f"external:{document_key[:96]}",
                    "paper": title,
                    "section": "Published abstract",
                    "section_kind": "abstract",
                    "doi": doi,
                    "exact_quote": abstract[:4000],
                    "html_path": f"external://academic-source/{index}",
                    "html_anchor": "abstract",
                    "original_url": source_url,
                    "source_href": source_url,
                    "confidence": float(record.get("score", 0.0) or 0.0),
                }
            )
            if len(evidence) >= 18:
                break

        if len(evidence) < 3:
            return {
                "phase": "external_evidence_unavailable",
                "question": question,
                "error": "Fewer than three traceable public abstracts were available for claim-level synthesis.",
                "evidence": evidence,
                "evidence_status": "discovery_leads",
            }

        perspectives = [dict(item) for item in list(plan.get("perspectives", []) or []) if isinstance(item, dict)]
        sections = [
            {
                "id": f"external-{index + 1}",
                "title": str(item.get("title") or f"Research perspective {index + 1}"),
                "objective": str(item.get("question") or question),
                "keywords": list(item.get("keywords", []) or []),
                "citation_ids": self._external_perspective_citation_ids(item, evidence),
            }
            for index, item in enumerate(perspectives[:4])
        ]
        if not sections:
            sections = [
                {
                    "id": "external-overview",
                    "title": "Research evidence overview",
                    "objective": question,
                    "keywords": [],
                    "citation_ids": [str(row["citation_id"]) for row in evidence[:8]],
                }
            ]
        return {
            "phase": "external_abstracts",
            "question": question,
            "review_plan": {"title": str(plan.get("title") or question), "sections": sections},
            "section_results": sections,
            "evidence": evidence,
            "retrieval_summary": {
                "section_count": len(sections),
                "document_count": len({row["doc_id"] for row in evidence}),
                "evidence_count": len(evidence),
            },
            "source_scope": {
                "kind": "external_academic_sources",
                "label": "本次任务收集的公开学术来源",
            },
            "writing_brief": {
                **requested_writing_brief,
                "focus": " ".join(part for part in (requested_focus, abstract_focus) if part),
            },
            "evidence_status": "external_source_abstracts",
            "evidence_notice": "本次深度研究使用公开学术来源摘要，不读取或写入任何个人知识库。",
        }

    @staticmethod
    def _external_perspective_citation_ids(
        perspective: dict[str, Any],
        evidence: list[dict[str, Any]],
        *,
        limit: int = 8,
    ) -> list[str]:
        """Bind each report section to the public abstracts that match it."""

        query = " ".join(
            [
                str(perspective.get("title", "") or ""),
                str(perspective.get("question", "") or ""),
                " ".join(str(value) for value in list(perspective.get("keywords", []) or [])),
            ]
        )
        stopwords = {
            "about", "and", "are", "evidence", "for", "from", "how", "methods", "research",
            "study", "system", "systems", "that", "the", "this", "what", "which", "with",
        }
        terms = {token for token in lexical_tokens(query) if len(token) > 1 and token not in stopwords}
        ranked: list[tuple[float, int, str]] = []
        for position, row in enumerate(evidence):
            title_terms = set(lexical_tokens(str(row.get("paper", "") or "")))
            quote_terms = set(lexical_tokens(str(row.get("exact_quote", "") or "")))
            score = len(terms & title_terms) * 2.0 + len(terms & quote_terms) * 0.5
            if score > 0:
                ranked.append((score, -position, str(row.get("citation_id", ""))))
        ranked.sort(reverse=True)
        selected = [citation_id for _score, _position, citation_id in ranked[: max(1, int(limit))] if citation_id]
        if selected:
            return selected
        return [
            str(row.get("citation_id", ""))
            for row in evidence[: min(4, max(1, int(limit)))]
            if row.get("citation_id")
        ]

    def _plan(self, run: dict[str, Any]) -> dict[str, Any]:
        spec = _WORKFLOWS[str(run["workflow_type"])]
        if str(run["workflow_type"]) == "research_idea":
            payload = dict(run.get("input", {}) or {})
            try:
                chat_client = self._writing_chat_client()
            except Exception:
                chat_client = None
            plan = plan_research_idea(
                str(payload.get("direction", "")),
                constraints=str(payload.get("constraints", "")),
                chat_client=chat_client,
                max_queries=int(payload.get("max_search_queries", 5) or 5),
            )
            plan["summary"] = "已界定研究方向、成功标准、约束和证据检索视角"
            return plan
        if str(run["workflow_type"]) == "novelty_check":
            payload = dict(run.get("input", {}) or {})
            try:
                chat_client = self._writing_chat_client()
            except Exception:
                chat_client = None
            plan = plan_novelty_check(
                str(payload.get("problem", "")),
                str(payload.get("novelty", "")),
                chat_client=chat_client,
                max_queries=int(payload.get("max_search_queries", 5) or 5),
            )
            plan["summary"] = "已将新颖性主张拆成问题设定、核心机制、关键洞见和应用领域四轴"
            return plan
        if str(run["workflow_type"]) == "deep_research":
            payload = dict(run.get("input", {}) or {})
            try:
                chat_client = self._writing_chat_client()
            except Exception:
                chat_client = None
            plan = plan_deep_research(
                str(payload.get("question", "")),
                chat_client=chat_client,
                max_queries=int(payload.get("max_search_queries", 5) or 5),
            )
            plan["summary"] = "已拆解研究问题、检索式、纳入边界与证据视角"
            return plan
        if str(run["workflow_type"]) == "academic_search":
            payload = dict(run.get("input", {}) or {})
            explicit_providers = (
                self._academic_provider_names(payload)
                if payload.get("providers") is not None
                else None
            )
            has_reviewed_plan = isinstance(payload.get("search_plan"), dict)
            try:
                chat_client = None if has_reviewed_plan else self._writing_chat_client()
            except Exception:
                chat_client = None
            plan = plan_academic_search(
                str(payload.get("raw_query") or payload.get("query", "")),
                explicit_providers=explicit_providers,
                chat_client=chat_client,
            )
            if has_reviewed_plan:
                plan = review_academic_search_plan(plan, dict(payload["search_plan"]))
            plan["query"] = str(plan["topic"])
            plan["artifact_contract"] = spec["artifact_type"]
            plan["summary"] = (
                f"已提取主题“{plan['normalized_topic']}”，"
                f"使用 {len(list(plan.get('query_variants', []) or []))} 条检索式和 "
                f"{len(list(plan.get('providers', []) or []))} 个学术来源"
            )
            return plan
        source_scope = "当前资料库" if run["notebook_id"] else "当前工作区"
        return {
            "summary": f"已确定 {spec['label']} 的输入、{source_scope}与交付格式",
            "workflow_type": run["workflow_type"],
            "artifact_contract": spec["artifact_type"],
            "stages": [stage.title for stage in spec["stages"]],
        }

    def _verify(self, run: dict[str, Any]) -> dict[str, Any]:
        result = self._tool_output(run)
        verification = dict(result.get("citation_verification", {}) or {})
        if str(run.get("workflow_type", "")) == "research_idea":
            gates = dict(result.get("quality_gates", {}) or {})
            required_gates = ("grounded_bottleneck", "single_candidate", "coherence", "falsifiability", "implementability", "citation_integrity")
            return {
                "passed": (
                    str(result.get("status", "")) == "ready_for_novelty_check"
                    and bool(verification.get("passed", False))
                    and all(bool(gates.get(key, False)) for key in required_gates)
                ),
                "no_unsupported_claims": bool(verification.get("passed", False)),
                "insufficient_evidence": not bool(gates.get("grounded_bottleneck", False)),
                "citation_count": int(dict(result.get("reader_answer", {}) or {}).get("citation_count", 0) or 0),
                "evidence_status": str(result.get("status", "")),
                "novelty_checked": bool(gates.get("novelty", False)),
                "details": {"citation_verification": verification, "quality_gates": gates},
            }
        if str(run.get("workflow_type", "")) == "novelty_check":
            adequacy = dict(result.get("evidence_adequacy", {}) or {})
            return {
                "passed": (
                    bool(verification.get("passed", False))
                    and bool(adequacy.get("sufficient", False))
                    and str(result.get("status", "")) == "assessed"
                ),
                "no_unsupported_claims": bool(verification.get("passed", False)),
                "insufficient_evidence": not bool(adequacy.get("sufficient", False)),
                "citation_count": int(dict(result.get("reader_answer", {}) or {}).get("citation_count", 0) or 0),
                "evidence_status": str(result.get("status", "")),
                "assessment_mode": str(result.get("assessment_mode", "")),
                "details": verification,
            }
        reader = dict(result.get("reader_answer", {}) or {})
        answer_payload = dict(result.get("answer", {}) or {})
        adequacy_payload = dict(result.get("adequacy", {}) or {})
        answerability = str(
            adequacy_payload.get("answerability") or answer_payload.get("answerability") or ""
        ).strip()
        # ``needs_review`` is a deliberate intermediate state: evidence can
        # be rendered with a warning when citation verification passes.  Only
        # an explicit insufficient answer or a non-reviewable failed adequacy
        # remains a hard stop for the workflow.
        insufficient = bool(answer_payload.get("insufficient_evidence")) or (
            adequacy_payload.get("is_sufficient") is False and answerability != "needs_review"
        )
        task_fulltext_ready = True
        verification_details: dict[str, Any] = verification
        if str(run.get("workflow_type", "")) == "deep_research" and str(result.get("evidence_status", "")) == "task_acquired_fulltext":
            task_evidence = dict(result.get("task_evidence", {}) or {})
            index = dict(task_evidence.get("index", {}) or {})
            quality = dict(task_evidence.get("quality", {}) or {})
            task_fulltext_ready = (
                str(task_evidence.get("evidence_level", "")) == "fulltext"
                and int(index.get("documents", 0) or 0) >= 2
                and int(index.get("spans", 0) or 0) >= 3
                and task_evidence_claim_ready(quality)
            )
            verification_details = {**verification, "task_fulltext_ready": task_fulltext_ready}
        verification_result = {
            "passed": bool(verification.get("passed", False)) and not insufficient and task_fulltext_ready,
            "no_unsupported_claims": bool(verification.get("passed", False)),
            "insufficient_evidence": insufficient,
            "citation_count": int(reader.get("citation_count", 0) or 0),
            "evidence_status": str(result.get("evidence_status", "")),
            "details": verification_details,
        }
        if answerability:
            verification_result["answerability"] = answerability
            verification_result["needs_review"] = answerability == "needs_review"
        return verification_result

    def _deliver(self, run: dict[str, Any]) -> dict[str, Any]:
        spec = _WORKFLOWS[str(run["workflow_type"])]
        result = self._tool_output(run)
        subagent = dict(dict(run.get("metadata", {}) or {}).get("subagent", {}) or {})
        scientific_child = (
            str(dict(run.get("metadata", {}) or {}).get("runtime", ""))
            == "scansci-scientific-subagent.v1"
        )
        if scientific_child:
            role_id = str(subagent.get("role", ""))
            trusted_uris = {str(run.get("uri", ""))}
            parent_run_id = str(run.get("parent_run_id", "") or "").strip()
            owned_runs = [run]
            if parent_run_id:
                owned_runs.append(self.store.get_run(parent_run_id))
            for owned_run in owned_runs:
                trusted_uris.add(str(owned_run.get("uri", "")))
                for artifact in list(owned_run.get("artifacts", []) or []):
                    if not isinstance(artifact, dict):
                        continue
                    trusted_uris.add(str(artifact.get("uri", "")))
                    trusted_uris.update(
                        str(link.get("uri", ""))
                        for link in list(artifact.get("evidence_links", []) or [])
                        if isinstance(link, dict) and str(link.get("uri", ""))
                    )
            evidence_db = self.evidence_db
            notebook_id = str(run.get("notebook_id", "") or "").strip()
            if notebook_id:
                try:
                    evidence_db = self._evidence_db_for_notebook(self._notebook(notebook_id))
                except FileNotFoundError:
                    evidence_db = self.evidence_db
            for candidate_uri in list(result.get("_scientific_allowed_uris", []) or []):
                parsed = parse_resource_uri(candidate_uri)
                segments = list(dict(parsed or {}).get("segments", []) or [])
                if str(dict(parsed or {}).get("kind", "")) != "evidence" or len(segments) != 2:
                    continue
                canonical = make_resource_uri("evidence", segments[0], segments[1])
                if canonical != str(candidate_uri):
                    continue
                trusted_uris.update(
                    _owned_scientific_evidence_uris(
                        "search_local_evidence",
                        {
                            "hits": [{
                                "doc_id": segments[0],
                                "evidence_id": segments[1],
                                "library_index": 0,
                            }]
                        },
                        [evidence_db],
                    )
                )
            trusted_uris.discard("")
            validation = validate_subagent_result(
                result,
                role_id=role_id,
                allowed_uris=trusted_uris,
            )
            if not validation["valid"]:
                raise RuntimeError(
                    "Scientific sub-agent handoff must be one valid JSON object: "
                    + ", ".join(str(item) for item in list(validation.get("errors", []) or []))
                )
            result = {**result, "subagent_handoff": dict(validation["result"] or {})}
        summary = self._artifact_summary(run, result)
        evidence_links: list[dict[str, Any]] = []
        if run["workflow_type"] in {"ask", "literature_review", "deep_research", "novelty_check", "research_idea"}:
            evidence_links = [
                {
                    **dict(item),
                    "source_href": str(
                        dict(item).get("source_href")
                        or dict(item).get("reader_url")
                        or dict(item).get("original_url")
                        or ""
                    ),
                    "relationship": "supports",
                }
                for item in list(dict(result.get("reader_answer", {}) or {}).get("citations", []) or [])
            ]
        if scientific_child:
            evidence_links = []
            handoff = dict(result.get("subagent_handoff", {}) or {})
            for resource_uri in list(handoff.get("evidence_uris", []) or []):
                parsed = parse_resource_uri(resource_uri)
                segments = list(dict(parsed or {}).get("segments", []) or [])
                if str(dict(parsed or {}).get("kind", "")) != "evidence" or not segments:
                    continue
                evidence_links.append(
                    {
                        "doc_id": str(segments[0]),
                        "evidence_id": str(segments[1]) if len(segments) > 1 else "",
                        "relationship": "supports",
                        "metadata": {"source_uri": str(resource_uri)},
                    }
                )
        file_path = str(
            result.get("file_path", "")
            or result.get("output_path", "")
            or result.get("project_path", "")
            or result.get("path", "")
        )
        return self.store.create_artifact(
            str(run["run_id"]),
            artifact_type=str(spec["artifact_type"]),
            title=str(run["title"]),
            summary=summary,
            payload=result,
            evidence_links=evidence_links,
            file_path=file_path,
        )

    @staticmethod
    def _tool_output(run: dict[str, Any]) -> dict[str, Any]:
        tool_stages = [stage for stage in run["stages"] if stage["kind"] == "tool" and stage["status"] == "completed"]
        if not tool_stages:
            raise RuntimeError("工具阶段尚未产生可交付结果")
        return dict(tool_stages[-1].get("output", {}) or {})

    @staticmethod
    def _stage_output(run: dict[str, Any], stage_key: str) -> dict[str, Any]:
        stage = next(
            (
                item
                for item in list(run.get("stages", []) or [])
                if str(item.get("key", "")) == stage_key and item.get("status") == "completed"
            ),
            None,
        )
        if stage is None:
            raise RuntimeError(f"前置阶段尚未完成：{stage_key}")
        return dict(stage.get("output", {}) or {})

    @staticmethod
    def _tool_summary(run: dict[str, Any], result: dict[str, Any]) -> str:
        workflow = str(run["workflow_type"])
        if workflow == "evidence_index":
            return str(result.get("message", "本地语义索引已就绪"))
        if workflow == "academic_search":
            gate = dict(result.get("quality_gate", {}) or {})
            accepted = int(gate.get("accepted_count", result.get("count", 0)) or 0)
            rejected = int(gate.get("rejected_count", 0) or 0)
            if str(gate.get("status", "")) in {"insufficient", "no_candidates"}:
                return f"已检索并筛除 {rejected} 条不匹配候选，未交付不相关文献"
            return (
                f"已用 {len(list(result.get('query_variants', []) or []))} 条检索式搜索，"
                f"保留 {accepted} 条高相关候选" + (f"，筛除 {rejected} 条" if rejected else "")
            )
        if workflow == "pdf_to_ppt":
            slides = list(dict(result.get("outline", {}) or {}).get("slides", []) or [])
            slide_count = int(result.get("slide_count", 0) or 0) or len(slides) + 1
            return f"已从 {int(result.get('source_count', 0) or 0)} 份材料生成 {slide_count} 页可编辑 PPTX"
        if workflow == "deep_research" and result.get("evidence_status") == "task_acquired_fulltext":
            trace = dict(result.get("research_trace", {}) or {})
            task_index = dict(dict(result.get("task_evidence", {}) or {}).get("index", {}) or {})
            document_count = int(
                trace.get("fulltext_indexed_documents", task_index.get("documents", 0)) or 0
            )
            evidence_span_count = int(
                trace.get("fulltext_evidence_spans", task_index.get("spans", 0)) or 0
            )
            return (
                f"已完成外部深度研究：基于 {document_count} 篇任务获取的全文，"
                f"建立 {evidence_span_count} 个可回跳证据片段"
            )
        if workflow == "deep_research" and result.get("evidence_status") == "external_source_abstracts":
            references = list(dict(result.get("reader_answer", {}) or {}).get("citations", []) or [])
            return f"已完成外部深度研究：基于 {len(references)} 条公开学术来源整理报告，未读取或写入知识库"
        if workflow == "deep_research" and result.get("evidence_status") == "discovery_leads":
            return (
                f"已汇集 {int(result.get('candidate_count', 0) or 0)} 条候选记录，"
                f"去重后 {int(result.get('deduplicated_count', 0) or 0)} 条"
            )
        if workflow == "deep_research" and "acquired_count" in result:
            return (
                f"已获取 {int(result.get('acquired_count', 0) or 0)} 篇合法全文，"
                f"失败 {int(result.get('failed_count', 0) or 0)} 篇"
            )
        if workflow == "deep_research" and result.get("phase") == "discovery_only":
            return "全文证据不足，已保留检索线索但未生成科学结论"
        if workflow == "novelty_check" and result.get("evidence_status") == "discovery_leads":
            return (
                f"已汇集 {int(result.get('candidate_count', 0) or 0)} 条查新候选，"
                f"去重后 {int(result.get('deduplicated_count', 0) or 0)} 条"
            )
        if workflow == "novelty_check" and "acquired_count" in result:
            return (
                f"已获取 {int(result.get('acquired_count', 0) or 0)} 篇合法全文，"
                f"失败 {int(result.get('failed_count', 0) or 0)} 篇"
            )
        if workflow == "novelty_check" and result.get("phase") == "novelty_assessment":
            return str(dict(result.get("verdict", {}) or {}).get("label", "查新审查已完成"))
        if workflow == "research_idea" and result.get("evidence_status") == "discovery_leads":
            return (
                f"已汇集 {int(result.get('candidate_count', 0) or 0)} 条研究线索，"
                f"去重后 {int(result.get('deduplicated_count', 0) or 0)} 条"
            )
        if workflow == "research_idea" and "acquired_count" in result:
            return (
                f"已获取 {int(result.get('acquired_count', 0) or 0)} 篇合法全文，"
                f"失败 {int(result.get('failed_count', 0) or 0)} 篇"
            )
        if workflow == "research_idea" and result.get("phase") == "bottleneck_diagnosis":
            return "已形成全文证据约束的承重瓶颈" if result.get("status") == "grounded" else str(result.get("reason", "瓶颈诊断未完成"))
        if workflow == "research_idea" and result.get("phase") == "candidate_generation":
            return str(result.get("title") or result.get("reason") or "候选方案生成完成")
        if workflow == "research_idea" and result.get("phase") in {"coherence_audit", "falsifiability_audit", "implementability_audit"}:
            return f"{result.get('phase')}：{result.get('verdict', result.get('passed', result.get('status', '完成')))}"
        if workflow == "research_idea" and result.get("phase") == "research_idea_card":
            return str(dict(result.get("reader_answer", {}) or {}).get("text", "研究 Idea Card 已生成"))
        if workflow in {"literature_review", "deep_research", "research_idea"} and result.get("phase") == "retrieval":
            summary = dict(result.get("retrieval_summary", {}) or {})
            return (
                f"已完成 {int(summary.get('section_count', 0) or 0)} 个章节的检索，"
                f"汇集 {int(summary.get('document_count', 0) or 0)} 篇文献、"
                f"{int(summary.get('evidence_count', 0) or 0)} 条证据"
            )
        if workflow in {"ask", "literature_review", "deep_research", "novelty_check", "research_idea"}:
            reader = dict(result.get("reader_answer", {}) or {})
            return f"已综合 {int(reader.get('citation_count', 0) or 0)} 条可回跳引用"
        count = result.get("count") or result.get("total") or len(result.get("results", []) or [])
        if count:
            return f"工具执行完成，返回 {count} 条结果"
        return "工具执行完成"

    @staticmethod
    def _artifact_summary(run: dict[str, Any], result: dict[str, Any]) -> str:
        if run["workflow_type"] == "pdf_to_ppt":
            return str(result.get("message", "已生成可编辑 PPTX"))
        if run["workflow_type"] == "academic_search":
            gate = dict(result.get("quality_gate", {}) or {})
            if str(gate.get("status", "")) in {"insufficient", "no_candidates"}:
                return "未找到通过主题相关性准入的候选；已阻止交付不相关结果"
            return (
                f"已交付 {int(result.get('count', 0) or 0)} 条高相关候选，"
                f"并保留检索计划与筛选记录"
            )
        if run["workflow_type"] in {"literature_review", "deep_research"}:
            abstract = dict(dict(result.get("review_document", {}) or {}).get("abstract", {}) or {})
            text = str(abstract.get("text", "")).strip()
            if not text and dict(result.get("answer", {}) or {}).get("insufficient_evidence"):
                return "已保存检索线索；全文证据不足，未生成科学结论"
            return text[:220] + ("…" if len(text) > 220 else "")
        if run["workflow_type"] == "novelty_check":
            text = str(dict(result.get("reader_answer", {}) or {}).get("text", "")).strip()
            return text[:220] + ("…" if len(text) > 220 else "") if text else "查新产物已保存"
        if run["workflow_type"] == "research_idea":
            text = str(dict(result.get("reader_answer", {}) or {}).get("text", "")).strip()
            return text[:220] + ("…" if len(text) > 220 else "") if text else "研究 Idea Card 已保存"
        if run["workflow_type"] == "ask":
            text = str(dict(result.get("reader_answer", {}) or {}).get("text", "")).strip()
            return text[:220] + ("…" if len(text) > 220 else "")
        if result.get("message"):
            return str(result["message"])
        count = result.get("count") or result.get("total") or len(result.get("results", []) or [])
        return f"已保存 {count} 条结构化结果" if count else "结构化研究产物已保存"

    def _requested_notebook(self, payload: dict[str, Any]) -> dict[str, Any]:
        notebook_id = str(payload.get("notebook_id", "")).strip()
        if notebook_id:
            return self._notebook(notebook_id)
        summary = load_workspace_summary(self.workspace)
        notebooks = list(summary.get("notebooks", []) or [])
        if not notebooks:
            # Legacy callers and one-off evidence jobs may provide a concrete
            # evidence database without first creating a notebook record.
            if self.evidence_db.is_file():
                return {"notebook_id": "", "sources": []}
            raise FileNotFoundError("当前工作区没有可用资料库")
        return dict(notebooks[0])

    def _notebook(self, notebook_id: str) -> dict[str, Any]:
        summary = load_workspace_summary(self.workspace, notebook_id=notebook_id)
        notebooks = list(summary.get("notebooks", []) or [])
        if not notebooks:
            raise FileNotFoundError(f"Notebook does not exist: {notebook_id}")
        return dict(notebooks[0])

    def _evidence_db_for_notebook(self, notebook: dict[str, Any]) -> Path:
        """Resolve the notebook's own index, with a legacy single-index fallback."""

        for source in list(notebook.get("sources", []) or []):
            candidate = Path(str(dict(source).get("evidence_db_path", "") or ""))
            if candidate.is_file():
                return candidate.resolve()
        isolated = notebook_evidence_db(self.evidence_db, str(notebook.get("notebook_id", "")))
        if isolated.is_file():
            return isolated
        return self.evidence_db

    @staticmethod
    def _scope_for_notebook(payload: dict[str, Any], notebook: dict[str, Any]) -> dict[str, Any]:
        """Pick the knowledge scope for one notebook from the request payload.

        The browser sends the full per-notebook list as ``knowledge_scopes``
        alongside the active scope as ``knowledge_scope``.  Prefer the entry
        that names the notebook being queried so per-notebook scopes are not
        silently dropped.
        """

        notebook_id = str(notebook.get("notebook_id", "")).strip()
        for item in list(payload.get("knowledge_scopes", []) or []):
            if isinstance(item, dict) and str(item.get("notebook_id", "")).strip() == notebook_id:
                return dict(item)
        return dict(payload.get("knowledge_scope", {}) or {})

    def _knowledge_scope_resolution(
        self,
        notebook: dict[str, Any],
        evidence_db: str | Path,
        scope: dict[str, Any] | None,
        question: str = "",
    ) -> dict[str, Any]:
        requested = dict(scope or {})
        requested_type = str(requested.get("type", "")).strip()
        if requested_type not in {"", "zotero-tag"}:
            return {"active": False, "status": "inactive"}
        requested_notebook_id = str(requested.get("notebook_id", "")).strip()
        notebook_id = str(notebook.get("notebook_id", "")).strip()
        if requested_notebook_id and requested_notebook_id != notebook_id:
            return {"active": False, "status": "other-library"}
        if requested_type == "zotero-tag":
            return resolve_zotero_tag_scope(notebook, evidence_db, requested)
        library_kind = str(dict(notebook.get("metadata", {}) or {}).get("library_kind", "")).strip().lower()
        if library_kind == "zotero":
            self._ensure_zotero_tag_index(notebook, evidence_db)
            return {"active": False, "status": "automatic-ranking"}
        return {"active": False, "status": "no-query" if not str(question or "").strip() else "not-zotero"}

    def _ensure_zotero_tag_index(
        self,
        notebook: dict[str, Any],
        evidence_db: str | Path,
    ) -> None:
        """Backfill tags for existing Zotero connections on first retrieval."""

        zotero = dict(dict(notebook.get("metadata", {}) or {}).get("zotero", {}) or {})
        items = [item for item in list(zotero.get("items", []) or []) if isinstance(item, dict)]
        if not items:
            return
        signature = hashlib.sha1(
            json.dumps(
                [
                    {
                        "doi": str(item.get("doi", "") or ""),
                        "title": str(item.get("title", "") or ""),
                        "attachments": [
                            str(dict(attachment).get("path", "") or "")
                            for attachment in list(item.get("attachments", []) or [])
                            if isinstance(attachment, dict)
                        ],
                        "tags": [str(tag.get("tag", "") if isinstance(tag, dict) else tag) for tag in list(item.get("tags", []) or [])],
                    }
                    for item in items
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cache_key = f"{Path(evidence_db).resolve()}|{signature}"
        if cache_key in self._zotero_tag_sync_cache:
            return
        result = sync_zotero_document_tags(evidence_db, items)
        if str(result.get("status", "")) in {"ready", "empty_catalogue"}:
            self._zotero_tag_sync_cache.add(cache_key)

    def _knowledge_filters_for_notebook(
        self,
        notebook: dict[str, Any],
        evidence_db: str | Path,
        scope: dict[str, Any] | None,
        question: str = "",
    ) -> dict[str, Any]:
        resolution = self._knowledge_scope_resolution(notebook, evidence_db, scope, question)
        if not bool(resolution.get("active")):
            return {}
        return {"doc_ids": list(resolution.get("doc_ids", []) or [])}

    def _knowledge_run_metadata(self, notebook: dict[str, Any]) -> dict[str, Any]:
        """Freeze selected knowledge identity for conversation continuation."""

        notebook_id = str(notebook.get("notebook_id", "") or "").strip()
        if not notebook_id:
            return {
                "knowledge_base_ids": [],
                "index_version": {},
                "evidence_snapshot": {},
            }
        paths = {
            str(dict(source).get("evidence_db_path", "") or "").strip()
            for source in list(notebook.get("sources", []) or [])
            if str(dict(source).get("evidence_db_path", "") or "").strip()
        }
        if not paths:
            candidate = self._evidence_db_for_notebook(notebook)
            if candidate.is_file():
                paths.add(str(candidate))
        snapshots: list[dict[str, Any]] = []
        for path in sorted(paths):
            snapshot = knowledge_base_snapshot(path, knowledge_base_id=notebook_id)
            snapshots.append(snapshot)
        if not snapshots:
            return {
                "knowledge_base_ids": [],
                "index_version": {},
                "evidence_snapshot": {},
            }
        return {
            "knowledge_base_ids": [notebook_id],
            "index_version": {
                notebook_id: max(int(item.get("index_version", 0) or 0) for item in snapshots)
            },
            "evidence_snapshot": {
                notebook_id: {
                    "index_version": max(int(item.get("index_version", 0) or 0) for item in snapshots),
                    "stores": snapshots,
                    "document_count": sum(int(item.get("document_count", 0) or 0) for item in snapshots),
                    "evidence_span_count": sum(
                        int(item.get("evidence_span_count", 0) or 0) for item in snapshots
                    ),
                }
            },
        }

    @staticmethod
    def _academic_provider_names(payload: dict[str, Any]) -> list[str]:
        raw = payload.get("providers")
        if raw is None:
            return list(DEFAULT_PROVIDER_NAMES)
        if isinstance(raw, str):
            values = [value.strip() for value in raw.split(",")]
        elif isinstance(raw, list):
            values = [str(value).strip() for value in raw]
        else:
            raise ValueError("providers must be a list or comma-separated string")
        allowed = set(DEFAULT_PROVIDER_NAMES) | {"s2", "semanticscholar", "europepmc"}
        selected = []
        for value in values:
            normalized = value.lower().replace("_", "-")
            if not normalized or normalized not in allowed:
                continue
            if normalized not in selected:
                selected.append(normalized)
        if not selected:
            raise ValueError("No supported academic search provider was selected")
        return selected

    @staticmethod
    def _optional_year(value: Any) -> int | None:
        if value in {None, ""}:
            return None
        year = int(value)
        if year < 1800 or year > 2100:
            raise ValueError("year_from must be between 1800 and 2100")
        return year

    @staticmethod
    def _normalize_input(workflow_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = {str(key): value for key, value in payload.items() if key not in {"workflow_type", "notebook_id"}}
        required = {
            "ask": "question",
            "literature_review": "question",
            "academic_search": "query",
            "deep_research": "question",
            "journal_search": "query",
            "citation_analysis": "text",
            "doi_verification": "doi",
            "paper_atlas": "query",
            "paper_download": "identifier",
        }.get(workflow_type)
        if required and not str(normalized.get(required, "")).strip():
            raise ValueError(f"{required} is required")
        if workflow_type == "novelty_check":
            for field_name in ("problem", "novelty"):
                if not str(normalized.get(field_name, "")).strip():
                    raise ValueError(f"{field_name} is required")
        if workflow_type == "research_idea" and not str(normalized.get("direction", "")).strip():
            raise ValueError("direction is required")
        if workflow_type == "pdf_to_ppt":
            sources = normalized.get("source_files")
            if not isinstance(sources, list) or not sources:
                raise ValueError("source_files is required for PDF-to-PPT")
        if workflow_type == "paper_download_batch":
            identifiers = normalized.get("identifiers")
            if not isinstance(identifiers, list) or not [i for i in identifiers if str(i or "").strip()]:
                raise ValueError("identifiers is required for batch paper download")
        if workflow_type == "paper_search_download":
            if not str(normalized.get("query", "")).strip() and not str(normalized.get("author", "")).strip():
                raise ValueError("query or author is required for search-and-download")
            normalized["limit"] = max(1, min(50, int(normalized.get("limit", 20) or 20)))
            if normalized.get("sort") not in {"relevance", "cited_by_count", "publication_date"}:
                normalized["sort"] = "relevance"
        return normalized

    @staticmethod
    def _run_title(workflow_type: str, payload: dict[str, Any]) -> str:
        if workflow_type == "evidence_index":
            notebook_title = " ".join(str(payload.get("notebook_title") or "当前知识库").split())
            return f"优化「{notebook_title[:64]}」的语义检索"
        source = (
            payload.get("question")
            or payload.get("query")
            or payload.get("problem")
            or payload.get("direction")
            or payload.get("doi")
            or payload.get("identifier")
            or payload.get("author")
            or payload.get("topic")
            or _WORKFLOWS[workflow_type]["label"]
        )
        title = " ".join(str(source).split())
        if workflow_type in {"literature_review", "deep_research", "novelty_check"}:
            return _concise_review_title(title)
        return title[:96] + ("…" if len(title) > 96 else "")

    @staticmethod
    def _reader_url(doc_id: str, anchor: str) -> str:
        suffix = f"#{anchor}" if anchor else ""
        return f"/api/sources/{quote(doc_id, safe='')}/reader{suffix}"

    @staticmethod
    def _task_evidence_reader_url(run_id: str, doc_id: str, anchor: str) -> str:
        suffix = f"#{anchor}" if anchor else ""
        return (
            f"/api/runs/{quote(str(run_id), safe='')}/sources/"
            f"{quote(str(doc_id), safe='')}/reader{suffix}"
        )

    @staticmethod
    def _original_url(doc_id: str) -> str:
        return f"/api/sources/{quote(doc_id, safe='')}/original"

    def _pi_role_chat_client(
        self,
        *,
        provider_kind: str,
        provider_id: str,
        base_url: str,
        api_key: str,
        model_id: str,
        role: str,
        timeout_seconds: float,
        api_surface: str = "chat_completions",
        responses_enabled: bool = False,
    ) -> _PiBackedChatJsonClient:
        selected_api_surface = select_api_surface(
            api_surface,
            provider_kind=provider_kind,
            provider_id=provider_id,
            model=model_id,
            responses_enabled=responses_enabled,
        )
        settings = load_settings(self.workspace)
        provider_record = next(
            (
                dict(item)
                for item in list(settings.get("providers", []) or [])
                if isinstance(item, dict) and str(item.get("id", "")) == provider_id
            ),
            {},
        )
        model_record = next(
            (
                dict(item)
                for item in list(provider_record.get("models", []) or [])
                if isinstance(item, dict) and str(item.get("id", "")) == model_id
            ),
            {},
        )
        if not model_record and provider_id.startswith("local:"):
            local_id = provider_id.removeprefix("local:")
            model_record = next(
                (
                    dict(item)
                    for item in list(settings.get("local_models", []) or [])
                    if isinstance(item, dict) and str(item.get("id", "")) == local_id
                ),
                {},
            )
        descriptor = descriptor_from_model_record(
            provider_id=provider_id,
            provider_kind=provider_kind,
            model_id=model_id,
            model_record=model_record,
            api_surface=selected_api_surface,
        )
        return _PiBackedChatJsonClient(
            workspace=self.workspace,
            evidence_db=self.evidence_db,
            provider_kind=provider_kind,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model_id,
            api_surface=selected_api_surface,
            responses_enabled=responses_enabled,
            role=role,
            timeout_seconds=timeout_seconds,
            model_runtime=descriptor,
        )

    def _writing_chat_client(self) -> Any:
        settings = load_settings(self.workspace)
        reference = self._role_reference(settings, "writing")
        if reference.startswith("provider:"):
            remainder = reference.removeprefix("provider:")
            provider_id, separator, model_id = remainder.partition(":")
            if not separator or not provider_id or not model_id:
                raise ValueError("写作模型配置无效，请在“设置 → 模型服务 → 功能分工”中重新选择")
            provider = next(
                (item for item in settings.get("providers", []) if str(item.get("id", "")) == provider_id),
                None,
            )
            if provider is None or not provider.get("enabled", True):
                raise ValueError("指定的写作模型提供商不存在或已停用")
            if str(provider.get("kind", "")) == "local" and provider_id == "local-huggingface":
                configured_model = next(
                    (
                        item
                        for item in list(provider.get("models", []) or [])
                        if str(item.get("id", "")) == model_id
                    ),
                    None,
                )
                if configured_model is None:
                    raise ValueError("本地模型尚未通过运行时探测，请稍后刷新设置页后再选择")
                base_url = ensure_local_transformers_runtime(model_id)
                return self._wrap_model_client(
                    build_chat_json_client(
                        "openai-compatible",
                        base_url=base_url,
                        api_key="scansci-local-runtime",
                        model=model_id,
                        timeout=300.0,
                        provider_id=provider_id,
                    )
                )
            if str(provider.get("kind", "")) == "local":
                raise ValueError(
                    "真正的文献综述需要生成模型。请在“设置 → 模型服务”添加云端模型服务或本地 Ollama/LM Studio，"
                    "并把“写作”分工指向该模型。"
                )
            api_key = "scansci-managed-gateway" if str(provider.get("auth_mode", "")) == "managed" else get_provider_api_key(self.workspace, provider_id)
            if not api_key:
                raise ValueError(f"写作模型 {provider.get('name', provider_id)} 尚未配置 API Key")
            managed = str(provider.get("auth_mode", "")) == "managed"
            cache_key = (provider_id, model_id, str(provider.get("base_url", "")))
            if managed and cache_key in self._managed_writing_clients:
                return self._wrap_model_client(self._managed_writing_clients[cache_key])
            primary_client = self._pi_role_chat_client(
                provider_kind=str(provider.get("kind", "")),
                provider_id=provider_id,
                base_url=str(provider.get("base_url", "")),
                api_key=api_key,
                model_id=model_id,
                role="writing",
                timeout_seconds=35.0 if managed else 60.0,
                api_surface=str(provider.get("api_surface", "chat_completions")),
                responses_enabled=bool(provider.get("responses_enabled", False)),
            )
            if not managed:
                return self._wrap_model_client(primary_client)
            fallback_model = next(
                (
                    str(item.get("id", ""))
                    for item in list(provider.get("models", []) or [])
                    if str(item.get("id", "")) and str(item.get("id", "")) != model_id
                ),
                "",
            )
            if not fallback_model:
                self._managed_writing_clients[cache_key] = primary_client
                return self._wrap_model_client(primary_client)
            fallback_client = self._pi_role_chat_client(
                provider_kind=str(provider.get("kind", "")),
                provider_id=provider_id,
                base_url=str(provider.get("base_url", "")),
                api_key=api_key,
                model_id=fallback_model,
                role="writing",
                timeout_seconds=50.0,
                api_surface=str(provider.get("api_surface", "chat_completions")),
                responses_enabled=bool(provider.get("responses_enabled", False)),
            )
            client = CascadingChatJsonClient([primary_client, fallback_client])
            self._managed_writing_clients[cache_key] = client
            return self._wrap_model_client(client)
        if reference.startswith("local:"):
            local_id = reference.removeprefix("local:")
            local_model = next(
                (item for item in settings.get("local_models", []) if str(item.get("id", "")) == local_id),
                None,
            )
            if local_model is None or not local_model.get("enabled", False):
                raise ValueError("指定的本地写作模型不存在或未启用")
            if str(local_model.get("runtime", "")) == "builtin":
                raise ValueError(
                    "本地证据引擎只负责检索，不能冒充综述作者。请为“写作”分工选择 Ollama、LM Studio 或 API 模型。"
                )
            base_url = str(local_model.get("base_url", "")).strip()
            model_id = str(local_model.get("model_id", "")).strip()
            if not base_url or not model_id:
                raise ValueError("本地写作模型需要配置 Base URL 和模型 ID")
            return self._wrap_model_client(self._pi_role_chat_client(
                provider_kind="openai-compatible",
                provider_id=f"local:{local_id}",
                base_url=base_url,
                api_key="scansci-local-runtime",
                model_id=model_id,
                role="writing",
                timeout_seconds=60.0,
            ))
        raise ValueError("尚未指定写作模型，请在“设置 → 模型服务 → 功能分工”中选择")

    @staticmethod
    def _retrieval_quality(payload: dict[str, Any], *, default: str = "balanced") -> str:
        requested = str(payload.get("retrieval_quality", "") or "").strip().lower()
        if requested in {"balanced", "precision"}:
            return requested
        return "precision" if str(default).lower() == "precision" else "balanced"

    def _local_evidence_stack(
        self,
        evidence_db: Path | None = None,
        *,
        quality_profile: str = "balanced",
        embedding_only: bool = False,
        _allow_neural_build: bool = False,
    ) -> LocalEvidenceStack:
        target = evidence_db or self.evidence_db
        # Every non-empty desktop library uses the configured neural embedding
        # model once its background index is ready. Tiny libraries may answer
        # through the fast exhaustive path during that one-time warm-up.
        requested_profile = "precision" if str(quality_profile).lower() == "precision" else "balanced"
        neural_cache_ready = False
        if target.is_file() and not embedding_only and target.stat().st_size < _NEURAL_LIBRARY_MIN_BYTES:
            vector_identity = default_vector_cache_identity()
            neural_cache_ready = bool(
                vector_cache_status(
                    target,
                    provider=str(vector_identity["provider"]),
                    dimensions=int(vector_identity["dimensions"]),
                    ).get("ready")
            )
        settings = load_settings(self.workspace)
        roles = dict(settings.get("model_roles", {}) or {})
        installed_evidence_models = [
            item
            for item in installed_models()
            if bool(item.get("ready"))
            and item.get("runtime_compatible", True) is not False
            and str(item.get("kind", "")).casefold() in {"embedding", "reranking"}
        ]

        def auto_local_model(role: str) -> str:
            """Pick a verified local evidence model without exposing model plumbing.

            The built-in evidence entry is a fallback marker, not a request to
            permanently use hashing or lexical ranking.  If a compatible model
            has been downloaded, let the Agent upgrade to it automatically;
            otherwise the normal built-in fallback remains available.

            When a library already contains a ready sqlite-vec cache for a
            compatible model, prefer that model even when a newer one is
            installed.  Picking a model whose cache must be built from scratch
            (or migrated) leaves the dense retrieval route dead for the
            library that the user is about to query.
            """

            candidates = [
                item
                for item in installed_evidence_models
                if str(item.get("kind", "")).casefold() == role.casefold()
            ]
            # Pre-load the vector cache status for this library once.  A
            # legacy library may have had its cache built with a different
            # model version; that ready cache still serves dense retrieval
            # today and outranks a fresh-but-empty cache.
            try:
                vector_status = (
                    vector_cache_status(str(target), provider="", dimensions=0)
                    if target.is_file()
                    else None
                )
            except Exception:
                vector_status = None
            cached_providers: dict[str, int] = {}
            if isinstance(vector_status, dict):
                for entry in list(vector_status.get("providers", []) or []):
                    if not isinstance(entry, dict):
                        continue
                    key = str(entry.get("provider", "")).strip()
                    count = int(entry.get("cached_vectors", 0) or 0)
                    if key and count > 0:
                        cached_providers[key] = max(cached_providers.get(key, 0), count)

            def _has_ready_cache(model_id: str) -> bool:
                """Return True when this model has cached vectors ready to serve."""
                stripped = str(model_id or "").strip()
                if not stripped or not cached_providers:
                    return False
                # A sentence-transformers model may be registered as
                # "sentence-transformers:org/model-name" in the cache.
                return (
                    stripped in cached_providers
                    or f"sentence-transformers:{stripped}" in cached_providers
                )

            candidates.sort(
                key=lambda item: (
                    0 if _has_ready_cache(str(item.get("id", ""))) else 1,
                    0 if "qwen3" in str(item.get("id", "")).casefold() else 1,
                    int(item.get("size_bytes", 0) or 0),
                    str(item.get("id", "")).casefold(),
                )
            )
            return str(candidates[0].get("id", "")).strip() if candidates else "__builtin__"

        def configured_local_model(role: str) -> str:
            reference = str(roles.get(role, "") or "").strip()
            if reference in {"", "auto", "local:auto", "local:builtin-evidence"} and role in {"embedding", "reranking"}:
                return auto_local_model(role)
            if reference.startswith("local:"):
                local_id = reference.removeprefix("local:")
                local = next(
                    (item for item in settings.get("local_models", []) if str(item.get("id", "")) == local_id),
                    None,
                )
                if not local or not local.get("enabled", False):
                    return ""
                if str(local.get("runtime", "")) == "builtin":
                    return "__builtin__"
                if local.get("runtime_compatible") is False:
                    return ""
                return str(local.get("model_id", "") or "").strip()
            if reference.startswith("provider:"):
                provider_id, separator, model_id = reference.removeprefix("provider:").partition(":")
                provider = next(
                    (item for item in settings.get("providers", []) if str(item.get("id", "")) == provider_id),
                    None,
                )
                if separator and provider and str(provider.get("kind", "")) == "local":
                    return model_id.strip()
            return ""

        def configured_remote_embedding() -> tuple[str, Any | None]:
            """Build the supported SiliconFlow embedding selected by the user.

            The provider uses SiliconFlow's OpenAI-compatible ``/embeddings``
            endpoint.  Constructing this adapter is local-only; no request is
            made until a query or vector-cache build actually needs a vector.
            """

            reference = str(roles.get("embedding", "") or "").strip()
            if not reference.startswith("provider:"):
                return "", None
            provider_id, separator, model_id = reference.removeprefix("provider:").partition(":")
            if not separator or provider_id != "siliconflow" or not model_id.strip():
                return "", None
            provider = next(
                (item for item in settings.get("providers", []) if str(item.get("id", "")) == provider_id),
                None,
            )
            if provider is None or not provider.get("enabled", True):
                return "", None
            model = next(
                (
                    item
                    for item in provider.get("models", [])
                    if str(item.get("id", "")) == model_id.strip()
                ),
                None,
            )
            capabilities = {
                str(capability).casefold()
                for capability in list((model or {}).get("capabilities", []) or [])
            }
            if model is None or "embedding" not in capabilities:
                return "", None
            api_key = get_provider_api_key(self.workspace, provider_id)
            if not api_key:
                return "", None
            try:
                embedding = build_embedding_provider(
                    "siliconflow",
                    base_url=str(provider.get("base_url", "")).strip(),
                    api_key=api_key,
                    model=model_id.strip(),
                )
            except Exception:
                return "", None
            return model_id.strip(), embedding

        def configured_remote_reranker() -> tuple[str, Any | None]:
            """Build the supported cloud reranker selected in Default abilities.

            Keep this allow-list explicit: provider model IDs are not enough to
            infer compatible APIs.  SiliconFlow's native ``/rerank`` contract
            is supported here; other cloud rerankers continue to use the local
            fallback until their protocol is implemented separately.
            """

            reference = str(roles.get("reranking", "") or "").strip()
            if not reference.startswith("provider:"):
                return "", None
            provider_id, separator, model_id = reference.removeprefix("provider:").partition(":")
            if not separator or provider_id != "siliconflow" or not model_id.strip():
                return "", None
            provider = next(
                (item for item in settings.get("providers", []) if str(item.get("id", "")) == provider_id),
                None,
            )
            if provider is None or not provider.get("enabled", True):
                return "", None
            model = next(
                (
                    item
                    for item in provider.get("models", [])
                    if str(item.get("id", "")) == model_id.strip()
                ),
                None,
            )
            capabilities = {
                str(capability).casefold()
                for capability in list((model or {}).get("capabilities", []) or [])
            }
            if model is None or "reranking" not in capabilities:
                return "", None
            api_key = get_provider_api_key(self.workspace, provider_id)
            if not api_key:
                return "", None
            try:
                reranker = build_reranker(
                    "siliconflow",
                    base_url=str(provider.get("base_url", "")).strip(),
                    api_key=api_key,
                    model_name=model_id.strip(),
                )
            except Exception:
                return "", None
            return model_id.strip(), reranker

        selected_embedding_model = configured_local_model("embedding")
        selected_reranker_model = configured_local_model("reranking")
        remote_embedding_model, remote_embedding = configured_remote_embedding()
        remote_reranker_model, remote_reranker = configured_remote_reranker()
        embedding_reference = str(roles.get("embedding", "") or "").strip()
        if remote_embedding is not None:
            selected_embedding_model = remote_embedding_model
        elif embedding_reference.startswith("provider:"):
            # If a remote key is missing or the provider is unavailable, use
            # the best verified local model instead of silently loading an
            # arbitrary model from the environment.
            selected_embedding_model = auto_local_model("embedding")
        reranking_reference = str(roles.get("reranking", "") or "").strip()
        if remote_reranker is not None:
            selected_reranker_model = remote_reranker_model
        elif reranking_reference.startswith("provider:"):
            # If a remote key is missing or the provider is unavailable, use
            # the best verified local model instead of disabling reranking.
            selected_reranker_model = auto_local_model("reranking")
        remote_embedding_selected = remote_embedding is not None
        remote_reranker_selected = remote_reranker is not None and not embedding_only
        if remote_embedding_selected and target.is_file():
            try:
                neural_cache_ready = bool(
                    vector_cache_status(
                        target,
                        provider=str(getattr(remote_embedding, "cache_key", "")),
                        dimensions=int(getattr(remote_embedding, "dimensions", 0) or 0),
                    ).get("ready")
                )
            except Exception:
                neural_cache_ready = False
        use_fast_path = not target.is_file() or (
            not embedding_only
            and target.stat().st_size < _NEURAL_LIBRARY_MIN_BYTES
            and not neural_cache_ready
            and not remote_embedding_selected
            and not remote_reranker_selected
        )
        stack_base = (
            "fast"
            if use_fast_path
            else "neural:embedding"
            if embedding_only
            else f"neural:{requested_profile}"
        )
        reranker_selector = str(
            getattr(remote_reranker, "cache_key", "")
            or selected_reranker_model
            or "auto"
        )
        embedding_selector = str(
            getattr(remote_embedding, "cache_key", "")
            or selected_embedding_model
            or "auto"
        )
        selector_key = f"{embedding_selector}|{reranker_selector}"
        stack_kind = f"{stack_base}:{selector_key}"
        preparation_key = self._local_evidence_preparation_key(target, requested_profile)
        with self._local_evidence_prepare_lock:
            preparation_state = str(
                dict(self._local_evidence_preparations.get(preparation_key, {}) or {}).get("state", "")
            )
            if preparation_state != "preparing":
                target_prefix = f"{target.resolve()}|"
                preparation_state = "preparing" if any(
                    key.startswith(target_prefix)
                    and str(dict(status or {}).get("state", "")) == "preparing"
                    for key, status in self._local_evidence_preparations.items()
                ) else preparation_state
        # A user query should remain responsive while the background worker
        # initialises PyTorch/Transformers. Index-building calls also receive
        # the explicit fast marker; their caller rejects it instead of writing
        # a fake dense cache, so they fail promptly and can be retried when
        # preparation completes.
        if (
            stack_base != "fast"
            and preparation_state == "preparing"
            and not _allow_neural_build
        ):
            return self._fast_preparing_evidence_stack(selector_key, requested_profile)
        with self._model_lock:
            if stack_kind not in self._local_evidence_models:
                if stack_base == "fast":
                    self._local_evidence_models[stack_kind] = LocalEvidenceStack(
                        embedding_provider=HashingEmbeddingProvider(),
                        reranker=LexicalReranker(),
                        metadata={
                            "embedding": "local-hash-v1",
                            "reranker": "local-lexical-v1",
                            "local_neural_embedding": False,
                            "local_neural_reranker": False,
                            "embedding_device": "cpu",
                            "reranker_device": "cpu",
                            "fallback": False,
                            "fallback_reasons": [],
                            "selection_reason": "small-library-fast-path",
                            "requested_quality_profile": requested_profile,
                            "effective_quality_profile": "fast",
                            "precision_reranker_active": False,
                        },
                    )
                else:
                    existing_neural = next(
                        (
                            stack
                            for key, stack in self._local_evidence_models.items()
                            if key.startswith("neural:") and key.endswith(f":{selector_key}")
                        ),
                        None,
                    )
                    existing_reranker = None
                    if existing_neural is not None:
                        for stage, _keep_top in getattr(
                            existing_neural.reranker,
                            "stages",
                            [],
                        ):
                            if not isinstance(stage, LexicalReranker):
                                existing_reranker = stage
                                break
                    self._local_evidence_models[stack_kind] = build_local_evidence_stack(
                        embedding_model=selected_embedding_model,
                        reranker_model=selected_reranker_model,
                        precision_reranker_model=selected_reranker_model,
                        quality_profile=requested_profile,
                        load_reranker=not embedding_only,
                        embedding_provider_override=(
                            remote_embedding
                            if remote_embedding is not None
                            else (
                                existing_neural.embedding_provider
                                if existing_neural is not None
                                else None
                            )
                        ),
                        reranker_override=(
                            remote_reranker
                            if remote_reranker is not None
                            else existing_reranker
                        ),
                    )
        # If the stack is neural but the cache is still cold, start a
        # background thread to build it.  The first query will already
        # benefit from the fast (hashing) path; subsequent queries switch
        # to the dense route automatically once the warm is done.
        if stack_base != "fast" and not neural_cache_ready and target.is_file():
            self._maybe_warm_cache_in_background(target, self._local_evidence_models[stack_kind])
        return self._local_evidence_models[stack_kind]

    def _academic_discovery_stack(self) -> LocalEvidenceStack:
        """Return a process-safe stack for public academic discovery.

        Discovery is independent of the selected notebook's size.  In
        particular, an empty or tiny local library must not silently downgrade
        public multi-source literature search.  Public providers already return
        titles, abstracts, keywords, citation counts, and provider rankings, so
        the in-process fusion can safely use deterministic hashing and lexical
        ranking.

        Heavy neural inference must stay outside the desktop web process.  A
        native ``torch_cpu.dll`` access violation cannot be caught by Python
        and previously killed the whole app during a background Deep Research
        run.  The task artifact records this routing decision explicitly; the
        independently installed local runtime remains available for generation,
        vision, and speech inference.
        """

        stack_kind = "academic-discovery:process-safe"
        with self._model_lock:
            if stack_kind not in self._academic_discovery_models:
                stack = build_local_evidence_stack(
                    embedding_model=BUILTIN_MODEL_MARKER,
                    reranker_model=BUILTIN_MODEL_MARKER,
                    quality_profile="balanced",
                )
                self._academic_discovery_models[stack_kind] = LocalEvidenceStack(
                    embedding_provider=stack.embedding_provider,
                    reranker=stack.reranker,
                    metadata={
                        **dict(stack.metadata),
                        "selection_reason": "public-discovery-process-isolation",
                        "in_process_neural_disabled": True,
                    },
                )
        return self._academic_discovery_models[stack_kind]

    def _task_fulltext_evidence_stack(self) -> LocalEvidenceStack:
        """Return a process-safe stack for task-acquired full-text evidence.

        The desktop process must never import a heavyweight native Torch stack:
        a DLL access violation terminates the whole application before Python
        can report an error.  Hashing plus lexical reranking still retrieves
        from the real, task-scoped PDFs and records the exact fallback in the
        artifact.  Neural generation, vision, and ASR remain isolated in the
        separately installed local runtime component.
        """

        stack_kind = "task-fulltext:process-safe"
        with self._model_lock:
            if stack_kind not in self._task_evidence_models:
                stack = build_local_evidence_stack(
                    embedding_model=BUILTIN_MODEL_MARKER,
                    reranker_model=BUILTIN_MODEL_MARKER,
                    quality_profile="precision",
                )
                self._task_evidence_models[stack_kind] = LocalEvidenceStack(
                    embedding_provider=stack.embedding_provider,
                    reranker=stack.reranker,
                    metadata={
                        **dict(stack.metadata),
                        "selection_reason": "task-fulltext-process-isolation",
                        "in_process_neural_disabled": True,
                    },
                )
        return self._task_evidence_models[stack_kind]

    def _slides_chat_client(self) -> Any:
        """Return the model dedicated to presentation planning, when configured."""

        settings = load_settings(self.workspace)
        reference = self._role_reference(settings, "slides")
        if reference.startswith("provider:"):
            provider_id, separator, model_id = reference.removeprefix("provider:").partition(":")
            if not separator or not provider_id or not model_id:
                raise ValueError("演示模型配置无效")
            provider = next(
                (item for item in settings.get("providers", []) if str(item.get("id", "")) == provider_id),
                None,
            )
            if provider is None or not provider.get("enabled", True):
                raise ValueError("指定的演示模型不存在或已停用")
            if str(provider.get("kind", "")) == "local":
                raise ValueError("内置检索引擎不能用于生成演示文稿")
            api_key = "scansci-managed-gateway" if str(provider.get("auth_mode", "")) == "managed" else get_provider_api_key(self.workspace, provider_id)
            if not api_key:
                raise ValueError("演示模型尚未配置 API Key")
            return self._wrap_model_client(self._pi_role_chat_client(
                provider_kind=str(provider.get("kind", "")),
                provider_id=provider_id,
                base_url=str(provider.get("base_url", "")),
                api_key=api_key,
                model_id=model_id,
                role="slides",
                timeout_seconds=60.0,
                api_surface=str(provider.get("api_surface", "chat_completions")),
                responses_enabled=bool(provider.get("responses_enabled", False)),
            ))
        if reference.startswith("local:"):
            local_id = reference.removeprefix("local:")
            local_model = next(
                (item for item in settings.get("local_models", []) if str(item.get("id", "")) == local_id),
                None,
            )
            if local_model is None or not local_model.get("enabled", False):
                raise ValueError("指定的本地演示模型不存在或未启用")
            if str(local_model.get("runtime", "")) == "builtin":
                raise ValueError("内置检索引擎不能用于生成演示文稿")
            base_url = str(local_model.get("base_url", "")).strip()
            model_id = str(local_model.get("model_id", "")).strip()
            if not base_url or not model_id:
                raise ValueError("本地演示模型需要配置 Base URL 和模型 ID")
            return self._wrap_model_client(self._pi_role_chat_client(
                provider_kind="openai-compatible",
                provider_id=f"local:{local_id}",
                base_url=base_url,
                api_key="scansci-local-runtime",
                model_id=model_id,
                role="slides",
                timeout_seconds=60.0,
            ))
        raise ValueError("尚未指定演示模型")

    @staticmethod
    def _role_reference(settings: dict[str, Any], role: str) -> str:
        reference = str(dict(settings.get("model_roles", {}) or {}).get(role, "")).strip()
        active = dict(settings.get("active_model", {}) or {})
        active_provider_id = str(active.get("provider_id", ""))
        active_model_id = str(active.get("model_id", ""))
        active_provider = next(
            (item for item in settings.get("providers", []) if str(item.get("id", "")) == active_provider_id),
            None,
        )
        if (
            role == "writing"
            and reference == "provider:local-evidence:evidence-retrieval"
            and active_provider is not None
            and str(active_provider.get("kind", "")) != "local"
        ):
            return f"provider:{active_provider_id}:{active_model_id}"
        return reference

    @classmethod
    def _role_identity(cls, settings: dict[str, Any], role: str) -> tuple[str, str]:
        reference = cls._role_reference(settings, role)
        if reference.startswith("provider:"):
            provider_id, _separator, model_id = reference.removeprefix("provider:").partition(":")
            return provider_id, model_id
        if reference.startswith("local:"):
            local_id = reference.removeprefix("local:")
            local_model = next(
                (item for item in settings.get("local_models", []) if str(item.get("id", "")) == local_id),
                {},
            )
            return f"local:{local_id}", str(local_model.get("model_id", ""))
        return "", ""


def _format_web_search_delivery(result: dict[str, Any]) -> str:
    """Deliver successful discovery output when a provider cannot resume after a tool result."""

    query = str(result.get("query", "") or "").strip()
    items = [dict(item) for item in list(result.get("items", []) or []) if isinstance(item, dict)][:8]
    general_web = str(result.get("evidence_level", "")) == "web-discovery"
    lines = [
        "已完成公开网页搜索。以下是带来源链接的网页发现结果；搜索摘要不等于已核验的页面全文。"
        if general_web
        else "已完成联网学术搜索。以下内容是外部题录或摘要层面的发现线索，不是已经核验的全文证据。"
    ]
    if query:
        lines.append(f"\n检索式：`{query}`")
    if not items:
        lines.append("\n本轮检索没有返回可用记录。")
        return "\n".join(lines)
    for index, item in enumerate(items, start=1):
        title = str(item.get("title", "") or "未命名记录").strip().replace("[", "［").replace("]", "］")
        doi = str(item.get("doi", "") or "").strip()
        url = str(item.get("oa_url", "") or item.get("url", "") or "").strip()
        if not url and doi:
            url = f"https://doi.org/{doi}"
        linked_title = f"[{title}]({url})" if url else title
        metadata = " · ".join(
            value
            for value in (
                str(item.get("year", "") or "").strip(),
                str(item.get("venue", "") or item.get("source", "") or "").strip(),
                f"DOI: {doi}" if doi else "",
            )
            if value
        )
        snippet = " ".join(str(item.get("snippet", "") or "").split())[:360]
        lines.append(
            f"\n{index}. {linked_title}"
            + (f"\n   {metadata}" if metadata else "")
            + (f"\n   {snippet}" if snippet else "")
        )
    if not general_web:
        lines.append("\n如需把这些线索用于可核查的科学结论，请继续获取全文并进入句级证据检索。")
    return "\n".join(lines)


def _format_document_summary_delivery(result: dict[str, Any]) -> str:
    """Deliver completed document maps when final model synthesis is unavailable."""

    documents = [
        dict(item)
        for item in list(result.get("documents", []) or [])
        if isinstance(item, dict)
    ][:8]
    total_recorded = int(result.get("total_recorded", len(documents)) or len(documents))
    title = " ".join(str(result.get("title", "") or "").split())
    focus = " ".join(str(result.get("focus", "") or "").split())
    lines = [
        f"已读取并结构化当前任务登记的 {len(documents)}/{total_recorded} 篇文献。"
        "最终措辞模型暂时不可用，以下直接交付从文档全文中提取的结果；未提取到的字段不会被推断补写。"
    ]
    if title:
        lines.append(f"\n任务：{title}")
    if focus:
        lines.append(f"分析重点：{focus}")
    if not documents:
        lines.append("\n当前任务没有成功解析的文档。")
        return "\n".join(lines)

    def excerpt(value: Any, limit: int) -> str:
        compact = " ".join(str(value or "").split())
        if not compact:
            return "未提取到"
        return compact if len(compact) <= limit else f"{compact[:limit].rstrip()}…"

    for index, document in enumerate(documents, start=1):
        name = " ".join(str(document.get("name", "") or f"文献 {index}").split())
        lines.extend(
            [
                f"\n### {index}. {name}",
                f"- 研究问题：{excerpt(document.get('research_question'), 700)}",
                f"- 研究方法：{excerpt(document.get('methods'), 900)}",
                f"- 主要发现：{excerpt(document.get('findings'), 1_000)}",
                f"- 局限性：{excerpt(document.get('limitations'), 600)}",
            ]
        )

    if len(documents) >= 2:
        method_comparison = "；".join(
            f"{excerpt(item.get('name'), 100)}：{excerpt(item.get('methods'), 300)}"
            for item in documents
        )
        finding_comparison = "；".join(
            f"{excerpt(item.get('name'), 100)}：{excerpt(item.get('findings'), 340)}"
            for item in documents
        )
        lines.extend(
            [
                "\n### 横向对照",
                f"- 方法：{method_comparison}",
                f"- 结论：{finding_comparison}",
            ]
        )

    failures = [
        dict(item)
        for item in list(result.get("failures", []) or [])
        if isinstance(item, dict)
    ][:8]
    if failures:
        lines.append("\n### 未成功读取")
        for failure in failures:
            lines.append(
                f"- {excerpt(failure.get('name'), 160)}：{excerpt(failure.get('error'), 300)}"
            )
    return "\n".join(lines)


def _provider_neutral_tool_calls(result: dict[str, Any], *, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe real application-side actions without inventing model calls.

    Provider-compatible chat endpoints are not required to serialize native
    tool-call messages.  The records below are derived solely from completed
    ScanSci workflow output and are therefore safe to show in the process
    trace and persist in a research run.
    """

    calls: list[dict[str, Any]] = []
    query_plan = dict(result.get("query_plan", {}) or {})
    if query_plan:
        calls.append({"name": "plan_query", "status": "completed", "output": {"question_type": query_plan.get("question_type", "")}})
    retrieval_queries = [str(item) for item in list(result.get("retrieval_queries", []) or []) if str(item).strip()]
    hits = list(result.get("hits", []) or [])
    if retrieval_queries or hits:
        calls.append(
            {
                "name": "search_local_evidence",
                "status": "completed",
                "output": {"queries": retrieval_queries, "hit_count": len(hits)},
            }
        )
    if steps:
        calls.append({"name": "assess_evidence", "status": "completed", "output": {"steps": len(steps)}})
    verification = dict(result.get("citation_verification", {}) or {})
    calls.append(
        {
            "name": "build_verified_answer",
            "status": "completed",
            "output": {
                "passed": bool(verification.get("passed")),
                "cited_quote_count": int(verification.get("cited_quote_count", 0) or 0),
            },
        }
    )
    return calls
