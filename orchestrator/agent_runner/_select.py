from __future__ import annotations

from dataclasses import dataclass

from orchestrator.agent_runner._claude import ClaudeCodePrintRunner
from orchestrator.agent_runner._claude_auto import ClaudeCodeAutoRunner
from orchestrator.agent_runner._codex import CodexCliRunner
from orchestrator.agent_runner._protocol import AgentRunner


@dataclass(frozen=True)
class AgentConfig:
    backend: str = "claude_code_print"
    model: str | None = None
    sterile_context: bool = True
    timeout_seconds: int | None = None
    permission_mode: str | None = None
    output_mode: str = "text"


_KNOWN_BACKENDS = frozenset({"claude_code_print", "claude_code_auto", "codex_cli"})


def resolve_agent_config(profile_agent: dict | None, stage_agent: dict | None) -> AgentConfig:
    """Shallow-merge profile-level defaults with stage-level overrides.

    Stage keys win over profile keys. Unknown keys raise so typos fail loudly
    rather than silently using defaults.
    """
    merged: dict = {}
    for src in (profile_agent or {}, stage_agent or {}):
        for k, v in src.items():
            if k not in AgentConfig.__dataclass_fields__:
                raise ValueError(f"Unknown agent config key: {k!r}")
            merged[k] = v
    return AgentConfig(**merged)


def build_runner(config: AgentConfig) -> AgentRunner:
    if config.backend not in _KNOWN_BACKENDS:
        raise ValueError(f"Unknown agent backend {config.backend!r}; supported: {sorted(_KNOWN_BACKENDS)}")
    if config.backend == "claude_code_print":
        return ClaudeCodePrintRunner(
            sterile_context=config.sterile_context,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            output_mode=config.output_mode,
        )
    if config.backend == "claude_code_auto":
        return ClaudeCodeAutoRunner(
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
