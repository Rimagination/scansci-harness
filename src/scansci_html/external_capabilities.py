"""Host-owned semantic adapters for external MCP providers.

An external provider owns its network and source-specific implementation.  The
host only keeps the small semantic contract needed by task leases, completion
accounting, and bounded result previews.  Unknown MCP tools remain unknown
until a host policy classifies them.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


EXTERNAL_CAPABILITY_SCHEMA_VERSION = "scansci.external-capability.v1"

_SEMANTIC_CAPABILITIES = frozenset({
    "discover_papers",
    "verify_doi",
    "download_papers",
})
_EFFECT_BY_CAPABILITY = {
    "discover_papers": "read",
    "verify_doi": "read",
    "download_papers": "reversible",
}
_DEFAULT_LITERATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "discover_papers": (
        "scansci_pdf_search",
    ),
    "verify_doi": (
        "scansci_pdf_verify_identifiers",
        "scansci_pdf_paper_metadata",
    ),
    "download_papers": (
        "scansci_pdf_download",
        "scansci_pdf_batch_download",
        "scansci_pdf_resolve_and_download",
    ),
}


def _clean_name(value: object, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(character.isspace() for character in text):
        return ""
    return text


def _provider_kind(record: Mapping[str, Any] | None) -> str:
    payload = record or {}
    return _clean_name(payload.get("provider_kind") or payload.get("connector_kind"), limit=48).lower()


def _normalise_aliases(value: object) -> dict[str, tuple[str, ...]]:
    source: dict[str, Any] = {}
    if isinstance(value, Mapping):
        source = dict(value)
    elif isinstance(value, list):
        for item in value[:32]:
            if not isinstance(item, Mapping):
                continue
            capability = _clean_name(item.get("id") or item.get("capability"), limit=64)
            if capability:
                source[capability] = item.get("tools") or item.get("tool_names")
    aliases: dict[str, tuple[str, ...]] = {}
    for raw_capability, raw_tools in list(source.items())[:32]:
        capability = _clean_name(raw_capability, limit=64)
        if capability not in _SEMANTIC_CAPABILITIES:
            continue
        values = [raw_tools] if isinstance(raw_tools, str) else raw_tools
        if not isinstance(values, list):
            continue
        names = tuple(dict.fromkeys(
            name
            for name in (_clean_name(item, limit=160) for item in values[:32])
            if name
        ))
        if names:
            aliases[capability] = names
    return aliases


def normalize_provider_manifest(value: object, *, provider_kind: str = "") -> dict[str, Any]:
    """Normalize provider metadata without turning it into executable authority."""

    payload = value if isinstance(value, Mapping) else {}
    kind = _clean_name(payload.get("provider_kind") or provider_kind, limit=48).lower()
    aliases = _normalise_aliases(payload.get("capability_aliases") or payload.get("capabilities"))
    return {
        "schema_version": EXTERNAL_CAPABILITY_SCHEMA_VERSION,
        "provider_kind": kind,
        "capability_aliases": {key: list(names) for key, names in sorted(aliases.items())},
    }


def provider_manifest_for_record(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the stable semantic manifest for one configured MCP record."""

    payload = record or {}
    kind = _provider_kind(payload)
    custom = normalize_provider_manifest(payload.get("provider_manifest"), provider_kind=kind)
    custom_aliases = dict(custom.get("capability_aliases", {}) or {})
    if custom_aliases:
        return custom
    if kind == "literature":
        return {
            "schema_version": EXTERNAL_CAPABILITY_SCHEMA_VERSION,
            "provider_kind": "literature",
            "capability_aliases": {
                key: list(names) for key, names in sorted(_DEFAULT_LITERATURE_ALIASES.items())
            },
        }
    return custom


def host_policy_defaults(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build host classifications for a declared semantic provider surface.

    The provider may declare aliases, but the effect assigned to each semantic
    capability comes from this host table.  The reversible class is still
    hidden by the runtime until ``allow_reversible`` is explicitly enabled.
    """

    manifest = provider_manifest_for_record(record)
    effects: dict[str, str] = {}
    policies: list[dict[str, Any]] = []
    for capability, raw_names in dict(manifest.get("capability_aliases", {}) or {}).items():
        effect = _EFFECT_BY_CAPABILITY.get(str(capability))
        if not effect:
            continue
        for remote_name in list(raw_names or []):
            name = _clean_name(remote_name)
            if not name or name in effects:
                continue
            effects[name] = effect
            policies.append({
                "name": name,
                "effect": effect,
                "idempotent": effect == "read",
                "freshness": "run" if effect == "read" else "volatile",
            })
    return {
        "provider_kind": str(manifest.get("provider_kind") or _provider_kind(record)),
        "provider_manifest": manifest,
        "tool_effects": effects,
        "tool_policies": policies,
        "semantic_capabilities": sorted(dict(manifest.get("capability_aliases", {}) or {})),
    }


def _safe_tool_segment(value: object) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", str(value or "mcp").lower()).strip("_")[:48] or "mcp"


def _tool_segment(value: object) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "mcp").lower()).strip("_") or "mcp"


def _local_mcp_tool_name(server_alias: str, remote_name: str) -> str:
    full = f"mcp__{_tool_segment(server_alias)}__{_tool_segment(remote_name)}"
    if len(full) <= 64:
        return full
    suffix = hashlib.sha256(full.encode("utf-8")).hexdigest()[:10]
    return f"{full[:53].rstrip('_')}_{suffix}"[:64]


def semantic_tool_aliases(servers: Iterable[Mapping[str, Any]] | None) -> dict[str, str]:
    """Map runtime-local MCP names to stable ScanSci capability ids."""

    aliases: dict[str, str] = {}
    for raw in servers or ():
        if not isinstance(raw, Mapping):
            continue
        server_alias = _safe_tool_segment(raw.get("id") or raw.get("name"))
        manifest = provider_manifest_for_record(raw)
        for capability, raw_names in dict(manifest.get("capability_aliases", {}) or {}).items():
            if capability not in _SEMANTIC_CAPABILITIES:
                continue
            for remote_name in list(raw_names or []):
                name = _clean_name(remote_name)
                if name:
                    aliases[_local_mcp_tool_name(server_alias, name)] = capability
    return aliases


def semantic_capability_for_tool(
    name: object,
    aliases: Mapping[str, str] | None = None,
    arguments: Mapping[str, Any] | None = None,
) -> str:
    """Resolve direct and deferred MCP calls to one stable capability id."""

    local_name = str(name or "").strip()
    mapping = aliases or {}
    direct = str(mapping.get(local_name, ""))
    if direct:
        return direct
    if not local_name.startswith("mcp__") or not local_name.endswith("__call"):
        return ""
    remote_name = _clean_name((arguments or {}).get("tool"), limit=160)
    if not remote_name:
        return ""
    server_alias = local_name[len("mcp__") : -len("__call")]
    return str(mapping.get(_local_mcp_tool_name(server_alias, remote_name), ""))


def _decode_json(value: object) -> object:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _result_payload(value: object) -> object:
    payload = _decode_json(value)
    if isinstance(payload, Mapping):
        for key in ("structuredContent", "structured_content", "details", "data"):
            nested = payload.get(key)
            if isinstance(nested, (Mapping, list)):
                return _result_payload(nested)
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, Mapping) and str(item.get("type", "")) == "text":
                    decoded = _decode_json(item.get("text", ""))
                    if isinstance(decoded, (Mapping, list)):
                        return _result_payload(decoded)
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, (Mapping, list)):
                nested = _result_payload(item)
                if isinstance(nested, Mapping):
                    return nested
    return payload


def _bounded_strings(value: object, *, limit: int = 32) -> list[str]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(item).strip()[:500] for item in values if str(item).strip()))[:limit]


def normalize_external_tool_result(
    value: object,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Create a bounded, provider-neutral preview of an external result."""

    payload = _result_payload(value)
    if not isinstance(payload, Mapping):
        return {"schema_version": EXTERNAL_CAPABILITY_SCHEMA_VERSION, "status": "unstructured"}
    result: dict[str, Any] = {
        "schema_version": EXTERNAL_CAPABILITY_SCHEMA_VERSION,
        "status": str(payload.get("status") or ("ok" if payload.get("ok", True) else "failed"))[:32],
    }
    for key in ("ok", "success", "count", "total", "source", "provider", "message", "error"):
        if key in payload and isinstance(payload[key], (bool, int, float, str)):
            result[key] = payload[key] if not isinstance(payload[key], str) else payload[key][:500]
    for key in ("identifiers", "dois", "papers", "results", "items"):
        if key in payload and isinstance(payload[key], list):
            result[key] = payload[key][:32]
    raw_files: list[str] = []
    for key in ("file", "file_path", "path", "files", "artifact_paths"):
        raw_files.extend(_bounded_strings(payload.get(key)))
    if workspace:
        root = Path(workspace).resolve()
        verified: list[str] = []
        for raw_path in raw_files:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                resolved = candidate.resolve()
                if resolved.is_relative_to(root):
                    verified.append(str(resolved))
            except (OSError, RuntimeError, ValueError):
                continue
        if verified:
            result["artifact_paths"] = list(dict.fromkeys(verified))[:32]
    return result


__all__ = [
    "EXTERNAL_CAPABILITY_SCHEMA_VERSION",
    "host_policy_defaults",
    "normalize_external_tool_result",
    "normalize_provider_manifest",
    "provider_manifest_for_record",
    "semantic_capability_for_tool",
    "semantic_tool_aliases",
]
