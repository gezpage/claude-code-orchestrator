from __future__ import annotations

from dataclasses import dataclass

from orchestrator.agent_runner._claude import ClaudeCodeRunner
from orchestrator.agent_runner._codex import CodexCliRunner
from orchestrator.agent_runner._protocol import AgentRunner


@dataclass(frozen=True)
class AgentConfig:
    backend: str = "claude_code"
    model: str | None = None
    sterile_context: bool = True
    timeout_seconds: int | None = None
    permission_mode: str | None = None
    output_mode: str = "text"


_KNOWN_BACKENDS = frozenset({"claude_code", "codex_cli"})

# Keys whose values are interpreted by the backend's own CLI and cannot be assumed
# portable across backends. When a stage switches backend, profile-level values for
# these keys are dropped so e.g. a Claude model name is never passed to `codex exec`.
_BACKEND_SPECIFIC_KEYS = frozenset({"model", "permission_mode"})


def resolve_agent_config(profile_agent: dict | None, stage_agent: dict | None) -> AgentConfig:
    """Shallow-merge profile-level defaults with stage-level overrides.

    Stage keys win over profile keys. Unknown keys raise so typos fail loudly
    rather than silently using defaults.

    When the stage declares a backend different from the profile's, backend-specific
    keys (model, permission_mode) are dropped from profile inheritance — their values
    are CLI-specific and cannot cross backends.
    """
    profile_agent = profile_agent or {}
    stage_agent = stage_agent or {}

    profile_backend = profile_agent.get("backend")
    stage_backend = stage_agent.get("backend")
    if stage_backend is not None and profile_backend is not None and stage_backend != profile_backend:
        profile_agent = {k: v for k, v in profile_agent.items() if k not in _BACKEND_SPECIFIC_KEYS}

    merged: dict = {}
    for src in (profile_agent, stage_agent):
        for k, v in src.items():
            if k not in AgentConfig.__dataclass_fields__:
                raise ValueError(f"Unknown agent config key: {k!r}")
            merged[k] = v
    return AgentConfig(**merged)


def build_runner(config: AgentConfig) -> AgentRunner:
    if config.backend not in _KNOWN_BACKENDS:
        raise ValueError(f"Unknown agent backend {config.backend!r}; supported: {sorted(_KNOWN_BACKENDS)}")
    if config.backend == "claude_code":
        return ClaudeCodeRunner(
            sterile_context=config.sterile_context,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            output_mode=config.output_mode,
        )
    if config.backend == "codex_cli":
        return CodexCliRunner(
            sterile_context=config.sterile_context,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            permission_mode=config.permission_mode,
            output_mode=config.output_mode,
        )
    raise AssertionError(f"unreachable: backend {config.backend}")
