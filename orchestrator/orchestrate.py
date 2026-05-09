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
from orchestrator.run_stage import run_stage
import orchestrator.review_cycle as review_cycle_mod


def _output_summary(stage, signal):
    if stage == "discovery":
        n = len(signal.get("findings_files", []))
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


def _create_branch(branch, logger):
    result = subprocess.run(
        ["git", "checkout", "-b", branch],
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

        if stage_name == "alignment":
            logger.log(stage_name, "INFO", "stage starting")
            if "prompt" in stage_def:
                variables = _build_variables(
                    stage_name, signals, branch, feature_path,
                    docs_root, project, run_folder, project_config,
                )
                impl = _impl_from_prompt(stage_def["prompt"])
                t0 = time.monotonic()
                sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path)
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
                continue

            alignment_log = run_folder / "alignment-log.md"
            if alignment_log.exists():
                signals[stage_name] = {"alignment_log": str(alignment_log)}
                state_mod.update_stage_status(run_folder, stage_name, "passed")
                state_mod.save_stage_signal(run_folder, stage_name, signals[stage_name])
                update_plan_md(run_folder, stage_name, "passed")
                continue
            st = state_mod.load_state(run_folder)
            st["blocked_at"] = "alignment"
            state_mod.save_state(run_folder, st)
            update_plan_md(run_folder, stage_name, "blocked")
            logger.log(stage_name, "INFO", "pipeline paused: waiting for human to create alignment-log.md")
            print(
                f"\n[orchestrator] Alignment pause.\n"
                f"  Run folder : {run_folder}\n"
                f"  Create     : {run_folder}/alignment-log.md\n"
                f"  Then resume: orchestrator resume --run-folder {run_folder} --docs-root {docs_root}\n"
            )
            sys.exit(0)

        variables = _build_variables(
            stage_name, signals, branch, feature_path,
            docs_root, project, run_folder, project_config,
        )

        if stage_name == "implementation":
            logger.log(stage_name, "INFO", "stage starting")
            _create_branch(branch, logger)
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
            expand_impl_nodes(run_folder, all_slices)
            impl = _impl_from_prompt(stage_def.get("prompt", "prompts/implementation/default.md"))
            all_commits = []
            for group in slice_groups:
                if len(group) == 1:
                    slice_file = group[0]
                    sub_id = slice_to_id[slice_file]
                    variables["slice_file"] = slice_file
                    update_plan_md(run_folder, sub_id, "in_progress")
                    t0 = time.monotonic()
                    sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path, output_suffix=sub_id)
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
            }
            signals[stage_name] = review_signal
            if changes_requested:
                result = review_cycle_mod.run(
                    run_folder, docs_root, project, branch, review_signal, project_log_path
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
        sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path)
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
