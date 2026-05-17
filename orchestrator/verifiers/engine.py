"""Verification engine — resolves toolchain, runs commands and probes, writes artifacts.

Public API:
- ``verify(repo_root, run_folder)`` returns a signal dict shaped for the
  deterministic stage (matches schemas/verification.json).
- ``capture_baseline(repo_root, run_folder)`` runs the same recipe against the
  pristine repo so subsequent ``verify()`` calls can distinguish baseline-only
  failures from net-new regressions. See ADR-033.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from orchestrator.verifiers import probes as probes_pkg
from orchestrator.verifiers.artifacts import (
    CommandResult,
    ProbeRecord,
    VerifyReport,
    aggregate_status,
    classify_against_baseline,
    write_json,
    write_markdown,
)
from orchestrator.verifiers.config import ProjectVerifyConfig, load_project_config
from orchestrator.verifiers.detection import detect_toolchain
from orchestrator.verifiers.recipe import Command, Recipe, load_bundled_recipes, load_recipe_by_toolchain


class VerificationError(RuntimeError):
    """Raised when verification cannot start (no toolchain resolvable)."""


BASELINE_SUBDIR = "baseline-verification"


def verify(
    repo_root: Path,
    run_folder: Path,
    *,
    artifact_subdir: str = "verification",
    baseline_path: Path | None = None,
) -> dict:
    """Run deterministic verification against `repo_root`, write artifacts under `run_folder/<artifact_subdir>/`.

    Returns a signal dict matching `schemas/verification.json`.

    ``artifact_subdir`` lets callers route per-wave runs into distinct folders
    (e.g. ``wave-verification/wave-1``) so wave-level reports do not overwrite
    the post-implementation ``verification/`` report. See ADR-030.

    ``baseline_path`` points at a previously-written ``verify.json`` describing
    the pre-pipeline failure set. When provided and readable, every failing
    command/probe is tagged as ``baseline`` or ``net_new``, and the signal carries
    a separate ``net_new_status`` so callers can apply policy to regressions
    without flagging pre-existing red tests. A missing or unreadable baseline is
    silently ignored — verification still runs, classification just doesn't. See
    ADR-033.

    If no toolchain can be detected and there is no `.cco.yaml` pin, returns a
    benign "skipped" report — verification is not a hard gate, and a repo
    without recognised markers is a valid state (greenfield projects, prose-only
    features). VerificationError is only raised for genuinely broken config:
    a `.cco.yaml` pin that references an unknown recipe.
    """
    repo_root = Path(repo_root)
    run_folder = Path(run_folder)

    project_cfg = load_project_config(repo_root)
    recipe = _resolve_recipe(repo_root, project_cfg)
    if recipe is None:
        return _skipped_report(run_folder, artifact_subdir)
    commands, probe_names = _apply_overrides(recipe, project_cfg)

    report = VerifyReport(status="passed", toolchain=recipe.toolchain)

    for cmd in commands:
        report.commands.append(_run_command(cmd, repo_root))

    for probe_name in probe_names:
        probe = probes_pkg.get(probe_name)
        try:
            result = probe(probes_pkg.ProbeContext(repo_root=repo_root))
        except Exception as exc:
            report.probes.append(ProbeRecord(id=probe_name, status="failed", findings=[f"probe raised: {exc}"]))
            continue
        report.probes.append(ProbeRecord(id=result.id, status=result.status, findings=list(result.findings)))

    report.status = aggregate_status(report)
    report.net_new_status = report.status

    baseline_cmds, baseline_probes = _load_baseline_failures(baseline_path)
    classified = baseline_cmds is not None
    if baseline_cmds is not None:
        classify_against_baseline(report, baseline_cmds, baseline_probes)

    artifacts_dir = run_folder / artifact_subdir
    verify_md = artifacts_dir / "VERIFY.md"
    verify_json = artifacts_dir / "verify.json"
    write_markdown(report, verify_md)
    write_json(report, verify_json)

    # Stage-level status is always "passed" when a report was produced — verification
    # is not a hard gate (see ADR-017). The detailed result is in `verification_status`
    # and the artifacts; reviewers consume those.
    return {
        "stage": "verification",
        "status": "passed",
        "verification_status": report.status,
        "net_new_status": report.net_new_status,
        "toolchain": report.toolchain,
        "verify_md_path": str(verify_md),
        "verify_json_path": str(verify_json),
        "summary": _summary(report, classified=classified),
        "command_ids": [c.id for c in report.commands],
        "failed_command_ids": [c.id for c in report.commands if c.status == "failed"],
        "probe_ids": [p.id for p in report.probes],
        "failed_probe_ids": [p.id for p in report.probes if p.status == "failed"],
        "baseline_failed_command_ids": list(report.baseline_failed_command_ids),
        "baseline_failed_probe_ids": list(report.baseline_failed_probe_ids),
        "new_failed_command_ids": list(report.new_failed_command_ids),
        "new_failed_probe_ids": list(report.new_failed_probe_ids),
        "resolved_command_ids": list(report.resolved_command_ids),
        "resolved_probe_ids": list(report.resolved_probe_ids),
        "baseline_compared": classified,
    }


def capture_baseline(repo_root: Path, run_folder: Path, *, artifact_subdir: str = BASELINE_SUBDIR) -> dict:
    """Run the recipe against the pristine repo and write the baseline report.

    Equivalent to ``verify()`` without classification — the output is what later
    runs compare against. Callers must invoke this before any pipeline change
    touches the integration branch; otherwise the "baseline" already contains
    pipeline-introduced regressions. See ADR-033.
    """
    return verify(repo_root, run_folder, artifact_subdir=artifact_subdir)


def baseline_path_for(run_folder: Path, artifact_subdir: str = BASELINE_SUBDIR) -> Path:
    """Return the conventional baseline ``verify.json`` path for a run folder."""
    return Path(run_folder) / artifact_subdir / "verify.json"


def _load_baseline_failures(baseline_path: Path | None) -> tuple[set[str] | None, set[str]]:
    """Return ``(failed_command_ids, failed_probe_ids)`` from a baseline report.

    Returns ``(None, set())`` when no baseline is configured or the file is
    missing/unreadable — the verifier silently falls back to no-classification
    in that case rather than erroring, because the absence of a baseline is a
    valid state (e.g. greenfield project, baseline capture skipped).
    """
    if baseline_path is None:
        return None, set()
    path = Path(baseline_path)
    if not path.exists():
        return None, set()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, set()
    cmd_ids: set[str] = set()
    probe_ids: set[str] = set()
    for c in data.get("commands", []):
        if c.get("status") == "failed":
            cid = c.get("id")
            if isinstance(cid, str):
                cmd_ids.add(cid)
    for p in data.get("probes", []):
        if p.get("status") == "failed":
            pid = p.get("id")
            if isinstance(pid, str):
                probe_ids.add(pid)
    return cmd_ids, probe_ids


def _resolve_recipe(repo_root: Path, cfg: ProjectVerifyConfig | None) -> Recipe | None:
    """Return the chosen recipe, or None when no toolchain is detected and none was pinned.

    Raises VerificationError when `.cco.yaml` pins a toolchain that has no bundled recipe —
    that is a user-facing config error, not a benign skip.
    """
    if cfg is not None and cfg.toolchain:
        try:
            return load_recipe_by_toolchain(cfg.toolchain)
        except FileNotFoundError as exc:
            raise VerificationError(str(exc)) from exc
    recipes = load_bundled_recipes()
    return detect_toolchain(repo_root, recipes)


def _skipped_report(run_folder: Path, artifact_subdir: str = "verification") -> dict:
    """Build a benign skipped report for repos with no detectable toolchain."""
    report = VerifyReport(status="passed", toolchain="none")
    artifacts_dir = run_folder / artifact_subdir
    verify_md = artifacts_dir / "VERIFY.md"
    verify_json = artifacts_dir / "verify.json"
    write_markdown(report, verify_md)
    write_json(report, verify_json)
    return {
        "stage": "verification",
        "status": "passed",
        "verification_status": "skipped",
        "net_new_status": "skipped",
        "toolchain": "none",
        "verify_md_path": str(verify_md),
        "verify_json_path": str(verify_json),
        "summary": "no toolchain detected — verification skipped",
        "command_ids": [],
        "failed_command_ids": [],
        "probe_ids": [],
        "failed_probe_ids": [],
        "baseline_failed_command_ids": [],
        "baseline_failed_probe_ids": [],
        "new_failed_command_ids": [],
        "new_failed_probe_ids": [],
        "resolved_command_ids": [],
        "resolved_probe_ids": [],
        "baseline_compared": False,
    }


def _apply_overrides(recipe: Recipe, cfg: ProjectVerifyConfig | None) -> tuple[tuple[Command, ...], tuple[str, ...]]:
    commands = recipe.commands if cfg is None or cfg.commands is None else cfg.commands
    probes = recipe.probes if cfg is None or cfg.probes is None else cfg.probes
    return commands, probes


def _run_command(cmd: Command, repo_root: Path) -> CommandResult:
    skipped_reason = _evaluate_precondition(cmd, repo_root)
    if skipped_reason is not None:
        return CommandResult(
            id=cmd.id,
            command=cmd.command,
            required=cmd.required,
            status="skipped",
            exit_code=None,
            duration_seconds=0.0,
            skipped_reason=skipped_reason,
        )

    t0 = time.monotonic()
    try:
        # Recipes declare commands as shell strings (`npm test`, `go build ./...`) — they are
        # not user input and live in trusted YAML shipped with the package or written by the
        # repo owner. shell=True is the correct mode here.
        proc = subprocess.run(  # noqa: S602
            cmd.command,
            shell=True,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=cmd.timeout_seconds,
            check=False,
        )
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        return CommandResult(
            id=cmd.id,
            command=cmd.command,
            required=cmd.required,
            status="failed",
            exit_code=None,
            duration_seconds=time.monotonic() - t0,
            skipped_reason=f"timed out after {cmd.timeout_seconds}s",
        )

    return CommandResult(
        id=cmd.id,
        command=cmd.command,
        required=cmd.required,
        status="passed" if exit_code == 0 else "failed",
        exit_code=exit_code,
        duration_seconds=time.monotonic() - t0,
    )


def _evaluate_precondition(cmd: Command, repo_root: Path) -> str | None:
    """Return a skip reason if the command's precondition is not met, else None.

    Currently only `if_script_exists` is supported. It checks the Node `package.json`
    scripts map. This is the one place that knows about Node's script convention —
    a recipe-level concept, but the check itself is mechanical enough to live here.
    Other ecosystems can add analogous fields without leaking ecosystem branching
    into the engine's main flow.
    """
    if cmd.if_script_exists is None:
        return None
    manifest = repo_root / "package.json"
    if not manifest.exists():
        return f"package.json not found (needed for if_script_exists: {cmd.if_script_exists})"
    try:
        data = json.loads(manifest.read_text())
    except json.JSONDecodeError:
        return "package.json is not valid JSON"
    scripts = data.get("scripts") or {}
    if cmd.if_script_exists not in scripts:
        return f"no '{cmd.if_script_exists}' script in package.json"
    return None


def _summary(report: VerifyReport, *, classified: bool) -> str:
    n_cmd = len(report.commands)
    n_failed = sum(1 for c in report.commands if c.status == "failed")
    n_skipped = sum(1 for c in report.commands if c.status == "skipped")
    n_probe_failed = sum(1 for p in report.probes if p.status == "failed")
    parts = [f"toolchain={report.toolchain}", f"{n_cmd} command{'s' if n_cmd != 1 else ''}"]
    if n_failed:
        parts.append(f"{n_failed} failed")
    if n_skipped:
        parts.append(f"{n_skipped} skipped")
    if n_probe_failed:
        parts.append(f"{n_probe_failed} probe failure{'s' if n_probe_failed != 1 else ''}")
    parts.append(f"status={report.status}")
    if classified:
        n_new = len(report.new_failed_command_ids) + len(report.new_failed_probe_ids)
        n_base = len(report.baseline_failed_command_ids) + len(report.baseline_failed_probe_ids)
        parts.append(f"net_new={n_new}")
        parts.append(f"baseline={n_base}")
        parts.append(f"net_new_status={report.net_new_status}")
    return ", ".join(parts)
