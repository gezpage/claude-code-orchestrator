---
status: accepted
date: 2026-05-07
affects: [review_cycle.py]
---

# ADR-010: Fix Cycle Triggered Automatically Without User Approval

**Status:** Accepted
**Date:** 2026-05-07

## Context

After the Review stage, if any reviewer's frontmatter status is `changes-requested`, a fix implementation must be dispatched. The question is whether Python pauses to ask the user before proceeding.

The general orchestrator principle is to pause for significant actions. A fix cycle commits additional code changes to the feature branch — it is not trivial. However, requiring user approval for each fix cycle adds friction to what should be a routine outcome of the review process.

## Decision

The fix cycle is triggered automatically. Python detects `changes-requested` in any reviewer's frontmatter status after the Review stage and immediately dispatches the fix cycle without requesting user approval. Only reviewers whose status is `changes-requested` re-run their review after the fix; reviewers who passed do not re-run. The fix implementation is single-pass: Python passes the `changes-requested` sections of `review.md` directly to the fix implementation agent as the brief.

## Consequences

- Zero friction for routine review fixes: the pipeline proceeds without developer intervention.
- The pipeline may commit additional code changes to the feature branch without the user explicitly authorising each round — reduced visibility when fixes are non-trivial.
- Developers who want to review fix content before it is committed must inspect `review.md` and the branch diff after the cycle completes rather than before it runs.
- If a reviewer's `changes-requested` reflects a fundamental disagreement rather than a correctable issue, the pipeline wastes a cycle before blocking at the iteration limit (ADR-011).
