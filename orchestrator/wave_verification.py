"""Wave-verification helpers extracted from ``orchestrator.orchestrate``.

Pure relocation — no behaviour changes. Pins ADR-030 (config-driven wave
verification), ADR-031 (slice vs. wave node split), and ADR-033 (net-new vs.
baseline classification). Call sites stay in ``orchestrate._dispatch_slices``;
this module only owns the per-wave verifier dispatch, fix-then-retry loop, and
plan.md / graph stamping that follows each wave merge.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.plan import update_plan_md
from orchestrator.profile import StageConfig
from orchestrator.run_stage import _fmt_elapsed, run_stage

if TYPE_CHECKING:
    from orchestrator.orchestrate import _PipelineContext


def _maybe_capture_wave_baseline(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
) -> None:
    """Capture the pre-pipeline verifier baseline for later net-new comparison.

    Runs the verifier engine against the integration branch *before* any slice
    has changed it, writing the result to ``baseline-verification/verify.json``
    in the run folder. Subsequent wave verifications load this file to classify
    failures as ``baseline`` vs ``net_new``.

    Idempotent — silently no-ops if a baseline file already exists (matters for
    a resumed pipeline). Best-effort — failures are logged and swallowed so the
    pipeline still proceeds without classification. Only runs when the stage
    has wave verification enabled; non-wave stages do not need a baseline. See
    ADR-033.
    """
    wv = stage.wave_verification
    if wv is None or not wv.enabled:
        return

    from orchestrator.verifiers import engine as verifier_engine
    from orchestrator.verifiers.engine import VerificationError

    baseline_file = verifier_engine.baseline_path_for(run_folder)
    if baseline_file.exists():
        return

    # On a resumed run the integration branch already carries earlier slice
    # commits, so a fresh capture here would snapshot pipeline-introduced
    # regressions as "baseline". The ADR-033 contract is "missing baseline
    # degrades to pre-ADR behaviour", not "synthesise a contaminated baseline".
    # Skip the capture and let the verifier fall back to no-classification.
    if ctx.resume:
        ctx.logger.log(
            "wave-verification",
            "WARN",
            "baseline capture skipped on resume — original baseline missing; "
            "wave verification will fall back to no-classification",
        )
        return

    repo_root = variables.get("repo_root")
    if not repo_root:
        ctx.logger.log("wave-verification", "WARN", "baseline capture skipped — no repo_root in variables")
        return

    ctx.logger.log("wave-verification", "INFO", "capturing pre-pipeline verifier baseline")
    try:
        sig = verifier_engine.capture_baseline(Path(repo_root), run_folder)
    except VerificationError as exc:
        ctx.logger.log("wave-verification", "WARN", f"baseline capture could not start: {exc}")
        return
    except Exception as exc:
        ctx.logger.log("wave-verification", "WARN", f"baseline capture failed: {exc}")
        return

    ctx.logger.log(
        "wave-verification",
        "INFO",
        f"baseline captured: {sig.get('summary', '')}",
    )


def _maybe_run_wave_verification(
    stage: StageConfig,
    wave_idx: int,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
) -> dict | None:
    """Run deterministic verification against the integration branch after a wave merges.

    Returns ``None`` if wave verification is not configured for this stage (e.g.
    the stage does not use slice expansion, or it was explicitly disabled). Returns
    a signal dict otherwise:

    - ``status=passed`` when the dispatcher should continue (passed, warned,
      skipped, or failed-under-``warn``/``fix_then_retry`` policies). The
      ``verification_status`` field carries the underlying verifier verdict so
      reviewers and post-mortems can see integration health per wave.
    - ``status=blocked`` only when the policy is ``block`` and verification
      failed; the dispatcher converts this into a pipeline halt.

    The hook is keyed off ``stage.wave_verification`` — never off the profile
    name. See ADR-030.
    """
    wv = stage.wave_verification
    if wv is None or not wv.enabled:
        return None

    from orchestrator.verifiers import engine as verifier_engine
    from orchestrator.verifiers.engine import VerificationError

    repo_root = variables.get("repo_root")
    if not repo_root:
        ctx.logger.log("wave-verification", "WARN", f"wave {wave_idx} skipped — no repo_root in variables")
        return {"stage": "wave-verification", "status": "passed", "verification_status": "skipped"}

    artifact_subdir = f"wave-verification/wave-{wave_idx}"
    baseline_file = verifier_engine.baseline_path_for(run_folder)
    baseline_arg = baseline_file if baseline_file.exists() else None
    ctx.logger.log("wave-verification", "INFO", f"dispatching wave {wave_idx} against integration branch")
    t0 = time.monotonic()
    try:
        sig = verifier_engine.verify(
            Path(repo_root),
            run_folder,
            artifact_subdir=artifact_subdir,
            baseline_path=baseline_arg,
        )
    except VerificationError as exc:
        ctx.logger.log("wave-verification", "WARN", f"wave {wave_idx} could not start: {exc}")
        return {
            "stage": "wave-verification",
            "status": "passed",
            "verification_status": "skipped",
            "summary": f"wave {wave_idx} verification could not start: {exc}",
        }
    elapsed = time.monotonic() - t0

    sig["wave_idx"] = wave_idx
    sig["on_failure"] = wv.on_failure
    sig["elapsed_secs"] = elapsed
    vstatus = sig.get("verification_status", "unknown")
    # ``net_new_status`` falls back to ``verification_status`` when no baseline
    # was available, so the policy gate degrades gracefully into the pre-ADR-033
    # behaviour (warn/block on any failure) rather than silently passing.
    net_new_status = sig.get("net_new_status", vstatus)
    summary = sig.get("summary", "")

    if vstatus == "failed":
        node_status = "blocked"
        if net_new_status == "failed":
            ctx.logger.log(
                "wave-verification",
                "WARN",
                f"wave {wave_idx} failed (net-new, {_fmt_elapsed(elapsed)}) on_failure={wv.on_failure}: {summary}",
            )
            if wv.on_failure == "fix_then_retry":
                sig = _wave_fix_then_retry(stage, wave_idx, sig, variables, run_folder, ctx)
                vstatus = sig.get("verification_status", "unknown")
                net_new_status = sig.get("net_new_status", vstatus)
                if net_new_status == "failed":
                    ctx.logger.log(
                        "wave-verification",
                        "WARN",
                        f"wave {wave_idx} net-new still failing after fix_then_retry — continuing",
                    )
            if wv.on_failure == "block" and net_new_status == "failed":
                sig["status"] = "blocked"
                sig["message"] = f"wave {wave_idx} integration verification failed (net-new): {summary}"
        else:
            # Baseline-only failures: pre-existing red tests, not regressions.
            # Always warn — never trigger fix or block — so a project carrying
            # known-failing tests can still advance waves under any policy.
            ctx.logger.log(
                "wave-verification",
                "WARN",
                f"wave {wave_idx} baseline-only failures ({_fmt_elapsed(elapsed)}) — continuing: {summary}",
            )

        # Always stamp the wave node as ``blocked`` on a failed integration check —
        # even when the policy is ``warn`` or ``fix_then_retry`` and the pipeline
        # continues. The slice nodes report local completion; this node is the
        # only place a reader sees integration health. See ADR-031.
    else:
        ctx.logger.log(
            "wave-verification",
            "INFO",
            f"wave {wave_idx} {vstatus} ({_fmt_elapsed(elapsed)}) — {summary}",
        )
        node_status = "passed" if vstatus == "passed" else "skipped"

    _stamp_wave_node(run_folder, wave_idx, node_status, elapsed)
    _append_wave_verification_section(run_folder, wave_idx, sig)
    return sig


def _stamp_wave_node(run_folder: Path, wave_idx: int, status: str, elapsed: float) -> None:
    """Stamp the wave_verify_{N} graph node with the integration-check outcome.

    The slice nodes (``impl_{N}``) keep their local "passed" status to represent
    slice completion; this node carries the merged-branch verdict so a passing
    slice and a failed wave render side-by-side without one masking the other.
    No-op when the run's plan graph never expanded a wave node (e.g. wave
    verification was disabled by config at expansion time). See ADR-031.
    """
    from orchestrator.plan._graph import load_graph

    wave_id = f"wave_verify_{wave_idx}"
    graph = load_graph(run_folder)
    if graph is None or wave_id not in graph.nodes:
        return
    update_plan_md(run_folder, wave_id, status, elapsed_secs=elapsed)


def _wave_fix_then_retry(
    stage: StageConfig,
    wave_idx: int,
    failed_sig: dict,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
) -> dict:
    """Dispatch a fix-verification agent against the wave VERIFY.md, then re-verify.

    Returns the re-verification signal (whether or not the fix worked). The caller
    decides what to do with a still-failed result based on the policy.
    """
    repo_root = variables.get("repo_root", "")
    verify_md_path = failed_sig.get("verify_md_path", "")
    verify_json_path = failed_sig.get("verify_json_path", "")
    fix_vars = {
        "run_folder": str(run_folder),
        "docs_root": ctx.docs_root,
        "branch": ctx.branch,
        "verify_md_path": verify_md_path,
        "verify_json_path": verify_json_path,
        "repo_root": repo_root,
    }
    ctx.logger.log("wave-verification", "INFO", f"wave {wave_idx} dispatching fix-verification agent")
    fix_sig = run_stage(
        "fix-verification",
        "default",
        fix_vars,
        run_folder,
        ctx.docs_root,
        ctx.project,
        ctx.project_log_path,
        cwd=repo_root or None,
        runner=ctx.runner_for(stage.name),
    )
    if fix_sig.get("status") != "passed":
        ctx.logger.log(
            "wave-verification",
            "WARN",
            f"wave {wave_idx} fix-verification did not pass: {fix_sig.get('message', '')}",
        )
        return failed_sig

    from orchestrator.verifiers import engine as verifier_engine
    from orchestrator.verifiers.engine import VerificationError

    retry_subdir = f"wave-verification/wave-{wave_idx}/retry"
    # Re-verification must carry the same baseline as the initial wave run, or
    # the retry would reclassify pre-existing baseline failures as net-new and
    # mask the fact that the fixer actually resolved the regression. See ADR-033.
    baseline_file = verifier_engine.baseline_path_for(run_folder)
    baseline_arg = baseline_file if baseline_file.exists() else None
    try:
        retry_sig = verifier_engine.verify(
            Path(repo_root),
            run_folder,
            artifact_subdir=retry_subdir,
            baseline_path=baseline_arg,
        )
    except VerificationError as exc:
        ctx.logger.log("wave-verification", "WARN", f"wave {wave_idx} retry could not start: {exc}")
        return failed_sig
    retry_sig["wave_idx"] = wave_idx
    retry_sig["on_failure"] = stage.wave_verification.on_failure if stage.wave_verification else "warn"
    retry_sig["retry"] = True
    return retry_sig


def _append_wave_verification_section(run_folder: Path, wave_idx: int, sig: dict) -> None:
    """Append a wave-verification result section to plan.md.

    Surfaces the per-wave verifier verdict alongside the stage's commit list so
    reviewers can see integration health at the wave boundary without trawling
    run.log. Best-effort: silently no-ops if plan.md does not exist.
    """
    plan_path = run_folder / "plan.md"
    if not plan_path.exists():
        return
    vstatus = sig.get("verification_status", "unknown")
    net_new_status = sig.get("net_new_status", vstatus)
    summary = sig.get("summary", "")
    elapsed = sig.get("elapsed_secs")
    elapsed_str = f" ({_fmt_elapsed(elapsed)})" if isinstance(elapsed, int | float) else ""
    on_failure = sig.get("on_failure", "warn")
    retry_note = " (after fix_then_retry)" if sig.get("retry") else ""

    lines = [
        "",
        f"## Wave {wave_idx} Verification{retry_note}",
        f"_Integration branch verification — `{vstatus}`{elapsed_str} (policy: `{on_failure}`)._",
        "",
    ]

    if sig.get("baseline_compared"):
        base_cmds = sig.get("baseline_failed_command_ids", []) or []
        base_probes = sig.get("baseline_failed_probe_ids", []) or []
        new_cmds = sig.get("new_failed_command_ids", []) or []
        new_probes = sig.get("new_failed_probe_ids", []) or []
        resolved_cmds = sig.get("resolved_command_ids", []) or []
        resolved_probes = sig.get("resolved_probe_ids", []) or []
        lines.append(f"- Net-new status: `{net_new_status}`")
        lines.append(
            f"- Baseline-only failures: {len(base_cmds) + len(base_probes)}"
            + (f" (commands: {', '.join(base_cmds)}" if base_cmds else "")
            + (f"; probes: {', '.join(base_probes)}" if base_probes else "")
            + (")" if base_cmds or base_probes else "")
        )
        lines.append(
            f"- Net-new failures: {len(new_cmds) + len(new_probes)}"
            + (f" (commands: {', '.join(new_cmds)}" if new_cmds else "")
            + (f"; probes: {', '.join(new_probes)}" if new_probes else "")
            + (")" if new_cmds or new_probes else "")
        )
        if resolved_cmds or resolved_probes:
            lines.append(
                f"- Resolved baseline failures: {len(resolved_cmds) + len(resolved_probes)}"
                + (f" (commands: {', '.join(resolved_cmds)}" if resolved_cmds else "")
                + (f"; probes: {', '.join(resolved_probes)}" if resolved_probes else "")
                + ")"
            )
        lines.append("")

    if summary:
        lines.append(summary)
        lines.append("")
    for label, key in (("VERIFY.md", "verify_md_path"), ("verify.json", "verify_json_path")):
        path_str = sig.get(key)
        if isinstance(path_str, str) and path_str:
            try:
                rel: Path | str = Path(path_str).relative_to(run_folder)
            except ValueError:
                rel = path_str
            lines.append(f"- [{label}]({rel})")
    lines.append("")

    section_text = "\n".join(lines)
    content = plan_path.read_text()
    markers = ["\n## File Manifest", "\n## Run Summary"]
    insert_at = len(content)
    for marker in markers:
        idx = content.find(marker)
        if 0 <= idx < insert_at:
            insert_at = idx
    if insert_at < len(content):
        plan_path.write_text(content[:insert_at] + section_text + content[insert_at:])
    else:
        plan_path.write_text(content + section_text)
