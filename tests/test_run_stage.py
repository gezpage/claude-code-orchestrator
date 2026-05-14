import pytest

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


def test_transcript_path_passed_to_runner(tmp_path):
    """run_stage must hand the runner a transcript_path inside the per-stage folder.
    Backends own transcript persistence — orchestration only specifies destination."""
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")
    run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), runner=runner)
    req = runner.requests[0]
    assert req.transcript_path is not None
    assert req.transcript_path.parent == run_folder / "discovery"
    assert req.transcript_path.name.endswith("-transcript.md")
    assert req.transcript_path.exists()


@pytest.mark.parametrize("suffix", ["", "planning", "architecture"])
def test_transcript_filename_includes_output_suffix(tmp_path, suffix):
    run_folder, log_path = _setup_run_folder(tmp_path)
    runner = FakeRunner(f"SIGNAL_JSON: {GOOD_SIGNAL}")
    run_stage(
        "discovery",
        "default",
        VARS,
        run_folder,
        str(tmp_path),
        "myproject",
        str(log_path),
        output_suffix=suffix,
        runner=runner,
    )
    tag = f"-{suffix}" if suffix else ""
    expected = run_folder / "discovery" / f"discovery{tag}-transcript.md"
    assert runner.requests[0].transcript_path == expected
