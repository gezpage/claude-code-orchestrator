---
status: accepted
date: 2026-05-07
affects: [orchestrator/schemas/, renderer.py, run_stage.py]
---

# ADR-008: Pluggable Stage Implementations with Stage-Level Output Schemas

**Status:** Accepted
**Date:** 2026-05-07

## Context

A fixed-implementation pipeline couples stage behaviour to a single prompt file per stage. Different features have different needs: alignment may be interactive for complex decisions and autonomous for well-scoped work. Discovery may need project-specific context injected. A rigid coupling prevents variation without forking the orchestrator.

Treating stages as interfaces — where the output schema is the contract and the implementation is swappable — allows multiple implementations per stage while preserving the guarantee that stage N's output is valid input for stage N+1.

## Decision

Stages are interfaces, not implementations. Each stage has exactly one output schema (enforced by Python via `jsonschema`). Any number of implementations may satisfy that schema.

- Implementations are prompt files: `prompts/{stage}/interactive.md`, `prompts/{stage}/autonomous.md`, etc. Core implementations live in the orchestrator package.
- Projects provide overrides via `workflow/prompts/{stage}.md` in the docs repo. Python appends project overrides to the core prompt after rendering both through Jinja2, separated by a `## Project conventions` heading.
- The profile YAML specifies which implementation runs per stage: `prompt: prompts/alignment/interactive.md` (core, resolved from orchestrator package) or `prompt: workflow/prompts/alignment/custom.md` (project override, resolved from docs-root). Python resolves in one step with no convention-guessing.
- Schema ownership is at the stage level: one schema per stage; all implementations must satisfy it.
- Python updates `plan.md` (Mermaid diagram) mechanically after each stage — no Claude required. `plan.md` is a Python artefact.

## Consequences

- Any stage can run in interactive, autonomous, or project-specific mode without modifying the orchestrator core.
- Profile authoring is more complex: authors must reference implementation paths explicitly.
- Schema enforcement is the only guarantee that implementations are interchangeable — schema drift across implementations breaks the contract.
- Core implementations are versioned in the orchestrator repo; project overrides are versioned in the project docs repo. Keeping them in sync is the project's responsibility.
- Hard to retrofit if the pipeline is built with fixed implementations first; the interface contract must be designed in from the start.
