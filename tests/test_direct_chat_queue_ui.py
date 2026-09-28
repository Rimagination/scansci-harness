from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_JS = ROOT / "src" / "scansci_html" / "web" / "app.js"
INDEX_HTML = ROOT / "src" / "scansci_html" / "web" / "index.html"
STYLES_CSS = ROOT / "src" / "scansci_html" / "web" / "styles.css"


def _script() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _styles() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


def _index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _css_rule(styles: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", styles)
    assert match, f"missing CSS rule {selector}"
    return match.group(1)


def _function_body(script: str, name: str, next_name: str) -> str:
    match = re.search(
        rf"function {re.escape(name)}\b[\s\S]*?(?=\n(?:async )?function {re.escape(next_name)}\b)",
        script,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_running_reply_has_no_persistent_choice_card_before_a_follow_up_is_sent() -> None:
    script = _script()
    render = _function_body(script, "renderDirectLiveControls", "setQueuedDirectTurnMode")

    assert "if (!job.queue.length)" in render
    assert 'data-action="set-direct-input-mode"' not in render
    assert "完成后继续" not in render.split("const queueRows", 1)[0]
    assert "立即调整" not in render.split("const queueRows", 1)[0]


def test_second_message_is_queued_by_default_and_owns_its_delivery_mode() -> None:
    script = _script()
    turn_configuration = _function_body(script, "directTurnConfiguration", "clearSubmittedDirectComposer")
    submit = _function_body(script, "submitToRunningDirectChat", "beginDirectChatJob")

    assert 'deliveryMode: "follow-up"' in turn_configuration
    assert "queueDirectChatTurn(job, turn);" in submit
    assert "job.inputMode" not in submit
    assert "steerDirectChat" not in submit


def test_each_queued_message_exposes_follow_up_and_immediate_adjust_actions() -> None:
    script = _script()
    render = _function_body(script, "renderDirectLiveControls", "setQueuedDirectTurnMode")
    switch_mode = _function_body(script, "setQueuedDirectTurnMode", "removeQueuedDirectTurn")

    assert 'data-action="set-queued-direct-mode"' in render
    assert 'data-queue-mode="steer"' in render
    assert 'data-action="edit-direct-follow-up"' in render
    assert 'data-action="remove-direct-follow-up"' in render
    assert "立即" in render
    assert '<li class="direct-live-queue-item"' in render
    assert "job.queue.findIndex" in switch_mode
    assert "job.queue.splice" in switch_mode
    assert "steerDirectChat(job, turn)" in switch_mode


def test_multiple_follow_ups_remain_fifo_and_click_dispatches_per_row_mode() -> None:
    script = _script()
    queue = _function_body(script, "queueDirectChatTurn", "steerDirectChat")
    runner = _function_body(script, "runDirectChatTurn", "pauseDirectChatJob")

    assert "job.queue.push(turn)" in queue
    assert "const nextTurn = job.queue.shift()" in runner
    assert 'action === "set-queued-direct-mode"' in script
    assert "element.dataset.queueId" in script
    assert "element.dataset.queueMode" in script


def test_running_composer_button_only_pauses_when_the_input_is_empty() -> None:
    script = _script()
    send_state = _function_body(script, "composerSendControlState", "renderComposerSendButtons")
    submit = _function_body(script, "askQuestion", "formatProcessingDuration")

    assert 'byId("chatQuestionInput")?.value' in send_state
    assert 'label: "加入后续队列"' in send_state
    assert "!input.value.trim()" in submit
    assert "renderComposerSendButtons();" in script.split('document.addEventListener("input"', 1)[1]


def test_queued_messages_are_one_full_width_row_each() -> None:
    styles = _styles()
    queue = _css_rule(styles, ".direct-live-queue ol")
    row = _css_rule(styles, ".direct-live-queue-item")
    message = _css_rule(styles, ".direct-live-queue-text")
    mode_button = _css_rule(
        styles,
        '.direct-live-queue-action.is-immediate',
    )
    actions = _css_rule(styles, ".direct-live-queue-actions")

    assert "flex-direction: column" in queue
    assert "flex-wrap: nowrap" in queue
    assert "width: 100%" in queue
    assert "width: 100%" in row
    assert "min-height: 34px" in row
    assert "background: transparent" in row
    assert "min-width: 0" in message
    assert "color: var(--ink)" in mode_button
    assert "justify-content: flex-end" in actions


def test_queue_surface_is_rendered_above_the_chat_input() -> None:
    index = _index()
    assert index.index('id="chatLiveControls"') < index.index('<form class="chat-composer" id="chatAskForm">')
    assert index.index('id="chatLiveControls"') < index.index('id="chatQuestionInput"')


def test_streaming_render_preserves_queue_nodes_during_drag() -> None:
    render = _function_body(_script(), "renderDirectLiveControls", "setQueuedDirectTurnMode")

    guard = (
        "if (job && directQueueDrag.queueId "
        "&& directQueueDrag.conversationId === job.conversationId) return;"
    )
    assert guard in render
    assert render.index(guard) < render.index("surface.innerHTML")


def test_direct_processing_trace_surfaces_latest_phase_and_compacts_repeated_updates() -> None:
    script = _script()
    trace = _function_body(script, "processTraceMarkup", "reuseImagePrompt")

    assert "const trace = compactProcessTrace(message);" in trace
    assert "const latest = trace.length ? trace[trace.length - 1] : null;" in trace
    assert "recordCount" in trace
    assert '<details class="answer-processing' in trace
    assert "return unique.slice(-8);" in script


def test_workflow_process_stays_in_one_collapsed_chat_facing_surface() -> None:
    script = _script()
    render = _function_body(script, "renderRun", "taskConversationMarkup")

    assert "const summaryTitle" in render
    assert "runSummaryActionsMarkup(run)" in render
    assert '<details class="run-execution-log' in render
    assert 'state.runExecutionDetailsOpen' in render
    assert '<strong>执行详情</strong>' not in render
    assert '<section class="run-activity-message' not in render
