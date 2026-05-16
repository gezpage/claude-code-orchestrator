"""Tests for the agent runner abstraction introduced in ADR-018."""

from __future__ import annotations

import subprocess

import pytest

from orchestrator.agent_runner import (
    AgentConfig,
    AgentRunRequest,
    ClaudeCodeRunner,
    CodexCliRunner,
    FakeRunner,
    build_runner,
    resolve_agent_config,
)

# ── Command construction ──────────────────────────────────────────────────────


def _stub_popen(monkeypatch, target_module, stdout="", exit_code=0):
    """Patch the subprocess.Popen used by the runner under test.

    Both the Claude runner and codex have their own subprocess imports; the
    helper points monkeypatch at whichever module the caller passes in.
    """
    captured: dict = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            self.stdout = iter([stdout])

        def wait(self, timeout=None):
            return exit_code

        def kill(self):
            pass

    monkeypatch.setattr(target_module.subprocess, "Popen", _FakePopen)
    return captured


def test_claude_runner_command_construction(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod, stdout="hello")
    runner = ClaudeCodeRunner(sterile_context=True)
    runner.run(AgentRunRequest(prompt="do the thing", stage_name="discovery"))
    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "do the thing" in cmd
    # ADR-025: --permission-mode auto replaces --dangerously-skip-permissions.
    assert "--permission-mode" in cmd
    assert "auto" in cmd
    assert "--dangerously-skip-permissions" not in cmd
    # ADR-022: --bare and -p are intentionally absent (OAuth/keychain auth path).
    assert "--bare" not in cmd
    assert "-p" not in cmd
    # ADR-023: sterile_context also suppresses every MCP server.
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" in cmd
    assert '{"mcpServers":{}}' in cmd


def test_claude_runner_mcp_suppression_absent_when_sterile_disabled(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodeRunner(sterile_context=False)
    runner.run(AgentRunRequest(prompt="x"))
    cmd = captured["cmd"]
    # Opting out of sterile_context re-enables the user's configured MCP servers.
    assert "--strict-mcp-config" not in cmd
    assert "--mcp-config" not in cmd


def test_claude_runner_strips_anthropic_api_key_env(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stale-token")
    runner = ClaudeCodeRunner()
    runner.run(AgentRunRequest(prompt="x"))
    env = captured["kwargs"]["env"]
    # ADR-022: external-key env vars must be removed so keychain/OAuth is used.
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_claude_runner_sterile_env_set_when_enabled(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodeRunner(sterile_context=True)
    runner.run(AgentRunRequest(prompt="x"))
    assert captured["kwargs"]["env"].get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"


def test_claude_runner_sterile_env_absent_when_disabled(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodeRunner(sterile_context=False)
    # Ensure no inherited value would leak through.
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)
    runner.run(AgentRunRequest(prompt="x"))
    assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in captured["kwargs"]["env"]


def test_claude_runner_request_env_overrides_inherited(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodeRunner(sterile_context=False)
    runner.run(AgentRunRequest(prompt="x", env={"FOO": "bar"}))
    assert captured["kwargs"]["env"].get("FOO") == "bar"


def test_claude_runner_model_flag(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodeRunner()
    runner.run(AgentRunRequest(prompt="x", model="sonnet"))
    cmd = captured["cmd"]
    assert "--model" in cmd
    assert "sonnet" in cmd

    # Request-level model overrides the constructor default.
    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodeRunner(model="claude-opus-4-7")
    runner.run(AgentRunRequest(prompt="x", model="sonnet"))
    cmd = captured["cmd"]
    assert "--model" in cmd
    assert "sonnet" in cmd


def test_claude_runner_timeout_marks_result(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    class _SlowPopen:
        def __init__(self, cmd, **kwargs):
            self.stdout = iter(["partial"])
            self._killed = False

        def wait(self, timeout=None):
            if self._killed:
                return -9
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

        def kill(self):
            self._killed = True

    monkeypatch.setattr(claude_mod.subprocess, "Popen", _SlowPopen)
    runner = ClaudeCodeRunner()
    result = runner.run(AgentRunRequest(prompt="x", timeout_seconds=1))
    assert result.timed_out is True


def test_claude_runner_non_zero_exit_captured(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    _stub_popen(monkeypatch, claude_mod, exit_code=2)
    runner = ClaudeCodeRunner()
    result = runner.run(AgentRunRequest(prompt="x"))
    assert result.exit_code == 2
    assert result.timed_out is False


# ── Codex runner ──────────────────────────────────────────────────────────────


def test_codex_runner_command_construction(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = CodexCliRunner()
    runner.run(AgentRunRequest(prompt="do work"))
    cmd = captured["cmd"]
    assert cmd[0] == "codex"
    assert cmd[1] == "exec"
    assert "do work" in cmd
    # Default is --sandbox workspace-write; --full-auto is opt-in via permission_mode.
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd
    assert "--full-auto" not in cmd
    # Non-full-auto sandbox modes must pair with --ask-for-approval never, otherwise
    # codex exec hangs/rejects on the implicit approval gate (see CodexCliRunner docstring).
    assert "--ask-for-approval" in cmd
    assert "never" in cmd


def test_codex_runner_sandbox_mode_mapping(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = CodexCliRunner()
    runner.run(AgentRunRequest(prompt="x", permission_mode="read-only"))
    cmd = captured["cmd"]
    assert "--sandbox" in cmd
    assert "read-only" in cmd
    assert "--full-auto" not in cmd
    assert "--ask-for-approval" in cmd
    assert "never" in cmd


def test_codex_runner_full_auto_opt_in(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = CodexCliRunner()
    runner.run(AgentRunRequest(prompt="x", permission_mode="full-auto"))
    cmd = captured["cmd"]
    # Codex CLI replaced --full-auto with --dangerously-bypass-approvals-and-sandbox.
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--full-auto" not in cmd
    assert "--sandbox" not in cmd
    # --dangerously-bypass-approvals-and-sandbox already implies "no approval";
    # don't add a redundant --ask-for-approval.
    assert "--ask-for-approval" not in cmd


def test_codex_runner_danger_full_access_uses_sandbox_flag(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = CodexCliRunner()
    runner.run(AgentRunRequest(prompt="x", permission_mode="danger-full-access"))
    cmd = captured["cmd"]
    assert "--sandbox" in cmd
    assert "danger-full-access" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert "--ask-for-approval" in cmd
    assert "never" in cmd


def test_codex_runner_model_flag(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = CodexCliRunner()
    runner.run(AgentRunRequest(prompt="x", model="gpt-5.1-codex"))
    cmd = captured["cmd"]
    assert "-m" in cmd
    assert "gpt-5.1-codex" in cmd


def test_codex_runner_uses_config_defaults(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = build_runner(AgentConfig(backend="codex_cli", model="gpt-5-codex", permission_mode="read-only"))
    runner.run(AgentRunRequest(prompt="x"))
    cmd = captured["cmd"]
    assert "-m" in cmd
    assert "gpt-5-codex" in cmd
    assert "--sandbox" in cmd
    assert "read-only" in cmd


def test_codex_runner_workspace_roots_and_last_message(monkeypatch, tmp_path):
    from orchestrator.agent_runner import _codex as codex_mod

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            output_path = cmd[cmd.index("--output-last-message") + 1]
            from pathlib import Path

            Path(output_path).write_text('SIGNAL_JSON: {"stage":"x","status":"passed"}')
            self.stdout = iter(["full transcript\n"])

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    captured: dict = {}
    monkeypatch.setattr(codex_mod.subprocess, "Popen", _FakePopen)
    runner = CodexCliRunner()
    result = runner.run(
        AgentRunRequest(
            prompt="x",
            workspace_root=str(tmp_path / "repo"),
            writable_roots=(str(tmp_path / "repo"), str(tmp_path / "docs")),
        )
    )
    cmd = captured["cmd"]
    assert "--cd" in cmd
    assert str(tmp_path / "repo") in cmd
    assert "--add-dir" in cmd
    assert str(tmp_path / "docs") in cmd
    assert result.stdout == 'SIGNAL_JSON: {"stage":"x","status":"passed"}'


def test_codex_runner_result_stdout_uses_clean_last_message(monkeypatch, tmp_path):
    from orchestrator.agent_runner import _codex as codex_mod

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            output_path = cmd[cmd.index("--output-last-message") + 1]
            from pathlib import Path

            Path(output_path).write_text("clean final message\nSIGNAL_JSON: {}")
            self.stdout = iter(["codex banner\n", "workdir: /tmp\n", "model: gpt-5\n", "(noisy stream)\n"])

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(codex_mod.subprocess, "Popen", _FakePopen)
    runner = CodexCliRunner()
    result = runner.run(AgentRunRequest(prompt="x"))
    # result.stdout uses the clean final message — signal parsing depends on this
    # being terse, even when the raw stream is noisy.
    assert result.stdout == "clean final message\nSIGNAL_JSON: {}"


def test_codex_runner_result_stdout_falls_back_to_stream_when_no_last_message(monkeypatch, tmp_path):
    from orchestrator.agent_runner import _codex as codex_mod

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            self.stdout = iter(["partial stream output\n"])

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(codex_mod.subprocess, "Popen", _FakePopen)
    runner = CodexCliRunner()
    result = runner.run(AgentRunRequest(prompt="x"))
    assert result.stdout == "partial stream output\n"


# ── Selection / config merge ──────────────────────────────────────────────────


def test_resolve_agent_config_defaults():
    cfg = resolve_agent_config(None, None)
    assert cfg.backend == "claude_code"
    assert cfg.sterile_context is True
    assert cfg.model is None


def test_resolve_agent_config_profile_only():
    cfg = resolve_agent_config({"model": "opus", "sterile_context": False}, None)
    assert cfg.model == "opus"
    assert cfg.sterile_context is False
    assert cfg.backend == "claude_code"


def test_resolve_agent_config_stage_overrides_profile():
    cfg = resolve_agent_config(
        {"backend": "claude_code", "model": "opus"},
        {"backend": "codex_cli", "model": "gpt-5.1-codex"},
    )
    assert cfg.backend == "codex_cli"
    assert cfg.model == "gpt-5.1-codex"


def test_resolve_agent_config_unknown_key_raises():
    with pytest.raises(ValueError, match="Unknown agent config key"):
        resolve_agent_config({"bogus": True}, None)


def test_resolve_agent_config_drops_profile_model_when_stage_switches_backend():
    """A Claude model at profile level must not bleed into a stage that switches to
    codex (or vice versa). Models are CLI-specific — passing `claude-opus-4-7` to
    `codex exec -m ...` produces a 400 from Codex's account-level model whitelist."""
    cfg = resolve_agent_config(
        {"backend": "claude_code", "model": "claude-opus-4-7"},
        {"backend": "codex_cli", "permission_mode": "read-only"},
    )
    assert cfg.backend == "codex_cli"
    assert cfg.model is None
    assert cfg.permission_mode == "read-only"


def test_resolve_agent_config_drops_profile_permission_mode_when_stage_switches_backend():
    """permission_mode is also backend-specific (claude's modes != codex's modes)."""
    cfg = resolve_agent_config(
        {"backend": "codex_cli", "permission_mode": "workspace-write"},
        {"backend": "claude_code"},
    )
    assert cfg.backend == "claude_code"
    assert cfg.permission_mode is None


def test_resolve_agent_config_keeps_non_backend_specific_keys_across_backend_switch():
    """Generic keys (timeout_seconds, sterile_context) survive a backend switch — they
    don't carry backend-specific semantics."""
    cfg = resolve_agent_config(
        {"backend": "claude_code", "timeout_seconds": 120, "sterile_context": False},
        {"backend": "codex_cli"},
    )
    assert cfg.backend == "codex_cli"
    assert cfg.timeout_seconds == 120
    assert cfg.sterile_context is False


def test_build_runner_claude_default():
    runner = build_runner(AgentConfig())
    assert isinstance(runner, ClaudeCodeRunner)


def test_build_runner_claude_explicit():
    runner = build_runner(AgentConfig(backend="claude_code", model="claude-opus-4-7", timeout_seconds=42))
    assert isinstance(runner, ClaudeCodeRunner)
    assert runner._model == "claude-opus-4-7"
    assert runner._timeout_seconds == 42


def test_build_runner_codex():
    runner = build_runner(AgentConfig(backend="codex_cli"))
    assert isinstance(runner, CodexCliRunner)


def test_build_runner_unknown_backend_raises():
    with pytest.raises(ValueError, match="Unknown agent backend"):
        build_runner(AgentConfig(backend="not_a_thing"))


# ── FakeRunner sanity ─────────────────────────────────────────────────────────


def test_fake_runner_records_requests():
    runner = FakeRunner("hello")
    runner.run(AgentRunRequest(prompt="a"))
    runner.run(AgentRunRequest(prompt="b"))
    assert [r.prompt for r in runner.requests] == ["a", "b"]


def test_fake_runner_consumes_canned_responses_in_order():
    runner = FakeRunner(["first", "second"])
    r1 = runner.run(AgentRunRequest(prompt="x"))
    r2 = runner.run(AgentRunRequest(prompt="y"))
    assert r1.stdout == "first"
    assert r2.stdout == "second"


def test_fake_runner_responder_callable():
    runner = FakeRunner(responder=lambda req: f"echo:{req.prompt}")
    result = runner.run(AgentRunRequest(prompt="hi"))
    assert result.stdout == "echo:hi"


# ── Streaming progress (ADR-024) ──────────────────────────────────────────────


def _stream_popen(monkeypatch, lines, exit_code=0):
    """Patch the shared Claude subprocess driver to yield ``lines`` then exit."""
    from orchestrator.agent_runner import _claude as claude_mod

    captured: dict = {}

    class _StreamPopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            self.stdout = iter(lines)

        def wait(self, timeout=None):
            return exit_code

        def kill(self):
            pass

    monkeypatch.setattr(claude_mod.subprocess, "Popen", _StreamPopen)
    return captured


def test_claude_runner_progress_callback_switches_to_stream_json(monkeypatch):
    captured = _stream_popen(monkeypatch, lines=[])
    runner = ClaudeCodeRunner()
    events: list = []
    runner.run(AgentRunRequest(prompt="x", progress_callback=events.append))
    cmd = captured["cmd"]
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    # --verbose is required by the claude CLI when --output-format=stream-json is set.
    assert "--verbose" in cmd


def test_claude_runner_no_callback_stays_text_mode(monkeypatch):
    captured = _stream_popen(monkeypatch, lines=["plain text\n"])
    runner = ClaudeCodeRunner()
    runner.run(AgentRunRequest(prompt="x"))
    cmd = captured["cmd"]
    assert "--output-format" not in cmd
    assert "stream-json" not in cmd
    assert "--verbose" not in cmd


def test_claude_runner_parses_stream_events_and_emits_to_callback(monkeypatch):
    lines = [
        '{"type":"system","subtype":"init","model":"claude-opus-4-7"}\n',
        (
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_use","name":"Bash","input":{"command":"pytest tests/"}},'
            '{"type":"text","text":"running tests now"}]}}\n'
        ),
        (
            '{"type":"result","subtype":"success","is_error":false,'
            '"result":"final reply\\nSIGNAL_JSON: {\\"stage\\":\\"x\\",\\"status\\":\\"passed\\"}"}\n'
        ),
    ]
    _stream_popen(monkeypatch, lines=lines)
    runner = ClaudeCodeRunner()
    events: list = []
    result = runner.run(AgentRunRequest(prompt="x", progress_callback=events.append))

    kinds = [e.kind for e in events]
    assert "session_start" in kinds
    assert "tool_use" in kinds
    assert "assistant_text" in kinds
    assert "session_end" in kinds

    tool_event = next(e for e in events if e.kind == "tool_use")
    assert tool_event.tool == "Bash"
    assert "pytest" in tool_event.summary

    # result.stdout reconstructed from the final result event so SIGNAL_JSON
    # extraction works exactly as in text mode.
    assert "SIGNAL_JSON" in result.stdout
    assert "final reply" in result.stdout


def test_claude_runner_streaming_falls_back_to_raw_stream_when_no_result(monkeypatch):
    """If the agent crashes before emitting a result event, output.md must still
    carry the raw JSONL so the failure remains diagnosable."""
    lines = [
        '{"type":"system","subtype":"init","model":"claude-opus-4-7"}\n',
        "ERROR: keychain auth failed\n",
    ]
    _stream_popen(monkeypatch, lines=lines)
    runner = ClaudeCodeRunner()
    result = runner.run(AgentRunRequest(prompt="x", progress_callback=lambda _e: None))
    assert "ERROR: keychain auth failed" in result.stdout


def test_claude_runner_callback_exceptions_do_not_break_run(monkeypatch):
    """A logger glitch must not abort a stage — the runner swallows callback errors."""
    lines = [
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"x.py"}}]}}\n',
        '{"type":"result","subtype":"success","result":"done"}\n',
    ]
    _stream_popen(monkeypatch, lines=lines)
    runner = ClaudeCodeRunner()

    def _exploding(_event):
        raise RuntimeError("logger crashed")

    result = runner.run(AgentRunRequest(prompt="x", progress_callback=_exploding))
    assert result.stdout == "done"
