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


def test_minimal_profile_is_claude_with_codex_review():
    """minimal is the hybrid default: claude_code for impl stages, codex_cli for review."""
    profile = load_profile("minimal")
    assert profile.name == "minimal"
    assert profile.agent == {"backend": "claude_code", "model": "claude-opus-4-7"}
    assert [s.name for s in profile.stages] == [
        "specification",
        "decomposition",
        "implementation",
        "verification",
        "review",
    ]
    review = next(s for s in profile.stages if s.name == "review")
    assert review.agent == {"backend": "codex_cli", "permission_mode": "workspace-write"}
    for stage_name in ("specification", "decomposition", "implementation"):
        stage = next(s for s in profile.stages if s.name == stage_name)
        assert stage.agent is None


def test_minimal_profile_pr_draft_pins_sonnet():
    """pr_draft override drops to a cheaper model for PR drafting. See ADR-029."""
    profile = load_profile("minimal")
    assert profile.pr_draft_agent == {"model": "claude-sonnet-4-6"}


def test_minimal_build_stage_runners_picks_correct_classes():
    """Smoke test the full path: load_profile → _build_stage_runners returns the
    right runner class per stage. Catches regressions in the registry wiring."""
    from orchestrator.agent_runner import ClaudeCodeRunner, CodexCliRunner
    from orchestrator.orchestrate import _build_stage_runners

    profile = load_profile("minimal")
    runners, metadata = _build_stage_runners(profile)
    assert isinstance(runners["implementation"], ClaudeCodeRunner)
    assert isinstance(runners["specification"], ClaudeCodeRunner)
    assert isinstance(runners["decomposition"], ClaudeCodeRunner)
    assert isinstance(runners["review"], CodexCliRunner)
    assert "verification" not in runners  # deterministic
    assert metadata["verification"]["backend"] == "deterministic"
    assert metadata["implementation"]["backend"] == "claude_code"
    assert metadata["review"]["backend"] == "codex_cli"


def test_minimal_review_resolves_to_codex():
    """Resolved agent config for the review stage flips backend to codex_cli."""
    from orchestrator.agent_runner import resolve_agent_config

    profile = load_profile("minimal")
    review = next(s for s in profile.stages if s.name == "review")
    cfg = resolve_agent_config(profile.agent, review.agent)
    assert cfg.backend == "codex_cli"
    assert cfg.permission_mode == "workspace-write"
    impl = next(s for s in profile.stages if s.name == "implementation")
    impl_cfg = resolve_agent_config(profile.agent, impl.agent)
    assert impl_cfg.backend == "claude_code"
    assert impl_cfg.model == "claude-opus-4-7"


def test_minimal_claude_profile_is_pure_claude():
    """minimal-claude uses claude_code throughout — no codex review override."""
    profile = load_profile("minimal-claude")
    assert profile.name == "minimal-claude"
    assert profile.agent == {"backend": "claude_code", "model": "claude-opus-4-7"}
    for stage in profile.stages:
        assert stage.agent is None, f"{stage.name} should inherit profile-level claude_code"


def test_minimal_claude_pr_draft_pins_sonnet():
    profile = load_profile("minimal-claude")
    assert profile.pr_draft_agent == {"model": "claude-sonnet-4-6"}


def test_minimal_claude_pr_draft_resolves_to_sonnet():
    """pr_draft agent merges the override over the profile-level claude_code default."""
    from orchestrator.agent_runner import resolve_agent_config

    profile = load_profile("minimal-claude")
    cfg = resolve_agent_config(profile.agent, profile.pr_draft_agent)
    assert cfg.backend == "claude_code"
    assert cfg.model == "claude-sonnet-4-6"


def test_profile_level_agent_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "agent": {"backend": "claude_code", "model": "opus", "sterile_context": True},
                "stages": [{"stage": "discovery"}],
            }
        )
    )
    profile = load_profile(str(p))
    assert profile.agent == {"backend": "claude_code", "model": "opus", "sterile_context": True}
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


def test_pr_draft_agent_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "agent": {"backend": "claude_code", "model": "claude-opus-4-7"},
                "pr_draft": {"agent": {"model": "claude-sonnet-4-6"}},
                "stages": [{"stage": "discovery"}],
            }
        )
    )
    profile = load_profile(str(p))
    assert profile.pr_draft_agent == {"model": "claude-sonnet-4-6"}


def test_pr_draft_absent_yields_none(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.dump({"name": "p", "stages": [{"stage": "discovery"}]}))
    profile = load_profile(str(p))
    assert profile.pr_draft_agent is None


def test_pr_draft_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"name": "p", "pr_draft": ["nope"], "stages": []}))
    with pytest.raises(ValueError, match="'pr_draft' must be a mapping"):
        load_profile(str(p))


def test_pr_draft_agent_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump({"name": "p", "pr_draft": {"agent": "nope"}, "stages": []}))
    with pytest.raises(ValueError, match=r"'pr_draft\.agent' must be a mapping"):
        load_profile(str(p))


# ── wave_verification (ADR-030) ───────────────────────────────────────────────


def test_slice_stage_default_enables_wave_verification_with_warn(tmp_path):
    """Default is on for slice expansion with on_failure=warn, no profile-name keying."""
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [
                    {"stage": "impl", "expansion": "slices", "slices_from_stage": "decomp"},
                ],
            }
        )
    )
    profile = load_profile(str(p))
    wv = profile.stages[0].wave_verification
    assert wv is not None
    assert wv.enabled is True
    assert wv.on_failure == "warn"


def test_non_slice_stage_has_no_wave_verification(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.dump({"name": "p", "stages": [{"stage": "spec"}]}))
    profile = load_profile(str(p))
    assert profile.stages[0].wave_verification is None


def test_slice_stage_can_disable_wave_verification(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [
                    {
                        "stage": "impl",
                        "expansion": "slices",
                        "wave_verification": {"enabled": False},
                    },
                ],
            }
        )
    )
    profile = load_profile(str(p))
    wv = profile.stages[0].wave_verification
    assert wv is not None
    assert wv.enabled is False


def test_wave_verification_block_policy_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [
                    {
                        "stage": "impl",
                        "expansion": "slices",
                        "wave_verification": {"enabled": True, "on_failure": "block"},
                    },
                ],
            }
        )
    )
    profile = load_profile(str(p))
    wv = profile.stages[0].wave_verification
    assert wv is not None
    assert wv.on_failure == "block"


def test_wave_verification_fix_then_retry_policy_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [
                    {
                        "stage": "impl",
                        "expansion": "slices",
                        "wave_verification": {"on_failure": "fix_then_retry"},
                    },
                ],
            }
        )
    )
    profile = load_profile(str(p))
    wv = profile.stages[0].wave_verification
    assert wv is not None
    assert wv.enabled is True
    assert wv.on_failure == "fix_then_retry"


def test_wave_verification_unknown_policy_raises(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [
                    {
                        "stage": "impl",
                        "expansion": "slices",
                        "wave_verification": {"on_failure": "panic"},
                    },
                ],
            }
        )
    )
    with pytest.raises(ValueError, match=r"on_failure"):
        load_profile(str(p))


def test_wave_verification_must_be_mapping(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [
                    {"stage": "impl", "expansion": "slices", "wave_verification": ["nope"]},
                ],
            }
        )
    )
    with pytest.raises(ValueError, match=r"'wave_verification' must be a mapping"):
        load_profile(str(p))


def test_full_profile_implementation_has_wave_verification():
    """The full profile uses slice expansion, so it gets the default-on policy."""
    profile = load_profile("full")
    impl = next(s for s in profile.stages if s.name == "implementation")
    assert impl.wave_verification is not None
    assert impl.wave_verification.enabled is True
    assert impl.wave_verification.on_failure == "warn"


def test_minimal_profile_implementation_has_no_wave_verification():
    """Minimal does not slice — wave verification stays off, no profile-name branching."""
    profile = load_profile("minimal")
    impl = next(s for s in profile.stages if s.name == "implementation")
    assert impl.wave_verification is None


# ── alignment_policy (ADR-032) ────────────────────────────────────────────────


def test_alignment_policy_absent_yields_none(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.dump({"name": "p", "stages": [{"stage": "alignment"}]}))
    profile = load_profile(str(p))
    assert profile.stages[0].alignment_policy is None


def test_alignment_policy_warn_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [{"stage": "alignment", "alignment_policy": {"on_unresolved": "warn"}}],
            }
        )
    )
    profile = load_profile(str(p))
    ap = profile.stages[0].alignment_policy
    assert ap is not None
    assert ap.on_unresolved == "warn"


def test_alignment_policy_block_parsed(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [{"stage": "alignment", "alignment_policy": {"on_unresolved": "block"}}],
            }
        )
    )
    profile = load_profile(str(p))
    ap = profile.stages[0].alignment_policy
    assert ap is not None
    assert ap.on_unresolved == "block"


def test_alignment_policy_default_on_unresolved_is_warn(tmp_path):
    """An empty mapping is valid and defaults to ``warn``."""
    p = tmp_path / "p.yaml"
    p.write_text(yaml.dump({"name": "p", "stages": [{"stage": "alignment", "alignment_policy": {}}]}))
    profile = load_profile(str(p))
    ap = profile.stages[0].alignment_policy
    assert ap is not None
    assert ap.on_unresolved == "warn"


def test_alignment_policy_unknown_value_raises(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.dump(
            {
                "name": "p",
                "stages": [{"stage": "alignment", "alignment_policy": {"on_unresolved": "panic"}}],
            }
        )
    )
    with pytest.raises(ValueError, match="on_unresolved"):
        load_profile(str(p))


def test_alignment_policy_must_be_mapping(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.dump({"name": "p", "stages": [{"stage": "alignment", "alignment_policy": ["nope"]}]}))
    with pytest.raises(ValueError, match="'alignment_policy' must be a mapping"):
        load_profile(str(p))
