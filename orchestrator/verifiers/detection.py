"""Toolchain detection from marker files; highest-priority match wins."""

from __future__ import annotations

from pathlib import Path

from orchestrator.verifiers.recipe import Recipe

_GLOB_CHARS = frozenset("*?[")


def detect_toolchain(repo_root: Path, recipes: list[Recipe]) -> Recipe | None:
    """Return the highest-priority recipe whose markers exist in repo_root, or None."""
    matches = [r for r in recipes if markers_satisfied(repo_root, r)]
    if not matches:
        return None
    matches.sort(key=lambda r: (-r.priority, r.toolchain))
    return matches[0]


def markers_satisfied(repo_root: Path, recipe: Recipe) -> bool:
    """Every `markers` entry must be present; if `any_markers` is non-empty, at least one of its entries must also be present."""
    if not all(_marker_present(repo_root, m) for m in recipe.markers):
        return False
    if recipe.any_markers and not any(_marker_present(repo_root, m) for m in recipe.any_markers):
        return False
    return True


def _marker_present(repo_root: Path, marker: str) -> bool:
    """Entries containing glob characters (`*`, `?`, `[`) are matched with `Path.glob`; literal entries fall through to `.exists()`."""
    if any(ch in marker for ch in _GLOB_CHARS):
        return next(iter(repo_root.glob(marker)), None) is not None
    return (repo_root / marker).exists()


def all_markers_present(repo_root: Path, recipe: Recipe) -> bool:
    return markers_satisfied(repo_root, recipe)
