---
status: accepted
date: 2026-05-07
affects: [orchestrate.py]
---

# ADR-004: Oblivious Orchestrator — Main Session Never Reads Stage Output File Contents

**Status:** Accepted
**Date:** 2026-05-07

## Context

In a naive orchestration design the main session reads each stage's output files in order to pass context to the next stage. As stages accumulate, the main session context grows without bound — eventually triggering OOM, context truncation, or gateway timeouts.

An alternative is to treat the main orchestration session as a coordinator that routes control but never accumulates content. Cross-stage context travels via signal JSON fields only, not via file reads.

## Decision

The main orchestration session (orchestrate.py) never reads stage output file contents. It receives file references (paths, commit hashes, status values) via the signal JSON emitted by each stage. All context a downstream stage needs is either surfaced in the signal JSON or pre-loaded into the stage's Jinja2 template variables by Python before dispatch.

Stage output schemas are designed around this constraint: they carry references, not content.

## Consequences

- The main session context remains bounded regardless of how many stages run or how large their output files are.
- Every stage must expose all downstream-relevant context as signal JSON fields — stages cannot rely on a downstream consumer reading their files directly.
- The orchestrator loses the ability to reason across stage outputs; it can only route and coordinate.
- Debugging cross-stage issues requires reading stage output files directly (the per-run `stage-output/` directory), not inspecting the orchestrator session.
- This constraint shapes every stage's output schema and is the reason the schema is enforced by Python rather than inferred from file contents.
