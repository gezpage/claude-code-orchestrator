---
status: accepted
date: 2026-05-16
affects: [orchestrator/agent_runner/_claude.py, orchestrator/agent_runner/_select.py, orchestrator/run_stage.py]
---

# ADR-025: Remove `--dangerously-skip-permissions` from Stage Dispatch

**Status:** Accepted
**Date:** 2026-05-16
**Supersedes:** [ADR-003](ADR-003-dangerously-skip-permissions.md)

## Context

ADR-003 mandated `--dangerously-skip-permissions` on every Claude stage invocation. At the time, that was the only documented way to keep an unattended pipeline from hanging on Claude Code's permission prompts. Since then:

- [ADR-018](ADR-018-agent-runner-abstraction.md) introduced the `AgentRunner` seam, moving the dispatch contract out of `run_stage()` and into per-backend runner classes.
- [ADR-022](ADR-022-claude-runners-oauth-only.md) introduced `ClaudeCodeAutoRunner`, which dispatches with `--permission-mode auto` instead. `auto` is the next-most-permissive mode short of `bypassPermissions`: most tool uses are approved without prompting while Claude's permission system remains engaged, OAuth/keychain auth keeps working, and operators retain a meaningful audit trail of what the agent was allowed to do.
- The Auto runner has been validated in real pipeline runs and shows no regression in unattended throughput versus the Print runner.

With both runners shipping side-by-side, the only difference is the permission flag. Carrying two near-identical runners (`claude_code_print` + `claude_code_auto`) — and the historical justification for the more permissive of the two — is dead weight.

## Decision

- Remove `ClaudeCodePrintRunner` and the `claude_code_print` backend ID.
- Rename `ClaudeCodeAutoRunner` → `ClaudeCodeRunner` and `claude_code_auto` → `claude_code`. There is no longer a Print/Auto distinction to disambiguate against.
- `--dangerously-skip-permissions` is no longer used by any orchestrator code path.
- `claude_code` is the new default backend for `AgentConfig.backend`.

## Consequences

- Stage dispatch runs under `--permission-mode auto`. Operators retain Claude Code's per-tool permission gating; on the rare tool use that `auto` does not approve, the agent will fail rather than skip the gate silently — running pipelines should be re-verified against this.
- The Codex backend with `--sandbox danger-full-access` remains the escape hatch for environments that genuinely need a fully permissive dispatch.
- ADR-003's reasoning is preserved as a historical record but no longer reflects the current invariant. The `--bare` (ADR-012) and `--dangerously-skip-permissions` (ADR-003) flags are now both fully removed from the codebase; only the `AgentRunner`-seam and OAuth/keychain decisions (ADR-018, ADR-022, ADR-023) remain load-bearing on the Claude runner.
- Profile YAMLs that previously named `claude_code_print` or `claude_code_auto` are migrated to `claude_code`. Operator-authored profiles must be updated to match — unknown backend names raise loudly via `_select.build_runner`.
- The CLAUDE.md invariant set is collapsed: there is one Claude runner with one permission flag, and it can be described in a single bullet.
