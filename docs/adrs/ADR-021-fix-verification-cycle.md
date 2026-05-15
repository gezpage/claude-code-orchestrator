---
status: accepted
date: 2026-05-15
affects: [orchestrator/orchestrate.py, orchestrator/run_stage.py]
---

# ADR-021: Fix-Verification Cycle — Verification Failures as Pipeline Blockers

**Status:** Accepted
**Date:** 2026-05-15

## Context

ADR-017 established that the deterministic verification stage always returns `status: "passed"` at the stage level, regardless of the actual verification result. The intent was to keep verification advisory: the detailed result (`verification_status`, `VERIFY.md`, `verify.json`) flows into the review stage, and reviewers are expected to treat probe failures as blocking findings.

In practice, this design produced a predictable failure mode:

1. Verification detects a probe failure (e.g. a no-op lint script) deterministically in seconds.
2. The pipeline continues to review, where a reviewer also flags the same failure.
3. The fix cycle is triggered to address reviewer feedback. The fix agent may address other review findings while leaving the probe failure unresolved, consuming a fix cycle.
4. A second review round re-flags the probe failure. The fix agent reports a stale commit hash, burning the second (last) fix cycle without making progress.
5. The pipeline blocks, even though the failure was machine-detectable from the start.

The review fix cycles — limited to two by ADR-011 — were designed for code-quality iteration between a human-equivalent reviewer and a fix agent. Spending those cycles on toolchain-setup problems (probe failures) is a misallocation: the verifier already characterised the issue precisely, and a fix agent given the raw probe output is better positioned to resolve it than one interpreting a reviewer's paraphrase.

## Decision

When a deterministic verification stage returns `verification_status: "failed"`, the pipeline now runs a `fix-verification` cycle before advancing to the review stage:

1. A `fix-verification` agent is dispatched with `VERIFY.md` and `verify.json` as its primary inputs.
2. Verification is re-run deterministically.
3. If the fix made no commits, or if re-verification still returns `verification_status: "failed"`, the pipeline returns a `blocked` signal. The existing pipeline halt machinery treats this the same as any other stage failure.
4. If re-verification passes, the pipeline continues with the updated verification signal. The review stage receives clean verification context.

The fix-verification cycle runs exactly once. There is no retry loop. If one targeted fix attempt cannot resolve the probe failures, the pipeline blocks immediately rather than falling through to review in a known-broken state.

The `fix-verification` stage uses its own prompt (`prompts/fix-verification/default.md`) and schema (`schemas/fix-verification.json`). It reuses the `implementation` runner configured in the profile, consistent with the fix-implementation stage in the review cycle.

The ADR-017 invariant — "verification is not a hard gate at the stage level" — is partially superseded. The stage-level signal still always returns `status: "passed"` from `run_deterministic_stage` itself, so the verifier engine is unchanged. The gate is enforced one level up, in `orchestrate.py`, by inspecting `verification_status` after the stage returns.

## Consequences

- Probe failures that were previously advisory are now pre-review blockers. A pipeline that previously reached review with a failed verification result will now block at the verification step unless the fix agent resolves the failure.
- The two review fix cycles are preserved exclusively for code-quality iteration, which is their intended purpose.
- Pipelines that resolve verification failures in the fix-verification cycle will reach review with `verification_status: "passed"`, giving reviewers a cleaner signal.
- If a probe failure is genuinely unfixable by an agent (e.g. a toolchain constraint in the repo), the pipeline blocks before review. The operator must resolve the issue manually and resume.
- The fix-verification cycle is not configurable per-profile. All profiles that include a `mode: deterministic` verification stage inherit this behaviour.
- `_run_fix_verification_cycle` in `orchestrate.py` is the single call site. Future changes to the fix-verification loop go there — not in the verifier engine, not in `review_cycle.py`.
