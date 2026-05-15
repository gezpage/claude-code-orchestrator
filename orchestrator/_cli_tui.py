"""Interactive resolution of `orchestrator run` inputs when flags are omitted.

When stdin is a TTY, any missing flag is prompted for via questionary. When not,
the resolver raises with a clear message naming the missing flags so CI runs
fail fast instead of hanging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from orchestrator import _prompts


class RunInputError(RuntimeError):
    """Raised when a required input is missing in a non-TTY environment."""


@dataclass
class RunInputs:
    docs_root: str | None = None
    project: str | None = None
    feature_path: str | None = None
    branch: str | None = None
    profile: str | None = None
    base_branch: str | None = None
    create_pr: bool | None = None


_BUNDLED_PROFILES = ("full", "full-interactive", "minimal", "minimal-codex", "spike")


def _missing(value: str | None) -> bool:
    return value is None or value == ""


def _list_projects(docs_root: str) -> list[str]:
    projects_dir = Path(docs_root) / "projects"
    if not projects_dir.is_dir():
        return []
    return sorted(p.name for p in projects_dir.iterdir() if p.is_dir() and (p / "project.yaml").is_file())


def _find_feature_dirs(docs_root: str, project: str) -> list[str]:
    """Return docs-relative paths of directories containing overview.md.

    Searches under projects/<project>/features/ first (the convention used in the
    README) and falls back to scanning the whole project tree if that directory
    is absent.
    """
    docs_root_p = Path(docs_root)
    candidates: list[Path] = []
    features_dir = docs_root_p / "projects" / project / "features"
    if features_dir.is_dir():
        candidates = sorted(features_dir.rglob("overview.md"))
    else:
        project_dir = docs_root_p / "projects" / project
        if project_dir.is_dir():
            candidates = sorted(project_dir.rglob("overview.md"))
    return [str(c.parent.relative_to(docs_root_p)).replace("\\", "/") for c in candidates]


def _list_profiles(docs_root: str, project: str) -> list[str]:
    project_profiles = Path(docs_root) / "projects" / project / "workflow" / "profiles"
    extras: list[str] = []
    if project_profiles.is_dir():
        extras = sorted(p.stem for p in project_profiles.glob("*.yaml"))
    seen = list(_BUNDLED_PROFILES)
    for name in extras:
        if name not in seen:
            seen.append(name)
    return seen


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _suggested_branch(feature_path: str) -> str:
    slug = _SLUG_RE.sub("-", Path(feature_path).stem.lower()).strip("-")
    if not slug:
        slug = "change"
    return f"feat/{slug}"


def resolve_run_inputs(inputs: RunInputs) -> RunInputs:
    """Fill in any missing fields via TUI prompts, or raise in non-TTY mode."""
    is_tty = _prompts.is_interactive()
    resolved = replace(inputs)

    missing = [
        name
        for name, value in [
            ("--docs-root", resolved.docs_root),
            ("--project", resolved.project),
            ("--feature-path", resolved.feature_path),
            ("--branch", resolved.branch),
        ]
        if _missing(value)
    ]
    if missing and not is_tty:
        raise RunInputError(
            "Missing required input(s): "
            + ", ".join(missing)
            + ". Pass them as CLI flags — interactive prompts are unavailable in non-TTY mode."
        )

    if _missing(resolved.docs_root):
        resolved.docs_root = _prompts.ask_path("Path to your docs root", must_exist=True)

    docs_root = str(resolved.docs_root)

    if _missing(resolved.project):
        existing = _list_projects(docs_root)
        if existing:
            choices = [*existing, "Enter a new name"]
            choice = _prompts.ask_select("Project", choices=choices, default=existing[0])
            if choice == "Enter a new name":
                resolved.project = _prompts.ask_text("New project name")
            else:
                resolved.project = choice
        else:
            resolved.project = _prompts.ask_text("Project name")

    project = str(resolved.project)

    if _missing(resolved.feature_path):
        existing_features = _find_feature_dirs(docs_root, project)
        if existing_features:
            choices = [*existing_features, "Enter a new path"]
            choice = _prompts.ask_select("Feature path", choices=choices, default=existing_features[0])
            if choice == "Enter a new path":
                resolved.feature_path = _prompts.ask_text("Feature path (docs-relative)")
            else:
                resolved.feature_path = choice
        else:
            resolved.feature_path = _prompts.ask_text("Feature path (docs-relative)")

    if _missing(resolved.branch):
        suggested = _suggested_branch(str(resolved.feature_path))
        resolved.branch = _prompts.ask_text("Implementation branch name", default=suggested)

    if _missing(resolved.profile):
        profiles = _list_profiles(docs_root, project)
        default = "full" if "full" in profiles else profiles[0]
        resolved.profile = _prompts.ask_select("Profile", choices=profiles, default=default)

    return resolved
