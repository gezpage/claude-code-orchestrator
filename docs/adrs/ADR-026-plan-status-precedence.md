---
status: accepted
date: 2026-05-16
affects: [orchestrator/plan/_constants.py, orchestrator/plan/_update.py, orchestrator/plan/_render.py, orchestrator/orchestrate.py]
---

# ADR-026: Consistent terminal-status semantics in plan.md

**Status:** Accepted
**Date:** 2026-05-16

## Context

`plan.md` is the live observability surface for a run — it is the first thing
demonstrated to stakeholders and the first thing inspected when something
went wrong. The mermaid graph is a projection of the per-node statuses held in
`_plan_graph.yaml`. Each stage's status is written in isolation by the
component that runs it (`orchestrate.run_pipeline`, `review_cycle.run`,
`_finalize_pr`, etc.), with no shared notion of how multiple statuses for the
same logical concept should resolve to one rendered state.

Three contradictions surfaced in real runs (issue #132):

1. **Aggregate vs. final review.** A reviewer that returned `changes-requested`
   in round 1 had its sub-node stamped `blocked`. When a later fix cycle
   approved the same reviewer, only the *round-N* sub-node was updated; the
   round-1 sub-node stayed red, contradicting the green sibling beside it.
2. **Passed parent panel shows "pending".** Stages with no `*-output.md` file
   (e.g. the review parent, or any stage that writes only structured
   artefacts) fell through `_PANEL_STATUS_TEXT["passed"] == ""` then
   `"" or "pending"`, surfacing the word "pending" inside the panel of a
   plainly-passed stage.
3. **PR node stays pending on a failed run.** The PR node is created at
   init time when `create-pr` is true. If the pipeline fails before
   `_finalize_pr` runs, no code resets the PR node, so the diagram renders a
   "pending" PR after a terminal failure — a contradictory terminal state.

Without an explicit precedence ordering it was impossible to write tests that
caught these contradictions, and impossible to make a principled call when a
future stage needs to combine sub-statuses.

## Decision

Introduce an explicit precedence ordering for terminal statuses and resolve
contradictions through small, named helpers rather than ad-hoc per-call-site
fixes:

```
failed (0) > blocked (1) > changes-requested (2) > in_progress (3)
            > passed (4) > skipped (5) > pending (6)
```

Lower numbers win. `worst_status(*statuses)` in `orchestrator/plan/_constants`
returns the highest-precedence value; unknown statuses sort after every known
one so a typo cannot beat a recognised state.

Three concrete applications target the three contradictions above. The first
two are *terminal-state restamping*, not aggregation — they intentionally
replace stale signal with the verdict that supersedes it. Aggregation
(combining several live statuses into one rendered state) is what
`worst_status` is for; restamping is the orthogonal operation.

- **`resolve_review_subnode_statuses(run_folder, final_reviewer_statuses)`**
  re-stamps the round-1 `review_{reviewer}` sub-node with the final cycle
  verdict (`approved` → `passed`, `changes-requested` → `blocked`). Called
  from `orchestrate._dispatch_prompts` after `review_cycle.run` returns.
  Restamping, not aggregation: a later approved verdict intentionally
  replaces the stale round-1 blocked stamp.
- **`mark_pr_blocked(run_folder)`** flips the init-time PR node to `blocked`
  when the pipeline aborts before `_finalize_pr`. Called from the failure
  branch in `run_pipeline` alongside the stage-level `update_plan_md(...,
  "blocked")`. Also restamping: the pending stamp is correct at init time
  and stale once the pipeline has aborted.
- **`_PANEL_STATUS_TEXT["passed"]`** is now the string `"done"` instead of `""`,
  and the panel-body fallback returns the table value directly rather than
  using `or "pending"`. A passed stage with no output prose now renders
  "done" — never "pending". This is a pure rendering fix; it does not touch
  the underlying status.

The precedence table is the single source of truth. Any future code that needs
to combine statuses (parent stage vs. children, multiple sub-reviewers
collapsing to one badge, etc.) goes through `worst_status` rather than
introducing new ad-hoc rules.

## Consequences

- The diagram for a completed run no longer shows contradictory red+green
  sub-nodes after a successful fix cycle, and parent-stage panels stop
  surfacing "pending" for plainly-passed stages.
- Failed pipelines render the PR node in the blocked colour rather than the
  pending grey, so observers can tell at a glance that no PR will be produced.
- New aggregation logic must use `worst_status` from
  `orchestrator/plan/_constants`. Adding a competing precedence rule (e.g.
  hard-coded `if`-chains inside renderer code) is a regression of this
  decision.
- The precedence ordering is a hard constraint: changing it (e.g. promoting
  `changes-requested` above `blocked`, or demoting `in_progress`) is a
  semantic change that affects every downstream test. It requires a new ADR.
- Tests cover the contradictions enumerated above. A future change that
  re-introduces a contradiction will fail one of:
  `test_resolve_review_subnode_status_*`,
  `test_panel_body_passed_renders_done_not_pending`,
  `test_mark_pr_blocked_*`, `test_review_subnode_status_resolved_after_cycle`,
  or `test_blocked_stage_exits`.
- `worst_status` is exported from `orchestrator.plan.__init__`. The
  precedence dict itself stays private to discourage callers from reaching
  past the helper.
