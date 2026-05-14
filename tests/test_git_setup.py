"""Tests for orchestrator._git_setup preflight resolution."""

from unittest.mock import patch

import pytest
import yaml

from orchestrator import _git_setup
from orchestrator._git_setup import OriginInfo, PreflightError


def _make_repo(tmp_path):
    """Return a path that masquerades as a git repo for the validator."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _patch_validate_repo():
    return patch("orchestrator._git_setup._validate_repo")


def test_preflight_falls_back_to_main_in_non_tty(tmp_path):
    repo = _make_repo(tmp_path)
    docs = tmp_path / "docs"
    (docs / "projects" / "p").mkdir(parents=True)
    (docs / "projects" / "p" / "project.yaml").write_text("repo-root: ./repo\n")

    with (
        _patch_validate_repo(),
        patch(
            "orchestrator._git_setup._inspect_origin", return_value=OriginInfo(url=None, is_github=False, gh_repo=None)
        ),
    ):
        result = _git_setup.preflight(
            docs_root=str(docs),
            project="p",
            repo_root=str(repo),
            project_config={"repo-root": str(repo)},
            is_tty=False,
        )
    assert result.base_branch == "main"
    assert result.create_pr is False
    assert result.origin.is_github is False


def test_preflight_uses_persisted_values(tmp_path):
    repo = _make_repo(tmp_path)
    docs = tmp_path / "docs"
    project_dir = docs / "projects" / "p"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text("repo-root: ./repo\nbase-branch: develop\ncreate-pr: false\n")
    cfg = yaml.safe_load((project_dir / "project.yaml").read_text())

    with (
        _patch_validate_repo(),
        patch(
            "orchestrator._git_setup._inspect_origin",
            return_value=OriginInfo(url=None, is_github=False, gh_repo=None),
        ),
    ):
        result = _git_setup.preflight(
            docs_root=str(docs),
            project="p",
            repo_root=str(repo),
            project_config=cfg,
            is_tty=False,
        )
    assert result.base_branch == "develop"
    assert result.create_pr is False


def test_preflight_flag_overrides_persisted(tmp_path):
    repo = _make_repo(tmp_path)
    docs = tmp_path / "docs"
    (docs / "projects" / "p").mkdir(parents=True)
    (docs / "projects" / "p" / "project.yaml").write_text("repo-root: ./repo\nbase-branch: develop\n")
    cfg = {"repo-root": str(repo), "base-branch": "develop"}

    with (
        _patch_validate_repo(),
        patch(
            "orchestrator._git_setup._inspect_origin",
            return_value=OriginInfo(url=None, is_github=False, gh_repo=None),
        ),
    ):
        result = _git_setup.preflight(
            docs_root=str(docs),
            project="p",
            repo_root=str(repo),
            project_config=cfg,
            flag_base_branch="trunk",
            is_tty=False,
        )
    assert result.base_branch == "trunk"


def test_preflight_persists_defaults(tmp_path):
    repo = _make_repo(tmp_path)
    docs = tmp_path / "docs"
    project_dir = docs / "projects" / "p"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text("repo-root: ./repo\n")
    cfg = {"repo-root": str(repo)}

    with (
        _patch_validate_repo(),
        patch(
            "orchestrator._git_setup._inspect_origin",
            return_value=OriginInfo(url=None, is_github=False, gh_repo=None),
        ),
    ):
        _git_setup.preflight(
            docs_root=str(docs),
            project="p",
            repo_root=str(repo),
            project_config=cfg,
            is_tty=False,
        )
    written = yaml.safe_load((project_dir / "project.yaml").read_text())
    assert written["base-branch"] == "main"
    assert written["create-pr"] is False


def test_preflight_requires_pr_and_origin_disables_when_skipped(tmp_path):
    """If --create-pr is requested in non-TTY with no origin, fall back to false."""
    repo = _make_repo(tmp_path)
    docs = tmp_path / "docs"
    (docs / "projects" / "p").mkdir(parents=True)
    (docs / "projects" / "p" / "project.yaml").write_text("repo-root: ./repo\n")
    cfg = {"repo-root": str(repo)}

    with (
        _patch_validate_repo(),
        patch(
            "orchestrator._git_setup._inspect_origin",
            return_value=OriginInfo(url=None, is_github=False, gh_repo=None),
        ),
    ):
        result = _git_setup.preflight(
            docs_root=str(docs),
            project="p",
            repo_root=str(repo),
            project_config=cfg,
            flag_create_pr=True,
            is_tty=False,
        )
    # non-TTY can't run origin setup wizard, so create_pr is forced false.
    assert result.create_pr is False


def test_preflight_invalid_repo_raises():
    with pytest.raises(PreflightError, match="does not exist"):
        _git_setup.preflight(
            docs_root="/tmp",
            project="p",
            repo_root="/this/does/not/exist",
            project_config={"repo-root": "/this/does/not/exist"},
            is_tty=False,
        )


def test_preflight_existing_path_but_not_git_raises(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(PreflightError, match="not a git"):
        _git_setup.preflight(
            docs_root="/tmp",
            project="p",
            repo_root=str(not_a_repo),
            project_config={"repo-root": str(not_a_repo)},
            is_tty=False,
        )


def test_inspect_origin_github_url():
    with patch("orchestrator._git_setup._git.get_remote_url", return_value="git@github.com:me/r.git"):
        info = _git_setup._inspect_origin("/tmp")
    assert info.is_github is True
    assert info.gh_repo == "me/r"


def test_inspect_origin_non_github():
    with patch("orchestrator._git_setup._git.get_remote_url", return_value="https://gitlab.com/x/y.git"):
        info = _git_setup._inspect_origin("/tmp")
    assert info.is_github is False
    assert info.gh_repo is None


def test_inspect_origin_no_remote():
    with patch("orchestrator._git_setup._git.get_remote_url", return_value=None):
        info = _git_setup._inspect_origin("/tmp")
    assert info.url is None
    assert info.is_github is False


def test_preflight_with_github_origin_checks_gh_auth(tmp_path):
    repo = _make_repo(tmp_path)
    docs = tmp_path / "docs"
    (docs / "projects" / "p").mkdir(parents=True)
    (docs / "projects" / "p" / "project.yaml").write_text("repo-root: ./repo\ncreate-pr: true\n")

    with (
        _patch_validate_repo(),
        patch(
            "orchestrator._git_setup._inspect_origin",
            return_value=OriginInfo(url="git@github.com:me/r.git", is_github=True, gh_repo="me/r"),
        ),
        patch("orchestrator._git_setup._github.check_gh_authed") as mock_auth,
    ):
        result = _git_setup.preflight(
            docs_root=str(docs),
            project="p",
            repo_root=str(repo),
            project_config={"repo-root": str(repo), "create-pr": True},
            is_tty=False,
        )
    assert result.create_pr is True
    mock_auth.assert_called_once()
