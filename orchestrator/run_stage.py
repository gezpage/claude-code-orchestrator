# Stage executor; invokes Claude via CLI, extracts the SIGNAL_JSON sentinel, and validates stage output.
import json
import subprocess
import sys
import time
from pathlib import Path

from orchestrator import renderer, signal as signal_mod, validator
from orchestrator.logger import OrchestratorLogger


def _fmt_elapsed(secs: float) -> str:
    if secs < 60:
        return f"{secs:.0f}s"
    return f"{int(secs) // 60}m {int(secs) % 60:02d}s"


def _signal_summary(sig: dict) -> str:
    if "summary" in sig:
        s = sig["summary"]
        return s.split(".")[0].strip() if "." in s[:200] else s[:200].strip()
    if "commit_hashes" in sig:
        h = sig["commit_hashes"]
        return f"{len(h)} commit{'s' if len(h) != 1 else ''}: {', '.join(h)}"
    if "slice_files" in sig:
        return f"{len(sig['slice_files'])} slices"
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
        return sig["message"][:200]
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


def _run_claude(prompt: str, cwd: str | None = None) -> str:
    proc = subprocess.Popen(
        ["claude", "-p", prompt, "--dangerously-skip-permissions", "--bare"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
    )
    lines = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    proc.wait()
    return "".join(lines)


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
) -> dict:
    run_folder = Path(run_folder)
    logger = OrchestratorLogger(run_folder, project_log_path)

    label = output_suffix or implementation
    logger.log(stage, "INFO", f"dispatching {label}")
    if prompt_file is not None:
        prompt = Path(prompt_file).read_text()
    else:
        prompt = renderer.render_prompt(stage, implementation, variables, docs_root, project)

    output_dir = run_folder / "stages"
    output_dir.mkdir(parents=True, exist_ok=True)
    tag = f"-{output_suffix}" if output_suffix else ""
    (output_dir / f"{stage}{tag}-prompt.md").write_text(prompt)

    t0 = time.monotonic()
    stdout = _run_claude(prompt, cwd=cwd)
    elapsed = time.monotonic() - t0
    (output_dir / f"{stage}{tag}.md").write_text(stdout)

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
        retry_stdout = _run_claude(grace_prompt, cwd=cwd)
        sig = signal_mod.extract_signal(retry_stdout)

    if sig is None:
        logger.log(stage, "ERROR", "signal missing after grace retry — treating as blocked")
        return {"stage": stage, "status": "blocked", "message": "No signal emitted"}

    (output_dir / f"{stage}{tag}.md").write_text(_format_stage_output(stdout, sig))

    validator.validate_output(schema_name if schema_name else stage, sig)
    elapsed_str = _fmt_elapsed(elapsed)
    summary = _signal_summary(sig)
    tag_label = output_suffix or stage
    completion_msg = f"{tag_label} {sig['status']} ({elapsed_str})"
    if summary:
        completion_msg += f" — {summary}"
    logger.log(stage, "INFO", completion_msg)
    for key, value in sig.items():
        v = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
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
        output_dir = run_folder / "stages"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{stage}-prompt.md").write_text(rendered)
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
        args.stage, args.implementation, variables,
        args.run_folder, args.docs_root, args.project, args.project_log_path,
    )
    print(json.dumps(sig, indent=2))
    sys.exit(0 if sig.get("status") == "passed" else 1)
