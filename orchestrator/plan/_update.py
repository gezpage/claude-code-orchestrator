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
from orchestrator.plan._graph import Graph, Node, load_graph, save_graph
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
        save_graph(run_folder, graph)
        replace_mermaid_block(plan_path, graph)

    if elapsed_secs is not None:
        state_mod.save_stage_elapsed(run_folder, stage, elapsed_secs)

    if status == "passed" and signal is not None:
        display = node.display if node is not None else stage.replace("_", " ").title()
        _append_stage_section(
            plan_path, display, output_summary, signal, run_folder, elapsed_secs, impl_name, repo_root
        )
    if status == "passed":
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
