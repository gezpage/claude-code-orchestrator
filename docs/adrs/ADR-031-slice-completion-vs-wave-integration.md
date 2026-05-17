---
status: accepted
date: 2026-05-17
affects: [orchestrator/orchestrate.py, orchestrator/plan/_expand.py, orchestrator/plan/_render.py]
---

# ADR-031: Distinguish Slice Completion from Wave Integration Health

**Status:** Accepted
**Date:** 2026-05-17

## Context

ADR-030 added wave-level deterministic verification for slice expansion: after each slice (or parallel slice group) merges into the integration branch, the verifier runs against the merged state. That hook already exists, but the result was surfaced only as an appended `## Wave N Verification` markdown section below the diagram.

Without a corresponding graph node, the rendered plan still reads as if a passed slice means a healthy repo:

- The slice node renders green (`passed`) because the slice agent reported local completion.
- There is no node representing "the integration branch is healthy" — that signal lives only in a prose section.
- A reader scanning the diagram sees a green slice and reasonably concludes the wave is good. They do not, by default, see the failed integration check.

The acceptance criteria for #146 require the rendered plan and the run summary to make two separate concepts visible:

1. Per-slice local completion (the slice agent's `passed`).
2. Wave/integration health (the merged-branch verifier verdict).

This must not change the wave-verification trigger (still keyed off `stage.wave_verification` per ADR-030) and must not break existing slice layouts where wave verification is disabled.

## Decision

For every wave in a slice-expanded stage where `wave_verification.enabled` is true, materialise a dedicated `wave_verify_{N}` node in the plan graph alongside the slice nodes:

- The wave node is a deterministic stage node (`mode="deterministic"`, no prompt input) with `stage_dir="wave-verification"` and `file_suffix=f"wave-{N}"`.
- The chain is rewritten so each wave is followed by its wave node before flowing into the next wave: `... → impl_1 → wave_verify_1 → impl_2 → wave_verify_2 → next`. For parallel groups: `... → fanin_N → wave_verify_N → ...`.
- `_maybe_run_wave_verification` stamps the wave node after each integration check: `passed` when `verification_status == "passed"`; `blocked` for any failed verification (regardless of `on_failure` policy, so a `warn`-policy failure is still visible); `skipped` when verification could not run.
- File matching is extended so `wave-verification/wave-N/VERIFY.md` attaches to the corresponding `wave_verify_N` node, surfacing the verifier report links in that node's panel.

Wave nodes appear in the run summary as their own rows (`Wave Verify 1`, `Wave Verify 2`, …) because `update_plan_md` calls `state_mod.save_stage_elapsed`, so the summary table reflects integration health alongside slice completion.

Rejected: introducing a new `warned` terminal status or status class. Slice nodes stay `passed` (local completion is the slice agent's truth) and wave nodes stay `blocked` (integration is broken regardless of how the dispatcher chose to react). Adding a `warned` status would require touching `_STATUS_PRECEDENCE` and `worst_status` for a single rendering nicety; the cost is not worth it when a `blocked` wave node sitting next to a `passed` slice already communicates the distinction.

## Consequences

- The diagram surfaces a passing slice next to a failed wave as two visibly different nodes — a passed slice no longer implies repo health. The bold "completed path" trail breaks at the failed wave node, making the boundary visible.
- The run summary lists wave verifications as separate rows, so a `warn`-policy failure is recorded in the summary table even though the pipeline continues. (Under `block` policy the summary still updates because the wave node is stamped before the dispatcher returns `blocked`.)
- Wave file matching uses a deeper path rule (`parts[1]` against `file_suffix`) added to `_scan_files`. The rule is gated on `len(parts) >= 3` and falls back to the existing depth-2 match, so other stages are unaffected.
- The bespoke `_append_wave_verification_section` from ADR-030 stays — it provides the per-wave summary text and explicit VERIFY.md / verify.json links below the mermaid block. The wave node's panel and the appended section are complementary, not duplicates: the node carries status and timing; the section carries the verbatim verifier summary.
- New deterministic stages following the same "per-instance subdirectory" layout (e.g. a future "wave-review/wave-N/") will Just Work via `_match_subdir_node` without further changes.
- Adding a new slice-expansion stage that bypasses the wave node (e.g. by editing `_expand_slices` directly) would re-collapse the two concepts and violate this ADR. The trigger remains `stage.wave_verification.enabled`.
