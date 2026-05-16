---
status: accepted
date: 2026-05-16
affects: [agent_runner/, run_stage.py]
amended_by: [ADR-025]
---

# ADR-024: Streaming Progress Events from Agent Runners

**Status:** Accepted (amended by [ADR-025](ADR-025-remove-dangerously-skip-permissions.md))
**Date:** 2026-05-16

> **Note.** This ADR refers to `ClaudeCodePrintRunner` and
> `ClaudeCodeAutoRunner` as a pair. [ADR-025](ADR-025-remove-dangerously-skip-permissions.md)
> has since collapsed them into a single `ClaudeCodeRunner` (backend
> `claude_code`); the streaming progress behaviour described below applies
> to that one runner unchanged.

## Context

Stage agents can run for many minutes. While running, the orchestrator's only
observable signal is the subprocess's tee'd stdout — and Claude's text-mode
output is buffered, so most of the run looks frozen from outside. There is no
way to tell whether the agent is still doing useful work, blocked on a tool
call, or hung. Operators have to wait for the stage to time out (or finish)
before they can react.

`run.log` already captures stage-level milestones ("dispatching", "passed",
"blocked") via `OrchestratorLogger`. It needs intra-stage breadcrumbs too —
"tool Edit src/foo.py", "tool Bash pytest tests/" — so that watchers can see
forward progress instead of betting on faith.

Claude Code's CLI supports `--output-format stream-json --verbose`, which emits
one JSON event per line covering every tool call, assistant text chunk, and
session boundary. The information needed is already available; what's missing
is a seam to pipe it into the orchestrator's existing logging.

## Decision

Introduce a streaming-progress seam at the `AgentRunner` boundary:

- `orchestrator.agent_runner._progress` defines `ProgressEvent`
  (`kind`, `summary`, `tool`, `text`) and `parse_claude_stream_line()` / 
  `extract_claude_final_text()`. Parsing is defensive — any line that isn't
  valid JSON or isn't a recognised event type is silently dropped so noise
  on stdout can never break a run.
- `AgentRunRequest` gains an optional `progress_callback: Callable[[ProgressEvent], None]`.
  When set, runners that support it (currently both Claude runners) flip the
  underlying CLI into `--output-format stream-json --verbose` and forward each
  parsed event to the callback. When unset the runner stays in text mode and
  behaviour is unchanged.
- The Claude runners reconstruct `result.stdout` from the final `result` event's
  text (the agent's clean last message containing `SIGNAL_JSON:`). The raw
  JSONL stream is kept as a fallback for noisy failure modes (auth errors,
  banner-only output) so `*-output.md` remains diagnosable.
- `run_stage()` builds a callback that logs each event as one INFO line via
  `OrchestratorLogger` — appearing both in `run.log` and on the operator's
  terminal exactly like existing stage-level entries.

Callback exceptions are caught at the runner boundary so a logger glitch can't
abort a stage.

`CodexCliRunner` is not modified in this ADR; its event format is different
enough to warrant a separate adapter when needed.

## Alternatives Considered

- **Tail `*-output.md` from outside the process.** Rejected: text-mode output
  is heavily buffered and only flushed at end-of-turn boundaries, so the file
  is empty for most of a stage's runtime.
- **Polling a heartbeat file.** Rejected: doesn't carry any information about
  what the agent is *doing*, only that it's still alive.
- **Always switch to stream-json unconditionally.** Rejected for now: text mode
  is fine for tests and for callers (like the grace-retry path) that don't need
  progress. Making streaming opt-in via the request field keeps the existing
  test surface stable and makes the cost path explicit.
- **Surface activity in `plan.md` directly.** Deferred: the graph model and
  renderer already own the diagram's projection contract (ADR-016, ADR-020).
  Pushing per-event mutations into them requires throttling, lock-discipline
  changes, and renderer work that's worth a separate ADR. `run.log` is the
  immediate need.

## Consequences

- Operators can watch `tail -f run.log` (or read the printed INFO stream) and
  see live forward progress — tool calls, brief assistant prose — while a stage
  runs.
- `*-output.md` for stages that streamed contains the agent's reconstructed
  final message rather than every intermediate event. Failure modes that
  produced no final message fall back to the raw JSONL stream, so debug-blind
  failures from ADR-018's drop of `claude_code_print` stream logs do not
  recur for streaming Claude stages.
- `ClaudeCodePrintRunner` and `ClaudeCodeAutoRunner` share a single subprocess
  driver in `_claude.py`. Tests that patched `subprocess.Popen` on the auto
  runner module must patch the shared driver instead.
- Adding progress streaming to `CodexCliRunner` is an additive follow-up — the
  protocol surface and callback contract already exist; what's missing is a
  parser for Codex's event format.
- Surfacing activity into `plan.md` is now a known follow-up: the events
  already flow through `run_stage`, so a future ADR can hook the same callback
  into a throttled graph-node mutation without changing the runner contract.
