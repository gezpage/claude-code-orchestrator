import datetime
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from orchestrator import paths, state as state_mod
from orchestrator.logger import OrchestratorLogger
from orchestrator.run_stage import run_stage
import orchestrator.review_cycle as review_cycle_mod

_STYLE_MAP = {
    "pending":     "fill:#f8fafc,stroke:#cbd5e1,color:#1e293b,stroke-width:2px",
    "passed":      "fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:2px",
    "blocked":     "fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:2px",
    "failed":      "fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:2px",
    "in_progress": "fill:#ffedd5,stroke:#ea580c,color:#7c2d12,stroke-width:2px",
    "skipped":     "fill:#f1f5f9,stroke:#94a3b8,color:#64748b,stroke-width:2px",
}


def _format_elapsed(secs):
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s"


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


def _node_label(display, impl, tick=False, elapsed_secs=None, output_summary=None):
    prefix = "✓ " if tick else ""
    parts = [f"{prefix}{display}", impl]
    if elapsed_secs is not None:
        parts.append(f"⏱ {_format_elapsed(elapsed_secs)}")
    if output_summary:
        parts.append(output_summary)
    return "\\n".join(parts)


def _init_plan_md(run_folder, profile):
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if plan_path.exists():
        return

    stages = profile.get("stages", [])
    pending = _STYLE_MAP["pending"]
    lines = [
        "```mermaid",
        "%%{init: {'theme': 'base', 'themeVariables': {'fontSize': '14px'}}}%%",
        "flowchart TD",
    ]

    # Collect top-level node IDs in order (for the chain)
    chain_ids = []
    # Track review sub-nodes separately
    review_sub_ids = []

    for stage_def in stages:
        name = stage_def["stage"]
        if "prompt" in stage_def:
            impl = Path(stage_def["prompt"]).stem
            label = _node_label(name.title(), impl)
            lines.append(f'    {name}["{label}"]')
            chain_ids.append(name)
        elif "prompts" in stage_def:
            # Parent node (no impl label — children carry that)
            lines.append(f'    {name}["{name.title()}"]')
            chain_ids.append(name)
            # Child nodes for each reviewer
            for reviewer in stage_def["prompts"]:
                reviewer_impl = Path(stage_def["prompts"][reviewer]).stem
                sub_id = f"{name}_{reviewer}"
                label = _node_label(reviewer.title(), reviewer_impl)
                lines.append(f'    {sub_id}["{label}"]')
                review_sub_ids.append((name, sub_id))
        else:
            impl = "interactive"
            label = _node_label(name.title(), impl)
            lines.append(f'    {name}["{label}"]')
            chain_ids.append(name)

    # Main chain
    if len(chain_ids) > 1:
        lines.append("    " + " --> ".join(chain_ids))
    # Fan-out edges for review sub-nodes
    for parent_id, sub_id in review_sub_ids:
        lines.append(f"    {parent_id} --> {sub_id}")

    # Styles
    for node_id in chain_ids:
        lines.append(f"    style {node_id} {pending}")
    for _, sub_id in review_sub_ids:
        lines.append(f"    style {sub_id} {pending}")

    lines.append("```")
    lines.append("")

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("\n".join(lines))


def _expand_impl_nodes(run_folder, slice_files):
    """Replace the single 'implementation' node with one node per slice."""
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if not plan_path.exists() or not slice_files:
        return

    pending = _STYLE_MAP["pending"]
    n = len(slice_files)
    sub_ids = [f"impl_{i+1}" for i in range(n)]

    content = plan_path.read_text()

    # Replace node definition
    old_def = re.search(r'    implementation\["[^"]*"\]', content)
    if old_def:
        new_defs = "\n".join(
            f'    {sid}["Slice {i+1}\\nimplementation"]'
            for i, sid in enumerate(sub_ids)
        )
        content = content[:old_def.start()] + new_defs + content[old_def.end():]

    # Replace chain segment: decomposition --> implementation --> (next)
    content = re.sub(
        r'decomposition --> implementation(?: --> (\w+))?',
        lambda m: "decomposition --> " + " --> ".join(sub_ids) + (f" --> {m.group(1)}" if m.group(1) else ""),
        content,
    )

    # Replace single style line with N style lines
    old_style = re.search(rf"    style implementation [^\n]+", content)
    if old_style:
        new_styles = "\n".join(f"    style {sid} {pending}" for sid in sub_ids)
        content = content[:old_style.start()] + new_styles + content[old_style.end():]

    plan_path.write_text(content)


def update_plan_md(run_folder, stage, status, elapsed_secs=None, output_summary=None):
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    style_str = _STYLE_MAP.get(status, _STYLE_MAP["pending"])
    tick = status == "passed"

    if not plan_path.exists():
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            f"```mermaid\nflowchart TD\n    style {stage} {style_str}\n```\n"
        )
        return

    content = plan_path.read_text()

    # Update style
    style_pattern = rf"(style {re.escape(stage)} )[^\n]+"
    if re.search(style_pattern, content):
        content = re.sub(style_pattern, rf"\g<1>{style_str}", content)
    else:
        last_fence = content.rfind("```")
        if last_fence >= 0:
            content = (
                content[:last_fence]
                + f"    style {stage} {style_str}\n"
                + content[last_fence:]
            )
        else:
            content += f"\nstyle {stage} {style_str}"

    # Update node label if we have extra info or tick
    if tick or elapsed_secs is not None or output_summary:
        node_pattern = rf'    {re.escape(stage)}\["([^"]*)"\]'
        m = re.search(node_pattern, content)
        if m:
            existing_label = m.group(1)
            # Extract display and impl from existing label (first two \\n-separated parts)
            parts = existing_label.split("\\n")
            # Strip any existing tick
            display = parts[0].lstrip("✓ ").strip() if parts else stage.title()
            impl = parts[1] if len(parts) > 1 else ""
            new_label = _node_label(display, impl, tick=tick, elapsed_secs=elapsed_secs, output_summary=output_summary)
            content = content[:m.start()] + f'    {stage}["{new_label}"]' + content[m.end():]

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

    _init_plan_md(run_folder, profile)

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
            for sig in signals.values():
                if isinstance(sig, dict) and "slice_files" in sig:
                    slice_files = sig["slice_files"]
                    break
            _expand_impl_nodes(run_folder, slice_files)
            impl = _impl_from_prompt(stage_def.get("prompt", "prompts/implementation/default.md"))
            all_commits = []
            for i, slice_file in enumerate(slice_files):
                variables["slice_file"] = slice_file
                sub_id = f"impl_{i+1}"
                update_plan_md(run_folder, sub_id, "in_progress")
                t0 = time.monotonic()
                sig = run_stage(stage_name, impl, variables, run_folder, docs_root, project, project_log_path)
                elapsed = time.monotonic() - t0
                if sig.get("status") != "passed":
                    st["blocked_at"] = stage_name
                    state_mod.save_state(run_folder, st)
                    update_plan_md(run_folder, sub_id, sig["status"])
                    logger.log(stage_name, "ERROR", f"pipeline stopped: stage {stage_name} {sig['status']} on slice {slice_file}: {sig.get('message', '')}")
                    sys.exit(1)
                commits = sig.get("commit_hashes", [])
                all_commits.extend(commits)
                update_plan_md(run_folder, sub_id, "passed", elapsed_secs=elapsed,
                               output_summary=f"{len(commits)} commit{'s' if len(commits) != 1 else ''}" if commits else None)
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
            st["blocked_at"] = stage_name
            state_mod.save_state(run_folder, st)
            update_plan_md(run_folder, stage_name, sig["status"])
            logger.log(stage_name, "ERROR", f"pipeline stopped: stage {stage_name} {sig['status']}: {sig.get('message', '')}")
            sys.exit(1)

        state_mod.update_stage_status(run_folder, stage_name, "passed")
        state_mod.save_stage_signal(run_folder, stage_name, sig)
        update_plan_md(run_folder, stage_name, "passed", elapsed_secs=elapsed, output_summary=_output_summary(stage_name, sig))

    logger.log("pipeline", "INFO", "pipeline completed successfully")
