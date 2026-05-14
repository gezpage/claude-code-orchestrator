"""Verification engine — resolves toolchain, runs commands and probes, writes artifacts.

Public API: `verify(repo_root, run_folder)` returns a signal dict shaped for the
deterministic stage (matches schemas/verification.json).
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
    write_json,
    write_markdown,
)
from orchestrator.verifiers.config import ProjectVerifyConfig, load_project_config
from orchestrator.verifiers.detection import detect_toolchain
from orchestrator.verifiers.recipe import Command, Recipe, load_bundled_recipes, load_recipe_by_toolchain


class VerificationError(RuntimeError):
    """Raised when verification cannot start (no toolchain resolvable)."""


def verify(repo_root: Path, run_folder: Path) -> dict:
    """Run deterministic verification against `repo_root`, write artifacts under `run_folder/verification/`.

    Returns a signal dict matching `schemas/verification.json`.

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
        return _skipped_report(run_folder)
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

    artifacts_dir = run_folder / "verification"
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
        "toolchain": report.toolchain,
        "verify_md_path": str(verify_md),
        "verify_json_path": str(verify_json),
        "summary": _summary(report),
        "command_ids": [c.id for c in report.commands],
        "failed_command_ids": [c.id for c in report.commands if c.status == "failed"],
        "probe_ids": [p.id for p in report.probes],
        "failed_probe_ids": [p.id for p in report.probes if p.status == "failed"],
    }


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


def _skipped_report(run_folder: Path) -> dict:
    """Build a benign skipped report for repos with no detectable toolchain."""
    report = VerifyReport(status="passed", toolchain="none")
    artifacts_dir = run_folder / "verification"
    verify_md = artifacts_dir / "VERIFY.md"
    verify_json = artifacts_dir / "verify.json"
    write_markdown(report, verify_md)
    write_json(report, verify_json)
    return {
        "stage": "verification",
        "status": "passed",
        "verification_status": "skipped",
        "toolchain": "none",
        "verify_md_path": str(verify_md),
        "verify_json_path": str(verify_json),
        "summary": "no toolchain detected — verification skipped",
        "command_ids": [],
        "failed_command_ids": [],
        "probe_ids": [],
        "failed_probe_ids": [],
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


def _summary(report: VerifyReport) -> str:
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
    return ", ".join(parts)
