from pathlib import Path

from orchestrator.verifiers.detection import detect_toolchain
from orchestrator.verifiers.recipe import Command, Recipe


def _recipe(name: str, priority: int, markers: tuple[str, ...]) -> Recipe:
    return Recipe(
        toolchain=name,
        priority=priority,
        markers=markers,
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


def test_php_marker_match(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{}")
    node = _recipe("node", 50, ("package.json",))
    php = _recipe("php", 50, ("composer.json",))
    chosen = detect_toolchain(tmp_path, [node, php])
    assert chosen is not None
    assert chosen.toolchain == "php"


def test_php_does_not_match_without_composer(tmp_path: Path):
    # A stray phpunit.xml without composer.json must not trigger PHP detection.
    (tmp_path / "phpunit.xml").write_text("<phpunit/>")
    php = _recipe("php", 50, ("composer.json",))
    chosen = detect_toolchain(tmp_path, [php])
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
