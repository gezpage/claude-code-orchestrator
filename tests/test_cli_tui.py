"""Tests for orchestrator._cli_tui (run-input resolver)."""

from unittest.mock import patch

import pytest

from orchestrator import _cli_tui
from orchestrator._cli_tui import RunInputError, RunInputs


def test_resolve_passthrough_when_all_supplied(tmp_path):
    inputs = RunInputs(
        docs_root=str(tmp_path),
        project="p",
        feature_path="features/x",
        branch="feat/x",
        profile="full",
    )
    with patch("orchestrator._cli_tui._prompts.is_interactive", return_value=False):
        out = _cli_tui.resolve_run_inputs(inputs)
    assert out.docs_root == str(tmp_path)
    assert out.project == "p"
    assert out.feature_path == "features/x"
    assert out.branch == "feat/x"
    assert out.profile == "full"


def test_resolve_raises_on_missing_input_non_tty(tmp_path):
    inputs = RunInputs(docs_root=None, project="p", feature_path="x", branch="b")
    with patch("orchestrator._cli_tui._prompts.is_interactive", return_value=False):
        with pytest.raises(RunInputError, match="--docs-root"):
            _cli_tui.resolve_run_inputs(inputs)


def test_resolve_prompts_when_tty_missing(tmp_path):
    inputs = RunInputs(
        docs_root=str(tmp_path),
        project="p",
        feature_path="features/x",
        branch=None,
        profile="full",
    )
    with (
        patch("orchestrator._cli_tui._prompts.is_interactive", return_value=True),
        patch("orchestrator._cli_tui._prompts.ask_text", return_value="feat/x"),
    ):
        out = _cli_tui.resolve_run_inputs(inputs)
    assert out.branch == "feat/x"


def test_list_projects(tmp_path):
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "alpha").mkdir()
    (pdir / "alpha" / "project.yaml").write_text("")
    (pdir / "beta").mkdir()
    (pdir / "beta" / "project.yaml").write_text("")
    # not a project — no yaml
    (pdir / "gamma").mkdir()
    assert _cli_tui._list_projects(str(tmp_path)) == ["alpha", "beta"]


def test_find_feature_dirs_uses_features_subtree(tmp_path):
    project_root = tmp_path / "projects" / "p"
    features = project_root / "features"
    (features / "auth").mkdir(parents=True)
    (features / "auth" / "overview.md").write_text("# auth\n")
    (features / "billing").mkdir()
    (features / "billing" / "overview.md").write_text("# billing\n")
    found = _cli_tui._find_feature_dirs(str(tmp_path), "p")
    assert "projects/p/features/auth" in found
    assert "projects/p/features/billing" in found


def test_suggested_branch_uses_feature_slug():
    assert _cli_tui._suggested_branch("features/auth-refresh") == "feat/auth-refresh"
    assert _cli_tui._suggested_branch("Some Feature Name") == "feat/some-feature-name"
    assert _cli_tui._suggested_branch("") == "feat/change"


def test_resolve_prompts_for_project_when_none_listed(tmp_path):
    """No projects on disk → fall back to free-text rather than offering an empty list."""
    inputs = RunInputs(
        docs_root=str(tmp_path),
        project=None,
        feature_path="features/x",
        branch="feat/x",
        profile="full",
    )
    with (
        patch("orchestrator._cli_tui._prompts.is_interactive", return_value=True),
        patch("orchestrator._cli_tui._prompts.ask_text", return_value="myproject"),
    ):
        out = _cli_tui.resolve_run_inputs(inputs)
    assert out.project == "myproject"
