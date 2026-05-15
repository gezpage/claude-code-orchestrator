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


def test_builtin_full_interactive_alignment():
    profile = load_profile("full-interactive")
    alignment = next(s for s in profile.stages if s.name == "alignment")

    assert alignment.mode == "interactive"
    assert alignment.artifact == "alignment-log.md"
    assert alignment.prompt == "prompts/alignment/interactive.md"


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


def test_minimal_codex_profile_uses_codex_backend():
    profile = load_profile("minimal-codex")
    assert profile.name == "minimal-codex"
    assert profile.agent == {
        "backend": "codex_cli",
        "sterile_context": True,
        "permission_mode": "workspace-write",
    }


def test_minimal_codex_profile_matches_minimal_stage_shape():
    codex = load_profile("minimal-codex")
    minimal = load_profile("minimal")
    assert [s.name for s in codex.stages] == [s.name for s in minimal.stages]
    codex_verification = next(s for s in codex.stages if s.name == "verification")
    assert codex_verification.mode == "deterministic"


def test_minimal_codex_implementation_overrides_permission_mode():
    """Implementation must commit, which needs `.git` writes — workspace-write blocks that."""
    codex = load_profile("minimal-codex")
    impl = next(s for s in codex.stages if s.name == "implementation")
    assert impl.agent == {"permission_mode": "danger-full-access"}
    # Non-committing stages stay on the profile-level workspace-write default.
    for non_committing in ("specification", "decomposition", "review"):
        stage = next(s for s in codex.stages if s.name == non_committing)
        assert stage.agent is None


def test_minimal_claude_profile_loads():
    """minimal-claude uses claude_code_auto for non-review stages, codex_cli for review."""
    profile = load_profile("minimal-claude")
    assert profile.name == "minimal-claude"
    assert profile.agent == {"backend": "claude_code_auto", "model": "claude-opus-4-7"}
    assert [s.name for s in profile.stages] == [
        "specification",
        "decomposition",
        "implementation",
        "verification",
        "review",
    ]
    review = next(s for s in profile.stages if s.name == "review")
    assert review.agent == {"backend": "codex_cli", "permission_mode": "read-only"}
    # Non-review stages inherit profile-level claude_code_auto.
    for stage_name in ("specification", "decomposition", "implementation"):
        stage = next(s for s in profile.stages if s.name == stage_name)
        assert stage.agent is None


def test_minimal_claude_build_stage_runners_picks_correct_classes():
    """Smoke test the full path: load_profile → _build_stage_runners returns the
    right runner class per stage. Catches regressions in the registry wiring."""
    from orchestrator.agent_runner import ClaudeCodeAutoRunner, CodexCliRunner
    from orchestrator.orchestrate import _build_stage_runners

    profile = load_profile("minimal-claude")
    runners, metadata = _build_stage_runners(profile)
    assert isinstance(runners["implementation"], ClaudeCodeAutoRunner)
    assert isinstance(runners["specification"], ClaudeCodeAutoRunner)
    assert isinstance(runners["decomposition"], ClaudeCodeAutoRunner)
    assert isinstance(runners["review"], CodexCliRunner)
    assert "verification" not in runners  # deterministic
    assert metadata["verification"]["backend"] == "deterministic"
    assert metadata["implementation"]["backend"] == "claude_code_auto"
    assert metadata["review"]["backend"] == "codex_cli"


def test_minimal_claude_review_resolves_to_codex():
    """Resolved agent config for the review stage flips backend to codex_cli."""
    from orchestrator.agent_runner import resolve_agent_config

    profile = load_profile("minimal-claude")
    review = next(s for s in profile.stages if s.name == "review")
    cfg = resolve_agent_config(profile.agent, review.agent)
    assert cfg.backend == "codex_cli"
    assert cfg.permission_mode == "read-only"
    # Non-review stage inherits the profile-level backend.
    impl = next(s for s in profile.stages if s.name == "implementation")
    impl_cfg = resolve_agent_config(profile.agent, impl.agent)
    assert impl_cfg.backend == "claude_code_auto"
    assert impl_cfg.model == "claude-opus-4-7"


def test_profile_level_agent_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "agent": {"backend": "claude_code_print", "model": "opus", "sterile_context": True},
                "stages": [{"stage": "discovery"}],
            }
        )
    )
    profile = load_profile(str(p))
    assert profile.agent == {"backend": "claude_code_print", "model": "opus", "sterile_context": True}
    assert profile.stages[0].agent is None


def test_stage_level_agent_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [
                    {"stage": "discovery"},
                    {"stage": "review", "agent": {"backend": "codex_cli", "model": "gpt-5.1-codex"}},
                ],
            }
        )
    )
    profile = load_profile(str(p))
    assert profile.agent is None
    assert profile.stages[0].agent is None
    assert profile.stages[1].agent == {"backend": "codex_cli", "model": "gpt-5.1-codex"}


def test_stage_agent_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"name": "p", "stages": [{"stage": "discovery", "agent": ["nope"]}]}))
    with pytest.raises(ValueError, match="'agent' must be a mapping"):
        load_profile(str(p))


def test_profile_agent_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"name": "p", "agent": "nope", "stages": []}))
    with pytest.raises(ValueError, match="'agent' must be a mapping"):
        load_profile(str(p))
