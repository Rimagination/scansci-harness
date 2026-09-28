#!/usr/bin/env python3
"""Validate and normalize a host evidence-search payload.

This helper is intentionally dependency-free and never makes network calls.
It preserves host-owned identifiers and keeps only audit-safe JEV fields.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, TextIO


_MAX_QUERY_CHARS = 1200
_MAX_HITS = 50
_MAX_TEXT_CHARS = 1600
_MAX_CONTEXT_CHARS = 700
_SENSITIVE_TEXT = re.compile(
    r"(?:\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret)\b\s*[:=]\s*[^\s,;]+"
    r"|\bbearer\s+[A-Za-z0-9._~-]{12,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|-----BEGIN [^-]{1,80}PRIVATE KEY-----)",
    re.IGNORECASE,
)


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _source_text(value: object, limit: int, *, field: str) -> str:
    clean = " ".join(str(value or "").split())
    if _SENSITIVE_TEXT.search(clean):
        raise ValueError(f"{field} contains a sensitive-looking value")
    return clean[:limit]


def _number(value: object, *, field: str, probability: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if probability and not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return round(result, 6 if not probability else 4)


def _safe_review(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "not_run", "reason": "not_reported"}
    review: dict[str, Any] = {}
    for key in ("provider", "semantic_provider", "scope", "reason"):
        if isinstance(value.get(key), str):
            review[key] = _text(value[key], 240)
    if isinstance(value.get("status"), str):
        review["status"] = _text(value["status"], 32)
    for key in ("reranked", "coverage_signal_only", "fallback_used", "retryable"):
        if isinstance(value.get(key), bool):
            review[key] = value[key]
    for key in ("candidate_count", "reviewed_count", "http_status"):
        raw_value = value.get(key)
        if raw_value is not None and (isinstance(raw_value, bool) or not isinstance(raw_value, int)):
            raise ValueError(f"review.{key} must be an integer")
        if raw_value is not None:
            review[key] = raw_value
        if key in review and review[key] is not None:
            if key in {"candidate_count", "reviewed_count"} and review[key] < 0:
                raise ValueError(f"review.{key} must be non-negative")
            if key == "http_status" and not 100 <= review[key] <= 599:
                raise ValueError("review.http_status must be between 100 and 599")
    if (
        review.get("candidate_count") is not None
        and review.get("reviewed_count") is not None
        and review["reviewed_count"] > review["candidate_count"]
    ):
        raise ValueError("review.reviewed_count cannot exceed candidate_count")
    if "coverage_probability" in review and review["coverage_probability"] is not None:
        review["coverage_probability"] = _number(
            review["coverage_probability"],
            field="review.coverage_probability",
            probability=True,
        )
    elif value.get("coverage_probability") is not None:
        review["coverage_probability"] = _number(
            value["coverage_probability"],
            field="review.coverage_probability",
            probability=True,
        )
    status = review.get("status")
    if status is None:
        review["status"] = "not_run"
    elif status not in {"applied", "not_run", "degraded"}:
        review["status"] = "degraded"
        review.setdefault("reason", "invalid_review_status")
    return review


def normalize_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("the input must be a JSON object")
    raw_hits = payload.get("hits", [])
    if not isinstance(raw_hits, list):
        raise ValueError("hits must be an array")

    hits: list[dict[str, Any]] = []
    for index, raw_hit in enumerate(raw_hits[:_MAX_HITS]):
        if not isinstance(raw_hit, dict):
            raise ValueError(f"hits[{index}] must be an object")
        evidence_id = _text(raw_hit.get("evidence_id"), 240)
        matched_text = _source_text(
            raw_hit.get("matched_text") or raw_hit.get("text"),
            _MAX_TEXT_CHARS,
            field=f"hits[{index}].matched_text",
        )
        if not evidence_id:
            raise ValueError(f"hits[{index}] is missing evidence_id")
        if not matched_text:
            raise ValueError(f"hits[{index}] is missing matched_text")
        hit: dict[str, Any] = {
            "evidence_id": evidence_id,
            "doc_id": _text(raw_hit.get("doc_id"), 240),
            "paper": _source_text(
                raw_hit.get("paper") or raw_hit.get("title"),
                240,
                field=f"hits[{index}].paper",
            ),
            "doi": _text(raw_hit.get("doi"), 240),
            "section": _text(raw_hit.get("section"), 160),
            "html_anchor": _text(raw_hit.get("html_anchor"), 240),
            "matched_text": matched_text,
            "context_before": _source_text(
                raw_hit.get("context_before"),
                _MAX_CONTEXT_CHARS,
                field=f"hits[{index}].context_before",
            ),
            "context_after": _source_text(
                raw_hit.get("context_after"),
                _MAX_CONTEXT_CHARS,
                field=f"hits[{index}].context_after",
            ),
        }
        if "score" in raw_hit and raw_hit.get("score") is not None:
            hit["score"] = _number(raw_hit["score"], field=f"hits[{index}].score")
        if "jev_support_probability" in raw_hit and raw_hit.get("jev_support_probability") is not None:
            hit["jev_support_probability"] = _number(
                raw_hit["jev_support_probability"],
                field=f"hits[{index}].jev_support_probability",
                probability=True,
            )
        hits.append(hit)

    review = _safe_review(payload.get("jev_review", payload.get("review")))
    upstream_status = _text(payload.get("status"), 32)
    status = upstream_status if upstream_status in {"ready", "empty", "degraded", "not_run"} else (
        "empty" if not hits else "ready"
    )
    if str(review.get("status", "")) == "degraded":
        status = "degraded"
    return {
        "schema_version": "scansci.evidence-envelope.v1",
        "status": status,
        "query": _source_text(payload.get("query"), _MAX_QUERY_CHARS, field="query"),
        "hits": hits,
        "review": review,
    }


def _load_input(value: str) -> tuple[dict[str, Any], TextIO | None]:
    if value == "-":
        return json.load(sys.stdin), None
    path = Path(value)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="JSON file, or - for stdin")
    args = parser.parse_args(argv)
    try:
        payload, _ = _load_input(str(args.input))
        normalized = normalize_payload(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    json.dump(normalized, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
