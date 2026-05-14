import json
from pathlib import Path
from unittest.mock import patch

from orchestrator.run_stage import run_deterministic_stage


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}}))
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    log_path = tmp_path / "logs"
    log_path.mkdir()
    return repo, run_folder, log_path


def test_dispatches_engine_and_returns_validated_signal(tmp_path: Path):
    repo, run_folder, log_path = _setup(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run") as mock_run:
        mock_proc = type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        mock_run.return_value = mock_proc
        sig = run_deterministic_stage("verification", str(repo), run_folder, str(log_path))
    assert sig["stage"] == "verification"
    assert sig["status"] == "passed"
    assert "verify_md_path" in sig
    assert (run_folder / "verification" / "VERIFY.md").exists()


def test_no_toolchain_passes_with_skipped_status(tmp_path: Path):
    """No detected toolchain should not block the pipeline — it's a benign state."""
    repo = tmp_path / "repo"
    repo.mkdir()  # no manifest → no toolchain
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    log_path = tmp_path / "logs"
    log_path.mkdir()
    sig = run_deterministic_stage("verification", str(repo), run_folder, str(log_path))
    assert sig["status"] == "passed"
    assert sig["verification_status"] == "skipped"


def test_unknown_pinned_toolchain_returns_blocked(tmp_path: Path):
    """A `.cco.yaml` pin to a non-existent recipe is a real config error."""
    import yaml as _yaml

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".cco.yaml").write_text(_yaml.dump({"verification": {"toolchain": "rustacean"}}))
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    log_path = tmp_path / "logs"
    log_path.mkdir()
    sig = run_deterministic_stage("verification", str(repo), run_folder, str(log_path))
    assert sig["status"] == "blocked"
    assert "rustacean" in sig["message"]


def test_no_claude_subprocess_invoked(tmp_path: Path):
    """Deterministic mode must never spawn the claude CLI — that's the whole point."""
    repo, run_folder, log_path = _setup(tmp_path)
    with (
        patch("orchestrator.agent_runner._claude.subprocess.Popen") as mock_popen,
        patch("orchestrator.verifiers.engine.subprocess.run") as mock_run,
    ):
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        run_deterministic_stage("verification", str(repo), run_folder, str(log_path))
    mock_popen.assert_not_called()
