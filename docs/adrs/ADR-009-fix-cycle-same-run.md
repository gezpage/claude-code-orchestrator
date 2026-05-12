---
status: accepted
date: 2026-05-07
affects: [review_cycle.py]
---

# ADR-009: Review Fix Cycle Runs Within the Same Run

**Status:** Accepted
**Date:** 2026-05-07

## Context

When reviewers request changes after the Review stage, a fix must be implemented and the affected reviewers must re-run their review. The question is whether this constitutes a new pipeline run or continues within the current run.

The current pattern treats any restart as a new run: a new run folder is created, and the previous run's state is historical only. Applying this pattern here would create a new run for each fix cycle, fragmenting the history of what was reviewed, what was fixed, and what the final outcome was.

## Decision

A review fix cycle is one unit of work. Fix cycles run within the same run — no new run folder is created. `review.md` is appended with round sections (e.g. `## Architecture Review — Round 2`). Per-reviewer frontmatter statuses are updated in place after each round. `_state.yaml`, `review.md`, and run history all accumulate in the same run folder across all fix cycles.

## Consequences

- The full review history (original findings, fix applied, re-review outcome) is co-located in one run folder, making it easy to audit.
- `_state.yaml` and the run folder structure encode the fix-cycle-within-run pattern; this is hard to reverse once multiple runs exist in this format.
- The "blocked → new run" convention used elsewhere in the pipeline does not apply to review fix cycles — this is an explicit exception.
- `review.md` grows across cycles; its append structure must be documented so reviewers know where to find the latest round.
