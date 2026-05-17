"""Stage dispatch helpers extracted from ``orchestrator.orchestrate``.

Pure relocation — no behaviour changes. Owns the five non-slice dispatchers
keyed off ``StageConfig.expansion`` (``_dispatch_default``, ``_dispatch_tracks``,
``_dispatch_prompts``), plus the interactive-stage dispatcher, the alignment
policy gate (ADR-032), and the fix-verification cycle helper (ADR-021).

Slice dispatch lives in ``orchestrator.slice_dispatcher``; this module mirrors
that split so ``orchestrate`` can stay focused on the run-pipeline loop and
post-loop finalisation.

Collaborator lookups (``run_stage``, ``run_deterministic_stage``,
``update_plan_md``, ``resolve_review_subnode_statuses``, ``_create_branch``,
``_impl_from_prompt``, etc.) go through the ``orchestrate`` module at call
time. Two reasons: it avoids a circular module-load with the dispatch table
in ``orchestrate``, and it preserves the patch path of every existing test —
patches at ``orchestrator.orchestrate.<name>`` keep reaching the actual call
sites.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import orchestrator.review_cycle as review_cycle_mod
from orchestrator._git import GitStateError
from orchestrator.logger import OrchestratorLogger
from orchestrator.profile import ExpansionKind, StageConfig
from orchestrator.slice_dispatcher import _dispatch_slices

if TYPE_CHECKING:
    from orchestrator.orchestrate import _PipelineContext


def _dispatch_default(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
    from orchestrator.orchestrate import _create_branch, _impl_from_prompt, run_stage

    impl = _impl_from_prompt(stage.prompt or f"prompts/{stage.name}/default.md")
    stage_cwd = variables.get("repo_root") if stage.cwd_from_repo_root else None
    stage_standards = ctx.project_standards if stage.standards else None
    # Stages that run in the repo root and may commit must do so on ctx.branch.
    # The slice dispatcher already enforces this before fan-out; mirror it here
    # so single-agent flows (e.g. the minimal profile's implementation stage)
    # do not commit to whatever branch happened to be checked out.
    if stage.cwd_from_repo_root:
        try:
            _create_branch(ctx.branch, variables["repo_root"], ctx.logger, stage.name)
        except GitStateError as exc:
            ctx.logger.log(stage.name, "ERROR", f"git state error before {stage.name} dispatch: {exc}")
            return {"stage": stage.name, "status": "blocked", "message": str(exc)}
    return run_stage(
        stage.name,
        impl,
        variables,
        run_folder,
        ctx.docs_root,
        ctx.project,
        ctx.project_log_path,
        cwd=stage_cwd,
        standards=stage_standards,
        runner=ctx.runner_for(stage.name),
    )


def _apply_alignment_policy(stage: StageConfig, sig: dict, logger: OrchestratorLogger) -> dict:
    """Gate the alignment stage's signal against the configured policy.

    Discovery surfaces unresolved questions/risks/assumptions as structured
    alignment inputs; alignment resolves what it can and reports leftover items
    in ``unresolved_remaining``. This function inspects that list and either:

    - logs a warning and returns the signal unchanged (``warn``, the default),
    - or converts the signal to ``status: blocked`` (``block``).

    The gate only fires for the ``alignment`` stage, and only when the signal
    is currently passing — failed/blocked signals reach the normal halt path
    untouched. Interactive alignment does not emit ``unresolved_remaining`` in
    its signal (the artifact-existence check is the gate), so the policy is a
    no-op there too. See ADR-032.
    """
    if stage.name != "alignment" or sig.get("status") != "passed":
        return sig
    raw_remaining = sig.get("unresolved_remaining")
    if not isinstance(raw_remaining, list):
        return sig
    # Normalise away empty / whitespace-only entries before counting. An LLM that
    # emits ``unresolved_remaining: [""]`` to satisfy the "always required" field
    # contract must not be treated as having left residue.
    remaining = [s for s in (str(v).strip() for v in raw_remaining) if s]
    if not remaining:
        return sig
    policy = stage.alignment_policy
    on_unresolved = policy.on_unresolved if policy is not None else "warn"
    n = len(remaining)
    if on_unresolved == "block":
        first = remaining[0]
        preview = first if len(first) <= 120 else first[:117] + "..."
        msg = f"alignment left {n} unresolved item{'s' if n != 1 else ''}: {preview}"
        logger.log("alignment", "ERROR", f"alignment_policy=block — {msg}")
        blocked = dict(sig)
        blocked["status"] = "blocked"
        blocked["message"] = msg
        return blocked
    logger.log(
        "alignment",
        "WARN",
        f"alignment_policy=warn — {n} unresolved item{'s' if n != 1 else ''} after alignment; specification proceeds",
    )
    return sig


def _dispatch_interactive(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
    from orchestrator.orchestrate import run_interactive_stage, update_plan_md

    if not stage.artifact:
        return {
            "stage": stage.name,
            "status": "blocked",
            "message": f"interactive stage '{stage.name}' missing required 'artifact' field",
        }
    artifact_path = run_folder / stage.name / stage.artifact
    if artifact_path.exists():
        artifact_key = Path(stage.artifact).stem.replace("-", "_")
        return {"stage": stage.name, "status": "passed", artifact_key: str(artifact_path)}
    update_plan_md(run_folder, stage.name, "in_progress")
    return run_interactive_stage(
        stage.name,
        stage.prompt,
        variables,
        run_folder,
        artifact_path,
        ctx.docs_root,
        ctx.project,
        ctx.project_log_path,
    )


def _run_track(
    track: dict, stage: StageConfig, variables: dict, run_folder: Path, ctx: _PipelineContext, node_ids: dict
) -> tuple[str, dict]:
    """Run a single discovery track and update its plan node. Returns (name, signal)."""
    from orchestrator.orchestrate import run_stage, update_plan_md

    tid = node_ids.get(track["name"])
    if tid:
        update_plan_md(run_folder, tid, "in_progress")
    t_start = time.monotonic()
    sig = run_stage(
        stage.name,
        "pregenerated",
        dict(variables),
        run_folder,
        ctx.docs_root,
        ctx.project,
        ctx.project_log_path,
        output_suffix=track["name"],
        prompt_file=track["prompt_file"],
        schema_name="discovery_track",
        runner=ctx.runner_for(stage.name),
        inputs=list(track.get("inputs") or []),
        node_id=tid,
    )
    track_elapsed = time.monotonic() - t_start
    if tid:
        t_status = "passed" if sig.get("status") == "passed" else "blocked"
        update_plan_md(run_folder, tid, t_status, elapsed_secs=track_elapsed, output_summary=sig.get("summary", ""))
    return track["name"], sig


def _dispatch_tracks(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
    from orchestrator.orchestrate import expand_nodes, run_stage

    planning_sig = run_stage(
        stage.name,
        "planning",
        variables,
        run_folder,
        ctx.docs_root,
        ctx.project,
        ctx.project_log_path,
        output_suffix="planning",
        schema_name="discovery_planning",
        runner=ctx.runner_for(stage.name),
        node_id=f"{stage.name}_planning",
    )
    if planning_sig.get("status") != "passed":
        return planning_sig

    tracks = planning_sig.get("tracks", [])
    if not tracks:
        return {
            "stage": stage.name,
            "status": "blocked",
            "message": f"{stage.name} planning produced no tracks — verify --feature-path contains overview.md",
        }

    ctx.logger.log(stage.name, "INFO", f"planning complete: {len(tracks)} track{'s' if len(tracks) != 1 else ''}")
    node_ids = expand_nodes(run_folder, stage, tracks=tracks, planning_elapsed_secs=0)

    if len(tracks) == 1:
        _, sig = _run_track(tracks[0], stage, variables, run_folder, ctx, node_ids)
        track_results = {tracks[0]["name"]: sig}
    else:
        ctx.logger.log(stage.name, "INFO", f"dispatching {len(tracks)} tracks in parallel")
        futures: dict[concurrent.futures.Future, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tracks)) as executor:
            for track in tracks:
                fut = executor.submit(_run_track, track, stage, dict(variables), run_folder, ctx, node_ids)
                futures[fut] = track["name"]
        track_results = {}
        for fut, name in futures.items():
            try:
                _, sig = fut.result()
            except Exception as exc:
                sig = {"status": "failed", "message": str(exc)}
            track_results[name] = sig

    failed_tracks = [n for n, s in track_results.items() if s.get("status") != "passed"]
    if failed_tracks:
        for name in failed_tracks:
            s = track_results[name]
            ctx.logger.log(
                stage.name, "ERROR", f"{stage.name} track '{name}' {s.get('status')}: {s.get('message', '')}"
            )
        return {"stage": stage.name, "status": "blocked", "message": f"tracks failed: {', '.join(failed_tracks)}"}

    aggregated_tracks = []
    findings_files = []
    unresolved_questions: list[str] = []
    risks: list[str] = []
    assumptions_needed: list[str] = []
    for track in tracks:
        track_sig = track_results[track["name"]]
        ff = track_sig.get("findings_file", "")
        aggregated_tracks.append(
            {
                "name": track["name"],
                "summary": track_sig.get("summary", ""),
                "findings_file": ff,
            }
        )
        if ff:
            findings_files.append(ff)
        # Unresolved items are structured alignment inputs — flatten them across
        # tracks so the alignment stage sees one merged list per category. The
        # presence of an item is what alignment needs to resolve; deduping is
        # left to alignment because two tracks may surface the same risk under
        # different wording. See ADR-032.
        for key, bucket in (
            ("unresolved_questions", unresolved_questions),
            ("risks", risks),
            ("assumptions_needed", assumptions_needed),
        ):
            val = track_sig.get(key)
            if isinstance(val, list):
                bucket.extend(str(v) for v in val if v)

    return {
        "stage": stage.name,
        "status": "passed",
        "tracks": aggregated_tracks,
        "findings_files": findings_files,
        "unresolved_questions": unresolved_questions,
        "risks": risks,
        "assumptions_needed": assumptions_needed,
    }


def _dispatch_prompts(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
    from orchestrator.orchestrate import _impl_from_prompt, resolve_review_subnode_statuses, run_stage, update_plan_md

    signals = signals or {}
    review_md_path = run_folder / stage.name / "review-log.md"
    review_md_path.parent.mkdir(parents=True, exist_ok=True)
    variables = dict(variables)
    variables["review_md"] = str(review_md_path)
    variables["round"] = "1"

    commit_hashes: list[str] = []
    for sig in signals.values():
        if isinstance(sig, dict):
            commit_hashes.extend(sig.get("commit_hashes", []))
    if commit_hashes and "repo_root" in variables:
        diff_text = review_cycle_mod.compute_stage_diff(variables["repo_root"], commit_hashes)
        diff_path = run_folder / stage.name / "diff-round-1.patch"
        diff_path.write_text(diff_text)
        variables["diff"] = str(diff_path)
    else:
        variables["diff"] = ""

    # Deterministic gate: reject the review stage if the diff input is missing, empty, or
    # not a real git diff. Without this, the reviewer prompt's rejection rule is the only
    # backstop, and an LLM may still attempt a speculative review of a prose summary.
    if not review_cycle_mod.is_valid_diff_file(variables["diff"]):
        msg = (
            f"no valid git diff for review stage '{stage.name}' "
            f"(diff={variables['diff']!r}); upstream stage produced no commits or diff is not a git diff"
        )
        ctx.logger.log(stage.name, "ERROR", msg)
        return {"stage": stage.name, "status": "blocked", "message": msg}

    reviewer_statuses: dict[str, str] = {}
    reviewer_findings: dict[str, list[str]] = {}
    reviewer_non_blocking_findings: dict[str, list[str]] = {}
    changes_requested: list[str] = []
    for reviewer, prompt_path in stage.prompts.items():
        sub_id = f"{stage.name}_{reviewer}"
        update_plan_md(run_folder, sub_id, "in_progress")
        impl = _impl_from_prompt(prompt_path)
        t0 = time.monotonic()
        sig = run_stage(
            stage.name,
            impl,
            variables,
            run_folder,
            ctx.docs_root,
            ctx.project,
            ctx.project_log_path,
            output_suffix=reviewer,
            cwd=variables.get("repo_root") or None,
            runner=ctx.runner_for(stage.name),
            node_id=sub_id,
        )
        elapsed = time.monotonic() - t0
        # A reviewer sub-stage that did not pass (runner failure, missing signal,
        # declared-artifact missing, etc.) never produced an informed verdict —
        # propagate the failure upward instead of silently treating a missing
        # verdict as approval.
        if sig.get("status") != "passed":
            sub_msg = sig.get("message", "reviewer sub-stage did not pass")
            update_plan_md(run_folder, sub_id, "blocked", elapsed_secs=elapsed, output_summary=sub_msg, impl_name=impl)
            ctx.logger.log(stage.name, "ERROR", f"reviewer '{reviewer}' blocked: {sub_msg}")
            return {
                "stage": stage.name,
                "status": "blocked",
                "message": f"reviewer '{reviewer}' did not produce a verdict: {sub_msg}",
            }
        verdict = sig.get("reviewer_statuses", {}).get(reviewer, "unknown")
        reviewer_statuses[reviewer] = verdict
        findings = sig.get("findings", [])
        if isinstance(findings, list) and findings:
            reviewer_findings[reviewer] = findings
        non_blocking = sig.get("non_blocking_findings", [])
        if isinstance(non_blocking, list) and non_blocking:
            reviewer_non_blocking_findings[reviewer] = non_blocking
        if verdict == "changes-requested":
            changes_requested.append(reviewer)
        sub_status = "blocked" if verdict == "changes-requested" else "passed"
        update_plan_md(run_folder, sub_id, sub_status, elapsed_secs=elapsed, output_summary=verdict, impl_name=impl)

    review_signal: dict = {
        "status": "passed",
        "reviewer_statuses": reviewer_statuses,
        "reviewer_findings": reviewer_findings,
        # Carried into review_cycle.run as the seed for accepted_risks, persisted in
        # plan.md after every cycle terminates (success, max-cycles, or invalid-diff abort).
        "reviewer_non_blocking_findings": reviewer_non_blocking_findings,
        "changes_requested": changes_requested,
        "review_md": str(review_md_path),
    }

    if changes_requested:
        ctx.logger.log(stage.name, "WARN", f"changes requested by: {', '.join(changes_requested)}")
        result = review_cycle_mod.run(
            run_folder,
            ctx.docs_root,
            ctx.project,
            ctx.branch,
            review_signal,
            ctx.project_log_path,
            repo_root=variables.get("repo_root", ""),
            implementation_runner=ctx.runner_for("implementation"),
            review_runner=ctx.runner_for(stage.name),
        )
        # Overwrite the aggregate review signal with the cycle's final reviewer_statuses
        # so _state.yaml and plan.md reflect the terminal outcome rather than the initial
        # round-1 verdicts. Without this, a successful re-review still shows
        # `changes_requested` in the persisted signal.
        final_statuses = result.get("reviewer_statuses")
        if isinstance(final_statuses, dict):
            review_signal["reviewer_statuses"] = final_statuses
            review_signal["changes_requested"] = [r for r, s in final_statuses.items() if s == "changes-requested"]
            # Re-stamp the round-1 sub-nodes so an approved final cycle does not leave
            # a red round-1 node beside a green round-N node. See ADR-026.
            resolve_review_subnode_statuses(run_folder, final_statuses)
        if not result.get("all_passed"):
            ctx.logger.log(
                stage.name, "ERROR", f"pipeline stopped: review cycle blocked, reviewers={result.get('reviewers', [])}"
            )
            return {
                "stage": stage.name,
                "status": "blocked",
                "message": f"review cycle incomplete, reviewers={result.get('reviewers', [])}",
            }
    else:
        # No cycle needed (all approved), but still surface non-blocking findings as accepted risks.
        review_cycle_mod.append_findings_summary(
            run_folder / "plan.md",
            findings_map={},
            reviewer_statuses=reviewer_statuses,
            accepted_risks=reviewer_non_blocking_findings,
        )

    return review_signal


def _run_fix_verification_cycle(
    verify_sig: dict,
    run_folder: Path,
    variables: dict,
    ctx: _PipelineContext,
) -> dict:
    """Run one fix→re-verify cycle when verification_status=failed.

    Dispatches a fix-verification agent with the VERIFY.md report as its primary
    input, then re-runs deterministic verification. Returns the updated verification
    signal on success or a blocked signal if the fix makes no commits or re-verification
    still fails. See ADR-021.

    Also injects a first-class ``fix_verification`` node into the plan graph so
    the diagram and Run Summary reflect the remediation step instead of hiding
    its artifacts in the "Other files" strip. See issue #194.
    """
    from orchestrator.orchestrate import _fmt_elapsed, run_deterministic_stage, run_stage
    from orchestrator.plan import add_fix_verification_node, update_plan_md

    repo_root = variables.get("repo_root", "")
    verify_md_path = verify_sig.get("verify_md_path", "")
    verify_json_path = verify_sig.get("verify_json_path", "")

    fix_vars = {
        "run_folder": str(run_folder),
        "docs_root": ctx.docs_root,
        "branch": ctx.branch,
        "verify_md_path": verify_md_path,
        "verify_json_path": verify_json_path,
        "repo_root": repo_root,
    }

    fix_meta = ctx.agent_metadata.get("implementation", {}) or {}
    add_fix_verification_node(
        run_folder,
        status="in_progress",
        backend=fix_meta.get("backend") or "",
        model=fix_meta.get("model") or "",
    )

    before_head = review_cycle_mod._head_sha(repo_root)
    fix_t0 = time.monotonic()
    fix_sig = run_stage(
        "fix-verification",
        "default",
        fix_vars,
        run_folder,
        ctx.docs_root,
        ctx.project,
        ctx.project_log_path,
        cwd=repo_root or None,
        runner=ctx.runner_for("implementation"),
        node_id="fix_verification",
    )
    fix_elapsed = time.monotonic() - fix_t0
    fix_status = fix_sig.get("status", "unknown")
    actual_hashes = (
        review_cycle_mod._commits_since(repo_root, before_head) if before_head else fix_sig.get("commit_hashes", [])
    )

    n = len(actual_hashes)
    commit_summary = f"{n} commit{'s' if n != 1 else ''}" if actual_hashes else "no commits"
    ctx.logger.log("fix-verification", "INFO", f"default {fix_status} ({_fmt_elapsed(fix_elapsed)}) — {commit_summary}")

    if fix_status != "passed" or not actual_hashes:
        msg = f"fix-verification made no commits (agent status={fix_status!r}, commits={actual_hashes!r})"
        ctx.logger.log("fix-verification", "ERROR", msg)
        update_plan_md(run_folder, "fix_verification", "blocked", elapsed_secs=fix_elapsed)
        return {"stage": "fix-verification", "status": "blocked", "message": msg}

    fix_sig_for_plan = dict(fix_sig)
    fix_sig_for_plan["commit_hashes"] = actual_hashes
    update_plan_md(
        run_folder,
        "fix_verification",
        "passed",
        elapsed_secs=fix_elapsed,
        signal=fix_sig_for_plan,
        impl_name="Default",
        repo_root=repo_root or None,
    )

    ctx.logger.log("verification", "INFO", "re-running verification after fix-verification")
    new_verify_sig = run_deterministic_stage("verification", repo_root, run_folder, ctx.project_log_path)

    if new_verify_sig.get("verification_status") == "failed":
        msg = "verification_status=failed after fix-verification cycle"
        ctx.logger.log("verification", "ERROR", msg)
        return {"stage": "verification", "status": "blocked", "message": msg}

    new_verify_sig["commit_hashes"] = actual_hashes
    return new_verify_sig


_DISPATCHERS: dict[ExpansionKind, Callable] = {
    ExpansionKind.NONE: _dispatch_default,
    ExpansionKind.TRACKS: _dispatch_tracks,
    ExpansionKind.SLICES: _dispatch_slices,
    ExpansionKind.PROMPTS: _dispatch_prompts,
}
