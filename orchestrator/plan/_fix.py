"""Fix-cycle node injection for review and verification stages.

Thread safety: public helpers acquire _plan_lock before delegating. Private
helpers must NOT be called without holding the lock.
"""

from pathlib import Path

from orchestrator.plan._constants import _STATUS_CLASS
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

    # Subgraph label tracks the fix cycle itself (1-indexed), not the review round it
    # feeds into. round_num = cycle_num + 1 because Round 1 is the initial review before
    # any fix has run; using round_num here produced misleading labels like "Fix Cycle 3"
    # after only two fix runs.
    graph.add_subgraph(Subgraph(id=sg_id, display=f"Fix Cycle {cycle_num}"))
    graph.add_node(
        Node(
            id=fix_node_id,
            display="Fix Implementation",
            impl=f"fix-{cycle_num}",
            status="in_progress",
            css_class="active",
            subgraph=sg_id,
            stage_dir="fix-implementation",
            file_suffix=str(cycle_num),
        )
    )
    for reviewer, rerun_id in zip(reviewers, rerun_ids, strict=True):
        # Inherit the original sub-node's display so re-review nodes carry the same
        # parent-stage suffix (e.g. "Implementation Review") without re-deriving it here.
        original = graph.nodes.get(f"review_{reviewer}")
        rerun_display = original.display if original is not None else reviewer.title()
        graph.add_node(
            Node(
                id=rerun_id,
                display=rerun_display,
                impl=reviewer,
                status="pending",
                css_class="pending",
                subgraph=sg_id,
                stage_dir="review",
                file_suffix=f"{reviewer}-round{round_num}",
            )
        )

    graph.edges.append(Edge(steps=[source_ids, [fix_node_id]]))
    graph.edges.append(Edge(steps=[[fix_node_id], rerun_ids]))
    if downstream:
        graph.edges.append(Edge(steps=[rerun_ids, downstream]))

    save_graph(run_folder, graph)
    replace_mermaid_block(plan_path, graph)


_FIX_VERIFICATION_NODE_ID = "fix_verification"
_VERIFICATION_NODE_ID = "verification"


def add_fix_verification_node(
    run_folder: Path,
    *,
    status: str = "in_progress",
    backend: str = "",
    model: str = "",
) -> None:
    """Inject a first-class ``fix_verification`` node between ``verification``
    and its downstream successor when a fix-verification cycle fires.

    Without this, the fix-verification agent's prompt/output files land in the
    "Other files" strip below the diagram and the workflow visually skips the
    remediation step entirely. See issue #194.

    Idempotent: re-calling with an existing node is a no-op so the helper can
    be invoked safely on resume. Best-effort: silently no-ops when the plan,
    graph, or verification node is missing.
    """
    with _plan_lock:
        _add_fix_verification_node(run_folder, status=status, backend=backend, model=model)


def _add_fix_verification_node(
    run_folder: Path,
    *,
    status: str,
    backend: str,
    model: str,
) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists():
        return
    graph = load_graph(run_folder)
    if graph is None or _VERIFICATION_NODE_ID not in graph.nodes:
        return
    if _FIX_VERIFICATION_NODE_ID in graph.nodes:
        return

    verify_node = graph.nodes[_VERIFICATION_NODE_ID]
    css_class = _STATUS_CLASS.get(status, "pending")
    graph.add_node(
        Node(
            id=_FIX_VERIFICATION_NODE_ID,
            display="Fix Verification",
            status=status,
            css_class=css_class,
            subgraph=verify_node.subgraph,
            mode="auto",
            stage_dir="fix-verification",
            backend=backend,
            model=model,
        )
    )

    # Splice the new node between verification and every existing downstream
    # successor of verification. We rewrite the first step (source) of edges
    # that originate from a bare verification source; this preserves any
    # multi-step / fan-out shape downstream while moving its anchor to
    # fix_verification. Edges where verification appears as a target are left
    # untouched.
    for edge in graph.edges:
        if edge.steps and edge.steps[0] == [_VERIFICATION_NODE_ID]:
            edge.steps[0] = [_FIX_VERIFICATION_NODE_ID]
    graph.edges.append(Edge(steps=[[_VERIFICATION_NODE_ID], [_FIX_VERIFICATION_NODE_ID]]))

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
