"""Artifact writers: VERIFY.md (human-readable) and verify.json (machine-readable).

The two files share the same status taxonomy so reviewers and tooling agree:
    passed  — all required commands and probes passed
    warned  — required passed; one or more non-required failed or were skipped
    failed  — at least one required command or probe failed
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class CommandResult:
    id: str
    command: str
    required: bool
    status: str  # "passed" | "failed" | "skipped"
    exit_code: int | None
    duration_seconds: float
    skipped_reason: str | None = None


@dataclass
class ProbeRecord:
    id: str
    status: str  # "passed" | "failed"
    findings: list[str] = field(default_factory=list)


@dataclass
class VerifyReport:
    status: str
    toolchain: str
    commands: list[CommandResult] = field(default_factory=list)
    probes: list[ProbeRecord] = field(default_factory=list)


def aggregate_status(report: VerifyReport) -> str:
    """Compute the overall status from command + probe results.

    Probes are always treated as required: a probe-detected failure is not
    something the recipe author can mark optional.

    A non-required command that was *skipped* because its precondition was not
    met (e.g. `if_script_exists` missed) is the recipe's own gating logic — not
    a warning. Only an outright `failed` non-required command warrants a warn.
    """
    has_required_failure = any(c.status == "failed" and c.required for c in report.commands)
    has_probe_failure = any(p.status == "failed" for p in report.probes)
    if has_required_failure or has_probe_failure:
        return "failed"
    has_non_required_failure = any(c.status == "failed" and not c.required for c in report.commands)
    if has_non_required_failure:
        return "warned"
    return "passed"


def write_json(report: VerifyReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n")


def write_markdown(report: VerifyReport, path: Path) -> None:
    lines: list[str] = []
    lines.append(f"# Verification Report — {report.toolchain}")
    lines.append("")
    lines.append(f"**Status:** `{report.status}`")
    lines.append("")
    lines.append("## Commands")
    if not report.commands:
        lines.append("_None._")
    else:
        lines.append("")
        lines.append("| ID | Required | Status | Exit | Duration |")
        lines.append("|----|----------|--------|------|----------|")
        for c in report.commands:
            duration = f"{c.duration_seconds:.1f}s"
            exit_code = "—" if c.exit_code is None else str(c.exit_code)
            req = "yes" if c.required else "no"
            lines.append(f"| `{c.id}` | {req} | `{c.status}` | {exit_code} | {duration} |")
            if c.skipped_reason:
                lines.append(f"  - skipped: {c.skipped_reason}")
    lines.append("")
    lines.append("## Probes")
    if not report.probes:
        lines.append("_None._")
    else:
        for p in report.probes:
            lines.append(f"### `{p.id}` — `{p.status}`")
            if p.findings:
                for f in p.findings:
                    lines.append(f"- {f}")
            else:
                lines.append("_No findings._")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")
