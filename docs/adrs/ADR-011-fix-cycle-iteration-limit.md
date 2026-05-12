---
status: accepted
date: 2026-05-07
affects: [review_cycle.py]
---

# ADR-011: 2-Cycle Limit Before Fix Pipeline Blocks

**Status:** Accepted
**Date:** 2026-05-07

## Context

The fix cycle runs automatically (ADR-010) and within the same run (ADR-009). Without a cap, a reviewer who repeatedly requests changes — whether due to unclear findings, a fundamental disagreement, or a bug in the fix agent — would cause the pipeline to loop indefinitely.

A cap is required. The specific number is a judgment call: too low and the pipeline blocks on genuinely complex fixes that need more than one iteration; too high and users wait through many wasted cycles before they can intervene.

## Decision

The fix cycle limit is 2. After 2 fix-then-re-review cycles that still leave any reviewer's status as `changes-requested`, the pipeline blocks and hands control to the user. The block message identifies which reviewer(s) are still unsatisfied and references the relevant round sections of `review.md`.

## Consequences

- The pipeline cannot loop indefinitely on review fix cycles.
- The limit of 2 is baked into `review_cycle.py` and documented as a package invariant; changing it requires a code change.
- Complex review findings that genuinely require more than 2 fix iterations will cause the pipeline to block, requiring manual intervention even when more automated cycles could have resolved the issue.
- The limit is arbitrary but consequential — it was chosen as a reasonable default for the expected scope of automated fix cycles (targeted, single-pass fixes as per ADR-010).
