# Orchestrator

Pipeline sequencer for feature development. Runs Claude Code agents through a fixed sequence of stages — discovery → alignment → specification → decomposition → implementation → QA → review → harvest — coordinated by the `orchestrator` CLI.

## Install

```bash
pipx install -e ~/Dev/tools/orchestrator
```

## Concepts

| Term | Meaning |
|------|---------|
| **docs-root** | Root of your team-hub docs repo (e.g. `/path/to/team-hub`). All project config and run output lives here. |
| **project** | Folder name under `{docs-root}/projects/`. Contains `project.yaml` with `repo-root` and optional settings. |
| **feature-path** | Docs-relative path to the feature directory. Must contain `overview.md`. The orchestrator reads this to understand what to build. |
| **branch** | Git branch created in the code repo when the implementation stage starts. |
| **profile** | Defines which stages to run and in what order. Either a built-in name or a path to a YAML file. |
| **run folder** | Created automatically under `{docs-root}/projects/{project}/workflow/runs/{feature-slug}/{date}-run-{N}/`. Holds all stage output and pipeline state. |

## Profiles

Built-in profiles ship with the package and can be referenced by name:

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
  - stage: specification
    prompt: prompts/specification/default.md
  - stage: decomposition
    prompt: prompts/decomposition/default.md
  - stage: implementation
    prompt: prompts/implementation/default.md
  - stage: qa
    prompt: prompts/qa/default.md
  - stage: review
    prompts:
      architecture: prompts/review/architecture.md
      implementation: prompts/review/implementation.md
      tests: prompts/review/tests.md
  - stage: harvest
    prompt: prompts/harvest/default.md
```

Interactive stages also require `mode: interactive` and `artifact`:

```yaml
  - stage: alignment
    mode: interactive
    artifact: alignment-log.md
    prompt: prompts/alignment/interactive.md
```

## Project configuration

Each project requires a `project.yaml` file at `{docs-root}/projects/{project}/project.yaml`.

```yaml
name: my-api
description: REST API for the platform
repo-root: ~/Dev/my-api        # path to the code repository
default-profile: full          # profile used when --profile is omitted
merge-target: main             # branch PRs merge into
agent-rules: CLAUDE.md         # agent rules file in the code repo

# Engineering standards injected into implementation and QA prompts.
# Each entry maps to a harsh-{name}-engineering-standards skill in .claude/skills/.
# The general standard is always included automatically.
standards:
  - php
  - mysql
```

The `standards` list is optional. If omitted, only the general engineering standard is injected (when the active profile opts stages in via `standards: true`). Add any identifier that has a corresponding `harsh-{name}-engineering-standards` skill symlinked in the orchestrator's `.claude/skills/` directory.

## Commands

### `orchestrator run` — start a pipeline

```bash
orchestrator run \
  --docs-root <path>       # required: path to team-hub docs root
  --project <name>         # required: project folder under docs-root/projects/
  --feature-path <path>    # required: docs-relative path to feature directory
  --branch <name>          # required: git branch to create for implementation
  --profile <name|path>    # optional: built-in name or .yaml file (default: full)
```

**Examples:**

```bash
# Full pipeline with default profile
orchestrator run \
  --docs-root ~/Dev/docs/team-hub \
  --project my-api \
  --feature-path projects/my-api/features/auth-refresh \
  --branch feat/auth-refresh

# Research only (no implementation)
orchestrator run \
  --docs-root ~/Dev/docs/team-hub \
  --project my-api \
  --feature-path projects/my-api/features/auth-refresh \
  --branch feat/auth-refresh \
  --profile spike

# Custom profile from a file
orchestrator run \
  --docs-root ~/Dev/docs/team-hub \
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
  --docs-root <path>       # required: path to team-hub docs root
```

The run folder path is printed by the orchestrator when the pipeline pauses. Project, feature, branch, and profile are read from the run folder's saved state.

**Example:**

```bash
orchestrator resume \
  --run-folder ~/Dev/docs/team-hub/projects/my-api/workflow/runs/auth-refresh/2026-05-10-run-1 \
  --docs-root ~/Dev/docs/team-hub
```

---

### `orchestrator stage` — run one stage directly

Runs a single stage in isolation. Intended for debugging and development.

```bash
orchestrator stage \
  --stage <name>               # required: stage name (e.g. discovery, specification)
  --input <path>               # required: path to JSON file containing input variables
  --run-folder <path>          # required: path to the run folder
  --docs-root <path>           # required: path to team-hub docs root
  --project <name>             # required: project name
  --project-log-path <path>    # required: path for project-level orchestrator.log
  --implementation <name>      # optional: prompt implementation name (default: default)
```

**Example:**

```bash
orchestrator stage \
  --stage specification \
  --input /tmp/spec-input.json \
  --run-folder ~/Dev/docs/team-hub/projects/my-api/workflow/runs/auth-refresh/2026-05-10-run-1 \
  --docs-root ~/Dev/docs/team-hub \
  --project my-api \
  --project-log-path ~/Dev/docs/team-hub/projects/my-api
```

Exits `0` on success, `1` on failure. Prints the stage signal JSON to stdout.

## Claude Code skill setup

To make `/orchestrator` available in Claude Code sessions, symlink the skill directory:

```bash
ln -s ~/Dev/tools/orchestrator/claude-skill ~/.claude/skills/orchestrator
```

Edits to `claude-skill/SKILL.md` are immediately live — no copy step needed.

## Tests

```bash
uv run pytest tests/
```
