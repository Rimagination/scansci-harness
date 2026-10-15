"""Durable at-most-once admission for loopback direct-chat submissions."""

from __future__ import annotations

from contextlib import closing
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable

from .direct_chat_history import DirectConversationNotFound


_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_MAX_TERMINAL_BYTES = 2_000_000
_RESULT_CACHE_SECONDS = 30 * 24 * 60 * 60


class DirectChatSubmissionStore:
    """Reserve a client request before effects and retain its terminal event."""

    def __init__(self, workspace: str | Path, *, filename: str = ".scansci-direct-chat-submissions.sqlite3") -> None:
        self.path = Path(workspace).resolve().parent / filename
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sqlite_filename = str(self.path)
        # SQLite appends journal filenames, which can cross Windows MAX_PATH.
        if os.name == "nt" and len(sqlite_filename) >= 240 and not sqlite_filename.startswith("\\\\?\\"):
            sqlite_filename = "\\\\?\\UNC\\" + sqlite_filename.lstrip("\\") if sqlite_filename.startswith("\\\\") else "\\\\?\\" + sqlite_filename
        self._filename = sqlite_filename
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("pragma secure_delete = on")
            connection.execute("""
                create table if not exists direct_chat_submissions (
                    request_key text primary key,
                    payload_sha256 text not null,
                    conversation_id text not null default '',
                    owner_pid integer not null default 0,
                    state text not null,
                    terminal_json text,
                    updated_at integer not null
                )
            """)
            connection.execute("""
                create table if not exists direct_chat_deleted_conversations (
                    conversation_id text primary key,
                    deleted_at integer not null
                )
            """)
            columns = {row[1] for row in connection.execute("pragma table_info(direct_chat_submissions)")}
            if "conversation_id" not in columns:
                connection.execute("alter table direct_chat_submissions add column conversation_id text not null default ''")
                connection.execute(
                    "update direct_chat_submissions set state = 'unreplayable', terminal_json = null "
                    "where terminal_json is not null"
                )
            if "owner_pid" not in columns:
                connection.execute("alter table direct_chat_submissions add column owner_pid integer not null default 0")
            connection.execute(
                "update direct_chat_submissions set state = 'unreplayable', terminal_json = null "
                "where terminal_json is not null and updated_at < ?",
                (int(time.time()) - _RESULT_CACHE_SECONDS,),
            )
            connection.commit()

    @staticmethod
    def request_key(payload: dict[str, Any]) -> str:
        key = str(payload.get("idempotency_key") or payload.get("image_request_id") or "").strip()
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError("流式对话需要有效的 idempotency_key（1–160 位字母、数字、点、下划线、冒号或连字符）。")
        return key

    @staticmethod
    def conversation_id(value: Any) -> str:
        conversation_id = str(value or "").strip()
        if not 0 < len(conversation_id) <= 160:
            raise ValueError("缺少有效的 conversation_id（1–160 字符）")
        return conversation_id

    @staticmethod
    def payload_digest(payload: dict[str, Any]) -> str:
        request = {key: value for key, value in payload.items() if key != "idempotency_key"}
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()

    def reserve(self, request_key: str, payload_digest: str, conversation_id: str) -> tuple[str, dict[str, Any] | None]:
        conversation_id = self.conversation_id(conversation_id)
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("begin immediate")
            if connection.execute(
                "select 1 from direct_chat_deleted_conversations where conversation_id = ?",
                (conversation_id,),
            ).fetchone():
                connection.commit()
                return "erased", None
            row = connection.execute(
                "select payload_sha256, state, terminal_json from direct_chat_submissions where request_key = ?",
                (request_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "insert into direct_chat_submissions "
                    "(request_key, payload_sha256, conversation_id, owner_pid, state, terminal_json, updated_at) "
                    "values (?, ?, ?, ?, 'running', null, ?)",
                    (request_key, payload_digest, conversation_id, os.getpid(), int(time.time())),
                )
                connection.commit()
                return "new", None
            connection.commit()
        if row[0] != payload_digest:
            return "conflict", None
        return str(row[1]), json.loads(row[2]) if row[2] else None

    def status(self, request_key: str, conversation_id: str) -> tuple[str, dict[str, Any] | None]:
        conversation_id = self.conversation_id(conversation_id)
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            if connection.execute(
                "select 1 from direct_chat_deleted_conversations where conversation_id = ?",
                (conversation_id,),
            ).fetchone():
                return "erased", None
            row = connection.execute(
                "select state, terminal_json, owner_pid from direct_chat_submissions "
                "where request_key = ? and conversation_id = ?",
                (request_key, conversation_id),
            ).fetchone()
        if row is None:
            return "missing", None
        if row[0] == "running" and not self._process_alive(int(row[2] or 0)):
            with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
                connection.execute(
                    "update direct_chat_submissions set state = 'uncertain' "
                    "where request_key = ? and conversation_id = ? and state = 'running' and owner_pid = ?",
                    (request_key, conversation_id, row[2]),
                )
                connection.commit()
            return self.status(request_key, conversation_id)
        return str(row[0]), json.loads(row[1]) if row[1] else None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        if pid <= 0:
            return True  # Unknown legacy owner: keep the effect fenced.
        if pid == os.getpid():
            return True
        if os.name == "nt":
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            kernel.WaitForSingleObject.restype = ctypes.c_uint32
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel.OpenProcess(0x100000, 0, pid)  # SYNCHRONIZE
            if not handle:
                return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID.
            try:
                return kernel.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0 means exited.
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except OSError as error:
            return error.errno != errno.ESRCH and getattr(error, "winerror", None) != 87
        except SystemError:
            return True

    def abandon_missing(self, request_key: str, conversation_id: str) -> str:
        """Fence a late first POST before an explicit replacement begins."""
        conversation_id = self.conversation_id(conversation_id)
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("begin immediate")
            if connection.execute(
                "select 1 from direct_chat_deleted_conversations where conversation_id = ?",
                (conversation_id,),
            ).fetchone():
                connection.commit()
                return "erased"
            row = connection.execute(
                "select conversation_id, state from direct_chat_submissions where request_key = ?",
                (request_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "insert into direct_chat_submissions "
                    "(request_key, payload_sha256, conversation_id, state, terminal_json, updated_at) "
                    "values (?, '', ?, 'abandoned', null, ?)",
                    (request_key, conversation_id, int(time.time())),
                )
                connection.commit()
                return "abandoned"
            connection.commit()
        return str(row[1]) if row[0] == conversation_id else "conflict"

    def finish(self, request_key: str, event: dict[str, Any]) -> None:
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        state = "completed" if event.get("type") in {"RUN_FINISHED", "done"} else "failed"
        if len(encoded.encode("utf-8")) > _MAX_TERMINAL_BYTES:
            encoded = ""
            state = "unreplayable"
        now = int(time.time())
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("pragma secure_delete = on")
            connection.execute(
                "update direct_chat_submissions set state = case when conversation_id = '' then 'unreplayable' else ? end, "
                "terminal_json = case when conversation_id = '' then null else ? end, updated_at = ? "
                "where request_key = ? and state = 'running'",
                (state, encoded or None, now, request_key),
            )
            # ponytail: Keep small request tombstones indefinitely for safety;
            # archive old keys if the per-workspace ledger becomes large.
            connection.execute(
                "update direct_chat_submissions set state = 'unreplayable', terminal_json = null "
                "where terminal_json is not null and updated_at < ?",
                (now - _RESULT_CACHE_SECONDS,),
            )
            connection.commit()

    def save_history(self, conversation_id: str, save: Callable[[], Any]) -> Any:
        conversation_id = self.conversation_id(conversation_id)
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("begin immediate")
            deleted = connection.execute(
                "select 1 from direct_chat_deleted_conversations where conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if deleted:
                raise ValueError("这条对话已经删除")
            result = save()
            connection.commit()
            return result

    def erase_conversation(self, conversation_id: str, delete_history: Callable[[], Any] | None = None) -> Any:
        conversation_id = self.conversation_id(conversation_id)
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("pragma secure_delete = on")
            connection.execute("begin immediate")
            was_deleted = connection.execute(
                "select 1 from direct_chat_deleted_conversations where conversation_id = ?",
                (conversation_id,),
            ).fetchone() is not None
            connection.execute(
                "insert or ignore into direct_chat_deleted_conversations values (?, ?)",
                (conversation_id, int(time.time())),
            )
            connection.execute(
                "update direct_chat_submissions set state = 'erased', terminal_json = null "
                "where conversation_id = ?",
                (conversation_id,),
            )
            connection.commit()
        if delete_history is None:
            return None
        try:
            return delete_history()
        except DirectConversationNotFound:
            if not was_deleted:
                raise
            return {"ok": True, "conversation_id": conversation_id}

    def erase_after(self, conversation_id: str, delete: Callable[[sqlite3.Connection], Any]) -> Any:
        """Delete a research run and its cached replies under one admission lock."""
        conversation_id = self.conversation_id(conversation_id)
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("pragma secure_delete = on")
            connection.execute("pragma foreign_keys = on")
            connection.execute("begin immediate")
            running = connection.execute(
                "select owner_pid from direct_chat_submissions "
                "where conversation_id = ? and state = 'running'",
                (conversation_id,),
            ).fetchall()
            if any(self._process_alive(int(row[0] or 0)) for row in running):
                raise RuntimeError("任务仍有正在执行的续问，请等待完成后删除")
            result = delete(connection)
            connection.execute(
                "insert or ignore into direct_chat_deleted_conversations values (?, ?)",
                (conversation_id, int(time.time())),
            )
            connection.execute(
                "update direct_chat_submissions set state = 'erased', terminal_json = null "
                "where conversation_id = ?",
                (conversation_id,),
            )
            connection.commit()
        return result

    def interrupt(self, request_key: str) -> None:
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute(
                "update direct_chat_submissions set state = 'uncertain', updated_at = ? "
                "where request_key = ? and state = 'running'",
                (int(time.time()), request_key),
            )
            connection.commit()
