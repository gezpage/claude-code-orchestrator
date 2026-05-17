from pathlib import Path

from orchestrator.verifiers.detection import detect_toolchain
from orchestrator.verifiers.recipe import Command, Recipe, load_bundled_recipes


def _recipe(name: str, priority: int, markers: tuple[str, ...], any_markers: tuple[str, ...] = ()) -> Recipe:
    return Recipe(
        toolchain=name,
        priority=priority,
        markers=markers,
        any_markers=any_markers,
        commands=(Command(id="t", command="true", required=True),),
        probes=(),
    )


def test_single_marker_match(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    node = _recipe("node", 50, ("package.json",))
    go = _recipe("go", 50, ("go.mod",))
    chosen = detect_toolchain(tmp_path, [node, go])
    assert chosen is not None
    assert chosen.toolchain == "node"


def test_no_match_returns_none(tmp_path: Path):
    node = _recipe("node", 50, ("package.json",))
    chosen = detect_toolchain(tmp_path, [node])
    assert chosen is None


def test_priority_wins_on_multi_match(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "go.mod").write_text("module x")
    node = _recipe("node", 30, ("package.json",))
    go = _recipe("go", 70, ("go.mod",))
    chosen = detect_toolchain(tmp_path, [node, go])
    assert chosen is not None
    assert chosen.toolchain == "go"


def test_all_markers_required(tmp_path: Path):
    # Recipe requires both files; only one present → no match.
    (tmp_path / "package.json").write_text("{}")
    multi = _recipe("multi", 50, ("package.json", "yarn.lock"))
    chosen = detect_toolchain(tmp_path, [multi])
    assert chosen is None


def test_tiebreak_deterministic_on_equal_priority(tmp_path: Path):
    # Same priority, both match → alphabetical toolchain name wins as tiebreak.
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "go.mod").write_text("module x")
    node = _recipe("node", 50, ("package.json",))
    go = _recipe("go", 50, ("go.mod",))
    chosen = detect_toolchain(tmp_path, [node, go])
    assert chosen is not None
    assert chosen.toolchain == "go"  # alphabetical


def test_any_markers_matches_when_one_present(tmp_path: Path):
    # any_markers semantics: at least one entry must be present.
    (tmp_path / "pyproject.toml").write_text("")
    py = _recipe("python", 50, markers=(), any_markers=("pyproject.toml", "requirements.txt", "setup.py"))
    chosen = detect_toolchain(tmp_path, [py])
    assert chosen is not None
    assert chosen.toolchain == "python"


def test_any_markers_no_match_when_none_present(tmp_path: Path):
    py = _recipe("python", 50, markers=(), any_markers=("pyproject.toml", "requirements.txt"))
    chosen = detect_toolchain(tmp_path, [py])
    assert chosen is None


def test_any_markers_directory_entry_matches(tmp_path: Path):
    # A directory literal marker — Path.exists() returns True for dirs too.
    (tmp_path / "src").mkdir()
    py = _recipe("python", 50, markers=(), any_markers=("pyproject.toml", "src"))
    chosen = detect_toolchain(tmp_path, [py])
    assert chosen is not None
    assert chosen.toolchain == "python"


def test_markers_and_any_markers_combine_with_and(tmp_path: Path):
    # `markers` must all be present AND at least one `any_markers` entry must be present.
    (tmp_path / "foo.lock").write_text("")
    # Only `markers` present, none of `any_markers` → no match.
    r = _recipe("hybrid", 50, markers=("foo.lock",), any_markers=("alt1", "alt2"))
    assert detect_toolchain(tmp_path, [r]) is None
    # Adding one any_markers entry satisfies the recipe.
    (tmp_path / "alt2").write_text("")
    assert detect_toolchain(tmp_path, [r]) is not None


def test_glob_marker_matches_when_file_present(tmp_path: Path):
    # Glob entries are matched with Path.glob — at least one match counts.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_quote.py").write_text("")
    r = _recipe("python", 50, markers=(), any_markers=("tests/**/*.py",))
    assert detect_toolchain(tmp_path, [r]) is not None


def test_glob_marker_no_match_when_bare_dir_only(tmp_path: Path):
    # Bare `tests/` with no .py files inside must not match a `tests/**/*.py` glob.
    (tmp_path / "tests").mkdir()
    r = _recipe("python", 50, markers=(), any_markers=("tests/**/*.py",))
    assert detect_toolchain(tmp_path, [r]) is None


# ---------------------------------------------------------------------------
# Regression coverage for the bundled Python recipe — bare `tests/` is now
# excluded from `any_markers` so it cannot mis-classify Go/Node repos as
# Python on alphabetical tiebreak.
# ---------------------------------------------------------------------------


def test_bundled_repo_with_go_mod_and_tests_dir_selects_go(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module x")
    (tmp_path / "tests").mkdir()
    chosen = detect_toolchain(tmp_path, load_bundled_recipes())
    assert chosen is not None
    assert chosen.toolchain == "go"


def test_bundled_repo_with_only_bare_tests_dir_does_not_select_python(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    chosen = detect_toolchain(tmp_path, load_bundled_recipes())
    assert chosen is None


def test_bundled_repo_with_tests_python_file_selects_python(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_quote.py").write_text("")
    chosen = detect_toolchain(tmp_path, load_bundled_recipes())
    assert chosen is not None
    assert chosen.toolchain == "python"
