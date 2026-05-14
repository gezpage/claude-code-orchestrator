import datetime
import subprocess
from pathlib import Path

from orchestrator.plan._constants import _DURATION_COLORS, _STATUS_ICON


def _duration_color(secs: float) -> str:
    for threshold, color in _DURATION_COLORS:
        if threshold is None or secs < threshold:
            return color
    return "#ef4444"


def _colored_duration(secs: float) -> str:
    color = _duration_color(secs)
    return f'<span style="color:{color}">{_format_elapsed(secs)}</span>'


def _format_elapsed(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s"


def _node_label(
    display: str,
    impl: str,
    status: str = "pending",
    elapsed_secs: float | None = None,
    output_summary: str | None = None,
) -> str:
    icon = _STATUS_ICON.get(status, "-")
    parts = [f"{display} {icon}", impl]
    if elapsed_secs is not None:
        parts.append(f"⏱ {_format_elapsed(elapsed_secs)}")
    # output_summary appears in the markdown section, not the diagram node
    return "\\n".join(parts)


def _track_node_id(stage_name: str, track_name: str) -> str:
    return f"{stage_name}_{track_name.replace('-', '_')}"


_PR_NOTICE_MARKER = "**Draft PR:**"


def _run_header(run_folder: Path, pr_notice: str | None = None) -> str:
    run_name = run_folder.name
    feature = run_folder.parent.name
    project = run_folder.parent.parent.parent.parent.name
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {project} · {feature}",
        "",
        f"**Run:** {run_name} &nbsp;·&nbsp; **Started:** {started}",
    ]
    if pr_notice:
        lines.extend(["", f"{_PR_NOTICE_MARKER} {pr_notice}"])
    return "\n".join(lines)


def _read_slice_title(path: str | Path) -> str | None:
    """Return the H1 heading from a slice file, or None."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except OSError:
        pass
    return None


def _stage_files(signal: dict) -> list[str]:
    """Extract output file paths from a signal, keeping only those that exist on disk."""
    files: list[str] = []
    for key in ("findings_files", "adr_paths", "kb_files", "adr_files", "slice_files"):
        val = signal.get(key)
        if isinstance(val, list):
            files.extend(v for v in val if v)
    for key in ("prd_path", "context_path", "alignment_log", "review_md"):
        val = signal.get(key)
        if val:
            files.append(val)
    return [f for f in files if Path(f).exists()]


def _fetch_commit_messages(hashes: list[str], repo_root: str) -> list[str]:
    """Return 'message (short_hash)' for each commit hash. Returns [] on any failure."""
    results = []
    for h in hashes:
        try:
            r = subprocess.run(
                ["git", "-C", repo_root, "log", "--format=%s", "-1", h],
                capture_output=True,
                text=True,
                timeout=10,
            )
            msg = r.stdout.strip()
            if msg:
                results.append(f"{msg} ({h[:8]})")
        except Exception:  # noqa: S110
            pass
    return results
