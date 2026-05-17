"""Renderer: serialize a :class:`Graph` to a complete mermaid fenced block.

The renderer materialises additional nodes around each stage in the graph
(prompt input, output/JSON panel, plus a single Overview node before the
first stage) without changing the underlying graph model. Stage nodes in the
graph keep their original ids; the renderer derives ``{id}_prompt`` and
``{id}_panel`` partners and rewrites edge endpoints so user-defined edges
connect through the materialised structure:

* ``A → B`` becomes ``A_panel --> B_prompt`` (when both stages have partners)
* ``Start → first`` is split into ``Start --> overview`` + ``overview --> first_prompt``
* ``B_prompt --> B`` and ``B --> B_panel`` are emitted as internal chain edges

The graph's :class:`Subgraph` records are ignored at render time. They remain
in the model only because expansion code still creates them for historical
reasons; nothing in the rendered output references them. See ADR-020.
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.plan._constants import _CLASSDEFS
from orchestrator.plan._graph import Edge, Graph, Node
from orchestrator.plan._helpers import _node_label

# Files that should never appear in the diagram (plan.md is the diagram's host).
_LEGEND_SKIP = {"plan.md"}
_OVERVIEW_NODE_ID = "overview"
_PR_NODE_ID = "pr"
_EXECUTIVE_SUMMARY_NODE_ID = "executive_summary"
# Root-level files claimed by a stage node rather than the legend. Keyed by the
# file's basename so subdir matching is unaffected.
_ROOT_FILE_OWNERS: dict[str, str] = {"executive_summary.md": _EXECUTIVE_SUMMARY_NODE_ID}
# Match the canonical GitHub PR URL form so we can derive a per-commit URL
# (`<base>/commit/<sha>`) from the PR URL the orchestrator already stamps on the
# pr stage's panel via ``set_pr_node``. SSH and non-GitHub remotes don't match
# — those just fall through to plain text in the commits block.
_GITHUB_PR_URL_RE = re.compile(r"^(https://github\.com/[^/]+/[^/]+)/pull/\d+")

# Prompt-link styling lives inside the prompt parallelogram label. color:inherit
# defers to the node's class fill so the link reads cleanly on the blue input
# background.
_PROMPT_LINK_STYLE = "color:inherit;text-decoration:underline"

# Output-link styling for the bold header at the top of each stage panel. The
# pale green colour matches the prior output-parallelogram fill so the user can
# still recognise it as the "output" anchor.
_OUTPUT_HEADER_STYLE = "font-size:16px;font-weight:bold;color:#dcfce7;text-decoration:underline;font-family:sans-serif"

# Pill button styling for non-prompt/output artifact links inside the panel.
_PILL_STYLE = (
    "display:inline-block;padding:4px 10px;margin:3px 2px;background:rgba(255,255,255,0.18);"
    "color:inherit;border-radius:4px;text-decoration:none;font-size:14px;font-family:sans-serif"
)

# Wrapper div for the panel content — monospace JSON-friendly font, left-aligned.
_PANEL_DIV_STYLE = "text-align:left;font-family:ui-monospace,monospace;font-size:11px;line-height:1.45;color:#d1d5db"

# Big prominent first line used by Start/Done/Overview/Prompt labels (stage
# nodes get the same span via _node_label).
_TITLE_STYLE = "font-size:18px;font-weight:bold"


def render_block(graph: Graph, run_folder: Path | None = None) -> str:
    """Render the full ```mermaid...``` fenced block, ending with a trailing newline.

    When ``run_folder`` is provided, files in the run folder are matched to nodes
    via ``Node.stage_dir`` / ``Node.file_suffix`` and surfaced as links on the
    appropriate materialised node (prompt files on the prompt input, output and
    artifact files on the panel). Files that don't match any node are surfaced
    in a separate "Other files" node placed near the bottom of the diagram.
    """
    node_files, legend_files = _scan_files(graph, run_folder) if run_folder else ({}, [])
    href_prefix = _href_prefix(run_folder) if run_folder else ""
    docs_root_dir = _docs_root_from_run_folder(run_folder) if run_folder else None
    overview_url = _overview_url(run_folder) if run_folder else ""
    commit_base_url = _commit_base_url(graph)

    aux = _aux_index(graph.nodes)
    has_overview = "Start" in graph.nodes

    lines: list[str] = ["```mermaid"]
    if graph.init_directive:
        lines.append(graph.init_directive)
    lines.append("flowchart TD")

    if has_overview:
        lines.append(f"    {_overview_node_decl(overview_url)}")

    # Each stage's prompt/stage/panel triple is emitted together so the file
    # reads top-to-bottom in chain order.
    for nid, node in graph.nodes.items():
        if aux[nid].has_prompt:
            lines.append(
                f"    {_prompt_node_decl(nid, node, node_files.get(nid, []), href_prefix, run_folder, docs_root_dir)}"
            )
        lines.append(f"    {_node_decl(node)}")
        if aux[nid].has_panel:
            lines.append(
                f"    {_panel_node_decl(nid, node, node_files.get(nid, []), href_prefix, run_folder, commit_base_url)}"
            )

    # Internal chain edges that wire each stage's materialised partners together.
    # Both prompt→stage and stage→panel are visible arrows so the data-flow
    # relationship (prompt drives the stage, stage produces the panel output)
    # reads clearly in the diagram. All edges render with the default thin
    # stroke (lineColor from the init directive) so the diagram reads as a
    # single uniform flow rather than highlighting a "completed trail".
    for nid in graph.nodes:
        if aux[nid].has_prompt:
            lines.append(f"    {nid}_prompt --> {nid}")
        if aux[nid].has_panel:
            lines.append(f"    {nid} --> {nid}_panel")

    if has_overview:
        lines.append(f"    Start --> {_OVERVIEW_NODE_ID}")

    for edge in graph.edges:
        for rendered, _ in _render_edge(edge, aux, has_overview):
            lines.append(f"    {rendered}")

    lines.extend(_CLASSDEFS)
    for nid, node in graph.nodes.items():
        lines.append(f"    class {nid} {node.css_class}")
        if aux[nid].has_prompt:
            lines.append(f"    class {nid}_prompt input")
        if aux[nid].has_panel:
            lines.append(f"    class {nid}_panel json")
    if has_overview:
        lines.append(f"    class {_OVERVIEW_NODE_ID} input")

    lines.append("```")
    block = "\n".join(lines) + "\n"
    if legend_files:
        block += _other_files_section(legend_files, href_prefix)
    return block


# --- "Other files" buttons rendered below the mermaid fence -----------------

_OTHER_FILES_BEGIN = "<!-- other-files-begin -->"
_OTHER_FILES_END = "<!-- other-files-end -->"
_OTHER_FILES_BTN_STYLE = (
    "display:inline-block;padding:4px 10px;margin:2px 3px;background:#1f2937;"
    "color:#d1d5db;border:1px solid #374151;border-radius:4px;text-decoration:none;"
    "font-size:12px;font-family:sans-serif"
)


def _other_files_section(legend_files: list[Path], href_prefix: str) -> str:
    """Render the unmatched-files button strip placed outside the mermaid fence.

    Wrapped in HTML comment markers so subsequent renders can identify and
    replace it without parsing the surrounding plan.md.
    """
    buttons = "".join(_other_file_button(f, href_prefix) for f in legend_files)
    return f"\n{_OTHER_FILES_BEGIN}\n<div>{buttons}</div>\n{_OTHER_FILES_END}\n"


def _other_file_button(file_path: Path, href_prefix: str) -> str:
    url = _file_url(file_path, href_prefix)
    display = _link_display(file_path.name)
    return f"<a href='{url}' style='{_OTHER_FILES_BTN_STYLE};'>{display}</a>"


# --- materialised-node helpers ----------------------------------------------


class _Aux:
    __slots__ = ("has_panel", "has_prompt")

    def __init__(self, has_prompt: bool, has_panel: bool) -> None:
        self.has_prompt = has_prompt
        self.has_panel = has_panel


def _aux_index(nodes: dict[str, Node]) -> dict[str, _Aux]:
    """For each node decide whether a prompt input and/or JSON panel applies.

    Only ``rect``-shape stage nodes get partners. Deterministic stages produce
    no prompt file, so their prompt input is suppressed; their panel still
    renders (e.g. verification's verify.json artefact lands there). Aggregate
    nodes that opt out via ``Node.materialize_prompt=False`` also lose their
    prompt input — there is no prompt artifact to link to, and the prior
    behaviour of rendering an unlinked ``Prompt`` placeholder was misleading.
    """
    result: dict[str, _Aux] = {}
    for nid, node in nodes.items():
        is_stage = node.shape == "rect" and nid not in {"Start", "Done"}
        has_prompt = is_stage and node.mode != "deterministic" and node.materialize_prompt
        has_panel = is_stage
        result[nid] = _Aux(has_prompt, has_panel)
    return result


def _render_edge(edge: Edge, aux: dict[str, _Aux], has_overview: bool) -> list[tuple[str, list[str]]]:
    """Break a multi-step edge into per-pair edges with rewritten endpoints.

    A middle step in a chain is simultaneously a target (of the previous step)
    and a source (of the next step). The same id can't carry both ``_prompt``
    and ``_panel`` suffixes, so we always emit one mermaid edge per consecutive
    pair instead of the original ``A --> B --> C`` chain form.

    Returns a list of ``(line, target_ids)`` tuples. ``target_ids`` is the list
    of rewritten target node ids (after ``_prompt`` / ``_panel`` rewriting) so
    callers can decide whether the edge belongs on the bold "completed path".
    """
    rendered: list[tuple[str, list[str]]] = []
    for i in range(len(edge.steps) - 1):
        src_step = edge.steps[i]
        tgt_step = edge.steps[i + 1]
        if not src_step or not tgt_step:
            continue
        src_ids = [_rewrite_source(s, aux, has_overview) for s in src_step]
        tgt_ids = [_rewrite_target(t, aux) for t in tgt_step]
        rendered.append((f"{' & '.join(src_ids)} --> {' & '.join(tgt_ids)}", tgt_ids))
    return rendered


def _rewrite_source(nid: str, aux: dict[str, _Aux], has_overview: bool) -> str:
    if nid == "Start" and has_overview:
        return _OVERVIEW_NODE_ID
    info = aux.get(nid)
    return f"{nid}_panel" if info and info.has_panel else nid


def _rewrite_target(nid: str, aux: dict[str, _Aux]) -> str:
    info = aux.get(nid)
    return f"{nid}_prompt" if info and info.has_prompt else nid


# --- node declarations ------------------------------------------------------


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
        # raw_label nodes (Start/Done/interactive gates) carry a hand-crafted top
        # line. Wrap it in the same big-title span the composed labels use so
        # node titles look consistent across the diagram, then append any subtitle
        # and Mode below at plain text size.
        title = f"<span style='{_TITLE_STYLE};'>{node.raw_label}</span>"
        extras: list[str] = []
        if node.subtitle:
            extras.append(node.subtitle)
        if node.mode:
            extras.append(f"Mode: {node.mode}")
        if extras:
            return title + "<br/>" + "<br/>".join(extras)
        return title
    return _node_label(
        node.display,
        status=node.status,
        elapsed_secs=node.elapsed_secs,
        mode=node.mode,
        backend=node.backend,
        model=node.model,
    )


def _overview_node_decl(overview_url: str) -> str:
    inner = "Overview"
    if overview_url:
        inner = f"<a href='{overview_url}' style='{_PROMPT_LINK_STYLE};'>Overview</a>"
    return f"{_OVERVIEW_NODE_ID}[/\"<span style='{_TITLE_STYLE};'>{inner}</span>\"/]"


def _prompt_node_decl(
    nid: str,
    node: Node,
    files: list[Path],
    href_prefix: str,
    run_folder: Path | None,
    docs_root_dir: Path | None,
) -> str:
    """Render the input parallelogram: 'Input' title, Prompt link, file pills.

    The body shares ``_PANEL_DIV_STYLE`` with the output panel so the two boxes
    read as a matched pair on either side of the stage node. Input pills come
    from ``node.inputs`` (stamped pre-dispatch by ``set_node_inputs``) and use
    the same ``_PILL_STYLE`` as the output panel's artifact pills.
    """
    prompt_file = next((f for f in files if f.name.endswith("-prompt.md")), None)
    if prompt_file is None:
        prompt_link = "Prompt"
    else:
        url = _file_url(prompt_file, href_prefix)
        prompt_link = f"<a href='{url}' style='{_PROMPT_LINK_STYLE};'>Prompt</a>"

    parts: list[str] = [
        f"<span style='{_TITLE_STYLE};'>Input</span>",
        "<br/><br/>",
        prompt_link,
    ]
    if node.inputs:
        parts.append("<br/><br/>")
        parts.append(_join_input_pills(node.inputs, run_folder, href_prefix, docs_root_dir))

    body = "".join(parts)
    label = f"<div style='{_PANEL_DIV_STYLE};'>{body}</div>"
    return f'{nid}_prompt@{{ shape: card, label: "{label}" }}'


def _panel_node_decl(
    nid: str,
    node: Node,
    files: list[Path],
    href_prefix: str,
    run_folder: Path | None,
    commit_base_url: str | None,
) -> str:
    label = _panel_label(node, files, href_prefix, run_folder, commit_base_url)
    return f'{nid}_panel@{{ shape: doc, label: "{label}" }}'


_NODE_URL_STYLE = "font-size:16px;font-weight:bold;color:#dcfce7;text-decoration:underline;font-family:sans-serif;word-break:break-all"


def _panel_label(
    node: Node,
    files: list[Path],
    href_prefix: str,
    run_folder: Path | None,
    commit_base_url: str | None,
) -> str:
    output_file = next((f for f in files if f.name.endswith("-output.md")), None)
    other_files = [f for f in files if not (f.name.endswith("-output.md") or f.name.endswith("-prompt.md"))]

    parts: list[str] = []
    if node.url:
        parts.append(f"<a href='{node.url}' style='{_NODE_URL_STYLE};'>{node.url}</a><br/><br/>")
    if output_file is not None:
        url = _file_url(output_file, href_prefix)
        parts.append(f"<a href='{url}' style='{_OUTPUT_HEADER_STYLE};'>Output</a><br/><br/>")
    parts.append(_panel_body(node, output_file, run_folder))
    if node.commits:
        parts.append("<br/><br/>")
        parts.append(_render_commits(node.commits, commit_base_url))
    if other_files:
        parts.append("<br/><br/>")
        parts.append(_join_pills(other_files, href_prefix))

    return f"<div style='{_PANEL_DIV_STYLE};'>{''.join(parts)}</div>"


def _render_commits(commits: list[str], commit_base_url: str | None) -> str:
    """Render one ``Commit #<sha>`` line per commit, optionally hyperlinked.

    Commits use the same ``_PROMPT_LINK_STYLE`` (color:inherit + underline) so
    they read as native body links rather than competing with the green Output
    header. When ``commit_base_url`` is absent (mid-run, before the PR is
    created) we render plain text — ``set_pr_node`` triggers a re-render once
    the URL lands, so the upgrade happens automatically.
    """
    lines: list[str] = []
    for sha in commits:
        if commit_base_url:
            lines.append(f"<a href='{commit_base_url}/commit/{sha}' style='{_PROMPT_LINK_STYLE};'>Commit #{sha}</a>")
        else:
            lines.append(f"Commit #{sha}")
    return "<br/>".join(lines)


def _commit_base_url(graph: Graph) -> str | None:
    """Derive the GitHub repo base URL (``https://github.com/owner/repo``) from
    the pr node's PR URL, or return None when no PR has been opened yet.
    """
    pr_node = graph.nodes.get(_PR_NODE_ID)
    if pr_node is None or not pr_node.url:
        return None
    m = _GITHUB_PR_URL_RE.match(pr_node.url)
    return m.group(1) if m else None


_PANEL_STATUS_TEXT = {
    "passed": "done",
    "in_progress": "in progress…",
    "blocked": "blocked",
    "failed": "blocked",
    "pending": "pending",
    "skipped": "skipped",
}

# Cap the prose summary at a length that keeps the panel readable in the diagram.
# Longer prose stays accessible via the Output link.
_PANEL_SUMMARY_MAX_CHARS = 360


def _panel_body(node: Node, output_file: Path | None, run_folder: Path | None) -> str:
    """Render the panel body: the stage's prose output if available, else a status word.

    For review sub-nodes whose direct ``-output.md`` is only SIGNAL_JSON, fall back
    to the matching section in ``review/review-log.md`` so an approved review does
    not render as a bare status word. The output file is read fresh on each render
    — this is bounded (capped at _PANEL_SUMMARY_MAX_CHARS) and discarded immediately
    after writing the diagram, so ADR-004's no-cross-stage-content invariant still
    holds.
    """
    if output_file is not None and run_folder is not None:
        prose = _extract_output_prose(run_folder / output_file)
        if prose:
            return _escape_mermaid_label(prose)
    if run_folder is not None:
        prose = _review_log_prose(run_folder, node)
        if prose:
            return _escape_mermaid_label(prose)
    return _PANEL_STATUS_TEXT.get(node.status, "pending")


_JSON_FENCE_RE = re.compile(r"^```json\n.*?\n```\s*$", re.DOTALL | re.MULTILINE)
_SIGNAL_SENTINEL_RE = re.compile(r"^SIGNAL_JSON:.*$", re.MULTILINE)
_REVIEWER_ROUND_RE = re.compile(r"^(?P<reviewer>.+)-round(?P<round>\d+)$")


def _extract_output_prose(output_path: Path) -> str:
    """Return a truncated prose summary from a stage's *-output.md file.

    Strips fenced ```json``` blocks (where the formatter places the signal) and
    any bare SIGNAL_JSON: lines; keeps the first non-empty paragraph; truncates
    to a panel-friendly length with an ellipsis marker.
    """
    try:
        text = output_path.read_text()
    except OSError:
        return ""
    return _prose_summary(text)


def _prose_summary(text: str) -> str:
    """Strip SIGNAL_JSON noise, keep the first non-empty paragraph, cap to panel size."""
    text = _JSON_FENCE_RE.sub("", text)
    text = _SIGNAL_SENTINEL_RE.sub("", text)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    summary = paragraphs[0]
    if len(summary) > _PANEL_SUMMARY_MAX_CHARS:
        summary = summary[:_PANEL_SUMMARY_MAX_CHARS].rstrip() + "…"
    return summary


def _review_log_prose(run_folder: Path, node: Node) -> str:
    """Return the review-log section prose for a review sub-node, capped.

    Reviewer agents write SIGNAL_JSON to their per-reviewer ``-output.md`` while
    the detailed prose lands in ``review/review-log.md`` under
    ``## {Reviewer.title()} Review — Round {N}``. When the direct output is
    SIGNAL_JSON-only the panel would otherwise fall back to a bare status word
    for a finished review; this helper recovers the matching log section so the
    diagram shows actual review content. Round 1 sub-nodes carry the bare
    reviewer name in ``file_suffix``; round N nodes use ``{reviewer}-round{N}``.
    """
    if node.stage_dir != "review" or not node.file_suffix:
        return ""

    m = _REVIEWER_ROUND_RE.match(node.file_suffix)
    if m:
        reviewer = m.group("reviewer")
        round_num = int(m.group("round"))
    else:
        reviewer = node.file_suffix
        round_num = 1

    review_log = run_folder / "review" / "review-log.md"
    try:
        text = review_log.read_text()
    except OSError:
        return ""

    section_body = _find_review_section(text, reviewer, round_num)
    if not section_body:
        return ""
    return _prose_summary(section_body)


def _find_review_section(text: str, reviewer: str, round_num: int) -> str:
    """Return the raw body under a ``## {Reviewer} Review — Round {N}`` heading.

    Matches both em-dash and ASCII hyphen between "Review" and "Round" so
    reviewers that emit the section heading with a plain dash still resolve.
    Body ends at the next ``##`` heading or a fix-cycle ``---`` divider so we
    don't bleed across rounds.
    """
    heading_re = re.compile(
        rf"^##\s+{re.escape(reviewer.title())}\s+Review\s+(?:—|-)\s+Round\s+{round_num}\b.*$",
        re.MULTILINE | re.IGNORECASE,
    )
    match = heading_re.search(text)
    if not match:
        return ""
    body_start = match.end()
    rest = text[body_start:]
    boundary = re.search(r"^(?:##\s+|---\s*$)", rest, re.MULTILINE)
    body = rest[: boundary.start()] if boundary else rest
    return body.strip()


def _escape_mermaid_label(text: str) -> str:
    # `&` first so we don't double-escape entities we introduce. `<`/`>` matter
    # because the panel label is rendered as HTML inside the mermaid node — an
    # unescaped `<tag>` in agent prose would otherwise be parsed as markup and
    # silently swallow surrounding text. `"` matters because the node label is
    # itself a `"..."`-quoted string in the mermaid source.
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br/>")
    )


# --- legend & file matching -------------------------------------------------


def _join_pills(files: list[Path], href_prefix: str) -> str:
    return "".join(_pill(f, href_prefix) for f in files)


def _pill(file_path: Path, href_prefix: str) -> str:
    url = _file_url(file_path, href_prefix)
    display = _link_display(file_path.name)
    return f"<a href='{url}' style='{_PILL_STYLE};'>{display}</a>"


def _join_input_pills(
    inputs: list[str],
    run_folder: Path | None,
    href_prefix: str,
    docs_root_dir: Path | None,
) -> str:
    """Render input pills, resolving each path to a URL anchored at run folder,
    docs root, or falling back to a non-clickable label when neither applies.
    """
    return "".join(_input_pill(value, run_folder, href_prefix, docs_root_dir) for value in inputs)


def _input_pill(
    value: str,
    run_folder: Path | None,
    href_prefix: str,
    docs_root_dir: Path | None,
) -> str:
    display = _link_display(Path(value).name)
    url = _input_url(value, run_folder, href_prefix, docs_root_dir)
    if url is None:
        return f"<span style='{_PILL_STYLE};'>{display}</span>"
    return f"<a href='{url}' style='{_PILL_STYLE};'>{display}</a>"


def _input_url(
    value: str,
    run_folder: Path | None,
    href_prefix: str,
    docs_root_dir: Path | None,
) -> str | None:
    """Resolve an input path to a docs-site URL.

    Tries, in order: relative-to-run-folder (use ``href_prefix``), then
    relative-to-docs-root (use ``/#`` prefix). Anything else (e.g. files in
    the repo root that the docs site does not host) returns None so the
    caller renders a non-clickable label rather than a broken link.
    """
    path = Path(value)
    if not path.is_absolute():
        # Already relative — assume relative to run_folder (the original
        # convention used by output pills).
        return _file_url(path, href_prefix) if href_prefix else path.as_posix()
    if run_folder is not None:
        try:
            rel = path.resolve().relative_to(Path(run_folder).resolve())
        except ValueError:
            pass
        else:
            return _file_url(rel, href_prefix) if href_prefix else rel.as_posix()
    if docs_root_dir is not None:
        try:
            rel = path.resolve().relative_to(Path(docs_root_dir).resolve())
        except ValueError:
            pass
        else:
            return "/#" + rel.as_posix()
    return None


def _docs_root_from_run_folder(run_folder: Path) -> Path | None:
    """Strip the conventional six-segment tail (``projects/.../runs/.../<run>``)
    off the run folder to recover the docs-root directory. Returns None when
    the layout doesn't match — same anchoring rule as ``_href_prefix``.
    """
    parts = Path(run_folder).resolve().parts
    if len(parts) < 6:
        return None
    tail = parts[-6:]
    if tail[0] != "projects" or tail[2] != "workflow" or tail[3] != "runs":
        return None
    return Path(*parts[:-6])


def _file_url(file_path: Path, href_prefix: str) -> str:
    rel = file_path.as_posix()
    return f"{href_prefix}{rel}" if href_prefix else rel


def _link_display(name: str) -> str:
    """Strip extension and translate -prompt / -output suffixes to friendly names."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.endswith("-prompt"):
        return "Prompt"
    if stem.endswith("-output"):
        return "Output"
    return stem


def _href_prefix(run_folder: Path) -> str:
    """Return the docs-site href prefix for the run folder, with a trailing slash.

    The orchestrator's path layout is ``{docs-root}/projects/{project}/workflow/runs/
    {feature-slug}/{run-name}/`` (see CLAUDE.md). We anchor on the trailing six
    segments — ``projects/{project}/workflow/runs/{feature}/{run}`` — rather than
    the first ``projects`` from the left, so docs roots that themselves live under
    a directory called ``projects`` (e.g. ``~/Dev/projects/docs``) don't produce a
    prefix that includes the host path. Returns an empty string when the layout
    doesn't match, so the caller falls back to plain relative URLs.

    The ``/#`` prefix targets the team-hub-style hash-routed docs site: the SPA
    router reads the path after ``#`` and resolves it from the docs root. Without
    the ``#`` the browser treats the encoded path as a single absolute segment
    and the link 404s.
    """
    parts = Path(run_folder).resolve().parts
    if len(parts) < 6:
        return ""
    tail = parts[-6:]
    if tail[0] != "projects" or tail[2] != "workflow" or tail[3] != "runs":
        return ""
    return "/#" + "/".join(tail) + "/"


def _overview_url(run_folder: Path) -> str:
    """Return the docs-site URL for the feature's overview.md, or empty string.

    Overview lives at ``projects/{project}/features/{feature}/overview.md`` — a
    sibling tree to ``workflow/runs/``. We extract project and feature from the
    same six-segment tail anchor used by ``_href_prefix``.
    """
    parts = Path(run_folder).resolve().parts
    if len(parts) < 6:
        return ""
    tail = parts[-6:]
    if tail[0] != "projects" or tail[2] != "workflow" or tail[3] != "runs":
        return ""
    project = tail[1]
    feature = tail[4]
    return f"/#projects/{project}/features/{feature}/overview.md"


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
        parts = rel.parts
        if len(parts) == 1:
            # Root-level files default to the legend, but a small allowlist
            # (e.g. executive_summary.md) attaches to a named stage node so the
            # corresponding artefact renders inside its output panel.
            owner_id = _ROOT_FILE_OWNERS.get(rel.name)
            if owner_id and owner_id in graph.nodes:
                node_files.setdefault(owner_id, []).append(rel)
            else:
                legend_files.append(rel)
            continue
        stage_dir = parts[0]
        nodes = by_stage_dir.get(stage_dir, [])
        # Try deeper match first (e.g. ``wave-verification/wave-2/VERIFY.md``
        # attaches to the node with file_suffix=="wave-2"). Falls back to the
        # standard depth-2 match so existing layouts are unaffected.
        target = _match_subdir_node(parts, nodes) if len(parts) >= 3 else None
        if target is None:
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


def _match_subdir_node(parts: tuple[str, ...], nodes: list[Node]) -> Node | None:
    """Match a depth ≥ 3 path against a node whose file_suffix equals the second segment.

    Used so artefacts that live in per-instance subdirectories (e.g.
    ``wave-verification/wave-2/VERIFY.md``) attach to the corresponding
    ``wave_verify_2`` node instead of all collapsing onto a single stage node.
    """
    if len(parts) < 3 or not nodes:
        return None
    subdir = parts[1]
    for node in nodes:
        if node.file_suffix and node.file_suffix == subdir:
            return node
    return None


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


def write_plan_md(plan_path, header: str, graph: Graph) -> None:
    """Create plan.md with header + rendered mermaid block. Used by init only."""
    plan_path = Path(plan_path)
    body = "\n".join([header, "", "## Orchestration Flow", "", render_block(graph, plan_path.parent), ""])
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(body)


def replace_mermaid_block(plan_path, graph: Graph) -> None:
    """Replace the existing ```mermaid``` block in plan.md with a fresh render.

    Preserves everything before and after the fence (run header, stage sections,
    run summary, file manifest). Also strips any prior ``other-files`` section
    placed directly below the fence so a single replace call refreshes both the
    diagram and its trailing button strip. If no fence is found, the file is
    left unchanged — callers should handle creation themselves.
    """
    plan_path = Path(plan_path)
    content = _strip_other_files_section(plan_path.read_text())
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


def _strip_other_files_section(content: str) -> str:
    """Remove any prior `<!-- other-files-begin -->...<!-- other-files-end -->` block,
    along with a single trailing newline, so the next render writes a fresh strip."""
    begin = content.find(_OTHER_FILES_BEGIN)
    if begin < 0:
        return content
    end_marker = content.find(_OTHER_FILES_END, begin)
    if end_marker < 0:
        return content
    end = end_marker + len(_OTHER_FILES_END)
    if end < len(content) and content[end] == "\n":
        end += 1
    # Also drop the blank line that the section's leading "\n" added before it.
    prefix = content[:begin]
    if prefix.endswith("\n\n"):
        prefix = prefix[:-1]
    return prefix + content[end:]
