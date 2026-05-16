---
status: accepted
date: 2026-05-16
affects: [agent_runner/_claude.py]
amends: [ADR-018, ADR-022]
amended_by: [ADR-025]
---

# ADR-023: Sterile Context Also Suppresses MCP Servers

**Status:** Accepted (amended by [ADR-025](ADR-025-remove-dangerously-skip-permissions.md))
**Date:** 2026-05-16

> **Note.** This ADR refers to `ClaudeCodePrintRunner` and
> `ClaudeCodeAutoRunner` as a pair. [ADR-025](ADR-025-remove-dangerously-skip-permissions.md)
> has since collapsed them into a single `ClaudeCodeRunner` (backend
> `claude_code`); the MCP-suppression behaviour described below applies to
> that one runner unchanged.

## Context

ADR-022 dropped `--bare` from both Claude runners so that OAuth/keychain auth
keeps working. A documented consequence was that "hooks and MCP servers
configured for the user's interactive Claude Code will fire during stage
runs." In practice that meant a stage agent inherited every MCP server the
user had configured globally in `~/.claude.json` or in any `.mcp.json` along
the cwd → root walk — Jira, Forge, ide-diagnostics, and others. None of those
servers were part of the pipeline contract, but their tools became visible to
the stage agent and ate context tokens on every turn.

ADR-018's `sterile_context` flag already covered the analogous problem for
auto-memory (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`). MCPs are the same shape of
problem — ambient, user-local state leaking into pipeline runs — but were
left unaddressed.

Claude Code exposes two flags that together close this off:

- `--strict-mcp-config` — "Only use MCP servers from `--mcp-config`, ignoring
  all other MCP configurations."
- `--mcp-config <configs...>` — accepts JSON files or inline JSON strings.

Passing both with an empty `{"mcpServers":{}}` deterministically disables
every MCP source.

## Decision

Both `ClaudeCodePrintRunner` and `ClaudeCodeAutoRunner` append
`--strict-mcp-config --mcp-config '{"mcpServers":{}}'` to the `claude`
invocation when `sterile_context=True` (the default). Opting out via
`agent.sterile_context: false` restores the user's configured MCP servers,
symmetric with how auto-memory suppression already behaves.

The decision deliberately reuses the existing `sterile_context` flag rather
than introducing a separate `disable_mcps` knob. "Sterile context" is the
single concept for "no ambient user-local state in pipeline runs"; auto-memory
and MCPs are both instances of that, and splitting them would invite
inconsistent combinations (auto-memory off, MCPs on) that have no real use
case.

CLAUDE.md no longer asserts the user's MCP servers fire during stage runs;
the updated runner invariant now references this ADR.

## Alternatives Considered

- **A separate `disable_mcps` config key.** Rejected: same trade-off as
  auto-memory; the only realistic configurations are "sterile" or "use my
  setup". A second knob adds combinatorial surface for no clear gain.
- **Pass an empty file path via `--mcp-config` instead of inline JSON.**
  Rejected: inline JSON is fully supported (`claude --help` documents
  `--mcp-config <configs...>` as accepting "JSON files or strings") and
  avoids a tempfile lifecycle that has to be cleaned up across timeouts and
  kills.
- **`--strict-mcp-config` alone (no `--mcp-config`).** Rejected: the flag's
  documented semantics are "only use servers from `--mcp-config`", but
  whether "no `--mcp-config`" cleanly means "no servers" is not guaranteed
  across CLI versions. Passing an explicit empty config removes the
  ambiguity.
- **Disable MCPs unconditionally regardless of `sterile_context`.** Rejected:
  `sterile_context=False` is the documented opt-out for ambient injection;
  MCPs should behave the same way for consistency.

## Consequences

- Stage agents no longer see any MCP server (Jira, Forge, IDE diagnostics,
  etc.) unless the profile sets `agent.sterile_context: false`. Pipelines
  that depended implicitly on a user's globally-configured MCP must either
  opt out of sterile context or invoke those tools via shell.
- ADR-022's "MCP servers configured for the user's interactive Claude Code
  will fire during stage runs" consequence is **superseded** — only true now
  if `sterile_context` is explicitly disabled.
- The `sterile_context` invariant in CLAUDE.md is updated to reflect that
  MCP suppression is also part of the default isolation profile.
- `codex_cli` remains the strongest isolation option (no Claude CLI surface
  at all), but `claude_code_print` / `claude_code_auto` are now meaningfully
  closer to reproducible — auto-memory and MCPs are both off by default;
  CLAUDE.md auto-discovery and hooks remain (no CLI surface exists to
  suppress them).
