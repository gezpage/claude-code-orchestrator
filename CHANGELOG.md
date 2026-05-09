# Changelog

All notable changes to the orchestrator are recorded here.
Format: [Unreleased] at the top, dated releases below, newest first.

---

## [Unreleased]

### Added
- Parallel implementation slice dispatch via `slice_groups`. Decomposition agent now
  emits an ordered list of execution waves alongside `slice_files`; the orchestrator
  dispatches slices within each wave concurrently using `ThreadPoolExecutor`, falling
  back to sequential order when `slice_groups` is absent. `plan.md` writes are guarded
  by a threading lock; each parallel slice writes to a unique stage output file via
  `output_suffix`.
- `slice_groups` field added to `orchestrator/schemas/decomposition.json`.
- `orchestrator/plan.py` — extracted Mermaid plan generation out of `orchestrate.py`
  into three public functions: `init_plan_md`, `expand_impl_nodes`, `update_plan_md`.
  Flowchart direction changed to left-to-right (`LR`); node styles use a named
  `classDef` palette instead of inline `style` directives.
- `tests/test_plan.py` — full unit-test coverage for all three plan functions,
  including alignment gate shape, multi-reviewer fan-out, idempotency, and elapsed
  time / output summary rendering.

### Changed
- `run_stage.py`: `--bare` added to every `claude` subprocess invocation. Skips
  MCP server connections, CLAUDE.md loading, hooks, skills, and auto-memory at
  stage startup; Bash and file tools remain fully available. Reduces per-stage
  startup overhead with no functional impact (stages use only direct file writes
  and shell commands, not MCP).
- Grace prompt (emitted when a stage omits `SIGNAL_JSON`) now includes the stage
  name and explicit examples for both `passed` and `blocked` outcomes, replacing
  the generic one-liner.
- Decomposition prompt rewritten to enforce tracer-bullet vertical slices with a
  canonical slice-file template (`## What to build`, `## Acceptance criteria`,
  `## Blocked by`).
- Implementation prompt adds an idempotency check (skips re-implementing a slice
  that already has a matching commit), a TDD red→green cycle, and test-quality
  rules (public-API-only, mock only at system boundaries).
- Review/tests prompt adds interface-coupling and refactor-survivability checks.

### Removed
- Inlined `_STYLE_MAP`, `_format_elapsed`, `_node_label`, `_init_plan_md`, and
  related helpers removed from `orchestrate.py` (now live in `plan.py`).
- Root-level `prompts/` and `schemas/` directories deleted. All runtime path resolution
  uses `Path(__file__).parent` within the package; the root copies were unreferenced
  stale duplicates (some behind the package copies).

---

## [0.2.0] — 2026-05-08

### Added
- `orchestrator/plan.py` (predecessor): Mermaid plan tracking added to
  `orchestrate.py` with elapsed-time display and stage output summaries on nodes.
- Auto-commit rule added to `CLAUDE.md`: every discrete task must be staged and
  committed before reporting done.
- Bugfix workflow section added to `CLAUDE.md`.
- `.claude/settings.json` — Claude Code permission allow-list and skill symlinks
  wired up for the orchestrator dev environment.
- `.claude/skills/` — symlinks to shared skills: `orchestrator`, `tdd`, `git-workflow`,
  `decomposition`, `grill-me`, `kb-authoring`, `create-doc`, `write-a-skill`,
  `commenting`, `to-prd`.
- `claude-skill/SKILL.md` — Claude Code skill shim so the orchestrator itself can
  be invoked as a `/orchestrator` skill from other projects.

### Changed
- `cli.py` `resume` command: replaced interactive `click.prompt` fallbacks for
  missing state keys with hard `UsageError`s (state keys are always written since
  S-09; prompting silently masked corrupt state).
- `logger.py`: all levels now write to the project-wide log (previously only
  `INFO`/`ERROR` did); `WARN` level added; console output suppressed for `DEBUG`.
- `orchestrate.py`: Mermaid node styles upgraded from bare fill colours to
  full `style` directives with stroke and text colour; elapsed time and signal
  summaries rendered into node labels.
- `run_stage.py`: grace prompt expanded with stage name and emit examples;
  `_GRACE_PROMPT` constant replaced with inline f-string for stage context.
- Prompts moved from top-level `prompts/` into `orchestrator/prompts/` so they
  are packaged with the distribution.
- Renderer test updated to match relocated prompt template paths.

---

## [0.1.0] — 2026-05-07 (initial build — slices S-01 through S-09)

### Added
- **S-01** — Standalone repo scaffold: `pyproject.toml`, `CLAUDE.md`, package
  skeleton, `.gitignore`.
- **S-02** — `paths.py` + `state.py`: path resolution helpers and `_state.yaml`
  read/write with atomic replace.
- **S-03** — `signal.py` + `validator.py` + JSON schemas for all stage signals
  (`discovery`, `specification`, `decomposition`, `alignment`, `implementation`,
  `review`, `qa`, `harvest`).
- **S-04** — `logger.py` + `renderer.py`: dual-sink logger (per-run + project-wide
  log files) and Markdown run-log renderer.
- **S-05** — `run_stage.py`: single-stage Claude Code subprocess dispatch with
  signal extraction, schema validation, grace-prompt retry, and formatted output
  capture.
- **S-06** — Stage prompt templates for all eight stages plus the
  `fix-implementation` remediation prompt.
- **S-07** — `orchestrate.py`: full pipeline loop — state load/save, per-stage
  dispatch, alignment gate pause, plan.md Mermaid tracking.
- **S-08** — `review_cycle.py`: review → fix → re-review loop with a two-cycle
  limit enforced via `_MAX_CYCLES`.
- **S-09** — `cli.py` + `profiles/full.yaml`: Click CLI with `run` and `resume`
  commands; `full.yaml` profile wiring all eight stages in order.
