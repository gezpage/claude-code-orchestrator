"""Thread-safe plan.md node status updates and stage section appending.

Thread safety: all public functions acquire _plan_lock before delegating to
private _* helpers. Private helpers must NOT be called without holding the lock.
"""
import datetime
import os
import re
import threading
from pathlib import Path

from orchestrator import state as state_mod
from orchestrator.plan._constants import _CLASSDEFS, _STATUS_CLASS
from orchestrator.plan._helpers import (
    _fetch_commit_messages,
    _format_elapsed,
    _node_label,
    _stage_files,
)
from orchestrator.plan._manifest import _update_run_files_table
from orchestrator.plan._summary import _update_run_summary

_plan_lock = threading.Lock()


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


def _update_plan_md(
    run_folder: Path,
    stage: str,
    status: str,
    elapsed_secs: float | None = None,
    output_summary: str | None = None,
    signal: dict | None = None,
    impl_name: str | None = None,
    repo_root: str | None = None,
) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    css_class = _STATUS_CLASS.get(status, "pending")

    if not plan_path.exists():
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        classdefs = "\n".join(_CLASSDEFS)
        plan_path.write_text(
            f"```mermaid\nflowchart TD\n{classdefs}\n    class {stage} {css_class}\n```\n"
        )
        return

    content = plan_path.read_text()

    class_pattern = rf"class {re.escape(stage)} \w+"
    if re.search(class_pattern, content):
        content = re.sub(class_pattern, f"class {stage} {css_class}", content)
    else:
        last_fence = content.rfind("```")
        if last_fence >= 0:
            content = content[:last_fence] + f"    class {stage} {css_class}\n" + content[last_fence:]
        else:
            content += f"\nclass {stage} {css_class}"

    node_pattern = rf'    {re.escape(stage)}\["([^"]*)"\]'
    m = re.search(node_pattern, content)
    if m:
        parts = m.group(1).split("\\n")
        display = re.sub(r'\s+(?:✅|⏳|🔴|-)\s*$', '', parts[0]).strip()
        impl = parts[1] if len(parts) > 1 else ""
        new_label = _node_label(display, impl, status=status, elapsed_secs=elapsed_secs)
        content = content[:m.start()] + f'    {stage}["{new_label}"]' + content[m.end():]

    plan_path.write_text(content)

    if elapsed_secs is not None:
        state_mod.save_stage_elapsed(run_folder, stage, elapsed_secs)

    if status == "passed" and signal is not None:
        _append_stage_section(plan_path, stage, output_summary, signal, run_folder, elapsed_secs, impl_name, repo_root)
    if status == "passed":
        _update_run_summary(plan_path, run_folder)
        _update_run_files_table(plan_path, run_folder)


def _append_stage_section(
    plan_path: Path,
    stage: str,
    summary: str | None,
    signal: dict,
    run_folder: Path,
    elapsed_secs: float | None,
    impl_name: str | None,
    repo_root: str | None,
) -> None:
    """Append a stage-completion section below the mermaid block."""
    content = plan_path.read_text()

    node_pattern = rf'    {re.escape(stage)}\["([^"]*)"\]'
    m = re.search(node_pattern, content)
    if m:
        parts = m.group(1).split("\\n")
        display = re.sub(r'\s+(?:✅|⏳|🔴|-)\s*$', '', parts[0]).strip()
        display = re.sub(r'\s*-\s*$', '', display)
    else:
        display = stage.replace("_", " ").title()

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
                rel = Path(f).relative_to(run_folder)
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
