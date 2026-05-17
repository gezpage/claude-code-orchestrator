"""Typed recipe model and YAML loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DEFAULT_TIMEOUT_SECONDS = 600

_RECIPES_DIR = Path(__file__).parent / "recipes"


@dataclass(frozen=True)
class Command:
    id: str
    command: str
    required: bool = True
    if_script_exists: str | None = None
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class Recipe:
    toolchain: str
    priority: int
    # All entries in `markers` must be present in the repo for the recipe to match.
    # When `any_markers` is non-empty, at least one of its entries must also be present.
    # Ecosystems like Python where any of several files (`pyproject.toml`,
    # `requirements.txt`, `setup.py`, ...) signals project type use `any_markers`.
    markers: tuple[str, ...] = ()
    any_markers: tuple[str, ...] = ()
    commands: tuple[Command, ...] = ()
    probes: tuple[str, ...] = field(default_factory=tuple)


def _parse_command(raw: dict) -> Command:
    return Command(
        id=raw["id"],
        command=raw["command"],
        required=bool(raw.get("required", True)),
        if_script_exists=raw.get("if_script_exists"),
        timeout_seconds=int(raw.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)),
    )


def _parse_recipe(raw: dict) -> Recipe:
    if "toolchain" not in raw:
        raise ValueError("recipe missing required field 'toolchain'")
    if "priority" not in raw:
        raise ValueError(f"recipe '{raw['toolchain']}' missing required field 'priority'")
    markers = raw.get("markers") or []
    any_markers = raw.get("any_markers") or []
    if not markers and not any_markers:
        raise ValueError(f"recipe '{raw['toolchain']}' must declare at least one marker or any_markers entry")
    return Recipe(
        toolchain=raw["toolchain"],
        priority=int(raw["priority"]),
        markers=tuple(markers),
        any_markers=tuple(any_markers),
        commands=tuple(_parse_command(c) for c in raw.get("commands", [])),
        probes=tuple(raw.get("probes", [])),
    )


def load_bundled_recipes(recipes_dir: Path | None = None) -> list[Recipe]:
    """Load every YAML recipe shipped with the orchestrator."""
    directory = recipes_dir or _RECIPES_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"recipes directory not found: {directory}")
    recipes = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text()) or {}
        recipes.append(_parse_recipe(raw))
    return recipes


def load_recipe_by_toolchain(name: str, recipes_dir: Path | None = None) -> Recipe:
    """Load a single recipe by toolchain name (filename stem)."""
    directory = recipes_dir or _RECIPES_DIR
    path = directory / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(p.stem for p in sorted(directory.glob("*.yaml")))
        raise FileNotFoundError(f"unknown toolchain '{name}'. Available: {available}")
    raw = yaml.safe_load(path.read_text()) or {}
    return _parse_recipe(raw)
