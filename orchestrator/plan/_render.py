"""Renderer: serialize a :class:`Graph` to a complete mermaid fenced block."""

from __future__ import annotations

from pathlib import Path

from orchestrator.plan._constants import _CLASSDEFS
from orchestrator.plan._graph import Edge, Graph, Node
from orchestrator.plan._helpers import _node_label

_LEGEND_NODE_ID = "legend_files"
_LEGEND_SUBGRAPH_ID = "sg_legend"
# Files that should never appear in the diagram (plan.md is the diagram's host).
_LEGEND_SKIP = {"plan.md"}


def render_block(graph: Graph, run_folder: Path | None = None) -> str:
    """Render the full ```mermaid...``` fenced block, ending with a trailing newline.

    When ``run_folder`` is provided, files in the run folder are matched to nodes
    via ``Node.stage_dir`` / ``Node.file_suffix`` and embedded as clickable links
    inside each node's label. Files that don't match any node are surfaced in a
    "Legend" subgraph as a single multi-link node.
    """
    node_files, legend_files = _scan_files(graph, run_folder) if run_folder else ({}, [])

    lines: list[str] = ["```mermaid"]
    if graph.init_directive:
        lines.append(graph.init_directive)
    lines.append("flowchart TD")

    # Nodes outside any subgraph come first (Start, Done, etc.)
    for node in graph.nodes.values():
        if node.subgraph is None:
            lines.append(f"    {_node_decl(node, node_files.get(node.id, []))}")

    # Then each subgraph with its member nodes.
    for sg in graph.subgraphs.values():
        members = [n for n in graph.nodes.values() if n.subgraph == sg.id]
        if not members:
            continue
        lines.append(f'    subgraph {sg.id}["{sg.display}"]')
        for node in members:
            lines.append(f"    {_node_decl(node, node_files.get(node.id, []))}")
        lines.append("    end")

    # Legend subgraph (run-folder files unattached to any node). Rendered after
    # other subgraphs but linked to Start via an invisible edge so mermaid lays
    # it out near the top instead of trailing.
    if legend_files:
        legend_label = _legend_label(legend_files)
        lines.append(f'    subgraph {_LEGEND_SUBGRAPH_ID}["Legend"]')
        lines.append(f'    {_LEGEND_NODE_ID}["{legend_label}"]')
        lines.append("    end")

    # Edges.
    for edge in graph.edges:
        rendered = _edge_str(edge)
        if rendered:
            lines.append(f"    {rendered}")

    # Anchor the Legend near the top with an invisible link to Start.
    if legend_files and "Start" in graph.nodes:
        lines.append(f"    {_LEGEND_NODE_ID} ~~~ Start")

    # Class definitions and assignments.
    lines.extend(_CLASSDEFS)
    for node in graph.nodes.values():
        lines.append(f"    class {node.id} {node.css_class}")
    if legend_files:
        lines.append(f"    class {_LEGEND_NODE_ID} pending")

    lines.append("```")
    return "\n".join(lines) + "\n"


def _node_decl(node: Node, files: list[Path]) -> str:
    label = _label_for(node, files)
    if node.shape == "stadium":
        return f'{node.id}(["{label}"])'
    if node.shape == "hex":
        return f'{node.id}{{{{"{label}"}}}}'
    if node.shape == "circle":
        # Circle pseudo-nodes always carry the raw " " label, unquoted.
        return f'{node.id}((" "))'
    return f'{node.id}["{label}"]'


def _label_for(node: Node, files: list[Path]) -> str:
    file_links = _file_links_for(node, files)
    if node.raw_label is not None:
        # raw_label nodes (Start/Done/interactive gates) carry a hand-crafted top
        # line; append Mode and any file links below using the same HTML format.
        extras: list[str] = []
        if node.mode:
            extras.append(f"Mode: {node.mode}")
        if file_links:
            extras.append(" · ".join(f"<a href='{url}'>{name}</a>" for name, url in file_links))
        if not extras:
            return node.raw_label
        return "<br/>".join([node.raw_label, *extras])
    return _node_label(
        node.display,
        node.impl,
        status=node.status,
        elapsed_secs=node.elapsed_secs,
        mode=node.mode,
        file_links=file_links,
    )


def _file_links_for(node: Node, files: list[Path]) -> list[tuple[str, str]]:
    """Return (display, relative_url) pairs for each file associated with the node."""
    links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for f in files:
        # The url is computed by the caller (relative to run_folder).
        url = f.as_posix() if isinstance(f, Path) else str(f)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        links.append((_link_display(f.name if isinstance(f, Path) else url), url))
    return links


def _link_display(name: str) -> str:
    """Strip extension and translate -prompt / -output suffixes to friendly names."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.endswith("-prompt"):
        return "Prompt"
    if stem.endswith("-output"):
        return "Output"
    return stem


def _scan_files(
    graph: Graph,
    run_folder: Path,
) -> tuple[dict[str, list[Path]], list[Path]]:
    """Walk run_folder and assign each file to a node (or to the legend).

    Returns ``(node_files, legend_files)``. ``node_files`` maps node id to a list
    of file paths *relative to* ``run_folder`` (so they render correctly in the
    diagram). ``legend_files`` is a list of relative paths that didn't match any
    node — root-level files and stage-dir files without a matching node.
    """
    run_folder = Path(run_folder)
    if not run_folder.exists():
        return {}, []

    by_stage_dir: dict[str, list[Node]] = {}
    for node in graph.nodes.values():
        if node.stage_dir:
            by_stage_dir.setdefault(node.stage_dir, []).append(node)

    node_files: dict[str, list[Path]] = {}
    legend_files: list[Path] = []

    for path in sorted(run_folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run_folder)
        if rel.name in _LEGEND_SKIP:
            continue
        # Hidden files (e.g. _plan_graph.yaml, _state.yaml) at the root go to the legend.
        parts = rel.parts
        if len(parts) == 1:
            legend_files.append(rel)
            continue
        stage_dir = parts[0]
        nodes = by_stage_dir.get(stage_dir, [])
        target = _match_node(rel.name, stage_dir, nodes)
        if target is not None:
            node_files.setdefault(target.id, []).append(rel)
        else:
            legend_files.append(rel)

    # Stable, friendly ordering inside each node: prompt → output → others alphabetically.
    for files in node_files.values():
        files.sort(key=_file_sort_key)
    legend_files.sort(key=_file_sort_key)
    return node_files, legend_files


def _match_node(file_name: str, stage_dir: str, nodes: list[Node]) -> Node | None:
    """Find the node whose stage_dir/file_suffix matches this file.

    Match rules, in order:
    1. ``{stage_dir}-{file_suffix}`` prefix (after stripping any -prompt/-output) → that node.
    2. Bare ``{stage_dir}`` prefix → the stage_dir's no-suffix node.
    3. Anything else (e.g. ``prd.md`` inside specification/) → the stage_dir's
       no-suffix node, since it represents a stage-level artifact.
    """
    if not nodes:
        return None
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    pattern_stem = stem
    for suffix in ("-prompt", "-output"):
        if pattern_stem.endswith(suffix):
            pattern_stem = pattern_stem[: -len(suffix)]
            break

    bare = None
    for node in nodes:
        if node.file_suffix:
            if pattern_stem == f"{stage_dir}-{node.file_suffix}":
                return node
        else:
            bare = node

    if pattern_stem == stage_dir and bare is not None:
        return bare

    # Stage-level artefacts with arbitrary names (prd.md, S-01-….md, review-log.md)
    # attach to the stage's primary node when it exists.
    if bare is not None:
        return bare
    return None


def _file_sort_key(rel: Path) -> tuple[int, str]:
    name = rel.name
    if name.endswith("-prompt.md"):
        return (0, name)
    if name.endswith("-output.md"):
        return (1, name)
    return (2, name)


def _legend_label(legend_files: list[Path]) -> str:
    links = " · ".join(f"<a href='{rel.as_posix()}'>{_link_display(rel.name)}</a>" for rel in legend_files)
    return f"Other files<br/>{links}"


def _edge_str(edge: Edge) -> str:
    parts = [" & ".join(step) for step in edge.steps if step]
    if len(parts) < 2:
        return ""
    return " --> ".join(parts)


def write_plan_md(plan_path, header: str, graph: Graph) -> None:
    """Create plan.md with header + rendered mermaid block. Used by init only."""
    plan_path = Path(plan_path)
    body = "\n".join([header, "", "## Orchestration Flow", "", render_block(graph, plan_path.parent), ""])
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(body)


def replace_mermaid_block(plan_path, graph: Graph) -> None:
    """Replace the existing ```mermaid``` block in plan.md with a fresh render.

    Preserves everything before and after the fence (run header, stage sections,
    run summary, file manifest). If no fence is found, the file is left
    unchanged — callers should handle creation themselves.
    """
    plan_path = Path(plan_path)
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
    new_block = render_block(graph, plan_path.parent)
    plan_path.write_text(content[:start] + new_block + content[end:])
