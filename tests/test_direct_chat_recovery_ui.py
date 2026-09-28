from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _script() -> str:
    return (ROOT / "src" / "scansci_html" / "web" / "app.js").read_text(encoding="utf-8")


def _function_body(script: str, start: str, end: str) -> str:
    return script.split(f"function {start}", 1)[1].split(f"function {end}", 1)[0]


def test_direct_chat_does_not_truncate_multi_turn_context_in_the_browser() -> None:
    script = _script()
    begin = _function_body(script, "beginDirectChatJob", "runDirectChatTurn")
    run = _function_body(script, "runDirectChatTurn", "pauseDirectChatJob")

    assert ".slice(-16)" not in begin
    assert ".slice(-16)" not in run
    assert "message_id: turn.queueId" in run
    assert "parent_message_id" in run


def test_direct_chat_job_snapshots_include_recoverable_queue_state() -> None:
    script = _script()
    persist = _function_body(script, "persistDirectChatJob", "generateDirectConversationTitle")

    assert "queue: job.queue" in persist
    assert "status: job.status" in persist
    assert "currentTurn: job.currentTurn" in persist
    assert "branchId: job.branchId" in persist
    assert "job.persistChain" in persist
    assert "function restoreDirectChatJob" in script
    assert "restoreDirectChatJob(conversation)" in script


def test_direct_chat_stages_attachments_before_starting_or_queueing_a_turn() -> None:
    script = _script()
    submit = script.index("await stageDirectTurnAttachments(directTurn)")

    assert 'request("/api/chat/attachments/stage"' in script
    assert submit < script.index("submitToRunningDirectChat(activeConversationJob, directTurn, input)")
    assert submit < script.index("beginDirectChatJob(directTurn)")


def test_history_list_preserves_persisted_recovery_status() -> None:
    script = _script()
    render = _function_body(script, "renderTasks", "renderHistoryControls")
    upsert = _function_body(script, "upsertDirectConversation", "persistDirectConversationSnapshot")

    assert "record.status" in render
    assert "record.queue_count" in render
    assert '"待恢复"' in render
    assert "status: String(record.status" in upsert
    assert "queue_count: Math.max" in upsert


def test_queue_mutations_are_saved_and_last_message_edit_declares_a_branch() -> None:
    script = _script()
    queue_edit = _function_body(script, "saveEditingQueuedDirectTurn", "steerDirectChat")
    queue_remove = _function_body(script, "removeQueuedDirectTurn", "updateProcessingTimers")
    message_edit = _function_body(script, "saveEditingLastConversationMessage", "renderAssistantMessageContent")

    assert "persistDirectChatJob(job)" in queue_edit
    assert "persistDirectChatJob(job)" in queue_remove
    assert "branchFromMessageId" in message_edit
    assert "branchReason" in message_edit


def test_retry_uses_a_new_session_and_preserves_the_failed_attempt_as_a_branch() -> None:
    script = _script()
    retry = _function_body(script, "retryDirectMessage", "renderDirectConversation")

    assert 'turn.branchReason = "retry_failed_assistant"' in retry
    assert "state.sessionId = null" in retry
    assert "beginDirectChatJob(turn)" in retry
    assert "input.form?.requestSubmit()" not in retry
