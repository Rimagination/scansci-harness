"""Small durable admission ledger for Host-executed Pi tool effects."""

from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any


class ToolEffectLedger:
    def __init__(self, agent_dir: str | Path) -> None:
        self.path = Path(agent_dir) / "effect-claims.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        filename = str(self.path.resolve())
        # SQLite appends journal filenames, which can cross Windows MAX_PATH.
        if os.name == "nt" and len(filename) >= 240 and not filename.startswith("\\\\?\\"):
            filename = "\\\\?\\UNC\\" + filename.lstrip("\\") if filename.startswith("\\\\") else "\\\\?\\" + filename
        self._filename = filename
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("begin immediate")
            connection.execute("""
                create table if not exists effect_claims (
                    effect_key text primary key,
                    scope_id text not null,
                    request_id text not null,
                    tool_name text not null,
                    arguments_sha256 text not null,
                    state text not null,
                    updated_at integer not null
                )
            """)
            columns = {row[1] for row in connection.execute("pragma table_info(effect_claims)")}
            if "request_id" not in columns:
                connection.execute("alter table effect_claims add column request_id text not null default ''")
            connection.execute("create index if not exists idx_effect_claims_scope on effect_claims(scope_id)")
            connection.commit()

    def claim(
        self, scope_id: str, request_id: str, tool_name: str, arguments: dict[str, Any],
    ) -> tuple[str, bool]:
        if not scope_id or not request_id or not tool_name:
            raise ValueError("Pi effect requires a Host turn, request, and tool name")
        encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        arguments_sha256 = sha256(encoded.encode("utf-8")).hexdigest()
        # ponytail: A scope belongs to its first Pi request after an effect;
        # a recovered request cannot safely distinguish a new effect from a
        # rephrased replay. A durable Host intent ID could permit both.
        effect_key = sha256(f"{scope_id}\0{tool_name}\0{arguments_sha256}".encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute("begin immediate")
            other_owner = connection.execute(
                "select 1 from effect_claims where scope_id = ? and request_id <> ? limit 1",
                (scope_id, request_id),
            ).fetchone()
            if other_owner is not None:
                connection.commit()
                return effect_key, False
            existing = connection.execute(
                "select 1 from effect_claims where effect_key = ?", (effect_key,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "insert into effect_claims "
                    "(effect_key, scope_id, request_id, tool_name, arguments_sha256, state, updated_at) "
                    "values (?, ?, ?, ?, ?, 'running', ?)",
                    (effect_key, scope_id, request_id, tool_name, arguments_sha256, int(time.time())),
                )
            connection.commit()
        # ponytail: Keep small effect tombstones indefinitely; archive them if
        # the per-workspace ledger becomes large.
        return effect_key, existing is None

    def complete(self, effect_key: str) -> None:
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            changed = connection.execute(
                "update effect_claims set state = 'completed', updated_at = ? "
                "where effect_key = ? and state = 'running'",
                (int(time.time()), effect_key),
            ).rowcount
            connection.commit()
        if changed != 1:
            raise RuntimeError("Pi effect claim was lost before completion")

    def uncertain(self, effect_key: str) -> None:
        with closing(sqlite3.connect(self._filename, timeout=30)) as connection:
            connection.execute(
                "update effect_claims set state = 'uncertain', updated_at = ? "
                "where effect_key = ? and state = 'running'",
                (int(time.time()), effect_key),
            )
            connection.commit()
