"""Fix-cycle node injection for review stages.

Thread safety: add_fix_cycle_node acquires _plan_lock before delegating.
_add_fix_cycle_node must NOT be called without holding the lock.
"""

from pathlib import Path

from orchestrator.plan._helpers import _node_label
from orchestrator.plan._update import _plan_lock


def add_fix_cycle_node(run_folder: Path, cycle_num: int, reviewers: list[str]) -> None:
    """Insert fix-implementation and re-review nodes into the mermaid diagram."""
    with _plan_lock:
        _add_fix_cycle_node(run_folder, cycle_num, reviewers)


def _add_fix_cycle_node(run_folder: Path, cycle_num: int, reviewers: list[str]) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not reviewers:
        return

    round_num = cycle_num + 1
    fix_node_id = f"fix_impl_{cycle_num}"

    # Source nodes: original sub-nodes for cycle 1, prior-round re-review nodes for later
    if cycle_num == 1:
        source_ids = [f"review_{r}" for r in reviewers]
    else:
        source_ids = [f"review_{r}_{round_num - 1}" for r in reviewers]

    rerun_ids = [f"review_{r}_{round_num}" for r in reviewers]
    content = plan_path.read_text()

    fix_label = _node_label("Fix Implementation", f"fix-{cycle_num}", status="in_progress")
    new_subgraph_lines = [f'    subgraph sg_fix_{cycle_num}["Fix Cycle {round_num}"]']
    new_subgraph_lines.append(f'    {fix_node_id}["{fix_label}"]')
    for reviewer, rerun_id in zip(reviewers, rerun_ids, strict=True):
        rerun_label = _node_label(reviewer.title(), reviewer, status="pending")
        new_subgraph_lines.append(f'    {rerun_id}["{rerun_label}"]')
    new_subgraph_lines.append("    end")

    src_str = " & ".join(source_ids)
    dst_str = " & ".join(rerun_ids)
    new_edges = [
        f"    {src_str} --> {fix_node_id}",
        f"    {fix_node_id} --> {dst_str}",
    ]
    new_classes = [f"    class {fix_node_id} active"]
    for rerun_id in rerun_ids:
        new_classes.append(f"    class {rerun_id} pending")

    classdef_pos = content.find("    classDef complete")
    if classdef_pos < 0:
        return  # malformed mermaid block
    insert = "\n".join(new_subgraph_lines) + "\n" + "\n".join(new_edges) + "\n"
    content = content[:classdef_pos] + insert + content[classdef_pos:]

    last_fence = content.rfind("```")
    if last_fence >= 0:
        content = content[:last_fence] + "\n".join(new_classes) + "\n" + content[last_fence:]

    plan_path.write_text(content)
