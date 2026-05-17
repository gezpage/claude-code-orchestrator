---
status: accepted
date: 2026-05-17
affects: [orchestrator/orchestrate.py, orchestrator/profile.py, orchestrator/verifiers/engine.py, orchestrator/profiles/]
---

# ADR-030: Wave-Level Deterministic Verification for Slice Expansion

**Status:** Accepted
**Date:** 2026-05-17

## Context

Profiles that use parallel slice expansion (`expansion: slices`) merge each slice — or each wave of parallel slices — back into the integration branch before moving on to the next wave. Each slice agent reports its own local status, but the merged state of the integration branch is never inspected until the post-implementation `verification` stage runs at the end of the pipeline.

That late check has a known failure mode: a slice agent can correctly report local success while the combined integration branch is broken — a build failure, a test that only fails when two slices' changes coexist, a probe regression triggered by a merge. By the time the post-implementation verification stage discovers the regression, every subsequent wave has been built on top of an already-broken tree, and the resulting `VERIFY.md` is hard to attribute back to any one slice or wave.

The fix should run close to the wave boundary, where attribution is cheap and the broken state is fresh. It must be triggered by configuration, not by profile name — orchestration code that branches on `if profile == "full"` becomes a maintenance hazard the moment a second slicing profile is added. The hook also must not be an aggressive hard stop by default: a single transient failure in mid-pipeline should not abort an otherwise-recoverable run when the post-implementation verifier and review cycle can still catch it.

## Decision

Add a per-stage `wave_verification` config block, default-on for any stage that uses slice expansion. After each slice group's commits land on the integration branch, the dispatcher (`_dispatch_slices` in `orchestrate.py`) calls `_maybe_run_wave_verification`, which invokes the existing verifier engine against the integration branch with a per-wave artifact subdir (`wave-verification/wave-{N}/`).

Config shape:

```yaml
implementation:
  expansion: slices
  wave_verification:
    enabled: true            # default true for slice stages, false otherwise
    on_failure: warn         # one of: warn | fix_then_retry | block
```

Policy semantics:

- `warn` — record the verifier verdict in `plan.md` + `run.log` and continue with the next wave. This is the default.
- `block` — return a blocked signal so the orchestrator's existing halt machinery stops the pipeline at the wave boundary.
- `fix_then_retry` — dispatch the existing `fix-verification` agent with the wave's `VERIFY.md`, then re-verify under `wave-verification/wave-{N}/retry/`. If the retry still fails, treat as `warn` and continue rather than blocking — the policy's intent is best-effort cleanup, not a hard gate.

The verifier engine's `verify()` accepts an `artifact_subdir` keyword so wave reports do not overwrite the post-implementation `verification/` report. The wave hook is keyed off `stage.wave_verification` — never off `profile.name` or stage name.

## Consequences

- Integration health is checked at every wave boundary for slice-expanded stages, surfacing merge-induced regressions immediately rather than at the post-implementation gate.
- The dispatcher's `_dispatch_slices` adds one helper call per wave with no profile-name branching, satisfying the profile-agnosticism invariant.
- Wave reports live under `wave-verification/wave-{N}/` so the post-implementation `verification/` artifacts remain authoritative for the full integration branch state.
- The default `warn` policy means existing pipelines see no behavioural change beyond extra log lines and a `plan.md` section per wave; runs that previously passed continue to pass.
- Stages can opt out (`enabled: false`) when the verifier overhead is not worth it (e.g. test profiles, single-slice plans).
- `fix_then_retry` reuses the existing `fix-verification` runner and schema, so no new schema or prompt is introduced for wave-level fixes.
- New ad-hoc branching on profile name in `orchestrate.py` to trigger wave behaviour would violate this ADR — the trigger is `stage.wave_verification.enabled` and nothing else.
