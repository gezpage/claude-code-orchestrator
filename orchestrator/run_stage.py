# Stage executor; dispatches via an AgentRunner, extracts SIGNAL_JSON, validates output.
import json
import subprocess
import sys
import time
from pathlib import Path

from orchestrator import renderer, validator
from orchestrator import signal as signal_mod
from orchestrator.agent_runner import (
    AgentRunner,
    AgentRunRequest,
    ClaudeCodePrintRunner,
    ProgressEvent,
)
from orchestrator.logger import OrchestratorLogger
from orchestrator.plan import rerender_plan_md


def _fmt_elapsed(secs: float) -> str:
    if secs < 60:
        return f"{secs:.0f}s"
    return f"{int(secs) // 60}m {int(secs) % 60:02d}s"


def _signal_summary(sig: dict) -> str:
    if "summary" in sig:
        s: str = sig["summary"]
        return s.split(".")[0].strip() if "." in s[:200] else s[:200].strip()
    if "commit_hashes" in sig:
        h = sig["commit_hashes"]
        return f"{len(h)} commit{'s' if len(h) != 1 else ''}: {', '.join(h)}"
    if "slice_files" in sig:
        return f"{len(sig['slice_files'])} implementation slices"
    if "reviewer_statuses" in sig:
        return ", ".join(f"{r}={v}" for r, v in sig["reviewer_statuses"].items())
    if "qa_pair_count" in sig:
        return f"{sig['qa_pair_count']} QA pairs, {sig.get('qualifying_decisions', 0)} qualifying decisions"
    if "outcome" in sig:
        parts = [f"outcome={sig['outcome']}"]
        for k in ("confidence", "regression_risk"):
            if k in sig:
                parts.append(f"{k}={sig[k]}")
        return " ".join(parts)
    if "message" in sig:
        return str(sig["message"])[:200]
    return ""


def _format_stage_output(stdout: str, sig: dict) -> str:
    lines = stdout.splitlines(keepends=True)
    result = []
    for line in lines:
        if line.strip().strip("`").startswith(signal_mod.SENTINEL):
            result.append("```json\n" + json.dumps(sig, indent=2) + "\n```\n")
        else:
            result.append(line)
    return "".join(result)


def _declared_artifact_paths(sig: dict) -> list[Path]:
    """Return output artifact paths declared by a passed signal that should exist now.

    This relies on the stage-schema convention that ``*_path``/``*_file`` fields in
    passed signals name outputs, not input references.
    """
    paths: list[Path] = []

    def visit(key: str, value) -> None:
        if key.endswith(("_path", "_file", "_md")) and isinstance(value, str):
            paths.append(Path(value))
            return
        if key.endswith(("_paths", "_files")) and isinstance(value, list):
            paths.extend(Path(item) for item in value if isinstance(item, str))
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_key, child_value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for child_key, child_value in item.items():
                        visit(child_key, child_value)

    for key, value in sig.items():
        visit(key, value)
    return paths


def _missing_declared_artifacts(sig: dict) -> list[Path]:
    if sig.get("status") != "passed":
        return []
    return [path for path in _declared_artifact_paths(sig) if not path.exists()]


def _agent_writable_roots(docs_root: str, run_folder: Path, variables: dict) -> tuple[str, ...]:
    roots = [
        Path(docs_root),
        run_folder,
        Path(str(variables["repo_root"])) if "repo_root" in variables else None,
    ]
    return tuple(str(path) for path in dict.fromkeys(roots) if path is not None)


def _default_runner() -> AgentRunner:
    """Default runner when callers haven't injected one. Matches the legacy command shape
    (claude -p ... --bare --dangerously-skip-permissions) with sterile context enabled."""
    return ClaudeCodePrintRunner(sterile_context=True)


def _make_progress_callback(logger: OrchestratorLogger, stage: str):
    """Build a callback that logs streaming runner events into run.log.

    Kept lightweight: every event becomes one INFO line. The runner already
    swallows callback exceptions so a logger glitch can't break a stage. See
    ADR-024.
    """

    def _on_event(event: ProgressEvent) -> None:
        summary = event.summary.strip()
        if not summary:
            return
        if event.kind == "tool_use":
            logger.log(stage, "INFO", f"▸ {summary}")
        elif event.kind == "assistant_text":
            logger.log(stage, "INFO", f"… {summary}")
        elif event.kind == "error":
            logger.log(stage, "WARN", summary)
        else:
            logger.log(stage, "DEBUG", summary)

    return _on_event


def _runner_failure_signal(stage: str, result) -> dict | None:
    """If the runner reported timeout or a non-zero exit, return a blocked signal.

    Must be checked before signal extraction — otherwise stdout from a failed or
    partial run could be parsed as a valid SIGNAL_JSON and accepted, which defeats
    the purpose of capturing exit_code / timed_out at the runner layer.
    """
    if result.timed_out:
        return {"stage": stage, "status": "blocked", "message": "Agent runner timed out"}
    if result.exit_code not in (0, None):
        return {
            "stage": stage,
            "status": "blocked",
            "message": f"Agent runner failed with exit code {result.exit_code}",
        }
    return None


def run_stage(
    stage: str,
    implementation: str,
    variables: dict,
    run_folder,
    docs_root: str,
    project: str,
    project_log_path: str,
    output_suffix: str = "",
    cwd: str | None = None,
    prompt_file: str | None = None,
    schema_name: str | None = None,
    standards: list[str] | None = None,
    runner: AgentRunner | None = None,
) -> dict:
    run_folder = Path(run_folder)
    logger = OrchestratorLogger(run_folder, project_log_path)
    runner = runner if runner is not None else _default_runner()

    label = output_suffix or implementation
    logger.log(stage, "INFO", f"dispatching {label}")
    if prompt_file is not None:
        prompt = Path(prompt_file).read_text()
    else:
        try:
            prompt = renderer.render_prompt(stage, implementation, variables, docs_root, project, standards=standards)
        except Exception as exc:
            import traceback as _tb

            logger.log(stage, "ERROR", f"prompt render failed: {exc}\n{_tb.format_exc()}")
            return {"stage": stage, "status": "blocked", "message": f"Prompt render failed: {exc}"}

    output_dir = run_folder / stage
    output_dir.mkdir(parents=True, exist_ok=True)
    tag = f"-{output_suffix}" if output_suffix else ""
    (output_dir / f"{stage}{tag}-prompt.md").write_text(prompt)
    # Re-render so the prompt link surfaces in plan.md before the agent dispatches
    # — otherwise it only appears after the stage finishes and update_plan_md re-renders.
    rerender_plan_md(run_folder)

    progress_callback = _make_progress_callback(logger, stage)
    t0 = time.monotonic()
    result = runner.run(
        AgentRunRequest(
            prompt=prompt,
            stage_name=stage,
            cwd=cwd,
            workspace_root=cwd or docs_root,
            writable_roots=_agent_writable_roots(docs_root, run_folder, variables),
            progress_callback=progress_callback,
        )
    )
    stdout = result.stdout
    elapsed = time.monotonic() - t0
    output_file = output_dir / f"{stage}{tag}-output.md"
    output_file.write_text(stdout)

    if failure := _runner_failure_signal(stage, result):
        logger.log(stage, "ERROR", failure["message"])
        return failure

    sig = signal_mod.extract_signal(stdout)

    if sig is None:
        logger.log(stage, "DEBUG", "no SIGNAL_JSON found — retrying")
        grace_prompt = (
            f"Your previous output did not include a SIGNAL_JSON: line. "
            f"You were executing the '{stage}' stage.\n"
            f"If the work is complete, emit:\n"
            f'SIGNAL_JSON: {{"stage": "{stage}", "status": "passed", ...}}\n'
            f"If the work could not be completed, emit:\n"
            f'SIGNAL_JSON: {{"stage": "{stage}", "status": "blocked", "message": "<reason>"}}\n'
            f"Emit the SIGNAL_JSON line now, with no other output."
        )
        retry_result = runner.run(
            AgentRunRequest(
                prompt=grace_prompt,
                stage_name=stage,
                cwd=cwd,
                workspace_root=cwd or docs_root,
                writable_roots=_agent_writable_roots(docs_root, run_folder, variables),
                progress_callback=progress_callback,
            )
        )
        if failure := _runner_failure_signal(stage, retry_result):
            logger.log(stage, "ERROR", f"grace retry: {failure['message']}")
            return failure
        sig = signal_mod.extract_signal(retry_result.stdout)

    if sig is None:
        logger.log(stage, "ERROR", "signal missing after grace retry — treating as blocked")
        return {"stage": stage, "status": "blocked", "message": "No signal emitted"}

    output_file.write_text(_format_stage_output(stdout, sig))

    validator.validate_output(schema_name if schema_name else stage, sig)
    missing_artifacts = _missing_declared_artifacts(sig)
    if missing_artifacts:
        missing = ", ".join(str(path) for path in missing_artifacts)
        logger.log(stage, "ERROR", f"passed signal declared missing artifact(s): {missing}")
        return {"stage": stage, "status": "blocked", "message": f"Declared artifact(s) missing: {missing}"}
    elapsed_str = _fmt_elapsed(elapsed)
    summary = _signal_summary(sig)
    tag_label = output_suffix or stage
    completion_msg = f"{tag_label} {sig['status']} ({elapsed_str})"
    if summary:
        completion_msg += f" — {summary}"
    logger.log(stage, "INFO", completion_msg)
    for key, value in sig.items():
        if key == "stage":
            continue
        v = json.dumps(value) if isinstance(value, dict | list) else str(value)
        logger.log(stage, "DEBUG", f"signal.{key}={v}")
    return sig


def run_deterministic_stage(
    stage: str,
    repo_root: str,
    run_folder,
    project_log_path: str,
) -> dict:
    """Run a deterministic verification stage in-process.

    No Claude subprocess is invoked; the --bare and --dangerously-skip-permissions
    invariants apply only to run_stage(). See ADR-017.

    Returns a signal dict matching schemas/verification.json. On engine failure
    (e.g. no toolchain resolvable), returns a 'blocked' signal so the existing
    pipeline halt machinery handles it.
    """
    from orchestrator.verifiers import engine as verifier_engine
    from orchestrator.verifiers.engine import VerificationError

    run_folder = Path(run_folder)
    logger = OrchestratorLogger(run_folder, project_log_path)
    logger.log(stage, "INFO", "dispatching deterministic")

    t0 = time.monotonic()
    try:
        sig = verifier_engine.verify(Path(repo_root), run_folder)
    except VerificationError as exc:
        logger.log(stage, "ERROR", f"verification could not start: {exc}")
        return {"stage": stage, "status": "blocked", "message": str(exc)}
    elapsed = time.monotonic() - t0

    validator.validate_output(stage, sig)
    summary = sig.get("summary", "")
    completion_msg = f"{stage} {sig['status']} ({_fmt_elapsed(elapsed)})"
    if summary:
        completion_msg += f" — {summary}"
    logger.log(stage, "INFO", completion_msg)
    for key, value in sig.items():
        if key == "stage":
            continue
        v = json.dumps(value) if isinstance(value, dict | list) else str(value)
        logger.log(stage, "DEBUG", f"signal.{key}={v}")
    return sig


def run_interactive_stage(
    stage: str,
    prompt_path: str | None,
    variables: dict,
    run_folder,
    artifact_path,
    docs_root: str,
    project: str,
    project_log_path: str,
) -> dict:
    """Launch an interactive Claude session for a stage that requires human participation.

    Unlike run_stage(), this does not use --bare or --dangerously-skip-permissions.
    Completion is determined by the existence of artifact_path after the session exits.
    """
    run_folder = Path(run_folder)
    artifact_path = Path(artifact_path)
    logger = OrchestratorLogger(run_folder, project_log_path)
    logger.log(stage, "INFO", "dispatching interactive")

    cmd = ["claude"]
    if prompt_path is not None:
        implementation = Path(prompt_path).stem
        rendered = renderer.render_prompt(stage, implementation, variables, docs_root, project)
        output_dir = run_folder / stage
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{stage}-prompt.md").write_text(rendered)
        rerender_plan_md(run_folder)
        cmd = ["claude", rendered]

    subprocess.run(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)

    artifact_key = artifact_path.stem.replace("-", "_")
    if artifact_path.exists():
        logger.log(stage, "INFO", f"interactive passed — artifact {artifact_path.name}")
        return {"stage": stage, "status": "passed", artifact_key: str(artifact_path)}
    logger.log(stage, "WARN", f"interactive incomplete — {artifact_path.name} not created")
    return {"stage": stage, "status": "blocked", "message": f"Artifact not created: {artifact_path.name}"}


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--implementation", default="default")
    parser.add_argument("--input", dest="input_json", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--docs-root", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--project-log-path", required=True)
    args = parser.parse_args()

    variables = json.loads(Path(args.input_json).read_text())
    sig = run_stage(
        args.stage,
        args.implementation,
        variables,
        args.run_folder,
        args.docs_root,
        args.project,
        args.project_log_path,
    )
    print(json.dumps(sig, indent=2))  # noqa: T201
    sys.exit(0 if sig.get("status") == "passed" else 1)
