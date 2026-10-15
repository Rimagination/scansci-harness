from __future__ import annotations

import re
from typing import Any


def plan_query(question: str, *, max_routes: int = 8, enable_hyde: bool = False) -> dict[str, object]:
    question_type = _question_type(question)
    answer_type = _answer_type(question, question_type=question_type)
    core_terms = _core_terms(question)
    filters: dict[str, object] = {}
    year_min = _year_min(question)
    if year_min is not None:
        filters["year_min"] = year_min
    section_kinds = _section_kinds(question, question_type=question_type)
    if section_kinds:
        filters["section_kinds"] = section_kinds
    section_hints = _section_hints(question, question_type=question_type, answer_type=answer_type)
    required_facets = _required_facets(question, question_type=question_type)
    legacy_variants = _query_variants(question, question_type=question_type, core_terms=core_terms)
    routes = _rewrite_routes(
        question,
        question_type=question_type,
        answer_type=answer_type,
        core_terms=core_terms,
        section_hints=section_hints,
        legacy_variants=legacy_variants,
        max_routes=max_routes,
        enable_hyde=enable_hyde,
    )
    return {
        "query": question,
        "question_type": question_type,
        "answer_type": answer_type,
        "expected_answer_count": _expected_answer_count(question),
        "language": _language(question),
        "core_terms": core_terms,
        "required_facets": required_facets,
        "filters": filters,
        "section_hints": section_hints,
        "rewrite_strategy": "deterministic_query_rewrite_plan_v1",
        "routes": routes,
        "query_variants": legacy_variants,
        "followup_queries": _followup_queries(
            core_terms,
            answer_type=answer_type,
            required_facets=required_facets,
        ),
    }


def query_routes(plan: dict[str, Any], *, max_routes: int = 1) -> list[dict[str, Any]]:
    limit = max(1, int(max_routes))
    raw_routes = plan.get("routes", []) or []
    routes: list[dict[str, Any]] = []
    for index, route in enumerate(raw_routes, start=1):
        if not isinstance(route, dict):
            continue
        query = " ".join(str(route.get("query", "")).split())
        if not query:
            continue
        copied = dict(route)
        copied["query"] = query
        copied.setdefault("label", f"query-{index}")
        copied.setdefault("weight", 1.0)
        routes.append(copied)
        if len(routes) >= limit:
            break
    return routes


def _question_type(question: str) -> str:
    value = question.lower()
    # Check the real Chinese phrases before the legacy compatibility aliases.
    # In particular, "证据是否一致" must be routed as a conflict question.
    if any(term in value for term in ("冲突", "矛盾", "不一致", "是否一致", "相互一致")):
        return "conflict"
    if any(term in value for term in ("综述", "总结", "研究进展", "跨文献", "多篇")):
        return "synthesis"
    if any(term in value for term in ("比较", "对比", "差异")):
        return "comparison"
    if any(term in value for term in ("机制", "为什么", "如何")):
        return "mechanism"
    if any(term in value for term in ("证据", "支持", "依据")):
        return "evidence"
    if any(term in value for term in ("conflict", "conflicting", "contradict", "contradiction", "inconsistent", "冲突", "矛盾", "不一致")):
        return "conflict"
    if any(term in value for term in ("synthesize", "synthesis", "review", "across studies", "across papers", "综述", "总结", "研究进展", "跨文献", "多篇")):
        return "synthesis"
    if any(term in value for term in ("compare", "comparison", "versus", " vs ", "比较", "对比", "差异")):
        return "comparison"
    if any(term in value for term in ("mechanism", "why", "how does", "机制", "为什么", "如何")):
        return "mechanism"
    if any(term in value for term in ("evidence", "support", "supports", "证据", "支持", "依据")):
        return "evidence"
    return "evidence"


def _answer_type(question: str, *, question_type: str) -> str:
    value = question.lower()
    if question_type == "comparison":
        return "comparison"
    if question_type == "conflict":
        return "conflict"
    if question_type == "synthesis":
        return "synthesis"
    if any(term in value for term in ("哪些", "哪几个", "是什么", "列出", "提出")):
        return "named_list" if _expected_answer_count(question) is not None or any(
            term in value for term in ("哪些", "哪几个", "列出")
        ) else "factoid"
    if any(term in value for term in ("多少", "几个")):
        return "count"
    if any(term in value for term in ("which", "what are", "list", "name", "identify", "哪些", "哪几个", "是什么", "列出", "提出")):
        return "named_list" if _expected_answer_count(question) is not None or any(term in value for term in ("which", "哪些", "哪几个", "list", "name")) else "factoid"
    if any(term in value for term in ("how many", "多少", "几个")):
        return "count"
    if question_type == "mechanism":
        return "mechanism"
    return "evidence"


def _core_terms(question: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in re.findall(r"[a-z0-9]+", question.lower()):
        if len(term) <= 1 or term in STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    for translated in _translated_terms(question):
        normalized = translated.lower()
        if len(normalized) <= 1 or normalized in STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    return terms


def _translated_terms(question: str) -> list[str]:
    value = question.lower()
    translated: list[str] = []
    for phrase, terms in PHRASE_TRANSLATIONS:
        if phrase.lower() in value:
            translated.extend(terms)
    return translated


def _year_min(question: str) -> int | None:
    match = re.search(r"\b(?:after|since)\s+(\d{4})\b", question.lower())
    if not match:
        match = re.search(r"(?:之后|以来|以后)\s*(\d{4})", question)
    if not match:
        return None
    return int(match.group(1))


def _section_kinds(question: str, *, question_type: str) -> list[str]:
    # A source policy such as “only cite the methods section when the paper
    # explicitly discusses methods” is an output constraint, not a request to
    # search methods. Strip that clause before detecting actual method intent;
    # otherwise the policy itself turns every evidence answer into a
    # methods-only retrieval.
    method_query = _without_method_policy(question)
    value = method_query.lower()
    if re.search(
        r"\b(methods?|methodological|protocol|procedure|procedures|experimental setup|sample preparation|"
        r"measurement|measured|calibrated|randomized|randomised)\b",
        value,
    ):
        return ["methods"]
    if any(term in method_query for term in ("方法", "实验设计", "流程", "测量", "随机")):
        return ["methods"]
    if re.search(r"\babstract\b", value) or "摘要" in question:
        return ["abstract"]
    return []


def _without_method_policy(question: str) -> str:
    """Remove source-selection instructions before classifying method intent."""

    return re.sub(
        r"(?:\bonly\s+(?:when|if|cite|include|use)|\bunless\b|"
        r"\u53ea\u6709|\u4ec5\u5f53|\u4ec5\u5728|\u9664\u975e)"
        r"[^.!?\u3002\uff01\uff1f\r\n]{0,180}?"
        r"(?:methods?|methodological|\u65b9\u6cd5|\u65b9\u6cd5\u5b66)"
        r"[^.!?\u3002\uff01\uff1f\r\n]{0,100}"
        r"(?:cite|include|use|\u5f15\u7528|\u4f7f\u7528|\u52a0\u5165|\u7eb3\u5165|\u624d|\u65f6)"
        r"[^.!?\u3002\uff01\uff1f\r\n]*",
        " ",
        str(question or ""),
        flags=re.IGNORECASE,
    )


def _section_hints(question: str, *, question_type: str, answer_type: str) -> list[str]:
    value = question.lower()
    hints: list[str] = []
    for keyword, section in SECTION_KEYWORDS:
        if keyword in value or keyword in question:
            hints.append(section)
    if answer_type in {"named_list", "factoid"}:
        hints.extend(["abstract", "introduction"])
    if question_type in {"comparison", "conflict"}:
        hints.extend(["results", "discussion"])
    if question_type == "synthesis":
        hints.extend(["abstract", "results", "discussion", "conclusion"])
    if question_type == "mechanism":
        hints.extend(["results", "discussion"])
    return _unique_nonempty(hints)


_FACET_CUES = re.compile(
    # ``对`` is a preposition in questions such as “电站对植被覆盖、土壤
    # 水分有什么影响”。  Treat it as a cue, but do not consume the first
    # half of ``对比``; the latter is itself a comparison cue.
    r"(?:影响|作用于|作用|涉及|关注|评估|比较|对比|对(?!比)|分析|讨论|考察|解释|关联|关于|针对|\b(?:between|among|for|affect|affects|impact|impacts|influence|influences)\b)",
    re.IGNORECASE,
)
_FACET_SPLIT = re.compile(r"(?:、|，|,|;|；|\band\b|\bor\b|和|与|及|以及)", re.IGNORECASE)
# Interrogative scaffolding inside a candidate CJK run marks the run as part
# of the question sentence rather than a concrete research dimension
# (“机制的核心思想是什么”“请依据资料回答”).  A real facet is a noun phrase such
# as “微气候” or “土壤碳储量”, so such runs are dropped entirely.
_FACET_SCAFFOLDING_RE = re.compile(
    r"是什么|为什么|为何|如何|哪些|有什么|怎么样|请|依据|回答|说明|介绍|列出"
)
_ENGLISH_FACET_CUE = re.compile(
    r"\b(?:affect|affects|affected|impact|impacts|impacted|influence|influences|influenced|change|changes|changed|vary|varies|varied)\b",
    re.IGNORECASE,
)
_FACET_GENERIC = frozenset(
    {
        "影响",
        "作用",
        "研究",
        "结果",
        "方法",
        "过程",
        "分析",
        "比较",
        "讨论",
        "问题",
        "因素",
        "方面",
        "表现",
        "变化",
        "性能",
        "机制",
        "项目",
        "建设年份",
        "不同建设年份",
        "光伏项目",
        # These are answer scaffolding rather than research dimensions.  If
        # they survive list splitting, treating them as required facets makes
        # strict citation completeness fail even when every concrete topic is
        # covered (for example, “微气候有哪些一致结论与争议”).
        "结论",
        "一致结论",
        "主要结论",
        "共同结论",
        "发现",
        "争议",
        "共识",
        "分歧",
        "差异",
        "异同",
    }
)


def _required_facets(question: str, *, question_type: str) -> list[dict[str, object]]:
    """Extract explicit dimensions in a multi-part research question.

    This is intentionally conservative.  Facets are only emitted when the
    question contains at least two concrete list-like dimensions, so ordinary
    factoid questions keep the legacy plan shape and do not acquire a false
    completeness requirement.
    """

    value = " ".join(str(question or "").split()).strip(" ?？。.!！")
    if not value:
        return []
    if not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", value) and not _FACET_SPLIT.search(value):
        return []
    value = re.sub(
        r"^(?:what\s+(?:are|is)|which|how\s+(?:does|do)|the\s+effects?\s+(?:of|on)|effects?\s+(?:of|on)|impact\s+(?:of|on)|influence\s+(?:of|on))\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^(?:effects?|impact|influence)\s+(?:of|on)\s+", "", value, flags=re.IGNORECASE)
    has_cjk = bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", value))
    impact_facets = re.search(
        r"对(?!比)(?P<facets>[^?？。]{2,100}?)(?:的)?(?:分别)?(?:有(?:什么|哪些))?影响",
        value,
    ) if has_cjk else None
    cue_matches = list(_FACET_CUES.finditer(value))
    if impact_facets and _FACET_SPLIT.search(impact_facets.group("facets")):
        value = impact_facets.group("facets")
    elif cue_matches:
        value = value[cue_matches[-1].end() :]
    elif has_cjk:
        # A Chinese question without a dimension cue (“影响”“对比”“对”…) is a
        # single-dimension question.  Splitting its remaining sentences into
        # facets previously promoted ordinary scaffolding such as “机制的核
        # 心思想是什么” and “请依据资料回答” to mandatory research dimensions,
        # and the failed facet coverage then withheld an otherwise supported
        # verified answer.
        return []
    # A leading preposition can remain after the cue (for example, “对微气候”).
    value = re.sub(r"^\s*(?:对|于|在|与|和|及|以及|the|the effects? of|on|in)\s*", "", value, flags=re.I)
    pieces = [piece.strip(" \t,，、;；:：") for piece in _FACET_SPLIT.split(value) if piece.strip()]

    candidates: list[str] = []
    for piece in pieces:
        # Remove trailing question scaffolding and split a compound phrase
        # that did not contain punctuation (e.g. “植被群落和土壤碳储量”).
        piece = re.sub(
            r"(?:是什么|如何|为何|为什么|有哪些|有什么|分别是|分别有哪些)"
            r"(?:一致|共同|主要|总体)?"
            r"(?:结论|发现|争议|共识|分歧|差异|异同)?$",
            "",
            piece,
        ).strip()
        piece = re.sub(
            r"(?:一致|共同|主要|总体)?(?:结论|发现|争议|共识|分歧|差异|异同)$",
            "",
            piece,
        ).strip()
        if not piece:
            continue
        cjk_parts = [
            part
            for part in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,}", piece)
            if not _FACET_SCAFFOLDING_RE.search(part)
        ]
        if cjk_parts:
            candidates.extend(cjk_parts)
            continue
        english_cue = _ENGLISH_FACET_CUE.search(piece)
        if english_cue:
            piece = piece[english_cue.end() :].strip()
        piece = re.sub(r"^(?:the|a|an|its|their|on|in|for)\s+", "", piece, flags=re.IGNORECASE)
        if not piece:
            continue
        words = re.findall(r"[A-Za-z][A-Za-z0-9+./-]*(?:\s+[A-Za-z][A-Za-z0-9+./-]*){0,3}", piece)
        candidates.extend(words)

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = " ".join(candidate.split()).strip(" \t,，、;；:：")
        if not candidate or candidate.casefold() in seen:
            continue
        if candidate in _FACET_GENERIC or len(candidate) < 2:
            continue
        # A CJK run may still include a leading subject when no cue was
        # present. Keep only its final concrete noun phrase in that case.
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", candidate):
            candidate = re.sub(r"^(?:不同|各种|相关|当前|光伏项目|该项目|这些项目)", "", candidate).strip()
        if not candidate or candidate in _FACET_GENERIC or len(candidate) < 2:
            continue
        seen.add(candidate.casefold())
        normalized.append(candidate)

    if len(normalized) < 2:
        return []
    return [
        {"id": facet, "label": facet, "terms": [facet]}
        for facet in normalized[:8]
    ]


def _followup_queries(
    core_terms: list[str],
    *,
    answer_type: str = "evidence",
    required_facets: list[dict[str, object]] | None = None,
) -> list[str]:
    # Multi-part questions need facet-specific recovery queries.  A broad
    # query can satisfy the document/quote count while still omitting one
    # requested dimension (for example, retrieving vegetation and soil but
    # not microclimate).  Keep the legacy generic followups for all other
    # questions, but put explicit facets first so the agent can spend its
    # small followup budget on missing dimensions.
    facet_queries = [
        " ".join(str(facet.get("id", facet.get("label", ""))).split())
        for facet in list(required_facets or [])
        if isinstance(facet, dict)
        and " ".join(str(facet.get("id", facet.get("label", ""))).split())
    ]
    content_terms = [term for term in core_terms if term not in {"compare", "comparison", "after", "since"} and not term.isdigit()]
    followups: list[str] = list(facet_queries)
    if content_terms:
        followups.append(" ".join(content_terms[:4]))
    if answer_type in {"named_list", "factoid"} and content_terms:
        followups.append("definition description " + " ".join(content_terms[:5]))
    if core_terms:
        full = " ".join(core_terms[:8])
        if full not in followups:
            followups.append(full)
    return _unique_nonempty(followups)


def _query_variants(question: str, *, question_type: str, core_terms: list[str]) -> list[str]:
    content_terms = [
        term
        for term in core_terms
        if term not in {"compare", "comparison", "versus", "after", "since", "across", "studies", "papers"}
        and not term.isdigit()
    ]
    candidates = []
    if content_terms:
        candidates.append(" ".join(content_terms[:6]))
    if question_type in {"comparison", "conflict", "synthesis"} and content_terms:
        candidates.append("findings results " + " ".join(content_terms[:5]))
        candidates.append("evidence " + " ".join(content_terms[:6]))
    if question_type == "mechanism" and content_terms:
        candidates.append("mechanism explanation " + " ".join(content_terms[:5]))
    if re.search(r"\b(methods?|protocol|procedure|measurement|sample|randomi[sz]ed)\b", question.lower()) and content_terms:
        candidates.append("methods protocol " + " ".join(content_terms[:5]))
    return _unique_nonempty(candidates)


def _rewrite_routes(
    question: str,
    *,
    question_type: str,
    answer_type: str,
    core_terms: list[str],
    section_hints: list[str],
    legacy_variants: list[str],
    max_routes: int,
    enable_hyde: bool,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = [
        _route("original", question, weight=1.0, purpose="question_as_written", section_hints=section_hints),
    ]
    if legacy_variants:
        candidates.append(
            _route(
                "keywords",
                legacy_variants[0],
                weight=0.95,
                purpose="content_terms_without_question_words",
                section_hints=section_hints,
            )
        )
    if len(legacy_variants) > 1:
        for index, variant in enumerate(legacy_variants[1:], start=1):
            candidates.append(
                _route(
                    f"legacy_variant_{index}",
                    variant,
                    weight=0.85,
                    purpose="existing_conservative_query_variant",
                    section_hints=section_hints,
                )
            )

    expanded_terms = _expanded_terms(core_terms)
    if expanded_terms:
        candidates.append(
            _route(
                "terminology_expansion",
                " ".join(expanded_terms[:12]),
                weight=0.75,
                purpose="controlled_vocabulary_and_inflection_expansion",
                section_hints=section_hints,
            )
        )

    if section_hints and core_terms:
        for section in section_hints[:3]:
            candidates.append(
                _route(
                    f"section_{section}",
                    f"{section} {' '.join(core_terms[:7])}",
                    weight=0.7,
                    purpose="section_aware_retrieval",
                    section_hints=[section],
                )
            )

    if answer_type in {"named_list", "factoid"} and core_terms:
        candidates.append(
            _route(
                "answer_type_named",
                "definition description named listed identified " + " ".join(core_terms[:7]),
                weight=0.65,
                purpose="named_entity_or_list_answer_pattern",
                section_hints=section_hints,
            )
        )

    for index, decomposed in enumerate(_decomposed_queries(question, core_terms=core_terms), start=1):
        candidates.append(
            _route(
                f"decomposition_{index}",
                decomposed,
                weight=0.6,
                purpose="sub_question_or_clause_retrieval",
                section_hints=section_hints,
            )
        )

    if enable_hyde and core_terms:
        candidates.append(
            _route(
                "pseudo_answer",
                " ".join(_pseudo_answer_terms(question_type=question_type, answer_type=answer_type, core_terms=core_terms)),
                weight=0.5,
                purpose="deterministic_hyde_style_query",
                section_hints=section_hints,
            )
        )

    return _unique_routes(candidates)[: max(1, int(max_routes))]


def _route(label: str, query: str, *, weight: float, purpose: str, section_hints: list[str]) -> dict[str, object]:
    return {
        "label": label,
        "query": " ".join(str(query).split()),
        "weight": float(weight),
        "retrieval": ["bm25", "dense"],
        "purpose": purpose,
        "section_hints": list(section_hints),
    }


def _expanded_terms(core_terms: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for term in core_terms:
        for value in [term, *TERM_EXPANSIONS.get(term, [])]:
            normalized = value.lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            expanded.append(normalized)
    return expanded if len(expanded) > len(core_terms) else []


def _decomposed_queries(question: str, *, core_terms: list[str]) -> list[str]:
    value = re.sub(r"\s+", " ", question.strip())
    pieces = re.split(r"\b(?:and|versus|vs\.?|compared with|compared to)\b|以及|和|与|对比|比较", value, flags=re.I)
    content_pieces = []
    for piece in pieces:
        tokens = [
            token
            for token in _core_terms(piece)
            if token not in {"compare", "comparison", "versus"} and not token.isdigit()
        ]
        if len(tokens) >= 2:
            content_pieces.append(" ".join(tokens[:6]))
    if len(content_pieces) >= 2:
        return _unique_nonempty(content_pieces[:3])
    return []


def _pseudo_answer_terms(*, question_type: str, answer_type: str, core_terms: list[str]) -> list[str]:
    prefixes = {
        "comparison": ["findings", "differences", "similarities"],
        "conflict": ["conflicting", "evidence", "supporting", "opposing"],
        "synthesis": ["findings", "results", "evidence", "across", "studies"],
        "mechanism": ["mechanism", "pathway", "explanation"],
    }
    if answer_type == "named_list":
        return ["named", "items", "include", *core_terms[:8]]
    return [*prefixes.get(question_type, ["evidence", "shows"]), *core_terms[:8]]


def _expected_answer_count(question: str) -> int | None:
    value = question.lower()
    # A Chinese number is only a requested item count when followed by a
    # classifier.  The previous permissive regex read the "一" in "一致" as
    # "one answer", silently truncating multi-evidence responses to one claim.
    chinese_count_match = re.search(
        r"([一二两三四五六七八九十]+)\s*(?:个|项|点|条|种|类|方面)",
        question,
    )
    if chinese_count_match:
        chinese_value = _chinese_number(chinese_count_match.group(1))
        if chinese_value is not None:
            return chinese_value
    if "一致" in question:
        return None
    digit_match = re.search(r"\b(\d{1,2})\b|([一二两三四五六七八九十]+)\s*(?:个|项|种|类)?", question)
    if digit_match:
        if digit_match.group(1):
            return int(digit_match.group(1))
        chinese_value = _chinese_number(digit_match.group(2))
        if chinese_value is not None:
            return chinese_value
    for word, number in ENGLISH_NUMBERS.items():
        if re.search(rf"\b{re.escape(word)}\b", value):
            return number
    return None


def _chinese_number(value: str | None) -> int | None:
    if not value:
        return None
    if value in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + CHINESE_NUMBERS.get(value[1:], 0)
    if value.endswith("十") and len(value) == 2:
        return CHINESE_NUMBERS.get(value[:1], 0) * 10
    if "十" in value and len(value) == 3:
        left, right = value.split("十", 1)
        return CHINESE_NUMBERS.get(left, 0) * 10 + CHINESE_NUMBERS.get(right, 0)
    return None


def _language(question: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", question):
        return "zh"
    return "en"


def _unique_routes(routes: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for route in routes:
        query = " ".join(str(route.get("query", "")).split())
        if not query:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        copied = dict(route)
        copied["query"] = query
        result.append(copied)
    return result


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(normalized)
    return result


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "did",
    "does",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "they",
    "to",
    "was",
    "were",
    "what",
    "which",
    "with",
}

SECTION_KEYWORDS = [
    ("abstract", "abstract"),
    ("摘要", "abstract"),
    ("introduction", "introduction"),
    ("background", "introduction"),
    ("引言", "introduction"),
    ("methods", "methods"),
    ("method", "methods"),
    ("protocol", "methods"),
    ("方法", "methods"),
    ("results", "results"),
    ("result", "results"),
    ("结果", "results"),
    ("discussion", "discussion"),
    ("讨论", "discussion"),
    ("conclusion", "conclusion"),
    ("结论", "conclusion"),
    ("table", "table"),
    ("figure", "figure"),
    ("图", "figure"),
    ("表", "table"),
]

TERM_EXPANSIONS = {
    "accuracies": ["accuracy", "performance", "score", "scores", "results"],
    "accuracy": ["accuracies", "performance", "score", "scores", "results"],
    "approach": ["method", "model", "system", "framework"],
    "approaches": ["methods", "models", "systems", "frameworks"],
    "baseline": ["baselines", "comparison", "compare"],
    "baselines": ["baseline", "comparison", "compare"],
    "cancer": ["tumor", "tumour", "oncology"],
    "data": ["dataset", "datasets", "corpus", "corpora"],
    "dataset": ["datasets", "data", "corpus", "corpora", "evaluation"],
    "datasets": ["dataset", "data", "corpus", "corpora", "evaluation"],
    "evidence": ["support", "supports", "finding", "findings"],
    "experiment": ["experiments", "experimental", "evaluation", "evaluate"],
    "experiments": ["experiment", "experimental", "evaluation", "evaluate"],
    "feature": ["characteristic", "attribute"],
    "features": ["characteristics", "attributes"],
    "hallmark": ["hallmarks", "capability", "trait"],
    "hallmarks": ["hallmark", "capabilities", "traits"],
    "method": ["methods", "approach", "model", "system"],
    "methods": ["method", "approaches", "models", "systems"],
    "model": ["models", "system", "method", "approach"],
    "models": ["model", "systems", "methods", "approaches"],
    "mutation": ["mutations", "mutational"],
    "result": ["results", "performance", "score", "scores", "accuracy"],
    "results": ["result", "performance", "score", "scores", "accuracy"],
    "tumor": ["tumour", "cancer", "oncology"],
    "tumour": ["tumor", "cancer", "oncology"],
}

PHRASE_TRANSLATIONS = [
    ("光伏", ["photovoltaic", "solar photovoltaic", "PV"]),
    ("光伏发电", ["photovoltaic power generation", "solar photovoltaic", "PV"]),
    ("光伏电站", ["photovoltaic power station", "solar farm", "PV plant"]),
    ("自注意力", ["self-attention", "attention"]),
    ("双向", ["bidirectional", "left", "right", "context"]),
    ("左侧上下文", ["left-to-right", "previous", "tokens", "causal"]),
    ("上下文", ["context"]),
    ("掩码语言模型", ["masked", "language", "model", "mlm"]),
    ("自回归", ["autoregressive", "left-to-right", "next", "token"]),
    ("微调", ["fine-tuning", "finetuning"]),
    ("少样本", ["few-shot", "in-context"]),
    ("零样本", ["zero-shot"]),
    ("训练目标", ["training", "objective"]),
    ("数据集", ["dataset", "benchmark", "corpus"]),
    ("语料", ["corpus", "dataset"]),
    ("方法", ["method", "approach"]),
    ("模型", ["model"]),
    ("实验", ["experiment", "evaluation"]),
    ("结果", ["results", "findings"]),
    ("证据", ["evidence", "support"]),
    ("综述", ["review", "synthesis"]),
    ("比较", ["compare", "comparison"]),
    ("对比", ["compare", "comparison"]),
    ("机制", ["mechanism"]),
    ("标志", ["hallmark", "hallmarks"]),
    ("使能特征", ["enabling", "characteristics"]),
    ("促成特征", ["enabling", "characteristics"]),
    ("特征", ["characteristics", "features"]),
    ("癌症", ["cancer", "tumor"]),
    ("肿瘤", ["tumor", "cancer"]),
    ("基因组", ["genome", "genomic"]),
    ("不稳定", ["instability", "unstable"]),
    ("突变", ["mutation", "mutations"]),
    ("炎症", ["inflammation"]),
    ("论文", ["paper", "study"]),
]

ENGLISH_NUMBERS = {
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

CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
