"""Reusable harness for end-to-end orchestrator tests.

Mocks only `orchestrator.run_stage._run_claude` and routes each call to a
test-supplied response based on which stage (and for reviews, which reviewer
and round) the rendered prompt represents. Everything else — prompt rendering,
schema validation, signal extraction, state persistence, plan.md rendering,
fan-out dispatchers, review-cycle loop — runs for real.

# Stage keys

`stage_key()` returns one of:
  - "discovery-planning"
  - "discovery-track"
  - "specification"
  - "decomposition"
  - "implementation"            (called once per slice)
  - "qa"
  - "fix-implementation"        (called once per review cycle)
  - "review:<reviewer>:r<N>"    (e.g. "review:architecture:r1")
  - "harvest"

Provide a signal dict for every key the scenario will hit. The harness merges
test-supplied `overrides` over `default_signals()` so most scenarios only need
to specify the keys that diverge from the happy path.

# Signal values

Each `signals` value may be:
  - a dict  — returned every time the key is hit
  - a list[dict] — returned in order; raises if exhausted
  - a callable(prompt, call_idx) -> dict — full control

# Output dir override

Set `ORCH_E2E_OUTPUT_DIR=/path` before pytest to pin run artefacts to a stable
location for inspection. The directory is wiped on each invocation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

_OUTPUT_DIR_ENV = "ORCH_E2E_OUTPUT_DIR"


def resolve_output_dir(tmp_path: Path) -> Path:
    override = os.environ.get(_OUTPUT_DIR_ENV)
    if not override:
        return tmp_path
    out = Path(override).expanduser().resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    print(f"\n[e2e] writing run artefacts under {out}", file=sys.stderr)  # noqa: T201
    return out


def git_ok() -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    r.stdout = ""
    return r


def setup_docs(out_dir: Path, feature_path: str = "projects/myproject/features/demo") -> tuple[str, str]:
    project_dir = out_dir / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(f"repo-root: {out_dir}\nlog_level: DEBUG\n")
    feature_dir = out_dir / feature_path
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "overview.md").write_text("# Demo feature\n")
    return str(out_dir), feature_path


def write_track_prompt(out_dir: Path, name: str = "track-a") -> Path:
    """Discovery tracks are dispatched via prompt_file=<path>, which run_stage
    reads from disk — so a real file with a recognisable heading is needed.
    """
    p = out_dir / f"{name}-prompt.md"
    p.write_text(f"# Discovery Track — {name}\n\nGather context.\n")
    return p


def pre_create_alignment(run_folder: Path) -> None:
    """Make the interactive alignment stage auto-pass via _dispatch_interactive."""
    (run_folder / "alignment").mkdir(parents=True, exist_ok=True)
    (run_folder / "alignment" / "alignment-log.md").write_text("# Alignment\n")


def stage_key(prompt: str) -> str:
    first = prompt.lstrip().splitlines()[0] if prompt.strip() else ""

    if "Discovery Planning" in first:
        return "discovery-planning"
    if first.startswith("# Discovery Track"):
        return "discovery-track"
    if "Specification Stage" in first:
        return "specification"
    if "Decomposition Stage" in first:
        return "decomposition"
    if "Implementation Stage" in first:
        return "implementation"
    if "QA Stage" in first:
        return "qa"
    if "Fix Implementation" in first:
        return "fix-implementation"
    if "Harvest Stage" in first:
        return "harvest"
    if "Review Stage" in first:
        m = re.search(r"Review Stage\s+[—-]+\s+(\w+)\s+Reviewer", first)
        reviewer = m.group(1).lower() if m else "unknown"
        rm = re.search(r"\*\*Round:\*\*\s+(\d+)", prompt)
        round_n = rm.group(1) if rm else "1"
        return f"review:{reviewer}:r{round_n}"

    raise AssertionError(f"unrecognised prompt heading: {first!r}")


def reviewer_signal(reviewer: str, verdict: str, findings: list[str] | None = None) -> dict:
    sig: dict[str, Any] = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {reviewer: verdict},
        "changes_requested": [reviewer] if verdict == "changes-requested" else [],
    }
    if findings:
        sig["findings"] = findings
    return sig


def default_signals(out_dir: Path, track_prompt: Path) -> dict[str, Any]:
    """Happy-path signal map. Reviewer keys here cover round 1 only; tests that
    drive the review fix cycle must supply `review:<r>:r2` (and any further
    cycles) explicitly.
    """
    return {
        "discovery-planning": {
            "stage": "discovery-planning",
            "status": "passed",
            "tracks": [
                {"name": "track-a", "prompt_file": str(track_prompt), "focus": "primary modules"},
            ],
        },
        "discovery-track": {
            "stage": "discovery",
            "status": "passed",
            "findings_file": str(out_dir / "findings-track-a.md"),
            "summary": "found three entry points",
        },
        "specification": {
            "stage": "specification",
            "status": "passed",
            "prd_path": str(out_dir / "prd.md"),
            "context_path": str(out_dir / "context.md"),
            "adr_paths": [],
        },
        "decomposition": {
            "stage": "decomposition",
            "status": "passed",
            "slice_files": ["S-01-foo.md", "S-02-bar.md"],
        },
        # Each implementation call gets a unique commit hash via the callable.
        "implementation": lambda prompt, idx: {
            "stage": "implementation",
            "status": "passed",
            "commit_hashes": [f"c0mmit{idx + 1:02d}"],
            "branch": "feat/test",
        },
        "qa": {
            "stage": "qa",
            "status": "passed",
            "outcome": "pass",
            "confidence": "high",
            "regression_risk": "low",
        },
        "review:architecture:r1": reviewer_signal("architecture", "approved"),
        "review:implementation:r1": reviewer_signal("implementation", "approved"),
        "review:tests:r1": reviewer_signal("tests", "approved"),
        "fix-implementation": {
            "stage": "fix-implementation",
            "status": "passed",
            "commit_hashes": ["f1xc0mm1t"],
            "diff": "",
        },
        "harvest": {
            "stage": "harvest",
            "status": "passed",
            "kb_files": [],
            "adr_files": [],
        },
    }


def make_fake_run_claude(signals: Mapping[str, Any]) -> Callable[[str, str | None], str]:
    """Build a fake _run_claude that routes by stage_key."""
    call_counts: dict[str, int] = {}

    def fake(prompt: str, cwd: str | None = None) -> str:
        key = stage_key(prompt)
        spec = signals.get(key)
        if spec is None:
            raise AssertionError(f"no signal configured for stage key {key!r}")
        idx = call_counts.get(key, 0)
        call_counts[key] = idx + 1

        if callable(spec):
            sig = spec(prompt, idx)
        elif isinstance(spec, list):
            if idx >= len(spec):
                raise AssertionError(f"stage key {key!r} called {idx + 1} times but only {len(spec)} signals provided")
            sig = dict(spec[idx])
        else:
            sig = dict(spec)

        return f"SIGNAL_JSON: {json.dumps(sig)}\n"

    fake.call_counts = call_counts  # type: ignore[attr-defined]
    return fake


def merge_signals(base: dict[str, Any], overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if overrides:
        merged.update(overrides)
    return merged
