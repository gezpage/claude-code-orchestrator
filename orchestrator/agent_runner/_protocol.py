from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


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
