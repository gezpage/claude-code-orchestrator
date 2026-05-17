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
  - For stages whose downstream consumers read specific headings or markers,
    a small per-stage writer overwrites the stub with the contract-shaped
    content (e.g. PRD/context section headings, slice file bodies, an
    implementation plan for the minimal decomposition flow, a glossary term
    that round-trips through `orchestrator.glossary.reconcile`). The intent is
    contract fidelity — not simulated agent prose.
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


_SPEC_PRD_TEMPLATE = """\
# PRD

## Problem Statement

Synthesised stub problem statement.

## Goals

- goal 1
- goal 2

## Non-Goals

- non-goal 1

## Success Criteria

- criterion 1

## Constraints

- constraint 1

## Out of Scope

- out-of-scope 1
"""

_SPEC_CONTEXT_TEMPLATE = """\
# Context

## Quality Bar and Standards

Synthesised stub quality bar.

## Standing Constraints

- constraint 1

## Domain Context

Synthesised stub domain context.

## Decisions

### Stub decision

**Decision:** stub
**Rationale:** stub
**Consequences:** stub

## Assumptions

- assumption 1
"""

_SLICE_TEMPLATE = """\
# {slice_id}: auto slice

## What to build

Synthesised stub slice {slice_id}.

## Acceptance criteria

- criterion 1
- criterion 2

## Blocked by

- None — can start immediately
"""

_MINIMAL_PLAN_TEMPLATE = """\
# Implementation plan

## Non-negotiable constraints

- constraint 1

## Architectural invariants

- invariant 1

## Quality bar expectations

Synthesised stub quality bar.

## Acceptance criteria

- criterion 1

## Testing expectations

- unit
- integration

## Build order

1. step 1

## Known risks / ambiguities

None.
"""

_REVIEW_TEMPLATE = """\
# Review

## Findings

- (none)

## Non-blocking findings

- (none)
"""


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
    """Build a minimum passing signal for `schema`. Writes any path-shaped files.

    Stub files materialised for ``*_path`` / ``*_file`` schema fields are then
    overwritten by per-stage writers with the headings/markers downstream
    consumers (and human inspectors) actually expect — never realistic prose.
    The goal is contract-shaped fakes, not simulated agent output.
    """
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

    # Decomposition has two flows. The default slice-flow emits slice_files +
    # slice_groups; the minimal flow emits a single plan_file. Selecting on
    # `implementation` here replaces the per-test `_decomposition_override`
    # shim that minimal-profile tests previously had to supply.
    is_minimal_decomp = stage_name == "decomposition" and implementation == "minimal"
    if "slice_files" in props and not is_minimal_decomp:
        sf_paths: list[str] = []
        sf_inputs: list[list[str]] = []
        for i in (1, 2):
            p = output_dir / f"S-{i:02d}-auto.md"
            p.write_text(_SLICE_TEMPLATE.format(slice_id=f"S-{i:02d}"))
            sf_paths.append(str(p))
            sf_inputs.append([str(p)])
        sig["slice_files"] = sf_paths
        # Emit one parallel wave containing both slices so the rendered
        # diagram exercises the slice fan-out path (vs sequential default).
        if "slice_groups" in props:
            sig["slice_groups"] = [sf_paths]
        if "slice_inputs" in props:
            sig["slice_inputs"] = sf_inputs
    elif is_minimal_decomp:
        # Drop the auto-stub plan_file path the schema loop created — minimal
        # writes to a fixed name, not the field-name-derived stub.
        sig.pop("plan_file", None)
        plan_path = output_dir / "implementation-plan.md"
        plan_path.write_text(_MINIMAL_PLAN_TEMPLATE)
        sig["plan_file"] = str(plan_path)
        # Remove any stray auto-stub slice files left over from a previous call.
        for stub in output_dir.glob("S-*.md"):
            stub.unlink()

    if "commit_hashes" in props:
        sig["commit_hashes"] = [f"c0mm{call_idx + 1:04d}"]

    if "branch" in props:
        sig["branch"] = str(variables.get("branch", "feat/test"))

    if "reviewer_statuses" in props:
        sig["reviewer_statuses"] = {implementation: "approved"}
        sig["changes_requested"] = []

    _enrich_stub_content(sig, stage_name=stage_name)

    return sig


def _enrich_stub_content(sig: dict[str, Any], *, stage_name: str) -> None:
    """Overwrite path-shaped stubs with contract-shaped content.

    Writers are stage-keyed and contract-oriented: headings/markers downstream
    consumers actually read, plus a glossary payload that round-trips through
    `orchestrator.glossary.reconcile`. Deliberately not a registry — adding a
    new ecosystem-style hook would push this past the lightweight-fake remit.
    """
    if stage_name == "specification":
        for field, body in (("prd_path", _SPEC_PRD_TEMPLATE), ("context_path", _SPEC_CONTEXT_TEMPLATE)):
            path = sig.get(field)
            if isinstance(path, str):
                Path(path).write_text(body)
    elif stage_name == "review":
        path = sig.get("review_md")
        if isinstance(path, str):
            Path(path).write_text(_REVIEW_TEMPLATE)
    elif stage_name == "harvest" and "proposed_glossary_terms" in sig:
        # One term, formatted to match what `glossary.reconcile` expects so a
        # configured `domain_language.path` exercises the append-only path.
        sig["proposed_glossary_terms"] = {
            "Synthesised Term": "A stub glossary term produced by the e2e harness for reconciliation tests.",
        }


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
        inputs: list[str] | None = None,
        node_id: str | None = None,
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
