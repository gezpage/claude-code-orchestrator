# CLI entry point; defines the run command that drives the full orchestration pipeline.
import json
import sys

import click

from orchestrator import _cli_tui, orchestrate, paths
from orchestrator import state as state_mod
from orchestrator.run_stage import run_stage


@click.group()
def main():
    """Orchestrator — pipeline sequencer for feature development."""


@main.command()
@click.option("--docs-root", default=None, help="Path to your docs root.")
@click.option("--project", default=None, help="Project name under docs-root/projects/.")
@click.option(
    "--feature-path",
    default=None,
    help="Docs-relative path to the feature directory (must contain overview.md).",
)
@click.option("--branch", default=None, help="Git branch name to create for implementation.")
@click.option(
    "--profile",
    default=None,
    help="Built-in profile name (full, full-interactive, minimal, minimal-codex, spike) or path to a profile YAML file.",
)
@click.option(
    "--base-branch",
    default=None,
    help="Branch the implementation should fork from (default: main, or the value in project.yaml).",
)
@click.option(
    "--create-pr/--no-create-pr",
    "create_pr",
    default=None,
    help="Open a draft GitHub PR when the pipeline completes (default: prompt or use project.yaml).",
)
def run(docs_root, project, feature_path, branch, profile, base_branch, create_pr):
    """Run the full pipeline for a feature."""
    try:
        resolved = _cli_tui.resolve_run_inputs(
            _cli_tui.RunInputs(
                docs_root=docs_root,
                project=project,
                feature_path=feature_path,
                branch=branch,
                profile=profile,
                base_branch=base_branch,
                create_pr=create_pr,
            )
        )
    except _cli_tui.RunInputError as exc:
        raise click.UsageError(str(exc)) from exc

    # resolve_run_inputs guarantees these four are populated (or it raises).
    # Re-bind to locally-typed strings so mypy doesn't see Optional past this point.
    docs_root_resolved = resolved.docs_root or ""
    project_resolved = resolved.project or ""
    feature_path_resolved = resolved.feature_path or ""
    branch_resolved = resolved.branch or ""

    try:
        paths.require_dir(docs_root_resolved)
    except FileNotFoundError as e:
        raise click.UsageError(str(e)) from e

    orchestrate.run_pipeline(
        docs_root_resolved,
        project_resolved,
        feature_path_resolved,
        branch_resolved,
        resolved.profile or "full",
        base_branch=resolved.base_branch,
        create_pr=resolved.create_pr,
    )


@main.command()
@click.option("--stage", "stage_name", required=True, help="Stage name to run.")
@click.option("--implementation", default="default", show_default=True, help="Prompt implementation.")
@click.option("--input", "input_json", required=True, help="Path to JSON file with input variables.")
@click.option("--run-folder", required=True, help="Path to the run folder.")
@click.option("--docs-root", required=True, help="Path to your docs root.")
@click.option("--project", required=True, help="Project name.")
@click.option("--project-log-path", required=True, help="Path for project-level orchestrator.log.")
def stage(stage_name, implementation, input_json, run_folder, docs_root, project, project_log_path):
    """Run a single pipeline stage directly."""
    try:
        paths.require_dir(docs_root)
    except FileNotFoundError as e:
        raise click.UsageError(str(e)) from e
    from pathlib import Path

    variables = json.loads(Path(input_json).read_text())
    sig = run_stage(stage_name, implementation, variables, run_folder, docs_root, project, project_log_path)
    click.echo(json.dumps(sig, indent=2))
    sys.exit(0 if sig.get("status") == "passed" else 1)


@main.command()
@click.option("--run-folder", required=True, help="Path to an existing run folder to resume.")
@click.option("--docs-root", required=True, help="Path to your docs root.")
def resume(run_folder, docs_root):
    """Resume a pipeline from a blocked run folder."""
    try:
        paths.require_dir(docs_root)
        paths.require_dir(run_folder)
    except FileNotFoundError as e:
        raise click.UsageError(str(e)) from e

    st = state_mod.load_state(run_folder)

    for key in ("project", "feature_path", "branch"):
        if not st.get(key):
            raise click.UsageError(f"State is missing '{key}' — run folder may predate state persistence.")

    project = st["project"]
    feature_path = st["feature_path"]
    branch = st["branch"]
    profile = st.get("profile", "full")

    orchestrate.run_pipeline(docs_root, project, feature_path, branch, profile, resume=True)
