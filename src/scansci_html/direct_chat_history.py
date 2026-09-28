"""Durable history for ScanSci's lightweight direct conversations.

Direct chats are intentionally not research runs: they do not have stages,
artifacts, or a worker lifecycle.  They still need a small local history store
so that a completed answer survives navigation and application restarts.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4


_MAX_CONVERSATIONS = 200
_MAX_MESSAGES = 128
_MAX_QUEUE_ITEMS = 64
_MAX_BRANCHES = 24
_MAX_EVENTS = 1024
_MAX_TEXT_LENGTH = 200_000
_MAX_LIST_ITEMS = 100
_MAX_DICT_ITEMS = 120
_STATUSES = {
    "starting",
    "running",
    "retrying",
    "queued",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = _MAX_TEXT_LENGTH) -> str:
    return str(value or "").strip()[:limit]


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _stable_id(*parts: Any) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return sha256(material.encode("utf-8", errors="replace")).hexdigest()[:24]


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    """Copy JSON-like UI metadata without retaining pasted image payloads."""

    if depth > 6:
        return "…"
    if isinstance(value, str):
        return value[:_MAX_TEXT_LENGTH]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [_bounded_json(item, depth=depth + 1) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_DICT_ITEMS]:
            name = str(key)
            if name in {"data_url", "preview_url", "base64", "content_bytes"}:
                continue
            result[name] = _bounded_json(item, depth=depth + 1)
        return result
    return str(value)[:_MAX_TEXT_LENGTH]


def _message(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    role = str(raw.get("role", "") or "").strip().lower()
    if role not in {"user", "assistant"}:
        return None
    result: dict[str, Any] = {
        "role": role,
        "content": _text(raw.get("content", "")),
    }
    for key in (
        "message_id",
        "parent_message_id",
        "branch_id",
        "created_at",
        "mode",
        "processing_ms",
        "usage",
        "model",
        "knowledgeScopes",
        "reader_answer",
        "trace",
        "interaction",
        "error",
        "failure",
        "paused",
    ):
        if key in raw and raw[key] is not None:
            result[key] = _bounded_json(raw[key])
    if isinstance(raw.get("image_model"), dict):
        result["image_model"] = {
            key: _text(raw["image_model"][key], limit)
            for key, limit in (("provider_id", 160), ("model_id", 300))
            if isinstance(raw["image_model"].get(key), str)
        }
    if isinstance(raw.get("skills"), list):
        result["skills"] = _bounded_json(raw["skills"])
    if isinstance(raw.get("sources"), list):
        result["sources"] = _bounded_json(raw["sources"])
    if isinstance(raw.get("images"), list):
        # Keep the durable attachment reference for history previews, but not
        # the potentially multi-megabyte image data URL.  Older versions only
        # kept name/type/size, which made the thumbnail disappear after reload.
        result["images"] = [
            {
                key: _bounded_json(item.get(key))
                for key in ("id", "name", "mime_type", "type", "size", "preview_url")
                if isinstance(item, dict) and item.get(key) is not None
            }
            for item in raw["images"][:_MAX_LIST_ITEMS]
            if isinstance(item, dict)
        ]
    if isinstance(raw.get("audio"), list):
        # Audio bytes are stored outside the history file.  Keep only enough
        # metadata to explain what was attached when the conversation is
        # reopened.
        result["audio"] = [
            {
                key: _bounded_json(item.get(key))
                for key in ("id", "name", "mime_type", "type", "size", "audio_url")
                if isinstance(item, dict) and item.get(key) is not None
            }
            for item in raw["audio"][:_MAX_LIST_ITEMS]
            if isinstance(item, dict)
        ]
    if isinstance(raw.get("artifacts"), list):
        result["artifacts"] = [
            {
                key: _bounded_json(item.get(key))
                for key in (
                    "artifact_type",
                    "title",
                    "file_path",
                    "size_bytes",
                    "provider_id",
                    "provider_name",
                    "model_id",
                    "size",
                    "mime_type",
                    "asset_url",
                )
                if isinstance(item, dict) and item.get(key) is not None
            }
            for item in raw["artifacts"][:_MAX_LIST_ITEMS]
            if isinstance(item, dict) and item.get("artifact_type") == "image"
        ]
    return result


def _messages(
    raw_messages: Any,
    *,
    conversation_id: str,
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_messages, list):
        return []
    prior = previous or []
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_messages[-_MAX_MESSAGES:]):
        item = _message(raw)
        if item is None:
            continue
        message_id = _text(item.get("message_id"), 160)
        prior_item = prior[index] if index < len(prior) else None
        if not message_id and isinstance(prior_item, dict):
            same_message = (
                str(prior_item.get("role", "")) == item["role"]
                and str(prior_item.get("content", "")) == item["content"]
                and str(prior_item.get("created_at", "")) == str(item.get("created_at", ""))
            )
            if same_message:
                message_id = _text(prior_item.get("message_id"), 160)
        if not message_id:
            message_id = f"message-{_stable_id(conversation_id, index, item['role'], item['content'], item.get('created_at', ''))}"
        if message_id in seen_ids:
            message_id = f"{message_id}-{index + 1}"
        seen_ids.add(message_id)
        item["message_id"] = message_id
        normalized.append(item)
    return normalized


def _queue_turn(raw: Any, *, conversation_id: str, index: int = 0) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    question = _text(raw.get("question"))
    if not question:
        return None
    result = _bounded_json(raw)
    if not isinstance(result, dict):
        return None
    result["question"] = question
    result["queueId"] = _text(result.get("queueId"), 160) or (
        f"turn-{_stable_id(conversation_id, index, question, result.get('createdAt', ''))}"
    )
    return result


def _queue(raw_queue: Any, *, conversation_id: str) -> list[dict[str, Any]]:
    if not isinstance(raw_queue, list):
        return []
    return [
        item
        for index, raw in enumerate(raw_queue[:_MAX_QUEUE_ITEMS])
        if (item := _queue_turn(raw, conversation_id=conversation_id, index=index)) is not None
    ]


def _status(value: Any) -> str:
    normalized = _text(value, 40).lower()
    return normalized if normalized in _STATUSES else "completed"


def _event(
    sequence: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or _now()
    return {
        "event_id": f"event-{uuid4().hex}",
        "sequence": sequence,
        "type": event_type,
        "created_at": timestamp,
        "payload": _bounded_json(payload or {}),
    }


class DirectChatHistoryStore:
    """A bounded, atomic JSON store scoped to the current ScanSci workspace."""

    def __init__(self, workspace: str | Path) -> None:
        workspace_path = Path(workspace).expanduser().resolve()
        self.path = workspace_path.parent / ".scansci-direct-chat-history.json"
        self._lock = threading.RLock()

    @staticmethod
    def _upgrade_row(raw: dict[str, Any]) -> dict[str, Any]:
        row = deepcopy(raw)
        conversation_id = _text(row.get("conversation_id"), 160)
        messages = _messages(row.get("messages", []), conversation_id=conversation_id)
        queue = _queue(row.get("queue", []), conversation_id=conversation_id)
        current_turn = _queue_turn(
            row.get("current_turn"),
            conversation_id=conversation_id,
        )
        events = [
            deepcopy(item)
            for item in row.get("events", [])[-_MAX_EVENTS:]
            if isinstance(item, dict)
            and _integer(item.get("sequence")) > 0
            and _text(item.get("type"), 100)
        ] if isinstance(row.get("events"), list) else []
        branches = [
            deepcopy(item)
            for item in row.get("branches", [])[-_MAX_BRANCHES:]
            if isinstance(item, dict) and _text(item.get("branch_id"), 160)
        ] if isinstance(row.get("branches"), list) else []
        row.update(
            {
                "kind": "direct",
                "conversation_id": conversation_id,
                "title": _text(row.get("title"), 120) or "直接对话",
                "title_source": _text(row.get("title_source"), 40),
                "created_at": str(row.get("created_at", "") or ""),
                "updated_at": str(row.get("updated_at", "") or ""),
                "session_id": _text(row.get("session_id"), 200),
                "messages": messages,
                "queue": queue,
                "queue_count": len(queue),
                "current_turn": current_turn,
                "status": _status(row.get("status")),
                "revision": max(0, _integer(row.get("revision"))),
                "event_sequence": max(
                    _integer(row.get("event_sequence")),
                    max((_integer(item.get("sequence")) for item in events), default=0),
                ),
                "events": events,
                "branches": branches,
                "archived": bool(row.get("archived", False)),
            }
        )
        return row

    @staticmethod
    def _append_event(
        row: dict[str, Any],
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        created_at: str | None = None,
    ) -> None:
        sequence = _integer(row.get("event_sequence")) + 1
        events = row.get("events", []) if isinstance(row.get("events"), list) else []
        events = [*events, _event(sequence, event_type, payload, created_at=created_at)]
        if len(events) > _MAX_EVENTS:
            dropped = events[: len(events) - _MAX_EVENTS]
            row["compacted_before_sequence"] = _integer(dropped[-1].get("sequence"))
            events = events[-_MAX_EVENTS:]
        row["events"] = events
        row["event_sequence"] = sequence

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            # A corrupt history must never prevent the main workspace from
            # opening.  The next successful save will replace it atomically.
            return []
        rows = payload.get("conversations", []) if isinstance(payload, dict) else []
        return [
            self._upgrade_row(row)
            for row in rows[:_MAX_CONVERSATIONS]
            if isinstance(row, dict) and str(row.get("conversation_id", "")).strip()
        ]

    def _write_unlocked(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {"version": 2, "conversations": rows[:_MAX_CONVERSATIONS]},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _title(payload: dict[str, Any], messages: list[dict[str, Any]]) -> str:
        supplied = _text(payload.get("title"), 120)
        if supplied:
            return supplied
        for message in messages:
            if message["role"] == "user" and message["content"]:
                return message["content"].splitlines()[0][:120]
        return "直接对话"

    @staticmethod
    def _model(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        for message in reversed(messages):
            if message["role"] == "assistant" and isinstance(message.get("model"), dict):
                return deepcopy(message["model"])
        return None

    @staticmethod
    def _summary(row: dict[str, Any]) -> dict[str, Any]:
        messages = row.get("messages", []) if isinstance(row.get("messages"), list) else []
        preview = ""
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("content"):
                preview = _text(message["content"], 180)
                break
        return {
            "kind": "direct",
            "conversation_id": str(row.get("conversation_id", "")),
            "title": _text(row.get("title"), 120) or "直接对话",
            "title_source": str(row.get("title_source", "") or ""),
            "preview": preview,
            "created_at": str(row.get("created_at", "") or ""),
            "updated_at": str(row.get("updated_at", "") or ""),
            "message_count": len(messages),
            "session_id": str(row.get("session_id", "") or ""),
            "model": deepcopy(row.get("model")),
            "status": _status(row.get("status")),
            "queue_count": len(row.get("queue", [])) if isinstance(row.get("queue"), list) else 0,
            "revision": max(0, _integer(row.get("revision"))),
            "branch_count": len(row.get("branches", [])) if isinstance(row.get("branches"), list) else 0,
            "archived": bool(row.get("archived", False)),
        }

    def list(self, limit: int = 200, *, view: str = "active") -> dict[str, Any]:
        requested_view = str(view or "active").strip().lower()
        if requested_view not in {"active", "archived", "all"}:
            requested_view = "active"
        with self._lock:
            rows = self._read_unlocked()
            rows.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
            if requested_view != "all":
                wanted_archived = requested_view == "archived"
                rows = [row for row in rows if bool(row.get("archived", False)) == wanted_archived]
            return {
                "ok": True,
                "view": requested_view,
                "conversations": [self._summary(row) for row in rows[: max(1, min(_MAX_CONVERSATIONS, int(limit or 200)))]],
            }

    def get(self, conversation_id: str) -> dict[str, Any]:
        wanted = _text(conversation_id, 160)
        with self._lock:
            for row in self._read_unlocked():
                if str(row.get("conversation_id", "")) == wanted:
                    return {"ok": True, **deepcopy(row)}
        raise FileNotFoundError("Direct conversation does not exist")

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Direct conversation payload must be an object")
        conversation_id = _text(payload.get("conversation_id"), 160) or uuid4().hex
        now = _now()
        with self._lock:
            rows = self._read_unlocked()
            previous = next((row for row in rows if str(row.get("conversation_id", "")) == conversation_id), None)
            previous_messages = (
                previous.get("messages", [])
                if isinstance((previous or {}).get("messages"), list)
                else []
            )
            messages = _messages(
                payload.get("messages", []),
                conversation_id=conversation_id,
                previous=previous_messages,
            )
            if not messages:
                raise ValueError("Direct conversation must contain at least one message")
            previous_title = _text((previous or {}).get("title"), 120)
            previous_title_source = _text((previous or {}).get("title_source"), 40)
            supplied_title_source = _text(payload.get("title_source"), 40)
            queue = _queue(payload.get("queue", []), conversation_id=conversation_id)
            current_turn = _queue_turn(
                payload.get("current_turn"),
                conversation_id=conversation_id,
            )
            status = _status(payload.get("status"))
            previous_events = deepcopy((previous or {}).get("events", []))
            previous_branches = deepcopy((previous or {}).get("branches", []))
            row = {
                "kind": "direct",
                "conversation_id": conversation_id,
                # A snapshot may be written after every turn.  Once a
                # conversation exists, its title belongs to the first turn;
                # later snapshots must never replace it with the latest
                # question or downgrade a model-generated title.
                "title": previous_title or self._title(payload, messages),
                "title_source": previous_title_source or supplied_title_source,
                "created_at": str((previous or {}).get("created_at", "") or now),
                "updated_at": now,
                "session_id": _text(payload.get("session_id"), 200),
                "messages": messages,
                "queue": queue,
                "queue_count": len(queue),
                "current_turn": current_turn,
                "status": status,
                "revision": _integer((previous or {}).get("revision")) + 1,
                "event_sequence": _integer((previous or {}).get("event_sequence")),
                "events": previous_events,
                "branches": previous_branches,
                "model": self._model(messages),
                "archived": bool((previous or {}).get("archived", False)),
            }

            branch_id = _text(payload.get("branch_id"), 160)
            known_branch_ids = {
                _text(item.get("branch_id"), 160)
                for item in previous_branches
                if isinstance(item, dict)
            }
            if previous is not None and branch_id and branch_id not in known_branch_ids:
                branch = {
                    "branch_id": branch_id,
                    "from_message_id": _text(payload.get("branch_from_message_id"), 160),
                    "reason": _text(payload.get("branch_reason"), 120) or "conversation_rewrite",
                    "created_at": now,
                    "base_revision": _integer(previous.get("revision")),
                    "session_id": _text(previous.get("session_id"), 200),
                    "status": _status(previous.get("status")),
                    "messages": deepcopy(previous_messages),
                    "queue": deepcopy(previous.get("queue", [])),
                }
                row["branches"] = [*previous_branches, branch][-_MAX_BRANCHES:]
                self._append_event(
                    row,
                    "branch.created",
                    {
                        "branch_id": branch_id,
                        "from_message_id": branch["from_message_id"],
                        "reason": branch["reason"],
                        "base_revision": branch["base_revision"],
                    },
                    created_at=now,
                )

            if previous is None:
                self._append_event(
                    row,
                    "conversation.created",
                    {"title": row["title"], "revision": row["revision"]},
                    created_at=now,
                )

            previous_by_id = {
                str(message.get("message_id", "")): message
                for message in previous_messages
                if isinstance(message, dict) and str(message.get("message_id", ""))
            }
            current_ids: set[str] = set()
            for message in messages:
                message_id = str(message.get("message_id", ""))
                current_ids.add(message_id)
                old_message = previous_by_id.get(message_id)
                if old_message is None:
                    self._append_event(
                        row,
                        "message.appended",
                        {"message": message},
                        created_at=str(message.get("created_at", "") or now),
                    )
                elif old_message != message:
                    self._append_event(
                        row,
                        "message.updated",
                        {"message": message},
                        created_at=now,
                    )
            removed_ids = [message_id for message_id in previous_by_id if message_id not in current_ids]
            if removed_ids:
                self._append_event(
                    row,
                    "messages.rebased",
                    {"removed_message_ids": removed_ids, "branch_id": branch_id},
                    created_at=now,
                )

            previous_queue = (previous or {}).get("queue", [])
            if (previous is not None and previous_queue != queue) or (previous is None and queue):
                self._append_event(
                    row,
                    "queue.updated",
                    {"queue": queue},
                    created_at=now,
                )
            previous_turn = (previous or {}).get("current_turn")
            if previous_turn != current_turn:
                self._append_event(
                    row,
                    "turn.changed",
                    {"current_turn": current_turn},
                    created_at=now,
                )
            previous_status = _status((previous or {}).get("status"))
            if (previous is None and status != "completed") or (
                previous is not None and previous_status != status
            ):
                self._append_event(
                    row,
                    "status.changed",
                    {"from": previous_status if previous is not None else "", "to": status},
                    created_at=now,
                )
            previous_session_id = _text((previous or {}).get("session_id"), 200)
            if row["session_id"] and row["session_id"] != previous_session_id:
                self._append_event(
                    row,
                    "session.bound",
                    {"session_id": row["session_id"]},
                    created_at=now,
                )
            self._append_event(
                row,
                "snapshot.committed",
                {"revision": row["revision"]},
                created_at=now,
            )
            rows = [item for item in rows if str(item.get("conversation_id", "")) != conversation_id]
            rows.insert(0, row)
            self._write_unlocked(rows)
            return {"ok": True, **deepcopy(row)}

    def set_title(
        self,
        conversation_id: str,
        title: str,
        *,
        source: str = "",
    ) -> dict[str, Any]:
        """Update only a conversation title without replacing its messages."""

        wanted = _text(conversation_id, 160)
        normalized = _text(title, 120)
        if not normalized:
            raise ValueError("Conversation title must not be empty")
        with self._lock:
            rows = self._read_unlocked()
            for row in rows:
                if str(row.get("conversation_id", "")) != wanted:
                    continue
                row["title"] = normalized
                if str(source or "").strip():
                    row["title_source"] = _text(source, 40)
                row["updated_at"] = _now()
                row["revision"] = _integer(row.get("revision")) + 1
                self._append_event(
                    row,
                    "title.updated",
                    {
                        "title": normalized,
                        "title_source": row.get("title_source", ""),
                        "revision": row["revision"],
                    },
                    created_at=row["updated_at"],
                )
                self._write_unlocked(rows)
                return {"ok": True, **deepcopy(row)}
        raise FileNotFoundError("Direct conversation does not exist")

    def set_archived(self, conversation_id: str, archived: bool) -> dict[str, Any]:
        wanted = _text(conversation_id, 160)
        with self._lock:
            rows = self._read_unlocked()
            for row in rows:
                if str(row.get("conversation_id", "")) != wanted:
                    continue
                previous_archived = bool(row.get("archived", False))
                row["archived"] = bool(archived)
                row["updated_at"] = _now()
                if previous_archived != row["archived"]:
                    row["revision"] = _integer(row.get("revision")) + 1
                    self._append_event(
                        row,
                        "conversation.archived" if row["archived"] else "conversation.restored",
                        {"revision": row["revision"]},
                        created_at=row["updated_at"],
                    )
                self._write_unlocked(rows)
                return {"ok": True, **deepcopy(row)}
        raise FileNotFoundError("Direct conversation does not exist")

    def delete(self, conversation_id: str) -> dict[str, Any]:
        wanted = _text(conversation_id, 160)
        with self._lock:
            rows = self._read_unlocked()
            remaining = [row for row in rows if str(row.get("conversation_id", "")) != wanted]
            if len(remaining) == len(rows):
                raise FileNotFoundError("Direct conversation does not exist")
            self._write_unlocked(remaining)
            return {"ok": True, "conversation_id": wanted}
