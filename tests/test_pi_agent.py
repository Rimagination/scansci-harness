from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque
import json
import os
import subprocess
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from types import SimpleNamespace

import pytest

import scansci_html.pi_agent as pi_agent
from scansci_html.app_settings import load_settings, save_settings
from scansci_html.evidence_store import index_evidence_library
from scansci_html.pi_agent import (
    PiAgentClient,
    _ManagedGatewayAdapter,
    _bounded_tool_result_for_model,
    _compact_verified_answer_for_model,
    _dynamic_tool_activation_intents,
    _parse_text_tool_intents,
    _redact_tool_value,
)
from scansci_html.research_agent import _PiBackedChatJsonClient
from scansci_html.research_runs import ResearchRunStore, StageSpec
from scansci_html.workspace import initialize_notebook


_TASK_CONTRACT_V2 = {"schema_version": "scansci.task-contract.v2", "version": 2}


@pytest.mark.skipif(os.name != "nt", reason="Windows path-length regression")
def test_session_registry_atomic_write_survives_a_near_limit_windows_path(tmp_path: Path) -> None:
    workspace_root = tmp_path
    placeholder = workspace_root / ".scansci-pi-agent" / "sessions.json"
    padding = 250 - len(str(placeholder)) - 1
    assert 0 < padding < 240
    workspace_root /= "x" * padding
    workspace = workspace_root / "workspace.sqlite"
    assert len(str(workspace.parent / ".scansci-pi-agent" / "sessions.json")) == 250
    client = PiAgentClient(workspace=workspace, evidence_db=workspace_root / "evidence.sqlite")

    client._save_session_registry({"session-long-path": "session.jsonl"})

    registry_path = workspace_root / ".scansci-pi-agent" / "sessions.json"
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {
        "session-long-path": "session.jsonl",
    }
    assert not list(registry_path.parent.glob("*.tmp"))


def test_session_registry_atomic_staging_does_not_overwrite_a_peer_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace.sqlite"
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")
    registry_path = tmp_path / ".scansci-pi-agent" / "sessions.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    peer = registry_path.parent / ".deadbeef.tmp"
    peer.write_text("peer-owned", encoding="utf-8")
    monkeypatch.setattr(
        pi_agent,
        "uuid4",
        lambda: type("FixedUuid", (), {"hex": "deadbeef" + ("0" * 24)})(),
    )

    client._save_session_registry({"session-collision": "session.jsonl"})

    assert peer.read_text(encoding="utf-8") == "peer-owned"
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {
        "session-collision": "session.jsonl",
    }


def test_session_registry_atomic_staging_is_cleaned_when_serialization_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")

    with pytest.raises(TypeError):
        client._save_session_registry({"session-invalid": object()})  # type: ignore[dict-item]

    registry_dir = tmp_path / ".scansci-pi-agent"
    assert not list(registry_dir.glob("*.tmp"))


def test_disabling_zotero_plugin_removes_native_zotero_tools_from_pi(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    settings = load_settings(workspace)
    for plugin in settings["plugins"]:
        if plugin["id"] == "zotero":
            plugin["enabled"] = False
    save_settings(workspace, settings)
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")

    disabled = set(client._disabled_artifact_tools())

    assert {
        "zotero_search",
        "zotero_fulltext",
        "zotero_attachment",
        "zotero_export_bibtex",
        "zotero_citations",
    } <= disabled
    assert "zotero_status" not in disabled


def test_pi_reads_only_documents_registered_by_the_active_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    paper = tmp_path / "downloaded-paper.txt"
    paper.write_text(
        "Canopy photosynthesis and plant respiration jointly determine forest carbon balance.",
        encoding="utf-8",
    )
    unrelated = tmp_path / "unrelated-secret.txt"
    unrelated.write_text("This must not be returned.", encoding="utf-8")
    store = ResearchRunStore(workspace)
    run = store.create_run(
        notebook_id="",
        workflow_type="paper_download_batch",
        title="Downloaded papers",
        input_payload={"identifiers": ["10.1/example"]},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    artifact = store.create_artifact(
        run["run_id"],
        artifact_type="downloaded_paper",
        title="Downloaded papers",
        summary="Downloaded 1 paper",
        payload={
            "files": [str(paper)],
            "imported": {
                "workspace": {
                    "notebooks": [
                        {
                            "root_path": str(unrelated),
                            "metadata": {"source_file": str(unrelated)},
                        }
                    ]
                }
            },
        },
    )
    store.complete_run(run["run_id"], output_artifact_id=artifact["artifact_id"])
    client = PiAgentClient(
        workspace=workspace,
        evidence_db=tmp_path / "evidence.sqlite",
        active_run_id=run["run_id"],
    )

    result = client._execute_tool(
        "read_task_documents",
        {"path": str(unrelated), "max_files": 20, "per_file_chars": 4000},
    )

    assert result["ok"] is True
    assert result["run_id"] == run["run_id"]
    assert result["document_count"] == 1
    assert result["documents"][0]["name"] == paper.name
    assert "forest carbon balance" in result["documents"][0]["excerpt"]
    assert unrelated.name not in {item["name"] for item in result["documents"]}


def test_pi_task_document_result_stays_within_gateway_budget(tmp_path: Path, monkeypatch) -> None:
    paths = []
    for index in range(20):
        paper = tmp_path / f"paper-{index}.txt"
        paper.write_text(
            f"Paper {index} abstract and methods. " + ("Results and conclusion. " * 2_000),
            encoding="utf-8",
        )
        paths.append(paper)

    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(
        client,
        "_task_run_with_documents",
        lambda _requested_run_id="": ({"run_id": "run-test", "title": "Papers", "workflow_type": "paper_download"}, paths),
    )

    result = client._read_task_documents({})
    encoded_size = len(json.dumps(result, ensure_ascii=False))

    assert result["document_count"] == 20
    assert encoded_size < 40_000
    assert "中间内容已省略" in result["documents"][0]["excerpt"]


def test_pi_workspace_inventory_is_compacted_before_model_context() -> None:
    result = {
        "workspace": "workspace.sqlite",
        "counts": {"notebooks": 1, "sources": 50_000},
        "notebooks": [
            {
                "notebook_id": "library",
                "title": "Large library",
                "description": "Evidence",
                "counts": {"sources": 50_000, "notes": 4, "layers": 2},
                "metadata": {"library_kind": "zotero"},
                "sources": [
                    {"title": f"Paper {index}", "fulltext": "large text " * 100}
                    for index in range(2_000)
                ],
            }
        ],
    }

    bounded, meta = _bounded_tool_result_for_model("inspect_workspace", result)

    assert meta["original_bytes"] > 1_000_000
    assert meta["model_bytes"] < 16_000
    assert meta["truncated"] is True
    assert meta["persist_full"] is False
    assert bounded["notebooks"][0]["counts"]["sources"] == 50_000
    assert "sources" not in bounded["notebooks"][0]


def test_pi_arbitrary_tool_result_has_a_hard_model_context_ceiling() -> None:
    bounded, meta = _bounded_tool_result_for_model(
        "mcp_result",
        {"items": [{"content": "证据" * 20_000} for _ in range(100)]},
    )

    assert meta["original_bytes"] > 1_000_000
    assert meta["model_bytes"] <= 16_000
    assert meta["truncated"] is True
    assert meta["persist_full"] is True
    assert bounded["_scansci_truncated"] is True


def test_pi_tool_results_redact_credentials_before_model_or_disk() -> None:
    secret = "sk-abcdefghijklmnop123456"
    result = _redact_tool_value(
        {
            "api_key": secret,
            "nested": {
                "authorization": f"Bearer {secret}",
                "url": f"https://example.test/data?access_token={secret}&page=1",
            },
        }
    )

    assert result["api_key"] == "[REDACTED]"
    assert result["nested"]["authorization"] == "[REDACTED]"
    assert "access_token=[REDACTED]" in result["nested"]["url"]
    assert secret not in json.dumps(result)


def test_pi_discover_papers_uses_federated_academic_search(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_search(query: str, **kwargs: object) -> dict[str, object]:
        captured.update({"query": query, **kwargs})
        return {"query": query, "count": 1, "items": [{"title": "A paper"}]}

    monkeypatch.setattr(pi_agent, "search_academic_papers", fake_search)
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
        embedding_provider="embedding-fixture",
        reranker="reranker-fixture",
    )

    result = client._execute_tool(
        "discover_papers",
        {
            "query": "scientific retrieval",
            "providers": ["openalex", "pubmed"],
            "year_from": 2021,
            "result_limit": 18,
            "per_source": 7,
        },
    )

    assert result["count"] == 1
    assert captured["query"] == "scientific retrieval"
    assert captured["query_variants"] == ["scientific retrieval"]
    assert captured["required_terms"] == ["scientific retrieval"]
    assert captured["provider_names"] == ["openalex", "pubmed"]
    assert captured["year_from"] == 2021
    assert captured["limit"] == 18
    assert captured["per_source"] == 7
    assert captured["embedding_provider"] == "embedding-fixture"
    assert captured["reranker"] == "reranker-fixture"
    assert result["search_plan"]["topic"] == "scientific retrieval"


def test_pi_discover_papers_keeps_full_result_off_model_and_accepts_limit_alias(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    full_abstract = "A" * 20_000
    papers = [
        {
            "title": f"Paper {index}",
            "doi": f"10.1000/paper-{index}",
            "authors": [f"Author {author}" for author in range(10)],
            "abstract": full_abstract,
            "sources": ["openalex", "crossref"],
            "source_records": [{"source": "openalex", "payload": "B" * 4_000}],
            "score_breakdown": {"dense": 0.9, "reranker": 1.0},
            "score": 1.0 - index / 100,
            "discovery_only": True,
        }
        for index in range(10)
    ]

    def fake_search(query: str, **kwargs: object) -> dict[str, object]:
        captured.update({"query": query, **kwargs})
        return {
            "query": query,
            "count": len(papers),
            "candidate_count": 40,
            "deduplicated_count": len(papers),
            "providers_requested": ["openalex", "crossref"],
            "providers_succeeded": ["openalex", "crossref"],
            "provider_counts": {"openalex": 10, "crossref": 10},
            "provider_errors": {},
            "items": papers,
            "evidence_status": "discovery_leads",
        }

    monkeypatch.setattr(pi_agent, "search_academic_papers", fake_search)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")

    result = client._execute_tool("discover_papers", {"query": "forest productivity", "limit": 10})

    assert captured["limit"] == 10
    assert captured["per_source"] == 8
    assert result["count"] == 8
    assert result["total_count"] == 10
    assert result["download_identifiers"] == [f"10.1000/paper-{index}" for index in range(10)]
    assert len(result["items"][0]["abstract_excerpt"]) == 600
    assert "source_records" not in result["items"][0]
    assert "score_breakdown" not in result["items"][0]
    assert len(json.dumps(result, ensure_ascii=False)) < 20_000

    full_result_path = client.agent_dir / result["full_result_reference"]
    assert full_result_path.is_file()
    persisted = json.loads(full_result_path.read_text(encoding="utf-8"))
    assert len(persisted["items"]) == 10
    assert persisted["items"][0]["abstract"] == full_abstract
    assert result["full_result_bytes"] == full_result_path.stat().st_size


def test_pi_search_local_evidence_federates_selected_knowledge_bases(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.sqlite"
    second = tmp_path / "second.sqlite"
    first.touch()
    second.touch()
    calls: list[Path] = []

    def fake_search(path: str | Path, query: str, **_kwargs: object) -> list[dict[str, object]]:
        resolved = Path(path).resolve()
        calls.append(resolved)
        return [
            {
                "evidence_id": f"evidence-{resolved.stem}",
                "doc_id": f"doc-{resolved.stem}",
                "title": resolved.stem,
                "text": f"{query} from {resolved.stem}",
                "matched_text": f"{query} from {resolved.stem}",
                "html_anchor": "results-p1-s0002",
                "context_before": "The preceding sentence.",
                "context_before_evidence_id": "evidence-before",
                "context_after": "The following sentence.",
                "context_after_evidence_id": "evidence-after",
                "score": 0.9 if resolved == second.resolve() else 0.6,
            }
        ]

    monkeypatch.setattr(pi_agent, "search_evidence_store", fake_search)
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=first,
        additional_evidence_dbs=[second, first],
    )

    result = client._execute_tool("search_local_evidence", {"query": "shared claim", "result_limit": 8})

    assert calls == [first.resolve(), second.resolve()]
    assert result["library_count"] == 2
    assert result["count"] == 2
    assert [hit["library_index"] for hit in result["hits"]] == [1, 0]
    assert result["hits"][0]["matched_text"] == "shared claim from second"
    assert result["hits"][0]["context_before"] == "The preceding sentence."
    assert result["hits"][0]["context_after"] == "The following sentence."


def test_pi_search_local_evidence_uses_jev_only_as_an_optional_reranking_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.touch()
    captured: dict[str, object] = {}
    hits = [
        {
            "evidence_id": "evidence-a",
            "doc_id": "doc-a",
            "title": "Paper A",
            "matched_text": "First passage.",
            "html_anchor": "results-p1-s0001",
            "score": 0.9,
        },
        {
            "evidence_id": "evidence-b",
            "doc_id": "doc-b",
            "title": "Paper B",
            "matched_text": "Second passage.",
            "html_anchor": "results-p1-s0002",
            "score": 0.5,
        },
    ]

    monkeypatch.setattr(
        pi_agent,
        "load_settings",
        lambda _workspace: {
            "plugins": [],
            "providers": [
                {
                    "id": "jev",
                    "enabled": True,
                    "api_key_configured": True,
                    "base_url": "https://api.typesafe.ai/v1/systemone",
                }
            ],
        },
    )

    def fake_search(_path: str | Path, _query: str, **kwargs: object) -> list[dict[str, object]]:
        captured["search_limit"] = kwargs["limit"]
        return list(hits)

    def fake_review(**kwargs: object) -> dict[str, object]:
        captured["review_hits"] = kwargs["hits"]
        return {
            "hits": list(reversed(kwargs["hits"])),
            "review": {"status": "applied", "reviewed_count": 2},
        }

    monkeypatch.setattr(pi_agent, "search_evidence_store", fake_search)
    monkeypatch.setattr(pi_agent, "review_evidence_candidates", fake_review)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)

    result = client._execute_tool("search_local_evidence", {"query": "second passage", "result_limit": 8})

    assert captured["search_limit"] == 16
    assert len(captured["review_hits"]) == 2
    assert [item["evidence_id"] for item in result["hits"]] == ["evidence-b", "evidence-a"]
    assert result["jev_review"]["status"] == "applied"


def test_pi_search_local_evidence_runs_real_sentence_context_recall(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        """
        <article class="paper" data-doi="10.1234/pi-context">
          <h1>Pi Context Paper</h1>
          <h2>Results</h2>
          <p id="results-p1">The cohort included 120 participants. Treatment increased cortical activity in language regions. The follow-up assessment found the effect persisted.</p>
        </article>
        """,
        encoding="utf-8",
    )
    evidence_db = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=evidence_db, min_sentence_length=10)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)

    result = client._execute_tool(
        "search_local_evidence",
        {"query": "treatment increased cortical activity", "result_limit": 1},
    )

    assert result["count"] == 1
    hit = result["hits"][0]
    assert hit["paper"] == "Pi Context Paper"
    assert hit["matched_text"] == "Treatment increased cortical activity in language regions."
    assert hit["context_before"] == "The cohort included 120 participants."
    assert hit["context_after"] == "The follow-up assessment found the effect persisted."
    assert hit["html_anchor"] == "results-p1-s0002"


def test_pi_rejects_an_unsupported_source_year_before_creating_presentation(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        '<article data-doi="10.1234/forest-year"><h1>Forest Study</h1>'
        '<p>Plot A contained twelve trees. Both plots were measured in spring.</p>'
        '<p>Published 2026.</p></article>',
        encoding="utf-8",
    )
    evidence_db = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=evidence_db, min_sentence_length=8)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    evidence_id = client._execute_tool(
        "search_local_evidence", {"query": "Plot A contained twelve trees", "result_limit": 1}
    )["hits"][0]["evidence_id"]
    context = pi_agent._RunContext(request_id="artifact-evidence", session_id="local", generation=1)
    context.history.append({"name": "search_local_evidence", "status": "ok", "evidence_hit_count": 1})
    client._worker_context.run_context = context
    arguments = {
        "title": "Forest results",
        "slides": [{
            "title": "Plot A",
            "bullets": [f"Plot A contained twelve trees. Evidence ID: {evidence_id}", "Measurement Date: Spring 2026"],
        }],
    }

    arguments["slides"][0]["bullets"][0] = "Plot A contained twelve trees."
    with pytest.raises(ValueError, match="加入检索结果的完整 evidence_id"):
        client._execute_tool("create_presentation", arguments)
    arguments["slides"][0]["content"] = f"Evidence ID: {evidence_id}"
    with pytest.raises(ValueError, match="加入检索结果的完整 evidence_id"):
        client._execute_tool("create_presentation", arguments)
    del arguments["slides"][0]["content"]
    arguments["slides"][0]["bullets"][0] += " Evidence ID: made-up.s0001"
    with pytest.raises(ValueError, match="不在当前本地证据库"):
        client._execute_tool("create_presentation", arguments)
    arguments["slides"][0]["bullets"][0] = f"Plot A contained twelve trees. Evidence ID: {evidence_id}"
    with pytest.raises(ValueError, match="没有 2026 年的测量或采集记录"):
        client._execute_tool("create_presentation", arguments)
    for tool in ("create_document", "create_pdf"):
        with pytest.raises(ValueError, match="加入检索结果的完整 evidence_id"):
            client._execute_tool(tool, {"title": "Forest results", "content": "Measurement Date: Spring 2026"})
    assert not list((tmp_path / "artifacts").rglob("*.pptx"))

    arguments["slides"][0]["bullets"][1] = "Measurement season: Spring"
    created = client._execute_tool("create_presentation", arguments)
    assert created["ok"] is True
    assert Path(created["file_path"]).is_file()

    (library / "paper.html").write_text(
        '<article data-doi="10.1234/forest-year"><h1>Forest Study</h1>'
        '<p>Plot A contained twelve trees. Both plots were measured in spring 2025.</p></article>',
        encoding="utf-8",
    )
    index_evidence_library(library, db_path=evidence_db, min_sentence_length=8)
    date_id = next(
        hit["evidence_id"]
        for hit in client._execute_tool(
            "search_local_evidence", {"query": "measured in spring 2025", "result_limit": 3}
        )["hits"]
        if "2025" in hit["text"]
    )
    arguments["slides"][0]["bullets"][1] = "Measurement Date: Spring 2025"
    arguments["slides"][0]["bullets"].append(f"Date evidence: {date_id}")
    arguments["output_name"] = "source-supported-year.pptx"
    assert client._execute_tool("create_presentation", arguments)["ok"] is True

    context.history[0]["evidence_hit_count"] = 0
    client._check_cited_artifact_years("create_document", {"content": "A blank template"})


def test_pi_build_verified_answer_federates_all_selected_evidence_stores(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.sqlite"
    second = tmp_path / "second.sqlite"
    first.touch()
    second.touch()
    calls: list[Path] = []

    def fake_answer(path: str | Path, question: str, **_kwargs: object) -> dict[str, object]:
        resolved = Path(path).resolve()
        calls.append(resolved)
        label = resolved.stem
        return {
            "question": question,
            "reader_answer": {
                "text": f"Verified answer from {label}.",
                "citation_count": 1,
                "citations": [
                    {
                        "citation_id": f"citation-{label}",
                        "evidence_id": f"evidence-{label}",
                        "doc_id": f"doc-{label}",
                        "paper": f"Paper {label}",
                        "exact_quote": f"Evidence from {label}.",
                    }
                ],
            },
            "answer": {"insufficient_evidence": False, "limitations": []},
            "adequacy": {
                "is_sufficient": True,
                "quote_count": 1,
                "document_count": 1,
                "followup_reason": "",
            },
            "citation_verification": {
                "passed": True,
                "claim_count": 1,
                "supported_claim_count": 1,
                "missing_quote_ids": [],
            },
        }

    monkeypatch.setattr(pi_agent, "answer_question", fake_answer)
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=first,
        additional_evidence_dbs=[second],
    )

    result = client._execute_tool(
        "build_verified_answer",
        {"question": "What do the selected libraries show?", "result_limit": 8},
    )

    assert calls == [first.resolve(), second.resolve()]
    assert result["federated"] is True
    assert result["library_count"] == 2
    assert result["successful_library_count"] == 2
    assert result["citation_verification"]["passed"] is True
    assert {item["library_index"] for item in result["reader_answer"]["citations"]} == {0, 1}
    assert "Knowledge base 1" in result["reader_answer"]["text"]
    assert "Knowledge base 2" in result["reader_answer"]["text"]


def test_pi_download_and_index_persists_a_completed_high_level_task(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace.sqlite"
    root_evidence = tmp_path / "evidence.sqlite"
    initialize_notebook(workspace, notebook_id="library", title="Research library")
    first = tmp_path / "paper-a.pdf"
    second = tmp_path / "paper-b.pdf"
    first.write_bytes(b"%PDF-1.4 fixture a")
    second.write_bytes(b"%PDF-1.4 fixture b")
    captured: dict[str, object] = {}

    def fake_download(identifiers: list[str], **kwargs: object) -> dict[str, object]:
        captured["identifiers"] = identifiers
        captured.update(kwargs)
        return {"ok": True, "files": [str(first), str(second)], "failed": []}

    def fake_import(workspace_path: str | Path, evidence_path: str | Path, **kwargs: object) -> dict[str, object]:
        captured["workspace"] = Path(workspace_path).resolve()
        captured["evidence_db"] = Path(evidence_path).resolve()
        captured["import"] = kwargs
        return {"added_files": 2, "skipped_files": []}

    monkeypatch.setattr(pi_agent, "download_papers", fake_download)
    monkeypatch.setattr(pi_agent, "import_library_files", fake_import)
    client = PiAgentClient(
        workspace=workspace,
        evidence_db=tmp_path / "library.sqlite",
        root_evidence_db=root_evidence,
        notebook_ids=["library"],
    )

    result = client._execute_tool(
        "download_and_index",
        {"identifiers": ["10.1000/alpha", "10.1000/beta"], "strategy": "oa_first"},
    )
    persisted = ResearchRunStore(workspace).get_run(result["run_id"])

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["downloaded_file_count"] == 2
    assert result["evidence_status"] == "indexed_fulltext"
    assert persisted["status"] == "completed"
    assert persisted["output_artifact"]["payload"]["evidence_status"] == "indexed_fulltext"
    assert captured["evidence_db"] == root_evidence.resolve()
    assert captured["import"]["notebook_id"] == "library"


def test_pi_download_and_index_uses_default_library_and_ignores_missing_reported_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace.sqlite"
    paper = tmp_path / "paper.pdf"
    missing = tmp_path / "missing.pdf"
    paper.write_bytes(b"%PDF-1.4 fixture")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        pi_agent,
        "download_papers",
        lambda *_args, **_kwargs: {
            "ok": False,
            "completed": 1,
            "failed": 1,
            "files": [str(paper), str(missing)],
            "items": [
                {"identifier": "10.1000/ok", "status": "completed", "files": [str(paper)], "error": ""},
                {"identifier": "10.1000/missing", "status": "failed", "files": [], "error": "not found"},
            ],
        },
    )

    def fake_import(_workspace, _evidence_db, **kwargs):
        captured.update(kwargs)
        return {"added_files": 1, "skipped_files": []}

    monkeypatch.setattr(pi_agent, "import_library_files", fake_import)
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")

    result = client._execute_tool(
        "download_and_index",
        {"identifiers": ["10.1000/ok", "10.1000/missing"]},
    )

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["status"] == "failed"
    assert result["downloaded_file_count"] == 1
    assert result["files"] == [str(paper.resolve())]
    assert result["notebook_id"] == "pi-research-downloads"
    assert captured["notebook_id"] == "pi-research-downloads"
    assert captured["file_paths"] == [str(paper.resolve())]
    assert any(item.get("file") == str(missing) for item in result["failures"])
    summary = pi_agent.load_workspace_summary(workspace, notebook_id="pi-research-downloads")
    assert summary["notebooks"][0]["metadata"]["created_by"] == "pi-agent"


def test_failed_new_download_task_never_falls_back_to_older_documents(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace.sqlite"
    old_paper = tmp_path / "old-paper.txt"
    old_paper.write_text("Old task content must not be summarized.", encoding="utf-8")
    store = ResearchRunStore(workspace)
    old_run = store.create_run(
        notebook_id="",
        workflow_type="paper_download",
        title="Old completed task",
        input_payload={},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    artifact = store.create_artifact(
        old_run["run_id"],
        artifact_type="downloaded_paper",
        title="Old paper",
        summary="Old completed paper",
        payload={"files": [str(old_paper)]},
    )
    store.complete_run(old_run["run_id"], output_artifact_id=artifact["artifact_id"])
    monkeypatch.setattr(
        pi_agent,
        "download_papers",
        lambda identifiers, **_kwargs: {
            "ok": True,
            "files": [str(tmp_path / "missing-new-paper.pdf")],
            "items": [
                {
                    "identifier": identifiers[0],
                    "status": "completed",
                    "files": [str(tmp_path / "missing-new-paper.pdf")],
                    "error": "",
                }
            ],
        },
    )
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")

    with pytest.raises(RuntimeError, match="without a readable document"):
        client._execute_tool("download_and_index", {"identifiers": ["10.1000/new"]})
    read_result = client._read_task_documents({})

    assert client.active_run_id != old_run["run_id"]
    assert read_result["run_id"] == client.active_run_id
    assert read_result["document_count"] == 0
    assert old_paper.name not in json.dumps(read_result, ensure_ascii=False)


def test_pi_summarize_documents_maps_full_task_files_by_research_aspect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    paper = tmp_path / "paper.txt"
    paper.write_text(
        "Abstract. We ask how forest productivity responds to drought.\n\n"
        "Methods. We analyzed a 20-year satellite dataset with a hierarchical model.\n\n"
        "Results. Productivity declined significantly during repeated drought events.\n\n"
        "Limitations. The observational design cannot isolate every causal mechanism.",
        encoding="utf-8",
    )
    store = ResearchRunStore(workspace)
    run = store.create_run(
        notebook_id="",
        workflow_type="paper_download",
        title="Downloaded paper",
        input_payload={"identifier": "10.1000/example"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    artifact = store.create_artifact(
        run["run_id"],
        artifact_type="downloaded_paper",
        title="Downloaded paper",
        summary="Downloaded 1 paper",
        payload={"files": [str(paper)], "evidence_status": "indexed_fulltext"},
    )
    store.complete_run(run["run_id"], output_artifact_id=artifact["artifact_id"])
    client = PiAgentClient(
        workspace=workspace,
        evidence_db=tmp_path / "evidence.sqlite",
        active_run_id=run["run_id"],
    )

    result = client._execute_tool(
        "summarize_documents",
        {"focus": "drought productivity", "max_files": 8},
    )

    assert result["ok"] is True
    assert result["coverage"] == 1.0
    assert result["document_count"] == 1
    mapped = result["documents"][0]
    assert "forest productivity" in mapped["research_question"]
    assert "hierarchical model" in mapped["methods"]
    assert "declined significantly" in mapped["findings"]
    assert "observational design" in mapped["limitations"]


def test_pi_check_task_completion_requires_indexed_downloads(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 fixture")
    store = ResearchRunStore(workspace)
    run = store.create_run(
        notebook_id="library",
        workflow_type="paper_download",
        title="Downloaded paper",
        input_payload={"identifier": "10.1000/example"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    store.begin_run(run["run_id"])
    store.start_stage(run["run_id"], "deliver")
    store.complete_stage(run["run_id"], "deliver", output={"files": [str(paper)]})
    artifact = store.create_artifact(
        run["run_id"],
        artifact_type="downloaded_paper",
        title="Downloaded paper",
        summary="Downloaded only",
        payload={"files": [str(paper)], "evidence_status": "downloaded_unindexed"},
    )
    store.complete_run(run["run_id"], output_artifact_id=artifact["artifact_id"])
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")

    result = client._execute_tool("check_task_completion", {"run_id": run["run_id"]})

    assert result["complete"] is False
    assert result["checks"]["run_completed"] is True
    assert result["checks"]["fulltext_indexed"] is False
    assert result["checks"]["downloaded_files"] == 1
    assert result["blockers"] == ["fulltext_indexed"]


def test_pi_kb_search_routes_selected_zotero_library_to_native_adapter(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace.sqlite"
    initialize_notebook(
        workspace,
        notebook_id="zotero-library",
        title="Zotero 文献库",
        metadata={"library_kind": "zotero", "zotero": {"connected": True}},
    )
    captured: dict[str, object] = {}

    def fake_zotero_search(query: str, **kwargs: object) -> dict[str, object]:
        captured.update({"query": query, **kwargs})
        return {"ok": True, "count": 2, "items": [{"title": "Paper A"}, {"title": "Paper B"}]}

    monkeypatch.setattr(pi_agent, "search_zotero_library", fake_zotero_search)
    client = PiAgentClient(
        workspace=workspace,
        evidence_db=tmp_path / "evidence.sqlite",
        notebook_ids=["zotero-library"],
    )

    result = client._execute_tool("kb_search", {"query": "核心主题", "result_limit": 6})

    assert result["count"] == 2
    assert result["hits"] == []
    assert result["zotero"][0]["items"][0]["title"] == "Paper A"
    assert captured == {
        "query": "核心主题",
        "limit": 6,
        "collection_key": "",
        "include_fulltext": True,
    }


def test_pi_routes_selected_obsidian_vault_to_native_read_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Methods.md").write_text("# Methods\n\nHybrid retrieval uses embeddings and BM25.", encoding="utf-8")
    initialize_notebook(
        workspace,
        notebook_id="obsidian-vault",
        title="Research Vault",
        root_path=vault,
        metadata={"library_kind": "obsidian", "library_root": str(vault)},
    )
    client = PiAgentClient(
        workspace=workspace,
        evidence_db=tmp_path / "evidence.sqlite",
        notebook_ids=["obsidian-vault"],
    )

    status = client._execute_tool("obsidian_status", {})
    search = client._execute_tool("obsidian_search", {"query": "hybrid retrieval", "result_limit": 5})
    note = client._execute_tool("obsidian_read", {"note_path": "Methods.md"})

    assert status["vaults"][0]["read_only"] is True
    assert search["vaults"][0]["results"][0]["title"] == "Methods"
    assert "BM25" in note["content"]


def test_pi_search_web_uses_public_web_boundary(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_search(query: str, **kwargs: object) -> dict[str, object]:
        captured.update({"query": query, **kwargs})
        return {"query": query, "count": 1, "items": [{"title": "Market close"}]}

    monkeypatch.setattr(pi_agent, "search_public_web", fake_search)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")

    result = client._execute_tool("search_web", {"query": "today A-share market", "result_limit": 6})

    assert result["count"] == 1
    assert captured == {"query": "today A-share market", "limit": 6}


def test_managed_gateway_retries_transient_ssl_transport_errors(monkeypatch) -> None:
    attempts = 0

    def flaky_post(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise pi_agent.requests.exceptions.SSLError("temporary EOF")
        return SimpleNamespace(status_code=200, headers={})

    monkeypatch.setattr(pi_agent.requests, "post", flaky_post)
    monkeypatch.setattr(pi_agent.time, "sleep", lambda _delay: None)
    adapter = _ManagedGatewayAdapter(upstream_base_url="https://gateway.example/v1", api_key="fixture")
    try:
        response = adapter._post_with_transport_retry({"model": "fixture-model"})
    finally:
        adapter.close()

    assert response.status_code == 200
    assert attempts == 2


def test_managed_gateway_honors_bounded_upstream_retry_after(monkeypatch) -> None:
    attempts = 0
    waits: list[float] = []

    def rate_limited_then_ready(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return SimpleNamespace(status_code=429, headers={"Retry-After": "90"})
        return SimpleNamespace(status_code=200, headers={})

    monkeypatch.setattr(pi_agent.requests, "post", rate_limited_then_ready)
    monkeypatch.setattr(pi_agent.time, "sleep", waits.append)
    adapter = _ManagedGatewayAdapter(upstream_base_url="https://gateway.example/v1", api_key="fixture")
    try:
        response = adapter._post_with_transport_retry({"model": "fixture-model"})
    finally:
        adapter.close()

    assert response.status_code == 200
    assert attempts == 2
    assert waits == [60.0]


@pytest.mark.parametrize("retry_after", ["90", "Wed, 09 Sep 2026 00:01:30 GMT"])
def test_pi_gateway_leaves_retries_to_session_and_shares_full_cooldown(monkeypatch, retry_after) -> None:
    posts = []
    cooldowns = []
    monkeypatch.setattr(pi_agent.requests, "post", lambda *args, **kwargs: (
        posts.append(kwargs) or SimpleNamespace(status_code=429, headers={"Retry-After": retry_after})
    ))
    monkeypatch.setattr(pi_agent.time, "time", lambda: 1788912000.0)
    monkeypatch.setattr(pi_agent, "_stream_set_cooldown", lambda key, delay: cooldowns.append((key, delay)))
    adapter = _ManagedGatewayAdapter(upstream_base_url="https://gateway.example/v1", api_key="fixture", transport_attempts=1)
    try:
        assert adapter._post_with_transport_retry({"model": "fixture-model"}).status_code == 429
        assert len(posts) == 1
        assert cooldowns == [(adapter._rate_key, 90.0)]
    finally:
        adapter.close()


def test_pi_gateway_cooldown_is_cancellable_without_a_provider_request(monkeypatch) -> None:
    adapter = _ManagedGatewayAdapter(upstream_base_url="https://gateway.example/v1", api_key="fixture", transport_attempts=1)
    monkeypatch.setattr(pi_agent, "_stream_cooldown_remaining", lambda _key: 86400.0)
    try:
        adapter._closed.set()
        with pytest.raises(pi_agent.requests.RequestException, match="cancelled"):
            adapter._await_rate_slot()
    finally:
        adapter.close()


@pytest.mark.parametrize("state", ["disconnected", "expired", "closed"])
def test_pi_gateway_rejects_abandoned_queued_requests(state) -> None:
    server_socket, client_socket = pi_agent.socket.socketpair()
    adapter = _ManagedGatewayAdapter(upstream_base_url="https://queue.example/v1", api_key="fixture", transport_attempts=1)
    try:
        started = pi_agent.time.perf_counter()
        if state == "disconnected":
            client_socket.close()
        elif state == "expired":
            started -= 211
        else:
            adapter._closed.set()
        with pytest.raises(pi_agent.requests.RequestException):
            adapter._assert_request_active(SimpleNamespace(connection=server_socket), started)
    finally:
        server_socket.close()
        client_socket.close()
        adapter.close()


def test_managed_gateway_forwards_only_safe_recovery_headers() -> None:
    response = SimpleNamespace(
        headers={
            "Retry-After": "12",
            "x-scansci-error-class": "upstream_rate_limited",
            "x-ratelimit-remaining": "0",
            "Authorization": "secret-value",
        }
    )

    assert _ManagedGatewayAdapter._recovery_headers(response) == {
        "Retry-After": "12",
        "x-scansci-error-class": "upstream_rate_limited",
        "x-ratelimit-remaining": "0",
    }


def test_managed_gateway_labels_input_limit_for_pi_auto_compaction(monkeypatch) -> None:
    error_text = json.dumps(
        {
            "code": "invalid_request",
            "message": "The request body is empty or exceeds the managed service input limit.",
        }
    )

    def reject_oversized(_payload: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            status_code=400,
            headers={},
            text=error_text,
            json=lambda: json.loads(error_text),
        )

    adapter = _ManagedGatewayAdapter(upstream_base_url="https://gateway.example/v1", api_key="fixture")
    monkeypatch.setattr(adapter, "_post_with_transport_retry", reject_oversized)
    try:
        response = pi_agent.requests.post(
            f"{adapter.base_url}/chat/completions",
            json={
                "model": "fixture-model",
                "messages": [{"role": "user", "content": "large context"}],
                "stream": True,
            },
            timeout=5,
        )
    finally:
        adapter.close()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "context_length_exceeded"
    assert "managed service input limit" in response.json()["error"]["message"]


class _OpenAIStreamHandler(BaseHTTPRequestHandler):
    request_payload: dict[str, object] = {}
    request_headers: dict[str, str] = {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payload = json.loads(self.rfile.read(length))
        type(self).request_headers = dict(self.headers.items())
        chunks = [
            {
                "id": "chatcmpl-scanscipi",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Pi bridge works"}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-scanscipi",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 105,
                    "completion_tokens": 3,
                    "total_tokens": 108,
                    "prompt_tokens_details": {"cached_tokens": 100},
                },
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIOutputLimitHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []
    always_truncate = False
    early_stop_html = False
    never_close_html = False
    truncate_tool_call = False
    empty_continuation = False

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payloads.append(json.loads(self.rfile.read(length)))
        attempt = len(type(self).request_payloads)
        fragment = "<html><script>function play(){" if attempt == 1 else "}</script></html>"
        if attempt > 1 and type(self).never_close_html:
            fragment = "}</script><p>still open"
        if attempt > 1 and type(self).empty_continuation:
            fragment = ""
        finish_reason = "length" if type(self).always_truncate or (attempt == 1 and not type(self).early_stop_html) else "stop"
        delta = {"role": "assistant", "content": fragment}
        if attempt == 1 and type(self).truncate_tool_call:
            delta = {"role": "assistant", "tool_calls": [{
                "index": 0,
                "id": "call_partial",
                "type": "function",
                "function": {"name": "inspect_available_tools", "arguments": "{}"},
            }]}
        chunks = [
            {
                "id": f"chatcmpl-output-{attempt}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-output-{attempt}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OllamaNativeCaptureHandler(BaseHTTPRequestHandler):
    request_payload: dict[str, object] = {}
    request_path = ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        body = json.dumps({"models": [{"name": "qwen3.5:9b"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payload = json.loads(self.rfile.read(length))
        type(self).request_path = self.path
        chunks = [
            {"model": "qwen3.5:9b", "message": {"role": "assistant", "content": "Pi adapter works."}, "done": False},
            {"model": "qwen3.5:9b", "message": {"role": "assistant", "content": ""}, "done": True,
             "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 4},
        ]
        body = ("\n".join(json.dumps(chunk) for chunk in chunks) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OllamaNativeFinalToolHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        body = json.dumps({"models": [{"name": "qwen3.8-4b-distill:latest"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payloads.append(json.loads(self.rfile.read(length)))
        if len(type(self).request_payloads) == 1:
            chunks = [
                {"model": "qwen3.8-4b-distill:latest", "message": {"role": "assistant", "thinking": "Call the tool."}, "done": False},
                {"model": "qwen3.8-4b-distill:latest", "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call_workspace", "function": {"index": 0, "name": "inspect_workspace", "arguments": {}}},
                ]}, "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 20},
            ]
        else:
            chunks = [
                {"model": "qwen3.8-4b-distill:latest", "message": {"role": "assistant", "content": "The workspace has 0 notebooks."}, "done": False},
                {"model": "qwen3.8-4b-distill:latest", "message": {"role": "assistant", "content": ""},
                 "done": True, "done_reason": "stop", "prompt_eval_count": 120, "eval_count": 10},
            ]
        body = ("\n".join(json.dumps(chunk) for chunk in chunks) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OllamaNativeErrorHandler(_OllamaNativeCaptureHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = b'{"error":"model runner out of memory"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _OpenAIToolLoopHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        if len(type(self).request_payloads) == 1:
            chunks = [
                {
                    "id": "chatcmpl-tool",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_workspace",
                                        "type": "function",
                                        "function": {"name": "inspect_available_tools", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-tool",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                },
            ]
        else:
            chunks = [
                {
                    "id": "chatcmpl-final",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Tool result received"}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl-final",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIProgressiveSkillHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        turn = len(type(self).request_payloads)
        if turn == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_load_skill",
                    "type": "function",
                    "function": {
                        "name": "load_skill",
                        "arguments": '{"skill_id":"good-question"}',
                    },
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": f"skill-turn-{turn}"}
            finish = "stop"
        chunks = [
            {
                "id": f"chatcmpl-skill-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-skill-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIRepeatedSkillHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        turn = len(type(self).request_payloads)
        if turn <= 5:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": f"call_load_skill_{turn}",
                    "type": "function",
                    "function": {
                        "name": "load_skill",
                        "arguments": '{"skill_id":"good-question"}',
                    },
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "deduplicated-skill-complete"}
            finish = "stop"
        chunks = [
            {
                "id": f"chatcmpl-repeated-skill-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-repeated-skill-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIResumeBudgetHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        turn = len(type(self).request_payloads)
        if turn == 2:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_search_after_restore",
                    "type": "function",
                    "function": {
                        "name": "search_skills",
                        "arguments": '{"query":"safe-64","limit":8}',
                    },
                }],
            }
            finish = "tool_calls"
        elif turn == 3:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_load_after_restore",
                    "type": "function",
                    "function": {
                        "name": "load_skill",
                        "arguments": '{"skill_id":"safe-64","resource":"one.md"}',
                    },
                }],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": f"resume-budget-turn-{turn}"}
            finish = "stop"
        chunks = [
            {
                "id": f"chatcmpl-resume-budget-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-resume-budget-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIExplicitSkillPreloadHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        chunks = [
            {
                "id": "chatcmpl-explicit-preload",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": "explicit-preload-complete"},
                    "finish_reason": None,
                }],
            },
            {
                "id": "chatcmpl-explicit-preload",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIJsonHandler(_OpenAIStreamHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        chunks = [
            {
                "id": "chatcmpl-json",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": '{"value":"ephemeral"}'},
                    "finish_reason": None,
                }],
            },
            {
                "id": "chatcmpl-json",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _OpenAIPlanDefaultDenyHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payloads.append(json.loads(self.rfile.read(length)))
        turn = len(type(self).request_payloads)
        if turn == 1:
            call = {
                "index": 0,
                "id": "call_plan",
                "type": "function",
                "function": {
                    "name": "submit_plan",
                    "arguments": json.dumps({
                        "summary": "Download one paper",
                        "steps": [{"id": "download", "title": "Download"}],
                    }),
                },
            }
            finish = "tool_calls"
            delta = {"role": "assistant", "tool_calls": [call]}
        elif turn == 2:
            call = {
                "index": 0,
                "id": "call_download",
                "type": "function",
                "function": {
                    "name": "download_and_index",
                    "arguments": '{"identifiers":["10.1/denied"]}',
                },
            }
            finish = "tool_calls"
            delta = {"role": "assistant", "tool_calls": [call]}
        else:
            finish = "stop"
            delta = {"role": "assistant", "content": "Plan was not approved."}
        chunks = [
            {
                "id": f"chatcmpl-plan-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"chatcmpl-plan-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIDeferredWebHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        turn = len(type(self).request_payloads)
        if turn == 1:
            chunks = [
                {
                    "id": "chatcmpl-plan",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": "现在开始执行调研，请稍候。"}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl-plan",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
        elif turn == 2:
            chunks = [
                {
                    "id": "chatcmpl-search",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_web",
                                "type": "function",
                                "function": {"name": "search_web", "arguments": '{"query":"today A-share market"}'},
                            }],
                        },
                        "finish_reason": None,
                    }],
                },
                {
                    "id": "chatcmpl-search",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                },
            ]
        else:
            chunks = [
                {
                    "id": "chatcmpl-result",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": "已完成检索，并根据来源给出最终市场概况。"}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl-result",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
        body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAITerminalToolHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payloads.append(json.loads(self.rfile.read(length)))
        chunks = [
            {
                "id": "chatcmpl-terminal",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_verified",
                                    "type": "function",
                                    "function": {
                                        "name": "build_verified_answer",
                                        "arguments": '{"question":"question"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-terminal",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIPersistentHandler(BaseHTTPRequestHandler):
    request_payloads: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).request_payloads.append(payload)
        response_text = f"turn-{len(type(self).request_payloads)}"
        chunks = [
            {
                "id": "chatcmpl-session",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": response_text}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-session",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAIHighUsageHandler(BaseHTTPRequestHandler):
    """One successful answer whose billed usage crosses the soft lease."""

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        chunks = [
            {
                "id": "chatcmpl-high-usage",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "completed answer"}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-high-usage",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20_000, "completion_tokens": 32, "total_tokens": 20_032},
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OpenAISlowHandler(BaseHTTPRequestHandler):
    started = threading.Event()
    release = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        first = {
            "id": "chatcmpl-slow",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "working"}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(first)}\n\n".encode("utf-8"))
        self.wfile.flush()
        type(self).started.set()
        type(self).release.wait(timeout=15)
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_pi_sidecar_responds_to_runtime_probe() -> None:
    status = PiAgentClient.runtime_status()

    assert status["ready"] is True
    assert status["runtime"] == "pi"
    assert status["version"] == "0.84.3"
    assert status["protocol"] == 7
    assert {
        "multi_session",
        "ask_user",
        "plan_approval",
        "follow_up",
        "structured_recovery",
        "session_fork",
        "task_contract_v2",
        "host_tool_authorization",
        "structured_mcp_effects",
        "current_request_context",
        "progressive_skills",
        "model_runtime_descriptor",
        "token_envelope",
        "multimodal_turns",
        "deferred_mcp_v2",
        "mcp_effect_audit_v1",
        "mcp_run_cache_v1",
    }.issubset(set(status["capabilities"]))


def test_python_host_rejects_protocol_v6_sidecar_without_deferred_mcp_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, _current_sidecar = PiAgentClient.runtime_paths()
    old_sidecar = tmp_path / "old-pi-sidecar.mjs"
    old_sidecar.write_text(
        """
import * as readline from "node:readline";
const capabilities = [
  "task_contract_v2",
  "explicit_empty_leases",
  "host_tool_authorization",
  "structured_mcp_effects",
  "current_request_context",
  "dynamic_tools",
];
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  const message = JSON.parse(line);
  const required = Array.isArray(message.required_features) ? message.required_features : [];
  process.stdout.write(JSON.stringify({
    type: "pong",
    runtime: "pi",
    version: "old-fixture",
    protocol: 6,
    capabilities,
    negotiated_features: required.filter((feature) => capabilities.includes(feature)),
    missing_features: required.filter((feature) => !capabilities.includes(feature)),
  }) + "\\n");
});
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        PiAgentClient,
        "runtime_paths",
        staticmethod(lambda: (node, old_sidecar)),
    )

    with pytest.raises(pi_agent.PiRuntimeUnavailable, match="protocol=6"):
        PiAgentClient.runtime_status()


def test_pi_sidecar_rejects_incompatible_protocol_before_starting_a_run() -> None:
    node, sidecar = PiAgentClient.runtime_paths()
    process = subprocess.Popen(
        [str(node), str(sidecar)],
        env=PiAgentClient._node_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps({
            "type": "run.start",
            "pi_protocol_version": 3,
            "required_features": ["task_contract_v2"],
            "request_id": "protocol-mismatch",
            "session_id": "protocol-mismatch",
        }) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
    finally:
        process.kill()
        process.wait(timeout=5)

    assert response["type"] == "run.failed"
    assert response["failure"]["code"] == "protocol_incompatible"
    assert response["failure"]["protocol"] == 3


@pytest.mark.parametrize(
    "invalid_contract",
    [
        {},
        {"schema_version": "scansci.task-contract.v999"},
        {"schema_version": "scansci.task-contract.v999", "version": 999},
        {"version": 999},
        {"schema_version": "scansci.task-contract.v1", "version": 2},
        {"schema_version": "scansci.task-contract.v2", "version": {"major": 2}},
    ],
)
def test_pi_sidecar_invalid_contract_version_exposes_no_domain_tools(
    tmp_path: Path,
    invalid_contract: dict[str, object],
) -> None:
    _OpenAIStreamHandler.request_payload = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIStreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    node, sidecar = PiAgentClient.runtime_paths()
    environment = PiAgentClient._node_environment()
    environment["SCANSCIPI_PROVIDER_KEY"] = "fixture-key"
    process = subprocess.Popen(
        [str(node), str(sidecar)],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps({
            "type": "run.start",
            "pi_protocol_version": 7,
            "required_features": list(pi_agent._PI_REQUIRED_FEATURES),
            "request_id": "invalid-contract-version",
            "session_id": "invalid-contract-version",
            "cwd": str(tmp_path),
            "agent_dir": str(tmp_path / ".agent"),
            "provider_kind": "openai-compatible",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model_id": "fixture-model",
            "thinking_level": "off",
            "system_prompt": "",
            "prompt": "Answer without tools.",
            "images": [],
            "model_runtime": pi_agent.ModelRuntimeDescriptor.for_testing().to_dict(),
            "task_mode": "knowledge",
            "task_contract": {
                **invalid_contract,
                "allowed_tools": ["inspect_workspace"],
                "initial_tools": ["inspect_workspace"],
                "risk_level": "read_only",
            },
            "mcp_servers": [],
            "disabled_tools": [],
        }) + "\n")
        process.stdin.flush()
        while True:
            event = json.loads(process.stdout.readline())
            if event.get("type") in {"run.completed", "run.failed"}:
                break
    finally:
        process.kill()
        process.wait(timeout=5)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert event["type"] == "run.completed"
    advertised = {
        str(dict(tool.get("function", {}) or {}).get("name", ""))
        for tool in list(_OpenAIStreamHandler.request_payload.get("tools", []) or [])
        if isinstance(tool, dict)
    }
    assert {"search_skills", "load_skill"} <= advertised
    assert advertised <= {
        "ask_user",
        "search_tools",
        "search_skills",
        "load_skill",
        "submit_plan",
    }


def test_pi_stream_forwards_host_owned_task_contract(tmp_path: Path, monkeypatch) -> None:
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    captured: dict = {}

    def fake_run_request(start_message, *, api_key, timeout_seconds):
        captured.update(start_message)
        assert api_key == "secret"
        assert timeout_seconds == 30
        yield {"type": "done", "stats": {}, "truncated": False}

    monkeypatch.setattr(client, "_run_request", fake_run_request)
    events = list(
        client.stream_chat(
            provider_kind="openai-compatible",
            base_url="https://example.test/v1",
            api_key="secret",
            model_id="fixture",
            messages=[{"role": "user", "content": "search my library"}],
            task_mode="knowledge",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "contract-test",
                "risk_level": "read_only",
                "allowed_tools": ["kb_search"],
            },
            timeout_seconds=30,
        )
    )

    assert events[-1]["type"] == "done"
    assert captured["task_contract"]["contract_id"] == "contract-test"
    assert captured["task_contract"]["allowed_tools"] == ["kb_search"]
    assert captured["task_contract"]["schema_version"] == "scansci.task-contract.v2"
    assert captured["pi_protocol_version"] == 7
    assert "host_tool_authorization" in captured["required_features"]
    assert "ephemeral_sessions" in captured["required_features"]
    assert captured["ephemeral_session"] is True


def test_pi_direct_stream_infers_runtime_metadata_from_matching_saved_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace.sqlite"
    save_settings(workspace, {
        "active_model": {"provider_id": "local-qwen", "model_id": "qwen3.5:4b"},
        "providers": [{
            "id": "local-qwen",
            "name": "Local Qwen",
            "kind": "openai-compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "models": [{
                "id": "qwen3.5:4b",
                "name": "Qwen3.5 4B",
                "context_window": "32K",
                "capabilities": ["reasoning", "tool", "coding"],
            }],
        }],
    })
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")
    captured: dict[str, object] = {}

    def fake_run_request(start_message, *, api_key, timeout_seconds):
        captured.update(start_message)
        yield {"type": "done", "stats": {}, "truncated": False}

    monkeypatch.setattr(client, "_run_request", fake_run_request)
    monkeypatch.setattr(pi_agent, "warm_ollama_chat_model", lambda _base, _model: iter(()))
    events = list(client.stream_chat(
        provider_kind="ollama",
        base_url="http://127.0.0.1:11434/v1",
        api_key="",
        model_id="qwen3.5:4b",
        messages=[{"role": "user", "content": "download one paper"}],
        task_mode="general",
        task_contract={
            **_TASK_CONTRACT_V2,
            "contract_id": "runtime-inference",
            "risk_level": "read_only",
            "allowed_tools": [],
        },
        timeout_seconds=30,
        session_id="runtime-inference-session",
    ))

    assert events[-1]["type"] == "done"
    runtime = captured["model_runtime"]
    assert isinstance(runtime, dict)
    assert runtime["provider_id"] == "local-qwen"
    assert runtime["context_window_tokens"] == 32 * 1024
    assert runtime["capabilities"] == ["reasoning", "tool", "coding"]
    assert runtime["degraded"] is False


def test_pi_qwen_thinking_reaches_native_ollama_on_custom_loopback_port(tmp_path: Path) -> None:
    _OllamaNativeCaptureHandler.request_payload = {}
    _OllamaNativeCaptureHandler.request_path = ""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaNativeCaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        descriptor = pi_agent.ModelRuntimeDescriptor.for_testing(context_window_tokens=16 * 1024).to_dict()
        descriptor.update({
            "provider_id": "local-runtime-ollama",
            "provider_kind": "ollama",
            "model_id": "qwen3.5:9b",
        })
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(client.stream_chat(
            provider_kind="ollama",
            provider_id="local-runtime-ollama",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="ollama",
            model_id="qwen3.5:9b",
            model_runtime=descriptor,
            messages=[{"role": "user", "content": "Say hello."}],
            thinking_level="high",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "ollama-custom-port-thinking",
                "risk_level": "read_only",
                "allowed_tools": [],
                "initial_tools": [],
            },
            timeout_seconds=30,
        ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    assert _OllamaNativeCaptureHandler.request_path == "/api/chat"
    assert _OllamaNativeCaptureHandler.request_payload["think"] is True
    assert _OllamaNativeCaptureHandler.request_payload["options"]["num_ctx"] == 16 * 1024


def test_pi_ollama_cold_load_status_precedes_native_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    warmed = []

    def fake_warm(base_url, model_id):
        warmed.append((base_url, model_id))
        yield {"type": "status", "status": "local_model_loading", "name": model_id}
        yield {"type": "status", "status": "local_model_ready", "name": model_id}

    monkeypatch.setattr(pi_agent, "warm_ollama_chat_model", fake_warm)
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(client, "_run_request", lambda *_args, **_kwargs: iter([{"type": "done", "stats": {}}]))
    descriptor = pi_agent.ModelRuntimeDescriptor.for_testing().to_dict()
    descriptor.update({"provider_id": "local-runtime-ollama", "provider_kind": "ollama", "model_id": "qwen3.5:4b"})

    events = list(client.stream_chat(
        provider_kind="ollama",
        provider_id="local-runtime-ollama",
        base_url="http://127.0.0.1:11555/v1",
        api_key="ollama",
        model_id="qwen3.5:4b",
        model_runtime=descriptor,
        messages=[{"role": "user", "content": "你好"}],
        timeout_seconds=5,
    ))

    assert warmed == [("http://127.0.0.1:11555/v1", "qwen3.5:4b")]
    assert [event.get("status") for event in events[:2]] == ["local_model_loading", "local_model_ready"]
    assert events[-1]["type"] == "done"


def test_pi_ollama_native_stream_surfaces_error_line(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaNativeErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        descriptor = pi_agent.ModelRuntimeDescriptor.for_testing().to_dict()
        descriptor.update({"provider_id": "local-runtime-ollama", "provider_kind": "ollama", "model_id": "qwen3.5:9b"})
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        with pytest.raises(pi_agent.PiAgentRunError, match="model runner out of memory"):
            list(client.stream_chat(
                provider_kind="ollama",
                provider_id="local-runtime-ollama",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="ollama",
                model_id="qwen3.5:9b",
                model_runtime=descriptor,
                messages=[{"role": "user", "content": "你好"}],
                timeout_seconds=20,
            ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_pi_ollama_tool_call_in_final_native_event_executes(tmp_path: Path) -> None:
    _OllamaNativeFinalToolHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaNativeFinalToolHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        descriptor = pi_agent.ModelRuntimeDescriptor.for_testing(context_window_tokens=32 * 1024).to_dict()
        descriptor.update({
            "provider_id": "local-runtime-ollama-qwen3.8-4b-distill",
            "provider_kind": "ollama",
            "model_id": "qwen3.8-4b-distill:latest",
        })
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(client.stream_chat(
            provider_kind="ollama",
            provider_id="local-runtime-ollama-qwen3.8-4b-distill",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="ollama",
            model_id="qwen3.8-4b-distill:latest",
            model_runtime=descriptor,
            messages=[{"role": "user", "content": "Call inspect_workspace, then report the notebook count."}],
            thinking_level="off",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "ollama-final-tool-call",
                "risk_level": "read_only",
                "allowed_tools": ["inspect_workspace"],
                "initial_tools": ["inspect_workspace"],
            },
            timeout_seconds=30,
        ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    assert any(event.get("type") == "tool.completed" and event.get("name") == "inspect_workspace" for event in events)
    assert len(_OllamaNativeFinalToolHandler.request_payloads) == 2


def test_pi_stream_forwards_compact_skill_catalog_without_expanding_domain_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    captured: dict[str, object] = {}

    def fake_run_request(start_message, *, api_key, timeout_seconds):
        captured.update(start_message)
        yield {"type": "done", "stats": {}, "truncated": False}

    monkeypatch.setattr(client, "_run_request", fake_run_request)
    task_contract = {
        **_TASK_CONTRACT_V2,
        "contract_id": "empty-skill-control-plane",
        "risk_level": "read_only",
        "allowed_tools": [],
        "initial_tools": [],
    }
    selected = [{
        "id": "nature-response",
        "name": "nature-response",
        "source": "builtin:nature-response",
        "provenance": "inferred",
        "status": "hint",
        "package_hash": "sha256:fixture",
    }]

    list(client.stream_chat(
        provider_kind="openai-compatible",
        base_url="https://example.test/v1",
        api_key="secret",
        model_id="fixture",
        messages=[{"role": "user", "content": "reply to reviewers"}],
        task_mode="general",
        task_contract=task_contract,
        selected_skills=selected,
        timeout_seconds=30,
    ))

    assert "progressive_skills" in captured["required_features"]
    assert captured["task_contract"]["allowed_tools"] == []
    assert captured["task_contract"]["initial_tools"] == []
    assert captured["skill_selection"] == selected
    assert 0 < len(captured["skill_catalog"]) <= 64
    assert "search_skills" not in captured["tool_set"]["registered_tools"]
    assert "load_skill" not in captured["tool_set"]["active_tools"]


def test_required_artifacts_do_not_expand_initial_tool_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    captured: dict[str, object] = {}

    def fake_run_request(start_message, *, api_key, timeout_seconds):
        captured.update(start_message)
        yield {"type": "done", "stats": {}, "truncated": False}

    monkeypatch.setattr(client, "_run_request", fake_run_request)
    list(client.stream_chat(
        provider_kind="openai-compatible",
        base_url="https://example.test/v1",
        api_key="secret",
        model_id="fixture",
        messages=[{"role": "user", "content": "Use evidence to create a document and slides."}],
        task_mode="slides",
        task_contract={
            **_TASK_CONTRACT_V2,
            "contract_id": "deferred-required-artifacts",
            "risk_level": "reversible",
            "allowed_tools": ["search_local_evidence", "create_document", "create_presentation"],
            "initial_tools": ["search_local_evidence"],
            "required_tool_groups": [
                ["search_local_evidence"], ["create_document"], ["create_presentation"],
            ],
        },
        timeout_seconds=30,
    ))

    assert set(captured["tool_set"]["registered_tools"]) >= {
        "search_local_evidence", "create_document", "create_presentation",
    }
    assert "search_local_evidence" in captured["tool_set"]["active_tools"]
    assert "create_document" not in captured["tool_set"]["active_tools"]
    assert "create_presentation" not in captured["tool_set"]["active_tools"]


def test_skill_control_plane_accepts_empty_domain_lease_and_rejects_spoofed_domain_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    executed: list[tuple[str, dict[str, object]]] = []
    written: list[dict[str, object]] = []
    fake_process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(client, "_ensure_process", lambda **_kwargs: fake_process)
    monkeypatch.setattr(client, "_write", lambda message: written.append(dict(message)))
    monkeypatch.setattr(
        client,
        "_execute_skill_tool",
        lambda request_id, name, arguments: executed.append((name, dict(arguments))) or {
            "skill_id": "fixture-skill",
            "content": "instruction only",
            "content_hash": "sha256:fixture",
        },
        raising=False,
    )
    client._output.put(json.dumps({
        "type": "skill.call",
        "request_id": "request-current",
        "call_id": "skill-call",
        "name": "load_skill",
        "arguments": {"skill_id": "fixture-skill"},
    }))
    client._output.put(json.dumps({
        "type": "tool.call",
        "request_id": "request-current",
        "call_id": "domain-spoof",
        "name": "inspect_workspace",
        "arguments": {},
    }))
    client._output.put(json.dumps({
        "type": "run.completed",
        "request_id": "request-current",
        "stats": {},
    }))
    contract = {
        "schema_version": "scansci.task-contract.v2",
        "version": 2,
        "allowed_tools": [],
        "initial_tools": [],
        "risk_level": "read_only",
        "max_tool_budget": 2,
    }

    events = list(client._run_request(
        {
            "type": "run.start",
            "request_id": "request-current",
            "session_id": "session-current",
            "task_mode": "general",
            "task_contract": contract,
        },
        api_key="fixture",
        timeout_seconds=5,
    ))

    assert executed == [("load_skill", {"skill_id": "fixture-skill"})]
    skill_result = next(message for message in written if message.get("call_id") == "skill-call")
    assert skill_result["type"] == "skill.result"
    assert skill_result["ok"] is True
    rejected = next(message for message in written if message.get("call_id") == "domain-spoof")
    assert rejected["type"] == "tool.result"
    assert rejected["ok"] is False
    assert contract["allowed_tools"] == []
    assert any(event.get("type") == "skill.loaded" for event in events)


def test_pi_bridge_forwards_live_reasoning_for_the_active_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(client, "_ensure_process", lambda **_kwargs: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(client, "_write", lambda _message: None)
    for event_type, delta in (("message.reasoning_delta", "Check evidence"), ("message.delta", "Answer")):
        client._output.put(json.dumps({"type": event_type, "request_id": "reasoning-request", "delta": delta}))
    client._output.put(json.dumps({"type": "run.completed", "request_id": "reasoning-request", "stats": {}}))

    events = list(client._run_request(
        {
            "type": "run.start",
            "request_id": "reasoning-request",
            "session_id": "reasoning-session",
            "task_mode": "general",
            "task_contract": {**_TASK_CONTRACT_V2, "allowed_tools": [], "initial_tools": [], "risk_level": "read_only"},
        },
        api_key="fixture",
        timeout_seconds=5,
    ))

    assert [(event["type"], event.get("content")) for event in events if event["type"] in {"reasoning", "delta"}] == [
        ("reasoning", "Check evidence"),
        ("delta", "Answer"),
    ]


def test_skill_control_plane_rejects_missing_request_identity_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    executed: list[object] = []
    written: list[dict[str, object]] = []
    monkeypatch.setattr(client, "_ensure_process", lambda **_kwargs: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(client, "_write", lambda message: written.append(dict(message)))
    monkeypatch.setattr(
        client,
        "_execute_skill_tool",
        lambda *_args: executed.append(_args) or {},
        raising=False,
    )
    client._output.put(json.dumps({
        "type": "skill.call",
        "call_id": "missing-request",
        "name": "search_skills",
        "arguments": {"query": "statistics"},
    }))
    client._output.put(json.dumps({
        "type": "skill.call",
        "request_id": "request-other",
        "call_id": "wrong-request",
        "name": "load_skill",
        "arguments": {"skill_id": "nature-statistics"},
    }))
    client._output.put(json.dumps({
        "type": "run.completed",
        "request_id": "request-current",
        "stats": {},
    }))

    list(client._run_request(
        {
            "type": "run.start",
            "request_id": "request-current",
            "session_id": "session-current",
            "task_mode": "general",
            "task_contract": {
                "schema_version": "scansci.task-contract.v2",
                "allowed_tools": [],
                "risk_level": "read_only",
            },
        },
        api_key="fixture",
        timeout_seconds=5,
    ))

    assert executed == []
    rejected = next(message for message in written if message.get("call_id") == "missing-request")
    assert rejected["type"] == "skill.result"
    assert rejected["ok"] is False
    assert "request" in str(rejected["error"]).lower()
    wrong_request = next(message for message in written if message.get("call_id") == "wrong-request")
    assert wrong_request["type"] == "skill.result"
    assert wrong_request["ok"] is False
    assert wrong_request["request_id"] == "request-current"
    assert "request" in str(wrong_request["error"]).lower()


def test_persisted_skill_priority_reads_only_the_active_session_branch(tmp_path: Path) -> None:
    from scansci_html.pi_agent import _persisted_skill_state

    def metadata(skill_id: str) -> dict[str, object]:
        return {
            "skill_id": skill_id,
            "resource": "SKILL.md",
            "package_hash": "sha256:" + ("a" * 64),
            "content_hash": "sha256:" + ("b" * 64),
            "provenance": "model",
            "bytes": 12,
        }

    session_file = tmp_path / "branched-session.jsonl"
    records = [
        {"type": "session", "version": 3, "id": "session-id", "cwd": str(tmp_path)},
        {
            "type": "custom", "id": "root", "parentId": None,
            "customType": "scansci.skill-state.v1", "data": metadata("active-root"),
        },
        {"type": "message", "id": "fork", "parentId": "root", "message": {"role": "user", "content": "fork"}},
        {
            "type": "custom", "id": "stale", "parentId": "fork",
            "customType": "scansci.skill-state.v1", "data": metadata("stale-branch"),
        },
        {"type": "message", "id": "leaf", "parentId": "fork", "message": {"role": "user", "content": "active"}},
    ]
    session_file.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    state = _persisted_skill_state(session_file)

    assert state["schema"] == "scansci.skill-state.v1"
    assert [item["skill_id"] for item in state["loaded"]] == ["active-root"]
    assert all(set(item) == {
        "skill_id", "resource", "package_hash", "content_hash", "provenance", "bytes",
    } for item in state["loaded"])


def test_progressive_skill_sidecar_load_resume_and_compaction_preserve_hash_and_provenance(
    tmp_path: Path,
) -> None:
    _OpenAIProgressiveSkillHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIProgressiveSkillHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    common = {
        "provider_kind": "openai-compatible",
        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "api_key": "fixture-key",
        "model_id": "fixture-model",
        "model_runtime": pi_agent.ModelRuntimeDescriptor.for_testing(
            context_window_tokens=200 * 1024,
        ).to_dict(),
        "thinking_level": "off",
        "task_mode": "general",
        "task_contract": {
            **_TASK_CONTRACT_V2,
            "contract_id": "progressive-skill-empty-domain",
            "allowed_tools": [],
            "initial_tools": [],
            "risk_level": "read_only",
        },
        "selected_skills": [{
            "id": "good-question",
            "provenance": "inferred",
            "status": "hint",
        }],
        "timeout_seconds": 30,
        "session_id": "progressive-skill-session",
    }
    first_client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    first_skill_calls: list[tuple[str, str, dict[str, object]]] = []
    first_wire_messages: list[dict[str, object]] = []
    original_execute_skill = first_client._execute_skill_tool
    original_first_write = first_client._write

    def record_skill_call(request_id, name, arguments):
        first_skill_calls.append((str(request_id), str(name), dict(arguments)))
        return original_execute_skill(request_id, name, arguments)

    def record_first_wire(message):
        first_wire_messages.append(dict(message))
        return original_first_write(message)

    first_client._execute_skill_tool = record_skill_call
    first_client._write = record_first_wire
    try:
        first = list(first_client.stream_chat(
            messages=[{
                "role": "user",
                "content": "Use the relevant method instructions. " + ("context " * 5_000),
            }],
            **common,
        ))
        session_file = Path(str(next(event for event in first if event["type"] == "session")["session_file"]))
        for turn in range(1, 4):
            continuation = list(first_client.stream_chat(
                messages=[{
                    "role": "user",
                    "content": f"Continue turn {turn}. " + ("context " * 5_000),
                }],
                **common,
            ))
            assert continuation[-1]["type"] == "done"
        compacted = first_client.compact(
            "progressive-skill-session",
            instructions="Retain the selected Skill state.",
            timeout_seconds=30,
        )
    finally:
        first_client.close()

    second_client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    second_skill_calls: list[tuple[str, str, dict[str, object]]] = []
    second_wire_messages: list[dict[str, object]] = []
    original_second_execute_skill = second_client._execute_skill_tool
    original_second_write = second_client._write

    def record_restored_skill_call(request_id, name, arguments):
        second_skill_calls.append((str(request_id), str(name), dict(arguments)))
        return original_second_execute_skill(request_id, name, arguments)

    def record_second_wire(message):
        second_wire_messages.append(dict(message))
        return original_second_write(message)

    second_client._execute_skill_tool = record_restored_skill_call
    second_client._write = record_second_wire
    try:
        second = list(second_client.stream_chat(
            messages=[{"role": "user", "content": "Continue using the same method."}],
            **common,
        ))
    finally:
        second_client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert compacted["summary"]
    assert first[-1]["type"] == "done"
    assert second[-1]["type"] == "done"
    loaded_event = next(event for event in first if event["type"] == "skill.loaded")
    assert loaded_event["value"]["provenance"] == "inferred"
    assert "content" not in loaded_event["value"]
    assert first_skill_calls == [
        (first_skill_calls[0][0], "load_skill", {"skill_id": "good-question", "provenance": "inferred"}),
    ]
    first_skill_result = next(message for message in first_wire_messages if message.get("type") == "skill.result")
    assert first_skill_result["ok"] is True
    assert first_skill_result["request_id"] == first_skill_calls[0][0]
    assert second_skill_calls == [
        (second_skill_calls[0][0], "restore_skill", {
            "skill_id": "good-question",
            "resource": "SKILL.md",
        }),
    ]
    assert any(message.get("type") == "skill.result" and message.get("ok") is True for message in second_wire_messages)
    first_tools = {
        item["function"]["name"]
        for item in _OpenAIProgressiveSkillHandler.request_payloads[0]["tools"]
    }
    assert {"search_skills", "load_skill"} <= first_tools
    assert "inspect_workspace" not in first_tools
    first_inventory = first[-1]["stats"]["toolInventory"]
    assert "search_skills" not in first_inventory["names"]
    assert "load_skill" not in first_inventory["names"]
    assert "search_skills" not in first_inventory["registeredNames"]
    assert "load_skill" not in first_inventory["registeredNames"]
    first_followup = json.dumps(_OpenAIProgressiveSkillHandler.request_payloads[1], ensure_ascii=False)
    assert "instructions_only" in first_followup
    assert "good-question" in first_followup
    skill_sentinel = "Help a researcher turn a vague interest, literature gap, rough idea"
    provider_skill_counts = [
        json.dumps(payload.get("messages", []), ensure_ascii=False).count(skill_sentinel)
        for payload in _OpenAIProgressiveSkillHandler.request_payloads
    ]
    assert any(count == 1 for count in provider_skill_counts)
    assert max(provider_skill_counts) == 1, provider_skill_counts

    records = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    skill_records = [
        record for record in records
        if record.get("type") == "custom" and record.get("customType") == "scansci.skill-state.v1"
    ]
    assert skill_records
    state_data = skill_records[-1]["data"]
    assert set(state_data) == {
        "skill_id", "resource", "package_hash", "content_hash", "provenance", "bytes",
    }
    assert state_data["provenance"] == "inferred"
    assert str(tmp_path) not in json.dumps(state_data, ensure_ascii=False)
    assert any(record.get("type") == "compaction" for record in records)

    restored = next(
        event for event in second
        if event.get("type") == "status" and event.get("status") == "skill_restored"
    )
    assert restored["details"]["content_hash"] == state_data["content_hash"]
    first_hash = first[-1]["stats"]["skillInventory"]["loadedHash"]
    second_hash = second[-1]["stats"]["skillInventory"]["loadedHash"]
    assert first_hash == second_hash
    resumed_system = "\n".join(
        str(message.get("content", ""))
        for message in _OpenAIProgressiveSkillHandler.request_payloads[-1]["messages"]
        if message.get("role") in {"system", "developer"}
    )
    assert '<loaded_skill id="good-question"' in resumed_system
    assert state_data["content_hash"] in resumed_system
    sentinel = "Help a researcher turn a vague interest, literature gap, rough idea"
    assert resumed_system.count(sentinel) == 1


def test_repeated_skill_load_transmits_content_and_custom_state_once(tmp_path: Path) -> None:
    _OpenAIRepeatedSkillHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIRepeatedSkillHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    wire_messages: list[dict[str, object]] = []
    original_write = client._write

    def record_wire(message):
        wire_messages.append(dict(message))
        return original_write(message)

    client._write = record_wire
    try:
        events = list(client.stream_chat(
            messages=[{"role": "user", "content": "Load the method once and reuse it."}],
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            model_runtime=pi_agent.ModelRuntimeDescriptor.for_testing(
                context_window_tokens=200 * 1024,
            ).to_dict(),
            thinking_level="off",
            task_mode="general",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "repeated-skill-empty-domain",
                "allowed_tools": [],
                "initial_tools": [],
                "risk_level": "read_only",
            },
            selected_skills=[{
                "id": "good-question",
                "provenance": "inferred",
                "status": "hint",
            }],
            timeout_seconds=30,
            session_id="repeated-skill-session",
        ))
        session_file = Path(str(next(event for event in events if event["type"] == "session")["session_file"]))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    skill_results = [message for message in wire_messages if message.get("type") == "skill.result"]
    assert len(skill_results) == 5
    assert all(message.get("ok") is True for message in skill_results)
    assert "content" in skill_results[0]["result"]
    assert all(message["result"].get("already_loaded") is True for message in skill_results[1:])
    assert all("content" not in message["result"] for message in skill_results[1:])

    final_messages = _OpenAIRepeatedSkillHandler.request_payloads[-1]["messages"]
    tool_messages = [message for message in final_messages if message.get("role") == "tool"]
    load_results = [message for message in tool_messages if "good-question" in str(message.get("content", ""))]
    assert len(load_results) == 5
    assert sum('"instructions"' in str(message.get("content", "")) for message in load_results) == 1

    records = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    custom_records = [
        record for record in records
        if record.get("type") == "custom" and record.get("customType") == "scansci.skill-state.v1"
    ]
    assert len(custom_records) == 1


def test_explicit_skill_body_appears_once_in_first_provider_request(tmp_path: Path) -> None:
    from scansci_html.agent_context import build_agent_system_context
    from scansci_html.skill_runtime import resolve_skill_selection

    user_text = "Help me sharpen a research question."
    selection = resolve_skill_selection(
        {"skills": ["good-question"]},
        [{"role": "user", "content": user_text}],
    )
    system_context, selected = build_agent_system_context(
        tmp_path / "workspace.sqlite",
        model_id="fixture-model",
        provider_name="fixture-provider",
        chat_mode="general",
        selected_ids=list(selection.selected_ids),
        selection=selection,
    )
    assert selected[0]["status"] == "loaded"

    _OpenAIExplicitSkillPreloadHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIExplicitSkillPreloadHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    try:
        events = list(client.stream_chat(
            messages=[
                {"role": "system", "content": system_context},
                {"role": "user", "content": user_text},
            ],
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            # This test verifies single-channel Skill injection, not the
            # fail-closed 32K policy for an unknown tokenizer.  The selected
            # Skill plus the host system contract is intentionally larger than
            # that unknown-model byte upper bound.
            model_runtime=pi_agent.ModelRuntimeDescriptor.for_testing(
                context_window_tokens=200 * 1024,
            ).to_dict(),
            thinking_level="off",
            task_mode="general",
            task_contract={
                **_TASK_CONTRACT_V2,
                "contract_id": "explicit-skill-single-channel",
                "allowed_tools": [],
                "initial_tools": [],
                "risk_level": "read_only",
            },
            selected_skills=selected,
            timeout_seconds=30,
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    assert events[-1]["stats"]["skillInventory"]["selected"] == 1
    assert events[-1]["stats"]["skillInventory"]["ids"] == ["good-question"]
    first_payload = _OpenAIExplicitSkillPreloadHandler.request_payloads[0]
    provider_text = "\n".join(str(message.get("content", "")) for message in first_payload["messages"])
    sentinel = "Help a researcher turn a vague interest, literature gap, rough idea"
    assert provider_text.count(sentinel) == 1


def test_restart_restores_out_of_catalog_skill_without_spending_model_skill_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import scansci_html.agent_skill_tools as agent_skill_tools
    import scansci_html.pi_agent as pi_agent_module

    records: list[dict[str, object]] = []
    for index in range(65):
        identifier = f"safe-{index:02d}"
        package = tmp_path / "skills" / identifier
        package.mkdir(parents=True)
        skill_file = package / "SKILL.md"
        skill_file.write_text(f"# {identifier}\n", encoding="utf-8")
        records.append({
            "id": identifier,
            "name": identifier,
            "description": f"bounded fixture {identifier}",
            "enabled": True,
            "available": True,
            "builtin": True,
            "source_type": "builtin",
            "source": "ScanSci",
            "package_path": str(package),
            "skill_file": str(skill_file),
        })
    target = records[-1]
    target_package = Path(str(target["package_path"]))
    (target_package / "one.md").write_text("# one\n", encoding="utf-8")
    (target_package / "two.md").write_text("# two\n", encoding="utf-8")

    seed_runtime = agent_skill_tools.ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=records,
        priority_ids=["safe-64"],
    )
    selected_skills = []
    for resource in ("SKILL.md", "one.md", "two.md"):
        loaded = seed_runtime.load_skill("safe-64", resource=resource, provenance="explicit")
        selected_skills.append({
            "id": loaded["skill_id"],
            "name": "safe-64",
            "source": loaded["source"],
            "provenance": "explicit",
            "status": "loaded",
            "resource": loaded["resource"],
            "package_hash": loaded["package_hash"],
            "content_hash": loaded["content_hash"],
            "bytes": loaded["bytes"],
        })

    real_runtime = agent_skill_tools.ProgressiveSkillRuntime

    def limited_runtime(*args, **kwargs):
        kwargs["max_instruction_calls"] = 2
        return real_runtime(*args, **kwargs)

    monkeypatch.setattr(agent_skill_tools, "installed_skills", lambda _workspace: records)
    monkeypatch.setattr(pi_agent_module, "ProgressiveSkillRuntime", limited_runtime)
    _OpenAIResumeBudgetHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIResumeBudgetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    common = {
        "provider_kind": "openai-compatible",
        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "api_key": "fixture-key",
        "model_id": "fixture-model",
        "thinking_level": "off",
        "task_mode": "general",
        "task_contract": {
            **_TASK_CONTRACT_V2,
            "contract_id": "restore-budget-empty-domain",
            "allowed_tools": [],
            "initial_tools": [],
            "risk_level": "read_only",
        },
        "timeout_seconds": 30,
        "session_id": "restore-budget-session",
    }
    first_client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    first_calls: list[tuple[str, dict[str, object]]] = []
    first_wire: list[dict[str, object]] = []
    original_first_execute = first_client._execute_skill_tool
    original_first_write = first_client._write

    def record_first_execute(request_id, name, arguments):
        first_calls.append((str(name), dict(arguments)))
        return original_first_execute(request_id, name, arguments)

    def record_first_wire(message):
        first_wire.append(dict(message))
        return original_first_write(message)

    first_client._execute_skill_tool = record_first_execute
    first_client._write = record_first_wire
    try:
        first = list(first_client.stream_chat(
            messages=[{"role": "user", "content": "Seed the explicit method state."}],
            selected_skills=selected_skills,
            **common,
        ))
    finally:
        first_client.close()

    second_client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    second_calls: list[tuple[str, dict[str, object]]] = []
    second_wire: list[dict[str, object]] = []
    original_execute = second_client._execute_skill_tool
    original_write = second_client._write

    def record_execute(request_id, name, arguments):
        second_calls.append((str(name), dict(arguments)))
        return original_execute(request_id, name, arguments)

    def record_wire(message):
        second_wire.append(dict(message))
        return original_write(message)

    second_client._execute_skill_tool = record_execute
    second_client._write = record_wire
    try:
        second = list(second_client.stream_chat(
            messages=[{"role": "user", "content": "Continue with no composer Skill selection."}],
            selected_skills=[],
            **common,
        ))
    finally:
        second_client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    first_start = next(message for message in first_wire if message.get("type") == "run.start")
    assert len(first_start["skill_state"]["loaded"]) == 3, first_start["skill_state"]
    first_loaded = first[-1]["stats"]["skillInventory"]["loaded"]
    second_loaded = second[-1]["stats"]["skillInventory"]["loaded"]
    assert len(first_loaded) == 3, {
        "calls": first_calls,
        "skill_results": [message for message in first_wire if message.get("type") == "skill.result"],
        "start": next((message for message in first_wire if message.get("type") == "run.start"), {}),
        "events": [event for event in first if str(event.get("type", "")).startswith("skill")],
    }
    assert second_loaded == first_loaded
    assert second[-1]["stats"]["skillInventory"]["loadedHash"] == first[-1]["stats"]["skillInventory"]["loadedHash"]
    assert [name for name, _args in second_calls[:3]] == ["restore_skill"] * 3
    assert [name for name, _args in second_calls[-2:]] == ["search_skills", "load_skill"]
    all_skill_results = [message for message in second_wire if message.get("type") == "skill.result"]
    assert len(all_skill_results) == 5
    model_results = all_skill_results[-2:]
    assert len(model_results) == 2
    assert all(message.get("ok") is True for message in model_results)
    assert model_results[-1]["result"]["already_loaded"] is True

    start = next(message for message in second_wire if message.get("type") == "run.start")
    catalog = start["skill_catalog"]
    assert len(catalog) == 64
    assert len(json.dumps(catalog, ensure_ascii=False).encode("utf-8")) <= 16 * 1024
    assert "safe-64" in {item["id"] for item in catalog}
    advertised = {
        item["function"]["name"]
        for payload in _OpenAIResumeBudgetHandler.request_payloads
        for item in payload.get("tools", [])
    }
    assert {"search_skills", "load_skill"} <= advertised
    assert "restore_skill" not in advertised


def test_pi_backed_json_client_keeps_two_transient_sidecar_sessions_off_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIJsonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observed_processes: list[subprocess.Popen[str]] = []
    original_ensure_process = PiAgentClient._ensure_process

    def recording_ensure_process(self, *, api_key: str):
        process = original_ensure_process(self, api_key=api_key)
        observed_processes.append(process)
        return process

    monkeypatch.setattr(PiAgentClient, "_ensure_process", recording_ensure_process)
    client = _PiBackedChatJsonClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
        provider_kind="openai-compatible",
        provider_id="fixture-provider",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key="fixture-key",
        model="fixture-model",
        api_surface="chat_completions",
        responses_enabled=False,
        role="writing",
        timeout_seconds=30,
    )
    try:
        first = client.complete_json(
            [{"role": "user", "content": "Return the first JSON result."}],
            schema_name="ephemeral_adapter_fixture",
        )
        second = client.complete_json(
            [{"role": "user", "content": "Return the second JSON result."}],
            schema_name="ephemeral_adapter_fixture",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert first == second == {"value": "ephemeral"}
    assert len(observed_processes) == 2
    assert all(process.poll() is not None for process in observed_processes)
    agent_dir = tmp_path / ".scansci-pi-agent"
    registry_path = agent_dir / "sessions.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    assert registry == {}
    assert list((agent_dir / "sessions").glob("*.jsonl")) == []


def test_python_bridge_reauthorizes_spoofed_tool_call_before_dispatch(tmp_path: Path, monkeypatch) -> None:
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    dispatched: list[tuple[str, dict[str, object]]] = []
    written: list[dict[str, object]] = []
    fake_process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(client, "_ensure_process", lambda **_kwargs: fake_process)
    monkeypatch.setattr(client, "_write", lambda message: written.append(dict(message)))
    monkeypatch.setattr(
        client,
        "_execute_tool",
        lambda name, arguments: dispatched.append((name, dict(arguments))) or {"ok": True},
    )
    client._output.put(json.dumps({
        "type": "tool.call",
        "request_id": "request-current",
        "call_id": "spoofed-call",
        "name": "download_and_index",
        "arguments": {"identifiers": ["10.1/spoofed"]},
    }))
    client._output.put(json.dumps({
        "type": "run.completed",
        "request_id": "request-current",
        "stats": {},
    }))

    events = list(client._run_request(
        {
            "type": "run.start",
            "request_id": "request-current",
            "session_id": "session-current",
            "task_mode": "knowledge",
            "task_contract": {
                "schema_version": "scansci.task-contract.v2",
                "allowed_tools": ["inspect_workspace"],
                "risk_level": "read_only",
                "max_tool_budget": 2,
            },
        },
        api_key="fixture",
        timeout_seconds=5,
    ))

    assert dispatched == []
    rejected = next(message for message in written if message.get("call_id") == "spoofed-call")
    assert rejected["ok"] is False
    assert "authorization" in str(rejected["error"]).lower() or "lease" in str(rejected["error"]).lower()
    authorization = next(event for event in events if event.get("type") == "tool.authorization")
    failure = next(event for event in events if event.get("type") == "tool.failed")
    assert authorization["decision"] == "deny"
    assert authorization["rule"] == "outside_lease"
    assert authorization["request_id"] == "request-current"
    assert authorization["call_ref"] == failure["call_ref"]
    assert "arguments" not in authorization


def test_python_bridge_rejects_reused_tool_call_id_before_dispatch(tmp_path: Path, monkeypatch) -> None:
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    dispatched: list[str] = []
    written: list[dict[str, object]] = []
    monkeypatch.setattr(client, "_ensure_process", lambda **_kwargs: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(client, "_write", lambda message: written.append(dict(message)))
    monkeypatch.setattr(
        client, "_execute_tool",
        lambda name, _arguments: dispatched.append(name) or {"ok": True},
    )
    for _ in range(2):
        client._output.put(json.dumps({
            "type": "tool.call", "request_id": "request-current", "call_id": "reused-call",
            "name": "inspect_workspace", "arguments": {},
        }))
    client._output.put(json.dumps({
        "type": "tool.call", "request_id": "request-current", "call_id": "",
        "name": "inspect_workspace", "arguments": {},
    }))
    client._output.put(json.dumps({
        "type": "run.completed", "request_id": "request-current", "stats": {},
    }))

    events = list(client._run_request(
        {
            "type": "run.start", "request_id": "request-current", "session_id": "session-current",
            "task_mode": "knowledge",
            "task_contract": {
                "schema_version": "scansci.task-contract.v2", "allowed_tools": ["inspect_workspace"],
                "risk_level": "read_only", "max_tool_budget": 2,
            },
        },
        api_key="fixture", timeout_seconds=5,
    ))

    assert dispatched == ["inspect_workspace"]
    decisions = [event for event in events if event.get("type") == "tool.authorization"]
    assert [(event["decision"], event["rule"]) for event in decisions] == [
        ("allow", "authorized"), ("deny", "duplicate_call_id"),
        ("deny", "missing_call_id"),
    ]
    assert len({event["call_ref"] for event in decisions}) == 3
    failures = [event for event in events if event.get("type") == "tool.failed"]
    assert {event["call_ref"] for event in failures} == {event["call_ref"] for event in decisions[1:]}
    assert len([message for message in written if message.get("call_id") == "reused-call" and message.get("ok") is False]) == 1
    assert len([message for message in written if message.get("call_id") == "" and message.get("ok") is False]) == 1


def test_explicit_empty_tool_lease_advertises_no_domain_tools(tmp_path: Path) -> None:
    _OpenAIStreamHandler.request_payload = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIStreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "Do not use tools."}],
            thinking_level="off",
            task_mode="knowledge",
            task_contract={
                **_TASK_CONTRACT_V2,
                "allowed_tools": [],
                "risk_level": "read_only",
            },
            timeout_seconds=30,
            session_id="empty-lease-session",
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    advertised = {
        str(dict(tool.get("function", {}) or {}).get("name", ""))
        for tool in list(_OpenAIStreamHandler.request_payload.get("tools", []) or [])
        if isinstance(tool, dict)
    }
    assert advertised <= {
        "ask_user", "search_tools", "submit_plan", "search_skills", "load_skill",
    }
    assert {"search_skills", "load_skill"} <= advertised
    domain_inventory = events[-1]["stats"]["toolInventory"]
    assert "search_skills" not in domain_inventory["names"]
    assert "load_skill" not in domain_inventory["names"]
    assert "search_skills" not in domain_inventory["registeredNames"]
    assert "load_skill" not in domain_inventory["registeredNames"]


def test_managed_gateway_adapter_only_parses_explicit_tool_intents() -> None:
    assert _parse_text_tool_intents('[TOOL CALL]\ntool_call(name="inspect_available_tools")') == [
        ("inspect_available_tools", {})
    ]
    assert _parse_text_tool_intents('<SCANSCI_TOOL_CALL>{"name":"verify_doi","arguments":{"doi":"10.1/x"}}</SCANSCI_TOOL_CALL>') == [
        ("verify_doi", {"doi": "10.1/x"})
    ]
    assert _parse_text_tool_intents('{"name":"build_verified_answer","arguments":{"question":"q"}}') == [
        ("build_verified_answer", {"question": "q"})
    ]
    assert _parse_text_tool_intents(
        '{"name":"build_verified_answer","arguments":"{\\"question\\":\\"q\\"}"}'
    ) == [("build_verified_answer", {"question": "q"})]


def test_managed_gateway_adapter_activates_inactive_reader_for_auto_web_turn() -> None:
    content = '<SCANSCI_TOOL_CALL>{"name":"search_web","arguments":{"query":"today technology news"}}</SCANSCI_TOOL_CALL>'

    assert _dynamic_tool_activation_intents(content, allowed_tool_names={"search_tools"}) == [
        ("search_tools", {"names": ["search_web"], "activate": True})
    ]
    assert _dynamic_tool_activation_intents(content, allowed_tool_names={"search_web"}) == []


def test_managed_gateway_adapter_normalizes_glm_legacy_tool_call_for_offered_tool() -> None:
    content = "<tool_call>agent-reach\nquery=\u54c8\u5c14\u6ee8\u91d1\u878d\u5b66\u9662\nsource=public_web"

    assert _parse_text_tool_intents(content, allowed_tool_names={"agent_reach"}) == [
        (
            "agent_reach",
            {
                "operation": "search",
                "query": "\u54c8\u5c14\u6ee8\u91d1\u878d\u5b66\u9662",
                "channel": "web",
            },
        )
    ]
    assert _parse_text_tool_intents(content, allowed_tool_names={"search_web"}) == []


def test_managed_gateway_adapter_names_the_single_mandatory_tool_exactly() -> None:
    payload = _ManagedGatewayAdapter._normalize_payload(
        {
            "messages": [{"role": "user", "content": "Continue\n\n[USER]\nquestion"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "build_verified_answer",
                        "parameters": {
                            "type": "object",
                            "properties": {"question": {"type": "string"}},
                            "required": ["question"],
                        },
                    },
                }
            ],
        }
    )

    instruction = payload["messages"][0]["content"]
    assert '"name":"build_verified_answer"' in instruction
    assert '"question":"question"' in instruction
    assert "exact JSON object" in instruction
    assert payload["max_tokens"] == 192
    assert payload["temperature"] == 0
    assert payload["thinking"] == {"type": "disabled"}


def test_managed_gateway_adapter_closes_completed_tool_loop_briefly() -> None:
    payload = _ManagedGatewayAdapter._normalize_payload(
        {
            "messages": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "function": {"name": "build_verified_answer"}}]},
                {"role": "tool", "tool_call_id": "call-1", "content": "large verified result"},
            ],
            "tools": [{"type": "function", "function": {"name": "build_verified_answer", "parameters": {}}}],
            "tool_choice": "auto",
            "max_tokens": 16384,
        }
    )

    assert "Reply with DONE only" in payload["messages"][0]["content"]
    assert payload["max_tokens"] == 16
    assert payload["temperature"] == 0
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["tools"] == []
    assert "tool_choice" not in payload


def test_managed_gateway_adapter_synthesizes_non_terminal_web_tool_result() -> None:
    payload = _ManagedGatewayAdapter._normalize_payload(
        {
            "messages": [
                {"role": "user", "content": "today's market"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call-web", "function": {"name": "search_web"}}]},
                {"role": "tool", "tool_call_id": "call-web", "content": '{"items":[{"url":"https://example.com"}]}'},
            ],
            "tools": [{"type": "function", "function": {"name": "search_web", "parameters": {}}}],
            "tool_choice": "auto",
            "max_tokens": 16384,
        }
    )

    instruction = payload["messages"][0]["content"]
    assert "Use those results to answer" in instruction
    assert "Reply with DONE only" not in instruction
    assert payload["max_tokens"] == 16384
    assert payload["tools"] == []
    assert "tool_choice" not in payload


def test_managed_gateway_orchestrates_explicit_download_index_and_summary_workflow() -> None:
    available_tools = [
        {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
        for name in ("discover_papers", "download_and_index", "summarize_documents", "check_task_completion")
    ]
    request = {
        "messages": [
            {
                "role": "user",
                "content": "搜索并下载 8 篇 Peter B. Reich 关于森林生产力的论文，完成全文索引，然后比较研究方法和主要结论。",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-discover", "function": {"name": "discover_papers"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-discover",
                "content": json.dumps(
                    {"download_identifiers": [f"10.1000/paper-{index}" for index in range(12)]}
                ),
            },
        ],
        "tools": available_tools,
    }

    download = _ManagedGatewayAdapter._managed_followup_tool_call(request)

    assert download is not None
    assert download["function"]["name"] == "download_and_index"
    assert json.loads(download["function"]["arguments"]) == {
        "identifiers": [f"10.1000/paper-{index}" for index in range(8)]
    }

    default_request = json.loads(json.dumps(request))
    default_request["messages"][0]["content"] = (
        "搜索并下载 Peter B. Reich 关于森林生产力的论文，完成全文索引，然后比较主要结论。"
    )
    default_download = _ManagedGatewayAdapter._managed_followup_tool_call(default_request)
    assert default_download is not None
    assert json.loads(default_download["function"]["arguments"]) == {
        "identifiers": [f"10.1000/paper-{index}" for index in range(3)]
    }

    request["messages"].extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-download", "function": {"name": "download_and_index"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-download",
                "content": json.dumps({"ok": True, "run_id": "run-1"}),
            },
        ]
    )
    summarize = _ManagedGatewayAdapter._managed_followup_tool_call(request)

    assert summarize is not None
    assert summarize["function"]["name"] == "summarize_documents"
    assert json.loads(summarize["function"]["arguments"]) == {"run_id": "run-1"}

    request["messages"].extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-summary", "function": {"name": "summarize_documents"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-summary",
                "content": json.dumps({"ok": True, "run_id": "run-1", "documents": []}),
            },
        ]
    )
    completion = _ManagedGatewayAdapter._managed_followup_tool_call(request)

    assert completion is not None
    assert completion["function"]["name"] == "check_task_completion"
    assert json.loads(completion["function"]["arguments"]) == {"run_id": "run-1"}


def test_managed_gateway_downloads_an_exact_doi_without_fuzzy_discovery() -> None:
    request = {
        "messages": [
            {
                "role": "user",
                "content": "下载 DOI 10.2307/2679812，完成全文索引并写一篇综述。",
            }
        ],
        "tools": [
            {"type": "function", "function": {"name": "discover_papers", "parameters": {}}},
            {"type": "function", "function": {"name": "download_and_index", "parameters": {}}},
            {"type": "function", "function": {"name": "summarize_documents", "parameters": {}}},
        ],
    }

    call = _ManagedGatewayAdapter._managed_followup_tool_call(request)

    assert call is not None
    assert call["function"]["name"] == "download_and_index"
    assert json.loads(call["function"]["arguments"]) == {"identifiers": ["10.2307/2679812"]}


def test_managed_gateway_treats_chinese_review_as_synthesis() -> None:
    request = {
        "messages": [
            {"role": "user", "content": "下载并索引这篇论文，然后写综述。"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-download", "function": {"name": "download_and_index"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-download",
                "content": json.dumps({"ok": True, "run_id": "run-review"}),
            },
        ],
        "tools": [
            {"type": "function", "function": {"name": "download_and_index", "parameters": {}}},
            {"type": "function", "function": {"name": "summarize_documents", "parameters": {}}},
            {"type": "function", "function": {"name": "check_task_completion", "parameters": {}}},
        ],
    }

    call = _ManagedGatewayAdapter._managed_followup_tool_call(request)

    assert call is not None
    assert call["function"]["name"] == "summarize_documents"
    assert json.loads(call["function"]["arguments"]) == {"run_id": "run-review"}


def test_managed_gateway_creates_a_real_presentation_after_document_mapping() -> None:
    request = {
        "messages": [
            {"role": "user", "content": "总结当前任务文献并创建一个实际可下载的 PPTX。"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-summary", "function": {"name": "summarize_documents"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-summary",
                "content": json.dumps(
                    {
                        "ok": True,
                        "run_id": "run-slides",
                        "query": "forest productivity",
                        "zotero": [
                            {
                                "items": [
                                    {
                                        "title": "Paper A",
                                        "methods": "Field experiment",
                                        "findings": "Productivity increased",
                                        "fulltext_excerpt": "Measured plot-level productivity.",
                                    }
                                ]
                            }
                        ],
                    }
                ),
            },
        ],
        "tools": [
            {"type": "function", "function": {"name": "summarize_documents", "parameters": {}}},
            {"type": "function", "function": {"name": "create_presentation", "parameters": {}}},
        ],
    }

    call = _ManagedGatewayAdapter._managed_followup_tool_call(request)
    arguments = json.loads(call["function"]["arguments"]) if call else {}

    assert call is not None
    assert call["function"]["name"] == "create_presentation"
    assert len(arguments["slides"]) >= 3
    assert any("Field experiment" in bullet for bullet in arguments["slides"][1]["bullets"])


def test_pi_check_task_completion_rejects_partial_requested_count(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 fixture")
    store = ResearchRunStore(workspace)
    run = store.create_run(
        notebook_id="library",
        workflow_type="paper_download_batch",
        title="Partial batch",
        input_payload={"identifiers": ["10.1000/a", "10.1000/b"]},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    store.begin_run(run["run_id"])
    store.start_stage(run["run_id"], "deliver")
    store.complete_stage(run["run_id"], "deliver", output={"files": [str(paper)]})
    artifact = store.create_artifact(
        run["run_id"],
        artifact_type="downloaded_papers",
        title="Partial batch",
        summary="One of two",
        payload={
            "files": [str(paper)],
            "evidence_status": "indexed_fulltext",
            "requested_identifiers": ["10.1000/a", "10.1000/b"],
            "requested_count": 2,
            "successful_count": 1,
            "requested_count_satisfied": False,
            "failure_records": [{"identifier": "10.1000/b", "error": "timeout"}],
        },
    )
    store.complete_run(run["run_id"], output_artifact_id=artifact["artifact_id"])
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")

    result = client._execute_tool("check_task_completion", {"run_id": run["run_id"]})

    assert result["complete"] is False
    assert result["checks"]["requested_documents"] == 2
    assert result["checks"]["successful_documents"] == 1
    assert {"requested_count_satisfied", "download_failures"} <= set(result["blockers"])


def test_managed_gateway_does_not_download_when_user_only_asked_to_search() -> None:
    request = {
        "messages": [
            {"role": "user", "content": "搜索 Peter B. Reich 关于森林生产力的论文。"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-discover", "function": {"name": "discover_papers"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-discover",
                "content": json.dumps({"download_identifiers": ["10.1000/paper"]}),
            },
        ],
        "tools": [
            {"type": "function", "function": {"name": "discover_papers", "parameters": {}}},
            {"type": "function", "function": {"name": "download_and_index", "parameters": {}}},
        ],
    }

    assert _ManagedGatewayAdapter._managed_followup_tool_call(request) is None


def test_managed_gateway_never_advances_a_failed_tool_result() -> None:
    request = {
        "messages": [
            {"role": "user", "content": "下载并总结论文。"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-download", "function": {"name": "download_and_index"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call-download",
                "content": "Error: downloader reported a missing file",
            },
        ],
        "tools": [
            {"type": "function", "function": {"name": "summarize_documents", "parameters": {}}},
            {"type": "function", "function": {"name": "check_task_completion", "parameters": {}}},
        ],
    }

    assert _ManagedGatewayAdapter._managed_followup_tool_call(request) is None


def test_managed_gateway_starts_explicit_recent_task_summary_without_model_guessing() -> None:
    request = {
        "messages": [
            {
                "role": "user",
                "content": "总结刚才下载并索引的论文，只比较当前任务中的文献。",
            }
        ],
        "tools": [
            {"type": "function", "function": {"name": "read_task_documents", "parameters": {}}},
            {"type": "function", "function": {"name": "summarize_documents", "parameters": {}}},
            {"type": "function", "function": {"name": "check_task_completion", "parameters": {}}},
        ],
    }

    call = _ManagedGatewayAdapter._managed_followup_tool_call(request)

    assert call is not None
    assert call["function"]["name"] == "summarize_documents"
    assert json.loads(call["function"]["arguments"]) == {}


def test_compact_verified_answer_preserves_reader_location_fields() -> None:
    compact = _compact_verified_answer_for_model(
        {
            "question": "question",
            "reader_answer": {
                "text": "answer [1]",
                "citations": [
                    {
                        "citation_id": "1",
                        "evidence_id": "evidence-1",
                        "doc_id": "doc-1",
                        "html_anchor": "sentence-1",
                        "exact_quote": "quote",
                        "matched_text": "Matched sentence.",
                        "context_before": "Previous sentence.",
                        "context_before_evidence_id": "evidence-before",
                        "context_after": "Next sentence.",
                        "context_after_evidence_id": "evidence-after",
                    }
                ],
            },
            "citation_verification": {"passed": True},
        }
    )

    citation = compact["reader_answer"]["citations"][0]
    assert citation["doc_id"] == "doc-1"
    assert citation["html_anchor"] == "sentence-1"
    assert citation["context_before"] == "Previous sentence."
    assert citation["context_after"] == "Next sentence."
    assert _parse_text_tool_intents('The tool has a field named "name": "inspect_workspace".') == []


@pytest.mark.parametrize("task_mode", ["knowledge", "agent"])
def test_pi_sdk_streams_through_the_python_bridge(tmp_path: Path, task_mode: str) -> None:
    _OpenAIStreamHandler.request_headers = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIStreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                provider_id="opencode-go",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[
                    {"role": "system", "content": "Reply briefly."},
                    {"role": "user", "content": "Confirm the bridge."},
                ],
                thinking_level="off",
                task_mode=task_mode,
                timeout_seconds=30,
                session_id="pi-opencode-session",
            )
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert "".join(str(item.get("content", "")) for item in events if item["type"] == "delta") == "Pi bridge works"
    assert events[-1]["type"] == "done"
    assert events[-1]["stats"]["tokens"]["input"] == 5
    assert events[-1]["stats"]["tokens"]["output"] == 3
    assert events[-1]["stats"]["tokens"]["cacheRead"] == 100
    assert events[-1]["stats"]["tokens"]["total"] == 8
    assert "summarize_documents" not in events[-1]["stats"]["toolInventory"]["names"]
    assert "check_task_completion" not in events[-1]["stats"]["toolInventory"]["names"]
    assert _OpenAIStreamHandler.request_payload["model"] == "fixture-model"
    assert _OpenAIStreamHandler.request_payload["stream"] is True
    assert _OpenAIStreamHandler.request_payload["tools"]
    assert _OpenAIStreamHandler.request_headers["x-opencode-session"] == "pi-opencode-session"
    assert _OpenAIStreamHandler.request_headers["User-Agent"] == "ScanSci"
    lifecycle = {
        str(item.get("event", ""))
        for item in events
        if item.get("type") == "lifecycle"
    }
    assert {
        "started",
        "turn_started",
        "message_started",
        "message_completed",
        "turn_completed",
        "completed",
    }.issubset(lifecycle)


def test_pi_continues_output_limit_and_keeps_the_complete_answer(tmp_path: Path) -> None:
    _OpenAIOutputLimitHandler.request_payloads = []
    _OpenAIOutputLimitHandler.always_truncate = False
    _OpenAIOutputLimitHandler.early_stop_html = False
    _OpenAIOutputLimitHandler.never_close_html = False
    _OpenAIOutputLimitHandler.truncate_tool_call = False
    _OpenAIOutputLimitHandler.empty_continuation = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIOutputLimitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "Create a complete HTML game."}],
            thinking_level="off",
            task_mode="agent",
            timeout_seconds=30,
        ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert "".join(str(event.get("content", "")) for event in events if event["type"] == "delta") == (
        "<html><script>function play(){}</script></html>"
    )
    assert events[-1]["type"] == "done"
    assert any(event.get("type") == "status" and event.get("name") == "output_limit" for event in events)
    assert len(_OpenAIOutputLimitHandler.request_payloads) == 2
    followup_messages = _OpenAIOutputLimitHandler.request_payloads[1]["messages"]
    assert any(message.get("role") == "user" and "function play(){" in str(message.get("content", ""))
               for message in followup_messages)
    assert any(message.get("role") == "user" and "output limit" in str(message.get("content", "")).lower()
               for message in followup_messages)


def test_pi_continues_html_game_when_provider_stops_before_closing_page(tmp_path: Path) -> None:
    _OpenAIOutputLimitHandler.request_payloads = []
    _OpenAIOutputLimitHandler.always_truncate = False
    _OpenAIOutputLimitHandler.early_stop_html = True
    _OpenAIOutputLimitHandler.never_close_html = False
    _OpenAIOutputLimitHandler.truncate_tool_call = False
    _OpenAIOutputLimitHandler.empty_continuation = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIOutputLimitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "做一个完整的 HTML 坦克大战游戏"}],
            thinking_level="off",
            task_mode="agent",
            timeout_seconds=30,
        ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    assert len(_OpenAIOutputLimitHandler.request_payloads) == 2
    assert any(event.get("type") == "status" and event.get("name") == "incomplete_html" for event in events)
    assert "".join(str(event.get("content", "")) for event in events if event["type"] == "delta").endswith("</html>")


def test_pi_html_completion_guard_uses_current_turn_not_history(tmp_path: Path) -> None:
    _OpenAIOutputLimitHandler.request_payloads = []
    _OpenAIOutputLimitHandler.always_truncate = False
    _OpenAIOutputLimitHandler.early_stop_html = True
    _OpenAIOutputLimitHandler.never_close_html = False
    _OpenAIOutputLimitHandler.truncate_tool_call = False
    _OpenAIOutputLimitHandler.empty_continuation = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIOutputLimitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[
                {"role": "user", "content": "Create a complete HTML game."},
                {"role": "assistant", "content": "Done."},
                {"role": "user", "content": "Explain the HTML tag."},
            ],
            thinking_level="off",
            task_mode="agent",
            timeout_seconds=30,
        ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    assert len(_OpenAIOutputLimitHandler.request_payloads) == 1


def test_pi_reports_html_game_that_never_closes_as_incomplete(tmp_path: Path) -> None:
    _OpenAIOutputLimitHandler.request_payloads = []
    _OpenAIOutputLimitHandler.always_truncate = False
    _OpenAIOutputLimitHandler.early_stop_html = True
    _OpenAIOutputLimitHandler.never_close_html = True
    _OpenAIOutputLimitHandler.truncate_tool_call = False
    _OpenAIOutputLimitHandler.empty_continuation = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIOutputLimitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        with pytest.raises(pi_agent.PiAgentRunError) as captured:
            list(client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "做一个完整的 HTML 坦克大战游戏"}],
                thinking_level="off",
                task_mode="agent",
                timeout_seconds=30,
            ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert captured.value.failure["code"] == "incomplete_model_response"
    assert len(_OpenAIOutputLimitHandler.request_payloads) == 4


def test_pi_reports_repeated_output_limit_as_incomplete(tmp_path: Path) -> None:
    _OpenAIOutputLimitHandler.request_payloads = []
    _OpenAIOutputLimitHandler.always_truncate = True
    _OpenAIOutputLimitHandler.early_stop_html = False
    _OpenAIOutputLimitHandler.never_close_html = False
    _OpenAIOutputLimitHandler.truncate_tool_call = False
    _OpenAIOutputLimitHandler.empty_continuation = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIOutputLimitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        with pytest.raises(pi_agent.PiAgentRunError) as captured:
            list(client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "Create a complete HTML game."}],
                thinking_level="off",
                task_mode="agent",
                timeout_seconds=30,
            ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert captured.value.failure["code"] == "model_output_limit"
    assert len(_OpenAIOutputLimitHandler.request_payloads) == 4


def test_pi_keeps_sdk_recovery_for_a_truncated_tool_call(tmp_path: Path) -> None:
    _OpenAIOutputLimitHandler.request_payloads = []
    _OpenAIOutputLimitHandler.always_truncate = False
    _OpenAIOutputLimitHandler.early_stop_html = False
    _OpenAIOutputLimitHandler.never_close_html = False
    _OpenAIOutputLimitHandler.truncate_tool_call = True
    _OpenAIOutputLimitHandler.empty_continuation = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIOutputLimitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "Inspect available tools."}],
                thinking_level="off",
                task_mode="agent",
                timeout_seconds=30,
            ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(_OpenAIOutputLimitHandler.request_payloads) == 2
    followup_messages = _OpenAIOutputLimitHandler.request_payloads[1]["messages"]
    assert any(message.get("role") == "tool" and "arguments may be truncated" in str(message.get("content", ""))
               for message in followup_messages)
    assert not any(event.get("type") == "tool.completed" for event in events)


def test_pi_does_not_mark_an_empty_continuation_complete(tmp_path: Path) -> None:
    _OpenAIOutputLimitHandler.request_payloads = []
    _OpenAIOutputLimitHandler.always_truncate = False
    _OpenAIOutputLimitHandler.early_stop_html = False
    _OpenAIOutputLimitHandler.never_close_html = False
    _OpenAIOutputLimitHandler.truncate_tool_call = False
    _OpenAIOutputLimitHandler.empty_continuation = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIOutputLimitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        with pytest.raises(pi_agent.PiAgentRunError) as captured:
            list(client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "Create a complete HTML game."}],
                thinking_level="off",
                task_mode="agent",
                timeout_seconds=30,
            ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert captured.value.failure["code"] == "model_output_limit"
    assert len(_OpenAIOutputLimitHandler.request_payloads) == 2


def test_pi_tool_call_round_trips_through_scansci_dispatcher(tmp_path: Path) -> None:
    _OpenAIToolLoopHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIToolLoopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "Inspect the available tools."}],
                thinking_level="off",
                task_mode="knowledge",
                task_contract={
                    **_TASK_CONTRACT_V2,
                    "contract_id": "contract-tool-loop",
                    "autonomy": "read_only",
                    "risk_level": "read_only",
                    "allowed_tools": ["inspect_available_tools"],
                    "initial_tools": ["inspect_available_tools"],
                    "required_tool_groups": [["inspect_available_tools"]],
                    "task_profile": {
                        "route": "tool_agent",
                        "cognitive_complexity": "low",
                        "execution_complexity": "tool",
                    },
                    "initial_tool_budget": 2,
                    "max_tool_budget": 4,
                    "recovery_budget": 2,
                },
                timeout_seconds=30,
            )
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    completed = [item for item in events if item["type"] == "tool.completed"]
    authorization = next(item for item in events if item["type"] == "tool.authorization")
    assert authorization["decision"] == "allow"
    assert authorization["rule"] == "authorized"
    assert authorization["approval"] == "not_required"
    assert authorization["contract_id"] == "contract-tool-loop"
    assert authorization["call_ref"] == completed[0]["call_ref"]
    assert authorization["request_id"] == completed[0]["request_id"]
    assert "arguments" not in authorization
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / ".scansci-diagnostics" / "runs").glob("*.json")]
    assert any(
        record.get("type") == "agent.tool.authorization"
        and record.get("call_ref") == authorization["call_ref"]
        and record.get("decision") == "allow"
        for manifest in manifests for record in manifest["events"]
    )
    assert completed[0]["name"] == "inspect_available_tools"
    assert completed[0]["result"]["workspace"] == str((tmp_path / "workspace.sqlite").resolve())
    assert "".join(str(item.get("content", "")) for item in events if item["type"] == "delta") == "Tool result received"
    assert len(_OpenAIToolLoopHandler.request_payloads) == 2
    advertised = {
        str(dict(tool.get("function", {}) or {}).get("name", ""))
        for tool in list(_OpenAIToolLoopHandler.request_payloads[0].get("tools", []) or [])
        if isinstance(tool, dict)
    }
    assert "inspect_available_tools" in advertised
    assert {"download_and_index", "create_document", "create_presentation"}.isdisjoint(advertised)
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in list(_OpenAIToolLoopHandler.request_payloads[0].get("messages", []) or [])
        if isinstance(message, dict) and message.get("role") in {"system", "developer"}
    )
    assert "HOST-OWNED TASK CONTRACT" in system_text
    assert "risk ceiling=read_only" in system_text
    assert "Required tool groups (host authoritative): inspect_available_tools." in system_text
    assert "activate its exact name with search_tools" in system_text
    assert "take study, measurement, and collection dates from cited source material" in system_text
    assert "place the exact evidence_id in its visible content" in system_text
    second_messages = _OpenAIToolLoopHandler.request_payloads[1]["messages"]
    assert any(message.get("role") == "tool" for message in second_messages)


def test_plan_approval_defaults_to_deny_without_explicit_approve(tmp_path: Path) -> None:
    _OpenAIPlanDefaultDenyHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPlanDefaultDenyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    dispatched: list[str] = []
    client._execute_tool = lambda name, _arguments: dispatched.append(name) or {"ok": True}  # type: ignore[method-assign]
    events: list[dict[str, object]] = []
    errors: list[BaseException] = []
    interaction_or_terminal = threading.Event()

    def consume() -> None:
        try:
            for event in client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "Download one paper after approval."}],
                thinking_level="off",
                task_mode="general",
                task_contract={
                    **_TASK_CONTRACT_V2,
                    "allowed_tools": ["download_and_index"],
                    "risk_level": "reversible",
                    "requires_plan": True,
                    "initial_tool_budget": 2,
                    "max_tool_budget": 4,
                },
                timeout_seconds=45,
                session_id="default-deny-plan",
            ):
                events.append(event)
                if event.get("type") == "interaction":
                    interaction_or_terminal.set()
        except BaseException as error:  # noqa: BLE001 - asserted below
            errors.append(error)
        finally:
            interaction_or_terminal.set()

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    try:
        assert interaction_or_terminal.wait(timeout=30), "Pi did not request plan approval within 30 seconds"
        interaction = next((item for item in events if item.get("type") == "interaction"), None)
        assert interaction is not None, f"Pi ended before requesting plan approval: {errors or events[-3:]}"
        assert client.respond_interaction(
            str(interaction["interaction_id"]),
            {},
            request_id=str(interaction["request_id"]),
        ) is True
        consumer.join(timeout=30)
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert not consumer.is_alive()
    assert errors == []
    assert "download_and_index" not in dispatched
    assert not any(
        event.get("type") == "tool.failed" and event.get("name") == "download_and_index"
        for event in events
    )
    assert events[-1]["type"] == "done"


def test_pi_continues_after_deferred_web_plan_until_tool_backed_final(tmp_path: Path) -> None:
    _OpenAIDeferredWebHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIDeferredWebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    client._execute_tool = lambda name, arguments: {  # type: ignore[method-assign]
        "query": arguments.get("query", ""),
        "count": 1,
        "evidence_level": "web-discovery",
        "items": [{"title": "Market close", "url": "https://example.com/market"}],
    }
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "$web-access 帮我看看今天大A的情况"}],
                thinking_level="off",
                task_mode="web",
                timeout_seconds=30,
                session_id="deferred-web-session",
            )
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(_OpenAIDeferredWebHandler.request_payloads) == 3
    assert any(event.get("type") == "status" and event.get("status") == "continuing" for event in events)
    assert any(event.get("type") == "tool.completed" and event.get("name") == "search_web" for event in events)
    assert "最终市场概况" in "".join(str(event.get("content", "")) for event in events if event.get("type") == "delta")
    assert events[-1]["type"] == "done"


def test_pi_web_turn_uses_host_budget_for_large_context_and_response(tmp_path: Path) -> None:
    """The sidecar must not silently reapply its former 24K/2K web limits."""

    _OpenAIPersistentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPersistentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    model_runtime = pi_agent.ModelRuntimeDescriptor.for_testing(
        context_window_tokens=200 * 1024,
    ).to_dict()
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                model_runtime=model_runtime,
                messages=[{"role": "user", "content": "a" * 100_000}],
                thinking_level="off",
                task_mode="web",
                task_contract={
                    **_TASK_CONTRACT_V2,
                    "contract_id": "contract-web-budget",
                    "autonomy": "direct",
                    "risk_level": "read_only",
                    "allowed_tools": ["search_web"],
                    "required_tool_groups": [],
                    "task_profile": {
                        "route": "direct_chat",
                        "cognitive_complexity": "low",
                        "execution_complexity": "none",
                    },
                    "initial_tool_budget": 4,
                    "max_tool_budget": 8,
                    "model_token_budget": 96_000,
                    "max_model_token_budget": 384_000,
                },
                timeout_seconds=30,
                session_id="web-budget-session",
            )
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    assert len(_OpenAIPersistentHandler.request_payloads) == 1
    payload = _OpenAIPersistentHandler.request_payloads[0]
    assert payload.get("max_tokens", payload.get("max_completion_tokens")) == model_runtime["max_output_tokens"]


def test_pi_extends_soft_model_token_lease_instead_of_failing_sound_answer(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIHighUsageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "answer completely"}],
                thinking_level="off",
                task_mode="general",
                task_contract={
                    **_TASK_CONTRACT_V2,
                    "contract_id": "contract-progressive-model-budget",
                    "allowed_tools": [],
                    "initial_tool_budget": 3,
                    "max_tool_budget": 6,
                    "model_token_budget": 16_000,
                    "max_model_token_budget": 64_000,
                },
                timeout_seconds=30,
                session_id="progressive-model-budget-session",
            )
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert events[-1]["type"] == "done"
    extension = next(event for event in events if event.get("status") == "model_budget_extended")
    assert extension["details"]["previous_budget"] == 16_000
    assert extension["details"]["current_budget"] > 20_000
    assert events[-1]["control"]["model_tokens"] == 20_032


def test_pi_verified_answer_tool_ends_run_without_second_model_answer(tmp_path: Path) -> None:
    _OpenAITerminalToolHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAITerminalToolHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    client._execute_tool = lambda name, arguments: {"reader_answer": {"text": "verified"}}  # type: ignore[method-assign]
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "question"}],
                thinking_level="off",
                task_mode="verified-answer",
                timeout_seconds=30,
                session_id="terminal-tool-session",
            )
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    completed = next(item for item in events if item["type"] == "tool.completed")
    assert completed["name"] == "build_verified_answer"
    assert events[-1]["type"] == "done"
    assert events[-1]["terminal_tool"] == "build_verified_answer"


def test_pi_session_survives_sidecar_restart(tmp_path: Path) -> None:
    _OpenAIPersistentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPersistentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        kwargs = {
            "provider_kind": "openai-compatible",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "api_key": "fixture-key",
            "model_id": "fixture-model",
            "thinking_level": "off",
            "task_mode": "knowledge",
            "timeout_seconds": 30,
            "session_id": "durable-session",
        }
        first_client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        first = list(first_client.stream_chat(messages=[{"role": "user", "content": "first question"}], **kwargs))
        session_event = next(event for event in first if event["type"] == "session")
        session_file = Path(str(session_event["session_file"]))
        first_client.close()

        second_client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
        second = list(second_client.stream_chat(messages=[{"role": "user", "content": "second question"}], **kwargs))
        second_client.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert session_file.is_file()
    registry = json.loads(
        (tmp_path / ".scansci-pi-agent" / "sessions.json").read_text(encoding="utf-8")
    )
    assert registry["durable-session"] == str(session_file)
    assert next(event for event in second if event["type"] == "session")["resumed"] is True
    second_messages = _OpenAIPersistentHandler.request_payloads[1]["messages"]
    assert any(message.get("role") == "user" and "first question" in str(message.get("content")) for message in second_messages)
    assert any(message.get("role") == "assistant" and "turn-1" in str(message.get("content")) for message in second_messages)
    assert any(message.get("role") == "user" and "second question" in str(message.get("content")) for message in second_messages)


def test_pi_session_reuses_context_when_only_contract_identity_and_goal_change(tmp_path: Path) -> None:
    _OpenAIPersistentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPersistentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    common_contract = {
        **_TASK_CONTRACT_V2,
        "autonomy": "direct",
        "risk_level": "none",
        "allowed_tools": [],
        "initial_tool_budget": 2,
        "max_tool_budget": 4,
        "recovery_budget": 2,
        "model_token_budget": 12_000,
    }
    try:
        first = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "first question"}],
            thinking_level="off",
            task_mode="general",
            task_contract={**common_contract, "contract_id": "turn-one", "goal": "first question"},
            timeout_seconds=30,
            session_id="same-permission-session",
        ))
        second = list(client.stream_chat(
            provider_kind="openai-compatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="fixture-key",
            model_id="fixture-model",
            messages=[{"role": "user", "content": "second question"}],
            thinking_level="off",
            task_mode="general",
            task_contract={**common_contract, "contract_id": "turn-two", "goal": "second question"},
            timeout_seconds=30,
            session_id="same-permission-session",
        ))
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    first_session = next(event for event in first if event["type"] == "session")
    second_session = next(event for event in second if event["type"] == "session")
    assert second_session["resumed"] is True
    assert second_session["session_file"] == first_session["session_file"]
    second_messages = _OpenAIPersistentHandler.request_payloads[1]["messages"]
    assert any(message.get("role") == "assistant" and "turn-1" in str(message.get("content")) for message in second_messages)
    second_system = "\n".join(
        str(message.get("content", ""))
        for message in second_messages
        if message.get("role") in {"system", "developer"}
    )
    assert "session-level base prompt is invariant and grants no per-turn authority" in second_system
    assert "Task contract turn-two: goal=second question." in second_system
    assert "Task contract turn-one: goal=first question." not in second_system


@pytest.mark.parametrize("grant_first", [True, False])
def test_pi_reused_session_applies_current_mcp_lease_in_both_directions(
    tmp_path: Path,
    grant_first: bool,
) -> None:
    workspace = tmp_path / "workspace.sqlite"
    node, _sidecar = PiAgentClient.runtime_paths()
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.mjs"
    settings = load_settings(workspace)
    settings["mcp_servers"] = [{
        "id": "fixture",
        "name": "Fixture MCP",
        "enabled": True,
        "transport": "stdio",
        "command": str(node),
        "args": f'"{fixture}"',
        "allow_write": False,
        "deferred": True,
    }]
    save_settings(workspace, settings)
    _OpenAIPersistentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPersistentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")
    base_contract = {
        "schema_version": "scansci.task-contract.v2",
        "version": 2,
        "allowed_tools": [],
        "initial_tools": [],
        "risk_level": "read_only",
        "initial_tool_budget": 2,
        "max_tool_budget": 4,
    }
    done_events: list[dict[str, object]] = []
    try:
        for turn, granted in enumerate((grant_first, not grant_first), start=1):
            events = list(client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": f"turn {turn}"}],
                thinking_level="off",
                task_mode="general",
                task_contract={
                    **base_contract,
                    "contract_id": f"mcp-turn-{turn}",
                    "goal": f"turn {turn}",
                    "allowed_mcp_servers": ["fixture"] if granted else [],
                },
                timeout_seconds=30,
                session_id=f"mcp-lease-{'grant' if grant_first else 'revoke'}-first",
            ))
            assert events[-1]["type"] == "done"
            done_events.append(events[-1])
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(_OpenAIPersistentHandler.request_payloads) == 2
    tool_names_by_turn = []
    for payload in _OpenAIPersistentHandler.request_payloads:
        tool_names_by_turn.append({
            str(dict(tool.get("function", {}) or {}).get("name", ""))
            for tool in list(payload.get("tools", []) or [])
            if isinstance(tool, dict)
        })
    mcp_names = {"mcp__fixture__search", "mcp__fixture__call"}
    # MCP definitions follow the same dynamic-tool contract as built-ins:
    # authorization registers them, while the bootstrap surface stays small
    # until search_tools activates a selected definition.
    assert all(mcp_names.isdisjoint(names) for names in tool_names_by_turn)
    registered_by_turn = [
        set(dict(dict(event.get("stats", {}) or {}).get("toolInventory", {}) or {}).get("registeredNames", []) or [])
        for event in done_events
    ]
    if grant_first:
        assert mcp_names <= registered_by_turn[0]
        assert mcp_names.isdisjoint(registered_by_turn[1])
    else:
        assert mcp_names.isdisjoint(registered_by_turn[0])
        assert mcp_names <= registered_by_turn[1]


def test_pi_cancel_aborts_active_sdk_run_without_killing_client(tmp_path: Path) -> None:
    _OpenAISlowHandler.started = threading.Event()
    _OpenAISlowHandler.release = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAISlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    events: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            events.extend(
                client.stream_chat(
                    provider_kind="openai-compatible",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    api_key="fixture-key",
                    model_id="fixture-model",
                    messages=[{"role": "user", "content": "take a long time"}],
                    thinking_level="off",
                    task_mode="knowledge",
                    timeout_seconds=30,
                    session_id="cancel-session",
                )
            )
        except BaseException as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    try:
        assert _OpenAISlowHandler.started.wait(timeout=10)
        deadline = time.monotonic() + 5
        while not client.active_request_id and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.cancel() is True
        consumer.join(timeout=5)
    finally:
        _OpenAISlowHandler.release.set()
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert not consumer.is_alive()
    assert not errors
    assert events[-1]["type"] == "cancelled"


def test_pi_steer_reports_native_queue_and_control_acknowledgement(tmp_path: Path) -> None:
    _OpenAISlowHandler.started = threading.Event()
    _OpenAISlowHandler.release = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAISlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    events: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            events.extend(
                client.stream_chat(
                    provider_kind="openai-compatible",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    api_key="fixture-key",
                    model_id="fixture-model",
                    messages=[{"role": "user", "content": "take a long time"}],
                    thinking_level="off",
                    task_mode="knowledge",
                    timeout_seconds=30,
                    session_id="steer-session",
                )
            )
        except BaseException as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    try:
        assert _OpenAISlowHandler.started.wait(timeout=10)
        deadline = time.monotonic() + 5
        while not client.active_request_id and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.steer("focus on the requested detail") is True
        acknowledgement_deadline = time.monotonic() + 5
        while not any(
            event.get("type") == "control"
            and event.get("action") == "steer"
            and event.get("status") == "accepted"
            for event in events
        ) and time.monotonic() < acknowledgement_deadline:
            time.sleep(0.01)
        queued = client.inspect_queue("steer-session")
        assert "focus on the requested detail" in list(queued.get("steering", []))
        cleared = client.clear_queue("steer-session")
        assert "focus on the requested detail" in list(cleared.get("steering", []))
        assert client.inspect_queue("steer-session").get("pending") == 0
        assert client.cancel() is True
        _OpenAISlowHandler.release.set()
        consumer.join(timeout=10)
    finally:
        _OpenAISlowHandler.release.set()
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert not consumer.is_alive()
    assert not errors
    assert any(
        event.get("type") == "control"
        and event.get("action") == "steer"
        and event.get("status") == "accepted"
        for event in events
    )
    assert any(
        event.get("type") == "queue"
        and "focus on the requested detail" in list(event.get("steering", []))
        for event in events
    )
    assert events[-1]["type"] == "cancelled"


def test_pi_manual_compaction_is_persisted(tmp_path: Path) -> None:
    _OpenAIPersistentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPersistentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        common = {
            "provider_kind": "openai-compatible",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "api_key": "fixture-key",
            "model_id": "fixture-model",
            "model_runtime": pi_agent.ModelRuntimeDescriptor.for_testing(
                context_window_tokens=200 * 1024,
            ).to_dict(),
            "thinking_level": "off",
            "task_mode": "general",
            "timeout_seconds": 30,
            "session_id": "compact-session",
        }
        events: list[dict[str, object]] = []
        for turn in range(4):
            events = list(
                client.stream_chat(
                    messages=[{"role": "user", "content": f"turn {turn}: alpha beta gamma " + ("context " * 5000)}],
                    task_contract={
                        "schema_version": "scansci.task-contract.v2",
                        "version": 2,
                        "contract_id": f"compact-turn-{turn}",
                        "goal": f"authority-sentinel-{turn}",
                        "allowed_tools": [],
                        "initial_tools": [],
                        "risk_level": "read_only",
                    },
                    **common,
                )
            )
        session_file = Path(str(next(event for event in events if event["type"] == "session")["session_file"]))
        assert client.abort_compaction("compact-session", timeout_seconds=30)["aborted"] is True
        result = client.compact("compact-session", instructions="Retain the alpha beta gamma fact.", timeout_seconds=30)
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result["summary"]
    records = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("type") == "compaction" for record in records)
    current_turn_system = "\n".join(
        str(message.get("content", ""))
        for message in _OpenAIPersistentHandler.request_payloads[-2]["messages"]
        if message.get("role") in {"system", "developer"}
    )
    assert "session-level base prompt is invariant and grants no per-turn authority" in current_turn_system
    assert "authority-sentinel-3" in current_turn_system
    assert "authority-sentinel-0" not in current_turn_system

    compaction_payload = json.dumps(_OpenAIPersistentHandler.request_payloads[-1], ensure_ascii=False)
    assert "context summarization assistant" in compaction_payload
    assert "session-level base prompt is invariant" not in compaction_payload
    assert "authority-sentinel-0" not in compaction_payload
    assert "authority-sentinel-3" not in compaction_payload


def test_protocol_dispatcher_rejects_cross_generation_command_ack() -> None:
    raw: Queue[str | None] = Queue()
    audit: deque[dict[str, object]] = deque(maxlen=20)
    dispatcher = pi_agent._ProtocolDispatcher(raw, generation=7, audit=audit)
    channel = dispatcher.register_command("command-1")
    dispatcher.start()
    raw.put(json.dumps({"type": "session.queue", "command_id": "command-1", "generation": 6}))
    with pytest.raises(Empty):
        channel.get(timeout=0.1)
    raw.put(json.dumps({"type": "session.queue", "command_id": "command-1", "generation": 7}))
    assert channel.get(timeout=1)["generation"] == 7
    raw.put(None)


def test_fork_command_correlation_rejects_cross_target_ack() -> None:
    with pytest.raises(RuntimeError, match="target sessions"):
        PiAgentClient._validate_command_correlation(
            {
                "source_session_id": "source",
                "target_session_id": "expected-target",
            },
            {
                "source_session_id": "source",
                "target_session_id": "other-target",
            },
        )


def test_session_queue_controls_do_not_take_lifecycle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    client._process_generation = 11
    client._process = SimpleNamespace(poll=lambda: None)

    class FakeDispatcher:
        def register_command(self, command_id: str):
            channel: Queue[dict[str, object] | None] = Queue()
            self.command_id = command_id
            self.channel = channel
            return channel

        def start(self) -> None:
            return None

        def unregister_command(self, _command_id: str) -> None:
            return None

    dispatcher = FakeDispatcher()
    monkeypatch.setattr(client, "_ensure_dispatcher", lambda: dispatcher)

    def write(message: dict[str, object]) -> None:
        response_type = {
            "session.queue.inspect": "session.queue",
            "session.queue.clear": "session.queue_cleared",
            "session.compact.abort": "session.compact_aborted",
        }[str(message["type"])]
        dispatcher.channel.put(
            {
                "type": response_type,
                "command_id": message["command_id"],
                "session_id": message["session_id"],
                "generation": message["generation"],
                "steering": [],
                "follow_up": [],
            }
        )

    monkeypatch.setattr(client, "_write", write)
    results: list[dict[str, object]] = []

    def invoke_controls() -> None:
        results.extend([
            client.inspect_queue("active-session"),
            client.clear_queue("active-session"),
            client.abort_compaction("active-session"),
        ])

    with client._lifecycle_lock:
        worker = threading.Thread(target=invoke_controls, daemon=True)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive(), "non-replacing controls must not wait on the lifecycle lock"
    assert [item["type"] for item in results] == [
        "session.queue",
        "session.queue_cleared",
        "session.compact_aborted",
    ]
    assert all(item["generation"] == 11 for item in results)


def test_entry_level_fork_sends_branch_boundary_and_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    client._process_generation = 4
    client._process = SimpleNamespace(poll=lambda: None)
    sent: list[dict[str, object]] = []

    def await_command(message: dict[str, object], **_kwargs: object) -> dict[str, object]:
        sent.append(dict(message))
        return {
            "type": "session.forked",
            "command_id": message["command_id"],
            "source_session_id": message["source_session_id"],
            "target_session_id": message["target_session_id"],
            "generation": message["generation"],
        }

    monkeypatch.setattr(client, "_await_command", await_command)
    result = client.fork_session(
        "source",
        target_session_id="target",
        entry_id="entry-2",
        before=True,
    )

    assert result["generation"] == 4
    assert sent[0]["entry_id"] == "entry-2"
    assert sent[0]["before"] is True
    assert sent[0]["full_history"] is False


def test_thinking_max_clamps_with_explicit_degradation_on_resume(tmp_path: Path) -> None:
    _OpenAIPersistentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPersistentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    degradations: list[dict[str, object]] = []
    try:
        for turn in range(2):
            events = list(
                client.stream_chat(
                    provider_kind="openai-compatible",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    api_key="fixture-key",
                    model_id="fixture-model-without-max",
                    messages=[{"role": "user", "content": f"turn {turn}"}],
                    thinking_level="max",
                    task_mode="general",
                    task_contract={
                        "schema_version": "scansci.task-contract.v2",
                        "version": 2,
                        "contract_id": f"max-{turn}",
                        "allowed_tools": [],
                        "initial_tools": [],
                        "risk_level": "read_only",
                    },
                    timeout_seconds=30,
                    session_id="thinking-max-resume",
                )
            )
            degradations.extend(
                event for event in events
                if event.get("type") == "status"
                and event.get("status") == "capability_degraded"
                and event.get("name") == "thinking_level"
            )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(degradations) == 2
    assert all(dict(event.get("details", {}))["requested"] == "max" for event in degradations)
    assert all(dict(event.get("details", {}))["applied"] != "max" for event in degradations)


def test_full_history_clone_and_entry_fork_leave_source_immutable(tmp_path: Path) -> None:
    _OpenAIPersistentHandler.request_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIPersistentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PiAgentClient(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    try:
        events = list(
            client.stream_chat(
                provider_kind="openai-compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="fixture-key",
                model_id="fixture-model",
                messages=[{"role": "user", "content": "source history"}],
                thinking_level="off",
                task_mode="general",
                task_contract={
                    "schema_version": "scansci.task-contract.v2",
                    "version": 2,
                    "contract_id": "fork-source",
                    "allowed_tools": [],
                    "initial_tools": [],
                    "risk_level": "read_only",
                },
                timeout_seconds=30,
                session_id="fork-source",
            )
        )
        source_file = Path(str(next(event for event in events if event["type"] == "session")["session_file"]))
        source_before = source_file.read_bytes()
        source_records = [json.loads(line) for line in source_before.decode("utf-8").splitlines()]
        assistant_entry = next(
            record for record in reversed(source_records)
            if record.get("type") == "message" and dict(record.get("message", {})).get("role") == "assistant"
        )

        cloned = client.fork_session("fork-source", target_session_id="fork-clone", timeout_seconds=30)
        branched = client.fork_session(
            "fork-source",
            target_session_id="fork-before-assistant",
            entry_id=str(assistant_entry["id"]),
            before=True,
            timeout_seconds=30,
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert source_file.read_bytes() == source_before
    clone_records = [json.loads(line) for line in Path(str(cloned["session_file"])).read_text(encoding="utf-8").splitlines()]
    branch_records = [json.loads(line) for line in Path(str(branched["session_file"])).read_text(encoding="utf-8").splitlines()]
    assert any(
        record.get("type") == "message" and dict(record.get("message", {})).get("role") == "assistant"
        for record in clone_records
    )
    assert not any(
        record.get("type") == "message" and dict(record.get("message", {})).get("role") == "assistant"
        for record in branch_records
    )
