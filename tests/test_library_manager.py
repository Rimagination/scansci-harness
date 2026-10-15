from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event

import pytest

from scansci_html.evidence_store import index_evidence_library
from scansci_html.library_manager import (
    connect_local_zotero,
    discover_local_zotero,
    import_library_files,
    import_library_folder,
    index_zotero_attachments,
    notebook_evidence_db,
    register_zotero_library,
)
from scansci_html.vector_index import load_embedding_cache_rows, prewarm_embedding_cache
from scansci_html.webapp import NotebookWebApp
from scansci_html.workspace import initialize_notebook, load_workspace_summary, sync_sources_from_evidence_store


def _paper(path: Path, title: str, sentence: str) -> None:
    path.write_text(
        f"<article><h1>{title}</h1><h2>Results</h2><p>{sentence}</p></article>",
        encoding="utf-8",
    )


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    old_library = tmp_path / "old-library"
    old_library.mkdir()
    _paper(old_library / "old.html", "Old paper", "The original evidence remains available before switching folders.")
    evidence = tmp_path / "evidence.sqlite"
    index_evidence_library(old_library, db_path=evidence, inject_evidence_html=True, min_sentence_length=10)
    workspace = tmp_path / "workspace.sqlite"
    initialize_notebook(workspace, notebook_id="research", title="Research", root_path=old_library)
    sync_sources_from_evidence_store(workspace, evidence, notebook_id="research")
    return workspace, evidence


def test_concurrent_file_imports_preserve_both_additions(tmp_path: Path, monkeypatch):
    from scansci_html import library_manager

    workspace, evidence = _workspace(tmp_path)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("The first paper contains evidence about attention mechanisms.", encoding="utf-8")
    second.write_text("The second paper contains evidence about retrieval augmentation.", encoding="utf-8")
    entered, release, second_started, second_finished = Event(), Event(), Event(), Event()
    extract = library_manager.extract_local_document

    def held_extract(path, **kwargs):
        if Path(path) == first:
            entered.set()
            assert release.wait(5), "first import was not released"
        return extract(path, **kwargs)

    def import_second():
        second_started.set()
        try:
            return import_library_files(workspace, evidence, notebook_id="research", file_paths=[second])
        finally:
            second_finished.set()

    monkeypatch.setattr(library_manager, "extract_local_document", held_extract)
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(import_library_files, workspace, evidence, notebook_id="research", file_paths=[first])
        try:
            assert entered.wait(5)
            b = executor.submit(import_second)
            assert second_started.wait(5)
            assert not second_finished.wait(0.2), "imports must not replace each other's active generation"
        finally:
            release.set()
        assert a.result()["added_files"] == 1
        assert b.result()["added_files"] == 1

    notebook = load_workspace_summary(workspace, notebook_id="research")["notebooks"][0]
    assert notebook["counts"]["sources"] == 3
    assert first.is_file() and second.is_file()


def test_cross_process_import_lock_rejects_conflict_and_releases_after_error(tmp_path: Path):
    from scansci_html.library_manager import _library_process_lock

    workspace, evidence = _workspace(tmp_path)
    source = tmp_path / "new.txt"
    source.write_text("New evidence remains searchable after the competing importer has stopped.", encoding="utf-8")
    root = workspace.parent / ".scansci-library" / "research"
    script = """
import sys
from scansci_html.library_manager import import_library_files
try:
    import_library_files(sys.argv[1], sys.argv[2], notebook_id='research', file_paths=[sys.argv[3]])
except RuntimeError as error:
    assert 'ScanSci' in str(error) and '进程' in str(error), str(error)
    print('locked')
else:
    raise AssertionError('another process imported while the library lock was held')
"""
    with pytest.raises(RuntimeError, match="simulated interruption"):
        with _library_process_lock(root):
            child = subprocess.run(
                [sys.executable, "-X", "utf8", "-c", script, str(workspace), str(evidence), str(source)],
                cwd=Path(__file__).resolve().parents[1] / "src",
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            assert child.returncode == 0, child.stderr
            assert child.stdout.strip() == "locked"
            assert not list(root.glob("generation-*"))
            raise RuntimeError("simulated interruption")

    result = import_library_files(workspace, evidence, notebook_id="research", file_paths=[source])
    assert result["added_files"] == 1
    assert result["notebook"]["counts"]["sources"] == 2
    assert source.read_text(encoding="utf-8").startswith("New evidence")


@pytest.mark.parametrize(
    ("filename", "title_lines", "embedded_title", "supplied_title", "expected"),
    [
        ("Vaswani2017_Attention.pdf", ["Attention Is All You Need"], "", "", "Attention Is All You Need"),
        (
            "Lewis2020_Retrieval.pdf",
            ["Retrieval-Augmented Generation for", "Knowledge-Intensive NLP Tasks"],
            "", "", "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        ),
        ("Author2020_Short.pdf", ["A visible title on the first page"], "Verified PDF title", "", "Verified PDF title"),
        ("Author2020_Short.pdf", ["A visible title on the first page"], "PDF title", "Curated catalog title", "Curated catalog title"),
    ],
)
def test_pdf_import_prefers_catalog_then_metadata_then_first_page_title(
    tmp_path: Path, filename, title_lines, embedded_title, supplied_title, expected,
):
    import fitz

    workspace, evidence = _workspace(tmp_path)
    source = tmp_path / filename
    with fitz.open() as document:
        page = document.new_page()
        for index, line in enumerate(title_lines):
            page.insert_text((60, 72 + index * 22), line, fontsize=18)
        page.insert_text((60, 135), "A. Researcher and B. Author", fontsize=11)
        page.insert_text((60, 158), "Research Institute", fontsize=10)
        page.insert_text((60, 185), "Abstract", fontsize=11)
        page.insert_text((60, 205), "This paper presents traceable evidence for reliable scientific research.", fontsize=10)
        page.insert_text((20, 400), "arXiv: 2005.11401", fontsize=24, rotate=90)
        if embedded_title:
            document.set_metadata({"title": embedded_title})
        document.save(source)
    original_bytes = source.read_bytes()
    result = import_library_files(
        workspace, evidence, notebook_id="research", file_paths=[source], replace_existing=True,
        source_metadata={str(source.resolve()): {"title": supplied_title}} if supplied_title else None,
    )
    assert result["notebook"]["sources"][0]["title"] == expected
    assert result["ingestion"]["sources"][0]["metadata"]["title"] == expected
    assert source.read_bytes() == original_bytes
    if not supplied_title and not embedded_title:
        assert "pdf-first-page" in result["ingestion"]["sources"][0]["metadata"]["metadata_source"]


def test_ambiguous_pdf_title_keeps_filename_and_ignores_page_markers(tmp_path: Path):
    import fitz
    from scansci_html.library_manager import _pdf_first_page_title, _title_from_extracted_text

    source = tmp_path / "notes.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 72), "An ordinary sentence in a uniform text document.", fontsize=11)
        page.insert_text((60, 94), "Another ordinary sentence without a prominent title.", fontsize=11)
        document.save(source)
    assert _pdf_first_page_title(source) == ""
    assert _title_from_extracted_text("# 第 1 页\n\nSome text\n\n# 第 2 页\nMore text") == ""
    assert _title_from_extracted_text("# A real document heading\n\nSome text") == "A real document heading"


@pytest.mark.parametrize("entrypoint", ["files", "folder", "replace"])
def test_pdf_reimport_updates_title_without_changing_source_identity(tmp_path: Path, entrypoint):
    import fitz

    workspace, evidence = tmp_path / "workspace.sqlite", tmp_path / "evidence.sqlite"
    initialize_notebook(workspace, notebook_id="research", title="Research", root_path=tmp_path)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "Lewis2020_Retrieval.pdf"
    other = tmp_path / "other.txt"
    other.write_text("Another paper remains available when the downloaded PDF is imported again.", encoding="utf-8")
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 72), "Retrieval-Augmented Generation for", fontsize=18)
        page.insert_text((60, 94), "Knowledge-Intensive NLP Tasks", fontsize=18)
        page.insert_text((60, 135), "P. Lewis and coauthors", fontsize=10)
        page.insert_text((60, 158), "This paper provides traceable evidence for retrieval augmented generation.", fontsize=10)
        document.save(source)
    original_bytes = source.read_bytes()
    initial = import_library_files(
        workspace, evidence, notebook_id="research", file_paths=[source, other],
        source_metadata={str(source.resolve()): {"title": "Lewis2020"}},
    )
    original_sources = {item["source_url"]: item for item in initial["notebook"]["sources"]}
    prior = original_sources[str(source.resolve())]
    with sqlite3.connect(notebook_evidence_db(evidence, "research")) as connection:
        prior_evidence = connection.execute(
            "select evidence_id, text from evidence_spans where doc_id = ? order by evidence_id", (prior["doc_id"],),
        ).fetchall()
    assert prior_evidence

    if entrypoint == "folder":
        updated = import_library_folder(
            workspace, evidence, notebook_id="research", folder_path=downloads, merge_existing=True,
        )
    else:
        updated = import_library_files(
            workspace, evidence, notebook_id="research",
            file_paths=[source, other] if entrypoint == "replace" else [source, source.parent / "." / source.name],
            replace_existing=entrypoint == "replace",
        )

    assert updated["notebook"]["counts"]["sources"] == 2
    current_sources = {item["source_url"]: item for item in updated["notebook"]["sources"]}
    for path, previous in original_sources.items():
        assert current_sources[path]["doc_id"] == previous["doc_id"]
        assert current_sources[path]["source_id"] == previous["source_id"]
    assert current_sources[str(source.resolve())]["title"] == (
        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    )
    with sqlite3.connect(notebook_evidence_db(evidence, "research")) as connection:
        assert connection.execute(
            "select evidence_id, text from evidence_spans where doc_id = ? order by evidence_id", (prior["doc_id"],),
        ).fetchall() == prior_evidence
    assert source.read_bytes() == original_bytes
    assert len(list(Path(updated["folder_path"]).glob("*.md"))) == 2


def test_failed_reimport_preserves_identity_and_same_named_distinct_sources(tmp_path: Path, monkeypatch):
    workspace, evidence = tmp_path / "workspace.sqlite", tmp_path / "evidence.sqlite"
    initialize_notebook(workspace, notebook_id="research", title="Research", root_path=tmp_path)
    originals = []
    for folder in ("first", "second"):
        path = tmp_path / folder / "paper.txt"
        path.parent.mkdir()
        path.write_text(f"The {folder} original has separate traceable evidence and must retain its identity.", encoding="utf-8")
        originals.append(path)
    initial = import_library_files(workspace, evidence, notebook_id="research", file_paths=originals)

    def failed_extract(*args, **kwargs):
        raise ValueError("simulated parser failure")

    monkeypatch.setattr("scansci_html.library_manager.extract_local_document", failed_extract)
    updated = import_library_files(workspace, evidence, notebook_id="research", file_paths=[originals[0]])

    assert updated["skipped_files"] == [{"name": "paper.txt", "error": "ValueError: simulated parser failure"}]
    assert updated["notebook"]["counts"]["sources"] == 2
    assert {
        (item["source_url"], item["doc_id"], item["source_id"]) for item in updated["notebook"]["sources"]
    } == {
        (item["source_url"], item["doc_id"], item["source_id"]) for item in initial["notebook"]["sources"]
    }
    assert all(path.read_text(encoding="utf-8").startswith("The ") for path in originals)


def test_reimport_refuses_to_merge_preexisting_duplicate_source_identities(tmp_path: Path):
    from scansci_html.library_manager import _markdown_document

    workspace, evidence = tmp_path / "workspace.sqlite", tmp_path / "evidence.sqlite"
    initialize_notebook(workspace, notebook_id="research", title="Research", root_path=tmp_path)
    source = tmp_path / "paper.txt"
    source.write_text("The original document has existing citations that must remain valid.", encoding="utf-8")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    for index, title in enumerate(("Old title", "Improved title")):
        (legacy / f"paper-{index}.md").write_text(
            _markdown_document({"title": title, "source_url": str(source.resolve())}, source.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
    initial = import_library_folder(workspace, evidence, notebook_id="research", folder_path=legacy)
    assert initial["notebook"]["counts"]["sources"] == 2

    with pytest.raises(ValueError, match="已关联多个资料记录"):
        import_library_files(workspace, evidence, notebook_id="research", file_paths=[source])

    current = load_workspace_summary(workspace, notebook_id="research")["notebooks"][0]
    assert current["sources"] == initial["notebook"]["sources"]
    assert source.read_text(encoding="utf-8").startswith("The original document")


def test_discover_local_zotero_prefers_profile_data_dir_over_stale_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    stale_default = home / "Zotero"
    configured = tmp_path / "configured-zotero"
    for data_dir in (stale_default, configured):
        data_dir.mkdir(parents=True)
        (data_dir / "zotero.sqlite").touch()
    prefs_path = appdata / "Zotero" / "Zotero" / "Profiles" / "profile" / "prefs.js"
    prefs_path.parent.mkdir(parents=True)
    prefs_path.write_text(
        f'user_pref("extensions.zotero.dataDir", {json.dumps(str(configured))});\n',
        encoding="utf-8",
    )

    monkeypatch.delenv("ZOTERO_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    result = discover_local_zotero()

    assert result["data_dir"] == str(configured.resolve())


class _StableEmbeddingProvider:
    dimensions = 2
    cache_key = "sentence-transformers:library-migration-test"

    def __init__(self) -> None:
        self.document_batches: list[int] = []

    def embed_query(self, _query: str) -> list[float]:
        return [1.0, 0.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.document_batches.append(len(texts))
        return [[1.0, 0.0] for _text in texts]


def test_import_library_folder_replaces_sources_and_persists_root(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    selected = tmp_path / "selected-library"
    selected.mkdir()
    _paper(selected / "one.html", "Paper one", "Treatment one improved the measured outcome in the study population.")
    _paper(selected / "two.html", "Paper two", "Treatment two showed a distinct response in an independent experiment.")

    result = import_library_folder(
        workspace,
        evidence,
        notebook_id="research",
        folder_path=selected,
    )

    notebook = result["notebook"]
    assert result["source_format"] == "html"
    assert result["indexed"]["documents"] == 2
    assert notebook["root_path"] == str(selected.resolve())
    assert notebook["counts"]["sources"] == 2
    assert {source["title"] for source in notebook["sources"]} == {"Paper one", "Paper two"}


def test_import_library_folder_merge_existing_keeps_previous_linked_sources(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    first = tmp_path / "first-linked-folder"
    second = tmp_path / "second-linked-folder"
    first.mkdir()
    second.mkdir()
    _paper(first / "one.html", "First linked paper", "The first linked folder contains the original searchable result.")
    _paper(second / "two.html", "Second linked paper", "The second linked folder adds another searchable result.")

    initial = import_library_folder(workspace, evidence, notebook_id="research", folder_path=first)
    merged = import_library_folder(
        workspace,
        evidence,
        notebook_id="research",
        folder_path=second,
        merge_existing=True,
    )

    assert initial["notebook"]["counts"]["sources"] == 1
    assert merged["merged_existing"] is True
    assert merged["notebook"]["root_path"] == str(first.resolve())
    assert merged["notebook"]["counts"]["sources"] == 2
    assert {source["title"] for source in merged["notebook"]["sources"]} == {
        "First linked paper",
        "Second linked paper",
    }


def test_import_library_folder_ignores_dependency_trees_and_prunes_removed_documents(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    selected = tmp_path / "selected-library"
    selected.mkdir()
    _paper(selected / "paper.html", "Research paper", "The field study reported a traceable ecological response.")
    _paper(selected / "obsolete.html", "Obsolete paper", "This stale paper must be removed from the next index generation.")

    first = import_library_folder(workspace, evidence, notebook_id="research", folder_path=selected)
    assert first["indexed"]["documents"] == 2

    (selected / "obsolete.html").unlink()
    dependency = selected / "node_modules" / "package"
    dependency.mkdir(parents=True)
    _paper(
        dependency / "README.html",
        "Dependency readme",
        "This package documentation must never be treated as a research source document.",
    )

    second = import_library_folder(workspace, evidence, notebook_id="research", folder_path=selected)

    assert second["indexed"]["documents"] == 1
    assert second["indexed"]["removed_documents"] == 1
    assert {source["title"] for source in second["notebook"]["sources"]} == {"Research paper"}


def test_import_library_folder_reports_pipeline_milestones_and_persists_quality(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    selected = tmp_path / "quality-library"
    selected.mkdir()
    _paper(selected / "paper.html", "Quality paper", "A traceable result belongs to a named section in the source document.")
    events: list[dict[str, object]] = []

    result = import_library_folder(
        workspace,
        evidence,
        notebook_id="research",
        folder_path=selected,
        progress=events.append,
    )

    phases = [str(event["phase"]) for event in events]
    assert phases[0] == "扫描资料目录"
    assert "建立文档与章节结构" in phases
    assert "核验原文证据定位" in phases
    assert phases[-1] == "资料已可检索"
    assert events[-1]["progress"] == 1.0
    assert result["indexed"]["quality"]["passed"] is True
    assert result["notebook"]["metadata"]["evidence_quality"] == {
        "passed": True,
        "documents": 1,
        "sections": 2,
        "spans": 1,
        "missing_structure_spans": 0,
        "oversized_spans": 0,
        "source_text_mismatches": 0,
        "orphan_sections": 0,
        "reference_spans": 0,
        "warning_count": 0,
    }


def test_import_library_files_passes_workspace_to_document_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace, evidence = _workspace(tmp_path)
    source = tmp_path / "paper.txt"
    source.write_text("placeholder", encoding="utf-8")
    seen: list[Path | None] = []

    def fake_extract(path, *, output_dir, parser="auto", workspace=None):
        seen.append(Path(workspace) if workspace is not None else None)
        text_path = Path(output_dir) / "source.md"
        text_path.write_text("# Catalog title\n\nA traceable text extraction is indexed as evidence.", encoding="utf-8")
        return {
            "source_id": "source-test",
            "text_path": str(text_path),
            "parser": "fake",
            "page_count": 1,
            "character_count": 70,
        }

    monkeypatch.setattr("scansci_html.library_manager.extract_local_document", fake_extract)
    result = import_library_files(
        workspace,
        evidence,
        notebook_id="research",
        file_paths=[source],
        replace_existing=True,
    )

    assert seen == [workspace.resolve()]
    assert result["notebook"]["sources"][0]["title"] == "Catalog title"


def test_each_notebook_keeps_an_isolated_evidence_index(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    evidence = tmp_path / "evidence.sqlite"
    first = tmp_path / "first-library"
    second = tmp_path / "second-library"
    first.mkdir()
    second.mkdir()
    _paper(first / "first.html", "First paper", "The first library contains a unique ecological treatment result.")
    _paper(second / "second.html", "Second paper", "The second library contains a distinct photovoltaic microclimate result.")
    initialize_notebook(workspace, notebook_id="first", title="First", root_path=first)
    initialize_notebook(workspace, notebook_id="second", title="Second", root_path=second)

    import_library_folder(workspace, evidence, notebook_id="first", folder_path=first)
    import_library_folder(workspace, evidence, notebook_id="second", folder_path=second)

    summary = load_workspace_summary(workspace)
    notebooks = {item["notebook_id"]: item for item in summary["notebooks"]}
    first_db = Path(notebooks["first"]["sources"][0]["evidence_db_path"])
    second_db = Path(notebooks["second"]["sources"][0]["evidence_db_path"])
    assert first_db != second_db
    with sqlite3.connect(first_db) as connection:
        assert connection.execute("select title from source_documents").fetchall() == [("First paper",)]
    with sqlite3.connect(second_db) as connection:
        assert connection.execute("select title from source_documents").fetchall() == [("Second paper",)]


def test_isolated_library_adopts_matching_vectors_from_legacy_shared_index(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    workspace, legacy_evidence = _workspace(tmp_path)
    provider = _StableEmbeddingProvider()
    legacy_rows = load_embedding_cache_rows(legacy_evidence)
    prewarm_embedding_cache(legacy_evidence, legacy_rows, provider=provider)
    assert provider.document_batches == [len(legacy_rows)]

    result = import_library_folder(
        workspace,
        legacy_evidence,
        notebook_id="research",
        folder_path=tmp_path / "old-library",
    )

    migration = result["indexed"]["vector_cache_migration"]
    assert migration["migrated_vectors"] == len(legacy_rows)
    assert migration["sources_used"] == [legacy_evidence.name]
    target = notebook_evidence_db(legacy_evidence, "research")
    reused_provider = _StableEmbeddingProvider()
    reused = prewarm_embedding_cache(
        target,
        load_embedding_cache_rows(target),
        provider=reused_provider,
    )
    assert reused["ready"] is True
    assert reused["reused"] == len(legacy_rows)
    assert reused_provider.document_batches == []
    assert result["notebook"]["metadata"]["vector_cache_migration"]["migrated_vectors"] == len(legacy_rows)


def test_rebuilding_a_library_preserves_matching_vectors_from_its_previous_index(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    workspace, evidence = _workspace(tmp_path)
    selected = tmp_path / "selected-library"
    selected.mkdir()
    _paper(selected / "one.html", "Paper one", "This stable original evidence should retain its semantic vector after reindexing.")
    initial = import_library_folder(workspace, evidence, notebook_id="research", folder_path=selected)
    target = Path(initial["indexed"]["db_path"])
    first_provider = _StableEmbeddingProvider()
    rows = load_embedding_cache_rows(target)
    prewarm_embedding_cache(target, rows, provider=first_provider)
    assert first_provider.document_batches == [len(rows)]

    rebuilt = import_library_folder(workspace, evidence, notebook_id="research", folder_path=selected)

    migration = rebuilt["indexed"]["vector_cache_migration"]
    assert migration["migrated_vectors"] == len(rows)
    assert target.name in migration["sources_used"]
    second_provider = _StableEmbeddingProvider()
    resumed = prewarm_embedding_cache(
        target,
        load_embedding_cache_rows(target),
        provider=second_provider,
    )
    assert resumed["ready"] is True
    assert resumed["reused"] == len(rows)
    assert second_provider.document_batches == []


def test_vector_migration_rejects_unmatched_legacy_evidence(tmp_path: Path):
    pytest.importorskip("sqlite_vec")
    workspace, legacy_evidence = _workspace(tmp_path)
    provider = _StableEmbeddingProvider()
    prewarm_embedding_cache(legacy_evidence, load_embedding_cache_rows(legacy_evidence), provider=provider)
    selected = tmp_path / "different-library"
    selected.mkdir()
    _paper(selected / "different.html", "Different paper", "This unrelated evidence must never inherit a vector from the old library.")

    result = import_library_folder(
        workspace,
        legacy_evidence,
        notebook_id="research",
        folder_path=selected,
    )

    migration = result["indexed"]["vector_cache_migration"]
    assert migration["migrated_vectors"] == 0
    target = notebook_evidence_db(legacy_evidence, "research")
    fresh_provider = _StableEmbeddingProvider()
    fresh = prewarm_embedding_cache(target, load_embedding_cache_rows(target), provider=fresh_provider)
    assert fresh["ready"] is True
    assert fresh["reused"] == 0
    assert fresh_provider.document_batches == [1]


def test_invalid_folder_does_not_replace_existing_library(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ValueError, match="没有可索引"):
        import_library_folder(workspace, evidence, notebook_id="research", folder_path=empty)

    notebook = load_workspace_summary(workspace, notebook_id="research")["notebooks"][0]
    assert notebook["counts"]["sources"] == 1
    assert notebook["sources"][0]["title"] == "Old paper"


def test_import_library_folder_uses_sqlite_backup_when_windows_blocks_replace(tmp_path: Path, monkeypatch):
    workspace, evidence = _workspace(tmp_path)
    selected = tmp_path / "selected-library"
    selected.mkdir()
    _paper(selected / "new.html", "Replacement paper", "The fallback safely installs the newly selected evidence library.")

    def blocked_replace(source, destination):
        raise PermissionError("database is open")

    monkeypatch.setattr("scansci_html.library_manager.os.replace", blocked_replace)
    result = import_library_folder(workspace, evidence, notebook_id="research", folder_path=selected)

    assert result["notebook"]["counts"]["sources"] == 1
    assert result["notebook"]["sources"][0]["title"] == "Replacement paper"


def test_obsidian_vault_import_persists_the_library_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace, evidence = _workspace(tmp_path)
    vault = tmp_path / "research-vault"
    vault.mkdir()
    (vault / "note.md").write_text(
        "# Field notebook\n\nThe Obsidian knowledge base preserves searchable experimental observations.",
        encoding="utf-8",
    )
    (vault / "attachment.pdf").write_bytes(b"%PDF-1.4\nattachment")
    assets = vault / "assets"
    assets.mkdir()
    (assets / "index.md").write_text("# Reference-only imported paper", encoding="utf-8")
    hidden = vault / ".obsidian"
    hidden.mkdir()
    (hidden / "internal.md").write_text("# Internal configuration", encoding="utf-8")

    def reject_attachment_conversion(*_args, **_kwargs):
        raise AssertionError("Obsidian attachments must not enter the document-conversion path")

    monkeypatch.setattr("scansci_html.library_manager.import_library_files", reject_attachment_conversion)

    result = import_library_folder(
        workspace,
        evidence,
        notebook_id="research",
        folder_path=vault,
        library_kind="obsidian",
    )

    assert result["library_kind"] == "obsidian"
    assert result["source_format"] == "markdown"
    assert result["notebook"]["metadata"]["library_kind"] == "obsidian"
    assert result["notebook"]["counts"]["sources"] == 2
    assert {source["title"] for source in result["notebook"]["sources"]} == {
        "Field notebook",
        "Reference-only imported paper",
    }


def test_register_zotero_library_keeps_the_active_knowledge_root_and_lists_pdfs(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    zotero = tmp_path / "zotero-storage"
    zotero.mkdir()
    (zotero / "paper-one.pdf").write_bytes(b"%PDF-1.4\nexample")
    (zotero / "paper-two.pdf").write_bytes(b"%PDF-1.4\nexample")
    active_root = load_workspace_summary(workspace, notebook_id="research")["notebooks"][0]["root_path"]

    result = register_zotero_library(workspace, notebook_id="research", folder_path=zotero)

    assert result["zotero"]["pdf_count"] == 2
    assert result["notebook"]["root_path"] == active_root
    assert result["notebook"]["metadata"]["zotero"]["path"] == str(zotero.resolve())
    assert result["notebook"]["metadata"]["zotero"]["sample_titles"] == ["paper-one", "paper-two"]


def test_connect_local_zotero_reads_metadata_without_a_cloud_key(tmp_path: Path):
    workspace, _evidence = _workspace(tmp_path)

    class FakeZotero:
        def items(self, *, limit):
            assert limit == 10_000
            return [
                {
                    "key": "ABC123",
                    "data": {
                        "itemType": "journalArticle",
                        "title": "Evidence-linked interfaces",
                        "DOI": "10.1000/example",
                        "date": "2026",
                        "publicationTitle": "Research UI",
                        "creators": [{"firstName": "Ada", "lastName": "Li"}],
                    },
                },
                {"key": "PDF123", "data": {"itemType": "attachment", "contentType": "application/pdf"}},
            ]

        def collections(self):
            return [
                {
                    "key": "COLL001",
                    "data": {"key": "COLL001", "name": "博士论文", "parentCollection": False},
                    "meta": {"numItems": 14},
                }
            ]

    result = connect_local_zotero(workspace, notebook_id="research", client=FakeZotero())

    assert result["zotero"]["connection"] == "local-api"
    assert result["zotero"]["item_count"] == 1
    assert result["zotero"]["pdf_count"] == 1
    assert result["zotero"]["items"][0]["creators"] == ["Ada Li"]
    assert result["zotero"]["collections"] == [
        {"key": "COLL001", "name": "博士论文", "parent": "", "item_count": 14}
    ]


def test_connect_local_zotero_reads_every_top_level_page_without_truncation(tmp_path: Path):
    workspace, _evidence = _workspace(tmp_path)

    class FakePaginatedZotero:
        def top(self, *, limit):
            assert limit == 100
            return self._literature()[:limit]

        def items(self, *, limit, itemType):
            assert limit == 100
            assert itemType == "attachment"
            return [
                {"key": "PDF001", "data": {"itemType": "attachment", "contentType": "application/pdf"}},
            ]

        def collections(self, *, limit):
            assert limit == 100
            return [
                {
                    "key": "COLL001",
                    "data": {"key": "COLL001", "name": "Complete library", "parentCollection": False},
                    "meta": {"numItems": 235},
                }
            ]

        def everything(self, first_page):
            first = list(first_page)
            if first and str(first[0].get("data", {}).get("itemType", "")) == "journalArticle":
                return self._literature()
            return first

        @staticmethod
        def _literature():
            return [
                {
                    "key": f"ITEM{index:04d}",
                    "data": {
                        "itemType": "journalArticle",
                        "title": f"Complete record {index}",
                        "collections": ["COLL001"],
                    },
                }
                for index in range(235)
            ]

    result = connect_local_zotero(workspace, notebook_id="research", client=FakePaginatedZotero())

    assert result["zotero"]["item_count"] == 235
    assert len(result["zotero"]["items"]) == 235
    assert result["zotero"]["items"][-1]["title"] == "Complete record 234"
    assert result["zotero"]["pdf_count"] == 1


def test_connect_local_zotero_auto_discovers_read_only_database_and_pdf_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace, _evidence = _workspace(tmp_path)
    data_dir = tmp_path / "Zotero"
    attachment_dir = data_dir / "storage" / "PDFKEY01"
    attachment_dir.mkdir(parents=True)
    pdf_path = attachment_dir / "evidence-linked.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfixture")
    database = data_dir / "zotero.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INTEGER, dateModified TEXT, key TEXT);
        CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
        CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER, orderIndex INTEGER);
        CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT, parentCollectionID INTEGER, key TEXT);
        CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER, orderIndex INTEGER);
        CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE itemTags (itemID INTEGER, tagID INTEGER);
        CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER, linkMode INTEGER, contentType TEXT, path TEXT);
        INSERT INTO itemTypes VALUES (1, 'journalArticle'), (2, 'attachment');
        INSERT INTO items VALUES (1, 1, '2026-07-23', 'ITEM0001'), (2, 2, '2026-07-23', 'PDFKEY01');
        INSERT INTO fields VALUES (1, 'title'), (2, 'DOI'), (3, 'date'), (4, 'publicationTitle');
        INSERT INTO itemDataValues VALUES
          (1, 'Evidence-linked interfaces'), (2, '10.1000/example'), (3, '2026'), (4, 'Research UI');
        INSERT INTO itemData VALUES (1, 1, 1), (1, 2, 2), (1, 3, 3), (1, 4, 4);
        INSERT INTO creators VALUES (1, 'Ada', 'Li');
        INSERT INTO itemCreators VALUES (1, 1, 0);
        INSERT INTO collections VALUES (1, '博士论文', NULL, 'COLL001');
        INSERT INTO collectionItems VALUES (1, 1, 0);
        INSERT INTO tags VALUES (1, '接口'), (2, 'priority');
        INSERT INTO itemTags VALUES (1, 1), (1, 2);
        INSERT INTO itemAttachments VALUES (2, 1, 1, 'application/pdf', 'storage:evidence-linked.pdf');
        """
    )
    connection.commit()
    connection.close()
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(data_dir))

    result = connect_local_zotero(workspace, notebook_id="research")

    zotero = result["zotero"]
    assert zotero["connection"] == "local-database"
    assert zotero["auto_discovered"] is True
    assert zotero["read_only"] is True
    assert zotero["source_storage"] == "external-reference"
    assert zotero["item_count"] == 1
    assert zotero["pdf_count"] == 1
    assert zotero["collection_count"] == 1
    assert zotero["items"][0]["title"] == "Evidence-linked interfaces"
    assert zotero["items"][0]["creators"] == ["Ada Li"]
    assert zotero["items"][0]["collections"] == ["COLL001"]
    assert zotero["items"][0]["tags"] == ["priority", "接口"]
    assert zotero["items"][0]["pdf_path"] == str(pdf_path.resolve())
    assert zotero["items"][0]["attachments"][0]["exists"] is True
    assert result["notebook"]["metadata"]["zotero"]["database_path"] == str(database.resolve())
    assert result["notebook"]["root_path"] == str(data_dir.resolve())


def test_connect_local_zotero_can_return_metadata_without_blocking_on_pdf_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace, evidence = _workspace(tmp_path)
    data_dir = tmp_path / "Zotero"
    attachment_dir = data_dir / "storage" / "PDFKEY01"
    attachment_dir.mkdir(parents=True)
    (attachment_dir / "evidence-linked.pdf").write_bytes(b"%PDF-1.4\nfixture")
    database = data_dir / "zotero.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INTEGER, dateModified TEXT, key TEXT);
        CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
        CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER, orderIndex INTEGER);
        CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT, parentCollectionID INTEGER, key TEXT);
        CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER, orderIndex INTEGER);
        CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER, linkMode INTEGER, contentType TEXT, path TEXT);
        INSERT INTO itemTypes VALUES (1, 'journalArticle'), (2, 'attachment');
        INSERT INTO items VALUES (1, 1, '2026-07-23', 'ITEM0001'), (2, 2, '2026-07-23', 'PDFKEY01');
        INSERT INTO fields VALUES (1, 'title');
        INSERT INTO itemDataValues VALUES (1, 'Metadata first');
        INSERT INTO itemData VALUES (1, 1, 1);
        INSERT INTO itemAttachments VALUES (2, 1, 1, 'application/pdf', 'storage:evidence-linked.pdf');
        """
    )
    connection.commit()
    connection.close()
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(data_dir))

    def fail_if_indexed(*_args, **_kwargs):
        raise AssertionError("PDF indexing must not run during the metadata request")

    monkeypatch.setattr("scansci_html.library_manager.index_zotero_attachments", fail_if_indexed)

    before_sources = load_workspace_summary(workspace, notebook_id="research")["notebooks"][0]["counts"]["sources"]
    result = connect_local_zotero(
        workspace,
        notebook_id="research",
        evidence_db=evidence,
        index_attachments=False,
    )

    assert result["zotero"]["item_count"] == 1
    assert result["zotero"]["evidence_index"]["status"] == "queued"
    assert result["notebook"]["counts"]["sources"] == before_sources


def test_zotero_attachment_index_does_not_treat_a_partial_evidence_db_as_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace, evidence = _workspace(tmp_path)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"%PDF-1.4\nfirst")
    second.write_bytes(b"%PDF-1.4\nsecond")
    target_db = notebook_evidence_db(evidence, "research")
    target_db.parent.mkdir(parents=True, exist_ok=True)
    target_db.write_bytes(b"x" * 1_000_000)
    calls: list[tuple[list[Path], bool]] = []

    def fake_import(_workspace, _evidence, *, notebook_id, file_paths, progress, replace_existing, source_metadata=None):
        calls.append((list(file_paths), replace_existing))
        progress({"phase": "提取原文内容", "progress": 1.0, "detail": "完成"})
        return {"db_path": str(target_db), "indexed": {"documents": 2}, "skipped_files": []}

    monkeypatch.setattr("scansci_html.library_manager.import_library_files", fake_import)

    result = index_zotero_attachments(
        workspace,
        evidence,
        notebook_id="research",
        zotero_state={
            "items": [
                {"attachments": [{"path": str(first), "exists": True}]},
                {"attachments": [{"path": str(second), "exists": True}]},
            ]
        },
    )

    assert result["status"] == "indexed"
    assert calls == [([first.resolve(), second.resolve()], True)]


def test_import_library_files_keeps_originals_in_place_and_discards_superseded_generations(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    evidence = tmp_path / "evidence.sqlite"
    initialize_notebook(
        workspace,
        notebook_id="local-references",
        title="Local references",
        root_path=tmp_path,
        metadata={"library_kind": "empty"},
    )
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("The first referenced document remains in its original folder and is searchable.", encoding="utf-8")
    second.write_text("The second referenced document is added without copying the original source file.", encoding="utf-8")

    initial = import_library_files(workspace, evidence, notebook_id="local-references", file_paths=[first])
    updated = import_library_files(workspace, evidence, notebook_id="local-references", file_paths=[second])

    assert first.read_text(encoding="utf-8").startswith("The first referenced")
    assert second.read_text(encoding="utf-8").startswith("The second referenced")
    assert not (tmp_path / ".scansci-ingestions").exists()
    assert updated["source_storage"] == "external-reference"
    assert updated["notebook"]["metadata"]["library_kind"] == "empty"
    assert updated["notebook"]["counts"]["sources"] == 2
    generations = list((tmp_path / ".scansci-library" / "local-references").glob("generation-*"))
    assert len(generations) == 1


def test_webapp_folder_endpoint_returns_refreshed_workspace(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    selected = tmp_path / "selected"
    selected.mkdir()
    _paper(selected / "new.html", "New paper", "The selected folder is now the active evidence library for this notebook.")
    app = NotebookWebApp(workspace=workspace, evidence_db=evidence)

    response = app.dispatch(
        "POST",
        "/api/library/folder",
        json.dumps({"notebook_id": "research", "path": str(selected)}).encode("utf-8"),
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status == 200
    assert payload["ok"] is True
    assert payload["workspace"]["counts"]["sources"] == 1
    assert payload["notebook"]["root_path"] == str(selected.resolve())


def test_webapp_zotero_endpoint_registers_a_local_pdf_shelf(tmp_path: Path):
    workspace, evidence = _workspace(tmp_path)
    storage = tmp_path / "zotero" / "storage"
    storage.mkdir(parents=True)
    (storage / "paper.pdf").write_bytes(b"%PDF-1.4\nexample")
    app = NotebookWebApp(workspace=workspace, evidence_db=evidence)

    response = app.dispatch(
        "POST",
        "/api/library/zotero",
        json.dumps({"notebook_id": "research", "path": str(storage)}).encode("utf-8"),
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status == 200
    assert payload["ok"] is True
    assert payload["zotero"]["pdf_count"] == 1
    assert payload["notebook"]["metadata"]["zotero"]["path"] == str(storage.resolve())


def test_webapp_first_folder_import_creates_a_notebook_automatically(tmp_path: Path):
    workspace = tmp_path / "fresh" / "workspace.sqlite"
    evidence = tmp_path / "fresh" / "evidence.sqlite"
    selected = tmp_path / "photovoltaic-literature"
    selected.mkdir()
    _paper(selected / "paper.html", "PV ecology", "Photovoltaic facilities can alter local microclimate and vegetation patterns.")
    app = NotebookWebApp(workspace=workspace, evidence_db=evidence)

    response = app.dispatch(
        "POST",
        "/api/library/folder",
        json.dumps({"path": str(selected), "library_kind": "folder"}).encode("utf-8"),
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status == 200
    assert payload["ok"] is True
    assert payload["notebook"]["title"] == "photovoltaic-literature"
    assert payload["workspace"]["counts"]["sources"] == 1


def test_webapp_creates_an_empty_local_knowledge_container(tmp_path: Path):
    workspace = tmp_path / "fresh" / "workspace.sqlite"
    app = NotebookWebApp(workspace=workspace, evidence_db=tmp_path / "fresh" / "evidence.sqlite")

    response = app.dispatch(
        "POST",
        "/api/library",
        json.dumps({"title": "博士论文核心文献"}).encode("utf-8"),
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status == 201
    assert payload["notebook"]["title"] == "博士论文核心文献"
    assert payload["notebook"]["metadata"]["library_kind"] == "empty"
    assert payload["notebook"]["counts"]["sources"] == 0


def test_webapp_removes_a_personal_library_but_preserves_linked_files(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    evidence = tmp_path / "evidence.sqlite"
    original = tmp_path / "kept-source.txt"
    original.write_text("The original file must remain available after removing its ScanSci library card.", encoding="utf-8")
    app = NotebookWebApp(workspace=workspace, evidence_db=evidence)
    created = json.loads(
        app.dispatch("POST", "/api/library", json.dumps({"title": "Temporary notes"}).encode("utf-8")).body.decode("utf-8")
    )
    notebook_id = str(created["notebook"]["notebook_id"])
    index = notebook_evidence_db(evidence, notebook_id)
    index.write_bytes(b"rebuildable index")

    response = app.dispatch("POST", f"/api/library/{notebook_id}/delete", b"{}")
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status == 200
    assert payload["removed"]["notebook_id"] == notebook_id
    assert payload["removed"]["linked_files_preserved"] is True
    assert original.is_file()
    assert not index.exists()
    assert notebook_id not in {item["notebook_id"] for item in payload["workspace"]["notebooks"]}


def test_webapp_does_not_delete_fixed_external_library_slots(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    app = NotebookWebApp(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")
    initialize_notebook(
        workspace,
        notebook_id="notion-source",
        title="Notion knowledge base",
        root_path=tmp_path,
        metadata={"library_kind": "notion"},
    )

    response = app.dispatch("POST", "/api/library/notion-source/delete", b"{}")
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status == 400
    assert "notion-source" in {item["notebook_id"] for item in load_workspace_summary(workspace)["notebooks"]}
    assert payload["error"]


def test_webapp_indexes_files_dropped_into_an_empty_library(tmp_path: Path):
    workspace = tmp_path / "fresh" / "workspace.sqlite"
    app = NotebookWebApp(workspace=workspace, evidence_db=tmp_path / "fresh" / "evidence.sqlite")
    created = json.loads(
        app.dispatch("POST", "/api/library", json.dumps({"title": "Drop target"}).encode("utf-8")).body.decode("utf-8")
    )
    content = base64.b64encode(b"Dropped local evidence remains searchable after the temporary upload is discarded.").decode("ascii")

    response = app.dispatch(
        "POST",
        "/api/library/uploads",
        json.dumps(
            {
                "notebook_id": created["notebook"]["notebook_id"],
                "files": [{"name": "dropped.txt", "data_url": f"data:text/plain;base64,{content}"}],
            }
        ).encode("utf-8"),
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status == 200
    assert payload["added_files"] == 1
    assert payload["notebook"]["counts"]["sources"] == 1
    assert payload["notebook"]["metadata"]["library_kind"] == "empty"
