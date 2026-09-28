"""Safe, inspectable planning for federated academic discovery.

The public metadata APIs used by :mod:`academic_search` accept short scholarly
queries, not a conversational instruction.  This module keeps the conversion
from a user request to those queries deterministic and auditable.  A configured
chat model may add cross-language aliases, but the host retains the extracted
topic, provider routing, limits, and validation rules.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .academic_search import DEFAULT_PROVIDER_NAMES
from .retrieval_intent import compile_retrieval_intent


_SPACE_RE = re.compile(r"\s+")
_GENERIC_TOPIC_SUFFIX_RE = re.compile(
    r"(?:的)?(?:研究|文献|论文|综述|进展|现状|研究进展|research|literature|review)$",
    re.IGNORECASE,
)
_BOILERPLATE_QUERY_RE = re.compile(
    r"(?:请|检索|搜索|查找|整理|相关性|题目|作者|年份|来源|doi|paper|article|search|find)",
    re.IGNORECASE,
)
_UNSAFE_SEARCH_REQUEST_RE = re.compile(
    r"(?:\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?\b"
    r"|\b(?:read|upload|exfiltrate|access)\b.{0,80}\b(?:local\s+)?(?:knowledge\s*base|files?|api\s*key|token)\b"
    r"|忽略.{0,24}(?:之前|先前|上文|指令|规则)"
    r"|(?:读取|上传|外传|访问).{0,40}(?:本地(?:知识库|文件)|知识库文件|密钥|令牌))",
    re.IGNORECASE,
)

# Temporal constraints are part of a research question, not decoration for the
# UI.  Keeping them in the plan means every public provider receives the same
# reproducible lower bound even when the browser does not explicitly submit it.
_CHINESE_YEAR_FROM_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})(?:\s*\u5e74)?\s*(?:\u4ee5\u6765|\u4e4b\u540e|\u8d77|\u81f3\u4eca)",
    re.IGNORECASE,
)
_ENGLISH_YEAR_FROM_RE = re.compile(
    r"\b(?:since|after|from)\s+(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)


_DOMAIN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "life_environment",
        (
            "植物", "生态", "土壤", "森林", "农业", "农田", "气候", "环境", "生物多样性", "水文",
            "plant", "ecology", "soil", "forest", "agri", "climate", "environment", "biodiversity", "hydro",
        ),
    ),
    (
        "biomedicine",
        (
            "临床", "患者", "疾病", "药物", "癌症", "肿瘤", "医学", "基因", "蛋白",
            "clinical", "patient", "disease", "drug", "cancer", "tumou", "medical", "gene", "protein",
        ),
    ),
    (
        "computing",
        (
            "人工智能", "机器学习", "大模型", "检索", "算法", "软件", "数据库", "知识图谱",
            "artificial intelligence", "machine learning", "language model", "retrieval", "algorithm", "software", "database", "knowledge graph",
        ),
    ),
)

_DOMAIN_PROVIDERS: dict[str, tuple[str, ...]] = {
    "life_environment": ("openalex", "semantic-scholar", "crossref", "europe-pmc"),
    "biomedicine": ("openalex", "semantic-scholar", "pubmed", "europe-pmc", "crossref"),
    "computing": ("openalex", "semantic-scholar", "arxiv", "openreview", "dblp", "crossref"),
    "general": ("openalex", "semantic-scholar", "crossref"),
}


def _deterministic_cross_language_queries(topic: str, *, max_queries: int) -> list[str]:
    """Return high-precision public-API aliases for well-defined concepts.

    Metadata providers predominantly index English titles and abstracts.  A
    Chinese-only phrase therefore cannot be the sole query for a concept with
    a standard English name.  These aliases are deliberately small, explicit,
    and shown in the review dialog; they are not generated answers and do not
    require a model or access to a local knowledge base.
    """

    normalized = _clean(topic).casefold()
    has_rag = (
        "\u68c0\u7d22\u589e\u5f3a\u751f\u6210" in normalized
        or "retrieval augmented generation" in normalized
        or bool(re.search(r"\brag\b", normalized))
    )
    has_factuality = any(
        term in normalized
        for term in (
            "\u4e8b\u5b9e\u4e00\u81f4\u6027",
            "\u4e8b\u5b9e\u6027",
            "\u5fe0\u5b9e\u6027",
            "factuality",
            "factual consistency",
            "faithfulness",
            "groundedness",
        )
    )
    has_evaluation = any(
        term in normalized
        for term in (
            "\u8bc4\u4f30",
            "\u8bc4\u4ef7",
            "\u57fa\u51c6",
            "evaluation",
            "benchmark",
            "assessment",
        )
    )
    if has_rag and has_factuality and has_evaluation:
        # Each query represents a distinct scholarly retrieval path: general
        # evaluation, the common faithfulness terminology, and benchmarks.
        return [
            "retrieval augmented generation factuality evaluation",
            "RAG faithfulness evaluation",
            "retrieval augmented generation factuality benchmark",
        ][:max_queries]
    if has_rag and has_factuality:
        return [
            "retrieval augmented generation factuality",
            "RAG faithfulness",
            "retrieval augmented generation groundedness",
        ][:max_queries]
    if has_rag:
        return ["retrieval augmented generation", "RAG"][:max_queries]
    has_mountain = any(term in normalized for term in ("山地", "高山", "mountain", "alpine", "montane"))
    has_soil = "土壤" in normalized or "soil" in normalized
    has_carbon = "碳" in normalized or "carbon" in normalized
    has_fractions = any(term in normalized for term in ("组分", "fraction"))
    if has_mountain and has_soil and has_carbon and has_fractions:
        return [
            "soil organic carbon fractions mountain soils",
            "alpine soil organic carbon fractions",
            "soil organic carbon fractions alpine meadow",
        ][:max_queries]
    return []


def extract_academic_topic(value: str) -> str:
    """Extract the actual research topic from an academic-search request.

    The labelled form is intentionally preferred because the app's quick task
    template uses ``研究主题：...``.  For free-form requests we retain the
    request rather than guessing a narrower topic.
    """

    intent = compile_retrieval_intent(value, kind="academic")
    return str(intent.get("normalized_subject") or intent["subject"])


def plan_academic_search(
    value: str,
    *,
    explicit_providers: Sequence[str] | None = None,
    chat_client: Any | None = None,
    max_queries: int = 3,
) -> dict[str, Any]:
    """Create a host-validated search plan from a user request.

    The optional model receives only the extracted topic.  It can propose
    English aliases for cross-language databases, but it cannot choose sources
    or turn a result into an answer.
    """

    intent = compile_retrieval_intent(value, kind="academic")
    raw_query = str(intent["raw_query"])
    # ``subject`` preserves the user's labelled wording for the UI and audit
    # record. Public metadata providers, the planner, and relevance gate must
    # instead receive the compact scholarly topic; otherwise a tail such as
    # ``的研究`` becomes part of every external query.
    topic = str(intent.get("normalized_subject") or intent["subject"])
    if _UNSAFE_SEARCH_REQUEST_RE.search(raw_query) or _UNSAFE_SEARCH_REQUEST_RE.search(topic):
        raise ValueError("学术搜索只接受研究主题或论文标识符，不能执行指令、访问本地资料或上传内容。")
    if len(_clean(topic)) > 180:
        raise ValueError("研究主题不能超过 180 个字符，请缩短后重试。")
    fallback = _fallback_plan(intent, explicit_providers=explicit_providers, max_queries=max_queries)
    if str(intent.get("intent_type", "")) == "exact_identifier":
        return fallback
    if chat_client is None:
        return fallback
    messages = [
        {
            "role": "system",
            "content": (
                "You are a scholarly search-query planner. Return JSON only. "
                "Given one research topic, propose up to three concise metadata-search queries and up to four "
                "specific core concepts, including an English equivalent when useful. Do not include instructions, "
                "papers, authors, DOI values, sources, claims, or explanations."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "topic": topic,
                    "return": {"query_variants": ["short scholarly query"], "core_concepts": ["specific concept"]},
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        raw = chat_client.complete_json(messages, schema_name="academic_search_plan") or {}
    except Exception as error:
        return {**fallback, "planner": "deterministic-fallback", "planner_error": f"{type(error).__name__}: {error}"[:500]}
    return _normalize_model_plan(raw, fallback=fallback, max_queries=max_queries)


def review_academic_search_plan(
    plan: dict[str, Any],
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply an inspectable user review without trusting it as a search plan.

    A public academic search is intentionally planned in two stages: ScanSci
    proposes bounded queries and suitable public sources, then the user may
    change those choices before any network call starts.  This function keeps
    that second stage host-validated.  In particular, a browser payload cannot
    introduce arbitrary endpoints, prompt instructions, or a hidden local
    knowledge scope.
    """

    base = dict(plan or {})
    reviewed = dict(review or {}) if isinstance(review, dict) else {}
    raw_providers = reviewed.get("providers")
    providers = _validated_providers(raw_providers) if raw_providers is not None else list(base.get("providers", []) or [])
    if not providers:
        providers = _validated_providers(base.get("providers"))

    proposed_queries = [
        query
        for query in _string_list(reviewed.get("query_variants"), limit=5)
        if _is_usable_query(query)
    ]
    query_variants = _unique(proposed_queries)[:3] or list(base.get("query_variants", []) or [])[:3]
    if not query_variants:
        query_variants = [str(base.get("normalized_topic") or base.get("topic") or "").strip()]
    query_variants = [query for query in query_variants if query]

    # Concepts remain a retrieval guard, rather than a free-form instruction
    # supplied by the browser.  User-reviewed query variants may add precise
    # terms, but the gate stays bounded and deterministic.
    required_terms = _unique([
        *list(base.get("required_terms", []) or []),
        *query_variants,
    ])
    required_terms = [term for term in required_terms if _is_specific_concept(term)][:6]
    return {
        **base,
        "schema_version": max(2, int(base.get("schema_version", 1) or 1)),
        "providers": providers,
        "query_variants": query_variants,
        "required_terms": required_terms,
        "planner": "user-reviewed-host-validated",
        "reviewed_by_user": True,
        "source_scope": "public_academic_apis",
        "local_knowledge_used": False,
    }


def _fallback_plan(
    intent: dict[str, Any],
    *,
    explicit_providers: Sequence[str] | None,
    max_queries: int,
) -> dict[str, Any]:
    topic = str(intent.get("normalized_subject") or intent["subject"])
    raw_query = str(intent["raw_query"])
    base_topic = str(intent["normalized_subject"])
    bounded_max_queries = max(1, min(3, int(max_queries)))
    query_variants = [
        query
        for query in _unique(list(intent.get("query_variants", []) or []))
        if _is_usable_query(query)
    ][:bounded_max_queries]
    cross_language_queries = _deterministic_cross_language_queries(
        base_topic,
        max_queries=bounded_max_queries,
    )
    # A known scholarly English term is preferable to a literal Chinese phrase
    # for global metadata APIs.  Keep the original topic as the display and
    # relevance contract, while using the alternatives as outbound queries.
    if cross_language_queries:
        query_variants = cross_language_queries
    if not query_variants and _is_usable_query(base_topic):
        query_variants = [base_topic]
    if not query_variants:
        raise ValueError("没有可用的检索式；请填写不超过 180 个字符的具体研究主题。")
    required_terms = _unique([
        *[term for term in list(intent.get("required_terms", []) or []) if _is_specific_concept(term)],
        *query_variants,
    ])
    required_terms = [term for term in required_terms if _is_specific_concept(term)][:6]
    domain = infer_academic_domain(base_topic)
    year_from = _infer_year_from(raw_query)
    # Public provider names must be validated here as well as at every caller.
    # Direct CLI or future API integrations must not bypass the browser's
    # allowlist and turn a provider option into an arbitrary endpoint.
    providers = _validated_providers(explicit_providers) or list(_DOMAIN_PROVIDERS[domain])
    return {
        "schema_version": 1,
        "raw_query": raw_query,
        "topic": topic,
        "normalized_topic": base_topic,
        "domain": domain,
        "providers": providers,
        "query_variants": query_variants,
        "required_terms": required_terms if str(intent.get("intent_type", "")) != "exact_identifier" else [],
        "year_from": year_from,
        "planner": "deterministic",
        "planner_confidence": "high" if topic != raw_query else "bounded",
        "source_scope": "public_academic_apis",
        "local_knowledge_used": False,
        "planning_note": (
            "已提取主题、时间范围并按领域选择公开学术来源；"
            "跨语言检索式会在开始前展示，用户可编辑后确认。"
        ),
        "retrieval_intent": intent,
    }


def _normalize_model_plan(raw: Any, *, fallback: dict[str, Any], max_queries: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fallback
    proposed_queries = [
        candidate
        for candidate in _string_list(raw.get("query_variants"), limit=max_queries)
        if _is_usable_query(candidate)
    ]
    proposed_concepts = [
        candidate
        for candidate in _string_list(raw.get("core_concepts"), limit=4)
        if _is_specific_concept(candidate)
    ]
    query_variants = _unique([*fallback["query_variants"], *proposed_queries])[: max(1, min(3, int(max_queries)))]
    required_terms = _unique([*fallback["required_terms"], *proposed_concepts, *query_variants])
    required_terms = [term for term in required_terms if _is_specific_concept(term)][:6]
    return {
        **fallback,
        "query_variants": query_variants,
        "required_terms": required_terms or list(fallback["required_terms"]),
        "planner": "model-assisted-host-validated",
        "planner_confidence": "high",
        "model_suggestions": {
            "accepted_query_variants": [query for query in proposed_queries if query in query_variants],
            "accepted_core_concepts": proposed_concepts,
        },
    }


def _infer_year_from(value: str) -> int | None:
    """Extract a lower publication-year bound from a natural-language request."""

    for pattern in (_CHINESE_YEAR_FROM_RE, _ENGLISH_YEAR_FROM_RE):
        match = pattern.search(str(value or ""))
        if match:
            year = int(match.group("year"))
            if 1800 <= year <= 2100:
                return year
    return None


def _validated_providers(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Sequence[Any] = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        values = []
    aliases = {
        "s2": "semantic-scholar",
        "semanticscholar": "semantic-scholar",
        "europepmc": "europe-pmc",
    }
    allowed = set(DEFAULT_PROVIDER_NAMES)
    selected: list[str] = []
    for value in values:
        normalized = str(value or "").strip().lower().replace("_", "-")
        normalized = aliases.get(normalized, normalized)
        if normalized in allowed and normalized not in selected:
            selected.append(normalized)
    return selected


def infer_academic_domain(topic: str) -> str:
    haystack = _clean(topic).casefold()
    for domain, keywords in _DOMAIN_KEYWORDS:
        if any(keyword.casefold() in haystack for keyword in keywords):
            return domain
    return "general"


def _strip_generic_topic_suffix(topic: str) -> str:
    stripped = _GENERIC_TOPIC_SUFFIX_RE.sub("", _clean(topic)).strip(" ，,;；。")
    return stripped if len(stripped) >= 2 else _clean(topic)


def _is_usable_query(value: str) -> bool:
    candidate = _clean(value)
    if not _is_specific_concept(candidate) or len(candidate) > 180:
        return False
    if _UNSAFE_SEARCH_REQUEST_RE.search(candidate):
        return False
    # Complete instructions are always a regression: API providers rank their
    # generic phrasing above the actual topic.
    return len(_BOILERPLATE_QUERY_RE.findall(candidate)) <= 1


def _is_specific_concept(value: str) -> bool:
    candidate = _clean(value)
    if not candidate or len(candidate) > 180:
        return False
    cjk_count = sum("\u3400" <= char <= "\u9fff" for char in candidate)
    latin_words = re.findall(r"[a-z0-9]+", candidate.casefold())
    return cjk_count >= 3 or len(latin_words) >= 2


def _string_list(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = [str(item) for item in value]
    else:
        values = []
    return _unique(values)[: max(0, int(limit))]


def _unique(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean(value)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            unique.append(clean)
    return unique


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\r", "\n")).strip()
