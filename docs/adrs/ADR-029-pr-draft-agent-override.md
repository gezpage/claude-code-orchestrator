---
status: accepted
date: 2026-05-16
affects: [orchestrator/profile.py, orchestrator/orchestrate.py, orchestrator/profiles/minimal.yaml, orchestrator/profiles/minimal-claude.yaml]
---

# ADR-029: Profile-Level Agent Override for `pr_draft`

**Status:** Accepted
**Date:** 2026-05-16

## Context

ADR-019 established `pr_draft` as a post-pipeline finalisation step rather than a profile stage — it runs conditionally (only when `create-pr` is true and origin is GitHub) and applies the same way regardless of profile. Its agent config has historically been resolved from `profile.agent` alone (`orchestrate.py`: `resolve_agent_config(profile.agent, None)`), with no per-finalisation override.

This becomes friction when a profile wants a different model for `pr_draft` than for the pipeline's heavy stages. Drafting a PR title and body from a diff is well within Sonnet's capability and meaningfully cheaper; deep code reasoning (implementation, review) benefits from Opus. With a single profile-level agent config, the two have to share a model.

A per-stage `agent:` override exists for stages declared in `stages:` (e.g. `minimal`'s review stage flipping to `codex_cli`). `pr_draft` cannot reuse that path because it is intentionally not a profile stage.

## Decision

Add an optional top-level `pr_draft.agent` block to the profile YAML schema. When present, it is merged on top of the profile-level `agent` block via the existing `resolve_agent_config()` machinery — the same shallow-merge rules that govern stage-level overrides.

Concretely:

- `Profile` gains a `pr_draft_agent: dict | None` field.
- `load_profile` parses `pr_draft.agent` and validates it is a mapping.
- `orchestrate.py` resolves `pr_draft_agent_config = resolve_agent_config(profile.agent, profile.pr_draft_agent)` and threads it into `_finalize_pr`. The executive summary finalisation step (ADR-028) keeps using `finalisation_agent` — they remain independently configurable.

`pr_draft` is NOT promoted to a profile stage; ADR-019 still applies.

## Why not promote `pr_draft` to a stage?

ADR-019's reasoning still holds: `pr_draft` is conditional and profile-independent. Promoting it would force every profile to either declare the stage or have the orchestrator silently inject it.

## Why not a flat `pr_draft_model:` field?

A flat field works for the common case but cannot express other valid overrides (backend swap, sterile_context, permission_mode). Mirroring the per-stage agent shape keeps the schema consistent, reuses the existing merge logic, and lets future overrides land without a further schema change.

## Consequences

- Profile YAML schema now accepts an optional `pr_draft.agent` mapping. Profiles that omit it are unchanged — `_finalize_pr` falls back to the profile-level agent exactly as before.
- The two finalisation steps (`pr_draft`, executive summary) can now drift in model/backend if needed. This is intentional: drafting a PR body and synthesising an executive summary have different cognitive profiles.
- The bundled `minimal` and `minimal-claude` profiles now pin `claude-sonnet-4-6` for `pr_draft`. Users of those profiles will see PR-draft latency and cost drop relative to the pre-change Opus path; quality of the title/body should be unchanged for this class of task.
- ADR-019's invariant ("`pr_draft` is not a profile stage") is preserved — the new block is at the profile top level, not inside `stages:`.
