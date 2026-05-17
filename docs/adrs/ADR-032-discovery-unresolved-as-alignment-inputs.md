---
status: accepted
date: 2026-05-17
affects: [orchestrator/orchestrate.py, orchestrator/profile.py, orchestrator/prompts/discovery, orchestrator/prompts/alignment, orchestrator/schemas]
---

# ADR-032: Discovery Unresolved Items Are Alignment Inputs, Not Pipeline Blockers

**Status:** Accepted
**Date:** 2026-05-17

## Context

Discovery surfaces ambiguities, open questions, and risks while it explores the feature. The original discovery prompt told agents to annotate risks with severity `Blocking / High / Medium / Low`, which the agent could read as "if I see something Blocking, I should return `status: blocked` and stop the pipeline." That conflated two different concepts:

- **Discovery cannot proceed.** The overview file is missing, a required path is unreadable, the agent literally has nothing to investigate. This is a true blocker — there is no signal to produce.
- **Discovery found something that needs a decision.** An auth model choice, a data-shape question, a known unknown that the spec depends on. This is not a blocker; it is exactly what alignment exists to resolve.

In practice both kinds of issues were ending up in the same `status: blocked` return, and unresolved decisions were halting pipelines that alignment could trivially have moved forward — either by deciding, by adopting a documented assumption, or by deferring with a paper trail. There was no structured handoff between the two stages: alignment had to read `findings.md` as prose and pattern-match "Suggested Questions for Alignment" headings to find what it owed an answer on.

## Decision

Discovery and alignment now exchange structured alignment inputs:

1. Discovery's signal includes three string arrays: `unresolved_questions`, `risks`, and `assumptions_needed`. Track-fan-out discovery aggregates these across tracks into one merged list per category on the parent stage signal. Empty arrays are valid and expected.
2. Discovery prompts no longer use the word `Blocking` as a severity label for findings. The prompt explicitly states that unresolved items are normal outputs and that `status: blocked` is reserved for "cannot proceed" cases (e.g. missing overview).
3. Alignment receives the three arrays as Jinja variables (via the signal-fields fall-through in `_build_variables`) and resolves each item by choosing one of: a concrete decision, a documented working assumption, or an explicit deferral. Alignment's signal includes two string arrays: `accepted_assumptions` (recorded verbatim so they can be revisited) and `unresolved_remaining` (items alignment could not resolve).
4. A new per-stage `alignment_policy` config governs what the orchestrator does with `unresolved_remaining`:
   - `on_unresolved: warn` (default) — log a warning, signal passes through, specification proceeds.
   - `on_unresolved: block` — convert the passed signal to `status: blocked` so the pipeline halts at alignment.
5. The policy gate is implemented in `_apply_alignment_policy(stage, sig, logger)` in `orchestrate.py` and is the only place this branching lives. It only fires for the `alignment` stage and only when the signal is currently `passed`; failed/blocked signals reach the normal halt path untouched.

Interactive alignment does not emit `unresolved_remaining` in its signal — the artifact-existence check is the gate, and the developer is in the room — so the policy is a no-op for `mode: interactive` alignment. The prompt still tells the developer to record **Accepted Assumptions** and **Unresolved Items Remaining** sections in the alignment log so the audit trail is consistent.

## Consequences

- Discovery prompts read "unresolved items are inputs for alignment" instead of "stop the pipeline" — agents are far less likely to over-trigger `status: blocked`.
- Alignment has a typed contract for what to resolve, so prompts can address each bucket explicitly and reviewers can audit what was answered vs. assumed vs. deferred.
- The default behaviour is permissive: a pipeline can now proceed past alignment with documented unresolved items, which makes the system useful for spike/early-stage features. Teams that need stricter governance opt in with `alignment_policy: {on_unresolved: block}` on the alignment stage.
- The gate fires only for the alignment stage — it checks `stage.name == "alignment"` and then defers all halt/continue behaviour to `stage.alignment_policy`. The stage-name check is acceptable here because the residue concept is alignment-specific (no other stage emits `unresolved_remaining`), but the *policy decision* must come from config, not from profile names or hard-coded defaults elsewhere. Adding profile-name branching to override the policy would violate the same "config drives behaviour, not name strings" principle as ADR-030.
- Profiles that do not declare `alignment_policy` get the default `warn` behaviour with no migration. The bundled `full`, `full-interactive`, and `spike` profiles inherit this default.
- The signal schemas for `discovery`, `discovery_track`, and `alignment` gain three / three / two optional array fields. Existing pipelines that do not populate them see no schema-validation error because the fields are optional.
