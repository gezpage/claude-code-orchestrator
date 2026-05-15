"""Probe registry.

Probes are Python callables that receive a ProbeContext and return a ProbeResult.
The registry is an explicit dict — no dynamic discovery — so the set of available
probes is auditable from this file alone (ADR-017).
"""

from __future__ import annotations

from collections.abc import Callable

from orchestrator.verifiers.probes import go_module_sanity, node_manifest_sanity
from orchestrator.verifiers.probes._types import ProbeContext, ProbeResult

ProbeFn = Callable[[ProbeContext], ProbeResult]

REGISTRY: dict[str, ProbeFn] = {
    "node_manifest_sanity": node_manifest_sanity.run,
    "go_module_sanity": go_module_sanity.run,
}


def get(name: str) -> ProbeFn:
    if name not in REGISTRY:
        raise KeyError(f"unknown probe '{name}'. Available: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[name]


__all__ = ["REGISTRY", "ProbeContext", "ProbeFn", "ProbeResult", "get"]
