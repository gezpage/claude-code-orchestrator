"""Fix-cycle node injection for review stages.

Thread safety: add_fix_cycle_node acquires _plan_lock before delegating.
_add_fix_cycle_node must NOT be called without holding the lock.
"""

from pathlib import Path

from orchestrator.plan._graph import Edge, Node, Subgraph, load_graph, save_graph
from orchestrator.plan._render import replace_mermaid_block
from orchestrator.plan._update import _plan_lock


def add_fix_cycle_node(run_folder: Path, cycle_num: int, reviewers: list[str]) -> None:
    """Insert fix-implementation and re-review nodes into the workflow graph."""
    with _plan_lock:
        _add_fix_cycle_node(run_folder, cycle_num, reviewers)


def _add_fix_cycle_node(run_folder: Path, cycle_num: int, reviewers: list[str]) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not reviewers:
        return
    graph = load_graph(run_folder)
    if graph is None:
        return

    round_num = cycle_num + 1
    fix_node_id = f"fix_impl_{cycle_num}"
    sg_id = f"sg_fix_{cycle_num}"

    # Failing reviewers from this round: original sub-nodes for cycle 1, prior-round re-reviews otherwise.
    if cycle_num == 1:
        source_ids = [f"review_{r}" for r in reviewers]
    else:
        source_ids = [f"review_{r}_{round_num - 1}" for r in reviewers]
    rerun_ids = [f"review_{r}_{round_num}" for r in reviewers]

    # Carve the failing reviewers out of the existing fan-in edge and remember the downstream target.
    downstream = _strip_sources_from_fanin(graph.edges, source_ids)

    graph.add_subgraph(Subgraph(id=sg_id, display=f"Fix Cycle {round_num}"))
    graph.add_node(
        Node(
            id=fix_node_id,
            display="Fix Implementation",
            impl=f"fix-{cycle_num}",
            status="in_progress",
            css_class="active",
            subgraph=sg_id,
        )
    )
    for reviewer, rerun_id in zip(reviewers, rerun_ids, strict=True):
        graph.add_node(
            Node(
                id=rerun_id,
                display=reviewer.title(),
                impl=reviewer,
                status="pending",
                css_class="pending",
                subgraph=sg_id,
            )
        )

    graph.edges.append(Edge(steps=[source_ids, [fix_node_id]]))
    graph.edges.append(Edge(steps=[[fix_node_id], rerun_ids]))
    if downstream:
        graph.edges.append(Edge(steps=[rerun_ids, downstream]))

    save_graph(run_folder, graph)
    replace_mermaid_block(plan_path, graph)


def _strip_sources_from_fanin(edges: list[Edge], source_ids: list[str]) -> list[str] | None:
    """Remove ``source_ids`` from any fan-in edge that contains them in its first step.

    Returns the downstream target captured from that edge (so the caller can re-attach
    re-review nodes), or ``None`` if no matching edge exists.
    """
    source_set = set(source_ids)
    for idx, edge in enumerate(edges):
        if len(edge.steps) < 2:
            continue
        first = edge.steps[0]
        if not source_set.issubset(set(first)):
            continue
        remaining = [n for n in first if n not in source_set]
        downstream = list(edge.steps[-1])
        if remaining:
            edges[idx] = Edge(steps=[remaining, *edge.steps[1:]])
        else:
            edges.pop(idx)
        return downstream
    return None
