import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from orchestrator.cli import main

# ── help commands exit 0 ─────────────────────────────────────────────────────


def test_main_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "stage" in result.output
    assert "resume" in result.output
    assert "bootstrap" in result.output


def test_bootstrap_help():
    result = CliRunner().invoke(main, ["bootstrap", "--help"])
    assert result.exit_code == 0
    assert "--toolchain" in result.output
    assert "--dry-run" in result.output
    assert "--force" in result.output
    assert "--commit" in result.output


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


# ── run: missing flag in non-TTY context exits non-zero with clear error ─────


def test_run_missing_docs_root_non_tty():
    # CliRunner stdin/stdout are not TTYs, so missing flags trigger the structured
    # non-TTY error path rather than an interactive prompt.
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--project",
            "myproject",
            "--feature-path",
            "features/x.md",
            "--branch",
            "feat/x",
        ],
    )
    assert result.exit_code != 0
    assert "docs-root" in result.output.lower() or "docs_root" in result.output.lower()
    assert "Traceback" not in result.output


def test_run_invalid_docs_root():
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--docs-root",
            "/no/such/path",
            "--project",
            "myproject",
            "--feature-path",
            "features/x.md",
            "--branch",
            "feat/x",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output


# ── run: dispatches to orchestrate.run_pipeline ──────────────────────────────


def test_run_dispatches(tmp_path):
    with patch("orchestrator.cli.orchestrate.run_pipeline") as mock_pipe, patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(
            main,
            [
                "run",
                "--docs-root",
                str(tmp_path),
                "--project",
                "myproject",
                "--feature-path",
                "features/x.md",
                "--branch",
                "feat/x",
                "--profile",
                "full",
                "--no-create-pr",
            ],
        )
    mock_pipe.assert_called_once_with(
        str(tmp_path),
        "myproject",
        "features/x.md",
        "feat/x",
        "full",
        base_branch=None,
        create_pr=False,
    )


def test_run_passes_base_branch_and_create_pr(tmp_path):
    with patch("orchestrator.cli.orchestrate.run_pipeline") as mock_pipe, patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(
            main,
            [
                "run",
                "--docs-root",
                str(tmp_path),
                "--project",
                "myproject",
                "--feature-path",
                "features/x.md",
                "--branch",
                "feat/x",
                "--profile",
                "full",
                "--base-branch",
                "develop",
                "--create-pr",
            ],
        )
    mock_pipe.assert_called_once_with(
        str(tmp_path),
        "myproject",
        "features/x.md",
        "feat/x",
        "full",
        base_branch="develop",
        create_pr=True,
    )


# ── stage: dispatches run_stage and prints signal JSON ───────────────────────


def test_stage_dispatches(tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps({"run_folder": str(tmp_path)}))

    fake_sig = {"stage": "discovery", "status": "passed", "findings_files": []}

    with (
        patch("orchestrator.cli.run_stage", return_value=fake_sig) as mock_rs,
        patch("orchestrator.cli.paths.require_dir"),
    ):
        result = CliRunner().invoke(
            main,
            [
                "stage",
                "--stage",
                "discovery",
                "--implementation",
                "default",
                "--input",
                str(input_file),
                "--run-folder",
                str(tmp_path),
                "--docs-root",
                str(tmp_path),
                "--project",
                "myproject",
                "--project-log-path",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0
    assert mock_rs.called
    output = json.loads(result.output)
    assert output["status"] == "passed"


def test_stage_blocked_exits_nonzero(tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps({}))

    fake_sig = {"stage": "discovery", "status": "blocked", "message": "no signal"}

    with patch("orchestrator.cli.run_stage", return_value=fake_sig), patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(
            main,
            [
                "stage",
                "--stage",
                "discovery",
                "--input",
                str(input_file),
                "--run-folder",
                str(tmp_path),
                "--docs-root",
                str(tmp_path),
                "--project",
                "myproject",
                "--project-log-path",
                str(tmp_path),
            ],
        )

    assert result.exit_code != 0


# ── resume: reads state and calls orchestrate ─────────────────────────────────


def test_resume_reads_state_and_calls_orchestrate(tmp_path):
    import yaml

    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    (run_folder / "_state.yaml").write_text(
        yaml.dump(
            {
                "blocked_at": "alignment",
                "project": "myproject",
                "feature_path": "features/x.md",
                "branch": "feat/x",
                "profile": "full",
            }
        )
    )

    with patch("orchestrator.cli.orchestrate.run_pipeline") as mock_pipe, patch("orchestrator.cli.paths.require_dir"):
        result = CliRunner().invoke(
            main,
            [
                "resume",
                "--run-folder",
                str(run_folder),
                "--docs-root",
                str(tmp_path),
            ],
        )

    mock_pipe.assert_called_once_with(str(tmp_path), "myproject", "features/x.md", "feat/x", "full", resume=True)


# ── full.yaml stages order ────────────────────────────────────────────────────


def test_full_yaml_stage_order():
    import yaml

    profiles_dir = Path(__file__).parent.parent / "orchestrator" / "profiles"
    full = yaml.safe_load((profiles_dir / "full.yaml").read_text())
    names = [s["stage"] for s in full["stages"]]
    expected = [
        "discovery",
        "alignment",
        "specification",
        "decomposition",
        "implementation",
        "qa",
        "verification",
        "review",
        "harvest",
    ]
    assert names == expected


# ── bootstrap: writes the planned files ──────────────────────────────────────


def _make_bootstrap_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Create a docs-root with a project.yaml pointing at a fresh repo-root."""
    docs_root = tmp_path / "docs"
    repo_root = tmp_path / "repo"
    (docs_root / "projects" / "myproject").mkdir(parents=True)
    repo_root.mkdir()
    (docs_root / "projects" / "myproject" / "project.yaml").write_text(f"repo-root: {repo_root}\n")
    return docs_root, repo_root


def test_bootstrap_python_writes_files(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--no-commit",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (repo_root / ".cco.yaml").is_file()
    assert "toolchain: python" in (repo_root / ".cco.yaml").read_text()
    assert (repo_root / "pyproject.toml").is_file()
    # project.yaml gained the matching standards entry.
    project_yaml = docs_root / "projects" / "myproject" / "project.yaml"
    import yaml as _yaml

    assert _yaml.safe_load(project_yaml.read_text())["standards"] == ["python"]


def test_bootstrap_dry_run_writes_nothing(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert not (repo_root / ".cco.yaml").exists()
    assert not (repo_root / "pyproject.toml").exists()


def test_bootstrap_existing_cco_yaml_blocks_without_force(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    (repo_root / ".cco.yaml").write_text("verification:\n  toolchain: rust\n")
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--no-commit",
        ],
    )
    # Non-TTY (CliRunner) treats conflicts as a UsageError.
    assert result.exit_code != 0
    # The existing file is untouched.
    assert "rust" in (repo_root / ".cco.yaml").read_text()


def test_bootstrap_force_overwrites(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    (repo_root / ".cco.yaml").write_text("verification:\n  toolchain: rust\n")
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--force",
            "--no-commit",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "toolchain: python" in (repo_root / ".cco.yaml").read_text()


def test_bootstrap_idempotent_second_run(tmp_path):
    docs_root, _repo_root = _make_bootstrap_inputs(tmp_path)
    first = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--no-commit",
        ],
    )
    assert first.exit_code == 0, first.output
    second = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--no-commit",
        ],
    )
    assert second.exit_code == 0, second.output
    assert "already present" in second.output


def test_bootstrap_non_tty_missing_toolchain_errors(tmp_path):
    docs_root, _ = _make_bootstrap_inputs(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--no-commit",
        ],
    )
    assert result.exit_code != 0
    assert "toolchain" in result.output.lower()


def test_bootstrap_missing_project_yaml_errors(tmp_path):
    docs_root = tmp_path / "docs"
    (docs_root / "projects" / "myproject").mkdir(parents=True)
    # No project.yaml.
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--no-commit",
        ],
    )
    assert result.exit_code != 0
    assert "project.yaml" in result.output


def test_bootstrap_enable_glossary_default_path(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--enable-glossary",
            "--no-commit",
        ],
    )
    assert result.exit_code == 0, result.output
    import yaml as _yaml

    project_yaml = docs_root / "projects" / "myproject" / "project.yaml"
    data = _yaml.safe_load(project_yaml.read_text())
    assert data["domain_language"] == {"path": "docs/glossary.md"}
    # Seed glossary file written under repo-root, not docs-root.
    assert (repo_root / "docs" / "glossary.md").is_file()
    assert (repo_root / "docs" / "glossary.md").read_text() == "# Domain language\n"


def test_bootstrap_enable_glossary_custom_path(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--enable-glossary=knowledge/lexicon.md",
            "--no-commit",
        ],
    )
    assert result.exit_code == 0, result.output
    import yaml as _yaml

    data = _yaml.safe_load((docs_root / "projects" / "myproject" / "project.yaml").read_text())
    assert data["domain_language"] == {"path": "knowledge/lexicon.md"}
    assert (repo_root / "knowledge" / "lexicon.md").is_file()


def test_bootstrap_without_enable_glossary_does_not_touch_project_yaml(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--no-commit",
        ],
    )
    assert result.exit_code == 0, result.output
    import yaml as _yaml

    data = _yaml.safe_load((docs_root / "projects" / "myproject" / "project.yaml").read_text())
    # Strict opt-in: glossary is silently skipped when the flag is absent in a non-TTY run.
    assert "domain_language" not in data
    assert not (repo_root / "docs" / "glossary.md").exists()


def test_bootstrap_enable_glossary_rejects_path_escape(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--enable-glossary=../escape.md",
            "--no-commit",
        ],
    )
    assert result.exit_code != 0
    assert "escape attempt" in result.output
    # Nothing was written outside the repo and the docs-side block was not added.
    import yaml as _yaml

    data = _yaml.safe_load((docs_root / "projects" / "myproject" / "project.yaml").read_text())
    assert "domain_language" not in data
    assert not (tmp_path / "escape.md").exists()


def test_bootstrap_enable_glossary_idempotent_on_second_run(tmp_path):
    docs_root, repo_root = _make_bootstrap_inputs(tmp_path)
    project_yaml = docs_root / "projects" / "myproject" / "project.yaml"
    # First run scaffolds the block + seed file.
    first = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--enable-glossary",
            "--no-commit",
        ],
    )
    assert first.exit_code == 0, first.output
    # The user has since written real content into the glossary.
    (repo_root / "docs" / "glossary.md").write_text("# My glossary\n\n## Order\n\nA customer order.\n")
    # Second run with a *different* glossary path must not change either the
    # project.yaml block or the existing glossary file — opt-in is strictly
    # additive and never silently moves a user's configured location.
    second = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--enable-glossary=other/spot.md",
            "--no-commit",
        ],
    )
    assert second.exit_code == 0, second.output
    import yaml as _yaml

    data = _yaml.safe_load(project_yaml.read_text())
    assert data["domain_language"] == {"path": "docs/glossary.md"}
    assert (repo_root / "docs" / "glossary.md").read_text() == "# My glossary\n\n## Order\n\nA customer order.\n"
    assert not (repo_root / "other" / "spot.md").exists()


def test_bootstrap_repo_root_missing_errors(tmp_path):
    docs_root = tmp_path / "docs"
    (docs_root / "projects" / "myproject").mkdir(parents=True)
    (docs_root / "projects" / "myproject" / "project.yaml").write_text("repo-root: /no/such/path\n")
    result = CliRunner().invoke(
        main,
        [
            "bootstrap",
            "--docs-root",
            str(docs_root),
            "--project",
            "myproject",
            "--toolchain",
            "python",
            "--no-commit",
        ],
    )
    assert result.exit_code != 0
    assert "repo-root" in result.output
