---
status: accepted
date: 2026-05-17
affects: [orchestrator/plan/_render.py, orchestrator/plan/_graph.py, orchestrator/plan/_update.py, orchestrator/run_stage.py, orchestrator/orchestrate.py, orchestrator/schemas/decomposition.json, orchestrator/schemas/discovery_planning.json, orchestrator/prompts/decomposition/default.md, orchestrator/prompts/discovery/planning.md]
---

# ADR-034: Input/Output box live-context redesign

**Status:** Accepted
**Date:** 2026-05-17

## Context

Each stage in `plan.md`'s mermaid block renders as a triple — `{id}_prompt` (blue input parallelogram) → `{id}` (the agent rect) → `{id}_panel` (the output panel). The prompt box surfaced only "Prompt" (a link to the rendered `.md`) and the panel surfaced an output link, a prose summary, and pill buttons for artefact files the stage produced. Two gaps:

1. The user could not see, before or during a stage, which files the agent was about to read — only the prompt artifact itself. For long-running stages this meant the diagram looked identical for minutes despite real work happening.
2. The user could not see, from the diagram alone, which commits a stage had produced. Commits were already in the signal JSON (`commit_hashes`) and were already enumerated in the run-summary section further down `plan.md`, but the panel only showed a prose excerpt and artefact pills.

We considered three ways to surface "files the agent reads":

- (a) Scan the rendered prompt text for backtick-wrapped paths that resolve to real files. Robust to any prompt, no schema changes, but noisy (catches example paths in code blocks) and post-hoc.
- (b) Walk the prompt-render `variables` dict using the existing `*_path` / `*_file` / `*_paths` / `*_files` convention that `_declared_artifact_paths` already relies on. Tight; requires no per-stage schema work; but unhelpful for stages dispatched with a pre-rendered `prompt_file` (slices, tracks) where the dispatching Python never sees the variable bindings.
- (c) Extend the planner-stage signals to emit a per-slice / per-track `inputs` list. Explicit; requires schema and prompt edits; gives precise control to the agent that knows what its generated prompt references.

For commits, we considered emitting plain text vs hyperlinks. Hyperlinks need a base URL; the orchestrator only knows the GitHub PR URL after `_finalize_pr` runs (`set_pr_node`). Doing per-stage git-remote inspection at render time would re-couple the renderer to git state.

## Decision

The Input parallelogram is redesigned as a body block (sharing the panel's `_PANEL_DIV_STYLE`) with:

1. An **"Input" title** in the same big-title span used elsewhere.
2. A **"Prompt" link** (or literal text before the prompt file exists).
3. **Pill-style file buttons** — same `_PILL_STYLE` as the output panel's artefact pills — one per `Node.inputs` entry, anchored at the run folder, the docs root, or rendered as a non-clickable label when neither resolves.

`Node.inputs` is populated at dispatch time, before the agent runs. Two sources:

- For Python-rendered prompts (the common case): `run_stage` walks `variables` via `_extract_input_paths` using the same `*_path` / `*_file` / `*_paths` / `*_files` naming convention `_declared_artifact_paths` uses (option (b)), filtering to existing files and excluding the prompt file itself. Then `set_node_inputs` stamps the node and re-renders.
- For pre-rendered prompts (slices, tracks): the dispatching code in `orchestrate.py` reads an explicit per-slice / per-track `inputs` list from the upstream planner's signal — `decomposition.slice_inputs` (array aligned by index with `slice_files`) and `discovery_planning.tracks[].inputs` — and passes it through `run_stage(..., inputs=...)` (option (c)). The planning prompts are extended to ask the agent to emit this list.

The Output panel is extended to render `Node.commits` between the prose summary and the artefact pills, one `Commit #<short-sha>` line per hash. `_update_plan_md` shortens each `commit_hashes` entry to seven characters and stamps it on `Node.commits` when a passed signal carries hashes. Hyperlinks are produced only when the graph's `pr` node has a URL set: a small regex parses the canonical `https://github.com/<owner>/<repo>/pull/N` URL and substitutes `/commit/<sha>` for each link. Before PR creation the same lines render as plain text; `set_pr_node`'s existing post-creation `replace_mermaid_block` call upgrades every stage panel's commits to clickable links on the next render — no extra plumbing required.

The dispatch path also accepts an explicit `node_id` parameter so callers whose graph node id does not match `output_suffix or stage` (review reviewers, discovery tracks, the discovery-planning stage) can pin the right node.

## Consequences

- `Node` gains two persisted fields (`inputs: list[str]`, `commits: list[str]`) round-tripped via `_plan_graph.yaml`. Older graph files load unchanged because both default to empty lists.
- `decomposition.json` and `discovery_planning.json` schemas grow optional fields (`slice_inputs`, `tracks[].inputs`). A planner agent that omits them produces an Input box with only the Prompt link — no regression, but no input pills either.
- The renderer now has a hard expectation that input file paths in `Node.inputs` are addressable as run-folder-relative, docs-root-relative, or just a label. Paths that live in the repo root (or any tree the docs site does not host) render as non-clickable labels rather than broken links.
- Future commit-link backends (GitLab, internal forges) would need to extend `_commit_base_url` — currently GitHub-only.
- The pre-existing `rerender_plan_md` call in `run_stage` was collapsed into the `set_node_inputs` re-render, so the prompt link still surfaces pre-dispatch with one re-render instead of two.
- `run_stage` gains two optional parameters (`inputs`, `node_id`). Test doubles (e.g. `tests/e2e_harness.FakeRunStage`) must accept them; the existing harness was updated accordingly.
