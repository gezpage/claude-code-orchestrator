"""Git state validation helpers.

Surgical wrappers around `git` subprocess calls used to pre-check repository
state before destructive operations (branch creation, worktree add, merge).
Keep this module narrow — it exists to make orchestrator failures explicit and
structured, not to abstract over git.
"""

from __future__ import annotations

import subprocess


class GitStateError(RuntimeError):
    """Raised when the orchestrator detects unexpected or unsafe git state."""


def _run(repo_root: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True,
        text=True,
    )


def is_clean(repo_root: str) -> bool:
    """True iff the working tree has no staged, unstaged, or untracked changes."""
    r = _run(repo_root, "status", "--porcelain")
    if r.returncode != 0:
        raise GitStateError(f"git status failed in {repo_root}: {r.stderr.strip()}")
    return r.stdout.strip() == ""


def current_branch(repo_root: str) -> str:
    r = _run(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if r.returncode != 0:
        raise GitStateError(f"git rev-parse failed in {repo_root}: {r.stderr.strip()}")
    return r.stdout.strip()


def branch_exists(repo_root: str, branch: str) -> bool:
    r = _run(repo_root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return r.returncode == 0


def worktree_registered(repo_root: str, path: str) -> bool:
    """True iff `path` appears in `git worktree list` for the given repo."""
    r = _run(repo_root, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        return False
    target = str(path).rstrip("/")
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            if line[len("worktree ") :].rstrip("/") == target:
                return True
    return False


def has_merge_conflicts(repo_root: str) -> bool:
    """True iff `git status --porcelain` shows any unmerged paths."""
    r = _run(repo_root, "status", "--porcelain")
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if not line:
            continue
        xy = line[:2]
        # Unmerged paths: any "U" in XY, plus AA/DD per git-status(1).
        if "U" in xy or xy in ("AA", "DD"):
            return True
    return False


def abort_merge(repo_root: str) -> None:
    """Best-effort `git merge --abort`. Silent on failure — caller has already failed."""
    _run(repo_root, "merge", "--abort")
