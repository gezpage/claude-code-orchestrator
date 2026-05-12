---
status: accepted
date: 2026-05-10
affects: [orchestrate.py, run_stage.py, prompts/discovery/planning.md]
---

# ADR-013: Parallel Discovery Fan-Out via Planning Agent

**Status:** Accepted
**Date:** 2026-05-10

## Context

The original discovery stage was a single monolithic agent that read the feature overview and produced a general findings file. This produced shallow coverage: one agent cannot simultaneously reason about code entry points, product requirements, observability signals, and risk without each concern diluting the others. The findings were often generic and gave the alignment and specification stages insufficient signal to work with.

The implementation stage already demonstrated that parallel dispatch via `ThreadPoolExecutor` is viable and useful. The question was whether a similar pattern could work for discovery, and if so, how to determine which tracks to run — since the right set of tracks varies by feature.

Two approaches were considered for track selection:
- **Fixed tracks:** always run the same N tracks (code, product, observability, risk); suppress inapplicable ones.
- **Planning agent:** a first-pass agent reads the feature brief and decides what tracks to run, how many, and what each track should focus on.

The fixed approach is simpler but produces boilerplate tracks for features where they don't apply, and cannot invent new track types when the feature warrants them. The planning agent adds one serial step before the parallel fan-out but produces a track list that is calibrated to the actual feature.

A synthesis agent (to merge all track findings into a single document) was also considered and rejected in favour of a structured index in the aggregated signal plus a consistent output format enforced by track prompts. The synthesis agent would repeat work that downstream agents do anyway, at the cost of an extra serial step and an additional failure point.

## Decision

Discovery is restructured as a two-phase operation special-cased in `orchestrate.py`, following the same pattern as `alignment` and `implementation`:

**Phase 1 — Planning agent (serial):** Reads the feature overview, decides what tracks to run (suggested defaults: code-entry-points, product-requirements, observability, risk — but the agent can invent or omit tracks as warranted). Writes one concise, bullet-point-only prompt file per track to `{run_folder}/stages/discovery-{name}-prompt.md`. Emits a signal with the track list.

**Phase 2 — Track agents (parallel via ThreadPoolExecutor):** Each track agent runs with its pre-generated prompt file, passed to `run_stage` via a new `prompt_file` parameter that bypasses Jinja2 template rendering. Track prompts are authored by the planning agent, not shipped as static templates. Each track emits a `summary` (2–3 sentences) and a `findings_file` path in its signal.

**Phase 3 — Python aggregation (no agent):** `orchestrate.py` collects all track signals and builds the unified discovery signal with a `tracks` index (name + summary + findings_file) and a `findings_files` array. No synthesis agent is run.

`run_stage` gains two optional parameters:
- `prompt_file: str | None` — if set, reads the prompt from this path instead of rendering from a Jinja2 template.
- `schema_name: str | None` — if set, uses this name for schema validation instead of the stage name. Needed because planning and track stages share the stage name `"discovery"` but validate against different schemas (`discovery_planning.json`, `discovery_track.json`).

New schemas: `discovery_planning.json` (planning signal), `discovery_track.json` (per-track signal). `discovery.json` updated to include the `tracks` array alongside `findings_files` (kept for downstream compatibility).

Any track failure blocks the entire discovery stage. This is consistent with how implementation slice failures are handled.

## Consequences

- Discovery now produces N findings files (one per track) instead of one. Downstream stages (alignment, specification) receive a `tracks` index in the signal that lets them navigate the files without reading all of them blindly.
- The planning agent adds one serial step before parallel execution. For a feature with 4 tracks, total discovery wall-clock time is approximately one planning call plus the longest single track call, rather than one long monolithic call.
- Track prompt files are visible in `{run_folder}/stages/` for inspection alongside all other stage prompts — satisfying the auditability requirement.
- Track prompts are generated prose by the planning agent, not static templates. This means track prompt quality depends on planning agent quality. If the planning agent produces a malformed prompt, the downstream track agent may fail or emit a weak signal.
- The `findings_files` field in the discovery signal is maintained for backward compatibility with any downstream stage that references it directly.
- The observability track is a suggestion only. Projects that have MCP-accessible observability tooling will need a future mechanism to pass `--mcp-config` flags to specific tracks — tracked as a future change in `project.yaml`.
