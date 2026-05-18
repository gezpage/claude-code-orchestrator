"""Project-level verification config loaded from `.cco.yaml` at repo root.

Schema:
    verification:
      toolchain: <name>          # optional — pins detection
      commands: [...]            # optional — REPLACES recipe commands when present
      probes: [...]              # optional — REPLACES recipe probes when present

Overrides replace rather than merge (see ADR-017).

Note: for Node and TypeScript projects, the verifier engine emits a non-blocking
``clean-install-audit`` warning when ``commands`` is overridden but contains no
``npm ci`` / ``yarn install --frozen-lockfile`` / ``pnpm install --frozen-lockfile``
step. The bundled recipes ship lockfile-gated clean-install commands precisely
to catch lockfile/dependency drift; replacing the recipe wholesale loses that
protection unless the override puts it back. The audit lifts ``verification_status``
to ``warned`` so the executive summary surfaces it, but never to ``failed`` —
it's an advisory, not a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from orchestrator.verifiers.recipe import Command, _parse_command


@dataclass(frozen=True)
class ProjectVerifyConfig:
    toolchain: str | None = None
    commands: tuple[Command, ...] | None = None  # None = no override; () = empty override
    probes: tuple[str, ...] | None = None


def load_project_config(repo_root: Path) -> ProjectVerifyConfig | None:
    """Return parsed config, or None if `.cco.yaml` is absent or has no `verification` block."""
    path = repo_root / ".cco.yaml"
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text()) or {}
    verify_block = raw.get("verification")
    if not isinstance(verify_block, dict):
        return None

    commands: tuple[Command, ...] | None = None
    if "commands" in verify_block:
        commands = tuple(_parse_command(c) for c in (verify_block["commands"] or []))

    probes: tuple[str, ...] | None = None
    if "probes" in verify_block:
        probes = tuple(verify_block["probes"] or [])

    return ProjectVerifyConfig(
        toolchain=verify_block.get("toolchain"),
        commands=commands,
        probes=probes,
    )
