"""Generic stage node expansion driven by StageConfig.expansion.

Thread safety: expand_nodes acquires _plan_lock before calling private helpers.
Private _expand_* functions must NOT be called without holding the lock.
"""

import re
from pathlib import Path

from orchestrator.plan._helpers import _node_label, _read_slice_title, _track_node_id
from orchestrator.plan._update import _plan_lock
from orchestrator.profile import ExpansionKind, StageConfig


def expand_nodes(
    run_folder: Path,
    stage: StageConfig,
    *,
    tracks: list[dict] | None = None,
    planning_elapsed_secs: float | None = None,
    slice_files: list[str] | None = None,
    slice_groups: list[list[str]] | None = None,
) -> dict[str, str]:
    """Thread-safe. Dispatches on stage.expansion; uses stage.name for node IDs.

    Returns a mapping of logical-name → mermaid-node-id (non-empty for TRACKS only).
    """
    with _plan_lock:
        if stage.expansion == ExpansionKind.TRACKS:
            return _expand_tracks(run_folder, stage.name, tracks or [], planning_elapsed_secs)
        if stage.expansion == ExpansionKind.SLICES:
            _expand_slices(
                run_folder,
                stage.name,
                stage.slices_from_stage or "",
                slice_files or [],
                slice_groups or [],
            )
        return {}


def _expand_tracks(
    run_folder: Path,
    stage_name: str,
    tracks: list[dict],
    planning_elapsed_secs: float | None,
) -> dict[str, str]:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not tracks:
        return {}

    track_node_ids = {t["name"]: _track_node_id(stage_name, t["name"]) for t in tracks}
    content = plan_path.read_text()
    display_name = stage_name.replace("_", " ").title()

    planning_label = _node_label(
        f"{display_name} Planning", "planning", status="passed", elapsed_secs=planning_elapsed_secs
    )
    planning_node = f"{stage_name}_planning"
    fanout_node = f"{stage_name}_fanout"
    fanin_node = f"{stage_name}_fanin"

    new_subgraph_lines = [f'    subgraph sg_{stage_name}["{display_name}"]']
    new_subgraph_lines.append(f'    {planning_node}["{planning_label}"]')
    if len(tracks) > 1:
        new_subgraph_lines.append(f'    {fanout_node}((" "))')
    for track in tracks:
        tid = track_node_ids[track["name"]]
        display = track["name"].replace("-", " ").title()
        new_subgraph_lines.append(f'    {tid}["{_node_label(display, track["name"])}"]')
    if len(tracks) > 1:
        new_subgraph_lines.append(f'    {fanin_node}((" "))')
    new_subgraph_lines.append("    end")

    new_subgraph = "\n".join(new_subgraph_lines)
    sg_pattern = rf'    subgraph sg_{re.escape(stage_name)}\["[^"]*"\]\n.*?    end'
    if re.search(sg_pattern, content, flags=re.DOTALL):
        content = re.sub(sg_pattern, new_subgraph, content, flags=re.DOTALL)
    else:
        old_def = re.search(rf'    {re.escape(stage_name)}\["[^"]*"\]', content)
        if old_def:
            content = content[: old_def.start()] + new_subgraph + content[old_def.end() :]

    # Rewrite outgoing chain edge: {stage_name} --> next
    if len(tracks) > 1:
        fan_ids = " & ".join(track_node_ids[t["name"]] for t in tracks)

        def _multi_chain(m: re.Match) -> str:
            next_stage = m.group(1)
            parts = [
                f"    {planning_node} --> {fanout_node}",
                f"    {fanout_node} --> {fan_ids}",
                f"    {fan_ids} --> {fanin_node}",
            ]
            if next_stage:
                parts.append(f"    {fanin_node} --> {next_stage}")
            return "\n".join(parts)

        content = re.sub(rf"    {re.escape(stage_name)} --> (\w+)", _multi_chain, content)
    else:
        tid = track_node_ids[tracks[0]["name"]]

        def _single_chain(m: re.Match) -> str:
            next_stage = m.group(1)
            line = f"    {planning_node} --> {tid}"
            if next_stage:
                line += f" --> {next_stage}"
            return line

        content = re.sub(rf"    {re.escape(stage_name)} --> (\w+)", _single_chain, content)

    # Rewrite any incoming edge to this stage → point to planning node
    content = re.sub(
        rf"    (\w+) --> {re.escape(stage_name)}\n",
        rf"    \1 --> {planning_node}\n",
        content,
    )

    # Replace class assignment
    old_class = re.search(rf"    class {re.escape(stage_name)} \w+", content)
    if old_class:
        new_classes = [f"    class {planning_node} complete"]
        if len(tracks) > 1:
            new_classes.append(f"    class {fanout_node} fannode")
        for track in tracks:
            new_classes.append(f"    class {track_node_ids[track['name']]} pending")
        if len(tracks) > 1:
            new_classes.append(f"    class {fanin_node} fannode")
        content = content[: old_class.start()] + "\n".join(new_classes) + content[old_class.end() :]

    plan_path.write_text(content)
    return track_node_ids


def _expand_slices(
    run_folder: Path,
    stage_name: str,
    prior_stage_name: str,
    slice_files: list[str],
    slice_groups: list[list[str]],
) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not slice_files:
        return

    if not slice_groups:
        slice_groups = [[sf] for sf in slice_files]

    all_slices = [sf for group in slice_groups for sf in group]
    slice_to_id = {sf: f"impl_{i + 1}" for i, sf in enumerate(all_slices)}
    has_parallel = any(len(g) > 1 for g in slice_groups)
    display_name = stage_name.replace("_", " ").title()
    content = plan_path.read_text()

    new_subgraph_lines = [f'    subgraph sg_{stage_name}["{display_name}"]']
    for g_idx, group in enumerate(slice_groups):
        if len(group) > 1:
            new_subgraph_lines.append(f'    fanout_{g_idx + 1}((" "))')
        for sf in group:
            nid = slice_to_id[sf]
            i = all_slices.index(sf)
            title = _read_slice_title(sf) or f"{display_name} Slice {i + 1}"
            new_subgraph_lines.append(f'    {nid}["{display_name} Slice {i + 1} -\\n{title}"]')
        if len(group) > 1:
            new_subgraph_lines.append(f'    fanin_{g_idx + 1}((" "))')
    new_subgraph_lines.append("    end")
    new_subgraph = "\n".join(new_subgraph_lines)

    sg_pattern = rf'    subgraph sg_{re.escape(stage_name)}\["[^"]*"\]\n.*?    end'
    if re.search(sg_pattern, content, flags=re.DOTALL):
        content = re.sub(sg_pattern, new_subgraph, content, flags=re.DOTALL)
    else:
        old_def = re.search(rf'    {re.escape(stage_name)}\["[^"]*"\]', content)
        if old_def:
            content = content[: old_def.start()] + new_subgraph + content[old_def.end() :]

    if has_parallel:

        def _parallel_chain(next_stage: str | None) -> str:
            parts: list[str] = []
            prev = prior_stage_name
            for g_idx, group in enumerate(slice_groups):
                if len(group) == 1:
                    nid = slice_to_id[group[0]]
                    parts.append(f"    {prev} --> {nid}" if parts else f"{prev} --> {nid}")
                    prev = nid
                else:
                    fanout_id = f"fanout_{g_idx + 1}"
                    fanin_id = f"fanin_{g_idx + 1}"
                    fan_ids = " & ".join(slice_to_id[sf] for sf in group)
                    parts.append(f"    {prev} --> {fanout_id}" if parts else f"{prev} --> {fanout_id}")
                    parts.append(f"    {fanout_id} --> {fan_ids}")
                    parts.append(f"    {fan_ids} --> {fanin_id}")
                    prev = fanin_id
            if next_stage:
                parts.append(f"    {prev} --> {next_stage}")
            return "\n".join(parts)

        content = re.sub(
            rf"{re.escape(prior_stage_name)} --> {re.escape(stage_name)}(?: --> (\w+))?",
            lambda m: _parallel_chain(m.group(1)),
            content,
        )
    else:
        sub_ids = [slice_to_id[sf] for sf in all_slices]
        chain = " --> ".join(sub_ids)
        content = re.sub(
            rf"{re.escape(prior_stage_name)} --> {re.escape(stage_name)}(?: --> (\w+))?",
            lambda m: f"{prior_stage_name} --> {chain}" + (f" --> {m.group(1)}" if m.group(1) else ""),
            content,
        )

    # Rewrite any stale "{stage_name} --> X" edge left from init_plan_md
    last_group = slice_groups[-1]
    if has_parallel and len(last_group) > 1:
        last_chain_node = f"fanin_{len(slice_groups)}"
    else:
        last_chain_node = slice_to_id[last_group[-1]]

    content = re.sub(
        rf"    {re.escape(stage_name)} --> (\w+)",
        lambda m: f"    {last_chain_node} --> {m.group(1)}",
        content,
    )

    old_class = re.search(rf"    class {re.escape(stage_name)} \w+", content)
    if old_class:
        new_classes = [f"    class {slice_to_id[sf]} pending" for sf in all_slices]
        for g_idx, group in enumerate(slice_groups):
            if len(group) > 1:
                new_classes.append(f"    class fanout_{g_idx + 1} fannode")
                new_classes.append(f"    class fanin_{g_idx + 1} fannode")
        content = content[: old_class.start()] + "\n".join(new_classes) + content[old_class.end() :]

    plan_path.write_text(content)
