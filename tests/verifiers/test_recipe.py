from pathlib import Path

import pytest
import yaml

from orchestrator.verifiers.recipe import (
    _DEFAULT_TIMEOUT_SECONDS,
    load_bundled_recipes,
    load_recipe_by_toolchain,
)


def test_bundled_node_recipe_loads():
    recipe = load_recipe_by_toolchain("node")
    assert recipe.toolchain == "node"
    assert "package.json" in recipe.markers
    assert recipe.priority == 50
    test_cmd = next(c for c in recipe.commands if c.id == "test")
    assert test_cmd.required
    assert test_cmd.if_script_exists == "test"
    assert "node_manifest_sanity" in recipe.probes


def test_bundled_go_recipe_loads():
    recipe = load_recipe_by_toolchain("go")
    assert recipe.toolchain == "go"
    assert "go.mod" in recipe.markers
    build = next(c for c in recipe.commands if c.id == "build")
    assert build.command == "go build ./..."
    assert build.required


def test_load_bundled_returns_all_recipes():
    recipes = load_bundled_recipes()
    names = {r.toolchain for r in recipes}
    assert {"node", "go", "python"} <= names


def test_bundled_python_recipe_loads():
    recipe = load_recipe_by_toolchain("python")
    assert recipe.toolchain == "python"
    # Python recipe uses any_markers since project type can be signalled by any of several files.
    assert recipe.markers == ()
    expected = {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "pytest.ini", "tox.ini", "tests"}
    assert expected <= set(recipe.any_markers)
    test_cmd = next(c for c in recipe.commands if c.id == "test")
    assert test_cmd.command == "python -m pytest"
    assert test_cmd.required
    # No lint/typecheck commands by default — they stay opt-in via .cco.yaml overrides.
    assert [c.id for c in recipe.commands] == ["test"]


def test_unknown_toolchain_lists_available():
    with pytest.raises(FileNotFoundError, match="unknown toolchain 'rust'"):
        load_recipe_by_toolchain("rust")


def test_default_timeout_applied(tmp_path: Path):
    p = tmp_path / "x.yaml"
    p.write_text(
        yaml.dump(
            {
                "toolchain": "x",
                "priority": 10,
                "markers": ["x.toml"],
                "commands": [{"id": "t", "command": "echo hi"}],
            }
        )
    )
    recipe = load_recipe_by_toolchain("x", recipes_dir=tmp_path)
    assert recipe.commands[0].timeout_seconds == _DEFAULT_TIMEOUT_SECONDS


def test_missing_toolchain_field_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"priority": 1, "markers": ["x"]}))
    with pytest.raises(ValueError, match="missing required field 'toolchain'"):
        load_recipe_by_toolchain("bad", recipes_dir=tmp_path)


def test_missing_markers_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"toolchain": "bad", "priority": 1, "markers": []}))
    with pytest.raises(ValueError, match="must declare at least one marker"):
        load_recipe_by_toolchain("bad", recipes_dir=tmp_path)


def test_any_markers_alone_satisfies_recipe(tmp_path: Path):
    p = tmp_path / "x.yaml"
    p.write_text(
        yaml.dump(
            {
                "toolchain": "x",
                "priority": 10,
                "any_markers": ["a.toml", "b.toml"],
                "commands": [{"id": "t", "command": "echo hi"}],
            }
        )
    )
    recipe = load_recipe_by_toolchain("x", recipes_dir=tmp_path)
    assert recipe.markers == ()
    assert recipe.any_markers == ("a.toml", "b.toml")


def test_missing_priority_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"toolchain": "bad", "markers": ["x"]}))
    with pytest.raises(ValueError, match="missing required field 'priority'"):
        load_recipe_by_toolchain("bad", recipes_dir=tmp_path)
