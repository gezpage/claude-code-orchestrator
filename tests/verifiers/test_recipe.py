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


def test_bundled_php_recipe_loads():
    recipe = load_recipe_by_toolchain("php")
    assert recipe.toolchain == "php"
    assert "composer.json" in recipe.markers
    composer_cmd = next(c for c in recipe.commands if c.id == "composer-test")
    assert composer_cmd.command == "composer test"
    assert composer_cmd.if_composer_script_exists == "test"
    phpunit_cmd = next(c for c in recipe.commands if c.id == "phpunit")
    assert phpunit_cmd.command == "vendor/bin/phpunit"
    assert phpunit_cmd.if_file_exists == "vendor/bin/phpunit"
    assert recipe.required_any_of == ("composer-test", "phpunit")


def test_required_any_of_unknown_id_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        yaml.dump(
            {
                "toolchain": "bad",
                "priority": 1,
                "markers": ["x"],
                "commands": [{"id": "t", "command": "true"}],
                "required_any_of": ["t", "missing"],
            }
        )
    )
    with pytest.raises(ValueError, match="required_any_of references unknown command id"):
        load_recipe_by_toolchain("bad", recipes_dir=tmp_path)


def test_load_bundled_returns_all_recipes():
    recipes = load_bundled_recipes()
    names = {r.toolchain for r in recipes}
    assert {"node", "go", "php", "python", "java", "typescript"} <= names


def test_bundled_typescript_recipe_loads():
    recipe = load_recipe_by_toolchain("typescript")
    assert recipe.toolchain == "typescript"
    assert "package.json" in recipe.markers
    assert recipe.priority > 50  # must beat Node
    # TypeScript signal can come from any of several files/globs.
    assert "tsconfig.json" in recipe.any_markers
    assert "vite.config.ts" in recipe.any_markers
    # test command is required, gated on the npm script existing.
    test_cmd = next(c for c in recipe.commands if c.id == "test")
    assert test_cmd.required
    assert test_cmd.if_script_exists == "test"
    # typecheck is optional but explicitly present.
    tc_cmd = next(c for c in recipe.commands if c.id == "typecheck")
    assert not tc_cmd.required
    assert tc_cmd.if_script_exists == "typecheck"
    assert "node_manifest_sanity" in recipe.probes


def test_bundled_python_recipe_loads():
    recipe = load_recipe_by_toolchain("python")
    assert recipe.toolchain == "python"
    # Python recipe uses any_markers since project type can be signalled by any of several files.
    assert recipe.markers == ()
    expected = {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"}
    assert expected <= set(recipe.any_markers)
    # Bare `tests/` is deliberately not a marker — it appears in non-Python repos
    # and would mis-detect them as Python on alphabetical tiebreak. Python test
    # files are matched via glob patterns instead.
    assert "tests" not in recipe.any_markers
    assert "tests/**/*.py" in recipe.any_markers
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
