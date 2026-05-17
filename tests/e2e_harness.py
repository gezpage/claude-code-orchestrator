"""Reusable harness for end-to-end orchestrator tests.

The harness patches `run_stage()` itself — not `_run_claude` — so e2e tests
contain no per-stage signal dicts and no prompt-heading parsing. Each call to
the fake run-stage:

  - Loads the stage's JSON schema from `orchestrator/schemas/`.
  - Synthesises a minimum passing signal: every `*_path`/`*_file` string is
    materialised as a real stub file under `{run_folder}/{stage}/`, enums
    default to their first allowed value, and a small convention table fills
    structured fields the schema can't describe (tracks, slice_files,
    commit_hashes, reviewer_statuses, branch).
  - Writes the same `{stage}{tag}-prompt.md` and `{stage}{tag}-output.md` the
    real `run_stage()` writes, so glob-based assertions keep working.
  - Returns the signal dict.

Tests express divergences from happy-path via `overrides`, keyed by stage call:

    "discovery-planning" | "discovery-track" | "specification" |
    "decomposition" | "implementation" | "qa" | "harvest" |
    "fix-implementation" | "review:<reviewer>:r<N>"

Each override is either a partial signal dict (shallow-merged onto the
synthesised default) or a callable `(default_sig, ctx) -> dict`. If the
override changes `reviewer_statuses` but not `changes_requested`, the latter
is recomputed automatically.

Set `ORCH_E2E_OUTPUT_DIR=/path` before pytest to pin run artefacts to a
stable location for inspection. The directory is wiped on each invocation.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import orchestrator

_OUTPUT_DIR_ENV = "ORCH_E2E_OUTPUT_DIR"
_SCHEMA_DIR = Path(orchestrator.__file__).parent / "schemas"


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
    # Default stdout is a minimal git diff so the orchestrator's diff validator accepts
    # the synthesised pipeline. Harmless for non-diff subprocess calls (they ignore stdout).
    r.stdout = "diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
    return r


def setup_docs(out_dir: Path, feature_path: str = "projects/myproject/features/demo") -> tuple[str, str]:
    project_dir = out_dir / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(f"repo-root: {out_dir}\nlog_level: DEBUG\n")
    feature_dir = out_dir / feature_path
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "overview.md").write_text("# Demo feature\n")
    return str(out_dir), feature_path


def pre_create_alignment(run_folder: Path) -> None:
    """Make the interactive alignment stage auto-pass via _dispatch_interactive."""
    (run_folder / "alignment").mkdir(parents=True, exist_ok=True)
    (run_folder / "alignment" / "alignment-log.md").write_text("# Alignment\n")


def _load_schema(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_SCHEMA_DIR / f"{name}.json").read_text())
    return data


def _route_key(stage: str, implementation: str, schema_name: str | None, variables: Mapping[str, Any]) -> str:
    """Compute the override key for a run_stage call.

    The mapping mirrors the orchestrator's dispatch decisions, not the prompt
    text, so renaming a prompt heading has zero impact on tests.
    """
    if schema_name == "discovery_planning":
        return "discovery-planning"
    if schema_name == "discovery_track":
        return "discovery-track"
    if stage == "review":
        rnd = variables.get("round", "1")
        return f"review:{implementation}:r{rnd}"
    return stage


def _synthesise(
    schema: dict[str, Any],
    *,
    stage_name: str,
    implementation: str,
    output_dir: Path,
    tag: str,
    variables: Mapping[str, Any],
    call_idx: int,
) -> dict[str, Any]:
    """Build a minimum passing signal for `schema`. Writes any path-shaped files."""
    sig: dict[str, Any] = {"stage": stage_name, "status": "passed"}
    props = schema.get("properties", {})

    for name, spec in props.items():
        if name in sig or name == "message":
            continue
        typ = spec.get("type")
        if typ == "string":
            if name.endswith(("_path", "_file")):
                p = output_dir / f"{name}{tag}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"# {name}{tag}\n")
                sig[name] = str(p)
            elif "enum" in spec:
                sig[name] = spec["enum"][0]
        elif typ == "array":
            sig[name] = []
        elif typ == "object":
            sig[name] = {}

    # Schema can describe field types but not stage semantics. The blocks below
    # are convention-driven: keyed on field name, not stage name, so they kick
    # in for any future stage whose schema declares the same field.
    if "tracks" in props:
        # Three tracks (vs the historical one) so the rendered discovery
        # fan-out exercises the parallel-dispatch path the orchestrator takes
        # whenever a planning agent returns 2+ tracks.
        tracks = []
        for track_name in ("track-a", "track-b", "track-c"):
            prompt_file = output_dir / f"{track_name}-prompt{tag}.md"
            prompt_file.write_text(f"# Discovery Track — {track_name}\n\nGather context.\n")
            tracks.append({"name": track_name, "prompt_file": str(prompt_file), "focus": "auto"})
        sig["tracks"] = tracks

    if "slice_files" in props:
        sf_paths = []
        for i in (1, 2):
            p = output_dir / f"S-{i:02d}-auto.md"
            p.write_text(f"# S-{i:02d} auto slice\n")
            sf_paths.append(str(p))
        sig["slice_files"] = sf_paths
        # Emit one parallel wave containing both slices so the rendered
        # diagram exercises the slice fan-out path (vs sequential default).
        if "slice_groups" in props:
            sig["slice_groups"] = [sf_paths]

    if "commit_hashes" in props:
        sig["commit_hashes"] = [f"c0mm{call_idx + 1:04d}"]

    if "branch" in props:
        sig["branch"] = str(variables.get("branch", "feat/test"))

    if "reviewer_statuses" in props:
        sig["reviewer_statuses"] = {implementation: "approved"}
        sig["changes_requested"] = []

    return sig


Override = dict[str, Any] | Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _apply_override(default: dict[str, Any], override: Override, ctx: dict[str, Any]) -> dict[str, Any]:
    if callable(override):
        return override(default, ctx)
    out = dict(default)
    out.update(override)
    # If a test overrode reviewer_statuses without spelling out changes_requested,
    # recompute it so the orchestrator's fix-cycle trigger sees a consistent signal.
    if "reviewer_statuses" in override and "changes_requested" not in override:
        out["changes_requested"] = [r for r, v in out["reviewer_statuses"].items() if v == "changes-requested"]
    return out


class FakeRunStage:
    """Callable that replaces `orchestrator.run_stage.run_stage` in e2e tests."""

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self.overrides: dict[str, Any] = dict(overrides or {})
        self.call_counts: dict[str, int] = defaultdict(int)
        self.call_count: int = 0

    def __call__(
        self,
        stage: str,
        implementation: str,
        variables: dict,
        run_folder: Any,
        docs_root: str,
        project: str,
        project_log_path: str,
        output_suffix: str = "",
        cwd: str | None = None,
        prompt_file: str | None = None,
        schema_name: str | None = None,
        standards: list[str] | None = None,
        runner: Any = None,
    ) -> dict[str, Any]:
        run_folder = Path(run_folder)
        output_dir = run_folder / stage
        output_dir.mkdir(parents=True, exist_ok=True)
        tag = f"-{output_suffix}" if output_suffix else ""

        key = _route_key(stage, implementation, schema_name, variables)
        idx = self.call_counts[key]
        self.call_counts[key] += 1
        self.call_count += 1

        schema = _load_schema(schema_name or stage)
        sig = _synthesise(
            schema,
            stage_name=stage,
            implementation=implementation,
            output_dir=output_dir,
            tag=tag,
            variables=variables,
            call_idx=idx,
        )

        if key in self.overrides:
            ctx = {
                "stage": stage,
                "implementation": implementation,
                "variables": variables,
                "run_folder": run_folder,
                "call_idx": idx,
            }
            sig = _apply_override(sig, self.overrides[key], ctx)

        (output_dir / f"{stage}{tag}-prompt.md").write_text(f"# {stage}{tag} (synthesised)\n")
        body = f"# {stage}{tag} (synthesised)\n\n```json\n{json.dumps(sig, indent=2)}\n```\n"
        (output_dir / f"{stage}{tag}-output.md").write_text(body)

        return sig


@contextlib.contextmanager
def patch_run_stage(overrides: Mapping[str, Any] | None = None) -> Iterator[FakeRunStage]:
    """Patch `run_stage` at both binding sites (orchestrate and review_cycle).

    Also stubs the orchestrator._git state validators with safe defaults so e2e
    tests that don't initialise a real git repo can still exercise the slice
    dispatcher.
    """
    fake = FakeRunStage(overrides)
    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake),
        patch("orchestrator.review_cycle.run_stage", side_effect=fake),
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=True),
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False),
        patch("orchestrator.orchestrate.git_state.current_branch", return_value="main"),
        patch("orchestrator.orchestrate.git_state.worktree_registered", return_value=False),
        patch("orchestrator.orchestrate.git_state.list_worktrees", return_value=[]),
        patch("orchestrator.orchestrate.git_state.worktree_for_branch", return_value=None),
        patch("orchestrator.orchestrate.git_state.has_merge_conflicts", return_value=False),
        patch("orchestrator.orchestrate.git_state.abort_merge"),
    ):
        yield fake
