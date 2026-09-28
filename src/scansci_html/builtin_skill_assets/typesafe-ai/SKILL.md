---
name: typesafe-ai
license: MIT
description: >
  Use TypeSafe System One and Jev as small, composable units of typed AI
  judgment for routing, ranking, extraction, verification, and interactive
  application state.
metadata:
  upstream: https://github.com/typesafe-ai/skills
  integration: ScanSci Harness
---

# TypeSafe / Jev in ScanSci Harness

This is a bounded ScanSci adaptation of the official TypeSafe skill. Use it
when a feature needs semantic judgment that ordinary deterministic code cannot
provide, especially when a prompt-and-parse step can become a typed decision.

## Core model

TypeSafe System One models provide focused judgments that application code can
consume directly. Jev is TypeSafe's flagship System One model. It returns typed
answers and probabilities rather than a chat response or an explanation. Code
owns the workflow, deterministic facts, calculations, and effects; Jev supplies
semantic judgment where the application needs it.

Useful patterns include:

- route a request and fill known typed arguments;
- select a candidate, value, or source span instead of generating a replacement;
- rank or verify evidence against a question;
- score dimensions once and let code apply weights and thresholds;
- verify a claim and escalate uncertain cases to a person or reasoning model.

## Design the judgment

Start from what the application must show, select, change, or hand off, then
work backward to the smallest useful judgment. Keep the relevant state
explicit, preferably as named JSON fields. Put the judgment in instructions
and define valid answers in criteria. Ask one narrow, coherent question at a
time; split independently useful dimensions without losing the relationship
being judged. Include a no-match outcome when no candidate fits.

Choose the primitive by meaning:

- **Choice** selects one option and exposes a distribution over candidates;
- **Noul** evaluates whether a condition holds;
- **Score** measures a described ordered dimension.

Run independent questions over the same state together when the API supports
it. Use probabilities and confidence as signals for bounded behavior, with
thresholds evaluated on representative ScanSci data and consequences. A typed
answer guarantees the interface, not the truth; validate it in the target
workflow.

## Live documentation

Before changing an integration, read the current TypeSafe documentation as the
source of truth. Start with the [documentation index](https://docs.typesafe.ai/llms.txt),
then read the relevant [System One guide](https://docs.typesafe.ai/concepts/system-one.md),
[question guidance](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md),
[confidence guidance](https://docs.typesafe.ai/confidence.md), and the current
[HTTP API](https://docs.typesafe.ai/api.md) or chosen SDK reference. Inspect the
closest cookbook for a new workflow instead of inventing a generic classifier.

Do not invent version-dependent API fields, limits, model IDs, or thresholds.
Treat cookbook examples as patterns to evaluate, not as universal guarantees.

## ScanSci Harness boundary

- This Skill changes instructions only. It cannot add tools, permissions,
  evidence authority, filesystem access, or MCP access.
- JEV requests use ScanSci's Host-owned model-service configuration and
  transport. Keep API credentials in the existing secret store or server-side
  environment; never place keys in prompts, source code, logs, or artifacts.
- Preserve explicit `degraded`, `not_run`, and fallback reporting when JEV is
  unavailable or the configured provider does not support the requested task.
- Keep deterministic policy and effect authorization in Host code. A Jev
  judgment can inform a route or ranking, but it cannot authorize an effect by
  itself.
- Do not ask the user to install the upstream Claude plugin or run an external
  skills installer for this built-in version. The Skill is already shipped with
  ScanSci; only the TypeSafe service configuration is separately required.

For failures, inspect the exact state, question, candidates, typed answers,
composition, and observed outcome. Separate missing evidence, model errors,
code errors, and service failures.
