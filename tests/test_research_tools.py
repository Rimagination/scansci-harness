import json
import threading
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from scansci_html import research_tools


@pytest.mark.parametrize("capabilities", [["image"], ["chat"]])
def test_provider_connection_reports_only_catalog_check(monkeypatch, tmp_path, capabilities):
    calls = []
    monkeypatch.setattr(research_tools, "get_provider_api_key", lambda *_: " first-key, second-key ")
    def request(url, **kwargs):
        calls.append((url, kwargs))
        return {"data": [{"id": "image-model"}]}
    monkeypatch.setattr(research_tools, "_request_json", request)
    result = research_tools.test_provider_connection(workspace=tmp_path / "workspace.sqlite", provider={
        "id": "fixture", "kind": "openai-compatible", "base_url": "https://example.org/v1",
        "models": [{"id": "image-model", "capabilities": capabilities}],
    })
    assert result["ok"] and result["checked_scope"] == "model_catalog"
    assert result["generation_verified"] is False and "尚未验证" in result["message"]
    assert len(calls) == 1 and calls[0][0].endswith("/models")
    assert calls[0][1]["headers"]["Authorization"] == "Bearer first-key"
    assert "first-key" not in json.dumps(result)
    if "image" in capabilities:
        assert "未发起生图" in result["message"]


@pytest.mark.parametrize("payload", [{}, {"error": {"message": "private-key"}}, {"data": "wrong"},
    {"data": [], "error": {"code": "forbidden"}}])
def test_provider_connection_rejects_invalid_catalog(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(research_tools, "get_provider_api_key", lambda *_: "private-key")
    monkeypatch.setattr(research_tools, "_request_json", lambda *_args, **_kwargs: payload)
    with pytest.raises(RuntimeError, match="连接检测未通过") as error:
        research_tools.test_provider_connection(workspace=tmp_path / "workspace.sqlite", provider={
            "id": "fixture", "kind": "openai-compatible", "base_url": "https://example.org/v1",
        })
    assert "private-key" not in str(error.value)


def test_jev_provider_connection_uses_typed_evaluation_endpoint(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(research_tools, "get_provider_api_key", lambda *_: "typesafe-secret")

    def ask(**kwargs):
        calls.append(kwargs)
        return {"model": "jev-latest", "answers": {"connection_check": {"type": "noul", "noul": 1}}}

    monkeypatch.setattr(research_tools, "ask_jev", ask)
    result = research_tools.test_provider_connection(
        workspace=tmp_path / "workspace.sqlite",
        provider={
            "id": "jev",
            "kind": "openai-compatible",
            "base_url": "https://api.typesafe.ai/v1/systemone",
        },
    )

    assert result["ok"] is True
    assert result["checked_scope"] == "jev_evaluation"
    assert result["generation_verified"] is False
    assert calls[0]["endpoint"] == "https://api.typesafe.ai/v1/systemone"
    assert calls[0]["state"] == "ScanSci connection check"
    assert calls[0]["questions"]["connection_check"]["type"] == "noul"
    assert "typesafe-secret" not in json.dumps(result)


def test_search_journals_uses_scansci_origin_headers(monkeypatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return {
            "items": [
                {
                    "id": 1,
                    "title": "Example Journal",
                    "issn": "1234-5678",
                    "if_2023": 5.2,
                    "jcr_quartile": "Q1",
                    "cas_2025": "1区",
                }
            ]
        }

    monkeypatch.setattr(research_tools, "_request_json", fake_request)

    result = research_tools.search_journals("请查询期刊：Example Journal")

    assert result["items"][0]["title"] == "Example Journal"
    assert parse_qs(urlparse(calls[0][0]).query)["q"] == ["Example Journal"]
    assert result["search_intent"]["subject"] == "Example Journal"
    assert calls[0][1]["headers"]["Origin"] == "https://journal.scansci.com"


def test_paper_atlas_returns_honest_web_fallback(monkeypatch):
    def fail_request(*args, **kwargs):
        raise RuntimeError("远程服务返回 HTTP 503")

    monkeypatch.setattr(research_tools, "_request_json", fail_request)

    result = research_tools.search_paper_atlas("climate change")

    assert result["status"] == "external"
    assert result["items"] == []
    assert result["external_url"] == "https://paperatlas.scansci.com/"
    assert "503" in result["message"]


def test_paper_atlas_forwards_only_compiled_subject(monkeypatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return []

    monkeypatch.setattr(research_tools, "_request_json", fake_request)

    result = research_tools.search_paper_atlas(
        "请检索并整理相关论文的作者、年份、来源与 DOI。\n研究主题：植物功能性状"
    )

    assert parse_qs(urlparse(calls[0][0]).query)["q"] == ["植物功能性状"]
    assert result["search_intent"]["raw_query"].startswith("请检索并整理")
    assert result["search_intent"]["subject"] == "植物功能性状"


def test_capability_snapshot_marks_paper_atlas_as_web_handoff(tmp_path: Path):
    evidence = tmp_path / "evidence.sqlite"
    evidence.write_bytes(b"")

    snapshot = research_tools.capability_snapshot(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence)
    atlas = next(item for item in snapshot["tools"] if item["id"] == "paper-atlas")

    assert atlas["status"] == "external"


# --------------------------------------------------------------------------- #
# download_papers — batch download loop
# --------------------------------------------------------------------------- #


_VALID_DOI = "10.1038/s41586-024-99999-9"
_VALID_ARXIV = "2401.04088"


@pytest.fixture(autouse=True)
def _no_batch_throttling(monkeypatch):
    """Zero out the inter-item delay so batch tests run instantly.

    Production defaults sleep 3-8s between downloads; tests must not.
    """
    monkeypatch.setattr(research_tools, "_BATCH_DELAY_MIN", 0.0)
    monkeypatch.setattr(research_tools, "_BATCH_DELAY_MAX", 0.0)


def _isolated_workspace(tmp_path: Path) -> Path:
    """Return a workspace path whose downloads dir is unique to this test.

    ``_download_directory`` resolves to ``workspace.parent / "downloads"``, and
    pytest nests every test's tmp_path under the same session parent — so two
    batch tests would otherwise share one downloads/ folder and a PDF written
    by one would be seen as "already downloaded" by the next. Nesting the
    workspace one level deeper gives each test its own downloads/ dir.
    """
    nested = tmp_path / "ws" / "workspace.sqlite"
    nested.parent.mkdir(parents=True, exist_ok=True)
    return nested


def _fake_download_factory(results: dict[str, dict]):
    """Build a fake download_paper that serves canned results per identifier.

    Identifiers without a canned result raise RuntimeError, simulating a
    network/archive failure so we can exercise the per-item resilience.
    """

    def _fake(identifier, *, workspace, strategy="legal_only", timeout=180.0, **_kwargs):
        if identifier in results:
            return results[identifier]
        raise RuntimeError(f"no fixture for {identifier}")

    return _fake


def test_download_papers_rejects_empty_or_invalid_list(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    monkeypatch.setattr(research_tools, "download_paper", _fake_download_factory({}))
    with pytest.raises(ValueError):
        research_tools.download_papers([], workspace=ws)
    with pytest.raises(ValueError):
        research_tools.download_papers(["not-a-doi", "also invalid"], workspace=ws)


def test_download_papers_reports_per_item_success_and_failure(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    monkeypatch.setattr(research_tools, "download_paper", _fake_download_factory({_VALID_DOI: {"files": [str(tmp_path / "a.pdf")]}}))
    progress: list[dict] = []

    result = research_tools.download_papers(
        [_VALID_DOI, _VALID_ARXIV],
        workspace=ws,
        on_progress=progress.append,
    )

    statuses = {item["identifier"]: item["status"] for item in result["items"]}
    assert statuses[_VALID_DOI] == "completed"
    assert statuses[_VALID_ARXIV] == "failed"
    assert result["completed"] == 1
    assert result["failed"] == 1
    assert result["total"] == 2
    assert result["ok"] is False  # one failure
    # on_progress called once at start, then per-item transitions.
    assert len(progress) >= 3
    assert progress[0]["total"] == 2  # initial emit lists both items


def test_download_papers_one_failure_does_not_abort_batch(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    calls: list[str] = []
    good = {"files": [str(tmp_path / "x.pdf")]}

    def _fake(identifier, **kwargs):
        calls.append(identifier)
        if identifier == _VALID_ARXIV:
            raise RuntimeError("boom")
        return good

    monkeypatch.setattr(research_tools, "download_paper", _fake)
    result = research_tools.download_papers([_VALID_DOI, _VALID_ARXIV], workspace=ws)

    assert _VALID_DOI in calls and _VALID_ARXIV in calls
    assert result["completed"] == 1 and result["failed"] == 1


def test_download_papers_isolates_parallel_worker_output_before_commit(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    identifiers = ["10.1000/alpha", "10.1000/beta"]
    barrier = threading.Barrier(2)

    def fake_download(identifier, *, _output_dir=None, **_kwargs):
        staging = Path(str(_output_dir))
        staging.mkdir(parents=True, exist_ok=True)
        barrier.wait(timeout=5)
        output = staging / "download.pdf"
        output.write_bytes(b"%PDF-1.4 " + identifier.encode("ascii"))
        return {"files": [str(output)]}

    monkeypatch.setattr(research_tools, "download_paper", fake_download)
    monkeypatch.setattr(
        research_tools,
        "_crossref_filename_metadata",
        lambda identifier, **_kwargs: {
            "author": [{"family": identifier.rsplit("/", 1)[-1]}],
            "published": {"date-parts": [[2024]]},
            "title": [identifier.rsplit("/", 1)[-1]],
        },
    )

    result = research_tools.download_papers(identifiers, workspace=ws)

    assert result["completed"] == 2
    assert result["failed"] == 0
    assert len(result["files"]) == 2
    committed = [Path(path) for path in result["files"]]
    assert all(path.is_file() for path in committed)
    assert len({path.name for path in committed}) == 2
    assert {
        path.read_bytes().removeprefix(b"%PDF-1.4 ").decode("ascii")
        for path in committed
    } == set(identifiers)


def test_download_papers_cancel_check_stops_loop_cleanly(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    monkeypatch.setattr(
        research_tools,
        "download_paper",
        _fake_download_factory({_VALID_DOI: {"files": [str(tmp_path / "a.pdf")]}}),
    )

    # Cancel after the first real download attempt.
    attempts = {"n": 0}

    def _cancel():
        attempts["n"] += 1
        return attempts["n"] > 1  # allow first item, cancel before second

    with pytest.raises(research_tools._BatchCancelled):
        research_tools.download_papers(
            [_VALID_DOI, _VALID_ARXIV, "10.1038/s41586-024-00001-0"],
            workspace=ws,
            cancel_check=_cancel,
        )


def test_download_papers_cancelled_after_fetch_does_not_commit_file(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    cancelled = {"value": False}

    def fake_download(identifier, *, _output_dir=None, **_kwargs):
        staging = Path(str(_output_dir))
        output = staging / "download.pdf"
        output.write_bytes(b"%PDF-1.4 staged")
        cancelled["value"] = True
        return {"files": [str(output)]}

    monkeypatch.setattr(research_tools, "download_paper", fake_download)

    with pytest.raises(research_tools._BatchCancelled):
        research_tools.download_papers(
            [_VALID_DOI],
            workspace=ws,
            cancel_check=lambda: cancelled["value"],
        )

    committed = [
        path
        for path in research_tools._download_directory(ws).glob("*.pdf")
        if path.is_file()
    ]
    assert committed == []


def test_download_papers_skips_items_already_on_disk(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    download_dir = research_tools._download_directory(ws)
    download_dir.mkdir(parents=True, exist_ok=True)
    # Pre-create a PDF whose filename contains the slugified identifier.
    slug = research_tools._SAFE_NAME.sub("_", _VALID_DOI).lower()
    existing = download_dir / f"{slug}-paper.pdf"
    existing.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(research_tools, "_crossref_filename_metadata", lambda *_args, **_kwargs: {})

    called: list[str] = []
    monkeypatch.setattr(
        research_tools,
        "download_paper",
        lambda identifier, **kwargs: called.append(identifier) or {"files": [str(tmp_path / "should-not-be-used.pdf")]},
    )

    result = research_tools.download_papers([_VALID_DOI], workspace=ws)

    assert result["completed"] == 1
    assert result["items"][0]["status"] == "completed"
    canonical = download_dir / f"{slug}.pdf"
    assert result["items"][0]["files"] == [str(canonical.resolve())]
    assert canonical.exists()
    assert called == []  # download_paper never invoked for the cached item


def test_download_papers_deduplicates_doi_urls_and_arxiv_aliases(tmp_path, monkeypatch):
    ws = _isolated_workspace(tmp_path)
    called: list[str] = []

    def _fake(identifier, **_kwargs):
        called.append(identifier)
        return {"files": []}

    monkeypatch.setattr(research_tools, "download_paper", _fake)
    result = research_tools.download_papers(
        [
            _VALID_DOI,
            f"https://doi.org/{_VALID_DOI}",
            "2401.04088",
            "arXiv:2401.04088",
            "10.48550/arXiv.2401.04088",
        ],
        workspace=ws,
    )

    # Batch downloads intentionally use a small worker pool, so completion
    # order is nondeterministic.  Deduplication is the contract; ordering is
    # not.
    assert set(called) == {_VALID_DOI, "2401.04088"}
    assert result["total"] == 2


def test_canonicalize_download_files_keeps_one_scansci_pdf_named_copy(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    first = download_dir / "10.1038_nature02403_LibGen.pdf"
    second = download_dir / "10.1038_nature02403_OpenAlex.pdf"
    first.write_bytes(b"%PDF-1.4" + b"a" * 128)
    second.write_bytes(b"%PDF-1.4" + b"b" * 256)
    monkeypatch.setattr(
        research_tools,
        "_crossref_filename_metadata",
        lambda *_args, **_kwargs: {
            "author": [{"family": "Reich"}],
            "published-print": {"date-parts": [[2004]]},
            "title": ["The worldwide leaf economics spectrum"],
        },
    )

    files = research_tools._canonicalize_download_files(
        "10.1038/nature02403",
        [str(first), str(second)],
        download_dir=download_dir,
        timeout=15,
    )

    canonical = download_dir / "Reich2004_Worldwide.pdf"
    assert files == [str(canonical.resolve())]
    assert canonical.read_bytes().endswith(b"b" * 256)
    assert not first.exists()
    assert not second.exists()
    index = json.loads((download_dir / ".doi_index.json").read_text(encoding="utf-8"))
    assert index["10.1038/nature02403"] == str(canonical.resolve())


def test_stale_doi_index_entries_are_pruned_before_cache_lookup(tmp_path):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    missing = download_dir / "missing.pdf"
    valid = download_dir / "valid.pdf"
    valid.write_bytes(b"%PDF-1.4 fixture")
    index_path = download_dir / ".doi_index.json"
    index_path.write_text(
        json.dumps(
            {
                "10.1000/missing": str(missing),
                "10.1000/valid": str(valid),
            }
        ),
        encoding="utf-8",
    )

    result = research_tools._identifier_already_downloaded("10.1000/missing", download_dir)
    cleaned = json.loads(index_path.read_text(encoding="utf-8"))

    assert result == []
    assert "10.1000/missing" not in cleaned
    assert cleaned["10.1000/valid"] == str(valid.resolve())


def test_oa_first_uses_one_public_location_before_cli_race(tmp_path, monkeypatch):
    destination = research_tools._download_directory(_isolated_workspace(tmp_path)) / "10.1038_nature02403.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"%PDF-1.4" + b"x" * 128)
    cli_called = False

    def fail_cli(*_args, **_kwargs):
        nonlocal cli_called
        cli_called = True
        raise AssertionError("OA-first should use the selected public location directly")

    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(research_tools, "subprocess", type("Subprocess", (), {"run": fail_cli}))
    monkeypatch.setattr(
        research_tools,
        "_download_from_public_archives",
        lambda identifier, **_kwargs: {"files": [str(destination)], "source": "OpenAlex best OA"},
    )
    monkeypatch.setattr(
        research_tools,
        "_crossref_filename_metadata",
        lambda *_args, **_kwargs: {
            "author": [{"family": "Reich"}],
            "published-print": {"date-parts": [[2004]]},
            "title": ["The worldwide leaf economics spectrum"],
        },
    )

    result = research_tools.download_paper(
        "10.1038/nature02403",
        workspace=_isolated_workspace(tmp_path),
        strategy="oa_first",
    )

    assert cli_called is False
    assert result["files"] == [str((destination.parent / "Reich2004_Worldwide.pdf").resolve())]


def test_download_paper_defaults_to_oa_first(tmp_path, monkeypatch):
    identifier = "10.1038/nature02403"
    destination = research_tools._download_directory(_isolated_workspace(tmp_path)) / "10.1038_nature02403.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"%PDF-1.4" + b"x" * 128)
    monkeypatch.setattr(
        research_tools,
        "_download_from_public_archives",
        lambda identifier, **_kwargs: {"files": [str(destination)], "source": "OpenAlex best OA"},
    )
    monkeypatch.setattr(
        research_tools,
        "_crossref_filename_metadata",
        lambda *_args, **_kwargs: {
            "author": [{"family": "Reich"}],
            "published-print": {"date-parts": [[2004]]},
            "title": ["The worldwide leaf economics spectrum"],
        },
    )

    result = research_tools.download_paper(
        identifier,
        workspace=_isolated_workspace(tmp_path),
    )

    assert result["strategy"] == "oa_first"


def test_download_papers_retries_on_rate_limit_then_succeeds(tmp_path, monkeypatch):
    """A 429-ish error is retried with backoff rather than recorded as failure."""
    monkeypatch.setattr(research_tools, "_BATCH_MAX_RETRIES", 3)
    monkeypatch.setattr(research_tools, "_cancelable_sleep", lambda _s, _c: None)
    attempts = {"n": 0}

    def _fake(identifier, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("HTTP 429 Too Many Requests")
        return {"files": [str(tmp_path / "recovered.pdf")]}

    monkeypatch.setattr(research_tools, "download_paper", _fake)
    result = research_tools.download_papers([_VALID_DOI], workspace=_isolated_workspace(tmp_path))

    assert attempts["n"] == 3
    assert result["completed"] == 1
    assert result["items"][0]["status"] == "completed"


def test_batch_downloads_keep_source_health_in_stable_download_dir(tmp_path, monkeypatch):
    captured: list[Path] = []
    monkeypatch.setattr(research_tools, "_BATCH_DELAY_MAX", 0.0)

    def fake_download(identifier, **kwargs):
        captured.append(Path(kwargs["_source_health_dir"]).resolve())
        return {"files": [str(tmp_path / "paper.pdf")], "source": "Working repository"}

    monkeypatch.setattr(research_tools, "download_paper", fake_download)

    workspace = _isolated_workspace(tmp_path)
    result = research_tools.download_papers([_VALID_DOI], workspace=workspace)

    assert result["completed"] == 1
    assert captured == [research_tools._download_directory(workspace).resolve()]


def test_download_papers_does_not_retry_permanent_failure(tmp_path, monkeypatch):
    """A genuine 'not found' is not retried — only rate-limit hints are."""
    monkeypatch.setattr(research_tools, "_cancelable_sleep", lambda _s, _c: None)
    attempts = {"n": 0}

    def _fake(identifier, **kwargs):
        attempts["n"] += 1
        raise RuntimeError("paper not found in any archive")

    monkeypatch.setattr(research_tools, "download_paper", _fake)
    result = research_tools.download_papers([_VALID_DOI], workspace=_isolated_workspace(tmp_path))

    assert attempts["n"] == 1  # no retry
    assert result["failed"] == 1
    assert "not found" in result["items"][0]["error"]


def test_download_papers_inter_request_delay_skipped_for_single_item(tmp_path, monkeypatch):
    """A one-item batch must not pay the inter-item delay (first request skips)."""
    monkeypatch.setattr(research_tools, "_BATCH_DELAY_MIN", 999.0)
    monkeypatch.setattr(research_tools, "_BATCH_DELAY_MAX", 999.0)
    monkeypatch.setattr(
        research_tools,
        "download_paper",
        lambda identifier, **kwargs: {"files": [str(tmp_path / "solo.pdf")]},
    )
    result = research_tools.download_papers([_VALID_DOI], workspace=_isolated_workspace(tmp_path))
    assert result["completed"] == 1


def test_download_paper_passes_env_overrides_to_subprocess(tmp_path, monkeypatch):
    """env_overrides merge into the scansci-pdf subprocess environment."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(research_tools.shutil, "which", lambda _n: "scansci-pdf")
    monkeypatch.setattr(research_tools.subprocess, "run", fake_run)
    monkeypatch.setattr(
        research_tools,
        "_download_from_public_archives",
        lambda *a, **k: {"files": [str(tmp_path / "x.pdf")]},
    )
    research_tools.download_paper(
        _VALID_DOI,
        workspace=_isolated_workspace(tmp_path),
        env_overrides={"TOR_PROXY": "socks5h://127.0.0.1:9050"},
    )
    assert captured["env"]["TOR_PROXY"] == "socks5h://127.0.0.1:9050"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "PATH" in captured["env"]  # inherited env preserved


def test_download_papers_rotates_circuit_every_n_downloads(tmp_path, monkeypatch):
    """rotate_circuit fires once per rotate_every successful downloads."""
    monkeypatch.setattr(
        research_tools,
        "download_paper",
        lambda identifier, **kwargs: {"files": [str(tmp_path / (identifier[-4:] + ".pdf"))]},
    )
    rotations: list[bool] = []

    def _rotate():
        rotations.append(True)
        return True

    dois = [f"10.1038/s41586-024-{i:05d}-0" for i in range(6)]
    progress: list[dict] = []
    research_tools.download_papers(
        dois,
        workspace=_isolated_workspace(tmp_path),
        rotate_circuit=_rotate,
        rotate_every=2,
        on_progress=progress.append,
    )
    # 6 downloads, rotate_every=2 → rotation before items 3 and 5 (after 2 and 4 succeed).
    assert len(rotations) == 2
    assert progress[-1].get("tor_rotations") == 2
    assert progress[-1].get("tor_last_rotation_ok") is True


def test_download_papers_no_rotation_when_rotate_every_zero(tmp_path, monkeypatch):
    """rotate_every=0 disables rotation entirely even if a callable is given."""
    monkeypatch.setattr(
        research_tools,
        "download_paper",
        lambda identifier, **kwargs: {"files": [str(tmp_path / "x.pdf")]},
    )
    rotations: list[bool] = []
    research_tools.download_papers(
        [_VALID_DOI, "10.1038/s41586-024-00002-9"],
        workspace=_isolated_workspace(tmp_path),
        rotate_circuit=lambda: rotations.append(True) or True,
        rotate_every=0,
    )
    assert rotations == []


def test_search_papers_for_download_uses_resolved_author_works_before_the_download_cli(monkeypatch):
    cli_called = False

    def unexpected_cli(*_args, **_kwargs):
        nonlocal cli_called
        cli_called = True
        raise AssertionError("author-only search should use the resolved OpenAlex author ID")

    def resolved_author(author, **kwargs):
        assert author == "Peter B. Reich"
        assert kwargs["sort"] == "cited_by_count"
        # The author path requests a wider candidate pool than the requested
        # download count so OA-first selection can fill the delivery list.
        assert kwargs["limit"] == 50
        return {
            "author_resolution": {"author_id": "A5044264078", "display_name": author, "works_count": 1290},
            "items": [{"title": "The worldwide leaf economics spectrum", "doi": "10.1038/nature02403", "authors": [author], "year": 2004, "cited_by_count": 8905, "is_oa": True, "source": "openalex"}],
        }

    monkeypatch.setattr(research_tools, "search_openalex_author_works", resolved_author)
    monkeypatch.setattr(research_tools.subprocess, "run", unexpected_cli)
    result = research_tools.search_papers_for_download(
        author="Peter B. Reich",
        limit=20,
        sort="cited_by_count",
    )

    assert cli_called is False
    assert result["identifiers"] == ["10.1038/nature02403"]
    assert result["items"][0]["cited_by_count"] == 8905
    assert result["author_resolution"]["author_id"] == "A5044264078"


def test_search_papers_for_download_extracts_author_and_citation_limit_from_query(monkeypatch):
    captured: dict[str, object] = {}

    def resolved_author(author, **kwargs):
        captured["author"] = author
        captured.update(kwargs)
        return {
            "author_resolution": {"author_id": "A123", "display_name": author},
            "items": [
                {
                    "title": "Carbon flux study",
                    "doi": "10.1038/nature02403",
                    "authors": [author],
                    "year": 2004,
                    "citation_count": 8905,
                    "is_oa": True,
                    "source": "openalex",
                }
            ],
        }

    monkeypatch.setattr(research_tools, "search_openalex_author_works", resolved_author)
    monkeypatch.setattr(
        research_tools.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("structured author search should skip the CLI")),
    )

    result = research_tools.search_papers_for_download(
        "帮我下载中科院于贵瑞院士的TOP10被引论文",
        limit=12,
        sort="relevance",
    )

    assert captured["author"] == "于贵瑞"
    assert captured["sort"] == "cited_by_count"
    assert captured["limit"] == 40
    assert result["author"] == "于贵瑞"
    assert result["sort"] == "cited_by_count"
    assert result["items"][0]["cited_by_count"] == 8905


def test_search_papers_for_download_retries_cli_encoding_failure(monkeypatch):
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")

    def fake_run(command, **kwargs):
        environments.append(kwargs["env"])
        if len(environments) == 1:
            return research_tools.subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="UnicodeEncodeError: 'gbk' codec can't encode character '\\u0131'",
            )
        return research_tools.subprocess.CompletedProcess(
            command,
            0,
            stdout='{"results":[{"title":"Soil ı study","doi":"10.1038/nature02403"}]}',
            stderr="",
        )

    monkeypatch.setattr(research_tools.subprocess, "run", fake_run)

    result = research_tools.search_papers_for_download("soil")

    assert result["items"][0]["title"] == "Soil ı study"
    assert [environment["PYTHONIOENCODING"] for environment in environments] == [
        "utf-8",
        "utf-8:backslashreplace",
    ]


def test_search_papers_for_download_skips_json_progress_preamble(monkeypatch):
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")

    def fake_run(command, **_kwargs):
        return research_tools.subprocess.CompletedProcess(
            command,
            0,
            stdout='{"event":"author_resolved","author":"Peter B. Reich"}\n{"results":[{"title":"Leaf economics","doi":"10.1038/nature02403","authors":["Peter B. Reich"]}]}',
            stderr="",
        )

    monkeypatch.setattr(research_tools.subprocess, "run", fake_run)
    result = research_tools.search_papers_for_download("leaf economics", author="Peter B. Reich")

    assert result["total"] == 1
    assert result["items"][0]["title"] == "Leaf economics"


def test_search_papers_for_download_falls_back_to_public_sources_when_cli_output_is_unreadable(monkeypatch):
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(
        research_tools.subprocess,
        "run",
        lambda command, **_kwargs: research_tools.subprocess.CompletedProcess(command, 0, stdout="progress: 100%", stderr=""),
    )
    monkeypatch.setattr(
        research_tools,
        "search_academic_papers",
        lambda *_args, **_kwargs: {
            "items": [{"title": "Leaf economics spectrum", "doi": "10.1038/nature02403", "source": "openalex"}],
            "provider_errors": {},
        },
    )

    result = research_tools.search_papers_for_download("leaf economics")

    assert result["source"] == "public_academic_fallback"
    assert result["identifiers"] == ["10.1038/nature02403"]
    assert result["fallback_reason"] == "download_cli_unavailable_or_unusable"


@pytest.mark.parametrize("has_relevant_fallback", [True, False])
def test_search_download_rejects_unrelated_cli_results(monkeypatch, has_relevant_fallback):
    title = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    unrelated = {"title": "Clinical assay of alcohol solution in newborn intensive care units", "doi": "10.1067/mic.2001.115582"}
    matching = {"title": title, "arxiv_id": "2005.11401", "source": "arxiv"}
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(
        research_tools.subprocess, "run",
        lambda command, **_kwargs: research_tools.subprocess.CompletedProcess(
            command, 0, stdout=json.dumps({"results": [unrelated]}), stderr="",
        ),
    )
    monkeypatch.setattr(
        research_tools, "search_academic_papers",
        lambda *_args, **_kwargs: {"items": [matching if has_relevant_fallback else unrelated]},
    )

    result = research_tools.search_papers_for_download(title, limit=1)

    assert result["identifiers"] == (["2005.11401"] if has_relevant_fallback else [])
    if not has_relevant_fallback:
        assert result["quality_gate"]["status"] == "insufficient"


def test_search_download_uses_planned_english_query_for_chinese_topic(monkeypatch):
    query = "山地土壤碳组分"
    english_query = "mountain soil carbon fractions"
    command_seen = []
    relevant = {
        "title": "Mountain soil carbon fractions across alpine ecosystems",
        "doi": "10.1000/mountain-carbon",
        "abstract": "We measured carbon fractions in mountain soils.",
    }
    partial_match = {
        "title": "Mountain soils of the Alps",
        "doi": "10.1000/mountain-soils",
        "abstract": "A regional survey of mountain soils.",
    }
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")

    def fake_run(command, **_kwargs):
        command_seen.append(command)
        return research_tools.subprocess.CompletedProcess(
            command, 0, stdout=json.dumps({"results": [relevant, partial_match]}), stderr="",
        )

    monkeypatch.setattr(research_tools.subprocess, "run", fake_run)

    result = research_tools.search_papers_for_download(
        query,
        search_plan={
            "query_variants": [query, english_query],
            "required_terms": ["mountain soils", "soil carbon fractions", english_query],
            "search_query": english_query,
        },
    )

    assert command_seen[0][2] == english_query
    assert result["query"] == query
    assert result["search_query"] == english_query
    assert result["identifiers"] == ["10.1000/mountain-carbon"]
    assert result["quality_gate"]["status"] == "passed"
    assert result["quality_gate"]["rejected_count"] == 1
    assert result["search_intent"]["required_terms"] == [english_query]


def test_deterministic_cross_language_download_prefers_bounded_public_search(monkeypatch):
    query = "山地土壤碳组分"
    english_queries = [
        "soil organic carbon fractions mountain soils",
        "alpine soil organic carbon fractions",
        "soil organic carbon fractions alpine meadow",
    ]
    observed = {}

    class FakeFederation:
        def __init__(self, *, providers):
            observed["provider_timeouts"] = [provider["timeout"] for provider in providers]
            observed["providers"] = [provider["name"] for provider in providers]

        def search(self, search_query, **kwargs):
            observed["search_query"] = search_query
            observed.update(kwargs)
            return {
                "items": [{
                    "title": "Labile and stable soil organic carbon fractions in surface horizons of mountain soils",
                    "doi": "10.1000/mountain-soil-carbon",
                    "authors": ["A. Researcher"],
                    "year": 2018,
                    "source": "openalex",
                }],
                "provider_errors": {},
            }

    monkeypatch.setattr(research_tools, "FederatedAcademicSearch", FakeFederation)
    monkeypatch.setattr(
        research_tools,
        "build_academic_provider",
        lambda name, *, timeout: {"name": name, "timeout": timeout},
    )
    monkeypatch.setattr(
        research_tools.shutil,
        "which",
        lambda _name: (_ for _ in ()).throw(AssertionError("bounded public search should run before the CLI")),
    )

    result = research_tools.search_papers_for_download(
        query,
        search_plan={
            "planner": "deterministic",
            "query_variants": english_queries,
            "search_query": english_queries[0],
        },
    )

    assert result["source"] == "public_academic_fallback"
    assert result["identifiers"] == ["10.1000/mountain-soil-carbon"]
    assert result["query"] == query
    assert result["search_query"] == english_queries[0]
    assert observed["query_variants"] == english_queries
    assert observed["required_terms"] == english_queries
    assert observed["provider_timeouts"] == [8.0, 8.0, 8.0]
    assert observed["providers"] == ["openalex", "crossref", "arxiv"]


def test_download_relevance_filter_runs_before_the_requested_limit():
    result = research_tools._download_search_result(
        [{"title": "Clinical intensive care", "doi": "10.1067/mic.2001.115582"},
         {"title": "Retrieval augmented generation", "arxiv_id": "2005.11401"}],
        query="retrieval augmented generation", search_intent={"required_terms": ["retrieval augmented generation"]},
        author="", sort="relevance", limit=1, source="test",
    )
    assert result["identifiers"] == ["2005.11401"]
    assert result["quality_gate"]["rejected_count"] == 1


def test_search_papers_for_download_turns_cli_timeout_into_retryable_failure(monkeypatch):
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")

    def fake_run(*_args, **_kwargs):
        raise research_tools.subprocess.TimeoutExpired("scansci-pdf", 60)

    monkeypatch.setattr(research_tools.subprocess, "run", fake_run)
    monkeypatch.setattr(research_tools, "_public_download_search_fallback", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="timed out after 60s"):
        research_tools.search_papers_for_download("evidence-grounded RAG")


def test_title_download_recalls_more_than_delivery_count_and_skips_metadata_only(monkeypatch):
    title = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")

    def cli(command, **kwargs):
        assert int(command[command.index("--limit") + 1]) >= 6
        return research_tools.subprocess.CompletedProcess(command, 0, stdout=json.dumps({"results": [
            {"title": title, "url": "https://discovery.ucl.ac.uk/id/eprint/10100504/"},
            {"title": title, "arxiv_id": "2005.11401"},
        ]}), stderr="")

    monkeypatch.setattr(research_tools.subprocess, "run", cli)
    result = research_tools.search_papers_for_download(title, limit=1)
    assert result["identifiers"] == ["2005.11401"]
    assert len(result["items"]) == 1


@pytest.mark.parametrize("cli_code", [0, 124])
def test_title_download_public_fallback_keeps_recall_budget(monkeypatch, cli_code):
    title = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(research_tools.subprocess, "run", lambda command, **kwargs:
        research_tools.subprocess.CompletedProcess(command, cli_code, stdout='{"results": []}', stderr="timeout"))

    def public(query, **kwargs):
        assert kwargs["per_source"] >= 6
        assert kwargs["limit"] > 1
        return {"items": [{"title": title}, {"title": title, "arxiv_id": "2005.11401v4"}]}

    monkeypatch.setattr(research_tools, "search_academic_papers", public)
    result = research_tools.search_papers_for_download(title, limit=1)
    assert result["identifiers"] == ["2005.11401v4"]


@pytest.mark.parametrize("record,expected", [
    ({"doi": "https://doi.org/10.1000/example"}, "10.1000/example"),
    ({"arxiv_id": "https://arxiv.org/abs/2005.11401v4"}, "2005.11401v4"),
    ({"url": "https://arxiv.org/pdf/2005.11401v4.pdf?download=1"}, "2005.11401v4"),
    ({"doi": "10.1000/example", "arxiv_id": "2005.11401"}, "2005.11401"),
])
def test_download_search_normalizes_identifiers(record, expected):
    result = research_tools._download_search_result(
        [{"title": "RAG", **record}], query="RAG", search_intent={}, author="",
        sort="relevance", limit=1, source="test",
    )
    assert result["identifiers"] == [expected]


def test_download_papers_pause_returns_while_request_is_blocked_and_latches_stop(tmp_path, monkeypatch):
    entered, release, worker_finished = threading.Event(), threading.Event(), threading.Event()
    stop = threading.Event()
    progress = []

    def blocked(identifier, *, _output_dir, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            path = Path(_output_dir) / "late.pdf"
            path.write_bytes(b"%PDF-1.4 late")
            return {"files": [str(path)]}
        finally:
            worker_finished.set()

    monkeypatch.setattr(research_tools, "download_paper", blocked)
    workspace = _isolated_workspace(tmp_path)
    outcome = []

    def run():
        try:
            research_tools.download_papers([_VALID_DOI], workspace=workspace,
                cancel_check=stop.is_set, on_progress=progress.append)
        except research_tools._BatchCancelled:
            outcome.append("paused")

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        stop.set()
        thread.join(0.75)
        assert not thread.is_alive()
        assert time.monotonic() - started < 0.75
        assert outcome == ["paused"]
        assert progress[-1]["items"][0]["status"] == "cancelled"
        stop.clear()  # A resumed Host must not re-enable this invocation.
    finally:
        release.set()
        thread.join(2)
        assert worker_finished.wait(2)
    # Wait for this worker's staging cleanup, without a network-sized sleep.
    deadline = time.monotonic() + 2
    downloads = research_tools._download_directory(workspace)
    while list(downloads.glob(".batch-*")) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not list(downloads.glob("*.pdf"))
    assert not list(downloads.glob(".batch-*"))


def test_download_cli_interrupts_owned_process():
    started = time.monotonic()
    with pytest.raises(research_tools._BatchCancelled):
        research_tools._run_scansci_pdf(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=30,
            cancel_check=lambda: time.monotonic() - started > 0.15,
        )
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("stop", ["pause", "timeout"])
def test_download_cli_cleans_children_after_launcher_exits(tmp_path, monkeypatch, stop):
    marker = tmp_path / "child-pid.txt"
    child_code = f"import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"
    launcher_code = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child_code!r}])"
    processes = []
    original_popen = research_tools.subprocess.Popen

    def spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(research_tools.subprocess, "Popen", spawn)
    parent_exited = []

    def cancelled():
        if processes and processes[0].poll() is not None and marker.exists():
            parent_exited.append(True)
            return stop == "pause"
        return False

    started = time.monotonic()
    if stop == "pause":
        with pytest.raises(research_tools._BatchCancelled):
            research_tools._run_scansci_pdf([sys.executable, "-c", launcher_code], timeout=2, cancel_check=cancelled)
    else:
        result = research_tools._run_scansci_pdf([sys.executable, "-c", launcher_code], timeout=0.8, cancel_check=cancelled)
        assert result.returncode == 124
    assert parent_exited
    assert time.monotonic() - started < 2
    # communicate() only finishes quickly if the child's inherited pipes close.


def test_download_cli_success_with_owned_process_tree():
    result = research_tools._run_scansci_pdf(
        [sys.executable, "-c", "print('download completed')"], timeout=2, cancel_check=lambda: False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "download completed"


@pytest.mark.parametrize("via_fallback", [False, True])
def test_download_search_preserves_author_and_year_constraints_before_limit(monkeypatch, via_fallback):
    records = [
        {"title": "Leaf economics", "doi": "10.1000/wrong-author", "authors": ["Different Person"], "year": 2020},
        {"title": "Leaf economics", "doi": "10.1000/old", "authors": ["Peter B. Reich"], "year": 2019},
        {"title": "Leaf economics", "doi": "10.1000/new", "authors": ["Peter B. Reich"], "year": 2022},
        {"title": "Leaf economics", "doi": "10.1000/unknown-year", "authors": ["Peter B. Reich"]},
        {"title": "Leaf economics", "doi": "10.1000/match", "authors": ["Peter B. Reich"], "year": 2021},
    ]
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: None if via_fallback else "scansci-pdf")
    monkeypatch.setattr(research_tools.subprocess, "run", lambda command, **kwargs:
        research_tools.subprocess.CompletedProcess(command, 0, stdout=json.dumps({"results": records}), stderr=""))
    monkeypatch.setattr(research_tools, "search_academic_papers", lambda *args, **kwargs: {"items": records})
    result = research_tools.search_papers_for_download(
        "leaf economics", author="Peter B. Reich", year_from=2020, year_to=2021, limit=1,
    )
    assert result["identifiers"] == ["10.1000/match"]
    assert result["query"] == "leaf economics"


def test_cancelled_pdf_read_never_writes_a_late_file(tmp_path, monkeypatch):
    stop = threading.Event()

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            stop.set()
            return b"%PDF-1.4 late"

    monkeypatch.setattr(research_tools.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(research_tools._BatchCancelled):
        research_tools._download_public_pdf(
            {"url": "https://example.invalid/paper.pdf", "source": "fixture"},
            output_dir=tmp_path, identifier=_VALID_DOI, timeout=30, cancel_check=stop.is_set,
        )
    assert not list(tmp_path.iterdir())


def test_download_search_result_drops_non_article_records():
    rows = [
        {
            "title": "Appendix 1—table 1. Empirical homing efficiencies",
            "doi": "10.7554/elife.33423.016",
            "source": "crossref",
        },
        {
            "title": 'Review for "CRISPR/Cas9 gene drive technology"',
            "doi": "10.1111/pim.12762/v1/review2",
            "source": "crossref",
        },
        {
            "title": "Safe Driving: CRISPR and the Gene Drive Landscape",
            "doi": "10.1089/crispr.2018.29001.tno",
            "source": "crossref",
        },
        {
            "title": "Evolutionary safety of a CRISPR-Cas9 gene-drive element",
            "doi": "10.1126/science.abj8755",
            "is_oa": False,
            "source": "crossref",
        },
        {
            "title": "CRISPR gene drive safety assessment",
            "arxiv_id": "1706.03762",
            "is_oa": True,
            "source": "arxiv",
        },
    ]

    result = research_tools._download_search_result(
        rows,
        query="CRISPR gene drive",
        search_intent={"required_terms": ["CRISPR gene drive"]},
        author="",
        sort="relevance",
        limit=5,
        source="test",
    )

    assert result["identifiers"] == ["10.1126/science.abj8755", "1706.03762"]
    assert result["non_article_dropped"]["count"] == 3
    assert result["quality_gate"]["status"] == "passed"


def test_download_search_result_oa_preferred_keeps_citation_order_inside_groups():
    rows = [
        {
            "title": "Paywalled classic on gene drives",
            "doi": "10.1038/nature02403",
            "cited_by_count": 9000,
            "is_oa": False,
            "authors": ["Peter B. Reich"],
        },
        {
            "title": "Open forest ecology synthesis",
            "doi": "10.1371/journal.pone.0201345",
            "cited_by_count": 1200,
            "is_oa": True,
            "authors": ["Peter B. Reich"],
        },
        {
            "title": "Paywalled follow-up",
            "doi": "10.1111/1365-2745.12211",
            "cited_by_count": 4000,
            "is_oa": False,
            "authors": ["Peter B. Reich"],
        },
    ]

    result = research_tools._download_search_result(
        rows,
        query="",
        search_intent={},
        author="Peter B. Reich",
        sort="cited_by_count",
        limit=2,
        source="test",
        author_verified=True,
        oa_preferred=True,
    )

    assert result["identifiers"] == ["10.1371/journal.pone.0201345", "10.1038/nature02403"]


def test_download_paper_uses_short_cli_timeout_for_closed_access_hint(tmp_path, monkeypatch):
    observed: dict[str, float] = {}

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "no document"

    def fake_run(command, *, timeout, **_kwargs):
        observed["cli"] = timeout
        return Completed()

    def fake_fetch(_executable, _identifier, *, timeout, **_kwargs):
        observed["institutional"] = timeout
        return None

    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(research_tools, "_download_from_public_archives", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("未找到可直接获取的开放全文或灰色文献存档版本。")))
    monkeypatch.setattr(research_tools, "_run_scansci_pdf", fake_run)
    monkeypatch.setattr(research_tools, "_run_scansci_pdf_fetch", fake_fetch)

    with pytest.raises(RuntimeError):
        research_tools.download_paper(
            "10.1038/nature02403",
            workspace=tmp_path,
            metadata_is_oa=False,
        )

    assert observed["cli"] == 45.0
    assert observed["institutional"] == 45.0


def test_download_paper_keeps_full_cli_timeout_without_closed_access_hint(tmp_path, monkeypatch):
    observed: dict[str, float] = {}

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "no document"

    def fake_run(command, *, timeout, **_kwargs):
        observed["cli"] = timeout
        return Completed()

    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(research_tools, "_download_from_public_archives", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("未找到可直接获取的开放全文或灰色文献存档版本。")))
    monkeypatch.setattr(research_tools, "_run_scansci_pdf", fake_run)
    monkeypatch.setattr(research_tools, "_run_scansci_pdf_fetch", lambda *a, **k: None)

    with pytest.raises(RuntimeError):
        research_tools.download_paper(
            "10.1038/nature02403",
            workspace=tmp_path,
        )

    assert observed["cli"] == 180.0


def test_download_papers_forwards_oa_hints_to_single_item_downloads(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_download_paper(identifier, *, metadata_is_oa=None, **_kwargs):
        captured[identifier] = metadata_is_oa
        raise RuntimeError("no open copy")

    monkeypatch.setattr(research_tools, "download_paper", fake_download_paper)

    result = research_tools.download_papers(
        ["10.1038/nature02403", "1706.03762"],
        workspace=tmp_path,
        oa_hints={"10.1038/nature02403": False, "1706.03762": True},
    )

    assert captured["10.1038/nature02403"] is False
    assert captured["1706.03762"] is True
    by_identifier = {item["identifier"]: item["status"] for item in result["items"]}
    assert by_identifier["10.1038/nature02403"] == "failed"
    assert by_identifier["1706.03762"] == "failed"
