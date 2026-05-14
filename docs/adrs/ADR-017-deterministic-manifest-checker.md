---
status: accepted
date: 2026-05-14
affects: [orchestrator/manifest_check.py, orchestrator/orchestrate.py, orchestrator/prompts/qa/default.md, orchestrator/prompts/review/implementation.md]
---

# ADR-017: Deterministic package manifest checker as a pre-QA gate

**Status:** Accepted
**Date:** 2026-05-14

## Context

Issue #69 documented a class of failures the LLM reviewers consistently missed
or downgraded: project-surface issues in `package.json` such as fake/no-op
quality scripts (`"lint": "echo add eslint"`), package scripts pointing to
files that do not exist, and unused production dependencies. PR #70 hardened
the QA and implementation-review prompts to ask the reviewer to look for these
problems, but prompt-only guarantees are best-effort — an LLM may still miss
or downgrade them, and reviewers disagree round-to-round.

The same issue (section 9) called for runtime probes covering a broader set of
failure modes — mutation leaks through repository returns, callback object
mutation, malformed CLI inputs, duplicate-conflict bypass. Those probes need
either a probe-spec format the project authors maintain, or LLM-generated
probes against the project under test. Both are net-new framework that does
not justify a single PR's worth of design at this point — and the highest-
leverage piece of section 9 (fake/broken scripts) is exactly what a
deterministic manifest checker already gives us.

## Decision

Add an `orchestrator/manifest_check.py` module that, for repositories
containing a `package.json`, runs a small set of deterministic checks and
writes findings to `$RUN_FOLDER/verify/manifest-findings.json` and
`manifest-findings.md`. The pre-pass is invoked from `orchestrate.py` before
the QA stage and, when QA passes, the findings path is injected into the
stage variables under `manifest_findings_path` so the QA and review prompts
can reference it.

Checks shipped in the first cut, all Node-only:

- **Fake quality scripts (blocking).** A `scripts` entry whose command is an
  `echo …` placeholder, `exit 0` no-op, or empty/whitespace-only string —
  for the canonical quality script names (`lint`, `typecheck`, `test`,
  `format`, `check`, and prefixed variants like `lint:fix`).
- **Missing script targets (blocking).** A `scripts` entry that runs
  `node <file>` or `python <file>` where `<file>` is a repository-relative
  path that does not exist.
- **Likely-unused production dependencies (advisory).** A `dependencies`
  entry that is not imported anywhere under the repo (excluding
  `node_modules` and dot-prefixed directories) via `require('<dep>')`,
  `from '<dep>'`, or `import '<dep>'`. Heuristic, false-positive-prone —
  recorded as advisory, never blocking.

The orchestrator hard-fails before QA dispatch if any blocking finding is
present. Advisory findings are written to the artefact and surfaced to QA
and reviewers via the path variable; the orchestrator does not block on
them.

Explicitly rejected:

- **Embedding the checker in QA's prompt.** QA already has prompt-level
  guidance for these checks (PR #70). The deterministic checker is a
  separate, more reliable backstop — not a replacement.
- **A new top-level `verify` profile stage.** A stage-shaped abstraction
  would force every profile to opt in or out and would force the result to
  flow through the standard signal/schema pipeline. The checker is small,
  Python-only, and has no LLM in the loop — running it inline before QA is
  simpler and keeps it independent of profile config.
- **Python/multi-language coverage on day one.** Section 8 of issue #69
  speaks specifically to Node; expanding to `pyproject.toml` / `Cargo.toml`
  is straightforward later but adds surface area today.
- **Runtime probes (section 9 of issue #69).** Deferred — the part of
  section 9 that maps to deterministic checks is subsumed by this checker;
  the rest (mutation/callback probes, CLI fault-injection, duplicate
  bypass) needs a probe-spec or generator framework that does not fit a
  single reviewable PR.

## Consequences

- The orchestrator now blocks before QA when `package.json` contains a fake
  quality script or a broken script target. This is a deterministic gate —
  not a judgement call — and is therefore appropriate to enforce
  unconditionally.
- QA and implementation-review prompts gain a `manifest_findings_path`
  variable they reference when present. The signal JSON still carries only
  a path, not contents, per ADR-004.
- The pre-pass runs once per pipeline invocation, not per fix cycle.
  Findings reflect the manifest as observed at QA time; subsequent fix
  cycles do not re-run the check (reviewers can still flag regressions
  through the standard review path).
- Repositories without a `package.json` are no-ops — the checker writes no
  findings and the variable is set to an empty string.
- Section 9 (runtime probes) remains open work, tracked outside this ADR.
