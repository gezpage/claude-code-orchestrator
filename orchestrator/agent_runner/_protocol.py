from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from orchestrator.agent_runner._progress import ProgressCallback, ProgressEvent

__all__ = ["AgentRunRequest", "AgentRunResult", "AgentRunner", "ProgressCallback", "ProgressEvent"]


@dataclass(frozen=True)
class AgentRunRequest:
    prompt: str
    stage_name: str = ""
    cwd: str | None = None
    workspace_root: str | None = None
    writable_roots: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    timeout_seconds: int | None = None
    model: str | None = None
    permission_mode: str | None = None
    output_mode: str = "text"
    # Optional sink for streaming ProgressEvents emitted by the runner as the agent
    # works. When set, runners that support streaming (currently both Claude
    # runners) flip the underlying CLI into ``--output-format stream-json --verbose``
    # so long-running stages can surface "tool X / text Y" breadcrumbs in run.log
    # instead of going silent. See ADR-024.
    progress_callback: ProgressCallback | None = None


@dataclass(frozen=True)
class AgentRunResult:
    backend: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    timed_out: bool
    command: list[str] | None = field(default=None)


class AgentRunner(Protocol):
    """Dispatch a single agent invocation. Implementations are backend-specific."""

    backend_name: str

    def run(self, request: AgentRunRequest) -> AgentRunResult: ...
