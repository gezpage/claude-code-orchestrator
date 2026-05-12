import datetime
from pathlib import Path


def _pair_prompt_output(files: list[Path]) -> list[tuple[Path | None, Path | None, float]]:
    """Group files with -prompt/-output suffixes onto shared rows."""
    prompt_map: dict[str, Path] = {}
    output_map: dict[str, Path] = {}
    unpaired: list[Path] = []
    for f in sorted(files):
        stem = f.stem
        if stem.endswith("-prompt"):
            prompt_map[stem[:-7] + f.suffix] = f
        elif stem.endswith("-output"):
            output_map[stem[:-7] + f.suffix] = f
        else:
            unpaired.append(f)
    result: list[tuple[Path | None, Path | None, float]] = []
    seen: set[str] = set()
    for key in list(prompt_map) + [k for k in output_map if k not in prompt_map]:
        if key in seen:
            continue
        seen.add(key)
        p, o = prompt_map.get(key), output_map.get(key)
        mtime = max(f.stat().st_mtime for f in (p, o) if f)
        result.append((p, o, mtime))
    for f in unpaired:
        result.append((f, None, f.stat().st_mtime))
    return result


def _update_run_files_table(plan_path: Path, run_folder: Path) -> None:
    """Replace the '## File Manifest' table at the bottom of plan.md with a fresh scan."""
    all_files = [f for f in run_folder.rglob("*") if f.is_file() and f.name != "plan.md"]
    root_files = sorted(f for f in all_files if f.parent == run_folder)
    subdir_files = [f for f in all_files if f.parent != run_folder]

    stage_dirs: dict[str, list[Path]] = {}
    for f in subdir_files:
        d = f.relative_to(run_folder).parts[0]
        stage_dirs.setdefault(d, []).append(f)

    ordered_dirs = sorted(stage_dirs.keys(), key=lambda d: min(f.stat().st_mtime for f in stage_dirs[d]))

    def _fmt_time(mtime: float) -> str:
        return datetime.datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

    def _link(f: Path) -> str:
        rel = f.relative_to(run_folder)
        return f"[{f.name}]({rel})"

    rows = ["## File Manifest", "", "| Prompt | Output | Time |", "| --- | --- | --- |"]
    for f in root_files:
        rows.append(f"| {_link(f)} | | {_fmt_time(f.stat().st_mtime)} |")
    for dir_name in ordered_dirs:
        rows.append(f"| **{dir_name}** | | |")
        for prompt_f, output_f, mtime in _pair_prompt_output(stage_dirs[dir_name]):
            if prompt_f and output_f:
                rows.append(f"| {_link(prompt_f)} | {_link(output_f)} | {_fmt_time(mtime)} |")
            elif prompt_f:
                rows.append(f"| {_link(prompt_f)} | | {_fmt_time(mtime)} |")
            else:
                rows.append(f"| | {_link(output_f)} | {_fmt_time(mtime)} |")  # type: ignore[arg-type]

    table_text = "\n".join(rows)
    content = plan_path.read_text()
    marker = "\n## File Manifest"
    if marker in content:
        content = content[: content.index(marker)] + "\n" + table_text
    else:
        content = content.rstrip("\n") + "\n\n" + table_text + "\n"
    plan_path.write_text(content)
