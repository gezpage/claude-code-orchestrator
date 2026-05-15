"""Tests for the agent runner abstraction introduced in ADR-018."""

from __future__ import annotations

import subprocess

import pytest

from orchestrator.agent_runner import (
    AgentConfig,
    AgentRunRequest,
    ClaudeCodeAutoRunner,
    ClaudeCodePrintRunner,
    CodexCliRunner,
    FakeRunner,
    build_runner,
    resolve_agent_config,
)

# ── Command construction ──────────────────────────────────────────────────────


def _stub_popen(monkeypatch, target_module, stdout="", exit_code=0):
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
    runner = ClaudeCodePrintRunner(sterile_context=True)
    runner.run(AgentRunRequest(prompt="do the thing", stage_name="discovery"))
    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "do the thing" in cmd
    assert "--bare" in cmd
    assert "--dangerously-skip-permissions" in cmd


def test_claude_runner_sterile_env_set_when_enabled(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodePrintRunner(sterile_context=True)
    runner.run(AgentRunRequest(prompt="x"))
    assert captured["kwargs"]["env"].get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"


def test_claude_runner_sterile_env_absent_when_disabled(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodePrintRunner(sterile_context=False)
    # Ensure no inherited value would leak through.
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)
    runner.run(AgentRunRequest(prompt="x"))
    assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in captured["kwargs"]["env"]


def test_claude_runner_request_env_overrides_inherited(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodePrintRunner(sterile_context=False)
    runner.run(AgentRunRequest(prompt="x", env={"FOO": "bar"}))
    assert captured["kwargs"]["env"].get("FOO") == "bar"


def test_claude_runner_model_flag(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    captured = _stub_popen(monkeypatch, claude_mod)
    runner = ClaudeCodePrintRunner()
    runner.run(AgentRunRequest(prompt="x", model="sonnet"))
    cmd = captured["cmd"]
    assert "--model" in cmd
    assert "sonnet" in cmd


def test_claude_runner_writes_stream_log(monkeypatch, tmp_path):
    from orchestrator.agent_runner import _claude as claude_mod

    _stub_popen(monkeypatch, claude_mod, stdout="stream body")
    stream_log_path = tmp_path / "stage" / "stage-stream.log"
    runner = ClaudeCodePrintRunner()
    result = runner.run(AgentRunRequest(prompt="x", stream_log_path=stream_log_path))
    assert stream_log_path.read_text() == "stream body"
    assert result.stream_log_path == stream_log_path


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
    runner = ClaudeCodePrintRunner()
    result = runner.run(AgentRunRequest(prompt="x", timeout_seconds=1))
    assert result.timed_out is True


def test_claude_runner_non_zero_exit_captured(monkeypatch):
    from orchestrator.agent_runner import _claude as claude_mod

    _stub_popen(monkeypatch, claude_mod, exit_code=2)
    runner = ClaudeCodePrintRunner()
    result = runner.run(AgentRunRequest(prompt="x"))
    assert result.exit_code == 2
    assert result.timed_out is False


# ── Claude code auto runner ───────────────────────────────────────────────────


def test_claude_code_auto_runner_builds_expected_command(monkeypatch):
    from orchestrator.agent_runner import _claude_auto as auto_mod

    captured = _stub_popen(monkeypatch, auto_mod, stdout="ok")
    runner = ClaudeCodeAutoRunner()
    runner.run(AgentRunRequest(prompt="do the thing"))
    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "do the thing" in cmd
    assert "--permission-mode" in cmd
    assert "auto" in cmd
    # The whole point of the auto runner: --bare and --dangerously-skip-permissions
    # must be absent. --bare would force ANTHROPIC_API_KEY auth, --dangerously-skip-permissions
    # would defeat the permission gating.
    assert "--bare" not in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_claude_code_auto_runner_sterile_context_env(monkeypatch):
    from orchestrator.agent_runner import _claude_auto as auto_mod

    captured = _stub_popen(monkeypatch, auto_mod)
    runner = ClaudeCodeAutoRunner(sterile_context=True)
    runner.run(AgentRunRequest(prompt="x"))
    assert captured["kwargs"]["env"].get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"

    captured = _stub_popen(monkeypatch, auto_mod)
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)
    runner = ClaudeCodeAutoRunner(sterile_context=False)
    runner.run(AgentRunRequest(prompt="x"))
    assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in captured["kwargs"]["env"]


def test_claude_code_auto_runner_model_flag(monkeypatch):
    from orchestrator.agent_runner import _claude_auto as auto_mod

    captured = _stub_popen(monkeypatch, auto_mod)
    runner = ClaudeCodeAutoRunner(model="claude-opus-4-7")
    runner.run(AgentRunRequest(prompt="x"))
    cmd = captured["cmd"]
    assert "--model" in cmd
    assert "claude-opus-4-7" in cmd

    # Request-level model overrides the constructor default.
    captured = _stub_popen(monkeypatch, auto_mod)
    runner = ClaudeCodeAutoRunner(model="claude-opus-4-7")
    runner.run(AgentRunRequest(prompt="x", model="sonnet"))
    cmd = captured["cmd"]
    assert "--model" in cmd
    assert "sonnet" in cmd


def test_claude_code_auto_runner_writes_stream_log(monkeypatch, tmp_path):
    from orchestrator.agent_runner import _claude_auto as auto_mod

    _stub_popen(monkeypatch, auto_mod, stdout="stream body")
    stream_log_path = tmp_path / "stage" / "stage-stream.log"
    runner = ClaudeCodeAutoRunner()
    result = runner.run(AgentRunRequest(prompt="x", stream_log_path=stream_log_path))
    assert stream_log_path.read_text() == "stream body"
    assert result.stream_log_path == stream_log_path


def test_claude_code_auto_runner_timeout_marks_result(monkeypatch):
    from orchestrator.agent_runner import _claude_auto as auto_mod

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

    monkeypatch.setattr(auto_mod.subprocess, "Popen", _SlowPopen)
    runner = ClaudeCodeAutoRunner()
    result = runner.run(AgentRunRequest(prompt="x", timeout_seconds=1))
    assert result.timed_out is True


def test_build_runner_resolves_claude_code_auto():
    runner = build_runner(AgentConfig(backend="claude_code_auto", model="claude-opus-4-7", timeout_seconds=42))
    assert isinstance(runner, ClaudeCodeAutoRunner)
    assert runner._model == "claude-opus-4-7"
    assert runner._timeout_seconds == 42


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


def test_codex_runner_sandbox_mode_mapping(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = CodexCliRunner()
    runner.run(AgentRunRequest(prompt="x", permission_mode="read-only"))
    cmd = captured["cmd"]
    assert "--sandbox" in cmd
    assert "read-only" in cmd
    assert "--full-auto" not in cmd


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


def test_codex_runner_danger_full_access_uses_sandbox_flag(monkeypatch):
    from orchestrator.agent_runner import _codex as codex_mod

    captured = _stub_popen(monkeypatch, codex_mod)
    runner = CodexCliRunner()
    runner.run(AgentRunRequest(prompt="x", permission_mode="danger-full-access"))
    cmd = captured["cmd"]
    assert "--sandbox" in cmd
    assert "danger-full-access" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


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


def test_codex_runner_stream_log_holds_raw_stream_result_stdout_clean(monkeypatch, tmp_path):
    from orchestrator.agent_runner import _codex as codex_mod

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            output_path = cmd[cmd.index("--output-last-message") + 1]
            from pathlib import Path

            Path(output_path).write_text("clean final message\nSIGNAL_JSON: {}")
            # Full terminal stream is noisy — banner, command logs, diffs, etc.
            self.stdout = iter(["codex banner\n", "workdir: /tmp\n", "model: gpt-5\n", "(noisy stream)\n"])

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(codex_mod.subprocess, "Popen", _FakePopen)
    stream_log = tmp_path / "stage" / "stage-stream.log"
    runner = CodexCliRunner()
    result = runner.run(AgentRunRequest(prompt="x", stream_log_path=stream_log))
    # On-disk stream log preserves the full raw stream for forensics.
    written = stream_log.read_text()
    assert "codex banner" in written
    assert "workdir: /tmp" in written
    assert "(noisy stream)" in written
    # In-memory result.stdout still gets the clean final message — signal parsing
    # downstream depends on this being terse.
    assert result.stdout == "clean final message\nSIGNAL_JSON: {}"


def test_codex_runner_stream_log_falls_back_to_stream_when_no_last_message(monkeypatch, tmp_path):
    from orchestrator.agent_runner import _codex as codex_mod

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            # Simulate a Codex run that did not produce --output-last-message content
            # (e.g. the binary crashed before writing). result.stdout falls back to
            # the raw stream so the failure is debuggable; the stream log holds the
            # same raw stream by definition.
            self.stdout = iter(["partial stream output\n"])

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(codex_mod.subprocess, "Popen", _FakePopen)
    stream_log = tmp_path / "stage" / "stage-stream.log"
    runner = CodexCliRunner()
    result = runner.run(AgentRunRequest(prompt="x", stream_log_path=stream_log))
    assert stream_log.read_text() == "partial stream output\n"
    assert result.stdout == "partial stream output\n"


# ── Selection / config merge ──────────────────────────────────────────────────


def test_resolve_agent_config_defaults():
    cfg = resolve_agent_config(None, None)
    assert cfg.backend == "claude_code_print"
    assert cfg.sterile_context is True
    assert cfg.model is None


def test_resolve_agent_config_profile_only():
    cfg = resolve_agent_config({"model": "opus", "sterile_context": False}, None)
    assert cfg.model == "opus"
    assert cfg.sterile_context is False
    assert cfg.backend == "claude_code_print"


def test_resolve_agent_config_stage_overrides_profile():
    cfg = resolve_agent_config(
        {"backend": "claude_code_print", "model": "opus"},
        {"backend": "codex_cli", "model": "gpt-5.1-codex"},
    )
    assert cfg.backend == "codex_cli"
    assert cfg.model == "gpt-5.1-codex"


def test_resolve_agent_config_unknown_key_raises():
    with pytest.raises(ValueError, match="Unknown agent config key"):
        resolve_agent_config({"bogus": True}, None)


def test_build_runner_claude_default():
    runner = build_runner(AgentConfig())
    assert isinstance(runner, ClaudeCodePrintRunner)


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
