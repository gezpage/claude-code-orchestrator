# Changelog

All notable changes to the orchestrator are recorded here.
Format: [Unreleased] at the top, dated releases below, newest first.

---

## [Unreleased]

### Fixed
- Pipeline now fails immediately with a clear message when `--feature-path` does not resolve to a directory containing `overview.md`, rather than dispatching a planning agent that silently improvises and emits a non-conforming signal. CLI help text updated to clarify that `--feature-path` is a directory, not a file. "No tracks" error message improved to hint at the path issue.
- Harvest stage crash: `review_md` (path to `review.md` in the run folder) is now seeded in `_build_variables` as a base variable derived from `run_folder`, so it is always available regardless of whether the run was freshly started or resumed from an older `_state.yaml` that predates the review-signal field.

### Added
- Interactive stage support: stages with `mode: interactive` in the profile YAML now launch a `claude` interactive session (inheriting the terminal) instead of pausing and requiring manual pipeline resume. A new `artifact` field declares the expected output file; after the session exits the pipeline checks for it and continues or blocks. `run_interactive_stage()` added to `run_stage.py`; the alignment special-case in `orchestrate.py` is replaced by a generic `mode: interactive` handler.
- `profiles/full.yaml` updated with `artifact: alignment-log.md` and `prompt: prompts/alignment/interactive.md` on the alignment stage.
- `prompts/alignment/interactive.md` rewritten as an agent-facing prompt (rendered and passed as initial context to the interactive session).

- `plan.py` now generates `flowchart TD` (top-down) diagrams instead of `flowchart LR`, adds a run-metadata header above the Mermaid block, reads H1 titles from slice files to label implementation nodes, and emits `fanout_N`/`fanin_N` circle nodes around any slice group with multiple parallel slices.
- Discovery stage restructured as a parallel fan-out: a planning agent reads the feature overview, decides which tracks to run, and writes a concise prompt file per track; track agents then run in parallel via `ThreadPoolExecutor`. Replaces the previous single-agent monolithic discovery. See ADR-013.
- `run_stage()` gains `prompt_file` and `schema_name` optional parameters. `prompt_file` bypasses Jinja2 template rendering and reads the prompt from a pre-generated file; `schema_name` overrides the schema lookup key used for signal validation.
- New schemas: `discovery_planning.json` (planning agent signal), `discovery_track.json` (per-track signal). `discovery.json` updated with a `tracks` array.
- New prompt: `prompts/discovery/planning.md` — instructs the planning agent to decide tracks and write bullet-point-only track prompt files.

### Fixed
- `_create_branch()` now uses `git -C repo_root checkout -b` instead of bare `git checkout -b`, preventing branch creation in the orchestrator's own working directory instead of the target project repo.

### Changed
- `run_stage()` and `_run_claude()` accept an optional `cwd` parameter forwarded to `subprocess.Popen`, so implementation, QA, and fix-implementation stage agents run with `repo_root` as their working directory — unqualified git commands can no longer silently target the wrong repository.
- `review_cycle.run()` now receives and passes `repo_root` so the fix-implementation stage resolves the `{{ repo_root }}` template variable it already referenced.
- Implementation and QA prompts now include an explicit constraint requiring `git -C {{ repo_root }}` for all git operations.

### Changed
- `CLAUDE.md`: "Bugfix Workflow" renamed to "Change Workflow" and made applicable to
  all changes (bugfix, feature, refactor). Bug-specific `overview.md` lookup step
  removed. ADR gate promoted to an explicit numbered step with template reference and
  frontmatter requirements. Changelog update added as a mandatory pre-commit step.
- `CLAUDE.md`: `--bare` invariant added alongside `--dangerously-skip-permissions`.

### Added
- `projects/orchestrator/adrs/ADR-012-bare-flag-on-stage-invocations.md` — documents
  the decision to pass `--bare` to all stage subprocess invocations.
- `projects/orchestrator/adrs/_template.md` — ADR template with required YAML
  frontmatter (`status`, `date`, `affects`) for all new ADRs.
- `projects/orchestrator/DEVELOPMENT.md`: "When to Write a New ADR" expanded into a
  five-step process pointing at the template; ADR-012 added to the index.

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
