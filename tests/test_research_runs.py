import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import scansci_html.research_agent as research_agent
from scansci_html.deep_research_evidence import _evidence_level, build_task_fulltext_evidence
from scansci_html.context_policy import build_token_envelope
from scansci_html.evidence_store import index_evidence_library
from scansci_html.pi_agent import PiAgentRunError
from scansci_html.research_agent import ResearchAgentRuntime, _is_restart_request
from scansci_html.research_runs import RESEARCH_SCHEMA_NAME, RESEARCH_SCHEMA_VERSION, ResearchRunStore, StageSpec
from scansci_html.workspace import initialize_notebook, sync_sources_from_evidence_store


@pytest.mark.parametrize("prior_events", [0, 81])
def test_run_read_refreshes_advisor_without_rewriting_historical_events(tmp_path: Path, monkeypatch, prior_events):
    import scansci_html.agent_advisor as advisor

    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(notebook_id="", workflow_type="ask", title="Saved research", input_payload={}, stages=[StageSpec("deliver", "Deliver", "delivery")])
    for index in range(prior_events):
        store.append_event(run["run_id"], event_type="progress", summary=str(index))
    store.append_event(run["run_id"], event_type="advisor.reviewed", summary="Original review", payload={"verdict": "needs_review"})
    original = store.events_after(run["run_id"])
    monkeypatch.setattr(advisor, "review_research_run", lambda payload: {"verdict": "passed", "run_id": payload["run_id"]})
    refreshed = store.get_run(run["run_id"])
    assert refreshed["advisor_report"]["verdict"] == "passed"
    assert store.snapshot(run["run_id"])["snapshot"]["advisor_report"]["verdict"] == "passed"
    assert store.events_after(run["run_id"]) == original
    assert next(e for e in original if e["type"] == "advisor.reviewed")["payload"]["verdict"] == "needs_review"


def test_research_store_upgrades_a_schema_v3_workspace_to_v4_without_rebuilding_data(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    ResearchRunStore(workspace)
    connection = sqlite3.connect(workspace)
    try:
        connection.execute(
            "update schema_meta set schema_version = 3 where schema_name = ?",
            (RESEARCH_SCHEMA_NAME,),
        )
        connection.execute(
            "delete from schema_migrations where schema_name = ? and version = 4",
            (RESEARCH_SCHEMA_NAME,),
        )
        connection.execute("drop table research_subagent_children")
        connection.execute("drop table research_subagent_batches")
        connection.commit()
    finally:
        connection.close()

    ResearchRunStore(workspace)
    connection = sqlite3.connect(workspace)
    try:
        version = connection.execute(
            "select schema_version from schema_meta where schema_name = ?",
            (RESEARCH_SCHEMA_NAME,),
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'")
        }
    finally:
        connection.close()

    assert version == RESEARCH_SCHEMA_VERSION == 4
    assert {"research_subagent_batches", "research_subagent_children"} <= tables


def test_local_evidence_preparation_runs_in_background_and_reports_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 1_100_000)
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=evidence_db,
    )
    started = threading.Event()
    release = threading.Event()
    stack = SimpleNamespace(
        embedding_provider=object(),
        reranker=object(),
        metadata={
            "embedding": "sentence-transformers:Qwen/Qwen3-Embedding-0.6B",
            "reranker": "qwen3:Qwen/Qwen3-Reranker-0.6B",
            "local_neural_embedding": True,
            "local_neural_reranker": True,
            "fallback": False,
            "embedding_device": "cuda:0",
            "reranker_device": "cuda:0",
        },
    )

    def fake_stack(**_kwargs):
        started.set()
        assert release.wait(5), "background model preparation did not stay cancellable"
        return stack

    monkeypatch.setattr(research_agent, "build_local_evidence_stack", fake_stack)
    monkeypatch.setattr(
        research_agent,
        "vector_cache_status",
        lambda *_args, **_kwargs: {"ready": False, "total": 1, "completed": 0},
    )

    status = runtime.prepare_local_evidence(evidence_db, quality_profile="precision")

    assert status["state"] == "preparing"
    assert status["phase"] == "loading_models"
    assert started.wait(3), "model loading should start without blocking the caller"

    release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        status = runtime.local_evidence_status(evidence_db, quality_profile="precision")
        if status["state"] in {"ready", "fallback", "error"}:
            break
        time.sleep(0.02)
    assert status["state"] == "ready"
    assert status["model"]["embedding_device"] == "cuda:0"


def test_local_evidence_stack_uses_fast_path_while_models_prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 1_100_000)
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=evidence_db,
    )
    started = threading.Event()
    release = threading.Event()

    def fake_stack(**_kwargs):
        started.set()
        assert release.wait(2)
        return SimpleNamespace(
            embedding_provider=object(),
            reranker=object(),
            metadata={"local_neural_embedding": True, "local_neural_reranker": True, "fallback": False},
        )

    monkeypatch.setattr(research_agent, "build_local_evidence_stack", fake_stack)
    monkeypatch.setattr(
        research_agent,
        "vector_cache_status",
        lambda *_args, **_kwargs: {"ready": False, "total": 1, "completed": 0},
    )

    runtime.prepare_local_evidence(evidence_db, quality_profile="precision")
    assert started.wait(1)
    fast = runtime._local_evidence_stack(evidence_db, quality_profile="precision")

    assert fast.metadata["preparing"] is True
    assert fast.metadata["effective_quality_profile"] == "fast-preparing"
    release.set()


def test_precision_answer_rejects_temporary_lexical_fallback_when_evidence_is_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    evidence_db = tmp_path / "evidence.sqlite"
    with sqlite3.connect(evidence_db) as connection:
        connection.execute("create table evidence_spans (evidence_id text)")
        connection.execute("insert into evidence_spans values ('span-1')")
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db,
    )
    monkeypatch.setattr(runtime, "_requested_notebook", lambda _payload: {"notebook_id": "test"})
    monkeypatch.setattr(runtime, "_notebook", lambda _notebook_id: {"notebook_id": "test"})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "prepare_local_evidence", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runtime, "_local_evidence_stack", lambda *_args, **_kwargs: SimpleNamespace(
        metadata={"preparing": True, "effective_quality_profile": "fast-preparing"},
    ))

    with pytest.raises(RuntimeError, match="高精度检索模型正在加载"):
        runtime.answer_sync({"question": "问题", "retrieval_quality": "precision"})


def test_precision_answer_requires_selected_provider_vector_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    evidence_db = tmp_path / "evidence.sqlite"
    with sqlite3.connect(evidence_db) as connection:
        connection.execute("create table evidence_spans (evidence_id text)")
        connection.execute("insert into evidence_spans values ('span-1')")
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db,
    )
    monkeypatch.setattr(runtime, "_requested_notebook", lambda _payload: {"notebook_id": "test"})
    monkeypatch.setattr(runtime, "_notebook", lambda _notebook_id: {"notebook_id": "test"})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "prepare_local_evidence", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runtime, "_local_evidence_stack", lambda *_args, **_kwargs: SimpleNamespace(
        embedding_provider=SimpleNamespace(cache_key="selected-provider", dimensions=1024),
        metadata={
            "effective_quality_profile": "precision",
            "local_neural_embedding": True,
            "local_neural_reranker": True,
        },
    ))
    monkeypatch.setattr("scansci_html.retrieval._has_ready_document_cards", lambda _path: True)
    requested = []
    monkeypatch.setattr(research_agent, "vector_cache_status", lambda *_args, **kwargs: requested.append(kwargs) or {"ready": False})
    warmed = []
    monkeypatch.setattr(runtime, "_maybe_warm_cache_in_background", lambda path, _stack: warmed.append(path))

    with pytest.raises(RuntimeError, match="知识库向量索引仍在构建"):
        runtime.answer_sync({"question": "问题"})

    assert requested == [{"provider": "selected-provider", "dimensions": 1024}]
    assert warmed == [evidence_db]


def test_forced_model_preparation_rebuilds_cached_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 1_100_000)
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db,
    )
    calls = []

    def fake_stack(**kwargs):
        calls.append(kwargs)
        ready = len(calls) > 1
        return SimpleNamespace(
            embedding_provider=object(),
            reranker=object(),
            metadata={
                "fallback": not ready,
                "local_neural_embedding": ready,
                "local_neural_reranker": ready,
                "effective_quality_profile": "precision" if ready else "fast",
            },
        )

    monkeypatch.setattr(research_agent, "vector_cache_status", lambda *_args, **_kwargs: {"ready": False})
    monkeypatch.setattr(research_agent, "build_local_evidence_stack", fake_stack)
    monkeypatch.setattr(runtime, "_maybe_warm_cache_in_background", lambda *_args: None)
    assert runtime._local_evidence_stack(evidence_db, quality_profile="precision", _allow_neural_build=True).metadata["fallback"]

    runtime.prepare_local_evidence(evidence_db, quality_profile="precision", force=True)
    deadline = time.time() + 3
    while time.time() < deadline:
        if runtime.local_evidence_status(evidence_db, quality_profile="precision")["state"] == "ready":
            break
        time.sleep(0.02)

    assert len(calls) == 2
    assert runtime.local_evidence_status(evidence_db, quality_profile="precision")["state"] == "ready"


def test_precision_stack_loads_neural_models_for_small_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"small library")
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db,
    )
    requested = []
    monkeypatch.setattr(research_agent, "vector_cache_status", lambda *_args, **_kwargs: {"ready": False})
    monkeypatch.setattr(research_agent, "build_local_evidence_stack", lambda **kwargs: requested.append(kwargs) or SimpleNamespace(
        metadata={"effective_quality_profile": "precision"},
    ))
    monkeypatch.setattr(runtime, "_maybe_warm_cache_in_background", lambda *_args: None)

    stack = runtime._local_evidence_stack(
        evidence_db, quality_profile="precision", _allow_neural_build=True,
    )

    assert stack.metadata["effective_quality_profile"] == "precision"
    assert requested[0]["quality_profile"] == "precision"


def test_local_evidence_status_resolves_the_selected_notebook_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected_db = tmp_path / "selected.sqlite"
    selected_db.write_bytes(b"selected")
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "root.sqlite",
    )
    monkeypatch.setattr(runtime, "_notebook", lambda _notebook_id: {"notebook_id": "selected"})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: selected_db)
    monkeypatch.setattr(
        research_agent,
        "vector_cache_status",
        lambda evidence_db, **_kwargs: {"ready": Path(evidence_db).name == "selected.sqlite"},
    )

    status = runtime.local_evidence_status(notebook_id="selected")

    assert status["cache_ready"] is True


def test_local_evidence_preparation_blocks_neither_quality_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 1_100_000)
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=evidence_db,
    )
    started = threading.Event()
    release = threading.Event()

    def fake_stack(**_kwargs):
        started.set()
        assert release.wait(2)
        return SimpleNamespace(
            embedding_provider=object(),
            reranker=object(),
            metadata={"local_neural_embedding": True, "local_neural_reranker": True, "fallback": False},
        )

    monkeypatch.setattr(research_agent, "build_local_evidence_stack", fake_stack)
    monkeypatch.setattr(
        research_agent,
        "vector_cache_status",
        lambda *_args, **_kwargs: {"ready": False, "total": 1, "completed": 0},
    )
    runtime.prepare_local_evidence(evidence_db, quality_profile="precision")
    assert started.wait(1)
    result: list[object] = []
    returned = threading.Event()

    def query_balanced():
        result.append(runtime._local_evidence_stack(evidence_db, quality_profile="balanced"))
        returned.set()

    threading.Thread(target=query_balanced, daemon=True).start()
    assert returned.wait(1), "a balanced query must not wait on a precision preload"
    assert getattr(result[0], "metadata", {}).get("preparing") is True
    release.set()


def test_repeated_local_evidence_prepare_returns_existing_status_without_deadlock(tmp_path: Path):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x")
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=evidence_db,
    )
    key = runtime._local_evidence_preparation_key(evidence_db, "precision")
    with runtime._local_evidence_prepare_lock:
        runtime._local_evidence_preparations[key] = {
            "state": "preparing",
            "phase": "loading_models",
            "progress": 0.2,
            "message": "正在加载本地 AI 模型",
            "error": "",
            "model": {},
        }

    result: list[dict[str, object]] = []
    returned = threading.Event()

    def repeat_prepare():
        result.append(runtime.prepare_local_evidence(evidence_db, quality_profile="precision"))
        returned.set()

    threading.Thread(target=repeat_prepare, daemon=True).start()
    assert returned.wait(1), "repeated status polling must not deadlock"
    assert result[0]["state"] == "preparing"


def _stages() -> list[StageSpec]:
    return [
        StageSpec("plan", "理解任务", "planner"),
        StageSpec("retrieve", "检索证据", "tool", "scansci.evidence.ask"),
        StageSpec("verify", "核验证据", "verification", "scansci.evidence.verify"),
        StageSpec("deliver", "交付结果", "delivery"),
    ]


def test_structured_recovery_preserves_pi_control_plane_failure() -> None:
    recovery = research_agent._structured_recovery(
        PiAgentRunError(
            "route stalled",
            failure={
                "code": "agent_no_progress",
                "message": "当前路线没有新结果",
                "retryable": True,
                "recovery_actions": [
                    {"id": "change_strategy", "label": "更换路线", "kind": "change_strategy"},
                ],
            },
        ),
        stage_key="retrieve",
    )

    assert recovery["code"] == "agent_no_progress"
    assert recovery["stage_key"] == "retrieve"
    assert recovery["retryable"] is True
    assert recovery["actions"][0]["id"] == "change_strategy"


def test_direct_chat_uses_only_a_release_approved_managed_backup(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    # Plain social turns now take the Host fast lane (see AGENTS.md), so these
    # fallback-chain contracts are pinned on a forced capability-aware route.
    monkeypatch.setattr(research_agent, "_should_route_through_pi", lambda *args, **kwargs: True)
    attempted_models: list[str] = []

    def fake_pi_events(chat_request, **_kwargs):
        model = str(chat_request.model_id)
        attempted_models.append(model)
        if model == "glm-4.7-flash":
            raise RuntimeError("HTTP 429 provider_rate_limited")
        yield {"type": "delta", "content": "已由备用模型完成回答。"}
        yield {"type": "done", "usage": {"total_tokens": 7}, "truncated": False}

    monkeypatch.setattr(runtime, "_pi_model_events", fake_pi_events)
    monkeypatch.setattr(
        research_agent,
        "stream_chat_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an approved managed backup must remain inside Pi")
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_managed_fallback_chat_request",
        lambda request: research_agent.replace(request, model_id="Qwen/Qwen2.5-7B-Instruct"),
    )

    events = list(runtime.chat_stream({
        "agent_harness": "direct",
        "chat_mode": "general",
        "messages": [{"role": "user", "content": "用一句话解释显著性水平。"}],
    }))

    assert attempted_models == ["glm-4.7-flash", "Qwen/Qwen2.5-7B-Instruct"]
    finished = events[-1]
    assert finished["type"] == "RUN_FINISHED"
    assert finished["result"]["message"]["content"] == "已由备用模型完成回答。"
    assert finished["result"]["model"] == {
        "provider_id": "scansci-managed",
        "model_id": "Qwen/Qwen2.5-7B-Instruct",
    }
    assert finished["result"]["agent_runtime"]["model_fallback"]["from_model"] == "glm-4.7-flash"
    assert any(
        event.get("name") == "process_trace"
        and any(item.get("status") == "fallback" for item in event.get("value", []))
        for event in events
    )


def test_direct_chat_never_switches_models_for_a_non_transient_error(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    # Plain social turns now take the Host fast lane (see AGENTS.md), so these
    # fallback-chain contracts are pinned on a forced capability-aware route.
    monkeypatch.setattr(research_agent, "_should_route_through_pi", lambda *args, **kwargs: True)
    pi_models: list[str] = []
    compatibility_models: list[str] = []

    def fake_pi_events(chat_request, **_kwargs):
        pi_models.append(str(chat_request.model_id))
        raise RuntimeError("invalid request: unsupported response schema")
        yield  # pragma: no cover - generator protocol

    def fake_stream(*_args, **kwargs):
        compatibility_models.append(str(kwargs["model"]))
        raise RuntimeError("invalid request: unsupported response schema")
        yield  # pragma: no cover - streaming compatibility protocol

    monkeypatch.setattr(runtime, "_pi_model_events", fake_pi_events)
    monkeypatch.setattr(research_agent, "stream_chat_text", fake_stream)

    events = list(runtime.chat_stream({
        "agent_harness": "direct",
        "chat_mode": "general",
        "messages": [{"role": "user", "content": "用一句话解释显著性水平。"}],
    }))

    assert pi_models == ["glm-4.7-flash"]
    assert compatibility_models == ["glm-4.7-flash"]
    assert events[-1]["type"] == "RUN_ERROR"


def test_direct_chat_does_not_use_standby_model_before_quality_approval(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    # Plain social turns now take the Host fast lane (see AGENTS.md), so these
    # fallback-chain contracts are pinned on a forced capability-aware route.
    monkeypatch.setattr(research_agent, "_should_route_through_pi", lambda *args, **kwargs: True)
    pi_models: list[str] = []
    compatibility_models: list[str] = []

    def fake_pi_events(chat_request, **_kwargs):
        pi_models.append(str(chat_request.model_id))
        raise RuntimeError("HTTP 429 provider_rate_limited")
        yield  # pragma: no cover - generator protocol

    def fake_stream(*_args, **kwargs):
        compatibility_models.append(str(kwargs["model"]))
        raise RuntimeError("HTTP 429 provider_rate_limited")
        yield  # pragma: no cover - streaming compatibility protocol

    monkeypatch.setattr(runtime, "_pi_model_events", fake_pi_events)
    monkeypatch.setattr(research_agent, "stream_chat_text", fake_stream)

    events = list(runtime.chat_stream({
        "agent_harness": "direct",
        "chat_mode": "general",
        "messages": [{"role": "user", "content": "Explain statistical significance in one sentence."}],
    }))

    # Qwen is a reachable standby route, but it cannot become a user-visible
    # automatic answer until it passes the same structured quality contract.
    assert pi_models == ["glm-4.7-flash"]
    assert compatibility_models == ["glm-4.7-flash"]
    assert events[-1]["type"] == "RUN_ERROR"


def test_pi_chat_uses_only_a_release_approved_managed_backup(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    request = runtime._direct_chat_request({
        "messages": [{"role": "user", "content": "请简要说明实验重复的重要性。"}],
    })
    attempted_models: list[str] = []

    def fake_pi_model_events(chat_request, **_kwargs):
        attempted_models.append(chat_request.model_id)
        if chat_request.model_id == "glm-4.7-flash":
            raise PiAgentRunError(
                "managed upstream limit",
                failure={"code": "provider_rate_limited", "reason": "rate_limit"},
            )
        yield {"type": "delta", "content": "备用 Pi 模型已完成。"}
        yield {"type": "done", "usage": {}, "truncated": False}

    monkeypatch.setattr(runtime, "_pi_model_events", fake_pi_model_events)
    monkeypatch.setattr(
        runtime,
        "_managed_fallback_chat_request",
        lambda chat_request: research_agent.replace(chat_request, model_id="Qwen/Qwen2.5-7B-Instruct"),
    )

    events = list(runtime._pi_events_with_compatibility_fallback(request))

    assert attempted_models == ["glm-4.7-flash", "Qwen/Qwen2.5-7B-Instruct"]
    fallback = next(event for event in events if event["type"] == "model.fallback")
    assert fallback["to_model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert events[-1]["type"] == "done"


def test_pi_managed_rate_limit_without_backup_does_not_replay_compatibility_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    request = runtime._direct_chat_request({
        "messages": [{"role": "user", "content": "你好"}],
    })

    def failed_pi(*_args, **_kwargs):
        if False:
            yield {}
        raise PiAgentRunError(
            "managed gateway limited",
            failure={"code": "provider_rate_limited", "retryable": True},
        )

    monkeypatch.setattr(runtime, "_pi_model_events", failed_pi)

    def must_not_replay(*_args, **_kwargs):
        raise AssertionError("the same managed turn must not replay through compatibility transport")

    monkeypatch.setattr(research_agent, "stream_chat_text", must_not_replay)

    with pytest.raises(PiAgentRunError):
        list(runtime._pi_events_with_compatibility_fallback(request))


def test_non_idempotent_tool_authorization_blocks_compatibility_replay(
    tmp_path: Path, monkeypatch,
) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    request = runtime._direct_chat_request({
        "messages": [{"role": "user", "content": "请生成一份文档。"}],
    })

    def failed_pi(*_args, **_kwargs):
        yield {
            "type": "tool.authorization", "name": "create_document",
            "decision": "allow", "idempotent": False,
        }
        raise RuntimeError("connection lost after authorization")

    monkeypatch.setattr(runtime, "_pi_model_events", failed_pi)
    monkeypatch.setattr(
        runtime, "_managed_fallback_chat_request",
        lambda *_args: (_ for _ in ()).throw(AssertionError("authorized writer must not replay")),
    )

    with pytest.raises(RuntimeError, match="connection lost after authorization"):
        list(runtime._pi_events_with_compatibility_fallback(request))


def test_non_idempotent_mcp_start_blocks_compatibility_replay(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    request = runtime._direct_chat_request({
        "messages": [{"role": "user", "content": "请更新一条记录。"}],
    })

    def failed_pi(*_args, **_kwargs):
        yield {
            "type": "status", "status": "mcp_effect_start",
            "details": {"effect": "write", "idempotent": False},
        }
        raise RuntimeError("connection lost after MCP write started")

    monkeypatch.setattr(runtime, "_pi_model_events", failed_pi)
    monkeypatch.setattr(
        runtime, "_managed_fallback_chat_request",
        lambda *_args: (_ for _ in ()).throw(AssertionError("MCP write must not replay")),
    )

    with pytest.raises(RuntimeError, match="connection lost after MCP write started"):
        list(runtime._pi_events_with_compatibility_fallback(request))


def test_structured_recovery_makes_invalid_paper_search_output_retryable() -> None:
    recovery = research_agent._structured_recovery(
        RuntimeError("scansci-pdf 未返回有效的 JSON 检索结果"),
        stage_key="search",
    )

    assert recovery["code"] == "paper_search_response_invalid"
    assert recovery["stage_key"] == "search"
    assert recovery["retryable"] is True
    assert recovery["message"] == "文献检索服务返回了无法读取的结果，下载尚未开始。"
    assert recovery["actions"] == [{"id": "retry", "label": "重新检索", "kind": "resume"}]


def test_structured_recovery_distinguishes_no_downloadable_identifier_from_bad_json() -> None:
    recovery = research_agent._structured_recovery(
        RuntimeError("未找到可下载的 DOI 或 arXiv 文献；请缩小主题范围"),
        stage_key="search",
    )

    assert recovery["code"] == "paper_search_no_downloadable_identifier"
    assert recovery["retryable"] is True
    assert "已完成检索" in recovery["message"]


def test_runtime_backfills_conservative_contracts_for_existing_history(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    store = ResearchRunStore(workspace)
    legacy = store.create_run(
        notebook_id="",
        workflow_type="paper_download_batch",
        title="Legacy downloads",
        input_payload={"identifiers": ["10.1/example"]},
        stages=[StageSpec("execute", "Download", "tool")],
    )
    assert legacy["task_contract"] == {}

    runtime = ResearchAgentRuntime(workspace=workspace, evidence_db=tmp_path / "evidence.sqlite")
    migrated = runtime.store.get_run(legacy["run_id"])

    assert migrated["task_contract"]["risk_level"] == "reversible"
    assert "download_and_index" in migrated["task_contract"]["allowed_tools"]


def test_transient_stage_retry_continues_in_same_worker_instead_of_sticking_queued(
    tmp_path: Path,
    monkeypatch,
):
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    runtime._pause_for_plan_review = False
    run = runtime.store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Retry safely",
        input_payload={"question": "retry"},
        stages=[StageSpec("plan", "Plan", "planner")],
    )
    calls = 0

    def flaky_plan(_run):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary provider timeout")
        return {"summary": "Recovered"}

    monkeypatch.setattr(runtime, "_plan", flaky_plan)
    runtime._execute_run(run["run_id"])

    completed = runtime.store.get_run(run["run_id"])
    assert calls == 2
    assert completed["status"] == "completed"
    assert completed["stages"][0]["attempt"] == 2


def test_permanent_stage_error_is_not_retried(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    runtime._pause_for_plan_review = False
    run = runtime.store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Do not retry bad input",
        input_payload={"question": "bad"},
        stages=[StageSpec("plan", "Plan", "planner")],
    )
    calls = 0

    def invalid_plan(_run):
        nonlocal calls
        calls += 1
        raise ValueError("invalid user parameter")

    monkeypatch.setattr(runtime, "_plan", invalid_plan)
    runtime._execute_run(run["run_id"])

    failed = runtime.store.get_run(run["run_id"])
    assert calls == 1
    assert failed["status"] == "failed"


def test_run_store_redacts_credentials_from_durable_errors_and_tool_records(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Redact secrets",
        input_payload={"question": "test"},
        stages=[StageSpec("execute", "Execute", "tool")],
    )
    store.begin_run(run["run_id"])
    store.start_stage(run["run_id"], "execute")
    failed_call = store.begin_tool_call(
        run["run_id"],
        "execute",
        tool_name="remote_tool",
        input_payload={
            "api_key": "sk-super-secret-value",
            "url": "https://example.test/mcp?access_token=secret-query-value",
        },
    )
    store.fail_tool_call(
        failed_call,
        RuntimeError("Authorization: Bearer bearer-secret-value"),
    )
    store.set_recovery(
        run["run_id"],
        {"detail": "password=plain-secret-value", "authorization": "Bearer hidden"},
    )
    store.fail_stage(
        run["run_id"],
        "execute",
        RuntimeError("request failed with secret=another-secret-value"),
    )

    serialized = str(store.get_run(run["run_id"]))
    for secret in (
        "sk-super-secret-value",
        "secret-query-value",
        "bearer-secret-value",
        "plain-secret-value",
        "another-secret-value",
    ):
        assert secret not in serialized
    assert "[REDACTED]" in serialized


def test_run_store_keeps_a_redacted_durable_event_timeline(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Event timeline",
        input_payload={"question": "test"},
        stages=[StageSpec("execute", "Execute", "tool")],
        metadata={"routing": {"origin": "freeform", "reason": "explicit_local_evidence"}},
    )
    store.begin_run(run["run_id"])
    store.start_stage(run["run_id"], "execute")
    call_id = store.begin_tool_call(
        run["run_id"],
        "execute",
        tool_name="scansci.evidence.ask",
        input_payload={"api_key": "sk-event-secret"},
    )
    store.complete_tool_call(call_id, {"ok": True})
    store.complete_stage(run["run_id"], "execute", summary="Evidence retrieved")
    completed = store.complete_run(run["run_id"])

    event_types = [item["type"] for item in completed["events"]]
    assert event_types == [
        "task.created",
        "task.started",
        "stage.started",
        "tool.started",
        "tool.completed",
        "stage.completed",
        "task.completed",
    ]
    assert completed["event_count"] == len(event_types)
    assert "sk-event-secret" not in str(completed["events"])


def test_control_plane_registry_persists_background_branches_interactions_and_recovery(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    parent = store.create_run(
        notebook_id="review",
        workflow_type="ask",
        title="Parent task",
        input_payload={"question": "Compare the evidence"},
        stages=_stages(),
        background=True,
        task_contract={
            "contract_id": "contract-parent",
            "autonomy": "read_only",
            "risk_level": "read_only",
            "allowed_tools": ["build_verified_answer"],
        },
    )
    assert parent["task_contract"]["contract_id"] == "contract-parent"
    message = store.append_message(parent["run_id"], role="user", content="Focus on methods")
    branch = store.fork_run(
        parent["run_id"],
        branch_from_message_id=message["message_id"],
        input_overrides={"question": "Compare only field methods"},
    )
    assert branch["parent_run_id"] == parent["run_id"]
    assert branch["branch_from_message_id"] == message["message_id"]
    assert branch["background"] is True
    assert branch["task_contract"] == parent["task_contract"]
    assert branch["messages"][0]["content"] == "Focus on methods"

    waiting = store.set_interaction(
        parent["run_id"],
        {
            "interaction_id": "plan-1",
            "kind": "plan",
            "summary": "Search, read and verify",
        },
    )
    assert waiting["status"] == "needs_confirmation"
    assert waiting["interaction"]["interaction_id"] == "plan-1"
    resolved = store.resolve_interaction(
        parent["run_id"],
        interaction_id="plan-1",
        response={"decision": "approve"},
    )
    assert resolved["status"] == "paused"
    assert resolved["interaction"] == {}

    recovered = store.set_recovery(
        parent["run_id"],
        {
            "code": "stage_timeout",
            "message": "Timed out",
            "actions": [{"id": "retry", "kind": "resume"}],
        },
    )
    assert recovered["recovery"]["code"] == "stage_timeout"
    registry = store.task_registry()
    assert registry["counts"]["total"] == 2
    assert registry["counts"]["branches"] == 1
    parent_entry = next(item for item in registry["tasks"] if item["run_id"] == parent["run_id"])
    assert parent_entry["branch_count"] == 1
    assert parent_entry["task_contract"]["risk_level"] == "read_only"


def test_scientific_subagents_are_bounded_and_share_parent_evidence_scope(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    parent = runtime.store.create_run(
        notebook_id="forest-library",
        workflow_type="deep_research",
        title="Forest productivity",
        input_payload={"question": "Why do models underestimate tropical productivity?"},
        stages=_stages(),
        metadata={"thinking_level": "high"},
    )
    submitted: list[str] = []
    monkeypatch.setattr(runtime, "_submit", submitted.append)

    result = runtime.delegate_scientific_agents(
        parent["run_id"],
        {
            "roles": [
                "literature_scout",
                "fulltext_analyst",
                "evidence_auditor",
                "synthesis_writer",
            ]
        },
    )

    assert result["accepted"] == 3
    assert len(submitted) == 3
    for child in result["children"]:
        assert child["parent_run_id"] == parent["run_id"]
        assert child["notebook_id"] == "forest-library"
        assert child["background"] is True
        assert child["metadata"]["runtime"] == "scansci-scientific-subagent.v1"
        assert child["metadata"]["shared_evidence"]["parent_run_id"] == parent["run_id"]
        assert child["task_contract"]["autonomy"] == "read_only"
        assert child["task_contract"]["allow_external_write"] is False
        assert "create_document" not in child["task_contract"]["allowed_tools"]
        assert child["metadata"]["subagent"]["output_schema"]["schema_version"] == "scansci.subagent-result.v1"


def test_scientific_agent_collection_only_aggregates_valid_json_handoffs(
    tmp_path: Path,
    monkeypatch,
):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    parent = runtime.store.create_run(
        notebook_id="library",
        workflow_type="ask",
        title="Parent",
        input_payload={"question": "Compare evidence"},
        stages=_stages(),
    )
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    child = runtime.delegate_scientific_agents(
        parent["run_id"],
        {"roles": ["literature_scout"], "idempotency_key": "valid-handoff"},
    )["children"][0]
    artifact = runtime.store.create_artifact(
        child["run_id"],
        artifact_type="evidence_answer",
        title="Scout handoff",
        summary="JSON handoff",
        payload={
            "reader_answer": {
                "text": '{"role":"literature_scout","findings":["candidate paper"],"evidence_uris":["scansci://evidence/doc-1/ev-1"],"uncertainties":[],"recommended_next_action":"verify DOI"}'
            }
        },
        evidence_links=[{"doc_id": "doc-1", "evidence_id": "ev-1"}],
    )
    runtime.store.complete_run(child["run_id"], output_artifact_id=artifact["artifact_id"])

    collected = runtime.collect_scientific_agents(parent["run_id"])

    assert collected["complete"] is True
    assert collected["completed"] == 1
    assert collected["children"][0]["handoff_status"] == "valid"
    assert collected["aggregated_findings"][0]["handoff"]["role"] == "literature_scout"
    assert collected["evidence_uris"] == ["scansci://evidence/doc-1/ev-1"]


def test_advisor_action_forks_an_evidence_safe_follow_up(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    run = runtime.store.create_run(
        notebook_id="library",
        workflow_type="ask",
        title="Needs evidence",
        input_payload={"question": "Claim"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
        task_contract={"task_profile": {"evidence_policy": "strict"}},
    )
    artifact = runtime.store.create_artifact(
        run["run_id"], artifact_type="evidence_answer", title="Answer", summary="No links", payload={"text": "Claim"}
    )
    runtime.store.complete_run(run["run_id"], output_artifact_id=artifact["artifact_id"])
    submitted: list[str] = []
    monkeypatch.setattr(runtime, "_submit", submitted.append)

    report = runtime.advisor_report(run["run_id"])
    branch = runtime.apply_advisor_action(run["run_id"], {"action": report["recommended_next_action"]})

    assert report["recommended_next_action"] == "run_evidence_verification"
    assert branch["parent_run_id"] == run["run_id"]
    assert submitted == [branch["run_id"]]
    assert any(event["type"] == "advisor.action_requested" for event in runtime.store.get_run(run["run_id"])["events"])


def test_completed_runtime_records_read_only_advisor_and_metrics_events(tmp_path: Path, monkeypatch) -> None:
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    created = runtime.store.create_run(
        notebook_id="review",
        workflow_type="ask",
        title="Advisor contract",
        input_payload={"question": "Summarize the evidence"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
        task_contract={"task_profile": {"evidence_policy": "off"}},
    )

    def deliver(run: dict[str, object]) -> dict[str, object]:
        return runtime.store.create_artifact(
            str(run["run_id"]),
            artifact_type="answer",
            title="Answer",
            summary="Completed",
            payload={"text": "Completed"},
        )

    monkeypatch.setattr(runtime, "_deliver", deliver)
    runtime._execute_run(str(created["run_id"]))

    completed = runtime.store.get_run(str(created["run_id"]))
    events = {item["type"]: item for item in completed["events"]}
    assert completed["status"] == "completed"
    assert events["advisor.reviewed"]["payload"]["verdict"] == "passed"
    assert events["task.metrics"]["payload"]["status"] == "completed"


def test_research_run_persists_stages_calls_artifacts_and_evidence(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    created = store.create_run(
        notebook_id="review",
        workflow_type="ask",
        title="What changed?",
        input_payload={"question": "What changed?"},
        stages=_stages(),
        model_provider_id="local-evidence",
        model_id="grounded-retrieval",
    )

    assert created["status"] == "queued"
    assert created["stage_counts"] == {"total": 4, "completed": 0}
    assert [stage["key"] for stage in created["stages"]] == ["plan", "retrieve", "verify", "deliver"]

    run_id = created["run_id"]
    store.begin_run(run_id)
    store.start_stage(run_id, "plan")
    store.complete_stage(run_id, "plan", summary="问题与资料边界已确认")
    store.start_stage(run_id, "retrieve")
    call_id = store.begin_tool_call(
        run_id,
        "retrieve",
        tool_name="scansci.evidence.ask",
        input_payload={"question": "What changed?"},
    )
    store.complete_tool_call(call_id, {"reader_answer": {"text": "It changed [1]."}})
    store.complete_stage(run_id, "retrieve", summary="找到 1 条证据", output={"answer": "It changed [1]."})
    store.start_stage(run_id, "verify")
    store.complete_stage(run_id, "verify", summary="引用核验通过", output={"passed": True})
    store.start_stage(run_id, "deliver")
    artifact = store.create_artifact(
        run_id,
        artifact_type="evidence_answer",
        title="What changed?",
        summary="It changed [1].",
        payload={"reader_answer": {"text": "It changed [1]."}},
        evidence_links=[
            {
                "evidence_id": "doc.s0001",
                "doc_id": "doc",
                "html_anchor": "results-s0001",
                "source_href": "paper.html#results-s0001",
                "exact_quote": "It changed.",
            }
        ],
    )
    store.complete_stage(run_id, "deliver", summary="已生成证据回答", output={"artifact_id": artifact["artifact_id"]})
    completed = store.complete_run(run_id, output_artifact_id=artifact["artifact_id"])

    assert completed["status"] == "completed"
    assert completed["uri"] == f"scansci://run/{run_id}"
    assert completed["progress"] == 1
    assert completed["output_artifact"]["artifact_type"] == "evidence_answer"
    assert completed["output_artifact"]["uri"].startswith(f"scansci://artifact/{run_id}/")
    assert completed["output_artifact"]["evidence_links"][0]["evidence_id"] == "doc.s0001"
    assert completed["output_artifact"]["evidence_links"][0]["uri"] == "scansci://evidence/doc/doc.s0001"
    assert completed["tool_calls"][0]["status"] == "completed"
    assert store.list_runs(notebook_id="review")[0]["run_id"] == run_id


def test_academic_search_workflow_persists_federated_discovery_artifact(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    # This contract test exercises the durable public-search workflow, not a
    # user's optional configured planning model.  Keep it isolated from a
    # developer machine's gateway credentials/rate limits so the release gate
    # cannot turn an otherwise deterministic discovery result into a failure.
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: None)
    monkeypatch.setattr(
        runtime,
        "_academic_discovery_stack",
        lambda: SimpleNamespace(
            embedding_provider="embedding-fixture",
            reranker="reranker-fixture",
            metadata={"embedding": "fixture", "reranker": "fixture", "fallback": False, "selection_reason": "academic-discovery"},
        ),
    )

    def fake_search(query, **kwargs):
        assert kwargs["provider_names"] == ["openalex", "pubmed"]
        assert kwargs["query_variants"] == ["scientific retrieval"]
        assert kwargs["required_terms"] == ["scientific retrieval"]
        assert kwargs["embedding_provider"] == "embedding-fixture"
        assert kwargs["reranker"] == "reranker-fixture"
        return {
            "query": query,
            "count": 1,
            "items": [{"title": "Grounded discovery", "doi": "10.1/example", "sources": ["openalex", "pubmed"]}],
            "evidence_status": "discovery_leads",
        }

    monkeypatch.setattr(research_agent, "search_academic_papers", fake_search)
    run = runtime.start(
        {
            "workflow_type": "academic_search",
            "query": "scientific retrieval",
            "providers": ["openalex", "pubmed"],
        }
    )
    assert run["notebook_id"] == ""
    assert run["task_contract"]["task_mode"] == "web"
    assert run["task_contract"]["risk_level"] == "read_only"
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])

    assert completed["status"] == "completed"
    assert completed["output_artifact"]["artifact_type"] == "academic_search_result"
    assert completed["output_artifact"]["payload"]["evidence_status"] == "discovery_leads"
    assert completed["output_artifact"]["payload"]["retrieval_runtime"]["fallback"] is False
    assert completed["output_artifact"]["payload"]["search_plan"]["topic"] == "scientific retrieval"


def test_academic_search_honours_a_reviewed_public_plan(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(
        runtime,
        "_academic_discovery_stack",
        lambda: SimpleNamespace(embedding_provider=None, reranker=None, metadata={"selection_reason": "academic-discovery"}),
    )
    observed: dict[str, object] = {}

    def fake_search(_query, **kwargs):
        observed.update(kwargs)
        return {"query": _query, "count": 0, "items": [], "evidence_status": "discovery_leads"}

    monkeypatch.setattr(research_agent, "search_academic_papers", fake_search)
    run = runtime.start(
        {
            "workflow_type": "academic_search",
            "query": "scientific retrieval",
            "providers": ["openalex", "semantic-scholar"],
            "search_plan": {
                "query_variants": ["scientific retrieval evidence ranking"],
                "providers": ["openalex", "semantic-scholar", "untrusted-provider"],
            },
        }
    )
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])
    plan = completed["output_artifact"]["payload"]["search_plan"]

    assert observed["provider_names"] == ["openalex", "semantic-scholar"]
    assert observed["query_variants"] == ["scientific retrieval evidence ranking"]
    assert plan["reviewed_by_user"] is True
    assert plan["source_scope"] == "public_academic_apis"
    assert plan["local_knowledge_used"] is False


def test_academic_search_inherits_the_year_and_aliases_inferred_from_the_request(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: None)
    monkeypatch.setattr(
        runtime,
        "_academic_discovery_stack",
        lambda: SimpleNamespace(embedding_provider=None, reranker=None, metadata={"selection_reason": "academic-discovery"}),
    )
    observed: dict[str, object] = {}

    def fake_search(_query, **kwargs):
        observed.update(kwargs)
        return {"query": _query, "count": 0, "items": [], "evidence_status": "discovery_leads"}

    monkeypatch.setattr(research_agent, "search_academic_papers", fake_search)
    request = (
        "\u8bf7\u68c0\u7d22 2022 \u5e74\u4ee5\u6765\u5173\u4e8e\u300c"
        "\u68c0\u7d22\u589e\u5f3a\u751f\u6210\u4e2d\u7684\u4e8b\u5b9e\u4e00\u81f4\u6027\u8bc4\u4f30"
        "\u300d\u7684\u5173\u952e\u8bba\u6587\u3002"
    )
    run = runtime.start(
        {
            "workflow_type": "academic_search",
            "query": request,
            "raw_query": request,
            "providers": ["openalex"],
        }
    )

    runtime._execute_run(run["run_id"])

    assert observed["year_from"] == 2022
    assert observed["query_variants"] == [
        "retrieval augmented generation factuality evaluation",
        "RAG faithfulness evaluation",
        "retrieval augmented generation factuality benchmark",
    ]


def test_academic_discovery_keeps_neural_inference_out_of_desktop_process(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    observed: list[dict] = []
    fixture = SimpleNamespace(
        embedding_provider="hash-fixture",
        reranker="lexical-fixture",
        metadata={"fallback": False},
    )

    def fake_stack(**kwargs):
        observed.append(kwargs)
        return fixture

    monkeypatch.setattr(research_agent, "build_local_evidence_stack", fake_stack)

    first = runtime._academic_discovery_stack()
    second = runtime._academic_discovery_stack()
    assert first is second
    assert first.embedding_provider == "hash-fixture"
    assert first.reranker == "lexical-fixture"
    assert first.metadata["selection_reason"] == "public-discovery-process-isolation"
    assert first.metadata["in_process_neural_disabled"] is True
    assert observed == [{
        "embedding_model": "__builtin__",
        "reranker_model": "__builtin__",
        "quality_profile": "balanced",
    }]


def test_local_evidence_stack_uses_selected_siliconflow_reranker(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 2_000_000)
    remote = SimpleNamespace(
        cache_key="siliconflow:https://api.siliconflow.cn/v1:BAAI/bge-reranker-v2-m3",
        device="remote",
    )
    build_calls = []

    monkeypatch.setattr(
        research_agent,
        "load_settings",
        lambda _workspace, **_kwargs: {
            "model_roles": {
                "embedding": "local:builtin-embedding",
                "reranking": "provider:siliconflow:BAAI/bge-reranker-v2-m3",
            },
            "local_models": [
                {
                    "id": "builtin-embedding",
                    "runtime": "builtin",
                    "enabled": True,
                    "model_id": "",
                }
            ],
            "providers": [
                {
                    "id": "siliconflow",
                    "kind": "openai-compatible",
                    "enabled": True,
                    "remote_rag_allowed": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "models": [
                        {
                            "id": "BAAI/bge-reranker-v2-m3",
                            "capabilities": ["reranking"],
                        }
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(research_agent, "get_provider_api_key", lambda _workspace, _provider: "secret")
    monkeypatch.setattr(
        research_agent,
        "build_reranker",
        lambda provider, **kwargs: build_calls.append((provider, kwargs)) or remote,
    )

    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=evidence_db,
    )
    stack = runtime._local_evidence_stack()

    assert len(build_calls) == 1
    assert callable(build_calls[0][1].pop("authorization_check"))
    assert build_calls == [
        (
            "siliconflow",
            {
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "secret",
                "model_name": "BAAI/bge-reranker-v2-m3",
            },
        )
    ]
    assert stack.metadata["reranker"] == remote.cache_key
    assert stack.metadata["remote_reranker_active"] is True
    assert stack.reranker.stages[1][0] is remote


def test_local_evidence_stack_keeps_pdf_retrieval_local_without_cloud_consent(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 2_000_000)
    monkeypatch.setattr(research_agent, "installed_models", lambda: [])
    monkeypatch.setattr(
        research_agent,
        "load_settings",
        lambda _workspace, **_kwargs: {
            "model_roles": {
                "embedding": "provider:siliconflow:Qwen/Qwen3-Embedding-8B",
                "reranking": "provider:siliconflow:Qwen/Qwen3-Reranker-8B",
            },
            "local_models": [],
            "providers": [{
                "id": "siliconflow",
                "enabled": True,
                "base_url": "https://api.siliconflow.cn/v1",
                "models": [
                    {"id": "Qwen/Qwen3-Embedding-8B", "capabilities": ["embedding"]},
                    {"id": "Qwen/Qwen3-Reranker-8B", "capabilities": ["reranking"]},
                ],
            }],
        },
    )
    monkeypatch.setattr(research_agent, "get_provider_api_key", lambda *_args: "secret")
    def reject_remote(*_args, **_kwargs):
        raise AssertionError("remote retrieval must remain disabled")

    monkeypatch.setattr(research_agent, "build_embedding_provider", reject_remote)
    monkeypatch.setattr(research_agent, "build_reranker", reject_remote)

    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    stack = runtime._local_evidence_stack(evidence_db, embedding_only=True)
    assert getattr(stack.embedding_provider, "device", "local") != "remote"
    assert stack.metadata["remote_embedding_active"] is False


def test_local_evidence_stack_uses_selected_siliconflow_embedding_and_reranker(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 2_000_000)
    embedding = SimpleNamespace(
        cache_key="siliconflow:https://api.siliconflow.cn/v1:Qwen/Qwen3-Embedding-8B:4096",
        dimensions=4096,
        device="remote",
        api_key="secret",
    )
    reranker = SimpleNamespace(
        cache_key="siliconflow:https://api.siliconflow.cn/v1:Qwen/Qwen3-Reranker-8B",
        device="remote",
        api_key="secret",
    )
    build_calls = []
    current_key = ["secret"]

    monkeypatch.setattr(
        research_agent,
        "load_settings",
        lambda _workspace, **_kwargs: {
            "model_roles": {
                "embedding": "provider:siliconflow:Qwen/Qwen3-Embedding-8B",
                "reranking": "provider:siliconflow:Qwen/Qwen3-Reranker-8B",
            },
            "local_models": [],
            "providers": [
                {
                    "id": "siliconflow",
                    "kind": "openai-compatible",
                    "enabled": True,
                    "remote_rag_allowed": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "models": [
                        {"id": "Qwen/Qwen3-Embedding-8B", "capabilities": ["embedding"]},
                        {"id": "Qwen/Qwen3-Reranker-8B", "capabilities": ["reranking"]},
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(research_agent, "get_provider_api_key", lambda _workspace, _provider: current_key[0])
    monkeypatch.setattr(research_agent, "remote_retrieval_granted", lambda *_args: True)
    monkeypatch.setattr(
        research_agent,
        "build_embedding_provider",
        lambda provider, **kwargs: build_calls.append(("embedding", provider, kwargs)) or (
            embedding if kwargs["api_key"] == "secret" else SimpleNamespace(**{**vars(embedding), "api_key": kwargs["api_key"]})
        ),
    )
    monkeypatch.setattr(
        research_agent,
        "build_reranker",
        lambda provider, **kwargs: build_calls.append(("reranker", provider, kwargs)) or (
            reranker if kwargs["api_key"] == "secret" else SimpleNamespace(**{**vars(reranker), "api_key": kwargs["api_key"]})
        ),
    )

    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=evidence_db,
    )
    stack = runtime._local_evidence_stack(evidence_db)

    assert len(build_calls) == 2
    authorization_checks = [kwargs.pop("authorization_check") for _, _, kwargs in build_calls]
    assert all(callable(check) for check in authorization_checks)
    assert build_calls == [
        (
            "embedding",
            "siliconflow",
            {
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "secret",
                "model": "Qwen/Qwen3-Embedding-8B",
            },
        ),
        (
            "reranker",
            "siliconflow",
            {
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "secret",
                "model_name": "Qwen/Qwen3-Reranker-8B",
            },
        ),
    ]
    assert stack.embedding_provider is embedding
    assert stack.reranker.stages[1][0] is reranker
    assert stack.metadata["remote_embedding_active"] is True
    assert stack.metadata["remote_reranker_active"] is True
    assert stack.metadata["embedding_device"] == "remote"
    assert stack.metadata["reranker_device"] == "remote"
    for check in authorization_checks:
        check()

    current_key[0] = "rotated-secret"
    for check in authorization_checks:
        with pytest.raises(PermissionError):
            check()
    rotated = runtime._local_evidence_stack(evidence_db)
    assert rotated is not stack
    assert rotated.embedding_provider.api_key == "rotated-secret"
    assert rotated.reranker.stages[1][0].api_key == "rotated-secret"


def test_prepare_remote_evidence_marks_ready_without_local_model_loading(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"x" * 1_100_000)
    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=evidence_db,
    )
    monkeypatch.setattr(
        research_agent,
        "load_settings",
        lambda _workspace, **_kwargs: {
            "model_roles": {
                "embedding": "provider:siliconflow:Qwen/Qwen3-Embedding-8B",
                "reranking": "provider:siliconflow:Qwen/Qwen3-Reranker-8B",
            },
            "providers": [
                {
                    "id": "siliconflow",
                    "enabled": True,
                    "models": [
                        {"id": "Qwen/Qwen3-Embedding-8B", "capabilities": ["embedding"]},
                        {"id": "Qwen/Qwen3-Reranker-8B", "capabilities": ["reranking"]},
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(research_agent, "get_provider_api_key", lambda _workspace, _provider: "secret")
    stack = SimpleNamespace(
        embedding_provider=SimpleNamespace(cache_key="siliconflow:embedding", device="remote"),
        reranker=SimpleNamespace(),
        metadata={
            "embedding": "siliconflow:embedding",
            "reranker": "siliconflow:reranker",
            "remote_embedding_active": True,
            "remote_reranker_active": True,
            "local_neural_embedding": False,
            "local_neural_reranker": False,
            "fallback": False,
            "effective_quality_profile": "precision",
            "embedding_device": "remote",
            "reranker_device": "remote",
        },
    )
    loading = []
    monkeypatch.setattr(
        runtime,
        "_local_evidence_stack",
        lambda *_args, **_kwargs: loading.append(True) or stack,
    )

    status = runtime.prepare_local_evidence(evidence_db, quality_profile="precision")

    assert status["state"] == "ready"
    assert status["phase"] == "remote"
    assert "远程" in status["message"]
    assert loading == [True]


def test_paper_download_batch_workflow_runs_per_item_and_reports_progress(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)

    progress_seen: list[dict] = []

    def fake_download_papers(identifiers, *, workspace, strategy="legal_only", timeout=180.0, on_progress=None, cancel_check=None, **kwargs):
        items = [
            {"identifier": ident, "status": "completed" if ident.endswith("0001") else "failed", "files": [], "error": ""}
            for ident in identifiers
        ]
        state = {"items": [dict(i) for i in items], "completed": 1, "failed": 1, "total": 2}
        if on_progress:
            on_progress(state)
            progress_seen.append(state)
        return {
            "ok": False,
            "items": items,
            "completed": 1,
            "failed": 1,
            "total": 2,
            "files": [],
            "output_dir": str(tmp_path / "downloads"),
            "message": "批量完成：成功 1/2，失败 1",
        }

    monkeypatch.setattr(research_agent, "download_papers", fake_download_papers)

    run = runtime.start(
        {
            "workflow_type": "paper_download_batch",
            "identifiers": ["10.1038/s41586-024-00001-0", "10.1038/s41586-024-00002-9"],
            "strategy": "legal_only",
        }
    )
    assert run["task_contract"]["risk_level"] == "reversible"
    assert "download_and_index" in run["task_contract"]["allowed_tools"]
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])

    assert completed["status"] == "completed"
    execute_stage = next(s for s in completed["stages"] if s["key"] == "execute")
    assert execute_stage["status"] == "completed"
    assert execute_stage["output"]["total"] == 2
    assert execute_stage["output"]["completed"] == 1
    assert execute_stage["output"]["failed"] == 1
    # The progress callback fired at least once during execution.
    assert progress_seen, "on_progress should have been invoked by the executor"
    assert completed["output_artifact"]["artifact_type"] == "downloaded_paper"
    assert completed["output_artifact"]["summary"].startswith("批量完成")


def test_paper_download_batch_rejects_missing_identifiers(tmp_path: Path):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    with pytest.raises(ValueError, match="identifiers is required"):
        runtime.start({"workflow_type": "paper_download_batch", "identifiers": []})
    with pytest.raises(ValueError, match="identifiers is required"):
        runtime.start({"workflow_type": "paper_download_batch"})


def test_paper_search_download_searches_then_downloads_without_notebook(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)

    monkeypatch.setattr(
        research_agent,
        "search_papers_for_download",
        lambda query, **kwargs: {
            "query": query,
            "author": kwargs["author"],
            "sort": kwargs["sort"],
            "total": 2,
            "identifiers": ["10.1038/nature02403", "10.1111/example"],
            "items": [
                {"title": "Leaf economics", "identifier": "10.1038/nature02403"},
                {"title": "Global change", "identifier": "10.1111/example"},
            ],
        },
    )
    monkeypatch.setattr(
        research_agent,
        "download_papers",
        lambda identifiers, **_kwargs: {
            "ok": True,
            "items": [{"identifier": value, "status": "completed", "files": [f"{value}.pdf"]} for value in identifiers],
            "completed": 2,
            "failed": 0,
            "total": 2,
            "files": ["a.pdf", "b.pdf"],
            "output_dir": str(tmp_path / "downloads"),
            "message": "批量完成：成功 2/2，失败 0",
        },
    )

    run = runtime.start(
        {
            "workflow_type": "paper_search_download",
            "author": "Peter B. Reich",
            "query": "",
            "limit": 20,
            "sort": "cited_by_count",
        }
    )
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])

    assert completed["status"] == "completed"
    assert completed["output_artifact"]["artifact_type"] == "downloaded_paper"
    assert completed["output_artifact"]["payload"]["completed"] == 2
    assert completed["output_artifact"]["payload"]["papers"][0]["title"] == "Leaf economics"


def test_paper_search_download_plans_english_alias_for_chinese_topic(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")

    monkeypatch.setattr(
        runtime,
        "_writing_chat_client",
        lambda: (_ for _ in ()).throw(AssertionError("known bilingual plan must not wait for a model")),
    )

    plan = runtime._plan({
        "workflow_type": "paper_search_download",
        "input": {"query": "山地土壤碳组分"},
    })

    assert plan["search_query"] == "soil organic carbon fractions mountain soils"
    assert plan["query_variants"] == [
        "soil organic carbon fractions mountain soils",
        "alpine soil organic carbon fractions",
        "soil organic carbon fractions alpine meadow",
    ]
    assert plan["summary"].endswith("将使用“soil organic carbon fractions mountain soils”检索论文")


def test_paper_search_download_requires_query_or_author(tmp_path: Path):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    with pytest.raises(ValueError, match="query or author"):
        runtime.start({"workflow_type": "paper_search_download"})


@pytest.mark.parametrize("workflow", ["paper_search_download", "paper_download", "paper_download_batch"])
def test_literature_workflow_preserves_pause_from_download_cancellation(tmp_path: Path, monkeypatch, workflow):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    run = runtime.start({"workflow_type": workflow, "query": "RAG", "identifier": "2005.11401", "identifiers": ["2005.11401"]})

    def stop(*args, cancel_check, **kwargs):
        runtime.pause(run["run_id"])
        assert cancel_check()
        raise research_agent._BatchCancelled("paused")

    monkeypatch.setattr(research_agent, "search_papers_for_download", stop)
    monkeypatch.setattr(research_agent, "download_papers", stop)
    runtime._execute_run(run["run_id"])
    paused = runtime.store.get_run(run["run_id"])
    assert paused["status"] == "paused"
    assert paused["resumable"] is True
    assert paused["stages"][0]["status"] == "completed"
    assert any(stage["status"] == "paused" for stage in paused["stages"])


def test_single_paper_download_keeps_failure_visible(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(research_agent, "download_papers", lambda *args, **kwargs: {
        "ok": False, "files": [], "items": [{"error": "no public PDF"}], "failed": 1,
    })
    run = runtime.start({"workflow_type": "paper_download", "identifier": "2005.11401"})
    runtime._execute_run(run["run_id"])
    failed = runtime.store.get_run(run["run_id"])
    assert failed["status"] == "failed"
    assert "no public PDF" in failed["error"]["message"]


def test_deep_research_plan_has_stage_summary_and_insufficient_evidence_is_not_verified(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: (_ for _ in ()).throw(ValueError("offline")))
    run = {"workflow_type": "deep_research", "input": {"question": "What evidence exists?"}}

    plan = runtime._plan(run)
    verification = runtime._verify(
        {
            "stages": [
                {
                    "kind": "tool",
                    "status": "completed",
                    "output": {
                        "answer": {"insufficient_evidence": True},
                        "adequacy": {"is_sufficient": False},
                        "citation_verification": {"passed": True},
                        "reader_answer": {"citation_count": 0},
                    },
                }
            ]
        }
    )

    assert plan["summary"]
    assert len(plan["search_queries"]) >= 2
    assert verification == {
        "passed": False,
        "no_unsupported_claims": True,
        "insufficient_evidence": True,
        "citation_count": 0,
        "evidence_status": "",
        "details": {"passed": True},
    }


def test_deep_research_reviewable_answer_is_not_marked_as_hard_insufficient(tmp_path: Path):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")

    verification = runtime._verify(
        {
            "stages": [
                {
                    "kind": "tool",
                    "status": "completed",
                    "output": {
                        "answer": {"insufficient_evidence": False, "answerability": "needs_review"},
                        "adequacy": {"is_sufficient": False, "answerability": "needs_review"},
                        "citation_verification": {"passed": True},
                        "reader_answer": {"citation_count": 1},
                    },
                }
            ]
        }
    )

    assert verification["passed"] is True
    assert verification["insufficient_evidence"] is False
    assert verification["answerability"] == "needs_review"


def test_deep_research_is_standalone_and_builds_external_abstract_evidence(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    run = runtime.start(
        {
            "workflow_type": "deep_research",
            "notebook_id": "should-not-be-used",
            "question": "How should scientific RAG be evaluated?",
            "writing_brief": {
                "audience": "researcher",
                "focus": "Compare evaluation protocols and write a source-linked academic report.",
            },
        }
    )

    assert run["notebook_id"] == ""
    assert run["task_contract"]["task_mode"] == "web"
    # The full ready read-only catalog remains discoverable; the product mode
    # only controls initial hints and this standalone run carries no notebook.
    assert "kb_search" in run["task_contract"]["allowed_tools"]
    assert "kb_search" not in run["task_contract"]["initial_tools"]

    plan = {
        "title": "Scientific RAG evaluation",
        "perspectives": [
            {"title": "Methods", "question": "Which methods are evaluated?", "keywords": ["evaluation"]},
            {"title": "Limits", "question": "Which limits are reported?", "keywords": ["limits"]},
        ],
    }
    discovery = {
        "items": [
            {
                "title": f"Study {index}",
                "doi": f"10.1000/study-{index}",
                "abstract": f"This is a traceable abstract for study {index}. It reports an evaluation method, dataset boundaries, and limitations for scientific retrieval grounded generation.",
                "url": f"https://example.org/study-{index}",
                "score": 0.9 - index / 10,
            }
            for index in range(1, 4)
        ]
    }
    monkeypatch.setattr(runtime, "_stage_output", lambda _run, key: plan if key == "plan" else discovery)

    result = runtime._external_deep_research_evidence(run)

    assert result["phase"] == "external_abstracts"
    assert result["evidence_status"] == "external_source_abstracts"
    assert len(result["evidence"]) == 3
    assert result["evidence"][0]["doc_id"].startswith("external:")
    assert result["evidence"][0]["original_url"] == "https://example.org/study-1"
    assert result["review_plan"]["sections"][0]["citation_ids"] == ["S1", "S2", "S3"]
    assert result["writing_brief"]["audience"] == "researcher"
    assert "Compare evaluation protocols" in result["writing_brief"]["focus"]
    assert "只可根据所给公开来源摘要作答" in result["writing_brief"]["focus"]


def test_deep_research_prefers_task_acquired_fulltext_evidence(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace.sqlite"
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    content = (
        "Abstract\n\nThis paper contains a sufficiently detailed and traceable finding about evidence grounded scientific retrieval.\n\n"
        "Results\n\nThe result compares retrieval methods across scientific questions and records concrete limitations for auditability.\n\n"
        "Discussion\n\nThe report should cite original full text instead of relying only on published abstracts."
    )
    first.write_text(content, encoding="utf-8")
    second.write_text(content.replace("retrieval methods", "ranking methods"), encoding="utf-8")
    task_evidence = build_task_fulltext_evidence(
        workspace,
        "run-fulltext",
        [
            {"title": "One", "doi": "10.1000/one", "files": [str(first)]},
            {"title": "Two", "doi": "10.1000/two", "files": [str(second)]},
        ],
        min_sentence_length=20,
    )
    runtime = ResearchAgentRuntime(workspace=workspace, evidence_db=tmp_path / "library.sqlite")
    run = {
        "run_id": "run-fulltext",
        "input": {
            "question": "How should scientific retrieval be audited?",
            "writing_brief": {"tone": "academic", "focus": "Compare audit frameworks."},
        },
    }
    monkeypatch.setattr(
        runtime,
        "_stage_output",
        lambda _run, key: {"task_evidence": task_evidence} if key == "acquire" else {},
    )
    monkeypatch.setattr(
        runtime,
        "_task_fulltext_evidence_stack",
        lambda: SimpleNamespace(
            embedding_provider="embedding-fixture",
            reranker="reranker-fixture",
            metadata={"embedding": "fixture", "reranker": "fixture"},
        ),
    )
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: "writing-client")
    captured: dict[str, object] = {}

    def fake_retrieve_review_evidence(_db, _question, **kwargs):
        captured.update(kwargs)
        return {
            "phase": "retrieval",
            "evidence": [{"citation_id": "1"}, {"citation_id": "2"}, {"citation_id": "3"}],
            "retrieval_summary": {"document_count": 2, "evidence_count": 3},
        }

    monkeypatch.setattr(research_agent, "retrieve_review_evidence", fake_retrieve_review_evidence)

    result = runtime._task_fulltext_deep_research_evidence(run)

    assert result["phase"] == "retrieval"
    assert result["evidence_status"] == "task_acquired_fulltext"
    assert result["evidence_level"] == "fulltext"
    assert result["source_scope"]["kind"] == "task_acquired_fulltext"
    assert result["task_evidence"]["root"].startswith(str(tmp_path))
    assert captured["writing_brief"]["tone"] == "academic"
    assert "Compare audit frameworks." in captured["writing_brief"]["focus"]
    assert "Prioritize claims supported by the acquired full text" in captured["writing_brief"]["focus"]


def test_literature_review_retrieval_uses_local_embedding_and_reranker(
    tmp_path: Path,
    monkeypatch,
):
    """A review over a selected notebook must use its configured neural stack."""

    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    run = runtime.store.create_run(
        notebook_id="pv-ecology",
        workflow_type="literature_review",
        title="Photovoltaic ecology review",
        input_payload={
            "question": "How do photovoltaic systems affect biodiversity?",
            "retrieval_quality": "precision",
            "source_doc_ids": ["doc-1"],
            "writing_brief": {"tone": "academic"},
        },
        stages=[StageSpec("research", "Retrieve evidence", "tool", "scansci.review.retrieve")],
        background=False,
    )
    runtime.store.begin_run(run["run_id"])
    runtime.store.start_stage(run["run_id"], "research")

    observed: dict[str, object] = {}
    neural_stack = SimpleNamespace(
        embedding_provider="embedding-neural",
        reranker="reranker-neural",
        metadata={
            "embedding": "sentence-transformers:Qwen/Qwen3-Embedding-0.6B",
            "reranker": "qwen3:Qwen/Qwen3-Reranker-0.6B",
            "local_neural_embedding": True,
            "local_neural_reranker": True,
        },
    )

    def fake_local_stack(_db, **kwargs):
        observed["local_stack_kwargs"] = kwargs
        return neural_stack

    monkeypatch.setattr(runtime, "_local_evidence_stack", fake_local_stack)
    monkeypatch.setattr(
        runtime,
        "_task_fulltext_evidence_stack",
        lambda: (_ for _ in ()).throw(AssertionError("review retrieval must not use task fallback")),
    )
    monkeypatch.setattr(
        runtime,
        "_notebook",
        lambda _notebook_id: {"notebook_id": "pv-ecology", "sources": []},
    )
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: tmp_path / "evidence.sqlite")
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: "writing-client")

    def fake_retrieve(_db, _question, **kwargs):
        observed.update(kwargs)
        return {"phase": "retrieval", "evidence": [], "retrieval_summary": {}}

    monkeypatch.setattr(research_agent, "retrieve_review_evidence", fake_retrieve)

    current = runtime.store.get_run(run["run_id"])
    stage = next(item for item in current["stages"] if item["key"] == "research")
    result = runtime._run_tool(current, stage)

    assert result["phase"] == "retrieval"
    assert observed["local_stack_kwargs"] == {"quality_profile": "precision"}
    assert observed["embedding_provider"] == "embedding-neural"
    assert observed["reranker"] == "reranker-neural"
    assert observed["retrieval_runtime"]["local_neural_embedding"] is True
    assert observed["retrieval_runtime"]["local_neural_reranker"] is True


def test_task_fulltext_quality_warning_does_not_discard_traceable_claim_evidence() -> None:
    assert _evidence_level(
        {"documents": 2, "spans": 850},
        {
            "passed": False,
            "claim_ready": True,
            "oversized_spans": 1,
            "missing_structure_spans": 0,
            "source_text_mismatches": 0,
            "orphan_sections": 0,
        },
    ) == "fulltext"


def test_deep_research_stage_summary_uses_task_index_before_final_trace_exists() -> None:
    summary = ResearchAgentRuntime._tool_summary(
        {"workflow_type": "deep_research"},
        {
            "evidence_status": "task_acquired_fulltext",
            "task_evidence": {"index": {"documents": 4, "spans": 2339}},
        },
    )

    assert "基于 4 篇任务获取的全文" in summary
    assert "建立 2339 个可回跳证据片段" in summary


def test_deep_research_auto_runs_past_planning_and_completes_honest_discovery_only_report(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    missing_evidence_db = tmp_path / "not-yet-indexed.sqlite"
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(runtime, "_requested_notebook", lambda _payload: {"notebook_id": "review", "sources": []})
    monkeypatch.setattr(runtime, "_notebook", lambda _notebook_id: {"notebook_id": "review", "sources": []})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: missing_evidence_db)
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: (_ for _ in ()).throw(ValueError("offline")))
    monkeypatch.setattr(
        runtime,
        "_local_evidence_stack",
        lambda _db, **_kwargs: SimpleNamespace(
            embedding_provider="embedding-fixture",
            reranker="reranker-fixture",
            metadata={"embedding": "fixture", "reranker": "fixture", "fallback": False},
        ),
    )
    monkeypatch.setattr(research_agent, "build_academic_provider", lambda name: SimpleNamespace(source_name=name))
    monkeypatch.setattr(
        research_agent,
        "run_discovery_loop",
        lambda question, plan, **_kwargs: {
            "question": question,
            "plan": plan,
            "items": [{"title": "Candidate only", "doi": "10.1/candidate", "sources": ["openalex"]}],
            "count": 1,
            "candidate_count": 1,
            "deduplicated_count": 1,
            "rounds": [{"round": 1, "queries": [{"query": question}]}],
            "coverage": [],
            "unresolved_gaps": [],
            "provider_errors": {},
            "evidence_status": "discovery_leads",
        },
    )
    monkeypatch.setattr(
        runtime,
        "_acquire_deep_research_fulltext",
        lambda _run, _stage, call_id: {
            "strategy": "legal_only",
            "acquired": [],
            "acquired_count": 0,
            "failed": [],
            "failed_count": 0,
            "evidence_status": "existing_or_unavailable",
        },
    )

    run = runtime.start(
        {
            "workflow_type": "deep_research",
            "notebook_id": "review",
            "question": "What evidence exists?",
            "providers": ["openalex"],
        }
    )
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])
    artifact = completed["output_artifact"]

    assert completed["status"] == "completed"
    assert completed["interaction"] == {}
    assert artifact["artifact_type"] == "deep_research_report"
    assert artifact["payload"]["phase"] == "discovery_only"
    assert artifact["payload"]["answer"]["insufficient_evidence"] is True
    assert artifact["payload"]["citation_verification"]["claim_count"] == 0
    assert artifact["evidence_links"] == []
    verify_stage = next(stage for stage in completed["stages"] if stage["key"] == "verify")
    assert verify_stage["output"]["passed"] is False


def test_novelty_check_persists_four_axis_fulltext_assessment(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.touch()
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    runtime._pause_for_plan_review = False  # skip plan approval gate in tests
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(runtime, "_requested_notebook", lambda _payload: {"notebook_id": "review", "sources": []})
    monkeypatch.setattr(runtime, "_notebook", lambda _notebook_id: {"notebook_id": "review", "sources": []})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: "writing-client")
    monkeypatch.setattr(
        runtime,
        "_local_evidence_stack",
        lambda _db, **_kwargs: SimpleNamespace(
            embedding_provider="embedding-fixture",
            reranker="reranker-fixture",
            metadata={"embedding": "fixture", "reranker": "fixture", "fallback": False},
        ),
    )
    monkeypatch.setattr(research_agent, "build_academic_provider", lambda name: SimpleNamespace(source_name=name))
    monkeypatch.setattr(
        research_agent,
        "plan_novelty_check",
        lambda problem, novelty, **_kwargs: {
            "problem": problem,
            "claimed_novelty": novelty,
            "axes": {
                key: {"label": key, "statement": novelty, "terms": ["evidence"]}
                for key in ("problem_framing", "core_mechanism", "key_insight", "application_domain")
            },
            "perspectives": [{"title": "Mechanism", "question": novelty, "keywords": ["evidence"]}],
            "search_queries": ["query one", "query two", "query three"],
            "planner": "fixture",
        },
    )
    monkeypatch.setattr(
        research_agent,
        "run_discovery_loop",
        lambda question, plan, **_kwargs: {
            "question": question,
            "plan": plan,
            "items": [{"title": "Prior A", "doi": "10.1/prior", "sources": ["openalex"]}],
            "count": 1,
            "candidate_count": 1,
            "deduplicated_count": 1,
            "rounds": [{"round": 1, "queries": [{"query": "query one"}]}],
            "coverage": [],
            "unresolved_gaps": [],
            "provider_errors": {},
            "evidence_status": "discovery_leads",
        },
    )
    monkeypatch.setattr(
        runtime,
        "_acquire_deep_research_fulltext",
        lambda _run, _stage, call_id: {
            "strategy": "legal_only",
            "acquired_count": 1,
            "failed_count": 0,
            "evidence_status": "indexed_fulltext",
        },
    )
    evidence = [
        {
            "citation_id": "1",
            "evidence_id": "prior-a.s1",
            "doc_id": "prior-a",
            "paper": "Prior A",
            "exact_quote": "Prior evidence.",
            "html_anchor": "s1",
        }
    ]
    monkeypatch.setattr(
        research_agent,
        "retrieve_review_evidence",
        lambda *_args, **_kwargs: {
            "phase": "retrieval",
            "evidence": evidence,
            "retrieval_summary": {"document_count": 1, "evidence_count": 1},
        },
    )
    monkeypatch.setattr(
        research_agent,
        "assess_novelty_evidence",
        lambda *_args, **_kwargs: {
            "phase": "novelty_assessment",
            "status": "assessed",
            "assessment_mode": "model_evidence_assessment",
            "verdict": {"level": 3, "label": "中等重合风险", "not_proof_of_novelty": True},
            "evidence_adequacy": {"sufficient": True},
            "reader_answer": {
                "text": "Prior A shows a medium overlap risk.",
                "citation_count": 1,
                "citations": evidence,
            },
            "citation_verification": {"passed": True, "claim_count": 1},
        },
    )

    run = runtime.start(
        {
            "workflow_type": "novelty_check",
            "notebook_id": "review",
            "problem": "Ground scientific answers",
            "novelty": "Bind every sentence to full-text evidence",
            "providers": ["openalex", "openreview", "dblp"],
        }
    )
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])

    assert completed["status"] == "completed"
    assert [stage["key"] for stage in completed["stages"]] == [
        "plan",
        "discover",
        "acquire",
        "research",
        "assess",
        "verify",
        "deliver",
    ]
    assert completed["output_artifact"]["artifact_type"] == "novelty_assessment"
    assert completed["output_artifact"]["payload"]["verdict"]["level"] == 3
    assert completed["output_artifact"]["evidence_links"][0]["evidence_id"] == "prior-a.s1"
    verify_stage = next(stage for stage in completed["stages"] if stage["key"] == "verify")
    assert verify_stage["output"]["passed"] is True


def test_research_idea_persists_quality_gates_and_requires_separate_novelty_check(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.touch()
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    runtime._pause_for_plan_review = False  # skip plan approval gate in tests
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(runtime, "_requested_notebook", lambda _payload: {"notebook_id": "review", "sources": []})
    monkeypatch.setattr(runtime, "_notebook", lambda _notebook_id: {"notebook_id": "review", "sources": []})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "_writing_chat_client", lambda: "writing-client")
    monkeypatch.setattr(
        runtime,
        "_local_evidence_stack",
        lambda _db, **_kwargs: SimpleNamespace(
            embedding_provider="embedding-fixture",
            reranker="reranker-fixture",
            metadata={"embedding": "fixture", "reranker": "fixture", "fallback": False},
        ),
    )
    monkeypatch.setattr(research_agent, "build_academic_provider", lambda name: SimpleNamespace(source_name=name))
    plan = {
        "direction": "Reduce unsupported scientific claims",
        "constraints": ["one GPU"],
        "problem_statement": "Unsupported claims remain after retrieval.",
        "success_criterion": "Lower unsupported sentence rate.",
        "search_queries": ["q1", "q2", "q3"],
        "perspectives": [{"title": "Limits", "question": "What fails?", "keywords": ["failure"]}],
    }
    monkeypatch.setattr(research_agent, "plan_research_idea", lambda *_args, **_kwargs: dict(plan))
    monkeypatch.setattr(
        research_agent,
        "run_discovery_loop",
        lambda question, plan, **_kwargs: {
            "question": question,
            "plan": plan,
            "items": [{"title": "Paper A", "doi": "10.1/a", "sources": ["openalex"]}],
            "count": 1,
            "candidate_count": 1,
            "deduplicated_count": 1,
            "rounds": [{"round": 1, "queries": [{"query": "q1"}]}],
            "coverage": [],
            "unresolved_gaps": [],
            "provider_errors": {},
            "evidence_status": "discovery_leads",
        },
    )
    monkeypatch.setattr(runtime, "_acquire_deep_research_fulltext", lambda *_args, **_kwargs: {"acquired_count": 1, "failed_count": 0})
    evidence = [{"citation_id": "1", "evidence_id": "a.s1", "doc_id": "a", "paper": "Paper A", "exact_quote": "Evidence."}]
    monkeypatch.setattr(
        research_agent,
        "retrieve_review_evidence",
        lambda *_args, **_kwargs: {"phase": "retrieval", "evidence": evidence, "retrieval_summary": {"document_count": 1, "evidence_count": 1}},
    )
    bottleneck = {"phase": "bottleneck_diagnosis", "status": "grounded", "bottleneck_statement": "Binding", "citation_ids": ["1"]}
    candidate = {"phase": "candidate_generation", "status": "candidate", "title": "Claim lock", "core_mechanism": "Lock claims", "citation_ids": ["1"], "mechanism_steps": [{"id": "s1"}, {"id": "s2"}]}
    coherence = {"phase": "coherence_audit", "status": "audited", "verdict": "pass"}
    falsifiability = {"phase": "falsifiability_audit", "status": "audited", "passed": True}
    implementability = {"phase": "implementability_audit", "status": "audited", "verdict": "ready"}
    monkeypatch.setattr(research_agent, "diagnose_research_bottleneck", lambda *_args, **_kwargs: dict(bottleneck))
    monkeypatch.setattr(research_agent, "generate_research_candidate", lambda *_args, **_kwargs: dict(candidate))
    monkeypatch.setattr(research_agent, "audit_candidate_coherence", lambda *_args, **_kwargs: dict(coherence))
    monkeypatch.setattr(research_agent, "audit_candidate_falsifiability", lambda *_args, **_kwargs: dict(falsifiability))
    monkeypatch.setattr(research_agent, "audit_candidate_implementability", lambda *_args, **_kwargs: dict(implementability))
    monkeypatch.setattr(
        research_agent,
        "assemble_research_idea_card",
        lambda *_args, **_kwargs: {
            "phase": "research_idea_card",
            "status": "ready_for_novelty_check",
            "title": "Claim lock",
            "candidate": candidate,
            "quality_gates": {
                "grounded_bottleneck": True,
                "single_candidate": True,
                "coherence": True,
                "falsifiability": True,
                "implementability": True,
                "citation_integrity": True,
                "novelty": False,
            },
            "required_next_gate": {"workflow_type": "novelty_check"},
            "reader_answer": {"text": "Ready for novelty check.", "citation_count": 1, "citations": evidence},
            "citation_verification": {"passed": True, "claim_count": 1},
        },
    )

    run = runtime.start(
        {
            "workflow_type": "research_idea",
            "notebook_id": "review",
            "direction": "Reduce unsupported scientific claims",
            "constraints": "one GPU",
            "providers": ["openalex", "openreview"],
        }
    )
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])

    assert completed["status"] == "completed"
    assert [stage["key"] for stage in completed["stages"]] == [
        "plan", "discover", "acquire", "evidence", "diagnose", "generate", "coherence", "falsify", "implement", "assemble", "verify", "deliver"
    ]
    assert completed["output_artifact"]["artifact_type"] == "research_idea_card"
    assert completed["output_artifact"]["payload"]["quality_gates"]["novelty"] is False
    assert completed["output_artifact"]["payload"]["required_next_gate"]["workflow_type"] == "novelty_check"
    assert completed["output_artifact"]["evidence_links"][0]["evidence_id"] == "a.s1"
    verify_stage = next(stage for stage in completed["stages"] if stage["key"] == "verify")
    assert verify_stage["output"]["passed"] is True
    assert verify_stage["output"]["novelty_checked"] is False


def test_research_run_cancel_and_resume_preserves_completed_stages(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="review",
        workflow_type="ask",
        title="Resume me",
        input_payload={"question": "Resume me"},
        stages=_stages(),
    )
    run_id = run["run_id"]
    store.begin_run(run_id)
    store.start_stage(run_id, "plan")
    store.complete_stage(run_id, "plan", summary="done")
    requested = store.request_cancel(run_id)

    assert requested["cancel_requested"] is True
    cancelled = store.mark_cancelled(run_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["resumable"] is True

    resumed = store.prepare_resume(run_id)
    assert resumed["status"] == "queued"
    assert resumed["current_stage"] == "retrieve"
    assert resumed["stages"][0]["status"] == "completed"
    assert resumed["stages"][1]["status"] == "pending"


def test_research_run_pause_and_resume_preserves_completed_stages(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="review",
        workflow_type="ask",
        title="Pause me",
        input_payload={"question": "Pause me"},
        stages=_stages(),
    )
    run_id = run["run_id"]
    store.begin_run(run_id)
    store.start_stage(run_id, "plan")
    store.complete_stage(run_id, "plan", summary="done")
    store.start_stage(run_id, "retrieve")

    requested = store.request_pause(run_id)
    assert requested["pause_requested"] is True
    assert requested["cancel_requested"] is False
    assert store.pause_requested(run_id) is True
    assert store.stop_requested(run_id) is True

    paused = store.mark_paused(run_id, summary="Paused by user")
    assert paused["status"] == "paused"
    assert paused["resumable"] is True
    assert paused["pause_requested"] is False
    assert paused["stages"][0]["status"] == "completed"
    assert paused["stages"][1]["status"] == "paused"
    assert [event["type"] for event in paused["events"]][-2:] == [
        "run.pause_requested",
        "task.paused",
    ]

    resumed = store.prepare_resume(run_id)
    assert resumed["status"] == "queued"
    assert resumed["current_stage"] == "retrieve"
    assert resumed["stages"][0]["status"] == "completed"
    assert resumed["stages"][1]["status"] == "pending"
    assert resumed["pause_requested"] is False


def test_research_run_persists_partial_stage_progress_and_cancelled_tool(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="review",
        workflow_type="evidence_index",
        title="Build index",
        input_payload={},
        stages=[StageSpec("build", "Build vectors", "tool", "scansci.evidence.index")],
    )
    store.begin_run(run["run_id"])
    store.start_stage(run["run_id"], "build")
    call_id = store.begin_tool_call(
        run["run_id"],
        "build",
        tool_name="scansci.evidence.index",
        input_payload={},
    )

    progress = store.update_stage_progress(
        run["run_id"],
        "build",
        fraction=0.4,
        summary="40/100",
        output={"completed": 40, "total": 100},
    )
    store.cancel_tool_call(call_id, {"completed": 40, "total": 100})
    cancelled = store.mark_cancelled(run["run_id"])

    assert progress["progress"] == pytest.approx(0.4)
    assert progress["stages"][0]["output"] == {"completed": 40, "total": 100}
    assert cancelled["tool_calls"][0]["status"] == "cancelled"
    resumed = store.prepare_resume(run["run_id"])
    assert resumed["status"] == "queued"
    assert resumed["stages"][0]["status"] == "pending"


def test_evidence_index_workflow_cooperatively_cancels_and_resumes(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "large-evidence.sqlite"
    evidence_db.write_bytes(b"0" * 1_000_001)
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    runtime._pause_for_plan_review = False  # skip plan approval gate in tests
    monkeypatch.setattr(
        runtime,
        "_notebook",
        lambda notebook_id: {"notebook_id": notebook_id, "title": "光伏生态文献", "sources": []},
    )
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(research_agent, "load_embedding_cache_rows", lambda _db: {"a": {"text": "A"}})
    monkeypatch.setattr(
        runtime,
        "_local_evidence_stack",
        lambda _db, **_kwargs: SimpleNamespace(embedding_provider=object(), metadata={"embedding": "test"}),
    )

    calls = 0
    batch_sizes: list[int] = []

    def fake_prewarm(_db, _rows, *, progress_callback, cancel_requested, **_kwargs):
        nonlocal calls
        calls += 1
        batch_sizes.append(int(_kwargs.get("cache_batch_size", 0)))
        if calls == 1:
            progress_callback(40, 100)
            active = runtime.store.list_runs(notebook_id="review")[0]
            runtime.store.request_cancel(active["run_id"])
            assert cancel_requested() is True
            return {"completed": 40, "total": 100, "cancelled": True}
        progress_callback(100, 100)
        return {"completed": 100, "total": 100, "cancelled": False}

    monkeypatch.setattr(research_agent, "prewarm_embedding_cache", fake_prewarm)
    run = runtime.start_evidence_index("review")
    assert run is not None
    assert run["title"] == "优化「光伏生态文献」的语义检索"
    assert run["input"]["notebook_title"] == "光伏生态文献"
    runtime._execute_run(run["run_id"])
    cancelled = runtime.store.get_run(run["run_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["progress"] == pytest.approx(0.4)
    assert cancelled["stages"][0]["output"] == {
        "completed": 40,
        "total": 100,
        "notebook_title": "光伏生态文献",
    }
    assert cancelled["tool_calls"][0]["status"] == "cancelled"

    runtime.store.prepare_resume(run["run_id"])
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])
    assert completed["status"] == "completed"
    assert completed["progress"] == 1
    assert sorted(call["status"] for call in completed["tool_calls"]) == ["cancelled", "completed"]
    assert batch_sizes == [research_agent.DEFAULT_NEURAL_CACHE_BATCH_SIZE] * 2


def test_evidence_index_never_persists_hash_fallback_as_qwen_vectors(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"0" * 1_000_001)
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    runtime._pause_for_plan_review = False
    monkeypatch.setattr(runtime, "_notebook", lambda notebook_id: {"notebook_id": notebook_id, "sources": []})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(research_agent, "load_embedding_cache_rows", lambda _db: {"a": {"text": "A"}})
    monkeypatch.setattr(
        runtime,
        "_local_evidence_stack",
        lambda _db, **_kwargs: SimpleNamespace(
            embedding_provider=object(),
            metadata={
                "qwen_embedding_active": False,
                "fallback": True,
                "fallback_reasons": ["model unavailable locally"],
            },
        ),
    )
    monkeypatch.setattr(
        research_agent,
        "prewarm_embedding_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback vectors must never be persisted")
        ),
    )

    run = runtime.start_evidence_index("review")
    assert run is not None
    runtime._execute_run(run["run_id"])
    failed = runtime.store.get_run(run["run_id"])

    assert failed["status"] == "failed"
    assert "不会写入伪语义索引" in failed["error"]["message"]


def test_evidence_index_accepts_siliconflow_embedding_without_local_qwen(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"0" * 1_000_001)
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    runtime._pause_for_plan_review = False
    monkeypatch.setattr(runtime, "_notebook", lambda notebook_id: {"notebook_id": notebook_id, "sources": []})
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(research_agent, "load_embedding_cache_rows", lambda _db: {"a": {"text": "A"}})
    provider = SimpleNamespace(cache_key="siliconflow:embedding", dimensions=4096, device="remote")
    monkeypatch.setattr(
        runtime,
        "_local_evidence_stack",
        lambda _db, **_kwargs: SimpleNamespace(
            embedding_provider=provider,
            metadata={
                "qwen_embedding_active": False,
                "remote_embedding_active": True,
                "fallback": False,
            },
        ),
    )
    monkeypatch.setattr(
        research_agent,
        "prewarm_embedding_cache",
        lambda *_args, **_kwargs: {"ready": True, "completed": 1, "total": 1},
    )

    run = runtime.start_evidence_index("review")
    assert run is not None
    runtime._execute_run(run["run_id"])
    completed = runtime.store.get_run(run["run_id"])

    assert completed["status"] == "completed"


def test_evidence_index_reports_vectors_migrated_from_a_previous_store(tmp_path: Path, monkeypatch):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"0" * 1_000_001)
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    monkeypatch.setattr(
        runtime,
        "_notebook",
        lambda notebook_id: {
            "notebook_id": notebook_id,
            "title": "光伏生态文献",
            "sources": [],
            "metadata": {
                "vector_cache_migration": {
                    "migrated_vectors": 128,
                    "sources": [str(tmp_path / "legacy-evidence.sqlite")],
                }
            },
        },
    )
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(runtime, "_submit", lambda _run_id: None)
    monkeypatch.setattr(
        research_agent,
        "vector_cache_status",
        lambda *_args, **_kwargs: {"available": True, "total": 200, "completed": 128, "ready": False},
    )

    run = runtime.start_evidence_index("review")

    assert run is not None
    assert run["input"]["migrated_vectors"] == 128
    assert run["input"]["migration_sources"] == [str(tmp_path / "legacy-evidence.sqlite")]


@pytest.mark.parametrize("cached", [0, 80])
def test_automatic_evidence_index_defers_for_new_and_reusable_libraries(tmp_path: Path, monkeypatch, cached):
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"0" * 1_000_001)
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=evidence_db)
    monkeypatch.setattr(
        runtime,
        "_notebook",
        lambda notebook_id: {"notebook_id": notebook_id, "title": "Reusable library", "sources": []},
    )
    monkeypatch.setattr(runtime, "_evidence_db_for_notebook", lambda _notebook: evidence_db)
    monkeypatch.setattr(
        research_agent,
        "vector_cache_status",
        lambda *_args, **_kwargs: {
            "available": True,
            "total": 100,
            "completed": cached,
            "serving_vectors": cached,
            "ready": False,
        },
    )
    monkeypatch.setattr(runtime, "start", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("automatic indexing should defer")))

    assert runtime.start_evidence_index("review", automatic=True) is None


def test_research_run_recovers_interrupted_process_as_paused(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    store = ResearchRunStore(workspace)
    run = store.create_run(
        notebook_id="review",
        workflow_type="ask",
        title="Interrupted",
        input_payload={"question": "Interrupted"},
        stages=_stages(),
    )
    store.begin_run(run["run_id"])
    store.start_stage(run["run_id"], "plan")

    reopened = ResearchRunStore(workspace)
    assert reopened.recover_interrupted_runs() == 1
    recovered = reopened.get_run(run["run_id"])
    assert recovered["status"] == "paused"
    assert recovered["stages"][0]["status"] == "paused"
    assert recovered["error"]["code"] == "app_restarted"


def test_research_run_follow_up_messages_stay_with_the_original_run(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="",
        workflow_type="pdf_to_ppt",
        title="Deck from paper",
        input_payload={"question": "Create a deck"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )

    user_message = store.append_message(run["run_id"], role="user", content="Can you explain slide 3?")
    assistant_message = store.append_message(
        run["run_id"],
        role="assistant",
        content="Slide 3 compares the reported evidence.",
        usage={"prompt_tokens": 8, "completion_tokens": 9, "total_tokens": 17},
        processing_ms=321,
    )
    reopened = ResearchRunStore(tmp_path / "workspace.sqlite").get_run(run["run_id"])

    assert [item["message_id"] for item in reopened["messages"]] == [user_message["message_id"], assistant_message["message_id"]]
    assert reopened["messages"][1]["usage"]["total_tokens"] == 17
    assert reopened["messages"][1]["processing_ms"] == 321
    assert store.list_runs()[0]["run_id"] == run["run_id"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("重来", True), ("请从头开始", True), ("restart from scratch", True), ("不要重来", False), ("继续解释", False)],
)
def test_restart_intent_is_explicit_and_does_not_match_negation(text: str, expected: bool):
    assert _is_restart_request(text) is expected


def test_direct_chat_routes_zotero_access_question_to_mandatory_status_tool():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[{"role": "user", "content": "你能访问我的 Zotero 吗？"}],
    )

    assert mode == "zotero-status"


def test_download_follow_up_routes_comparison_to_registered_task_documents():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[{"role": "user", "content": "这些文献有什么共同点？"}],
        run={"workflow_type": "paper_search_download"},
    )

    assert mode == "task-documents"


def test_recent_download_and_index_phrase_routes_to_task_documents_without_selected_run():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[
            {
                "role": "user",
                "content": "总结刚才下载并索引的论文，只比较当前任务中的文献。",
            }
        ],
    )

    assert mode == "task-documents"


def test_download_follow_up_short_continue_reuses_prior_document_intent():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[
            {"role": "user", "content": "这些文献有什么共同点？"},
            {"role": "assistant", "content": "我会读取已下载文献后进行比较。"},
            {"role": "user", "content": "继续"},
        ],
        run={"workflow_type": "paper_search_download"},
    )

    assert mode == "task-documents"


@pytest.mark.parametrize(
    "text",
    [
        "请搜索、下载并总结 Peter B. Reich 的论文",
        "Find papers about forest drought and compare their conclusions",
        "Download these papers as PDFs",
    ],
)
def test_multi_step_paper_acquisition_routes_to_research_agent_tools(text: str):
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[{"role": "user", "content": text}],
    )

    assert mode == "research"


def test_local_only_knowledge_request_wins_over_global_web_toggle():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        "on",
        messages=[
            {
                "role": "user",
                "content": "Use only the linked ScanSci knowledge base and run RAG. Do not use the public web.",
            }
        ],
    )

    assert mode == "knowledge"


def test_knowledge_retrieval_and_real_presentation_are_composed():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "knowledge",
        messages=[
            {
                "role": "user",
                "content": "检索当前知识库，总结这些文献并创建一个实际可下载的 PPTX。",
            }
        ],
    )

    assert mode == "knowledge+slides"


def test_downloaded_task_documents_and_presentation_are_composed():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[
            {
                "role": "user",
                "content": "总结刚才下载的文献，并生成一个实际的 PPT 演示文稿。",
            }
        ],
        run={"workflow_type": "paper_download_batch"},
    )

    assert mode == "task-documents+slides"


def test_research_download_review_and_presentation_are_composed():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[
            {
                "role": "user",
                "content": "搜索并下载 3 篇森林生产力论文，写综述并创建实际 PPTX。",
            }
        ],
    )

    assert mode == "research+slides"


def test_exact_doi_download_routes_to_strict_research_mode():
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        messages=[
            {
                "role": "user",
                "content": "Download the exact DOI 10.2307/2679812, index it, then summarize it.",
            }
        ],
    )

    assert mode == "research"


def test_follow_up_context_contains_actual_search_candidates_for_summary():
    run = {
        "workflow_type": "academic_search", "input": {"query": "RAG evaluation"},
        "output_artifact": {
            "artifact_type": "academic_search_result", "summary": "24 candidates",
            "payload": {"items": [
                {"title": f"Paper {i}", "year": 2024, "doi": f"10.1234/{i}", "abstract": "abstract evidence", "source_records": "large raw provider response"}
                for i in range(24)
            ]},
        },
    }
    context = ResearchAgentRuntime._run_conversation_context(run)
    assert "Paper 23" in context and "10.1234/23" in context
    assert "abstract evidence" in context and "not verified full text" in context
    assert "source_records" not in context and "large raw provider response" not in context


def test_follow_up_context_preserves_each_workflow_goal_and_mode():
    run = {
        "workflow_type": "novelty_check",
        "title": "Novelty audit",
        "input": {
            "problem": "How can agents improve evidence retrieval?",
            "novelty": "Adaptive evidence-budget routing",
            "constraints": "Single machine and three months",
        },
        "output_artifact": {
            "title": "Novelty assessment",
            "summary": "Two related methods require comparison.",
            "payload": {},
        },
    }
    context = ResearchAgentRuntime._run_conversation_context(run)
    assert "How can agents improve evidence retrieval?" in context
    assert "Adaptive evidence-budget routing" in context
    assert "Single machine and three months" in context
    assert "Task mode: knowledge" in context
    assert "Novelty assessment" in context


def test_follow_up_context_names_registered_task_files(tmp_path: Path):
    paper = tmp_path / "reich-paper.pdf"
    run = {
        "workflow_type": "paper_download_batch",
        "title": "Peter B. Reich",
        "input": {"identifiers": ["10.1/example"]},
        "stages": [{"status": "completed", "output": {"files": [str(paper)]}}],
        "output_artifact": {
            "title": "Peter B. Reich",
            "summary": "Downloaded paper",
            "payload": {"files": [str(paper)]},
        },
    }

    context = ResearchAgentRuntime._run_conversation_context(run)

    assert "Registered task files: reich-paper.pdf" in context


def test_failed_follow_up_persists_a_failure_marker_and_keeps_context_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A model failure must not leave an answered-looking dangling user turn."""

    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    run = runtime.store.create_run(
        notebook_id="",
        workflow_type="paper_download",
        title="批量文献下载",
        input_payload={"identifiers": ["10.1/example"]},
        stages=[StageSpec("plan", "解析论文标识", "planner")],
    )
    def _fail(*_args, **_kwargs):
        raise RuntimeError("gateway unreachable")

    # Both transports are pinned so the test stays offline and deterministic
    # regardless of which route the router picks for this turn.
    monkeypatch.setattr(runtime, "_complete_with_pi", _fail)
    monkeypatch.setattr(runtime, "_direct_events_with_managed_fallback", _fail)

    with pytest.raises(Exception):
        runtime.continue_run_conversation(run["run_id"], {"content": "请解释一下显著性水平。"})

    messages = runtime.store.get_run(run["run_id"])["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    failure_notice = messages[-1]
    assert failure_notice["usage"].get("failure") is True
    assert "重新发送或重试" in failure_notice["content"]

    # The failure marker documents the broken turn for the user but must not
    # leak back into the model context on the next attempt.
    context = ResearchAgentRuntime._follow_up_messages(
        runtime.store.get_run(run["run_id"])["messages"]
    )
    assert [item["role"] for item in context] == ["user"]


@pytest.mark.parametrize(
    ("workflow", "expected_mode"),
    [
        ("pdf_to_ppt", "slides"),
        ("ppt_outline", "slides"),
        ("ask", "knowledge"),
        ("literature_review", "knowledge"),
        ("deep_research", "academic"),
        ("novelty_check", "knowledge"),
        ("writing", "writing"),
        ("writing_task", "writing"),
        ("paper_download", "general"),
    ],
)
def test_follow_up_keeps_the_originating_workflow_mode(workflow: str, expected_mode: str):
    assert ResearchAgentRuntime._run_chat_mode({"workflow_type": workflow}) == expected_mode


def test_evidence_index_follow_up_reads_its_bound_library_catalog_without_task_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A completed index is durable library state, not a task attachment."""

    runtime = ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    run = runtime.store.create_run(
        notebook_id="pv-ecology",
        workflow_type="evidence_index",
        title="优化「光伏生态文献」的语义检索",
        input_payload={},
        stages=[StageSpec("index", "建立索引", "tool")],
    )
    observed: dict[str, object] = {}

    def catalog(*, notebook_ids: list[str], request: dict[str, object]):
        observed["notebook_ids"] = notebook_ids
        observed["request"] = request
        return {
            "operation": "list",
            "library_titles": ["光伏生态文献"],
            "match_terms": [],
            "total_documents": 376,
            "document_count": 376,
            "items": [
                {"title": "Solar ecology evidence"},
                {"title": "Photovoltaic biodiversity review"},
            ],
        }

    monkeypatch.setattr(runtime, "_knowledge_catalog_summary", catalog)
    monkeypatch.setattr(
        runtime,
        "_complete_with_pi",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("catalog follow-up must not enter the task-document/Pi path")
        ),
    )

    result = runtime.continue_run_conversation(
        run["run_id"],
        {"content": "这个知识库有什么？"},
    )

    assert observed["notebook_ids"] == ["pv-ecology"]
    assert result["agent_runtime"]["harness"] == "knowledge-catalog"
    assert "376 篇已索引资料" in result["message"]["content"]
    assert "Solar ecology evidence" in result["message"]["content"]
    assert [item["role"] for item in result["run"]["messages"]] == ["user", "assistant"]


def test_evidence_index_catalog_follow_up_reads_the_real_bound_index(tmp_path: Path):
    library_root = tmp_path / "pv-library"
    library_root.mkdir()
    (library_root / "ecology.html").write_text(
        "<article><h1>Photovoltaic ecology review</h1><p>Ecological evidence for solar sites.</p></article>",
        encoding="utf-8",
    )
    evidence_db = tmp_path / "evidence.sqlite"
    index_evidence_library(library_root, db_path=evidence_db, inject_evidence_html=False, min_sentence_length=10)
    workspace = tmp_path / "workspace.sqlite"
    initialize_notebook(
        workspace,
        notebook_id="pv-ecology",
        title="光伏生态文献",
        root_path=library_root,
    )
    sync_sources_from_evidence_store(workspace, evidence_db, notebook_id="pv-ecology")
    runtime = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db)
    run = runtime.store.create_run(
        notebook_id="pv-ecology",
        workflow_type="evidence_index",
        title="优化「光伏生态文献」的语义检索",
        input_payload={},
        stages=[StageSpec("index", "建立索引", "tool")],
    )

    result = runtime.continue_run_conversation(
        run["run_id"],
        {"content": "这个知识库有什么？"},
    )

    assert result["agent_runtime"]["harness"] == "knowledge-catalog"
    assert "1 篇已索引资料" in result["message"]["content"]
    assert "Photovoltaic ecology review" in result["message"]["content"]


def test_catalog_query_counts_a_large_library_without_loading_every_document(tmp_path: Path):
    evidence_db = tmp_path / "evidence.sqlite"
    with sqlite3.connect(evidence_db) as connection:
        connection.execute(
            """
            create table source_documents (
                doc_id text primary key,
                title text not null,
                doi text,
                source_url text not null,
                publication_year integer,
                html_path text not null,
                evidence_html_path text not null
            )
            """
        )
        connection.executemany(
            """
            insert into source_documents
            (doc_id, title, doi, source_url, publication_year, html_path, evidence_html_path)
            values (?, ?, '', ?, ?, '', '')
            """,
            [
                (f"doc-{index}", f"Library document {index}", f"file:///doc-{index}", 2020 + index % 5)
                for index in range(80)
            ],
        )

    total, matched, preview = ResearchAgentRuntime._catalog_rows_from_evidence_db(
        evidence_db,
        [],
        preview_limit=50,
    )

    assert total == 80
    assert matched == 80
    assert len(preview) == 50


def test_long_follow_up_history_is_compacted_without_losing_recent_turns():
    messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn-{index} " + "x" * 6000}
        for index in range(30)
    ]
    messages.append({"role": "user", "content": "turn-30 FINAL-SENTINEL"})
    normalized = ResearchAgentRuntime._follow_up_messages(messages, max_recent=10, max_chars=12_000)
    descriptor = research_agent.descriptor_from_model_record(
        provider_id="fixture",
        provider_kind="openai-compatible",
        model_id="fixture",
        model_record={"id": "fixture", "context_window": "32K", "capabilities": ["reasoning"]},
    )
    compacted, report = build_token_envelope(normalized, descriptor=descriptor)

    assert "FINAL-SENTINEL" in compacted[-1]["content"]
    assert report.estimated_tokens <= descriptor.provider_input_tokens
    assert report.omitted_messages > 0
    # Optional dialogue is admitted as whole user/assistant turns.
    optional_roles = [item["role"] for item in compacted[:-1]]
    assert all(
        optional_roles[index : index + 2] == ["user", "assistant"]
        for index in range(0, len(optional_roles), 2)
    )


def test_restart_request_restarts_terminal_run_in_place_without_model_call(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    run = runtime.store.create_run(
        notebook_id="",
        workflow_type="paper_search_download",
        title="Peter B. Reich",
        input_payload={"author": "Peter B. Reich", "limit": 20},
        stages=[StageSpec("search", "Search", "tool"), StageSpec("deliver", "Deliver", "delivery")],
    )
    runtime.store.complete_stage(run["run_id"], "search", summary="Previous search")
    runtime.store.complete_stage(run["run_id"], "deliver", summary="Previous delivery")
    runtime.store.complete_run(run["run_id"], output_artifact_id="artifact_old")
    submitted: list[str] = []
    monkeypatch.setattr(runtime, "_submit", lambda run_id: submitted.append(run_id))
    monkeypatch.setattr(runtime, "_direct_chat_request", lambda _payload: (_ for _ in ()).throw(AssertionError("restart must not call the model")))

    result = runtime.continue_run_conversation(run["run_id"], {"content": "重来"})

    assert submitted == [run["run_id"]]
    assert result["agent_runtime"]["restart"] is True
    assert result["run"]["run_id"] == run["run_id"]
    assert result["run"]["status"] == "queued"
    assert result["run"]["progress"] == 0
    assert result["run"]["output_artifact_id"] == ""
    assert [item["role"] for item in result["run"]["messages"]][-2:] == ["user", "assistant"]
    assert "Peter B. Reich" in result["run"]["messages"][-1]["content"]


def test_prepare_restart_resets_all_stage_state_but_keeps_audit_rows(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Restart me",
        input_payload={"question": "Keep the original question"},
        stages=[StageSpec("plan", "Plan", "planner"), StageSpec("deliver", "Deliver", "delivery")],
    )
    store.complete_stage(run["run_id"], "plan", summary="Done once", output={"kept": True})
    store.fail_stage(run["run_id"], "deliver", RuntimeError("temporary"))
    restarted = store.prepare_restart(run["run_id"])

    assert restarted["status"] == "queued"
    assert restarted["current_stage"] == "plan"
    assert restarted["output_artifact_id"] == ""
    assert restarted["metadata"]["effect_epoch"]
    assert all(stage["status"] == "pending" for stage in restarted["stages"])
    assert all(stage["summary"] == "" and stage["error_message"] == "" for stage in restarted["stages"])


def test_resume_keeps_partial_download_checkpoint_visible(tmp_path: Path):
    store = ResearchRunStore(tmp_path / "workspace.sqlite")
    run = store.create_run(
        notebook_id="",
        workflow_type="paper_download_batch",
        title="Partial downloads",
        input_payload={"identifiers": ["10.1/a", "10.1/b", "10.1/c"]},
        stages=[StageSpec("execute", "Download", "tool")],
    )
    store.begin_run(run["run_id"])
    store.start_stage(run["run_id"], "execute")
    store.update_stage_progress(
        run["run_id"],
        "execute",
        fraction=0.66,
        summary="Saved 2/3",
        output={
            "completed": 2,
            "failed": 1,
            "items": [
                {"identifier": "10.1/a", "status": "completed", "files": ["a.pdf"]},
                {"identifier": "10.1/b", "status": "failed", "error": "timeout"},
                {"identifier": "10.1/c", "status": "completed", "files": ["c.pdf"]},
            ],
        },
    )
    store.recover_interrupted_runs()

    paused = store.get_run(run["run_id"])
    assert paused["status"] == "paused"
    assert paused["stages"][0]["output"]["items"][0]["files"] == ["a.pdf"]

    resumed = store.prepare_resume(run["run_id"])
    assert resumed["status"] == "queued"
    assert resumed["stages"][0]["output"]["completed"] == 2


def test_research_run_follow_ups_reuse_the_same_persistent_pi_session(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    run = runtime.store.create_run(
        notebook_id="linked-library",
        workflow_type="pdf_to_ppt",
        title="Deck from paper",
        input_payload={"question": "Create a deck"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    observed_sessions: list[str | None] = []
    observed_notebooks: list[str] = []

    def fake_complete(request, *, task_mode=None, session_id=None, active_run_id=""):
        observed_sessions.append(session_id)
        observed_notebooks.append(request.notebook_id)
        assert active_run_id == run["run_id"]
        return (
            f"Pi reply {len(observed_sessions)}",
            {"total_tokens": 4},
            {
                "harness": "pi-agent-sdk",
                "task_mode": task_mode,
                "tool_calls": [],
                "session": {
                    "session_id": session_id,
                    "session_file": "persistent.jsonl",
                    "resumed": len(observed_sessions) > 1,
                },
                "compactions": [],
                "compatibility_fallback": False,
                "compatibility_error": "",
            },
        )

    monkeypatch.setattr(runtime, "_complete_with_pi", fake_complete)

    first = runtime.continue_run_conversation(run["run_id"], {"content": "Explain slide 3."})
    second = runtime.continue_run_conversation(run["run_id"], {"content": "Now shorten it."})

    expected_session = f"research-run-{run['run_id']}"
    assert observed_sessions == [expected_session, expected_session]
    assert observed_notebooks == ["linked-library", "linked-library"]
    assert first["agent_runtime"]["harness"] == "pi-agent-sdk"
    assert second["agent_runtime"]["session"]["resumed"] is True
    assert [item["role"] for item in second["run"]["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


@pytest.mark.parametrize("code", ["provider_rate_limited", "timeout", "upstream_connection_failed", "model_transport_unavailable", "plain_timeout"])
def test_follow_up_does_not_replay_provider_after_pi_recovery(tmp_path: Path, monkeypatch, code):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    run = runtime.store.create_run(
        notebook_id="", workflow_type="ask", title="Saved research",
        input_payload={"question": "Prior research"}, stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    def failed_pi(*_args, **_kwargs):
        if code == "plain_timeout":
            raise TimeoutError("Whole Pi turn expired")
        raise PiAgentRunError("provider unavailable", failure={"code": code, "retryable": True})
    def must_not_replay(*_args, **_kwargs):
        raise AssertionError("Pi already owns model-response recovery")
    monkeypatch.setattr(runtime, "_complete_with_pi", failed_pi)
    monkeypatch.setattr(research_agent, "complete_chat_text", must_not_replay)
    with pytest.raises((PiAgentRunError, TimeoutError)):
        runtime.continue_run_conversation(run["run_id"], {"content": "Summarize the saved result."})
    assert runtime.store.get_run(run["run_id"])["title"] == "Saved research"


def test_follow_up_final_user_turn_is_not_rejected_by_a_fixed_character_limit(tmp_path: Path, monkeypatch):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    run = runtime.store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Long model-aware follow-up",
        input_payload={"question": "Start"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    sentinel = "FINAL-LONG-FOLLOW-UP-SENTINEL"
    question = sentinel + ("x" * 12_500)
    observed: dict[str, object] = {}

    def fake_complete(request, **_kwargs):
        observed["messages"] = request.messages
        return "accepted", {}, {"harness": "pi-agent-sdk", "tool_calls": []}

    monkeypatch.setattr(runtime, "_complete_with_pi", fake_complete)

    result = runtime.continue_run_conversation(run["run_id"], {"content": question})

    assert result["message"]["content"] == "accepted"
    assert sentinel in str(observed["messages"][-1]["content"])


@pytest.mark.parametrize(
    "workflow",
    [
        "ask",
        "literature_review",
        "deep_research",
        "novelty_check",
        "research_idea",
        "ppt_outline",
        "writing",
        "writing_task",
        "paper_download",
        "paper_download_batch",
        "paper_search_download",
        "pdf_to_ppt",
    ],
)
def test_multiple_feedback_turns_stay_in_one_run_for_every_workflow(tmp_path: Path, monkeypatch, workflow: str):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    payload = {"question": "原始任务", "author": "Peter B. Reich", "limit": 20, "identifiers": ["10.1/example"]}
    run = runtime.store.create_run(
        notebook_id="",
        workflow_type=workflow,
        title="Multi-turn task",
        input_payload=payload,
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )
    observed_sessions: list[str | None] = []

    def fake_complete(_request, *, task_mode=None, session_id=None, active_run_id=""):
        observed_sessions.append(session_id)
        assert active_run_id == run["run_id"]
        return (
            f"Reply {len(observed_sessions)} for {workflow}",
            {"total_tokens": 4},
            {"harness": "pi-agent-sdk", "task_mode": task_mode, "session": {"session_id": session_id}},
        )

    monkeypatch.setattr(runtime, "_complete_with_pi", fake_complete)

    first = runtime.continue_run_conversation(run["run_id"], {"content": "第一轮反馈"})
    second = runtime.continue_run_conversation(run["run_id"], {"content": "第二轮反馈"})

    assert first["run"]["run_id"] == second["run"]["run_id"] == run["run_id"]
    assert observed_sessions == [f"research-run-{run['run_id']}"] * 2
    assert [item["content"] for item in second["run"]["messages"] if item["role"] == "user"] == [
        "第一轮反馈",
        "第二轮反馈",
    ]
    assert [item["role"] for item in second["run"]["messages"]] == ["user", "assistant", "user", "assistant"]


def test_research_run_cancel_aborts_the_active_pi_session(tmp_path: Path):
    runtime = ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    run = runtime.store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Cancelable Pi task",
        input_payload={"question": "Find evidence"},
        stages=_stages(),
    )
    runtime.store.begin_run(run["run_id"])
    cancelled: list[bool] = []

    class ActivePi:
        def cancel(self):
            cancelled.append(True)
            return True

    with runtime._active_pi_lock:
        runtime._active_pi_clients[run["run_id"]] = ActivePi()

    updated = runtime.cancel(run["run_id"])

    assert cancelled == [True]
    assert updated["cancel_requested"] is True


def test_research_run_archive_restore_and_delete_are_persistent(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    store = ResearchRunStore(workspace)
    run = store.create_run(
        notebook_id="",
        workflow_type="ask",
        title="Conversation to organize",
        input_payload={"question": "Keep the exported files"},
        stages=[StageSpec("deliver", "Deliver", "delivery")],
    )

    with pytest.raises(ValueError, match="运行中的对话不能归档或删除"):
        store.archive_run(run["run_id"])

    store.append_message(run["run_id"], role="user", content="Keep this until I delete the conversation")
    artifact = store.create_artifact(
        run["run_id"],
        artifact_type="evidence_answer",
        title="Saved answer",
        summary="Saved locally",
        payload={"text": "Answer"},
        evidence_links=[{"evidence_id": "doc.s0001", "doc_id": "doc", "exact_quote": "Answer"}],
    )
    store.complete_run(run["run_id"], output_artifact_id=artifact["artifact_id"])
    archived = store.archive_run(run["run_id"])
    assert archived["archived"] is True
    assert store.list_runs() == []
    assert store.list_runs(archived=True)[0]["run_id"] == run["run_id"]

    restored = ResearchRunStore(workspace).restore_run(run["run_id"])
    assert restored["archived"] is False
    assert store.list_runs()[0]["run_id"] == run["run_id"]

    deleted = store.delete_run(run["run_id"])
    assert deleted == {
        "ok": True,
        "run_id": run["run_id"],
        "title": "Conversation to organize",
        "deleted": True,
    }
    with pytest.raises(FileNotFoundError):
        store.get_run(run["run_id"])
