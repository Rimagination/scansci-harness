"""Loopback effect endpoints reserve a stable client submission before execution."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scansci_html.research_runs import StageSpec
from scansci_html.webapp import NotebookWebApp


def _app(tmp_path: Path) -> NotebookWebApp:
    return NotebookWebApp(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )


def _post(app: NotebookWebApp, path: str, payload: dict) -> tuple[int, dict]:
    response = app.dispatch("POST", path, json.dumps(payload).encode("utf-8"))
    return int(response.status), json.loads(response.body)


def test_run_follow_up_replays_terminal_without_appending_twice(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    calls: list[str] = []

    def answer(run_id, payload, *, effect_scope=""):
        calls.append(effect_scope)
        return {"run": {"run_id": run_id}, "message": {"content": "private answer"}}

    monkeypatch.setattr(app.research_agent, "continue_run_conversation", answer)
    path = "/api/runs/run-1/messages"
    payload = {"content": "draw a bird", "idempotency_key": "turn-1"}

    assert _post(app, path, {"content": "draw a bird"})[0] == 400
    first = _post(app, path, payload)
    replay = _post(app, path, payload)
    conflict = _post(app, path, {**payload, "content": "draw a cat"})

    assert first == replay
    assert first[0] == 200
    assert conflict[0] == 409
    assert len(calls) == 1 and len(calls[0]) == 64

    monkeypatch.setattr(app.research_agent.store, "delete_run_in_transaction", lambda _connection, run_id: {"ok": True, "run_id": run_id})
    assert _post(app, "/api/runs/run-1/delete", {})[0] == 200
    erased_status, erased = _post(app, path, payload)
    assert erased_status == 410
    assert erased["error"]["code"] == "conversation_deleted"
    assert b"private answer" not in app.run_message_submissions.path.read_bytes()


def test_run_follow_up_in_progress_and_uncertain_are_not_reexecuted(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow(_run_id, _payload, *, effect_scope=""):
        calls.append(effect_scope)
        entered.set()
        assert release.wait(5)
        raise RuntimeError("response lost after effect")

    monkeypatch.setattr(app.research_agent, "continue_run_conversation", slow)
    path = "/api/runs/run-2/messages"
    payload = {"content": "draw a bird", "idempotency_key": "turn-2"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_post, app, path, payload)
        assert entered.wait(5)
        assert _post(app, path, payload)[0] == 409
        monkeypatch.setattr(
            app.research_agent.store, "delete_run_in_transaction",
            lambda _connection, _run_id: (_ for _ in ()).throw(AssertionError("active follow-up was deleted")),
        )
        assert _post(app, "/api/runs/run-2/delete", {})[0] == 502
        release.set()
        assert first.result(timeout=5)[0] == 502

    assert _post(app, path, payload)[0] == 409
    assert len(calls) == 1


def test_failed_run_delete_leaves_follow_up_admission_open(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    monkeypatch.setattr(
        app.research_agent.store, "delete_run_in_transaction",
        lambda _connection, _run_id: (_ for _ in ()).throw(ValueError("run is still active")),
    )
    assert _post(app, "/api/runs/run-3/delete", {})[0] == 400
    monkeypatch.setattr(
        app.research_agent, "continue_run_conversation",
        lambda run_id, _payload, *, effect_scope="": {"run": {"run_id": run_id}, "scope": effect_scope},
    )
    assert _post(app, "/api/runs/run-3/messages", {
        "content": "continue", "idempotency_key": "after-failed-delete",
    })[0] == 200


def test_run_and_cached_reply_delete_roll_back_together(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    run = app.research_agent.store.create_run(
        notebook_id="", workflow_type="ask", title="Private task",
        input_payload={"question": "fixture"}, stages=[StageSpec("tool", "Tool", "tool")],
    )
    run_id = run["run_id"]
    app.research_agent.store.fail_stage(run_id, "tool", RuntimeError("fixture complete"))
    monkeypatch.setattr(
        app.research_agent, "continue_run_conversation",
        lambda _run_id, _payload, *, effect_scope="": {"run": {"run_id": run_id}, "message": {"content": "private reply"}},
    )
    path = f"/api/runs/{run_id}/messages"
    payload = {"content": "show reply", "idempotency_key": "private-turn"}
    assert _post(app, path, payload)[0] == 200
    with sqlite3.connect(app.workspace) as connection:
        connection.execute("""
            create trigger block_submission_erasure before insert on direct_chat_deleted_conversations
            begin select raise(abort, 'simulated erasure failure'); end
        """)

    assert _post(app, f"/api/runs/{run_id}/delete", {})[0] == 500
    assert app.research_agent.store.get_run(run_id)["run_id"] == run_id
    assert _post(app, path, payload)[1]["message"]["content"] == "private reply"

    with sqlite3.connect(app.workspace) as connection:
        connection.execute("drop trigger block_submission_erasure")
    assert _post(app, f"/api/runs/{run_id}/delete", {})[0] == 200
    with pytest.raises(FileNotFoundError):
        app.research_agent.store.get_run(run_id)
    assert _post(app, path, payload)[0] == 410


def test_run_delete_clears_durable_stage_records(tmp_path: Path) -> None:
    app = _app(tmp_path)
    store = app.research_agent.store
    run = store.create_run(
        notebook_id="", workflow_type="ask", title="Finished stage",
        input_payload={"question": "fixture"}, stages=[StageSpec("tool", "Tool", "tool")],
    )
    run_id = run["run_id"]
    store.checkpoint_stage(run_id, "tool", checkpoint_key="after-effect", state={"done": True})
    store.acquire_worker_lease(run_id, "tool", worker_id="fixture-worker")
    store.record_side_effect_receipt(
        run_id, effect_type="image", effect_key="image-1", status="completed", result={"ok": True},
    )
    store.fail_stage(run_id, "tool", RuntimeError("fixture done"))

    assert _post(app, f"/api/runs/{run_id}/delete", {})[0] == 200
    with pytest.raises(FileNotFoundError):
        store.get_run(run_id)


def test_sync_chat_and_ask_require_keys_and_replay_cached_result(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    chat_calls: list[dict] = []
    ask_calls: list[dict] = []
    monkeypatch.setattr(
        app.research_agent, "chat",
        lambda payload: chat_calls.append(dict(payload)) or {"message": {"content": "chat reply"}},
    )
    monkeypatch.setattr(
        app.research_agent, "answer_sync",
        lambda payload: ask_calls.append(dict(payload)) or {"answer": "ask reply"},
    )
    chat = {"messages": [{"role": "user", "content": "hi"}], "conversation_id": "chat-conversation-1", "idempotency_key": "chat-1"}
    ask = {"question": "hi", "idempotency_key": "ask-1", "_research_run_id": "spoofed-run"}

    assert _post(app, "/api/chat", {"messages": chat["messages"]})[0] == 400
    assert _post(app, "/api/ask", {"question": "hi"})[0] == 400
    assert _post(app, "/api/chat", chat) == _post(app, "/api/chat", chat)
    assert _post(app, "/api/ask", ask)[0] == 200
    assert _post(app, "/api/ask", ask)[0] == 409
    assert len(chat_calls) == len(ask_calls) == 1
    assert "_research_run_id" not in ask_calls[0]
    assert b"ask reply" not in app.run_message_submissions.path.read_bytes()
