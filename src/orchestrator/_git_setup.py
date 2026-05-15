"""Pre-flight resolution of git + GitHub state.

Centralises the checks that used to live inline in run_pipeline() plus the new
prompts for base branch and PR creation. See ADR-019.

Public surface:
- OriginInfo, GitPreflightResult dataclasses
- preflight(...) — one entrypoint, returns a GitPreflightResult or raises
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from orchestrator import _git, _github, _prompts


class PreflightError(RuntimeError):
    """Raised when pre-flight cannot complete (bad repo, missing required input in non-TTY)."""


@dataclass
class OriginInfo:
    url: str | None
    is_github: bool
    gh_repo: str | None  # "owner/name" if is_github else None


@dataclass
class GitPreflightResult:
    base_branch: str
    create_pr: bool
    origin: OriginInfo


_DEFAULT_BASE_BRANCH = "main"


def _validate_repo(repo_root: str) -> None:
    if not Path(repo_root).exists():
        raise PreflightError(f"project.yaml repo-root does not exist: {repo_root}")
    result = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "--git-dir"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise PreflightError(
            f"repo-root is not a git repository: {repo_root}\n"
            f"  Ensure the path in project.yaml 'repo-root' points to the root of a git repository."
        )


def _inspect_origin(repo_root: str) -> OriginInfo:
    url = _git.get_remote_url(repo_root, "origin")
    gh_repo = _github.parse_github_remote(url)
    return OriginInfo(url=url, is_github=gh_repo is not None, gh_repo=gh_repo)


def _resolve_base_branch(
    flag_value: str | None,
    persisted_value: str | None,
    is_tty: bool,
) -> str:
    if flag_value:
        return flag_value
    if persisted_value:
        return persisted_value
    if is_tty:
        return (
            _prompts.ask_text(
                "Which branch should the implementation be based off?",
                default=_DEFAULT_BASE_BRANCH,
            )
            or _DEFAULT_BASE_BRANCH
        )
    return _DEFAULT_BASE_BRANCH


def _resolve_create_pr(
    flag_value: bool | None,
    persisted_value: bool | None,
    origin_is_github: bool,
    is_tty: bool,
) -> bool:
    if flag_value is not None:
        return flag_value
    if not origin_is_github and not is_tty:
        return False
    if persisted_value is not None and origin_is_github:
        return persisted_value
    if not is_tty:
        return False
    if origin_is_github:
        default = persisted_value if persisted_value is not None else True
        return _prompts.ask_confirm(
            "Open a draft PR on completion?",
            default=default,
        )
    return False


def _offer_origin_setup(repo_root: str, is_tty: bool) -> OriginInfo:
    """When origin is missing or non-GitHub and the user wants PRs, prompt for setup.

    Returns the (possibly updated) OriginInfo. The caller decides whether to honour
    the result — for example, if the user chose 'skip', the new OriginInfo is the
    same as before and create_pr will be flipped to False.
    """
    info = _inspect_origin(repo_root)
    if info.is_github:
        return info
    if not is_tty:
        return info

    label = "origin is not a GitHub repository" if info.url else "this repo has no 'origin' remote"
    print(f"\n[orchestrator] PR creation requested but {label}.")  # noqa: T201
    choice = _prompts.ask_select(
        "How would you like to proceed?",
        choices=[
            "Link an existing GitHub repository (provide URL)",
            "Create a new GitHub repository via gh repo create",
            "Continue without the draft PR feature",
        ],
    )

    if choice.startswith("Link an existing"):
        url = _prompts.ask_text(
            "GitHub repository URL (https or ssh)",
            validate=lambda v: True if _github.parse_github_remote(v) else "Not a recognised GitHub URL",
        )
        if info.url is None:
            _git.remote_add(repo_root, "origin", url)
        else:
            if _prompts.ask_confirm(
                f"Replace existing origin URL ({info.url}) with the new one?",
                default=False,
            ):
                _git.remote_set_url(repo_root, "origin", url)
            else:
                return info
        return _inspect_origin(repo_root)

    if choice.startswith("Create a new"):
        default_name = Path(repo_root).name
        name = _prompts.ask_text("New repository name", default=default_name)
        visibility = _prompts.ask_select(
            "Visibility",
            choices=["private", "public"],
            default="private",
        )
        description = _prompts.ask_text("Description (optional)", default="")
        try:
            _github.create_repo(name, visibility, description, repo_root)
        except _github.GhError as exc:
            print(f"\n[orchestrator] gh repo create failed: {exc}")  # noqa: T201
            return info
        return _inspect_origin(repo_root)

    # "Continue without the draft PR feature"
    return info


def _persist_project_config(
    docs_root: str,
    project: str,
    base_branch: str,
    create_pr: bool,
) -> None:
    """Write base-branch and create-pr back to project.yaml if not already present.

    Existing values are never clobbered silently — they were the source of truth for
    this run's defaults, so they are already correct.
    """
    config_path = Path(docs_root) / "projects" / project / "project.yaml"
    if not config_path.exists():
        return
    raw = yaml.safe_load(config_path.read_text()) or {}
    changed = False
    if "base-branch" not in raw:
        raw["base-branch"] = base_branch
        changed = True
    if "create-pr" not in raw:
        raw["create-pr"] = create_pr
        changed = True
    if changed:
        config_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))


def preflight(
    docs_root: str,
    project: str,
    repo_root: str,
    project_config: dict,
    flag_base_branch: str | None = None,
    flag_create_pr: bool | None = None,
    is_tty: bool | None = None,
) -> GitPreflightResult:
    """Validate git state, resolve base branch + PR preference, validate gh if needed.

    Raises PreflightError on unrecoverable problems. Prompts the user when running
    on a TTY and a value is missing from both flags and project.yaml.
    """
    if is_tty is None:
        is_tty = _prompts.is_interactive()

    _validate_repo(repo_root)

    persisted_base = project_config.get("base-branch")
    persisted_create_pr = project_config.get("create-pr")

    base_branch = _resolve_base_branch(flag_base_branch, persisted_base, is_tty)

    origin = _inspect_origin(repo_root)
    create_pr = _resolve_create_pr(flag_create_pr, persisted_create_pr, origin.is_github, is_tty)

    if create_pr and not origin.is_github:
        origin = _offer_origin_setup(repo_root, is_tty)
        if not origin.is_github:
            print("[orchestrator] Continuing without PR creation.")  # noqa: T201
            create_pr = False

    if create_pr and origin.is_github:
        try:
            _github.check_gh_authed()
        except _github.GhError as exc:
            if is_tty and _prompts.ask_confirm(
                f"\n{exc}\nContinue without PR creation?",
                default=True,
            ):
                create_pr = False
            else:
                raise PreflightError(str(exc)) from exc

    _persist_project_config(docs_root, project, base_branch, create_pr)

    return GitPreflightResult(base_branch=base_branch, create_pr=create_pr, origin=origin)


def fail_with_message(message: str) -> None:
    """Print a structured error and exit non-zero. Used by callers that don't want to handle the exception."""
    sys.exit(f"[orchestrator] [ERROR] {message}")
