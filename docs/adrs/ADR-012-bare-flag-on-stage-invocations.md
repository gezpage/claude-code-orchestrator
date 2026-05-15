---
status: superseded
superseded_by: ADR-022
date: 2026-05-09
affects: [run_stage.py]
---

# ADR-012: `--bare` Flag on All Stage Subprocess Invocations

**Status:** Superseded by [ADR-022](ADR-022-claude-runners-oauth-only.md) (originally moved into `ClaudeCodePrintRunner` by [ADR-018](ADR-018-agent-runner-abstraction.md))
**Date:** 2026-05-09

> **Note.** ADR-018 moved this invariant from every `run_stage()` call site
> into `ClaudeCodePrintRunner`. ADR-022 then removed the flag entirely:
> `--bare` forces `ANTHROPIC_API_KEY`-only auth, which is incompatible with
> the OAuth/keychain logins most contributors now use. The reasoning below
> is preserved for historical context but no longer reflects current code.

## Context

Stage agents are spawned via `subprocess.run()` in `run_stage.py`. Without additional flags, Claude Code loads all configured MCP servers and runs session hooks at startup. In a pipeline context this adds latency on every stage invocation and may execute hooks that are intended only for interactive sessions. Stage agents have no need for MCP tools — their interface is through files and the SIGNAL_JSON sentinel.

`--dangerously-skip-permissions` (ADR-003) already handles permission bypass for unattended execution. A separate mechanism is needed to suppress startup overhead and hook side effects.

## Decision

All `run_stage.py` subprocess invocations pass `--bare` to the Claude Code CLI. `--bare` skips MCP server loading and hook execution at startup. This flag is mandatory alongside `--dangerously-skip-permissions` and is documented as a package invariant in `CLAUDE.md`.

## Consequences

- Stage startup is faster — no MCP negotiation round-trip per invocation.
- No hook side effects fire during pipeline execution.
- Stage agents have no access to MCP tools. Stages must rely on Claude Code's built-in tools (Read, Edit, Write, Bash, etc.) only. Any stage that needs external service access cannot use MCP for it.
- `--bare` must be present in every `run_stage.py` subprocess call. Removing it would silently re-enable MCP and hooks without an obvious failure signal.
