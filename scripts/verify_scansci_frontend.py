"""Run a deterministic browser-level regression check for ScanSci's composer.

The check substitutes only the model stream. The browser still loads ScanSci's
real server, app.js, routing preview, form event handlers and SSE parser. It
catches async submit regressions that mocked HTTP clients cannot, including an
event handler reading Event.currentTarget after an await.
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Iterator
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scansci_html.app_settings import load_settings, save_settings  # noqa: E402
from scansci_html.research_agent import ResearchAgentRuntime  # noqa: E402
from scansci_html.webapp import create_notebook_server  # noqa: E402


_PARALLEL_PROBE_LOCK = threading.Lock()
_PARALLEL_PROBE_ACTIVE = 0
_PARALLEL_PROBE_MAX_ACTIVE = 0
_STREAM_EVENT_LOCK = threading.Lock()
_STREAM_EVENTS: list[tuple[str, str]] = []


def _reset_parallel_probe() -> None:
    global _PARALLEL_PROBE_ACTIVE, _PARALLEL_PROBE_MAX_ACTIVE
    with _PARALLEL_PROBE_LOCK:
        _PARALLEL_PROBE_ACTIVE = 0
        _PARALLEL_PROBE_MAX_ACTIVE = 0


def _parallel_probe_enter() -> None:
    global _PARALLEL_PROBE_ACTIVE, _PARALLEL_PROBE_MAX_ACTIVE
    with _PARALLEL_PROBE_LOCK:
        _PARALLEL_PROBE_ACTIVE += 1
        _PARALLEL_PROBE_MAX_ACTIVE = max(_PARALLEL_PROBE_MAX_ACTIVE, _PARALLEL_PROBE_ACTIVE)


def _parallel_probe_exit() -> None:
    global _PARALLEL_PROBE_ACTIVE
    with _PARALLEL_PROBE_LOCK:
        _PARALLEL_PROBE_ACTIVE -= 1


def _parallel_probe_max_active() -> int:
    with _PARALLEL_PROBE_LOCK:
        return _PARALLEL_PROBE_MAX_ACTIVE


def _reset_stream_events() -> None:
    with _STREAM_EVENT_LOCK:
        _STREAM_EVENTS.clear()


def _record_stream_event(kind: str, question: str) -> None:
    with _STREAM_EVENT_LOCK:
        _STREAM_EVENTS.append((kind, question))


def _stream_events() -> list[tuple[str, str]]:
    with _STREAM_EVENT_LOCK:
        return list(_STREAM_EVENTS)


def _assert_follow_up_sequence(
    events: list[tuple[str, str]],
    questions: list[str],
) -> list[str]:
    expected = [event for question in questions for event in (("start", question), ("finish", question))]
    selected = [event for event in events if event[1] in set(questions)]
    if selected != expected:
        raise AssertionError(f"Follow-up queue did not execute in FIFO order: {selected}")
    return questions[1:]


def _open_new_task(page: Any) -> None:
    sidebar_button = page.locator('.sidebar-action[data-action="new-task"]')
    if sidebar_button.is_visible():
        sidebar_button.click()
        return
    page.locator('.titlebar-new-task').click()


def _open_history_conversation(page: Any, conversation_id: str) -> None:
    item = page.locator(
        f'.task-item[data-action="open-direct-conversation"][data-conversation-id="{conversation_id}"]'
    )
    if not item.is_visible():
        page.locator('[data-action="toggle-sidebar"]').click()
    item.click()


def _fake_chat_stream(_self: ResearchAgentRuntime, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    messages = list(payload.get("messages", []) or [])
    question = str(messages[-1].get("content", "")) if messages else ""
    reply = f"frontend submit regression passed: {question}"
    if question.startswith("long: "):
        reply += " This visible paragraph keeps the conversation tall enough to verify manual scrolling." * 32
    track_parallel = question.startswith("hold: explain parallel conversation ")
    if track_parallel:
        _parallel_probe_enter()
    _record_stream_event("start", question)
    try:
        yield {"type": "RUN_STARTED", "run_id": f"frontend-submit-{uuid4().hex}"}
        if question.startswith("hold: "):
            chunk_size = max(1, len(reply) // 6)
            chunk_delay = 0.5 if "deterministic follow-up queues" in question else 0.25
            for offset in range(0, len(reply), chunk_size):
                time.sleep(chunk_delay)
                yield {"type": "TEXT_MESSAGE_CONTENT", "content": reply[offset : offset + chunk_size]}
        else:
            yield {"type": "TEXT_MESSAGE_CONTENT", "content": reply}
        yield {
            "type": "RUN_FINISHED",
            "result": {
                "message": {
                    "role": "assistant",
                    "content": reply,
                    "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
                },
                "model": {"provider_id": "scansci-managed", "model_id": "glm-4.7-flash"},
            },
        }
    finally:
        _record_stream_event("finish", question)
        if track_parallel:
            _parallel_probe_exit()


def _fake_transcribe_audio(_self: ResearchAgentRuntime, _payload: dict[str, Any]) -> dict[str, Any]:
    # Keep the processing state visible long enough for the browser assertion
    # to observe it.  The browser still performs the real record/stop, WebM to
    # WAV conversion, request serialization, response parsing and text insert.
    time.sleep(0.35)
    return {
        "transcripts": [{"name": "recording.wav", "text": "语音验收文本"}],
        "attachments": [],
        "model_id": "fixture-asr",
    }


def _launch_chromium(chromium: Any) -> Any:
    """Prefer Playwright's pinned browser, then use an installed stable channel."""

    try:
        return chromium.launch(headless=True)
    except Exception as error:  # Playwright's Error type is imported lazily in verify().
        if "Executable doesn't exist" not in str(error):
            raise
        managed_error = error
    for channel in ("chrome", "msedge"):
        try:
            return chromium.launch(headless=True, channel=channel)
        except Exception as error:
            marker = f"distribution '{channel}' is not found"
            if marker not in str(error).lower():
                raise
            managed_error = error
    raise RuntimeError(
        "Playwright browser is unavailable; install Chromium or provide Chrome/Microsoft Edge."
    ) from managed_error


def verify() -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:  # pragma: no cover - release machines must provide this dependency.
        raise RuntimeError("Playwright Python is required for the release browser regression check") from error

    original_stream = ResearchAgentRuntime.chat_stream
    original_transcribe_audio = ResearchAgentRuntime.transcribe_audio
    ResearchAgentRuntime.chat_stream = _fake_chat_stream
    ResearchAgentRuntime.transcribe_audio = _fake_transcribe_audio
    server = None
    thread = None
    try:
        with TemporaryDirectory(prefix="scansci-frontend-release-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace.sqlite"
            settings = load_settings(workspace)
            settings.setdefault("onboarding", {})["welcome_dismissed"] = True
            save_settings(workspace, settings)
            server = create_notebook_server(workspace=workspace, evidence_db=root / "evidence.sqlite")
            thread = threading.Thread(target=server.serve_forever, daemon=True, name="scansci-frontend-release")
            thread.start()
            with sync_playwright() as playwright:
                browser = _launch_chromium(playwright.chromium)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.add_init_script(
                    """
                    (() => {
                      const fakeTrack = { stop() {} };
                      Object.defineProperty(navigator, "mediaDevices", {
                        configurable: true,
                        value: { getUserMedia: async () => ({ getTracks: () => [fakeTrack] }) },
                      });
                      class ScanSciFakeMediaRecorder extends EventTarget {
                        static isTypeSupported() { return true; }
                        constructor(stream, options = {}) {
                          super();
                          this.stream = stream;
                          this.mimeType = options.mimeType || "audio/webm";
                          this.state = "inactive";
                        }
                        start() { this.state = "recording"; }
                        stop() {
                          this.state = "inactive";
                          const chunk = new Blob([new Uint8Array([1, 2, 3, 4])], { type: this.mimeType });
                          const dataEvent = new Event("dataavailable");
                          Object.defineProperty(dataEvent, "data", { value: chunk });
                          this.dispatchEvent(dataEvent);
                          this.dispatchEvent(new Event("stop"));
                        }
                      }
                      class ScanSciFakeAudioContext {
                        async decodeAudioData() {
                          return {
                            length: 1600,
                            numberOfChannels: 1,
                            sampleRate: 16000,
                            duration: 0.1,
                            getChannelData: () => new Float32Array(1600),
                          };
                        }
                        async close() {}
                      }
                      Object.defineProperty(globalThis, "MediaRecorder", {
                        configurable: true,
                        value: ScanSciFakeMediaRecorder,
                      });
                      Object.defineProperty(globalThis, "AudioContext", {
                        configurable: true,
                        value: ScanSciFakeAudioContext,
                      });
                      Object.defineProperty(globalThis, "webkitAudioContext", {
                        configurable: true,
                        value: undefined,
                      });
                      Object.defineProperty(globalThis, "OfflineAudioContext", {
                        configurable: true,
                        value: undefined,
                      });
                      Object.defineProperty(globalThis, "webkitOfflineAudioContext", {
                        configurable: true,
                        value: undefined,
                      });
                    })();
                    """
                )
                console_errors: list[str] = []
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                page.locator("#homeQuestionInput").wait_for(state="visible", timeout=8_000)

                # Fresh temporary workspaces may show onboarding. Dismiss it only
                # after its async content is visible. The empty host can briefly
                # participate in layout while settings are still loading.
                onboarding = page.locator("#resourceOnboarding")
                skip_onboarding = page.locator('[data-action="skip-resource-onboarding"]')
                for _ in range(50):
                    if skip_onboarding.count() and skip_onboarding.is_visible():
                        skip_onboarding.click()
                        onboarding.locator('[role="dialog"]').wait_for(state="detached", timeout=3_000)
                        break
                    page.wait_for_timeout(100)

                record_button = page.locator('#homeAskForm [data-action="toggle-composer-recording"]')
                record_button.click()
                recording_button = page.locator(
                    '#homeAskForm [data-action="toggle-composer-recording"].is-recording'
                )
                processing_button = page.locator(
                    '#homeAskForm [data-action="toggle-composer-recording"].is-processing'
                )
                recording_button.wait_for(state="visible", timeout=3_000)
                record_button.click()
                processing_button.wait_for(state="visible", timeout=3_000)
                for _ in range(50):
                    if "语音验收文本" in page.locator("#homeQuestionInput").input_value():
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError("Microphone transcription did not enter the composer as text")
                processing_button.wait_for(state="detached", timeout=3_000)
                if page.locator("#homeAudioAttachments .composer-audio-card").count():
                    raise AssertionError("Microphone transcription incorrectly remained as an audio attachment")
                page.locator("#homeQuestionInput").fill("")

                click_question = "explain knowledge graph RAG"
                page.locator("#homeQuestionInput").fill(click_question)
                page.locator("#homeAskForm button[type=submit]").click()
                page.get_by_text(f"frontend submit regression passed: {click_question}").wait_for(timeout=8_000)
                conversation = page.locator('section[data-view="conversation"]')
                if not conversation.is_visible():
                    raise AssertionError("Click submission did not transition the application into the conversation view")
                if page.locator("#chatQuestionInput").input_value() != "":
                    raise AssertionError("The click-submitted text remained in the chat composer")

                enter_question = "explain evidence citation anchors"
                page.locator("#chatQuestionInput").fill(enter_question)
                page.locator("#chatQuestionInput").press("Enter")
                page.get_by_text(f"frontend submit regression passed: {enter_question}").wait_for(timeout=8_000)
                if page.locator("#chatQuestionInput").input_value() != "":
                    raise AssertionError("The Enter-submitted text remained in the chat composer")

                queue_parent = "hold: explain deterministic follow-up queues"
                queue_child = "summarize the first queued turn"
                queue_child_two = "summarize the second queued turn"
                _reset_stream_events()
                page.locator("#chatQuestionInput").fill(queue_parent)
                page.locator("#chatAskForm button[type=submit]").click()
                page.locator(
                    '#chatAskForm button[type=submit][data-composer-state="running"]'
                ).wait_for(state="visible", timeout=8_000)
                page.locator("#chatQuestionInput").fill(queue_child)
                page.locator("#chatAskForm button[type=submit]").click()
                page.locator("#chatLiveControls").wait_for(state="visible", timeout=3_000)
                page.locator(".direct-live-queue").get_by_text(queue_child, exact=True).wait_for(timeout=3_000)
                page.locator("#chatQuestionInput").fill(queue_child_two)
                page.locator("#chatAskForm button[type=submit]").click()
                queue = page.locator(".direct-live-queue")
                queue.get_by_text(queue_child_two, exact=True).wait_for(timeout=3_000)
                queue_style = queue.evaluate(
                    """queue => {
                      const row = queue.querySelector('.direct-live-queue-item');
                      const controls = queue.closest('.direct-live-controls');
                      return {
                        controlsBackground: getComputedStyle(controls).backgroundColor,
                        rowBackground: getComputedStyle(row).backgroundColor,
                        rowHeight: row.getBoundingClientRect().height,
                        nestedWhite: [...row.querySelectorAll('*')]
                          .some(node => getComputedStyle(node).backgroundColor === 'rgb(255, 255, 255)'),
                      };
                    }"""
                )
                if queue_style["controlsBackground"] == "rgb(255, 255, 255)":
                    raise AssertionError(f"Follow-up queue did not use a neutral gray surface: {queue_style}")
                if queue_style["rowBackground"] not in {"rgba(0, 0, 0, 0)", "transparent"} or queue_style["nestedWhite"]:
                    raise AssertionError(f"Follow-up queue still contains a nested white card: {queue_style}")
                if float(queue_style["rowHeight"]) > 38:
                    raise AssertionError(f"Follow-up queue row is taller than 38px: {queue_style}")

                queue_rows = page.locator(".direct-live-queue-item")
                queue_rows.nth(1).locator("[data-direct-queue-drag]").drag_to(
                    queue_rows.nth(0),
                    target_position={"x": 8, "y": 2},
                )
                dragged_order = [text.strip() for text in queue.locator(".direct-live-queue-text").all_text_contents()]
                if dragged_order != [queue_child_two, queue_child]:
                    raise AssertionError(f"Follow-up queue drag did not persist the new order: {dragged_order}")

                page.get_by_text(f"frontend submit regression passed: {queue_child_two}", exact=True).wait_for(
                    timeout=10_000
                )
                page.get_by_text(f"frontend submit regression passed: {queue_child}", exact=True).wait_for(timeout=10_000)
                follow_up_order = _assert_follow_up_sequence(
                    _stream_events(),
                    [queue_parent, queue_child_two, queue_child],
                )

                title_before_edit = page.locator("#conversationTitle").inner_text().strip()
                last_user_turn = page.locator(".conversation-message.user-turn").last
                action_order = last_user_turn.locator("[data-action]").evaluate_all(
                    "nodes => nodes.map(node => node.dataset.action)"
                )
                if action_order != ["copy-conversation-message", "edit-last-conversation-message"]:
                    raise AssertionError(f"Last-message edit button is not immediately after copy: {action_order}")
                edited_question = "edited: summarize the reordered follow-up"
                last_user_turn.locator('[data-action="edit-last-conversation-message"]').click()
                edit_input = page.locator("[data-last-message-edit-input]")
                edit_input.wait_for(state="visible", timeout=3_000)
                edit_input.fill(edited_question)
                page.locator('[data-action="save-last-message-edit"]').click()
                page.get_by_text(f"frontend submit regression passed: {edited_question}", exact=True).wait_for(
                    timeout=10_000
                )
                user_turns = [text.strip() for text in page.locator(".user-turn-bubble > p").all_text_contents()]
                if user_turns[-1:] != [edited_question] or queue_child in user_turns:
                    raise AssertionError(f"Editing the last turn did not replace its superseded branch: {user_turns}")
                if page.locator("#conversationTitle").inner_text().strip() != title_before_edit:
                    raise AssertionError("Editing the last turn changed the first-turn conversation title")

                active_conversation = page.locator(
                    '.task-item.is-active[data-action="open-direct-conversation"]'
                )
                conversation_id = active_conversation.get_attribute("data-conversation-id")
                if not conversation_id:
                    raise AssertionError("Completed direct conversation did not receive a persistent id")
                _open_new_task(page)
                _open_history_conversation(page, conversation_id)
                page.get_by_text(edited_question, exact=True).wait_for(timeout=5_000)
                page.reload(wait_until="networkidle")
                page.get_by_text(edited_question, exact=True).wait_for(timeout=8_000)
                if page.locator("#conversationTitle").inner_text().strip() != title_before_edit:
                    raise AssertionError("Reloading the edited conversation changed its stable title")

                scroll_question = "long: verify the independent jump-to-latest control"
                page.locator("#chatQuestionInput").fill(scroll_question)
                page.locator("#chatAskForm button[type=submit]").click()
                page.locator(".assistant-turn .answer-sentence").filter(
                    has_text=f"frontend submit regression passed: {scroll_question}"
                ).wait_for(timeout=10_000)
                answer_area = page.locator("#answerArea")
                answer_area.evaluate("area => { area.scrollTop = 0; area.dispatchEvent(new Event('scroll')); }")
                jump_button = page.locator("#conversationJumpLatest")
                jump_button.wait_for(state="visible", timeout=3_000)
                jump_layout = jump_button.evaluate(
                    """button => {
                      const form = document.querySelector('#chatAskForm');
                      const buttonBox = button.getBoundingClientRect();
                      const formBox = form.getBoundingClientRect();
                      return {
                        outsideForm: !button.closest('form'),
                        anchoredAboveComposer: buttonBox.top < formBox.top
                          && buttonBox.top + buttonBox.height / 2 <= formBox.top + 2,
                      };
                    }"""
                )
                if not jump_layout["outsideForm"] or not jump_layout["anchoredAboveComposer"]:
                    raise AssertionError(f"Jump-to-latest control is still embedded in the composer: {jump_layout}")
                jump_button.click()
                for _ in range(20):
                    at_bottom = answer_area.evaluate(
                        "area => area.scrollHeight - area.scrollTop - area.clientHeight < 16"
                    )
                    if at_bottom:
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError("Jump-to-latest control did not reach the conversation bottom")

                parallel_a = "hold: explain parallel conversation A"
                parallel_b = "hold: explain parallel conversation B"
                _reset_parallel_probe()
                page.locator("#chatQuestionInput").fill(parallel_a)
                page.locator("#chatAskForm button[type=submit]").click()
                page.locator(
                    '#chatAskForm button[type=submit][data-composer-state="running"]'
                ).wait_for(state="visible", timeout=8_000)
                _open_new_task(page)
                page.locator("#homeQuestionInput").fill(parallel_b)
                page.locator("#homeAskForm button[type=submit]").click()
                page.locator(
                    '#chatAskForm button[type=submit][data-composer-state="running"]'
                ).wait_for(state="visible", timeout=8_000)
                for _ in range(30):
                    if page.locator(".task-status.running").count() >= 2:
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError("Direct chat jobs were still serialized across conversations")
                page.get_by_text(f"frontend submit regression passed: {parallel_b}", exact=True).wait_for(timeout=10_000)
                parallel_max_active = _parallel_probe_max_active()
                if parallel_max_active < 2:
                    raise AssertionError("Direct chat model streams did not overlap")
                if any("currentTarget" in item or "querySelector" in item for item in console_errors):
                    raise AssertionError(f"Composer emitted a stale-event console error: {console_errors}")
                return {
                    "click_submit": True,
                    "enter_submit": True,
                    "follow_up_queue": True,
                    "follow_up_order": follow_up_order,
                    "follow_up_drag": dragged_order,
                    "queue_style": queue_style,
                    "last_turn_edit": True,
                    "conversation_persistence": True,
                    "stable_title": title_before_edit,
                    "independent_jump_to_latest": True,
                    "parallel_conversations": True,
                    "parallel_max_active": parallel_max_active,
                    "voice_to_text": True,
                    "conversation_visible": True,
                    "console_errors": console_errors,
                }
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)
        # Some sqlite connections are opened with ``with sqlite3.connect(...)``
        # (transaction-only, not closing).  A reference cycle can keep the
        # handle alive until the cyclic collector runs; force it before the
        # TemporaryDirectory cleanup so Windows can delete the workspace file
        # and the gate never fails on a stale file lock.
        gc.collect()
        ResearchAgentRuntime.chat_stream = original_stream
        ResearchAgentRuntime.transcribe_audio = original_transcribe_audio


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ScanSci's real browser composer submission flow.")
    parser.add_argument("--output", required=True, help="Write the JSON verification report here.")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result = verify()
    output.write_text(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
