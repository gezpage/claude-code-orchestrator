"""Renderer: serialize a :class:`Graph` to a complete mermaid fenced block."""

from __future__ import annotations

from pathlib import Path

from orchestrator.plan._constants import _CLASSDEFS
from orchestrator.plan._graph import Edge, Graph, Node
from orchestrator.plan._helpers import _node_label

_LEGEND_NODE_ID = "legend_files"
# Files that should never appear in the diagram (plan.md is the diagram's host).
_LEGEND_SKIP = {"plan.md"}
# `color: inherit` on the anchor keeps link text in the node's white class colour
# instead of the browser's default link blue, which is unreadable on the green/orange
# status backgrounds.
_LINK_STYLE = "color:inherit;text-decoration:underline"


def render_block(graph: Graph, run_folder: Path | None = None) -> str:
    """Render the full ```mermaid...``` fenced block, ending with a trailing newline.

    When ``run_folder`` is provided, files in the run folder are matched to nodes
    via ``Node.stage_dir`` / ``Node.file_suffix`` and embedded as clickable links
    inside each node's label. Files that don't match any node are surfaced in a
    separate "Other files" node placed near the bottom of the diagram.
    """
    node_files, legend_files = _scan_files(graph, run_folder) if run_folder else ({}, [])
    href_prefix = _href_prefix(run_folder) if run_folder else ""

    lines: list[str] = ["```mermaid"]
    if graph.init_directive:
        lines.append(graph.init_directive)
    lines.append("flowchart TD")

    # Nodes outside any subgraph come first (Start, Done, etc.)
    for node in graph.nodes.values():
        if node.subgraph is None:
            lines.append(f"    {_node_decl(node, node_files.get(node.id, []), href_prefix)}")

    # Then each subgraph with its member nodes.
    for sg in graph.subgraphs.values():
        members = [n for n in graph.nodes.values() if n.subgraph == sg.id]
        if not members:
            continue
        lines.append(f'    subgraph {sg.id}["{sg.display}"]')
        for node in members:
            lines.append(f"    {_node_decl(node, node_files.get(node.id, []), href_prefix)}")
        lines.append("    end")

    # "Other files" floats as a bare node (no subgraph wrapper) at the bottom of the
    # diagram. Anchoring it to Done's predecessor below makes it a sibling of Done so
    # mermaid lays it out alongside, not above.
    if legend_files:
        legend_label = _legend_label(legend_files, href_prefix)
        lines.append(f'    {_LEGEND_NODE_ID}["{legend_label}"]')

    # Edges.
    for edge in graph.edges:
        rendered = _edge_str(edge)
        if rendered:
            lines.append(f"    {rendered}")

    # Sibling-of-Done placement: connect Done's predecessor to legend with an
    # invisible link. Falls back to anchoring against Done itself if no predecessor
    # is wired yet (init render, fresh graphs).
    if legend_files:
        anchor = _legend_anchor(graph)
        if anchor:
            lines.append(f"    {anchor} ~~~ {_LEGEND_NODE_ID}")

    # Class definitions and assignments.
    lines.extend(_CLASSDEFS)
    for node in graph.nodes.values():
        lines.append(f"    class {node.id} {node.css_class}")
    if legend_files:
        lines.append(f"    class {_LEGEND_NODE_ID} pending")

    lines.append("```")
    return "\n".join(lines) + "\n"


def _node_decl(node: Node, files: list[Path], href_prefix: str) -> str:
    label = _label_for(node, files, href_prefix)
    if node.shape == "stadium":
        return f'{node.id}(["{label}"])'
    if node.shape == "hex":
        return f'{node.id}{{{{"{label}"}}}}'
    if node.shape == "circle":
        # Circle pseudo-nodes always carry the raw " " label, unquoted.
        return f'{node.id}((" "))'
    return f'{node.id}["{label}"]'


def _label_for(node: Node, files: list[Path], href_prefix: str) -> str:
    file_links = _file_links_for(files, href_prefix)
    if node.raw_label is not None:
        # raw_label nodes (Start/Done/interactive gates) carry a hand-crafted top
        # line; append Mode and any file links below using the same HTML format.
        extras: list[str] = []
        if node.mode:
            extras.append(f"Mode: {node.mode}")
        if file_links:
            extras.append(_join_links(file_links))
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


def _file_links_for(files: list[Path], href_prefix: str) -> list[tuple[str, str]]:
    """Return (display, url) pairs for each file. URLs are prefixed with the
    run-folder's path from docs-root so mermaid SVG anchors resolve correctly
    regardless of the page URL the diagram is rendered on."""
    links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for f in files:
        rel = f.as_posix() if isinstance(f, Path) else str(f)
        url = f"{href_prefix}{rel}" if href_prefix else rel
        if url in seen_urls:
            continue
        seen_urls.add(url)
        name = f.name if isinstance(f, Path) else rel
        links.append((_link_display(name), url))
    return links


def _join_links(file_links: list[tuple[str, str]]) -> str:
    return " · ".join(f"<a href='{url}' style='{_LINK_STYLE}'>{name}</a>" for name, url in file_links)


def _link_display(name: str) -> str:
    """Strip extension and translate -prompt / -output suffixes to friendly names."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.endswith("-prompt"):
        return "Prompt"
    if stem.endswith("-output"):
        return "Output"
    return stem


def _href_prefix(run_folder: Path) -> str:
    """Return the path-from-docs-root for the run folder, with a trailing slash.

    The orchestrator's path layout is ``{docs-root}/projects/{project}/workflow/runs/
    {feature-slug}/{run-name}/`` (see CLAUDE.md). We anchor on "projects" — everything
    from that segment onward is the docs-root-relative path. Returns an empty string
    when the layout doesn't match (e.g. tests using bare tmp paths), so the caller
    falls back to plain relative URLs.
    """
    parts = Path(run_folder).resolve().parts
    try:
        idx = parts.index("projects")
    except ValueError:
        return ""
    return "/".join(parts[idx:]) + "/"


def _legend_anchor(graph: Graph) -> str | None:
    """Pick the node to invisibly chain the legend off so it lands as a sibling of Done."""
    if "Done" not in graph.nodes:
        return None
    for edge in graph.edges:
        steps = edge.steps
        for i in range(1, len(steps)):
            if "Done" in steps[i] and steps[i - 1]:
                return steps[i - 1][0]
    # No predecessor wired yet — fall back to Done itself so the legend at least
    # ends up downstream of the flow instead of floating at the top.
    return "Done"


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


def _legend_label(legend_files: list[Path], href_prefix: str) -> str:
    file_links = _file_links_for(legend_files, href_prefix)
    return f"Other files<br/>{_join_links(file_links)}"


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
