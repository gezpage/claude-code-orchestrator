"""Tests for the bootstrap module — templates, plan, apply, commit, detection."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from orchestrator import bootstrap, orchestrate

# ── plan_bootstrap ──────────────────────────────────────────────────────────


def test_plan_python_empty_repo_marks_all_new(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "python")
    assert plan.toolchain == "python"
    paths = {f.path.name for f in plan.new_files}
    assert paths == {".cco.yaml", "pyproject.toml"}
    assert plan.conflicts == []
    assert plan.already_present == []


def test_plan_typescript_includes_tsconfig(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "typescript")
    names = sorted(f.path.name for f in plan.new_files)
    assert names == [".cco.yaml", "package.json", "tsconfig.json"]


def test_plan_all_supported_toolchains_produce_cco_yaml(tmp_path):
    for toolchain in bootstrap.SUPPORTED_TOOLCHAINS:
        repo = tmp_path / toolchain
        repo.mkdir()
        plan = bootstrap.plan_bootstrap(repo, toolchain)
        names = [f.path.name for f in plan.new_files]
        assert ".cco.yaml" in names, f"{toolchain} missing .cco.yaml"


def test_plan_unknown_toolchain_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown toolchain"):
        bootstrap.plan_bootstrap(tmp_path, "cobol")


def test_plan_missing_repo_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bootstrap.plan_bootstrap(tmp_path / "missing", "python")


def test_plan_marks_existing_identical_file_as_already_present(tmp_path):
    # Pre-populate .cco.yaml with the exact template contents.
    plan_initial = bootstrap.plan_bootstrap(tmp_path, "python")
    cco_change = next(f for f in plan_initial.new_files if f.path.name == ".cco.yaml")
    cco_change.path.write_text(cco_change.contents)

    plan = bootstrap.plan_bootstrap(tmp_path, "python")
    already = [f.path.name for f in plan.already_present]
    assert ".cco.yaml" in already
    assert plan.conflicts == []


def test_plan_marks_diverging_file_as_conflict(tmp_path):
    (tmp_path / ".cco.yaml").write_text("verification:\n  toolchain: rust\n")
    plan = bootstrap.plan_bootstrap(tmp_path, "python")
    conflict_names = [f.path.name for f in plan.conflicts]
    assert conflict_names == [".cco.yaml"]


# ── apply_plan ──────────────────────────────────────────────────────────────


def test_apply_writes_new_files(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "python")
    written = bootstrap.apply_plan(plan)
    names = sorted(p.name for p in written)
    assert names == [".cco.yaml", "pyproject.toml"]
    cco = (tmp_path / ".cco.yaml").read_text()
    assert "toolchain: python" in cco
    assert "python -m pytest" in cco


def test_apply_refuses_to_overwrite_without_force(tmp_path):
    (tmp_path / ".cco.yaml").write_text("verification:\n  toolchain: rust\n")
    plan = bootstrap.plan_bootstrap(tmp_path, "python")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        bootstrap.apply_plan(plan)
    # File contents unchanged.
    assert "rust" in (tmp_path / ".cco.yaml").read_text()


def test_apply_force_overwrites(tmp_path):
    (tmp_path / ".cco.yaml").write_text("verification:\n  toolchain: rust\n")
    plan = bootstrap.plan_bootstrap(tmp_path, "python")
    written = bootstrap.apply_plan(plan, force=True)
    assert (tmp_path / ".cco.yaml") in written
    assert "toolchain: python" in (tmp_path / ".cco.yaml").read_text()


def test_apply_skips_files_already_matching(tmp_path):
    # First apply.
    plan1 = bootstrap.plan_bootstrap(tmp_path, "python")
    written1 = bootstrap.apply_plan(plan1)
    assert len(written1) == 2
    # Second apply with no changes — should write nothing.
    plan2 = bootstrap.plan_bootstrap(tmp_path, "python")
    written2 = bootstrap.apply_plan(plan2)
    assert written2 == []


# ── update_project_standards ────────────────────────────────────────────────


def test_update_project_standards_appends_when_missing(tmp_path):
    project_yaml = tmp_path / "project.yaml"
    project_yaml.write_text("repo-root: /tmp/repo\nbase-branch: main\n")
    changed = bootstrap.update_project_standards(project_yaml, "python")
    assert changed is True
    data = yaml.safe_load(project_yaml.read_text())
    assert data["standards"] == ["python"]


def test_update_project_standards_adds_to_existing_list(tmp_path):
    project_yaml = tmp_path / "project.yaml"
    project_yaml.write_text("repo-root: /tmp/repo\nstandards:\n  - go\n")
    changed = bootstrap.update_project_standards(project_yaml, "python")
    assert changed is True
    data = yaml.safe_load(project_yaml.read_text())
    assert data["standards"] == ["go", "python"]


def test_update_project_standards_skips_when_already_present(tmp_path):
    project_yaml = tmp_path / "project.yaml"
    project_yaml.write_text("repo-root: /tmp/repo\nstandards:\n  - python\n")
    changed = bootstrap.update_project_standards(project_yaml, "python")
    assert changed is False


def test_update_project_standards_skips_php(tmp_path):
    # PHP is intentionally absent from STANDARDS_FOR_TOOLCHAIN today.
    project_yaml = tmp_path / "project.yaml"
    project_yaml.write_text("repo-root: /tmp/repo\n")
    changed = bootstrap.update_project_standards(project_yaml, "php")
    assert changed is False
    assert "standards" not in yaml.safe_load(project_yaml.read_text())


def test_update_project_standards_maps_node_to_nodejs(tmp_path):
    project_yaml = tmp_path / "project.yaml"
    project_yaml.write_text("repo-root: /tmp/repo\n")
    changed = bootstrap.update_project_standards(project_yaml, "node")
    assert changed is True
    data = yaml.safe_load(project_yaml.read_text())
    assert data["standards"] == ["nodejs"]


# ── commit_changes ──────────────────────────────────────────────────────────


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def test_commit_changes_writes_expected_message(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    plan = bootstrap.plan_bootstrap(repo, "python")
    written = bootstrap.apply_plan(plan)
    sha = bootstrap.commit_changes(repo, written)
    assert sha  # non-empty short SHA
    msg = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert msg == "chore: bootstrap orchestrator project config"


def test_commit_changes_rejects_empty_list(tmp_path):
    with pytest.raises(ValueError, match="empty paths_to_stage"):
        bootstrap.commit_changes(tmp_path, [])


# ── looks_unbootstrapped ────────────────────────────────────────────────────


def test_looks_unbootstrapped_true_for_empty_repo(tmp_path):
    assert bootstrap.looks_unbootstrapped(tmp_path) is True


def test_looks_unbootstrapped_false_when_cco_yaml_present(tmp_path):
    (tmp_path / ".cco.yaml").write_text("verification:\n  toolchain: python\n")
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


def test_looks_unbootstrapped_false_when_recipe_detects(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'\n")
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


def test_looks_unbootstrapped_false_when_go_mod_present(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/x\n")
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


# ── round-trip: plan + apply makes the recipe selectable ────────────────────


def test_bootstrap_python_makes_recipe_detect(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "python")
    bootstrap.apply_plan(plan)
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


def test_bootstrap_node_makes_recipe_detect(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "node")
    bootstrap.apply_plan(plan)
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


def test_bootstrap_php_makes_recipe_detect(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "php")
    bootstrap.apply_plan(plan)
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


def test_bootstrap_java_makes_recipe_detect(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "java")
    bootstrap.apply_plan(plan)
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


def test_bootstrap_go_makes_recipe_detect(tmp_path):
    plan = bootstrap.plan_bootstrap(tmp_path, "go")
    bootstrap.apply_plan(plan)
    assert bootstrap.looks_unbootstrapped(tmp_path) is False


# ── _maybe_warn_unbootstrapped ───────────────────────────────────────────────


def test_maybe_warn_unbootstrapped_silent_when_recipe_matches(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n")
    orchestrate._maybe_warn_unbootstrapped(
        docs_root=str(tmp_path), project="myproject", repo_root=str(tmp_path), resume=False
    )
    captured = capsys.readouterr()
    assert "[WARN]" not in captured.out


def test_maybe_warn_unbootstrapped_warns_on_empty_repo(tmp_path, capsys):
    # Non-TTY: just emits the warning and the bootstrap hint, never prompts.
    with patch("orchestrator._prompts.is_interactive", return_value=False):
        orchestrate._maybe_warn_unbootstrapped(
            docs_root="/tmp/docs",
            project="myproject",
            repo_root=str(tmp_path),
            resume=False,
        )
    captured = capsys.readouterr()
    assert "[WARN]" in captured.out
    assert "not appear to be bootstrapped" in captured.out
    assert "orchestrator bootstrap" in captured.out


def test_maybe_warn_unbootstrapped_silent_on_resume(tmp_path, capsys):
    # Empty repo — would normally warn — but resume=True suppresses the check.
    with patch("orchestrator._prompts.is_interactive", return_value=False):
        orchestrate._maybe_warn_unbootstrapped(
            docs_root="/tmp/docs",
            project="myproject",
            repo_root=str(tmp_path),
            resume=True,
        )
    captured = capsys.readouterr()
    assert captured.out == ""


def _bootstrap_inline_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Build a docs-root + an empty repo that triggers the unbootstrapped warning."""
    docs_root = tmp_path / "docs"
    project_dir = docs_root / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text("repo-root: /unused\n")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    return docs_root, repo_root


def test_maybe_warn_unbootstrapped_inline_decline_commit_exits_cleanly(tmp_path, capsys):
    # When inline bootstrap writes files and the user declines to commit, we must
    # stop the run cleanly so the downstream base-branch sync does not fail on a
    # dirty tree (see PR #184 review).
    docs_root, repo_root = _bootstrap_inline_inputs(tmp_path)
    with (
        patch("orchestrator._prompts.is_interactive", return_value=True),
        patch("orchestrator._prompts.ask_confirm", side_effect=[True, False]),
        patch("orchestrator._prompts.ask_select", return_value="python"),
        pytest.raises(SystemExit) as exc,
    ):
        orchestrate._maybe_warn_unbootstrapped(
            docs_root=str(docs_root),
            project="myproject",
            repo_root=str(repo_root),
            resume=False,
        )
    captured = capsys.readouterr()
    assert "working tree is now dirty" in str(exc.value)
    assert "Commit or stash" in str(exc.value)
    # Bootstrap files should still be on disk so the user can stash/commit them.
    assert (repo_root / ".cco.yaml").exists()
    assert "wrote" in captured.out


def test_maybe_warn_unbootstrapped_inline_commit_failure_exits_cleanly(tmp_path, capsys):
    # If the user agrees to commit but the commit fails (e.g. repo is not a git
    # repo), we still must not fall through to the base-branch sync.
    docs_root, repo_root = _bootstrap_inline_inputs(tmp_path)
    with (
        patch("orchestrator._prompts.is_interactive", return_value=True),
        patch("orchestrator._prompts.ask_confirm", side_effect=[True, True]),
        patch("orchestrator._prompts.ask_select", return_value="python"),
        patch(
            "orchestrator.bootstrap.commit_changes",
            side_effect=subprocess.CalledProcessError(128, ["git", "commit"]),
        ),
        pytest.raises(SystemExit) as exc,
    ):
        orchestrate._maybe_warn_unbootstrapped(
            docs_root=str(docs_root),
            project="myproject",
            repo_root=str(repo_root),
            resume=False,
        )
    captured = capsys.readouterr()
    assert "working tree is now dirty" in str(exc.value)
    assert "[WARN] commit failed" in captured.out
