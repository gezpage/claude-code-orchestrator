import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from orchestrator.run_stage import run_stage

GOOD_SIGNAL = '{"stage": "discovery", "status": "passed", "findings_files": []}'
BLOCKED_SIGNAL = '{"stage": "discovery", "status": "blocked", "message": "Could not find overview"}'


def _make_result(stdout):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = 0
    return r


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
    with patch("subprocess.run", return_value=_make_result(stdout)) as mock_sub:
        result = run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert result["status"] == "passed"
    assert result["stage"] == "discovery"
    # stage output file written
    assert (run_folder / "stage-output" / "discovery.txt").exists()


def test_dangerously_skip_permissions_present(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    with patch("subprocess.run", return_value=_make_result(stdout)) as mock_sub:
        run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    for call in mock_sub.call_args_list:
        args = call.args[0]
        assert "--dangerously-skip-permissions" in args


def test_grace_retry_triggered_and_succeeds(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    no_signal = "Some output with no signal line."
    with_signal = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    side_effects = [_make_result(no_signal), _make_result(with_signal)]
    with patch("subprocess.run", side_effect=side_effects) as mock_sub:
        result = run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert result["status"] == "passed"
    assert mock_sub.call_count == 2
    # Second call must be the grace retry prompt
    grace_call_args = mock_sub.call_args_list[1].args[0]
    assert "SIGNAL_JSON" in grace_call_args[2]


def test_grace_retry_fails_returns_blocked(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    no_signal = "Still no signal."
    side_effects = [_make_result(no_signal), _make_result(no_signal)]
    with patch("subprocess.run", side_effect=side_effects):
        result = run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))
    assert result["status"] == "blocked"
    assert result["message"] == "No signal emitted"


def test_stage_output_written_before_signal_extraction(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    written_contents = []

    original_run = __import__("subprocess").run

    def capturing_run(cmd, **kwargs):
        r = _make_result(stdout)
        out_file = run_folder / "stage-output" / "discovery.txt"
        if out_file.exists():
            written_contents.append(out_file.read_text())
        return r

    with patch("subprocess.run", side_effect=capturing_run):
        run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))

    assert (run_folder / "stage-output" / "discovery.txt").exists()


def test_run_stage_does_not_read_stage_output_files(tmp_path):
    run_folder, log_path = _setup_run_folder(tmp_path)
    stdout = f"SIGNAL_JSON: {GOOD_SIGNAL}"
    import builtins
    original_open = builtins.open
    opened_files = []

    def tracking_open(path, *args, **kwargs):
        opened_files.append(str(path))
        return original_open(path, *args, **kwargs)

    with patch("subprocess.run", return_value=_make_result(stdout)):
        with patch("builtins.open", side_effect=tracking_open):
            run_stage("discovery", "default", VARS, run_folder, str(tmp_path), "myproject", str(log_path))

    stage_output_reads = [f for f in opened_files if "stage-output" in f and f.endswith(".txt") and "prompt" not in f]
    assert stage_output_reads == [], f"run_stage read stage output files: {stage_output_reads}"
