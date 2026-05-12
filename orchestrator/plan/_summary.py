from pathlib import Path

from orchestrator import state as state_mod
from orchestrator.plan._helpers import _colored_duration, _format_elapsed


def _update_run_summary(plan_path: Path, run_folder: Path) -> None:
    """Replace the '## Run Summary' section immediately after the mermaid block."""
    elapsed_map = state_mod.load_elapsed(run_folder)
    if not elapsed_map:
        return

    total_secs = sum(elapsed_map.values())
    rows = [
        "## Run Summary",
        "",
        f"### ⏱ Total elapsed: {_format_elapsed(total_secs)}",
        "",
        "| Stage | Duration | Cumulative |",
        "| --- | --- | --- |",
    ]
    cumulative = 0.0
    for stage, secs in elapsed_map.items():
        cumulative += secs
        display = stage.replace("_", " ").title()
        rows.append(f"| {display} | {_colored_duration(secs)} | {_format_elapsed(cumulative)} |")

    table_text = "\n".join(rows)
    content = plan_path.read_text()
    marker = "\n## Run Summary"

    fence_end = content.find("\n```\n")
    fence_end = (fence_end + 5) if fence_end >= 0 else 0

    if marker in content:
        start = content.index(marker)
        next_section = content.find("\n## ", start + 1)
        if next_section >= 0:
            content = content[:start] + "\n" + table_text + content[next_section:]
        else:
            content = content[:start] + "\n" + table_text + "\n"
    else:
        content = content[:fence_end] + "\n" + table_text + "\n" + content[fence_end:]

    plan_path.write_text(content)
