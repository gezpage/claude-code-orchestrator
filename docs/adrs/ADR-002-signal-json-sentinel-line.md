---
status: accepted
date: 2026-05-07
affects: [signal.py, run_stage.py]
---

# ADR-002: SIGNAL_JSON Sentinel Line as Stage Output Contract

**Status:** Accepted
**Date:** 2026-05-07

## Context

The existing pipeline used regex pattern matching on free-text stage output (`SIGNAL_PATTERNS`) to detect stage completion. This caused a silent-block bug when stage names changed: the patterns became stale and failed silently. The regex approach also makes the contract implicit — nothing in the stage file formally specifies what the output must look like.

Two alternatives were evaluated:
- **API-level structured outputs (`output_config.format` with constrained decoding):** Available via the Anthropic SDK and `ant messages create`, but not via `claude -p` (the Claude Code CLI). Stage agents require Claude Code's built-in tools (Read, Write, Edit, Bash, MCP); switching to the SDK to gain structured outputs would mean losing the entire tool layer. Note: structured outputs do not strip reasoning — an earlier draft of this ADR incorrectly stated that they do.
- **Sentinel line prefix:** A line beginning with `SIGNAL_JSON:` followed by a JSON object. Unambiguous, grep-friendly, immune to incidental JSON produced by tool-use output during stage execution.

## Decision

Every stage emits exactly one line of the form:

```
SIGNAL_JSON: {"stage":"<name>","status":"<passed|blocked|failed>","message":"<human-readable>", ...}
```

This sentinel line is the stage's output contract. Python extracts it by scanning stdout for lines beginning with `SIGNAL_JSON:`. Required fields for every stage signal: `stage` (string), `status` (one of `passed` / `blocked` / `failed`), `message` (required when status is `blocked` or `failed`). Additional fields are permitted and are stage-specific. The contract is enforced by prompt rules in each stage file.

If no valid sentinel line is found, Python issues one grace retry before treating the stage as `blocked`.

## Consequences

- Signal extraction is reliable and does not require changes to Python when stage names or output prose changes.
- Every stage file must be updated to include the sentinel-line output rule — a coordinated, hard-to-partially-revert change across all stage files.
- Stage files are coupled to a specific output convention; any stage that fails to emit the sentinel line triggers the grace retry path.
- The sentinel line is a code-level contract visible in stage output, making it easy to audit during debugging.
