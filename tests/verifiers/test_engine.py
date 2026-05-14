import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from orchestrator.verifiers.engine import VerificationError, verify


def _make_repo(tmp_path: Path, manifest: dict | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    if manifest is not None:
        (repo / "package.json").write_text(json.dumps(manifest))
    return repo


def _make_run_folder(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    return run


def _completed(code: int, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = code
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_passing_node_repo(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)
    assert sig["status"] == "passed"
    assert sig["verification_status"] == "passed"
    assert sig["toolchain"] == "node"
    assert mock_run.called
    assert (run_folder / "verification" / "VERIFY.md").exists()
    assert (run_folder / "verification" / "verify.json").exists()


def test_failed_required_command_marks_failed(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)
    # Stage status is still "passed" — verification is not a hard gate (ADR-017).
    assert sig["status"] == "passed"
    assert sig["verification_status"] == "failed"
    assert "test" in sig["failed_command_ids"]


def test_missing_script_skips_command(tmp_path: Path):
    # No scripts in manifest → test command must be skipped, not failed.
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run") as mock_run:
        sig = verify(repo, run_folder)
    # All commands have if_script_exists in node.yaml; none present → none invoked.
    assert mock_run.call_count == 0
    # All required commands skipped → verification not "passed", but probes still run.
    # No required failures and no probe failures, so "warned" (non-required skipped).
    assert sig["verification_status"] in {"passed", "warned"}


def test_noop_lint_script_caught_by_probe(tmp_path: Path):
    repo = _make_repo(
        tmp_path,
        {"name": "x", "version": "0.0.1", "scripts": {"test": "jest", "lint": "echo skipped"}},
    )
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "node_manifest_sanity" in sig["failed_probe_ids"]
    assert sig["verification_status"] == "failed"


def test_timeout_marks_failed(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch(
        "orchestrator.verifiers.engine.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="npm test", timeout=600),
    ):
        sig = verify(repo, run_folder)
    assert sig["verification_status"] == "failed"
    assert "test" in sig["failed_command_ids"]


def test_no_toolchain_returns_skipped_not_blocked(tmp_path: Path):
    """Repos without recognised markers (greenfield, prose-only) must not block the pipeline."""
    repo = _make_repo(tmp_path)  # no manifest
    run_folder = _make_run_folder(tmp_path)
    sig = verify(repo, run_folder)
    assert sig["status"] == "passed"
    assert sig["verification_status"] == "skipped"
    assert sig["toolchain"] == "none"
    assert (run_folder / "verification" / "VERIFY.md").exists()


def test_unknown_pinned_toolchain_raises(tmp_path: Path):
    """A `.cco.yaml` pin for a recipe that doesn't exist is a user config error, not a benign skip."""
    repo = _make_repo(tmp_path)
    (repo / ".cco.yaml").write_text(yaml.dump({"verification": {"toolchain": "rustacean"}}))
    run_folder = _make_run_folder(tmp_path)
    with pytest.raises(VerificationError, match="unknown toolchain 'rustacean'"):
        verify(repo, run_folder)


def test_explicit_toolchain_via_cco_yaml(tmp_path: Path):
    # No markers → would normally fail to detect; .cco.yaml pins node.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".cco.yaml").write_text(yaml.dump({"verification": {"toolchain": "node", "commands": [], "probes": []}}))
    run_folder = _make_run_folder(tmp_path)
    sig = verify(repo, run_folder)
    # No commands, no probes → trivially passed.
    assert sig["toolchain"] == "node"
    assert sig["verification_status"] == "passed"


def test_command_override_replaces_recipe_commands(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    (repo / ".cco.yaml").write_text(
        yaml.dump(
            {
                "verification": {
                    "commands": [{"id": "custom", "command": "true", "required": True}],
                }
            }
        )
    )
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)
    assert sig["command_ids"] == ["custom"]
    # Recipe's `test` command must NOT have been invoked.
    invoked = [c.kwargs.get("cmd") or c.args[0] for c in mock_run.call_args_list]
    assert all("npm test" not in cmd for cmd in invoked)


def test_artifacts_contain_machine_and_human_summary(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        verify(repo, run_folder)
    data = json.loads((run_folder / "verification" / "verify.json").read_text())
    assert data["toolchain"] == "node"
    assert data["status"] in {"passed", "warned", "failed"}
    md = (run_folder / "verification" / "VERIFY.md").read_text()
    assert "Verification Report" in md
    assert "node" in md
