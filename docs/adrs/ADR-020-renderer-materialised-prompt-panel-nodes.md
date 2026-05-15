---
status: accepted
date: 2026-05-15
affects: [orchestrator/plan/_render.py, orchestrator/plan/_helpers.py, orchestrator/plan/_constants.py, orchestrator/plan/_init.py]
---

# ADR-020: Renderer materialises prompt, panel, and overview nodes around stage nodes

**Status:** Accepted
**Date:** 2026-05-15

## Context

The plan-graph model from ADR-016 stored one node per workflow stage. The
mermaid block rendered each stage as a single rectangle containing the stage
title, mode/elapsed sub-info, and an inline run of `<a>` tags for every related
file in the run folder — prompt, output, transcript, PRD, context, and so on.
Subgraphs wrapped each stage with the stage's display name as the cluster
label, doubling the title outside the box.

That layout made every stage rectangle a busy, multi-line block of underlined
text. The output file (the most significant artefact of each stage) had no
visual distinction from a transcript. There was no place to surface
output-derived information without further inflating the stage node, and the
duplicated stage title outside the box added noise on small diagrams.

We iterated visually on a new layout in `plan.md` and converged on a design
where every stage is surrounded by three materialised nodes — a `Prompt`
parallelogram before it, a JSON-style panel after it that fronts the output
artefact, plus a single `Overview` parallelogram before the very first stage.
Subgraph wrappers are removed. The chain reads as a single vertical column.

## Decision

The renderer (`orchestrator/plan/_render.py`) now materialises prompt and
panel partners around each `rect`-shape stage node at render time:

- `{id}_prompt` — lean-right parallelogram (`[/.../]`) carrying the link to
  the stage's `*-prompt.md` (or just the literal "Prompt" if no file exists
  yet). Styled with the new `input` classDef.
- `{id}_panel` — rectangle containing the bold "Output" link header (when
  the stage has an `*-output.md` file), a placeholder JSON body derived from
  the stage's status, and any remaining artefact files rendered as pill-style
  buttons. Styled with the new `json` classDef.
- A single `overview` parallelogram between `Start` and the first stage's
  prompt, linking to `projects/{project}/features/{feature}/overview.md`.

Deterministic stages (those with `mode == "deterministic"`) get no prompt
partner because they don't produce a `*-prompt.md`; their panel still renders.

Edge endpoints in the graph are rewritten when serialised: `A → B` becomes
`A_panel --> B_prompt` (when both stages have partners), `Start → first` is
split into `Start --> overview` and `overview --> first_prompt`, and chain
edges (`A → B → C`) are broken into per-pair edges so the middle node can
serve as both the source-with-panel of one edge and the target-with-prompt of
the next.

The graph model is unchanged. `Subgraph` records remain in the model because
`_init`/`_expand`/`_fix` still write them, but the renderer ignores them —
nothing in the rendered mermaid block references a subgraph. The
`clusterBkg`/`clusterBorder` theme variables are dropped from the init
directive accordingly.

Stage labels themselves are slimmed: the first line is a prominent
`<span style='font-size:18px;font-weight:bold;'>...</span>` containing the
display name plus status icon; the second line is a compact `impl · Mode · ⏱`
sub-line. File links no longer live inside stage labels.

## Consequences

- The mermaid block grows: each stage emits three nodes (prompt, stage,
  panel) plus an internal `prompt → stage → panel` chain pair, where it
  previously emitted one. For an eight-stage workflow this is roughly 24
  node declarations and ~16 chain edges; layouts remain manageable because
  the main column is a single linear chain.
- The panel body is filled from the stage's prose `*-output.md` at render
  time — the fenced ```json``` signal block and any bare `SIGNAL_JSON:`
  lines are stripped, and the resulting prose is truncated to a
  panel-friendly cap. ADR-004 (the orchestration session must not
  accumulate stage output content) still holds: the read is bounded,
  performed inside the renderer, and the extracted prose is discarded
  immediately after the diagram is written — it never feeds a downstream
  stage. Stages without an output file yet fall back to a status-derived
  word ("pending", "blocked", "in progress…"). The earlier placeholder
  JSON body (`{"status": "ok"}`) was uninformative — it duplicated
  what the colour/icon already showed.
- `Subgraph` records in the graph model are now dead data from the renderer's
  perspective. Removing them entirely would be a wider refactor across
  `_init`/`_expand`/`_fix` and their tests; we keep them for now to limit
  blast radius. A future cleanup ADR can retire them.
- Test assertions about edge format had to be rewritten to match the new
  rewriting (`A_panel --> B_prompt`, `Start --> overview --> first_prompt`,
  per-pair edges instead of `A --> B --> C` chains). The contract with
  downstream consumers — that the mermaid block surfaces every stage's
  prompt, output, status, and artefacts — is preserved.
- The `output` classDef defined in earlier iterations is not used (the
  former output parallelogram is now folded into the panel header). It is
  not added to `_CLASSDEFS`; the `input` and `json` classDefs are new.
