# Orchestrator

Pipeline sequencer for feature development. Sequences discovery → alignment →
specification → decomposition → implementation → QA → review → harvest, coordinating
Claude Code agents at each stage via the `orchestrator` CLI.

## Install

```bash
pipx install -e ~/Dev/tools/orchestrator
```

## Usage

```bash
# Run the full pipeline for a feature
orchestrator run \
  --docs-root /path/to/team-hub \
  --project my-project \
  --feature-path projects/my-project/feature-requests/my-feature.md \
  --branch feat/my-feature

# Resume after the alignment pause
orchestrator resume \
  --run-folder /path/to/team-hub/projects/my-project/workflow/runs/my-feature/2026-01-01-run-1 \
  --docs-root /path/to/team-hub

# Run a single stage directly
orchestrator stage \
  --stage discovery \
  --input input.json \
  --run-folder /path/to/run-folder \
  --docs-root /path/to/team-hub \
  --project my-project \
  --project-log-path /path/to/team-hub/projects/my-project
```

## Claude Code skill setup

To make `/orchestrator` available in Claude Code sessions, create a symlink
from the global skills directory to the `claude-skill/` folder in this repo:

```bash
ln -s ~/Dev/tools/orchestrator/claude-skill ~/.claude/skills/orchestrator
```

The symlink means any edit to `claude-skill/SKILL.md` is immediately live —
no copy step needed.

## Tests

```bash
uv run pytest tests/
```
