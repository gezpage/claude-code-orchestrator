"""Unit tests for orchestrator._git state helpers."""

from unittest.mock import MagicMock, patch

import pytest

from orchestrator import _git as git_state
from orchestrator._git import GitStateError


def _proc(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_is_clean_true_when_porcelain_empty():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout="")):
        assert git_state.is_clean("/repo") is True


def test_is_clean_false_when_porcelain_has_modified_paths():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout=" M file.py\n")):
        assert git_state.is_clean("/repo") is False


def test_is_clean_raises_when_status_fails():
    with patch(
        "orchestrator._git.subprocess.run",
        return_value=_proc(returncode=128, stderr="not a git repo"),
    ):
        with pytest.raises(GitStateError):
            git_state.is_clean("/repo")


def test_current_branch_returns_stripped_name():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout="main\n")):
        assert git_state.current_branch("/repo") == "main"


def test_branch_exists_true_on_zero_returncode():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(returncode=0)):
        assert git_state.branch_exists("/repo", "feat/x") is True


def test_branch_exists_false_on_nonzero_returncode():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(returncode=1)):
        assert git_state.branch_exists("/repo", "feat/x") is False


def test_worktree_registered_matches_path():
    porcelain = "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\nworktree /tmp/wt-a\n"
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout=porcelain)):
        assert git_state.worktree_registered("/repo", "/tmp/wt-a") is True


def test_worktree_registered_false_for_unknown_path():
    porcelain = "worktree /repo\nHEAD abc\nbranch refs/heads/main\n"
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout=porcelain)):
        assert git_state.worktree_registered("/repo", "/tmp/missing") is False


def test_list_worktrees_parses_porcelain_entries():
    porcelain = (
        "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n"
        "worktree /tmp/wt-a\nHEAD def\nbranch refs/heads/feat/x-impl_1\n\n"
        "worktree /tmp/wt-detached\nHEAD ghi\ndetached\n"
    )
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout=porcelain)):
        entries = git_state.list_worktrees("/repo")
    assert entries == [
        {"path": "/repo", "branch": "main"},
        {"path": "/tmp/wt-a", "branch": "feat/x-impl_1"},
        {"path": "/tmp/wt-detached", "branch": None},
    ]


def test_list_worktrees_returns_empty_on_git_failure():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(returncode=1, stderr="boom")):
        assert git_state.list_worktrees("/repo") == []


def test_worktree_for_branch_returns_path_when_held():
    porcelain = (
        "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n"
        "worktree /tmp/wt-a\nHEAD def\nbranch refs/heads/feat/x-impl_1\n"
    )
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout=porcelain)):
        assert git_state.worktree_for_branch("/repo", "feat/x-impl_1") == "/tmp/wt-a"


def test_worktree_for_branch_returns_none_when_not_held():
    porcelain = "worktree /repo\nHEAD abc\nbranch refs/heads/main\n"
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout=porcelain)):
        assert git_state.worktree_for_branch("/repo", "feat/x-impl_1") is None


def test_has_merge_conflicts_true_on_UU_marker():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout="UU conflict.py\n")):
        assert git_state.has_merge_conflicts("/repo") is True


def test_has_merge_conflicts_false_on_normal_modified():
    with patch("orchestrator._git.subprocess.run", return_value=_proc(stdout=" M file.py\n")):
        assert git_state.has_merge_conflicts("/repo") is False


def test_abort_merge_invokes_git_merge_abort():
    with patch("orchestrator._git.subprocess.run", return_value=_proc()) as mock_run:
        git_state.abort_merge("/repo")
    args = mock_run.call_args[0][0]
    assert args[:3] == ["git", "-C", "/repo"]
    assert "merge" in args and "--abort" in args
