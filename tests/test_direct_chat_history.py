from __future__ import annotations

import json
from pathlib import Path

from scansci_html.direct_chat_history import DirectChatHistoryStore


def _messages(turns: int) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for index in range(1, turns + 1):
        messages.extend(
            [
                {
                    "message_id": f"user-{index}",
                    "role": "user",
                    "content": f"问题 {index}",
                    "created_at": f"2026-08-31T00:{index:02d}:00+00:00",
                },
                {
                    "message_id": f"assistant-{index}",
                    "parent_message_id": f"user-{index}",
                    "role": "assistant",
                    "content": f"回答 {index}",
                    "created_at": f"2026-08-31T00:{index:02d}:30+00:00",
                },
            ]
        )
    return messages


def test_direct_history_restores_only_bounded_image_model_ids(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    store = DirectChatHistoryStore(workspace)
    selection = {"provider_id": "paratera", "model_id": "GLM-CogView3-Flash"}
    store.upsert({
        "conversation_id": "image-retry",
        "messages": [
            {
                "message_id": "image-turn", "role": "user", "content": "生成科研示意图",
                "image_model": {
                    **selection, "base_url": "https://private-image.example/v1",
                    "api_key": "must-not-persist", "headers": {"Authorization": "hidden-token"},
                },
            },
            {
                "message_id": "bounded-turn", "role": "user", "content": "长度边界",
                "image_model": {"provider_id": "p" * 200, "model_id": "m" * 400},
            },
            {"message_id": "text-turn", "role": "user", "content": "普通聊天"},
        ],
    })

    restored = DirectChatHistoryStore(workspace).get("image-retry")["messages"]

    assert restored[0]["image_model"] == selection
    assert restored[0]["message_id"] == "image-turn"
    assert restored[1]["image_model"] == {"provider_id": "p" * 160, "model_id": "m" * 300}
    assert "image_model" not in restored[2]
    persisted = store.path.read_text(encoding="utf-8")
    for excluded in ("private-image.example", "must-not-persist", "Authorization", "hidden-token"):
        assert excluded not in persisted


def test_direct_history_keeps_twenty_turns_and_appends_session_events(tmp_path: Path) -> None:
    store = DirectChatHistoryStore(tmp_path / "workspace.sqlite")
    saved = store.upsert(
        {
            "conversation_id": "long-conversation",
            "title": "持续多轮问答",
            "title_source": "model",
            "session_id": "pi-session-1",
            "status": "completed",
            "messages": _messages(20),
        }
    )

    assert len(saved["messages"]) == 40
    assert saved["revision"] == 1
    assert saved["messages"][0]["message_id"] == "user-1"
    assert saved["messages"][-1]["message_id"] == "assistant-20"
    event_types = [event["type"] for event in saved["events"]]
    assert event_types[0] == "conversation.created"
    assert event_types.count("message.appended") == 40
    assert "session.bound" in event_types

    updated = store.upsert(
        {
            "conversation_id": "long-conversation",
            "title": "不应覆盖首轮标题",
            "session_id": "pi-session-1",
            "status": "completed",
            "messages": _messages(21),
        }
    )

    assert updated["title"] == "持续多轮问答"
    assert updated["revision"] == 2
    assert len(updated["messages"]) == 42
    assert [event["type"] for event in updated["events"]].count("message.appended") == 42
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 2


def test_direct_history_keeps_generated_image_reference(tmp_path: Path) -> None:
    store = DirectChatHistoryStore(tmp_path / "workspace.sqlite")
    saved = store.upsert({
        "conversation_id": "image-conversation",
        "messages": [{
            "message_id": "assistant-image",
            "role": "assistant",
            "content": "图片已生成。",
            "artifacts": [{
                "artifact_type": "image",
                "title": "光合作用机制",
                "file_path": str(tmp_path / "artifacts" / "images" / "photo.png"),
                "provider_name": "并行智算云（Paratera）",
                "model_id": "GLM-CogView3-Flash",
                "asset_url": "/api/generated-images/photo.png",
            }],
        }],
    })

    assert saved["messages"][0]["artifacts"][0]["asset_url"] == "/api/generated-images/photo.png"


def test_direct_history_persists_current_turn_queue_and_recovery_status(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    store = DirectChatHistoryStore(workspace)
    current_turn = {
        "queueId": "turn-current",
        "question": "先解释第一点",
        "directChatMode": "general",
        "createdAt": "2026-08-31T08:00:00+00:00",
    }
    queued_turn = {
        "queueId": "turn-next",
        "question": "再给一个例子",
        "deliveryMode": "follow-up",
        "createdAt": "2026-08-31T08:00:01+00:00",
        "images": [{
            "id": "image-0123456789abcdef0123456789abcdef",
            "name": "figure.png",
            "data_url": "data:image/png;base64,discard-me",
        }],
        "audio": [{
            "id": "audio-0123456789abcdef0123456789abcdef",
            "name": "note.wav",
            "data_url": "data:audio/wav;base64,discard-me",
        }],
        "sourceFiles": [{
            "job_id": "ing-0123456789abcdef0123456789abcdef",
            "source_id": "source-01",
            "name": "notes.txt",
            "data_url": "data:text/plain;base64,discard-me",
        }],
    }
    store.upsert(
        {
            "conversation_id": "recoverable-conversation",
            "messages": [_messages(1)[0]],
            "status": "running",
            "current_turn": current_turn,
            "queue": [queued_turn],
        }
    )

    reopened = DirectChatHistoryStore(workspace).get("recoverable-conversation")

    assert reopened["status"] == "running"
    assert reopened["current_turn"]["queueId"] == "turn-current"
    assert reopened["queue"][0]["images"][0]["id"].startswith("image-")
    assert reopened["queue"][0]["audio"][0]["id"].startswith("audio-")
    assert reopened["queue"][0]["sourceFiles"][0]["job_id"].startswith("ing-")
    assert "data_url" not in json.dumps(reopened["queue"], ensure_ascii=False)
    assert reopened["queue_count"] == 1
    assert "queue.updated" in [event["type"] for event in reopened["events"]]
    assert "status.changed" in [event["type"] for event in reopened["events"]]


def test_editing_last_message_creates_one_durable_branch(tmp_path: Path) -> None:
    store = DirectChatHistoryStore(tmp_path / "workspace.sqlite")
    original = _messages(2)
    store.upsert(
        {
            "conversation_id": "branched-conversation",
            "title": "原始问题",
            "session_id": "pi-session-old",
            "messages": original,
            "status": "completed",
        }
    )
    replacement = [
        *original[:2],
        {
            "message_id": "user-2-edited",
            "role": "user",
            "content": "修改后的第二个问题",
            "created_at": "2026-08-31T09:00:00+00:00",
        },
        {
            "message_id": "assistant-2-edited",
            "parent_message_id": "user-2-edited",
            "role": "assistant",
            "content": "修改后的回答",
            "created_at": "2026-08-31T09:00:01+00:00",
        },
    ]
    branch_payload = {
        "conversation_id": "branched-conversation",
        "messages": replacement,
        "session_id": "pi-session-new",
        "status": "completed",
        "branch_id": "branch-edit-2",
        "branch_from_message_id": "user-2",
        "branch_reason": "edit_last_user_message",
    }

    edited = store.upsert(branch_payload)
    repeated = store.upsert(branch_payload)

    assert edited["messages"][-2]["content"] == "修改后的第二个问题"
    assert len(edited["branches"]) == 1
    branch = edited["branches"][0]
    assert branch["branch_id"] == "branch-edit-2"
    assert branch["from_message_id"] == "user-2"
    assert branch["base_revision"] == 1
    assert branch["messages"] == original
    assert len(repeated["branches"]) == 1
    assert [event["type"] for event in repeated["events"]].count("branch.created") == 1


def test_version_one_history_is_migrated_without_losing_messages(tmp_path: Path) -> None:
    store = DirectChatHistoryStore(tmp_path / "workspace.sqlite")
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "conversations": [
                    {
                        "kind": "direct",
                        "conversation_id": "legacy-conversation",
                        "title": "旧对话",
                        "created_at": "2026-08-30T00:00:00+00:00",
                        "updated_at": "2026-08-30T00:00:00+00:00",
                        "session_id": "legacy-session",
                        "messages": [
                            {"role": "user", "content": "旧问题"},
                            {"role": "assistant", "content": "旧回答"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    legacy = store.get("legacy-conversation")
    assert [message["content"] for message in legacy["messages"]] == ["旧问题", "旧回答"]
    assert all(message["message_id"] for message in legacy["messages"])
    assert legacy["revision"] == 0
    assert legacy["queue"] == []

    migrated = store.upsert(
        {
            "conversation_id": "legacy-conversation",
            "messages": [
                *legacy["messages"],
                {"role": "user", "content": "继续追问"},
            ],
            "status": "running",
        }
    )

    assert migrated["title"] == "旧对话"
    assert len(migrated["messages"]) == 3
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 2


def test_malformed_state_counters_do_not_prevent_history_recovery(tmp_path: Path) -> None:
    store = DirectChatHistoryStore(tmp_path / "workspace.sqlite")
    store.path.write_text(
        json.dumps(
            {
                "version": 2,
                "conversations": [
                    {
                        "conversation_id": "recover-corrupt-counters",
                        "title": "仍可恢复",
                        "messages": [{"role": "user", "content": "你好"}],
                        "revision": {"invalid": True},
                        "event_sequence": "not-a-number",
                        "events": [
                            {"sequence": "broken", "type": "message.appended"},
                            {"sequence": 2, "type": "snapshot.committed", "payload": {}},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    recovered = store.get("recover-corrupt-counters")
    updated = store.upsert(
        {
            "conversation_id": "recover-corrupt-counters",
            "messages": [*recovered["messages"], {"role": "assistant", "content": "你好！"}],
        }
    )

    assert recovered["revision"] == 0
    assert recovered["event_sequence"] == 2
    assert [event["sequence"] for event in recovered["events"]] == [2]
    assert updated["revision"] == 1
    assert updated["event_sequence"] > 2


def test_release_gate_runs_direct_chat_state_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "config" / "release-gate.json").read_text(encoding="utf-8"))
    targeted = next(item for item in contract["targeted_commands"] if item["id"] == "targeted-python")

    assert "tests/test_direct_chat_history.py" in targeted["command"]
    assert "tests/test_direct_chat_recovery_ui.py" in targeted["command"]
