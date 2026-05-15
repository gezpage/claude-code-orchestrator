---
status: accepted
date: 2026-05-15
affects: [agent_runner/_claude.py, agent_runner/_claude_auto.py]
supersedes: [ADR-012]
amends: [ADR-018]
---

# ADR-022: Claude Runners Use OAuth/Keychain Auth Only

**Status:** Accepted
**Date:** 2026-05-15

## Context

ADR-012 and ADR-018 made `--bare` mandatory on every `ClaudeCodePrintRunner`
invocation. `--bare` is documented (in `claude --help`) as forcing
Anthropic auth to be "strictly `ANTHROPIC_API_KEY` or `apiKeyHelper` via
`--settings` (OAuth and keychain are never read)". That was tolerable when
every developer running the orchestrator had a valid `ANTHROPIC_API_KEY`
exported in their shell.

That assumption no longer holds. Most contributors authenticate to Claude Code
via the interactive OAuth login that writes to the system keychain — they
never set `ANTHROPIC_API_KEY` at all, or they have a stale value from an old
trial. Running the pipeline produces:

    Invalid API key · Fix external API key

…which is exactly what Claude Code emits when `--bare` (or any other path
that prefers the env-var key) finds an invalid `ANTHROPIC_API_KEY`. The
hook installed by ADR-012 was supposed to remove startup overhead; instead
it now removes the only working auth path for most users.

`ClaudeCodeAutoRunner` (ADR-018, `_claude_auto.py`) was introduced earlier as a
transitional escape hatch for OAuth-only users. With this ADR the escape
hatch becomes the default: both runners drop `--bare`.

`-p` is removed alongside `--bare` for a separate reason. Subprocess-piped
stdout already triggers Claude Code's non-interactive mode (see
`claude --help`: "non-interactive mode (via `-p`, or when stdout is not a TTY,
e.g. piped or redirected output)"). The explicit flag is redundant once the
runner uses `subprocess.Popen` with `stdout=subprocess.PIPE`. Keeping it adds
no behaviour and shifts a load-bearing detail to a flag whose semantics could
drift in future CLI versions.

## Decision

Both `ClaudeCodePrintRunner` and `ClaudeCodeAutoRunner`:

1. **Drop `--bare`.** Hooks, LSP, plugin sync, keychain reads, and
   `CLAUDE.md` auto-discovery are all active during stage runs. Stage
   isolation now relies on `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` (sterile
   context) only — the same default the runners already set.
2. **Drop `-p`.** Non-interactive behaviour is established by piping stdout,
   not by a flag.
3. **Strip `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the
   forwarded subprocess env.** A stale or invalid external key in the
   caller's shell cannot override the user's keychain/OAuth auth.

`--dangerously-skip-permissions` (ADR-003) is unchanged for
`ClaudeCodePrintRunner`. `--permission-mode auto` (ADR-018) is unchanged for
`ClaudeCodeAutoRunner`. Those are the only flags that differ between the
two runners now.

## Alternatives Considered

- **Keep `--bare`, require an `ANTHROPIC_API_KEY`.** Rejected: nearly every
  user we observed hitting this bug had only OAuth/keychain auth. The
  orchestrator is meant to be runnable end-to-end with whatever auth Claude
  Code is already configured with.
- **Keep `-p`, drop only `--bare`.** Rejected on minimality grounds: with
  piped stdout the flag has no effect, so leaving it in the command is
  cargo. It also misleads future readers into thinking it's load-bearing.
- **Keep `--bare` only for stages that don't need keychain auth.** Rejected:
  there is no clean per-stage signal for "this stage doesn't need auth", and
  branching would re-introduce the kind of call-site coupling ADR-018 set
  out to remove.
- **Set `ANTHROPIC_API_KEY=""` (empty string) instead of `pop`.** Rejected:
  Claude Code treats an empty value as "set but invalid" in some paths;
  removing the key entirely is the documented "use keychain" signal.

## Consequences

- ADR-012's `--bare` invariant is **superseded** for both Claude runners.
  CLAUDE.md no longer asserts `--bare` is mandatory.
- ADR-018's "Always passes `--bare` and `--dangerously-skip-permissions`"
  invariant on `ClaudeCodePrintRunner` is **amended**: only
  `--dangerously-skip-permissions` (ADR-003) remains.
- The two Claude runners are now nearly identical in command shape — only
  the permission flag differs. They are kept as separate classes for now
  to preserve the `backend_name` registration and per-runner config surface,
  but a future ADR may collapse them once `_claude_auto.py` has no other
  divergence.
- Hooks and MCP servers configured for the user's interactive Claude Code
  will fire during stage runs. Users who relied on `--bare` to silence
  noisy hooks during pipelines must either (a) disable those hooks in
  their global settings, or (b) switch to `codex_cli`. The threat-model
  document is updated to reflect that hooks/MCP are no longer suppressed
  by `--bare` at stage time.
- Setting `ANTHROPIC_API_KEY` in the shell will no longer affect Claude
  runner subprocesses launched by the orchestrator. Users who genuinely
  want API-key auth must use a different runner (e.g. a future
  `claude_api` backend) — the existing Claude CLI runners are now
  OAuth-only.
