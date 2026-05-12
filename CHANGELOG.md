# Changelog

All notable changes to the orchestrator are recorded here.
Format: [Unreleased] at the top, dated releases below, newest first.

---

## [Unreleased]

### Changed
- All three reviewer stages (implementation, architecture, tests) now run with `cwd=repo_root` and receive `{{ repo_root }}` in their prompt templates, enabling purposeful codebase exploration to substantiate findings; fix-cycle re-reviews propagate the same variable and working directory.

- Refactored `plan.py` (782 lines) into a `plan/` subpackage with nine focused private modules; `orchestrate.py` dispatch now branches on `ExpansionKind` (tracks/slices/prompts/none) from a new typed `StageConfig`/`Profile` model in `profile.py`, eliminating all hardcoded stage-name strings from dispatch logic and diagram generation.

### Fixed
- `orchestrate.py` / `review_cycle.py`: all reviewers were writing to the same `review-prompt.md` / `review-output.md` files because `output_suffix` was never passed to `run_stage()`; now each reviewer in round 1 writes to `review-{reviewer}-{prompt,output}.md` and each fix-cycle reviewer writes to `review-{reviewer}-round{N}-{prompt,output}.md`; fix-implementation cycles write to `fix-implementation-{N}-{prompt,output}.md`.
- `review_cycle.py`: `context_path` was missing from `review_vars` in fix cycles, causing a Jinja2 `UndefinedError` in all review templates (`architecture`, `implementation`, `tests`); now loaded from the specification signal at the start of `run()`.
- `plan.py`: removed white background boxes from mermaid stage subgraphs by setting `clusterBkg` and `clusterBorder` to `transparent` in the diagram theme variables.
- `run_stage.py`: prompt render failures (e.g. Jinja2 `UndefinedError`) are now caught, logged to the run log and console via `OrchestratorLogger`, and returned as a blocked signal so the pipeline marks state correctly instead of crashing with a raw traceback.

- `implementation.md` review prompt: expanded from 4 generic bullets to a structured 7-dimension checklist (correctness, code quality, architecture, API behaviour, testing, security, production readiness) with explicit guidance on subtle issues such as mutable object leaks, TOCTOU races, swallowed errors, and missing test enumeration; review output format now requires severity-tagged blocking issues and explicit missing-test list.
- `architecture.md` review prompt: expanded from 4 generic bullets to an 8-dimension checklist (invariant alignment, layering, coupling, interface design, cohesion, hidden state, concurrency safety, design cost/over-engineering); output format now requires severity-tagged blocking issues with file citations.
- `tests.md` review prompt: expanded with coverage mapping (criterion-to-test), mandatory named missing-test enumeration, assertion quality, test naming, flakiness signals, mocking discipline, and test level appropriateness checks; output format now requires blocking issues and an explicit missing-tests section.
- `qa/default.md`: added QA persona ("prove it doesn't work"), explicit qa-report.md structure (criterion table, test gaps, regression risk, confidence), defined confidence and regression-risk level criteria, false-positive test detection, and clarified that `status: "failed"` is required when `outcome` is `fail`.
- `fix-implementation/default.md`: added blocking-first prioritisation, conflict detection (emit blocked rather than resolve silently), per-concern commit discipline, prohibition on opportunistic refactoring, and clean working tree check before signal.
- `discovery/default.md`: added 8-area investigative checklist (touch points, tests, data model, API contracts, auth boundaries, performance paths, patterns, prior decisions), explicit findings.md structure (executive summary, what is clear, ambiguities, risks, patterns, suggested alignment questions), and scope restriction to repo_root.
- `alignment/autonomous.md`: added explicit alignment-log.md structure (blocking items, Q&A sections with resolution/reasoning/alternatives/risk, architectural decisions, open items for developer review), qualifying decision criteria, and log completeness requirement.
- `specification/default.md`: added PRD section template (problem statement, goals, non-goals, success criteria, constraints, out-of-scope), context.md template with completeness requirement (no cross-references to other files), and ADR template with YAML frontmatter.
- `harvest/default.md`: added ADR-vs-KB decision criteria with examples and bars, ADR template, KB entry template (context/insight/example/when-to-apply), deduplication check against existing files, and harvest bar ("would absence cause a future run to repeat a mistake?").
- `decomposition/default.md`: added vertical-slice anti-pattern ("a slice is not 'add the database schema'"), slice quality checklist (end-to-end path, independently testable, ≤1 day, demonstrable), and ambiguity surfacing in slice spec rather than silent resolution.
- `implementation/default.md`: added ambiguity gate (emit blocked with specific question rather than guessing), and clean working tree check before emitting signal.
- `discovery/planning.md`: added track focus quality guidance ("a question, not a topic"), explicit scope-bounding requirement for each track prompt.
- `alignment/interactive.md`: added read-back step before writing log, completeness requirement for the log (reconstructable by someone not in the session).

### Added
- plan.md display improvements: Start/Done stadium nodes (indigo) bookend the flowchart; review fan-out/fan-in now correctly routes reviewer sub-nodes to the next stage instead of the review-parent node; File Manifest replaces the Stage column with a Time column (HH:MM:SS mtime) and adds bold stage-header rows; a Run Summary section appears after the mermaid block showing per-stage duration and a total-elapsed headline; implementation stage sections list each commit message and short hash; elapsed times persist to `_state.yaml` via `state.save_stage_elapsed`.
- plan.md visual improvements: Mermaid subgraph boxes group agent nodes per stage; Run Summary shows colored duration text (green→red by speed) and a Cumulative column; File Manifest shows filename-only link text and pairs `-prompt`/`-output` files on a shared row; parallel impl slices now record individual per-thread elapsed times instead of shared group wall-clock.
- ENH-001: `project_context_path` injected into all stage variables (path: `{docs_root}/projects/{project}/context.md`); `run_pipeline()` creates the file if absent so spec agents always have a readable baseline; harvest stage now updates this file after each run so meta-context and standing constraints accumulate across runs; all downstream stage prompts (implementation, QA, review) read `context_path` under a Jinja2 guard so pipelines without a spec stage continue to work.

---

## [0.5.0] — 2026-05-11

### Added
- `plan.expand_discovery_nodes()` replaces the single `discovery` node with `discovery_planning` + fanout circle + per-track nodes + fanin circle, mirroring the implementation fan-out pattern; `orchestrate.py` calls it after planning completes and updates each track node's status and timing as tracks run.
- `plan.add_fix_cycle_node()` inserts `fix_impl_N` and `review_{reviewer}_{round}` nodes into the mermaid diagram whenever a review cycle runs, so the fix-implement → re-review flow is visible rather than hidden.
- `review_cycle.run()` now calls `plan_mod.add_fix_cycle_node` before each fix stage and `plan_mod.update_plan_md` after the fix and each re-review, with elapsed timing and verdict reflected in the diagram.
- Decomposition prompt now instructs agents to derive execution waves from the dependency graph and emit `slice_groups`; parallel slices in a wave each run in their own git worktree (via `_create_worktree` / `_merge_worktree_branch` / `_remove_worktree` in `orchestrate.py`) to prevent index races when committing.
- `plan.md` now appends a `## Stage` section below the mermaid diagram each time a stage passes, containing the output summary and relative-path markdown links to any files the stage produced (findings, PRD, slices, review log, ADRs, KB files, alignment log).
- `plan.md` header replaced with an H1 title (`# project · feature`) and a started timestamp line; mermaid diagram now includes an "Orchestration Flow" title; node colours updated to saturated fills with white text and a mid-grey edge colour for visibility in dark mode; stage sections now include template name in heading (e.g., "Alignment (Interactive)") and a timing line (`HH:MM → HH:MM (Xm Ys)`); a "## File Manifest" table is appended at the bottom and refreshed after each stage, listing all files in the run folder.
- "Slice" renamed to "Implementation Slice" throughout: diagram node labels, log messages, `_output_summary`, and `_signal_summary`.
- Engineering standards injection: `orchestrator/standards.py` discovers `harsh-*-engineering-standards` skills from `.claude/skills/` and injects their content (frontmatter stripped) into stage prompts. The `general` standard is always included first; per-project standards are declared in `project.yaml` under a `standards:` list. Per-stage opt-in is controlled by `standards: true` in the profile YAML — added to `implementation` and `qa` in the built-in `full` profile. Six harsh-* skill symlinks added to `.claude/skills/` pointing to the docs repo.

### Changed
- `.claude/settings.json`: removed redundant `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` env var (inherited from user settings), added `Bash(pytest:*)` allow rule (moved from `settings.local.json`).
- `.claude/settings.json`: removed `env` block entirely — all vars now inherited from user-level settings.
- `.claude/settings.local.template.json`: deleted; template is no longer needed now that `settings.local.json` is gitignored and the shared rules live in `settings.json`.
- `.gitignore`: added `.claude/settings.local.json` so personal local settings are never committed.
- `.claude/settings.local.json`: cleared shared tool rules now that they live in `settings.json`; file is gitignored and each developer can populate from `settings.local.template.json`.

### Fixed
- Pre-flight validation now checks that `repo-root` is a git repository (via `git rev-parse --git-dir`) immediately after verifying the path exists, so misconfigured projects fail fast before any stage is dispatched instead of surfacing a cryptic git error mid-pipeline.
- `resume` no longer requires `blocked_at` in state; runs interrupted without an explicit stage failure (e.g. process killed mid-pipeline) can now be resumed using the completed-stages list that `run_pipeline` already skips correctly.
- Stage response files now written as `{stage}{tag}-output.md` instead of `{stage}{tag}.md`, preventing the agent's stdout from overwriting artifact files the stage writes to the same path (e.g. `discovery-code-entry-points.md`).
- `plan.md` "Orchestration Flow" is now a markdown `##` heading before the mermaid fence, not a mermaid `title:` directive.
- Stage completion sections now appear for every stage: `_append_stage_section` inserts new sections before `## File Manifest` instead of appending to the end of the file (where they were immediately truncated by the manifest refresh).
- `## File Manifest` table ordering corrected: root run-folder files (`_state.yaml`, `run.log`) appear first, followed by stage subdirectories sorted by earliest file mtime (reflecting execution order) rather than alphabetically.
- Discovery stage section now lists each track's name and summary as bold sub-entries below the aggregate summary line; any stage whose signal carries a `tracks` array with `name`/`summary` fields gets the same treatment automatically.

---

## [0.4.0] — 2026-05-10

### Added
- Interactive stage support: stages with `mode: interactive` in the profile YAML now launch a `claude` interactive session (inheriting the terminal) instead of pausing and requiring manual pipeline resume. A new `artifact` field declares the expected output file; after the session exits the pipeline checks for it and continues or blocks. `run_interactive_stage()` added to `run_stage.py`; the alignment special-case in `orchestrate.py` is replaced by a generic `mode: interactive` handler.
- `profiles/full.yaml` updated with `artifact: alignment-log.md` and `prompt: prompts/alignment/interactive.md` on the alignment stage.
- `prompts/alignment/interactive.md` rewritten as an agent-facing prompt (rendered and passed as initial context to the interactive session).
- `plan.py` now generates `flowchart TD` (top-down) diagrams instead of `flowchart LR`, adds a run-metadata header above the Mermaid block, reads H1 titles from slice files to label implementation nodes, and emits `fanout_N`/`fanin_N` circle nodes around any slice group with multiple parallel slices.
- Discovery stage restructured as a parallel fan-out: a planning agent reads the feature overview, decides which tracks to run, and writes a concise prompt file per track; track agents then run in parallel via `ThreadPoolExecutor`. Replaces the previous single-agent monolithic discovery. See ADR-013.
- `run_stage()` gains `prompt_file` and `schema_name` optional parameters. `prompt_file` bypasses Jinja2 template rendering and reads the prompt from a pre-generated file; `schema_name` overrides the schema lookup key used for signal validation.
- New schemas: `discovery_planning.json` (planning agent signal), `discovery_track.json` (per-track signal). `discovery.json` updated with a `tracks` array.
- New prompt: `prompts/discovery/planning.md` — instructs the planning agent to decide tracks and write bullet-point-only track prompt files.
- `projects/orchestrator/adrs/ADR-012-bare-flag-on-stage-invocations.md` — documents the decision to pass `--bare` to all stage subprocess invocations.
- `projects/orchestrator/adrs/_template.md` — ADR template with required YAML frontmatter (`status`, `date`, `affects`) for all new ADRs.
- `projects/orchestrator/DEVELOPMENT.md`: "When to Write a New ADR" expanded into a five-step process pointing at the template; ADR-012 added to the index.

### Changed
- Run folder reorganized: every stage now writes its transcripts and artifacts into a dedicated subfolder named after the stage (e.g. `discovery/`, `alignment/`, `specification/`); only `_state.yaml`, `run.log`, and `plan.md` remain at the run root. The `stages/` flat directory and root-level `slices/`/`adrs/` directories are gone. The review artifact is renamed `review-log.md` to avoid collision with the stage transcript in the same folder. All prompt files, Python path construction, and tests updated accordingly.
- Logging overhauled for clarity and scannability: stage column padded to 14 chars for alignment; "stage starting" logs removed (redundant before dispatch); per-field signal dump replaced with a single timed completion line including a human-readable summary derived from the signal; dispatch messages now include track/slice/implementation name rather than the stage name again; "already passed — skipping" demoted to DEBUG; WARN emitted when review requests changes; stage-level completion logs added for discovery and implementation (previously silent); review-cycle log messages clarified; signal fields preserved at DEBUG level for diagnostics.
- `--profile` now accepts a built-in name (`full`, `spike`) or a path to a YAML file; docs-repo `workflow/profiles/` lookup removed. Built-in profiles moved into the package at `orchestrator/profiles/` and included in package data. `profiles/full.yaml` had misleading `prompt` field on discovery stage removed. New `spike` profile added (discovery only). Tests updated; `test_load_profile.py` added.
- README rewritten with concepts table, profiles reference, and full parameter docs for all three commands. CLI help text for `--profile` updated to reflect new behaviour.
- `run_stage()` and `_run_claude()` accept an optional `cwd` parameter forwarded to `subprocess.Popen`, so implementation, QA, and fix-implementation stage agents run with `repo_root` as their working directory — unqualified git commands can no longer silently target the wrong repository.
- `review_cycle.run()` now receives and passes `repo_root` so the fix-implementation stage resolves the `{{ repo_root }}` template variable it already referenced.
- Implementation and QA prompts now include an explicit constraint requiring `git -C {{ repo_root }}` for all git operations.
- `CLAUDE.md`: "Bugfix Workflow" renamed to "Change Workflow" and made applicable to all changes (bugfix, feature, refactor). Bug-specific `overview.md` lookup step removed. ADR gate promoted to an explicit numbered step with template reference and frontmatter requirements. Changelog update added as a mandatory pre-commit step.

### Fixed
- `signal.stage=` debug log omitted; the stage name is already present in every log line's tag column.
- `repo_root` is now surfaced in the header of all stage prompts that may need to read source code (discovery/default, discovery/planning, specification, harvest); discovery/planning also injects it into the track prompt format it writes, so generated track prompts carry the correct source path. Test fixtures updated to include `repo_root`.
- Alignment prompt templates now iterate over the `findings_files` array instead of referencing a hardcoded `findings.md` path; the hardcoded path only exists in single-shot discovery runs, not multi-track planning runs.
- Discovery planning prompt now renders the feature overview read path as an absolute path (`docs_root/feature_path/overview.md`); previously used a relative path that stage agents (no MCP access) could not resolve with the Read tool.
- QA prompt now lists individual `slice_files` paths (from the decomposition signal) instead of instructing the agent to read a directory path, which the Read tool does not support.
- Implementation stage now filters non-slice artifacts (e.g. `dependency-graph.md`) from `slice_files` before dispatch; decomposition prompt updated to explicitly prohibit including the dependency graph in `slice_files`.
- Discovery planning phase now hardcodes `"planning"` as the prompt implementation instead of deriving it from the profile's `prompt` field; a profile specifying `prompts/discovery/default.md` previously caused the planning phase to run the single-shot discovery prompt, producing a `findings_files` signal instead of the required `tracks` signal.
- Pipeline now fails immediately with a clear message when `--feature-path` does not resolve to a directory containing `overview.md`, rather than dispatching a planning agent that silently improvises and emits a non-conforming signal. CLI help text updated to clarify that `--feature-path` is a directory, not a file. "No tracks" error message improved to hint at the path issue.
- Harvest stage crash: `review_md` (path to `review.md` in the run folder) is now seeded in `_build_variables` as a base variable derived from `run_folder`, so it is always available regardless of whether the run was freshly started or resumed from an older `_state.yaml` that predates the review-signal field.
- `_create_branch()` now uses `git -C repo_root checkout -b` instead of bare `git checkout -b`, preventing branch creation in the orchestrator's own working directory instead of the target project repo.

---

## [0.3.0] — 2026-05-09

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
- `CLAUDE.md`: `--bare` invariant added alongside `--dangerously-skip-permissions`.

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
