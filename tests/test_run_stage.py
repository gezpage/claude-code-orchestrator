from orchestrator.agent_runner import FakeRunner
from orchestrator.run_stage import run_stage

GOOD_SIGNAL = '{"stage": "discovery", "status": "passed", "findings_files": []}'
BLOCKED_SIGNAL = '{"stage": "discovery", "status": "blocked", "message": "Could not find overview"}'


def _setup_run_folder(tmp_path):
    run_folder = tmp_path / "2026-05-08-run-1"
    run_folder.mkdir()
    log_path = tmp_path / "logs"
    log_path.mkdir()
    return run_folder, log_path


VARS = {
    "run_folder": "/tmp/run",
    "feature_path": "/tmp/docs/projects/myproject/feature",
    "docs_root": "/tmp/docs",
    "repo_root": "/tmp/repo",
}


def test_happy_path(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"Some reasoning\nSIGNAL_JSON: {GOOD_SIGNAL}")
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "passed"
    assert result["stage"] == "discovery"
    assert (run_folder / "discovery" / "discovery-output.md").exists()


def test_default_runner_passes_required_flags(tmp_path, monkeypatch):
    """The default ClaudeCodePrintRunner must always pass --bare and --dangerously-skip-permissions.
    These are the ADR-003/012 invariants now scoped to the runner."""
    from orchestrator.agent_runner import _claude as claude_mod

    run_folder, log_path = _setup_run_folder(tmp_path)
    captured: dict = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            self.stdout = iter([f"SIGNAL_JSON: {GOOD_SIGNAL}\n"])

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(claude_mod.subprocess, "Popen", _FakePopen)
    run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert "--dangerously-skip-permissions" in captured["cmd"]
    assert "--bare" in captured["cmd"]
    # Sterile context is the default — auto-memory env must be set.
    assert captured["env"].get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"


def test_grace_retry_triggered_and_succeeds(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(["Some output with no signal line.", f"SIGNAL_JSON: {GOOD_SIGNAL}"])
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "passed"
    assert len(runner.requests) == 2
    # Second call must be the grace retry prompt.
    assert "SIGNAL_JSON" in runner.requests[1].prompt


def test_grace_retry_fails_returns_blocked(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(["Still no signal.", "Still no signal."])
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "blocked"
    assert result["message"] == "No signal emitted"


def test_stage_output_written_before_signal_extraction(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")
    run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner)
    assert (run_folder / "discovery" / "discovery-output.md").exists()


def test_cwd_forwarded_to_runner(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")
    run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), cwd="/repo", runner=runner
    )
    assert runner.requests[0].cwd == "/repo"


def test_prompt_file_overrides_template_rendering(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    custom_prompt_file = tmp_path / "custom-prompt.md"
    custom_prompt_file.write_text("Custom prompt content\nSIGNAL_JSON: " + GOOD_SIGNAL)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")
    run_stage(
        "discovery",
        "default",
        VARS,
        run_folder,
        str(tmp_path),
        "myproject",
        str(log_path),
        prompt_file=str(custom_prompt_file),
        runner=runner,
    )
    # Prompt passed to the runner must be the file content, not a rendered template.
    assert "Custom prompt content" in runner.requests[0].prompt


def test_schema_name_overrides_stage_for_validation(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    planning_signal = '{"stage": "discovery-planning", "status": "passed", "tracks": []}'
    runner = FakeRunner(f"SIGNAL_JSON: {planning_signal}")
    result = run_stage(
        "discovery",
        "default",
        VARS,
        run_folder,
        str(tmp_path),
        "myproject",
        str(log_path),
        output_suffix="planning",
        schema_name="discovery_planning",
        runner=runner,
    )
    assert result["status"] == "passed"
    assert result["stage"] == "discovery-planning"


def test_run_stage_does_not_read_stage_output_files(tmp_path, monkeypatch):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")

    import builtins

    original_open = builtins.open
    opened_files: list[str] = []

    def tracking_open(path, *args, **kwargs):
        opened_files.append(str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)
    run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner)

    stage_output_reads = [f for f in opened_files if str(run_folder) in f and f.endswith(".md") and "prompt" not in f]
    assert stage_output_reads == [], f"run_stage read stage output files: {stage_output_reads}"


def test_output_files_in_stage_subfolder(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")
    run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner)
    assert (run_folder / "discovery" / "discovery-prompt.md").exists()
    assert (run_folder / "discovery" / "discovery-output.md").exists()
    assert not (run_folder / "stages").exists()


def test_no_stream_log_written(tmp_path):
    """Stream logs were dropped as redundant with output.md. Stages should leave
    nothing matching *-stream.log behind."""
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")
    run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner)
    assert list((run_folder / "discovery").glob("*-stream.log")) == []


def test_non_zero_exit_blocks_signal_extraction(tmp_path):
    """A runner that fails with a non-zero exit code must not have its stdout parsed
    as a valid SIGNAL_JSON. Failure dominates whatever the agent claims to have
    emitted — otherwise a partial run could be accepted as success."""
    run_folder, log_path = _setup_run_folder(tmp_path)
    # stdout *looks* successful, but the process failed.
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}", exit_code=1)
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "blocked"
    assert "exit code 1" in result["message"]


def test_timed_out_blocks_signal_extraction(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}", timed_out=True)
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "blocked"
    assert "timed out" in result["message"]


def test_exit_code_none_is_treated_as_success(tmp_path):
    """The FakeRunner-default of None and the explicit success of 0 must both pass
    through to signal extraction. Only non-zero, non-None exit codes block."""
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}", exit_code=0)
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "passed"


def test_prompt_example_signal_does_not_override_final_signal(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(
        "\n".join(
            [
                'SIGNAL_JSON: {"stage": "discovery", "status": "passed", "findings_files": []}',
                'SIGNAL_JSON: {"stage": "discovery", "status": "blocked", "message": "real failure"}',
            ]
        )
    )
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "blocked"
    assert result["message"] == "real failure"


def test_passed_signal_blocks_when_declared_artifact_missing(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    missing = tmp_path / "missing.md"
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("prompt")
    signal = f'{{"stage": "decomposition", "status": "passed", "plan_file": "{missing}"}}'
    runner = FakeRunner(f"SIGNAL_JSON: {signal}")
    result = run_stage(
        "decomposition",
        "default",
        VARS,
        run_folder,
        str(tmp_path),
        "myproject",
        str(log_path),
        prompt_file=str(prompt_file),
        runner=runner,
    )
    assert result["status"] == "blocked"
    assert str(missing) in result["message"]


def test_grace_retry_blocks_on_runner_failure(tmp_path):
    """If the initial call returned no SIGNAL_JSON and the grace retry then fails
    (non-zero exit), the stage must block on the retry failure rather than report
    'No signal emitted'."""
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(["no signal here", f"SIGNAL_JSON: {GOOD_SIGNAL}"], exit_code=[0, 1])
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "blocked"
    assert "exit code 1" in result["message"]


def test_grace_retry_blocks_on_timeout(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(["no signal here", f"SIGNAL_JSON: {GOOD_SIGNAL}"], timed_out=[False, True])
    result = run_stage(
        "discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner
    )
    assert result["status"] == "blocked"
    assert "timed out" in result["message"]
