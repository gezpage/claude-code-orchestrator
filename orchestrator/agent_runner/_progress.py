"""Per-event progress emitted by streaming agent runners. See ADR-024.

A ``ProgressEvent`` is a small typed snapshot of one observable step in a
running agent — a tool call, a chunk of assistant prose, or a transient status
("session started", "session finished"). Runners produce them; ``run_stage``
forwards them to the orchestrator logger so long-running stages emit periodic
"the agent is still alive and doing X" lines into ``run.log`` instead of going
silent for minutes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ProgressCallback = Callable[["ProgressEvent"], None]

_TEXT_PREVIEW_CHARS = 160
_TOOL_INPUT_PREVIEW_CHARS = 120


@dataclass(frozen=True)
class ProgressEvent:
    """One observable step. ``kind`` discriminates which fields are populated."""

    kind: str  # "tool_use" | "assistant_text" | "session_start" | "session_end" | "error"
    summary: str  # human-readable one-liner suitable for run.log
    tool: str = ""  # tool name when kind == "tool_use"
    text: str = ""  # raw payload (tool input or assistant text) for callers that want detail


def _stringify_tool_input(value: Any) -> str:
    """Brief, log-friendly rendering of a tool's input dict."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        rendered = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(value)
    return rendered


def _truncate(value: str, limit: int) -> str:
    value = value.replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _summarise_tool_use(name: str, raw_input: Any) -> tuple[str, str]:
    """Return ``(summary_for_log, raw_input_string)`` for one tool_use block.

    The summary is deliberately terse: a `Bash(git status)` style fragment is
    more useful in run.log than a JSON dump. Callers that want detail get the
    raw input string in ``ProgressEvent.text``.
    """
    raw_str = _stringify_tool_input(raw_input)
    detail = ""
    if isinstance(raw_input, dict):
        for key in ("command", "file_path", "path", "pattern", "url", "description"):
            if key in raw_input and isinstance(raw_input[key], str):
                detail = raw_input[key]
                break
        else:
            detail = raw_str
    elif raw_str:
        detail = raw_str
    summary = f"tool {name}"
    if detail:
        summary = f"{summary} {_truncate(detail, _TOOL_INPUT_PREVIEW_CHARS)}"
    return summary, raw_str


def parse_claude_stream_line(line: str) -> list[ProgressEvent]:
    """Parse one JSONL line from ``claude --output-format stream-json`` into events.

    Returns an empty list for lines that are not valid JSON (banner output, blank
    lines, partial chunks) or for events we don't surface as progress. Robustness
    here matters: a malformed line must not break the run, it should just be
    skipped.
    """
    stripped = line.strip()
    if not stripped:
        return []
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    if not isinstance(event, dict):
        return []

    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "init":
        model = event.get("model") or event.get("session", {}).get("model", "")
        summary = "session start" if not model else f"session start ({model})"
        return [ProgressEvent(kind="session_start", summary=summary, text=model or "")]

    if etype == "assistant":
        message = event.get("message") or {}
        content = message.get("content") or []
        events: list[ProgressEvent] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    name = str(block.get("name", "") or "")
                    summary, raw = _summarise_tool_use(name, block.get("input"))
                    events.append(ProgressEvent(kind="tool_use", summary=summary, tool=name, text=raw))
                elif btype == "text":
                    text = str(block.get("text", "") or "").strip()
                    if text:
                        events.append(
                            ProgressEvent(
                                kind="assistant_text",
                                summary=_truncate(text, _TEXT_PREVIEW_CHARS),
                                text=text,
                            )
                        )
        return events

    if etype == "result":
        subtype = event.get("subtype", "")
        is_error = bool(event.get("is_error"))
        if is_error or subtype not in ("", "success"):
            return [
                ProgressEvent(
                    kind="error",
                    summary=f"session ended with {subtype or 'error'}",
                    text=str(event.get("result", "") or ""),
                )
            ]
        return [ProgressEvent(kind="session_end", summary="session end")]

    return []


def extract_claude_final_text(line: str) -> str | None:
    """If ``line`` is a stream-json ``result`` event, return its final ``result`` text.

    Used by streaming runners to recover a clean stdout (the agent's final
    message, containing SIGNAL_JSON) when output-format is stream-json.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("type") != "result":
        return None
    text = event.get("result")
    return text if isinstance(text, str) else None
