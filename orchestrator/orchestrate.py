import datetime
import re
import subprocess
import sys
from pathlib import Path

import yaml

from orchestrator import paths, state as state_mod
from orchestrator.logger import OrchestratorLogger
from orchestrator.run_stage import run_stage
import orchestrator.review_cycle as review_cycle_mod

_COLOR_MAP = {
    "passed": "#90EE90",
    "blocked": "#FFA500",
    "failed": "#FF6B6B",
    "in_progress": "#FFD700",
    "skipped": "#D3D3D3",
}


def update_plan_md(run_folder, stage, status):
    """Rewrite Mermaid node colour in plan.md for the given stage."""
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    color = _COLOR_MAP.get(status, "#FFFFFF")

    if not plan_path.exists():
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("```mermaid\nflowchart TD\n```\n")

    content = plan_path.read_text()
    pattern = rf"(style {re.escape(stage)} fill:)[^\n]*"
    replacement = rf"\g<1>{color}"

    if re.search(pattern, content):
        content = re.sub(pattern, replacement, content)
    else:
        last_fence = content.rfind("```")
        if last_fence >= 0:
            content = (
                content[:last_fence]
                + f"    style {stage} fill:{color}\n"
                + content[last_fence:]
            )
        else:
            content += f"\nstyle {stage} fill:{color}"

    plan_path.write_text(content)


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
    if "repo_root" in project_config:
        vars_dict["repo_root"] = project_config["repo_root"]
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
    profile = _load_profile(docs_root, project, profile_name)

    project_log_path = str(Path(docs_root) / "projects" / project)
    log_level = project_config.get("log_level", "DEBUG")

    run_folder = Path(_resolve_run_folder(docs_root, project, feature_path, resume))
    run_folder.mkdir(parents=True, exist_ok=True)

    st = state_mod.load_state(run_folder)
    completed = {
        stage for stage, status in st.get("stages", {}).items() if status == "passed"
    }

    logger = OrchestratorLogger(run_folder, project_log_path, log_level)
    signals = {}

    for stage_def in profile.get("stages", []):
        stage_name = stage_def["stage"]

        if stage_name in completed:
            logger.log(stage_name, "INFO", "already passed — skipping")
            continue

        if stage_name == "alignment":
            alignment_log = run_folder / "alignment-log.md"
            if alignment_log.exists():
                signals[stage_name] = {"alignment_log": str(alignment_log)}
                state_mod.update_stage_status(run_folder, stage_name, "passed")
                update_plan_md(run_folder, stage_name, "passed")
                continue
            st["blocked_at"] = "alignment"
            state_mod.save_state(run_folder, st)
            update_plan_md(run_folder, stage_name, "blocked")
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
            _create_branch(branch, logger)
            slice_files = []
            for sig in signals.values():
                if isinstance(sig, dict) and "slice_files" in sig:
                    slice_files = sig["slice_files"]
                    break
            impl = _impl_from_prompt(stage_def.get("prompt", "prompts/implementation/default.md"))
            all_commits = []
            for slice_file in slice_files:
                variables["slice_file"] = slice_file
                sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path)
                if sig.get("status") != "passed":
                    st["blocked_at"] = stage_name
                    state_mod.save_state(run_folder, st)
                    update_plan_md(run_folder, stage_name, sig["status"])
                    print(f"\n[orchestrator] Stage '{stage_name}' {sig['status']} on slice {slice_file}: {sig.get('message', '')}\n")
                    sys.exit(1)
                all_commits.extend(sig.get("commit_hashes", []))
            signals[stage_name] = {"status": "passed", "commit_hashes": all_commits, "branch": branch}
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            update_plan_md(run_folder, stage_name, "passed")
            continue

        if stage_name == "review":
            prompts = stage_def.get("prompts", {})
            reviewer_statuses = {}
            changes_requested = []
            for reviewer, prompt_path in prompts.items():
                impl = _impl_from_prompt(prompt_path)
                sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path)
                reviewer_statuses[reviewer] = sig.get("status", "unknown")
                if sig.get("status") == "changes-requested":
                    changes_requested.append(reviewer)
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
                    st["blocked_at"] = "review"
                    state_mod.save_state(run_folder, st)
                    update_plan_md(run_folder, stage_name, "blocked")
                    print(f"\n[orchestrator] Review cycle blocked. Reviewers: {result.get('reviewers', [])}\n")
                    sys.exit(1)
            state_mod.update_stage_status(run_folder, stage_name, "passed")
            update_plan_md(run_folder, stage_name, "passed")
            continue

        impl = _impl_from_prompt(stage_def.get("prompt", f"prompts/{stage_name}/default.md"))
        sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path)
        signals[stage_name] = sig

        if sig.get("status") in ("blocked", "failed"):
            st["blocked_at"] = stage_name
            state_mod.save_state(run_folder, st)
            update_plan_md(run_folder, stage_name, sig["status"])
            print(f"\n[orchestrator] Stage '{stage_name}' {sig['status']}: {sig.get('message', '')}\n")
            sys.exit(1)

        state_mod.update_stage_status(run_folder, stage_name, "passed")
        update_plan_md(run_folder, stage_name, "passed")

    logger.log("pipeline", "INFO", "Pipeline completed successfully")
    print("\n[orchestrator] Pipeline completed.\n")
