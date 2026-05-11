import datetime
import os
import re
import threading
from pathlib import Path

_STATUS_CLASS = {
    "pending":     "pending",
    "passed":      "complete",
    "blocked":     "blocked",
    "failed":      "blocked",
    "in_progress": "active",
    "skipped":     "skipped",
}

_STATUS_ICON = {
    "pending":     "-",
    "passed":      "✅",
    "blocked":     "🔴",
    "failed":      "🔴",
    "in_progress": "⏳",
    "skipped":     "-",
}

_plan_lock = threading.Lock()

_CLASSDEFS = [
    "    classDef complete fill:#059669,color:#fff,stroke:none",
    "    classDef active fill:#d97706,color:#fff,stroke:none",
    "    classDef pending fill:#6b7280,color:#fff,stroke:#4b5563",
    "    classDef blocked fill:#dc2626,color:#fff,stroke:none",
    "    classDef skipped fill:#4b5563,color:#9ca3af,stroke:#374151",
    "    classDef gate fill:#92400e,color:#fff,stroke:#d97706,stroke-width:2px",
    "    classDef fannode fill:#374151,color:#9ca3af,stroke:#1f2937,stroke-width:1px",
]


def _stage_files(signal: dict) -> list[str]:
    """Extract output file paths from a signal, keeping only those that exist on disk."""
    files = []
    for key in ("findings_files", "adr_paths", "kb_files", "adr_files", "slice_files"):
        val = signal.get(key)
        if isinstance(val, list):
            files.extend(v for v in val if v)
    for key in ("prd_path", "context_path", "alignment_log", "review_md"):
        val = signal.get(key)
        if val:
            files.append(val)
    return [f for f in files if Path(f).exists()]


def _read_slice_title(path):
    """Return the H1 heading from a slice file, or None."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except OSError:
        pass
    return None


def _run_header(run_folder):
    """Title and subtitle lines for the top of plan.md."""
    rf = Path(run_folder)
    run_name = rf.name
    feature  = rf.parent.name
    project  = rf.parent.parent.parent.parent.name
    started  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return "\n".join([
        f"# {project} · {feature}",
        "",
        f"**Run:** {run_name} &nbsp;·&nbsp; **Started:** {started}",
    ])


def _format_elapsed(secs):
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s"


def _node_label(display, impl, status="pending", elapsed_secs=None, output_summary=None):
    icon = _STATUS_ICON.get(status, "-")
    parts = [f"{display} {icon}", impl]
    if elapsed_secs is not None:
        parts.append(f"⏱ {_format_elapsed(elapsed_secs)}")
    if output_summary:
        parts.append(output_summary)
    return "\\n".join(parts)


def init_plan_md(run_folder, profile):
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if plan_path.exists():
        return

    stages = profile.get("stages", [])
    lines = [
        _run_header(run_folder),
        "",
        "## Orchestration Flow",
        "",
        "```mermaid",
        "%%{init: {'theme': 'base', 'themeVariables': {'fontSize': '14px', 'lineColor': '#6b7280'}}}%%",
        "flowchart TD",
    ]

    chain_ids = []
    review_sub_ids = []  # [(parent_id, sub_id), ...]
    class_assignments = []

    for stage_def in stages:
        name = stage_def["stage"]
        if "prompt" in stage_def:
            impl = Path(stage_def["prompt"]).stem
            label = _node_label(name.title(), impl)
            lines.append(f'    {name}["{label}"]')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} pending")
        elif "prompts" in stage_def:
            lines.append(f'    {name}["{name.title()}"]')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} pending")
            for reviewer, prompt_path in stage_def["prompts"].items():
                reviewer_impl = Path(prompt_path).stem
                sub_id = f"{name}_{reviewer}"
                label = _node_label(reviewer.title(), reviewer_impl)
                lines.append(f'    {sub_id}["{label}"]')
                review_sub_ids.append((name, sub_id))
                class_assignments.append(f"    class {sub_id} pending")
        elif stage_def.get("mode") == "interactive":
            lines.append(f'    {name}{{{{\"✋ {name.title()}\"}}}}')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} gate")
        else:
            impl = Path(stage_def.get("prompt", f"prompts/{name}/default.md")).stem
            label = _node_label(name.title(), impl)
            lines.append(f'    {name}["{label}"]')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} pending")

    if len(chain_ids) > 1:
        lines.append("    " + " --> ".join(chain_ids))

    # Fan-out edges for review sub-nodes (group by parent for & syntax)
    parents: dict[str, list[str]] = {}
    for parent_id, sub_id in review_sub_ids:
        parents.setdefault(parent_id, []).append(sub_id)
    for parent_id, sub_ids in parents.items():
        lines.append(f"    {parent_id} --> {' & '.join(sub_ids)}")

    lines.extend(_CLASSDEFS)
    lines.extend(class_assignments)
    lines.append("```")
    lines.append("")

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("\n".join(lines))


def expand_impl_nodes(run_folder, slice_files, slice_groups=None):
    """Replace the single 'implementation' node with one node per slice.

    When slice_groups contains any group with multiple slices (parallel dispatch),
    fanout/fanin circle nodes are inserted around that group's slices.
    Single-slice groups remain as plain sequential nodes.
    """
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not slice_files:
        return

    if not slice_groups:
        slice_groups = [[sf] for sf in slice_files]

    all_slices = [sf for group in slice_groups for sf in group]
    slice_to_id = {sf: f"impl_{i+1}" for i, sf in enumerate(all_slices)}
    has_parallel = any(len(g) > 1 for g in slice_groups)

    content = plan_path.read_text()

    # Build node definitions
    new_defs = []
    for g_idx, group in enumerate(slice_groups):
        if len(group) > 1:
            new_defs.append(f'    fanout_{g_idx + 1}((" "))')
        for sf in group:
            nid = slice_to_id[sf]
            i = all_slices.index(sf)
            title = _read_slice_title(sf) or f"Implementation Slice {i + 1}"
            new_defs.append(f'    {nid}["Implementation Slice {i + 1} -\\n{title}"]')
        if len(group) > 1:
            new_defs.append(f'    fanin_{g_idx + 1}((" "))')

    old_def = re.search(r'    implementation\["[^"]*"\]', content)
    if old_def:
        content = content[:old_def.start()] + "\n".join(new_defs) + content[old_def.end():]

    # Rewrite chain edges
    if has_parallel:
        def _parallel_chain(next_stage):
            lines = []
            prev = "decomposition"
            for g_idx, group in enumerate(slice_groups):
                if len(group) == 1:
                    nid = slice_to_id[group[0]]
                    prefix = "decomposition --> " if not lines else f"    {prev} --> "
                    lines.append(f"{prefix}{nid}" if lines else f"decomposition --> {nid}")
                    prev = nid
                else:
                    fanout_id = f"fanout_{g_idx + 1}"
                    fanin_id = f"fanin_{g_idx + 1}"
                    fan_ids = " & ".join(slice_to_id[sf] for sf in group)
                    lines.append(
                        f"decomposition --> {fanout_id}" if not lines
                        else f"    {prev} --> {fanout_id}"
                    )
                    lines.append(f"    {fanout_id} --> {fan_ids}")
                    lines.append(f"    {fan_ids} --> {fanin_id}")
                    prev = fanin_id
            if next_stage:
                lines.append(f"    {prev} --> {next_stage}")
            return "\n".join(lines)

        content = re.sub(
            r'decomposition --> implementation(?: --> (\w+))?',
            lambda m: _parallel_chain(m.group(1)),
            content,
        )
    else:
        sub_ids = [slice_to_id[sf] for sf in all_slices]
        content = re.sub(
            r'decomposition --> implementation(?: --> (\w+))?',
            lambda m: "decomposition --> " + " --> ".join(sub_ids) + (f" --> {m.group(1)}" if m.group(1) else ""),
            content,
        )

    # Replace class assignments
    old_class = re.search(r'    class implementation \w+', content)
    if old_class:
        new_classes = [f"    class {slice_to_id[sf]} pending" for sf in all_slices]
        for g_idx, group in enumerate(slice_groups):
            if len(group) > 1:
                new_classes.append(f"    class fanout_{g_idx + 1} fannode")
                new_classes.append(f"    class fanin_{g_idx + 1} fannode")
        content = content[:old_class.start()] + "\n".join(new_classes) + content[old_class.end():]

    plan_path.write_text(content)


def _append_stage_section(plan_path, stage, summary, signal, run_folder, elapsed_secs, impl_name):
    """Append a stage-completion section below the mermaid block."""
    content = plan_path.read_text()

    # Derive heading from mermaid node label (matches the diagram text exactly)
    node_pattern = rf'    {re.escape(stage)}\["([^"]*)"\]'
    m = re.search(node_pattern, content)
    if m:
        parts = m.group(1).split("\\n")
        display = re.sub(r'\s+(?:✅|⏳|🔴|-)\s*$', '', parts[0]).strip()
        display = re.sub(r'\s*-\s*$', '', display)  # strip trailing " -" on impl slice labels
    else:
        display = stage.replace("_", " ").title()

    heading = f"{display} ({impl_name.title()})" if impl_name else display

    # Build time metadata line
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
    marker = "\n## File Manifest"
    if marker in content:
        idx = content.index(marker)
        plan_path.write_text(content[:idx] + section_text + content[idx:])
    else:
        plan_path.write_text(content + section_text)


def update_plan_md(run_folder, stage, status, elapsed_secs=None, output_summary=None, signal=None, impl_name=None):
    with _plan_lock:
        _update_plan_md(run_folder, stage, status, elapsed_secs, output_summary, signal, impl_name)


def _update_plan_md(run_folder, stage, status, elapsed_secs=None, output_summary=None, signal=None, impl_name=None):
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

    # Update class assignment
    class_pattern = rf"class {re.escape(stage)} \w+"
    if re.search(class_pattern, content):
        content = re.sub(class_pattern, f"class {stage} {css_class}", content)
    else:
        last_fence = content.rfind("```")
        if last_fence >= 0:
            content = (
                content[:last_fence]
                + f"    class {stage} {css_class}\n"
                + content[last_fence:]
            )
        else:
            content += f"\nclass {stage} {css_class}"

    # Update node label (updates status icon and optional details)
    node_pattern = rf'    {re.escape(stage)}\["([^"]*)"\]'
    m = re.search(node_pattern, content)
    if m:
        parts = m.group(1).split("\\n")
        # Strip trailing status icon from display line
        display = re.sub(r'\s+(?:✅|⏳|🔴|-)\s*$', '', parts[0]).strip()
        impl = parts[1] if len(parts) > 1 else ""
        new_label = _node_label(display, impl, status=status, elapsed_secs=elapsed_secs, output_summary=output_summary)
        content = content[:m.start()] + f'    {stage}["{new_label}"]' + content[m.end():]

    plan_path.write_text(content)

    if status == "passed" and signal is not None:
        _append_stage_section(plan_path, stage, output_summary, signal, run_folder, elapsed_secs, impl_name)
    if status == "passed":
        _update_run_files_table(plan_path, run_folder)


def _update_run_files_table(plan_path, run_folder):
    """Replace the '## File Manifest' table at the bottom of plan.md with a fresh scan."""
    run_folder = Path(run_folder)
    all_files = [f for f in run_folder.rglob("*") if f.is_file() and f.name != "plan.md"]

    root_files = sorted(f for f in all_files if f.parent == run_folder)
    subdir_files = [f for f in all_files if f.parent != run_folder]

    stage_dirs: dict[str, list[Path]] = {}
    for f in subdir_files:
        d = f.relative_to(run_folder).parts[0]
        stage_dirs.setdefault(d, []).append(f)

    def _min_mtime(dir_name):
        return min(f.stat().st_mtime for f in stage_dirs[dir_name])

    ordered_dirs = sorted(stage_dirs.keys(), key=_min_mtime)

    rows = ["## File Manifest", "", "| File | Stage |", "| --- | --- |"]
    for f in root_files:
        rel = f.relative_to(run_folder)
        rows.append(f"| [{rel}]({rel}) | — |")
    for dir_name in ordered_dirs:
        for f in sorted(stage_dirs[dir_name]):
            rel = f.relative_to(run_folder)
            rows.append(f"| [{rel}]({rel}) | {dir_name} |")

    table_text = "\n".join(rows)
    content = plan_path.read_text()
    marker = "\n## File Manifest"
    if marker in content:
        content = content[: content.index(marker)] + "\n" + table_text
    else:
        content = content.rstrip("\n") + "\n\n" + table_text + "\n"
    plan_path.write_text(content)
