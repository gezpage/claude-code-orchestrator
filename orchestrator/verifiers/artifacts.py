"""Artifact writers: VERIFY.md (human-readable) and verify.json (machine-readable).

The two files share the same status taxonomy so reviewers and tooling agree:
    passed  — all required commands and probes passed
    warned  — required passed; one or more non-required failed or were skipped
    failed  — at least one required command or probe failed

When a baseline is supplied to ``verify()``, each failed command and probe is
classified as ``baseline`` (already failing pre-pipeline) or ``net_new`` (newly
introduced by the changes under verification). The report carries a separate
``net_new_status`` so callers can apply policy to regressions without being
held hostage by pre-existing failures. See ADR-033.
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
    # Free-text explanation for a result that did not actually invoke a shell
    # command — used by synthetic audit entries (e.g. clean-install audit) where
    # ``skipped_reason`` would mislead readers because the status is ``failed``
    # rather than ``skipped``.
    note: str | None = None
    # ``baseline`` — the same command was already failing before the changes.
    # ``net_new`` — the command failed only after the changes.
    # ``None`` — no baseline was provided, classification not available.
    failure_kind: str | None = None


@dataclass
class ProbeRecord:
    id: str
    status: str  # "passed" | "failed"
    findings: list[str] = field(default_factory=list)
    failure_kind: str | None = None


@dataclass
class VerifyReport:
    status: str
    toolchain: str
    commands: list[CommandResult] = field(default_factory=list)
    probes: list[ProbeRecord] = field(default_factory=list)
    # Command IDs that form an "at least one must run" group. Carried through
    # from ``Recipe.required_any_of`` so :func:`aggregate_status` (and the
    # markdown writer's "no test ran" hint) can apply the policy without
    # re-reading the recipe. Empty tuple = no such group; original aggregate
    # semantics apply unchanged.
    required_any_of: tuple[str, ...] = field(default_factory=tuple)
    # Status computed from net-new failures only — equal to ``status`` when no
    # baseline classification was performed. See ADR-033.
    net_new_status: str = "passed"
    # IDs of failing items that match the baseline (carried over from before the
    # pipeline ran). Lists are kept separate so reviewers can tell at a glance
    # which artefact type each ID refers to.
    baseline_failed_command_ids: list[str] = field(default_factory=list)
    baseline_failed_probe_ids: list[str] = field(default_factory=list)
    new_failed_command_ids: list[str] = field(default_factory=list)
    new_failed_probe_ids: list[str] = field(default_factory=list)
    # Items that failed in the baseline but pass now — informational only.
    resolved_command_ids: list[str] = field(default_factory=list)
    resolved_probe_ids: list[str] = field(default_factory=list)


def aggregate_status(report: VerifyReport) -> str:
    """Compute the overall status from command + probe results.

    Probes are always treated as required: a probe-detected failure is not
    something the recipe author can mark optional.

    A non-required command that was *skipped* because its precondition was not
    met (e.g. ``if_script_exists`` missed) is the recipe's own gating logic —
    not a warning. Only an outright ``failed`` non-required command warrants a
    warn.

    ``required_any_of`` adds a recipe-level OR-group on top: if any listed
    command actually ran and failed the result is ``failed`` (even when the
    command itself is non-required, because the recipe is asserting that this
    group *is* the toolchain's test); if every listed command was skipped the
    result is at least ``warned`` (a passed-with-zero-commands report would
    otherwise be silent false confidence).

    When a recipe matched but every command and probe was skipped — i.e. zero
    actual signal was produced — the status is ``skipped`` rather than
    ``passed``. Reporting green for a run that never executed anything would
    let detection-only matches (e.g. a stray source-tree marker without the
    accompanying build files) silently masquerade as a clean verification.
    """
    has_required_failure = any(c.status == "failed" and c.required for c in report.commands)
    has_probe_failure = any(p.status == "failed" for p in report.probes)
    if has_required_failure or has_probe_failure:
        return "failed"
    if report.required_any_of:
        group = [c for c in report.commands if c.id in report.required_any_of]
        if any(c.status == "failed" for c in group):
            return "failed"
        if not any(c.status == "passed" for c in group):
            return "warned"
    has_non_required_failure = any(c.status == "failed" and not c.required for c in report.commands)
    if has_non_required_failure:
        return "warned"
    if not _any_ran(report):
        return "skipped"
    return "passed"


def _any_ran(report: VerifyReport) -> bool:
    """True if any command or probe actually executed (passed or failed)."""
    if any(c.status in {"passed", "failed"} for c in report.commands):
        return True
    return any(p.status in {"passed", "failed"} for p in report.probes)


def classify_against_baseline(
    report: VerifyReport,
    baseline_failed_command_ids: set[str],
    baseline_failed_probe_ids: set[str],
) -> None:
    """Annotate each failure in ``report`` as ``baseline`` or ``net_new``.

    Also populates the baseline / new / resolved ID lists on the report and
    computes ``net_new_status`` from net-new items only. Skipped commands are
    not classified (they are not failures). Mutates the report in place.

    Net-new aggregation mirrors :func:`aggregate_status` exactly — same
    required/probe rules — so policy that gates on regressions matches the
    rules that gate on overall health.
    """
    new_cmds: list[str] = []
    base_cmds: list[str] = []
    for c in report.commands:
        if c.status != "failed":
            continue
        if c.id in baseline_failed_command_ids:
            c.failure_kind = "baseline"
            base_cmds.append(c.id)
        else:
            c.failure_kind = "net_new"
            new_cmds.append(c.id)

    new_probes: list[str] = []
    base_probes: list[str] = []
    for p in report.probes:
        if p.status != "failed":
            continue
        if p.id in baseline_failed_probe_ids:
            p.failure_kind = "baseline"
            base_probes.append(p.id)
        else:
            p.failure_kind = "net_new"
            new_probes.append(p.id)

    resolved_cmds = sorted(
        cid for cid in baseline_failed_command_ids if cid not in {c.id for c in report.commands if c.status == "failed"}
    )
    resolved_probes = sorted(
        pid for pid in baseline_failed_probe_ids if pid not in {p.id for p in report.probes if p.status == "failed"}
    )

    report.baseline_failed_command_ids = base_cmds
    report.baseline_failed_probe_ids = base_probes
    report.new_failed_command_ids = new_cmds
    report.new_failed_probe_ids = new_probes
    report.resolved_command_ids = resolved_cmds
    report.resolved_probe_ids = resolved_probes
    report.net_new_status = _net_new_status(report)


def _net_new_status(report: VerifyReport) -> str:
    """Compute aggregate status considering only net-new failures.

    Mirrors :func:`aggregate_status` precedence — including the
    ``required_any_of`` OR-group escalation — so policy gates on regressions
    match the rules that gate on overall health.
    """
    has_required_new = any(c.status == "failed" and c.required and c.failure_kind == "net_new" for c in report.commands)
    has_probe_new = any(p.status == "failed" and p.failure_kind == "net_new" for p in report.probes)
    if has_required_new or has_probe_new:
        return "failed"
    if report.required_any_of:
        group_new_failure = any(
            c.status == "failed" and c.id in report.required_any_of and c.failure_kind == "net_new"
            for c in report.commands
        )
        if group_new_failure:
            return "failed"
    has_non_required_new = any(
        c.status == "failed" and not c.required and c.failure_kind == "net_new" for c in report.commands
    )
    if has_non_required_new:
        return "warned"
    if not _any_ran(report):
        return "skipped"
    return "passed"


def _any_of_ran(report: VerifyReport) -> bool:
    return any(c.id in report.required_any_of and c.status != "skipped" for c in report.commands)


def write_json(report: VerifyReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n")


def write_markdown(report: VerifyReport, path: Path) -> None:
    has_baseline_classification = (
        report.baseline_failed_command_ids
        or report.baseline_failed_probe_ids
        or report.new_failed_command_ids
        or report.new_failed_probe_ids
        or report.resolved_command_ids
        or report.resolved_probe_ids
    )

    lines: list[str] = []
    lines.append(f"# Verification Report — {report.toolchain}")
    lines.append("")
    lines.append(f"**Status:** `{report.status}`")
    if has_baseline_classification:
        lines.append(f"**Net-new status:** `{report.net_new_status}`")
    if report.required_any_of and not _any_of_ran(report):
        lines.append(
            f"**Note:** none of the expected test commands ran "
            f"({', '.join(f'`{cid}`' for cid in report.required_any_of)}) — "
            "verification cannot vouch for the change."
        )
    lines.append("")

    if has_baseline_classification:
        lines.append("## Baseline Comparison")
        lines.append("")
        lines.append(
            f"- Baseline-only failures (pre-existing): {_fmt_id_list(report.baseline_failed_command_ids, report.baseline_failed_probe_ids)}"
        )
        lines.append(
            f"- Net-new failures (introduced by changes): {_fmt_id_list(report.new_failed_command_ids, report.new_failed_probe_ids)}"
        )
        lines.append(
            f"- Resolved (failed in baseline, pass now): {_fmt_id_list(report.resolved_command_ids, report.resolved_probe_ids)}"
        )
        lines.append("")

    lines.append("## Commands")
    if not report.commands:
        lines.append("_None._")
    else:
        lines.append("")
        lines.append("| ID | Required | Status | Kind | Exit | Duration |")
        lines.append("|----|----------|--------|------|------|----------|")
        for c in report.commands:
            duration = f"{c.duration_seconds:.1f}s"
            exit_code = "—" if c.exit_code is None else str(c.exit_code)
            req = "yes" if c.required else "no"
            kind = c.failure_kind if c.failure_kind else "—"
            lines.append(f"| `{c.id}` | {req} | `{c.status}` | {kind} | {exit_code} | {duration} |")
            if c.skipped_reason:
                lines.append(f"  - skipped: {c.skipped_reason}")
            if c.note:
                lines.append(f"  - note: {c.note}")
    lines.append("")
    lines.append("## Probes")
    if not report.probes:
        lines.append("_None._")
    else:
        for p in report.probes:
            kind_suffix = f" ({p.failure_kind})" if p.failure_kind else ""
            lines.append(f"### `{p.id}` — `{p.status}`{kind_suffix}")
            if p.findings:
                for f in p.findings:
                    lines.append(f"- {f}")
            else:
                lines.append("_No findings._")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")


def _fmt_id_list(commands: list[str], probes: list[str]) -> str:
    parts: list[str] = []
    if commands:
        parts.append("commands: " + ", ".join(f"`{c}`" for c in commands))
    if probes:
        parts.append("probes: " + ", ".join(f"`{p}`" for p in probes))
    return "; ".join(parts) if parts else "_none_"
