# Host tool contract

This package is deliberately host-neutral. The host exposes the tools and
remains the authority for the evidence store. A compatible adapter should keep
the following semantic contract even if its transport is Python, MCP, HTTP, or
another agent SDK.

## Search

Tool: `search_local_evidence`

Input:

```json
{
  "query": "the focused research question",
  "result_limit": 6
}
```

The host may also return `jev_review` when an optional semantic ranking stage
was configured. Its status is one of `applied`, `not_run`, or `degraded`.

Each hit should preserve these fields when available:

| Field | Meaning |
| --- | --- |
| `evidence_id` | Stable sentence/span identifier owned by the host. |
| `doc_id` | Stable source document identifier. |
| `paper` | Human-readable source title. |
| `doi` | DOI, when known. |
| `section` | Source section label. |
| `html_anchor` | Locator used to open the source at the span. |
| `matched_text` | The source-grounded passage shown to the model. |
| `context_before` / `context_after` | Optional bounded context. |
| `score` | Local retrieval score, used only for ordering. |
| `jev_support_probability` | Optional JEV ranking signal, not a truth value. |

An evidence hit without a stable ID and source text is not citeable. The host
should reject or mark it as incomplete rather than silently turning it into a
citation.

## Verified synthesis

Tool: `build_verified_answer` (optional)

Use it when the host provides a citation-aware answer builder. Its result must
retain the original `evidence_id`, exact quote, document ID, and locator for
every citation. A generated answer without a supporting span must be marked
insufficient.

## Portable envelope

Adapters can normalize a search response to:

```json
{
  "schema_version": "scansci.evidence-envelope.v1",
  "status": "ready",
  "query": "...",
  "hits": [
    {
      "evidence_id": "paper-001.s0004",
      "doc_id": "paper-001",
      "paper": "Example paper",
      "section": "Results",
      "html_anchor": "results-p4-s0004",
      "matched_text": "A bounded source passage.",
      "score": 0.82
    }
  ],
  "review": {
    "status": "not_run",
    "reason": "provider_disabled"
  }
}
```

`status=empty` means no local hit was found. `status=degraded` means an
optional stage failed while the local result remains usable. `status=not_run`
means the host did not execute retrieval. These states must remain visible to
the caller. The envelope carries candidates and provenance; it does not grant
permission to write, publish, or alter the evidence store.
