import concurrent.futures
import datetime
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

import orchestrator.review_cycle as review_cycle_mod
from orchestrator import _git as git_state
from orchestrator import _git_setup, _github, glossary, paths
from orchestrator import state as state_mod
from orchestrator._git import GitStateError
from orchestrator.agent_runner import AgentConfig, AgentRunner, build_runner, resolve_agent_config
from orchestrator.logger import OrchestratorLogger
from orchestrator.plan import (
    expand_nodes,
    init_plan_md,
    mark_pipeline_done,
    mark_pr_blocked,
    resolve_review_subnode_statuses,
    set_pr_node,
    set_pr_notice,
    update_plan_md,
)
from orchestrator.profile import ExpansionKind, Profile, StageConfig, load_profile
from orchestrator.run_stage import _fmt_elapsed, run_deterministic_stage, run_interactive_stage, run_stage

_SLICE_RE = re.compile(r"S-\d+-")


@dataclass
class _PipelineContext:
    docs_root: str
    project: str
    project_log_path: str
    logger: OrchestratorLogger
    branch: str
    project_config: dict
    project_standards: list
    runners: dict[str, AgentRunner]
    agent_metadata: dict[str, dict[str, str | None]]
    # True when ``run_pipeline`` was invoked with ``--resume``. Used by the
    # baseline-capture path to refuse snapshotting an already-mutated integration
    # branch when the run's original baseline is gone. See ADR-033.
    resume: bool = False

    def runner_for(self, stage_name: str) -> AgentRunner | None:
        # Returns None for stages without a runner (e.g. deterministic stages or test
        # contexts that patch run_stage at the call site). run_stage falls back to its
        # default ClaudeCodeRunner when runner=None.
        return self.runners.get(stage_name)


def _output_summary(stage: StageConfig, signal: dict) -> str | None:
    if stage.expansion == ExpansionKind.TRACKS:
        tracks = signal.get("tracks", [])
        n = len(signal.get("findings_files", []))
        if tracks:
            t = len(tracks)
            return f"{t} track{'s' if t != 1 else ''}, {n} finding{'s' if n != 1 else ''}"
        return f"{n} research file{'s' if n != 1 else ''}" if n else None

    if stage.expansion == ExpansionKind.SLICES:
        n = len(signal.get("commit_hashes", []))
        return f"{n} commit{'s' if n != 1 else ''}" if n else None

    if stage.expansion == ExpansionKind.PROMPTS:
        statuses = signal.get("reviewer_statuses", {})
        if statuses:
            return ", ".join(f"{r}: {v}" for r, v in statuses.items())
        return None

    return _generic_summary(signal)


def _generic_summary(signal: dict) -> str | None:
    """Build a short summary from well-known signal fields without naming the stage."""
    parts = []
    if outcome := signal.get("outcome"):
        parts.append(str(outcome))
    if vstatus := signal.get("verification_status"):
        toolchain = signal.get("toolchain", "?")
        parts.append(f"{toolchain}: {vstatus}")
    if signal.get("prd_path"):
        parts.append("PRD")
    if signal.get("context_path"):
        parts.append("context")
    for key, label in [
        ("adr_paths", "ADR"),
        ("slice_files", "implementation slice"),
        ("kb_files", "KB file"),
        ("adr_files", "ADR"),
    ]:
        val = signal.get(key)
        if isinstance(val, list) and val:
            n = len(val)
            parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    return ", ".join(parts) if parts else None


def _load_project_config(docs_root: str, project: str) -> dict:
    config_path = paths.require_file(Path(docs_root) / "projects" / project / "project.yaml")
    return yaml.safe_load(config_path.read_text())  # type: ignore[no-any-return]


_BUNDLED_PROFILES_DIR = Path(__file__).parent / "profiles"


def _impl_from_prompt(prompt_path: str) -> str:
    return Path(prompt_path).stem


def _build_variables(
    stage_name: str,
    signals: dict,
    branch: str,
    base_branch: str,
    feature_path: str,
    docs_root: str,
    project: str,
    run_folder: Path,
    project_config: dict,
) -> dict:
    """Collect variables from config and prior signal fields only — no file reads."""
    vars_dict = {
        "run_folder": str(run_folder),
        "review_md": str(run_folder / "review" / "review-log.md"),
        "docs_root": docs_root,
        "project": project,
        "branch": branch,
        "base_branch": base_branch,
        "feature_path": feature_path,
        "project_context_path": str(Path(docs_root) / "projects" / project / "context.md"),
    }
    if "repo-root" in project_config:
        vars_dict["repo_root"] = project_config["repo-root"]
    # Glossary variables are always present so prompts can rely on Jinja `{% if %}`
    # blocks without StrictUndefined errors. Empty strings mean the feature is
    # not configured for this project.
    canonical = glossary.resolve_canonical_path(project_config, project_config.get("repo-root"))
    if canonical is not None:
        vars_dict["canonical_glossary_path"] = str(canonical) if canonical.is_file() else ""
        vars_dict["run_glossary_path"] = str(run_folder / "specification" / "glossary.md")
    else:
        vars_dict["canonical_glossary_path"] = ""
        vars_dict["run_glossary_path"] = ""
    for sig in signals.values():
        if isinstance(sig, dict):
            for k, v in sig.items():
                if k not in vars_dict:
                    vars_dict[k] = v
    return vars_dict


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


def _create_branch(branch: str, repo_root: str, logger, stage_name: str) -> None:
    if git_state.branch_exists(repo_root, branch):
        if git_state.current_branch(repo_root) == branch:
            if not git_state.is_clean(repo_root):
                raise GitStateError(f"working tree not clean in {repo_root} — refuse to continue on '{branch}'")
            logger.log(stage_name, "INFO", f"already on branch '{branch}' — continuing")
            return
        if not git_state.is_clean(repo_root):
            raise GitStateError(f"working tree not clean in {repo_root} — refuse to switch to '{branch}'")
        result = subprocess.run(["git", "-C", repo_root, "checkout", branch], capture_output=True, text=True)
        if result.returncode != 0:
            raise GitStateError(f"git checkout {branch} failed: {result.stderr.strip()}")
        logger.log(stage_name, "INFO", f"checked out existing branch '{branch}'")
        return
    if not git_state.is_clean(repo_root):
        raise GitStateError(f"working tree not clean in {repo_root} — refuse to create branch '{branch}'")
    result = subprocess.run(
        ["git", "-C", repo_root, "checkout", "-b", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitStateError(f"git checkout -b {branch} failed: {result.stderr.strip()}")
    logger.log(stage_name, "INFO", f"Created branch {branch}")


def _sync_base_and_create_impl_branch(
    repo_root: str,
    base_branch: str,
    impl_branch: str,
    logger: OrchestratorLogger,
) -> None:
    """Fetch + checkout base + ff-pull, then create impl branch off it.

    If the impl branch already exists (resume case), only the base sync runs.
    Working-tree-clean check is enforced before any state mutation.
    """
    if not git_state.is_clean(repo_root):
        raise GitStateError(f"working tree not clean in {repo_root} — refuse to sync base branch '{base_branch}'")
    if git_state.branch_exists(repo_root, impl_branch):
        logger.log("pipeline", "INFO", f"impl branch '{impl_branch}' exists — skipping base sync")
        if git_state.current_branch(repo_root) != impl_branch:
            git_state.checkout(repo_root, impl_branch)
        return
    # Only fetch if origin exists — brand-new repos with no remote are valid.
    if git_state.get_remote_url(repo_root, "origin"):
        git_state.fetch(repo_root, "origin")
    git_state.checkout(repo_root, base_branch)
    if git_state.get_remote_url(repo_root, "origin"):
        git_state.pull_ff_only(repo_root, base_branch, "origin")
    result = subprocess.run(
        ["git", "-C", repo_root, "checkout", "-b", impl_branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitStateError(f"git checkout -b {impl_branch} failed: {result.stderr.strip()}")
    logger.log("pipeline", "INFO", f"Created branch '{impl_branch}' from '{base_branch}'")


def _build_stage_runners(profile: Profile) -> tuple[dict[str, AgentRunner], dict[str, dict[str, str | None]]]:
    """Resolve one runner per stage by merging profile-level + stage-level agent config.

    Deterministic stages are skipped — they execute Python in-process and never invoke
    the runner. Their metadata records `backend: "deterministic"` so the state.yaml is
    truthful about what executed each stage.
    """
    runners: dict[str, AgentRunner] = {}
    metadata: dict[str, dict[str, str | None]] = {}
    for stage in profile.stages:
        if stage.mode == "deterministic":
            metadata[stage.name] = {"backend": "deterministic", "model": None}
            continue
        config = resolve_agent_config(profile.agent, stage.agent)
        runners[stage.name] = build_runner(config)
        metadata[stage.name] = {"backend": config.backend, "model": config.model}
    return runners, metadata


def _resolve_run_folder(docs_root: str, project: str, feature_path: str, resume: bool) -> Path:
    date_str = datetime.date.today().isoformat()
    feature_slug = Path(feature_path).stem.lower().replace(" ", "-")
    runs_base = Path(docs_root) / "projects" / project / "workflow" / "runs" / feature_slug

    if resume and runs_base.exists():
        existing = sorted(d for d in runs_base.iterdir() if d.is_dir())
        if existing:
            return existing[-1]

    n = 1
    if runs_base.exists():
        for d in sorted(runs_base.iterdir()):
            if d.name.startswith(date_str):
                try:
                    n = max(n, int(d.name.split("-run-")[-1]) + 1)
                except (IndexError, ValueError):
                    pass

    return paths.resolve_run_folder(docs_root, project, feature_slug, date_str, n)


# ── stage dispatchers ──────────────────────────────────────────────────────────


def _dispatch_default(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
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


def _dispatch_slices(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
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
            update_plan_md(
                run_folder,
                sub_id,
                "passed",
                elapsed_secs=elapsed,
                output_summary=f"{len(commits)} commit{'s' if len(commits) != 1 else ''}" if commits else None,
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
                        update_plan_md(
                            run_folder,
                            sub_id,
                            "passed",
                            elapsed_secs=elapsed,
                            output_summary=f"{len(commits)} commit{'s' if len(commits) != 1 else ''}"
                            if commits
                            else None,
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


def _dispatch_prompts(
    stage: StageConfig,
    variables: dict,
    run_folder: Path,
    ctx: _PipelineContext,
    signals: dict | None = None,
) -> dict:
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
    """
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
        return {"stage": "fix-verification", "status": "blocked", "message": msg}

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


def run_pipeline(
    docs_root: str,
    project: str,
    feature_path: str,
    branch: str,
    profile_name: str,
    resume: bool = False,
    *,
    base_branch: str | None = None,
    create_pr: bool | None = None,
) -> None:
    project_config = _load_project_config(docs_root, project)
    if "repo-root" not in project_config:
        print("ERROR: project.yaml is missing required field 'repo-root'")  # noqa: T201
        sys.exit(1)
    try:
        preflight = _git_setup.preflight(
            docs_root=docs_root,
            project=project,
            repo_root=project_config["repo-root"],
            project_config=project_config,
            flag_base_branch=base_branch,
            flag_create_pr=create_pr,
        )
    except _git_setup.PreflightError as exc:
        sys.exit(f"[orchestrator] [ERROR] {exc}")
    # Re-read project config in case preflight persisted new defaults.
    project_config = _load_project_config(docs_root, project)

    project_context = Path(docs_root) / "projects" / project / "context.md"
    if not project_context.exists():
        project_context.touch()

    profile = load_profile(profile_name, _BUNDLED_PROFILES_DIR)

    project_log_path = str(Path(docs_root) / "projects" / project)
    log_level = project_config.get("log_level", "DEBUG")
    project_standards = project_config.get("standards", [])

    run_folder = Path(_resolve_run_folder(docs_root, project, feature_path, resume))
    run_folder.mkdir(parents=True, exist_ok=True)

    overview_path = Path(docs_root) / feature_path / "overview.md"
    if not overview_path.exists():
        sys.exit(
            f"[orchestrator] [ERROR] overview.md not found at {overview_path}\n"
            f"  --feature-path must be a docs-relative directory containing overview.md\n"
            f"  Example: projects/{project}/features/my-feature"
        )

    st = state_mod.load_state(run_folder)
    completed = {stage for stage, status in st.get("stages", {}).items() if status == "passed"}
    st.setdefault("project", project)
    st.setdefault("feature_path", feature_path)
    st.setdefault("branch", branch)
    st.setdefault("profile", profile_name)
    state_mod.save_state(run_folder, st)

    logger = OrchestratorLogger(run_folder, project_log_path, log_level)
    logger.log(
        "pipeline",
        "INFO",
        f"pipeline started: project={project}, feature_path={feature_path}, branch={branch}, profile={profile_name}",
    )
    signals = state_mod.load_signals(run_folder)

    pr_notice = None
    if preflight.create_pr:
        pr_notice = f"_will be created on completion (base: `{preflight.base_branch}`)_"
    runners, agent_metadata = _build_stage_runners(profile)
    # Resolved here (not in the post-loop section) so the initial diagram can
    # stamp the executive_summary node with the runner that will write it.
    finalisation_agent = resolve_agent_config(profile.agent, None)
    agent_metadata.setdefault(
        "executive_summary",
        {"backend": finalisation_agent.backend, "model": finalisation_agent.model},
    )
    init_plan_md(
        run_folder,
        profile,
        pr_notice=pr_notice,
        agent_metadata=agent_metadata,
        create_pr=bool(preflight.create_pr and preflight.origin.is_github and preflight.origin.gh_repo),
    )

    if not resume:
        try:
            _sync_base_and_create_impl_branch(
                project_config["repo-root"],
                preflight.base_branch,
                branch,
                logger,
            )
        except GitStateError as exc:
            logger.log("pipeline", "ERROR", f"base-branch sync failed: {exc}")
            sys.exit(f"[orchestrator] [ERROR] base-branch sync failed: {exc}")

    glossary_paths = glossary.setup_for_run(project_config, project_config.get("repo-root"), run_folder)
    if glossary_paths is not None:
        if glossary_paths.canonical_existed:
            logger.log(
                "pipeline",
                "INFO",
                f"domain-language glossary copied from {glossary_paths.canonical} to {glossary_paths.run_local}",
            )
        else:
            logger.log(
                "pipeline",
                "WARN",
                f"domain-language glossary configured but canonical file not found: {glossary_paths.canonical}",
            )

    ctx = _PipelineContext(
        docs_root=docs_root,
        project=project,
        project_log_path=project_log_path,
        logger=logger,
        branch=branch,
        project_config=project_config,
        project_standards=project_standards,
        runners=runners,
        agent_metadata=agent_metadata,
        resume=resume,
    )

    # pr_draft can carry its own profile-level override so a cheaper model
    # (e.g. Sonnet) can draft the PR while heavy stages stay on Opus. See ADR-029.
    pr_draft_agent_config = resolve_agent_config(profile.agent, profile.pr_draft_agent)
    pr_url: str | None = None

    # The stage loop and PR finalisation run inside try/finally so the executive
    # summary fires on every exit path — clean completion, blocked stage
    # (sys.exit(1)), or interactive incomplete (sys.exit(0)). See ADR-028.
    try:
        for stage in profile.stages:
            stage_name = stage.name

            if stage_name in completed:
                logger.log(stage_name, "DEBUG", "already passed — skipping")
                continue

            variables = _build_variables(
                stage_name,
                signals,
                branch,
                preflight.base_branch,
                feature_path,
                docs_root,
                project,
                run_folder,
                project_config,
            )

            if stage.mode == "interactive":
                t0 = time.monotonic()
                try:
                    sig = _dispatch_interactive(stage, variables, run_folder, ctx)
                except Exception:
                    logger.log(stage_name, "ERROR", f"unhandled exception in '{stage_name}':\n{traceback.format_exc()}")
                    raise
                elapsed = time.monotonic() - t0
                if sig.get("status") != "passed":
                    st = state_mod.load_state(run_folder)
                    st["blocked_at"] = stage_name
                    state_mod.save_state(run_folder, st)
                    update_plan_md(run_folder, stage_name, "blocked")
                    logger.log(
                        stage_name, "WARN", f"interactive stage '{stage_name}' incomplete: {sig.get('message', '')}"
                    )
                    artifact_path = run_folder / stage_name / (stage.artifact or "")
                    print(  # noqa: T201
                        f"\n[orchestrator] Stage '{stage_name}' incomplete.\n"
                        f"  Expected : {artifact_path}\n"
                        f"  Resume   : orchestrator resume --run-folder {run_folder} --docs-root {docs_root}\n"
                    )
                    sys.exit(0)
                signals[stage_name] = sig
                state_mod.update_stage_status(run_folder, stage_name, "passed")
                state_mod.save_stage_signal(run_folder, stage_name, sig)
                meta = ctx.agent_metadata.get(stage_name, {})
                state_mod.save_stage_agent(
                    run_folder, stage_name, meta.get("backend", "interactive"), meta.get("model")
                )
                update_plan_md(
                    run_folder, stage_name, "passed", elapsed_secs=elapsed, signal=sig, impl_name="Interactive"
                )
                continue

            update_plan_md(run_folder, stage_name, "in_progress")
            t0 = time.monotonic()
            try:
                if stage.mode == "deterministic":
                    sig = run_deterministic_stage(stage_name, variables["repo_root"], run_folder, ctx.project_log_path)
                    if sig.get("verification_status") == "failed":
                        sig = _run_fix_verification_cycle(sig, run_folder, variables, ctx)
                else:
                    sig = _DISPATCHERS[stage.expansion](stage, variables, run_folder, ctx, signals)
            except Exception:
                logger.log(stage_name, "ERROR", f"unhandled exception in '{stage_name}':\n{traceback.format_exc()}")
                raise
            elapsed = time.monotonic() - t0

            sig = _apply_alignment_policy(stage, sig, logger)

            signals[stage_name] = sig

            if sig.get("status") != "passed":
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, sig["status"])
                # The PR node is added at init time when create-pr is true; on stage
                # failure the finalisation phase never runs, so flip it to blocked
                # rather than leaving it pending forever. See ADR-026.
                mark_pr_blocked(run_folder)
                logger.log(
                    stage_name,
                    "ERROR",
                    f"pipeline stopped: stage {stage_name} {sig['status']}: {sig.get('message', '')}",
                )
                sys.exit(1)

            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, sig)
            meta = ctx.agent_metadata.get(stage_name, {})
            state_mod.save_stage_agent(run_folder, stage_name, meta.get("backend", "unknown"), meta.get("model"))
            impl_name = _impl_from_prompt(stage.prompt) if stage.prompt else None
            update_plan_md(
                run_folder,
                stage_name,
                "passed",
                elapsed_secs=elapsed,
                output_summary=_output_summary(stage, sig),
                signal=sig,
                impl_name=impl_name,
            )

            if stage.expansion == ExpansionKind.TRACKS:
                n_tracks = len(sig.get("tracks", []))
                n_findings = len(sig.get("findings_files", []))
                logger.log(
                    stage_name,
                    "INFO",
                    f"{stage_name} passed ({_fmt_elapsed(elapsed)}) — "
                    f"{n_tracks} track{'s' if n_tracks != 1 else ''}, "
                    f"{n_findings} findings file{'s' if n_findings != 1 else ''}",
                )
            elif stage.expansion == ExpansionKind.SLICES:
                n_commits = len(sig.get("commit_hashes", []))
                logger.log(
                    stage_name,
                    "INFO",
                    f"{stage_name} passed — {n_commits} commit{'s' if n_commits != 1 else ''} on {branch}",
                )

        logger.log("pipeline", "INFO", "pipeline completed successfully")

        if glossary_paths is not None and "harvest" in signals:
            _reconcile_glossary(run_folder, glossary_paths, signals["harvest"], logger)

        gh_repo = preflight.origin.gh_repo
        pr_will_run = bool(preflight.create_pr and preflight.origin.is_github and gh_repo)
        if not pr_will_run:
            mark_pipeline_done(run_folder)

        if pr_will_run and gh_repo:
            # pr_draft is not a profile stage, so _build_stage_runners does not produce a
            # runner for it. The finalisation runner is shared with the executive
            # summary step. See ADR-019.
            pr_url = _finalize_pr(
                run_folder=run_folder,
                docs_root=docs_root,
                project=project,
                project_log_path=project_log_path,
                feature_path=feature_path,
                repo_root=project_config["repo-root"],
                impl_branch=branch,
                base_branch=preflight.base_branch,
                gh_repo=gh_repo,
                logger=logger,
                agent_config=pr_draft_agent_config,
            )
    finally:
        # Always fires — pass, fail, or blocked. Failures here log a warning and
        # never change the pipeline exit status. See ADR-028.
        _finalize_summary(
            run_folder=run_folder,
            docs_root=docs_root,
            project=project,
            project_log_path=project_log_path,
            feature_path=feature_path,
            repo_root=project_config["repo-root"],
            impl_branch=branch,
            base_branch=preflight.base_branch,
            pr_url=pr_url,
            logger=logger,
            agent_config=finalisation_agent,
        )


def _finalize_pr(
    run_folder: Path,
    docs_root: str,
    project: str,
    project_log_path: str,
    feature_path: str,
    repo_root: str,
    impl_branch: str,
    base_branch: str,
    gh_repo: str,
    logger: OrchestratorLogger,
    agent_config: AgentConfig,
) -> str | None:
    """Push the implementation branch and open a draft PR.

    Returns the PR URL on success, or None if any step failed. Any failure here
    is logged as a warning and surfaced in plan.md, but does not change the
    pipeline exit status. See ADR-019.
    """
    plan_path = run_folder / "plan.md"
    fallback_cmd = f"gh pr create --draft --base {base_branch} --head {impl_branch} --repo {gh_repo}"
    t0 = time.monotonic()

    def _fail(reason: str) -> None:
        logger.log("pipeline", "WARN", f"PR creation skipped: {reason}")
        set_pr_notice(
            run_folder,
            f"_PR creation failed — run manually:_ `{fallback_cmd}`",
        )
        update_plan_md(run_folder, "pr", "blocked", elapsed_secs=time.monotonic() - t0)

    set_pr_notice(run_folder, "_drafting…_")
    update_plan_md(run_folder, "pr", "in_progress")

    overview_path = Path(docs_root) / feature_path / "overview.md"
    variables = {
        "run_folder": str(run_folder),
        "docs_root": docs_root,
        "project": project,
        "branch": impl_branch,
        "base_branch": base_branch,
        "feature_path": feature_path,
        "plan_md_path": str(plan_path),
        "overview_md_path": str(overview_path),
        "repo_root": repo_root,
    }
    try:
        sig = run_stage(
            "pr_draft",
            "default",
            variables,
            run_folder,
            docs_root,
            project,
            project_log_path,
            runner=build_runner(agent_config),
        )
    except Exception as exc:
        _fail(f"pr_draft stage error: {exc}")
        return None

    if sig.get("status") != "passed" or "title" not in sig or "body" not in sig:
        _fail(f"pr_draft stage did not produce title/body: {sig.get('message', sig.get('status'))}")
        return None

    title = str(sig["title"]).strip()
    body = str(sig["body"]).strip()

    # Record metadata for the post-pipeline stage so _state.yaml stays truthful.
    state_mod.save_stage_signal(run_folder, "pr_draft", sig)
    state_mod.save_stage_agent(run_folder, "pr_draft", agent_config.backend, agent_config.model)

    try:
        git_state.push_branch(repo_root, impl_branch, "origin", set_upstream=True)
    except GitStateError as exc:
        _fail(f"git push failed: {exc}")
        return None

    try:
        url = _github.create_draft_pr(gh_repo, base_branch, impl_branch, title, body)
    except _github.GhError as exc:
        _fail(f"gh pr create failed: {exc}")
        return None

    set_pr_notice(run_folder, url)
    set_pr_node(run_folder, url)
    update_plan_md(run_folder, "pr", "passed", elapsed_secs=time.monotonic() - t0)
    mark_pipeline_done(run_folder)
    logger.log("pipeline", "INFO", f"draft PR opened: {url}")
    return url


def _finalize_summary(
    run_folder: Path,
    docs_root: str,
    project: str,
    project_log_path: str,
    feature_path: str,
    repo_root: str,
    impl_branch: str,
    base_branch: str,
    pr_url: str | None,
    logger: OrchestratorLogger,
    agent_config: AgentConfig,
) -> None:
    """Dispatch a finalisation stage that writes ``executive_summary.md`` to the run folder.

    Runs via the profile's resolved agent (Claude by default, Codex for codex-only
    profiles) unconditionally as the last finalisation step — pass, fail, or
    blocked pipelines all receive a post-mortem summary. Any failure here logs a
    warning and does not change the pipeline exit status. See ADR-028.
    """
    plan_path = run_folder / "plan.md"
    state_path = run_folder / "_state.yaml"
    overview_path = Path(docs_root) / feature_path / "overview.md"
    summary_path = run_folder / "executive_summary.md"

    variables = {
        "run_folder": str(run_folder),
        "docs_root": docs_root,
        "project": project,
        "branch": impl_branch,
        "base_branch": base_branch,
        "feature_path": feature_path,
        "plan_md_path": str(plan_path),
        "overview_md_path": str(overview_path),
        "state_yaml_path": str(state_path),
        "summary_path": str(summary_path),
        "pr_url": pr_url or "not created",
        "repo_root": repo_root,
    }
    t0 = time.monotonic()
    try:
        sig = run_stage(
            "executive_summary",
            "default",
            variables,
            run_folder,
            docs_root,
            project,
            project_log_path,
            runner=build_runner(agent_config),
        )
    except Exception as exc:
        logger.log("pipeline", "WARN", f"executive summary skipped: {exc}")
        update_plan_md(run_folder, "executive_summary", "blocked", elapsed_secs=time.monotonic() - t0)
        return

    elapsed_secs = time.monotonic() - t0
    status = sig.get("status", "blocked")
    if status != "passed":
        logger.log(
            "pipeline",
            "WARN",
            f"executive summary not produced: {sig.get('message', status)}",
        )
        update_plan_md(run_folder, "executive_summary", status, elapsed_secs=elapsed_secs)
        return

    state_mod.save_stage_signal(run_folder, "executive_summary", sig)
    state_mod.save_stage_agent(run_folder, "executive_summary", agent_config.backend, agent_config.model)
    update_plan_md(run_folder, "executive_summary", "passed", elapsed_secs=elapsed_secs, signal=sig)
    logger.log("pipeline", "INFO", f"executive summary written: {summary_path}")


def _reconcile_glossary(
    run_folder: Path,
    glossary_paths: glossary.GlossaryPaths,
    harvest_signal: dict,
    logger: OrchestratorLogger,
) -> None:
    """Append harvest-proposed glossary terms to the canonical file.

    Append-only by design: existing definitions are never overwritten and
    conflicts surface as warnings + a report at `glossary-conflicts.md`.
    Failures here are logged and never change the pipeline exit status — a
    stale or partially-reconciled glossary is preferable to losing the
    pipeline's actual verdict over a docs hiccup.
    """
    proposed = harvest_signal.get("proposed_glossary_terms")
    if not isinstance(proposed, dict) or not proposed:
        logger.log("glossary", "DEBUG", "no proposed_glossary_terms in harvest signal — nothing to reconcile")
        return
    canonical = glossary_paths.canonical
    if canonical is None:
        return
    try:
        result = glossary.reconcile(canonical, {str(k): str(v) for k, v in proposed.items()})
    except OSError as exc:
        logger.log("glossary", "WARN", f"glossary reconciliation skipped: {exc}")
        return

    summary_parts = [f"{len(result.appended)} appended"]
    if result.unchanged:
        summary_parts.append(f"{len(result.unchanged)} unchanged")
    if result.skipped_empty:
        summary_parts.append(f"{len(result.skipped_empty)} skipped (empty)")
    if result.conflicts:
        summary_parts.append(f"{len(result.conflicts)} conflict{'s' if len(result.conflicts) != 1 else ''}")
    logger.log("glossary", "INFO", f"reconciliation: {', '.join(summary_parts)}")
    for conflict in result.conflicts:
        logger.log("glossary", "WARN", f"conflict on term '{conflict.name}' — canonical definition preserved")

    report = glossary.render_conflicts_report(result)
    report_path = run_folder / "glossary-reconciliation.md"
    try:
        report_path.write_text(report)
    except OSError as exc:
        logger.log("glossary", "WARN", f"glossary report write failed: {exc}")
