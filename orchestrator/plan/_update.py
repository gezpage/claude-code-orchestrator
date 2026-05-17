"""Thread-safe plan.md node status updates and stage section appending.

Thread safety: all public functions acquire _plan_lock before delegating to
private _* helpers. Private helpers must NOT be called without holding the lock.
"""

import datetime
import os
import threading
from pathlib import Path

from orchestrator import state as state_mod
from orchestrator.plan._constants import _STATUS_CLASS
from orchestrator.plan._graph import Edge, Graph, Node, load_graph, save_graph
from orchestrator.plan._helpers import (
    _PR_NOTICE_MARKER,
    _fetch_commit_messages,
    _format_elapsed,
    _stage_files,
)
from orchestrator.plan._manifest import _update_run_files_table
from orchestrator.plan._render import render_block, replace_mermaid_block
from orchestrator.plan._summary import _update_run_summary

_plan_lock = threading.Lock()


def set_pr_notice(run_folder: Path, message: str) -> None:
    """Insert or replace the 'Draft PR' notice line in plan.md.

    The notice lives outside the mermaid block (between the run header and the
    '## Orchestration Flow' heading) so it survives every render_block / append
    cycle. Thread-safe via _plan_lock.
    """
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists():
        return
    with _plan_lock:
        content = plan_path.read_text()
        new_line = f"{_PR_NOTICE_MARKER} {message}"
        lines = content.split("\n")
        existing_idx = next(
            (i for i, line in enumerate(lines) if line.startswith(_PR_NOTICE_MARKER)),
            None,
        )
        if existing_idx is not None:
            lines[existing_idx] = new_line
            plan_path.write_text("\n".join(lines))
            return
        # Insert just before the Orchestration Flow heading (or before the mermaid block).
        anchor_idx = next(
            (i for i, line in enumerate(lines) if line.startswith("## Orchestration Flow")),
            None,
        )
        if anchor_idx is None:
            anchor_idx = next(
                (i for i, line in enumerate(lines) if line.startswith("```mermaid")),
                None,
            )
        if anchor_idx is None:
            plan_path.write_text(content.rstrip("\n") + f"\n\n{new_line}\n")
            return
        insertion = [new_line, ""]
        if anchor_idx > 0 and lines[anchor_idx - 1] != "":
            insertion = ["", *insertion]
        lines[anchor_idx:anchor_idx] = insertion
        plan_path.write_text("\n".join(lines))


def update_plan_md(
    run_folder: Path,
    stage: str,
    status: str,
    elapsed_secs: float | None = None,
    output_summary: str | None = None,
    signal: dict | None = None,
    impl_name: str | None = None,
    repo_root: str | None = None,
) -> None:
    with _plan_lock:
        _update_plan_md(run_folder, stage, status, elapsed_secs, output_summary, signal, impl_name, repo_root)


_PR_NODE_ID = "pr"


def set_pr_node(run_folder: Path, url: str) -> None:
    """Stamp the PR stage's ``url`` field with the created GitHub PR URL.

    The PR node is added at init time when ``create-pr`` is true (see
    :func:`init_plan_md`); this function only updates its URL/status fields so
    the panel surfaces a clickable link. Idempotent: re-calling with a new URL
    just refreshes it.

    For backwards compatibility with older runs that pre-date init-time PR
    nodes, falls back to splicing a stadium-shape node between the last stage
    and Done.
    """
    with _plan_lock:
        run_folder = Path(run_folder)
        plan_path = run_folder / "plan.md"
        graph = load_graph(run_folder)
        if graph is None or not plan_path.exists() or "Done" not in graph.nodes:
            return

        url_text = url.strip()
        if _PR_NODE_ID in graph.nodes:
            node = graph.nodes[_PR_NODE_ID]
            if node.shape == "rect":
                # Init-time PR stage rect — surface URL via the panel.
                node.url = url_text
                node.status = "passed"
                node.css_class = "complete"
            else:
                # Legacy stadium-shape PR node — refresh the embedded link.
                node.raw_label = (
                    f"<a href='{url_text}' "
                    "style='font-size:18px;font-weight:bold;color:#fff;text-decoration:underline'>"
                    f"{url_text}</a>"
                )
        else:
            large_url = (
                f"<a href='{url_text}' "
                "style='font-size:18px;font-weight:bold;color:#fff;text-decoration:underline'>"
                f"{url_text}</a>"
            )
            graph.add_node(
                Node(
                    id=_PR_NODE_ID,
                    shape="stadium",
                    raw_label=large_url,
                    css_class="startend",
                    status="passed",
                )
            )
            for edge in graph.edges:
                if edge.steps and "Done" in edge.steps[-1]:
                    edge.steps[-1] = [_PR_NODE_ID if n == "Done" else n for n in edge.steps[-1]]
            graph.edges.append(Edge(steps=[[_PR_NODE_ID], ["Done"]]))

        save_graph(run_folder, graph)
        replace_mermaid_block(plan_path, graph)


def mark_pipeline_done(run_folder: Path) -> None:
    """Flip the Done node's class to ``complete`` so it renders with the green fill.

    Called when the pipeline reaches Done without an error. Best-effort: silently
    no-ops if the plan or Done node does not exist.
    """
    with _plan_lock:
        run_folder = Path(run_folder)
        plan_path = run_folder / "plan.md"
        graph = load_graph(run_folder)
        if graph is None or not plan_path.exists() or "Done" not in graph.nodes:
            return
        graph.nodes["Done"].css_class = "complete"
        graph.nodes["Done"].status = "passed"
        save_graph(run_folder, graph)
        replace_mermaid_block(plan_path, graph)


def mark_pr_blocked(run_folder: Path) -> None:
    """Stamp the PR node as blocked when the pipeline fails before PR creation.

    Init-time PR nodes are created with ``pending`` status when ``create-pr`` is
    true. Without this call, a failed pipeline run leaves the PR node ``pending``
    forever — a contradictory terminal state when the pipeline is plainly not
    going to produce a PR. See ADR-026.
    """
    with _plan_lock:
        run_folder = Path(run_folder)
        plan_path = run_folder / "plan.md"
        graph = load_graph(run_folder)
        if graph is None or not plan_path.exists() or _PR_NODE_ID not in graph.nodes:
            return
        graph.nodes[_PR_NODE_ID].status = "blocked"
        graph.nodes[_PR_NODE_ID].css_class = _STATUS_CLASS["blocked"]
        save_graph(run_folder, graph)
        replace_mermaid_block(plan_path, graph)


def resolve_review_subnode_statuses(
    run_folder: Path,
    final_reviewer_statuses: dict[str, str],
) -> None:
    """Re-stamp round-1 review sub-nodes with the final cycle outcome.

    This is *terminal-verdict restamping*, not status aggregation. The round-1
    blocked stamp is stale signal once a later fix cycle has produced a final
    verdict; an ``approved`` final intentionally replaces it rather than being
    combined with it via :func:`worst_status`. Calling ``worst_status`` here
    would preserve the stale ``blocked`` and defeat the whole point of the
    helper. See ADR-026.

    A reviewer that initially returned ``changes-requested`` is recorded on the
    round-1 sub-node (``review_{reviewer}``) as ``blocked``; when a later fix
    cycle re-review approves, only the round-N sub-node is updated to
    ``passed``. Without this propagation the round-1 node stays red next to a
    green round-N sibling — a contradictory terminal state for an approved run.

    Mapping rule:
    - ``approved`` final verdict → round-1 sub-node becomes ``passed``.
    - ``changes-requested`` final verdict → round-1 sub-node stays ``blocked``.
    Unknown verdicts are left untouched.
    """
    with _plan_lock:
        run_folder = Path(run_folder)
        plan_path = run_folder / "plan.md"
        graph = load_graph(run_folder)
        if graph is None or not plan_path.exists():
            return

        changed = False
        for reviewer, verdict in final_reviewer_statuses.items():
            sub_id = f"review_{reviewer}"
            node = graph.nodes.get(sub_id)
            if node is None:
                continue
            if verdict == "approved":
                new_status = "passed"
            elif verdict == "changes-requested":
                new_status = "blocked"
            else:
                continue
            if node.status != new_status:
                node.status = new_status
                node.css_class = _STATUS_CLASS.get(new_status, "pending")
                changed = True

        if changed:
            save_graph(run_folder, graph)
            replace_mermaid_block(plan_path, graph)


def rerender_plan_md(run_folder: Path) -> None:
    """Re-render the mermaid block without changing any node status.

    Used to surface files that appear in the run folder mid-stage (e.g. the
    prompt file, written before the agent dispatches) so the diagram links to
    them while the stage is still running, rather than only after it completes.
    """
    with _plan_lock:
        run_folder = Path(run_folder)
        plan_path = run_folder / "plan.md"
        graph = load_graph(run_folder)
        if graph is None or not plan_path.exists():
            return
        replace_mermaid_block(plan_path, graph)


def set_node_inputs(run_folder: Path, stage_id: str, inputs: list[str]) -> None:
    """Stamp ``Node.inputs`` and re-render so the Input box surfaces the agent's
    reading list before dispatch. No-ops if the graph/plan or node does not
    exist — callers don't need to gate on init state.
    """
    with _plan_lock:
        run_folder = Path(run_folder)
        plan_path = run_folder / "plan.md"
        graph = load_graph(run_folder)
        if graph is None or not plan_path.exists():
            return
        node = graph.nodes.get(stage_id)
        if node is None:
            return
        node.inputs = list(inputs)
        save_graph(run_folder, graph)
        replace_mermaid_block(plan_path, graph)


def _update_plan_md(
    run_folder: Path,
    stage: str,
    status: str,
    elapsed_secs: float | None,
    output_summary: str | None,
    signal: dict | None,
    impl_name: str | None,
    repo_root: str | None,
) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    css_class = _STATUS_CLASS.get(status, "pending")

    graph = load_graph(run_folder)
    if graph is None or not plan_path.exists():
        # Minimal bootstrap so downstream callers can keep stamping status without an init.
        graph = graph or Graph()
        if stage not in graph.nodes:
            graph.add_node(
                Node(
                    id=stage,
                    display=stage.replace("_", " ").title(),
                    status=status,
                    elapsed_secs=elapsed_secs,
                    css_class=css_class,
                    stage_dir=stage,
                )
            )
        save_graph(run_folder, graph)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(render_block(graph, run_folder))
        return

    node = graph.nodes.get(stage)
    if node is not None:
        node.status = status
        node.css_class = css_class
        if elapsed_secs is not None:
            node.elapsed_secs = elapsed_secs
        if signal is not None:
            commit_hashes = signal.get("commit_hashes") or []
            if commit_hashes:
                node.commits = [str(h)[:7] for h in commit_hashes]
        save_graph(run_folder, graph)
        replace_mermaid_block(plan_path, graph)

    if elapsed_secs is not None:
        state_mod.save_stage_elapsed(run_folder, stage, elapsed_secs)

    # ``skipped`` is a terminal completion status (deterministic verification that
    # found no toolchain, wave verification that warned instead of failing) — it
    # gets the same plan-side accounting as ``passed`` so the stage's explanatory
    # prose, run-summary row, and file-manifest entries all land. The css_class
    # carries the visual distinction. See issue #172 / ADR-031.
    if status in ("passed", "skipped") and signal is not None:
        display = node.display if node is not None else stage.replace("_", " ").title()
        _append_stage_section(
            plan_path, display, output_summary, signal, run_folder, elapsed_secs, impl_name, repo_root
        )
    if status in ("passed", "skipped"):
        _update_run_summary(plan_path, run_folder)
        _update_run_files_table(plan_path, run_folder)


def _append_stage_section(
    plan_path: Path,
    display: str,
    summary: str | None,
    signal: dict,
    run_folder: Path,
    elapsed_secs: float | None,
    impl_name: str | None,
    repo_root: str | None,
) -> None:
    """Append a stage-completion section below the mermaid block."""
    content = plan_path.read_text()
    heading = f"{display} ({impl_name.title()})" if impl_name else display

    now = datetime.datetime.now()
    completed_str = now.strftime("%H:%M:%S")
    if elapsed_secs is not None:
        started = now - datetime.timedelta(seconds=int(elapsed_secs))
        time_line = f"_{started.strftime('%H:%M:%S')} → {completed_str} ({_format_elapsed(elapsed_secs)})_"
    else:
        time_line = f"_Completed: {completed_str}_"

    section = [f"\n## {heading}\n", time_line + "\n"]
    if summary:
        section.append(f"\n{summary}\n")

    commit_hashes = signal.get("commit_hashes", [])
    if commit_hashes and repo_root:
        commit_lines = _fetch_commit_messages(commit_hashes, repo_root)
        if commit_lines:
            section.append("")
            for cl in commit_lines:
                section.append(f"`{cl}`")
            section.append("")

    tracks = signal.get("tracks", [])
    if tracks:
        section.append("")
        for track in tracks:
            name = track.get("name", "")
            track_summary = track.get("summary", "")
            if name and track_summary:
                section.append(f"**{name}** — {track_summary}")
                section.append("")

    files = _stage_files(signal)
    if files:
        section.append("")
        run_folder = Path(run_folder)
        for f in files:
            try:
                rel: Path | str = Path(f).relative_to(run_folder)
            except ValueError:
                rel = os.path.relpath(f, run_folder)
            section.append(f"- [{Path(f).name}]({rel})")
        section.append("")

    section_text = "\n".join(section)
    markers = ["\n## File Manifest", "\n## Run Summary"]
    insert_at = len(content)
    for marker in markers:
        idx = content.find(marker)
        if 0 <= idx < insert_at:
            insert_at = idx

    if insert_at < len(content):
        plan_path.write_text(content[:insert_at] + section_text + content[insert_at:])
    else:
        plan_path.write_text(content + section_text)
