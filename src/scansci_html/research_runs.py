"""Persistent research-run state for the ScanSci desktop agent.

The notebook workspace owns durable research execution state.  This module is
deliberately model- and tool-agnostic: the web application executes tools while
the store records stages, calls, artifacts, evidence links, and recovery state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Sequence
from uuid import uuid4

from .agent_capabilities import artifact_uri, evidence_uri, run_uri
from .schema_migrations import Migration, apply_migrations, current_schema_version, rollback_migration


RUN_STATUSES = {
    "draft",
    "queued",
    "planning",
    "running",
    "needs_confirmation",
    "waiting_input",
    "paused",
    "verifying",
    "completed",
    "failed",
    "cancelled",
}
STAGE_STATUSES = {"pending", "running", "paused", "completed", "failed", "cancelled", "skipped"}
ACTIVE_RUN_STATUSES = {"queued", "planning", "running", "verifying"}
RESUMABLE_RUN_STATUSES = {"paused", "failed", "cancelled", "needs_confirmation", "waiting_input"}
SCIENTIFIC_EVENT_SCHEMA_VERSION = "scientific_event.v1"
RESEARCH_SCHEMA_NAME = "research_runs"
RESEARCH_SCHEMA_VERSION = 4

_CANONICAL_EVENT_TYPES = {
    "task.created": "run.created",
    "task.started": "run.started",
    "task.paused": "run.paused",
    "task.resumed": "run.resumed",
    "task.completed": "run.completed",
    "task.failed": "run.failed",
    "task.cancelled": "run.cancelled",
}

_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)"
    r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"
    r"|(?:sk-|key-)[A-Za-z0-9_-]{12,}"
    r"|((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
    r"(?:%3[dD]|=|:)\s*)[^&\s,;\"']+"
)
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
}


class _ClosingConnection(sqlite3.Connection):
    """Make ``with store._connect()`` release Windows file handles promptly."""

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def redact_sensitive_text(value: object) -> str:
    """Remove credential-shaped values before errors reach UI or durable logs."""

    text = str(value or "")

    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1) or match.group(2) or ""
        return f"{prefix}[REDACTED]"

    return _SENSITIVE_VALUE_PATTERN.sub(replace, text)


def _redact_sensitive_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    normalized_key = key.strip().lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(nested_key): _redact_sensitive_value(item, key=str(nested_key), depth=depth + 1)
            for nested_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def classify_error(error: BaseException | str, *, operation: str = "") -> str:
    """Return a stable recovery category without persisting exception details."""

    text = str(error or "").lower()
    name = error.__class__.__name__.lower() if isinstance(error, BaseException) else ""
    combined = f"{name} {operation.lower()} {text}"
    if any(token in combined for token in ("cancel", "aborted by user", "user stopped")):
        return "user_cancelled"
    if any(token in combined for token in ("api_key", "access_token", "permission", "forbidden", "unauthorized", "credential")):
        return "credential_or_permission"
    if any(token in combined for token in ("429", "rate limit", "too many requests", "ratelimit")):
        return "rate_limited"
    if isinstance(error, TimeoutError) or any(token in combined for token in ("timeout", "timed out", "deadline exceeded")):
        return "timeout"
    if any(token in combined for token in ("connection", "dns", "network", "socket", "unreachable", "http 5")):
        return "network"
    if any(token in combined for token in ("jsondecode", "parse", "malformed", "invalid response", "unexpected token")):
        return "parse"
    if any(token in combined for token in ("repeat", "duplicate output", "on on on", "degenerate", "循环", "重复")):
        return "model_degraded"
    if any(token in combined for token in ("module not found", "no module named", "executable not found", "missing component")):
        return "missing_component"
    return "unknown"


def _add_column_if_missing(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in connection.execute(f"pragma table_info({table})")}
    if column not in columns:
        connection.execute(f"alter table {table} add column {column} {definition}")


def _apply_research_event_migration(connection: sqlite3.Connection) -> None:
    """Additive v2 migration for ScientificEvent v1 and recovery primitives."""

    _add_column_if_missing(connection, "research_runs", "idempotency_key", "text not null default ''")
    event_columns = (
        ("schema_version", "text not null default 'scientific_event.v1'"),
        ("sequence", "integer not null default 0"),
        ("timestamp", "text not null default ''"),
        ("actor", "text not null default 'system'"),
        ("stage_id", "text not null default ''"),
        ("request_id", "text not null default ''"),
        ("tool_id", "text not null default ''"),
        ("parent_event_id", "text not null default ''"),
        ("input_hash", "text not null default ''"),
        ("output_hash", "text not null default ''"),
        ("idempotency_key", "text not null default ''"),
        ("terminal", "integer not null default 0"),
        ("canonical_type", "text not null default ''"),
        ("error_category", "text not null default ''"),
    )
    for column, definition in event_columns:
        _add_column_if_missing(connection, "research_run_events", column, definition)

    # Existing events receive a deterministic per-run sequence.  rowid is used
    # only as a tie breaker for old rows with the same second-level timestamp.
    run_ids = [str(row[0]) for row in connection.execute("select distinct run_id from research_run_events")]
    for run_id in run_ids:
        rows = connection.execute(
            "select event_id, event_type, created_at from research_run_events where run_id = ? order by created_at, rowid",
            (run_id,),
        ).fetchall()
        for sequence, row in enumerate(rows, start=1):
            event_type = str(row[1] or "")
            connection.execute(
                """
                update research_run_events
                set sequence = ?, timestamp = case when timestamp = '' then ? else timestamp end,
                    schema_version = case when schema_version = '' then ? else schema_version end,
                    actor = case when actor = '' then 'system' else actor end,
                    canonical_type = case when canonical_type = '' then ? else canonical_type end,
                    terminal = case when event_type in ('task.completed', 'task.failed', 'task.cancelled', 'run.completed', 'run.failed', 'run.cancelled') then 1 else terminal end
                where event_id = ?
                """,
                (
                    sequence,
                    str(row[2] or ""),
                    SCIENTIFIC_EVENT_SCHEMA_VERSION,
                    _CANONICAL_EVENT_TYPES.get(event_type, event_type),
                    str(row[0]),
                ),
            )

    statements = (
        "create unique index if not exists idx_research_events_sequence on research_run_events(run_id, sequence)",
        "create unique index if not exists idx_research_events_idempotency on research_run_events(run_id, idempotency_key) where idempotency_key <> ''",
        "create unique index if not exists idx_research_runs_idempotency on research_runs(notebook_id, idempotency_key) where idempotency_key <> ''",
        """
        create table if not exists research_run_checkpoints (
          checkpoint_id text primary key,
          run_id text not null,
          stage_key text not null,
          checkpoint_key text not null,
          status text not null,
          state_json text not null,
          input_hash text not null default '',
          output_hash text not null default '',
          idempotency_key text not null default '',
          created_at text not null,
          updated_at text not null,
          unique(run_id, stage_key, checkpoint_key),
          foreign key(run_id) references research_runs(run_id)
        )
        """,
        "create unique index if not exists idx_research_checkpoints_idempotency on research_run_checkpoints(run_id, idempotency_key) where idempotency_key <> ''",
        "create index if not exists idx_research_checkpoints_stage on research_run_checkpoints(run_id, stage_key, updated_at)",
        """
        create table if not exists research_worker_leases (
           lease_id text primary key,
          run_id text not null,
          stage_key text not null,
          worker_id text not null,
          status text not null,
          acquired_at text not null,
          heartbeat_at text not null,
          expires_at text not null,
          metadata_json text not null default '{}',
          unique(run_id, stage_key),
          foreign key(run_id) references research_runs(run_id)
        )
        """,
        "create index if not exists idx_research_worker_leases_expiry on research_worker_leases(status, expires_at)",
        """
        create table if not exists research_side_effect_receipts (
          receipt_id text primary key,
          run_id text not null,
          effect_type text not null,
          effect_key text not null,
          status text not null,
          input_hash text not null default '',
          output_hash text not null default '',
          result_json text not null default '{}',
          error_category text not null default '',
          error_message text not null default '',
          created_at text not null,
          updated_at text not null,
          unique(run_id, effect_type, effect_key),
          foreign key(run_id) references research_runs(run_id)
        )
        """,
        "create index if not exists idx_research_receipts_status on research_side_effect_receipts(run_id, effect_type, status)",
    )
    for statement in statements:
        connection.execute(statement)


def _apply_scientific_subagent_migration(connection: sqlite3.Connection) -> None:
    """Add host-owned parent membership and idempotent batch reservations."""

    connection.execute(
        """
        create table if not exists research_subagent_batches (
          parent_run_id text not null,
          idempotency_key text not null,
          child_run_ids_json text not null default '[]',
          created_at text not null,
          primary key(parent_run_id, idempotency_key),
          foreign key(parent_run_id) references research_runs(run_id)
        )
        """
    )
    connection.execute(
        """
        create table if not exists research_subagent_children (
          child_run_id text primary key,
          parent_run_id text not null,
          batch_key text not null,
          role_id text not null,
          created_at text not null,
          foreign key(child_run_id) references research_runs(run_id),
          foreign key(parent_run_id) references research_runs(run_id)
        )
        """
    )
    connection.execute(
        "create index if not exists idx_research_subagent_parent on research_subagent_children(parent_run_id, created_at)"
    )


def _research_migrations() -> tuple[Migration, ...]:
    return (
        Migration(1, "research baseline registry", lambda _connection: None),
        Migration(2, "scientific event v1 and recovery primitives", _apply_research_event_migration),
        Migration(3, "cooperative user pause requests", lambda connection: _add_column_if_missing(connection, "research_runs", "pause_requested", "integer not null default 0")),
        Migration(4, "atomic scientific sub-agent reservation", _apply_scientific_subagent_migration),
    )


@dataclass(frozen=True)
class StageSpec:
    key: str
    title: str
    kind: str
    tool_name: str = ""


class ResearchRunStore:
    """SQLite-backed state machine shared by the UI and research workers."""

    def __init__(self, workspace_path: str | Path) -> None:
        self.workspace = Path(workspace_path)
        self.workspace.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize_schema(connection)
            connection.commit()

    def create_run(
        self,
        *,
        notebook_id: str,
        workflow_type: str,
        title: str,
        input_payload: dict[str, Any],
        stages: Sequence[StageSpec],
        model_provider_id: str = "",
        model_id: str = "",
        metadata: dict[str, Any] | None = None,
        task_contract: dict[str, Any] | None = None,
        parent_run_id: str = "",
        branch_from_message_id: str = "",
        background: bool = True,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not stages:
            raise ValueError("A research run requires at least one stage")
        normalized_idempotency_key = str(idempotency_key or "").strip()
        if normalized_idempotency_key:
            with self._connect() as connection:
                self._initialize_schema(connection)
                existing = connection.execute(
                    "select run_id from research_runs where notebook_id = ? and idempotency_key = ?",
                    (str(notebook_id), normalized_idempotency_key),
                ).fetchone()
                connection.commit()
            if existing is not None:
                return self.get_run(str(existing[0]))
        run_id = _new_id("run")
        now = _utc_now()
        with self._connect() as connection:
            self._initialize_schema(connection)
            connection.execute(
                """
                insert into research_runs (
                  run_id, notebook_id, workflow_type, title, status,
                  current_stage, progress, input_json, output_artifact_id,
                  cancel_requested, pause_requested, error_code, error_message,
                  model_provider_id, model_id, created_at, updated_at,
                  started_at, completed_at, metadata_json, parent_run_id,
                  branch_from_message_id, background, idempotency_key, interaction_json, recovery_json,
                  task_contract_json
                ) values (?, ?, ?, ?, 'queued', ?, 0, ?, '', 0, 0, '', '', ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, '{}', '{}', ?)
                """,
                (
                    run_id,
                    str(notebook_id),
                    str(workflow_type),
                    str(title).strip() or "未命名研究任务",
                    stages[0].key,
                    _json_dumps(input_payload),
                    str(model_provider_id),
                    str(model_id),
                    now,
                    now,
                    _json_dumps(metadata or {}),
                    str(parent_run_id),
                    str(branch_from_message_id),
                    1 if background else 0,
                    normalized_idempotency_key,
                    _json_dumps(_redact_sensitive_value(dict(task_contract or {}))),
                ),
            )
            for position, stage in enumerate(stages):
                connection.execute(
                    """
                    insert into research_stages (
                      stage_id, run_id, stage_key, position, title, kind,
                      tool_name, status, attempt, started_at, completed_at,
                      summary, error_message, input_json, output_json
                    ) values (?, ?, ?, ?, ?, ?, ?, 'pending', 0, '', '', '', '', '{}', '{}')
                    """,
                    (
                        _new_id("stage"),
                        run_id,
                        stage.key,
                        position,
                        stage.title,
                        stage.kind,
                        stage.tool_name,
                    ),
                )
            self._append_event(
                connection,
                run_id,
                event_type="task.created",
                summary="任务已创建",
                payload={
                    "workflow_type": str(workflow_type),
                    "background": bool(background),
                    "routing": dict(metadata or {}).get("routing", {}),
                },
                created_at=now,
            )
            connection.commit()
        return self.get_run(run_id)

    def reserve_scientific_children(
        self,
        parent_run_id: str,
        *,
        idempotency_key: str,
        child_specs: Sequence[dict[str, Any]],
        max_children: int = 3,
    ) -> dict[str, Any]:
        """Atomically reserve bounded durable child runs for one parent.

        The reservation commits before a worker is submitted.  This makes the
        durable rows the source of truth across retries and process restarts,
        while ``BEGIN IMMEDIATE`` prevents concurrent callers from exceeding
        the parent's child limit.
        """

        batch_key = str(idempotency_key or "").strip()
        if not batch_key:
            raise ValueError("Scientific delegation requires an idempotency key")
        bounded_max = max(0, min(3, int(max_children)))
        selected_ids: list[str] = []
        accepted = 0
        replayed = False
        with self._connect() as connection:
            self._initialize_schema(connection)
            connection.execute("begin immediate")
            parent = self._require_run(connection, str(parent_run_id))
            recursive = connection.execute(
                "select 1 from research_subagent_children where child_run_id = ?",
                (str(parent_run_id),),
            ).fetchone()
            if recursive is not None:
                raise PermissionError("Scientific child runs cannot delegate recursively")
            existing = connection.execute(
                "select child_run_ids_json from research_subagent_batches where parent_run_id = ? and idempotency_key = ?",
                (str(parent_run_id), batch_key),
            ).fetchone()
            if existing is not None:
                try:
                    loaded = json.loads(str(existing[0]))
                except json.JSONDecodeError:
                    loaded = []
                selected_ids = [str(item) for item in loaded] if isinstance(loaded, list) else []
                replayed = True
                connection.commit()
            else:
                current_count = int(
                    connection.execute(
                        "select count(*) from research_subagent_children where parent_run_id = ?",
                        (str(parent_run_id),),
                    ).fetchone()[0]
                    or 0
                )
                capacity = max(0, bounded_max - current_count)
                now = _utc_now()
                for spec in list(child_specs)[:capacity]:
                    stages = list(spec.get("stages", []) or [])
                    if not stages or not all(isinstance(stage, StageSpec) for stage in stages):
                        raise ValueError("A scientific child requires durable stage specifications")
                    child_run_id = _new_id("run")
                    role_id = str(spec.get("role_id", "") or "").strip()
                    run_key = f"scientific:{parent_run_id}:{batch_key}:{role_id}"
                    metadata = dict(spec.get("metadata", {}) or {})
                    connection.execute(
                        """
                        insert into research_runs (
                          run_id, notebook_id, workflow_type, title, status,
                          current_stage, progress, input_json, output_artifact_id,
                          cancel_requested, pause_requested, error_code, error_message,
                          model_provider_id, model_id, created_at, updated_at,
                          started_at, completed_at, metadata_json, parent_run_id,
                          branch_from_message_id, background, idempotency_key, interaction_json, recovery_json,
                          task_contract_json
                        ) values (?, ?, ?, ?, 'queued', ?, 0, ?, '', 0, 0, '', '', ?, ?, ?, ?, '', '', ?, ?, '', 1, ?, '{}', '{}', ?)
                        """,
                        (
                            child_run_id,
                            str(parent["notebook_id"]),
                            str(spec.get("workflow_type", "scientific_subagent")),
                            str(spec.get("title", "")).strip() or "Scientific sub-agent",
                            stages[0].key,
                            _json_dumps(dict(spec.get("input_payload", {}) or {})),
                            str(spec.get("model_provider_id", parent["model_provider_id"])),
                            str(spec.get("model_id", parent["model_id"])),
                            now,
                            now,
                            _json_dumps(metadata),
                            str(parent_run_id),
                            run_key,
                            _json_dumps(_redact_sensitive_value(dict(spec.get("task_contract", {}) or {}))),
                        ),
                    )
                    for position, stage in enumerate(stages):
                        connection.execute(
                            """
                            insert into research_stages (
                              stage_id, run_id, stage_key, position, title, kind,
                              tool_name, status, attempt, started_at, completed_at,
                              summary, error_message, input_json, output_json
                            ) values (?, ?, ?, ?, ?, ?, ?, 'pending', 0, '', '', '', '', '{}', '{}')
                            """,
                            (
                                _new_id("stage"),
                                child_run_id,
                                stage.key,
                                position,
                                stage.title,
                                stage.kind,
                                stage.tool_name,
                            ),
                        )
                    self._append_event(
                        connection,
                        child_run_id,
                        event_type="task.created",
                        summary="Scientific sub-agent reserved",
                        payload={"workflow_type": str(spec.get("workflow_type", "scientific_subagent")), "background": True},
                        created_at=now,
                    )
                    connection.execute(
                        """
                        insert into research_subagent_children (
                          child_run_id, parent_run_id, batch_key, role_id, created_at
                        ) values (?, ?, ?, ?, ?)
                        """,
                        (child_run_id, str(parent_run_id), batch_key, role_id, now),
                    )
                    selected_ids.append(child_run_id)
                accepted = len(selected_ids)
                connection.execute(
                    """
                    insert into research_subagent_batches (
                      parent_run_id, idempotency_key, child_run_ids_json, created_at
                    ) values (?, ?, ?, ?)
                    """,
                    (str(parent_run_id), batch_key, _json_dumps(selected_ids), now),
                )
                connection.commit()
        return {
            "children": [self.get_run(run_id) for run_id in selected_ids],
            "accepted": accepted,
            "replayed": replayed,
        }

    def list_scientific_children(self, parent_run_id: str) -> list[dict[str, Any]]:
        """Return the durable membership list for one parent in creation order."""

        with self._connect() as connection:
            self._initialize_schema(connection)
            self._require_run(connection, str(parent_run_id))
            rows = connection.execute(
                """
                select child_run_id from research_subagent_children
                where parent_run_id = ? order by created_at, rowid
                """,
                (str(parent_run_id),),
            ).fetchall()
        return [self.get_run(str(row[0])) for row in rows]

    def list_runs(
        self,
        *,
        notebook_id: str = "",
        limit: int = 50,
        archived: bool | None = False,
    ) -> list[dict[str, Any]]:
        limit_value = min(200, max(1, int(limit)))
        with self._connect() as connection:
            self._initialize_schema(connection)
            conditions: list[str] = []
            values: list[Any] = []
            if notebook_id:
                conditions.append("notebook_id = ?")
                values.append(str(notebook_id))
            if archived is True:
                conditions.append("archived_at <> ''")
            elif archived is False:
                conditions.append("archived_at = ''")
            where = f"where {' and '.join(conditions)}" if conditions else ""
            rows = connection.execute(
                f"""
                select * from research_runs
                {where}
                order by updated_at desc, created_at desc
                limit ?
                """,
                (*values, limit_value),
            ).fetchall()
            return [self._run_summary(connection, row) for row in rows]

    def archive_run(self, run_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            self._initialize_schema(connection)
            row = self._require_manageable_run(connection, run_id)
            if str(row["archived_at"]):
                return self.get_run(run_id)
            connection.execute(
                "update research_runs set archived_at = ?, updated_at = ? where run_id = ?",
                (now, now, run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def restore_run(self, run_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            self._initialize_schema(connection)
            self._require_run(connection, run_id)
            connection.execute(
                "update research_runs set archived_at = '', updated_at = ? where run_id = ?",
                (now, run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def delete_run(self, run_id: str) -> dict[str, Any]:
        """Permanently remove one conversation while preserving exported files."""

        with self._connect() as connection:
            self._initialize_schema(connection)
            row = self._require_manageable_run(connection, run_id)
            title = str(row["title"])
            child_membership = connection.execute(
                "select parent_run_id, batch_key from research_subagent_children where child_run_id = ?",
                (run_id,),
            ).fetchone()
            if child_membership is not None:
                connection.execute(
                    "delete from research_subagent_batches where parent_run_id = ? and idempotency_key = ?",
                    (str(child_membership["parent_run_id"]), str(child_membership["batch_key"])),
                )
                connection.execute(
                    "delete from research_subagent_children where child_run_id = ?",
                    (run_id,),
                )
            # Deleting one parent conversation preserves its independently
            # auditable child runs, but removes the ownership/batch rows whose
            # foreign keys point at that parent.
            connection.execute(
                "delete from research_subagent_batches where parent_run_id = ?",
                (run_id,),
            )
            connection.execute(
                "delete from research_subagent_children where parent_run_id = ?",
                (run_id,),
            )
            connection.execute("delete from research_evidence_links where run_id = ?", (run_id,))
            connection.execute("delete from research_tool_calls where run_id = ?", (run_id,))
            connection.execute("delete from research_run_messages where run_id = ?", (run_id,))
            connection.execute("delete from research_run_events where run_id = ?", (run_id,))
            connection.execute("delete from research_artifacts where run_id = ?", (run_id,))
            connection.execute("delete from research_stages where run_id = ?", (run_id,))
            connection.execute("delete from research_runs where run_id = ?", (run_id,))
            connection.commit()
        return {"ok": True, "run_id": run_id, "title": title, "deleted": True}

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            self._initialize_schema(connection)
            row = connection.execute("select * from research_runs where run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise FileNotFoundError(f"Research run does not exist: {run_id}")
            payload = self._run_summary(connection, row)
            payload["input"] = _json_loads(row["input_json"])
            payload["metadata"] = _json_loads(row["metadata_json"])
            payload["interaction"] = _json_loads(row["interaction_json"])
            payload["recovery"] = _json_loads(row["recovery_json"])
            payload["task_contract"] = _json_loads(row["task_contract_json"])
            payload["stages"] = [
                self._stage_payload(stage)
                for stage in connection.execute(
                    "select * from research_stages where run_id = ? order by position", (run_id,)
                ).fetchall()
            ]
            payload["tool_calls"] = [
                self._tool_call_payload(call)
                for call in connection.execute(
                    "select * from research_tool_calls where run_id = ? order by started_at, tool_call_id", (run_id,)
                ).fetchall()
            ]
            payload["messages"] = [
                self._message_payload(message)
                for message in connection.execute(
                    "select * from research_run_messages where run_id = ? order by rowid", (run_id,)
                ).fetchall()
            ]
            payload["events"] = [
                self._event_payload(item)
                for item in connection.execute(
                    """
                    select * from research_run_events
                    where run_id = ? order by sequence limit 80
                    """,
                    (run_id,),
                ).fetchall()
            ]
            artifacts = [
                self._artifact_payload(connection, artifact)
                for artifact in connection.execute(
                    "select * from research_artifacts where run_id = ? order by created_at", (run_id,)
                ).fetchall()
            ]
            payload["artifacts"] = artifacts
            payload["output_artifact"] = next(
                (item for item in artifacts if item["artifact_id"] == payload["output_artifact_id"]), None
            )
            if connection.execute(
                "select 1 from research_run_events where run_id = ? and event_type = 'advisor.reviewed' limit 1",
                (run_id,),
            ).fetchone():
                from .agent_advisor import review_research_run

                # Re-evaluate the display without rewriting historical audit events.
                payload["advisor_report"] = review_research_run(payload)
            return payload

    def events_after(self, run_id: str, *, after_sequence: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        """Replay only the append-only tail after a client checkpoint."""

        limit_value = min(1000, max(1, int(limit)))
        with self._connect() as connection:
            self._initialize_schema(connection)
            self._require_run(connection, run_id)
            rows = connection.execute(
                """
                select * from research_run_events
                where run_id = ? and sequence > ?
                order by sequence limit ?
                """,
                (str(run_id), max(0, int(after_sequence)), limit_value),
            ).fetchall()
            return [self._event_payload(row) for row in rows]

    # The explicit name is useful to API adapters and keeps the replay contract
    # discoverable without making clients know the SQLite table name.
    def get_events_after(self, run_id: str, *, after_sequence: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return self.events_after(run_id, after_sequence=after_sequence, limit=limit)

    def snapshot(self, run_id: str, *, after_sequence: int = 0, event_limit: int = 200) -> dict[str, Any]:
        """Return a durable state snapshot plus an ordered event replay tail."""

        state = self.get_run(run_id)
        events = self.events_after(run_id, after_sequence=after_sequence, limit=event_limit)
        snapshot_state = dict(state)
        snapshot_state["events"] = []
        return {
            "schema_version": SCIENTIFIC_EVENT_SCHEMA_VERSION,
            "snapshot": snapshot_state,
            "events": events,
            "after_sequence": max(0, int(after_sequence)),
            "last_sequence": int(state.get("last_event_sequence", 0) or 0),
            "has_more": bool(events and int(events[-1].get("sequence", 0)) < int(state.get("last_event_sequence", 0) or 0)),
        }

    def schema_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            self._initialize_schema(connection)
            return {
                "schema_name": RESEARCH_SCHEMA_NAME,
                "schema_version": current_schema_version(connection, RESEARCH_SCHEMA_NAME),
                "target_version": RESEARCH_SCHEMA_VERSION,
            }

    def fork_run(
        self,
        source_run_id: str,
        *,
        branch_from_message_id: str = "",
        title: str = "",
        input_overrides: dict[str, Any] | None = None,
        background: bool = True,
    ) -> dict[str, Any]:
        """Create an independent durable branch while preserving the source task."""

        source = self.get_run(source_run_id)
        branch_input = {**dict(source.get("input", {}) or {}), **dict(input_overrides or {})}
        stages = [
            StageSpec(
                str(stage.get("key", "")),
                str(stage.get("title", "")),
                str(stage.get("kind", "")),
                str(stage.get("tool_name", "")),
            )
            for stage in list(source.get("stages", []) or [])
        ]
        metadata = {
            **dict(source.get("metadata", {}) or {}),
            "branch": {
                "parent_run_id": source_run_id,
                "branch_from_message_id": str(branch_from_message_id),
                "created_at": _utc_now(),
            },
        }
        branch = self.create_run(
            notebook_id=str(source.get("notebook_id", "")),
            workflow_type=str(source.get("workflow_type", "")),
            title=str(title).strip() or f"{str(source.get('title', '研究任务'))} · 分支",
            input_payload=branch_input,
            stages=stages,
            model_provider_id=str(dict(source.get("model", {}) or {}).get("provider_id", "")),
            model_id=str(dict(source.get("model", {}) or {}).get("model_id", "")),
            metadata=metadata,
            task_contract=dict(source.get("task_contract", {}) or {}),
            parent_run_id=source_run_id,
            branch_from_message_id=str(branch_from_message_id),
            background=background,
        )
        copied: list[dict[str, Any]] = []
        for message in list(source.get("messages", []) or []):
            copied.append(message)
            if branch_from_message_id and str(message.get("message_id", "")) == str(branch_from_message_id):
                break
        for message in copied:
            self.append_message(
                str(branch["run_id"]),
                role=str(message.get("role", "")),
                content=str(message.get("content", "")),
                usage=dict(message.get("usage", {}) or {}),
                processing_ms=int(message.get("processing_ms", 0) or 0),
            )
        return self.get_run(str(branch["run_id"]))

    def set_interaction(self, run_id: str, interaction: dict[str, Any]) -> dict[str, Any]:
        """Persist a blocking AskUser/Plan request so it survives UI navigation."""

        payload = dict(interaction or {})
        kind = str(payload.get("kind", payload.get("interaction_kind", "ask_user")))
        status = "needs_confirmation" if kind == "plan" else "waiting_input"
        now = _utc_now()
        with self._connect() as connection:
            self._initialize_schema(connection)
            self._require_run(connection, run_id)
            connection.execute(
                """
                update research_runs
                set status = ?, interaction_json = ?, updated_at = ?
                where run_id = ?
                """,
                (status, _json_dumps(payload), now, run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def resolve_interaction(
        self,
        run_id: str,
        *,
        interaction_id: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Record the user's decision and leave the task ready to continue."""

        now = _utc_now()
        with self._connect() as connection:
            self._initialize_schema(connection)
            row = self._require_run(connection, run_id)
            interaction = _json_loads(row["interaction_json"])
            expected = str(interaction.get("interaction_id", ""))
            if expected and expected != str(interaction_id):
                raise ValueError("This interaction is no longer pending")
            metadata = _json_loads(row["metadata_json"])
            decisions = list(metadata.get("interaction_decisions", []) or [])
            decisions.append(
                {
                    "interaction_id": str(interaction_id),
                    "kind": str(interaction.get("kind", "")),
                    "response": dict(response or {}),
                    "resolved_at": now,
                }
            )
            metadata["interaction_decisions"] = decisions[-50:]
            connection.execute(
                """
                update research_runs
                set status = 'paused', interaction_json = '{}', metadata_json = ?, updated_at = ?
                where run_id = ?
                """,
                (_json_dumps(metadata), now, run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def set_recovery(self, run_id: str, recovery: dict[str, Any]) -> dict[str, Any]:
        """Persist structured failure classification and recovery actions."""

        with self._connect() as connection:
            self._initialize_schema(connection)
            self._require_run(connection, run_id)
            connection.execute(
                "update research_runs set recovery_json = ?, updated_at = ? where run_id = ?",
                (_json_dumps(_redact_sensitive_value(dict(recovery or {}))), _utc_now(), run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def set_task_contract(self, run_id: str, task_contract: dict[str, Any]) -> dict[str, Any]:
        """Attach or replace the host-owned autonomy contract for one task."""

        with self._connect() as connection:
            self._initialize_schema(connection)
            self._require_run(connection, run_id)
            connection.execute(
                "update research_runs set task_contract_json = ? where run_id = ?",
                (
                    _json_dumps(_redact_sensitive_value(dict(task_contract or {}))),
                    run_id,
                ),
            )
            connection.commit()
        return self.get_run(run_id)

    def task_registry(self, *, limit: int = 200) -> dict[str, Any]:
        """Return one control-plane snapshot for foreground, background, and branched tasks."""

        runs = self.list_runs(limit=limit, archived=None)
        branch_counts: dict[str, int] = {}
        for run in runs:
            parent = str(run.get("parent_run_id", ""))
            if parent:
                branch_counts[parent] = branch_counts.get(parent, 0) + 1
        items = [
            {
                **run,
                "branch_count": branch_counts.get(str(run.get("run_id", "")), 0),
                "blocked": str(run.get("status", "")) in {"needs_confirmation", "waiting_input"},
            }
            for run in runs
        ]
        return {
            "tasks": items,
            "counts": {
                "total": len(items),
                "active": sum(str(item.get("status", "")) in ACTIVE_RUN_STATUSES for item in items),
                "background": sum(bool(item.get("background")) for item in items),
                "blocked": sum(bool(item.get("blocked")) for item in items),
                "branches": sum(bool(item.get("parent_run_id")) for item in items),
            },
        }

    def append_message(
        self,
        run_id: str,
        *,
        role: str,
        content: str,
        usage: dict[str, Any] | None = None,
        processing_ms: int = 0,
    ) -> dict[str, Any]:
        """Persist a follow-up turn against an existing research task.

        A task can therefore remain the single source of truth for its output
        artifact and all conversations that refine or explain that output.
        """

        normalized_role = str(role).strip().lower()
        normalized_content = str(content).strip()
        if normalized_role not in {"user", "assistant"}:
            raise ValueError("Research-run messages must be user or assistant messages")
        if not normalized_content:
            raise ValueError("Research-run message content is required")
        now = _utc_now()
        message_id = _new_id("message")
        with self._connect() as connection:
            self._initialize_schema(connection)
            self._require_run(connection, run_id)
            connection.execute(
                """
                insert into research_run_messages (
                  message_id, run_id, role, content, usage_json, processing_ms, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    run_id,
                    normalized_role,
                    normalized_content,
                    _json_dumps(usage or {}),
                    max(0, int(processing_ms or 0)),
                    now,
                ),
            )
            # A follow-up should also move the original task to the top of the
            # history list; it must never create a second pseudo-conversation.
            connection.execute("update research_runs set updated_at = ? where run_id = ?", (now, run_id))
            connection.commit()
        return {
            "message_id": message_id,
            "run_id": run_id,
            "role": normalized_role,
            "content": normalized_content,
            "usage": usage or {},
            "processing_ms": max(0, int(processing_ms or 0)),
            "created_at": now,
        }

    def recover_interrupted_runs(self) -> int:
        """Pause active runs left behind by an application restart."""
        now = _utc_now()
        with self._connect() as connection:
            self._initialize_schema(connection)
            placeholders = ",".join("?" for _ in ACTIVE_RUN_STATUSES)
            rows = connection.execute(
                f"select run_id from research_runs where status in ({placeholders})", tuple(ACTIVE_RUN_STATUSES)
            ).fetchall()
            run_ids = [str(row["run_id"]) for row in rows]
            if run_ids:
                run_placeholders = ",".join("?" for _ in run_ids)
                connection.execute(
                    f"""
                    update research_runs
                    set status = 'paused', updated_at = ?, error_code = 'app_restarted',
                        error_message = '应用已重启，可从当前阶段继续。'
                    where run_id in ({run_placeholders})
                    """,
                    (now, *run_ids),
                )
                connection.execute(
                    f"""
                    update research_stages
                    set status = 'paused', summary = '应用重启后等待继续'
                    where run_id in ({run_placeholders}) and status = 'running'
                    """,
                    tuple(run_ids),
                )
                for interrupted_run_id in run_ids:
                    self._append_event(
                        connection,
                        interrupted_run_id,
                        event_type="task.paused",
                        summary="应用已重启，可从当前阶段继续",
                        payload={"reason": "app_restarted", "resume_available": True},
                        created_at=now,
                    )
            connection.commit()
            return len(run_ids)

    def begin_run(self, run_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            # Serialize ownership before reading status. This prevents two
            # runtime instances from both executing the same durable run.
            connection.execute("begin immediate")
            row = self._require_run(connection, run_id)
            status = str(row["status"])
            if status not in {"queued", "paused", "failed", "cancelled"}:
                raise ValueError(f"Research run cannot start from status: {status}")
            updated = connection.execute(
                """
                update research_runs
                set status = 'running', cancel_requested = 0, pause_requested = 0, error_code = '', error_message = '',
                    started_at = case when started_at = '' then ? else started_at end,
                    completed_at = '', updated_at = ?
                where run_id = ? and status in ('queued', 'paused', 'failed', 'cancelled')
                """,
                (now, now, run_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Research run ownership changed before execution began")
            self._append_event(
                connection,
                run_id,
                event_type="task.started",
                summary="任务开始执行",
                created_at=now,
            )
            connection.commit()
        return self.get_run(run_id)

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            status = str(row["status"])
            if status in {"completed", "failed", "cancelled"}:
                return self.get_run(run_id)
            connection.execute(
                "update research_runs set cancel_requested = 1, pause_requested = 0, updated_at = ? where run_id = ?",
                (now, run_id),
            )
            self._append_event(
                connection,
                run_id,
                event_type="run.cancel_requested",
                summary="已请求停止任务",
                payload={"status": status},
                created_at=now,
                idempotency_key="run.cancel_requested",
            )
            connection.commit()
        return self.get_run(run_id)

    def request_pause(self, run_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            status = str(row["status"])
            if status in {"completed", "failed", "cancelled", "paused"}:
                return self.get_run(run_id)
            connection.execute(
                "update research_runs set pause_requested = 1, cancel_requested = 0, updated_at = ? where run_id = ?",
                (now, run_id),
            )
            self._append_event(
                connection,
                run_id,
                event_type="run.pause_requested",
                summary="已请求暂停任务",
                payload={"status": status},
                created_at=now,
                idempotency_key="run.pause_requested",
            )
            connection.commit()
        return self.get_run(run_id)

    def cancel_requested(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            return bool(row["cancel_requested"])

    def pause_requested(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            return bool(row["pause_requested"])

    def stop_requested(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            return bool(row["cancel_requested"] or row["pause_requested"])

    def mark_paused(self, run_id: str, *, summary: str = "任务已暂停") -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            if str(row["status"]) in {"completed", "failed", "cancelled"}:
                connection.commit()
                return self.get_run(run_id)
            connection.execute(
                """
                update research_stages set status = 'paused', summary = ?
                where run_id = ? and status = 'running'
                """,
                (summary, run_id),
            )
            connection.execute(
                """
                update research_runs
                set status = 'paused', cancel_requested = 0, pause_requested = 0,
                    completed_at = '', updated_at = ?, error_code = '', error_message = ?
                where run_id = ?
                """,
                (now, summary, run_id),
            )
            self._append_event(
                connection,
                run_id,
                event_type="task.paused",
                summary=summary,
                payload={"reason": "user_paused", "resume_available": True},
                created_at=now,
            )
            connection.commit()
        return self.get_run(run_id)

    def mark_cancelled(self, run_id: str, *, summary: str = "任务已停止") -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            # A terminal transition is durable and must be idempotent.  In
            # particular, a late cancellation request must not rewrite a
            # completed/failed run or emit a second terminal event.
            if str(row["status"]) in {"completed", "failed", "cancelled"}:
                connection.commit()
                return self.get_run(run_id)
            connection.execute(
                """
                update research_stages set status = 'cancelled', completed_at = ?, summary = ?
                where run_id = ? and status in ('pending', 'running', 'paused')
                """,
                (now, summary, run_id),
            )
            connection.execute(
                """
                update research_runs
                set status = 'cancelled', cancel_requested = 0, pause_requested = 0, completed_at = ?, updated_at = ?,
                    error_code = '', error_message = ?
                where run_id = ?
                """,
                (now, now, summary, run_id),
            )
            self._append_event(
                connection,
                run_id,
                event_type="task.cancelled",
                summary=summary,
                payload={"reason": "user_cancelled"},
                created_at=now,
                terminal=True,
                error_category="user_cancelled",
            )
            connection.commit()
        return self.get_run(run_id)

    def mark_needs_confirmation(self, run_id: str, *, summary: str = "计划已生成，等待确认") -> dict[str, Any]:
        """Transition the run to needs_confirmation without touching stages."""
        now = _utc_now()
        with self._connect() as connection:
            self._require_run(connection, run_id)
            connection.execute(
                """
                update research_runs
                set status = 'needs_confirmation', updated_at = ?
                where run_id = ?
                """,
                (now, run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def prepare_stage_retry(self, run_id: str, stage_key: str, error: Exception, *, attempt: int = 1) -> None:
        """Reset a single failed stage to pending for auto-retry with context."""
        now = _utc_now()
        with self._connect() as connection:
            self._require_run(connection, run_id)
            connection.execute(
                """
                update research_stages
                set status = 'pending', started_at = '', completed_at = '',
                    summary = '', error_message = ?
                where run_id = ? and stage_key = ?
                """,
                (
                    redact_sensitive_text(
                        f"[重试 {attempt}/2] {type(error).__name__}: {str(error)[:200]}"
                    ),
                    run_id,
                    stage_key,
                ),
            )
            connection.execute(
                """
                update research_runs
                set status = 'queued', updated_at = ?, error_code = '', error_message = ''
                where run_id = ?
                """,
                (now, run_id),
            )
            connection.commit()

    def prepare_resume(self, run_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            status = str(row["status"])
            if status not in RESUMABLE_RUN_STATUSES:
                raise ValueError(f"Research run is not resumable from status: {status}")
            connection.execute(
                """
                update research_stages
                set status = 'pending', started_at = '', completed_at = '', summary = '', error_message = ''
                where run_id = ? and status in ('running', 'paused', 'failed', 'cancelled')
                """,
                (run_id,),
            )
            next_stage = connection.execute(
                """
                select stage_key from research_stages
                where run_id = ? and status != 'completed'
                order by position limit 1
                """,
                (run_id,),
            ).fetchone()
            if next_stage is None:
                raise ValueError("Research run has no remaining stage to resume")
            connection.execute(
                """
                update research_runs
                set status = 'queued', current_stage = ?, cancel_requested = 0, pause_requested = 0,
                    completed_at = '', error_code = '', error_message = '',
                    interaction_json = '{}', recovery_json = '{}', updated_at = ?
                where run_id = ?
                """,
                (next_stage["stage_key"], now, run_id),
            )
            self._refresh_progress(connection, run_id)
            connection.commit()
        return self.get_run(run_id)

    def prepare_restart(self, run_id: str) -> dict[str, Any]:
        """Reset a terminal task in place so a short “重来” keeps its thread.

        Restarting is intentionally different from resuming: a completed task
        starts again from its first stage, while the existing run id and
        conversation messages remain intact.  Prior artifacts and tool calls
        stay in the database for auditability; clearing ``output_artifact_id``
        prevents the UI from showing stale output while the new attempt runs.
        """

        now = _utc_now()
        with self._connect() as connection:
            row = self._require_run(connection, run_id)
            status = str(row["status"])
            if status not in {"completed", "failed", "paused", "cancelled"}:
                raise ValueError(f"Research run is not restartable from status: {status}")
            first_stage = connection.execute(
                "select stage_key from research_stages where run_id = ? order by position limit 1",
                (run_id,),
            ).fetchone()
            if first_stage is None:
                raise ValueError("Research run has no stage to restart")
            connection.execute(
                """
                update research_stages
                set status = 'pending', started_at = '', completed_at = '',
                    summary = '', error_message = '', output_json = '{}'
                where run_id = ?
                """,
                (run_id,),
            )
            connection.execute(
                """
                update research_runs
                set status = 'queued', current_stage = ?, progress = 0,
                    output_artifact_id = '', cancel_requested = 0,
                    error_code = '', error_message = '', completed_at = '', updated_at = ?
                where run_id = ?
                """,
                (str(first_stage["stage_key"]), now, run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def start_stage(self, run_id: str, stage_key: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            stage = self._require_stage(connection, run_id, stage_key)
            if str(stage["status"]) == "completed":
                return self._stage_payload(stage)
            run_status = {
                "planner": "planning",
                "verification": "verifying",
            }.get(str(stage["kind"]), "running")
            connection.execute(
                """
                update research_stages
                set status = 'running', attempt = attempt + 1, started_at = ?, completed_at = '',
                    summary = '', error_message = ''
                where run_id = ? and stage_key = ?
                """,
                (now, run_id, stage_key),
            )
            connection.execute(
                "update research_runs set status = ?, current_stage = ?, updated_at = ? where run_id = ?",
                (run_status, stage_key, now, run_id),
            )
            self._append_event(
                connection,
                run_id,
                event_type="stage.started",
                summary=str(stage["title"]),
                payload={"stage_key": stage_key, "attempt": int(stage["attempt"] or 0) + 1},
                created_at=now,
                stage_id=str(stage["stage_id"]),
            )
            connection.commit()
            return self._stage_payload(self._require_stage(connection, run_id, stage_key))

    def complete_stage(
        self,
        run_id: str,
        stage_key: str,
        *,
        summary: str = "",
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            stage = self._require_stage(connection, run_id, stage_key)
            connection.execute(
                """
                update research_stages
                set status = 'completed', completed_at = ?, summary = ?, output_json = ?, error_message = ''
                where run_id = ? and stage_key = ?
                """,
                (now, str(summary), _json_dumps(output or {}), run_id, stage_key),
            )
            self._refresh_progress(connection, run_id)
            connection.execute("update research_runs set updated_at = ? where run_id = ?", (now, run_id))
            self._append_event(
                connection,
                run_id,
                event_type="stage.completed",
                summary=str(summary).strip() or str(stage_key),
                payload={"stage_key": stage_key},
                created_at=now,
                stage_id=str(stage["stage_id"]),
            )
            connection.commit()
        return self.get_run(run_id)

    def update_stage_progress(
        self,
        run_id: str,
        stage_key: str,
        *,
        fraction: float,
        summary: str = "",
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist observable progress for a long-running stage.

        Stage status remains ``running`` until the worker explicitly completes
        or cancels it.  This makes partial vector-cache builds safe to resume
        without falsely presenting them as finished.
        """

        stage_fraction = max(0.0, min(1.0, float(fraction)))
        now = _utc_now()
        with self._connect() as connection:
            stage = self._require_stage(connection, run_id, stage_key)
            if str(stage["status"]) != "running":
                return self.get_run(run_id)
            completed = int(
                connection.execute(
                    "select count(*) from research_stages where run_id = ? and status = 'completed'",
                    (run_id,),
                ).fetchone()[0]
                or 0
            )
            total = int(
                connection.execute(
                    "select count(*) from research_stages where run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                or 0
            )
            progress = (completed + stage_fraction) / total if total else stage_fraction
            connection.execute(
                """
                update research_stages
                set summary = ?, output_json = ?
                where run_id = ? and stage_key = ?
                """,
                (str(summary), _json_dumps(output or {}), run_id, stage_key),
            )
            connection.execute(
                "update research_runs set progress = ?, updated_at = ? where run_id = ?",
                (progress, now, run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def fail_stage(
        self,
        run_id: str,
        stage_key: str,
        error: Exception,
        *,
        output_artifact_id: str = "",
    ) -> dict[str, Any]:
        now = _utc_now()
        message = redact_sensitive_text(str(error) or error.__class__.__name__)
        with self._connect() as connection:
            self._require_stage(connection, run_id, stage_key)
            run = self._require_run(connection, run_id)
            # A worker that reports a stale failure after the run has already
            # reached a terminal state must not mutate state or append another
            # terminal event.
            if str(run["status"]) in {"completed", "failed", "cancelled"}:
                connection.commit()
                return self.get_run(run_id)
            connection.execute(
                """
                update research_stages
                set status = 'failed', completed_at = ?, error_message = ?
                where run_id = ? and stage_key = ?
                """,
                (now, message, run_id, stage_key),
            )
            connection.execute(
                """
                update research_runs
                set status = 'failed', error_code = 'stage_failed', error_message = ?,
                    output_artifact_id = case when ? != '' then ? else output_artifact_id end,
                    completed_at = ?, updated_at = ? where run_id = ?
                """,
                (message, str(output_artifact_id), str(output_artifact_id), now, now, run_id),
            )
            self._refresh_progress(connection, run_id)
            self._append_event(
                connection,
                run_id,
                event_type="stage.failed",
                summary=message,
                payload={"stage_key": stage_key},
                created_at=now,
                error_category=classify_error(error, operation="stage"),
            )
            self._append_event(
                connection,
                run_id,
                event_type="task.failed",
                summary=message,
                payload={"stage_key": stage_key},
                created_at=now,
                terminal=True,
                error_category=classify_error(error, operation="stage"),
            )
            connection.commit()
        return self.get_run(run_id)

    def complete_run(self, run_id: str, *, output_artifact_id: str = "") -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            self._require_run(connection, run_id)
            connection.execute(
                """
                update research_runs
                set status = 'completed', progress = 1, current_stage = '', output_artifact_id = ?,
                    cancel_requested = 0, pause_requested = 0, error_code = '', error_message = '', completed_at = ?, updated_at = ?
                where run_id = ?
                """,
                (str(output_artifact_id), now, now, run_id),
            )
            self._append_event(
                connection,
                run_id,
                event_type="task.completed",
                summary="任务已完成",
                payload={"output_artifact_id": str(output_artifact_id)},
                created_at=now,
                terminal=True,
            )
            connection.commit()
        return self.get_run(run_id)

    def stage_output(self, run_id: str, stage_key: str) -> dict[str, Any]:
        with self._connect() as connection:
            stage = self._require_stage(connection, run_id, stage_key)
            return _json_loads(stage["output_json"])

    def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        actor: str = "system",
        stage_id: str = "",
        request_id: str = "",
        tool_id: str = "",
        parent_event_id: str = "",
        input_value: Any = None,
        output_value: Any = None,
        input_hash: str = "",
        output_hash: str = "",
        idempotency_key: str = "",
        terminal: bool = False,
        error_category: str = "",
    ) -> dict[str, Any]:
        """Append an auditable, redacted task event without changing task state."""

        now = _utc_now()
        with self._connect() as connection:
            self._require_run(connection, run_id)
            event_id = self._append_event(
                connection,
                run_id,
                event_type=event_type,
                summary=summary,
                payload=payload,
                created_at=now,
                actor=actor,
                stage_id=stage_id,
                request_id=request_id,
                tool_id=tool_id,
                parent_event_id=parent_event_id,
                input_value=input_value,
                output_value=output_value,
                input_hash=input_hash,
                output_hash=output_hash,
                idempotency_key=idempotency_key,
                terminal=terminal,
                error_category=error_category,
            )
            connection.execute("update research_runs set updated_at = ? where run_id = ?", (now, run_id))
            connection.commit()
            row = connection.execute(
                "select * from research_run_events where event_id = ?", (event_id,)
            ).fetchone()
            return self._event_payload(row)

    def begin_tool_call(
        self,
        run_id: str,
        stage_key: str,
        *,
        tool_name: str,
        input_payload: dict[str, Any],
    ) -> str:
        call_id = _new_id("call")
        now = _utc_now()
        with self._connect() as connection:
            stage = self._require_stage(connection, run_id, stage_key)
            connection.execute(
                """
                insert into research_tool_calls (
                  tool_call_id, run_id, stage_id, tool_name, status,
                  input_json, output_json, error_message, started_at, completed_at, duration_ms
                ) values (?, ?, ?, ?, 'running', ?, '{}', '', ?, '', 0)
                """,
                (
                    call_id,
                    run_id,
                    stage["stage_id"],
                    str(tool_name),
                    _json_dumps(_redact_sensitive_value(input_payload)),
                    now,
                ),
            )
            self._append_event(
                connection,
                run_id,
                event_type="tool.started",
                summary=str(tool_name),
                payload={"stage_key": stage_key, "tool_call_id": call_id},
                created_at=now,
                stage_id=str(stage["stage_id"]),
                tool_id=call_id,
                input_value=input_payload,
            )
            connection.commit()
        return call_id

    def complete_tool_call(self, tool_call_id: str, output: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            row = self._require_tool_call(connection, tool_call_id)
            duration = _duration_ms(str(row["started_at"]), now)
            connection.execute(
                """
                update research_tool_calls
                set status = 'completed', output_json = ?, completed_at = ?, duration_ms = ?, error_message = ''
                where tool_call_id = ?
                """,
                (_json_dumps(_redact_sensitive_value(output)), now, duration, tool_call_id),
            )
            self._append_event(
                connection,
                str(row["run_id"]),
                event_type="tool.completed",
                summary=str(row["tool_name"]),
                payload={"tool_call_id": tool_call_id, "duration_ms": duration},
                created_at=now,
                stage_id=str(row["stage_id"]),
                tool_id=tool_call_id,
                output_value=output,
            )
            connection.commit()
            return self._tool_call_payload(self._require_tool_call(connection, tool_call_id))

    def fail_tool_call(self, tool_call_id: str, error: Exception) -> dict[str, Any]:
        now = _utc_now()
        message = redact_sensitive_text(str(error) or error.__class__.__name__)
        with self._connect() as connection:
            row = self._require_tool_call(connection, tool_call_id)
            duration = _duration_ms(str(row["started_at"]), now)
            connection.execute(
                """
                update research_tool_calls
                set status = 'failed', completed_at = ?, duration_ms = ?, error_message = ?
                where tool_call_id = ?
                """,
                (now, duration, message, tool_call_id),
            )
            self._append_event(
                connection,
                str(row["run_id"]),
                event_type="tool.failed",
                summary=message,
                payload={"tool_call_id": tool_call_id, "tool_name": str(row["tool_name"])},
                created_at=now,
                stage_id=str(row["stage_id"]),
                tool_id=tool_call_id,
                error_category=classify_error(error, operation="tool"),
            )
            connection.commit()
            return self._tool_call_payload(self._require_tool_call(connection, tool_call_id))

    def cancel_tool_call(self, tool_call_id: str, output: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record a cooperative tool cancellation without converting it to failure."""

        now = _utc_now()
        with self._connect() as connection:
            row = self._require_tool_call(connection, tool_call_id)
            duration = _duration_ms(str(row["started_at"]), now)
            connection.execute(
                """
                update research_tool_calls
                set status = 'cancelled', output_json = ?, completed_at = ?, duration_ms = ?, error_message = ''
                where tool_call_id = ?
                """,
                (_json_dumps(_redact_sensitive_value(output or {})), now, duration, tool_call_id),
            )
            self._append_event(
                connection,
                str(row["run_id"]),
                event_type="tool.cancelled",
                summary=str(row["tool_name"]),
                payload={"tool_call_id": tool_call_id, "duration_ms": duration},
                created_at=now,
                stage_id=str(row["stage_id"]),
                tool_id=tool_call_id,
                output_value=output or {},
                error_category="user_cancelled",
            )
            connection.commit()
            return self._tool_call_payload(self._require_tool_call(connection, tool_call_id))

    def checkpoint_stage(
        self,
        run_id: str,
        stage_key: str,
        *,
        checkpoint_key: str,
        state: dict[str, Any],
        status: str = "completed",
        input_value: Any = None,
        output_value: Any = None,
        input_hash: str = "",
        output_hash: str = "",
        idempotency_key: str = "",
        summary: str = "",
    ) -> dict[str, Any]:
        """Persist a resumable stage checkpoint and its trace atomically."""

        normalized_key = str(checkpoint_key or "").strip()
        if not normalized_key:
            raise ValueError("checkpoint_key is required")
        now = _utc_now()
        resolved_input_hash = str(input_hash or "") or _value_hash(input_value)
        resolved_output_hash = str(output_hash or "") or _value_hash(output_value if output_value is not None else state)
        with self._connect() as connection:
            stage = self._require_stage(connection, run_id, stage_key)
            existing = connection.execute(
                "select * from research_run_checkpoints where run_id = ? and stage_key = ? and checkpoint_key = ?",
                (str(run_id), str(stage_key), normalized_key),
            ).fetchone()
            if existing is None:
                checkpoint_id = _new_id("checkpoint")
                connection.execute(
                    """
                    insert into research_run_checkpoints (
                      checkpoint_id, run_id, stage_key, checkpoint_key, status, state_json,
                      input_hash, output_hash, idempotency_key, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint_id,
                        str(run_id),
                        str(stage_key),
                        normalized_key,
                        str(status or "completed"),
                        _json_dumps(_redact_sensitive_value(dict(state or {}))),
                        resolved_input_hash,
                        resolved_output_hash,
                        str(idempotency_key or ""),
                        now,
                        now,
                    ),
                )
            else:
                checkpoint_id = str(existing["checkpoint_id"])
                connection.execute(
                    """
                    update research_run_checkpoints
                    set status = ?, state_json = ?, input_hash = ?, output_hash = ?,
                        idempotency_key = case when ? <> '' then ? else idempotency_key end,
                        updated_at = ?
                    where checkpoint_id = ?
                    """,
                    (
                        str(status or "completed"),
                        _json_dumps(_redact_sensitive_value(dict(state or {}))),
                        resolved_input_hash,
                        resolved_output_hash,
                        str(idempotency_key or ""),
                        str(idempotency_key or ""),
                        now,
                        checkpoint_id,
                    ),
                )
            self._append_event(
                connection,
                run_id,
                event_type="stage.checkpointed",
                summary=str(summary or normalized_key),
                payload={"stage_key": stage_key, "checkpoint_key": normalized_key, "status": str(status)},
                created_at=now,
                stage_id=str(stage["stage_id"]),
                input_hash=resolved_input_hash,
                output_hash=resolved_output_hash,
                idempotency_key=str(idempotency_key or ""),
            )
            connection.commit()
            row = connection.execute(
                "select * from research_run_checkpoints where checkpoint_id = ?", (checkpoint_id,)
            ).fetchone()
            return self._checkpoint_payload(row)

    def list_checkpoints(self, run_id: str, *, stage_key: str = "") -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._require_run(connection, run_id)
            if stage_key:
                rows = connection.execute(
                    "select * from research_run_checkpoints where run_id = ? and stage_key = ? order by updated_at",
                    (str(run_id), str(stage_key)),
                ).fetchall()
            else:
                rows = connection.execute(
                    "select * from research_run_checkpoints where run_id = ? order by updated_at",
                    (str(run_id),),
                ).fetchall()
            return [self._checkpoint_payload(row) for row in rows]

    def acquire_worker_lease(
        self,
        run_id: str,
        stage_key: str,
        *,
        worker_id: str,
        ttl_seconds: int = 30,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Claim one stage until heartbeat expiry, preventing duplicate workers."""

        now = _utc_now()
        expires = _lease_expiry(ttl_seconds)
        with self._connect() as connection:
            stage = self._require_stage(connection, run_id, stage_key)
            existing = connection.execute(
                "select * from research_worker_leases where run_id = ? and stage_key = ?",
                (str(run_id), str(stage_key)),
            ).fetchone()
            if existing is not None and str(existing["status"]) == "active" and not _is_expired(str(existing["expires_at"])):
                if str(existing["worker_id"]) != str(worker_id):
                    raise RuntimeError(f"Stage lease is held by worker {str(existing['worker_id'])}")
                lease_id = str(existing["lease_id"])
                connection.execute(
                    """
                    update research_worker_leases
                    set heartbeat_at = ?, expires_at = ?, metadata_json = ? where lease_id = ?
                    """,
                    (now, expires, _json_dumps(_redact_sensitive_value(dict(metadata or {}))), lease_id),
                )
            else:
                lease_id = str(existing["lease_id"]) if existing is not None else _new_id("lease")
                connection.execute(
                    """
                    insert into research_worker_leases (
                      lease_id, run_id, stage_key, worker_id, status, acquired_at,
                      heartbeat_at, expires_at, metadata_json
                    ) values (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    on conflict(run_id, stage_key) do update set
                      worker_id = excluded.worker_id, status = 'active', acquired_at = excluded.acquired_at,
                      heartbeat_at = excluded.heartbeat_at, expires_at = excluded.expires_at,
                      metadata_json = excluded.metadata_json
                    """,
                    (
                        lease_id,
                        str(run_id),
                        str(stage_key),
                        str(worker_id),
                        now,
                        now,
                        expires,
                        _json_dumps(_redact_sensitive_value(dict(metadata or {}))),
                    ),
                )
            self._append_event(
                connection,
                run_id,
                event_type="worker.lease.acquired",
                summary=str(worker_id),
                payload={"stage_key": stage_key, "lease_id": lease_id, "expires_at": expires},
                created_at=now,
                stage_id=str(stage["stage_id"]),
                idempotency_key=f"lease:{stage_key}:{worker_id}",
            )
            connection.commit()
            row = connection.execute("select * from research_worker_leases where lease_id = ?", (lease_id,)).fetchone()
            return self._lease_payload(row)

    def heartbeat_worker_lease(self, lease_id: str, *, ttl_seconds: int = 30) -> dict[str, Any]:
        now = _utc_now()
        expires = _lease_expiry(ttl_seconds)
        with self._connect() as connection:
            row = connection.execute("select * from research_worker_leases where lease_id = ?", (str(lease_id),)).fetchone()
            if row is None:
                raise FileNotFoundError(f"Worker lease does not exist: {lease_id}")
            if str(row["status"]) != "active" or _is_expired(str(row["expires_at"])):
                raise RuntimeError("Worker lease has expired")
            connection.execute(
                "update research_worker_leases set heartbeat_at = ?, expires_at = ? where lease_id = ?",
                (now, expires, str(lease_id)),
            )
            connection.commit()
            return self._lease_payload(connection.execute("select * from research_worker_leases where lease_id = ?", (str(lease_id),)).fetchone())

    def release_worker_lease(self, lease_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute("select * from research_worker_leases where lease_id = ?", (str(lease_id),)).fetchone()
            if row is None:
                raise FileNotFoundError(f"Worker lease does not exist: {lease_id}")
            connection.execute(
                "update research_worker_leases set status = 'released', heartbeat_at = ?, expires_at = ? where lease_id = ?",
                (now, now, str(lease_id)),
            )
            connection.commit()
            return self._lease_payload(connection.execute("select * from research_worker_leases where lease_id = ?", (str(lease_id),)).fetchone())

    def reap_expired_worker_leases(self) -> int:
        now = _utc_now()
        with self._connect() as connection:
            updated = connection.execute(
                "update research_worker_leases set status = 'expired' where status = 'active' and expires_at <= ?",
                (now,),
            )
            connection.commit()
            return int(updated.rowcount or 0)

    def record_side_effect_receipt(
        self,
        run_id: str,
        *,
        effect_type: str,
        effect_key: str,
        status: str,
        result: dict[str, Any] | None = None,
        input_value: Any = None,
        output_value: Any = None,
        input_hash: str = "",
        output_hash: str = "",
        error: BaseException | str | None = None,
    ) -> dict[str, Any]:
        """Record download/index/export side effects so retries can be skipped."""

        normalized_type = str(effect_type or "").strip()
        normalized_key = str(effect_key or "").strip()
        if not normalized_type or not normalized_key:
            raise ValueError("effect_type and effect_key are required")
        now = _utc_now()
        category = classify_error(error, operation=normalized_type) if error else ""
        message = redact_sensitive_text(str(error or ""))[:600]
        resolved_input_hash = str(input_hash or "") or _value_hash(input_value)
        resolved_output_hash = str(output_hash or "") or _value_hash(output_value if output_value is not None else result)
        with self._connect() as connection:
            self._require_run(connection, run_id)
            existing = connection.execute(
                "select * from research_side_effect_receipts where run_id = ? and effect_type = ? and effect_key = ?",
                (str(run_id), normalized_type, normalized_key),
            ).fetchone()
            if existing is not None and str(existing["status"]) == "completed" and str(status or "") == "completed":
                connection.commit()
                return self._receipt_payload(existing)
            if existing is None:
                receipt_id = _new_id("receipt")
                connection.execute(
                    """
                    insert into research_side_effect_receipts (
                      receipt_id, run_id, effect_type, effect_key, status, input_hash,
                      output_hash, result_json, error_category, error_message, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        str(run_id),
                        normalized_type,
                        normalized_key,
                        str(status or "pending"),
                        resolved_input_hash,
                        resolved_output_hash,
                        _json_dumps(_redact_sensitive_value(dict(result or {}))),
                        category,
                        message,
                        now,
                        now,
                    ),
                )
            else:
                receipt_id = str(existing["receipt_id"])
                connection.execute(
                    """
                    update research_side_effect_receipts
                    set status = ?, input_hash = ?, output_hash = ?, result_json = ?,
                        error_category = ?, error_message = ?, updated_at = ?
                    where receipt_id = ?
                    """,
                    (
                        str(status or "pending"),
                        resolved_input_hash,
                        resolved_output_hash,
                        _json_dumps(_redact_sensitive_value(dict(result or {}))),
                        category,
                        message,
                        now,
                        receipt_id,
                    ),
                )
            event_suffix = "progress" if str(status) in {"running", "progress", "pending"} else str(status)
            self._append_event(
                connection,
                run_id,
                event_type=f"{normalized_type}.{event_suffix}",
                summary=f"{normalized_type}: {normalized_key}",
                payload={"effect_type": normalized_type, "effect_key": normalized_key, "status": str(status), "receipt_id": receipt_id},
                created_at=now,
                input_hash=resolved_input_hash,
                output_hash=resolved_output_hash,
                error_category=category,
            )
            connection.commit()
            row = connection.execute("select * from research_side_effect_receipts where receipt_id = ?", (receipt_id,)).fetchone()
            return self._receipt_payload(row)

    def get_side_effect_receipt(self, run_id: str, *, effect_type: str, effect_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "select * from research_side_effect_receipts where run_id = ? and effect_type = ? and effect_key = ?",
                (str(run_id), str(effect_type), str(effect_key)),
            ).fetchone()
            return self._receipt_payload(row) if row is not None else None

    def side_effect_receipt(self, run_id: str, *, effect_type: str, effect_key: str) -> dict[str, Any] | None:
        return self.get_side_effect_receipt(run_id, effect_type=effect_type, effect_key=effect_key)

    def create_artifact(
        self,
        run_id: str,
        *,
        artifact_type: str,
        title: str,
        summary: str,
        payload: dict[str, Any],
        evidence_links: Iterable[dict[str, Any]] = (),
        file_path: str = "",
        schema_version: str = "research_artifact.v1",
    ) -> dict[str, Any]:
        artifact_id = _new_id("artifact")
        now = _utc_now()
        with self._connect() as connection:
            run = self._require_run(connection, run_id)
            connection.execute(
                """
                insert into research_artifacts (
                  artifact_id, run_id, notebook_id, artifact_type, schema_version,
                  title, summary, payload_json, file_path, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    run_id,
                    run["notebook_id"],
                    str(artifact_type),
                    str(schema_version),
                    str(title),
                    str(summary),
                    _json_dumps(payload),
                    str(file_path),
                    now,
                ),
            )
            for link in evidence_links:
                item = dict(link)
                connection.execute(
                    """
                    insert into research_evidence_links (
                      evidence_link_id, artifact_id, run_id, evidence_id, doc_id,
                      html_anchor, source_href, relationship, quote_snapshot, metadata_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id("evidence"),
                        artifact_id,
                        run_id,
                        str(item.get("evidence_id", "")),
                        str(item.get("doc_id", "")),
                        str(item.get("html_anchor", "")),
                        str(item.get("source_href", "") or item.get("reader_url", "")),
                        str(item.get("relationship", "supports")),
                        str(item.get("exact_quote", "") or item.get("quote_snapshot", "")),
                        _json_dumps(dict(item.get("metadata", {}) or {})),
                    ),
                )
            self._append_event(
                connection,
                run_id,
                event_type="artifact.created",
                summary=str(title),
                payload={"artifact_id": artifact_id, "artifact_type": str(artifact_type)},
                created_at=now,
                input_value=payload,
                output_value={"artifact_id": artifact_id, "file_path": str(file_path)},
            )
            connection.commit()
            row = connection.execute(
                "select * from research_artifacts where artifact_id = ?", (artifact_id,)
            ).fetchone()
            return self._artifact_payload(connection, row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.workspace, timeout=15, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma busy_timeout = 15000")
        return connection

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            create table if not exists research_runs (
              run_id text primary key,
              notebook_id text not null,
              workflow_type text not null,
              title text not null,
              status text not null,
              current_stage text not null,
              progress real not null,
              input_json text not null,
              output_artifact_id text not null,
              cancel_requested integer not null,
              pause_requested integer not null default 0,
              error_code text not null,
              error_message text not null,
              model_provider_id text not null,
              model_id text not null,
              created_at text not null,
              updated_at text not null,
              started_at text not null,
              completed_at text not null,
              metadata_json text not null,
              archived_at text not null default '',
              parent_run_id text not null default '',
              branch_from_message_id text not null default '',
              background integer not null default 1,
              interaction_json text not null default '{}',
              recovery_json text not null default '{}',
              task_contract_json text not null default '{}'
            );
            create table if not exists research_stages (
              stage_id text primary key,
              run_id text not null,
              stage_key text not null,
              position integer not null,
              title text not null,
              kind text not null,
              tool_name text not null,
              status text not null,
              attempt integer not null,
              started_at text not null,
              completed_at text not null,
              summary text not null,
              error_message text not null,
              input_json text not null,
              output_json text not null,
              unique(run_id, stage_key),
              foreign key(run_id) references research_runs(run_id)
            );
            create table if not exists research_tool_calls (
              tool_call_id text primary key,
              run_id text not null,
              stage_id text not null,
              tool_name text not null,
              status text not null,
              input_json text not null,
              output_json text not null,
              error_message text not null,
              started_at text not null,
              completed_at text not null,
              duration_ms integer not null,
              foreign key(run_id) references research_runs(run_id),
              foreign key(stage_id) references research_stages(stage_id)
            );
            create table if not exists research_artifacts (
              artifact_id text primary key,
              run_id text not null,
              notebook_id text not null,
              artifact_type text not null,
              schema_version text not null,
              title text not null,
              summary text not null,
              payload_json text not null,
              file_path text not null,
              created_at text not null,
              foreign key(run_id) references research_runs(run_id)
            );
            create table if not exists research_evidence_links (
              evidence_link_id text primary key,
              artifact_id text not null,
              run_id text not null,
              evidence_id text not null,
              doc_id text not null,
              html_anchor text not null,
              source_href text not null,
              relationship text not null,
              quote_snapshot text not null,
              metadata_json text not null,
              foreign key(artifact_id) references research_artifacts(artifact_id),
              foreign key(run_id) references research_runs(run_id)
            );
            create table if not exists research_run_messages (
              message_id text primary key,
              run_id text not null,
              role text not null,
              content text not null,
              usage_json text not null,
              processing_ms integer not null,
              created_at text not null,
              foreign key(run_id) references research_runs(run_id)
            );
            create table if not exists research_run_events (
              event_id text primary key,
              run_id text not null,
              event_type text not null,
              summary text not null,
              payload_json text not null,
              created_at text not null,
              foreign key(run_id) references research_runs(run_id)
            );
            create index if not exists idx_research_runs_notebook on research_runs(notebook_id, updated_at);
            create index if not exists idx_research_stages_run on research_stages(run_id, position);
            create index if not exists idx_research_calls_run on research_tool_calls(run_id, started_at);
            create index if not exists idx_research_artifacts_run on research_artifacts(run_id, created_at);
            create index if not exists idx_research_evidence_artifact on research_evidence_links(artifact_id);
            create index if not exists idx_research_messages_run on research_run_messages(run_id, created_at);
            create index if not exists idx_research_events_run on research_run_events(run_id, created_at);
            """
        )
        columns = {str(row["name"]) for row in connection.execute("pragma table_info(research_runs)")}
        if "archived_at" not in columns:
            connection.execute("alter table research_runs add column archived_at text not null default ''")
        if "parent_run_id" not in columns:
            connection.execute("alter table research_runs add column parent_run_id text not null default ''")
        if "branch_from_message_id" not in columns:
            connection.execute("alter table research_runs add column branch_from_message_id text not null default ''")
        if "background" not in columns:
            connection.execute("alter table research_runs add column background integer not null default 1")
        if "interaction_json" not in columns:
            connection.execute("alter table research_runs add column interaction_json text not null default '{}'")
        if "recovery_json" not in columns:
            connection.execute("alter table research_runs add column recovery_json text not null default '{}'")
        if "task_contract_json" not in columns:
            connection.execute("alter table research_runs add column task_contract_json text not null default '{}'")
        if "pause_requested" not in columns:
            connection.execute("alter table research_runs add column pause_requested integer not null default 0")
        connection.execute(
            "create index if not exists idx_research_runs_archived on research_runs(archived_at, updated_at)"
        )
        connection.execute(
            "create index if not exists idx_research_runs_parent on research_runs(parent_run_id, updated_at)"
        )
        apply_migrations(connection, RESEARCH_SCHEMA_NAME, _research_migrations(), target_version=RESEARCH_SCHEMA_VERSION)

    def _run_summary(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        stage_counts = connection.execute(
            """
            select count(*) as total,
                   sum(case when status = 'completed' then 1 else 0 end) as completed
            from research_stages where run_id = ?
            """,
            (row["run_id"],),
        ).fetchone()
        event_count = connection.execute(
            "select count(*) from research_run_events where run_id = ?", (row["run_id"],)
        ).fetchone()[0]
        last_sequence = connection.execute(
            "select coalesce(max(sequence), 0) from research_run_events where run_id = ?", (row["run_id"],)
        ).fetchone()[0]
        return {
            "run_id": str(row["run_id"]),
            "uri": run_uri(row["run_id"]),
            "notebook_id": str(row["notebook_id"]),
            "workflow_type": str(row["workflow_type"]),
            "title": str(row["title"]),
            "status": str(row["status"]),
            "current_stage": str(row["current_stage"]),
            "progress": float(row["progress"]),
            "output_artifact_id": str(row["output_artifact_id"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "pause_requested": bool(row["pause_requested"]),
            "error": {
                "code": str(row["error_code"]),
                "message": str(row["error_message"]),
            },
            "model": {
                "provider_id": str(row["model_provider_id"]),
                "model_id": str(row["model_id"]),
            },
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "started_at": str(row["started_at"]),
            "completed_at": str(row["completed_at"]),
            "archived_at": str(row["archived_at"]),
            "archived": bool(row["archived_at"]),
            "parent_run_id": str(row["parent_run_id"]),
            "branch_from_message_id": str(row["branch_from_message_id"]),
            "background": bool(row["background"]),
            "idempotency_key": str(row["idempotency_key"]),
            "interaction": _json_loads(row["interaction_json"]),
            "recovery": _json_loads(row["recovery_json"]),
            "task_contract": _json_loads(row["task_contract_json"]),
            "stage_counts": {
                "total": int(stage_counts["total"] or 0),
                "completed": int(stage_counts["completed"] or 0),
            },
            "event_count": int(event_count or 0),
            "last_event_sequence": int(last_sequence or 0),
            "resumable": str(row["status"]) in RESUMABLE_RUN_STATUSES,
            "cancellable": str(row["status"]) in ACTIVE_RUN_STATUSES,
            "pausable": str(row["status"]) in ACTIVE_RUN_STATUSES or bool(row["pause_requested"]),
        }

    @staticmethod
    def _require_manageable_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = ResearchRunStore._require_run(connection, run_id)
        if str(row["status"]) in ACTIVE_RUN_STATUSES | {"needs_confirmation"}:
            raise ValueError("运行中的对话不能归档或删除，请先等待任务结束或停止任务。")
        return row

    @staticmethod
    def _stage_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "stage_id": str(row["stage_id"]),
            "run_id": str(row["run_id"]),
            "key": str(row["stage_key"]),
            "position": int(row["position"]),
            "title": str(row["title"]),
            "kind": str(row["kind"]),
            "tool_name": str(row["tool_name"]),
            "status": str(row["status"]),
            "attempt": int(row["attempt"]),
            "started_at": str(row["started_at"]),
            "completed_at": str(row["completed_at"]),
            "summary": str(row["summary"]),
            "error_message": str(row["error_message"]),
            "input": _json_loads(row["input_json"]),
            "output": _json_loads(row["output_json"]),
        }

    @staticmethod
    def _tool_call_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "tool_call_id": str(row["tool_call_id"]),
            "run_id": str(row["run_id"]),
            "stage_id": str(row["stage_id"]),
            "tool_name": str(row["tool_name"]),
            "status": str(row["status"]),
            "input": _json_loads(row["input_json"]),
            "output": _json_loads(row["output_json"]),
            "error_message": str(row["error_message"]),
            "started_at": str(row["started_at"]),
            "completed_at": str(row["completed_at"]),
            "duration_ms": int(row["duration_ms"]),
        }

    @staticmethod
    def _message_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "message_id": str(row["message_id"]),
            "run_id": str(row["run_id"]),
            "role": str(row["role"]),
            "content": str(row["content"]),
            "usage": _json_loads(row["usage_json"]),
            "processing_ms": int(row["processing_ms"]),
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        run_id: str,
        *,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        created_at: str,
        canonical_type: str = "",
        actor: str = "system",
        stage_id: str = "",
        request_id: str = "",
        tool_id: str = "",
        parent_event_id: str = "",
        input_value: Any = None,
        output_value: Any = None,
        input_hash: str = "",
        output_hash: str = "",
        idempotency_key: str = "",
        terminal: bool = False,
        error_category: str = "",
    ) -> str:
        normalized_idempotency_key = str(idempotency_key or "").strip()
        resolved_canonical_type = str(canonical_type or "") or _CANONICAL_EVENT_TYPES.get(str(event_type), str(event_type))
        if normalized_idempotency_key:
            existing = connection.execute(
                "select event_id from research_run_events where run_id = ? and idempotency_key = ?",
                (str(run_id), normalized_idempotency_key),
            ).fetchone()
            if existing is not None:
                return str(existing[0])
        if terminal:
            existing = connection.execute(
                """
                select event_id from research_run_events
                where run_id = ? and terminal = 1 and (canonical_type = ? or event_type = ?)
                order by sequence desc limit 1
                """,
                (str(run_id), resolved_canonical_type, str(event_type)),
            ).fetchone()
            if existing is not None:
                return str(existing[0])
        event_id = _new_id("event")
        sequence = int(
            connection.execute(
                "select coalesce(max(sequence), 0) + 1 from research_run_events where run_id = ?",
                (str(run_id),),
            ).fetchone()[0]
            or 1
        )
        resolved_input_hash = str(input_hash or "") or _value_hash(input_value)
        resolved_output_hash = str(output_hash or "") or _value_hash(output_value)
        try:
            connection.execute(
                """
                insert into research_run_events (
                  event_id, run_id, event_type, summary, payload_json, created_at,
                  schema_version, sequence, timestamp, actor, stage_id, request_id,
                  tool_id, parent_event_id, input_hash, output_hash, idempotency_key,
                  terminal, canonical_type, error_category
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(run_id),
                    str(event_type),
                    redact_sensitive_text(str(summary or ""))[:600],
                    _json_dumps(_redact_sensitive_value(dict(payload or {}))),
                    str(created_at),
                    SCIENTIFIC_EVENT_SCHEMA_VERSION,
                    sequence,
                    str(created_at),
                    redact_sensitive_text(str(actor or "system"))[:120],
                    str(stage_id or ""),
                    str(request_id or ""),
                    str(tool_id or ""),
                    str(parent_event_id or ""),
                    resolved_input_hash,
                    resolved_output_hash,
                    normalized_idempotency_key,
                    1 if terminal else 0,
                    resolved_canonical_type,
                    str(error_category or ""),
                ),
            )
        except sqlite3.IntegrityError:
            if normalized_idempotency_key:
                existing = connection.execute(
                    "select event_id from research_run_events where run_id = ? and idempotency_key = ?",
                    (str(run_id), normalized_idempotency_key),
                ).fetchone()
                if existing is not None:
                    return str(existing[0])
            raise
        return event_id

    @staticmethod
    def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": str(row["event_id"]),
            "run_id": str(row["run_id"]),
            "type": str(row["event_type"]),
            "summary": str(row["summary"]),
            "payload": _json_loads(row["payload_json"]),
            "created_at": str(row["created_at"]),
            "schema_version": str(row["schema_version"]),
            "sequence": int(row["sequence"] or 0),
            "timestamp": str(row["timestamp"] or row["created_at"]),
            "actor": str(row["actor"]),
            "stage_id": str(row["stage_id"]),
            "request_id": str(row["request_id"]),
            "tool_id": str(row["tool_id"]),
            "parent_event_id": str(row["parent_event_id"]),
            "input_hash": str(row["input_hash"]),
            "output_hash": str(row["output_hash"]),
            "idempotency_key": str(row["idempotency_key"]),
            "terminal": bool(row["terminal"]),
            "canonical_type": str(row["canonical_type"] or row["event_type"]),
            "error_category": str(row["error_category"]),
        }

    @staticmethod
    def _checkpoint_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "checkpoint_id": str(row["checkpoint_id"]),
            "run_id": str(row["run_id"]),
            "stage_key": str(row["stage_key"]),
            "checkpoint_key": str(row["checkpoint_key"]),
            "status": str(row["status"]),
            "state": _json_loads(row["state_json"]),
            "input_hash": str(row["input_hash"]),
            "output_hash": str(row["output_hash"]),
            "idempotency_key": str(row["idempotency_key"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _lease_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "lease_id": str(row["lease_id"]),
            "run_id": str(row["run_id"]),
            "stage_key": str(row["stage_key"]),
            "worker_id": str(row["worker_id"]),
            "status": str(row["status"]),
            "acquired_at": str(row["acquired_at"]),
            "heartbeat_at": str(row["heartbeat_at"]),
            "expires_at": str(row["expires_at"]),
            "metadata": _json_loads(row["metadata_json"]),
        }

    @staticmethod
    def _receipt_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "receipt_id": str(row["receipt_id"]),
            "run_id": str(row["run_id"]),
            "effect_type": str(row["effect_type"]),
            "effect_key": str(row["effect_key"]),
            "status": str(row["status"]),
            "input_hash": str(row["input_hash"]),
            "output_hash": str(row["output_hash"]),
            "result": _json_loads(row["result_json"]),
            "error_category": str(row["error_category"]),
            "error_message": str(row["error_message"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _artifact_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        links = connection.execute(
            "select * from research_evidence_links where artifact_id = ? order by evidence_link_id",
            (row["artifact_id"],),
        ).fetchall()
        return {
            "artifact_id": str(row["artifact_id"]),
            "uri": artifact_uri(run_id=row["run_id"], artifact_id=row["artifact_id"]),
            "run_id": str(row["run_id"]),
            "notebook_id": str(row["notebook_id"]),
            "artifact_type": str(row["artifact_type"]),
            "schema_version": str(row["schema_version"]),
            "title": str(row["title"]),
            "summary": str(row["summary"]),
            "payload": _json_loads(row["payload_json"]),
            "file_path": str(row["file_path"]),
            "created_at": str(row["created_at"]),
            "evidence_links": [
                {
                    "evidence_link_id": str(link["evidence_link_id"]),
                    "uri": evidence_uri(
                        doc_id=link["doc_id"],
                        evidence_id=link["evidence_id"],
                        anchor=link["html_anchor"],
                    ),
                    "evidence_id": str(link["evidence_id"]),
                    "doc_id": str(link["doc_id"]),
                    "html_anchor": str(link["html_anchor"]),
                    "source_href": str(link["source_href"]),
                    "relationship": str(link["relationship"]),
                    "quote_snapshot": str(link["quote_snapshot"]),
                    "metadata": _json_loads(link["metadata_json"]),
                }
                for link in links
            ],
        }

    @staticmethod
    def _require_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute("select * from research_runs where run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"Research run does not exist: {run_id}")
        return row

    @staticmethod
    def _require_stage(connection: sqlite3.Connection, run_id: str, stage_key: str) -> sqlite3.Row:
        row = connection.execute(
            "select * from research_stages where run_id = ? and stage_key = ?", (run_id, stage_key)
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Research stage does not exist: {run_id}/{stage_key}")
        return row

    @staticmethod
    def _require_tool_call(connection: sqlite3.Connection, tool_call_id: str) -> sqlite3.Row:
        row = connection.execute(
            "select * from research_tool_calls where tool_call_id = ?", (tool_call_id,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Research tool call does not exist: {tool_call_id}")
        return row

    @staticmethod
    def _refresh_progress(connection: sqlite3.Connection, run_id: str) -> None:
        counts = connection.execute(
            """
            select count(*) as total,
                   sum(case when status = 'completed' then 1 else 0 end) as completed
            from research_stages where run_id = ?
            """,
            (run_id,),
        ).fetchone()
        total = int(counts["total"] or 0)
        completed = int(counts["completed"] or 0)
        progress = completed / total if total else 0
        connection.execute("update research_runs set progress = ? where run_id = ?", (progress, run_id))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_dumps_value(value: Any) -> str:
    return json.dumps(_redact_sensitive_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _value_hash(value: Any) -> str:
    if value is None:
        return ""
    return hashlib.sha256(_json_dumps_value(value).encode("utf-8")).hexdigest()


def _json_loads(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _duration_ms(started_at: str, completed_at: str) -> int:
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except ValueError:
        return 0
    return max(0, int((completed - started).total_seconds() * 1000))


def _lease_expiry(ttl_seconds: int) -> str:
    ttl = max(1, min(24 * 60 * 60, int(ttl_seconds or 30)))
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat(timespec="seconds")


def _is_expired(value: str) -> bool:
    try:
        expiry = datetime.fromisoformat(str(value))
    except ValueError:
        return True
    return expiry <= datetime.now(timezone.utc)
