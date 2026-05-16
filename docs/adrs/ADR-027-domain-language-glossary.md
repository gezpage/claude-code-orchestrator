---
status: accepted
date: 2026-05-16
affects: [orchestrator/glossary.py, orchestrator/orchestrate.py, orchestrator/prompts/specification, orchestrator/prompts/decomposition, orchestrator/prompts/implementation, orchestrator/prompts/harvest, orchestrator/schemas/harvest.json]
---

# ADR-027: Codebase-Backed Domain-Language Glossary

**Status:** Accepted
**Date:** 2026-05-16

## Context

Each pipeline stage runs in a sterile context (ADR-023) and reads only the
artifacts handed to it through prior signals (ADR-004). That isolation is the
right default — it prevents context bloat and lets stages run in parallel —
but it also means stages have no shared notion of the project's vocabulary.
Specification might call something a "draft session"; decomposition rephrases
it as a "pending interaction"; implementation chooses "preview state"; the
harvest agent invents a fourth name. Subsequent runs inherit the drift, and a
human reading the resulting code finds three names for one concept.

The codebase is the obvious anchor: if there were a canonical glossary file
checked in alongside the source, every stage could pull terms from the same
authority and harvest could feed new vocabulary back. The hard question is who
is allowed to *write* to that canonical file:

- Letting agents edit the canonical glossary directly defeats the safety goal
  — a hallucinated paraphrase silently overwrites a definition the team
  agreed on weeks ago.
- Refusing to update the canonical glossary at all defeats the discovery goal
  — vocabulary that emerges from real work never makes it back into the
  source-of-truth file.
- Requiring a human to triage every glossary change blocks the autonomous
  pipeline at the last mile, every run.

The pipeline also already enforces an invariant (ADR-004) that
`orchestrate.py` does not read stage output files, so any non-signal data
required by downstream stages has to be either declared in signal JSON or
materialised by a deterministic step that the orchestrator itself runs. Both
the run-local copy and the reconciliation step fall into that "deterministic
step" category — they are Python-side operations, not agent prompts.

## Decision

Introduce an **optional**, **opt-in** domain-language glossary backed by a
single file in the target codebase. The orchestrator treats that file as
canonical and append-only.

**Configuration.** A project enables the feature by adding to `project.yaml`:

```yaml
domain_language:
  path: docs/domain-language.md
```

The path is relative to `repo-root` — the canonical glossary lives in the
target codebase, not in the docs repo. Projects that omit `domain_language`
get no glossary variables, no run-local copy, no reconciliation; existing
profiles and prompts are unaffected.

**Lifecycle.**

1. **Setup.** Immediately after the run folder is created,
   `glossary.setup_for_run` copies the canonical file to
   `$RUN_FOLDER/specification/glossary.md` (or writes a placeholder if the
   canonical does not yet exist). `_build_variables` exposes
   `canonical_glossary_path` and `run_glossary_path` to every stage's
   prompt-rendering variables. When the feature is disabled both variables
   are empty strings so Jinja `{% if %}` blocks work uniformly.

2. **Specification.** Reads the run-local glossary, uses canonical terms
   verbatim in PRD and `context.md`, lists any new candidate vocabulary in a
   dedicated `Candidate glossary terms` section of `context.md`.

3. **Decomposition and implementation.** Consume the run-local glossary as a
   read-only reference. They never edit the canonical file.

4. **Harvest.** Compares the run-local glossary with vocabulary that actually
   emerged in the run and proposes new terms in SIGNAL_JSON as
   `proposed_glossary_terms: {term: definition}`. **The harvest agent never
   edits the canonical glossary itself.**

5. **Reconciliation.** After harvest passes, `orchestrate._reconcile_glossary`
   invokes `glossary.reconcile`, which:
   - **Appends** terms whose names are not present in the canonical file.
   - **Skips** terms whose names exist with identical definitions (recorded
     as `unchanged`).
   - **Records a conflict** when a name exists with a different definition.
     The canonical definition is never overwritten. Conflicts are logged at
     `WARN` and rendered into `$RUN_FOLDER/glossary-reconciliation.md` so the
     operator can resolve them deliberately.

Reconciliation is deliberately conservative and append-only. Anything more
clever (semantic merging, definition rewrites, deletions, reordering) is out
of scope for the initial implementation — it would require either human
review or a far higher confidence bar than agents currently meet, and the
issue (#134) explicitly flags those as non-goals.

**Why a Python helper, not an agent.** The safety rules — never overwrite,
report conflicts rather than silently merge — are deterministic by nature. A
Python module enforces them precisely; a prompt can only ask politely. The
agent's job is to *propose*; reconciliation is the orchestrator's job. This
also matches ADR-017's principle: when a step is deterministic, it should
not consume an agent's budget.

## Consequences

- Projects that opt in get cross-stage vocabulary consistency without
  sacrificing the sterile-context property each stage relies on.
- The canonical glossary remains authoritative. Even with malicious or
  buggy harvest output, an existing definition cannot be silently
  overwritten — the worst case is an `## Existing` term appearing once with
  a conflict report adjacent.
- A new architectural surface lives in `orchestrator/glossary.py`. The
  module is the single place that owns markdown parsing, append-only
  semantics, and conflict reporting. Future merge strategies (semantic
  merge, rename detection, deletion proposals) extend this module rather
  than scattering through stage code.
- The harvest stage signal now carries an optional `proposed_glossary_terms`
  field. The schema accepts an `{ name: definition }` object; absent or
  empty objects mean "nothing new". Adding more reconciliation primitives
  later (e.g. `proposed_glossary_deletions`) is a schema extension, not a
  redesign.
- Reconciliation failures do not block the pipeline. A docs-repo write
  error or a partial reconciliation is logged at WARN; the pipeline's exit
  status reflects the actual work done. This matches the established
  `_finalize_pr` policy (ADR-019).
- Prompts for specification, decomposition, implementation, and harvest now
  carry `{% if run_glossary_path %}` blocks. Projects without the feature
  see no change in rendered prompts.
- Adding glossary reads to `orchestrate.py` does not violate ADR-004: the
  orchestrator never reads *stage output files* to extract context for
  downstream stages. It does read the *canonical glossary*, which is an
  input artifact owned by the codebase, not a per-stage output. The
  run-local copy is also not read by `orchestrate.py` after it is written
  — only by agents.
