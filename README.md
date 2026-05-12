# Orchestrator

Pipeline sequencer for feature development. Takes a feature spec and drives it through a fixed sequence of stages — discovery, alignment, specification, decomposition, implementation, QA, review, harvest — by orchestrating Claude Code agents, managing state, and coordinating parallel execution.

## Overview

Building a feature with an AI agent is straightforward for a single task. Building one that requires research, specification, parallel implementation across multiple worktrees, and multi-pass code review is not. Context grows unboundedly, state is lost between sessions, and there is no structure to enforce quality gates between phases.

Orchestrator solves this by acting as a thin coordination layer around Claude Code. It renders a structured prompt for each stage, dispatches a Claude Code subprocess, extracts a typed signal from the output, and uses that signal to drive the next stage. The orchestrator session itself never reads the content of any stage output file — only references (paths, hashes, status values) flow upstream. This keeps the orchestration session's context bounded regardless of how many stages run or how large their outputs are.

Each run produces a folder of stage prompts, raw outputs, and a `_state.yaml` that lets the pipeline be interrupted and resumed at any stage boundary.

## Design Philosophy

**Bounded context.** The main orchestration session operates on signal JSON only, never on stage output file contents. Downstream stages receive output references — file paths, commit hashes — and read them directly. This is a hard invariant: adding a file read to `orchestrate.py` breaks the token-minimisation contract and causes unbounded context growth across long pipelines.

**Signals as contracts.** Each stage emits exactly one `SIGNAL_JSON:` sentinel line carrying a structured dict with status, message, and typed output fields validated against a schema. The schema is the interface; any prompt implementation that satisfies it can be substituted. If a stage fails to emit the sentinel, the orchestrator sends one grace retry prompt before marking the stage as blocked.

**Stages as trusted workers.** Stage agents are dispatched with `--dangerously-skip-permissions --bare`: full built-in tool access, no MCP servers loaded, no hook execution. This is the intended use case — unattended, trusted pipeline execution on a developer workstation or controlled CI environment. The `--bare` flag reduces startup latency and eliminates hook side effects; `--dangerously-skip-permissions` removes permission gates so stages can read, write, and run commands without interruption.

**Human-in-the-loop by design.** The alignment stage is a declared pipeline pause point. When the pipeline reaches it, the orchestrator prints a `resume` command and exits. The developer runs alignment in a full interactive Claude Code session — with Forge MCP, full tool access, and human participation — then reinvokes the orchestrator to continue. This is not a workaround; it is the documented execution model for stages that require human judgment.

## Pipeline

The default `full` profile runs eight stages in sequence:

| Stage | Expansion | Description |
|-------|-----------|-------------|
| **discovery** | tracks | Planning agent determines research tracks; each track runs in parallel via `ThreadPoolExecutor` |
| **alignment** | — | Pipeline pause; developer collaborates in an interactive Claude Code session and produces `alignment-log.md` |
| **specification** | — | Produces a formal spec from discovery findings and alignment feedback |
| **decomposition** | — | Breaks the spec into named implementation slices |
| **implementation** | slices | Each slice runs in a dedicated git worktree in parallel; merged back on completion |
| **qa** | — | Quality assurance pass against the implemented branch, run from the repo root |
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
name: my-api
description: REST API for the platform
repo-root: /path/to/my-api     # path to the code repository
default-profile: full          # profile used when --profile is omitted
merge-target: main             # branch PRs merge into
agent-rules: CLAUDE.md         # agent rules file in the code repo

# Engineering standards injected into implementation and QA stage prompts.
# Each entry maps to a harsh-{name}-engineering-standards skill in .claude/skills/.
# The general standard is always included automatically.
standards:
  - php
  - mysql
```

The `standards` list is optional. If omitted, only the general engineering standard is injected when the active profile opts stages in via `standards: true`. Add any identifier that has a corresponding `harsh-{name}-engineering-standards` skill symlinked in the orchestrator's `.claude/skills/` directory.

## Profiles

Built-in profiles ship with the package:

| Name | Stages |
|------|--------|
| `full` *(default)* | discovery → alignment → specification → decomposition → implementation → QA → review → harvest |
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

## Commands

### `orchestrator run` — start a pipeline

```bash
orchestrator run \
  --docs-root <path>       # required: path to your docs root
  --project <name>         # required: project folder under docs-root/projects/
  --feature-path <path>    # required: docs-relative path to feature directory
  --branch <name>          # required: git branch to create for implementation
  --profile <name|path>    # optional: built-in name or .yaml file (default: full)
```

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

## Tests

```bash
uv run pytest tests/
```
