---
status: accepted
date: 2026-05-07
affects: [run_stage.py, orchestrate.py]
---

# ADR-007: Alignment Stage as Pipeline Pause Point

**Status:** Accepted
**Date:** 2026-05-07

> **Note.** The "`claude -p` with `--dangerously-skip-permissions`" dispatch
> shape referenced in the Context section is historical. The current
> dispatch path is `claude <prompt> --permission-mode auto` via the
> `ClaudeCodeRunner` agent-runner seam — see
> [ADR-018](ADR-018-agent-runner-abstraction.md),
> [ADR-022](ADR-022-claude-runners-oauth-only.md) and
> [ADR-025](ADR-025-remove-dangerously-skip-permissions.md). The decision
> below (alignment as a declared pipeline pause point that bypasses the
> uniform dispatch path) is unaffected.

## Context

The core architectural principle of the rebuild is that every stage runs through `run_stage.py` — a uniform dispatch path via `claude -p` with `--dangerously-skip-permissions`. This enables independent invocation, consistent signal handling, and testability.

The Alignment stage cannot be fully served by this model. Alignment requires the full Claude Code REPL (interactive session with complete tool access and any configured MCP tools available), human participation to resolve decisions, and the ability to write to the alignment log interactively. `claude -p` runs non-interactively and lacks MCP tool access.

Two options were evaluated:
- **Option B (pipeline pause):** Python detects the alignment stage and pauses, surfacing instructions for the developer to run the stage manually in a REPL session. On resume, Python checks for `alignment-log.md` and skips to Specification.
- **Option C (fully autonomous):** Alignment runs non-interactively via `claude -p`, with a structured brief. Available as a profile mode (`alignment.mode: autonomous`) for tightly-specified features.

## Decision

Alignment is a declared pipeline pause point, not a `run_stage.py` dispatch. When Python encounters the alignment stage in the profile, it pauses and surfaces:

> Alignment required — open Claude Code and run `/orchestrator --stage alignment --run-folder {path}`. Re-invoke when complete.

The alignment stage runs in a full Claude Code REPL session, writes to `alignment-log.md`, and the developer re-invokes the orchestrator. Python on resume sees `alignment-log.md` exists and skips to Specification.

Option C (fully autonomous) remains available as a profile mode for features where alignment can be done non-interactively.

## Consequences

- Alignment quality is maximised: full tool access, human in the loop.
- The uniform dispatch model has a declared exception: alignment is explicitly excluded from `run_stage.py`.
- The pipeline requires a manual re-invocation step after alignment — it does not run end-to-end unattended when alignment is in the profile.
- The pause mechanism must be maintained separately from the standard dispatch path.
- Autonomous mode is available as a profile option when human alignment is not required.
