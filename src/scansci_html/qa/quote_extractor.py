from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Iterable, Protocol

from .schemas import ExtractedQuoteSchema


class ChatJsonClient(Protocol):
    def complete_json(self, messages: list[dict[str, str]], *, schema_name: str) -> Any:
        ...


@dataclass(frozen=True)
class ExtractedQuote:
    quote_id: str
    question: str
    evidence_ids: list[str]
    exact_quote: str
    role: str
    claim_hint: str
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_REFERENCE_SECTION_RE = re.compile(
    r"(?:^|\s)(?:references?|bibliography|works\s+cited|literature\s+cited|参考文献|引用文献)(?:\s|$)",
    re.IGNORECASE,
)
_REFERENCE_CUE_RE = re.compile(
    r"\b(?:doi|et\s+al\.?|journal|vol(?:ume)?\.?|issue|pp?\.?|how\s+to\s+cite|citation|science\s+direct)\b",
    re.IGNORECASE,
)
_REFERENCE_MARKER_RE = re.compile(r"(?:^|\s)[\[［]\s*\d{1,4}\s*[\]］](?:\s|$)")
_AUTHOR_LIST_RE = re.compile(
    r"^(?:[A-Z][A-Za-z'’\-]+(?:\s+[A-Z]\.)?[,;]\s*){2,}[A-Z][A-Za-z'’\-]+",
)
_NUMBERED_AUTHOR_REFERENCE_RE = re.compile(
    r"^\s*\[\s*\d{1,4}\s*\]\s*[A-Z][A-Za-z'’\-]+\s*,\s*[A-Z](?:\.|\s*,)",
)
_AUTHOR_YEAR_REFERENCE_RE = re.compile(
    r"^\s*[A-Z][A-Za-z'’\-]+\s*,\s*(?:[A-Z]\.){1,4}\s*,?\s*\d{4}[a-z]?\.?\s*$",
)
_REFERENCE_URL_RE = re.compile(r"\b(?:https?://\S+|doi\s*[:/]\s*\S+|doi\.org/\S+)", re.IGNORECASE)
_AUTHOR_FRAGMENT_RE = re.compile(
    r"^\s*[A-Z][A-Za-z'’\-]+\s*,\s*(?:(?:[A-Z]\.){1,4}|[A-Z][A-Za-z'’\-]+)(?:\s*,|\s*\.)?\s*$",
)
_VOLUME_PAGE_REFERENCE_RE = re.compile(
    r"^\s*(?:\d{1,4}\s*,\s*)?\d{1,4}\s*(?:\([^)]{1,12}\))?\s*[,:;]?\s*\d{1,5}(?:\s*[–-]\s*\d{1,5})?\.?\s*$",
)
_VOLUME_YEAR_REFERENCE_RE = re.compile(r"^\s*\d{1,4}\s*,\s*\d{1,4}\s*\(\d{4}\)\.?\s*$")
_JOURNAL_VOLUME_REFERENCE_RE = re.compile(
    r"^\s*[A-Z][A-Za-z .-]{1,60}\s+\d{1,4}\s*,\s*\d{1,5}(?:\s*[–-]\s*\d{1,5})?\s*\(\d{4}\)\.?\s*$",
)
_COMPACT_AUTHOR_REFERENCE_RE = re.compile(r"^\s*(?:[A-Z]{2,}\s*[A-Z]{1,3}\s*,\s*){2,}")
_PREDICATE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|has|have|had|show(?:s|ed)?|find(?:s|ing)?|found|"
    r"suggest(?:s|ed)?|indicat(?:e|es|ed|ing)|observ(?:e|es|ed|ing)|report(?:s|ed|ing)|"
    r"reveal(?:s|ed|ing)|demonstrat(?:e|es|ed|ing)|increase(?:s|d|ing)?|decrease(?:s|d|ing)?|"
    r"affect(?:s|ed|ing)?|result(?:s|ed|ing)?|investigat(?:e|es|ed|ing)|assess(?:es|ed|ing)?|"
    r"evaluat(?:e|es|ed|ing)|explain(?:s|ed|ing)?|improv(?:e|es|ed|ing)|reduc(?:e|es|ed|ing)|"
    r"yield(?:s|ed|ing)?|achiev(?:e|es|ed|ing)|predict(?:s|ed|ing)?|measur(?:e|es|ed|ing)|"
    r"contain(?:s|ed|ing)?|"
    r"quantif(?:y|ies|ied|ying)|correlat(?:e|es|ed|ing)|associate(?:s|d|ing)?|"
    # These ordinary research predicates are common in concise result
    # sentences. They remain subject to the bibliography checks below.
    r"fell|rose|fall(?:s|ing|en)?|ris(?:e|es|en|ing)|declin(?:e|es|ed|ing)|"
    r"dropp?(?:s|ed|ing)?|deploy(?:s|ed|ing)?|use(?:s|d|ing)?|support(?:s|ed|ing)?|"
    r"link(?:s|ed|ing)?|involv(?:e|es|ed|ing)?)\b",
    re.IGNORECASE,
)
_TITLE_LIKE_STUDY_RE = re.compile(
    r"\b(?:a|an|the)?\s*(?:modelling|modeling|experimental|field|case|observational|review)\s+study\.?$",
    re.IGNORECASE,
)


def is_substantive_evidence_hit(hit: dict[str, Any]) -> bool:
    """Return whether a retrieval hit can support a scientific claim.

    PDF text extraction frequently treats bibliography entries, cover-page
    author lists, and article titles as ordinary paragraphs.  Those strings
    can rank very highly for topical queries but are not findings.  Keeping
    the filter here makes both local and LLM quote extraction share the same
    evidence boundary.
    """

    text = " ".join(str(hit.get("text", "")).split()).strip()
    if not text:
        return False
    section = " ".join(
        str(value).strip()
        for value in (
            hit.get("section", ""),
            hit.get("section_kind", ""),
            hit.get("block_type", ""),
            hit.get("block_type_name", ""),
        )
        if str(value).strip()
    )
    context = " ".join(
        str(value)
        for value in (
            hit.get("parent_text", ""),
            hit.get("context_quote_text", ""),
            text,
        )
        if str(value).strip()
    )
    if _REFERENCE_SECTION_RE.search(section) or re.search(
        r"(?:^|\s)(?:references?|bibliography|works\s+cited|literature\s+cited|参考文献|引用文献)(?:\s|$)",
        section,
        re.IGNORECASE,
    ):
        return False
    if re.search(r"\bhow\s+to\s+cite\b|\bjournal\s+item\b", context, re.IGNORECASE):
        return False

    has_reference_marker = bool(_REFERENCE_MARKER_RE.search(context))
    has_reference_cue = bool(_REFERENCE_CUE_RE.search(context))
    if has_reference_marker and has_reference_cue:
        return False
    if _NUMBERED_AUTHOR_REFERENCE_RE.match(text):
        return False
    if _AUTHOR_YEAR_REFERENCE_RE.match(text):
        return False
    if _AUTHOR_FRAGMENT_RE.match(text):
        return False
    if _VOLUME_PAGE_REFERENCE_RE.match(text):
        return False
    if _VOLUME_YEAR_REFERENCE_RE.match(text):
        return False
    if _JOURNAL_VOLUME_REFERENCE_RE.match(text):
        return False
    if _COMPACT_AUTHOR_REFERENCE_RE.match(text):
        return False
    author_list = _AUTHOR_LIST_RE.match(text)
    if author_list and not _PREDICATE_RE.search(text[author_list.end() :]):
        return False
    author_et_al = re.match(r"^\s*[A-Z][A-Za-z'’\-]+\s+et\s+al\.?", text)
    if author_et_al and not _PREDICATE_RE.search(text[author_et_al.end() :]):
        return False

    # OCR frequently splits a reference across separate evidence rows.  A
    # journal/DOI tail such as ``Energy 53, 1-13. https://doi...`` has no
    # author marker of its own, but it still cannot support a research claim.
    if _REFERENCE_URL_RE.search(text) and not _PREDICATE_RE.search(text):
        return False

    # A source title, journal label, or publisher line is not evidence. For
    # English-only fragments, require a factual predicate; actual scientific
    # findings state an observation, relation, method, or measured result,
    # whereas bibliography fragments do not.
    english_words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", text)
    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", text))
    if re.search(r"\[\s*[JMR]\s*\]", text, re.IGNORECASE):
        return False
    if len(english_words) <= 2 and not has_chinese and not _PREDICATE_RE.search(text):
        return False
    if len(english_words) >= 3 and not has_chinese and not _PREDICATE_RE.search(text):
        return False

    # A bibliographic title is often split from its numbered author entry.
    # When it arrives without the preceding line, identify the common
    # "...: a modelling study" shape only when it contains no factual verb.
    word_count = len(re.findall(r"[A-Za-z][A-Za-z'’\-]*", text))
    if word_count >= 8 and _TITLE_LIKE_STUDY_RE.search(text) and not _PREDICATE_RE.search(text):
        return False
    return True


def extract_quotes(
    question: str,
    evidence_hits: Iterable[dict[str, Any]],
    *,
    max_quotes: int = 8,
) -> list[ExtractedQuote]:
    selected: list[dict[str, Any]] = []
    for hit in evidence_hits:
        evidence_id = str(hit.get("evidence_id", "")).strip()
        text = str(hit.get("text", "")).strip()
        matched_terms = list(hit.get("matched_terms", []) or [])
        try:
            neural_match = float(hit.get("qwen3_score", 0.0) or 0.0) >= 0.65
        except (TypeError, ValueError):
            neural_match = False
        if not evidence_id or not text or (not matched_terms and not neural_match) or not is_substantive_evidence_hit(hit):
            continue
        exact_quote = _exact_quote_text(hit)
        if not is_substantive_evidence_hit({"text": exact_quote, "section": hit.get("section", "")}):
            continue
        selected.append(hit)
        if len(selected) >= max(0, int(max_quotes)):
            break

    max_score = max((float(hit.get("score", 0.0) or 0.0) for hit in selected), default=0.0)
    quotes: list[ExtractedQuote] = []
    for index, hit in enumerate(selected, start=1):
        text = _exact_quote_text(hit)
        confidence = _confidence(float(hit.get("score", 0.0) or 0.0), max_score)
        quotes.append(
            ExtractedQuote(
                quote_id=f"q{index:04d}",
                question=question,
                evidence_ids=[str(hit["evidence_id"])],
                exact_quote=text,
                role="supports",
                claim_hint=_claim_hint(text),
                confidence=confidence,
            )
        )
    evidence_by_id = {str(hit.get("evidence_id", "")): hit for hit in selected}
    validate_quotes(quotes, evidence_by_id)
    return quotes


def extract_quotes_with_llm(
    question: str,
    evidence_hits: Iterable[dict[str, Any]],
    *,
    chat_client: ChatJsonClient,
) -> list[ExtractedQuote]:
    hits = [hit for hit in evidence_hits if is_substantive_evidence_hit(hit)]
    evidence_by_id = {str(hit.get("evidence_id", "")): hit for hit in hits if str(hit.get("evidence_id", ""))}
    messages = [
        {
            "role": "system",
            "content": (
                "Extract exact evidence quotes for the question. Use only provided evidence_ids. "
                "Every exact_quote must be copied verbatim from the corresponding evidence text."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "evidence": [
                        {
                            "evidence_id": hit.get("evidence_id", ""),
                            "text": hit.get("text", ""),
                            "title": hit.get("title", ""),
                            "section": hit.get("section", ""),
                        }
                        for hit in hits
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]
    payload = chat_client.complete_json(messages, schema_name="extracted_quotes")
    quote_items = payload.get("quotes", []) if isinstance(payload, dict) else payload
    quotes: list[ExtractedQuote] = []
    for index, item in enumerate(quote_items or [], start=1):
        item_payload = dict(item)
        item_payload.setdefault("quote_id", f"q{index:04d}")
        schema = ExtractedQuoteSchema.model_validate(item_payload)
        quotes.append(
            ExtractedQuote(
                quote_id=schema.quote_id,
                question=question,
                evidence_ids=schema.evidence_ids,
                exact_quote=schema.exact_quote,
                role=schema.role,
                claim_hint=schema.claim_hint,
                confidence=schema.confidence,
            )
        )
    validate_quotes(quotes, evidence_by_id)
    return quotes


def validate_quotes(
    quotes: Iterable[ExtractedQuote],
    evidence_by_id: dict[str, dict[str, Any]],
) -> None:
    for quote in quotes:
        if not quote.evidence_ids:
            raise ValueError(f"{quote.quote_id} has no evidence_ids")
        for evidence_id in quote.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                raise ValueError(f"{quote.quote_id} references unknown evidence_id: {evidence_id}")
            source_text = str(evidence.get("text", ""))
            if quote.exact_quote not in source_text:
                raise ValueError(
                    f"{quote.quote_id} exact_quote is not an exact substring of evidence_id {evidence_id}"
                )


def _confidence(score: float, max_score: float) -> float:
    if max_score <= 0:
        return 0.0
    return round(max(0.0, min(1.0, score / max_score)), 2)


def _claim_hint(text: str) -> str:
    value = text.strip()
    if len(value) <= 180:
        return value
    return value[:177].rstrip() + "..."


def _exact_quote_text(hit: dict[str, Any]) -> str:
    context_quote_text = str(hit.get("context_quote_text", "")).strip()
    if context_quote_text:
        return context_quote_text
    span_text = str(hit.get("span_text", "")).strip()
    if span_text:
        return span_text
    return str(hit.get("text", "")).strip()
