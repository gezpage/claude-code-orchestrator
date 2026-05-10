import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

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
}


def test_happy_path(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"Some reasoning\nSIGNAL_JSON: {GOOD_SIGNAL}"
    with patch("orchestrator.run_stage._run_claude", return_value=stdout):
        result = run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert result["status"] == "passed"
    assert result["stage"] == "discovery"
    assert (run_folder / "stages" / "discovery.md").exists()


def test_dangerously_skip_permissions_present(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    mock_proc = MagicMock()
    mock_proc.stdout = iter([stdout])
    mock_proc.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    for call in mock_popen.call_args_list:
        args = call.args[0]
        assert "--dangerously-skip-permissions" in args


def test_grace_retry_triggered_and_succeeds(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    no_signal = "Some output with no signal line."
    with_signal = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    with patch("orchestrator.run_stage._run_claude", side_effect=[no_signal, with_signal]) as mock_claude:
        result = run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert result["status"] == "passed"
    assert mock_claude.call_count == 2
    # Second call must be the grace retry prompt
    grace_prompt = mock_claude.call_args_list[1].args[0]
    assert "SIGNAL_JSON" in grace_prompt


def test_grace_retry_fails_returns_blocked(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    no_signal = "Still no signal."
    with patch("orchestrator.run_stage._run_claude", side_effect=[no_signal, no_signal]):
        result = run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert result["status"] == "blocked"
    assert result["message"] == "No signal emitted"


def test_stage_output_written_before_signal_extraction(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    with patch("orchestrator.run_stage._run_claude", return_value=stdout):
        run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert (run_folder / "stages" / "discovery.md").exists()


def test_cwd_forwarded_to_popen(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    mock_proc = MagicMock()
    mock_proc.stdout = iter([stdout])
    mock_proc.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path), cwd="/repo")
    assert mock_popen.call_args.kwargs.get("cwd") == "/repo"


def test_prompt_file_overrides_template_rendering(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    custom_prompt_file = tmp_path / "custom-prompt.md"
    custom_prompt_file.write_text("Custom prompt content\nSIGNAL_JSON: " + GOOD_SIGNAL)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    with patch("orchestrator.run_stage._run_claude", return_value=stdout) as mock_claude:
        run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path),
                  prompt_file=str(custom_prompt_file))
    # prompt passed to Claude must be the file content, not a rendered template
    called_prompt = mock_claude.call_args.args[0]
    assert "Custom prompt content" in called_prompt


def test_schema_name_overrides_stage_for_validation(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    # Use discovery_planning schema (which exists) for a stage that has no schema file
    planning_signal = '{"stage": "discovery-planning", "status": "passed", "tracks": []}'
    stdout = f"SIGNAL_JSON: {planning_signal}"
    with patch("orchestrator.run_stage._run_claude", return_value=stdout):
        result = run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path),
                           output_suffix="planning", schema_name="discovery_planning")
    assert result["status"] == "passed"
    assert result["stage"] == "discovery-planning"


def test_run_stage_does_not_read_stage_output_files(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    import builtins
    original_open = builtins.open
    opened_files = []

    def tracking_open(path, *args, **kwargs):
        opened_files.append(str(path))
        return original_open(path, *args, **kwargs)

    with patch("orchestrator.run_stage._run_claude", return_value=stdout):
        with patch("builtins.open", side_effect=tracking_open):
            run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))

    stage_output_reads = [f for f in opened_files if "stages" in f and f.endswith(".md") and "prompt" not in f]
    assert stage_output_reads == [], f"run_stage read stage output files: {stage_output_reads}"
