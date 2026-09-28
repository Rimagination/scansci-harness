# ScanSci Evidence

`scansci-evidence` is a small open-source plugin for evidence-first research
agents. It gives a model a compact way to use a host's local evidence store
without moving evidence authority, credentials, or filesystem access into the
Skill.

The package contains:

- `skills/evidence-retrieval/SKILL.md`: the model-facing retrieval policy;
- `skills/evidence-retrieval/references/host-tool-contract.md`: the portable
  host/tool contract;
- `scripts/normalize_evidence.py`: a dependency-free validator and normalizer
  for a host search result.

ScanSci ships the same retrieval Skill as a built-in asset. The plugin folder is
the public source package; the built-in copy lets the desktop app use the
capability without a separate installation.

## Host boundary

The host remains authoritative for the active workspace, evidence IDs, HTML
anchors, citation verification, permissions, and persistence. A compatible
host should expose a read-only `search_local_evidence` tool and may expose
`build_verified_answer` for citation-aware synthesis.

JEV is optional. When enabled, the host may send a bounded set of snippets to
JEV for ranking or coverage judgment. The plugin contains no API key, network
client, or provider-specific secret handling. `not_run` and `degraded` results
remain valid outcomes.

## Local check

Pipe the JSON returned by a host's `search_local_evidence` call into the
script, or pass a JSON file with `--input <path>`:

```powershell
Get-Content .\search-result.json | python .\scripts\normalize_evidence.py
```

The normalizer emits `scansci.evidence-envelope.v1` and rejects missing IDs or
source text. It does not contact a provider and does not create citations.

## License

MIT. See [LICENSE](LICENSE).
