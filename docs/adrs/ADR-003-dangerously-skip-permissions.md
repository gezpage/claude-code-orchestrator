---
status: accepted
date: 2026-05-07
affects: [run_stage.py]
---

# ADR-003: `--dangerously-skip-permissions` in All Stage Dispatches

**Status:** Accepted
**Date:** 2026-05-07

## Context

Each pipeline stage is dispatched by calling `claude -p` via `subprocess.run()` in Python. Claude Code's permission system requires user approval for many tool calls. In an unattended pipeline, permission prompts block execution indefinitely.

Four options were evaluated:

1. **Multi-agent (Agent tool):** Subagent output returns to the main session context, violating the token-minimisation rule. Team-session clean-shutdown is also an observed UX problem.
2. **Direct Anthropic Python SDK:** Claude Code's built-in tools (Read, Write, Edit, Bash, MCP) do not exist in the raw API. Reimplementing the full tool layer is months of rework for no net gain.
3. **`--allowedTools` per stage:** Tool needs emerge from the work, not a spec; you end up allowing everything anyway, adding maintenance overhead for no safety gain.
4. **`--dangerously-skip-permissions`:** Skips the permission gate entirely. Documented use case: trusted, controlled orchestration pipelines.

## Decision

`--dangerously-skip-permissions` is added to the `subprocess.run()` call in `run_stage()` for every stage dispatch. No per-stage `--allowedTools` list is maintained.

## Consequences

- Pipeline stages run unattended without permission prompts.
- The permission gate is removed entirely from stage invocations — stages can execute any tool call without user approval.
- This is the documented, intended use case for the flag; it is not a workaround.
- The security posture depends on the orchestrator itself being invoked in a trusted context (developer workstation, controlled CI). The flag is not appropriate in untrusted or multi-tenant environments.
- The flag name is alarming without this context; this ADR exists to document why the choice is correct rather than reckless.
