---
status: accepted
date: 2026-05-16
affects: [orchestrator/orchestrate.py, orchestrator/prompts/executive_summary/, orchestrator/schemas/executive_summary.json]
---

# ADR-027: Executive Summary as Always-On Post-Pipeline Finalisation

**Status:** Accepted
**Date:** 2026-05-16

## Context

A run folder accumulates a lot of artefacts — `plan.md`, `_state.yaml`, per-stage prompt/output pairs, review logs, verification probe output. Skimming all of that to answer "what happened, what changed, what still needs attention" takes longer than it should, especially for blocked or partially-failed runs. Issue #31 asked for a single `EXECUTIVE_SUMMARY.md`-style artefact aimed at the developer who launched the pipeline (distinct from the PR body, which is aimed at reviewers).

ADR-019 introduced a post-pipeline finalisation phase for draft-PR creation. It established that finalisation is *not* a profile stage — it runs outside the stage loop and its failures are warnings rather than pipeline-level failures. That ADR contemplated a single finalisation step (PR creation) and described it that way in the CLAUDE.md invariant.

The executive summary has different semantics from PR creation:

- PR creation is **conditional** on `create-pr` and a GitHub origin; it only fires on a clean pipeline because pushing a failed branch and asking for review is not useful.
- The executive summary is most valuable precisely when something went wrong: blocked stages, incomplete interactive prompts, verification failures the fix-cycle could not resolve. A successful run also benefits from a summary, but a failed run benefits more.

This makes the summary an **always-on** finalisation step. It also raises the surface-area question: there will now be two post-pipeline Claude stages, both running outside the profile loop. ADR-019's "post-pipeline finalisation step, not a profile stage" framing was singular; this ADR generalises it.

## Decision

Add a built-in `executive_summary` Claude stage that runs after the stage loop and after the PR finalisation phase, regardless of pipeline outcome.

1. **Always-on.** The summary is dispatched on every exit path:
   - clean pipeline completion,
   - a non-passing stage that triggers `sys.exit(1)`,
   - an incomplete interactive stage that triggers `sys.exit(0)`.

   The stage loop and the PR-finalisation block are wrapped in `try / finally`. `_finalize_summary()` is called from the `finally`. Because `sys.exit()` raises `SystemExit`, the finally clause executes before the process exits, and the exit code is preserved.

2. **Prompt + schema.** New prompt at `orchestrator/prompts/executive_summary/default.md` and matching JSON schema at `orchestrator/schemas/executive_summary.json`. The signal carries `{stage, status, summary_path}` only — the file content stays on disk, in keeping with ADR-004 (orchestrator never reads stage output).

3. **Output location.** `executive_summary.md` is written to the run folder root, alongside `plan.md`. Lowercase filename matches the rest of the run folder convention.

4. **Inputs given to the agent.** Run-folder path, `plan_md_path`, `overview_md_path`, `state_yaml_path`, branch / base-branch, and the PR URL when one was created (passed through from `_finalize_pr`'s return value, falling back to `"not created"`). The agent reads stage subfolders for any further context it needs.

5. **Independence from PR creation.** The summary runs whether or not PR creation was attempted. When the PR phase ran and succeeded, its URL flows into the summary prompt; when it didn't, the summary still fires.

6. **Failure handling.** Any exception or blocked signal from the summary stage is logged at WARN and swallowed — the pipeline exit status is unaffected. This matches ADR-019's stance for finalisation steps in general.

7. **Backend selection.** The runner is resolved once via `resolve_agent_config(profile.agent, None)` and reused for both finalisation steps, so codex-only profiles do not silently fall back to the default Claude runner.

### Why always-on rather than gated by a flag

Adding `create-summary` alongside `create-pr` is the obvious parallel, but the failure-mode argument cuts the other way. The user reading a blocked run is the one with the lowest tolerance for trawling through `_state.yaml` and review logs — and that user might not have remembered to enable a flag. Defaulting to always-on, swallowing failures as warnings, gives the highest-value behaviour without making the user opt in.

### Why a Claude stage rather than a deterministic template

Many sections of the summary — "what was done", "open risks", "recommended next actions" — require synthesis the orchestrator cannot do from signal JSON. A deterministic template can only emit fact-shaped sections (timings, commands, test counts). Picking deterministic for those parts would result in a thin summary missing the most useful sections; picking Claude for the whole thing keeps the prompt cohesive. The cost is one additional LLM call per run.

### Why ordering: PR before summary

When a PR is created, the summary should reference its URL. Running the summary last makes that natural; reversing the order would require either re-running the summary after PR creation or holding the PR URL in extra state.

### Why generalise ADR-019's invariant

CLAUDE.md previously stated: *"PR creation is a post-pipeline finalisation step, not a profile stage."* That phrasing made PR finalisation an explicit carve-out — but now there are two such steps. Rather than enumerate them, the invariant is rephrased to cover finalisation steps in general; PR creation and executive summary are listed as the current instances.

## Consequences

- One additional LLM call per run (regardless of outcome). For runs that block at the first stage, this is the only Claude call after that point.
- The run folder gains `executive_summary.md` as a new top-level artefact.
- The "PR creation is a post-pipeline finalisation step" invariant in CLAUDE.md is rephrased to cover finalisation steps in general. New finalisation steps must follow the same rules: outside the stage loop, swallow their own failures, never change the pipeline exit status.
- `_finalize_pr` now returns the PR URL (or `None`) instead of returning `None` unconditionally — callers that previously discarded the return value continue to work because Python ignores unused returns.
- Tests that count `run_stage` invocations (`tests/test_e2e_*.py`) had to be incremented by one to account for the always-on summary call.
- The test fixture in `tests/test_orchestrate.py` patches `_finalize_summary` to a no-op by default, mirroring how preflight and base-sync are stubbed. Tests that exercise the summary path patch it explicitly within their own `with` block.
