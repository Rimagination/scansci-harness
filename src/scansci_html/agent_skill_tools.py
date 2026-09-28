"""Fail-closed progressive Skill discovery for the Pi instruction plane.

Skills are untrusted instructions, not executable capabilities.  This module
therefore exposes a compact catalogue and bounded text reads without touching
the task capability lease, evidence policy, or tool dispatcher.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from .builtin_skills import builtin_skill_asset_path
from .skill_manager import installed_skills
from .skill_security import scan_skill_packages


SKILL_STATE_SCHEMA = "scansci.skill-state.v1"
DISCOVERY_DECISION_SCHEMA = "scansci.discovery-decision.v1"
MAX_SKILL_CATALOG_ITEMS = 64
MAX_SKILL_CATALOG_BYTES = 16 * 1024
MAX_SKILL_RESOURCE_BYTES = 64 * 1024
MAX_SKILL_TOTAL_BYTES = 256 * 1024
MAX_SKILL_INSTRUCTION_CALLS = 32

_TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv",
})
_URI_OR_DRIVE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class SkillAccessError(PermissionError):
    """Raised when a Skill or package resource fails an instruction-plane gate."""


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _assert_resource_size(path: Path, max_bytes: int) -> None:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise SkillAccessError("Skill resource could not be inspected") from error
    if size > max_bytes:
        raise SkillAccessError(
            f"Skill resource exceeds the individual {max_bytes}-byte limit"
        )


def _read_bounded_resource(path: Path, max_bytes: int) -> bytes:
    """Read at most ``max_bytes + 1`` bytes, rejecting a concurrent growth."""

    _assert_resource_size(path, max_bytes)
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as error:
        raise SkillAccessError("Skill resource could not be read") from error
    if len(raw) > max_bytes:
        raise SkillAccessError(
            f"Skill resource exceeds the individual {max_bytes}-byte limit"
        )
    return raw


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _package_snapshot(root: Path) -> tuple[str, dict[str, str]]:
    """Hash a package snapshot and every regular resource without following links."""

    if _is_link(root):
        raise SkillAccessError("Skill package root is a symlink or junction")
    digest = hashlib.sha256()
    resource_hashes: dict[str, str] = {}
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if _is_link(candidate):
            raise SkillAccessError("Skill package contains a symlink or junction escape")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise SkillAccessError("Skill package contains a non-regular resource")
        relative = candidate.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        resource_digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                resource_digest.update(chunk)
        resource_hashes[relative.decode("utf-8")] = f"sha256:{resource_digest.hexdigest()}"
    return f"sha256:{digest.hexdigest()}", resource_hashes


def _safe_source(record: Mapping[str, Any], identifier: str) -> str:
    if bool(record.get("builtin")):
        return f"builtin:{identifier}"
    source = str(record.get("source", "") or "").strip()
    if source.lower().startswith("https://") and len(source) <= 500 and not any(char.isspace() for char in source):
        try:
            parsed = urlsplit(source)
            if (
                parsed.scheme.lower() == "https"
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and "@" not in parsed.netloc
            ):
                port = parsed.port
                host = parsed.hostname.lower()
                netloc = f"[{host}]" if ":" in host else host
                if port is not None:
                    netloc = f"{netloc}:{port}"
                return urlunsplit(("https", netloc, parsed.path, "", ""))
        except ValueError:
            pass
    return f"installed:{identifier}"


def _compact_catalog(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for entry in entries:
        if len(compact) >= MAX_SKILL_CATALOG_ITEMS:
            break
        candidate = {
            "id": str(entry.get("id", ""))[:100],
            "name": str(entry.get("name", ""))[:100],
            "description": str(entry.get("description", ""))[:240],
            "source": str(entry.get("source", ""))[:500],
            "package_hash": str(entry.get("package_hash", ""))[:80],
        }
        encoded = json.dumps([*compact, candidate], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_SKILL_CATALOG_BYTES:
            break
        compact.append(candidate)
    return compact


class ProgressiveSkillRuntime:
    """One bounded Skill catalogue/load budget for a Pi request or resume."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        records: Iterable[Mapping[str, Any]] | None = None,
        restored_state: Mapping[str, Any] | None = None,
        priority_ids: Iterable[object] | None = None,
        max_resource_bytes: int = MAX_SKILL_RESOURCE_BYTES,
        max_total_bytes: int = MAX_SKILL_TOTAL_BYTES,
        max_instruction_calls: int = MAX_SKILL_INSTRUCTION_CALLS,
    ) -> None:
        self.workspace = Path(workspace)
        self.max_resource_bytes = max(1, min(int(max_resource_bytes), MAX_SKILL_RESOURCE_BYTES))
        self.max_total_bytes = max(self.max_resource_bytes, min(int(max_total_bytes), MAX_SKILL_TOTAL_BYTES))
        self.max_instruction_calls = max(1, min(int(max_instruction_calls), MAX_SKILL_INSTRUCTION_CALLS))
        self._instruction_calls = 0
        self._records: dict[str, dict[str, Any]] = {}
        self._rejections: dict[str, str] = {}
        self._loaded: dict[str, dict[str, Any]] = {}
        self._delivered: set[str] = set()
        self._loaded_bytes = 0
        source_records = list(records) if records is not None else installed_skills(self.workspace)
        for raw in source_records:
            self._consider_record(dict(raw))
        priority: dict[str, int] = {}
        for identifier in priority_ids or ():
            normalized = str(identifier or "").strip().lower()
            if normalized and normalized not in priority:
                priority[normalized] = len(priority)
        ordered_ids = sorted(
            self._records,
            key=lambda identifier: (
                0 if identifier in priority else 1,
                priority.get(identifier, len(priority)),
                identifier,
            ),
        )
        self._catalog = _compact_catalog(
            {
                "id": identifier,
                "name": self._records[identifier]["name"],
                "description": self._records[identifier]["description"],
                "source": self._records[identifier]["safe_source"],
                "package_hash": self._records[identifier]["package_hash"],
            }
            for identifier in ordered_ids
        )
        self._catalog_ids = {str(item["id"]) for item in self._catalog}
        self._restore(restored_state)

    def _consider_record(self, record: dict[str, Any]) -> None:
        identifier = str(record.get("id", "") or "").strip().lower()
        if not identifier:
            return
        try:
            if record.get("uninstalled") is True:
                raise SkillAccessError("Skill is uninstalled")
            if record.get("enabled", True) is not True:
                raise SkillAccessError("Skill is disabled")
            if record.get("available") is not True:
                raise SkillAccessError("Skill package is unavailable")
            package_text = str(record.get("package_path", "") or "").strip()
            skill_text = str(record.get("skill_file", "") or "").strip()
            if not package_text or not skill_text:
                raise SkillAccessError("Skill package metadata is incomplete")
            package = Path(package_text).expanduser()
            skill_file = Path(skill_text).expanduser()
            if _is_link(package):
                raise SkillAccessError("Skill package root is a symlink or junction")
            package = package.resolve(strict=True)
            skill_file = skill_file.resolve(strict=True)
            if not package.is_dir() or not skill_file.is_file():
                raise SkillAccessError("Skill package is not installed")
            try:
                skill_file.relative_to(package)
            except ValueError as error:
                raise SkillAccessError("Skill instructions escape the package root") from error
            builtin = bool(record.get("builtin"))
            if builtin:
                configured_path = str(record.get("path", "") or "")
                if configured_path:
                    if not configured_path.startswith("builtin:"):
                        raise SkillAccessError("Built-in Skill metadata does not name a built-in package")
                    expected = builtin_skill_asset_path(configured_path.removeprefix("builtin:")).resolve(strict=True)
                    if package != expected:
                        raise SkillAccessError("Built-in Skill package escapes the trusted asset root")
            security_fingerprint = ""
            if not builtin:
                security = dict(record.get("security_scan", {}) or {})
                verdict = str(security.get("verdict", "") or "").upper()
                fingerprint = str(security.get("fingerprint", "") or "")
                if verdict not in {"SAFE", "REVIEW"} or not fingerprint.startswith("sha256:"):
                    raise SkillAccessError("External Skill has no security-cleared fingerprint")
                live = scan_skill_packages(
                    [package],
                    source_type=str(record.get("source_type", "") or "local"),
                    source=str(record.get("source", "") or ""),
                )
                if (
                    str(live.get("verdict", "") or "").upper() == "BLOCKED"
                    or str(live.get("fingerprint", "") or "") != fingerprint
                ):
                    raise SkillAccessError("External Skill security fingerprint changed")
                security_fingerprint = fingerprint
            package_fingerprint, resource_hashes = _package_snapshot(package)
            self._records[identifier] = {
                **record,
                "id": identifier,
                "name": str(record.get("name", identifier) or identifier)[:100],
                "description": str(record.get("description", "") or "")[:240],
                "package": package,
                "skill_file": skill_file,
                "builtin": builtin,
                "package_hash": package_fingerprint,
                "resource_hashes": resource_hashes,
                "security_fingerprint": security_fingerprint,
                "safe_source": _safe_source(record, identifier),
            }
        except (OSError, RuntimeError, ValueError, SkillAccessError) as error:
            self._rejections[identifier] = str(error)[:300]

    def catalog(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._catalog]

    def _claim_instruction_call(self) -> None:
        if self._instruction_calls >= self.max_instruction_calls:
            raise SkillAccessError(
                f"Skill instruction-plane call limit of {self.max_instruction_calls} was exhausted"
            )
        self._instruction_calls += 1

    def search_skills(self, query: object = "", limit: object = 8) -> dict[str, Any]:
        self._claim_instruction_call()
        try:
            safe_limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            safe_limit = 8
        needle = re.sub(r"\s+", " ", str(query or "").strip().lower())[:240]
        words = [word for word in needle.split(" ") if word]

        def score(item: Mapping[str, Any]) -> int:
            identifier = str(item.get("id", "")).lower()
            text = "\n".join(
                str(item.get(key, "")).lower()
                for key in ("id", "name", "description", "source")
            )
            if not needle:
                return 1
            if identifier == needle:
                return 1000
            if needle in text:
                return 500
            return sum(100 for word in words if word in text)

        matches = [
            {**item, "score": score(item)}
            for item in self._catalog
        ]
        matches = [item for item in matches if int(item["score"]) > 0]
        matches.sort(key=lambda item: (-int(item["score"]), str(item["id"])))
        selected = matches[:safe_limit]
        for item in selected:
            score_value = int(item["score"])
            item["confidence"] = (
                0.99 if score_value >= 1000 else
                0.95 if score_value >= 500 else
                0.72 if score_value >= 200 else
                0.58 if score_value >= 100 else
                0.0
            )
        return {
            "schema_version": "scansci.skill-discovery.v1",
            "query": needle,
            "limit": safe_limit,
            "count": len(selected),
            "skills": selected,
            "decision": {
                "schema_version": DISCOVERY_DECISION_SCHEMA,
                "kind": "skill_selection",
                "source": "local_heuristic",
                "semantic_provider": "not_configured",
                "fallback_used": True,
                "candidate_count": len(matches),
                "selected": [str(item["id"]) for item in selected],
            },
        }

    def _assert_current_snapshot(self, record: Mapping[str, Any]) -> None:
        package = Path(record["package"])
        package_hash, resource_hashes = _package_snapshot(package)
        if (
            package_hash != str(record.get("package_hash", ""))
            or resource_hashes != dict(record.get("resource_hashes", {}) or {})
        ):
            raise SkillAccessError("Skill package snapshot hash changed after security clearance")
        if not bool(record.get("builtin")):
            live = scan_skill_packages(
                [package],
                source_type=str(record.get("source_type", "") or "local"),
                source=str(record.get("source", "") or ""),
            )
            if (
                str(live.get("verdict", "") or "").upper() == "BLOCKED"
                or str(live.get("fingerprint", "") or "") != str(record.get("security_fingerprint", ""))
            ):
                raise SkillAccessError("External Skill security fingerprint changed before loading")

    def _resource_path(self, identifier: str, resource: object) -> tuple[dict[str, Any], str, Path]:
        normalized_id = str(identifier or "").strip().lower()
        record = self._records.get(normalized_id)
        if record is None:
            reason = self._rejections.get(normalized_id, "Skill is not installed, enabled, and security-cleared")
            raise SkillAccessError(reason)
        if normalized_id not in self._catalog_ids:
            raise SkillAccessError("Skill is outside the bounded active catalog")
        raw_resource = str(resource or "SKILL.md").strip()
        if (
            not raw_resource
            or "\x00" in raw_resource
            or raw_resource.startswith(("/", "\\"))
            or _URI_OR_DRIVE.match(raw_resource)
        ):
            raise SkillAccessError("Skill resource path is absolute, URI-like, or invalid")
        portable = raw_resource.replace("\\", "/")
        parts = PurePosixPath(portable).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise SkillAccessError("Skill resource path traversal is forbidden")
        relative = PurePosixPath(*parts).as_posix()
        root = Path(record["package"])
        candidate = root.joinpath(*parts)
        cursor = root
        for part in parts:
            cursor = cursor / part
            if _is_link(cursor):
                raise SkillAccessError("Skill resource symlink or junction escape is forbidden")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as error:
            raise SkillAccessError("Skill resource escapes the package root or is missing") from error
        if not resolved.is_file():
            raise SkillAccessError("Skill resource is not a regular text file")
        if resolved.suffix.lower() not in _TEXT_SUFFIXES:
            raise SkillAccessError("Skill resource is not an approved text format")
        return record, relative, resolved

    def load_skill(
        self,
        skill_id: object,
        *,
        resource: object = None,
        provenance: object = "model",
    ) -> dict[str, Any]:
        self._claim_instruction_call()
        return self._read_skill(
            skill_id,
            resource=resource,
            provenance=provenance,
            require_restored=False,
        )

    def restore_skill(
        self,
        skill_id: object,
        *,
        resource: object = None,
    ) -> dict[str, Any]:
        """Rehydrate host-validated state without spending the model call lease."""

        return self._read_skill(
            skill_id,
            resource=resource,
            provenance="resume",
            require_restored=True,
        )

    def _read_skill(
        self,
        skill_id: object,
        *,
        resource: object,
        provenance: object,
        require_restored: bool,
    ) -> dict[str, Any]:
        record, relative, resolved = self._resource_path(str(skill_id or ""), resource)
        _assert_resource_size(resolved, self.max_resource_bytes)
        self._assert_current_snapshot(record)
        record, relative, resolved = self._resource_path(str(skill_id or ""), resource)
        key = f"{record['id']}:{relative}"
        cached = self._loaded.get(key)
        if require_restored and cached is None:
            raise SkillAccessError("Skill resource is absent from validated restored state")
        raw = _read_bounded_resource(resolved, self.max_resource_bytes)
        if b"\x00" in raw:
            raise SkillAccessError("Skill resource is not UTF-8 text")
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SkillAccessError("Skill resource is not UTF-8 text") from error
        content_hash = _sha256(raw)
        if content_hash != str(dict(record.get("resource_hashes", {}) or {}).get(relative, "")):
            raise SkillAccessError("Skill resource content changed after the cleared package snapshot")
        self._assert_current_snapshot(record)
        if cached is None:
            if self._loaded_bytes + len(raw) > self.max_total_bytes:
                raise SkillAccessError(
                    f"Skill resources exceed the cumulative {self.max_total_bytes}-byte limit"
                )
            metadata = {
                "skill_id": str(record["id"]),
                "resource": relative,
                "source": str(record["safe_source"]),
                "package_hash": str(record["package_hash"]),
                "content_hash": content_hash,
                "provenance": str(provenance or "model")[:32],
                "bytes": len(raw),
            }
            self._loaded[key] = metadata
            self._loaded_bytes += len(raw)
        else:
            if (
                str(cached.get("package_hash", "")) != str(record["package_hash"])
                or str(cached.get("content_hash", "")) != content_hash
            ):
                raise SkillAccessError("Loaded Skill hash changed during the session")
            metadata = cached
        if key in self._delivered:
            return {**dict(metadata), "already_loaded": True}
        self._delivered.add(key)
        return {**dict(metadata), "content": content}

    def _restore(self, restored_state: Mapping[str, Any] | None) -> None:
        raw = dict(restored_state or {})
        if raw.get("schema") != SKILL_STATE_SCHEMA:
            return
        for item in list(raw.get("loaded", []) or []):
            if not isinstance(item, Mapping):
                continue
            skill_id = str(item.get("skill_id", "") or "").lower()
            resource = str(item.get("resource", "") or "")
            try:
                record, relative, resolved = self._resource_path(skill_id, resource)
                _assert_resource_size(resolved, self.max_resource_bytes)
                self._assert_current_snapshot(record)
                record, relative, resolved = self._resource_path(skill_id, resource)
                data = _read_bounded_resource(resolved, self.max_resource_bytes)
            except (OSError, SkillAccessError):
                continue
            if (
                str(item.get("package_hash", "")) != str(record["package_hash"])
                or str(item.get("content_hash", "")) != _sha256(data)
                or str(dict(record.get("resource_hashes", {}) or {}).get(relative, "")) != _sha256(data)
                or int(item.get("bytes", -1) or -1) != len(data)
                or self._loaded_bytes + len(data) > self.max_total_bytes
            ):
                continue
            try:
                self._assert_current_snapshot(record)
            except SkillAccessError:
                continue
            key = f"{skill_id}:{relative}"
            metadata = {
                "skill_id": skill_id,
                "resource": relative,
                "source": str(record["safe_source"]),
                "package_hash": str(record["package_hash"]),
                "content_hash": _sha256(data),
                "provenance": str(item.get("provenance", "resume") or "resume")[:32],
                "bytes": len(data),
            }
            self._loaded[key] = metadata
            self._loaded_bytes += len(data)

    def state(self) -> dict[str, Any]:
        return {
            "schema": SKILL_STATE_SCHEMA,
            "loaded": [dict(self._loaded[key]) for key in sorted(self._loaded)],
            "total_bytes": self._loaded_bytes,
        }


__all__ = [
    "MAX_SKILL_CATALOG_BYTES",
    "MAX_SKILL_CATALOG_ITEMS",
    "MAX_SKILL_INSTRUCTION_CALLS",
    "MAX_SKILL_RESOURCE_BYTES",
    "MAX_SKILL_TOTAL_BYTES",
    "ProgressiveSkillRuntime",
    "SKILL_STATE_SCHEMA",
    "SkillAccessError",
]
