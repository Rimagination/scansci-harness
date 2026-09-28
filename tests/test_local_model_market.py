import json
import os
from pathlib import Path

import pytest

from scansci_html import local_model_market
from scansci_html import app_settings
from scansci_html import research_agent
from scansci_html import research_tools


def test_model_root_uses_per_user_local_app_data(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SCANSCI_MODEL_ROOT", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert local_model_market.model_root() == tmp_path / "ScanSci" / "models"
    assert local_model_market.hub_cache_root() == tmp_path / "ScanSci" / "models" / "HuggingFace" / "hub"


def test_hub_cache_root_uses_configured_huggingface_cache(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SCANSCI_MODEL_ROOT", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "huggingface"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)

    assert local_model_market.hub_cache_root() == tmp_path / "huggingface" / "hub"


def test_discover_model_roots_includes_configured_huggingface_hub_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "selected-hub"
    cache.mkdir()
    monkeypatch.delenv("SCANSCI_MODEL_ROOT", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))

    assert cache.resolve() in local_model_market.discover_model_roots()


def test_installed_models_reads_huggingface_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    snapshot = tmp_path / "HuggingFace" / "hub" / "models--Qwen--Qwen2.5-1.5B-Instruct" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text(
        json.dumps({"model_type": "qwen2", "architectures": ["Qwen2ForCausalLM"]}), encoding="utf-8"
    )
    (snapshot / "model.safetensors").write_bytes(b"weights")

    rows = local_model_market.installed_models()

    assert rows == [
        {
            "id": "Qwen/Qwen2.5-1.5B-Instruct",
            "name": "Qwen/Qwen2.5-1.5B-Instruct",
            "path": str(snapshot),
            "source_root": str(tmp_path),
            "size_bytes": (snapshot / "config.json").stat().st_size + len(b"weights"),
            "ready": True,
            "kind": "chat",
            "capabilities": ["chat"],
            "architecture": "Qwen2ForCausalLM",
            "model_type": "qwen2",
            "format": "transformers",
            "icon_url": "https://resources.modelscope.cn/avatar/4c40e6ce-1348-43b5-a3bc-6bafec6ac805.jpg",
        }
    ]


def test_snapshot_prefers_complete_revision_over_newer_incomplete_revision(tmp_path: Path):
    model_folder = tmp_path / "models--openbmb--MiniCPM-V-4.6-BNB"
    complete = model_folder / "snapshots" / "complete"
    incomplete = model_folder / "snapshots" / "incomplete"
    complete.mkdir(parents=True)
    incomplete.mkdir(parents=True)
    (complete / "model.safetensors").write_bytes(b"weights")
    os.utime(complete, (1, 1))
    os.utime(incomplete, (2, 2))

    assert local_model_market._snapshot(model_folder) == complete


def test_delete_installed_model_removes_huggingface_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    cache = tmp_path / "HuggingFace" / "hub" / "models--Qwen--Qwen2.5-1.5B-Instruct"
    snapshot = cache / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text(
        json.dumps({"model_type": "qwen2", "architectures": ["Qwen2ForCausalLM"]}), encoding="utf-8"
    )
    (snapshot / "model.safetensors").write_bytes(b"weights")

    result = local_model_market.delete_installed_model("Qwen/Qwen2.5-1.5B-Instruct")

    assert result == {
        "deleted": True,
        "id": "Qwen/Qwen2.5-1.5B-Instruct",
        "path": str(cache),
    }
    assert not cache.exists()
    assert local_model_market.installed_models() == []


def test_delete_installed_model_removes_manually_copied_folder(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    folder = tmp_path / "Qwen3-Embedding-0.6B"
    folder.mkdir()
    (folder / "config.json").write_text(
        json.dumps({"_name_or_path": "Qwen/Qwen3-Embedding-0.6B", "model_type": "qwen3"}), encoding="utf-8"
    )
    (folder / "model.safetensors").write_bytes(b"weights")

    result = local_model_market.delete_installed_model("Qwen/Qwen3-Embedding-0.6B")

    assert result["deleted"] is True
    assert result["id"] == "Qwen/Qwen3-Embedding-0.6B"
    assert result["path"] == str(folder)
    assert not folder.exists()


def test_delete_installed_model_rejects_unknown_or_unsafe_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))

    with pytest.raises(ValueError):
        local_model_market.delete_installed_model("../outside")
    with pytest.raises(FileNotFoundError):
        local_model_market.delete_installed_model("Qwen/missing")


def test_installed_models_reuses_a_manually_copied_model_folder(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    folder = tmp_path / "Qwen3-Embedding-0.6B"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(
        json.dumps(
            {
                "_name_or_path": "Qwen/Qwen3-Embedding-0.6B",
                "model_type": "qwen3",
                "architectures": ["Qwen3Model"],
            }
        ),
        encoding="utf-8",
    )
    (folder / "model.safetensors").write_bytes(b"weights")

    rows = local_model_market.installed_models()

    assert len(rows) == 1
    assert rows[0]["id"] == "Qwen/Qwen3-Embedding-0.6B"
    assert rows[0]["path"] == str(folder)
    assert rows[0]["ready"] is True
    assert rows[0]["kind"] == "embedding"


def test_installed_models_does_not_rediscover_huggingface_snapshot_hash(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    snapshot = tmp_path / "HuggingFace" / "hub" / "models--Qwen--Qwen2.5-1.5B-Instruct" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text(
        json.dumps({"model_type": "qwen2", "architectures": ["Qwen2ForCausalLM"]}), encoding="utf-8"
    )
    (snapshot / "model.safetensors").write_bytes(b"weights")

    rows = local_model_market.installed_models()

    assert [row["id"] for row in rows] == ["Qwen/Qwen2.5-1.5B-Instruct"]


def test_install_manager_treats_downloaded_qwen35_files_as_ready_for_runtime_probe(monkeypatch):
    model_id = "Qwen/Qwen3.5-2B"
    monkeypatch.setattr(
        local_model_market,
        "installed_models",
        lambda: [
            {
                "id": model_id,
                "ready": False,
                "model_files_present": True,
                "runtime_probe_state": "pending",
                "format": "transformers",
                "kind": "vision",
            }
        ],
    )

    manager = local_model_market.create_install_manager()

    assert manager.ready_checker(model_id) is True


def test_market_catalog_marks_cached_model_without_network(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    snapshot = tmp_path / "HuggingFace" / "hub" / "models--Qwen--Qwen2.5-1.5B-Instruct" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_bytes(b"weights")

    catalog = local_model_market.market_catalog()
    row = next(item for item in catalog["items"] if item["id"] == "Qwen/Qwen2.5-1.5B-Instruct")

    assert catalog["source"] == "curated"
    assert row["installed"] is True
    assert row["ready"] is True


def test_market_catalog_does_not_probe_huggingface_for_default_view(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))

    def unexpected_remote_probe(_query, _limit):
        raise AssertionError("the curated default view must not probe Hugging Face")

    monkeypatch.setattr(local_model_market, "_remote_catalog", unexpected_remote_probe)

    catalog = local_model_market.market_catalog()

    assert catalog["source"] == "curated"
    assert any(item["id"] == "openbmb/MiniCPM-V-4.6-BNB" for item in catalog["items"])


def test_market_catalog_keeps_native_asr_visible_when_searching(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(local_model_market, "_remote_catalog", lambda _query, _limit: [])

    catalog = local_model_market.market_catalog("ASR")

    assert any(item["id"] == local_model_market.QWEN3_ASR_NATIVE_MODEL_ID for item in catalog["items"])


def test_minicpm_vision_models_are_curated_for_local_download(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(local_model_market, "_remote_catalog", lambda _query, _limit: [])

    catalog = local_model_market.market_catalog("MiniCPM-V")
    rows = {item["id"]: item for item in catalog["items"]}

    assert rows["openbmb/MiniCPM-V-4.6-BNB"]["kind"] == "vision"
    assert rows["openbmb/MiniCPM-V-4.6-BNB"]["runtime"] == "local-huggingface"
    assert rows["openbmb/MiniCPM-V-4.6-GPTQ"]["kind"] == "vision"


def test_installed_minicpm_snapshot_is_vision(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    snapshot = tmp_path / "HuggingFace" / "hub" / "models--openbmb--MiniCPM-V-4.6-BNB" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text(
        json.dumps(
            {
                "model_type": "minicpmv4_6",
                "architectures": ["MiniCPMV4_6ForConditionalGeneration"],
                "vision_config": {"model_type": "siglip"},
            }
        ),
        encoding="utf-8",
    )
    (snapshot / "model.safetensors").write_bytes(b"weights")

    row = next(item for item in local_model_market.installed_models() if item["id"] == "openbmb/MiniCPM-V-4.6-BNB")

    assert row["ready"] is True
    assert row["kind"] == "vision"
    assert row["capabilities"] == ["chat", "vision"]
    assert row["architecture"] == "MiniCPMV4_6ForConditionalGeneration"


def test_installed_tts_snapshot_is_audio_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    snapshot = tmp_path / "Qwen3-TTS-12Hz-0.6B-Base"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen3_tts",
                "architectures": ["Qwen3TTSForConditionalGeneration"],
                "audio_config": {"sample_rate": 24000},
            }
        ),
        encoding="utf-8",
    )
    (snapshot / "model.safetensors").write_bytes(b"weights")

    row = local_model_market.installed_models()[0]

    assert row["kind"] == "audio"
    assert row["capabilities"] == ["audio"]


def test_installed_model_capabilities_are_not_collapsed_to_primary_kind(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SCANSCI_MODEL_ROOT", str(tmp_path))
    snapshot = tmp_path / "Qwen-multimodal"
    snapshot.mkdir()
    (snapshot / "config.json").write_text(
        json.dumps(
            {
                "model_type": "fixture",
                "architectures": ["FixtureForConditionalGeneration"],
                "vision_config": {"model_type": "fixture-vision"},
            }
        ),
        encoding="utf-8",
    )
    (snapshot / "model.safetensors").write_bytes(b"weights")

    row = local_model_market.installed_models()[0]

    assert row["kind"] == "vision"
    assert row["capabilities"] == ["chat", "vision"]


def test_installed_vision_snapshot_is_exposed_with_vision_capability(monkeypatch):
    monkeypatch.setattr(
        app_settings,
        "installed_models",
        lambda: [
            {
                "id": "openbmb/MiniCPM-V-4.6-BNB",
                "name": "MiniCPM-V 4.6（BNB 4-bit）",
                "ready": True,
                "format": "transformers",
                "architecture": "MiniCPMV4_6ForConditionalGeneration",
                "model_type": "minicpmv4_6",
                "kind": "vision",
            }
        ],
    )

    settings = app_settings._normalize_settings({})
    provider = next(item for item in settings["providers"] if item["id"] == "local-huggingface")

    assert provider["models"][0]["capabilities"] == ["reasoning", "coding", "vision"]


def test_cached_local_model_stays_configured_while_its_drive_is_offline(monkeypatch, tmp_path: Path):
    source_root = str(tmp_path / "detached-model-drive")
    model_id = "openbmb/MiniCPM-V-4.6-BNB"
    monkeypatch.setattr(app_settings, "installed_models", lambda: [])
    monkeypatch.setattr(app_settings, "missing_configured_model_roots", lambda: [source_root])

    settings = app_settings._normalize_settings(
        {
            "active_model": {"provider_id": "local-huggingface", "model_id": model_id},
            "providers": [
                {
                    "id": "local-huggingface",
                    "name": "本地 Hugging Face",
                    "kind": "local",
                    "enabled": True,
                    "models": [
                        {
                            "id": model_id,
                            "name": "MiniCPM-V 4.6",
                            "capabilities": ["reasoning", "vision"],
                            "source_root": source_root,
                        }
                    ],
                }
            ],
        }
    )

    provider = next(item for item in settings["providers"] if item["id"] == "local-huggingface")
    model = provider["models"][0]
    assert settings["active_model"] == {"provider_id": "local-huggingface", "model_id": model_id}
    assert provider["missing_roots"] == [source_root]
    assert model["available"] is False
    assert model["unavailable_reason"] == "模型目录当前不可访问"


def test_tts_snapshot_is_not_exposed_as_a_chat_model(monkeypatch):
    monkeypatch.setattr(
        app_settings,
        "installed_models",
        lambda: [
            {
                "id": "Qwen3-TTS-12Hz-0.6B-Base",
                "name": "Qwen3-TTS-12Hz-0.6B-Base",
                "ready": True,
                "format": "transformers",
                "architecture": "Qwen3TTSForConditionalGeneration",
                "model_type": "qwen3_tts",
                "kind": "audio",
                "capabilities": ["audio"],
            }
        ],
    )

    settings = app_settings._normalize_settings({})

    assert not any(item["id"] == "local-huggingface" for item in settings["providers"])
    assert app_settings._normalize_capabilities(["reasoning"], fallback="Qwen3-TTS-12Hz-0.6B-Base") == ["audio"]


def test_complete_chat_snapshot_is_exposed_as_local_provider(monkeypatch):
    monkeypatch.setattr(
        app_settings,
        "installed_models",
        lambda: [
            {
                "id": "Qwen/Qwen2.5-1.5B-Instruct",
                "name": "Qwen2.5 1.5B Instruct",
                "ready": True,
                "format": "transformers",
                "architecture": "Qwen2ForCausalLM",
                "model_type": "qwen2",
            },
            {
                "id": "Qwen/Qwen3-Embedding-0.6B",
                "name": "Qwen3 Embedding 0.6B",
                "ready": True,
                "format": "transformers",
                "architecture": "Qwen3Model",
                "model_type": "qwen3",
            },
        ],
    )

    settings = app_settings._normalize_settings({})
    provider = next(item for item in settings["providers"] if item["id"] == "local-huggingface")

    assert provider["kind"] == "local"
    assert provider["models"] == [
        {
            "id": "Qwen/Qwen2.5-1.5B-Instruct",
            "name": "Qwen2.5 1.5B Instruct",
            "group": "本地 Hugging Face",
            "context_window": "本机",
            "capabilities": ["reasoning", "coding"],
        }
    ]


def test_installed_chat_snapshot_with_failed_runtime_probe_stays_visible(monkeypatch):
    monkeypatch.setattr(
        app_settings,
        "installed_models",
        lambda: [
            {
                "id": "Qwen/Qwen3.5-4B",
                "name": "Qwen3.5 4B",
                "ready": False,
                "model_files_present": True,
                "runtime_compatible": False,
                "runtime_message": "GPU 显存不足，当前显存约 8.0 GB。",
                "format": "transformers",
                "architecture": "Qwen3_5ForConditionalGeneration",
                "model_type": "qwen3_5",
                "capabilities": ["chat", "vision"],
            }
        ],
    )
    monkeypatch.setattr(app_settings, "missing_configured_model_roots", lambda: [])

    settings = app_settings._normalize_settings({})
    provider = next(item for item in settings["providers"] if item["id"] == "local-huggingface")
    model = provider["models"][0]

    assert model["id"] == "Qwen/Qwen3.5-4B"
    assert model["available"] is False
    assert "显存不足" in model["unavailable_reason"]


def test_direct_chat_starts_loopback_runtime_for_huggingface_model(tmp_path: Path, monkeypatch):
    runtime = research_agent.ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(
        research_agent,
        "load_settings",
        lambda _workspace: {
            "active_model": {"provider_id": "local-huggingface", "model_id": "Qwen/Qwen2.5-1.5B-Instruct"},
            "providers": [
                {
                    "id": "local-huggingface",
                    "kind": "local",
                    "enabled": True,
                    "base_url": "http://stale/v1",
                    "models": [{"id": "Qwen/Qwen2.5-1.5B-Instruct", "capabilities": ["reasoning"]}],
                }
            ],
        },
    )
    monkeypatch.setattr(research_agent, "ensure_local_transformers_runtime", lambda model_id: f"http://127.0.0.1:17863/v1/{model_id}")

    request = runtime._direct_chat_request({"messages": [{"role": "user", "content": "hello"}]})

    assert request.provider_kind == "openai-compatible"
    assert request.api_key == "local"
    assert request.base_url == "http://127.0.0.1:17863/v1/Qwen/Qwen2.5-1.5B-Instruct"


def test_paper_download_workflow_can_start_without_notebook(tmp_path: Path, monkeypatch):
    runtime = research_agent.ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)

    run = runtime.start({"workflow_type": "paper_download", "identifier": "10.1000/example"})

    assert run["workflow_type"] == "paper_download"
    assert run["notebook_id"] == ""


def test_paper_download_falls_back_to_public_archives_without_cli(tmp_path: Path, monkeypatch):
    destination = tmp_path / "downloads" / "paper.pdf"
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(research_tools, "_public_fulltext_candidates", lambda _identifier, timeout, **kwargs: [{"url": "https://example.test/paper.pdf", "source": "Institutional repository"}])
    monkeypatch.setattr(research_tools, "_download_public_pdf", lambda _candidate, output_dir, identifier, timeout, **kwargs: destination)

    result = research_tools.download_paper("10.1000/example", workspace=tmp_path / "workspace.sqlite", strategy="gray_oa")

    assert result["source"] == "Institutional repository"
    assert result["files"] == [str(destination)]


def test_arxiv_download_uses_the_authoritative_public_archive_even_when_cli_exists(tmp_path: Path, monkeypatch):
    destination = tmp_path / "downloads" / "1706.03762.pdf"
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(
        research_tools.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("arXiv must not use the optional CLI")),
    )
    monkeypatch.setattr(
        research_tools,
        "_download_public_pdf",
        lambda _candidate, output_dir, identifier, timeout, **kwargs: destination,
    )

    result = research_tools.download_paper("1706.03762", workspace=tmp_path / "workspace.sqlite")

    assert result["source"] == "arXiv"
    assert result["files"] == [str(destination)]


def test_public_fulltext_candidates_prioritize_unpaywall_and_europe_pmc(monkeypatch):
    monkeypatch.setenv("SCANSCI_UNPAYWALL_EMAIL", "researcher@example.org")

    def fake_request(url, *, timeout):
        assert timeout == 12
        if "api.unpaywall.org" in url:
            return {
                "best_oa_location": {
                    "url_for_pdf": "https://repository.test/paper.pdf",
                    "host_type": "repository",
                },
                "oa_locations": [],
            }
        if "europepmc" in url:
            return {
                "resultList": {
                    "result": [
                        {
                            "pmcid": "PMC123",
                            "fullTextUrlList": {
                                "fullTextUrl": [
                                    {
                                        "documentStyle": "pdf",
                                        "availability": "Open access",
                                        "url": "https://europepmc.test/PMC123.pdf",
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        if "openalex.org" in url:
            return {}
        if "crossref.org" in url:
            return {"message": {}}
        raise AssertionError(url)

    monkeypatch.setattr(research_tools, "_request_json", fake_request)

    candidates = research_tools._public_fulltext_candidates("10.1000/example", timeout=12)

    assert candidates[:2] == [
        {"url": "https://repository.test/paper.pdf", "source": "Unpaywall repository"},
        {"url": "https://europepmc.test/PMC123.pdf", "source": "Europe PMC open access"},
    ]


def test_public_archive_race_keeps_one_pdf_and_records_source_health(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "downloads"
    candidates = [
        {"url": "https://broken.test/paper.pdf", "source": "Broken repository"},
        {"url": "https://working.test/paper.pdf", "source": "Working repository"},
    ]

    monkeypatch.setattr(
        research_tools,
        "_public_fulltext_candidates",
        lambda _identifier, timeout, **kwargs: candidates,
    )

    def fake_download(candidate, *, output_dir, identifier, timeout, **kwargs):
        if candidate["source"] == "Broken repository":
            raise RuntimeError("HTTP 404 Not Found")
        destination = Path(output_dir) / "staged.pdf"
        destination.write_bytes(b"%PDF-1.4" + b"x" * 128)
        return destination

    monkeypatch.setattr(research_tools, "_download_public_pdf", fake_download)

    result = research_tools._download_from_public_archives(
        "10.1000/example",
        workspace=tmp_path / "workspace.sqlite",
        strategy="oa_first",
        timeout=10,
        output_dir=output_dir,
    )

    assert result["source_race"] is True
    assert len(result["files"]) == 1
    assert Path(result["files"][0]).is_file()
    assert sum(item.get("selected", False) for item in result["attempts"]) == 1
    assert {item["source"] for item in result["attempts"]} == {
        "Broken repository",
        "Working repository",
    }
    scores = json.loads((output_dir / ".source_scores.json").read_text(encoding="utf-8"))
    assert scores["Broken repository"]["last_error"] == "not_found"
    assert scores["Working repository"]["attempts"] == 1
    assert list(output_dir.glob("*.pdf")) == [Path(result["files"][0])]
    assert not list(output_dir.glob(".source-race-*"))


def test_arxiv_doi_resolves_without_waiting_for_metadata_apis(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "_request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("metadata API should not run")),
    )

    candidates = research_tools._public_fulltext_candidates("10.48550/arXiv.1706.03762", timeout=12)

    assert candidates == [{"url": "https://arxiv.org/pdf/1706.03762.pdf", "source": "arXiv"}]


def test_cli_success_without_a_pdf_falls_back_to_public_archive(tmp_path: Path, monkeypatch):
    destination = tmp_path / "downloads" / "paper.pdf"
    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(
        research_tools.subprocess,
        "run",
        lambda *_args, **_kwargs: research_tools.subprocess.CompletedProcess(
            args=["scansci-pdf"], returncode=0, stdout="FAILED: login required", stderr=""
        ),
    )
    monkeypatch.setattr(
        research_tools,
        "_public_fulltext_candidates",
        lambda _identifier, timeout, **kwargs: [{"url": "https://example.test/paper.pdf", "source": "Open archive"}],
    )
    monkeypatch.setattr(
        research_tools,
        "_download_public_pdf",
        lambda _candidate, output_dir, identifier, timeout, **kwargs: destination,
    )

    result = research_tools.download_paper("10.1000/example", workspace=tmp_path / "workspace.sqlite")

    assert result["source"] == "Open archive"
    assert result["files"] == [str(destination)]


def test_download_paper_uses_structured_institutional_fetch_after_get(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace.sqlite"
    output_dir = research_tools._download_directory(workspace)
    output_dir.mkdir(parents=True, exist_ok=True)
    staged = output_dir / "institutional.pdf"
    staged.write_bytes(b"%PDF-1.4" + b"x" * 128)
    commands: list[list[str]] = []

    monkeypatch.setattr(research_tools.shutil, "which", lambda _name: "scansci-pdf")
    monkeypatch.setattr(research_tools, "_crossref_filename_metadata", lambda *_args, **_kwargs: {})

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        if command[1] == "get":
            return research_tools.subprocess.CompletedProcess(command, 0, stdout="login required", stderr="")
        return research_tools.subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "status": "success",
                    "quality": "full_text",
                    "paper": {"pdf_path": str(staged), "source": "publisher_pdf"},
                    "attempts": [{"stage": "publisher_pdf", "status": "success"}],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(research_tools.subprocess, "run", fake_run)

    result = research_tools.download_paper(
        "10.1000/example",
        workspace=workspace,
        strategy="legal_only",
    )

    assert [command[1] for command in commands] == ["get", "fetch"]
    assert result["provider"] == "scansci-pdf-institutional"
    assert result["files"] and Path(result["files"][0]).is_file()
