import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from orchestrator.cli import main


# ── help commands exit 0 ─────────────────────────────────────────────────────

def test_main_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "stage" in result.output
    assert "resume" in result.output


def test_run_help():
    result = CliRunner().invoke(main, ["run", "--help"])
    assert result.exit_code == 0
    assert "--docs-root" in result.output


def test_stage_help():
    result = CliRunner().invoke(main, ["stage", "--help"])
    assert result.exit_code == 0
    assert "--stage" in result.output


def test_resume_help():
    result = CliRunner().invoke(main, ["resume", "--help"])
    assert result.exit_code == 0
    assert "--run-folder" in result.output


# ── run: missing --docs-root exits non-zero with clear error ─────────────────

def test_run_missing_docs_root():
    result = CliRunner().invoke(main, [
        "run",
        "--project", "myproject",
        "--feature-path", "features/x.md",
        "--branch", "feat/x",
    ])
    assert result.exit_code != 0
    assert "docs-root" in result.output.lower() or "docs_root" in result.output.lower()
    # Must not be a Python traceback
    assert "Traceback" not in result.output


def test_run_invalid_docs_root():
    result = CliRunner().invoke(main, [
        "run",
        "--docs-root", "/no/such/path",
        "--project", "myproject",
        "--feature-path", "features/x.md",
        "--branch", "feat/x",
    ])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


# ── run: dispatches to orchestrate.run_pipeline ──────────────────────────────

def test_run_dispatches(tmp_path):
    with patch("orchestrator.cli.orchestrate.run_pipeline") as mock_pipe, \
         patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(main, [
            "run",
            "--docs-root", str(tmp_path),
            "--project", "myproject",
            "--feature-path", "features/x.md",
            "--branch", "feat/x",
            "--profile", "full",
        ])
    mock_pipe.assert_called_once_with(
        str(tmp_path), "myproject", "features/x.md", "feat/x", "full"
    )


# ── stage: dispatches run_stage and prints signal JSON ───────────────────────

def test_stage_dispatches(tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps({"run_folder": str(tmp_path)}))

    fake_sig = {"stage": "discovery", "status": "passed", "findings_files": []}

    with patch("orchestrator.cli.run_stage", return_value=fake_sig) as mock_rs, \
         patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(main, [
            "stage",
            "--stage", "discovery",
            "--implementation", "default",
            "--input", str(input_file),
            "--run-folder", str(tmp_path),
            "--docs-root", str(tmp_path),
            "--project", "myproject",
            "--project-log-path", str(tmp_path),
        ])

    assert result.exit_code == 0
    assert mock_rs.called
    output = json.loads(result.output)
    assert output["status"] == "passed"


def test_stage_blocked_exits_nonzero(tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps({}))

    fake_sig = {"stage": "discovery", "status": "blocked", "message": "no signal"}

    with patch("orchestrator.cli.run_stage", return_value=fake_sig), \
         patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(main, [
            "stage",
            "--stage", "discovery",
            "--input", str(input_file),
            "--run-folder", str(tmp_path),
            "--docs-root", str(tmp_path),
            "--project", "myproject",
            "--project-log-path", str(tmp_path),
        ])

    assert result.exit_code != 0


# ── resume: reads state and calls orchestrate ─────────────────────────────────

def test_resume_reads_state_and_calls_orchestrate(tmp_path):
    import yaml
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    (run_folder / "_state.yaml").write_text(yaml.dump({
        "blocked_at": "alignment",
        "project": "myproject",
        "feature_path": "features/x.md",
        "branch": "feat/x",
        "profile": "full",
    }))

    with patch("orchestrator.cli.orchestrate.run_pipeline") as mock_pipe, \
         patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(main, [
            "resume",
            "--run-folder", str(run_folder),
            "--docs-root", str(tmp_path),
        ])

    mock_pipe.assert_called_once_with(
        str(tmp_path), "myproject", "features/x.md", "feat/x", "full", resume=True
    )


# ── full.yaml stages order ────────────────────────────────────────────────────

def test_full_yaml_stage_order():
    import yaml
    profiles_dir = Path(__file__).parent.parent / "orchestrator" / "profiles"
    full = yaml.safe_load((profiles_dir / "full.yaml").read_text())
    names = [s["stage"] for s in full["stages"]]
    expected = ["discovery", "alignment", "specification", "decomposition",
                "implementation", "qa", "review", "harvest"]
    assert names == expected
