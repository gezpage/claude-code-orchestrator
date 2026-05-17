---
status: accepted
date: 2026-05-17
affects: [orchestrator/orchestrate.py, orchestrator/verifiers/engine.py, orchestrator/verifiers/artifacts.py, orchestrator/schemas/verification.json]
---

# ADR-033: Distinguish Baseline from Net-New Verification Failures

**Status:** Accepted
**Date:** 2026-05-17

## Context

Slice agents sometimes report test failures as "pre-existing" or "unrelated to my slice". In parallel slice expansion that can be true — the verifier runs on the merged integration branch, which already contains commits beyond the slice's own diff. But ADR-030 wave verification has no way to confirm the claim: it reports every failure with the same severity, and the `on_failure: block` policy halts the pipeline on a long-standing red test the same way it halts on a regression introduced this wave.

Two failure modes follow:

1. Projects carrying known-failing tests cannot use `block` because the very first wave will halt on the existing red state.
2. Agent claims of "pre-existing failures" cannot be verified — there is no recorded comparison, so reviewers cannot tell apart a genuine baseline failure from one the slice introduced.

The goal is to detect regressions, not to halt on every existing red test. The verifier already runs the same recipe deterministically — it just needs a pre-pipeline snapshot to diff against.

## Decision

Capture a pre-pipeline verifier **baseline** and classify each wave-verification failure as `baseline` (already failing before any slice ran) or `net_new` (introduced this wave). Apply the existing `on_failure` policy only to net-new failures.

### Capture

`orchestrator/verifiers/engine.py` gains:

- `capture_baseline(repo_root, run_folder)` — runs the recipe and writes the report to `run_folder/baseline-verification/verify.json`.
- `baseline_path_for(run_folder)` — the canonical baseline path.

`_dispatch_slices` in `orchestrate.py` calls `_maybe_capture_wave_baseline(...)` immediately after `_create_branch` succeeds, **before** any slice runs. It is gated on `stage.wave_verification.enabled`, idempotent (skips when the file already exists, so a resumed pipeline never overwrites the original baseline), and best-effort (a capture failure is logged and swallowed so the pipeline still proceeds without classification).

On a resumed run (`ctx.resume == True`) where the original baseline file is missing, the capture is explicitly **refused**. The integration branch already carries earlier slice commits at that point, so a fresh capture would snapshot pipeline-introduced regressions as "baseline" and silently flip every real regression into a "baseline failure" — the worst possible failure mode for this feature. Missing baseline on resume degrades to pre-ADR-033 behaviour (policy applies to every failure) rather than a contaminated baseline.

### Classification

`verify()` accepts a new `baseline_path` keyword. When provided and readable, it tags every failing command and probe as `baseline` or `net_new` and computes a separate `net_new_status` using the same required/probe aggregation rules as `verification_status`. The `fix_then_retry` retry verify call must also pass `baseline_path` — otherwise a fixer that resolves the only net-new failure would see baseline-only failures reclassified as net-new and the retry would still report failed, masking the successful fix. The signal grows:

- `baseline_failed_command_ids`, `baseline_failed_probe_ids`
- `new_failed_command_ids`, `new_failed_probe_ids`
- `resolved_command_ids`, `resolved_probe_ids` — baseline failures that now pass (informational)
- `net_new_status: "passed" | "warned" | "failed" | "skipped"`
- `baseline_compared: bool`

The `VERIFY.md` artifact carries a `Baseline Comparison` section plus a `Kind` column on the command table so reviewers can see the classification without parsing JSON.

A missing or malformed baseline file silently falls back to no-classification — verification still runs, the signal omits the new lists, and `net_new_status` mirrors `verification_status`. This keeps greenfield projects, resumed runs that lost their baseline, and any other valid-no-baseline state working without a hard error.

### Policy gate

`_maybe_run_wave_verification` now applies `on_failure` to `net_new_status`, not `verification_status`:

- `warn` — baseline-only failures and net-new failures both log a WARN line; the pipeline continues either way. The plan section records both counts so reviewers see what changed.
- `block` — only **net-new** failures return `status: "blocked"`. Baseline-only failures continue with a WARN line regardless of policy.
- `fix_then_retry` — runs only when there are net-new failures. A baseline-only failure is not a regression worth a fix agent's time.

When `baseline_compared` is false (no baseline available), `net_new_status` falls back to `verification_status` so the policy degrades into the pre-ADR-033 behaviour rather than silently passing every failure.

### Rejected alternatives

- **Per-test diffing of stdout/stderr.** Reliable only for toolchains with structured test reporters; brittle on prose output. The command/probe-ID level is the contract the recipe already exposes — that is where comparison belongs.
- **Running the verifier on the base branch from inside `verify()`.** Would couple the engine to git state and double the verifier overhead per wave. Capturing once at pipeline start is cheaper and lets the same baseline serve every wave.
- **A new `baseline` status alongside `passed`/`warned`/`failed`.** Would force every consumer of `verification_status` to learn a new value. Keeping `verification_status` unchanged and adding `net_new_status` lets existing consumers work unmodified.

## Consequences

- Projects carrying pre-existing red tests can adopt `on_failure: block` without false halts; only regressions stop the pipeline.
- Agent claims of "pre-existing failure" are now evidence-backed: the baseline file is a deterministic snapshot, and the wave verifier's classification is visible in `VERIFY.md` and the signal JSON.
- `block` and `fix_then_retry` apply only to regressions. A pipeline that previously halted at wave 1 on existing failures will now warn and proceed; review still sees the failure and can stop the merge.
- The verifier runs one extra time at pipeline start (baseline capture). For toolchains with multi-minute test suites this adds noticeable wall-clock; it is the minimum needed to distinguish regressions reliably.
- The bundled signal grows by seven optional fields. The schema accepts them as additional properties and existing consumers ignore them.
- Wave verification continues to be keyed off `stage.wave_verification.enabled` (ADR-030 invariant). Adding profile-name branching to control baseline capture would violate this and should not be introduced.
- Failed baseline capture is logged but never aborts the pipeline. A run without a baseline degrades gracefully into the pre-ADR-033 behaviour (policy applies to every failure) rather than silently passing.
- The same baseline serves every wave in a run. Capturing per-wave would change the meaning of "baseline" mid-pipeline; instead, the single pre-pipeline snapshot is the stable reference and `resolved_*` lists make progress visible.
