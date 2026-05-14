from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AgentRunRequest:
    prompt: str
    stage_name: str = ""
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    timeout_seconds: int | None = None
    model: str | None = None
    permission_mode: str | None = None
    output_mode: str = "text"
    # If set, the runner writes its transcript here. run_stage computes the path so
    # transcripts land in the existing per-stage folder convention.
    transcript_path: Path | None = None


@dataclass(frozen=True)
class AgentRunResult:
    backend: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    timed_out: bool
    transcript_path: Path | None = None
    command: list[str] | None = field(default=None)


class AgentRunner(Protocol):
    """Dispatch a single agent invocation. Implementations are backend-specific."""

    backend_name: str

    def run(self, request: AgentRunRequest) -> AgentRunResult: ...
