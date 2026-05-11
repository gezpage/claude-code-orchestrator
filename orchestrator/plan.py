import datetime
import os
import re
import subprocess
import threading
from pathlib import Path

from orchestrator import state as state_mod

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
    "    classDef startend fill:#4f46e5,color:#fff,stroke:none",
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
    # output_summary is intentionally excluded — it appears in the markdown section, not the diagram
    return "\\n".join(parts)


def _fetch_commit_messages(hashes: list, repo_root: str) -> list[str]:
    """Return 'message (short_hash)' for each commit hash. Returns [] on any failure."""
    results = []
    for h in hashes:
        try:
            r = subprocess.run(
                ["git", "-C", repo_root, "log", "--format=%s", "-1", h],
                capture_output=True, text=True, timeout=10,
            )
            msg = r.stdout.strip()
            if msg:
                results.append(f"{msg} ({h[:8]})")
        except Exception:
            pass
    return results


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

    # Start node
    lines.append('    Start(["▶ Start"])')

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

    # Done node
    lines.append('    Done(["■ Done"])')

    # Group review sub-nodes by parent
    parents: dict[str, list[str]] = {}
    for parent_id, sub_id in review_sub_ids:
        parents.setdefault(parent_id, []).append(sub_id)

    # Build sequential chain, splitting at review parents so that fan-in connects
    # reviewer sub-nodes to the next stage instead of review --> next_stage.
    if chain_ids:
        lines.append(f"    Start --> {chain_ids[0]}")
        i = 0
        while i < len(chain_ids):
            cur = chain_ids[i]
            nxt = chain_ids[i + 1] if i + 1 < len(chain_ids) else None
            if cur in parents:
                # Fan-out from review parent to sub-nodes
                sub_ids = parents[cur]
                lines.append(f"    {cur} --> {' & '.join(sub_ids)}")
                # Fan-in from sub-nodes to next stage (or Done).
                # Do NOT emit cur --> nxt; the fan-in edge replaces it.
                fanin_target = nxt if nxt else "Done"
                lines.append(f"    {' & '.join(sub_ids)} --> {fanin_target}")
                # Advance normally so nxt is still processed next iteration
                # and can emit its own outgoing edge (e.g. harvest --> Done).
                i += 1
            else:
                if nxt:
                    lines.append(f"    {cur} --> {nxt}")
                else:
                    lines.append(f"    {cur} --> Done")
                i += 1
    else:
        lines.append("    Start --> Done")

    lines.extend(_CLASSDEFS)
    lines.extend(class_assignments)
    lines.append("    class Start startend")
    lines.append("    class Done startend")
    lines.append("```")
    lines.append("")

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("\n".join(lines))


def _track_node_id(track_name: str) -> str:
    return "disc_" + track_name.replace("-", "_")


def expand_discovery_nodes(run_folder, tracks, planning_elapsed_secs=None):
    """Replace the single 'discovery' node with discovery_planning + per-track nodes.

    Called after the planning phase returns track definitions. Marks discovery_planning
    as complete and each track node as pending. Returns a dict mapping track name →
    node ID so orchestrate.py can update individual track statuses.
    """
    with _plan_lock:
        return _expand_discovery_nodes(run_folder, tracks, planning_elapsed_secs)


def _expand_discovery_nodes(run_folder, tracks, planning_elapsed_secs=None):
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not tracks:
        return {}

    track_node_ids = {t["name"]: _track_node_id(t["name"]) for t in tracks}
    content = plan_path.read_text()

    # Build replacement node definitions
    planning_label = _node_label("Discovery Planning", "planning", status="passed", elapsed_secs=planning_elapsed_secs)
    new_defs = [f'    discovery_planning["{planning_label}"]']
    if len(tracks) > 1:
        new_defs.append('    disc_fanout((" "))')
    for track in tracks:
        tid = track_node_ids[track["name"]]
        display = track["name"].replace("-", " ").title()
        track_label = _node_label(display, track["name"])
        new_defs.append(f'    {tid}["{track_label}"]')
    if len(tracks) > 1:
        new_defs.append('    disc_fanin((" "))')

    # Replace node definition
    old_def = re.search(r'    discovery\["[^"]*"\]', content)
    if old_def:
        content = content[:old_def.start()] + "\n".join(new_defs) + content[old_def.end():]

    # Rewrite chain edge: discovery --> <next>
    if len(tracks) > 1:
        fan_ids = " & ".join(track_node_ids[t["name"]] for t in tracks)

        def _discovery_chain(m):
            next_stage = m.group(1)
            lines = [
                "    discovery_planning --> disc_fanout",
                f"    disc_fanout --> {fan_ids}",
                f"    {fan_ids} --> disc_fanin",
            ]
            if next_stage:
                lines.append(f"    disc_fanin --> {next_stage}")
            return "\n".join(lines)

        content = re.sub(r"    discovery --> (\w+)", _discovery_chain, content)
    else:
        tid = track_node_ids[tracks[0]["name"]]

        def _single_chain(m):
            next_stage = m.group(1)
            line = f"    discovery_planning --> {tid}"
            if next_stage:
                line += f" --> {next_stage}"
            return line

        content = re.sub(r"    discovery --> (\w+)", _single_chain, content)

    # Rewrite Start --> discovery to point at discovery_planning
    content = content.replace("    Start --> discovery\n", "    Start --> discovery_planning\n")

    # Replace class assignment
    old_class = re.search(r"    class discovery \w+", content)
    if old_class:
        new_classes = ["    class discovery_planning complete"]
        if len(tracks) > 1:
            new_classes.append("    class disc_fanout fannode")
        for track in tracks:
            new_classes.append(f"    class {track_node_ids[track['name']]} pending")
        if len(tracks) > 1:
            new_classes.append("    class disc_fanin fannode")
        content = content[:old_class.start()] + "\n".join(new_classes) + content[old_class.end():]

    plan_path.write_text(content)
    return track_node_ids


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


def _append_stage_section(plan_path, stage, summary, signal, run_folder, elapsed_secs, impl_name, repo_root=None):
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

    # Show commit messages for implementation stages
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
    # Insert before File Manifest or Run Summary, whichever comes first
    markers = ["\n## File Manifest", "\n## Run Summary"]
    insert_at = len(content)
    for marker in markers:
        idx = content.find(marker)
        if idx >= 0 and idx < insert_at:
            insert_at = idx

    if insert_at < len(content):
        plan_path.write_text(content[:insert_at] + section_text + content[insert_at:])
    else:
        plan_path.write_text(content + section_text)


def add_fix_cycle_node(run_folder, cycle_num: int, reviewers: list) -> None:
    """Insert fix-implementation and re-review nodes into the mermaid diagram for a review cycle."""
    with _plan_lock:
        _add_fix_cycle_node(run_folder, cycle_num, reviewers)


def _add_fix_cycle_node(run_folder, cycle_num: int, reviewers: list) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not reviewers:
        return

    round_num = cycle_num + 1  # cycle 1 → round 2, cycle 2 → round 3
    fix_node_id = f"fix_impl_{cycle_num}"

    # Source nodes: original sub-nodes for cycle 1, previous-round re-review nodes for later cycles
    if cycle_num == 1:
        source_ids = [f"review_{r}" for r in reviewers]
    else:
        source_ids = [f"review_{r}_{round_num - 1}" for r in reviewers]

    rerun_ids = [f"review_{r}_{round_num}" for r in reviewers]

    content = plan_path.read_text()

    # Build new node definitions
    fix_label = _node_label("Fix Implementation", f"fix-{cycle_num}", status="in_progress")
    new_defs = [f'    {fix_node_id}["{fix_label}"]']
    for reviewer, rerun_id in zip(reviewers, rerun_ids):
        rerun_label = _node_label(reviewer.title(), reviewer, status="pending")
        new_defs.append(f'    {rerun_id}["{rerun_label}"]')

    # Build new edges: source reviewer nodes → fix_impl_N → re-review nodes
    src_str = " & ".join(source_ids)
    dst_str = " & ".join(rerun_ids)
    new_edges = [
        f"    {src_str} --> {fix_node_id}",
        f"    {fix_node_id} --> {dst_str}",
    ]

    # New class assignments
    new_classes = [f"    class {fix_node_id} active"]
    for rerun_id in rerun_ids:
        new_classes.append(f"    class {rerun_id} pending")

    # Insert node defs and edges just before the first classDef line
    classdef_pos = content.find("    classDef complete")
    if classdef_pos >= 0:
        insert = "\n".join(new_defs + new_edges) + "\n"
        content = content[:classdef_pos] + insert + content[classdef_pos:]
    else:
        return  # malformed mermaid block; skip

    # Insert class assignments just before the closing fence
    last_fence = content.rfind("```")
    if last_fence >= 0:
        insert = "\n".join(new_classes) + "\n"
        content = content[:last_fence] + insert + content[last_fence:]

    plan_path.write_text(content)


def update_plan_md(run_folder, stage, status, elapsed_secs=None, output_summary=None, signal=None, impl_name=None, repo_root=None):
    with _plan_lock:
        _update_plan_md(run_folder, stage, status, elapsed_secs, output_summary, signal, impl_name, repo_root)


def _update_plan_md(run_folder, stage, status, elapsed_secs=None, output_summary=None, signal=None, impl_name=None, repo_root=None):
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

    if elapsed_secs is not None:
        state_mod.save_stage_elapsed(run_folder, stage, elapsed_secs)

    if status == "passed" and signal is not None:
        _append_stage_section(plan_path, stage, output_summary, signal, run_folder, elapsed_secs, impl_name, repo_root)
    if status == "passed":
        _update_run_summary(plan_path, run_folder)
        _update_run_files_table(plan_path, run_folder)


def _update_run_summary(plan_path, run_folder):
    """Replace the '## Run Summary' section immediately after the mermaid block."""
    elapsed_map = state_mod.load_elapsed(run_folder)
    if not elapsed_map:
        return

    total_secs = sum(elapsed_map.values())
    total_str = _format_elapsed(total_secs)

    rows = [
        "## Run Summary",
        "",
        f"### ⏱ Total elapsed: {total_str}",
        "",
        "| Stage | Status | Duration |",
        "| --- | --- | --- |",
    ]

    # Load stage statuses so we can show the icon
    state = state_mod.load_state(run_folder)
    stage_statuses = state.get("stages", {})

    for stage, secs in elapsed_map.items():
        status = stage_statuses.get(stage, "pending")
        icon = _STATUS_ICON.get(status, "-")
        display = stage.replace("_", " ").title()
        rows.append(f"| {display} | {icon} | {_format_elapsed(secs)} |")

    table_text = "\n".join(rows)
    content = plan_path.read_text()
    marker = "\n## Run Summary"

    # Find where to anchor the Run Summary section: right after the closing mermaid fence
    fence_end = content.find("\n```\n")
    if fence_end >= 0:
        fence_end += 5  # skip past "\n```\n"
    else:
        fence_end = 0

    if marker in content:
        # Replace existing section up to the next ## heading or File Manifest
        start = content.index(marker)
        next_section = content.find("\n## ", start + 1)
        if next_section >= 0:
            content = content[:start] + "\n" + table_text + content[next_section:]
        else:
            content = content[:start] + "\n" + table_text + "\n"
    else:
        # Insert immediately after the mermaid fence
        content = content[:fence_end] + "\n" + table_text + "\n" + content[fence_end:]

    plan_path.write_text(content)


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

    def _fmt_time(f: Path) -> str:
        return datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%H:%M:%S")

    rows = ["## File Manifest", "", "| File | Time |", "| --- | --- |"]
    for f in root_files:
        rel = f.relative_to(run_folder)
        rows.append(f"| [{rel}]({rel}) | {_fmt_time(f)} |")
    for dir_name in ordered_dirs:
        rows.append(f"| **{dir_name}** | |")
        for f in sorted(stage_dirs[dir_name]):
            rel = f.relative_to(run_folder)
            rows.append(f"| [{rel}]({rel}) | {_fmt_time(f)} |")

    table_text = "\n".join(rows)
    content = plan_path.read_text()
    marker = "\n## File Manifest"
    if marker in content:
        content = content[: content.index(marker)] + "\n" + table_text
    else:
        content = content.rstrip("\n") + "\n\n" + table_text + "\n"
    plan_path.write_text(content)
