from pathlib import Path

from orchestrator.plan._constants import _CLASSDEFS
from orchestrator.plan._helpers import _node_label, _run_header
from orchestrator.profile import ExpansionKind, Profile


def init_plan_md(run_folder: Path, profile: Profile) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if plan_path.exists():
        return

    lines = [
        _run_header(run_folder),
        "",
        "## Orchestration Flow",
        "",
        "```mermaid",
        "%%{init: {'theme': 'base', 'themeVariables': {'fontSize': '14px', 'lineColor': '#6b7280', 'clusterBkg': 'transparent', 'clusterBorder': 'transparent'}}}%%",
        "flowchart TD",
        '    Start(["▶ Start"])',
    ]

    chain_ids: list[str] = []
    review_sub_ids: list[tuple[str, str]] = []
    class_assignments: list[str] = []

    for stage in profile.stages:
        name = stage.name
        display_name = name.replace("_", " ").title()

        if stage.mode == "interactive":
            lines.append(f'    subgraph sg_{name}["{display_name}"]')
            lines.append(f'    {name}{{{{\"✋ {name.title()}\"}}}}')
            lines.append('    end')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} gate")

        elif stage.expansion == ExpansionKind.PROMPTS:
            lines.append(f'    subgraph sg_{name}["{display_name}"]')
            lines.append(f'    {name}["{name.title()}"]')
            for reviewer, prompt_path in stage.prompts.items():
                reviewer_impl = Path(prompt_path).stem
                sub_id = f"{name}_{reviewer}"
                label = _node_label(reviewer.title(), reviewer_impl)
                lines.append(f'    {sub_id}["{label}"]')
                review_sub_ids.append((name, sub_id))
                class_assignments.append(f"    class {sub_id} pending")
            lines.append('    end')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} pending")

        else:
            # NONE, TRACKS, SLICES — all render as a standard single node initially
            prompt = stage.prompt or f"prompts/{name}/default.md"
            impl = Path(prompt).stem
            label = _node_label(name.title(), impl)
            lines.append(f'    subgraph sg_{name}["{display_name}"]')
            lines.append(f'    {name}["{label}"]')
            lines.append('    end')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} pending")

    lines.append('    Done(["■ Done"])')

    # Group PROMPTS sub-nodes by parent for fan-out/fan-in chain building
    parents: dict[str, list[str]] = {}
    for parent_id, sub_id in review_sub_ids:
        parents.setdefault(parent_id, []).append(sub_id)

    if chain_ids:
        lines.append(f"    Start --> {chain_ids[0]}")
        i = 0
        while i < len(chain_ids):
            cur = chain_ids[i]
            nxt = chain_ids[i + 1] if i + 1 < len(chain_ids) else None
            if cur in parents:
                sub_ids = parents[cur]
                lines.append(f"    {cur} --> {' & '.join(sub_ids)}")
                fanin_target = nxt if nxt else "Done"
                lines.append(f"    {' & '.join(sub_ids)} --> {fanin_target}")
                i += 1
            else:
                lines.append(f"    {cur} --> {nxt}" if nxt else f"    {cur} --> Done")
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
