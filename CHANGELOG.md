# Changelog

All notable changes to the orchestrator are recorded here.
Format: [Unreleased] at the top, dated releases below, newest first.

---

## [Unreleased]

### Added
- 2026-05-15: New `claude_code_auto` backend (`claude -p --permission-mode auto`) and bundled `minimal-claude` profile (claude_code_auto for non-review stages, codex_cli for review). Transitional Claude path for environments without `ANTHROPIC_API_KEY`; `--bare` is intentionally absent, sterile-context still suppresses ambient auto-memory.
- 2026-05-15: `verification_status: "failed"` now triggers a `fix-verification` cycle before the review stage. The cycle dispatches a fix agent with `VERIFY.md` and `verify.json` as its primary inputs, then re-runs deterministic verification. If the fix makes no commits or re-verification still fails, the pipeline blocks immediately rather than falling through to review in a broken state. This reserves the two review fix cycles for code-quality issues rather than toolchain-setup problems. See ADR-021.

### Fixed
- 2026-05-15: `_create_branch` now checks working-tree cleanliness even when already on the target branch, closing the gap where resumed runs could dispatch QA against uncommitted implementation output.
- 2026-05-15: QA stage no longer blocks on project-surface findings (e.g. fake lint scripts) that pre-existed on the base branch and were not introduced by the feature branch; the check now uses `git show <base>:<file>` to verify the specific offending content rather than file-level diff
- 2026-05-15: `base_branch` is now propagated to all stage prompt templates via `_build_variables`, replacing the hard-coded `main` in QA surface verification
- 2026-05-15: `_create_branch` no longer checks `is_clean` when the repo is already on the target branch, preventing spurious "working tree not clean" failures at the start of stages that follow implementation (2026-05-15)

### Changed
- 2026-05-15: Per-stage `*-transcript.md` files are now written as `*-stream.log` and contain the full raw agent CLI stream (banner, command logs, diffs, token accounting). Previously the codex runner trimmed its on-disk file to only the clean final agent message — which duplicated `*-output.md`. `result.stdout` (consumed by signal-JSON parsing) continues to use the clean last-message when available. Renames `transcript_path` → `stream_log_path` on `AgentRunRequest` / `AgentRunResult`.
- 2026-05-15: Plan mermaid renderer now materialises a `Prompt` input parallelogram and a `JSON`-style panel around every stage, plus a single `Overview` input between `Start` and the first stage. The panel folds the previous Output parallelogram into a bold header, embeds a status-derived JSON stub, and surfaces other stage artefacts as pill-style buttons. Stage labels are slimmed to a prominent title and a compact `impl · Mode · ⏱` sub-line. Subgraph wrappers are no longer rendered. Edges are rewritten through the materialised partners (`A_panel --> B_prompt`). See ADR-020.
- 2026-05-15: The bundled `full` profile now runs alignment autonomously by default via `prompts/alignment/autonomous.md`, and the previous interactive alignment flow is available as the new `full-interactive` profile.

### Fixed
- 2026-05-15: `_create_worktree` now sanitises the temp branch name before using it as a `tempfile.mkdtemp` prefix; branches containing `/` (e.g. `feat/my-feature-impl_1`) previously caused a `FileNotFoundError` because the slash was treated as a directory separator.
- 2026-05-15: Unhandled exceptions escaping stage dispatchers are now written to `run.log` (with full traceback) before propagating, so post-mortem investigation no longer requires reconstructing the crash from stderr alone.
- 2026-05-15: Review-cycle re-review prompts now receive the deterministic verification signal context (`verify_md_path` and `verification_status`) from `_state.yaml`, and review prompts tolerate runs without verification context. This fixes the `minimal-codex` round-2 render failure where `verify_md_path` was undefined.
- 2026-05-15: Review cycles now detect commits made by `fix-implementation` via `git rev-list` rather than trusting the agent's `commit_hashes` self-report, so a stage that commits but under-reports its SHAs (observed with the Codex backend emitting `commit_hashes: []` despite the work being done) no longer aborts the re-review with a misleading "no valid git diff" error. When the agent genuinely makes no commits, the abort message now points at the fix-implementation output file so the operator can see why. When `review-log.md` is missing the expected reviewer sections, the fix prompt's `changes_brief` falls back to a brief rendered from the in-memory findings so the agent still has actionable input.
- 2026-05-15: Fix cycles now preserve profile-selected agent backends. `fix-implementation` reuses the implementation stage runner and review reruns reuse the review stage runner, so `minimal-codex` no longer falls back to `ClaudeCodePrintRunner` after an initial reviewer requests changes.
- 2026-05-15: Plan mermaid file links now prepend `/#` to the docs-root-anchored href (e.g. `/#projects/foo/workflow/runs/.../specification-prompt.md`) so the team-hub-style hash-routed docs site resolves them via its SPA router. Without the `#` the browser URL-encoded the slashes into a single absolute path segment and the link 404'd.
- 2026-05-15: `minimal-codex` implementation stage now overrides the profile-level `workspace-write` sandbox with `permission_mode: danger-full-access` so Codex can write `.git/` and commit. The non-committing stages (specification, decomposition, review) keep the sandboxed `workspace-write` default, so Codex's filesystem isolation still protects planning and review work — only the stage that has to commit is granted full repo write access.
- 2026-05-15: `CodexCliRunner` now writes the clean final agent message (from `--output-last-message`) to `*-transcript.md` instead of the full Codex terminal stream. Previously the transcript was a wall of banner output, workdir/model/sandbox metadata, prompt echo, command logs, diffs, token accounting, and a repeated copy of the final message. Stage `*-output.md` formatting is unchanged. If `--output-last-message` was empty (e.g. the CLI crashed before writing it), the transcript falls back to the raw stream so the failure is still debuggable.
- 2026-05-15: `CodexCliRunner`'s `full-auto` alias now emits `--dangerously-bypass-approvals-and-sandbox`; Codex CLI dropped `--full-auto` in favour of the longer-named flag. README and module docstring updated to match the actual command construction.

### Added
- 2026-05-14: Bundled `minimal-codex` profile — same stage shape as `minimal` but dispatches autonomous stages through the `codex_cli` backend (`--sandbox workspace-write`) using the user's Codex CLI default model. Lets users run the minimal flow when Claude Code print-mode is unavailable, with backend selection driven entirely by profile config.
- 2026-05-14: Plan mermaid diagram now embeds clickable file links inside each node (`Prompt` / `Output` / artefact stems) and surfaces unattached run-folder files in an "Other files" node anchored as a sibling of `Done` so it lays out alongside the end of the flow. Link hrefs are full paths from the docs-root (derived via the `projects/` segment) so mermaid SVG anchors resolve correctly regardless of the rendering URL, and the anchors carry an inline `color:inherit` style so the text stays readable on the coloured status backgrounds instead of fading into default-blue. Each node also carries a `Mode: <mode>` line below its existing impl identifier. `Node` gained `mode`, `stage_dir`, and `file_suffix` fields so the renderer can map each file in the run folder to its owning node without scanning conventions inline.

### Fixed
- 2026-05-14: `minimal-codex` no longer pins `gpt-5-codex`; the bundled profile now lets Codex choose the user's configured/default model so ChatGPT-backed Codex accounts do not fail before stage execution.
- 2026-05-14: `minimal-codex` stage dispatch now passes Codex the intended workspace plus docs/run/repo writable roots, captures only the final Codex message as stage output, and blocks passed signals that declare missing artifact files so failed writes cannot advance the pipeline. Signal extraction now globally uses the last `SIGNAL_JSON` sentinel, so prompt examples cannot override the real final stage signal for any backend.
- 2026-05-14: Plan mermaid link hrefs now anchor on the trailing `projects/{project}/workflow/runs/{feature}/{run}` segments instead of the first `projects` segment from the left, so docs roots that themselves live under a directory called `projects` (e.g. `~/Dev/projects/docs`) no longer leak the host path into the URL.
- 2026-05-14: TTY-aware pre-flight that prompts for missing `run` inputs, asks for the base branch (default `main`), syncs the base branch before creating the implementation branch, and optionally opens a draft GitHub PR via `gh` once the pipeline completes. New `--base-branch` and `--create-pr/--no-create-pr` flags; existing flags become optional. PR creation failures are warnings, never pipeline failures. See ADR-019.

### Fixed
- 2026-05-14: PR finalisation (`pr_draft` stage) now honours the profile-level agent backend instead of silently falling back to `ClaudeCodePrintRunner`. A Codex-backed profile previously could finish the pipeline successfully and then attempt to invoke Claude during finalisation; the resolved runner is now passed into `run_stage`, and the recorded `_state.yaml` agent metadata reflects the effective backend/model. See ADR-019.
- 2026-05-14: Default dispatcher now creates/checks out `ctx.branch` before running any stage with `cwd_from_repo_root: true`, matching the slice dispatcher's pre-amble. Without this, the `minimal` profile's single-agent implementation would run on whatever branch was already checked out and commit there instead of the requested `--branch`.

### Changed
- 2026-05-14: `minimal` profile now runs a single-agent decomposition + implementation flow. Decomposition writes one `implementation-plan.md` and emits a `plan_file` signal; implementation runs once with `expansion: none` and consumes the plan alongside the PRD and context. The slice fan-out machinery (worktrees, waves, S-NN artefacts) only runs under the `full` profile now.

### Added
- 2026-05-14: Agent runner abstraction (`orchestrator/agent_runner/`) introducing `AgentRunner` Protocol, `AgentRunRequest`/`AgentRunResult` dataclasses, `ClaudeCodePrintRunner`, `CodexCliRunner`, and `FakeRunner`. Backend selection is config-driven via an optional `agent:` block at profile and stage levels (stage overrides shallow-merge over profile defaults). The effective backend and model are recorded in `_state.yaml` under `agent:`. Sterile context (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`) is now the default for the Claude Code backend — existing pipelines no longer inherit ambient auto-memory unless they opt out with `agent.sterile_context: false`. `run_stage()` blocks the stage on `timed_out=true` or a non-zero `exit_code` from the runner (and from the grace-retry call) before any signal extraction, so a failed agent process can never have its partial stdout parsed as a valid SIGNAL_JSON. The Codex backend defaults to `--sandbox workspace-write`; `--full-auto` is opt-in via `permission_mode: full-auto`. ADR-003 and ADR-012 are superseded by [ADR-018](docs/adrs/ADR-018-agent-runner-abstraction.md). See issue #75.
- 2026-05-14: README documents the `verification` stage, the `minimal` built-in profile, `mode: deterministic`, and the `.cco.yaml` verification override schema. Trimmed the `project.yaml` example to fields the orchestrator actually reads (`repo-root`, `log_level`, `standards`) and removed five never-read keys (`name`, `description`, `default-profile`, `merge-target`, `agent-rules`).
- 2026-05-14: Hardened git/worktree state handling at slice dispatch. A new `orchestrator/_git.py` provides explicit validators (`is_clean`, `branch_exists`, `current_branch`, `worktree_registered`, `has_merge_conflicts`, `abort_merge`) used to refuse destructive operations on dirty repos, switch onto an existing branch instead of silently continuing on a stale one, detect merge conflicts and `git merge --abort` them, and treat unexpected git states as structured `blocked` signals. Covers issue #79.
- 2026-05-14: Deterministic verification stage (`mode: deterministic`) and recipe-driven verifier framework under `orchestrator/verifiers/`. Bundled Node and Go recipes with `node_manifest_sanity` and `go_module_sanity` probes; project-level overrides via `.cco.yaml` at the repo root; `VERIFY.md` and `verify.json` artefacts consumed by review prompts. Inserted into the minimal and full profiles between implementation/QA and review. Not a hard gate — verification surfaces evidence to reviewers; repos without recognised toolchain markers produce a benign `skipped` report. See ADR-017 and issue #22.

### Changed
- 2026-05-14: Reworked the issue #69 hardening after PR review. `non_blocking_findings` is now an optional field on review signals (was incorrectly marked required, which would have broken existing/custom reviewer prompts); the schema accepts both shapes. The "reject invalid diff" rule is no longer prompt-only — the orchestrator validates the diff file before dispatching any reviewer: `_dispatch_prompts` (round 1) and `review_cycle.run` (rounds 2+) call `is_valid_diff_file`, which rejects missing files, empty files, and prose summaries (anything without a `diff --git` header). Review stages and review cycles are now blocked deterministically on invalid diff inputs instead of relying on the LLM reviewer to detect them. Accepted-risk persistence is now exercised on every cycle terminating path (success, max-cycles-fail, and invalid-diff abort) and covered by tests. QA's project-surface rules separate deterministic checks (always run, blocking on failure) from judgement checks ("documented commands run successfully" gets a "where practical" carve-out so long-running or destructive commands can be recorded as "not run + reason" without a false negative).
- 2026-05-14: Hardened review prompts and the review-cycle verifier flow per issue #69. Decomposition now requires preserving the strongest meaningful interpretation of semantic invariants (defensive copy, isolated state, structured error contract, streaming, safe callback API). The implementation reviewer blocks on reproducible mutable-reference leaks and on package-manifest / public-surface issues (fake lint scripts, broken script targets, unused production dependencies). The tests reviewer is told to cover the semantic invariants implied by the design, not only the generated acceptance criteria. QA gains a project-surface verification step and an explicit stream/pipeline abort-path checklist. All three review prompts (architecture, implementation, tests) now reject a missing or non-diff `diff` input as a blocking finding. Reviewers emit `non_blocking_findings`, which the orchestrator persists in `plan.md` under an "Accepted Risks (non-blocking)" section — even when no fix cycle ran. `review_cycle` now generates a real `review/diff-round-N.patch` from the fix-cycle commits; fix-implementation no longer reports a prose `diff` summary.
- 2026-05-14: `mypy` configuration now excludes `^build/` and `^dist/`. A stale wheel-build tree at `./build/lib/orchestrator/` collides with the source package and causes `mypy .` to bail with "Duplicate module named 'orchestrator'" before any real type-checking runs. Both directories are already gitignored — this just prevents the local artefact from breaking the tool invocation.
- 2026-05-14: E2E harness now patches `run_stage()` directly and synthesises signals from each stage's JSON schema (`orchestrator/schemas/*.json`) instead of carrying per-stage signal dicts and parsing prompt headings. Tests express divergences from happy-path via a small `overrides` dict keyed by stage call (e.g. `"review:architecture:r1"`). Adding or renaming a stage no longer requires test changes as long as the schema is in place. Replaces the previous `default_signals`/`stage_key`/`reviewer_signal` scaffolding.
- 2026-05-14: `orchestrator/plan/` now models the workflow diagram as an in-memory graph (`_graph.py`) rendered to mermaid by a dedicated renderer (`_render.py`). Init, expand, fix-cycle, and status updates all mutate the typed graph and re-render, replacing the regex rewrites that previously parsed mermaid text. See ADR-016. The graph is persisted as `_plan_graph.yaml` inside each run folder.
- Specification prompt: ADRs now default to zero per run; only required when a decision is non-obvious *and* hard to reverse (multi-module migration cost). Replaces the prior 2–4 ADR-per-run target; covers issue #57 item 11.
- Decomposition prompt: slice quality checklist now enforces a reviewability budget (≤ 400 diff lines, ≤ 10 files, ≤ 1 primary concept) and an independently-mergeable check; covers issue #57 item 9.
- Review prompts (architecture, implementation, tests): added a Triage and scope section capping each round at 5 blocking + 5 non-blocking findings, with explicit guidance against blocking on style/naming/speculative concerns; covers issue #57 item 7.

### Fixed
- 2026-05-14: Pre-existing mypy errors in test files are now resolved so `uv run mypy .` exits clean. Tests construct typed `findings_map`/`call_order` containers, narrow `extract_signal` results with `assert result is not None`, type-annotate `_load_schema`'s return assignment, and introduce a `Callable | dict` `Override` alias for `_apply_override` rather than `Any`. No production signatures widened, no `# type: ignore`.
- 2026-05-14: Plan mermaid diagram now redirects failing-reviewer fan-in through the fix-implementation node when a fix cycle is added: the failing reviewer's edge to the downstream stage is rewritten to point at `fix_impl_N`, and the new re-review nodes fan into the original downstream target. Previously the failing review node retained its arrow to the downstream stage (e.g. `harvest`) alongside the new arrow into `fix_impl_N`, and re-review nodes had no edge to the downstream target at all.
- 2026-05-14: Review-cycle round-2+ stages now receive `docs_root` in their Jinja variables; previously `fix_vars` and `review_vars` in `orchestrator/review_cycle.py` omitted it, so the shared `prompts/_includes/aliases.md` partial (introduced by ADR-015) failed to render under `StrictUndefined`, silently blocking fix-implementation and re-review on every cycle. Caught by the new e2e fix-cycle test.

### Added
- 2026-05-14: `CONTRIBUTING.md` now documents the Claude configuration policy — nothing under `.claude/` is tracked; tracked Claude assets are opt-in via `.gitignore` negation and must be justified against `SECURITY.md`. Closes #39.
- 2026-05-14: End-to-end happy-path tests for the `minimal` and `spike` profiles (`tests/test_e2e_minimal_profile.py`, `tests/test_e2e_spike_profile.py`). Both reuse `tests/e2e_harness.py` and honour `ORCH_E2E_OUTPUT_DIR`. Asserts each profile only exercises its declared stages — minimal records spec/decomp/impl/review with a single `implementation` reviewer; spike records only `discovery` with a planning prompt plus one track.
- 2026-05-14: New `minimal` profile (`orchestrator/profiles/minimal.yaml`) — four-stage pipeline (specification → decomposition → implementation → single-agent review) for small, well-understood features. Skips discovery, alignment, qa, and harvest; review uses `expansion: prompts` with a single `implementation:` entry so the fix-cycle machinery still engages. New `prompts/specification/minimal.md` reads the feature overview directly from `$DOCS_ROOT/{{ feature_path }}/overview.md` instead of an alignment log.
- 2026-05-14: End-to-end test harness (`tests/e2e_harness.py`) — shared scaffolding that mocks only `_run_claude` and routes responses by stage/reviewer/round so variant scenarios (happy path, fix cycle, failures, blockers, alternate profiles) can be expressed as a small override dict over `default_signals()`. Two scenarios shipped: `test_e2e_happy_path.py` (all stages approve, no fix cycle) and `test_e2e_fix_cycle.py` (architecture reviewer requests changes in round 1, fix-implementation runs, architecture approves in round 2). `ORCH_E2E_OUTPUT_DIR` env var pins run artefacts to a stable path for inspection.
- 2026-05-13: Path aliases (`$REPO_ROOT`, `$RUN_FOLDER`, `$DOCS_ROOT`) defined once per stage prompt via a shared `prompts/_includes/aliases.md` partial; body prose now references the aliases instead of repeating long absolute paths. `SIGNAL_JSON` examples and the discovery track-prompt template keep `{{ ... }}` Jinja so downstream consumers still see fully-expanded paths. Addresses [issue #57 item 6](https://github.com/gezpage/claude-code-orchestrator/issues/57); see ADR-015.
- `standards.discover()` prefers `COMPACT.md` over `SKILL.md` when present in a `harsh-*-engineering-standards` skill dir, letting projects ship a hard-rule list for prompt injection while keeping the full skill for human reading; covers issue #57 item 5.
- Safety notice block at the top of `README.md`, new `SECURITY.md` (reporting, threat model summary, safe execution, credential handling, unsafe-mode warning, secret-scanning guidance), and new `docs/threat-model.md` (trust boundaries, filesystem/subprocess/network/credential assumptions, sandbox expectations, known unsafe modes, hardening roadmap) covering items 1–3 of issue #53.
- CI workflow gains a `package` job: builds wheel and sdist via `uv build`, installs the wheel with `pip`, and runs `orchestrator --help` as a smoke test; covers issue #53 item 7.

### Changed
- Replaced auto-tagging `version-tag.yml` with a manual-dispatch `release.yml`: releases are now cut from the Actions UI, scan all commits since the last tag for the bump signal, re-run the full quality gate (lint, format, type, test, build, install, smoke), then push the tag and create a GitHub Release with auto-generated notes; fails loudly if no release-bearing commits are present in the range; covers [issue #42](https://github.com/gezpage/claude-code-orchestrator/issues/42), see ADR-014.
- `version-tag.yml` now only tags commits whose type is `feat:`, `feat!:`, or `fix:` (or carries a `BREAKING CHANGE` footer); `chore:`, `docs:`, `ci:`, `refactor:`, and `test:` commits no longer trigger a tag; covers issue #53 item 8.
- `version-tag.yml`: removed the `uv lock` + `git push origin main` steps that committed the lockfile back to main from CI — lockfile updates must be committed by developers to avoid push-to-main conflicts with branch protection and CI-loop risks.

### Changed
- Repository hygiene (issue #53 items 4–6): removed ``.idea/.gitignore`` from the git index (`.gitignore` already ignores `.idea/`); removed eight machine-specific `.claude/skills/` symlinks that pointed to absolute local paths (unusable on other machines — `.claude/` is and remains fully gitignored); verified `.gitignore`, CI workflow YAMLs, and `pyproject.toml` are correctly formatted with no single-line array issues.
- Reformatted `orchestrator/review_cycle.py` and `tests/test_review_cycle.py` with ruff to unblock the CI quality gate on PR #13.
- CI workflow opts into Node.js 24 via `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` to silence GitHub Actions deprecation warnings
- Added branch housekeeping step (step 5) to Change Workflow: prunes merged-PR branches via `gh pr list --state merged` before each new worktree, covering squash-merge repos where `git branch --merged` is unreliable.
- Refactored `orchestrate.py`: extracted five stage dispatcher functions (`_dispatch_default`, `_dispatch_interactive`, `_dispatch_tracks`, `_dispatch_slices`, `_dispatch_prompts`) from the 424-line `run_pipeline` god function; each dispatcher is independently unit-tested (24 new tests); `_PipelineContext` dataclass replaces 8-argument function signatures; `_DISPATCHERS` dict replaces the `if/elif` expansion chain; `run_pipeline` reduced from 424 to 147 lines.
- Review findings correlation: reviewer prompts now emit a `findings` array in their SIGNAL_JSON (one sentence per blocking issue); `review_cycle.py` injects a fix-commit divider into `review-log.md` between rounds and appends a `## Review Findings` table to `plan.md` after all cycles complete, linking each finding to the fix cycle that resolved it or marking it unresolved.

## [2026-05-12]

### Changed
- Raised ADR bar in specification and harvest prompts: added concrete negative test ("if you'd reach the same decision from language idiom or a stated constraint, it is not an ADR"), 2–4 per run target, and explicit deduplication step in harvest against specification ADRs from the same run.
- Tightened decomposition spec depth: `What to build` placeholder now specifies observable behaviour over implementation detail, with a 100–200 word soft signal; new step 5 requires explicit enumeration of all config fields/env-vars/error paths in acceptance criteria.
- Strengthened implementation test quality rules: both `{% if context_path %}` and `{% else %}` branches now require concrete value assertions for field-level checks (not just presence).

## 2026-05-12

- chore: adopt git worktree workflow in CLAUDE.md so concurrent Claude sessions are fully isolated

- chore: switch to PR-based change workflow; add auto-versioning via GitHub Actions on merge to main
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

## 2026-05-12

- docs: rewrote README with overview, design philosophy, and accurate pipeline table for GitHub audience
- chore: remove internal references (team-hub names, personal paths, internal MCP permissions) ahead of open-source publication
- docs: move ADRs into repo under docs/adrs/ and add cross-references from CLAUDE.md invariants to their ADRs
