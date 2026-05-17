---
status: accepted
date: 2026-05-17
affects: [orchestrator/orchestrate.py, orchestrator/plan/_init.py, orchestrator/profile.py, orchestrator/profiles/]
---

# ADR-036: Executive Summary is Profile-Declared, Not Implicit

**Status:** Accepted
**Date:** 2026-05-17

## Context

ADR-028 established the executive summary as an *always-on* post-pipeline finalisation step: every profile, every run, every exit path. That made sense when the value of the artefact was being argued — opting in via a flag would have meant most users never saw it. ADR-035 went further and pinned the `executive_summary` node into every initial diagram so the finalisation step would be visible to readers of `plan.md`.

The combined effect: `executive_summary` became an orchestration concept hardcoded into the runtime and the renderer. `orchestrate.py` resolved a finalisation agent and seeded `agent_metadata["executive_summary"]` unconditionally; `plan/_init.py` appended an `executive_summary` node to `chain_ids` regardless of profile; `_finalize_summary` fired in the pipeline's `finally` block regardless of profile. Profile YAML files said nothing about executive summary — the behaviour was implicit, keyed off "the orchestrator has this feature."

This contradicts the project direction (visible across ADR-017, ADR-030, ADR-032) that orchestration behaviour should be capability/config-driven, not hardcoded around stage or profile names. Issue #162 makes the point directly: executive summary is becoming a magic stage. Other consequences:

- A new profile (e.g. a fast spike that intentionally wants no summary) has no way to opt out without code changes.
- The diagram shows a finalisation node even for profiles where the artefact is noise.
- The "absence of the block means disabled" contract is the same shape used elsewhere (`pr_draft`, glossary), so the divergence is unnecessary.

## Decision

Add a profile-level `executive_summary` block. Presence enables the finalisation step; absence disables it entirely.

```yaml
# Enable with defaults — agent resolves from profile-level `agent`.
executive_summary: {}

# Enable with an agent override — same pattern as pr_draft (ADR-029).
executive_summary:
  agent:
    model: claude-sonnet-4-6
```

Concretely:

1. **Schema.** `Profile.executive_summary: ExecutiveSummary | None = None`. The typed object carries an optional `agent` override mapping. Parsing rejects non-mapping values for both `executive_summary` and `executive_summary.agent` with the same error shape used by `pr_draft`.

2. **Orchestration.** `orchestrate.py` resolves the finalisation agent only when `profile.executive_summary is not None`; `agent_metadata["executive_summary"]` is only seeded then; the `finally`-block call to `_finalize_summary` is gated on the same condition. When the profile omits the block, none of the executive-summary machinery runs — no warning, no blocked node, no fallback.

3. **Diagram.** `plan/_init.py` only appends the `executive_summary` node to `chain_ids` when `profile.executive_summary is not None`. The renderer's existing `_ROOT_FILE_OWNERS` allowlist already falls back to the legend when the owner id is absent from the graph, so a stray `executive_summary.md` on disk in a disabled-summary run lands in the other-files button strip rather than orphaning the diagram.

4. **Agent resolution.** When enabled, the finalisation agent is resolved via `resolve_agent_config(profile.agent, profile.executive_summary.agent)` — symmetric with `pr_draft_agent`. This is the natural place to wire the override; no new resolution path is introduced.

5. **Bundled profiles.** Every bundled profile (`full`, `full-interactive`, `minimal`, `minimal-claude`, `minimal-codex`, `spike`) gains an explicit `executive_summary: {}` block to preserve the pre-ADR-036 behaviour. Removing the block is now the documented way to opt out.

This **partially reverses ADR-028 point 1 ("Always-on")**. The other ADR-028 contracts (try/finally placement, failures swallowed as warnings, summary fires on every exit path *when enabled*, output to `executive_summary.md` at the run folder root) are preserved verbatim. The "always-on" framing is replaced by "always-on **when declared**."

### Why a profile-level block, not a stage in `stages:`

Two alternatives were considered:

- **A stage entry with `mode: executive_summary`.** Rejected. The stage list governs the pipeline loop. Executive summary is a *finalisation* step that runs outside the loop, after PR creation, on every exit path including blocked ones. Putting it in `stages:` would either require special-casing it inside the loop (the very magic this ADR removes) or invent a new "finalisation stages" list, which is more surface area than the actual decision warrants.
- **A boolean flag (`executive_summary: true`).** Rejected. It evolves poorly — the moment you want an agent override, the flag has to grow into a mapping, breaking older YAML. Starting with a mapping (`{}` for defaults) avoids that migration.

The profile-level block is the same shape already used for `pr_draft` (ADR-029). Two finalisation steps, both opt-in via a profile-level mapping, both with an optional agent override — the pattern is now uniform.

### Why update every bundled profile rather than treating omission as default-on

Per-profile defaults imply behaviour that's invisible in the YAML. The acceptance criterion in issue #162 is explicit: omission means disabled, presence means enabled. Bundled profiles preserve current UX by declaring the block, not by relying on a default. A reader of `full.yaml` can now see that an executive summary will be produced; before this ADR, that fact lived in `_finalize_summary` and nowhere else.

## Consequences

- New profiles must opt in. A profile that omits the block produces no `executive_summary.md`, no finalisation node in the diagram, and no warning. This is documented in the bundled profiles by example.
- The CLAUDE.md invariant from ADR-028 ("Executive summary … is always-on") is rephrased to gate on the profile declaration. The PR-creation invariant from ADR-019 is unchanged.
- `_finalize_summary` itself is unchanged — it still swallows failures and never affects the pipeline exit status. The change is purely at the call site (and at the renderer's graph-init site).
- `Profile.executive_summary` is a new typed field; tests that construct `Profile(...)` directly need to pass `ExecutiveSummary()` (or omit the field for the opt-out path). The test-helper `_simple_profile` defaults to opt-in to keep diff size small.
- Resumed runs whose `_plan_graph.yaml` predates this ADR remain readable — those graphs already contain an `executive_summary` node; only newly-initialised graphs for opt-out profiles will omit it.
