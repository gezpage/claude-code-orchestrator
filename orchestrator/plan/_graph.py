"""Workflow graph model — the single source of truth for the orchestration diagram.

The mermaid block in plan.md is a *projection* of this graph. All plan mutations
(init, expand, fix-cycle, status update) operate on the graph in memory, then
re-render the diagram by calling :func:`render_block`.

The graph is persisted alongside plan.md as ``_plan_graph.yaml`` so concurrent
runs and subsequent process invocations can resume the projection.

Thread safety: callers must hold ``_plan_lock`` from :mod:`._update` while
loading, mutating, and saving a graph. ``load_graph`` / ``save_graph`` are not
intrinsically locked because the locking discipline mirrors the existing
plan-module contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

_GRAPH_FILENAME = "_plan_graph.yaml"


@dataclass
class Node:
    id: str
    shape: str = "rect"  # rect | stadium | hex | circle
    display: str = ""
    impl: str = ""
    mode: str = ""
    # Runner backend (e.g. claude_code, codex_cli, deterministic) and model
    # name (e.g. claude-opus-4-7, gpt-5). Populated from resolved agent config so
    # the diagram shows which agent ran each stage.
    backend: str = ""
    model: str = ""
    # stage_dir + file_suffix locate this node's prompt/output files in the run folder.
    # A file matches when its parent directory equals stage_dir and its stem (after
    # stripping any -prompt / -output suffix) equals f"{stage_dir}-{file_suffix}" — or
    # stage_dir itself when file_suffix is empty.
    stage_dir: str = ""
    file_suffix: str = ""
    status: str = "pending"
    elapsed_secs: float | None = None
    css_class: str = "pending"
    subgraph: str | None = None
    raw_label: str | None = None  # if set, used verbatim instead of the composed label


@dataclass
class Subgraph:
    id: str
    display: str


@dataclass
class Edge:
    """A mermaid edge or edge-chain. Each step is a set of node ids.

    Renders as ``" --> ".join(" & ".join(step) for step in steps)``. A simple
    edge ``A --> B`` is ``[["A"], ["B"]]``; a fan-out ``A --> B & C`` is
    ``[["A"], ["B", "C"]]``; a chain ``A --> B --> C`` is ``[["A"], ["B"], ["C"]]``.
    """

    steps: list[list[str]] = field(default_factory=list)


@dataclass
class Graph:
    init_directive: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    subgraphs: dict[str, Subgraph] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node) -> Node:
        self.nodes[node.id] = node
        return node

    def add_subgraph(self, sg: Subgraph) -> Subgraph:
        self.subgraphs[sg.id] = sg
        return sg

    def remove_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)

    def references(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if any(node_id in step for step in e.steps)]

    def remove_edges_referencing(self, node_id: str) -> list[Edge]:
        kept: list[Edge] = []
        removed: list[Edge] = []
        for e in self.edges:
            if any(node_id in step for step in e.steps):
                removed.append(e)
            else:
                kept.append(e)
        self.edges = kept
        return removed


def graph_path(run_folder: Path) -> Path:
    return Path(run_folder) / _GRAPH_FILENAME


def load_graph(run_folder: Path) -> Graph | None:
    p = graph_path(run_folder)
    if not p.exists():
        return None
    raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}
    nodes = {nid: Node(id=nid, **data) for nid, data in (raw.get("nodes") or {}).items()}
    subgraphs = {sid: Subgraph(id=sid, **data) for sid, data in (raw.get("subgraphs") or {}).items()}
    edges = [Edge(steps=[list(step) for step in e.get("steps", [])]) for e in raw.get("edges") or []]
    return Graph(
        init_directive=raw.get("init_directive", ""),
        nodes=nodes,
        subgraphs=subgraphs,
        edges=edges,
    )


def save_graph(run_folder: Path, graph: Graph) -> None:
    payload = {
        "init_directive": graph.init_directive,
        "nodes": {nid: _node_payload(n) for nid, n in graph.nodes.items()},
        "subgraphs": {sid: {"display": sg.display} for sid, sg in graph.subgraphs.items()},
        "edges": [{"steps": [list(step) for step in e.steps]} for e in graph.edges],
    }
    graph_path(run_folder).write_text(yaml.safe_dump(payload, sort_keys=False))


def _node_payload(n: Node) -> dict[str, Any]:
    d = asdict(n)
    d.pop("id")
    return d
