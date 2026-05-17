from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml


class ExpansionKind(StrEnum):
    NONE = "none"
    TRACKS = "tracks"  # two-phase: planning phase + parallel track sub-nodes
    SLICES = "slices"  # parallel per-slice nodes driven by prior stage signal
    PROMPTS = "prompts"  # fan-out to named prompt sub-nodes (e.g. multi-reviewer)


_WAVE_VERIFICATION_POLICIES: tuple[str, ...] = ("warn", "fix_then_retry", "block")


@dataclass(frozen=True)
class WaveVerification:
    """Per-stage policy for deterministic verification between slice waves.

    Triggered by slice expansion/config — never by profile name. See ADR-030.
    """

    enabled: bool = False
    on_failure: Literal["warn", "fix_then_retry", "block"] = "warn"


@dataclass(frozen=True)
class StageConfig:
    name: str
    mode: Literal["auto", "interactive", "deterministic"] = "auto"
    prompt: str | None = None
    prompts: dict[str, str] = field(default_factory=dict)
    artifact: str | None = None  # interactive stages only
    standards: bool = False
    expansion: ExpansionKind = ExpansionKind.NONE
    slices_from_stage: str | None = None  # SLICES: which prior signal carries slice_files
    cwd_from_repo_root: bool = False
    wave_verification: WaveVerification | None = None
    # Raw agent config; merged with the profile-level default at dispatch time so
    # profile parsing stays oblivious to the agent_runner module.
    agent: dict[str, object] | None = None


@dataclass(frozen=True)
class Profile:
    name: str
    stages: tuple[StageConfig, ...]
    agent: dict[str, object] | None = None
    # Optional override for the post-pipeline pr_draft finalisation step.
    # Merged on top of `agent` via resolve_agent_config so profiles can pin a
    # cheaper model for PR drafting without affecting pipeline stages. See ADR-029.
    pr_draft_agent: dict[str, object] | None = None


_BUNDLED_PROFILES_DIR = Path(__file__).parent / "profiles"


def _parse_stage(raw: dict) -> StageConfig:
    expansion_str = raw.get("expansion", "none")
    try:
        expansion = ExpansionKind(expansion_str)
    except ValueError as exc:
        raise ValueError(f"Unknown expansion kind {expansion_str!r} in stage {raw.get('stage')!r}") from exc

    prompts = raw.get("prompts") or {}
    if not isinstance(prompts, dict):
        raise ValueError(f"Stage {raw.get('stage')!r}: 'prompts' must be a mapping")

    mode = raw.get("mode", "auto")
    if mode not in ("auto", "interactive", "deterministic"):
        raise ValueError(f"Stage {raw.get('stage')!r}: unknown mode {mode!r}")

    agent = raw.get("agent")
    if agent is not None and not isinstance(agent, dict):
        raise ValueError(f"Stage {raw.get('stage')!r}: 'agent' must be a mapping")

    wave_verification = _parse_wave_verification(raw, expansion)

    return StageConfig(
        name=raw["stage"],
        mode=mode,
        prompt=raw.get("prompt"),
        prompts=dict(prompts),
        artifact=raw.get("artifact"),
        standards=bool(raw.get("standards", False)),
        expansion=expansion,
        slices_from_stage=raw.get("slices_from_stage"),
        cwd_from_repo_root=bool(raw.get("cwd_from_repo_root", False)),
        wave_verification=wave_verification,
        agent=dict(agent) if agent else None,
    )


def _parse_wave_verification(raw: dict, expansion: ExpansionKind) -> WaveVerification | None:
    """Parse the optional ``wave_verification`` block on a stage.

    Default-on for slice-expansion stages with ``on_failure: warn`` — keyed off the
    expansion kind, never a profile name. See ADR-030. Stages can disable explicitly
    by setting ``wave_verification: {enabled: false}``; non-slice stages return None
    so the dispatcher loop short-circuits.
    """
    raw_wv = raw.get("wave_verification")
    if raw_wv is None:
        if expansion == ExpansionKind.SLICES:
            return WaveVerification(enabled=True, on_failure="warn")
        return None
    if not isinstance(raw_wv, dict):
        raise ValueError(f"Stage {raw.get('stage')!r}: 'wave_verification' must be a mapping")
    on_failure = raw_wv.get("on_failure", "warn")
    if on_failure not in _WAVE_VERIFICATION_POLICIES:
        raise ValueError(
            f"Stage {raw.get('stage')!r}: 'wave_verification.on_failure' must be one of "
            f"{list(_WAVE_VERIFICATION_POLICIES)}; got {on_failure!r}"
        )
    return WaveVerification(
        enabled=bool(raw_wv.get("enabled", True)),
        on_failure=on_failure,
    )


def load_profile(profile: str | Path, bundled_dir: Path | None = None) -> Profile:
    """Parse a profile YAML (path or bundled name) into a typed Profile."""
    if bundled_dir is None:
        bundled_dir = _BUNDLED_PROFILES_DIR

    profile_str = str(profile)
    if profile_str.endswith((".yaml", ".yml")):
        p = Path(profile_str)
        if not p.is_file():
            raise FileNotFoundError(f"Profile file not found: {p}")
        raw = yaml.safe_load(p.read_text())
    else:
        bundled = Path(bundled_dir) / f"{profile_str}.yaml"
        if not bundled.is_file():
            available = ", ".join(p.stem for p in sorted(Path(bundled_dir).glob("*.yaml")))
            raise FileNotFoundError(f"Unknown profile '{profile_str}'. Available: {available}")
        raw = yaml.safe_load(bundled.read_text())

    profile_agent = raw.get("agent")
    if profile_agent is not None and not isinstance(profile_agent, dict):
        raise ValueError(f"Profile {raw.get('name')!r}: 'agent' must be a mapping")

    pr_draft_raw = raw.get("pr_draft")
    pr_draft_agent: dict | None = None
    if pr_draft_raw is not None:
        if not isinstance(pr_draft_raw, dict):
            raise ValueError(f"Profile {raw.get('name')!r}: 'pr_draft' must be a mapping")
        pr_draft_agent_raw = pr_draft_raw.get("agent")
        if pr_draft_agent_raw is not None:
            if not isinstance(pr_draft_agent_raw, dict):
                raise ValueError(f"Profile {raw.get('name')!r}: 'pr_draft.agent' must be a mapping")
            pr_draft_agent = dict(pr_draft_agent_raw)

    return Profile(
        name=raw.get("name", ""),
        stages=tuple(_parse_stage(s) for s in raw.get("stages", [])),
        agent=dict(profile_agent) if profile_agent else None,
        pr_draft_agent=pr_draft_agent,
    )
