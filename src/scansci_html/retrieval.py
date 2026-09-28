from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import re
from pathlib import Path
import sqlite3
from time import perf_counter
from typing import Any

from .embeddings import EmbeddingProvider, HashingEmbeddingProvider, cosine_similarity, embed_query
from .rerankers import LexicalReranker, Reranker
from .text_tokenization import lexical_tokens
from .vector_index import cached_embedding_candidates, query_cached_embedding_candidates


STOPWORDS = {
    # English
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "of", "on", "or", "that", "the", "to", "was", "were",
    "what", "which", "with",

    # Chinese stopwords — boilerplate that carries no topical signal.
    # Without them a query like "光伏组件衰减率通常受到哪些因素影响？"
    # and an unrelated psychology paper both contain "通常/因素",
    # making the psychology paper rank first.
    # General verbs / function words
    "是", "有", "会", "可以", "能够", "需要", "使用", "进行", "包括",
    "存在", "产生", "提供", "使得", "成为", "作为", "认为", "表示", "发现",
    # Conjunctions / prepositions
    "和", "与", "以及", "或", "且", "但", "而", "然而",
    "在", "对", "从", "到", "用", "让", "被", "把", "将", "向", "由", "以",
    "于", "关于", "对于", "根据", "经过", "除了",
    # Adverbs / degree
    "都", "也", "只", "就", "还", "更", "最", "很", "较", "太",
    "已经", "正在", "将要", "可能", "应该",
    # Question words
    "什么", "怎样", "为什么", "如何", "哪些", "哪", "谁", "怎么",
    # Demonstratives / particles
    "这", "那", "这些", "那些", "这个", "那个", "其", "此", "该",
    "的", "了", "吗", "呢", "吧", "着", "过", "得", "所", "者", "之",
    # High-frequency boilerplate (Chinese academic prose)
    "因素", "通常", "受到", "一般", "方面", "问题", "作用", "关系",
    "过程", "机制", "特征", "特点", "基础", "方法", "结果", "表现",
    "论文", "文献", "研究", "本文", "作者", "提出", "利用", "影响",
    # English academic boilerplate
    "paper", "study", "research", "result", "method", "approach",
    "introduction", "conclusion", "discussion", "background",
    "related", "work", "section", "figure", "table", "et", "al",
    "propose", "proposed", "show", "shows", "shown", "also", "thus",
    "therefore", "however", "moreover", "furthermore", "both", "each",
    "using", "used", "based", "due", "well", "may", "one", "two",
    "whether", "not", "still", "yet", "often", "many",
    "much", "such", "various", "different", "between", "among",
    "within", "these", "those", "their", "its",
}


def classify_evidence_query(query: str) -> dict[str, Any]:
    """Choose a bounded retrieval plan from the user's question.

    This is a deterministic router, not a hidden LLM judgement.  It decides
    how broadly to consult document cards before the retriever descends into
    original evidence spans.  Every result records the route in its trace.
    """

    normalized = " ".join(str(query or "").casefold().split())
    if not normalized:
        return {"route": "empty", "document_budget": 0, "raw_candidate_limit": 0}
    if any(token in normalized for token in ("多少", "几篇", "几条", "数量", "总数", "count", "how many")):
        return {"route": "catalog-count", "document_budget": 32, "raw_candidate_limit": 80}
    if any(token in normalized for token in ("综述", "进展", "现状", "发展", "review", "survey", "overview")):
        return {"route": "evidence-review", "document_budget": 80, "raw_candidate_limit": 420}
    if any(token in normalized for token in ("比较", "对比", "差异", "不同", "versus", "compare")):
        return {"route": "evidence-comparison", "document_budget": 64, "raw_candidate_limit": 320}
    if any(token in normalized for token in ("有什么", "包含", "结构", "目录", "有哪些资料", "what is in")):
        return {"route": "catalog-overview", "document_budget": 40, "raw_candidate_limit": 120}
    return {"route": "evidence-answer", "document_budget": 40, "raw_candidate_limit": 240}


def search_evidence_index(
    index_path: str | Path,
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    query_terms = _query_terms(query)
    if not query_terms:
        return []

    hits: list[dict[str, Any]] = []
    for row in _load_jsonl(Path(index_path)):
        score, matched_terms = _score_row(row, query_terms)
        if score <= 0:
            continue
        hit = dict(row)
        hit["score"] = round(score, 6)
        hit["matched_terms"] = matched_terms
        hits.append(hit)

    hits.sort(key=lambda hit: (-float(hit["score"]), str(hit.get("block_id", ""))))
    return hits[: max(0, int(limit))]


def search_evidence_store(
    db_path: str | Path,
    query: str,
    *,
    limit: int = 5,
    initial_limit: int = 200,
    paper_recall_limit: int = 0,
    per_document_limit: int = 5,
    filters: dict[str, Any] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: Reranker | None = None,
    context_mode: str = "sentence",
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    started_at = perf_counter()
    query_terms = _query_terms(query)
    if not query_terms:
        _append_retrieval_trace(
            trace,
            query=query,
            query_terms=query_terms,
            total_rows=0,
            filtered_rows=0,
            initial_limit=initial_limit,
            dense_limit=-1,
            paper_recall_limit=paper_recall_limit,
            paper_recalled_documents=0,
            per_document_limit=per_document_limit,
            fts_candidates=0,
            dense_candidates=0,
            unique_candidates=0,
            reranked_candidates=0,
            returned_hits=0,
            route_counts={},
            started_at=started_at,
            status="empty_query",
            dense_backend="none",
        )
        return []

    db = Path(db_path)
    # A current evidence store contains a deliberately small document catalogue:
    # one source-grounded card per file.  Use it as the first retrieval layer so
    # an ordinary question never materializes a 190k-span library in memory.
    # Older stores retain the legacy path below until their overview is built.
    if _has_ready_document_cards(db):
        return _search_evidence_store_from_document_cards(
            db,
            query,
            query_terms=query_terms,
            limit=limit,
            initial_limit=initial_limit,
            paper_recall_limit=paper_recall_limit,
            per_document_limit=per_document_limit,
            filters=filters or {},
            embedding_provider=embedding_provider,
            reranker=reranker,
            context_mode=context_mode,
            trace=trace,
            started_at=started_at,
        )

    rows = _load_evidence_spans(db)
    total_rows = len(rows)
    rows = _filter_evidence_spans(rows, filters or {})
    provider = embedding_provider or HashingEmbeddingProvider()
    recalled_documents: list[dict[str, Any]] = []
    if paper_recall_limit > 0 and rows:
        recalled_documents = recall_relevant_documents(
            db,
            query,
            limit=paper_recall_limit,
            filters=filters,
            embedding_provider=provider,
            trace=trace,
            rows=rows,
        )
        recalled_doc_ids = {str(document.get("doc_id", "")) for document in recalled_documents}
        recalled_doc_ids.discard("")
        rows = {
            evidence_id: row
            for evidence_id, row in rows.items()
            if str(row.get("doc_id", "")) in recalled_doc_ids
        }
    if not rows:
        _append_retrieval_trace(
            trace,
            query=query,
            query_terms=query_terms,
            total_rows=total_rows,
            filtered_rows=0,
            initial_limit=initial_limit,
            dense_limit=-1,
            paper_recall_limit=paper_recall_limit,
            paper_recalled_documents=len(recalled_documents),
            per_document_limit=per_document_limit,
            fts_candidates=0,
            dense_candidates=0,
            unique_candidates=0,
            reranked_candidates=0,
            returned_hits=0,
            route_counts={},
            started_at=started_at,
            status="empty_corpus",
            dense_backend="none",
        )
        return []

    candidates: dict[str, dict[str, Any]] = {}
    fts_candidate_count = 0
    for evidence_id, fts_score in _fts_candidates(db, query_terms, limit=initial_limit):
        row = rows.get(evidence_id)
        if row is None:
            continue
        fts_candidate_count += 1
        candidate = candidates.setdefault(evidence_id, dict(row))
        candidate["fts_score"] = max(float(candidate.get("fts_score", 0.0)), fts_score)
        candidate.setdefault("routes", set()).add("fts")

    query_vector = embed_query(provider, query)
    evidence_ids = list(rows)
    cached_dense = cached_embedding_candidates(
        db,
        rows,
        provider=provider,
        query_vector=query_vector,
        limit=max(initial_limit, max(1, int(limit)) * 20, 200),
        # Tiny libraries remain convenient in tests and one-off notebooks.
        # Large production libraries are migrated by the observable background
        # index task instead of freezing the first question for many minutes.
        build_missing=len(rows) <= 2_000,
        prune_stale=len(rows) == total_rows and paper_recall_limit <= 0,
    )
    dense_backend = str(cached_dense[1]["backend"]) if cached_dense else "python"
    dense_values: list[tuple[str, float]]
    if cached_dense:
        dense_values = cached_dense[0]
        evidence_ids = [evidence_id for evidence_id, _score in dense_values]
    else:
        text_vectors = provider.embed_texts([str(rows[evidence_id].get("text", "")) for evidence_id in evidence_ids])
        dense_values = [
            (evidence_id, cosine_similarity(query_vector, vector))
            for evidence_id, vector in zip(evidence_ids, text_vectors)
        ]
    dense_candidate_count = 0
    document_recall_by_id = {str(document.get("doc_id", "")): document for document in recalled_documents}
    for evidence_id, dense_score in dense_values:
        # sqlite-vec returns float32 distances; orthogonal normalized vectors
        # can round back to a tiny positive cosine instead of exact zero.
        if dense_score <= 1e-6:
            continue
        dense_candidate_count += 1
        candidate = candidates.setdefault(evidence_id, dict(rows[evidence_id]))
        candidate["dense_score"] = max(float(candidate.get("dense_score", 0.0)), dense_score)
        candidate.setdefault("routes", set()).add("dense")

    # Legacy span-first stores have no document-card layer to carry tags into
    # recall.  Seed one representative span per tag-matched document so a tag
    # can introduce a document even when the exact label is absent from its
    # full text.  The final reranker still sees all ordinary FTS/dense signals.
    tags_by_doc = _load_document_tags(db, {str(row.get("doc_id", "")) for row in rows.values()})
    tag_seed_rows: dict[str, tuple[dict[str, Any], float]] = {}
    for row in rows.values():
        doc_id = str(row.get("doc_id", ""))
        tags = tags_by_doc.get(doc_id, [])
        tag_score = _tag_match_score(tags, query_terms)
        if tag_score <= 0.0 or doc_id in tag_seed_rows:
            continue
        tag_seed_rows[doc_id] = (row, tag_score)
    for row, tag_score in tag_seed_rows.values():
        evidence_id = str(row.get("evidence_id", ""))
        if not evidence_id:
            continue
        candidate = candidates.setdefault(evidence_id, dict(row))
        candidate["tags"] = list(tags_by_doc.get(str(row.get("doc_id", "")), []))
        candidate["tag_score"] = max(float(candidate.get("tag_score", 0.0) or 0.0), tag_score)
        candidate.setdefault("routes", set()).add("tag")

    normalized_candidates = []
    for candidate in candidates.values():
        routes = {str(route) for route in candidate.get("routes", []) or []}
        candidate.setdefault("fts_score", 0.0)
        candidate.setdefault("dense_score", 0.0)
        candidate.setdefault("tags", list(tags_by_doc.get(str(candidate.get("doc_id", "")), [])))
        candidate["tag_score"] = max(
            float(candidate.get("tag_score", 0.0) or 0.0),
            _tag_match_score(candidate.get("tags", []), query_terms),
        )
        if candidate["tag_score"] > 0.0:
            routes.add("tag")
        candidate["routes"] = sorted(routes)
        document_recall = document_recall_by_id.get(str(candidate.get("doc_id", "")))
        if document_recall:
            candidate["paper_recall_rank"] = document_recall.get("rank")
            candidate["paper_recall_score"] = document_recall.get("score")
        normalized_candidates.append(candidate)

    normalized_candidates = _expand_neighbor_candidates(query, normalized_candidates, rows)
    ranked = (reranker or LexicalReranker()).rerank(query, normalized_candidates)
    capped = _apply_per_document_limit(ranked, per_document_limit=per_document_limit)
    hits = capped[: max(0, int(limit))]
    hits = _attach_sentence_context(hits, rows)
    if _normalized_context_mode(context_mode) == "block":
        hits = _expand_hits_to_parent_blocks(hits, rows)
    _append_retrieval_trace(
        trace,
        query=query,
        query_terms=query_terms,
        total_rows=total_rows,
        filtered_rows=len(rows),
        initial_limit=initial_limit,
        dense_limit=len(evidence_ids),
        paper_recall_limit=paper_recall_limit,
        paper_recalled_documents=len(recalled_documents),
        per_document_limit=per_document_limit,
        fts_candidates=fts_candidate_count,
        dense_candidates=dense_candidate_count,
        unique_candidates=len(normalized_candidates),
        reranked_candidates=len(ranked),
        returned_hits=len(hits),
        route_counts=_route_counts(hits),
        started_at=started_at,
        status="ok",
        dense_backend=dense_backend,
    )
    return hits


def _search_evidence_store_from_document_cards(
    db: Path,
    query: str,
    *,
    query_terms: list[str],
    limit: int,
    initial_limit: int,
    paper_recall_limit: int,
    per_document_limit: int,
    filters: dict[str, Any],
    embedding_provider: EmbeddingProvider | None,
    reranker: Reranker | None,
    context_mode: str,
    trace: list[dict[str, Any]] | None,
    started_at: float,
) -> list[dict[str, Any]]:
    """Retrieve through document cards, then fetch only bounded raw evidence.

    The cards are not a citation source.  They give the retriever a compact
    corpus map (one per file) and select documents; every final hit still comes
    from ``evidence_spans`` and carries its original evidence ID.
    """

    cards = _load_document_cards(db, filters=filters)
    total_rows = _count_evidence_spans(db)
    if not cards:
        _append_retrieval_trace(
            trace,
            query=query,
            query_terms=query_terms,
            total_rows=total_rows,
            filtered_rows=0,
            initial_limit=initial_limit,
            dense_limit=0,
            paper_recall_limit=paper_recall_limit,
            paper_recalled_documents=0,
            per_document_limit=per_document_limit,
            fts_candidates=0,
            dense_candidates=0,
            unique_candidates=0,
            reranked_candidates=0,
            returned_hits=0,
            route_counts={},
            started_at=started_at,
            status="empty_catalogue",
            dense_backend="none",
            strategy="document-card-first",
            catalog_documents=0,
            catalog_selected_documents=0,
        )
        return []

    provider = embedding_provider or HashingEmbeddingProvider()
    query_route = classify_evidence_query(query)
    document_budget = (
        max(1, int(paper_recall_limit))
        if paper_recall_limit > 0
        else min(
            int(query_route["document_budget"]),
            max(24, max(1, int(limit)) * 8),
        )
    )
    card_ranked = _rank_document_cards(
        cards,
        query_terms,
        db_path=db,
        query=query,
        provider=provider,
    )

    # Both sparse and dense evidence routes query SQLite directly.  The dense
    # route reads a completed immutable sqlite-vec generation; it never asks a
    # serving query to enumerate, embed, or validate every evidence span.
    candidate_limit = max(
        max(1, int(initial_limit)),
        max(1, int(limit)) * 30,
        int(query_route["raw_candidate_limit"]),
    )
    sparse_seed = _fts_candidates_with_documents(db, query_terms, limit=candidate_limit, filters=filters)
    query_vector = embed_query(provider, query)
    cached_dense = query_cached_embedding_candidates(
        db,
        provider=provider,
        query_vector=query_vector,
        limit=candidate_limit,
    )
    dense_backend = str(cached_dense[1].get("backend", "none")) if cached_dense else "none"
    dense_scores = dict(cached_dense[0]) if cached_dense else {}
    dense_seed_rows = _load_evidence_by_ids(db, list(dense_scores), filters=filters)

    document_scores: dict[str, float] = {}
    document_routes: dict[str, set[str]] = defaultdict(set)
    cards_by_id = {str(card["doc_id"]): card for card in cards}
    for card in card_ranked:
        doc_id = str(card["doc_id"])
        score = float(card.get("score", 0.0) or 0.0)
        if score > 0.0:
            document_scores[doc_id] = max(document_scores.get(doc_id, 0.0), score)
            document_routes[doc_id].add("document-card")
            if float(card.get("tag_score", 0.0) or 0.0) > 0.0:
                document_routes[doc_id].add("tag")
            if float(card.get("card_dense_score", 0.0) or 0.0) > 1e-6:
                document_routes[doc_id].add("document-card-dense")
    for evidence_id, doc_id, sparse_score in sparse_seed:
        if doc_id not in cards_by_id:
            continue
        document_scores[doc_id] = max(document_scores.get(doc_id, 0.0), float(sparse_score))
        document_routes[doc_id].add("fts")
    for evidence_id, row in dense_seed_rows.items():
        doc_id = str(row.get("doc_id", ""))
        if doc_id not in cards_by_id:
            continue
        dense_score = float(dense_scores.get(evidence_id, 0.0) or 0.0)
        if dense_score <= 1e-6:
            continue
        document_scores[doc_id] = max(document_scores.get(doc_id, 0.0), dense_score)
        document_routes[doc_id].add("dense")

    selected_documents: list[dict[str, Any]] = []
    for card in card_ranked:
        doc_id = str(card["doc_id"])
        if doc_id not in document_scores:
            continue
        selected = dict(card)
        selected["score"] = round(float(document_scores[doc_id]), 6)
        selected["routes"] = sorted(document_routes.get(doc_id, {"document-card"}))
        selected_documents.append(selected)
    selected_documents.sort(key=lambda row: (-float(row.get("score", 0.0)), str(row.get("doc_id", ""))))
    selected_documents = selected_documents[:document_budget]
    for rank, document in enumerate(selected_documents, start=1):
        document["rank"] = rank
    selected_doc_ids = {str(document["doc_id"]) for document in selected_documents}

    _append_document_card_recall_trace(
        trace,
        query=query,
        query_terms=query_terms,
        total_documents=len(cards),
        selected_documents=selected_documents,
        started_at=started_at,
        status="ok" if selected_documents else "no_matching_documents",
    )
    _append_lazy_graph_trace(
        trace,
        db,
        selected_doc_ids,
        query=query,
        started_at=started_at,
    )
    if not selected_doc_ids:
        _append_retrieval_trace(
            trace,
            query=query,
            query_terms=query_terms,
            total_rows=total_rows,
            filtered_rows=0,
            initial_limit=initial_limit,
            dense_limit=len(dense_scores),
            paper_recall_limit=paper_recall_limit,
            paper_recalled_documents=0,
            per_document_limit=per_document_limit,
            fts_candidates=len(sparse_seed),
            dense_candidates=len(dense_seed_rows),
            unique_candidates=0,
            reranked_candidates=0,
            returned_hits=0,
            route_counts={},
            started_at=started_at,
            status="no_matching_documents",
            dense_backend=dense_backend,
            strategy="document-card-first",
            catalog_documents=len(cards),
            catalog_selected_documents=0,
        )
        return []

    sparse_candidates = _fts_candidates_with_documents(
        db,
        query_terms,
        limit=max(1, int(initial_limit)),
        filters=filters,
        doc_ids=selected_doc_ids,
    )
    candidate_ids = {evidence_id for evidence_id, _doc_id, _score in sparse_candidates}
    candidate_ids.update(
        evidence_id
        for evidence_id, row in dense_seed_rows.items()
        if str(row.get("doc_id", "")) in selected_doc_ids and float(dense_scores.get(evidence_id, 0.0) or 0.0) > 1e-6
    )
    # Sparse-only stores can still produce grounded candidates from the
    # representative evidence anchors attached to the selected document cards.
    for document in selected_documents:
        candidate_ids.update(str(item) for item in document.get("anchor_evidence_ids", []) or [] if str(item))
    candidate_rows = _load_evidence_by_ids(db, sorted(candidate_ids), filters=filters)

    sparse_scores = {evidence_id: score for evidence_id, _doc_id, score in sparse_candidates}
    candidates: dict[str, dict[str, Any]] = {}
    document_recall_by_id = {str(document["doc_id"]): document for document in selected_documents}
    for evidence_id, row in candidate_rows.items():
        doc_id = str(row.get("doc_id", ""))
        if doc_id not in selected_doc_ids:
            continue
        candidate = dict(row)
        routes: set[str] = set()
        recalled_document = document_recall_by_id.get(doc_id)
        if recalled_document is not None:
            candidate["tags"] = list(recalled_document.get("tags", []) or [])
            candidate["tag_score"] = float(recalled_document.get("tag_score", 0.0) or 0.0)
        else:
            candidate["tags"] = []
            candidate["tag_score"] = 0.0
        if evidence_id in sparse_scores:
            candidate["fts_score"] = float(sparse_scores[evidence_id])
            routes.add("fts")
        else:
            candidate["fts_score"] = 0.0
        dense_score = float(dense_scores.get(evidence_id, 0.0) or 0.0)
        if dense_score > 1e-6:
            candidate["dense_score"] = dense_score
            routes.add("dense")
        else:
            candidate["dense_score"] = 0.0
        if not routes:
            routes.add("document-card-anchor")
        if float(candidate.get("tag_score", 0.0) or 0.0) > 0.0:
            routes.add("tag")
        candidate["routes"] = sorted(routes)
        if recalled_document is not None:
            candidate["document_card_rank"] = recalled_document.get("rank")
            candidate["document_card_score"] = recalled_document.get("score")
            if paper_recall_limit > 0:
                candidate["paper_recall_rank"] = recalled_document.get("rank")
                candidate["paper_recall_score"] = recalled_document.get("score")
        candidates[evidence_id] = candidate

    context_rows = _load_block_context_rows(db, candidates, filters=filters)
    normalized_candidates = _expand_neighbor_candidates(query, list(candidates.values()), context_rows)
    ranked = (reranker or LexicalReranker()).rerank(query, normalized_candidates)
    capped = _apply_per_document_limit(ranked, per_document_limit=per_document_limit)
    hits = capped[: max(0, int(limit))]
    hits = _attach_sentence_context(hits, context_rows)
    if _normalized_context_mode(context_mode) == "block":
        block_rows = _load_block_context_rows(db, {str(hit.get("evidence_id", "")): hit for hit in hits}, filters=filters)
        hits = _expand_hits_to_parent_blocks(hits, block_rows)
    _append_retrieval_trace(
        trace,
        query=query,
        query_terms=query_terms,
        total_rows=total_rows,
        filtered_rows=len(candidate_rows),
        initial_limit=initial_limit,
        dense_limit=len(dense_scores),
        paper_recall_limit=paper_recall_limit,
        paper_recalled_documents=len(selected_documents) if paper_recall_limit > 0 else 0,
        per_document_limit=per_document_limit,
        fts_candidates=len(sparse_candidates),
        dense_candidates=sum(1 for evidence_id in candidate_rows if float(dense_scores.get(evidence_id, 0.0) or 0.0) > 1e-6),
        unique_candidates=len(normalized_candidates),
        reranked_candidates=len(ranked),
        returned_hits=len(hits),
        route_counts=_route_counts(hits),
        started_at=started_at,
        status="ok",
        dense_backend=dense_backend,
        strategy="document-card-first",
        catalog_documents=len(cards),
        catalog_selected_documents=len(selected_documents),
    )
    return hits


def recall_relevant_documents(
    db_path: str | Path,
    query: str,
    *,
    limit: int = 50,
    filters: dict[str, Any] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    trace: list[dict[str, Any]] | None = None,
    rows: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    started_at = perf_counter()
    query_terms = _query_terms(query)
    if not query_terms:
        _append_paper_recall_trace(
            trace,
            query=query,
            query_terms=query_terms,
            total_documents=0,
            selected_documents=[],
            started_at=started_at,
            status="empty_query",
        )
        return []

    db = Path(db_path)
    evidence_rows = rows if rows is not None else _filter_evidence_spans(_load_evidence_spans(db), filters or {})
    documents = _document_profiles(evidence_rows)
    if not documents:
        _append_paper_recall_trace(
            trace,
            query=query,
            query_terms=query_terms,
            total_documents=0,
            selected_documents=[],
            started_at=started_at,
            status="empty_corpus",
        )
        return []

    provider = embedding_provider or HashingEmbeddingProvider()
    query_vector = embed_query(provider, query)
    profile_vectors = provider.embed_texts([str(document["profile_text"]) for document in documents])
    ranked: list[dict[str, Any]] = []
    for document, vector in zip(documents, profile_vectors):
        lexical_score = _document_lexical_score(document, query_terms)
        dense_score = cosine_similarity(query_vector, vector)
        score = lexical_score + dense_score
        if score <= 0:
            continue
        ranked.append(
            {
                "doc_id": str(document["doc_id"]),
                "title": str(document.get("title", "")),
                "doi": str(document.get("doi", "")),
                "publication_year": document.get("publication_year"),
                "score": round(score, 6),
                "lexical_score": round(lexical_score, 6),
                "dense_score": round(dense_score, 6),
                "matched_terms": _matched_terms_in_text(str(document["profile_text"]), query_terms),
                "profile_span_count": int(document.get("profile_span_count", 0) or 0),
                "span_count": int(document.get("span_count", 0) or 0),
            }
        )
    ranked.sort(key=lambda document: (-float(document["score"]), str(document.get("doc_id", ""))))
    # Drop documents that have zero lexical overlap with the question and only
    # a weak dense signal.  A hashing embedding (128-dim hash) can produce
    # small cosine noise (~0.16) that is not evidence of topical relevance.
    # Real neural embeddings will reliably push relevant documents above this
    # floor.
    _RANK_NOISE_DENSE_THRESHOLD = 0.3
    ranked = [
        item
        for item in ranked
        if (
            item["lexical_score"] > 0.0
            or item["matched_terms"]
            or item["dense_score"] >= _RANK_NOISE_DENSE_THRESHOLD
        )
    ]
    selected = ranked[: max(0, int(limit))]
    for rank, document in enumerate(selected, start=1):
        document["rank"] = rank
    _append_paper_recall_trace(
        trace,
        query=query,
        query_terms=query_terms,
        total_documents=len(documents),
        selected_documents=selected,
        started_at=started_at,
        status="ok",
    )
    return selected


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _has_ready_document_cards(db_path: Path) -> bool:
    """Return whether a store has a complete one-card-per-document catalogue."""

    try:
        with sqlite3.connect(db_path) as connection:
            tables = {str(row[0]) for row in connection.execute("select name from sqlite_master where type = 'table'")}
            if not {"source_documents", "document_cards"}.issubset(tables):
                return False
            document_count = int(connection.execute("select count(*) from source_documents").fetchone()[0] or 0)
            card_count = int(connection.execute("select count(*) from document_cards").fetchone()[0] or 0)
            return document_count > 0 and card_count == document_count
    except sqlite3.Error:
        return False


def _load_document_cards(db_path: Path, *, filters: dict[str, Any]) -> list[dict[str, Any]]:
    doc_ids = _string_set(filters.get("doc_ids"))
    if "doc_ids" in filters and not doc_ids:
        return []
    year_min = _int_or_none(filters.get("year_min"))
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select
                    dc.doc_id,
                    dc.title,
                    dc.summary,
                    dc.keywords_json,
                    dc.anchor_evidence_ids_json,
                    dc.section_count,
                    dc.evidence_count,
                    dc.source_digest,
                    sd.doi,
                    sd.publication_year
                from document_cards as dc
                join source_documents as sd on sd.doc_id = dc.doc_id
                order by dc.title collate nocase, dc.doc_id
                """
            ).fetchall()
    except sqlite3.Error:
        return []
    cards: list[dict[str, Any]] = []
    tags_by_doc = _load_document_tags(db_path, [str(row["doc_id"]) for row in rows])
    for row in rows:
        card = dict(row)
        doc_id = str(card.get("doc_id", "")).strip()
        if not doc_id:
            continue
        if doc_ids and doc_id.lower() not in doc_ids:
            continue
        if year_min is not None:
            publication_year = _int_or_none(card.get("publication_year"))
            if publication_year is None or publication_year < year_min:
                continue
        card["keywords"] = _json_string_list(card.pop("keywords_json", "[]"))
        card["anchor_evidence_ids"] = _json_string_list(card.pop("anchor_evidence_ids_json", "[]"))
        card["tags"] = list(tags_by_doc.get(doc_id, []))
        card["tag_text"] = " ".join(card["tags"])
        card["profile_text"] = " ".join(
            value
            for value in (
                str(card.get("title", "")).strip(),
                " ".join(card["keywords"]),
                str(card.get("summary", "")).strip(),
            )
            if value
        )
        # Tags influence the compact semantic route, while their exact-match
        # contribution is scored separately below as a deliberately small
        # lexical boost.
        card["semantic_profile_text"] = " ".join(
            value for value in (card["profile_text"], card["tag_text"]) if value
        )
        cards.append(card)
    return cards


def _load_document_tags(
    db_path: Path,
    doc_ids: list[str] | set[str] | None = None,
) -> dict[str, list[str]]:
    """Load optional Zotero tags without making retrieval depend on Zotero."""

    normalized_ids = sorted({str(value).strip() for value in (doc_ids or []) if str(value).strip()})
    clauses = ""
    parameters: list[Any] = []
    if doc_ids is not None:
        if not normalized_ids:
            return {}
        clauses = f" where doc_id in ({', '.join('?' for _ in normalized_ids)})"
        parameters.extend(normalized_ids)
    try:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                f"""
                select doc_id, tag
                from document_tags
                {clauses}
                order by doc_id, normalized_tag, tag
                """,
                parameters,
            ).fetchall()
    except sqlite3.Error:
        # Existing evidence stores may predate the sidecar.  Their ordinary
        # FTS/vector retrieval remains fully valid.
        return {}
    tags_by_doc: dict[str, list[str]] = {}
    for doc_id, tag in rows:
        clean = str(tag or "").strip()
        if clean and clean not in tags_by_doc.setdefault(str(doc_id), []):
            tags_by_doc[str(doc_id)].append(clean)
    return tags_by_doc


def _document_card_embedding_digest(card: dict[str, Any]) -> str:
    """Invalidate a compact card vector when its Zotero tag profile changes."""

    source_digest = str(card.get("source_digest", "") or "")
    tags = [str(tag).strip() for tag in list(card.get("tags", []) or []) if str(tag).strip()]
    if not tags:
        return source_digest
    payload = "\n".join([source_digest, *sorted({tag.casefold() for tag in tags})])
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=20).hexdigest()


def _tag_match_score(tags: Any, query_terms: list[str]) -> float:
    """Return a bounded soft score for exact/partial tag overlap.

    This is intentionally much smaller than a hard filter.  A tag can lift a
    relevant document into the candidate set, but it cannot suppress documents
    whose evidence text or embedding matches the question better.
    """

    clean_tags = [str(tag or "").strip() for tag in list(tags or []) if str(tag or "").strip()]
    if not clean_tags or not query_terms:
        return 0.0
    query_set = set(query_terms)
    compact_query = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", " ".join(query_terms).casefold())
    score = 0.0
    for tag in clean_tags:
        tag_terms = set(_tokens(tag))
        overlap = len(tag_terms & query_set)
        if overlap:
            score += min(1.2, overlap / max(1, len(tag_terms)) * 1.2)
        compact_tag = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", tag.casefold())
        if compact_tag and compact_tag in compact_query:
            score += 0.8
    return min(3.0, score)


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _json_string_list(value: Any) -> list[str]:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item).strip() for item in decoded if str(item).strip()]


def _rank_document_cards(
    cards: list[dict[str, Any]],
    query_terms: list[str],
    *,
    db_path: Path | None = None,
    query: str = "",
    provider: EmbeddingProvider | None = None,
) -> list[dict[str, Any]]:
    dense_scores = (
        _document_card_dense_scores(db_path, cards, query=query, provider=provider)
        if db_path is not None and provider is not None and query.strip()
        else {}
    )
    ranked: list[dict[str, Any]] = []
    for card in cards:
        profile_text = str(card.get("profile_text", ""))
        title = str(card.get("title", ""))
        tag_text = str(card.get("tag_text", ""))
        tag_score = _tag_match_score(card.get("tags", []), query_terms)
        lexical_score = _document_lexical_score(
            {"profile_text": profile_text, "title": title},
            query_terms,
        )
        ranked_card = dict(card)
        dense_score = float(dense_scores.get(str(card.get("doc_id", "")), 0.0) or 0.0)
        # Lexical matches are useful for exact terminology; the compact card
        # vector supplies semantic recall without opening every source span.
        ranked_card["card_dense_score"] = round(dense_score, 6)
        ranked_card["tag_score"] = round(tag_score, 6)
        ranked_card["score"] = round(
            float(lexical_score) + max(0.0, dense_score) * 1.25 + tag_score * 0.85,
            6,
        )
        ranked_card["matched_terms"] = _unique_strings(
            [
                *_matched_terms_in_text(profile_text, query_terms),
                *_matched_terms_in_text(tag_text, query_terms),
            ]
        )
        ranked.append(ranked_card)
    ranked.sort(key=lambda card: (-float(card.get("score", 0.0)), str(card.get("doc_id", ""))))
    return ranked


def _document_card_dense_scores(
    db_path: Path,
    cards: list[dict[str, Any]],
    *,
    query: str,
    provider: EmbeddingProvider,
) -> dict[str, float]:
    """Read or create vectors for the small document catalogue only.

    A card embedding is derived from a title, headings/keywords and an
    extractive source-grounded summary.  It is never an embedding of the
    191k-span evidence corpus, and its digest ties it to one source document
    so an edit invalidates only that card.
    """

    dimensions = int(getattr(provider, "dimensions", 0) or 0)
    provider_key = str(getattr(provider, "cache_key", "") or "").strip()
    if not provider_key or dimensions <= 0 or dimensions > 4_096 or not cards:
        return {}
    try:
        query_vector = embed_query(provider, query)
    except Exception:
        return {}
    if len(query_vector) != dimensions:
        return {}
    expected = {
        str(card.get("doc_id", "")): _document_card_embedding_digest(card)
        for card in cards
        if str(card.get("doc_id", "")) and str(card.get("source_digest", ""))
    }
    if not expected:
        return {}
    cached: dict[str, list[float]] = {}
    try:
        with sqlite3.connect(db_path, timeout=5.0) as connection:
            rows = connection.execute(
                f"""
                select doc_id, source_digest, embedding_json
                from document_card_embeddings
                where provider = ? and dimensions = ?
                  and doc_id in ({', '.join('?' for _ in expected)})
                """,
                (provider_key, dimensions, *expected),
            ).fetchall()
            for doc_id, source_digest, embedding_json in rows:
                if expected.get(str(doc_id)) != str(source_digest):
                    continue
                try:
                    vector = [float(value) for value in json.loads(str(embedding_json))]
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if len(vector) == dimensions:
                    cached[str(doc_id)] = vector
    except sqlite3.Error:
        return {}

    pending = [card for card in cards if str(card.get("doc_id", "")) not in cached]
    # The catalogue is intentionally bounded to one row per source file. Do
    # not let a malformed database convert this serving path into a large
    # foreground model job.
    if pending and len(pending) <= 2_000:
        try:
            vectors = provider.embed_texts(
                [str(card.get("semantic_profile_text", card.get("profile_text", ""))) for card in pending]
            )
            writes: list[tuple[str, str, int, str, str]] = []
            for card, vector in zip(pending, vectors):
                values = [float(value) for value in vector]
                if len(values) != dimensions:
                    continue
                doc_id = str(card.get("doc_id", ""))
                cached[doc_id] = values
                writes.append(
                    (
                        doc_id,
                        provider_key,
                        dimensions,
                        str(card.get("source_digest", "")),
                        json.dumps(values, separators=(",", ":")),
                    )
                )
            if writes:
                with sqlite3.connect(db_path, timeout=5.0) as connection:
                    connection.executemany(
                        """
                        insert into document_card_embeddings(
                          doc_id, provider, dimensions, source_digest, embedding_json
                        ) values (?, ?, ?, ?, ?)
                        on conflict(doc_id, provider, dimensions) do update set
                          source_digest = excluded.source_digest,
                          embedding_json = excluded.embedding_json,
                          updated_at = current_timestamp
                        """,
                        writes,
                    )
                    connection.commit()
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            # Sparse card ranking remains a correct source-grounded fallback.
            pass
    return {
        doc_id: cosine_similarity(query_vector, vector)
        for doc_id, vector in cached.items()
        if len(vector) == len(query_vector)
    }


def _count_evidence_spans(db_path: Path) -> int:
    try:
        with sqlite3.connect(db_path) as connection:
            return int(connection.execute("select count(*) from evidence_spans").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def _load_evidence_spans(db_path: Path) -> dict[str, dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        publication_year_expr = (
            "publication_year" if _has_column(connection, "evidence_spans", "publication_year") else "null as publication_year"
        )
        rows = connection.execute(
            f"""
            select
              evidence_id,
              doc_id,
              title,
              doi,
              source_url,
              {publication_year_expr},
              html_path,
              html_anchor,
              section,
              section_kind,
              block_id,
              block_type,
              sentence_index,
              char_start,
              char_end,
              text
            from evidence_spans
            order by evidence_id
            """
        ).fetchall()
    return {str(row["evidence_id"]): dict(row) for row in rows}


def _load_evidence_by_ids(
    db_path: Path,
    evidence_ids: list[str],
    *,
    filters: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Load a bounded evidence candidate set, never the whole library."""

    normalized_ids = list(dict.fromkeys(str(value).strip() for value in evidence_ids if str(value).strip()))
    if not normalized_ids:
        return {}
    rows: list[sqlite3.Row] = []
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            publication_year_expr = (
                "publication_year" if _has_column(connection, "evidence_spans", "publication_year") else "null as publication_year"
            )
            for offset in range(0, len(normalized_ids), 800):
                batch = normalized_ids[offset : offset + 800]
                placeholders = ", ".join("?" for _ in batch)
                rows.extend(
                    connection.execute(
                        f"""
                        select
                          evidence_id, doc_id, title, doi, source_url,
                          {publication_year_expr}, html_path, html_anchor,
                          section, section_kind, block_id, block_type,
                          sentence_index, char_start, char_end, text
                        from evidence_spans
                        where evidence_id in ({placeholders})
                        """,
                        batch,
                    ).fetchall()
                )
    except sqlite3.Error:
        return {}
    result = {str(row["evidence_id"]): dict(row) for row in rows}
    return _filter_evidence_spans(result, filters)


def _load_block_context_rows(
    db_path: Path,
    candidates: dict[str, dict[str, Any]],
    *,
    filters: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Fetch only complete parent blocks needed for bounded context expansion."""

    if not candidates:
        return {}
    block_ids = list(
        dict.fromkeys(
            str(row.get("block_id", "")).strip()
            for row in candidates.values()
            if str(row.get("block_id", "")).strip()
        )
    )
    context = {str(evidence_id): dict(row) for evidence_id, row in candidates.items()}
    if not block_ids:
        return context
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            publication_year_expr = (
                "publication_year" if _has_column(connection, "evidence_spans", "publication_year") else "null as publication_year"
            )
            for offset in range(0, len(block_ids), 800):
                batch = block_ids[offset : offset + 800]
                placeholders = ", ".join("?" for _ in batch)
                for row in connection.execute(
                    f"""
                    select
                      evidence_id, doc_id, title, doi, source_url,
                      {publication_year_expr}, html_path, html_anchor,
                      section, section_kind, block_id, block_type,
                      sentence_index, char_start, char_end, text
                    from evidence_spans
                    where block_id in ({placeholders})
                    """,
                    batch,
                ):
                    context[str(row["evidence_id"])] = dict(row)
    except sqlite3.Error:
        return context
    return _filter_evidence_spans(context, filters)


def _expand_neighbor_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    *,
    seed_limit: int = 24,
) -> list[dict[str, Any]]:
    """Let answer-bearing sentences next to a strong lead sentence reach reranking.

    Abstracts often introduce a finding in one sentence and enumerate the
    actual answer in the next.  Dense/FTS recall can retrieve only the lead;
    this bounded one-hop expansion preserves sentence-level evidence identity
    while giving the final reranker a chance to judge the neighboring sentence.
    """

    if not candidates or not rows or not _needs_neighbor_expansion(query):
        return candidates
    ranked_seeds = LexicalReranker().rerank(query, candidates)[: max(1, int(seed_limit))]
    by_block_position: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows.values():
        block_id = str(row.get("block_id", "")).strip()
        if not block_id:
            continue
        try:
            position = int(row.get("sentence_index", 0))
        except (TypeError, ValueError):
            continue
        by_block_position[(block_id, position)] = row

    expanded_by_id = {str(item.get("evidence_id", "")): dict(item) for item in candidates}
    for seed_rank, seed in enumerate(ranked_seeds, start=1):
        block_id = str(seed.get("block_id", "")).strip()
        try:
            position = int(seed.get("sentence_index", 0))
        except (TypeError, ValueError):
            continue
        if not block_id:
            continue
        for neighbor_position in (position - 1, position + 1):
            row = by_block_position.get((block_id, neighbor_position))
            if row is None:
                continue
            evidence_id = str(row.get("evidence_id", ""))
            neighbor = expanded_by_id.setdefault(evidence_id, dict(row))
            routes = {str(route) for route in neighbor.get("routes", []) or []}
            routes.add("neighbor-context")
            neighbor["routes"] = sorted(routes)
            neighbor.setdefault("fts_score", 0.0)
            neighbor.setdefault("dense_score", 0.0)
            neighbor["context_score"] = max(
                float(neighbor.get("context_score", 0.0)),
                max(0.2, 1.0 - seed_rank / 30.0),
            )
            if not str(neighbor.get("rerank_context_text", "")).strip():
                ordered_text = (
                    [str(row.get("text", "")), str(seed.get("text", ""))]
                    if neighbor_position < position
                    else [str(seed.get("text", "")), str(row.get("text", ""))]
                )
                neighbor["rerank_context_text"] = " ".join(part.strip() for part in ordered_text if part.strip())
            neighbor_of = {str(value) for value in neighbor.get("neighbor_of", []) or []}
            neighbor_of.add(str(seed.get("evidence_id", "")))
            neighbor["neighbor_of"] = sorted(value for value in neighbor_of if value)
    return list(expanded_by_id.values())


def _needs_neighbor_expansion(query: str) -> bool:
    value = str(query or "").casefold()
    cues = (
        "properties",
        "characteristics",
        "features",
        "types",
        "categories",
        "mechanisms",
        "factors",
        "components",
        "elements",
        "哪些属性",
        "哪些特征",
        "哪些类型",
        "哪些机制",
        "哪些因素",
        "哪些组成",
    )
    return any(cue in value for cue in cues)


def _filter_evidence_spans(
    rows: dict[str, dict[str, Any]],
    filters: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    year_min = _int_or_none(filters.get("year_min"))
    section_kinds = _string_set(filters.get("section_kinds"))
    doc_ids = _string_set(filters.get("doc_ids"))
    if "doc_ids" in filters and not doc_ids:
        return {}
    filtered = rows
    if doc_ids:
        filtered = {
            evidence_id: row
            for evidence_id, row in filtered.items()
            if str(row.get("doc_id", "")).strip().lower() in doc_ids
        }
    if year_min is None:
        pass
    else:
        filtered = {
            evidence_id: row
            for evidence_id, row in filtered.items()
            if (publication_year := _int_or_none(row.get("publication_year"))) is not None
            and publication_year >= year_min
        }
    if section_kinds:
        filtered = {
            evidence_id: row
            for evidence_id, row in filtered.items()
            if str(row.get("section_kind", "")).strip().lower() in section_kinds
        }
    return filtered


def _has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return column_name in {str(row[1]) for row in connection.execute(f"pragma table_info({table_name})")}


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_set(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        raw_values = value.split(",")
    else:
        try:
            raw_values = list(value)
        except TypeError:
            raw_values = [value]
    return {str(item).strip().lower() for item in raw_values if str(item).strip()}


def _document_profiles(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows.values():
        doc_id = str(row.get("doc_id", "")).strip()
        if doc_id:
            grouped[doc_id].append(row)

    documents: list[dict[str, Any]] = []
    for doc_id, doc_rows in grouped.items():
        doc_rows.sort(
            key=lambda row: (
                _section_priority(str(row.get("section_kind", ""))),
                _int_or_none(row.get("sentence_index")) or 0,
                str(row.get("evidence_id", "")),
            )
        )
        first = doc_rows[0]
        title = str(first.get("title", ""))
        doi = str(first.get("doi", ""))
        publication_year = first.get("publication_year")
        abstract_rows = [row for row in doc_rows if str(row.get("section_kind", "")).lower() == "abstract"]
        priority_rows = (abstract_rows or doc_rows)[:5]
        section_names = []
        seen_sections: set[str] = set()
        for row in doc_rows:
            section = str(row.get("section", "")).strip()
            normalized = section.lower()
            if section and normalized not in seen_sections:
                seen_sections.add(normalized)
                section_names.append(section)
            if len(section_names) >= 8:
                break
        text_parts = [
            title,
            " ".join(section_names),
            " ".join(str(row.get("text", "")).strip() for row in priority_rows if str(row.get("text", "")).strip()),
        ]
        profile_text = " ".join(part for part in text_parts if part).strip()
        documents.append(
            {
                "doc_id": doc_id,
                "title": title,
                "doi": doi,
                "publication_year": publication_year,
                "profile_text": profile_text,
                "profile_span_count": len(priority_rows),
                "span_count": len(doc_rows),
            }
        )
    documents.sort(key=lambda document: str(document.get("doc_id", "")))
    return documents


def _section_priority(section_kind: str) -> int:
    priorities = {
        "abstract": 0,
        "introduction": 1,
        "results": 2,
        "discussion": 3,
        "methods": 4,
        "conclusion": 5,
    }
    return priorities.get(section_kind.strip().lower(), 9)


def _document_lexical_score(document: dict[str, Any], query_terms: list[str]) -> float:
    profile_counts = Counter(_tokens(str(document.get("profile_text", ""))))
    title_counts = Counter(_tokens(str(document.get("title", ""))))
    score = 0.0
    for term in query_terms:
        score += profile_counts.get(term, 0)
        score += title_counts.get(term, 0) * 0.8
    return score


def _matched_terms_in_text(text: str, query_terms: list[str]) -> list[str]:
    tokens = set(_tokens(text))
    return [term for term in query_terms if term in tokens]


def _fts_candidates(db_path: Path, query_terms: list[str], *, limit: int) -> list[tuple[str, float]]:
    query = _fts_match_query(query_terms)
    if not query:
        return []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            select evidence_id, bm25(evidence_spans_fts) as rank
            from evidence_spans_fts
            where evidence_spans_fts match ?
            order by rank
            limit ?
            """,
            (query, max(0, int(limit))),
        ).fetchall()
    return [(str(evidence_id), 1.0 / (1.0 + abs(float(rank)))) for evidence_id, rank in rows]


def _fts_candidates_with_documents(
    db_path: Path,
    query_terms: list[str],
    *,
    limit: int,
    filters: dict[str, Any],
    doc_ids: set[str] | None = None,
) -> list[tuple[str, str, float]]:
    """Read sparse candidates and their document IDs directly from FTS.

    FTS is intentionally used before the evidence table is read.  This keeps
    sparse recall at SQLite scale and lets the document-card layer choose which
    original spans deserve materialization.
    """

    query = _fts_match_query(query_terms)
    if not query or int(limit) <= 0:
        return []
    clauses = ["evidence_spans_fts match ?"]
    parameters: list[Any] = [query]
    normalized_doc_ids = sorted(str(doc_id).strip() for doc_id in (doc_ids or set()) if str(doc_id).strip())
    if normalized_doc_ids:
        clauses.append(f"doc_id in ({', '.join('?' for _ in normalized_doc_ids)})")
        parameters.extend(normalized_doc_ids)
    try:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                f"""
                select evidence_id, doc_id, bm25(evidence_spans_fts) as rank
                from evidence_spans_fts
                where {' and '.join(clauses)}
                order by rank
                limit ?
                """,
                (*parameters, max(0, int(limit))),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        (str(evidence_id), str(doc_id), 1.0 / (1.0 + abs(float(rank))))
        for evidence_id, doc_id, rank in rows
    ]


def _fts_match_query(query_terms: list[str]) -> str:
    return " OR ".join(_fts_match_term(term) for term in query_terms if term)


def _fts_match_term(term: str) -> str:
    if len(term) >= 4 and term.isalnum():
        return f"{term}*"
    return term


def _apply_per_document_limit(
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


def _expand_hits_to_parent_blocks(
    hits: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    spans_by_block = _spans_by_block(rows)
    expanded_hits: list[dict[str, Any]] = []
    for hit in hits:
        block_id = str(hit.get("block_id", "")).strip()
        block_spans = spans_by_block.get(block_id, [])
        if not block_spans:
            expanded_hits.append(hit)
            continue
        parent_text = _parent_block_text(block_spans)
        expanded = dict(hit)
        expanded["span_text"] = str(hit.get("text", ""))
        expanded["parent_text"] = parent_text
        expanded["parent_block_id"] = block_id
        expanded["parent_evidence_ids"] = [str(span.get("evidence_id", "")) for span in block_spans]
        expanded["text"] = parent_text
        expanded_hits.append(expanded)
    return expanded_hits


def _attach_sentence_context(
    hits: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach exact one-hop context without changing the citation boundary.

    The hit remains the selected sentence and keeps its own evidence ID and
    anchor.  Context is read only from the same indexed parent block, so a
    paragraph boundary can never make an unrelated sentence look adjacent.
    """

    if not hits:
        return []
    spans_by_block = _spans_by_block(rows)
    enriched: list[dict[str, Any]] = []
    for raw_hit in hits:
        hit = dict(raw_hit)
        evidence_id = str(hit.get("evidence_id", ""))
        source = rows.get(evidence_id, hit)
        block_id = str(source.get("block_id", hit.get("block_id", ""))).strip()
        block_spans = spans_by_block.get(block_id, [])
        current_index = next(
            (index for index, span in enumerate(block_spans) if str(span.get("evidence_id", "")) == evidence_id),
            -1,
        )
        before = block_spans[current_index - 1] if current_index > 0 else None
        after = block_spans[current_index + 1] if current_index >= 0 and current_index + 1 < len(block_spans) else None
        matched_text = _context_sentence_text(source.get("text", hit.get("text", "")))
        hit["matched_text"] = matched_text
        hit["context_before"] = _context_sentence_text(before.get("text", "")) if before else ""
        hit["context_after"] = _context_sentence_text(after.get("text", "")) if after else ""
        hit["context_before_evidence_id"] = str(before.get("evidence_id", "")) if before else ""
        hit["context_after_evidence_id"] = str(after.get("evidence_id", "")) if after else ""
        enriched.append(hit)
    return enriched


def _context_sentence_text(value: Any, *, max_chars: int = 1_200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _spans_by_block(rows: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows.values():
        block_id = str(row.get("block_id", "")).strip()
        if not block_id:
            continue
        grouped.setdefault(block_id, []).append(row)
    for block_rows in grouped.values():
        block_rows.sort(
            key=lambda row: (
                _int_or_none(row.get("char_start")) if _int_or_none(row.get("char_start")) is not None else 0,
                _int_or_none(row.get("sentence_index")) if _int_or_none(row.get("sentence_index")) is not None else 0,
                str(row.get("evidence_id", "")),
            )
        )
    return grouped


def _parent_block_text(block_spans: list[dict[str, Any]]) -> str:
    return " ".join(str(span.get("text", "")).strip() for span in block_spans if str(span.get("text", "")).strip())


def _route_counts(hits: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        for route in hit.get("routes", []) or []:
            route_name = str(route).strip()
            if not route_name:
                continue
            counts[route_name] = counts.get(route_name, 0) + 1
    return dict(sorted(counts.items()))


def query_lazy_graph_neighborhood(
    db_path: str | Path,
    document_ids: set[str] | list[str],
    *,
    limit: int = 24,
) -> dict[str, Any]:
    """Return only graph relations adjacent to selected catalogue documents.

    The durable graph stores document/section/concept structure.  This helper
    never scans all evidence and never materializes a global LLM-derived graph;
    it is a query-local map with source anchors for the chosen documents.
    """

    doc_ids = sorted({str(value).strip() for value in document_ids if str(value).strip()})
    if not doc_ids or int(limit) <= 0:
        return {"documents": 0, "nodes": [], "edges": []}
    node_ids = [f"document:{doc_id}" for doc_id in doc_ids]
    try:
        with sqlite3.connect(Path(db_path), timeout=5.0) as connection:
            connection.row_factory = sqlite3.Row
            edges = connection.execute(
                f"""
                select edge_id, source_node_id, target_node_id, edge_type, weight,
                       anchor_evidence_ids_json, metadata_json
                from knowledge_graph_edges
                where source_node_id in ({', '.join('?' for _ in node_ids)})
                   or target_node_id in ({', '.join('?' for _ in node_ids)})
                order by case edge_type when 'mentions' then 0 when 'contains' then 1 else 2 end,
                         weight desc, edge_id
                limit ?
                """,
                (*node_ids, *node_ids, max(1, int(limit))),
            ).fetchall()
            related_nodes = sorted(
                {
                    str(row["source_node_id"])
                    for row in edges
                }
                | {
                    str(row["target_node_id"])
                    for row in edges
                }
            )
            nodes = connection.execute(
                f"""
                select node_id, node_type, label, metadata_json
                from knowledge_graph_nodes
                where node_id in ({', '.join('?' for _ in related_nodes)})
                order by node_type, label, node_id
                """,
                related_nodes,
            ).fetchall() if related_nodes else []
    except sqlite3.Error:
        return {"documents": len(doc_ids), "nodes": [], "edges": []}
    return {
        "documents": len(doc_ids),
        "nodes": [
            {
                **dict(row),
                "metadata": _json_mapping(row["metadata_json"]),
            }
            for row in nodes
        ],
        "edges": [
            {
                **dict(row),
                "anchor_evidence_ids": _json_string_list(row["anchor_evidence_ids_json"]),
                "metadata": _json_mapping(row["metadata_json"]),
            }
            for row in edges
        ],
    }


def _json_mapping(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _append_lazy_graph_trace(
    trace: list[dict[str, Any]] | None,
    db_path: Path,
    document_ids: set[str],
    *,
    query: str,
    started_at: float,
) -> None:
    if trace is None or not document_ids:
        return
    graph = query_lazy_graph_neighborhood(db_path, document_ids, limit=24)
    trace.append(
        {
            "stage": "lazy_graph_neighborhood",
            "status": "ok" if graph["edges"] else "empty",
            "query": query,
            "documents": int(graph["documents"]),
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "anchored_edges": sum(1 for edge in graph["edges"] if edge.get("anchor_evidence_ids")),
            "elapsed_ms": round((perf_counter() - started_at) * 1000.0, 3),
        }
    )


def _append_retrieval_trace(
    trace: list[dict[str, Any]] | None,
    *,
    query: str,
    query_terms: list[str],
    total_rows: int,
    filtered_rows: int,
    initial_limit: int,
    dense_limit: int,
    paper_recall_limit: int = 0,
    paper_recalled_documents: int = 0,
    per_document_limit: int,
    fts_candidates: int,
    dense_candidates: int,
    unique_candidates: int,
    reranked_candidates: int,
    returned_hits: int,
    route_counts: dict[str, int],
    started_at: float,
    status: str,
    dense_backend: str = "python",
    strategy: str = "span-first",
    catalog_documents: int = 0,
    catalog_selected_documents: int = 0,
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "stage": "search",
            "status": status,
            "strategy": strategy,
            "query_route": str(classify_evidence_query(query).get("route", "evidence-answer")),
            "dense_backend": dense_backend,
            "query": query,
            "query_terms": list(query_terms),
            "total_rows": int(total_rows),
            "filtered_rows": int(filtered_rows),
            "initial_limit": int(initial_limit),
            "dense_limit": int(dense_limit),
            "paper_recall_limit": int(paper_recall_limit),
            "paper_recalled_documents": int(paper_recalled_documents),
            "catalog_documents": int(catalog_documents),
            "catalog_selected_documents": int(catalog_selected_documents),
            "per_document_limit": int(per_document_limit),
            "fts_candidates": int(fts_candidates),
            "dense_candidates": int(dense_candidates),
            "unique_candidates": int(unique_candidates),
            "reranked_candidates": int(reranked_candidates),
            "returned_hits": int(returned_hits),
            "route_counts": dict(sorted(route_counts.items())),
            "elapsed_ms": round((perf_counter() - started_at) * 1000.0, 3),
        }
    )


def _append_document_card_recall_trace(
    trace: list[dict[str, Any]] | None,
    *,
    query: str,
    query_terms: list[str],
    total_documents: int,
    selected_documents: list[dict[str, Any]],
    started_at: float,
    status: str,
) -> None:
    """Record the compact catalogue stage separately from evidence retrieval."""

    if trace is None:
        return
    trace.append(
        {
            "stage": "document_card_recall",
            "status": status,
            "query": query,
            "query_terms": list(query_terms),
            "query_route": str(classify_evidence_query(query).get("route", "evidence-answer")),
            "total_documents": int(total_documents),
            "selected_documents": len(selected_documents),
            "selected_doc_ids": [str(document.get("doc_id", "")) for document in selected_documents],
            "top_documents": [
                {
                    "rank": int(document.get("rank", index)),
                    "doc_id": str(document.get("doc_id", "")),
                    "title": str(document.get("title", "")),
                    "score": float(document.get("score", 0.0) or 0.0),
                    "matched_terms": list(document.get("matched_terms", []) or []),
                    "routes": list(document.get("routes", []) or []),
                }
                for index, document in enumerate(selected_documents[:10], start=1)
            ],
            "elapsed_ms": round((perf_counter() - started_at) * 1000.0, 3),
        }
    )


def _append_paper_recall_trace(
    trace: list[dict[str, Any]] | None,
    *,
    query: str,
    query_terms: list[str],
    total_documents: int,
    selected_documents: list[dict[str, Any]],
    started_at: float,
    status: str,
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "stage": "paper_recall",
            "status": status,
            "query": query,
            "query_terms": list(query_terms),
            "total_documents": int(total_documents),
            "selected_documents": len(selected_documents),
            "selected_doc_ids": [str(document.get("doc_id", "")) for document in selected_documents],
            "top_documents": [
                {
                    "rank": int(document.get("rank", index)),
                    "doc_id": str(document.get("doc_id", "")),
                    "title": str(document.get("title", "")),
                    "score": float(document.get("score", 0.0) or 0.0),
                    "matched_terms": list(document.get("matched_terms", []) or []),
                }
                for index, document in enumerate(selected_documents[:10], start=1)
            ],
            "elapsed_ms": round((perf_counter() - started_at) * 1000.0, 3),
        }
    )


def _normalized_context_mode(context_mode: str) -> str:
    mode = str(context_mode or "sentence").strip().lower()
    if mode in {"sentence", "span"}:
        return "sentence"
    if mode in {"block", "parent"}:
        return "block"
    raise ValueError(f"Unsupported context_mode: {context_mode}")


def _score_row(row: dict[str, Any], query_terms: list[str]) -> tuple[float, list[str]]:
    text_counts = Counter(_tokens(str(row.get("text", ""))))
    title_counts = Counter(_tokens(str(row.get("title", ""))))
    section_counts = Counter(_tokens(str(row.get("section", ""))))
    matched_terms: list[str] = []
    score = 0.0
    for term in query_terms:
        term_score = (
            text_counts.get(term, 0)
            + title_counts.get(term, 0) * 0.4
            + section_counts.get(term, 0) * 0.25
        )
        if term_score > 0:
            matched_terms.append(term)
            score += term_score
    return score, matched_terms


def _query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in _tokens(query):
        if term in STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _tokens(value: str) -> list[str]:
    return lexical_tokens(value)
