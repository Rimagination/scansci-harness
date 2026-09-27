from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .evidence_table import build_evidence_table
from .query_planner import plan_query, query_routes
from .quote_extractor import (
    ChatJsonClient,
    ExtractedQuote,
    extract_quotes,
    extract_quotes_with_llm,
    is_substantive_evidence_hit,
)
from .synthesizer import synthesize_answer, synthesize_answer_with_llm
from .verifier import apply_verification_policy, verify_answer_claims, verify_answer_claims_with_llm
from ..query_fusion import fuse_ranked_hits
from ..retrieval import STOPWORDS, search_evidence_store
from ..text_tokenization import lexical_tokens


def answer_question(
    db_path: str | Path,
    question: str,
    *,
    limit: int = 12,
    max_quotes: int = 8,
    min_quotes: int = 1,
    min_documents: int = 1,
    adequacy_profile: str = "manual",
    agentic_profile: str = "custom",
    query_variants: int = 1,
    max_followup_queries: int = 2,
    paper_recall_limit: int = 0,
    per_document_limit: int = 5,
    context_mode: str = "sentence",
    embedding_provider: Any | None = None,
    reranker: Any | None = None,
    quote_provider: str = "local",
    answer_provider: str = "local",
    verification_provider: str = "local",
    query_rewrite_provider: str = "local",
    chat_client: ChatJsonClient | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query_plan = plan_query(question)
    if str(query_plan.get("question_type", "")).strip().lower() == "synthesis":
        # A research-status question needs enough independent material to be a
        # synthesis, rather than a polished paraphrase of a single excerpt.
        if str(adequacy_profile).strip().lower() == "manual":
            adequacy_profile = "auto"
        query_variants = max(2, int(query_variants))
        max_followup_queries = max(2, int(max_followup_queries))
    query_rewrite_generation = {"provider": "local", "fallback": False, "reason": ""}
    model_retrieval_queries: list[str] = []
    if _uses_llm(query_rewrite_provider):
        if chat_client is None:
            raise ValueError("chat_client is required when query_rewrite_provider is llm")
        query_rewrite_generation = {"provider": "llm", "fallback": False, "reason": ""}
        try:
            model_retrieval_queries = _model_retrieval_queries(question, chat_client=chat_client)
        except (RuntimeError, TimeoutError, ValueError) as error:
            query_rewrite_generation = {
                "provider": "local",
                "fallback": True,
                "reason": f"{type(error).__name__}: {error}"[:300],
            }
    agentic_controls = resolve_agentic_controls(
        query_plan,
        profile=agentic_profile,
        query_variants=query_variants,
        max_followup_queries=max_followup_queries,
        paper_recall_limit=paper_recall_limit,
    )
    query_variants = int(agentic_controls["query_variants"])
    max_followup_queries = int(agentic_controls["max_followup_queries"])
    paper_recall_limit = int(agentic_controls["paper_recall_limit"])
    adequacy_thresholds = evidence_adequacy_thresholds(
        str(query_plan.get("question_type", "")),
        profile=adequacy_profile,
        min_quotes=min_quotes,
        min_documents=min_documents,
    )
    planned_filters = dict(query_plan.get("filters", {}) or {})
    planned_filters.update(dict(filters or {}))
    initial_routes = _initial_retrieval_routes(
        question,
        query_plan,
        query_variants=query_variants,
        model_queries=model_retrieval_queries,
    )
    retrieval_query_routes: list[dict[str, Any]] = [dict(route) for route in initial_routes]
    retrieval_queries = [str(route.get("query", "")) for route in initial_routes]
    hits = _search_rewrite_routes(
        db_path,
        question,
        initial_routes,
        limit=limit,
        per_document_limit=per_document_limit,
        filters=planned_filters,
        embedding_provider=embedding_provider,
        reranker=reranker,
        context_mode=context_mode,
        paper_recall_limit=paper_recall_limit,
    )
    hits = _expand_hits_with_neighbor_context(db_path, hits, query_plan=query_plan)
    hits = _prioritize_evidence_sections(hits, query_plan)
    hits = _prioritize_required_facet_hits(hits, query_plan)
    quotes = _extract_quotes_for_provider(
        question,
        hits,
        max_quotes=max_quotes,
        quote_provider=quote_provider,
        chat_client=chat_client,
    )
    quote_dicts = [quote.to_dict() for quote in quotes]
    adequacy = assess_evidence_adequacy(
        quote_dicts,
        min_quotes=int(adequacy_thresholds["min_quotes"]),
        min_documents=int(adequacy_thresholds["min_documents"]),
        profile=str(adequacy_thresholds["profile"]),
    )
    adequacy = _apply_relation_grounding_gate(question, hits, adequacy)
    # Check facet coverage before deciding whether the initial retrieval is
    # sufficient.  Count/doc thresholds alone can pass while one explicit
    # research dimension is absent, which previously prevented the slow path
    # from issuing a targeted recovery query.
    initial_evidence_by_id = {str(hit.get("evidence_id", "")): hit for hit in hits}
    initial_evidence_table = build_evidence_table(quotes, initial_evidence_by_id)
    facet_coverage = assess_facet_coverage(query_plan, initial_evidence_table)
    agentic_steps: list[dict[str, Any]] = [
        _agentic_trace_step(
            "initial_retrieval",
            queries=[str(route.get("query", "")) for route in initial_routes],
            route_labels=[str(route.get("label", "")) for route in initial_routes],
            hit_count=len(hits),
            quote_count=len(quote_dicts),
            adequacy=adequacy,
            reason="planned_query_routes",
        )
    ]
    slow_path_triggered = False
    if (
        not bool(adequacy.get("is_sufficient", False))
        or facet_coverage["status"] in {"partial", "none"}
    ):
        followup_budget = max(0, int(max_followup_queries))
        executed_followups = 0
        for followup_index, followup_query in enumerate(
            list(query_plan.get("followup_queries", []) or []), start=1
        ):
            if executed_followups >= followup_budget:
                break
            followup = str(followup_query).strip()
            if not followup or followup in retrieval_queries:
                continue
            # Skip a facet query that is already covered, leaving the budget
            # for the first missing dimension instead of burning a request on
            # a duplicate.
            covered_facets = set(facet_coverage.get("covered_facets", []) or [])
            if followup in covered_facets:
                continue
            if model_retrieval_queries and not _followup_preserves_query_identity(followup, model_retrieval_queries):
                continue
            executed_followups += 1
            slow_path_triggered = True
            followup_route = {
                "label": f"followup_{followup_index}",
                "query": followup,
                "weight": 0.55,
                "retrieval": ["bm25", "dense"],
                "purpose": "evidence_adequacy_followup",
                "section_hints": list(query_plan.get("section_hints", []) or []),
            }
            retrieval_query_routes.append(followup_route)
            retrieval_queries.append(followup)
            followup_hits = _search_rewrite_routes(
                db_path,
                question,
                [followup_route],
                limit=limit,
                per_document_limit=per_document_limit,
                filters=planned_filters,
                embedding_provider=embedding_provider,
                reranker=reranker,
                context_mode=context_mode,
                paper_recall_limit=paper_recall_limit,
            )
            hits = _merge_hits(hits, followup_hits)
            hits = _expand_hits_with_neighbor_context(db_path, hits, query_plan=query_plan)
            hits = _prioritize_evidence_sections(hits, query_plan)
            hits = _prioritize_required_facet_hits(hits, query_plan)
            quotes = _extract_quotes_for_provider(
                question,
                hits,
                max_quotes=max_quotes,
                quote_provider=quote_provider,
                chat_client=chat_client,
            )
            quote_dicts = [quote.to_dict() for quote in quotes]
            adequacy = assess_evidence_adequacy(
                quote_dicts,
                min_quotes=int(adequacy_thresholds["min_quotes"]),
                min_documents=int(adequacy_thresholds["min_documents"]),
                profile=str(adequacy_thresholds["profile"]),
            )
            adequacy = _apply_relation_grounding_gate(question, hits, adequacy)
            evidence_by_id = {str(hit.get("evidence_id", "")): hit for hit in hits}
            evidence_table = build_evidence_table(quotes, evidence_by_id)
            facet_coverage = assess_facet_coverage(query_plan, evidence_table)
            agentic_steps.append(
                _agentic_trace_step(
                    "followup_retrieval",
                    queries=[followup],
                    route_labels=[str(followup_route["label"])],
                    hit_count=len(hits),
                    quote_count=len(quote_dicts),
                    adequacy=adequacy,
                    reason=str(adequacy.get("followup_reason", "")) or "evidence_adequacy_followup",
                )
            )
            if bool(adequacy.get("is_sufficient", False)) and facet_coverage["status"] in {
                "complete",
                "not_applicable",
            }:
                break
    adequacy = _soften_adequacy_for_review(query_plan, adequacy)
    evidence_by_id = {str(hit.get("evidence_id", "")): hit for hit in hits}
    evidence_table = build_evidence_table(quotes, evidence_by_id)
    # Generic relevance gate: a question whose non-stopword domain terms
    # never appear in any retrieved quote has no topical connection to this
    # library.  The specialised causal grounding check above only covers
    # English "X caused Y" patterns; this fallback handles any question.
    adequacy = _apply_topical_relevance_gate(question, evidence_table, adequacy, hits=hits)
    facet_coverage = assess_facet_coverage(query_plan, evidence_table)
    if facet_coverage["status"] != "not_applicable":
        current_answerability = str(adequacy.get("answerability", "")).strip()
        facet_answerability = {
            "complete": "answerable",
            "partial": "partially_answerable",
            "none": "not_enough_information",
        }.get(str(facet_coverage["status"]), "not_enough_information")
        # A borderline topical match must not be upgraded to a confident
        # answer merely because one requested facet happens to overlap.
        merged_answerability = (
            "needs_review"
            if current_answerability == "needs_review" and facet_coverage["status"] != "none"
            else facet_answerability
        )
        adequacy = {
            **adequacy,
            "facet_coverage": facet_coverage,
            "answerability": merged_answerability,
        }
        if str(facet_coverage["status"]) == "none" and bool(adequacy.get("is_sufficient", False)):
            adequacy = {
                **adequacy,
                "is_sufficient": False,
                "followup_reason": "retrieved evidence does not cover any requested research dimension",
            }
    if bool(adequacy.get("is_sufficient", False)) and not str(adequacy.get("answerability", "")).strip():
        adequacy = {**adequacy, "answerability": "answerable"}
    answer_generation = {"provider": "local-evidence", "fallback": False, "reason": ""}
    verification_generation = {"provider": "local-evidence", "fallback": False, "reason": ""}
    can_generate_answer = _can_generate_answer(adequacy, evidence_table)
    if not can_generate_answer:
        verified_answer = apply_verification_policy(_insufficient_adequacy_answer(question, adequacy))
    elif _uses_llm(answer_provider):
        if chat_client is None:
            raise ValueError("chat_client is required when answer_provider is llm")
        answer_generation = {"provider": "llm", "fallback": False, "reason": ""}
        try:
            answer = synthesize_answer_with_llm(
                question,
                evidence_table,
                chat_client=chat_client,
                query_plan=query_plan,
            )
        except (RuntimeError, TimeoutError, ValueError) as error:
            # A compatible provider may ignore response_format or emit prose
            # without stable quote IDs.  Never discard an otherwise valid
            # evidence task: synthesize directly from the already validated
            # evidence table, then run the same verification gate below.
            answer = synthesize_answer(question, evidence_table, query_plan=query_plan)
            answer_generation = {
                "provider": "local-evidence",
                "fallback": True,
                "reason": f"{type(error).__name__}: {error}"[:300],
            }
        if _uses_llm(verification_provider):
            if chat_client is None:
                raise ValueError("chat_client is required when verification_provider is llm")
            verification_generation = {"provider": "llm", "fallback": False, "reason": ""}
            try:
                verified_answer = verify_answer_claims_with_llm(answer, evidence_table, chat_client=chat_client)
            except (RuntimeError, TimeoutError, ValueError) as error:
                verified_answer = verify_answer_claims(answer, evidence_table)
                verification_generation = {
                    "provider": "local-evidence",
                    "fallback": True,
                    "reason": f"{type(error).__name__}: {error}"[:300],
                }
        else:
            verified_answer = verify_answer_claims(answer, evidence_table)
        verified_answer = apply_verification_policy(verified_answer)
    else:
        answer_generation = {"provider": "local-evidence", "fallback": False, "reason": ""}
        answer = synthesize_answer(question, evidence_table, query_plan=query_plan)
        if _uses_llm(verification_provider):
            if chat_client is None:
                raise ValueError("chat_client is required when verification_provider is llm")
            verification_generation = {"provider": "llm", "fallback": False, "reason": ""}
            try:
                verified_answer = verify_answer_claims_with_llm(answer, evidence_table, chat_client=chat_client)
            except (RuntimeError, ValueError) as error:
                verified_answer = verify_answer_claims(answer, evidence_table)
                verification_generation = {
                    "provider": "local-evidence",
                    "fallback": True,
                    "reason": f"{type(error).__name__}: {error}"[:300],
                }
        else:
            verified_answer = verify_answer_claims(answer, evidence_table)
        verified_answer = apply_verification_policy(verified_answer)
    verified_answer = _filter_answer_to_requested_source_language(verified_answer, evidence_table, question)
    if verified_answer.get("source_language_filter"):
        # Re-run the ordinary citation policy after removing claims that do not
        # satisfy an explicit source-language request.  The UI must never show
        # a filtered claim as verified merely because it was verified before the
        # presentation constraint was applied.
        verified_answer = apply_verification_policy(verify_answer_claims(verified_answer, evidence_table))
    if can_generate_answer and _requires_chinese_claims(question, query_plan):
        if not _verified_answer_has_chinese_claims(verified_answer):
            # Citation integrity comes first: never discard supported material
            # merely because a provider ignored the requested output language.
            # The reader marks this as a transparent degradation instead.
            verified_answer["language_fallback"] = True
            limitations = list(verified_answer.get("limitations", []) or [])
            notice = "生成模型未完成中文改写；以下保留经核验的原文证据摘录，便于继续核查。"
            if notice not in limitations:
                limitations.append(notice)
            verified_answer["limitations"] = limitations
            answer_generation = {
                "provider": "evidence-excerpt-fallback",
                "fallback": True,
                "reason": "generated claims did not meet the requested Chinese answer language",
            }
    citation_verification = verify_citations(
        verified_answer,
        evidence_table,
        required_facets=list(query_plan.get("required_facets", []) or []) or None,
    )
    citation_repair = {
        "attempted": False,
        "applied": False,
        "reason": "",
    }
    if (
        can_generate_answer
        and not bool(citation_verification.get("passed", False))
        and citation_verification.get("citation_completeness", True) is not False
    ):
        failed_verification = citation_verification
        citation_repair["attempted"] = True
        repaired_answer = synthesize_answer(question, evidence_table, query_plan=query_plan)
        repaired_answer = apply_verification_policy(verify_answer_claims(repaired_answer, evidence_table))
        if _requires_chinese_claims(question, query_plan) and not _verified_answer_has_chinese_claims(repaired_answer):
            repaired_answer["language_fallback"] = True
            limitations = list(repaired_answer.get("limitations", []) or [])
            notice = "生成模型的答案未通过逐条引用核验；以下自动改用经核验的原文证据摘录。"
            if notice not in limitations:
                limitations.append(notice)
            repaired_answer["limitations"] = limitations
        repaired_verification = verify_citations(
            repaired_answer,
            evidence_table,
            required_facets=list(query_plan.get("required_facets", []) or []) or None,
        )
        if bool(repaired_verification.get("passed", False)):
            citation_repair.update(
                {
                    "applied": True,
                    "reason": "generated answer failed strict citation verification; rebuilt from validated evidence rows",
                    "failed_verification": failed_verification,
                }
            )
            repaired_answer["citation_repair"] = citation_repair
            verified_answer = repaired_answer
            citation_verification = repaired_verification
            answer_generation = {
                "provider": "local-evidence",
                "fallback": True,
                "reason": "strict citation verification repair",
            }
            verification_generation = {
                "provider": "local-evidence",
                "fallback": True,
                "reason": "strict citation verification repair",
            }
        else:
            citation_repair.update(
                {
                    "reason": "deterministic evidence repair also failed strict citation verification",
                    "failed_verification": failed_verification,
                    "repair_verification": repaired_verification,
                }
            )
            verified_answer["citation_repair"] = citation_repair
    verified_answer["citation_verification"] = citation_verification
    answerability = str(adequacy.get("answerability", "")).strip()
    if answerability:
        verified_answer["answerability"] = answerability
    if answerability == "needs_review":
        verified_answer["review_required"] = True
        limitations = list(verified_answer.get("limitations", []) or [])
        notice = (
            "Evidence relevance is borderline. The answer is generated from the available excerpts, "
            "but should be reviewed or followed by corrective retrieval."
        )
        if notice not in limitations:
            limitations.append(notice)
        verified_answer["limitations"] = limitations
    if facet_coverage["status"] != "not_applicable":
        answer_completeness = {
            "status": facet_coverage["status"],
            "covered_facets": list(facet_coverage["covered_facets"]),
            "missing_facets": list(facet_coverage["missing_facets"]),
            "coverage_ratio": facet_coverage["coverage_ratio"],
        }
        verified_answer["answer_completeness"] = answer_completeness
        if facet_coverage["status"] == "partial":
            limitations = list(verified_answer.get("limitations", []) or [])
            notice = (
                "The retrieved evidence covers only part of the requested dimensions; "
                "missing facets: "
                + ", ".join(str(item) for item in facet_coverage["missing_facets"])
                + "."
            )
            if notice not in limitations:
                limitations.append(notice)
            verified_answer["limitations"] = limitations
    reader_answer = build_reader_answer(verified_answer, evidence_table, query_plan=query_plan)
    verified_answer["reader_answer"] = reader_answer
    agentic_steps.append(
        {
            "step": "citation_verification",
            "passed": bool(citation_verification.get("passed", False)),
            "cited_quote_count": int(citation_verification.get("cited_quote_count", 0) or 0),
            "uncited_claim_ids": list(citation_verification.get("uncited_claim_ids", []) or []),
            "unsupported_cited_claim_ids": list(citation_verification.get("unsupported_cited_claim_ids", []) or []),
        }
    )
    agentic_trace = {
        "profile": str(agentic_controls["profile"]),
        "controls": agentic_controls,
        "slow_path_triggered": slow_path_triggered,
        "stop_reason": _agentic_stop_reason(
            adequacy=adequacy,
            slow_path_triggered=slow_path_triggered,
            max_followup_queries=max_followup_queries,
        ),
        "steps": agentic_steps,
    }
    return {
        "question": question,
        "query_plan": query_plan,
        "agentic_trace": agentic_trace,
        "retrieval_queries": retrieval_queries,
        "retrieval_query_routes": retrieval_query_routes,
        "hits": hits,
        "quotes": quote_dicts,
        "adequacy": adequacy,
        "evidence_table": evidence_table,
        "answer": verified_answer,
        "reader_answer": reader_answer,
        "verification": verified_answer.get("verification", {}),
        "citation_verification": citation_verification,
        "citation_repair": citation_repair,
        "answer_generation": answer_generation,
        "verification_generation": verification_generation,
        "query_rewrite_generation": query_rewrite_generation,
    }


def _insufficient_adequacy_answer(question: str, adequacy: dict[str, object]) -> dict[str, object]:
    reason = str(adequacy.get("followup_reason", "") or "evidence adequacy gate failed")
    quote_count = adequacy.get("quote_count", 0)
    min_quotes = adequacy.get("min_quotes", 0)
    document_count = adequacy.get("document_count", 0)
    min_documents = adequacy.get("min_documents", 0)
    return {
        "question": question,
        "answer": [],
        "limitations": [
            (
                "No answer was generated because the evidence adequacy gate failed: "
                f"{reason} (quotes {quote_count}/{min_quotes}, documents {document_count}/{min_documents})."
            )
        ],
        "insufficient_evidence": True,
        "answerability": str(adequacy.get("answerability", "not_enough_information")),
    }


def _can_generate_answer(
    adequacy: dict[str, object],
    evidence_table: list[dict[str, Any]],
) -> bool:
    """Allow a transparent review answer without weakening hard evidence gates."""

    if bool(adequacy.get("is_sufficient", False)):
        return True
    return (
        str(adequacy.get("answerability", "")).strip() == "needs_review"
        and any(str(row.get("exact_quote", "")).strip() for row in evidence_table if isinstance(row, dict))
    )


def _soften_adequacy_for_review(
    query_plan: dict[str, Any] | None,
    adequacy: dict[str, object],
) -> dict[str, object]:
    """Turn recoverable source scarcity into review instead of silent refusal.

    A synthesis or comparison based on one source is not complete, but it can
    still expose the supported evidence while clearly asking for review.  A
    conflict question remains strict because one source cannot establish two
    sides of a disagreement.
    """

    if bool(adequacy.get("is_sufficient", False)):
        return adequacy
    if int(adequacy.get("quote_count", 0) or 0) <= 0:
        return adequacy
    question_type = str((query_plan or {}).get("question_type", "")).strip().lower()
    if question_type not in {"synthesis", "comparison"}:
        return adequacy
    reason = str(adequacy.get("followup_reason", "")).strip().lower()
    if reason not in {"not enough source-document diversity", "not enough validated quotes"}:
        return adequacy
    return {
        **adequacy,
        "answerability": "needs_review",
        "retryable": True,
        "followup_reason": (
            "only part of the requested synthesis/comparison was retrieved; "
            "the available evidence is shown for review"
        ),
    }


def _requires_chinese_claims(question: str, query_plan: dict[str, Any]) -> bool:
    return str(query_plan.get("language", "")).strip().lower() == "zh" or bool(re.search(r"[\u4e00-\u9fff]", question))


def _filter_answer_to_requested_source_language(
    answer: dict[str, Any],
    evidence_table: list[dict[str, Any]],
    question: str,
) -> dict[str, Any]:
    """Enforce an explicit request for source excerpts in Chinese.

    A Chinese question alone should still be allowed to use an English paper
    and produce a Chinese paraphrase.  The stricter filter is only activated by
    wording such as ``中文原文``/``中文摘要``/``只返回中文``.  This is applied
    after model verification because provider-side JSON can still contain an
    otherwise valid English quote claim.
    """

    normalized = " ".join(str(question or "").split()).casefold()
    if not any(marker in normalized for marker in ("中文原文", "中文摘要", "中文证据", "只返回中文")):
        return answer
    rows_by_quote_id = {
        str(row.get("quote_id", "")): row
        for row in evidence_table
        if str(row.get("quote_id", ""))
    }
    chinese_quote_ids = {
        quote_id
        for quote_id, row in rows_by_quote_id.items()
        if len(re.findall(r"[\u4e00-\u9fff]", str(row.get("exact_quote", "")))) >= 2
    }
    if not chinese_quote_ids:
        return answer
    retained: list[dict[str, Any]] = []
    for claim in list(answer.get("answer", []) or []):
        item = dict(claim)
        quote_ids = [
            str(quote_id)
            for quote_id in list(item.get("quote_ids", []) or [])
            if str(quote_id) in chinese_quote_ids
        ]
        text = str(item.get("text", ""))
        if quote_ids and len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2:
            item["quote_ids"] = quote_ids
            retained.append(item)
    if len(retained) == len(list(answer.get("answer", []) or [])):
        return answer
    filtered = {**answer, "answer": retained, "source_language_filter": "zh_source_excerpt"}
    limitations = list(filtered.get("limitations", []) or [])
    notice = "已按请求仅保留有中文原文证据支持的主张。"
    if notice not in limitations:
        limitations.append(notice)
    filtered["limitations"] = limitations
    return filtered


def _verified_answer_has_chinese_claims(answer: dict[str, Any]) -> bool:
    text = " ".join(str(claim.get("text", "")) for claim in list(answer.get("answer", []) or []))
    if not text.strip():
        return True
    chinese_characters = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_characters = len(re.findall(r"[A-Za-z]", text))
    return chinese_characters >= 2 and chinese_characters >= latin_characters * 0.04


def assess_evidence_adequacy(
    quotes: list[dict[str, Any] | ExtractedQuote],
    *,
    min_quotes: int = 1,
    min_documents: int = 1,
    profile: str = "manual",
) -> dict[str, object]:
    profile = _normalized_adequacy_profile(profile)
    min_quotes = max(0, int(min_quotes))
    min_documents = max(0, int(min_documents))
    quote_count = len(quotes)
    doc_ids = {_doc_id_from_quote(quote) for quote in quotes}
    doc_ids.discard("")
    document_count = len(doc_ids)
    if quote_count <= 0:
        return {
            "is_sufficient": False,
            "quote_count": quote_count,
            "document_count": document_count,
            "profile": profile,
            "min_quotes": min_quotes,
            "min_documents": min_documents,
            "followup_reason": "no validated quotes",
        }
    if quote_count < min_quotes:
        return {
            "is_sufficient": False,
            "quote_count": quote_count,
            "document_count": document_count,
            "profile": profile,
            "min_quotes": min_quotes,
            "min_documents": min_documents,
            "followup_reason": "not enough validated quotes",
        }
    if document_count < min_documents:
        return {
            "is_sufficient": False,
            "quote_count": quote_count,
            "document_count": document_count,
            "profile": profile,
            "min_quotes": min_quotes,
            "min_documents": min_documents,
            "followup_reason": "not enough source-document diversity",
        }
    return {
        "is_sufficient": True,
        "quote_count": quote_count,
        "document_count": document_count,
        "profile": profile,
        "min_quotes": min_quotes,
        "min_documents": min_documents,
        "followup_reason": "",
    }


def assess_facet_coverage(
    query_plan: dict[str, Any] | None,
    evidence_rows: list[dict[str, Any]],
    *,
    strict: bool = False,
) -> dict[str, object]:
    """Measure whether retrieved evidence covers each explicit question facet.

    The check is deliberately lexical and transparent.  It is a completeness
    signal, not a substitute for claim-level entailment verification.  A
    partial result may still be generated, but the caller can expose the
    missing dimensions instead of presenting it as a complete review.
    """

    facets = list((query_plan or {}).get("required_facets", []) or [])
    normalized_facets: list[dict[str, object]] = []
    for raw in facets:
        if not isinstance(raw, dict):
            continue
        facet_id = " ".join(str(raw.get("id", raw.get("label", ""))).split()).strip()
        if not facet_id:
            continue
        terms = [
            " ".join(str(term).split()).strip().casefold()
            for term in list(raw.get("terms", []) or [])
            if " ".join(str(term).split()).strip()
        ]
        # Scientific Chinese sources use near-synonyms for the same requested
        # dimension (e.g. “微气候” is often written as “小气候”, and soil
        # moisture as “土壤含水量”).  Keep the plan's canonical facet label
        # stable while allowing transparent lexical coverage across those
        # source variants.
        for alias in _facet_aliases(facet_id):
            if alias.casefold() not in terms:
                terms.append(alias.casefold())
        normalized_facets.append({"id": facet_id, "terms": terms or [facet_id.casefold()]})
    if not normalized_facets:
        return {
            "status": "not_applicable",
            "required_facets": [],
            "covered_facets": [],
            "missing_facets": [],
            "coverage_ratio": 1.0,
            "facet_scores": {},
        }

    text = " ".join(
        " ".join(
            str(row.get(field, ""))
            for field in ("exact_quote", "claim_target", "context_text")
            if str(row.get(field, "")).strip()
        )
        for row in evidence_rows
        if isinstance(row, dict)
    ).casefold()
    covered: list[str] = []
    missing: list[str] = []
    facet_scores: dict[str, float] = {}
    for facet in normalized_facets:
        facet_id = str(facet["id"])
        terms = [str(term) for term in facet["terms"]]
        score = _facet_match_score(terms, text, strict=strict)
        facet_scores[facet_id] = round(score, 6)
        if score >= 0.6:
            covered.append(facet_id)
        else:
            missing.append(facet_id)
    ratio = len(covered) / len(normalized_facets)
    status = "complete" if not missing else "partial" if covered else "none"
    return {
        "status": status,
        "required_facets": [str(facet["id"]) for facet in normalized_facets],
        "covered_facets": covered,
        "missing_facets": missing,
        "coverage_ratio": round(ratio, 6),
        "facet_scores": facet_scores,
    }


def _facet_match_score(terms: list[str], evidence_text: str, *, strict: bool = False) -> float:
    """Score a facet with phrase, token, and light morphology fallbacks.

    Exact phrase matching remains the strongest signal, but scientific writing
    often changes ``storage`` to ``stocks`` or inserts modifiers such as
    ``organic``.  A two-of-three content-token match is therefore enough to
    mark a facet as covered; claim-level citation verification remains the hard
    integrity check later in the pipeline.
    """

    normalized_text = " ".join(str(evidence_text or "").split()).casefold()
    if not normalized_text:
        return 0.0
    # Tokenize the evidence package once.  The previous implementation rebuilt
    # this set for every facet term, which multiplied memory use on large local
    # libraries and could trigger a native/Python OOM during verification.
    evidence_tokens = set(lexical_tokens(normalized_text)) if not strict else set()
    best = 0.0
    for raw_term in terms:
        term = " ".join(str(raw_term or "").split()).casefold()
        if not term:
            continue
        if term in normalized_text:
            best = max(best, 1.0)
            continue
        if strict:
            continue
        term_tokens = [token for token in lexical_tokens(term) if len(token) > 2 and token not in STOPWORDS]
        if not term_tokens:
            term_tokens = [token for token in re.findall(r"[a-z0-9][a-z0-9./+-]*", term) if len(token) > 2]
        if not term_tokens:
            # CJK phrases are intentionally kept exact because character
            # n-grams would make unrelated compounds look equivalent.
            continue
        matched_tokens = 0
        for token in term_tokens:
            if token in evidence_tokens or token in normalized_text:
                matched_tokens += 1
                continue
            stem = _light_stem(token)
            if stem and any(_light_stem(candidate) == stem for candidate in evidence_tokens):
                matched_tokens += 1
        best = max(best, matched_tokens / len(term_tokens))
    return min(1.0, best)


def _facet_aliases(facet_id: str) -> tuple[str, ...]:
    return {
        "微气候": ("小气候", "局地气候"),
        "植被覆盖": ("植被盖度", "覆盖度"),
        "土壤水分": ("土壤含水量", "土壤湿度"),
    }.get(str(facet_id).strip(), ())


def _prioritize_required_facet_hits(
    hits: list[dict[str, Any]],
    query_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Keep at least one candidate for every explicit facet in the quote window.

    Retrieval scores are useful for ranking but can crowd a multi-part question
    with several near-duplicate climate excerpts.  This stable partition only
    changes the bounded quote window; claim verification still operates on the
    exact source text and IDs.
    """

    facets = [
        str(facet.get("id", facet.get("label", ""))).strip()
        for facet in list(query_plan.get("required_facets", []) or [])
        if isinstance(facet, dict)
    ]
    if not facets or not hits:
        return hits
    selected_ids: set[str] = set()
    prioritized: list[dict[str, Any]] = []
    for facet in facets:
        terms = [facet.casefold(), *[alias.casefold() for alias in _facet_aliases(facet)]]
        for hit in hits:
            evidence_id = str(hit.get("evidence_id", ""))
            text = " ".join(
                str(hit.get(field, ""))
                for field in ("text", "claim_target", "context_quote_text")
                if str(hit.get(field, "")).strip()
            ).casefold()
            if evidence_id and evidence_id not in selected_ids and any(term in text for term in terms):
                prioritized.append(hit)
                selected_ids.add(evidence_id)
                break
    prioritized.extend(
        hit
        for hit in hits
        if str(hit.get("evidence_id", "")) not in selected_ids
    )
    return prioritized


def _light_stem(token: str) -> str:
    """Apply conservative suffix normalization for English scientific terms."""

    value = str(token or "").casefold().strip()
    if len(value) <= 5:
        return value
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if value.endswith(suffix) and len(value) - len(suffix) >= 4:
            return value[: -len(suffix)]
    return value


def _apply_topical_relevance_gate(
    question: str,
    evidence_table: list[dict[str, Any]],
    adequacy: dict[str, object],
    *,
    hits: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    """Fail evidence adequacy when no quote shares a domain term with the question.

    The causal grounding check above only covers English cause/effect patterns.
    A generic Chinese question can match unrelated papers through boilerplate
    terms (\"因素\", \"影响\").  Those terms are filtered by STOPWORDS, but the
    remaining domain terms (\"光伏\", \"组件\", \"衰减\") must overlap retrieved
    quotes; otherwise the evidence is unrelated.
    """

    if not bool(adequacy.get("is_sufficient", False)):
        return adequacy
    semantic_score = _semantic_relevance_score(hits or [])
    domain_terms = [term for term in lexical_tokens(question) if term not in STOPWORDS and len(term) >= 3]
    # ``lexical_tokens`` deliberately emits CJK n-grams for retrieval.  Using
    # those raw n-grams as a ratio denominator makes short Chinese questions
    # look unrelated: query scaffolding such as ``当前选择的知识库`` contributes
    # many 3-grams while a quote may contain only ``光伏电站`` and ``植物``.
    # Strip common request scaffolding first and score the remaining CJK topic
    # terms separately so a direct domain match is not rejected by boilerplate.
    chinese_topic_terms = _chinese_topic_terms(question)
    if chinese_topic_terms:
        domain_terms = [
            term
            for term in domain_terms
            if not all("\u4e00" <= char <= "\u9fff" for char in term)
        ] + chinese_topic_terms
    # Chinese bi-grams from lexical_tokens are meaningless noise; a CJK
    # domain term needs at least three characters; Latin terms need four.
    domain_terms = [t for t in domain_terms if all('\u4e00' <= c <= '\u9fff' for c in t) or len(t) >= 4]
    # Stopwords cover individual glyphs, but char-level bigrams/trigrams
    # like "因素影" (from "因素"+"影") slip through when they share
    # characters with a stopword.  Filter CJK domain terms that contain
    # a multi-character stopword as a substring.  Latin terms are not
    # subject to this filter — short stopwords like "on", "at", "be" are
    # frequent substrings of legitimate English content words.
    domain_terms = [
        t for t in domain_terms
        if not all('\u4e00' <= c <= '\u9fff' for c in t)
        or not any(len(sw) >= 2 and sw in t for sw in STOPWORDS)
    ]
    # Review-template terms like "foundations", "architecture", "methods"
    # are boilerplate that the writing model appends to section queries
    # regardless of the actual topic.  Treat them as stopwords.
    _REVIEW_BOILERPLATE = frozenset({
        "foundations", "architecture", "mechanism", "mechanisms",
        "methods", "method", "training", "objectives", "objective",
        "pretraining", "pre-training", "results", "experiments",
        "performance", "comparison", "comparisons",
        "adaptation", "adaptations", "fine-tuning", "transfer",
        "limitations", "limitation", "weaknesses", "weakness",
        "discussion", "discussions", "conclusion", "conclusions",
        "background", "introduction", "evaluation", "evaluations",
        "benchmark", "benchmarks", "framework", "frameworks",
        "survey", "surveys", "overview", "systematic", "review",
        "analysis", "analyses", "application", "applications",
        "approach", "approaches", "methodology", "methodologies",
    })
    domain_terms = [t for t in domain_terms if t.casefold() not in _REVIEW_BOILERPLATE]
    if not domain_terms:
        return adequacy
    combined_text = " ".join(
        str(row.get("exact_quote", "") or "")
        for row in evidence_table
        if isinstance(row, dict)
    ).casefold()
    matched = [term for term in domain_terms if term in combined_text]
    if not matched:
        if semantic_score >= 0.78 and evidence_table:
            return {
                **adequacy,
                "is_sufficient": False,
                "answerability": "needs_review",
                "retryable": True,
                "followup_reason": "semantic relevance is promising but lexical topic evidence is sparse; review or corrective retrieval is recommended",
                "topical_relevance": {
                    "status": "review",
                    "score": round(semantic_score, 6),
                    "lexical_score": 0.0,
                    "semantic_score": round(semantic_score, 6),
                    "domain_terms": domain_terms[:12],
                    "matched_terms": [],
                    "checked_quotes": len(evidence_table),
                },
            }
        return {
            **adequacy,
            "is_sufficient": False,
            "answerability": "not_enough_information",
            "followup_reason": "retrieved evidence does not share topical terms with the question; the library may not contain relevant material",
            "topical_relevance": {
                "status": "insufficient",
                "score": 0.0,
                "lexical_score": 0.0,
                "semantic_score": round(semantic_score, 6),
                "domain_terms": domain_terms[:12],
                "matched_terms": [],
                "checked_quotes": len(evidence_table),
            },
        }
    # A one-word match among many domain terms can pass the gate while
    # every substantive term is absent.  Require a meaningful fraction.
    matched_chinese = [term for term in chinese_topic_terms if term in combined_text]
    # Generic research scaffolding such as “影响/研究/结果/发电” can make an
    # unrelated paper look topical. Require at least one independent,
    # non-generic scientific term (or two non-overlapping short terms) before
    # accepting a CJK match. This is the answerability gate, not a recall
    # filter: English/Latin queries retain their existing ratio logic.
    cjk_generic_terms = frozenset(
        {
            "影响", "研究", "结果", "方法", "作用", "分析", "比较", "变化", "过程",
            "性能", "系统", "环境", "生态", "发电", "问题", "因素", "方面", "项目",
            "建设", "年份", "不同", "如何", "什么", "是否", "数据", "实验", "模型",
        }
    )
    specific_chinese = [
        term
        for term in matched_chinese
        if len(term) >= 3
        and not any(generic in term for generic in cjk_generic_terms if len(generic) >= 2)
    ]
    short_specific = [
        term
        for term in matched_chinese
        if len(term) == 2
        and not any(generic in term for generic in cjk_generic_terms if len(generic) >= 2)
    ]
    independent_short = []
    for term in short_specific:
        if not any(term in other or other in term for other in independent_short):
            independent_short.append(term)
    if specific_chinese or len(independent_short) >= 2:
        lexical_score = min(1.0, len(specific_chinese or independent_short) / max(1, len(chinese_topic_terms)))
        score = max(lexical_score, semantic_score * 0.85)
        return {
            **adequacy,
            "topical_relevance": {
                "status": "strong",
                "score": round(score, 6),
                "lexical_score": round(lexical_score, 6),
                "semantic_score": round(semantic_score, 6),
                "domain_terms": domain_terms[:12],
                "matched_terms": (specific_chinese or independent_short)[:8],
                "checked_quotes": len(evidence_table),
            },
        }
    if chinese_topic_terms:
        if matched_chinese and semantic_score >= 0.58:
            lexical_score = min(1.0, len(matched_chinese) / max(1, len(chinese_topic_terms)))
            score = max(0.35, lexical_score * 0.65 + semantic_score * 0.35)
            return {
                **adequacy,
                "is_sufficient": False,
                "answerability": "needs_review",
                "retryable": True,
                "followup_reason": "evidence has a partial Chinese topic match; semantic relevance should be reviewed before treating it as complete",
                "topical_relevance": {
                    "status": "review",
                    "score": round(score, 6),
                    "lexical_score": round(lexical_score, 6),
                    "semantic_score": round(semantic_score, 6),
                    "domain_terms": domain_terms[:12],
                    "matched_terms": matched_chinese[:8],
                    "checked_quotes": len(evidence_table),
                },
            }
        return {
            **adequacy,
            "is_sufficient": False,
            "answerability": "not_enough_information",
            "followup_reason": "retrieved evidence overlaps only generic Chinese research terms; the library may not contain relevant material",
            "topical_relevance": {
                "status": "insufficient",
                "score": 0.0,
                "lexical_score": 0.0,
                "semantic_score": round(semantic_score, 6),
                "domain_terms": domain_terms[:12],
                "matched_terms": [],
                "checked_quotes": len(evidence_table),
            },
        }
    # Apply the same conservative answerability rule to English.  Generic
    # words such as “evidence”, “effect” and “power” are not enough to prove
    # that an unrelated paper answers the question. Two independent topic
    # terms are preferred; one long term is accepted when it is distinctive
    # (or is an explicitly named model/entity).
    english_generic_terms = frozenset(
        {
            "evidence", "link", "links", "linked", "study", "studies", "research", "paper", "papers",
            "result", "results", "effect", "effects", "impact", "impacts", "influence", "influences",
            "change", "changes", "changed", "cause", "causes", "caused", "relationship", "association",
            "associated", "analysis", "data", "model", "models", "system", "systems", "method", "methods",
            "approach", "approaches", "factor", "factors", "different", "construction", "year", "years",
            "power", "generation", "show", "shows", "what", "which", "how", "does", "do", "is", "are",
            "can", "could", "would", "use", "uses", "using",
        }
    )
    english_terms = [
        term.casefold()
        for term in domain_terms
        if re.fullmatch(r"[a-z0-9][a-z0-9.+/-]*", term.casefold())
        and term.casefold() not in english_generic_terms
    ]
    matched_english = [term for term in english_terms if term in combined_text]
    named_terms = {
        token.casefold()
        for token in re.findall(r"\b[A-Z][A-Za-z0-9+./-]{2,}\b", str(question or ""))
        if token.casefold() not in english_generic_terms
    }
    if len(matched_english) >= 2 or any(
        term in named_terms or len(term) >= 6
        for term in matched_english
    ):
        lexical_score = min(1.0, len(matched_english) / max(1, len(english_terms)))
        score = max(lexical_score, semantic_score * 0.85)
        return {
            **adequacy,
            "topical_relevance": {
                "status": "strong",
                "score": round(score, 6),
                "lexical_score": round(lexical_score, 6),
                "semantic_score": round(semantic_score, 6),
                "domain_terms": domain_terms[:12],
                "matched_terms": matched_english[:8],
                "checked_quotes": len(evidence_table),
            },
        }
    if matched_english or semantic_score >= 0.58:
        lexical_score = min(1.0, len(matched_english) / max(1, len(english_terms)))
        score = max(0.35, lexical_score * 0.65 + semantic_score * 0.35)
        return {
            **adequacy,
            "is_sufficient": False,
            "answerability": "needs_review",
            "retryable": True,
            "followup_reason": "evidence has a partial English topic match; semantic relevance should be reviewed before treating it as complete",
            "topical_relevance": {
                "status": "review",
                "score": round(score, 6),
                "lexical_score": round(lexical_score, 6),
                "semantic_score": round(semantic_score, 6),
                "domain_terms": domain_terms[:12],
                "matched_terms": matched_english[:8],
                "checked_quotes": len(evidence_table),
            },
        }
    return {
        **adequacy,
        "is_sufficient": False,
        "answerability": "not_enough_information",
        "followup_reason": "too few domain terms overlap retrieved evidence; the library may not contain relevant material",
        "topical_relevance": {
            "status": "insufficient",
            "score": 0.0,
            "lexical_score": 0.0,
            "semantic_score": round(semantic_score, 6),
            "domain_terms": domain_terms[:12], "matched_terms": matched_english[:8],
            "checked_quotes": len(evidence_table),
        },
    }


def _semantic_relevance_score(hits: list[dict[str, Any]]) -> float:
    """Return a bounded semantic signal without trusting raw reranker scales."""

    neural_scores: list[float] = []
    dense_scores: list[float] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        for field in ("siliconflow_score", "cross_encoder_score", "jina_score", "semantic_score"):
            value = hit.get(field)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if 0.0 <= numeric <= 1.0:
                neural_scores.append(numeric)
            else:
                neural_scores.append(1.0 / (1.0 + pow(2.718281828, -numeric)))
        value = hit.get("dense_score")
        try:
            if value is not None:
                dense_scores.append(max(0.0, min(1.0, float(value))))
        except (TypeError, ValueError):
            pass
    if neural_scores:
        return round(max(neural_scores), 6)
    if dense_scores:
        # Dense scores are useful for triage, but should not outrank a neural
        # reranker because local/hash embeddings can be poorly calibrated.
        return round(max(dense_scores) * 0.75, 6)
    return 0.0


_CJK_QUERY_SCAFFOLDING = (
    "基于当前选择的知识库",
    "基于当前选择的",
    "当前选择的知识库",
    "当前知识库",
    "找出与",
    "直接相关的",
    "相关的三条证据",
    "三条证据",
    "只输出",
    "没有证据就写未找到",
    "如果当前库中找不到可靠证据",
    "请明确写未找到证据",
)

_CJK_NON_TOPIC_TERMS = frozenset(
    {
        "基于",
        "当前",
        "选择",
        "知识",
        "知识库",
        "找出",
        "直接",
        "相关",
        "证据",
        "文献",
        "题名",
        "年份",
        "摘要",
        "原文",
        "页码",
        "没有",
        "找到",
        "明确",
        "输出",
        "一个",
        "三条",
    }
)


def _chinese_topic_terms(question: str) -> list[str]:
    """Return CJK n-grams after removing UI/request boilerplate."""

    text = " ".join(str(question or "").casefold().split())
    for phrase in _CJK_QUERY_SCAFFOLDING:
        text = text.replace(phrase, " ")
    terms: set[str] = set()
    for sequence in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", text):
        for width in (2, 3):
            if len(sequence) < width:
                continue
            for index in range(len(sequence) - width + 1):
                term = sequence[index : index + width]
                if term not in _CJK_NON_TOPIC_TERMS:
                    terms.add(term)
    return sorted(terms, key=lambda item: (-len(item), item))


def _apply_relation_grounding_gate(
    question: str,
    hits: list[dict[str, Any]],
    adequacy: dict[str, object],
) -> dict[str, object]:
    """Reject causal answers assembled from unrelated source fragments.

    Dense retrieval can independently find one document for the proposed cause
    and another for the proposed outcome.  That is useful recall, but it is not
    evidence for the relation between them.  A causal question therefore needs
    at least one retrieved document containing terms from both sides.
    """

    if not bool(adequacy.get("is_sufficient", False)):
        return adequacy
    match = re.search(
        r"\b(?:shows?\s+that\s+)?(.+?)\s+(?:caused?|causes?|led\s+to|leads?\s+to|resulted\s+in|results?\s+in)\s+(.+?)[?.!]*$",
        str(question or "").strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return adequacy
    ignored = {
        "what", "which", "evidence", "library", "study", "studies", "paper", "papers",
        "show", "shows", "showed", "that", "this", "these", "those", "from", "with",
    }
    left_terms = [term for term in lexical_tokens(match.group(1)) if len(term) >= 4 and term not in ignored]
    right_terms = [term for term in lexical_tokens(match.group(2)) if len(term) >= 4 and term not in ignored]
    if not left_terms or not right_terms:
        return adequacy
    text_by_document: dict[str, str] = {}
    for hit in hits:
        doc_id = str(hit.get("doc_id", "") or hit.get("evidence_id", ""))
        text_by_document[doc_id] = " ".join(
            [
                text_by_document.get(doc_id, ""),
                str(hit.get("title", "")),
                str(hit.get("section", "")),
                str(hit.get("text", "")),
            ]
        ).casefold()
    grounded = any(
        any(term in text for term in left_terms)
        and any(term in text for term in right_terms)
        for text in text_by_document.values()
    )
    if grounded:
        return adequacy
    return {
        **adequacy,
        "is_sufficient": False,
        "answerability": "not_enough_information",
        "followup_reason": "no single source supports both sides of the requested causal relation",
        "relation_grounding": {
            "relation": "causal",
            "left_terms": left_terms[:8],
            "right_terms": right_terms[:8],
            "supported_in_one_document": False,
        },
    }


def evidence_adequacy_thresholds(
    question_type: str,
    *,
    profile: str = "manual",
    min_quotes: int = 1,
    min_documents: int = 1,
) -> dict[str, object]:
    profile = _normalized_adequacy_profile(profile)
    quote_threshold = max(0, int(min_quotes))
    document_threshold = max(0, int(min_documents))
    if profile == "auto" and question_type.strip().lower() in {"comparison", "conflict", "synthesis"}:
        quote_threshold = max(quote_threshold, 2)
        document_threshold = max(document_threshold, 2)
    return {
        "profile": profile,
        "min_quotes": quote_threshold,
        "min_documents": document_threshold,
    }


def _normalized_adequacy_profile(profile: str) -> str:
    normalized = profile.strip().lower()
    if normalized not in {"manual", "auto"}:
        raise ValueError(f"Unsupported adequacy profile: {profile}")
    return normalized


def resolve_agentic_controls(
    query_plan: dict[str, Any],
    *,
    profile: str = "custom",
    query_variants: int = 1,
    max_followup_queries: int = 2,
    paper_recall_limit: int = 0,
) -> dict[str, object]:
    normalized = str(profile or "custom").strip().lower()
    if normalized not in {"custom", "fast", "balanced", "deep"}:
        raise ValueError(f"Unsupported agentic profile: {profile}")

    query_count = max(1, int(query_variants))
    followup_count = max(0, int(max_followup_queries))
    paper_count = max(0, int(paper_recall_limit))
    if normalized == "fast":
        query_count = 1
        followup_count = 0
        paper_count = 0
    elif normalized == "balanced":
        query_count = max(query_count, 2)
        followup_count = max(followup_count, 2)
        paper_count = max(paper_count, 50)
    elif normalized == "deep":
        query_count = max(query_count, 4)
        followup_count = max(followup_count, 4)
        paper_count = max(paper_count, 100)

    return {
        "profile": normalized,
        "question_type": str(query_plan.get("question_type", "")),
        "answer_type": str(query_plan.get("answer_type", "")),
        "query_variants": query_count,
        "max_followup_queries": followup_count,
        "paper_recall_limit": paper_count,
    }


def _agentic_trace_step(
    step: str,
    *,
    queries: list[str],
    route_labels: list[str],
    hit_count: int,
    quote_count: int,
    adequacy: dict[str, object],
    reason: str,
) -> dict[str, Any]:
    return {
        "step": step,
        "queries": [query for query in queries if query],
        "route_labels": [label for label in route_labels if label],
        "hit_count": int(hit_count),
        "quote_count": int(quote_count),
        "evidence_sufficient": bool(adequacy.get("is_sufficient", False)),
        "answerability": str(adequacy.get("answerability", "")),
        "retryable": bool(adequacy.get("retryable", False)),
        "followup_reason": str(adequacy.get("followup_reason", "")),
        "reason": reason,
    }


def _agentic_stop_reason(
    *,
    adequacy: dict[str, object],
    slow_path_triggered: bool,
    max_followup_queries: int,
) -> str:
    if bool(adequacy.get("is_sufficient", False)):
        return "evidence_sufficient_after_followup" if slow_path_triggered else "evidence_sufficient_initially"
    if str(adequacy.get("answerability", "")).strip() == "needs_review":
        return "needs_review_after_retrieval"
    if max_followup_queries <= 0:
        return "followup_disabled"
    return "followup_budget_exhausted"


def _doc_id_from_quote(quote: dict[str, Any] | ExtractedQuote) -> str:
    if isinstance(quote, ExtractedQuote):
        evidence_ids = quote.evidence_ids
    else:
        evidence_ids = list(quote.get("evidence_ids", []) or [])
    if not evidence_ids:
        return ""
    return str(evidence_ids[0]).split(".s", 1)[0]


def _uses_llm(provider: str) -> bool:
    return provider.strip().lower() in {"llm", "chat", "openai-compatible"}


def _extract_quotes_for_provider(
    question: str,
    hits: list[dict[str, Any]],
    *,
    max_quotes: int,
    quote_provider: str,
    chat_client: ChatJsonClient | None,
) -> list[ExtractedQuote]:
    if not hits:
        return []
    if _uses_llm(quote_provider):
        if chat_client is None:
            raise ValueError("chat_client is required when quote_provider is llm")
        return extract_quotes_with_llm(question, hits, chat_client=chat_client)[: max(0, int(max_quotes))]
    return extract_quotes(question, hits, max_quotes=max_quotes)


def _initial_retrieval_routes(
    question: str,
    query_plan: dict[str, Any],
    *,
    query_variants: int,
    model_queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, int(query_variants))
    routes = query_routes(query_plan, max_routes=limit)
    if not routes:
        routes = [
            {
                "label": "original",
                "query": question,
                "weight": 1.0,
                "retrieval": ["bm25", "dense"],
                "purpose": "question_as_written",
                "section_hints": [],
            }
        ]
    seen = {" ".join(str(route.get("query", "")).split()).casefold() for route in routes}
    for index, raw_query in enumerate(model_queries or [], start=1):
        query = " ".join(str(raw_query).split())
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        routes.append(
            {
                "label": f"model_rewrite_{index}",
                "query": query,
                "weight": 0.92,
                "retrieval": ["bm25", "dense"],
                "purpose": "cross_language_model_query_rewrite",
                "section_hints": list(query_plan.get("section_hints", []) or []),
            }
        )
    return routes


def _model_retrieval_queries(question: str, *, chat_client: ChatJsonClient) -> list[str]:
    needs_english = bool(re.search(r"[\u4e00-\u9fff]", question))
    messages = [
        {
            "role": "system",
            "content": (
                "Create search queries for a local scientific-document index. Documents may use a different language "
                "from the question. Preserve model names, variables, dates and requested contrasts. "
                + (
                    "All query strings MUST be written in English; translate every Chinese scientific phrase into English. "
                    if needs_english
                    else "Keep the query language suitable for the source terminology. "
                )
                + "Do not answer."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"question": question, "target_query_language": "English" if needs_english else "source terminology"},
                ensure_ascii=False,
            ),
        },
    ]
    payload = chat_client.complete_json(messages, schema_name="retrieval_queries") or {}
    raw_queries = payload.get("queries", []) if isinstance(payload, dict) else []
    if not isinstance(raw_queries, list):
        raise ValueError("retrieval query rewrite returned a non-list")
    unique: list[str] = []
    seen: set[str] = set()
    for raw in raw_queries:
        query = " ".join(str(raw).split())
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        unique.append(query[:500])
        if len(unique) >= 4:
            break
    if not unique:
        raise ValueError("retrieval query rewrite returned no usable queries")
    return unique


def _followup_preserves_query_identity(followup: str, model_queries: list[str]) -> bool:
    """Reject generic slow-path queries that drop every cross-language entity."""

    generic = {
        "analysis",
        "data",
        "document",
        "evidence",
        "finding",
        "findings",
        "model",
        "paper",
        "research",
        "result",
        "results",
        "study",
    }
    counts: dict[str, int] = {}
    for query in model_queries:
        seen = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", str(query))
            if token.casefold() not in generic and (len(token) >= 3 or any(character.isdigit() for character in token))
        }
        for token in seen:
            counts[token] = counts.get(token, 0) + 1
    identity_terms = {token for token, count in counts.items() if count >= 2 or any(character.isdigit() for character in token)}
    if not identity_terms:
        return True
    followup_terms = {token.casefold() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", str(followup))}
    return bool(identity_terms & followup_terms)


def _initial_retrieval_queries(
    question: str,
    query_plan: dict[str, Any],
    *,
    query_variants: int,
) -> list[str]:
    return [str(route.get("query", "")) for route in _initial_retrieval_routes(question, query_plan, query_variants=query_variants)]


def _search_rewrite_routes(
    db_path: str | Path,
    question: str,
    routes: list[dict[str, Any]],
    *,
    limit: int,
    per_document_limit: int,
    filters: dict[str, Any],
    embedding_provider: Any | None,
    reranker: Any | None,
    context_mode: str,
    paper_recall_limit: int,
) -> list[dict[str, Any]]:
    normalized_routes = _normalized_routes(routes)
    if not normalized_routes:
        return []
    if len(normalized_routes) == 1:
        route = normalized_routes[0]
        return _search_evidence(
            db_path,
            str(route["query"]),
            limit=limit,
            per_document_limit=per_document_limit,
            filters=filters,
            embedding_provider=embedding_provider,
            reranker=reranker,
            context_mode=context_mode,
            paper_recall_limit=paper_recall_limit,
        )

    route_results: list[dict[str, Any]] = []
    candidate_limit = _fusion_candidate_limit(limit)
    for index, route in enumerate(normalized_routes, start=1):
        route_hits = _search_evidence(
            db_path,
            str(route["query"]),
            limit=candidate_limit,
            per_document_limit=0,
            filters=filters,
            embedding_provider=embedding_provider,
            reranker=None,
            context_mode=context_mode,
            paper_recall_limit=paper_recall_limit,
        )
        route_results.append(
            {
                "label": str(route.get("label") or f"query-{index}"),
                "query": str(route["query"]),
                "weight": float(route.get("weight", 1.0) or 1.0),
                "hits": route_hits,
            }
        )

    fused = fuse_ranked_hits(route_results, limit=_fusion_pool_limit(limit))
    ranked = reranker.rerank(question, fused) if reranker is not None and fused else fused
    capped = _apply_agent_per_document_limit(ranked, per_document_limit=per_document_limit)
    return capped[: max(0, int(limit))]


def _normalized_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, route in enumerate(routes, start=1):
        query = " ".join(str(route.get("query", "")).split())
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        copied = dict(route)
        copied["query"] = query
        copied.setdefault("label", f"query-{index}")
        copied.setdefault("weight", 1.0)
        result.append(copied)
    return result


def _fusion_candidate_limit(limit: int) -> int:
    resolved_limit = max(1, int(limit))
    return max(resolved_limit * 4, min(50, resolved_limit + 20))


def _fusion_pool_limit(limit: int) -> int:
    resolved_limit = max(1, int(limit))
    return max(resolved_limit * 8, min(100, resolved_limit + 40))


def _apply_agent_per_document_limit(
    hits: list[dict[str, Any]],
    *,
    per_document_limit: int,
) -> list[dict[str, Any]]:
    if per_document_limit <= 0:
        return hits
    counts: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for hit in hits:
        doc_id = str(hit.get("doc_id", ""))
        count = counts.get(doc_id, 0)
        if count >= per_document_limit:
            continue
        counts[doc_id] = count + 1
        capped.append(hit)
    return capped


def _prioritize_evidence_sections(
    hits: list[dict[str, Any]],
    query_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Prefer sections that normally contain findings without hard filtering.

    PDF extraction still labels many useful spans as ``other``.  Therefore the
    policy is a stable ranking preference rather than a filter: known abstract,
    results, discussion and conclusion rows win when available, while ``other``
    remains a fallback for libraries with incomplete section metadata.
    """

    if len(hits) < 2:
        return list(hits)
    plan = query_plan or {}
    hints = {
        str(value).strip().casefold()
        for value in list(plan.get("section_hints", []) or [])
        if str(value).strip()
    }
    question_type = str(plan.get("question_type", "")).strip().casefold()
    if not hints and question_type in {"synthesis", "comparison", "conflict", "mechanism", "evidence"}:
        hints = {"abstract", "results", "discussion", "conclusion"}
    if not hints:
        return list(hits)

    indexed = list(enumerate(hits))

    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        index, hit = item
        section_kind = str(hit.get("section_kind", "") or hit.get("section", "")).strip().casefold()
        preferred = 0 if section_kind in hints else 1
        score = float(hit.get("score", 0.0) or 0.0)
        return preferred, -score, index

    return [hit for _, hit in sorted(indexed, key=key)]


def _expand_hits_with_neighbor_context(
    db_path: str | Path,
    hits: list[dict[str, Any]],
    *,
    query_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    if not hits or not _should_expand_neighbor_context(query_plan):
        return hits
    db = Path(db_path)
    if not db.exists():
        return hits
    block_ids = _hit_block_ids(hits)
    if not block_ids:
        return hits
    block_rows = _load_context_block_rows(db, block_ids)
    if not block_rows:
        return hits
    expanded_hits: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    for hit in hits:
        block_id = str(hit.get("block_id", "")).strip()
        rows = block_rows.get(block_id, [])
        if len(rows) <= 1:
            evidence_id = str(hit.get("evidence_id", ""))
            if evidence_id not in seen_evidence_ids:
                expanded_hits.append(hit)
                seen_evidence_ids.add(evidence_id)
            continue
        parent_text = " ".join(str(row.get("text", "")).strip() for row in rows if str(row.get("text", "")).strip())
        if not parent_text:
            expanded_hits.append(hit)
            continue
        parent_evidence_ids = [str(row.get("evidence_id", "")) for row in rows if str(row.get("evidence_id", ""))]
        seed_position = int(hit.get("sentence_index", 0) or 0)
        for row in rows:
            evidence_id = str(row.get("evidence_id", ""))
            if not evidence_id or evidence_id in seen_evidence_ids:
                continue
            distance = abs(int(row.get("sentence_index", 0) or 0) - seed_position)
            # A block can be an entire PDF page or a reference list. Keeping
            # every row from it turns one topical hit into unrelated sources.
            # Two neighboring sentences preserve local context without
            # treating the rest of the page as independently retrieved.
            if distance > 2:
                continue
            expanded = dict(hit)
            expanded.update(row)
            expanded["parent_text"] = parent_text
            expanded["parent_block_id"] = block_id
            expanded["parent_evidence_ids"] = parent_evidence_ids
            if distance:
                expanded["score"] = max(0.0, float(hit.get("score", 0.0)) - 0.05 * distance)
                routes = {str(route) for route in expanded.get("routes", []) or []}
                routes.add("neighbor-context")
                expanded["routes"] = sorted(routes)
                # Contextual list continuations can legitimately omit the
                # noun phrase from the lead sentence.  Keep them eligible for
                # exact sentence quote extraction without pretending the
                # neighboring text itself matched every inherited term.
                expanded["matched_terms"] = list(expanded.get("matched_terms", []) or ["neighbor-context"])
            seen_evidence_ids.add(evidence_id)
            expanded_hits.append(expanded)
    expanded_hits.sort(key=lambda item: (-float(item.get("score", 0.0)), str(item.get("evidence_id", ""))))
    return expanded_hits


def _should_expand_neighbor_context(query_plan: dict[str, Any]) -> bool:
    answer_type = str(query_plan.get("answer_type", "")).strip().lower()
    question_type = str(query_plan.get("question_type", "")).strip().lower()
    expected_count = query_plan.get("expected_answer_count")
    if answer_type in {"named_list", "comparison", "conflict", "synthesis"}:
        return True
    if question_type in {"comparison", "conflict", "synthesis"}:
        return True
    try:
        return int(expected_count) > 1
    except (TypeError, ValueError):
        return False


def _hit_block_ids(hits: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    block_ids: list[str] = []
    for hit in hits:
        block_id = str(hit.get("block_id", "")).strip()
        if not block_id or block_id in seen:
            continue
        seen.add(block_id)
        block_ids.append(block_id)
    return block_ids


def _load_context_block_rows(db_path: Path, block_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not block_ids:
        return {}
    placeholders = ",".join("?" for _ in block_ids)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            select *
            from evidence_spans
            where block_id in ({placeholders})
            order by block_id, sentence_index, char_start, evidence_id
            """,
            block_ids,
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["block_id"]), []).append(dict(row))
    return grouped


def _search_evidence(
    db_path: str | Path,
    query: str,
    *,
    limit: int,
    per_document_limit: int,
    filters: dict[str, Any],
    embedding_provider: Any | None,
    reranker: Any | None,
    context_mode: str,
    paper_recall_limit: int,
) -> list[dict[str, Any]]:
    # References and bibliography tails often contain every query term, so a
    # broad research-status query can otherwise spend its small result budget
    # on non-evidence before quote extraction gets a chance to reject them.
    # Fetch a modestly larger candidate window, reject those rows first, then
    # apply source diversity and the requested limit.
    resolved_limit = max(1, int(limit))
    # Multi-route retrieval already expands each route to a sufficiently large
    # fusion pool. Do not multiply it again here: doing so makes a broad local
    # review re-rank thousands of extra spans for no recall benefit.
    raw_limit = max(resolved_limit, 30)
    kwargs: dict[str, Any] = {
        "limit": raw_limit,
        # Source limits apply after bibliography filtering, otherwise a
        # document with several reference rows can crowd out its findings.
        "per_document_limit": 0,
        "filters": filters,
        "embedding_provider": embedding_provider,
        "reranker": reranker,
        "context_mode": context_mode,
    }
    if paper_recall_limit > 0:
        kwargs["paper_recall_limit"] = paper_recall_limit
    candidates = search_evidence_store(db_path, query, **kwargs)
    substantive = [hit for hit in candidates if is_substantive_evidence_hit(hit)]
    capped = _apply_agent_per_document_limit(substantive, per_document_limit=per_document_limit)
    return capped[:resolved_limit]


def verify_citations(
    answer: dict[str, Any],
    evidence_table: list[dict[str, Any]],
    *,
    required_facets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows_by_quote_id: dict[str, list[dict[str, Any]]] = {}
    for row in evidence_table:
        quote_id = str(row.get("quote_id", ""))
        if quote_id:
            rows_by_quote_id.setdefault(quote_id, []).append(row)

    claims = list(answer.get("answer", []) or [])
    cited_quote_ids: list[str] = []
    missing_quote_ids: list[str] = []
    uncited_claim_ids: list[str] = []
    unsupported_cited_claim_ids: list[str] = []
    for claim in claims:
        claim_id = str(claim.get("claim_id", ""))
        quote_ids = [str(item) for item in claim.get("quote_ids", []) or []]
        if not quote_ids:
            uncited_claim_ids.append(claim_id)
        support_status = str(claim.get("support_status", ""))
        if quote_ids and support_status not in {"supported", "partially_supported"}:
            unsupported_cited_claim_ids.append(claim_id)
        for quote_id in quote_ids:
            if quote_id not in cited_quote_ids:
                cited_quote_ids.append(quote_id)
            if quote_id not in rows_by_quote_id and quote_id not in missing_quote_ids:
                missing_quote_ids.append(quote_id)

    cited_rows = [row for quote_id in cited_quote_ids for row in rows_by_quote_id.get(quote_id, [])]
    missing_source_anchors = [
        str(row.get("evidence_id", ""))
        for row in cited_rows
        if not str(row.get("html_path", "")).strip() or not str(row.get("html_anchor", "")).strip()
    ]
    missing_exact_quotes = [
        str(row.get("evidence_id", ""))
        for row in cited_rows
        if not str(row.get("exact_quote", "")).strip()
    ]
    supported_claims = [
        str(claim.get("claim_id", ""))
        for claim in claims
        if str(claim.get("support_status", "")) in {"supported", "partially_supported"}
    ]
    claim_audit: list[dict[str, Any]] = []
    for claim in claims:
        claim_id = str(claim.get("claim_id", ""))
        quote_ids = [str(item) for item in claim.get("quote_ids", []) or [] if str(item)]
        rows = [row for quote_id in quote_ids for row in rows_by_quote_id.get(quote_id, [])]
        support_status = str(claim.get("support_status", "not_enough_information"))
        exact_anchor_ids = [
            str(row.get("evidence_id", ""))
            for row in rows
            if str(row.get("evidence_id", ""))
            and str(row.get("html_path", "")).strip()
            and str(row.get("html_anchor", "")).strip()
        ]
        audit_status = (
            "supported"
            if support_status in {"supported", "partially_supported"}
            and len(exact_anchor_ids) == len(rows)
            and len(rows) == len(quote_ids)
            else "needs_review"
        )
        claim_audit.append(
            {
                "claim_id": claim_id,
                "support_status": support_status,
                "audit_status": audit_status,
                "quote_ids": quote_ids,
                "evidence_ids": [str(row.get("evidence_id", "")) for row in rows if str(row.get("evidence_id", ""))],
                "exact_anchor_evidence_ids": exact_anchor_ids,
            }
        )
    citation_completeness = True
    facet_coverage: dict[str, object] | None = None
    if required_facets:
        facet_coverage = assess_facet_coverage(
            {"required_facets": required_facets},
            cited_rows,
            strict=True,
        )
        citation_completeness = facet_coverage["status"] == "complete"
    passed = (
        bool(claims)
        and not uncited_claim_ids
        and not unsupported_cited_claim_ids
        and not missing_quote_ids
        and not missing_source_anchors
        and not missing_exact_quotes
        and citation_completeness
    )
    result = {
        "passed": passed,
        "claim_count": len(claims),
        "supported_claim_count": len(supported_claims),
        "cited_quote_count": len(cited_quote_ids),
        "cited_evidence_rows": len(cited_rows),
        "available_quote_count": len(rows_by_quote_id),
        "uncited_claim_ids": uncited_claim_ids,
        "unsupported_cited_claim_ids": unsupported_cited_claim_ids,
        "missing_quote_ids": missing_quote_ids,
        "missing_source_anchor_evidence_ids": missing_source_anchors,
        "missing_exact_quote_evidence_ids": missing_exact_quotes,
        "claim_evidence_audit": claim_audit,
        "audited_claim_count": len(claim_audit),
        "supported_anchor_claim_count": sum(1 for item in claim_audit if item["audit_status"] == "supported"),
    }
    if facet_coverage is not None:
        result.update(
            {
                "citation_completeness": citation_completeness,
                "required_facets": facet_coverage["required_facets"],
                "covered_facets": facet_coverage["covered_facets"],
                "missing_facets": facet_coverage["missing_facets"],
                "facet_coverage_ratio": facet_coverage["coverage_ratio"],
            }
        )
    return result


def build_reader_answer(
    answer: dict[str, Any],
    evidence_table: list[dict[str, Any]],
    *,
    query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows_by_quote_id = {
        str(row.get("quote_id", "")): dict(row)
        for row in evidence_table
        if str(row.get("quote_id", ""))
    }
    citation_id_by_quote_id: dict[str, str] = {}
    citations: list[dict[str, Any]] = []
    sentences: list[dict[str, Any]] = []
    question = str(answer.get("question", ""))
    raw_claims = list(answer.get("answer", []) or [])
    if str((query_plan or {}).get("answer_type", "")).strip().lower() == "named_list":
        # The synthesizer/model has already ordered list answers by semantic
        # usefulness.  Lexical re-ranking tends to promote a sentence that
        # merely repeats the question over the sentence that contains the
        # actual named items.
        claims = raw_claims
    else:
        claims = _reader_ranked_claims(question, raw_claims, rows_by_quote_id)
    claims = _reader_limited_claims_for_question_type(question, claims, rows_by_quote_id, query_plan=query_plan)

    for claim in claims:
        status = str(claim.get("support_status", ""))
        if status not in {"supported", "partially_supported"}:
            continue
        quote_ids = [str(quote_id) for quote_id in claim.get("quote_ids", []) or [] if str(quote_id) in rows_by_quote_id]
        if not quote_ids:
            continue
        citation_ids: list[str] = []
        for quote_id in quote_ids:
            citation_id = citation_id_by_quote_id.get(quote_id)
            if citation_id is None:
                citation_id = str(len(citation_id_by_quote_id) + 1)
                citation_id_by_quote_id[quote_id] = citation_id
                citations.append(_reader_citation(citation_id, quote_id, rows_by_quote_id[quote_id]))
            citation_ids.append(citation_id)
        sentence_text = _reader_sentence_text(claim, quote_ids, rows_by_quote_id)
        if not sentence_text:
            continue
        sentences.append(
            {
                "claim_id": str(claim.get("claim_id", "")),
                "text": sentence_text,
                "quote_ids": quote_ids,
                "citation_ids": citation_ids,
                "support_status": status,
                "verification_score": claim.get("verification_score", 0.0),
                "rendered_text": f"{sentence_text} {_citation_markers(citation_ids)}".strip(),
            }
        )

    text = " ".join(str(sentence.get("rendered_text", "")).strip() for sentence in sentences if sentence.get("rendered_text"))
    is_synthesis = str((query_plan or {}).get("question_type", "")).strip().lower() == "synthesis"
    document_ids = {
        str(rows_by_quote_id.get(quote_id, {}).get("doc_id", "")).strip()
        for sentence in sentences
        for quote_id in list(sentence.get("quote_ids", []) or [])
    }
    document_ids.discard("")
    scope_note = ""
    if is_synthesis:
        document_count = len(document_ids) or len(citations)
        if re.search(r"[\u4e00-\u9fff]", question):
            scope_note = f"以下归纳仅基于本次检索到的 {document_count} 篇资料，反映所选知识库中的相关证据，并不替代对整个领域的完整综述。"
        else:
            scope_note = f"This synthesis is limited to the {document_count} source(s) retrieved for this answer; it is not a complete review of the whole field."
    if bool(answer.get("language_fallback", False)):
        fallback_note = "生成模型未完成中文改写，以下保留带原文脚标的证据摘录。"
        scope_note = f"{scope_note} {fallback_note}".strip()
    return {
        "style": "notebooklm_like_inline_citations",
        "presentation": "synthesis" if is_synthesis else "answer",
        "scope_note": scope_note,
        "text": text,
        "sentences": sentences,
        "citations": citations,
        "citation_count": len(citations),
    }


def _reader_ranked_claims(
    question: str,
    claims: list[dict[str, Any]],
    rows_by_quote_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, claim in enumerate(claims):
        score_text = str(claim.get("text", ""))
        quote_text = " ".join(
            str(rows_by_quote_id.get(str(quote_id), {}).get("exact_quote", ""))
            for quote_id in claim.get("quote_ids", []) or []
        )
        score = max(
            _reader_claim_relevance(question, score_text),
            _reader_claim_relevance(question, quote_text),
        )
        scored.append((score, index, claim))
    if len(scored) <= 1:
        return [claim for _score, _index, claim in scored]
    best_score = max((score for score, _index, _claim in scored), default=0.0)
    cutoff = max(0.2, best_score - 0.2)
    filtered = [item for item in scored if item[0] >= cutoff]
    if not filtered:
        filtered = scored
    filtered.sort(key=lambda item: (-item[0], item[1]))
    return [claim for _score, _index, claim in filtered]


def _reader_limited_claims_for_question_type(
    question: str,
    claims: list[dict[str, Any]],
    rows_by_quote_id: dict[str, dict[str, Any]],
    *,
    query_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not claims or not query_plan:
        return claims
    answer_type = str(query_plan.get("answer_type", "")).strip().lower()
    expected_count = query_plan.get("expected_answer_count")
    if answer_type != "named_list" or expected_count in {None, "", 0}:
        return claims
    if _reader_claim_best_relevance(question, claims[0], rows_by_quote_id) >= 0.8:
        return claims[:1]
    return claims


def _reader_claim_best_relevance(
    question: str,
    claim: dict[str, Any],
    rows_by_quote_id: dict[str, dict[str, Any]],
) -> float:
    score_text = str(claim.get("text", ""))
    quote_text = " ".join(
        str(rows_by_quote_id.get(str(quote_id), {}).get("exact_quote", ""))
        for quote_id in claim.get("quote_ids", []) or []
    )
    return max(
        _reader_claim_relevance(question, score_text),
        _reader_claim_relevance(question, quote_text),
    )


def _reader_claim_relevance(question: str, claim_text: str) -> float:
    terms = _reader_content_terms(question)
    if not terms:
        return 1.0
    claim_terms = set(_reader_content_terms(claim_text))
    if not claim_terms:
        return 0.0
    matched = [term for term in terms if term in claim_terms]
    return len(matched) / len(terms)


def _reader_sentence_text(
    claim: dict[str, Any],
    quote_ids: list[str],
    rows_by_quote_id: dict[str, dict[str, Any]],
) -> str:
    claim_text = str(claim.get("text", "")).strip()
    if claim_text and not claim_text.endswith("..."):
        return _sentence_with_terminal_punctuation(claim_text)
    quote_text = " ".join(
        str(rows_by_quote_id.get(quote_id, {}).get("exact_quote", "")).strip()
        for quote_id in quote_ids
        if str(rows_by_quote_id.get(quote_id, {}).get("exact_quote", "")).strip()
    )
    if quote_text:
        return _sentence_with_terminal_punctuation(_compact_reader_text(quote_text))
    return _sentence_with_terminal_punctuation(claim_text)


def _compact_reader_text(text: str, *, max_chars: int = 1000) -> str:
    value = " ".join(text.split())
    if len(value) <= max_chars:
        return value
    window = value[:max_chars].rstrip()
    sentence_end = max(window.rfind(". "), window.rfind("。"), window.rfind("! "), window.rfind("? "))
    if sentence_end >= int(max_chars * 0.55):
        return window[: sentence_end + 1].strip()
    truncated = window.rsplit(" ", 1)[0].rstrip()
    return truncated + "..."


def _reader_content_terms(text: str) -> list[str]:
    return [
        term
        for term in lexical_tokens(text)
        if len(term) > 2 and term not in _READER_STOPWORDS
    ]


def _reader_citation(citation_id: str, quote_id: str, row: dict[str, Any]) -> dict[str, Any]:
    html_path = str(row.get("html_path", "")).strip()
    html_anchor = str(row.get("html_anchor", "")).strip()
    source_href = f"{html_path}#{html_anchor}" if html_path and html_anchor else ""
    return {
        "citation_id": citation_id,
        "quote_id": quote_id,
        "evidence_id": str(row.get("evidence_id", "")),
        "doc_id": str(row.get("doc_id", "")),
        "paper": str(row.get("paper", "")),
        "doi": str(row.get("doi", "")),
        "section": str(row.get("section", "")),
        "exact_quote": str(row.get("exact_quote", "")),
        "source_href": source_href,
        "html_path": html_path,
        "html_anchor": html_anchor,
        "support_status": str(row.get("support_status", "")),
    }


def _citation_markers(citation_ids: list[str]) -> str:
    return "".join(f"[{citation_id}]" for citation_id in citation_ids)


def _sentence_with_terminal_punctuation(text: str) -> str:
    value = " ".join(text.split())
    if not value:
        return ""
    if value[-1] in ".!?。！？":
        return value
    return value + "."


_READER_STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "paper",
    "proposed",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def _unique_queries(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(str(value).split())
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(normalized)
    return result


def _merge_hits(
    existing_hits: list[dict[str, Any]],
    new_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    best_by_id: dict[str, dict[str, Any]] = {}
    for hit in [*existing_hits, *new_hits]:
        evidence_id = str(hit.get("evidence_id", ""))
        if not evidence_id:
            continue
        current = best_by_id.get(evidence_id)
        if current is None or float(hit.get("score", 0.0) or 0.0) > float(current.get("score", 0.0) or 0.0):
            best_by_id[evidence_id] = dict(hit)
    return sorted(best_by_id.values(), key=lambda hit: (-float(hit.get("score", 0.0) or 0.0), str(hit.get("evidence_id", ""))))
