"""Deterministic verification framework.

Public entry point is `verify(repo_root, run_folder)` in `engine`; see ADR-017.
"""

from orchestrator.verifiers.engine import verify

__all__ = ["verify"]
