---
status: accepted
date: 2026-05-17
affects: [orchestrator/bootstrap.py, orchestrator/cli.py, orchestrator/orchestrate.py]
---

# ADR-037: Explicit project bootstrap command

**Status:** Accepted
**Date:** 2026-05-17

## Context

New target repositories are easy to under-configure. The common failure mode is:

1. User creates `docs/projects/<project>/project.yaml` pointing at a code repo.
2. User points the orchestrator at a feature with no `.cco.yaml` and no recognised
   toolchain markers (`pyproject.toml`, `package.json`, `composer.json`, `go.mod`,
   `pom.xml`, etc.) in the code repo root.
3. Deterministic verification's recipe-detection step returns `None` and
   `engine.verify(...)` writes a "skipped" report (see `_skipped_report` in
   `orchestrator/verifiers/engine.py`).
4. The verification stage signal carries `verification_status: "skipped"` —
   which `_plan_status_from_signal` already renders as the gray `skipped` node
   per the issue #172 fix — but the overall pipeline still completes "passing"
   because no required gate was hit.

For coding-challenge / demo projects this looks like a green run even though no
test ever executed. Issue #177 is the request to make this state explicit and
recoverable rather than silently green.

Two paths existed for fixing this:

- **Implicit setup**: have the pipeline scaffold `.cco.yaml` and minimal marker
  files automatically when none are detected. Rejected — it mutates a repo the
  user did not ask us to mutate. That is the kind of "magic" that breaks the
  user's mental model of which commits belong to which stage and which files
  were written by which actor (the user, the implementation agent, or the
  scaffolder).
- **Explicit bootstrap command**: a separate CLI verb the user runs once, that
  writes a deterministic set of template files based on a chosen toolchain.

## Decision

Add a first-class CLI command `orchestrator bootstrap` (defined in
`orchestrator/cli.py`, implemented in `orchestrator/bootstrap.py`):

- It takes `--docs-root`, `--project`, `--toolchain`, plus `--dry-run`,
  `--force`, and `--commit / --no-commit`.
- It reads `repo-root` from the docs-side `project.yaml`.
- It writes `.cco.yaml` (always) plus minimal marker files when absent
  (`pyproject.toml` for python, `package.json` and `tsconfig.json` for
  typescript, `composer.json` for php, `go.mod` for go, `pom.xml` for java,
  `package.json` for node).
- It refuses to overwrite an existing file whose contents differ from the
  template, unless `--force` is set (or the user confirms in a TTY).
- It optionally appends a matching `standards:` entry to the docs-side
  `project.yaml` (`python`, `nodejs`, `typescript`, `go`, `java`; `php` is
  absent because no harsh-php-engineering-standards skill exists today).
- It optionally stages and commits the bootstrap with the fixed message
  `chore: bootstrap orchestrator project config`.

Supported toolchains are `python`, `node`, `typescript`, `php`, `go`, `java`.
Templates are static strings in `orchestrator/bootstrap.py` — no LLM is invoked.

Adding a new toolchain means: add the templates and the dispatch case in
`_templates_for`, optionally extend `STANDARDS_FOR_TOOLCHAIN`. No changes to
`orchestrate.py`, `cli.py`, or the verifier engine.

### Startup detection

`run_pipeline` calls `_maybe_warn_unbootstrapped` after preflight. When the
repo has no `.cco.yaml` and no bundled recipe matches it:

- In any mode, print a `[WARN]` line that explains verification will be
  skipped and `plan.md` can look green even though no tests ran.
- In a TTY, offer to bootstrap inline (uses the same module as the CLI
  command). The user picks a toolchain; if accepted, files are written, the
  matching standards entry is added, and the user is asked whether to
  commit. Aborting at any step continues the pipeline without verification.
- In non-TTY, also print the exact `orchestrator bootstrap …` command the
  user should run.
- On `--resume`, skip the check entirely — the warning is only useful at the
  start of a fresh run.

The pipeline never aborts on this check. A repo with no toolchain is a valid
state (greenfield projects, prose-only work). The decision is to refuse to be
quiet about it, not to refuse to run.

## Consequences

- Bootstrap is a one-shot, idempotent, explicitly-invoked action. Re-running it
  is safe: matching files are skipped, diverging files require `--force`.
- New toolchains in the bootstrap roster do not require changes to the verifier
  engine, the CLI dispatcher, or any orchestration code — only the templates
  table and the toolchain enum in `cli.py`.
- The startup warning surfaces the failure mode that issue #177 was filed
  against. Quietly passing on an unbootstrapped repo is no longer possible:
  the user sees an explicit warning on every fresh run.
- The startup check is intentionally narrow (no `.cco.yaml` AND no recipe
  match). A repo with a `.cco.yaml` that pins a toolchain unknown to the engine
  raises elsewhere (see `_resolve_recipe`'s `VerificationError`), so we do not
  duplicate that diagnostic here.
- The interactive bootstrap path inside `_maybe_warn_unbootstrapped` and the
  CLI `bootstrap` command share the same `bootstrap.plan_bootstrap` /
  `apply_plan` / `update_project_standards` / `commit_changes` primitives, so
  behaviour cannot drift between the two entrypoints.
- New invariant for ecosystem support: adding a toolchain to the verifier
  recipes does not automatically extend `bootstrap`. The two surfaces (detect
  + scaffold) are coupled by intent but kept loosely coupled in code — a
  recipe can ship without a template, and vice versa. The bootstrap roster is
  the deliberate set of "we are willing to scaffold these for you", which is a
  smaller commitment than "we will run verification if you scaffolded them
  yourself".
