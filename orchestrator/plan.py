import re
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

_CLASSDEFS = [
    "    classDef complete fill:#2d6a4f,color:#fff,stroke:none",
    "    classDef active fill:#e9c46a,color:#000,stroke:none",
    "    classDef pending fill:#e9ecef,color:#888,stroke:#ced4da",
    "    classDef blocked fill:#e63946,color:#fff,stroke:none",
    "    classDef skipped fill:#dee2e6,color:#adb5bd,stroke:#ced4da",
    "    classDef gate fill:#fff3cd,color:#664d03,stroke:#ffc107,stroke-width:2px",
]


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
        "```mermaid",
        "%%{init: {'theme': 'base', 'themeVariables': {'fontSize': '14px'}}}%%",
        "flowchart LR",
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
        else:
            # Interactive gate
            lines.append(f'    {name}{{{{\"✋ {name.title()}\"}}}}')
            chain_ids.append(name)
            class_assignments.append(f"    class {name} gate")

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


def expand_impl_nodes(run_folder, slice_files):
    """Replace the single 'implementation' node with one node per slice."""
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not slice_files:
        return

    n = len(slice_files)
    sub_ids = [f"impl_{i+1}" for i in range(n)]

    content = plan_path.read_text()

    old_def = re.search(r'    implementation\["[^"]*"\]', content)
    if old_def:
        new_defs = "\n".join(
            f'    {sid}["Slice {i+1}\\nimplementation -"]'
            for i, sid in enumerate(sub_ids)
        )
        content = content[:old_def.start()] + new_defs + content[old_def.end():]

    content = re.sub(
        r'decomposition --> implementation(?: --> (\w+))?',
        lambda m: "decomposition --> " + " --> ".join(sub_ids) + (f" --> {m.group(1)}" if m.group(1) else ""),
        content,
    )

    old_class = re.search(r'    class implementation \w+', content)
    if old_class:
        new_classes = "\n".join(f"    class {sid} pending" for sid in sub_ids)
        content = content[:old_class.start()] + new_classes + content[old_class.end():]

    plan_path.write_text(content)


def update_plan_md(run_folder, stage, status, elapsed_secs=None, output_summary=None):
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    css_class = _STATUS_CLASS.get(status, "pending")

    if not plan_path.exists():
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        classdefs = "\n".join(_CLASSDEFS)
        plan_path.write_text(
            f"```mermaid\nflowchart LR\n{classdefs}\n    class {stage} {css_class}\n```\n"
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
