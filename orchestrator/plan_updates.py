"""Boundary helpers for orchestration -> plan.md mutations.

Thin layer that ``orchestrator.orchestrate`` imports for all plan-status
writes. Most names here are re-exports from :mod:`orchestrator.plan`; a
small number of compound helpers are factored out so recurring multi-line
stamp patterns live in one place rather than at each dispatch call site.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.plan import (
    init_plan_md,
    mark_pipeline_done,
    mark_pr_blocked,
    resolve_review_subnode_statuses,
    set_pr_node,
    set_pr_notice,
    update_plan_md,
)

__all__ = [
    "init_plan_md",
    "mark_pipeline_done",
    "mark_pr_blocked",
    "resolve_review_subnode_statuses",
    "set_pr_node",
    "set_pr_notice",
    "stamp_node_passed_with_commits",
    "update_plan_md",
]


def stamp_node_passed_with_commits(
    run_folder: Path,
    node_id: str,
    *,
    elapsed_secs: float,
    commits: list,
    signal: dict,
    impl_name: str,
    repo_root: str | None,
) -> None:
    """Stamp a slice/sub-node as passed with a commit-count output summary."""
    summary = f"{len(commits)} commit{'s' if len(commits) != 1 else ''}" if commits else None
    update_plan_md(
        run_folder,
        node_id,
        "passed",
        elapsed_secs=elapsed_secs,
        output_summary=summary,
        signal=signal,
        impl_name=impl_name,
        repo_root=repo_root,
    )
