# CLI entry point; defines the run command that drives the full orchestration pipeline.
import json
import sys

import click

from orchestrator import paths
from orchestrator import state as state_mod
from orchestrator import orchestrate
from orchestrator.run_stage import run_stage


@click.group()
def main():
    """Orchestrator — pipeline sequencer for feature development."""


@main.command()
@click.option("--docs-root", required=True, help="Path to the team-hub docs root.")
@click.option("--project", required=True, help="Project name under docs-root/projects/.")
@click.option("--feature-path", required=True, help="Docs-relative path to the feature directory (must contain overview.md).")
@click.option("--branch", required=True, help="Git branch name to create for implementation.")
@click.option("--profile", default="full", show_default=True, help="Built-in profile name (full, spike) or path to a profile YAML file.")
def run(docs_root, project, feature_path, branch, profile):
    """Run the full pipeline for a feature."""
    try:
        paths.require_dir(docs_root)
    except FileNotFoundError as e:
        raise click.UsageError(str(e))
    orchestrate.run_pipeline(docs_root, project, feature_path, branch, profile)


@main.command()
@click.option("--stage", "stage_name", required=True, help="Stage name to run.")
@click.option("--implementation", default="default", show_default=True, help="Prompt implementation.")
@click.option("--input", "input_json", required=True, help="Path to JSON file with input variables.")
@click.option("--run-folder", required=True, help="Path to the run folder.")
@click.option("--docs-root", required=True, help="Path to the team-hub docs root.")
@click.option("--project", required=True, help="Project name.")
@click.option("--project-log-path", required=True, help="Path for project-level orchestrator.log.")
def stage(stage_name, implementation, input_json, run_folder, docs_root, project, project_log_path):
    """Run a single pipeline stage directly."""
    try:
        paths.require_dir(docs_root)
    except FileNotFoundError as e:
        raise click.UsageError(str(e))
    from pathlib import Path
    variables = json.loads(Path(input_json).read_text())
    sig = run_stage(stage_name, implementation, variables, run_folder, docs_root, project, project_log_path)
    click.echo(json.dumps(sig, indent=2))
    sys.exit(0 if sig.get("status") == "passed" else 1)


@main.command()
@click.option("--run-folder", required=True, help="Path to an existing run folder to resume.")
@click.option("--docs-root", required=True, help="Path to the team-hub docs root.")
def resume(run_folder, docs_root):
    """Resume a pipeline from a blocked run folder."""
    from pathlib import Path
    try:
        paths.require_dir(docs_root)
        paths.require_dir(run_folder)
    except FileNotFoundError as e:
        raise click.UsageError(str(e))

    st = state_mod.load_state(run_folder)
    blocked_at = st.get("blocked_at")
    if not blocked_at:
        raise click.UsageError("No blocked_at in state — nothing to resume.")

    for key in ("project", "feature_path", "branch"):
        if not st.get(key):
            raise click.UsageError(f"State is missing '{key}' — run folder may predate state persistence.")

    project = st["project"]
    feature_path = st["feature_path"]
    branch = st["branch"]
    profile = st.get("profile", "full")

    orchestrate.run_pipeline(docs_root, project, feature_path, branch, profile, resume=True)
