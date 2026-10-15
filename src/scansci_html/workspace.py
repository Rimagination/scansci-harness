from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .annotation_layers import load_annotation_layers
from .resolver import safe_identifier_part
from .schema_migrations import Migration, apply_migrations, current_schema_version


SCHEMA_VERSION = "notebook_workspace.v1"
WORKSPACE_SCHEMA_NAME = "notebook_workspace"
WORKSPACE_SCHEMA_VERSION = 2
DISPLAYABLE_CITATION_STATUSES = {"supported", "partial_support"}


def _workspace_migrations() -> tuple[Migration, ...]:
    return (
        Migration(1, "notebook workspace baseline registry", lambda _connection: None),
        Migration(2, "workspace terminology and recovery compatibility", lambda _connection: None),
    )


def initialize_notebook(
    workspace_path: str | Path,
    *,
    notebook_id: str = "",
    title: str = "",
    description: str = "",
    root_path: str | Path = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    workspace = Path(workspace_path)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    resolved_title = str(title or Path(root_path or Path.cwd()).name or "ScanSci Notebook").strip()
    resolved_root = _path_string(root_path or Path.cwd())
    resolved_notebook_id = _safe_object_id(notebook_id) if notebook_id else _generated_id("nb", resolved_title, resolved_root)
    now = _utc_now()
    with sqlite3.connect(workspace) as connection:
        _initialize_schema(connection)
        existing = connection.execute(
            "select created_at from notebooks where notebook_id = ?",
            (resolved_notebook_id,),
        ).fetchone()
        created_at = str(existing[0]) if existing else now
        connection.execute(
            """
            insert into notebooks (
              notebook_id, title, description, root_path, created_at, updated_at, metadata_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            on conflict(notebook_id) do update set
              title = excluded.title,
              description = excluded.description,
              root_path = excluded.root_path,
              updated_at = excluded.updated_at,
              metadata_json = excluded.metadata_json
            """,
            (
                resolved_notebook_id,
                resolved_title,
                str(description or ""),
                resolved_root,
                created_at,
                now,
                _json_dumps_object(metadata or {}),
            ),
        )
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "notebook_id": resolved_notebook_id,
        "title": resolved_title,
        "description": str(description or ""),
        "root_path": resolved_root,
        "created": not bool(existing),
    }


def delete_notebook(workspace_path: str | Path, *, notebook_id: str) -> dict[str, object]:
    """Remove one notebook record and every workspace artifact it owns.

    This deliberately leaves linked source files alone.  A caller that owns an
    isolated evidence index may remove that rebuildable cache separately.
    """

    workspace = Path(workspace_path)
    resolved_notebook_id = _safe_object_id(notebook_id)
    with sqlite3.connect(workspace) as connection:
        _initialize_schema(connection)
        row = connection.execute(
            "select title from notebooks where notebook_id = ?",
            (resolved_notebook_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Notebook does not exist: {resolved_notebook_id}")
        title = str(row[0])
        citation_ids = [
            str(item[0])
            for item in connection.execute(
                "select citation_record_id from citation_records where notebook_id = ?",
                (resolved_notebook_id,),
            ).fetchall()
        ]
        layer_ids = [
            str(item[0])
            for item in connection.execute(
                "select layer_object_id from layers where notebook_id = ?",
                (resolved_notebook_id,),
            ).fetchall()
        ]
        if citation_ids:
            placeholders = ", ".join("?" for _ in citation_ids)
            connection.execute(f"delete from citation_audits where citation_record_id in ({placeholders})", citation_ids)
        if layer_ids:
            placeholders = ", ".join("?" for _ in layer_ids)
            connection.execute(f"delete from layer_sources where layer_object_id in ({placeholders})", layer_ids)
        connection.execute("delete from citation_records where notebook_id = ?", (resolved_notebook_id,))
        connection.execute("delete from layers where notebook_id = ?", (resolved_notebook_id,))
        connection.execute("delete from notes where notebook_id = ?", (resolved_notebook_id,))
        connection.execute("delete from sources where notebook_id = ?", (resolved_notebook_id,))
        connection.execute("delete from notebooks where notebook_id = ?", (resolved_notebook_id,))
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "notebook_id": resolved_notebook_id,
        "title": title,
        "linked_files_preserved": True,
    }


def sync_sources_from_evidence_store(
    workspace_path: str | Path,
    evidence_db_path: str | Path,
    *,
    notebook_id: str,
    metadata: dict[str, Any] | None = None,
    replace: bool = False,
) -> dict[str, object]:
    workspace = Path(workspace_path)
    evidence_db = Path(evidence_db_path)
    resolved_notebook_id = _safe_object_id(notebook_id)
    source_rows = _load_source_documents(evidence_db)
    now = _utc_now()
    with sqlite3.connect(workspace) as connection:
        _initialize_schema(connection)
        _ensure_notebook(connection, resolved_notebook_id)
        if replace:
            connection.execute("delete from sources where notebook_id = ?", (resolved_notebook_id,))
        for row in source_rows:
            doc_id = str(row.get("doc_id", "") or "")
            source_id = _source_object_id(resolved_notebook_id, doc_id)
            connection.execute(
                """
                insert into sources (
                  source_id,
                  notebook_id,
                  doc_id,
                  title,
                  doi,
                  source_url,
                  publication_year,
                  html_path,
                  evidence_html_path,
                  evidence_db_path,
                  created_at,
                  updated_at,
                  metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source_id) do update set
                  title = excluded.title,
                  doi = excluded.doi,
                  source_url = excluded.source_url,
                  publication_year = excluded.publication_year,
                  html_path = excluded.html_path,
                  evidence_html_path = excluded.evidence_html_path,
                  evidence_db_path = excluded.evidence_db_path,
                  updated_at = excluded.updated_at,
                  metadata_json = excluded.metadata_json
                """,
                (
                    source_id,
                    resolved_notebook_id,
                    doc_id,
                    str(row.get("title", "") or ""),
                    str(row.get("doi", "") or ""),
                    str(row.get("source_url", "") or ""),
                    row.get("publication_year"),
                    str(row.get("html_path", "") or ""),
                    str(row.get("evidence_html_path", "") or ""),
                    str(evidence_db),
                    now,
                    now,
                    _json_dumps_object(metadata or {}),
                ),
            )
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "notebook_id": resolved_notebook_id,
        "evidence_db_path": str(evidence_db),
        "sources": len(source_rows),
    }


def update_evidence_db_paths(
    workspace_path: str | Path,
    mappings: dict[str, str],
) -> int:
    """Update durable workspace references after an evidence-store move.

    Sources and annotation layers both retain evidence database paths.  Keep
    the update transactional so a failed migration cannot leave a workspace
    pointing at only half of the copied stores.
    """

    if not mappings:
        return 0
    workspace = Path(workspace_path)
    normalized = {
        str(Path(source).expanduser().resolve()): str(Path(target).expanduser().resolve())
        for source, target in mappings.items()
        if str(source).strip() and str(target).strip()
    }
    if not normalized:
        return 0
    updated = 0
    with closing(sqlite3.connect(workspace)) as connection:
        _initialize_schema(connection)
        for source, target in normalized.items():
            cursor = connection.execute(
                "update sources set evidence_db_path = ?, updated_at = ? where evidence_db_path = ?",
                (target, _utc_now(), source),
            )
            updated += int(cursor.rowcount or 0)
            connection.execute(
                "update layers set evidence_db_path = ?, updated_at = ? where evidence_db_path = ?",
                (target, _utc_now(), source),
            )
        connection.commit()
    return updated


def set_notebook_root_path(
    workspace_path: str | Path,
    *,
    notebook_id: str,
    root_path: str | Path,
    metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Persist the folder currently used as a notebook's source library."""

    workspace = Path(workspace_path)
    resolved_notebook_id = _safe_object_id(notebook_id)
    resolved_root = _path_string(root_path)
    now = _utc_now()
    with sqlite3.connect(workspace) as connection:
        _initialize_schema(connection)
        row = connection.execute(
            "select title, metadata_json from notebooks where notebook_id = ?",
            (resolved_notebook_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Notebook does not exist: {resolved_notebook_id}")
        existing_metadata = _json_loads_object(str(row[1] or "{}"))
        updated_metadata = {**existing_metadata, **dict(metadata or {})}
        connection.execute(
            "update notebooks set root_path = ?, updated_at = ?, metadata_json = ? where notebook_id = ?",
            (resolved_root, now, _json_dumps_object(updated_metadata), resolved_notebook_id),
        )
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "notebook_id": resolved_notebook_id,
        "root_path": resolved_root,
        "metadata": updated_metadata,
    }


def update_notebook_metadata(
    workspace_path: str | Path,
    *,
    notebook_id: str,
    metadata: dict[str, Any],
) -> dict[str, object]:
    """Merge local-library connection metadata without changing the active source root."""

    workspace = Path(workspace_path)
    resolved_notebook_id = _safe_object_id(notebook_id)
    now = _utc_now()
    with sqlite3.connect(workspace) as connection:
        _initialize_schema(connection)
        row = connection.execute(
            "select metadata_json from notebooks where notebook_id = ?",
            (resolved_notebook_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Notebook does not exist: {resolved_notebook_id}")
        updated_metadata = {**_json_loads_object(str(row[0] or "{}")), **dict(metadata or {})}
        connection.execute(
            "update notebooks set metadata_json = ?, updated_at = ? where notebook_id = ?",
            (_json_dumps_object(updated_metadata), now, resolved_notebook_id),
        )
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "notebook_id": resolved_notebook_id,
        "metadata": updated_metadata,
    }


def add_note_to_notebook(
    workspace_path: str | Path,
    *,
    notebook_id: str,
    title: str,
    body: str,
    note_id: str = "",
    note_type: str = "research_note",
    source_path: str | Path = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    workspace = Path(workspace_path)
    resolved_notebook_id = _safe_object_id(notebook_id)
    resolved_title = str(title or _first_line(body) or "Untitled note").strip()
    resolved_note_id = _safe_object_id(note_id) if note_id else _generated_id("note", resolved_notebook_id, resolved_title, body)
    now = _utc_now()
    with sqlite3.connect(workspace) as connection:
        _initialize_schema(connection)
        _ensure_notebook(connection, resolved_notebook_id)
        existing = connection.execute(
            "select created_at from notes where note_id = ?",
            (resolved_note_id,),
        ).fetchone()
        created_at = str(existing[0]) if existing else now
        connection.execute(
            """
            insert into notes (
              note_id,
              notebook_id,
              title,
              body,
              note_type,
              source_path,
              created_at,
              updated_at,
              metadata_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(note_id) do update set
              notebook_id = excluded.notebook_id,
              title = excluded.title,
              body = excluded.body,
              note_type = excluded.note_type,
              source_path = excluded.source_path,
              updated_at = excluded.updated_at,
              metadata_json = excluded.metadata_json
            """,
            (
                resolved_note_id,
                resolved_notebook_id,
                resolved_title,
                str(body or ""),
                str(note_type or "research_note"),
                _path_string(source_path),
                created_at,
                now,
                _json_dumps_object(metadata or {}),
            ),
        )
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "notebook_id": resolved_notebook_id,
        "note_id": resolved_note_id,
        "title": resolved_title,
        "note_type": str(note_type or "research_note"),
        "created": not bool(existing),
    }


def attach_annotation_layers_to_notebook(
    workspace_path: str | Path,
    layer_db_path: str | Path,
    *,
    notebook_id: str,
    layer_ids: Iterable[str] | None = None,
    note_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    workspace = Path(workspace_path)
    layer_db = Path(layer_db_path)
    resolved_notebook_id = _safe_object_id(notebook_id)
    layers = load_annotation_layers(layer_db, layer_ids=[str(layer_id) for layer_id in layer_ids] if layer_ids else None)
    now = _utc_now()
    resolved_note_id = _safe_object_id(note_id) if note_id else ""
    attached_ids: list[str] = []
    citation_records = 0
    with sqlite3.connect(workspace) as connection:
        _initialize_schema(connection)
        _ensure_notebook(connection, resolved_notebook_id)
        for layer in layers:
            annotation_layer_id = str(layer.get("layer_id", "") or "")
            layer_object_id = _layer_object_id(resolved_notebook_id, layer_db, annotation_layer_id)
            evidence_db_path = str(layer.get("evidence_db_path", "") or "")
            connection.execute(
                """
                insert into layers (
                  layer_object_id,
                  notebook_id,
                  annotation_layer_id,
                  layer_db_path,
                  note_id,
                  name,
                  question,
                  evidence_db_path,
                  created_at,
                  updated_at,
                  metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(layer_object_id) do update set
                  note_id = excluded.note_id,
                  name = excluded.name,
                  question = excluded.question,
                  evidence_db_path = excluded.evidence_db_path,
                  updated_at = excluded.updated_at,
                  metadata_json = excluded.metadata_json
                """,
                (
                    layer_object_id,
                    resolved_notebook_id,
                    annotation_layer_id,
                    str(layer_db),
                    resolved_note_id,
                    str(layer.get("name", "") or ""),
                    str(layer.get("question", "") or ""),
                    evidence_db_path,
                    str(layer.get("created_at", "") or now),
                    now,
                    _json_dumps_object(metadata or {}),
                ),
            )
            connection.execute("delete from layer_sources where layer_object_id = ?", (layer_object_id,))
            for doc_id, count in _layer_doc_counts(layer).items():
                source_id = _source_object_id(resolved_notebook_id, doc_id)
                connection.execute(
                    """
                    insert into layer_sources (layer_object_id, source_id, doc_id, evidence_count)
                    values (?, ?, ?, ?)
                    """,
                    (layer_object_id, source_id, doc_id, count),
                )
            citation_records += _sync_citation_records_for_layer(
                connection,
                notebook_id=resolved_notebook_id,
                layer_db=layer_db,
                layer=layer,
                layer_object_id=layer_object_id,
                note_id=resolved_note_id,
                metadata=metadata or {},
                now=now,
            )
            attached_ids.append(layer_object_id)
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "notebook_id": resolved_notebook_id,
        "layer_db_path": str(layer_db),
        "layers": len(attached_ids),
        "layer_object_ids": attached_ids,
        "citation_records": citation_records,
    }


def list_citation_records(
    workspace_path: str | Path,
    *,
    notebook_id: str = "",
    note_id: str = "",
    layer_object_id: str = "",
    support_statuses: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    workspace = Path(workspace_path)
    if not workspace.exists():
        return []
    with sqlite3.connect(workspace) as connection:
        connection.row_factory = sqlite3.Row
        _initialize_schema(connection)
        return _citation_records(
            connection,
            notebook_id=notebook_id,
            note_id=note_id,
            layer_object_id=layer_object_id,
            support_statuses=support_statuses,
        )


def record_citation_audit(
    workspace_path: str | Path,
    *,
    citation_record_id: str,
    provider: str,
    verdict: str,
    reasoning: str = "",
    confidence: float | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, object]:
    workspace = Path(workspace_path)
    now = _utc_now()
    normalized_record_id = str(citation_record_id or "").strip()
    if not normalized_record_id:
        raise ValueError("citation_record_id cannot be empty")
    with sqlite3.connect(workspace) as connection:
        connection.row_factory = sqlite3.Row
        _initialize_schema(connection)
        record = connection.execute(
            "select citation_record_id from citation_records where citation_record_id = ?",
            (normalized_record_id,),
        ).fetchone()
        if record is None:
            raise ValueError(f"Citation record does not exist: {normalized_record_id}")
        audit_id = _generated_id(
            "audit",
            normalized_record_id,
            provider,
            verdict,
            reasoning,
            now,
        )
        confidence_value = None if confidence is None else float(confidence)
        connection.execute(
            """
            insert into citation_audits (
              citation_audit_id,
              citation_record_id,
              provider,
              verdict,
              reasoning,
              confidence,
              created_at,
              payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                normalized_record_id,
                str(provider or ""),
                str(verdict or ""),
                str(reasoning or ""),
                confidence_value,
                now,
                _json_dumps_object(payload or {}),
            ),
        )
        connection.commit()
    return {
        "workspace_path": str(workspace),
        "citation_audit_id": audit_id,
        "citation_record_id": normalized_record_id,
        "provider": str(provider or ""),
        "verdict": str(verdict or ""),
        "confidence": confidence_value,
    }


def load_workspace_summary(
    workspace_path: str | Path,
    *,
    notebook_id: str = "",
) -> dict[str, object]:
    workspace = Path(workspace_path)
    workspace_directory = str(workspace.expanduser().resolve().parent)
    if not workspace.exists():
        return {
            "workspace_path": str(workspace),
            "workspace_directory": workspace_directory,
            "notebooks": [],
            "counts": {"notebooks": 0, "sources": 0, "notes": 0, "layers": 0},
        }
    # ``with sqlite3.connect(...)`` manages only the transaction; it does not
    # close the connection.  Close deterministically: on Windows an open
    # sqlite handle keeps the file locked until GC, and a reference cycle can
    # delay that past the caller's cleanup.
    with closing(sqlite3.connect(workspace)) as connection:
        connection.row_factory = sqlite3.Row
        _initialize_schema(connection)
        notebook_where, params = _notebook_where(notebook_id)
        notebook_rows = connection.execute(
            f"""
            select notebook_id, title, description, root_path, created_at, updated_at, metadata_json
            from notebooks
            {notebook_where}
            order by updated_at desc, notebook_id
            """,
            params,
        ).fetchall()
        notebooks = [_notebook_summary(connection, dict(row)) for row in notebook_rows]
        if notebook_id:
            counts = notebooks[0]["counts"] if notebooks else {
                "sources": 0,
                "notes": 0,
                "layers": 0,
                "citations": 0,
                "citation_audits": 0,
            }
            counts = {"notebooks": len(notebooks), **dict(counts)}
        else:
            counts = {
                "notebooks": len(notebooks),
                "sources": _count_rows(connection, "sources"),
                "notes": _count_rows(connection, "notes"),
                "layers": _count_rows(connection, "layers"),
                "citations": _count_rows(connection, "citation_records"),
                "citation_audits": _count_rows(connection, "citation_audits"),
            }
    for notebook in notebooks:
        if notebook.get("metadata", {}).get("library_kind") == "translation":
            notebook["translation_files"] = translation_folder_files(notebook["root_path"])
    return {
        "workspace_path": str(workspace),
        "workspace_directory": workspace_directory,
        "notebooks": notebooks,
        "counts": counts,
    }


def translation_folder_files(root_path: str | Path) -> list[dict[str, str]]:
    """List PDFs linked through a translation folder, including files added outside ScanSci."""

    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        return []
    files = []
    # ponytail: scan on read for modest local libraries; add a persistent file index if this grows to thousands of PDFs.
    for path in sorted(path for path in root.rglob("*") if path.suffix.lower() == ".pdf"):
        resolved = path.resolve()
        if not resolved.is_file() or root not in resolved.parents:
            continue
        relative = path.relative_to(root).as_posix()
        files.append({
            "id": hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24],
            "title": path.stem,
            "path": str(resolved),
            "relative_path": relative,
            "type": "PDF",
        })
    return files


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists notebooks (
            notebook_id text primary key,
            title text not null,
            description text not null,
            root_path text not null,
            created_at text not null,
            updated_at text not null,
            metadata_json text not null
        )
        """
    )
    connection.execute(
        """
        create table if not exists sources (
            source_id text primary key,
            notebook_id text not null,
            doc_id text not null,
            title text not null,
            doi text not null,
            source_url text not null,
            publication_year integer,
            html_path text not null,
            evidence_html_path text not null,
            evidence_db_path text not null,
            created_at text not null,
            updated_at text not null,
            metadata_json text not null,
            foreign key(notebook_id) references notebooks(notebook_id)
        )
        """
    )
    connection.execute(
        """
        create table if not exists notes (
            note_id text primary key,
            notebook_id text not null,
            title text not null,
            body text not null,
            note_type text not null,
            source_path text not null,
            created_at text not null,
            updated_at text not null,
            metadata_json text not null,
            foreign key(notebook_id) references notebooks(notebook_id)
        )
        """
    )
    connection.execute(
        """
        create table if not exists layers (
            layer_object_id text primary key,
            notebook_id text not null,
            annotation_layer_id text not null,
            layer_db_path text not null,
            note_id text not null,
            name text not null,
            question text not null,
            evidence_db_path text not null,
            created_at text not null,
            updated_at text not null,
            metadata_json text not null,
            foreign key(notebook_id) references notebooks(notebook_id)
        )
        """
    )
    connection.execute(
        """
        create table if not exists layer_sources (
            layer_object_id text not null,
            source_id text not null,
            doc_id text not null,
            evidence_count integer not null,
            primary key(layer_object_id, doc_id),
            foreign key(layer_object_id) references layers(layer_object_id)
        )
        """
    )
    connection.execute(
        """
        create table if not exists citation_records (
            citation_record_id text primary key,
            notebook_id text not null,
            note_id text not null,
            layer_object_id text not null,
            annotation_layer_id text not null,
            item_id text not null,
            segment_id text not null,
            citation_marker text not null,
            claim_text text not null,
            evidence_id text not null,
            doc_id text not null,
            support_status text not null,
            support_score real not null,
            review_state text not null,
            quote_snapshot text not null,
            source_href text not null,
            html_path text not null,
            html_anchor text not null,
            source_location_json text not null,
            created_at text not null,
            updated_at text not null,
            metadata_json text not null,
            foreign key(notebook_id) references notebooks(notebook_id),
            foreign key(layer_object_id) references layers(layer_object_id)
        )
        """
    )
    connection.execute(
        """
        create table if not exists citation_audits (
            citation_audit_id text primary key,
            citation_record_id text not null,
            provider text not null,
            verdict text not null,
            reasoning text not null,
            confidence real,
            created_at text not null,
            payload_json text not null,
            foreign key(citation_record_id) references citation_records(citation_record_id)
        )
        """
    )
    connection.execute("create unique index if not exists idx_sources_notebook_doc on sources(notebook_id, doc_id)")
    connection.execute("create index if not exists idx_notes_notebook on notes(notebook_id)")
    connection.execute(
        "create unique index if not exists idx_layers_notebook_layer on layers(notebook_id, layer_db_path, annotation_layer_id)"
    )
    connection.execute("create index if not exists idx_layers_notebook on layers(notebook_id)")
    connection.execute("create index if not exists idx_layer_sources_source on layer_sources(source_id)")
    connection.execute(
        "create unique index if not exists idx_citation_records_layer_item on citation_records(layer_object_id, item_id)"
    )
    connection.execute("create index if not exists idx_citation_records_notebook on citation_records(notebook_id)")
    connection.execute("create index if not exists idx_citation_records_note on citation_records(note_id)")
    connection.execute("create index if not exists idx_citation_records_evidence on citation_records(evidence_id)")
    connection.execute("create index if not exists idx_citation_audits_record on citation_audits(citation_record_id)")
    apply_migrations(connection, WORKSPACE_SCHEMA_NAME, _workspace_migrations(), target_version=WORKSPACE_SCHEMA_VERSION)


def _sync_citation_records_for_layer(
    connection: sqlite3.Connection,
    *,
    notebook_id: str,
    layer_db: Path,
    layer: dict[str, object],
    layer_object_id: str,
    note_id: str,
    metadata: dict[str, Any],
    now: str,
) -> int:
    annotation_layer_id = str(layer.get("layer_id", "") or "")
    existing_note_id = connection.execute(
        "select note_id from layers where layer_object_id = ?",
        (layer_object_id,),
    ).fetchone()
    resolved_note_id = note_id or (str(existing_note_id[0]) if existing_note_id else "")
    seen_record_ids: set[str] = set()
    for item in layer.get("items", []) or []:
        item_payload = dict(item)
        support_status = str(item_payload.get("support_status", "") or "weak_candidate")
        if support_status not in DISPLAYABLE_CITATION_STATUSES:
            continue
        evidence_id = str(item_payload.get("evidence_id", "") or "").strip()
        item_id = str(item_payload.get("item_id", "") or "").strip()
        if not evidence_id or not item_id:
            continue
        citation_record_id = _citation_record_id(notebook_id, layer_object_id, item_id)
        seen_record_ids.add(citation_record_id)
        created_at = _existing_created_at(connection, "citation_records", "citation_record_id", citation_record_id) or now
        source_location = _source_location_from_annotation_item(item_payload)
        connection.execute(
            """
            insert into citation_records (
              citation_record_id,
              notebook_id,
              note_id,
              layer_object_id,
              annotation_layer_id,
              item_id,
              segment_id,
              citation_marker,
              claim_text,
              evidence_id,
              doc_id,
              support_status,
              support_score,
              review_state,
              quote_snapshot,
              source_href,
              html_path,
              html_anchor,
              source_location_json,
              created_at,
              updated_at,
              metadata_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(citation_record_id) do update set
              note_id = excluded.note_id,
              annotation_layer_id = excluded.annotation_layer_id,
              item_id = excluded.item_id,
              segment_id = excluded.segment_id,
              citation_marker = excluded.citation_marker,
              claim_text = excluded.claim_text,
              evidence_id = excluded.evidence_id,
              doc_id = excluded.doc_id,
              support_status = excluded.support_status,
              support_score = excluded.support_score,
              review_state = excluded.review_state,
              quote_snapshot = excluded.quote_snapshot,
              source_href = excluded.source_href,
              html_path = excluded.html_path,
              html_anchor = excluded.html_anchor,
              source_location_json = excluded.source_location_json,
              updated_at = excluded.updated_at,
              metadata_json = excluded.metadata_json
            """,
            (
                citation_record_id,
                notebook_id,
                resolved_note_id,
                layer_object_id,
                annotation_layer_id,
                item_id,
                str(item_payload.get("segment_id", "") or ""),
                _citation_marker(item_payload),
                str(item_payload.get("claim_text", "") or ""),
                evidence_id,
                str(item_payload.get("doc_id", "") or ""),
                support_status,
                _float_or_zero(item_payload.get("support_score")),
                str(item_payload.get("review_state", "") or "unreviewed"),
                str(item_payload.get("quote", "") or ""),
                str(item_payload.get("source_href", "") or ""),
                str(item_payload.get("html_path", "") or ""),
                str(item_payload.get("html_anchor", "") or ""),
                json.dumps(source_location, ensure_ascii=False, sort_keys=True),
                created_at,
                now,
                _json_dumps_object({"layer_db_path": str(layer_db), **dict(metadata or {})}),
            ),
        )
    _delete_stale_citation_records(connection, layer_object_id=layer_object_id, keep_ids=seen_record_ids)
    return len(seen_record_ids)


def _citation_records(
    connection: sqlite3.Connection,
    *,
    notebook_id: str = "",
    note_id: str = "",
    layer_object_id: str = "",
    support_statuses: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    where: list[str] = []
    params: list[object] = []
    if notebook_id:
        where.append("notebook_id = ?")
        params.append(_safe_object_id(notebook_id))
    if note_id:
        where.append("note_id = ?")
        params.append(_safe_object_id(note_id))
    if layer_object_id:
        where.append("layer_object_id = ?")
        params.append(str(layer_object_id))
    status_values = [str(status) for status in support_statuses or [] if str(status)]
    if status_values:
        where.append(f"support_status in ({', '.join('?' for _ in status_values)})")
        params.extend(status_values)
    where_sql = f"where {' and '.join(where)}" if where else ""
    rows = _rows(
        connection,
        f"""
        select
          citation_record_id,
          notebook_id,
          note_id,
          layer_object_id,
          annotation_layer_id,
          item_id,
          segment_id,
          citation_marker,
          claim_text,
          evidence_id,
          doc_id,
          support_status,
          support_score,
          review_state,
          quote_snapshot,
          source_href,
          html_path,
          html_anchor,
          source_location_json,
          created_at,
          updated_at,
          metadata_json
        from citation_records
        {where_sql}
        order by updated_at desc, layer_object_id, segment_id, citation_marker, citation_record_id
        """,
        tuple(params),
    )
    for row in rows:
        row["source_location"] = _json_loads_object(str(row.pop("source_location_json") or "{}"))
        row["metadata"] = _json_loads_object(str(row.pop("metadata_json") or "{}"))
        row["audits"] = _citation_audits(connection, str(row["citation_record_id"]))
    return rows


def _citation_audits(connection: sqlite3.Connection, citation_record_id: str) -> list[dict[str, object]]:
    rows = _rows(
        connection,
        """
        select
          citation_audit_id,
          citation_record_id,
          provider,
          verdict,
          reasoning,
          confidence,
          created_at,
          payload_json
        from citation_audits
        where citation_record_id = ?
        order by created_at desc, citation_audit_id
        """,
        (citation_record_id,),
    )
    for row in rows:
        row["payload"] = _json_loads_object(str(row.pop("payload_json") or "{}"))
    return rows


def _load_source_documents(evidence_db: Path) -> list[dict[str, object]]:
    if not evidence_db.exists():
        raise FileNotFoundError(f"Evidence store does not exist: {evidence_db}")
    connection = sqlite3.connect(evidence_db)
    try:
        connection.row_factory = sqlite3.Row
        publication_year_expr = (
            "publication_year" if _has_column(connection, "source_documents", "publication_year") else "null as publication_year"
        )
        evidence_html_expr = (
            "evidence_html_path"
            if _has_column(connection, "source_documents", "evidence_html_path")
            else "'' as evidence_html_path"
        )
        rows = connection.execute(
            f"""
            select
              doc_id,
              title,
              doi,
              source_url,
              {publication_year_expr},
              html_path,
              {evidence_html_expr}
            from source_documents
            order by title, doc_id
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def _ensure_notebook(connection: sqlite3.Connection, notebook_id: str) -> None:
    normalized = _safe_object_id(notebook_id)
    if not normalized:
        raise ValueError("notebook_id cannot be empty")
    existing = connection.execute(
        "select 1 from notebooks where notebook_id = ?",
        (normalized,),
    ).fetchone()
    if existing:
        return
    now = _utc_now()
    connection.execute(
        """
        insert into notebooks (
          notebook_id, title, description, root_path, created_at, updated_at, metadata_json
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (normalized, normalized, "", _path_string(Path.cwd()), now, now, _json_dumps_object({"auto_created": True})),
    )


def _notebook_summary(connection: sqlite3.Connection, notebook: dict[str, Any]) -> dict[str, object]:
    notebook_id = str(notebook["notebook_id"])
    sources = _rows(
        connection,
        """
        select source_id, doc_id, title, doi, source_url, publication_year, html_path, evidence_html_path, evidence_db_path
        from sources
        where notebook_id = ?
        order by title, doc_id
        """,
        (notebook_id,),
    )
    notes = _rows(
        connection,
        """
        select note_id, title, note_type, source_path, created_at, updated_at, substr(body, 1, 240) as body_preview
        from notes
        where notebook_id = ?
        order by updated_at desc, note_id
        """,
        (notebook_id,),
    )
    layers = _rows(
        connection,
        """
        select
          l.layer_object_id,
          l.annotation_layer_id,
          l.layer_db_path,
          l.note_id,
          l.name,
          l.question,
          l.evidence_db_path,
          l.created_at,
          l.updated_at,
          coalesce(sum(ls.evidence_count), 0) as evidence_count,
          count(ls.doc_id) as source_documents
        from layers l
        left join layer_sources ls on ls.layer_object_id = l.layer_object_id
        where l.notebook_id = ?
        group by l.layer_object_id
        order by l.updated_at desc, l.layer_object_id
        """,
        (notebook_id,),
    )
    for layer in layers:
        layer["sources"] = _rows(
            connection,
            """
            select ls.source_id, ls.doc_id, ls.evidence_count, s.title, s.doi, s.html_path
            from layer_sources ls
            left join sources s on s.source_id = ls.source_id
            where ls.layer_object_id = ?
            order by ls.evidence_count desc, ls.doc_id
            """,
            (layer["layer_object_id"],),
        )
    citations = _citation_records(connection, notebook_id=notebook_id)
    citation_audit_count = sum(len(record.get("audits", []) or []) for record in citations)
    knowledge_counts = _knowledge_store_counts(sources)
    return {
        "notebook_id": notebook_id,
        "title": str(notebook.get("title", "") or ""),
        "description": str(notebook.get("description", "") or ""),
        "root_path": str(notebook.get("root_path", "") or ""),
        "created_at": str(notebook.get("created_at", "") or ""),
        "updated_at": str(notebook.get("updated_at", "") or ""),
        "metadata": _json_loads_object(str(notebook.get("metadata_json", "{}") or "{}")),
        "schema_version": SCHEMA_VERSION,
        "counts": {
            "sources": len(sources),
            "notes": len(notes),
            "layers": len(layers),
            "citations": len(citations),
            "citation_audits": citation_audit_count,
        },
        # Keep the legacy workspace counts stable for integrations while
        # exposing the actual evidence hierarchy with unambiguous units.
        "knowledge_counts": knowledge_counts,
        "sources": sources,
        "notes": notes,
        "layers": layers,
        "citations": citations,
    }


def _knowledge_store_counts(sources: list[dict[str, object]]) -> dict[str, int]:
    """Count durable knowledge layers without confusing spans with documents."""

    counts = {
        "documents": 0,
        "summaries": 0,
        "sections": 0,
        "evidence_spans": 0,
        "vectors": 0,
        "graph_nodes": 0,
        "graph_edges": 0,
    }
    db_paths = sorted(
        {
            str(row.get("evidence_db_path", "") or "").strip()
            for row in sources
            if str(row.get("evidence_db_path", "") or "").strip()
        }
    )
    tables = {
        "documents": "source_documents",
        "summaries": "document_cards",
        "sections": "document_sections",
        "evidence_spans": "evidence_spans",
        "vectors": "scansci_vector_cache_meta",
        "graph_nodes": "knowledge_graph_nodes",
        "graph_edges": "knowledge_graph_edges",
    }
    for raw_path in db_paths:
        try:
            with sqlite3.connect(raw_path) as evidence_connection:
                evidence_connection.execute("pragma query_only = on")
                for key, table in tables.items():
                    try:
                        counts[key] += int(
                            evidence_connection.execute(f"select count(*) from {table}").fetchone()[0] or 0
                        )
                    except sqlite3.Error:
                        # Older stores are upgraded lazily; missing derived
                        # tables therefore report zero instead of breaking the
                        # workspace page.
                        continue
        except (OSError, sqlite3.Error):
            continue
    return counts


def _rows(connection: sqlite3.Connection, query: str, params: tuple[object, ...]) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(query, params).fetchall()]


def _count_rows(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"select count(*) from {table_name}").fetchone()[0])


def _notebook_where(notebook_id: str) -> tuple[str, list[object]]:
    if not notebook_id:
        return "", []
    return "where notebook_id = ?", [_safe_object_id(notebook_id)]


def _layer_doc_counts(layer: dict[str, object]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in layer.get("items", []) or []:
        doc_id = str(dict(item).get("doc_id", "") or "").strip()
        if doc_id:
            counts[doc_id] += 1
    return dict(counts)


def _source_object_id(notebook_id: str, doc_id: str) -> str:
    return _generated_id("src", notebook_id, doc_id)


def _layer_object_id(notebook_id: str, layer_db: Path, annotation_layer_id: str) -> str:
    return _generated_id("layer", notebook_id, str(layer_db), annotation_layer_id)


def _citation_record_id(notebook_id: str, layer_object_id: str, item_id: str) -> str:
    return _generated_id("cite", notebook_id, layer_object_id, item_id)


def _citation_marker(item: dict[str, object]) -> str:
    citation_id = str(item.get("citation_id", "") or "").strip()
    if citation_id:
        return citation_id
    evidence_id = str(item.get("evidence_id", "") or "").strip()
    return evidence_id or str(item.get("item_id", "") or "").strip()


def _source_location_from_annotation_item(item: dict[str, object]) -> dict[str, object]:
    html_path = str(item.get("html_path", "") or "").strip()
    html_anchor = str(item.get("html_anchor", "") or "").strip()
    source_href = str(item.get("source_href", "") or "").strip()
    evidence_id = str(item.get("evidence_id", "") or "").strip()
    doc_id = str(item.get("doc_id", "") or "").strip()
    kind = "html_anchor" if html_path and html_anchor else "evidence_id"
    return {
        "kind": kind,
        "doc_id": doc_id,
        "evidence_id": evidence_id,
        "html_path": html_path,
        "html_anchor": html_anchor,
        "source_href": source_href,
    }


def _existing_created_at(
    connection: sqlite3.Connection,
    table_name: str,
    id_column: str,
    id_value: str,
) -> str:
    row = connection.execute(
        f"select created_at from {table_name} where {id_column} = ?",
        (id_value,),
    ).fetchone()
    return str(row[0]) if row else ""


def _delete_stale_citation_records(
    connection: sqlite3.Connection,
    *,
    layer_object_id: str,
    keep_ids: set[str],
) -> None:
    rows = connection.execute(
        "select citation_record_id from citation_records where layer_object_id = ?",
        (layer_object_id,),
    ).fetchall()
    stale_ids = [str(row[0]) for row in rows if str(row[0]) not in keep_ids]
    for citation_record_id in stale_ids:
        connection.execute("delete from citation_audits where citation_record_id = ?", (citation_record_id,))
        connection.execute("delete from citation_records where citation_record_id = ?", (citation_record_id,))


def _generated_id(prefix: str, *parts: object) -> str:
    seed = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    slug = safe_identifier_part(seed)[:42].strip("_")
    if slug == "paper" or not slug:
        return f"{prefix}_{digest}"
    return f"{prefix}_{slug}_{digest}"


def _safe_object_id(value: str) -> str:
    return safe_identifier_part(str(value or ""))


def _has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return column_name in {str(row[1]) for row in connection.execute(f"pragma table_info({table_name})")}


def _path_string(value: str | Path) -> str:
    if not value:
        return ""
    return str(Path(value))


def _json_dumps_object(value: dict[str, Any]) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True)


def _json_loads_object(value: str) -> dict[str, object]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _float_or_zero(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _first_line(value: str) -> str:
    for line in str(value or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return ""
