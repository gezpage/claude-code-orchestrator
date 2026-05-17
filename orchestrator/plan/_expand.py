"""Generic stage node expansion driven by StageConfig.expansion.

Thread safety: expand_nodes acquires _plan_lock before calling private helpers.
Private _expand_* functions must NOT be called without holding the lock.
"""

from pathlib import Path

from orchestrator.plan._graph import Edge, Node, Subgraph, load_graph, save_graph
from orchestrator.plan._helpers import _read_slice_title, _track_node_id
from orchestrator.plan._render import replace_mermaid_block
from orchestrator.plan._update import _plan_lock
from orchestrator.profile import ExpansionKind, StageConfig


def expand_nodes(
    run_folder: Path,
    stage: StageConfig,
    *,
    tracks: list[dict] | None = None,
    planning_elapsed_secs: float | None = None,
    slice_files: list[str] | None = None,
    slice_groups: list[list[str]] | None = None,
) -> dict[str, str]:
    """Thread-safe. Dispatches on stage.expansion; uses stage.name for node IDs.

    Returns a mapping of logical-name → mermaid-node-id (non-empty for TRACKS only).
    """
    with _plan_lock:
        if stage.expansion == ExpansionKind.TRACKS:
            return _expand_tracks(run_folder, stage.name, stage.mode, tracks or [], planning_elapsed_secs)
        if stage.expansion == ExpansionKind.SLICES:
            wave_verification_enabled = stage.wave_verification is not None and stage.wave_verification.enabled
            _expand_slices(
                run_folder,
                stage.name,
                stage.mode,
                stage.slices_from_stage or "",
                slice_files or [],
                slice_groups or [],
                wave_verification_enabled=wave_verification_enabled,
            )
        return {}


def _expand_tracks(
    run_folder: Path,
    stage_name: str,
    stage_mode: str,
    tracks: list[dict],
    planning_elapsed_secs: float | None,
) -> dict[str, str]:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not tracks:
        return {}
    graph = load_graph(run_folder)
    if graph is None or stage_name not in graph.nodes:
        return {}

    sg_id = f"sg_{stage_name}"
    display_name = stage_name.replace("_", " ").title()
    graph.subgraphs.setdefault(sg_id, Subgraph(id=sg_id, display=display_name))

    planning_node = f"{stage_name}_planning"
    fanout_node = f"{stage_name}_fanout"
    fanin_node = f"{stage_name}_fanin"
    track_node_ids = {t["name"]: _track_node_id(stage_name, t["name"]) for t in tracks}
    parallel = len(tracks) > 1

    graph.remove_node(stage_name)
    graph.add_node(
        Node(
            id=planning_node,
            display=f"{display_name} Planning",
            impl="planning",
            status="passed",
            elapsed_secs=planning_elapsed_secs,
            css_class="complete",
            subgraph=sg_id,
            mode=stage_mode,
            stage_dir=stage_name,
            file_suffix="planning",
        )
    )
    if parallel:
        graph.add_node(Node(id=fanout_node, shape="circle", css_class="fannode", subgraph=sg_id))
    for track in tracks:
        tid = track_node_ids[track["name"]]
        graph.add_node(
            Node(
                id=tid,
                display=track["name"].replace("-", " ").title(),
                impl=track["name"],
                css_class="pending",
                subgraph=sg_id,
                mode=stage_mode,
                stage_dir=stage_name,
                file_suffix=track["name"],
            )
        )
    if parallel:
        graph.add_node(Node(id=fanin_node, shape="circle", css_class="fannode", subgraph=sg_id))

    # Splice the stage node out of the edge graph, capturing the downstream target.
    incoming, downstream, kept = _splice_node(graph.edges, stage_name, planning_node)
    graph.edges = kept + incoming
    if parallel:
        track_ids = list(track_node_ids.values())
        graph.edges.append(Edge(steps=[[planning_node], [fanout_node]]))
        graph.edges.append(Edge(steps=[[fanout_node], track_ids]))
        graph.edges.append(Edge(steps=[track_ids, [fanin_node]]))
        if downstream:
            graph.edges.append(Edge(steps=[[fanin_node], downstream]))
    else:
        track_id = next(iter(track_node_ids.values()))
        chain: list[list[str]] = [[planning_node], [track_id]]
        if downstream:
            chain.append(downstream)
        graph.edges.append(Edge(steps=chain))

    save_graph(run_folder, graph)
    replace_mermaid_block(plan_path, graph)
    return track_node_ids


def _expand_slices(
    run_folder: Path,
    stage_name: str,
    stage_mode: str,
    prior_stage_name: str,
    slice_files: list[str],
    slice_groups: list[list[str]],
    wave_verification_enabled: bool = False,
) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not slice_files:
        return
    graph = load_graph(run_folder)
    if graph is None or stage_name not in graph.nodes:
        return

    if not slice_groups:
        slice_groups = [[sf] for sf in slice_files]

    all_slices = [sf for group in slice_groups for sf in group]
    slice_to_id = {sf: f"impl_{i + 1}" for i, sf in enumerate(all_slices)}
    sg_id = f"sg_{stage_name}"
    display_name = stage_name.replace("_", " ").title()
    graph.subgraphs.setdefault(sg_id, Subgraph(id=sg_id, display=display_name))

    graph.remove_node(stage_name)
    for g_idx, group in enumerate(slice_groups):
        if len(group) > 1:
            graph.add_node(Node(id=f"fanout_{g_idx + 1}", shape="circle", css_class="fannode", subgraph=sg_id))
        for sf in group:
            nid = slice_to_id[sf]
            i = all_slices.index(sf)
            title = _read_slice_title(sf) or f"{display_name} Slice {i + 1}"
            graph.add_node(
                Node(
                    id=nid,
                    display=f"{display_name} Slice {i + 1}",
                    impl=title,
                    css_class="pending",
                    subgraph=sg_id,
                    mode=stage_mode,
                    stage_dir=stage_name,
                    file_suffix=nid,
                )
            )
        if len(group) > 1:
            graph.add_node(Node(id=f"fanin_{g_idx + 1}", shape="circle", css_class="fannode", subgraph=sg_id))
        # A wave-verify node represents *integration* health (merged branch) and is
        # deliberately a separate, deterministic node from the slice nodes so a
        # passing slice can never look like a passing integration. See ADR-031.
        if wave_verification_enabled:
            wave_id = f"wave_verify_{g_idx + 1}"
            graph.add_node(
                Node(
                    id=wave_id,
                    display=f"Wave {g_idx + 1} Verification",
                    css_class="pending",
                    subgraph=sg_id,
                    mode="deterministic",
                    stage_dir="wave-verification",
                    file_suffix=f"wave-{g_idx + 1}",
                )
            )

    # Drop both prior→stage and stage→next; we replace them with a chain/fan structure.
    _, downstream, kept = _splice_node(graph.edges, stage_name, planning_node=None)
    graph.edges = kept

    prev: list[str] = [prior_stage_name] if prior_stage_name else []
    for g_idx, group in enumerate(slice_groups):
        if len(group) == 1:
            nid = slice_to_id[group[0]]
            if prev:
                graph.edges.append(Edge(steps=[prev, [nid]]))
            prev = [nid]
        else:
            fanout_id = f"fanout_{g_idx + 1}"
            fanin_id = f"fanin_{g_idx + 1}"
            fan_ids = [slice_to_id[sf] for sf in group]
            if prev:
                graph.edges.append(Edge(steps=[prev, [fanout_id]]))
            graph.edges.append(Edge(steps=[[fanout_id], fan_ids]))
            graph.edges.append(Edge(steps=[fan_ids, [fanin_id]]))
            prev = [fanin_id]
        if wave_verification_enabled:
            wave_id = f"wave_verify_{g_idx + 1}"
            graph.edges.append(Edge(steps=[prev, [wave_id]]))
            prev = [wave_id]
    if downstream and prev:
        graph.edges.append(Edge(steps=[prev, downstream]))

    save_graph(run_folder, graph)
    replace_mermaid_block(plan_path, graph)


def _splice_node(
    edges: list[Edge], node_id: str, planning_node: str | None
) -> tuple[list[Edge], list[str] | None, list[Edge]]:
    """Remove every edge that references ``node_id``.

    Incoming edges (those whose last step contains ``node_id``) are rewritten to
    point at ``planning_node`` if provided, and returned in ``incoming``.
    Outgoing edges (those whose first step contains ``node_id``) are dropped;
    the immediate next step is returned as ``downstream`` so the caller can
    re-attach the new structure.

    Returns ``(incoming, downstream, kept)`` — the caller decides how to merge.
    """
    incoming: list[Edge] = []
    downstream: list[str] | None = None
    kept: list[Edge] = []
    for edge in edges:
        if not edge.steps:
            continue
        if node_id in edge.steps[0]:
            if downstream is None and len(edge.steps) > 1:
                downstream = [n for n in edge.steps[1] if n != node_id]
            continue
        if node_id in edge.steps[-1]:
            new_last = [planning_node if n == node_id else n for n in edge.steps[-1]] if planning_node else None
            if new_last:
                incoming.append(Edge(steps=[*edge.steps[:-1], new_last]))
            continue
        kept.append(edge)
    return incoming, downstream, kept
