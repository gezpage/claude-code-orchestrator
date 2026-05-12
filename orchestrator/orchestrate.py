import concurrent.futures
import datetime
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from orchestrator import paths, state as state_mod
from orchestrator.logger import OrchestratorLogger
from orchestrator.plan import expand_nodes, init_plan_md, update_plan_md
from orchestrator.profile import ExpansionKind, StageConfig, load_profile
from orchestrator.run_stage import _fmt_elapsed, run_interactive_stage, run_stage
import orchestrator.review_cycle as review_cycle_mod

_SLICE_RE = re.compile(r'S-\d+-')


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
    if signal.get("prd_path"):
        parts.append("PRD")
    if signal.get("context_path"):
        parts.append("context")
    for key, label in [("adr_paths", "ADR"), ("slice_files", "implementation slice"),
                        ("kb_files", "KB file"), ("adr_files", "ADR")]:
        val = signal.get(key)
        if isinstance(val, list) and val:
            n = len(val)
            parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    return ", ".join(parts) if parts else None


def _load_project_config(docs_root: str, project: str) -> dict:
    config_path = paths.require_file(
        Path(docs_root) / "projects" / project / "project.yaml"
    )
    return yaml.safe_load(config_path.read_text())


_BUNDLED_PROFILES_DIR = Path(__file__).parent / "profiles"


def _impl_from_prompt(prompt_path: str) -> str:
    return Path(prompt_path).stem


def _build_variables(
    stage_name: str, signals: dict, branch: str, feature_path: str,
    docs_root: str, project: str, run_folder: Path, project_config: dict,
) -> dict:
    """Collect variables from config and prior signal fields only — no file reads."""
    vars_dict = {
        "run_folder": str(run_folder),
        "review_md": str(run_folder / "review" / "review-log.md"),
        "docs_root": docs_root,
        "project": project,
        "branch": branch,
        "feature_path": feature_path,
        "project_context_path": str(Path(docs_root) / "projects" / project / "context.md"),
    }
    if "repo-root" in project_config:
        vars_dict["repo_root"] = project_config["repo-root"]
    for sig in signals.values():
        if isinstance(sig, dict):
            for k, v in sig.items():
                if k not in vars_dict:
                    vars_dict[k] = v
    return vars_dict


def _create_worktree(repo_root: str, temp_branch: str, base_branch: str, logger, stage_name: str) -> str:
    import tempfile
    wt_path = tempfile.mkdtemp(prefix=f"orch-wt-{temp_branch}-")
    result = subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", wt_path, "-b", temp_branch, base_branch],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr}")
    logger.log(stage_name, "INFO", f"Created worktree {wt_path} on branch {temp_branch}")
    return wt_path


def _remove_worktree(repo_root: str, wt_path: str, temp_branch: str, logger, stage_name: str) -> None:
    subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", wt_path],
                   capture_output=True)
    subprocess.run(["git", "-C", repo_root, "branch", "-D", temp_branch],
                   capture_output=True)
    logger.log(stage_name, "INFO", f"Removed worktree {wt_path}")


def _merge_worktree_branch(repo_root: str, temp_branch: str, logger, stage_name: str) -> None:
    result = subprocess.run(
        ["git", "-C", repo_root, "merge", temp_branch, "--no-ff",
         "-m", f"merge parallel slice branch {temp_branch}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git merge {temp_branch} failed: {result.stderr}")
    logger.log(stage_name, "INFO", f"Merged {temp_branch} into HEAD")


def _create_branch(branch: str, repo_root: str, logger, stage_name: str) -> None:
    result = subprocess.run(
        ["git", "-C", repo_root, "checkout", "-b", branch],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        if "already exists" in result.stderr:
            logger.log(stage_name, "WARN", f"branch '{branch}' already exists — continuing on existing branch")
            return
        logger.log(stage_name, "ERROR", f"git checkout -b {branch} failed: {result.stderr}")
        raise RuntimeError(f"Failed to create branch {branch}: {result.stderr}")
    logger.log(stage_name, "INFO", f"Created branch {branch}")


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


def run_pipeline(
    docs_root: str,
    project: str,
    feature_path: str,
    branch: str,
    profile_name: str,
    resume: bool = False,
) -> None:
    project_config = _load_project_config(docs_root, project)
    if "repo-root" not in project_config:
        print("ERROR: project.yaml is missing required field 'repo-root'")
        sys.exit(1)
    if not Path(project_config["repo-root"]).exists():
        print(f"ERROR: project.yaml repo-root does not exist: {project_config['repo-root']}")
        sys.exit(1)
    result = subprocess.run(
        ["git", "-C", project_config["repo-root"], "rev-parse", "--git-dir"],
        capture_output=True,
    )
    if result.returncode != 0:
        sys.exit(
            f"[orchestrator] [ERROR] repo-root is not a git repository: {project_config['repo-root']}\n"
            f"  Ensure the path in project.yaml 'repo-root' points to the root of a git repository."
        )

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
    completed = {
        stage for stage, status in st.get("stages", {}).items() if status == "passed"
    }
    st.setdefault("project", project)
    st.setdefault("feature_path", feature_path)
    st.setdefault("branch", branch)
    st.setdefault("profile", profile_name)
    state_mod.save_state(run_folder, st)

    logger = OrchestratorLogger(run_folder, project_log_path, log_level)
    logger.log("pipeline", "INFO", f"pipeline started: project={project}, feature_path={feature_path}, branch={branch}, profile={profile_name}")
    signals = state_mod.load_signals(run_folder)

    init_plan_md(run_folder, profile)

    for stage in profile.stages:
        stage_name = stage.name

        if stage_name in completed:
            logger.log(stage_name, "DEBUG", "already passed — skipping")
            continue

        if stage.mode == "interactive":
            if not stage.artifact:
                print(f"ERROR: interactive stage '{stage_name}' missing required 'artifact' field in profile")
                sys.exit(1)
            artifact_path = run_folder / stage_name / stage.artifact
            variables = _build_variables(
                stage_name, signals, branch, feature_path,
                docs_root, project, run_folder, project_config,
            )
            if artifact_path.exists():
                artifact_key = Path(stage.artifact).stem.replace("-", "_")
                sig = {artifact_key: str(artifact_path)}
                signals[stage_name] = sig
                state_mod.update_stage_status(run_folder, stage_name, "passed")
                state_mod.save_stage_signal(run_folder, stage_name, sig)
                update_plan_md(run_folder, stage_name, "passed", signal=sig, impl_name="Interactive")
                logger.log(stage_name, "INFO", "artifact exists — skipping interactive session")
                continue
            update_plan_md(run_folder, stage_name, "in_progress")
            t0 = time.monotonic()
            sig = run_interactive_stage(
                stage_name, stage.prompt, variables,
                run_folder, artifact_path, docs_root, project, project_log_path,
            )
            elapsed = time.monotonic() - t0
            if sig.get("status") != "passed":
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, "blocked")
                logger.log(stage_name, "WARN", f"interactive stage '{stage_name}' incomplete: {stage.artifact} not created")
                print(
                    f"\n[orchestrator] Stage '{stage_name}' incomplete.\n"
                    f"  Expected : {artifact_path}\n"
                    f"  Resume   : orchestrator resume --run-folder {run_folder} --docs-root {docs_root}\n"
                )
                sys.exit(0)
            signals[stage_name] = sig
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, sig)
            update_plan_md(run_folder, stage_name, "passed", elapsed_secs=elapsed, signal=sig, impl_name="Interactive")
            continue

        variables = _build_variables(
            stage_name, signals, branch, feature_path,
            docs_root, project, run_folder, project_config,
        )

        if stage.expansion == ExpansionKind.TRACKS:
            update_plan_md(run_folder, stage_name, "in_progress")
            t0 = time.monotonic()

            planning_sig = run_stage(
                stage_name, "planning", variables, run_folder, docs_root, project, project_log_path,
                output_suffix="planning", schema_name="discovery_planning",
            )
            if planning_sig.get("status") != "passed":
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, planning_sig["status"])
                logger.log(stage_name, "ERROR", f"pipeline stopped: {stage_name} planning {planning_sig['status']}: {planning_sig.get('message', '')}")
                sys.exit(1)

            tracks = planning_sig.get("tracks", [])
            if not tracks:
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, "blocked")
                logger.log(stage_name, "ERROR", f"pipeline stopped: {stage_name} planning produced no tracks — verify --feature-path is a directory containing overview.md")
                sys.exit(1)

            logger.log(stage_name, "INFO", f"planning complete: {len(tracks)} track{'s' if len(tracks) != 1 else ''}")
            planning_elapsed = time.monotonic() - t0
            track_node_ids = expand_nodes(run_folder, stage, tracks=tracks, planning_elapsed_secs=planning_elapsed)

            if len(tracks) == 1:
                track = tracks[0]
                tid = track_node_ids.get(track["name"])
                if tid:
                    update_plan_md(run_folder, tid, "in_progress")
                t_start = time.monotonic()
                sig = run_stage(
                    stage_name, "pregenerated", dict(variables),
                    run_folder, docs_root, project, project_log_path,
                    output_suffix=track["name"],
                    prompt_file=track["prompt_file"],
                    schema_name="discovery_track",
                )
                track_elapsed = time.monotonic() - t_start
                if tid:
                    t_status = "passed" if sig.get("status") == "passed" else "blocked"
                    update_plan_md(run_folder, tid, t_status, elapsed_secs=track_elapsed, output_summary=sig.get("summary", ""))
                track_results = {track["name"]: sig}
            else:
                logger.log(stage_name, "INFO", f"dispatching {len(tracks)} tracks in parallel")

                def _run_track_with_updates(track, variables_copy):
                    tid = track_node_ids.get(track["name"])
                    if tid:
                        update_plan_md(run_folder, tid, "in_progress")
                    t_start = time.monotonic()
                    sig = run_stage(
                        stage_name, "pregenerated", variables_copy,
                        run_folder, docs_root, project, project_log_path,
                        track["name"],        # output_suffix
                        None,                 # cwd
                        track["prompt_file"], # prompt_file
                        "discovery_track",    # schema_name
                    )
                    track_elapsed = time.monotonic() - t_start
                    if tid:
                        t_status = "passed" if sig.get("status") == "passed" else "blocked"
                        update_plan_md(run_folder, tid, t_status, elapsed_secs=track_elapsed, output_summary=sig.get("summary", ""))
                    return sig

                futures: dict[concurrent.futures.Future, str] = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(tracks)) as executor:
                    for track in tracks:
                        fut = executor.submit(_run_track_with_updates, track, dict(variables))
                        futures[fut] = track["name"]
                track_results = {name: fut.result() for fut, name in futures.items()}

            failed_tracks = [n for n, s in track_results.items() if s.get("status") != "passed"]
            if failed_tracks:
                for name in failed_tracks:
                    s = track_results[name]
                    logger.log(stage_name, "ERROR", f"{stage_name} track '{name}' {s.get('status')}: {s.get('message', '')}")
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, "blocked")
                sys.exit(1)

            elapsed = time.monotonic() - t0
            aggregated_tracks = []
            findings_files = []
            for track in tracks:
                track_sig = track_results[track["name"]]
                ff = track_sig.get("findings_file", "")
                aggregated_tracks.append({
                    "name": track["name"],
                    "summary": track_sig.get("summary", ""),
                    "findings_file": ff,
                })
                if ff:
                    findings_files.append(ff)

            aggregated_sig = {
                "stage": stage_name,
                "status": "passed",
                "tracks": aggregated_tracks,
                "findings_files": findings_files,
            }
            signals[stage_name] = aggregated_sig
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, aggregated_sig)
            update_plan_md(run_folder, stage_name, "passed", elapsed_secs=elapsed, output_summary=_output_summary(stage, aggregated_sig), signal=aggregated_sig)
            n_tracks = len(tracks)
            n_findings = len(findings_files)
            logger.log(stage_name, "INFO",
                f"{stage_name} passed ({_fmt_elapsed(elapsed)}) — {n_tracks} track{'s' if n_tracks != 1 else ''}, {n_findings} findings file{'s' if n_findings != 1 else ''}")
            continue

        elif stage.expansion == ExpansionKind.SLICES:
            stage_standards = project_standards if stage.standards else None
            _create_branch(branch, variables["repo_root"], logger, stage_name)
            prior_sig = signals.get(stage.slices_from_stage, {}) if stage.slices_from_stage else {}
            slice_files = prior_sig.get("slice_files", [])
            slice_groups = prior_sig.get("slice_groups", [])
            pre_count = len(slice_files)
            slice_files = [sf for sf in slice_files if _SLICE_RE.search(Path(sf).name)]
            if len(slice_files) != pre_count:
                logger.log(stage_name, "WARN",
                           f"filtered {pre_count - len(slice_files)} non-slice file(s) from slice_files")
            if slice_groups:
                slice_groups = [
                    [sf for sf in g if _SLICE_RE.search(Path(sf).name)]
                    for g in slice_groups
                ]
                slice_groups = [g for g in slice_groups if g]
            if not slice_groups:
                slice_groups = [[sf] for sf in slice_files]
            all_slices = [sf for group in slice_groups for sf in group]
            slice_to_id = {sf: f"impl_{i+1}" for i, sf in enumerate(all_slices)}
            expand_nodes(run_folder, stage, slice_files=all_slices, slice_groups=slice_groups)
            impl = _impl_from_prompt(stage.prompt or f"prompts/{stage_name}/default.md")
            all_commits = []
            for group in slice_groups:
                if len(group) == 1:
                    slice_file = group[0]
                    sub_id = slice_to_id[slice_file]
                    variables["slice_file"] = slice_file
                    update_plan_md(run_folder, sub_id, "in_progress")
                    t0 = time.monotonic()
                    sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path, output_suffix=sub_id, cwd=variables.get("repo_root"), standards=stage_standards)
                    elapsed = time.monotonic() - t0
                    if sig.get("status") != "passed":
                        st = state_mod.load_state(run_folder)
                        st["blocked_at"] = stage_name
                        state_mod.save_state(run_folder, st)
                        update_plan_md(run_folder, sub_id, sig["status"])
                        logger.log(stage_name, "ERROR", f"pipeline stopped: stage {stage_name} {sig['status']} on slice {slice_file}: {sig.get('message', '')}")
                        sys.exit(1)
                    commits = sig.get("commit_hashes", [])
                    all_commits.extend(commits)
                    update_plan_md(run_folder, sub_id, "passed", elapsed_secs=elapsed,
                                   output_summary=f"{len(commits)} commit{'s' if len(commits) != 1 else ''}" if commits else None,
                                   signal=sig, impl_name=impl, repo_root=variables.get("repo_root"))
                else:
                    repo_root = variables["repo_root"]
                    logger.log(stage_name, "INFO", f"dispatching {len(group)} implementation slices in parallel")
                    worktrees: dict[str, tuple[str, str]] = {}
                    try:
                        for sf in group:
                            sub_id = slice_to_id[sf]
                            temp_branch = f"{branch}-{sub_id}"
                            wt_path = _create_worktree(repo_root, temp_branch, branch, logger, stage_name)
                            worktrees[sub_id] = (wt_path, temp_branch)
                            update_plan_md(run_folder, sub_id, "in_progress")

                        def _run_timed(sn, im, vc, rf, dr, pr, lp, sid, wt, std):
                            t = time.monotonic()
                            sig = run_stage(sn, im, vc, rf, dr, pr, lp, sid, cwd=wt, standards=std)
                            return sig, time.monotonic() - t

                        futures2: dict[concurrent.futures.Future, tuple[str, str]] = {}
                        with concurrent.futures.ThreadPoolExecutor(max_workers=len(group)) as executor:
                            for slice_file in group:
                                sub_id = slice_to_id[slice_file]
                                wt_path, _ = worktrees[sub_id]
                                vars_copy = dict(variables)
                                vars_copy["slice_file"] = slice_file
                                fut = executor.submit(
                                    _run_timed, stage_name, impl, vars_copy,
                                    run_folder, docs_root, project, project_log_path,
                                    sub_id, wt_path, stage_standards,
                                )
                                futures2[fut] = (sub_id, slice_file)
                        failed = False
                        for fut, (sub_id, slice_file) in futures2.items():
                            sig, elapsed = fut.result()
                            if sig.get("status") != "passed":
                                st = state_mod.load_state(run_folder)
                                st["blocked_at"] = stage_name
                                state_mod.save_state(run_folder, st)
                                update_plan_md(run_folder, sub_id, sig["status"])
                                logger.log(stage_name, "ERROR", f"pipeline stopped: stage {stage_name} {sig['status']} on slice {slice_file}: {sig.get('message', '')}")
                                failed = True
                            else:
                                _, temp_branch = worktrees[sub_id]
                                _merge_worktree_branch(repo_root, temp_branch, logger, stage_name)
                                commits = sig.get("commit_hashes", [])
                                all_commits.extend(commits)
                                update_plan_md(run_folder, sub_id, "passed", elapsed_secs=elapsed,
                                               output_summary=f"{len(commits)} commit{'s' if len(commits) != 1 else ''}" if commits else None,
                                               signal=sig, impl_name=impl, repo_root=variables.get("repo_root"))
                        if failed:
                            sys.exit(1)
                    finally:
                        for sub_id, (wt_path, temp_branch) in worktrees.items():
                            _remove_worktree(repo_root, wt_path, temp_branch, logger, stage_name)
            final_sig = {"status": "passed", "commit_hashes": all_commits, "branch": branch}
            signals[stage_name] = final_sig
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, final_sig)
            n_commits = len(all_commits)
            logger.log(stage_name, "INFO",
                f"{stage_name} passed — {n_commits} commit{'s' if n_commits != 1 else ''} on {branch}")
            continue

        elif stage.expansion == ExpansionKind.PROMPTS:
            review_md_path = run_folder / stage_name / "review-log.md"
            review_md_path.parent.mkdir(parents=True, exist_ok=True)
            variables["review_md"] = str(review_md_path)
            variables["round"] = "1"
            commit_hashes = next(
                (sig.get("commit_hashes", []) for sig in signals.values()
                 if isinstance(sig, dict) and sig.get("commit_hashes")),
                [],
            )
            if commit_hashes and "repo_root" in variables:
                first, last = commit_hashes[0], commit_hashes[-1]
                diff_result = subprocess.run(
                    ["git", "-C", variables["repo_root"], "diff", f"{first}^..{last}"],
                    capture_output=True, text=True,
                )
                diff_path = run_folder / stage_name / "diff-round-1.patch"
                diff_path.write_text(diff_result.stdout)
                variables["diff"] = str(diff_path)
            else:
                variables["diff"] = ""
            reviewer_statuses = {}
            changes_requested = []
            for reviewer, prompt_path in stage.prompts.items():
                sub_id = f"{stage_name}_{reviewer}"
                update_plan_md(run_folder, sub_id, "in_progress")
                impl = _impl_from_prompt(prompt_path)
                t0 = time.monotonic()
                sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path, output_suffix=reviewer)
                elapsed = time.monotonic() - t0
                verdict = sig.get("reviewer_statuses", {}).get(reviewer, sig.get("status", "unknown"))
                reviewer_statuses[reviewer] = verdict
                if verdict == "changes-requested":
                    changes_requested.append(reviewer)
                sub_status = "blocked" if verdict == "changes-requested" else "passed"
                update_plan_md(run_folder, sub_id, sub_status, elapsed_secs=elapsed, output_summary=verdict, impl_name=impl)
            review_signal = {
                "status": "passed",
                "reviewer_statuses": reviewer_statuses,
                "changes_requested": changes_requested,
                "review_md": str(review_md_path),
            }
            signals[stage_name] = review_signal
            if changes_requested:
                logger.log(stage_name, "WARN", f"changes requested by: {', '.join(changes_requested)}")
                result = review_cycle_mod.run(
                    run_folder, docs_root, project, branch, review_signal, project_log_path,
                    repo_root=variables.get("repo_root", ""),
                )
                if not result.get("all_passed"):
                    st = state_mod.load_state(run_folder)
                    st["blocked_at"] = stage_name
                    state_mod.save_state(run_folder, st)
                    update_plan_md(run_folder, stage_name, "blocked")
                    logger.log(stage_name, "ERROR", f"pipeline stopped: review cycle blocked, reviewers={result.get('reviewers', [])}")
                    sys.exit(1)
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, review_signal)
            update_plan_md(run_folder, stage_name, "passed", output_summary=_output_summary(stage, review_signal), signal=review_signal)
            continue

        else:
            update_plan_md(run_folder, stage_name, "in_progress")
            impl = _impl_from_prompt(stage.prompt or f"prompts/{stage_name}/default.md")
            t0 = time.monotonic()
            stage_cwd = variables.get("repo_root") if stage.cwd_from_repo_root else None
            stage_standards = project_standards if stage.standards else None
            sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path, cwd=stage_cwd, standards=stage_standards)
            elapsed = time.monotonic() - t0
            signals[stage_name] = sig

            if sig.get("status") in ("blocked", "failed"):
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, sig["status"])
                logger.log(stage_name, "ERROR", f"pipeline stopped: stage {stage_name} {sig['status']}: {sig.get('message', '')}")
                sys.exit(1)

            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, sig)
            update_plan_md(run_folder, stage_name, "passed", elapsed_secs=elapsed, output_summary=_output_summary(stage, sig), signal=sig, impl_name=impl)

    logger.log("pipeline", "INFO", "pipeline completed successfully")
