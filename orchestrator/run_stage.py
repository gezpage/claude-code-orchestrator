import subprocess
from pathlib import Path

from orchestrator import renderer, signal as signal_mod, validator
from orchestrator.logger import OrchestratorLogger

_GRACE_PROMPT = (
    "Your output did not include a SIGNAL_JSON: line. "
    "Please emit one now."
)


def run_stage(
    stage: str,
    implementation: str,
    variables: dict,
    run_folder,
    docs_root: str,
    project: str,
    project_log_path: str,
) -> dict:
    run_folder = Path(run_folder)
    logger = OrchestratorLogger(run_folder, project_log_path)

    prompt = renderer.render_prompt(stage, implementation, variables, docs_root, project)

    output_dir = run_folder / "stage-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{stage}-prompt.txt").write_text(prompt)

    result = subprocess.run(
        ["claude", "-p", prompt, "--dangerously-skip-permissions"],
        capture_output=True,
        text=True,
    )
    stdout = result.stdout
    (output_dir / f"{stage}.txt").write_text(stdout)

    sig = signal_mod.extract_signal(stdout)

    if sig is None:
        logger.log(stage, "DEBUG", "no SIGNAL_JSON found — retrying")
        retry_result = subprocess.run(
            ["claude", "-p", _GRACE_PROMPT, "--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
        )
        sig = signal_mod.extract_signal(retry_result.stdout)

    if sig is None:
        logger.log(stage, "ERROR", "signal missing after grace retry — treating as blocked")
        return {"stage": stage, "status": "blocked", "message": "No signal emitted"}

    validator.validate_output(stage, sig)
    logger.log(stage, "INFO", f"stage {stage} completed with status={sig['status']}")
    return sig


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
