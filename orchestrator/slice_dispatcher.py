"""Slice-expansion stage dispatcher extracted from ``orchestrator.orchestrate``.

Pure relocation — no behaviour changes. Owns the slice_files / slice_groups
filtering and alignment logic, per-slice run helper, single-slice and parallel
worktree fan-out, and the wave-verification call boundaries that follow each
wave merge. Plan.md / graph stamping per slice still goes through the shared
``plan_updates`` helpers; wave verification still goes through
``orchestrator.wave_verification``.

Collaborator lookups (``run_stage``, ``update_plan_md``, ``expand_nodes``,
``stamp_node_passed_with_commits``, the wave-verification hooks, and the
``_create_branch`` / ``_impl_from_prompt`` helpers still in ``orchestrate``)
go through the ``orchestrate`` module at call time. Two reasons: it avoids
a circular module-load with the dispatch table in ``orchestrate``, and it
preserves the patch path of every existing test — patches at
``orchestrator.orchestrate.<name>`` keep reaching the actual call sites.
"""

from __future__ import annotations

import concurrent.futures
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator import _git as git_state
from orchestrator._git import GitStateError
from orchestrator.profile import StageConfig

if TYPE_CHECKING:
    from orchestrator.orchestrate import _PipelineContext

_SLICE_RE = re.compile(r"S-\d+-")


def _create_worktree(repo_root: str, temp_branch: str, base_branch: str, logger, stage_name: str) -> str:
    import tempfile

    if git_state.branch_exists(repo_root, temp_branch):
        raise GitStateError(f"cannot create worktree on '{temp_branch}': branch already exists in {repo_root}")
    safe_prefix = temp_branch.replace("/", "-")
    wt_path = tempfile.mkdtemp(prefix=f"orch-wt-{safe_prefix}-")
    result = subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", wt_path, "-b", temp_branch, base_branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitStateError(f"git worktree add failed: {result.stderr.strip()}")
    logger.log(stage_name, "INFO", f"Created worktree {wt_path} on branch {temp_branch}")
    return wt_path


def _remove_worktree(repo_root: str, wt_path: str, temp_branch: str, logger, stage_name: str) -> None:
    """Clean up a slice worktree using git's registry as the source of truth.

    Parallel runs have shown the orchestrator's recorded `wt_path` can drift
    from what `git worktree list` actually holds — usually a stale entry on
    the same branch under a different path. Match on either path OR branch,
    remove every matching worktree first, then delete the branch only when
    nothing still references it.
    """
    target = str(wt_path).rstrip("/")
    registry = git_state.list_worktrees(repo_root)

    matches = [wt["path"] for wt in registry if wt["path"] == target or wt["branch"] == temp_branch]
    if not matches:
        logger.log(stage_name, "INFO", f"worktree {wt_path} not registered — skipping remove")
    for path in matches:
        r1 = subprocess.run(
            ["git", "-C", repo_root, "worktree", "remove", "--force", path],
            capture_output=True,
            text=True,
        )
        if r1.returncode != 0:
            logger.log(stage_name, "WARN", f"git worktree remove failed for {path}: {r1.stderr.strip()}")

    holder = git_state.worktree_for_branch(repo_root, temp_branch)
    if holder is not None:
        logger.log(
            stage_name,
            "WARN",
            f"branch '{temp_branch}' still held by worktree at {holder} — skipping branch delete (resolve manually)",
        )
    elif git_state.branch_exists(repo_root, temp_branch):
        r2 = subprocess.run(["git", "-C", repo_root, "branch", "-D", temp_branch], capture_output=True, text=True)
        if r2.returncode != 0:
            logger.log(stage_name, "WARN", f"git branch -D {temp_branch} failed: {r2.stderr.strip()}")
    logger.log(stage_name, "INFO", f"Removed worktree {wt_path}")


def _merge_worktree_branch(repo_root: str, temp_branch: str, logger, stage_name: str) -> None:
    result = subprocess.run(
        ["git", "-C", repo_root, "merge", temp_branch, "--no-ff", "-m", f"merge parallel slice branch {temp_branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if git_state.has_merge_conflicts(repo_root):
            git_state.abort_merge(repo_root)
            raise GitStateError(f"merge conflict on '{temp_branch}' — aborted; manual resolution required")
        raise GitStateError(f"git merge {temp_branch} failed: {result.stderr.strip()}")
    logger.log(stage_name, "INFO", f"Merged {temp_branch} into HEAD")


def _run_slice(
    stage_name: str,
    impl: str,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    sub_id: str,
    cwd: str | None,
    standards: list | None,
    inputs: list[str] | None = None,
) -> tuple[dict, float]:
    """Run a single implementation slice. Returns (signal, elapsed_secs)."""
    from orchestrator.orchestrate import run_stage

    t0 = time.monotonic()
    sig = run_stage(
        stage_name,
        impl,
        variables,
        run_folder,
        ctx.docs_root,
        ctx.project,
        ctx.project_log_path,
        sub_id,
        cwd=cwd,
        standards=standards,
        runner=ctx.runner_for(stage_name),
        inputs=inputs,
    )
    return sig, time.monotonic() - t0


def _dispatch_slices(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
    # Late lookups through ``orchestrate`` so tests can patch these at
    # ``orchestrator.orchestrate.<name>`` and so this module avoids a circular
    # load with the dispatch table in ``orchestrate``.
    from orchestrator.orchestrate import (
        _create_branch,
        _impl_from_prompt,
        _maybe_capture_wave_baseline,
        _maybe_run_wave_verification,
        expand_nodes,
        stamp_node_passed_with_commits,
        update_plan_md,
    )

    signals = signals or {}
    stage_standards = ctx.project_standards if stage.standards else None
    try:
        _create_branch(ctx.branch, variables["repo_root"], ctx.logger, stage.name)
    except GitStateError as exc:
        ctx.logger.log(stage.name, "ERROR", f"git state error before slice dispatch: {exc}")
        return {"stage": stage.name, "status": "blocked", "message": str(exc)}

    # Capture the baseline verifier output before any slice touches the integration
    # branch so wave verification can distinguish pre-existing failures from
    # net-new regressions. Best-effort: a failure here only loses classification,
    # the pipeline still proceeds. See ADR-033.
    _maybe_capture_wave_baseline(stage, variables, run_folder, ctx)

    prior_sig = signals.get(stage.slices_from_stage or "", {}) if stage.slices_from_stage else {}
    slice_files: list[str] = prior_sig.get("slice_files", [])
    slice_groups: list[list[str]] = prior_sig.get("slice_groups", [])
    slice_inputs: list[list[str]] = prior_sig.get("slice_inputs", [])

    pre_count = len(slice_files)
    # Pair each slice_files entry with its (optional) per-slice inputs list so
    # filtering keeps them aligned by index. The planner emits slice_inputs in
    # the same order as slice_files; older planner runs that omit it leave the
    # list empty and we fall back to no input pills (the Prompt link still
    # renders alone).
    paired = list(zip(slice_files, slice_inputs + [[]] * (len(slice_files) - len(slice_inputs)), strict=False))
    paired = [(sf, ins) for sf, ins in paired if _SLICE_RE.search(Path(sf).name)]
    slice_files = [sf for sf, _ in paired]
    slice_inputs_by_file: dict[str, list[str]] = {sf: list(ins) for sf, ins in paired}
    if len(slice_files) != pre_count:
        ctx.logger.log(
            stage.name, "WARN", f"filtered {pre_count - len(slice_files)} non-slice file(s) from slice_files"
        )

    if slice_groups:
        slice_groups = [[sf for sf in g if _SLICE_RE.search(Path(sf).name)] for g in slice_groups]
        slice_groups = [g for g in slice_groups if g]
    if not slice_groups:
        slice_groups = [[sf] for sf in slice_files]

    all_slices = [sf for group in slice_groups for sf in group]
    slice_to_id = {sf: f"impl_{i + 1}" for i, sf in enumerate(all_slices)}
    expand_nodes(run_folder, stage, slice_files=all_slices, slice_groups=slice_groups)

    impl = _impl_from_prompt(stage.prompt or f"prompts/{stage.name}/default.md")
    all_commits: list[str] = []
    wave_verifications: list[dict] = []

    for wave_idx, group in enumerate(slice_groups, start=1):
        if len(group) == 1:
            slice_file = group[0]
            sub_id = slice_to_id[slice_file]
            vars_copy = dict(variables)
            vars_copy["slice_file"] = slice_file
            update_plan_md(run_folder, sub_id, "in_progress")
            sig, elapsed = _run_slice(
                stage.name,
                impl,
                vars_copy,
                run_folder,
                ctx,
                sub_id,
                variables.get("repo_root"),
                stage_standards,
                inputs=slice_inputs_by_file.get(slice_file),
            )
            if sig.get("status") != "passed":
                ctx.logger.log(
                    stage.name,
                    "ERROR",
                    f"pipeline stopped: stage {stage.name} {sig['status']} on slice {slice_file}: {sig.get('message', '')}",
                )
                update_plan_md(run_folder, sub_id, sig["status"])
                return {"stage": stage.name, "status": sig["status"], "message": sig.get("message", "")}
            commits = sig.get("commit_hashes", [])
            all_commits.extend(commits)
            stamp_node_passed_with_commits(
                run_folder,
                sub_id,
                elapsed_secs=elapsed,
                commits=commits,
                signal=sig,
                impl_name=impl,
                repo_root=variables.get("repo_root"),
            )
            wave_sig = _maybe_run_wave_verification(stage, wave_idx, variables, run_folder, ctx)
            if wave_sig is not None:
                wave_verifications.append(wave_sig)
                if wave_sig.get("status") == "blocked":
                    return {
                        "stage": stage.name,
                        "status": "blocked",
                        "message": wave_sig.get("message", f"wave {wave_idx} integration verification failed"),
                        "wave_verifications": wave_verifications,
                    }
        else:
            repo_root = variables["repo_root"]
            ctx.logger.log(stage.name, "INFO", f"dispatching {len(group)} implementation slices in parallel")
            worktrees: dict[str, tuple[str, str]] = {}
            failed_sig: dict | None = None
            try:
                try:
                    for sf in group:
                        sub_id = slice_to_id[sf]
                        temp_branch = f"{ctx.branch}-{sub_id}"
                        wt_path = _create_worktree(repo_root, temp_branch, ctx.branch, ctx.logger, stage.name)
                        worktrees[sub_id] = (wt_path, temp_branch)
                        update_plan_md(run_folder, sub_id, "in_progress")
                except GitStateError as exc:
                    ctx.logger.log(stage.name, "ERROR", f"worktree setup failed: {exc}")
                    failed_sig = {"status": "blocked", "message": str(exc)}

                futures2: dict[concurrent.futures.Future, tuple[str, str]] = {}
                if failed_sig is None:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(group)) as executor:
                        for slice_file in group:
                            sub_id = slice_to_id[slice_file]
                            wt_path, _ = worktrees[sub_id]
                            vars_copy = dict(variables)
                            vars_copy["slice_file"] = slice_file
                            fut = executor.submit(
                                _run_slice,
                                stage.name,
                                impl,
                                vars_copy,
                                run_folder,
                                ctx,
                                sub_id,
                                wt_path,
                                stage_standards,
                                slice_inputs_by_file.get(slice_file),
                            )
                            futures2[fut] = (sub_id, slice_file)

                for fut, (sub_id, slice_file) in futures2.items():
                    try:
                        sig, elapsed = fut.result()
                    except Exception as exc:
                        sig = {"status": "failed", "message": str(exc)}
                        elapsed = 0.0
                    if sig.get("status") != "passed":
                        ctx.logger.log(
                            stage.name,
                            "ERROR",
                            f"pipeline stopped: stage {stage.name} {sig['status']} on slice {slice_file}: {sig.get('message', '')}",
                        )
                        update_plan_md(run_folder, sub_id, sig["status"])
                        if failed_sig is None:
                            failed_sig = sig
                    else:
                        _, temp_branch = worktrees[sub_id]
                        try:
                            _merge_worktree_branch(repo_root, temp_branch, ctx.logger, stage.name)
                        except GitStateError as exc:
                            ctx.logger.log(
                                stage.name,
                                "ERROR",
                                f"pipeline stopped: merge failed for slice {slice_file}: {exc}",
                            )
                            update_plan_md(run_folder, sub_id, "blocked")
                            if failed_sig is None:
                                failed_sig = {"status": "blocked", "message": str(exc)}
                            continue
                        commits = sig.get("commit_hashes", [])
                        all_commits.extend(commits)
                        stamp_node_passed_with_commits(
                            run_folder,
                            sub_id,
                            elapsed_secs=elapsed,
                            commits=commits,
                            signal=sig,
                            impl_name=impl,
                            repo_root=variables.get("repo_root"),
                        )
            finally:
                for _sub_id, (wt_path, temp_branch) in worktrees.items():
                    _remove_worktree(repo_root, wt_path, temp_branch, ctx.logger, stage.name)

            if failed_sig is not None:
                return {
                    "stage": stage.name,
                    "status": failed_sig.get("status", "failed"),
                    "message": failed_sig.get("message", ""),
                }

            wave_sig = _maybe_run_wave_verification(stage, wave_idx, variables, run_folder, ctx)
            if wave_sig is not None:
                wave_verifications.append(wave_sig)
                if wave_sig.get("status") == "blocked":
                    return {
                        "stage": stage.name,
                        "status": "blocked",
                        "message": wave_sig.get("message", f"wave {wave_idx} integration verification failed"),
                        "wave_verifications": wave_verifications,
                    }

    result: dict = {"stage": stage.name, "status": "passed", "commit_hashes": all_commits, "branch": ctx.branch}
    if wave_verifications:
        result["wave_verifications"] = wave_verifications
    return result
