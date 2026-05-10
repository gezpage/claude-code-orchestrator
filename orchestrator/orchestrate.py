import concurrent.futures
import datetime
import subprocess
import sys
import time
from pathlib import Path

import yaml

from orchestrator import paths, state as state_mod
from orchestrator.logger import OrchestratorLogger
from orchestrator.plan import init_plan_md, expand_impl_nodes, update_plan_md
from orchestrator.run_stage import run_stage, run_interactive_stage
import orchestrator.review_cycle as review_cycle_mod


def _output_summary(stage, signal):
    if stage == "discovery":
        tracks = signal.get("tracks", [])
        n = len(signal.get("findings_files", []))
        if tracks:
            t = len(tracks)
            return f"{t} track{'s' if t != 1 else ''}, {n} finding{'s' if n != 1 else ''}"
        return f"{n} research file{'s' if n != 1 else ''}" if n else None
    if stage == "specification":
        parts = []
        if signal.get("prd_path"):
            parts.append("PRD")
        if signal.get("context_path"):
            parts.append("context")
        n = len(signal.get("adr_paths", []))
        if n:
            parts.append(f"{n} ADR{'s' if n != 1 else ''}")
        return ", ".join(parts) if parts else None
    if stage == "decomposition":
        n = len(signal.get("slice_files", []))
        return f"{n} slice{'s' if n != 1 else ''}" if n else None
    if stage == "implementation":
        n = len(signal.get("commit_hashes", []))
        return f"{n} commit{'s' if n != 1 else ''}" if n else None
    if stage == "qa":
        outcome = signal.get("outcome")
        return outcome if outcome else None
    if stage == "review":
        statuses = signal.get("reviewer_statuses", {})
        if statuses:
            return ", ".join(f"{r}: {v}" for r, v in statuses.items())
        return None
    if stage == "harvest":
        kb = len(signal.get("kb_files", []))
        adr = len(signal.get("adr_files", []))
        parts = []
        if kb:
            parts.append(f"{kb} KB file{'s' if kb != 1 else ''}")
        if adr:
            parts.append(f"{adr} ADR{'s' if adr != 1 else ''}")
        return ", ".join(parts) if parts else None
    return None



def _load_project_config(docs_root, project):
    config_path = paths.require_file(
        Path(docs_root) / "projects" / project / "project.yaml"
    )
    return yaml.safe_load(config_path.read_text())


def _load_profile(docs_root, project, profile_name):
    profiles_dir = paths.resolve_profiles_dir(docs_root, project)
    profile_path = paths.require_file(profiles_dir / f"{profile_name}.yaml")
    return yaml.safe_load(profile_path.read_text())


def _impl_from_prompt(prompt_path):
    return Path(prompt_path).stem


def _build_variables(stage, signals, branch, feature_path, docs_root, project, run_folder, project_config):
    """Collect variables from config and prior signal fields only — no file reads."""
    vars_dict = {
        "run_folder": str(run_folder),
        "review_md": str(run_folder / "review.md"),
        "docs_root": docs_root,
        "project": project,
        "branch": branch,
        "feature_path": feature_path,
    }
    if "repo-root" in project_config:
        vars_dict["repo_root"] = project_config["repo-root"]
    for sig in signals.values():
        if isinstance(sig, dict):
            for k, v in sig.items():
                if k not in vars_dict:
                    vars_dict[k] = v
    return vars_dict


def _create_branch(branch, repo_root, logger):
    result = subprocess.run(
        ["git", "-C", repo_root, "checkout", "-b", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "already exists" in result.stderr:
            logger.log("implementation", "WARN", f"branch '{branch}' already exists — continuing on existing branch")
            return
        logger.log("implementation", "ERROR", f"git checkout -b {branch} failed: {result.stderr}")
        raise RuntimeError(f"Failed to create branch {branch}: {result.stderr}")
    logger.log("implementation", "INFO", f"Created branch {branch}")


def _resolve_run_folder(docs_root, project, feature_path, resume):
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


def run_pipeline(docs_root, project, feature_path, branch, profile_name, resume=False):
    project_config = _load_project_config(docs_root, project)
    if "repo-root" not in project_config:
        print("ERROR: project.yaml is missing required field 'repo-root'")
        sys.exit(1)
    if not Path(project_config["repo-root"]).exists():
        print(f"ERROR: project.yaml repo-root does not exist: {project_config['repo-root']}")
        sys.exit(1)
    profile = _load_profile(docs_root, project, profile_name)

    project_log_path = str(Path(docs_root) / "projects" / project)
    log_level = project_config.get("log_level", "DEBUG")

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

    for stage_def in profile.get("stages", []):
        stage_name = stage_def["stage"]

        if stage_name in completed:
            logger.log(stage_name, "INFO", "already passed — skipping")
            continue

        if stage_def.get("mode") == "interactive":
            logger.log(stage_name, "INFO", "stage starting (interactive)")
            artifact_name = stage_def.get("artifact")
            if not artifact_name:
                print(f"ERROR: interactive stage '{stage_name}' missing required 'artifact' field in profile")
                sys.exit(1)
            artifact_path = run_folder / artifact_name
            variables = _build_variables(
                stage_name, signals, branch, feature_path,
                docs_root, project, run_folder, project_config,
            )
            if artifact_path.exists():
                artifact_key = Path(artifact_name).stem.replace("-", "_")
                sig = {artifact_key: str(artifact_path)}
                signals[stage_name] = sig
                state_mod.update_stage_status(run_folder, stage_name, "passed")
                state_mod.save_stage_signal(run_folder, stage_name, sig)
                update_plan_md(run_folder, stage_name, "passed")
                logger.log(stage_name, "INFO", "artifact exists — skipping interactive session")
                continue
            update_plan_md(run_folder, stage_name, "in_progress")
            t0 = time.monotonic()
            sig = run_interactive_stage(
                stage_name, stage_def.get("prompt"), variables,
                run_folder, artifact_path, docs_root, project, project_log_path,
            )
            elapsed = time.monotonic() - t0
            if sig.get("status") != "passed":
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, "blocked")
                logger.log(stage_name, "WARN", f"interactive stage '{stage_name}' incomplete: {artifact_name} not created")
                print(
                    f"\n[orchestrator] Stage '{stage_name}' incomplete.\n"
                    f"  Expected : {artifact_path}\n"
                    f"  Resume   : orchestrator resume --run-folder {run_folder} --docs-root {docs_root}\n"
                )
                sys.exit(0)
            signals[stage_name] = sig
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, sig)
            update_plan_md(run_folder, stage_name, "passed", elapsed_secs=elapsed)
            continue

        variables = _build_variables(
            stage_name, signals, branch, feature_path,
            docs_root, project, run_folder, project_config,
        )

        if stage_name == "discovery":
            logger.log(stage_name, "INFO", "stage starting")
            update_plan_md(run_folder, stage_name, "in_progress")
            impl = _impl_from_prompt(stage_def.get("prompt", "prompts/discovery/planning.md"))
            t0 = time.monotonic()

            # Phase 1: planning agent decides tracks and writes track prompt files
            planning_sig = run_stage(
                "discovery", impl, variables, run_folder, docs_root, project, project_log_path,
                output_suffix="planning", schema_name="discovery_planning",
            )
            if planning_sig.get("status") != "passed":
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, planning_sig["status"])
                logger.log(stage_name, "ERROR", f"pipeline stopped: discovery planning {planning_sig['status']}: {planning_sig.get('message', '')}")
                sys.exit(1)

            tracks = planning_sig.get("tracks", [])
            if not tracks:
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, "blocked")
                logger.log(stage_name, "ERROR", "pipeline stopped: discovery planning produced no tracks — verify --feature-path is a directory containing overview.md")
                sys.exit(1)

            logger.log(stage_name, "INFO", f"planning complete: {len(tracks)} track{'s' if len(tracks) != 1 else ''}")

            # Phase 2: run all tracks in parallel
            if len(tracks) == 1:
                track = tracks[0]
                sig = run_stage(
                    "discovery", "pregenerated", dict(variables),
                    run_folder, docs_root, project, project_log_path,
                    output_suffix=track["name"],
                    prompt_file=track["prompt_file"],
                    schema_name="discovery_track",
                )
                track_results = {track["name"]: sig}
            else:
                logger.log(stage_name, "INFO", f"dispatching {len(tracks)} tracks in parallel")
                futures: dict[concurrent.futures.Future, str] = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(tracks)) as executor:
                    for track in tracks:
                        fut = executor.submit(
                            run_stage,
                            "discovery", "pregenerated", dict(variables),
                            run_folder, docs_root, project, project_log_path,
                            track["name"],        # output_suffix
                            None,                 # cwd
                            track["prompt_file"], # prompt_file
                            "discovery_track",    # schema_name
                        )
                        futures[fut] = track["name"]
                track_results = {name: fut.result() for fut, name in futures.items()}

            failed_tracks = [n for n, s in track_results.items() if s.get("status") != "passed"]
            if failed_tracks:
                for name in failed_tracks:
                    s = track_results[name]
                    logger.log(stage_name, "ERROR", f"discovery track '{name}' {s.get('status')}: {s.get('message', '')}")
                st = state_mod.load_state(run_folder)
                st["blocked_at"] = stage_name
                state_mod.save_state(run_folder, st)
                update_plan_md(run_folder, stage_name, "blocked")
                sys.exit(1)

            # Phase 3: aggregate
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

            discovery_sig = {
                "stage": "discovery",
                "status": "passed",
                "tracks": aggregated_tracks,
                "findings_files": findings_files,
            }
            signals[stage_name] = discovery_sig
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, discovery_sig)
            update_plan_md(run_folder, stage_name, "passed", elapsed_secs=elapsed, output_summary=_output_summary(stage_name, discovery_sig))
            continue

        if stage_name == "implementation":
            logger.log(stage_name, "INFO", "stage starting")
            _create_branch(branch, variables["repo_root"], logger)
            slice_files = []
            slice_groups = []
            for sig in signals.values():
                if isinstance(sig, dict) and "slice_files" in sig:
                    slice_files = sig["slice_files"]
                    slice_groups = sig.get("slice_groups", [])
                    break
            if not slice_groups:
                slice_groups = [[sf] for sf in slice_files]
            # Flatten groups to get canonical ordered list (for sub_id assignment)
            all_slices = [sf for group in slice_groups for sf in group]
            slice_to_id = {sf: f"impl_{i+1}" for i, sf in enumerate(all_slices)}
            expand_impl_nodes(run_folder, all_slices, slice_groups)
            impl = _impl_from_prompt(stage_def.get("prompt", "prompts/implementation/default.md"))
            all_commits = []
            for group in slice_groups:
                if len(group) == 1:
                    slice_file = group[0]
                    sub_id = slice_to_id[slice_file]
                    variables["slice_file"] = slice_file
                    update_plan_md(run_folder, sub_id, "in_progress")
                    t0 = time.monotonic()
                    sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path, output_suffix=sub_id, cwd=variables.get("repo_root"))
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
                                   output_summary=f"{len(commits)} commit{'s' if len(commits) != 1 else ''}" if commits else None)
                else:
                    logger.log(stage_name, "INFO", f"dispatching {len(group)} slices in parallel")
                    for sf in group:
                        update_plan_md(run_folder, slice_to_id[sf], "in_progress")
                    t0 = time.monotonic()
                    futures: dict[concurrent.futures.Future, tuple[str, str]] = {}
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(group)) as executor:
                        for slice_file in group:
                            sub_id = slice_to_id[slice_file]
                            vars_copy = dict(variables)
                            vars_copy["slice_file"] = slice_file
                            fut = executor.submit(
                                run_stage, stage_name, impl, vars_copy,
                                run_folder, docs_root, project, project_log_path,
                                sub_id,
                                cwd=variables.get("repo_root"),
                            )
                            futures[fut] = (sub_id, slice_file)
                    elapsed = time.monotonic() - t0
                    failed = False
                    for fut, (sub_id, slice_file) in futures.items():
                        sig = fut.result()
                        if sig.get("status") != "passed":
                            st = state_mod.load_state(run_folder)
                            st["blocked_at"] = stage_name
                            state_mod.save_state(run_folder, st)
                            update_plan_md(run_folder, sub_id, sig["status"])
                            logger.log(stage_name, "ERROR", f"pipeline stopped: stage {stage_name} {sig['status']} on slice {slice_file}: {sig.get('message', '')}")
                            failed = True
                        else:
                            commits = sig.get("commit_hashes", [])
                            all_commits.extend(commits)
                            update_plan_md(run_folder, sub_id, "passed", elapsed_secs=elapsed,
                                           output_summary=f"{len(commits)} commit{'s' if len(commits) != 1 else ''}" if commits else None)
                    if failed:
                        sys.exit(1)
            signals[stage_name] = {"status": "passed", "commit_hashes": all_commits, "branch": branch}
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, signals[stage_name])
            continue

        if stage_name == "review":
            logger.log(stage_name, "INFO", "stage starting")
            review_md_path = run_folder / "review.md"
            variables["review_md"] = str(review_md_path)
            variables["round"] = "1"
            impl_sig = signals.get("implementation", {})
            commit_hashes = impl_sig.get("commit_hashes", [])
            if commit_hashes and "repo_root" in variables:
                first, last = commit_hashes[0], commit_hashes[-1]
                diff_result = subprocess.run(
                    ["git", "-C", variables["repo_root"], "diff", f"{first}^..{last}"],
                    capture_output=True, text=True,
                )
                diff_path = run_folder / "diff-round-1.patch"
                diff_path.write_text(diff_result.stdout)
                variables["diff"] = str(diff_path)
            else:
                variables["diff"] = ""
            prompts = stage_def.get("prompts", {})
            reviewer_statuses = {}
            changes_requested = []
            for reviewer, prompt_path in prompts.items():
                sub_id = f"{stage_name}_{reviewer}"
                update_plan_md(run_folder, sub_id, "in_progress")
                impl = _impl_from_prompt(prompt_path)
                t0 = time.monotonic()
                sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path)
                elapsed = time.monotonic() - t0
                verdict = sig.get("reviewer_statuses", {}).get(reviewer, sig.get("status", "unknown"))
                reviewer_statuses[reviewer] = verdict
                if verdict == "changes-requested":
                    changes_requested.append(reviewer)
                sub_status = "blocked" if verdict == "changes-requested" else "passed"
                update_plan_md(run_folder, sub_id, sub_status, elapsed_secs=elapsed, output_summary=verdict)
            review_signal = {
                "status": "passed",
                "reviewer_statuses": reviewer_statuses,
                "changes_requested": changes_requested,
                "review_md": str(review_md_path),
            }
            signals[stage_name] = review_signal
            if changes_requested:
                result = review_cycle_mod.run(
                    run_folder, docs_root, project, branch, review_signal, project_log_path,
                    repo_root=variables.get("repo_root", ""),
                )
                if not result.get("all_passed"):
                    st = state_mod.load_state(run_folder)
                    st["blocked_at"] = "review"
                    state_mod.save_state(run_folder, st)
                    update_plan_md(run_folder, stage_name, "blocked")
                    logger.log(stage_name, "ERROR", f"pipeline stopped: review cycle blocked, reviewers={result.get('reviewers', [])}")
                    sys.exit(1)
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            state_mod.save_stage_signal(run_folder, stage_name, review_signal)
            update_plan_md(run_folder, stage_name, "passed", output_summary=_output_summary(stage_name, review_signal))
            continue

        logger.log(stage_name, "INFO", "stage starting")
        update_plan_md(run_folder, stage_name, "in_progress")
        impl = _impl_from_prompt(stage_def.get("prompt", f"prompts/{stage_name}/default.md"))
        t0 = time.monotonic()
        stage_cwd = variables.get("repo_root") if stage_name == "qa" else None
        sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path, cwd=stage_cwd)
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
        update_plan_md(run_folder, stage_name, "passed", elapsed_secs=elapsed, output_summary=_output_summary(stage_name, sig))

    logger.log("pipeline", "INFO", "pipeline completed successfully")
