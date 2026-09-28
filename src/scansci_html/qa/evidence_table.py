from __future__ import annotations

from typing import Any

from .quote_extractor import ExtractedQuote, validate_quotes


def build_evidence_table(
    quotes: list[ExtractedQuote],
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, object]]:
    validate_quotes(quotes, evidence_by_id)
    rows: list[dict[str, object]] = []
    for quote in quotes:
        for evidence_id in quote.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            row: dict[str, object] = {
                "quote_id": quote.quote_id,
                "claim_target": quote.claim_hint,
                "stance": quote.role,
                "exact_quote": quote.exact_quote,
                "paper": str(evidence.get("title", "")),
                "section": str(evidence.get("section", "")),
                "section_kind": str(evidence.get("section_kind", "")),
                "doi": str(evidence.get("doi", "")),
                "source_url": str(evidence.get("source_url", "")),
                "evidence_id": evidence_id,
                "html_path": str(evidence.get("html_path", "")),
                "html_anchor": str(evidence.get("html_anchor", "")),
                "matched_text": str(evidence.get("matched_text", evidence.get("text", ""))),
                "context_before": str(evidence.get("context_before", "")),
                "context_before_evidence_id": str(evidence.get("context_before_evidence_id", "")),
                "context_after": str(evidence.get("context_after", "")),
                "context_after_evidence_id": str(evidence.get("context_after_evidence_id", "")),
                "context_text": str(evidence.get("parent_text", "")),
                "parent_block_id": str(evidence.get("parent_block_id", "")),
                "parent_evidence_ids": list(evidence.get("parent_evidence_ids", []) or []),
                "confidence": quote.confidence,
            }
            doc_id = str(evidence.get("doc_id", ""))
            if doc_id:
                row["doc_id"] = doc_id
            rows.append(row)
    return rows
