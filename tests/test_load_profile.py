import pytest
import yaml

from orchestrator.profile import ExpansionKind, load_profile


def test_builtin_full():
    profile = load_profile("full")
    assert profile.name == "full"
    assert any(s.name == "discovery" for s in profile.stages)


def test_builtin_full_discovery_has_tracks_expansion():
    profile = load_profile("full")
    discovery = next(s for s in profile.stages if s.name == "discovery")
    assert discovery.expansion == ExpansionKind.TRACKS


def test_builtin_full_implementation_has_slices_expansion():
    profile = load_profile("full")
    impl = next(s for s in profile.stages if s.name == "implementation")
    assert impl.expansion == ExpansionKind.SLICES
    assert impl.slices_from_stage == "decomposition"


def test_builtin_full_review_has_prompts_expansion():
    profile = load_profile("full")
    review = next(s for s in profile.stages if s.name == "review")
    assert review.expansion == ExpansionKind.PROMPTS
    assert "architecture" in review.prompts


def test_builtin_spike():
    profile = load_profile("spike")
    assert profile.name == "spike"
    assert len(profile.stages) == 1
    assert profile.stages[0].name == "discovery"
    assert profile.stages[0].expansion == ExpansionKind.TRACKS


def test_unknown_builtin_raises():
    with pytest.raises(FileNotFoundError, match="Unknown profile 'bogus'"):
        load_profile("bogus")


def test_unknown_builtin_lists_available():
    with pytest.raises(FileNotFoundError, match="full"):
        load_profile("bogus")


def test_file_path_loads(tmp_path):
    p = tmp_path / "custom.yaml"
    p.write_text(yaml.dump({"name": "custom", "stages": [{"stage": "discovery"}]}))
    profile = load_profile(str(p))
    assert profile.name == "custom"
    assert profile.stages[0].name == "discovery"


def test_file_path_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Profile file not found"):
        load_profile(str(tmp_path / "missing.yaml"))


def test_unknown_expansion_kind_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"name": "bad", "stages": [{"stage": "foo", "expansion": "invalid"}]}))
    with pytest.raises(ValueError, match="Unknown expansion kind"):
        load_profile(str(p))


def test_deterministic_mode_parsed(tmp_path):
    p = tmp_path / "det.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "det",
                "stages": [{"stage": "verification", "mode": "deterministic"}],
            }
        )
    )
    profile = load_profile(str(p))
    assert profile.stages[0].mode == "deterministic"


def test_unknown_mode_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"name": "bad", "stages": [{"stage": "v", "mode": "magic"}]}))
    with pytest.raises(ValueError, match="unknown mode 'magic'"):
        load_profile(str(p))


def test_minimal_profile_has_deterministic_verification():
    profile = load_profile("minimal")
    verification = next((s for s in profile.stages if s.name == "verification"), None)
    assert verification is not None
    assert verification.mode == "deterministic"


def test_full_profile_has_deterministic_verification():
    profile = load_profile("full")
    verification = next((s for s in profile.stages if s.name == "verification"), None)
    assert verification is not None
    assert verification.mode == "deterministic"
