from pathlib import Path
import sqlite3

import pytest

import scansci_html.retrieval as retrieval
from scansci_html.evidence_store import (
    build_library_overview,
    index_evidence_library,
    index_markdown_library,
)
from scansci_html.retrieval import search_evidence_store
from scansci_html.vector_index import (
    load_embedding_cache_rows,
    prewarm_embedding_cache,
    vector_cache_status,
)


def test_search_evidence_store_returns_reranked_sentence_hits_with_routes(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper_a.html").write_text(
        """
        <article class="paper" data-doi="10.1234/a">
          <h1>Cortical Model Paper</h1>
          <h2>Results</h2>
          <p id="a-results">Model predictions explained cortical activity in language regions. The baseline captured motor activity.</p>
        </article>
        """,
        encoding="utf-8",
    )
    (library / "paper_b.html").write_text(
        """
        <article class="paper" data-doi="10.1234/b">
          <h1>Fermentation Paper</h1>
          <h2>Discussion</h2>
          <p id="b-discussion">Fermentation performance depended on immobilized fungal biomass.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    hits = search_evidence_store(db_path, "cortical activity language model", limit=3)

    assert [hit["evidence_id"] for hit in hits[:2]] == [
        "10.1234_a.s0001",
        "10.1234_a.s0002",
    ]
    assert hits[0]["text"] == "Model predictions explained cortical activity in language regions."
    assert hits[0]["section_kind"] == "results"
    assert hits[0]["score"] > hits[1]["score"]
    assert "fts" in hits[0]["routes"]
    assert "dense" in hits[0]["routes"]
    assert hits[0]["matched_terms"] == ["cortical", "activity", "language", "model"]


def test_search_evidence_store_can_append_retrieval_trace(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        """
        <article class="paper" data-doi="10.1234/trace">
          <h1>Trace Paper</h1>
          <h2>Results</h2>
          <p id="results-p1">Cortical activity changed after treatment.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    trace = []
    hits = search_evidence_store(
        db_path,
        "cortical activity treatment",
        limit=1,
        initial_limit=5,
        per_document_limit=1,
        trace=trace,
    )

    assert [hit["evidence_id"] for hit in hits] == ["10.1234_trace.s0001"]
    assert len(trace) == 1
    event = trace[0]
    assert event["stage"] == "search"
    assert event["query"] == "cortical activity treatment"
    assert event["query_terms"] == ["cortical", "activity", "treatment"]
    assert event["fts_candidates"] == 1
    assert event["dense_candidates"] == 1
    assert event["unique_candidates"] == 1
    assert event["reranked_candidates"] == 1
    assert event["returned_hits"] == 1
    assert event["route_counts"] == {"dense": 1, "fts": 1}
    assert event["elapsed_ms"] >= 0.0


def test_search_evidence_store_can_start_with_paper_level_recall(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "target.html").write_text(
        """
        <article class="paper" data-doi="10.1234/target">
          <h1>Language Modeling Paper</h1>
          <h2>Abstract</h2>
          <p id="target-abstract">Language modeling experiments motivated the cortical activity analysis.</p>
          <h2>Results</h2>
          <p id="target-results">The model explained cortical activity in language regions.</p>
        </article>
        """,
        encoding="utf-8",
    )
    (library / "distractor.html").write_text(
        """
        <article class="paper" data-doi="10.1234/distractor">
          <h1>Motor Control Paper</h1>
          <h2>Results</h2>
          <p id="distractor-results">Cortical activity changed during movement tasks.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    trace = []
    hits = search_evidence_store(
        db_path,
        "language modeling cortical activity",
        limit=5,
        paper_recall_limit=1,
        trace=trace,
    )

    assert {hit["doc_id"] for hit in hits} == {"10.1234_target"}
    assert hits[0]["paper_recall_rank"] == 1
    assert trace[0]["stage"] == "paper_recall"
    assert trace[0]["selected_doc_ids"] == ["10.1234_target"]
    assert trace[-1]["paper_recalled_documents"] == 1


def test_search_evidence_store_can_expand_hits_to_parent_block_context(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        """
        <article class="paper" data-doi="10.1234/context">
          <h1>Context Paper</h1>
          <h2>Results</h2>
          <p id="results-p1">The cohort included 120 participants. Treatment increased cortical activity in language regions.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    sentence_hits = search_evidence_store(db_path, "cortical activity language", limit=1)
    block_hits = search_evidence_store(
        db_path,
        "cortical activity language",
        limit=1,
        context_mode="block",
    )

    assert sentence_hits[0]["text"] == "Treatment increased cortical activity in language regions."
    assert block_hits[0]["evidence_id"] == sentence_hits[0]["evidence_id"]
    assert block_hits[0]["span_text"] == "Treatment increased cortical activity in language regions."
    assert block_hits[0]["text"] == (
        "The cohort included 120 participants. "
        "Treatment increased cortical activity in language regions."
    )
    assert block_hits[0]["parent_text"] == block_hits[0]["text"]
    assert block_hits[0]["parent_block_id"] == "10.1234_context:results-p1"
    assert block_hits[0]["parent_evidence_ids"] == [
        "10.1234_context.s0001",
        "10.1234_context.s0002",
    ]


def test_search_evidence_store_returns_one_hop_sentence_context(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        """
        <article class="paper" data-doi="10.1234/neighbor-context">
          <h1>Context Paper</h1>
          <h2>Results</h2>
          <p id="results-p1">The cohort included 120 participants. Treatment increased cortical activity in language regions. The follow-up assessment found the effect persisted.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    hits = search_evidence_store(db_path, "treatment increased cortical activity", limit=1)

    assert hits[0]["evidence_id"] == "10.1234_neighbor-context.s0002"
    assert hits[0]["matched_text"] == hits[0]["text"]
    assert hits[0]["context_before"] == "The cohort included 120 participants."
    assert hits[0]["context_before_evidence_id"] == "10.1234_neighbor-context.s0001"
    assert hits[0]["context_after"] == "The follow-up assessment found the effect persisted."
    assert hits[0]["context_after_evidence_id"] == "10.1234_neighbor-context.s0003"


def test_search_evidence_store_applies_per_document_limit_after_reranking(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper_a.html").write_text(
        """
        <article class="paper" data-doi="10.1234/a">
          <h1>Repeated Evidence Paper</h1>
          <h2>Results</h2>
          <p id="a-results">Cortical activity increased after stimulation. Cortical activity remained elevated at follow up.</p>
        </article>
        """,
        encoding="utf-8",
    )
    (library / "paper_b.html").write_text(
        """
        <article class="paper" data-doi="10.1234/b">
          <h1>Independent Evidence Paper</h1>
          <h2>Results</h2>
          <p id="b-results">Cortical activity changed in an independent cohort.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    hits = search_evidence_store(
        db_path,
        "cortical activity",
        limit=10,
        per_document_limit=1,
    )

    assert [hit["doc_id"] for hit in hits] == ["10.1234_a", "10.1234_b"]


def test_search_evidence_store_applies_publication_year_filter(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "old.html").write_text(
        """
        <article class="paper" data-doi="10.1234/old" data-publication-year="2019">
          <h1>Old Evidence Paper</h1>
          <h2>Results</h2>
          <p id="old-results">Cortical activity changed in the treatment cohort.</p>
        </article>
        """,
        encoding="utf-8",
    )
    (library / "new.html").write_text(
        """
        <article class="paper" data-doi="10.1234/new" data-publication-year="2024">
          <h1>New Evidence Paper</h1>
          <h2>Results</h2>
          <p id="new-results">Cortical activity changed in the treatment cohort.</p>
        </article>
        """,
        encoding="utf-8",
    )
    (library / "unknown.html").write_text(
        """
        <article class="paper" data-doi="10.1234/unknown">
          <h1>Unknown Year Paper</h1>
          <h2>Results</h2>
          <p id="unknown-results">Cortical activity changed in the treatment cohort.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    hits = search_evidence_store(
        db_path,
        "cortical activity treatment cohort",
        limit=10,
        filters={"year_min": 2020},
    )

    assert [hit["evidence_id"] for hit in hits] == ["10.1234_new.s0001"]
    assert hits[0]["publication_year"] == 2024


def test_search_evidence_store_applies_section_kind_filter(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        """
        <article class="paper" data-doi="10.1234/section-filter">
          <h1>Section Filter Paper</h1>
          <h2>Methods</h2>
          <p id="methods-p1">Samples were randomized before biomass measurement.</p>
          <h2>Results</h2>
          <p id="results-p1">Randomized samples produced higher biomass.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    hits = search_evidence_store(
        db_path,
        "randomized samples biomass",
        limit=10,
        filters={"section_kinds": ["methods"]},
    )

    assert [hit["evidence_id"] for hit in hits] == ["10.1234_section-filter.s0001"]
    assert hits[0]["section_kind"] == "methods"


def test_search_evidence_store_uses_provider_query_embedding_when_available(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "relevant.html").write_text(
        """
        <article class="paper" data-doi="10.1234/relevant">
          <h1>Relevant Paper</h1>
          <p id="relevant-p1">Relevant answer evidence appears in this paper.</p>
        </article>
        """,
        encoding="utf-8",
    )
    (library / "distractor.html").write_text(
        """
        <article class="paper" data-doi="10.1234/distractor">
          <h1>Distractor Paper</h1>
          <p id="distractor-p1">Distractor evidence appears in this paper.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    class PromptAwareProvider:
        dimensions = 2

        def embed_texts(self, texts):
            vectors = []
            for text in texts:
                if "Relevant answer evidence" in text:
                    vectors.append([1.0, 0.0])
                else:
                    vectors.append([0.0, 1.0])
            return vectors

        def embed_query(self, query):
            return [1.0, 0.0]

    hits = search_evidence_store(
        db_path,
        "needle",
        limit=1,
        initial_limit=0,
        embedding_provider=PromptAwareProvider(),
    )

    assert [hit["evidence_id"] for hit in hits] == ["10.1234_relevant.s0001"]


def test_search_evidence_store_returns_empty_for_empty_query(tmp_path: Path):
    db_path = tmp_path / "evidence.sqlite"
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper'><h1>Paper</h1><p>Evidence exists.</p></article>",
        encoding="utf-8",
    )
    index_evidence_library(library, db_path=db_path, min_sentence_length=1)

    assert search_evidence_store(db_path, "the and of", limit=5) == []


def test_default_local_embeddings_are_cached_with_sqlite_vec(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/vector'><h1>Vector cache</h1>"
        "<p>Traceable cortical evidence improves scientific decisions.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    first_trace = []
    first = search_evidence_store(db_path, "cortical evidence", limit=1, trace=first_trace)
    second_trace = []
    second = search_evidence_store(db_path, "cortical evidence", limit=1, trace=second_trace)

    assert [item["evidence_id"] for item in first] == [item["evidence_id"] for item in second]
    assert first_trace[-1]["dense_backend"] == "sqlite-vec"
    assert second_trace[-1]["dense_backend"] == "sqlite-vec"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("select count(*) from scansci_vector_cache_meta").fetchone()[0] == 1


def test_stable_local_neural_provider_reuses_sqlite_vec_cache(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/neural-cache'><h1>Neural cache</h1>"
        "<p>Clinical agent evidence is grounded in patient records.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    class StableProvider:
        dimensions = 2
        cache_key = "sentence-transformers:test-model"

        def __init__(self):
            self.document_batches = 0

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            self.document_batches += 1
            return [[1.0, 0.0] for _text in texts]

    provider = StableProvider()
    first_trace = []
    search_evidence_store(db_path, "clinical evidence", embedding_provider=provider, trace=first_trace)
    second_trace = []
    search_evidence_store(db_path, "clinical evidence", embedding_provider=provider, trace=second_trace)
    filtered_trace = []
    filtered = search_evidence_store(
        db_path,
        "clinical evidence",
        embedding_provider=provider,
        filters={"doc_ids": ["10.1234_neural-cache"]},
        trace=filtered_trace,
    )

    assert provider.document_batches == 1
    assert first_trace[-1]["dense_backend"] == "sqlite-vec"
    assert second_trace[-1]["dense_backend"] == "sqlite-vec"
    assert filtered_trace[-1]["dense_backend"] == "sqlite-vec"
    assert filtered[0]["doc_id"] == "10.1234_neural-cache"
    with sqlite3.connect(db_path) as connection:
        providers = {
            row[0]
            for row in connection.execute("select distinct provider from scansci_vector_cache_meta")
        }
    assert providers == {"sentence-transformers:test-model"}


def test_prewarm_embedding_cache_cancels_after_committed_batch_and_resumes(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/resume'><h1>Resume cache</h1>"
        "<p>First scientific evidence sentence. Second scientific evidence sentence. "
        "Third scientific evidence sentence.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    rows = load_embedding_cache_rows(db_path)

    class StableProvider:
        dimensions = 2
        cache_key = "sentence-transformers:resume-test"

        def __init__(self):
            self.document_sizes = []

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            self.document_sizes.append(len(texts))
            return [[1.0, 0.0] for _text in texts]

    completed = 0
    first_provider = StableProvider()

    def progress(done, _total):
        nonlocal completed
        completed = done

    first = prewarm_embedding_cache(
        db_path,
        rows,
        provider=first_provider,
        cache_batch_size=2,
        progress_callback=progress,
        cancel_requested=lambda: completed >= 2,
    )
    assert first["cancelled"] is True
    assert first["completed"] == 2
    assert first_provider.document_sizes == [2]

    second_provider = StableProvider()
    second = prewarm_embedding_cache(db_path, rows, provider=second_provider, cache_batch_size=2)
    assert second["cancelled"] is False
    assert second["completed"] == 3
    assert second["cached"] == 2
    assert second_provider.document_sizes == [1]


def test_vector_cache_status_counts_only_the_requested_model(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/status'><h1>Status</h1>"
        "<p>One complete evidence sentence.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    rows = load_embedding_cache_rows(db_path)

    class Provider:
        dimensions = 2

        def __init__(self, cache_key):
            self.cache_key = cache_key

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            return [[1.0, 0.0] for _text in texts]

    prewarm_embedding_cache(db_path, rows, provider=Provider("sentence-transformers:old"))
    before = vector_cache_status(
        db_path,
        provider="sentence-transformers:new",
        dimensions=2,
    )
    prewarm_embedding_cache(db_path, rows, provider=Provider("sentence-transformers:new"))
    after = vector_cache_status(
        db_path,
        provider="sentence-transformers:new",
        dimensions=2,
    )

    assert before["cached_vectors"] == 0
    assert before["other_cached_vectors"] == 1
    assert before["migration_required"] is True
    assert after["cached_vectors"] == 1
    assert after["other_cached_vectors"] == 1
    assert after["ready"] is True
    assert after["progress"] == 1.0


def test_vector_generation_reuses_unchanged_rows_and_switches_after_validation(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/generation'><h1>Generation</h1>"
        "<p>First stable evidence sentence. Second evidence sentence changes later.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    class Provider:
        dimensions = 2
        cache_key = "sentence-transformers:generation-test"

        def __init__(self):
            self.document_texts = []

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            self.document_texts.extend(texts)
            return [[1.0, 0.0] for _text in texts]

    first_provider = Provider()
    first_rows = load_embedding_cache_rows(db_path)
    first = prewarm_embedding_cache(db_path, first_rows, provider=first_provider)
    assert first["ready"] is True
    assert len(first_provider.document_texts) == 2

    changed_id = sorted(first_rows)[1]
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "update evidence_spans set text = ? where evidence_id = ?",
            ("Second evidence sentence now contains revised findings.", changed_id),
        )
        connection.commit()

    second_provider = Provider()
    second_rows = load_embedding_cache_rows(db_path)
    second = prewarm_embedding_cache(db_path, second_rows, provider=second_provider)
    status = vector_cache_status(
        db_path,
        provider=Provider.cache_key,
        dimensions=Provider.dimensions,
    )

    assert second["ready"] is True
    assert second["embedded"] == 1
    assert second["reused"] == 1
    assert second_provider.document_texts == ["Second evidence sentence now contains revised findings."]
    assert status["ready"] is True
    assert status["serving_stale"] is False
    with sqlite3.connect(db_path) as connection:
        states = [
            row[0]
            for row in connection.execute(
                """
                select state from scansci_vector_index_generations
                where logical_provider = ?
                order by created_at
                """,
                (Provider.cache_key,),
            )
        ]
    assert states == ["retired", "active"]


def test_incremental_library_change_reuses_vectors_from_stale_generation(tmp_path: Path):
    """Changing one source document must not re-embed the whole library.

    The incremental evidence index marks the old serving generation stale
    before the next prewarm.  That generation is still a valid source of
    digest-matched vectors, even though it must not be served while the new
    generation is incomplete.
    """

    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    first = library / "first.md"
    second = library / "second.md"
    first.write_text(
        "# First\n\n## Results\n\nA stable evidence sentence remains reusable.",
        encoding="utf-8",
    )
    second.write_text(
        "# Second\n\n## Results\n\nAn editable evidence sentence starts in its first version.",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_markdown_library(library, db_path=db_path, min_sentence_length=10, incremental=True)

    class Provider:
        dimensions = 2
        cache_key = "sentence-transformers:stale-generation-test"

        def __init__(self):
            self.document_texts: list[str] = []

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            self.document_texts.extend(str(text) for text in texts)
            return [[1.0, 0.0] for _text in texts]

    first_provider = Provider()
    first_rows = load_embedding_cache_rows(db_path)
    first_cache = prewarm_embedding_cache(db_path, first_rows, provider=first_provider)
    assert first_cache["ready"] is True
    assert len(first_provider.document_texts) == 2

    second.write_text(
        "# Second\n\n## Results\n\nAn editable evidence sentence now has a second version.",
        encoding="utf-8",
    )
    refreshed = index_markdown_library(library, db_path=db_path, min_sentence_length=10, incremental=True)
    assert refreshed["reused_documents"] == 1
    assert refreshed["changed_documents"] == 1

    second_provider = Provider()
    second_cache = prewarm_embedding_cache(
        db_path,
        load_embedding_cache_rows(db_path),
        provider=second_provider,
    )

    assert second_cache["ready"] is True
    assert second_cache["embedded"] == 1
    assert second_cache["reused"] == 1
    assert second_provider.document_texts == [
        "An editable evidence sentence now has a second version."
    ]


def test_vector_generation_rejects_invalid_document_vectors_without_replacing_active(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/invalid-vector'><h1>Invalid vector</h1>"
        "<p>Evidence must not activate an invalid vector generation.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    rows = load_embedding_cache_rows(db_path)

    class InvalidProvider:
        dimensions = 2
        cache_key = "sentence-transformers:invalid-vector-test"

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            return [[0.0, 0.0] for _text in texts]

    with pytest.raises(RuntimeError, match="向量缓存不可用"):
        prewarm_embedding_cache(db_path, rows, provider=InvalidProvider())

    status = vector_cache_status(
        db_path,
        provider=InvalidProvider.cache_key,
        dimensions=InvalidProvider.dimensions,
    )
    assert status["ready"] is False
    assert status["state"] == "failed"
    assert status["active_generation_id"] == ""


def test_prewarm_discovers_remote_embedding_dimensions_before_building_generation(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/remote-dimensions'><h1>Remote dimensions</h1>"
        "<p>A remote embedding endpoint may report its native vector size on the first request.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    rows = load_embedding_cache_rows(db_path)

    class Provider:
        cache_key = "siliconflow:https://api.siliconflow.cn/v1:BAAI/bge-m3"
        dimensions = 0

        def embed_query(self, _query):
            self.dimensions = 2
            return [1.0, 0.0]

        def embed_texts(self, texts):
            return [[1.0, 0.0] for _text in texts]

    result = prewarm_embedding_cache(db_path, rows, provider=Provider())

    assert result["ready"] is True
    assert result["total"] == len(rows)


def test_search_evidence_store_adds_adjacent_answer_sentence_before_reranking(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        "<article class='paper' data-doi='10.1234/neighbor'><h1>Neighbor recall</h1>"
        "<p>The study identifies fine-grained language representations. "
        "They include grammatical relationships, parts of speech, and higher-order syntax.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)

    class LeadOnlyProvider:
        dimensions = 2

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            return [[1.0, 0.0] if "identifies" in text else [0.0, 1.0] for text in texts]

    hits = search_evidence_store(
        db_path,
        "which fine-grained language properties does the study identify",
        limit=2,
        initial_limit=1,
        per_document_limit=0,
        embedding_provider=LeadOnlyProvider(),
    )

    assert len(hits) == 2
    assert "grammatical relationships" in hits[1]["text"]
    assert "neighbor-context" in hits[1]["routes"]
    assert hits[1]["neighbor_of"] == [hits[0]["evidence_id"]]


def test_search_evidence_store_uses_document_cards_before_bounded_raw_evidence_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    library = tmp_path / "library"
    library.mkdir()
    (library / "target.html").write_text(
        "<article class='paper' data-doi='10.1234/card-target'><h1>Language Cortex</h1>"
        "<h2>Results</h2><p>Language cortex activity increased after treatment.</p></article>",
        encoding="utf-8",
    )
    (library / "other.html").write_text(
        "<article class='paper' data-doi='10.1234/card-other'><h1>Fermentation</h1>"
        "<h2>Results</h2><p>Fungal biomass increased after treatment.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    build_library_overview(db_path)

    def reject_full_corpus_load(_db_path):
        raise AssertionError("document-card retrieval must not load every evidence span")

    monkeypatch.setattr(retrieval, "_load_evidence_spans", reject_full_corpus_load)
    trace: list[dict[str, object]] = []
    hits = search_evidence_store(
        db_path,
        "language cortex activity",
        limit=1,
        trace=trace,
    )

    assert [hit["evidence_id"] for hit in hits] == ["10.1234_card-target.s0001"]
    assert trace[0]["stage"] == "document_card_recall"
    assert trace[-1]["strategy"] == "document-card-first"
    assert trace[-1]["catalog_documents"] == 2
    assert trace[-1]["catalog_selected_documents"] >= 1


def test_zotero_tags_add_soft_document_card_recall(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "target.html").write_text(
        "<article class='paper' data-doi='10.1234/tag-target'><h1>Field observations</h1>"
        "<h2>Results</h2><p>Seasonal measurements were consistent across the sampled plots.</p></article>",
        encoding="utf-8",
    )
    (library / "other.html").write_text(
        "<article class='paper' data-doi='10.1234/tag-other'><h1>Laboratory study</h1>"
        "<h2>Results</h2><p>Laboratory measurements varied across the sampled plots.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    build_library_overview(db_path)
    with sqlite3.connect(db_path) as connection:
        target_doc_id = connection.execute(
            "select doc_id from source_documents where doi = ?",
            ("10.1234/tag-target",),
        ).fetchone()[0]
        connection.execute(
            "insert into document_tags(doc_id, tag, normalized_tag, source) values (?, ?, ?, ?)",
            (target_doc_id, "priority", "priority", "zotero"),
        )
        connection.commit()

    hits = search_evidence_store(db_path, "priority", limit=1)

    assert hits[0]["doc_id"] == target_doc_id
    assert "priority" in hits[0]["tags"]
    assert hits[0]["tag_score"] > 0
    assert "tag" in hits[0]["routes"]


def test_document_card_semantic_cache_routes_to_source_anchors(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "flora.html").write_text(
        "<article data-doi='10.1234/flora'><h1>Vegetation outcome</h1><h2>Results</h2>"
        "<p>Plant richness increased after grazing maintained the understory.</p></article>",
        encoding="utf-8",
    )
    (library / "wind.html").write_text(
        "<article data-doi='10.1234/wind'><h1>Wind outcome</h1><h2>Results</h2>"
        "<p>Bird nesting varied with turbine spacing.</p></article>",
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    build_library_overview(db_path)

    class CardSemanticProvider:
        dimensions = 2
        cache_key = "test:card-semantic"

        def embed_query(self, _query):
            return [1.0, 0.0]

        def embed_texts(self, texts):
            return [[1.0, 0.0] if "Vegetation" in text else [0.0, 1.0] for text in texts]

    trace: list[dict[str, object]] = []
    hits = search_evidence_store(
        db_path,
        "canopy response",
        limit=1,
        embedding_provider=CardSemanticProvider(),
        trace=trace,
    )

    assert hits[0]["doc_id"] == "10.1234_flora"
    card_trace = next(item for item in trace if item["stage"] == "document_card_recall")
    assert "document-card-dense" in card_trace["top_documents"][0]["routes"]
    assert any(item["stage"] == "lazy_graph_neighborhood" for item in trace)
