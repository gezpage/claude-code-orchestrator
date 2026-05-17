"""Toolchain detection from marker files; highest-priority match wins."""

from __future__ import annotations

from pathlib import Path

from orchestrator.verifiers.recipe import Recipe


def detect_toolchain(repo_root: Path, recipes: list[Recipe]) -> Recipe | None:
    """Return the highest-priority recipe whose markers exist in repo_root, or None."""
    matches = [r for r in recipes if markers_satisfied(repo_root, r)]
    if not matches:
        return None
    matches.sort(key=lambda r: (-r.priority, r.toolchain))
    return matches[0]


def markers_satisfied(repo_root: Path, recipe: Recipe) -> bool:
    """Every `markers` entry must be present; if `any_markers` is non-empty, at least one of its entries must also be present."""
    if not all((repo_root / marker).exists() for marker in recipe.markers):
        return False
    if recipe.any_markers and not any((repo_root / marker).exists() for marker in recipe.any_markers):
        return False
    return True


def all_markers_present(repo_root: Path, recipe: Recipe) -> bool:
    return markers_satisfied(repo_root, recipe)
