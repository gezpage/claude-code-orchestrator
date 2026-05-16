# Orchestrator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Pipeline sequencer for feature development. Takes a feature spec and drives it through a fixed sequence of stages — discovery, alignment, specification, decomposition, implementation, QA, verification, review, harvest — by orchestrating Claude Code agents, managing state, and coordinating parallel execution.

> ## Safety notice — read before running
>
> Orchestrator dispatches stage agents via the `AgentRunner` seam (see [ADR-018](docs/adrs/ADR-018-agent-runner-abstraction.md)). The default backend, `ClaudeCodeRunner`, dispatches `claude` with `--permission-mode auto` — the next-most-permissive mode short of `bypassPermissions`. The Codex backend defaults to `--sandbox workspace-write`. Each stage runs **unattended** and, depending on the configured backend's permission mode, has full ability to:
>
> - **execute arbitrary shell commands** in your `repo-root` and `docs-root`
> - **read, write, and delete files** under those roots
> - **make and amend git commits**, create branches, and create worktrees
> - **invoke any tool the Claude Code CLI exposes** to the stage agent
>
> There are no per-tool permission prompts, no human approvals between stages, and no built-in sandbox. The pipeline is designed for **trusted, single-tenant developer workstations and controlled CI runners** — not shared, multi-tenant, or untrusted environments.
>
> Before running:
>
> 1. **Treat `repo-root` and `docs-root` as fully writable** by the agent — back up or commit anything you can't lose.
> 2. **Run inside isolation** when feasible: a dedicated container, VM, or devcontainer with no host filesystem mounts beyond the repos. Sandbox/container isolation is the recommended deployment posture (tracked in [issue #14](https://github.com/gezpage/claude-code-orchestrator/issues/14)).
> 3. **Review every PR by hand before merging.** Stage agents commit and push on their own; merge is the human gate. Do not auto-merge.
> 4. **Do not expose orchestrator-managed repositories or credentials to untrusted input** — a malicious feature spec or downstream prompt could steer an agent into unintended actions.
> 5. **Keep credentials out of the repos.** Use short-lived tokens, scope them tightly, and rotate after long runs.
>
> See [SECURITY.md](SECURITY.md) for the full threat model summary, vulnerability reporting process, and credential handling guidance, and [docs/threat-model.md](docs/threat-model.md) for trust boundaries and known unsafe modes.

## Overview

Building a feature with an AI agent is straightforward for a single task. Building one that requires research, specification, parallel implementation across multiple worktrees, and multi-pass code review is not. Context grows unboundedly, state is lost between sessions, and there is no structure to enforce quality gates between phases.

Orchestrator solves this by acting as a thin coordination layer around Claude Code. It renders a structured prompt for each stage, dispatches a Claude Code subprocess, extracts a typed signal from the output, and uses that signal to drive the next stage. The orchestrator session itself never reads the content of any stage output file — only references (paths, hashes, status values) flow upstream. This keeps the orchestration session's context bounded regardless of how many stages run or how large their outputs are.

Each run produces a folder of stage prompts, raw outputs, and a `_state.yaml` that lets the pipeline be interrupted and resumed at any stage boundary.

## Design Philosophy

**Bounded context.** The main orchestration session operates on signal JSON only, never on stage output file contents. Downstream stages receive output references — file paths, commit hashes — and read them directly. This is a hard invariant: adding a file read to `orchestrate.py` breaks the token-minimisation contract and causes unbounded context growth across long pipelines.

**Signals as contracts.** Each stage emits exactly one `SIGNAL_JSON:` sentinel line carrying a structured dict with status, message, and typed output fields validated against a schema. The schema is the interface; any prompt implementation that satisfies it can be substituted. If a stage fails to emit the sentinel, the orchestrator sends one grace retry prompt before marking the stage as blocked.

**Stages as trusted workers.** Stage agents are dispatched through the `AgentRunner` seam ([ADR-018](docs/adrs/ADR-018-agent-runner-abstraction.md)) under whichever backend the active profile selects. The default backend, `ClaudeCodeRunner`, dispatches `claude` with `--permission-mode auto`: full built-in tool access with Claude's permission system engaged at its most permissive setting short of `bypassPermissions`. This is the intended use case — unattended, trusted pipeline execution on a developer workstation or controlled CI environment. The runner's permission flag is a runner-level invariant, not a project-wide invariant — a different backend (e.g. Codex with `--sandbox workspace-write`) is free to enforce sandboxing instead.

**Human-in-the-loop by design.** The alignment stage is a declared pipeline pause point. When the pipeline reaches it, the orchestrator prints a `resume` command and exits. The developer runs alignment in a full interactive Claude Code session — with Forge MCP, full tool access, and human participation — then reinvokes the orchestrator to continue. This is not a workaround; it is the documented execution model for stages that require human judgment.

## Pipeline

The default `full` profile runs nine stages in sequence:

| Stage | Expansion | Description |
|-------|-----------|-------------|
| **discovery** | tracks | Planning agent determines research tracks; each track runs in parallel via `ThreadPoolExecutor` |
| **alignment** | — | Pipeline pause; developer collaborates in an interactive Claude Code session and produces `alignment-log.md` |
| **specification** | — | Produces a formal spec from discovery findings and alignment feedback |
| **decomposition** | — | Breaks the spec into named implementation slices |
| **implementation** | slices | Each slice runs in a dedicated git worktree in parallel; merged back on completion |
| **qa** | — | Quality assurance pass against the implemented branch, run from the repo root |
| **verification** | — | Deterministic, in-process check — runs the toolchain's lint/test/build commands and probes, writes `VERIFY.md` and `verify.json` for reviewers. No Claude invocation. See [ADR-017](docs/adrs/ADR-017-deterministic-verification-stage.md). |
| **review** | prompts | Architecture, implementation, and tests reviewers run in parallel; triggers up to two fix cycles if changes are requested |
| **harvest** | — | Final outcome summary and documentation |

Stages with `tracks` expansion run a planning agent first, then fan out to N parallel track agents. `slices` expansion runs each slice in its own worktree and merges branches back. `prompts` expansion fans out to multiple reviewer agents concurrently. Fix cycles (up to two) run in the same run folder — no new run is created.

## Install

Requires Python 3.11+ and [pipx](https://pipx.pypa.io/).

```bash
pipx install -e /path/to/orchestrator
```

## Concepts

| Term | Meaning |
|------|---------|
| **docs-root** | Root of your docs repo (e.g. `/path/to/docs`). All project config and run output lives here. |
| **project** | Folder name under `{docs-root}/projects/`. Contains `project.yaml` with `repo-root` and optional settings. |
| **feature-path** | Docs-relative path to the feature directory. Must contain `overview.md`. The orchestrator reads this to understand what to build. |
| **branch** | Git branch created in the code repo when the implementation stage starts. |
| **profile** | Defines which stages to run and in what order. Either a built-in name or a path to a YAML file. |
| **run folder** | Created automatically under `{docs-root}/projects/{project}/workflow/runs/{feature-slug}/{date}-run-{N}/`. Holds all stage output and pipeline state. |

## Project configuration

Each project requires a `project.yaml` file at `{docs-root}/projects/{project}/project.yaml`.

```yaml
repo-root: /path/to/my-api     # required: path to the code repository
log_level: DEBUG               # optional: orchestrator log level (default: DEBUG)

# Engineering standards injected into implementation and QA stage prompts.
# Each entry maps to a harsh-{name}-engineering-standards skill in .claude/skills/.
# The general standard is always included automatically.
standards:
  - php
  - mysql

# Optional: codebase-backed domain-language glossary. Path is relative to
# repo-root. When set, specification reads the canonical glossary, downstream
# stages use the run-local copy, and harvest proposes new terms that the
# orchestrator appends to the canonical file (append-only — existing
# definitions are never overwritten; conflicts surface in
# $RUN_FOLDER/glossary-reconciliation.md). See ADR-027.
domain_language:
  path: docs/domain-language.md
```

Only `repo-root` is required. The `standards` list is optional — if omitted, only the general engineering standard is injected when the active profile opts stages in via `standards: true`. Add any identifier that has a corresponding `harsh-{name}-engineering-standards` skill symlinked in the orchestrator's `.claude/skills/` directory.

The `domain_language` block is optional. Omitting it leaves all stages unchanged; configuring it activates the glossary lifecycle described in ADR-027.

The default profile is controlled by the `--profile` CLI flag (defaults to `full`); it is not read from `project.yaml`.

## Profiles

Built-in profiles ship with the package:

| Name | Stages |
|------|--------|
| `full` *(default)* | discovery → alignment → specification → decomposition → implementation → QA → verification → review → harvest |
| `minimal` | specification → decomposition → implementation → verification → review (single reviewer) — no discovery, alignment, QA, or harvest. Uses the default `claude_code` backend at the user's default Claude model. |
| `minimal-codex` | Same stages as `minimal`, but dispatches autonomous stages through the `codex_cli` backend (`--sandbox workspace-write`) using the user's Codex CLI default model. The implementation stage overrides to `--sandbox danger-full-access` so it can write `.git` and commit. Intended for fast local runs when the Claude runner is unavailable. |
| `minimal-claude` | Same stages as `minimal`, but pins the Claude runner to model `claude-opus-4-7` and routes the review stage through `codex_cli` (`--sandbox workspace-write`). Use this when you want a fixed Claude model for the implementation chain and a Codex reviewer; for the default model and a Claude reviewer, prefer `minimal`. |
| `spike` | discovery only — research and findings, no implementation |

To use a custom profile, pass a path to any YAML file:

```bash
--profile /path/to/my-profile.yaml
```

A profile YAML lists the stages to run. Example:

```yaml
name: no-alignment
stages:
  - stage: discovery
    expansion: tracks

  - stage: specification
    prompt: prompts/specification/default.md

  - stage: decomposition
    prompt: prompts/decomposition/default.md

  - stage: implementation
    prompt: prompts/implementation/default.md
    standards: true
    expansion: slices
    slices_from_stage: decomposition
    cwd_from_repo_root: true

  - stage: qa
    prompt: prompts/qa/default.md
    standards: true
    cwd_from_repo_root: true

  - stage: review
    expansion: prompts
    prompts:
      architecture: prompts/review/architecture.md
      implementation: prompts/review/implementation.md
      tests: prompts/review/tests.md

  - stage: harvest
    prompt: prompts/harvest/default.md
```

Interactive stages require `mode: interactive` and an `artifact` field:

```yaml
  - stage: alignment
    mode: interactive
    artifact: alignment-log.md
    prompt: prompts/alignment/interactive.md
```

## Agent backends

Autonomous stages dispatch through an `AgentRunner` (see [ADR-018](docs/adrs/ADR-018-agent-runner-abstraction.md)). The backend is selected via an optional `agent:` block at the profile level, with optional per-stage overrides:

```yaml
name: mixed
agent:                       # profile-level default
  backend: claude_code
  model: opus
  sterile_context: true      # default — sets CLAUDE_CODE_DISABLE_AUTO_MEMORY=1

stages:
  - stage: implementation
    prompt: prompts/implementation/default.md
    agent:
      model: sonnet           # stage-level override; backend inherited from profile

  - stage: review
    expansion: prompts
    prompts:
      architecture: prompts/review/architecture.md
    agent:
      backend: codex_cli
      model: gpt-5.1-codex
```

| Backend | Selector | Command shape | Notes |
|---------|----------|---------------|-------|
| Claude Code | `claude_code` *(default)* | `claude <prompt> --permission-mode auto [--model <m>] [--strict-mcp-config --mcp-config '{"mcpServers":{}}']` | Dispatches via OAuth/keychain auth (`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are stripped from the forwarded env to prevent stale keys overriding the keychain — see ADR-022). `--permission-mode auto` is the next-most-permissive mode short of `bypassPermissions` (ADR-025). `sterile_context: true` (default) sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and adds `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` to suppress globally configured MCP servers (ADR-023). Hooks, LSP, plugin sync, keychain reads and repo-root `CLAUDE.md` auto-discovery remain active — reproducibility-sensitive runs should prefer `codex_cli`. |
| Codex CLI | `codex_cli` | `codex exec <prompt> --sandbox <mode> [-m <model>]` | `permission_mode` accepts `read-only`, `workspace-write` *(default)*, `danger-full-access` (lifts the FS sandbox so e.g. `.git` writes work), and the explicit `full-auto` alias (maps to `--dangerously-bypass-approvals-and-sandbox` — no sandbox and no approvals). Requires the `codex` binary on PATH. |

Current limitations:

- Anthropic API and OpenAI API backends are not implemented (the seam is in place — adding them is a single module + one branch in `agent_runner/_select.py`).
- Output streaming/JSON-mode normalisation is not implemented; `output_mode` is consumed by `ClaudeCodeRunner` (maps to `--output-format`) but ignored by Codex.
- Interactive stages (`mode: interactive`) bypass the runner seam and always use `claude`.
- Fix-cycle dispatches inside `review_cycle.py` currently use the default runner; per-stage backend overrides do not propagate into fix cycles.

The effective backend and model for each stage are persisted to `_state.yaml` under the `agent:` key so a finished run is reproducible without re-deriving config from the profile.
Deterministic stages use `mode: deterministic` — they run pure Python in-process and never invoke Claude. The only deterministic stage today is `verification`:

```yaml
  - stage: verification
    mode: deterministic
```

## Verification config

The deterministic `verification` stage detects the repo's toolchain by looking for marker files (e.g. `package.json`, `go.mod`) and runs the matching bundled recipe under `orchestrator/verifiers/recipes/`. Repos without a recognised toolchain produce a benign `skipped` report — verification is not a hard gate.

To override the bundled behaviour, drop a `.cco.yaml` at the **code repo root** (the `repo-root` from `project.yaml`, not the docs root):

```yaml
verification:
  toolchain: node              # optional — pins detection; skips marker-based auto-detect
  commands:                    # optional — REPLACES the recipe's commands wholesale
    - id: test
      command: npm test
      required: true
      if_script_exists: test
      timeout_seconds: 600
    - id: lint
      command: npm run lint
      required: false
      if_script_exists: lint
  probes:                      # optional — REPLACES the recipe's probes wholesale
    - node_manifest_sanity
```

Overrides replace rather than merge — predictable beats clever. Bundled recipes ship for `node` and `go`; probes ship as `node_manifest_sanity` and `go_module_sanity`. Adding a new toolchain means adding a recipe YAML (and any probes it needs) under `orchestrator/verifiers/`, not editing orchestration code. See [ADR-017](docs/adrs/ADR-017-deterministic-verification-stage.md).

## Commands

### `orchestrator run` — start a pipeline

```bash
orchestrator run \
  --docs-root <path>       # optional: path to your docs root (prompted if omitted)
  --project <name>         # optional: project folder under docs-root/projects/
  --feature-path <path>    # optional: docs-relative path to feature directory
  --branch <name>          # optional: git branch to create for implementation
  --profile <name|path>    # optional: built-in name or .yaml file (default: full)
  --base-branch <name>     # optional: branch to fork from (default: main, or project.yaml)
  --create-pr/--no-create-pr   # optional: open a draft PR on completion
```

Every flag is optional. If you run `orchestrator run` with no flags on a TTY, you'll be prompted for each missing value: project (picker), feature path (auto-detected from `overview.md`), branch (default suggested from the feature slug), profile (picker), base branch (default `main`), and whether to open a draft PR. In non-TTY contexts (CI, piped scripts), missing flags cause an immediate exit with a clear error rather than a hang.

**Examples:**

```bash
# Full pipeline with default profile
orchestrator run \
  --docs-root /path/to/docs \
  --project my-api \
  --feature-path projects/my-api/features/auth-refresh \
  --branch feat/auth-refresh

# Research only (no implementation)
orchestrator run \
  --docs-root /path/to/docs \
  --project my-api \
  --feature-path projects/my-api/features/auth-refresh \
  --branch feat/auth-refresh \
  --profile spike

# Custom profile from a file
orchestrator run \
  --docs-root /path/to/docs \
  --project my-api \
  --feature-path projects/my-api/features/auth-refresh \
  --branch feat/auth-refresh \
  --profile ~/profiles/no-alignment.yaml
```

The pipeline pauses automatically at interactive stages (e.g. alignment) and prints a `resume` command to continue.

#### GitHub integration

When the repo's `origin` remote points at GitHub and `--create-pr` is enabled (or you opt in at the prompt), the orchestrator opens a draft PR after the last stage passes. This requires the [GitHub CLI](https://cli.github.com/) — `gh` must be on PATH and `gh auth login` must have completed. If `gh` is missing or unauthenticated, the run will offer to skip PR creation rather than hard-failing.

If `origin` is missing or non-GitHub when you opt in, the orchestrator offers three options: link an existing GitHub URL, create a new GitHub repo via `gh repo create` (with prompts for name, visibility, and description), or continue without the PR feature.

Resolved defaults are persisted to `project.yaml` as `base-branch` and `create-pr`. Subsequent runs pre-fill these; CLI flags still win.

The PR appears as a draft and is never marked ready for review automatically — the human is always the one to flip that switch.

---

### `orchestrator resume` — continue after a pause

```bash
orchestrator resume \
  --run-folder <path>      # required: path to the blocked run folder
  --docs-root <path>       # required: path to your docs root
```

The run folder path is printed by the orchestrator when the pipeline pauses. Project, feature, branch, and profile are read from the run folder's saved state.

**Example:**

```bash
orchestrator resume \
  --run-folder /path/to/docs/projects/my-api/workflow/runs/auth-refresh/2026-05-10-run-1 \
  --docs-root /path/to/docs
```

---

### `orchestrator stage` — run one stage directly

Runs a single stage in isolation. Intended for debugging and development.

```bash
orchestrator stage \
  --stage <name>               # required: stage name (e.g. discovery, specification)
  --input <path>               # required: path to JSON file containing input variables
  --run-folder <path>          # required: path to the run folder
  --docs-root <path>           # required: path to your docs root
  --project <name>             # required: project name
  --project-log-path <path>    # required: path for project-level orchestrator.log
  --implementation <name>      # optional: prompt implementation name (default: default)
```

**Example:**

```bash
orchestrator stage \
  --stage specification \
  --input /tmp/spec-input.json \
  --run-folder /path/to/docs/projects/my-api/workflow/runs/auth-refresh/2026-05-10-run-1 \
  --docs-root /path/to/docs \
  --project my-api \
  --project-log-path /path/to/docs/projects/my-api
```

Exits `0` on success, `1` on failure. Prints the stage signal JSON to stdout.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Read `CLAUDE.md` before making code changes — it documents the architectural invariants every contributor must follow.

## Releasing

Releases are cut manually by a maintainer. Merges to `main` do not auto-tag; the act of releasing is a deliberate workflow dispatch. See [ADR-014](docs/adrs/ADR-014-explicit-release-workflow.md) for the rationale.

To cut a release:

1. Ensure `main` is in the state you want to ship and that its CI is green.
2. Update the `CHANGELOG.md` `[Unreleased]` entries if needed and merge any final docs PRs.
3. Open the **Actions** tab → **Release** workflow → **Run workflow** → choose `main` → **Run workflow**.

The workflow:

- scans every commit between the last `vX.Y.Z` tag and `HEAD` for conventional-commit prefixes;
- computes the next version (`feat!:`/`BREAKING CHANGE` → major, `feat:` → minor, `fix:` → patch — strongest signal in the range wins);
- re-runs lint, format check, type check, tests, builds the wheel and sdist with `uv build`, installs the wheel, and runs `orchestrator --help` as a smoke test;
- pushes the new tag and creates a GitHub Release with auto-generated notes covering the released range.

If the range contains no `feat:`/`fix:`/`feat!:`/`BREAKING CHANGE` commits, the workflow fails with a clear message — "nothing to release" is a real error worth surfacing rather than silently no-op'ing.

## Tests

```bash
uv run pytest tests/
```
