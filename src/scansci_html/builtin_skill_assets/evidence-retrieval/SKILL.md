---
name: evidence-retrieval
description: Search the active local evidence store and answer research questions with anchored, source-grounded citations. Use when claims need evidence from an indexed library; do not use it as a general web-search workflow.
license: MIT
metadata:
  package: scansci-evidence
  integration: ScanSci Harness
---

# Evidence Retrieval

Use this Skill when the answer depends on the user's indexed papers, notes, or
other host-owned evidence sources.

1. Turn the request into one focused question or a small set of claims.
2. Call the host's `search_local_evidence` tool with a focused query and a
   small result limit (normally 6; use at most 12 when comparing sources).
3. Treat `evidence_id`, `doc_id`, and `html_anchor` as opaque host identifiers.
   Quote `matched_text` and preserve the source locator in the answer.
4. If the hits do not directly support the claim, run one narrower follow-up
   query or use the host's `build_verified_answer` tool when available. State
   that the evidence is insufficient when the gap remains.
5. Treat lexical scores and optional JEV probabilities as ranking signals.
   They do not prove a claim and cannot authorize a write or change a citation.
6. Keep retrieval and tool traces out of the user-facing answer unless the user
   asks for the method. Report the conclusion, source, locator, and uncertainty.

The host owns evidence truth, citation verification, workspace scope, secrets,
permissions, and persistence. This Skill adds guidance only; it cannot add a
tool, widen a lease, fabricate an evidence ID, or replace a missing passage.
JEV is optional. Preserve the host's `not_run` or `degraded` status and use the
local retrieval order when semantic review is unavailable.

Read [the host contract](references/host-tool-contract.md) when adapting this
Skill to another evidence store or tool schema.
