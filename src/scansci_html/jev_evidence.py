"""Optional JEV judgment stage for bounded local evidence candidates.

The local retriever remains the recall authority.  JEV only supplies typed
signals for a small candidate set; this module never creates evidence IDs,
citations, or permissions and never returns the upstream response body.
"""

from __future__ import annotations

from typing import Any

from .app_settings import get_provider_api_key
from .jev_client import DEFAULT_ENDPOINT, DEFAULT_MODEL, JEVError, ask_jev


_MAX_CANDIDATES = 12
_MAX_QUESTION_CHARS = 1200
_MAX_PAPER_CHARS = 240
_MAX_SECTION_CHARS = 160
_MAX_EVIDENCE_ID_CHARS = 240
_MAX_PASSAGE_CHARS = 1400
_MAX_CONTEXT_CHARS = 320
_JEV_REVIEW_TIMEOUT_SECONDS = 8.0
_JEV_REVIEW_RETRIES = 1


def _compact_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _first_api_key(value: object) -> str:
    return next(
        (item.strip() for item in str(value or "").split(",") if item.strip()),
        "",
    )


def _probability(answer: object) -> float | None:
    if isinstance(answer, bool):
        return 1.0 if answer else 0.0
    if isinstance(answer, (int, float)):
        value = float(answer)
    elif isinstance(answer, dict):
        value = None
        for key in ("noul", "probability", "confidence", "score"):
            candidate = answer.get(key)
            if isinstance(candidate, bool):
                value = 1.0 if candidate else 0.0
                break
            if isinstance(candidate, (int, float)):
                value = float(candidate)
                break
        if value is None:
            return None
    else:
        return None
    if value != value:  # NaN
        return None
    return max(0.0, min(1.0, value))


def _base_review(*, candidate_count: int, reason: str) -> dict[str, Any]:
    return {
        "status": "not_run",
        "provider": "jev",
        "semantic_provider": "jev",
        "scope": "question_plus_bounded_evidence_candidates",
        "candidate_count": int(candidate_count),
        "reviewed_count": 0,
        "reranked": False,
        "coverage_probability": None,
        "fallback_used": True,
        "reason": reason,
    }


def _review_request(question: str, hits: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    questions: dict[str, Any] = {}
    for source_index, hit in enumerate(hits[:_MAX_CANDIDATES]):
        evidence_id = _compact_text(hit.get("evidence_id"), _MAX_EVIDENCE_ID_CHARS)
        passage = _compact_text(hit.get("matched_text") or hit.get("text"), _MAX_PASSAGE_CHARS)
        if not evidence_id or not passage:
            continue
        candidate_index = len(candidates)
        candidate_id = f"candidate_{candidate_index}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "evidence_id": evidence_id,
                "paper": _compact_text(hit.get("paper") or hit.get("title"), _MAX_PAPER_CHARS),
                "section": _compact_text(hit.get("section"), _MAX_SECTION_CHARS),
                "matched_text": passage,
                "context_before": _compact_text(hit.get("context_before"), _MAX_CONTEXT_CHARS),
                "context_after": _compact_text(hit.get("context_after"), _MAX_CONTEXT_CHARS),
            }
        )
        question_key = f"supports_{candidate_index}"
        questions[question_key] = {
            "type": "noul",
            "instructions": (
                f"Does `candidates[{candidate_index}].matched_text` directly support answering `question`? "
                "Use only the supplied passage. Return true only when it contains a specific fact, result, "
                "method, or comparison that materially answers the question."
            ),
        }
        rows.append(
            {
                "source_index": source_index,
                "question_key": question_key,
                "evidence_id": evidence_id,
            }
        )
    questions["coverage_sufficient"] = {
        "type": "noul",
        "instructions": (
            "Do the supplied candidate passages collectively provide enough direct evidence to answer `question` "
            "without adding unsupported claims? Use only the candidates and return false when an important part "
            "of the question remains unsupported."
        ),
    }
    state = {
        "question": _compact_text(question, _MAX_QUESTION_CHARS),
        "candidates": candidates,
    }
    return state, questions, rows


def review_evidence_candidates(
    *,
    workspace: str,
    provider: dict[str, Any] | None,
    question: str,
    hits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return locally owned hits with an optional JEV relevance ordering.

    JEV is opt-in through the enabled JEV provider.  Any unavailable or
    malformed response leaves the local order untouched and reports a
    bounded ``degraded`` result for the caller's audit trail.
    """

    source_hits = [dict(hit) for hit in hits if isinstance(hit, dict)]
    state, questions, rows = _review_request(question, source_hits)
    candidate_count = len(rows)
    if not candidate_count:
        return {"hits": source_hits, "review": _base_review(candidate_count=0, reason="no_candidates")}
    if not provider or not bool(provider.get("enabled")):
        return {
            "hits": source_hits,
            "review": _base_review(candidate_count=candidate_count, reason="provider_disabled"),
        }

    api_key = _first_api_key(get_provider_api_key(workspace, str(provider.get("id", "jev"))))
    if not api_key:
        return {
            "hits": source_hits,
            "review": _base_review(candidate_count=candidate_count, reason="missing_api_key"),
        }

    try:
        payload = ask_jev(
            api_key=api_key,
            endpoint=str(provider.get("base_url") or DEFAULT_ENDPOINT).strip(),
            model=DEFAULT_MODEL,
            state=state,
            questions=questions,
            timeout=_JEV_REVIEW_TIMEOUT_SECONDS,
            retries=_JEV_REVIEW_RETRIES,
        )
    except JEVError as exc:
        return {
            "hits": source_hits,
            "review": {
                **_base_review(candidate_count=candidate_count, reason="jev_unavailable"),
                "status": "degraded",
                "http_status": exc.status_code,
                "retryable": bool(exc.retryable),
            },
        }

    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        return {
            "hits": source_hits,
            "review": {
                **_base_review(candidate_count=candidate_count, reason="invalid_answers"),
                "status": "degraded",
            },
        }

    scores: dict[int, float] = {}
    signals: list[dict[str, Any]] = []
    for row in rows:
        value = _probability(answers.get(row["question_key"]))
        if value is None:
            continue
        source_index = int(row["source_index"])
        scores[source_index] = value
        signals.append({"evidence_id": row["evidence_id"], "support_probability": round(value, 4)})
    if not scores:
        return {
            "hits": source_hits,
            "review": {
                **_base_review(candidate_count=candidate_count, reason="missing_candidate_scores"),
                "status": "degraded",
            },
        }

    original_order = list(range(len(source_hits)))
    reviewed_order = sorted(
        [int(row["source_index"]) for row in rows],
        key=lambda index: (
            index in scores,
            scores.get(index, -1.0),
            float(source_hits[index].get("score", 0.0) or 0.0),
            -index,
        ),
        reverse=True,
    )
    reviewed_indices = {int(row["source_index"]) for row in rows}
    ordered_indices = reviewed_order + [index for index in original_order if index not in reviewed_indices]
    ordered_hits: list[dict[str, Any]] = []
    for index in ordered_indices:
        hit = dict(source_hits[index])
        if index in scores:
            hit["jev_support_probability"] = round(scores[index], 4)
        ordered_hits.append(hit)

    coverage_probability = _probability(answers.get("coverage_sufficient"))
    review = {
        "status": "applied",
        "provider": "jev",
        "semantic_provider": "jev",
        "scope": "question_plus_bounded_evidence_candidates",
        "candidate_count": candidate_count,
        "reviewed_count": len(scores),
        "reranked": ordered_indices != original_order,
        "coverage_probability": round(coverage_probability, 4) if coverage_probability is not None else None,
        "coverage_signal_only": True,
        "signals": signals,
        "fallback_used": False,
        "reason": "candidate_order_updated",
    }
    return {"hits": ordered_hits, "review": review}


__all__ = ["review_evidence_candidates"]
