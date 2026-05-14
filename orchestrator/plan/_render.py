"""Renderer: serialize a :class:`Graph` to a complete mermaid fenced block."""

from __future__ import annotations

from orchestrator.plan._constants import _CLASSDEFS
from orchestrator.plan._graph import Edge, Graph, Node
from orchestrator.plan._helpers import _node_label


def render_block(graph: Graph) -> str:
    """Render the full ```mermaid...``` fenced block, ending with a trailing newline."""
    lines: list[str] = ["```mermaid"]
    if graph.init_directive:
        lines.append(graph.init_directive)
    lines.append("flowchart TD")

    # Nodes outside any subgraph come first (Start, Done, etc.)
    for node in graph.nodes.values():
        if node.subgraph is None:
            lines.append(f"    {_node_decl(node)}")

    # Then each subgraph with its member nodes.
    for sg in graph.subgraphs.values():
        members = [n for n in graph.nodes.values() if n.subgraph == sg.id]
        if not members:
            continue
        lines.append(f'    subgraph {sg.id}["{sg.display}"]')
        for node in members:
            lines.append(f"    {_node_decl(node)}")
        lines.append("    end")

    # Edges.
    for edge in graph.edges:
        rendered = _edge_str(edge)
        if rendered:
            lines.append(f"    {rendered}")

    # Class definitions and assignments.
    lines.extend(_CLASSDEFS)
    for node in graph.nodes.values():
        lines.append(f"    class {node.id} {node.css_class}")

    lines.append("```")
    return "\n".join(lines) + "\n"


def _node_decl(node: Node) -> str:
    label = _label_for(node)
    if node.shape == "stadium":
        return f'{node.id}(["{label}"])'
    if node.shape == "hex":
        return f'{node.id}{{{{"{label}"}}}}'
    if node.shape == "circle":
        # Circle pseudo-nodes always carry the raw " " label, unquoted.
        return f'{node.id}((" "))'
    return f'{node.id}["{label}"]'


def _label_for(node: Node) -> str:
    if node.raw_label is not None:
        return node.raw_label
    return _node_label(node.display, node.impl, status=node.status, elapsed_secs=node.elapsed_secs)


def _edge_str(edge: Edge) -> str:
    parts = [" & ".join(step) for step in edge.steps if step]
    if len(parts) < 2:
        return ""
    return " --> ".join(parts)


def write_plan_md(plan_path, header: str, graph: Graph) -> None:
    """Create plan.md with header + rendered mermaid block. Used by init only."""
    body = "\n".join([header, "", "## Orchestration Flow", "", render_block(graph), ""])
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(body)


def replace_mermaid_block(plan_path, graph: Graph) -> None:
    """Replace the existing ```mermaid``` block in plan.md with a fresh render.

    Preserves everything before and after the fence (run header, stage sections,
    run summary, file manifest). If no fence is found, the file is left
    unchanged — callers should handle creation themselves.
    """
    content = plan_path.read_text()
    start = content.find("```mermaid")
    if start < 0:
        return
    end_fence = content.find("```", start + len("```mermaid"))
    if end_fence < 0:
        return
    end = end_fence + len("```")
    # Consume one trailing newline so consecutive renders don't grow blank lines.
    if end < len(content) and content[end] == "\n":
        end += 1
    new_block = render_block(graph)
    plan_path.write_text(content[:start] + new_block + content[end:])
