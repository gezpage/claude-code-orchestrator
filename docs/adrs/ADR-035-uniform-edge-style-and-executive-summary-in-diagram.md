---
status: accepted
date: 2026-05-17
affects: [orchestrator/plan/_render.py, orchestrator/plan/_init.py, orchestrator/orchestrate.py]
---

# ADR-035: Uniform edge style and executive summary as a diagram node

**Status:** Accepted
**Date:** 2026-05-17

## Context

Two diagram concerns were tangled together in `plan.md`'s mermaid block.

First, the renderer maintained a "completed path" overlay: every edge whose target stage had reached `passed` status was added to a `linkStyle` directive with `stroke-width:3px,stroke:#9ca3af`. The intent was a visible "progress trail". In practice the diagram ended up with two visually distinct line weights — thin gray for the not-yet-reached path and thick gray for the trail — and the rest of the panel content (status icons, the green "passed" pill on the stage node, the prose summary, the green Output header) already communicated stage completion more cleanly than an edge thickness change ever could. The dual line weight competed with the panel signal rather than reinforcing it.

Second, the always-on executive-summary finalisation step (ADR-028) writes `executive_summary.md` to the run folder root but had no node in the graph. The renderer's `_scan_files` routed every root-level file to the legend, so the summary surfaced only as a small button below the diagram alongside `_state.yaml` and `run.log`. The orchestration step itself was invisible to readers of the diagram — it appeared as if `Harvest → Done` was the terminal hop when in fact `_finalize_summary` always runs between them.

We considered:

- Keeping the completed-path overlay but making it more subtle (e.g. dotted style, lighter colour). Rejected — the panel already signals completion; any edge styling is redundant noise.
- Materialising the executive_summary node only in the renderer (the same way `overview` is materialised) rather than the graph. Rejected — the executive_summary stage is a real dispatched stage with prompt/output files, runtime, and a panel body; storing it in the graph keeps the rendering rules uniform and lets `update_plan_md` flip its status the same way it flips every other stage.
- Leaving `executive_summary.md` in the legend and adding a synthetic pill on the harvest panel. Rejected — that conflates two stages and breaks the convention that each stage owns its own artefacts.

## Decision

The renderer no longer emits a `linkStyle` directive. All edges render with the default thin stroke driven by the init directive's `lineColor: #6b7280`. The `bold_indices` / `_is_passed` / `_target_completed` machinery is removed; edges are no longer indexed for restyling. The diagram reads as a single uniform flow and the panel content carries the completion signal.

The graph gains an `executive_summary` node, appended to `chain_ids` after every profile stage (and after `pr` when `create_pr` is true) and before `Done`. The node is a rect-shape stage with `mode="auto"`, `stage_dir="executive_summary"`, and backend/model resolved from the same finalisation agent (`resolve_agent_config(profile.agent, None)`) that `_finalize_summary` uses. `agent_metadata["executive_summary"]` is populated before `init_plan_md` so the runner-line in the node label is correct from first render.

To attach the root-level summary artefact to the right panel, `_scan_files` consults a small allowlist (`_ROOT_FILE_OWNERS = {"executive_summary.md": "executive_summary"}`) before falling back to the legend. The allowlist is keyed by exact filename so the change does not affect any other root-level file.

`_finalize_summary` now calls `update_plan_md` for the executive_summary node on every exit path (`passed`, `blocked`, or exception), mirroring the contract every other stage already follows. Failures here are still swallowed and never change the pipeline exit status.

## Consequences

- The diagram has one consistent edge style. Future renderer changes that want to highlight specific edges have to choose a non-stroke-width mechanism (e.g. node fills, badges) so the "all edges look the same" invariant is preserved.
- `executive_summary` is the new terminal stage in every initial graph, regardless of profile. Tests, prompts, or tooling that hardcoded "the last node before Done is X" need to assume X is now `executive_summary` (or `pr → executive_summary` when create-pr is on).
- The `_ROOT_FILE_OWNERS` allowlist is a new (small) extension surface: any future always-on finalisation step that writes a root-level artefact (e.g. a final SBOM or release manifest) registers itself here rather than re-deriving routing logic in `_scan_files`.
- Resumed runs whose `_plan_graph.yaml` predates this ADR will still render correctly because `executive_summary.md` lands in the legend (no node id collision) — only new runs pick up the in-diagram node.
