"""Tests for orchestrator._github (thin wrapper around gh CLI)."""

from unittest.mock import MagicMock, patch

import pytest

from orchestrator import _github


def test_parse_github_remote_https():
    assert _github.parse_github_remote("https://github.com/owner/name.git") == "owner/name"
    assert _github.parse_github_remote("https://github.com/owner/name") == "owner/name"
    assert _github.parse_github_remote("https://github.com/owner/name/") == "owner/name"


def test_parse_github_remote_ssh():
    assert _github.parse_github_remote("git@github.com:owner/name.git") == "owner/name"
    assert _github.parse_github_remote("git@github.com:owner/name") == "owner/name"


def test_parse_github_remote_other_hosts_return_none():
    assert _github.parse_github_remote("https://gitlab.com/owner/name.git") is None
    assert _github.parse_github_remote("git@gitlab.com:owner/name.git") is None
    assert _github.parse_github_remote(None) is None
    assert _github.parse_github_remote("") is None


def test_check_gh_available_raises_when_missing():
    with patch("orchestrator._github.shutil.which", return_value=None):
        with pytest.raises(_github.GhError, match="gh"):
            _github.check_gh_available()


def test_check_gh_authed_raises_on_nonzero():
    fake = MagicMock(returncode=1, stderr="not logged in", stdout="")
    with (
        patch("orchestrator._github.shutil.which", return_value="/usr/local/bin/gh"),
        patch("orchestrator._github.subprocess.run", return_value=fake),
    ):
        with pytest.raises(_github.GhError, match="not authenticated"):
            _github.check_gh_authed()


def test_create_repo_invokes_gh_with_visibility_and_description():
    fake = MagicMock(returncode=0, stdout="https://github.com/me/new-repo\n", stderr="")
    with (
        patch("orchestrator._github.shutil.which", return_value="/usr/local/bin/gh"),
        patch("orchestrator._github.subprocess.run", return_value=fake) as mock_run,
    ):
        url = _github.create_repo("new-repo", "private", "Some description", "/tmp/src")
    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["gh", "repo", "create", "new-repo"]
    assert "--private" in cmd
    assert "--source" in cmd and cmd[cmd.index("--source") + 1] == "/tmp/src"
    assert "--remote" in cmd and cmd[cmd.index("--remote") + 1] == "origin"
    assert "--description" in cmd and cmd[cmd.index("--description") + 1] == "Some description"
    assert url == "https://github.com/me/new-repo"


def test_create_repo_rejects_bad_visibility():
    with patch("orchestrator._github.shutil.which", return_value="/usr/local/bin/gh"):
        with pytest.raises(_github.GhError, match="visibility"):
            _github.create_repo("name", "unlisted", "", "/tmp")


def test_create_draft_pr_returns_url():
    fake = MagicMock(returncode=0, stdout="https://github.com/me/repo/pull/42\n", stderr="")
    with (
        patch("orchestrator._github.shutil.which", return_value="/usr/local/bin/gh"),
        patch("orchestrator._github.subprocess.run", return_value=fake) as mock_run,
    ):
        url = _github.create_draft_pr("me/repo", "main", "feat/x", "Title", "Body")
    cmd = mock_run.call_args.args[0]
    assert "--draft" in cmd
    assert "--base" in cmd and cmd[cmd.index("--base") + 1] == "main"
    assert "--head" in cmd and cmd[cmd.index("--head") + 1] == "feat/x"
    assert "--title" in cmd and cmd[cmd.index("--title") + 1] == "Title"
    assert "--body" in cmd and cmd[cmd.index("--body") + 1] == "Body"
    assert url == "https://github.com/me/repo/pull/42"


def test_create_draft_pr_raises_on_failure():
    fake = MagicMock(returncode=1, stdout="", stderr="auth required")
    with (
        patch("orchestrator._github.shutil.which", return_value="/usr/local/bin/gh"),
        patch("orchestrator._github.subprocess.run", return_value=fake),
    ):
        with pytest.raises(_github.GhError, match="auth required"):
            _github.create_draft_pr("me/repo", "main", "feat/x", "t", "b")
