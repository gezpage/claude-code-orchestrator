"""Probe types kept in their own module to avoid circular imports with the registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProbeContext:
    repo_root: Path


@dataclass
class ProbeResult:
    id: str
    status: str  # "passed" | "failed"
    findings: list[str] = field(default_factory=list)
