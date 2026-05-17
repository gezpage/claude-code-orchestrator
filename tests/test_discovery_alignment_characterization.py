"""Characterization tests for ADR-032 discovery-emit + alignment-resolve flow.

Pins the invariant that discovery never blocks on its own unresolved items;
``_apply_alignment_policy`` is the sole gate that converts non-empty
``unresolved_remaining`` into a halt, and only under ``on_unresolved=block``.
Tests are config-driven; no branching on profile name.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from orchestrator import orchestrate
from orchestrator._git_setup import GitPreflightResult, OriginInfo
from orchestrator.orchestrate import (
    _apply_alignment_policy,
    _dispatch_tracks,
    _PipelineContext,
)
from orchestrator.profile import AlignmentPolicy, ExpansionKind, StageConfig


@pytest.fixture(autouse=True)
def _stub_preflight_and_sync():
    """ADR-019 / ADR-028 finalisation stubs (mirrors tests/test_orchestrate.py)."""
    with (
        patch(
            "orchestrator.orchestrate._git_setup.preflight",
            return_value=GitPreflightResult(
                base_branch="main",
                create_pr=False,
                origin=OriginInfo(url=None, is_github=False, gh_repo=None),
            ),
        ),
        patch("orchestrator.orchestrate._sync_base_and_create_impl_branch"),
        patch("orchestrator.orchestrate._finalize_summary"),
    ):
        yield


def _make_ctx(tmp_path: Path) -> _PipelineContext:
    return _PipelineContext(
        docs_root=str(tmp_path),
        project="myproject",
        project_log_path=str(tmp_path / "log.log"),
        logger=MagicMock(),
        branch="feat/test",
        project_config={"repo-root": "/tmp"},
        project_standards=[],
        runners={},
        agent_metadata={},
    )


def _setup_docs(tmp_path: Path, stages: list[dict]) -> str:
    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text("repo-root: /tmp\nlog_level: DEBUG\n")
    (tmp_path / "test.yaml").write_text(yaml.dump({"name": "test", "stages": stages}))
    feature_dir = tmp_path / "feature"
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "overview.md").write_text("# Feature Overview\n")
    return str(tmp_path)


def _git_ok() -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    r.stdout = "diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
    return r


def test_discovery_with_unresolved_items_does_not_block_pipeline(tmp_path):
    """Discovery passing with unresolved items must not halt — items are
    alignment inputs, never gates at the discovery stage.
    """
    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    planning = {
        "stage": "discovery-planning",
        "status": "passed",
        "tracks": [{"name": "t1", "prompt_file": "/tmp/p.md", "focus": "x"}],
    }
    track = {
        "stage": "discovery-t1",
        "status": "passed",
        "findings_file": "/tmp/f.md",
        "summary": "ok",
        "unresolved_questions": ["q1", "q2"],
        "risks": ["r1"],
        "assumptions_needed": ["a1"],
    }
    sigs = iter([planning, track])
    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=lambda *a, **k: next(sigs)),
        patch("orchestrator.orchestrate.expand_nodes", return_value={"t1": "n1"}),
        patch("orchestrator.orchestrate.update_plan_md"),
    ):
        sig = _dispatch_tracks(stage, {"run_folder": str(run_folder), "docs_root": str(tmp_path)}, run_folder, ctx)
    assert sig["status"] == "passed"
    assert sig["unresolved_questions"] == ["q1", "q2"]
    assert sig["risks"] == ["r1"]
    assert sig["assumptions_needed"] == ["a1"]


def test_dispatch_tracks_merges_unresolved_lists_across_all_tracks(tmp_path):
    """Per-track unresolved lists are flattened into one merged list per
    category on the parent signal so alignment sees a single input set.
    """
    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    planning = {
        "stage": "discovery-planning",
        "status": "passed",
        "tracks": [
            {"name": "alpha", "prompt_file": "/tmp/a.md", "focus": "x"},
            {"name": "beta", "prompt_file": "/tmp/b.md", "focus": "y"},
        ],
    }
    track_alpha = {
        "stage": "discovery-alpha",
        "status": "passed",
        "findings_file": "/tmp/a.md",
        "summary": "alpha ok",
        "unresolved_questions": ["Which queue backend?"],
        "risks": ["timeout cliff"],
        "assumptions_needed": [],
    }
    track_beta = {
        "stage": "discovery-beta",
        "status": "passed",
        "findings_file": "/tmp/b.md",
        "summary": "beta ok",
        "unresolved_questions": ["Auth scope?"],
        "risks": [],
        "assumptions_needed": ["assume single-tenant"],
    }
    sigs = iter([planning, track_alpha, track_beta])
    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=lambda *a, **k: next(sigs)),
        patch("orchestrator.orchestrate.expand_nodes", return_value={"alpha": "n1", "beta": "n2"}),
        patch("orchestrator.orchestrate.update_plan_md"),
    ):
        sig = _dispatch_tracks(stage, {"run_folder": str(run_folder), "docs_root": str(tmp_path)}, run_folder, ctx)
    # Multi-track runs use a thread pool — order may vary, compare as sets.
    assert sig["status"] == "passed"
    assert set(sig["unresolved_questions"]) == {"Which queue backend?", "Auth scope?"}
    assert set(sig["risks"]) == {"timeout cliff"}
    assert set(sig["assumptions_needed"]) == {"assume single-tenant"}


def test_apply_alignment_policy_default_warn_keeps_passed_status():
    """No AlignmentPolicy → default ``warn``: residue does not flip status."""
    stage = StageConfig(name="alignment")
    sig = {
        "stage": "alignment",
        "status": "passed",
        "unresolved_remaining": ["Decide retry backoff", "Pick queue depth"],
    }
    out = _apply_alignment_policy(stage, sig, MagicMock())
    assert out["status"] == "passed"
    assert out is sig


def test_apply_alignment_policy_block_flips_status_and_preserves_input():
    """Under ``block`` the gate returns a new dict with status=blocked + a
    non-empty message; the input signal stays untouched.
    """
    stage = StageConfig(name="alignment", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {"stage": "alignment", "status": "passed", "unresolved_remaining": ["Decide retry backoff"]}
    out = _apply_alignment_policy(stage, sig, MagicMock())
    assert out is not sig
    assert out["status"] == "blocked"
    assert isinstance(out.get("message"), str) and out["message"]
    assert sig["status"] == "passed"


def test_apply_alignment_policy_treats_whitespace_only_residue_as_resolved():
    """Empty / whitespace-only strings are normalised away; counts as resolved."""
    stage = StageConfig(name="alignment", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {"stage": "alignment", "status": "passed", "unresolved_remaining": ["", "  ", "\t", "\n"]}
    out = _apply_alignment_policy(stage, sig, MagicMock())
    assert out["status"] == "passed"
    assert out is sig


def test_pipeline_halts_under_alignment_policy_block(tmp_path):
    """End-to-end: ``on_unresolved=block`` + a passing alignment signal with
    residue halts the pipeline at alignment; specification never runs.
    """
    stages: list[dict] = [
        {
            "stage": "alignment",
            "prompt": "prompts/alignment/autonomous.md",
            "alignment_policy": {"on_unresolved": "block"},
        },
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder.mkdir(parents=True)

    alignment_sig = {
        "stage": "alignment",
        "status": "passed",
        "alignment_log": "/tmp/align.md",
        "qa_pair_count": 1,
        "qualifying_decisions": 0,
        "accepted_assumptions": [],
        "unresolved_remaining": ["Pick retry strategy"],
    }
    seen_stages: list[str] = []

    def fake_run_stage(stage, *args, **kwargs):
        seen_stages.append(stage)
        if stage == "alignment":
            return alignment_sig
        return {"stage": stage, "status": "passed"}

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md") as mock_plan,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert exc_info.value.code == 1
    assert "specification" not in seen_stages
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("alignment", "blocked") in plan_calls
    state = yaml.safe_load((run_folder / "_state.yaml").read_text())
    assert state.get("blocked_at") == "alignment"
