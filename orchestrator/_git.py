"""Git state validation helpers.

Surgical wrappers around `git` subprocess calls used to pre-check repository
state before destructive operations (branch creation, worktree add, merge).
Keep this module narrow — it exists to make orchestrator failures explicit and
structured, not to abstract over git.
"""

from __future__ import annotations

import subprocess
from typing import TypedDict


class GitStateError(RuntimeError):
    """Raised when the orchestrator detects unexpected or unsafe git state."""


class WorktreeEntry(TypedDict):
    path: str
    branch: str | None


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


def list_worktrees(repo_root: str) -> list[WorktreeEntry]:
    """Parsed `git worktree list --porcelain`. Returns [{"path", "branch"}, ...].

    `branch` is the short branch name (`refs/heads/X` → `X`) or `None` for
    detached HEADs. Returns an empty list if git fails — callers treat
    "couldn't ask git" the same as "registry empty" for cleanup purposes.
    """
    r = _run(repo_root, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        return []
    worktrees: list[WorktreeEntry] = []
    current: WorktreeEntry | None = None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = {"path": line[len("worktree ") :].rstrip("/"), "branch": None}
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch ") :]
            current["branch"] = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
        elif line == "" and current is not None:
            worktrees.append(current)
            current = None
    if current is not None:
        worktrees.append(current)
    return worktrees


def worktree_registered(repo_root: str, path: str) -> bool:
    """True iff `path` appears in `git worktree list` for the given repo."""
    target = str(path).rstrip("/")
    return any(wt["path"] == target for wt in list_worktrees(repo_root))


def worktree_for_branch(repo_root: str, branch: str) -> str | None:
    """Path of the worktree currently checked out on `branch`, or None."""
    for wt in list_worktrees(repo_root):
        if wt["branch"] == branch:
            return wt["path"]
    return None


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


def get_remote_url(repo_root: str, remote: str = "origin") -> str | None:
    """Return the URL of the named remote, or None if it does not exist."""
    r = _run(repo_root, "remote", "get-url", remote)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def remote_add(repo_root: str, remote: str, url: str) -> None:
    r = _run(repo_root, "remote", "add", remote, url)
    if r.returncode != 0:
        raise GitStateError(f"git remote add {remote} failed: {r.stderr.strip()}")


def remote_set_url(repo_root: str, remote: str, url: str) -> None:
    r = _run(repo_root, "remote", "set-url", remote, url)
    if r.returncode != 0:
        raise GitStateError(f"git remote set-url {remote} failed: {r.stderr.strip()}")


def fetch(repo_root: str, remote: str = "origin") -> None:
    r = _run(repo_root, "fetch", remote)
    if r.returncode != 0:
        raise GitStateError(f"git fetch {remote} failed: {r.stderr.strip()}")


def checkout(repo_root: str, branch: str) -> None:
    r = _run(repo_root, "checkout", branch)
    if r.returncode != 0:
        raise GitStateError(f"git checkout {branch} failed: {r.stderr.strip()}")


def pull_ff_only(repo_root: str, branch: str, remote: str = "origin") -> None:
    """`git pull --ff-only <remote> <branch>`. Raises on conflict or non-FF.

    Silently no-ops if the remote does not know the branch — a brand-new repo
    with a single local branch is a valid state and should not block the pipeline.
    """
    r = _run(repo_root, "pull", "--ff-only", remote, branch)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").lower()
        if "couldn't find remote ref" in err or "no such ref" in err:
            return
        raise GitStateError(f"git pull --ff-only {remote} {branch} failed: {r.stderr.strip()}")


def push_branch(
    repo_root: str,
    branch: str,
    remote: str = "origin",
    set_upstream: bool = True,
) -> None:
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args.extend([remote, branch])
    r = _run(repo_root, *args)
    if r.returncode != 0:
        raise GitStateError(f"git push {remote} {branch} failed: {r.stderr.strip()}")
