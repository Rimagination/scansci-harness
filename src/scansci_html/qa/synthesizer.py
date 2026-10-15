from __future__ import annotations

import json
import re
from typing import Any

from .quote_extractor import ChatJsonClient
from .schemas import AnswerPayloadSchema
from ..text_tokenization import lexical_tokens


def synthesize_answer(
    question: str,
    evidence_table: list[dict[str, Any]],
    *,
    query_plan: dict[str, Any] | None = None,
) -> dict[str, object]:
    if not evidence_table:
        return {
            "question": question,
            "answer": [],
            "limitations": ["No validated evidence quotes were available for this question."],
            "insufficient_evidence": True,
        }

    if _is_conflict_question(question):
        conflict_claim = _conflict_claim(evidence_table)
        if conflict_claim:
            return {
                "question": question,
                "answer": [conflict_claim],
                "limitations": [],
                "insufficient_evidence": False,
            }

    claims: list[dict[str, object]] = []
    seen_claims: dict[str, list[str]] = {}
    ranked_rows = _rank_local_evidence(question, evidence_table, query_plan=query_plan)
    required_facets = [
        facet
        for facet in list((query_plan or {}).get("required_facets", []) or [])
        if isinstance(facet, dict)
    ]
    if required_facets:
        ranked_rows = _select_facet_rows(ranked_rows, required_facets)
        ranked_rows = ranked_rows[: min(4, max(1, len(required_facets)))]
    elif len(evidence_table) > 1:
        ranked_rows = ranked_rows[: 1 if _is_direct_question(question) else 3]
    for row in ranked_rows:
        claim_text = (
            _best_local_excerpt(question, str(row.get("exact_quote", "")))
            if len(evidence_table) > 1
            else str(row.get("claim_target", "")).strip()
        )
        claim_text = claim_text or str(row.get("claim_target", "")).strip()
        quote_id = str(row.get("quote_id", "")).strip()
        if not claim_text or not quote_id:
            continue
        seen_claims.setdefault(claim_text, [])
        if quote_id not in seen_claims[claim_text]:
            seen_claims[claim_text].append(quote_id)

    for index, (claim_text, quote_ids) in enumerate(seen_claims.items(), start=1):
        claims.append(
            {
                "claim_id": f"c{index:04d}",
                "text": claim_text,
                "quote_ids": quote_ids,
                "support_status": "supported_by_evidence_table",
            }
        )

    return {
        "question": question,
        "answer": claims,
        "limitations": [],
        "insufficient_evidence": False if claims else True,
    }


def _select_facet_rows(
    ranked_rows: list[dict[str, Any]],
    required_facets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select at least one high-ranked row for each requested dimension."""

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for facet in required_facets:
        terms = [
            " ".join(str(term).split()).strip().casefold()
            for term in list(facet.get("terms", []) or [])
            if " ".join(str(term).split()).strip()
        ]
        if not terms:
            terms = [" ".join(str(facet.get("label", facet.get("id", ""))).split()).strip().casefold()]
        for row in ranked_rows:
            row_id = str(row.get("quote_id", row.get("evidence_id", "")))
            if row_id in selected_ids:
                continue
            text = " ".join(
                str(row.get(field, ""))
                for field in ("exact_quote", "claim_target")
                if str(row.get(field, "")).strip()
            ).casefold()
            semantic_score = float(dict(row.get("semantic_facet_scores", {}) or {}).get(str(facet.get("id", "")), 0.0) or 0.0)
            if any(term and term in text for term in terms) or semantic_score >= 0.65:
                selected.append(row)
                selected_ids.add(row_id)
                break
    for row in ranked_rows:
        row_id = str(row.get("quote_id", row.get("evidence_id", "")))
        if row_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(row_id)
    return selected


def _rank_local_evidence(
    question: str,
    evidence_table: list[dict[str, Any]],
    *,
    query_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    terms = _question_evidence_terms(question)
    named_list = str((query_plan or {}).get("answer_type", "")) == "named_list"
    indexed = list(enumerate(evidence_table))

    def score(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        index, row = item
        quote = " ".join(str(row.get("exact_quote", "")).split()).casefold()
        matched = sum(1.0 for term in terms if term in quote)
        if named_list:
            matched = min(2.0, matched) + _named_list_answer_bonus(quote)
        facet_bonus = 0.0
        for facet in list((query_plan or {}).get("required_facets", []) or []):
            if not isinstance(facet, dict):
                continue
            facet_terms = [
                " ".join(str(term).split()).strip().casefold()
                for term in list(facet.get("terms", []) or [])
                if " ".join(str(term).split()).strip()
            ]
            if any(term and term in quote for term in facet_terms):
                facet_bonus += 2.0
        confidence = float(row.get("confidence", 0.0) or 0.0)
        length_penalty = max(0.0, (len(quote) - 420) / 700)
        return (matched + facet_bonus + confidence - length_penalty, -index)

    return [row for _, row in sorted(indexed, key=score, reverse=True)]


def _named_list_answer_bonus(quote: str) -> float:
    cues = (
        "we find",
        "include",
        "including",
        "such as",
        "whereas",
        "others",
        "parts of",
        "first ",
        "second ",
        "third ",
        "respectively",
        "包括",
        "分别",
        "一是",
        "二是",
        "三是",
    )
    return min(4.0, 0.9 * sum(cue in quote for cue in cues))


def _question_evidence_terms(question: str) -> set[str]:
    terms = {
        token.casefold()
        for token in lexical_tokens(question)
        if re.fullmatch(r"[a-z0-9][a-z0-9.-]*", token) and len(token) >= 3
    }
    mappings = {
        "双向": {"bidirectional", "left and right", "both directions"},
        "左侧": {"left-to-right", "previous tokens", "left context"},
        "自注意力": {"self-attention", "self attention"},
        "掩码": {"masked language", "mlm"},
        "微调": {"fine-tuning", "fine tuning"},
        "少样本": {"few-shot", "few shot"},
        "零样本": {"zero-shot", "zero shot"},
        "自回归": {"autoregressive", "left-to-right", "next token"},
    }
    folded = question.casefold()
    for cue, values in mappings.items():
        if cue in folded:
            terms.update(values)
    return terms


def _best_local_excerpt(question: str, exact_quote: str) -> str:
    clean = " ".join(str(exact_quote or "").split()).strip()
    if not clean:
        return ""
    terms = _question_evidence_terms(question)
    segments = [
        segment.strip(" •")
        for segment in re.split(r"(?<=[.!?。！？])\s+|\s*[•]\s*", clean)
        if segment.strip(" •")
    ]
    if not segments:
        segments = [clean]
    best = max(
        enumerate(segments),
        key=lambda item: (
            sum(term in item[1].casefold() for term in terms),
            -abs(len(item[1]) - 180),
            -item[0],
        ),
    )[1]
    best = re.split(r"\s+\d+(?:https?://|www\.)", best, maxsplit=1, flags=re.I)[0]
    best = re.sub(r"(?<=[.!?。！？])\d+$", "", best).strip()
    best = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", best)
    if len(best) <= 360:
        return best
    clipped = best[:360]
    boundary = clipped.rfind(" ")
    return clipped[:boundary].rstrip(" ,;:") if boundary >= 120 else ""


def _is_direct_question(question: str) -> bool:
    value = question.strip().casefold()
    return any(marker in value for marker in ("？", "?", "是否", "还是", "吗", "does ", "is ", "are ", "can "))


def _is_conflict_question(question: str) -> bool:
    value = question.lower()
    return any(term in value for term in ("conflict", "conflicting", "contradict", "contradiction", "inconsistent"))


def _conflict_claim(evidence_table: list[dict[str, Any]]) -> dict[str, object] | None:
    parts: list[str] = []
    quote_ids: list[str] = []
    for row in evidence_table:
        quote_id = str(row.get("quote_id", "")).strip()
        claim_text = str(row.get("claim_target", "") or row.get("exact_quote", "")).strip()
        if not quote_id or not claim_text:
            continue
        prefix = "One source reports that" if not parts else "another source reports that"
        parts.append(f"{prefix} {_without_terminal_period(claim_text)}")
        if quote_id not in quote_ids:
            quote_ids.append(quote_id)
    if len(parts) < 2:
        return None
    return {
        "claim_id": "c0001",
        "text": "; ".join(parts) + ".",
        "quote_ids": quote_ids,
        "support_status": "supported_by_evidence_table",
    }


def _without_terminal_period(text: str) -> str:
    return text.rstrip().rstrip(".")


def synthesize_answer_with_llm(
    question: str,
    evidence_table: list[dict[str, Any]],
    *,
    chat_client: ChatJsonClient,
    query_plan: dict[str, Any] | None = None,
) -> dict[str, object]:
    if not evidence_table:
        return synthesize_answer(question, evidence_table)
    compact_evidence = _compact_evidence_for_llm(evidence_table)
    target_language = _requested_answer_language(question, query_plan=query_plan)
    is_synthesis = str((query_plan or {}).get("question_type", "")).strip().lower() == "synthesis"
    known_quote_ids = {str(row.get("quote_id", "")) for row in evidence_table}
    attempts = 2 if target_language == "zh" else 1
    for attempt in range(attempts):
        messages = _llm_synthesis_messages(
            question,
            compact_evidence,
            target_language=target_language,
            is_synthesis=is_synthesis,
            retrying_language=attempt > 0,
            query_plan=query_plan,
        )
        payload = AnswerPayloadSchema.model_validate(chat_client.complete_json(messages, schema_name="answer_claims") or {})
        claims = _validated_llm_claims(payload.answer, known_quote_ids)
        if target_language != "zh" or _claims_are_natural_chinese(claims):
            return {
                "question": question,
                "answer": claims,
                "limitations": [str(item) for item in payload.limitations],
                "insufficient_evidence": False if claims else True,
            }

    # The source material may be English even when the user asks in Chinese.
    # Preserve the verified claims as a last-resort evidence answer instead of
    # turning useful, cited material into an empty refusal. The caller marks
    # this explicitly so the UI can explain the degraded presentation.
    return {
        "question": question,
        "answer": claims,
        "limitations": [
            "生成模型未完成中文改写；以下保留经核验的原文证据摘录，便于继续核查。"
        ],
        "insufficient_evidence": False if claims else True,
        "language_fallback": True,
    }


def _llm_synthesis_messages(
    question: str,
    evidence_table: list[dict[str, str]],
    *,
    target_language: str,
    is_synthesis: bool,
    retrying_language: bool,
    query_plan: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    language_rule = (
        "你正在为中文科研用户撰写答案。所有结论必须使用自然、简洁的简体中文；需要转述英文证据，不能粘贴英文原句。"
        "英文仅可作为不可替代的专有名词、缩写或术语括注。"
        if target_language == "zh"
        else "Answer in the same language as the question. "
    )
    synthesis_rule = (
        "这是“研究进展/研究现状”问题。请写成紧凑的证据综述，而不是摘录列表或参考文献表。"
        "围绕证据支持的研究方向、主要发现和局限组织结论；所有宽泛判断必须限定为本次检索到的资料，不得把少量文献说成整个领域。"
        if target_language == "zh"
        else "This is a research-status synthesis. Produce a compact evidence review, not a list of excerpts or bibliography. "
        "Organize the claims around the evidence-supported research directions, findings, and limitations. "
        "Scope every broad statement to the retrieved literature; do not present a small library sample as the whole field. "
        if is_synthesis
        else ""
    )
    required_facets = [
        str(facet.get("label", facet.get("id", ""))).strip()
        for facet in list((query_plan or {}).get("required_facets", []) or [])
        if isinstance(facet, dict) and str(facet.get("label", facet.get("id", ""))).strip()
    ]
    facet_rule = (
        "Explicitly cover each requested dimension when the evidence table contains support; "
        "if a dimension is not supported, do not invent it and state it as missing: "
        + ", ".join(required_facets)
        + ". "
        if required_facets
        else ""
    )
    retry_rule = (
        "上一稿主体是英文原文摘录，未达到中文回答要求。现在必须把结论改写为简体中文，不要输出英文引文句或参考文献条目。"
        if target_language == "zh"
        else "The previous draft was rejected because it was dominated by source-language excerpts. "
        "This retry MUST be a synthesis in the requested language, not a quotation or reference entry. "
        if retrying_language
        else ""
    )
    return [
        {
            "role": "system",
            "content": (
                language_rule
                + synthesis_rule
                + facet_rule
                + retry_rule
                + "Use only relevant rows from the evidence table. Return 1 to at most 4 concise, non-overlapping claims "
                "ordered by importance; never repeat a claim. Every claim must be directly entailed by its exact supporting "
                "quote_ids. Do not infer the objective, architecture or result of an entity that the cited quote does not name. "
                "Do not output raw quotations, a reference list, or uncited general background."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "输出语言": "简体中文" if target_language == "zh" else "与问题相同",
                    "写作任务": "研究现状证据综述" if is_synthesis else "证据约束回答",
                    "evidence_table": evidence_table,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _validated_llm_claims(answer_claims: list[Any], known_quote_ids: set[str]) -> list[dict[str, object]]:
    claims: list[dict[str, object]] = []
    seen_claim_texts: set[str] = set()
    for claim in answer_claims:
        normalized_text = " ".join(claim.text.split()).casefold()
        if not normalized_text or normalized_text in seen_claim_texts:
            continue
        seen_claim_texts.add(normalized_text)
        quote_ids = [str(quote_id) for quote_id in claim.quote_ids]
        for quote_id in quote_ids:
            if quote_id not in known_quote_ids:
                raise ValueError(f"answer claim references unknown quote_id: {quote_id}")
        claims.append(
            {
                "claim_id": claim.claim_id or f"c{len(claims) + 1:04d}",
                "text": claim.text,
                "quote_ids": quote_ids,
                "support_status": "pending_verification",
            }
        )
        if len(claims) >= 4:
            break
    return claims


def _requested_answer_language(question: str, *, query_plan: dict[str, Any] | None = None) -> str:
    planned = str((query_plan or {}).get("language", "")).strip().lower()
    if planned in {"zh", "zh-cn", "chinese"}:
        return "zh"
    return "zh" if re.search(r"[\u4e00-\u9fff]", question) else "other"


def _claims_are_natural_chinese(claims: list[dict[str, object]]) -> bool:
    text = " ".join(str(claim.get("text", "")) for claim in claims)
    if not text.strip():
        return True
    chinese_characters = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_characters = len(re.findall(r"[A-Za-z]", text))
    return chinese_characters >= 2 and chinese_characters >= latin_characters * 0.04


def _compact_evidence_for_llm(evidence_table: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep evidence prompts below conservative compatible-gateway limits.

    The complete evidence rows remain in ScanSci for citation verification and
    source navigation.  The model only needs the quote identifier, exact quote,
    claim hint and compact provenance to write supported claims.  This mirrors
    mature agent runtimes that prune bulky tool metadata before the next model
    step instead of sending paths, anchors and duplicate parent blocks back to
    every provider.
    """

    rows: list[dict[str, str]] = []
    for raw in evidence_table[:12]:
        row = {
            "quote_id": str(raw.get("quote_id", "")),
            "claim_target": _truncate_utf8(str(raw.get("claim_target", "")), 480),
            "exact_quote": _truncate_utf8(str(raw.get("exact_quote", "")), 720),
            "paper": _truncate_utf8(str(raw.get("paper", "")), 240),
            "section": _truncate_utf8(str(raw.get("section", "")), 120),
            "stance": _truncate_utf8(str(raw.get("stance", "")), 80),
        }
        rows.append(row)

    # Managed and self-hosted compatible gateways often enforce a request-body
    # limit lower than the advertised model context.  Stay within 12 KiB while
    # retaining source diversity whenever possible.
    while len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) > 12_000 and len(rows) > 3:
        rows.pop()
    return rows


def _truncate_utf8(value: str, max_bytes: int) -> str:
    clean = " ".join(str(value or "").split())
    encoded = clean.encode("utf-8")
    if len(encoded) <= max_bytes:
        return clean
    clipped = encoded[: max(1, int(max_bytes) - 3)]
    while clipped:
        try:
            return clipped.decode("utf-8") + "…"
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return "…"
