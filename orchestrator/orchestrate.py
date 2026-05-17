import datetime
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import yaml

import orchestrator.review_cycle as review_cycle_mod  # noqa: F401 — re-exported so tests can patch via ``orchestrator.orchestrate.review_cycle_mod``
from orchestrator import _git as git_state
from orchestrator import _git_setup, _github, glossary, paths
from orchestrator import state as state_mod
from orchestrator._git import GitStateError
from orchestrator.agent_runner import AgentConfig, AgentRunner, build_runner, resolve_agent_config
from orchestrator.logger import OrchestratorLogger
from orchestrator.plan import expand_nodes  # noqa: F401 — re-exported for stage_dispatchers/slice_dispatcher lookup
from orchestrator.plan_updates import (
    init_plan_md,
    mark_pipeline_done,
    mark_pr_blocked,
    resolve_review_subnode_statuses,  # noqa: F401 — re-exported for stage_dispatchers lookup
    set_pr_node,
    set_pr_notice,
    stamp_node_passed_with_commits,  # noqa: F401 — re-export for slice_dispatcher lookup
    update_plan_md,
)
from orchestrator.profile import ExpansionKind, Profile, StageConfig, load_profile
from orchestrator.run_stage import (
    _fmt_elapsed,
    run_deterministic_stage,
    run_interactive_stage,  # noqa: F401 — re-exported so tests can patch ``orchestrator.orchestrate.run_interactive_stage``
    run_stage,
)
from orchestrator.slice_dispatcher import (
    _SLICE_RE,  # noqa: F401 — re-exported for tests that import the constant
    _create_worktree,  # noqa: F401 — re-exported for direct-call tests
    _dispatch_slices,  # noqa: F401 — re-exported for direct-call tests
    _merge_worktree_branch,  # noqa: F401 — re-exported for direct-call tests
    _remove_worktree,  # noqa: F401 — re-exported for direct-call tests
    _run_slice,  # noqa: F401 — re-exported for tests that import the helper
)
from orchestrator.stage_dispatchers import (
    _DISPATCHERS,
    _apply_alignment_policy,
    _dispatch_default,  # noqa: F401 — re-exported for direct-call tests
    _dispatch_interactive,
    _dispatch_prompts,  # noqa: F401 — re-exported for direct-call tests
    _dispatch_tracks,  # noqa: F401 — re-exported for direct-call tests
    _run_fix_verification_cycle,
    _run_track,  # noqa: F401 — re-exported for direct-call tests
)
from orchestrator.wave_verification import (
    _maybe_capture_wave_baseline,  # noqa: F401 — re-exported for tests
    _maybe_run_wave_verification,  # noqa: F401 — re-exported for tests
)


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
        if vstatus != "passed":
            # Surface the verifier's own summary so a skipped or warned run reads as
            # "no toolchain detected — verification skipped" rather than the cryptic
            # "none: skipped". A passed verification stays compact ("node: passed").
            parts.append(signal.get("summary") or f"verification {vstatus}")
        else:
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


def _plan_status_from_signal(signal: dict) -> str:
    """Map a passed-pipeline signal to the plan node status used for rendering.

    Verification stages always return ``status="passed"`` because verification is
    not a hard gate (ADR-017) — the actual verdict lives in ``verification_status``.
    Rendering the node green for every passing pipeline turn would hide a run
    where no deterministic checks executed (``verification_status="skipped"``) or
    where non-required checks failed (``"warned"``). Mirror the wave-verification
    rule (ADR-031): a verification verdict that isn't ``passed`` renders as
    ``skipped`` so reviewers can see at a glance which runs had genuine
    deterministic coverage. See issue #172.
    """
    vstatus = signal.get("verification_status")
    if vstatus and vstatus != "passed":
        return "skipped"
    return "passed"


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


def _maybe_warn_unbootstrapped(
    docs_root: str,
    project: str,
    repo_root: str,
    resume: bool,
) -> None:
    """Warn (and optionally offer bootstrap) when the target repo has no toolchain markers.

    A "likely unbootstrapped" repo is one with no `.cco.yaml` and no recipe match.
    Deterministic verification would silently skip — that masquerades as success
    and is the failure mode the bootstrap command exists to prevent. See ADR-037.

    On resume we skip the check entirely; the warning is only useful at start.
    The pipeline never aborts on this check — a verifier-less repo is a valid
    state for prose-only work. We just refuse to be quiet about it.
    """
    if resume:
        return
    # Local import — `bootstrap` is otherwise unused by orchestrate.py and we want
    # to keep its import graph (verifiers.recipe) lazy from this entrypoint.
    from orchestrator import _prompts, bootstrap

    repo_root_path = Path(repo_root)
    if not bootstrap.looks_unbootstrapped(repo_root_path):
        return

    print(  # noqa: T201
        "[orchestrator] [WARN] This project does not appear to be bootstrapped for "
        "deterministic verification: no `.cco.yaml` and no recognised toolchain markers "
        f"in {repo_root}. Verification will be skipped and `plan.md` can read as passing "
        "even though no tests ran."
    )
    if not _prompts.is_interactive():
        print(  # noqa: T201
            "[orchestrator]   Run `orchestrator bootstrap --docs-root "
            f"{docs_root} --project {project} --toolchain <python|node|typescript|php|go|java>` "
            "before the next run to enable deterministic verification."
        )
        return

    try:
        if not _prompts.ask_confirm("Bootstrap this project now?", default=False):
            return
        toolchain = _prompts.ask_select("Toolchain", choices=list(bootstrap.SUPPORTED_TOOLCHAINS), default="python")
    except _prompts.PromptNotAvailable:
        return

    plan = bootstrap.plan_bootstrap(repo_root_path, toolchain)
    if plan.conflicts and not _prompts.ask_confirm(
        f"{len(plan.conflicts)} file(s) differ from template — overwrite?", default=False
    ):
        print("[orchestrator] Bootstrap aborted. Continuing without verification.")  # noqa: T201
        return
    written = bootstrap.apply_plan(plan, force=bool(plan.conflicts))
    project_yaml = Path(docs_root) / "projects" / project / "project.yaml"
    bootstrap.update_project_standards(project_yaml, toolchain)
    for path in written:
        print(f"[orchestrator]   wrote {path}")  # noqa: T201
    if not written:
        return
    # Bootstrap left the working tree dirty. The downstream base-branch sync
    # refuses to operate on a dirty tree, so we must either land the changes
    # in a commit here or stop the run cleanly with instructions. We never
    # silently fall through to the sync — that produced confusing failures.
    if _prompts.ask_confirm("Commit bootstrap changes?", default=True):
        try:
            sha = bootstrap.commit_changes(repo_root_path, written)
            print(f"[orchestrator] Committed {sha}: chore: bootstrap orchestrator project config")  # noqa: T201
            return
        except (subprocess.CalledProcessError, ValueError, OSError) as exc:
            print(f"[orchestrator] [WARN] commit failed: {exc}")  # noqa: T201
    sys.exit(
        "[orchestrator] Bootstrap files created but the working tree is now dirty.\n"
        "  Commit or stash them, then rerun the pipeline."
    )


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

    _maybe_warn_unbootstrapped(docs_root, project, project_config["repo-root"], resume)

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
    # When the profile does not declare executive_summary, this stays None and
    # the finalisation step is skipped entirely. See ADR-036.
    finalisation_agent: AgentConfig | None = None
    if profile.executive_summary is not None:
        finalisation_agent = resolve_agent_config(profile.agent, profile.executive_summary.agent)
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
                _plan_status_from_signal(sig),
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
        # Always fires when the profile declared executive_summary — pass, fail,
        # or blocked. Failures here log a warning and never change the pipeline
        # exit status. Profiles that omit executive_summary skip this entirely.
        # See ADR-028 and ADR-036.
        if finalisation_agent is not None:
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
