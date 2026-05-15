---
status: accepted
date: 2026-05-14
affects: [run_stage.py, orchestrate.py, profile.py, state.py, agent_runner/]
---

# ADR-018: Agent Runner Abstraction

**Status:** Accepted
**Date:** 2026-05-14

## Context

Stage dispatch was hardcoded to a single CLI invocation: `claude -p <prompt> --bare
--dangerously-skip-permissions`. That coupling created four problems:

1. **Backend lock-in.** Adding Codex CLI, an API-backed runner, or a local model
   runner required edits to every call site in `run_stage.py`, `review_cycle.py`,
   and the dispatchers in `orchestrate.py`.
2. **Ambient context leak.** Claude Code injects auto-memory from
   `~/.claude/projects/.../memory/` into every print-mode run. The orchestrator
   had no mechanism to suppress it, so pipeline determinism depended on the
   user's local memory state.
3. **CLI drift.** Flag semantics evolved across Claude Code versions; the
   `--bare` / `--dangerously-skip-permissions` invariants were enforced by
   comments and lint, not by a typed surface.
4. **Per-stage backend choice.** Mixing implementations across stages
   (e.g. Claude for implementation, Codex for review) was infeasible.

Issue #75 framed the fix as a strategic seam, not a workaround for a single
flag.

## Decision

Introduce `orchestrator.agent_runner`, a small package exposing:

- `AgentRunner` — Protocol with one method, `run(request: AgentRunRequest) -> AgentRunResult`.
- `AgentRunRequest` / `AgentRunResult` — frozen dataclasses describing prompt,
  cwd, env, timeout, model, permission mode, output mode, stage name,
  transcript path / stdout, stderr, exit code, duration, timed-out flag.
- `ClaudeCodePrintRunner` — wraps the legacy command shape. **Always** passes
  `--bare` and `--dangerously-skip-permissions`. These were the ADR-003 and
  ADR-012 invariants; they are now invariants of this runner specifically.
  Sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` when `sterile_context=True`
  (the default).
- `CodexCliRunner` — wraps `codex exec <prompt>`. Defaults to
  `--sandbox workspace-write` (least-permissive sandbox that is still useful
  for stage work — sandboxed FS writes, no network egress, no host access).
  `permission_mode` accepts `read-only`, `workspace-write`,
  `danger-full-access`, and the explicit alias `full-auto` (which maps to
  `--full-auto`). `--full-auto` is never the default for a freshly added
  backend; opting into it is an explicit profile decision.
- `FakeRunner` — test double. Records requests, returns canned stdout.
- `AgentConfig` + `resolve_agent_config(profile_agent, stage_agent)` +
  `build_runner(config)` — config-driven backend selection. Stage-level
  config shallow-merges over profile-level config.

`run_stage()` takes an optional `runner: AgentRunner | None` parameter. The
default (when not injected) is `ClaudeCodePrintRunner(sterile_context=True)`.
`orchestrate.run_pipeline` pre-builds one runner per stage from merged
profile + stage `agent:` config and passes it through every dispatcher.
Stage signal extraction stays in `run_stage` — the runner returns raw stdout
and the orchestrator owns SIGNAL_JSON semantics.

State recording: `_state.yaml` gains an `agent:` section mapping stage name
to `{backend, model}` so run artifacts truthfully reflect what executed each
stage (issue #75 acceptance criterion: "backend used is recorded in run
artifacts").

Profile YAML grows two optional fields:

```yaml
agent:                    # profile-level default
  backend: claude_code_print
  model: opus
  sterile_context: true

stages:
  - stage: review
    agent:                # stage-level override (shallow merge)
      backend: codex_cli
      model: gpt-5.1-codex
```

Sterile context defaults to **true**. Existing pipelines lose ambient
auto-memory injection unless they opt out explicitly. This is a behavioural
change, accepted because pipeline determinism is more valuable than
convenience.

Interactive stages (`mode: interactive`) remain outside this seam.
`run_interactive_stage()` continues to launch `claude` directly with stdin
passthrough — its semantics (no `-p`, no `--bare`, no SIGNAL_JSON) are
incompatible with the request/result shape and would only obscure the
abstraction.

## Alternatives Considered

- **Refactor only.** Keep the single CLI but make flags configurable. Rejected:
  doesn't address backend lock-in or sterile context.
- **Per-call subprocess construction inline.** Rejected: gives up the seam
  before any second backend lands.
- **Stream protocol normalisation.** Rejected for first PR; the runner result
  exposes a stdout string and writes a transcript file, which is sufficient
  for SIGNAL_JSON extraction. Streaming/JSON-mode output can be a follow-up
  through `AgentRunRequest.output_mode`.
- **Interactive in the same protocol.** Rejected: `AgentRunner` would become
  a union of two unrelated shapes.

## Consequences

- ADR-003 (`--dangerously-skip-permissions`) and ADR-012 (`--bare`) are
  **superseded**. Their invariants move into `ClaudeCodePrintRunner` and
  are tested directly against the runner.
- The CLAUDE.md invariants for those flags now reference this ADR.
- Existing pipeline runs no longer inherit ambient Claude auto-memory by
  default — sterile context is the new default. Opt out via
  `agent.sterile_context: false`.
- Codex CLI is a supported backend; `codex` must be installed on PATH if
  selected. The runner does not pre-check availability — a missing `codex`
  binary surfaces as a subprocess error at dispatch time. (Pre-check could
  land in a follow-up.)
- Per-stage backend choice is wired through `orchestrate.run_pipeline`,
  including fix cycles. `fix-implementation` reuses the implementation
  stage runner, and review reruns reuse the review stage runner.
- Run artifacts (`_state.yaml`) now record `agent.{stage}.{backend, model}`
  so the run is reproducible without re-deriving config from the profile.
- The full agent surface is one small package (`orchestrator/agent_runner/`);
  adding a new backend means one new module + one branch in `_select.build_runner`.
