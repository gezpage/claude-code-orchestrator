# CLI entry point; defines the run command that drives the full orchestration pipeline.
import json
import sys
from pathlib import Path

import click
import yaml

from orchestrator import _cli_tui, _prompts, bootstrap, orchestrate, paths
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


@main.command(name="bootstrap")
@click.option("--docs-root", default=None, help="Path to your docs root.")
@click.option("--project", default=None, help="Project name under docs-root/projects/.")
@click.option(
    "--toolchain",
    type=click.Choice(list(bootstrap.SUPPORTED_TOOLCHAINS)),
    default=None,
    help="Language/toolchain to scaffold (python, node, typescript, php, go, java).",
)
@click.option(
    "--commit/--no-commit",
    "commit",
    default=None,
    help="Stage and commit the bootstrap changes in the target repo (default: prompt or no-commit).",
)
@click.option("--dry-run", is_flag=True, default=False, help="Print planned changes without writing files.")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing files whose contents differ from the template.",
)
def bootstrap_cmd(docs_root, project, toolchain, commit, dry_run, force):
    """Bootstrap a target repo for deterministic verification."""
    is_tty = _prompts.is_interactive()

    if not docs_root:
        if not is_tty:
            raise click.UsageError("Missing --docs-root. Required when stdin/stdout is not a TTY.")
        docs_root = _prompts.ask_path("Path to your docs root", must_exist=True)
    try:
        paths.require_dir(docs_root)
    except FileNotFoundError as e:
        raise click.UsageError(str(e)) from e

    if not project:
        if not is_tty:
            raise click.UsageError("Missing --project. Required when stdin/stdout is not a TTY.")
        # Reuse the project picker from the run-command TUI.
        existing = _cli_tui._list_projects(docs_root)
        if existing:
            project = _prompts.ask_select("Project", choices=existing, default=existing[0])
        else:
            project = _prompts.ask_text("Project name")

    project_yaml = Path(docs_root) / "projects" / project / "project.yaml"
    if not project_yaml.is_file():
        raise click.UsageError(f"project.yaml not found at {project_yaml}")
    project_config = yaml.safe_load(project_yaml.read_text()) or {}
    repo_root_raw = project_config.get("repo-root")
    if not repo_root_raw:
        raise click.UsageError(f"{project_yaml} is missing required field 'repo-root'.")
    repo_root = Path(repo_root_raw).expanduser()
    if not repo_root.is_dir():
        raise click.UsageError(f"repo-root does not exist: {repo_root}")

    if not toolchain:
        if not is_tty:
            raise click.UsageError(
                "Missing --toolchain. Required when stdin/stdout is not a TTY. "
                f"Choices: {', '.join(bootstrap.SUPPORTED_TOOLCHAINS)}."
            )
        toolchain = _prompts.ask_select(
            "Toolchain",
            choices=list(bootstrap.SUPPORTED_TOOLCHAINS),
            default="python",
        )

    plan = bootstrap.plan_bootstrap(repo_root, toolchain)

    _print_plan(plan)

    if dry_run:
        click.echo("[orchestrator] --dry-run: no files written.")
        return

    conflicts = plan.conflicts
    if conflicts and not force:
        if not is_tty:
            names = ", ".join(str(c.path.relative_to(repo_root)) for c in conflicts)
            raise click.UsageError(
                f"Existing file(s) would be overwritten: {names}. "
                "Re-run with --force to overwrite, or remove the file(s) first."
            )
        if not _prompts.ask_confirm(
            f"{len(conflicts)} file(s) already exist with different contents — overwrite?",
            default=False,
        ):
            click.echo("[orchestrator] Aborted — keeping existing files.")
            sys.exit(1)
        force = True

    if not plan.new_files and not (force and conflicts):
        click.echo("[orchestrator] Nothing to do — all template files are already present.")
        return

    written = bootstrap.apply_plan(plan, force=force)
    for path in written:
        click.echo(f"  wrote {path.relative_to(repo_root)}")

    if bootstrap.update_project_standards(project_yaml, toolchain):
        click.echo(f"  updated standards in {project_yaml}")

    if commit is None:
        commit = is_tty and _prompts.ask_confirm("Commit bootstrap changes now?", default=False)

    if commit:
        try:
            sha = bootstrap.commit_changes(repo_root, written)
            click.echo(f"[orchestrator] Committed {sha}: chore: bootstrap orchestrator project config")
        except (OSError, ValueError) as exc:
            raise click.ClickException(f"commit failed: {exc}") from exc


def _print_plan(plan: "bootstrap.BootstrapPlan") -> None:
    click.echo(f"[orchestrator] Bootstrap plan for {plan.repo_root} (toolchain: {plan.toolchain})")
    if plan.new_files:
        click.echo("  files to create:")
        for f in plan.new_files:
            click.echo(f"    + {f.path.relative_to(plan.repo_root)}")
    if plan.already_present:
        click.echo("  unchanged (already match template):")
        for f in plan.already_present:
            click.echo(f"    = {f.path.relative_to(plan.repo_root)}")
    if plan.conflicts:
        click.echo("  WILL OVERWRITE (contents differ):")
        for f in plan.conflicts:
            click.echo(f"    ! {f.path.relative_to(plan.repo_root)}")
