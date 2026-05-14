---
status: accepted
date: 2026-05-14
affects: [orchestrator/plan/_graph.py, orchestrator/plan/_render.py, orchestrator/plan/_init.py, orchestrator/plan/_expand.py, orchestrator/plan/_fix.py, orchestrator/plan/_update.py]
---

# ADR-016: Plan diagram as a projection of an in-memory graph model

**Status:** Accepted
**Date:** 2026-05-14

## Context

The mermaid block in `plan.md` was built by string concatenation in `_init.py`,
then mutated by every subsequent plan operation through regex rewrites of the
already-rendered diagram. `_expand.py` reassembled subgraphs with
`re.sub(...DOTALL...)` patterns; `_fix.py` walked rendered edge lines parsing
the `A & B --> C` syntax by hand to redirect fan-in; `_update.py` re-extracted
node labels from the diagram so it could rewrite them with new status icons.

State and rendering were entangled: the source of truth for the workflow
topology was the *output*, not a data structure. Every new feature
(parallel slice groups, fix-cycle redirection, fan-in handling) had to either
extend the regex matrix or accept that subtle interactions would slip through
— which is exactly what produced the failing-reviewer fan-in bug fixed in PR
#64.

## Decision

`plan.md` is now a **projection** of a `Graph` data structure. The graph
(`orchestrator/plan/_graph.py`) is the single source of truth. It is persisted
alongside `plan.md` as `_plan_graph.yaml` so concurrent runs and subsequent
process invocations can resume from it.

Every plan operation follows the same shape:

1. `load_graph(run_folder)` — read the persisted graph.
2. Mutate the graph as typed Python objects (`Node`, `Edge`, `Subgraph`).
3. `save_graph(run_folder, graph)` — write it back.
4. `render_block(graph)` — produce the full mermaid block from the model.
5. `replace_mermaid_block(plan_path, graph)` — splice it into `plan.md`,
   preserving markdown sections (stage completion, run summary, file manifest)
   that live outside the fence.

The renderer is the *only* place that knows about mermaid syntax. Mutations
never see rendered text.

Edges are modelled as a list of step-sets — `Edge(steps=[[a], [b, c], [d]])`
renders as `a --> b & c --> d` — so fan-in, fan-out, and chains all share the
same shape.

## Consequences

- All regex rewrites of the mermaid block are gone. `_init`/`_expand`/`_fix`/
  `_update` now operate on typed structures. The combined size of these
  modules dropped roughly 25%.
- New diagram features can be added by extending `Node` / `Edge` / `Subgraph`
  attributes plus the renderer. No regex matrix to extend.
- `_plan_graph.yaml` is a new run-folder artefact. It is internal state, not
  consumed by anything outside `orchestrator.plan`. Hand-editing `plan.md`
  no longer affects subsequent operations — they read the graph, not the
  rendered file. This is a deliberate trade: the graph wins.
- Tests rely on substring matches against rendered output and continue to
  pass without modification — the contract with downstream consumers (the
  shape of the rendered mermaid block) is preserved.
- The graph YAML file is not human-edited and not committed as a long-lived
  artefact; it lives inside the run folder alongside `_state.yaml`.
