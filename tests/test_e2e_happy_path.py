"""End-to-end happy-path test for the bundled `full` profile.

Mocks only the single `claude` subprocess call (`_run_claude`) and lets every
other layer — prompt rendering, schema validation, signal extraction, state
persistence, plan.md rendering, fan-out dispatchers — run for real. Stage is
identified by the rendered prompt's heading; canned signals satisfy each
stage's JSON schema and carry the fields downstream stages depend on.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from orchestrator import orchestrate

_OUTPUT_DIR_ENV = "ORCH_E2E_OUTPUT_DIR"


def _resolve_output_dir(tmp_path: Path) -> Path:
    """Honour ORCH_E2E_OUTPUT_DIR if set, otherwise fall back to pytest's tmp_path.

    The override is wiped on each run so the test starts from a clean slate
    (matching tmp_path's contract). Set the env var to inspect run artefacts
    at a stable path:  ORCH_E2E_OUTPUT_DIR=/tmp/orch-e2e uv run pytest ...
    """
    override = os.environ.get(_OUTPUT_DIR_ENV)
    if not override:
        return tmp_path
    out = Path(override).expanduser().resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    print(f"\n[e2e] writing run artefacts under {out}", file=sys.stderr)
    return out


def _git_ok():
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    r.stdout = ""
    return r


def _setup_docs(tmp_path: Path) -> tuple[str, Path]:
    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    # repo-root must exist on disk; tmp_path itself is a valid directory.
    (project_dir / "project.yaml").write_text(f"repo-root: {tmp_path}\nlog_level: DEBUG\n")
    feature_path = "projects/myproject/features/demo"
    feature_dir = tmp_path / feature_path
    feature_dir.mkdir(parents=True)
    (feature_dir / "overview.md").write_text("# Demo feature\n")
    return str(tmp_path), Path(feature_path)


def _make_fake_run_claude(tmp_path: Path, track_prompt_path: Path):
    impl_calls = {"n": 0}

    def fake(prompt: str, cwd: str | None = None) -> str:
        first_line = prompt.lstrip().splitlines()[0] if prompt.strip() else ""

        if "Discovery Planning" in first_line:
            sig = {
                "stage": "discovery-planning",
                "status": "passed",
                "tracks": [
                    {
                        "name": "track-a",
                        "prompt_file": str(track_prompt_path),
                        "focus": "primary modules",
                    }
                ],
            }
        elif first_line.startswith("# Discovery Track"):
            sig = {
                "stage": "discovery",
                "status": "passed",
                "findings_file": str(tmp_path / "findings-track-a.md"),
                "summary": "found three entry points",
            }
        elif "Specification Stage" in first_line:
            sig = {
                "stage": "specification",
                "status": "passed",
                "prd_path": str(tmp_path / "prd.md"),
                "context_path": str(tmp_path / "context.md"),
                "adr_paths": [],
            }
        elif "Decomposition Stage" in first_line:
            sig = {
                "stage": "decomposition",
                "status": "passed",
                "slice_files": ["S-01-foo.md", "S-02-bar.md"],
            }
        elif "Implementation Stage" in first_line:
            impl_calls["n"] += 1
            sig = {
                "stage": "implementation",
                "status": "passed",
                "commit_hashes": [f"c0mmit{impl_calls['n']:02d}"],
                "branch": "feat/test",
            }
        elif "QA Stage" in first_line:
            sig = {
                "stage": "qa",
                "status": "passed",
                "outcome": "pass",
                "confidence": "high",
                "regression_risk": "low",
            }
        elif "Review Stage" in first_line:
            m = re.search(r"Review Stage\s+[—-]+\s+(\w+)\s+Reviewer", first_line)
            reviewer = m.group(1).lower() if m else "unknown"
            sig = {
                "stage": "review",
                "status": "passed",
                "reviewer_statuses": {reviewer: "approved"},
                "changes_requested": [],
            }
        elif "Harvest Stage" in first_line:
            sig = {
                "stage": "harvest",
                "status": "passed",
                "kb_files": [],
                "adr_files": [],
            }
        else:
            raise AssertionError(f"unrecognised prompt heading: {first_line!r}")

        return f"SIGNAL_JSON: {json.dumps(sig)}\n"

    return fake


def test_full_profile_e2e_happy_path(tmp_path):
    out_dir = _resolve_output_dir(tmp_path)
    docs_root, feature_path = _setup_docs(out_dir)

    run_folder = (
        out_dir / "projects" / "myproject" / "workflow" / "runs" / "demo" / "2026-05-14-run-1"
    )
    # Pre-create the alignment artifact so the interactive stage auto-passes
    # (no run_interactive_stage invocation, per orchestrate._dispatch_interactive).
    (run_folder / "alignment").mkdir(parents=True)
    (run_folder / "alignment" / "alignment-log.md").write_text("# Alignment\n")

    # Discovery tracks are dispatched via prompt_file=<path>, which run_stage
    # reads from disk — so a real file with a recognisable heading is needed.
    track_prompt = out_dir / "track-a-prompt.md"
    track_prompt.write_text("# Discovery Track — track-a\n\nGather context.\n")

    fake_run_claude = _make_fake_run_claude(out_dir, track_prompt)

    with (
        patch("orchestrator.run_stage._run_claude", side_effect=fake_run_claude) as mock_claude,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(
            docs_root,
            project="myproject",
            feature_path=str(feature_path),
            branch="feat/test",
            profile_name="full",
        )

    # --- state reached terminal stage cleanly ---
    state = yaml.safe_load((run_folder / "_state.yaml").read_text())
    assert "blocked_at" not in state
    stages = state.get("stages", {})
    for name in (
        "discovery",
        "alignment",
        "specification",
        "decomposition",
        "implementation",
        "qa",
        "review",
        "harvest",
    ):
        assert stages.get(name) == "passed", f"{name} did not pass (got {stages.get(name)!r})"

    # --- signals captured the contract fields downstream stages rely on ---
    signals = state.get("signals", {})
    assert signals["discovery"]["findings_files"], "discovery did not surface findings_files"
    assert signals["specification"]["prd_path"]
    assert signals["specification"]["context_path"]
    assert len(signals["decomposition"]["slice_files"]) == 2
    assert len(signals["implementation"]["commit_hashes"]) == 2
    assert signals["qa"]["outcome"] == "pass"
    assert signals["review"]["reviewer_statuses"] == {
        "architecture": "approved",
        "implementation": "approved",
        "tests": "approved",
    }
    assert signals["review"]["changes_requested"] == []

    # planning + 1 track + spec + decomp + 2 impl + qa + 3 reviewers + harvest = 11.
    # alignment is interactive and pre-skipped, so it does not invoke _run_claude.
    assert mock_claude.call_count == 11

    # --- on-disk artefacts written by run_stage and dispatchers ---
    assert (run_folder / "plan.md").exists()
    plan_md = (run_folder / "plan.md").read_text().lower()
    for name in ("discovery", "alignment", "specification", "decomposition", "harvest"):
        assert name in plan_md

    impl_outputs = sorted((run_folder / "implementation").glob("implementation-*-output.md"))
    assert len(impl_outputs) == 2  # one per slice

    review_outputs = sorted((run_folder / "review").glob("review-*-output.md"))
    assert {p.name for p in review_outputs} == {
        "review-architecture-output.md",
        "review-implementation-output.md",
        "review-tests-output.md",
    }

    # Review-cycle artefacts must NOT exist on the happy path.
    assert not any((run_folder / "review").glob("fix-implementation-*.md"))
