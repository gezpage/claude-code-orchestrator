import pytest
import yaml

from orchestrator.orchestrate import _load_profile


def test_builtin_full():
    profile = _load_profile("full")
    assert profile["name"] == "full"
    assert any(s["stage"] == "discovery" for s in profile["stages"])


def test_builtin_spike():
    profile = _load_profile("spike")
    assert profile["name"] == "spike"
    assert profile["stages"] == [{"stage": "discovery"}]


def test_unknown_builtin_raises():
    with pytest.raises(FileNotFoundError, match="Unknown profile 'bogus'"):
        _load_profile("bogus")


def test_unknown_builtin_lists_available():
    with pytest.raises(FileNotFoundError, match="full"):
        _load_profile("bogus")


def test_file_path_loads(tmp_path):
    p = tmp_path / "custom.yaml"
    p.write_text(yaml.dump({"name": "custom", "stages": [{"stage": "discovery"}]}))
    profile = _load_profile(str(p))
    assert profile["name"] == "custom"


def test_file_path_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Profile file not found"):
        _load_profile(str(tmp_path / "missing.yaml"))
