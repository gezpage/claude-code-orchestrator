"""Unit tests for the stream-json progress parser (ADR-024)."""

from __future__ import annotations

from orchestrator.agent_runner._progress import (
    ProgressEvent,
    extract_claude_final_text,
    parse_claude_stream_line,
)


def test_parse_init_event_returns_session_start():
    events = parse_claude_stream_line('{"type":"system","subtype":"init","model":"claude-opus-4-7"}')
    assert len(events) == 1
    assert events[0].kind == "session_start"
    assert "claude-opus-4-7" in events[0].summary


def test_parse_tool_use_picks_friendly_field():
    line = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Edit","input":{"file_path":"src/foo.py","new_string":"x"}}]}}'
    )
    events = parse_claude_stream_line(line)
    assert [e.kind for e in events] == ["tool_use"]
    assert events[0].tool == "Edit"
    # Friendly field (file_path) should be used in the summary, not the whole JSON.
    assert "src/foo.py" in events[0].summary
    assert "new_string" not in events[0].summary


def test_parse_tool_use_falls_back_to_json_when_no_friendly_field():
    line = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Custom","input":{"foo":"bar"}}]}}'
    events = parse_claude_stream_line(line)
    assert events[0].tool == "Custom"
    assert "bar" in events[0].summary


def test_parse_assistant_text_truncated():
    big = "x" * 500
    line = '{"type":"assistant","message":{"content":[{"type":"text","text":"' + big + '"}]}}'
    events = parse_claude_stream_line(line)
    assert len(events) == 1
    assert events[0].kind == "assistant_text"
    # Truncated for the human summary, full text preserved on the event.
    assert len(events[0].summary) < len(big)
    assert events[0].text == big


def test_parse_assistant_with_multiple_blocks_emits_event_per_block():
    line = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Bash","input":{"command":"ls"}},'
        '{"type":"text","text":"listing"}]}}'
    )
    events = parse_claude_stream_line(line)
    assert [e.kind for e in events] == ["tool_use", "assistant_text"]


def test_parse_result_success_returns_session_end():
    line = '{"type":"result","subtype":"success","is_error":false,"result":"done"}'
    events = parse_claude_stream_line(line)
    assert len(events) == 1
    assert events[0].kind == "session_end"


def test_parse_result_error_returns_error_event():
    line = '{"type":"result","subtype":"error_max_turns","is_error":true,"result":"too many turns"}'
    events = parse_claude_stream_line(line)
    assert len(events) == 1
    assert events[0].kind == "error"
    assert "too many turns" in events[0].text


def test_parse_invalid_json_returns_empty_list():
    assert parse_claude_stream_line("not json at all") == []
    assert parse_claude_stream_line("") == []
    assert parse_claude_stream_line("   \n") == []


def test_parse_unknown_event_type_is_skipped():
    # Forward compatibility: unknown event types must not surface as progress.
    assert parse_claude_stream_line('{"type":"future_event","detail":42}') == []


def test_extract_final_text_from_result_event():
    line = '{"type":"result","subtype":"success","result":"final reply"}'
    assert extract_claude_final_text(line) == "final reply"


def test_extract_final_text_returns_none_for_non_result_events():
    assert extract_claude_final_text('{"type":"assistant","message":{"content":[]}}') is None
    assert extract_claude_final_text("not json") is None


def test_progress_event_is_frozen():
    import dataclasses

    event = ProgressEvent(kind="tool_use", summary="hi")
    try:
        event.kind = "other"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ProgressEvent should be frozen")
