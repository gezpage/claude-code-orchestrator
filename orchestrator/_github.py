"""Thin wrapper around the `gh` CLI for GitHub integration.

Keep this narrow — it exists to make GitHub side effects explicit and testable,
not to abstract over the gh subcommand surface. All functions are subprocess
calls; no GitHub REST or graphql traffic is initiated here.
"""

from __future__ import annotations

import re
import shutil
import subprocess


class GhError(RuntimeError):
    """Raised when a gh subcommand fails or its output is unexpected."""


_GITHUB_REMOTE_RE = re.compile(r"^(?:https?://github\.com/|git@github\.com:)([^/]+)/([^/]+?)(?:\.git)?/?$")


def parse_github_remote(url: str | None) -> str | None:
    """Return 'owner/name' if the URL is a GitHub remote, else None."""
    if not url:
        return None
    match = _GITHUB_REMOTE_RE.match(url.strip())
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def check_gh_available() -> None:
    """Raise GhError with an install hint if `gh` is not on PATH."""
    if shutil.which("gh") is None:
        raise GhError(
            "GitHub CLI ('gh') was not found on PATH. Install it from https://cli.github.com/ "
            "or rerun with --no-create-pr to skip PR creation."
        )


def check_gh_authed() -> None:
    """Raise GhError if `gh auth status` indicates the user is not authenticated."""
    check_gh_available()
    result = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise GhError(f"GitHub CLI is not authenticated. Run `gh auth login` and try again. Details: {stderr}")


def create_repo(
    name: str,
    visibility: str,
    description: str,
    source_dir: str,
) -> str:
    """Create a new GitHub repo and wire it up as `origin` for the local source_dir.

    Returns the new origin URL on success. Raises GhError on any failure.
    """
    check_gh_available()
    if visibility not in ("public", "private"):
        raise GhError(f"visibility must be 'public' or 'private', got {visibility!r}")
    cmd = [
        "gh",
        "repo",
        "create",
        name,
        f"--{visibility}",
        "--source",
        source_dir,
        "--remote",
        "origin",
        "--push=false",
    ]
    if description:
        cmd.extend(["--description", description])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GhError(f"gh repo create failed: {(result.stderr or result.stdout).strip()}")
    # gh prints the new repo URL to stdout on success.
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not url:
        raise GhError("gh repo create succeeded but did not report the new URL")
    return url


def create_draft_pr(
    repo: str,
    base: str,
    head: str,
    title: str,
    body: str,
) -> str:
    """Open a draft PR via `gh pr create`. Returns the PR URL."""
    check_gh_available()
    cmd = [
        "gh",
        "pr",
        "create",
        "--repo",
        repo,
        "--base",
        base,
        "--head",
        head,
        "--title",
        title,
        "--body",
        body,
        "--draft",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GhError(f"gh pr create failed: {(result.stderr or result.stdout).strip()}")
    # gh prints the PR URL as the final line on stdout.
    out = result.stdout.strip()
    if not out:
        raise GhError("gh pr create succeeded but did not report a URL")
    return out.splitlines()[-1].strip()
