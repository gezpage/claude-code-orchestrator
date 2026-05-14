---
status: accepted
date: 2026-05-14
affects: [orchestrator/verifiers/, orchestrator/run_stage.py, orchestrator/profile.py, orchestrator/orchestrate.py, orchestrator/schemas/verification.json, orchestrator/profiles/minimal.yaml, orchestrator/profiles/full.yaml]
---

# ADR-017: Deterministic verification stage and recipe-driven verifier framework

**Status:** Accepted
**Date:** 2026-05-14

## Context

Pipeline health to date has been judged almost entirely by Claude reviewers
reading the working tree. That arrangement misses a class of failures that
are trivially detectable without an LLM: lint scripts that silently exit 0,
build commands that point to missing files, archive artifacts that are
malformed, documented commands that no longer run. Reviewer prompts also
narrow the surface area of "what is healthy" to whatever the generated
acceptance criteria mention, so semantic invariants slip through.

Pushing those checks into reviewer prompts has two problems. First, it costs
LLM tokens and time for work that is a `subprocess.run` away. Second, it
encourages the orchestration layer to grow ecosystem-specific branches
(`if node`, `if go`, `if python`) as new toolchains are added — exactly the
shape ADR-008 was designed to keep out of the core.

A separate verification path is needed: deterministic, configuration-driven,
and uniform across ecosystems.

## Decision

A new stage execution mode — `mode: deterministic` — is added alongside
`auto` and `interactive`. Deterministic stages run pure Python in-process;
no `claude` subprocess is spawned. The `--bare` and
`--dangerously-skip-permissions` invariants (ADR-003, ADR-012) apply only to
`run_stage()` and continue to hold there — they have no meaning for
deterministic stages, which never invoke Claude.

Verification logic lives in a new package, `orchestrator/verifiers/`:

- **Recipes** (`verifiers/recipes/*.yaml`) declare a toolchain's markers,
  commands, and probes as data. Each recipe carries a `priority` integer so
  that when multiple recipes' markers match a repository (e.g. a
  Node frontend in a Go monorepo), the highest-priority recipe wins
  deterministically.
- **Probes** (`verifiers/probes/`) are Python callables registered in an
  explicit dict — no dynamic discovery. Probes hold any language-specific
  validation logic (no-op script detection, manifest sanity, archive
  hygiene). The engine never inspects probe internals.
- **Project config** (`.cco.yaml` at the repo root) can pin
  `verification.toolchain` to skip detection, and can replace the recipe's
  `commands` / `probes` lists wholesale. Overrides replace rather than merge
  — predictable beats clever.
- **Engine** (`verifiers/engine.py`) resolves the toolchain, runs commands
  via `subprocess.run` with a per-command timeout, runs probes, aggregates
  status (`passed` / `warned` / `failed`), and writes `VERIFY.md` +
  `verify.json` into the run folder.
- **Greenfield / unrecognised repos** produce a benign `skipped` report
  (`verification_status: skipped`) rather than blocking the pipeline.
  Verification is not a hard gate, and a repo without recognised markers
  is a valid state (greenfield projects, prose-only features). A
  `.cco.yaml` pin to a recipe that doesn't exist is a different case — it
  is a user config error and raises.

The deterministic stage builds its signal dict **in-process** and hands it
up to `orchestrate.py` through the existing signal plumbing. ADR-004 is
preserved: `orchestrate.py` still does not `open()` or `Read` any artifact
file. The verifier knows everything it produced because it produced it.

Verification is not a hard gate. The signal carries paths to `VERIFY.md`
and `verify.json`; the downstream review stage reads `VERIFY.md` from the
run folder (review stages already operate with the run folder as working
context) and treats failed required commands as authoritative evidence.
This matches the existing review-driven gating model — reviewers remain the
decision-makers, but they now have deterministic evidence to point at.

Rejected alternatives:

- **Standalone verifier outside the stage system.** Would have meant
  bespoke ordering, logging, and signal handling — every consumer of stage
  output (plan diagram, state file, fix cycle) would need a special case.
- **A Claude stage that shells out to a verifier CLI.** Defeats the
  determinism goal: pays LLM cost and risks misinterpretation of structured
  output.
- **Failing the pipeline on any required-command failure.** Too brittle for
  flaky tests; removes reviewer discretion that the existing system relies
  on.
- **Throwing when multiple recipes match.** Forces explicit `.cco.yaml`
  even in obvious cases (a JS tool repo that happens to ship a Go example).
  Priority-ordered detection is predictable and overridable.

## Consequences

- Orchestration code stays free of toolchain conditionals. Adding Python,
  Java, or PHP support means adding `recipes/<name>.yaml` and any probes
  it needs — no changes to `orchestrate.py`, `run_stage.py`, or profiles.
- `run_stage.py` now hosts three dispatch paths. The `--bare` /
  `--dangerously-skip-permissions` invariants are scoped to one of them
  and must stay that way.
- `mode: deterministic` is the only path where stage code runs in the same
  Python process as the orchestrator. Probe authors must keep their work
  side-effect-light: no global state, no long-lived threads, no
  monkey-patching. Failures should raise — the engine catches and records
  them as probe failures.
- `.cco.yaml` is a new optional file at repo root. Its absence means
  "auto-detect". Its presence with `verification.commands` or
  `verification.probes` replaces the recipe lists for that key.
- `VERIFY.md` becomes part of the review stage's input context.
  Review prompts are updated to instruct reviewers to read it when present.
- Recipes and probes ship bundled with the orchestrator package. Project
  repos cannot add custom recipes without contributing them upstream — a
  deliberate constraint, revisitable if needed.
- Initial recipes cover Node and Go. Python and others are deferred until
  the framework has shaken out in practice.
