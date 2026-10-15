"""Checks for a lost stream before its first observable event."""

from pathlib import Path
from types import SimpleNamespace
import os
import sqlite3
import threading

import pytest

from scansci_html.direct_chat_history import DirectConversationNotFound
from scansci_html.direct_chat_submissions import DirectChatSubmissionStore
from scansci_html.research_agent import ResearchAgentRuntime
from scansci_html.webapp import NotebookWebApp


def _app(workspace: Path, producer):
    app = NotebookWebApp.__new__(NotebookWebApp)
    app.direct_chat_submissions = DirectChatSubmissionStore(workspace)
    app.research_agent = SimpleNamespace(chat_stream=producer)
    return app


def test_lost_first_event_cannot_execute_same_submission_again(tmp_path: Path) -> None:
    executions: list[str] = []

    def producer(_payload):
        executions.append("effect committed")
        yield {"type": "RUN_STARTED", "runId": "first-run"}
        yield {"type": "RUN_FINISHED", "result": {"message": {"content": "done"}}}

    workspace = tmp_path / "workspace.sqlite"
    app = _app(workspace, producer)
    payload = {"idempotency_key": "same-request", "conversation_id": "conversation-1", "messages": [{"role": "user", "content": "write"}]}
    first_stream = app.stream_chat(payload)
    assert next(first_stream)["type"] == "RUN_STARTED"
    assert list(app.stream_chat(payload))[0]["code"] == "submission_in_progress"
    first_stream.close()  # Simulate the client disconnecting before seeing the first event.

    restarted_app = _app(workspace, producer)
    replay = list(restarted_app.stream_chat(payload))
    assert replay[0]["code"] == "submission_uncertain"
    assert replay[0]["failure"]["retryable"] is False
    assert executions == ["effect committed"]


def test_completed_submission_replays_result_and_rejects_key_reuse(tmp_path: Path) -> None:
    executions: list[str] = []

    def producer(_payload):
        executions.append("effect committed")
        yield {"type": "RUN_FINISHED", "result": {"message": {"content": "done"}}}

    workspace = tmp_path / "workspace.sqlite"
    payload = {"idempotency_key": "finished-request", "conversation_id": "conversation-1", "messages": [{"role": "user", "content": "write"}]}
    app = _app(workspace, producer)
    original = list(app.stream_chat(payload))
    restarted_app = _app(workspace, producer)

    assert list(restarted_app.stream_chat(payload)) == original
    changed = {**payload, "messages": [{"role": "user", "content": "different write"}]}
    conflict = list(restarted_app.stream_chat(changed))
    assert conflict[0]["code"] == "idempotency_conflict"
    assert conflict[0]["failure"]["retryable"] is False
    assert executions == ["effect committed"]


def test_erasing_conversation_removes_cached_answer_but_keeps_replay_tombstone(tmp_path: Path) -> None:
    executions: list[str] = []

    def producer(_payload):
        executions.append("effect committed")
        yield {"type": "RUN_FINISHED", "result": {"message": {"content": "private answer"}}}

    workspace = tmp_path / "workspace.sqlite"
    payload = {"idempotency_key": "erased-request", "conversation_id": "conversation-1", "messages": [{"role": "user", "content": "write"}]}
    app = _app(workspace, producer)
    assert list(app.stream_chat(payload))[0]["type"] == "RUN_FINISHED"
    assert b"private answer" in app.direct_chat_submissions.path.read_bytes()

    app.direct_chat_submissions.erase_conversation("conversation-1")
    assert b"private answer" not in app.direct_chat_submissions.path.read_bytes()
    assert app.direct_chat_submissions.status("erased-request", "conversation-1") == ("erased", None)
    assert list(_app(workspace, producer).stream_chat(payload))[0]["code"] == "conversation_deleted"
    assert executions == ["effect committed"]


def test_empty_direct_chat_control_id_cannot_control_another_run(tmp_path: Path) -> None:
    called: list[str] = []
    client = SimpleNamespace(
        active_request_id="request-b",
        pause=lambda _request_id: called.append("pause"),
        cancel=lambda _request_id: called.append("cancel"),
        steer=lambda *_args, **_kwargs: called.append("steer"),
        follow_up=lambda *_args, **_kwargs: called.append("follow_up"),
        respond_interaction=lambda *_args, **_kwargs: called.append("respond_interaction"),
    )
    runtime = ResearchAgentRuntime.__new__(ResearchAgentRuntime)
    runtime.workspace = tmp_path / "workspace.sqlite"
    runtime._active_pi_lock = threading.Lock()
    runtime._active_pi_clients = {"run-b": client}
    runtime._explicit_image_cancellations = {}

    assert runtime.pause_chat({"run_id": ""}) is False
    assert runtime.cancel_chat({"run_id": ""}) is False
    assert runtime.steer_chat({"run_id": "", "text": "wrong run"})["ok"] is False
    assert runtime.follow_up_chat({"run_id": "", "text": "wrong run"})["ok"] is False
    assert runtime.respond_chat_interaction({"run_id": "", "interaction_id": "approval"})["ok"] is False
    assert called == []


@pytest.mark.skipif(os.name != "nt", reason="Windows path-length regression")
def test_submission_store_opens_near_limit_windows_workspace(tmp_path: Path) -> None:
    filename = ".scansci-direct-chat-submissions.sqlite3"
    padding = 250 - len(str(tmp_path / filename)) - 1
    assert 0 < padding < 240
    root = tmp_path / ("x" * padding)
    store = DirectChatSubmissionStore(root / "workspace.sqlite")

    assert store.reserve("long-path", "digest", "conversation-1") == ("new", None)
    assert store.status("long-path", "conversation-1")[0] == "running"


def test_expired_terminal_is_purged_on_store_startup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    store = DirectChatSubmissionStore(workspace)
    store.reserve("expired-request", "payload-digest", "conversation-1")
    store.finish("expired-request", {
        "type": "RUN_FINISHED", "result": {"message": {"content": "expired private answer"}},
    })
    with sqlite3.connect(store.path) as connection:
        connection.execute("update direct_chat_submissions set updated_at = 1 where request_key = ?", ("expired-request",))

    reopened = DirectChatSubmissionStore(workspace)

    assert reopened.status("expired-request", "conversation-1") == ("unreplayable", None)
    assert b"expired private answer" not in reopened.path.read_bytes()


def test_abandon_missing_key_fences_a_late_first_post(tmp_path: Path) -> None:
    executions: list[str] = []

    def producer(_payload):
        executions.append("effect committed")
        yield {"type": "RUN_FINISHED", "result": {"message": {"content": "done"}}}

    workspace = tmp_path / "workspace.sqlite"
    app = _app(workspace, producer)
    payload = {"idempotency_key": "late-request", "conversation_id": "conversation-1", "messages": [{"role": "user", "content": "write"}]}

    assert app.direct_chat_submissions.abandon_missing("late-request", "conversation-1") == "abandoned"
    assert list(app.stream_chat(payload))[0]["code"] == "idempotency_conflict"
    assert executions == []

    admitted = {**payload, "idempotency_key": "already-admitted"}
    first = app.stream_chat(admitted)
    assert next(first)["type"] == "RUN_FINISHED"
    assert app.direct_chat_submissions.abandon_missing("already-admitted", "conversation-1") == "completed"
    first.close()
    assert executions == ["effect committed"]


def test_abandon_and_admission_race_has_one_winner(tmp_path: Path) -> None:
    store = DirectChatSubmissionStore(tmp_path / "workspace.sqlite")
    barrier = threading.Barrier(3)
    outcomes: dict[str, str] = {}

    def admit() -> None:
        barrier.wait()
        outcomes["admit"] = store.reserve("racing-key", "payload-digest", "conversation-1")[0]

    def abandon() -> None:
        barrier.wait()
        outcomes["abandon"] = store.abandon_missing("racing-key", "conversation-1")

    threads = [threading.Thread(target=admit), threading.Thread(target=abandon)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert (outcomes["admit"], outcomes["abandon"]) in {
        ("new", "running"),
        ("conflict", "abandoned"),
    }


def test_deleted_conversation_rejects_late_first_post(tmp_path: Path) -> None:
    executions: list[str] = []

    def producer(_payload):
        executions.append("effect committed")
        yield {"type": "RUN_FINISHED", "result": {"message": {"content": "done"}}}

    app = _app(tmp_path / "workspace.sqlite", producer)
    app.direct_chat_submissions.erase_conversation("deleted-conversation")
    payload = {
        "idempotency_key": "late-after-delete",
        "conversation_id": "deleted-conversation",
        "messages": [{"role": "user", "content": "write"}],
    }

    assert list(app.stream_chat(payload))[0]["code"] == "conversation_deleted"
    assert app.direct_chat_submissions.status("late-after-delete", "deleted-conversation") == ("erased", None)
    assert executions == []


def test_running_owner_is_only_reconciled_after_process_death(tmp_path: Path) -> None:
    store = DirectChatSubmissionStore(tmp_path / "workspace.sqlite")
    store.reserve("owner-request", "payload-digest", "conversation-1")
    assert store.status("owner-request", "conversation-1") == ("running", None)

    with sqlite3.connect(store.path) as connection:
        connection.execute("update direct_chat_submissions set owner_pid = ? where request_key = ?", (99999999, "owner-request"))

    assert store.status("owner-request", "conversation-1") == ("uncertain", None)


def test_failed_history_delete_keeps_submission_fenced_for_retry(tmp_path: Path) -> None:
    store = DirectChatSubmissionStore(tmp_path / "workspace.sqlite")
    store.reserve("retained-request", "payload-digest", "conversation-1")
    store.finish("retained-request", {"type": "RUN_FINISHED", "result": {"message": {"content": "answer"}}})

    def fail_delete() -> None:
        raise FileNotFoundError("history write failed")

    try:
        store.erase_conversation("conversation-1", fail_delete)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("delete failure was swallowed")

    assert store.status("retained-request", "conversation-1") == ("erased", None)
    assert b"answer" not in store.path.read_bytes()
    assert store.reserve("late-request", "payload-digest", "conversation-1")[0] == "erased"
    with pytest.raises(FileNotFoundError, match="history write failed"):
        store.erase_conversation("conversation-1", fail_delete)

    def already_missing() -> None:
        raise DirectConversationNotFound("Direct conversation does not exist")

    assert store.erase_conversation("conversation-1", already_missing) == {
        "ok": True, "conversation_id": "conversation-1",
    }


def test_sqlite_commit_failure_does_not_delete_history_before_fence(tmp_path: Path, monkeypatch) -> None:
    store = DirectChatSubmissionStore(tmp_path / "workspace.sqlite")
    deleted: list[str] = []
    original_connect = sqlite3.connect

    class CommitFailure:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def commit(self):
            raise sqlite3.OperationalError("simulated disk full")

        def close(self):
            self.connection.close()

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *args, **kwargs: CommitFailure(original_connect(*args, **kwargs)))
        with pytest.raises(sqlite3.OperationalError, match="simulated disk full"):
            store.erase_conversation("conversation-1", lambda: deleted.append("history deleted"))

    assert deleted == []
    assert store.status("missing-request", "conversation-1") == ("missing", None)
    assert store.erase_conversation("conversation-1", lambda: deleted.append("history deleted")) is None
    assert deleted == ["history deleted"]
