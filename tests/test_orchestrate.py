import json
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest
import yaml

from orchestrator import orchestrate
from orchestrator._git import GitStateError
from orchestrator._git_setup import GitPreflightResult, OriginInfo
from orchestrator.orchestrate import _finalize_summary as _real_finalize_summary
from orchestrator.orchestrate import _PipelineContext
from orchestrator.profile import ExpansionKind, StageConfig


@pytest.fixture(autouse=True)
def _stub_preflight_and_sync():
    """Stub the ADR-019 preflight, base-branch sync, and ADR-028 executive summary
    finalisation for every test in this file.

    Tests that explicitly exercise these paths re-patch within their own `with`
    block; autouse means the rest of the suite is unaffected by the finalisation
    machinery.
    """
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


# ── helpers ──────────────────────────────────────────────────────────────────


def _setup_docs(tmp_path, stages, profile_name="test", feature_path="feature"):
    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text("repo-root: /tmp\nlog_level: DEBUG\n")
    profile_path = tmp_path / f"{profile_name}.yaml"
    profile_path.write_text(yaml.dump({"name": profile_name, "stages": stages}))
    feature_dir = tmp_path / feature_path
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "overview.md").write_text("# Feature Overview\n")
    return str(tmp_path)


def _git_ok():
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    # Default stdout is a minimal git diff so the orchestrator's diff validator accepts
    # the synthesised pipeline. Harmless for non-diff subprocess calls (they ignore stdout).
    r.stdout = "diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
    return r


def _patch_safe_git_state():
    """Return a contextlib.ExitStack with patches that make _git validators report a
    clean repo on a fresh branch. Use in integration tests that exercise the slice
    dispatcher without explicitly testing git state validation.

    Also stubs the new preflight and base-branch sync added in ADR-019 so tests
    don't need to know about gh/origin discovery.
    """
    import contextlib

    from orchestrator._git_setup import GitPreflightResult, OriginInfo

    stack = contextlib.ExitStack()
    stack.enter_context(patch("orchestrator.orchestrate.git_state.is_clean", return_value=True))
    stack.enter_context(patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False))
    stack.enter_context(patch("orchestrator.orchestrate.git_state.current_branch", return_value="main"))
    stack.enter_context(patch("orchestrator.orchestrate.git_state.worktree_registered", return_value=False))
    stack.enter_context(patch("orchestrator.orchestrate.git_state.has_merge_conflicts", return_value=False))
    stack.enter_context(patch("orchestrator.orchestrate.git_state.abort_merge"))
    stack.enter_context(
        patch(
            "orchestrator.orchestrate._git_setup.preflight",
            return_value=GitPreflightResult(
                base_branch="main",
                create_pr=False,
                origin=OriginInfo(url=None, is_github=False, gh_repo=None),
            ),
        )
    )
    stack.enter_context(patch("orchestrator.orchestrate._sync_base_and_create_impl_branch"))
    return stack


DISCOVERY_PLANNING_SIGNAL = {
    "stage": "discovery-planning",
    "status": "passed",
    "tracks": [
        {
            "name": "code-entry-points",
            "prompt_file": "/tmp/stages/discovery-code-entry-points-prompt.md",
            "focus": "Identify relevant modules and call paths",
        }
    ],
}

DISCOVERY_TRACK_SIGNAL = {
    "stage": "discovery-code-entry-points",
    "status": "passed",
    "findings_file": "/tmp/discovery-code-entry-points.md",
    "summary": "Found 3 relevant entry points in the auth module.",
}

# Convenience alias for tests that just need discovery to pass
DISCOVERY_SIGNAL = {
    "stage": "discovery",
    "status": "passed",
    "tracks": [
        {
            "name": "code-entry-points",
            "summary": "Found 3 relevant entry points in the auth module.",
            "findings_file": "/tmp/discovery-code-entry-points.md",
        }
    ],
    "findings_files": ["/tmp/discovery-code-entry-points.md"],
}

SPEC_SIGNAL = {
    "stage": "specification",
    "status": "passed",
    "prd_path": "/tmp/prd.md",
    "context_path": "/tmp/ctx.md",
    "adr_paths": [],
}

DECOMP_SIGNAL = {
    "stage": "decomposition",
    "status": "passed",
    "slice_files": ["S-01-slice.md", "S-02-slice.md"],
}

IMPL_SIGNAL = {
    "stage": "implementation",
    "status": "passed",
    "commit_hashes": ["abc123"],
    "branch": "feat/test",
}

QA_SIGNAL = {
    "stage": "qa",
    "status": "passed",
    "outcome": "pass",
    "confidence": "high",
    "regression_risk": "low",
}

REVIEW_ARCH_SIGNAL = {
    "stage": "review",
    "status": "passed",
    "reviewer_statuses": {"architecture": "passed"},
    "changes_requested": [],
}

HARVEST_SIGNAL = {
    "stage": "harvest",
    "status": "passed",
    "kb_files": [],
    "adr_files": [],
}

BLOCKED_SIGNAL = {
    "stage": "discovery",
    "status": "blocked",
    "message": "Could not discover anything",
}


# ── full happy path ───────────────────────────────────────────────────────────


def test_full_happy_path(tmp_path):
    stages = [
        {"stage": "discovery", "expansion": "tracks"},
        {
            "stage": "alignment",
            "mode": "interactive",
            "artifact": "alignment-log.md",
            "prompt": "prompts/alignment/interactive.md",
        },
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
        {"stage": "decomposition", "prompt": "prompts/decomposition/default.md"},
        {
            "stage": "implementation",
            "prompt": "prompts/implementation/default.md",
            "expansion": "slices",
            "slices_from_stage": "decomposition",
        },
        {"stage": "qa", "prompt": "prompts/qa/default.md"},
        {
            "stage": "review",
            "expansion": "prompts",
            "prompts": {"architecture": "prompts/review/architecture.md"},
        },
        {"stage": "harvest", "prompt": "prompts/harvest/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages, feature_path="feature-xyz")

    # Create alignment-log.md so alignment auto-skips (simulates resumed run)
    runs_base = tmp_path / "projects" / "myproject" / "workflow" / "runs"

    stage_signals = [
        DISCOVERY_PLANNING_SIGNAL,  # discovery — planning phase
        DISCOVERY_TRACK_SIGNAL,  # discovery — single track (parallel dispatch)
        SPEC_SIGNAL,
        DECOMP_SIGNAL,
        IMPL_SIGNAL,  # called twice (2 slices)
        IMPL_SIGNAL,
        QA_SIGNAL,
        REVIEW_ARCH_SIGNAL,
        HARVEST_SIGNAL,
    ]
    signal_iter = iter(stage_signals)

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        if stage == "review":
            assert "review_md" in variables
            assert "diff" in variables
            assert variables["round"] == "1"
        return next(signal_iter)

    git_mock = MagicMock(return_value=_git_ok())

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage) as mock_rs,
        patch("orchestrator.orchestrate.run_interactive_stage") as mock_ris,
        patch("orchestrator.orchestrate.update_plan_md") as mock_plan,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        _patch_safe_git_state(),
    ):
        # alignment-log.md must exist inside the actual run folder; patch resolve to a known path
        run_folder_path = runs_base / "feature-xyz" / "2026-01-01-run-1"
        run_folder_path.mkdir(parents=True)
        (run_folder_path / "alignment").mkdir()
        (run_folder_path / "alignment" / "alignment-log.md").write_text("# Alignment\n")

        with patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
            orchestrate.run_pipeline(docs_root, "myproject", "feature-xyz", "feat/test", str(tmp_path / "test.yaml"))

    # run_stage called for: discovery-planning, discovery-track, specification, decomposition,
    # 2x implementation, qa, review, harvest = 9
    assert mock_rs.call_count == 9

    # alignment auto-skipped (artifact existed) — run_stage and run_interactive_stage not called
    all_stages_called = [c.args[0] for c in mock_rs.call_args_list]
    assert "alignment" not in all_stages_called
    mock_ris.assert_not_called()

    # plan.md updated with (stage, status) tuples — check key milestones
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "passed") in plan_calls
    assert ("alignment", "passed") in plan_calls
    assert ("harvest", "passed") in plan_calls
    # implementation sub-nodes updated
    assert ("impl_1", "passed") in plan_calls
    assert ("impl_2", "passed") in plan_calls
    # review sub-node updated
    assert ("review_architecture", "passed") in plan_calls


# ── alignment pause exit ──────────────────────────────────────────────────────


def test_alignment_pause_exits(tmp_path):
    stages = [
        {"stage": "discovery", "expansion": "tracks"},
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)
    # No alignment-log.md → interactive session launched but returns blocked (user didn't create artifact)
    blocked_signal = {"stage": "alignment", "status": "blocked", "message": "Artifact not created: alignment-log.md"}

    with (
        patch(
            "orchestrator.orchestrate.run_stage", side_effect=[DISCOVERY_PLANNING_SIGNAL, DISCOVERY_TRACK_SIGNAL]
        ) as mock_rs,
        patch("orchestrator.orchestrate.run_interactive_stage", return_value=blocked_signal),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert exc_info.value.code == 0
    # alignment stage never reached run_stage
    all_stages_called = [c.args[0] for c in mock_rs.call_args_list]
    assert "alignment" not in all_stages_called
    # state saved with blocked_at=alignment
    import yaml as _yaml

    state = _yaml.safe_load((run_folder_path / "_state.yaml").read_text())
    assert state.get("blocked_at") == "alignment"


# ── blocked stage exit ────────────────────────────────────────────────────────


def test_blocked_stage_exits(tmp_path):
    stages = [
        {"stage": "discovery", "prompt": "prompts/discovery/default.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=BLOCKED_SIGNAL),
        patch("orchestrator.orchestrate.update_plan_md") as mock_plan,
        patch("orchestrator.orchestrate.mark_pr_blocked") as mock_pr_blocked,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert exc_info.value.code == 1
    state = yaml.safe_load((run_folder_path / "_state.yaml").read_text())
    assert state.get("blocked_at") == "discovery"
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "blocked") in plan_calls
    # PR node must be flipped to blocked on pipeline failure so it does not
    # remain pending after a failed run. See ADR-026.
    mock_pr_blocked.assert_called_once_with(run_folder_path)


def test_review_subnode_status_resolved_after_cycle(tmp_path):
    """After a fix cycle approves a previously changes-requested reviewer, the
    orchestrator re-stamps the round-1 sub-node so it does not stay red beside
    the green round-N sibling. See ADR-026."""
    stages = [
        {"stage": "review", "expansion": "prompts", "prompts": {"architecture": "prompts/review/architecture.md"}},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    # Round-1: architecture requests changes, triggering the fix cycle path.
    round1_signal = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"architecture": "changes-requested"},
        "reviewer_findings": {"architecture": ["fix this"]},
        "non_blocking_findings": [],
    }
    # The fake cycle approves on round 2.
    cycle_result = {"all_passed": True, "reviewer_statuses": {"architecture": "approved"}}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=round1_signal),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
        patch("orchestrator.orchestrate.review_cycle_mod.run", return_value=cycle_result),
        patch("orchestrator.orchestrate.resolve_review_subnode_statuses") as mock_resolve,
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    mock_resolve.assert_called_once_with(run_folder_path, {"architecture": "approved"})


# ── resume skips completed stages ─────────────────────────────────────────────


def test_resume_skips_completed_stages(tmp_path):
    stages = [
        {"stage": "discovery", "prompt": "prompts/discovery/default.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    # Pre-populate state with discovery=passed
    import yaml as _yaml

    (run_folder_path / "_state.yaml").write_text(_yaml.dump({"stages": {"discovery": "passed"}}))

    called_stages = []

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        called_stages.append(stage)
        return SPEC_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(
            docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"), resume=True
        )

    assert "discovery" not in called_stages
    assert "specification" in called_stages


# ── branch created at implementation start ────────────────────────────────────


def test_branch_created_at_implementation_start(tmp_path):
    stages = [
        {"stage": "discovery", "expansion": "tracks"},
        {"stage": "decomposition", "prompt": "prompts/decomposition/default.md"},
        {
            "stage": "implementation",
            "prompt": "prompts/implementation/default.md",
            "expansion": "slices",
            "slices_from_stage": "decomposition",
        },
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    call_order: list[tuple[str, ...]] = []
    git_cmds = []
    sig_iter = iter([DISCOVERY_PLANNING_SIGNAL, DISCOVERY_TRACK_SIGNAL, DECOMP_SIGNAL, IMPL_SIGNAL, IMPL_SIGNAL])

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        call_order.append(("run_stage", stage))
        return next(sig_iter)

    def fake_git(cmd, **kwargs):
        git_cmds.append(cmd)
        if "checkout" in cmd:
            call_order.append(("git_checkout",))
        return _git_ok()

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", side_effect=fake_git),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        _patch_safe_git_state(),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    # git checkout should come after discovery but before implementation
    assert ("git_checkout",) in call_order
    git_pos = call_order.index(("git_checkout",))
    impl_pos = call_order.index(("run_stage", "implementation"))
    discovery_pos = call_order.index(("run_stage", "discovery"))  # first discovery call (planning)
    assert discovery_pos < git_pos < impl_pos

    # branch creation must target repo_root via -C, not the orchestrator's cwd
    checkout_cmd = next(cmd for cmd in git_cmds if "checkout" in cmd)
    assert "-C" in checkout_cmd
    assert "/tmp" in checkout_cmd  # repo-root from project.yaml fixture


# ── interactive stage not dispatched through run_stage ────────────────────────


def test_interactive_stage_not_dispatched_through_run_stage(tmp_path):
    stages = [
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)
    # alignment-log.md present → alignment auto-skipped (no interactive session launched)
    (run_folder_path / "alignment").mkdir()
    (run_folder_path / "alignment" / "alignment-log.md").write_text("# Alignment\n")

    called_stages = []

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        called_stages.append(stage)
        return SPEC_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.run_interactive_stage") as mock_ris,
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert "alignment" not in called_stages
    mock_ris.assert_not_called()


# ── update_plan_md called after each stage ────────────────────────────────────


def test_plan_md_updated_after_each_stage(tmp_path):
    stages = [
        {"stage": "discovery", "expansion": "tracks"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    signals = [DISCOVERY_PLANNING_SIGNAL, DISCOVERY_TRACK_SIGNAL, SPEC_SIGNAL]
    sig_iter = iter(signals)

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=lambda *a, **kw: next(sig_iter)),
        patch("orchestrator.orchestrate.update_plan_md") as mock_plan,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "passed") in plan_calls
    assert ("specification", "passed") in plan_calls


# ── discovery fan-out ─────────────────────────────────────────────────────────


def test_discovery_fanout_calls_planning_then_tracks(tmp_path):
    stages = [
        {"stage": "discovery", "expansion": "tracks"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    planning_signal = {
        "stage": "discovery-planning",
        "status": "passed",
        "tracks": [
            {
                "name": "code-entry-points",
                "prompt_file": "/tmp/stages/discovery-code-entry-points-prompt.md",
                "focus": "Find entry points",
            },
            {"name": "risk", "prompt_file": "/tmp/stages/discovery-risk-prompt.md", "focus": "Identify risks"},
        ],
    }
    track_signal_a = {
        "stage": "discovery-code-entry-points",
        "status": "passed",
        "findings_file": "/tmp/code.md",
        "summary": "Found 2 entry points",
    }
    track_signal_b = {
        "stage": "discovery-risk",
        "status": "passed",
        "findings_file": "/tmp/risk.md",
        "summary": "Low risk",
    }

    call_log = []
    sig_iter = iter([planning_signal, track_signal_a, track_signal_b, SPEC_SIGNAL])

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        call_log.append(
            {"stage": stage, "output_suffix": output_suffix, "schema_name": schema_name, "prompt_file": prompt_file}
        )
        return next(sig_iter)

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    # Planning call: schema_name=discovery_planning, output_suffix=planning
    assert call_log[0]["output_suffix"] == "planning"
    assert call_log[0]["schema_name"] == "discovery_planning"

    # Two track calls (parallel — order may vary): schema_name=discovery_track, prompt_file set
    track_calls = [c for c in call_log if c["schema_name"] == "discovery_track"]
    assert len(track_calls) == 2
    track_suffixes = {c["output_suffix"] for c in track_calls}
    assert track_suffixes == {"code-entry-points", "risk"}
    for tc in track_calls:
        assert tc["prompt_file"] is not None

    # Aggregated signal has both tracks and both findings_files
    sig_file = run_folder_path / "_state.yaml"
    # Just verify the pipeline reached specification (discovery signal saved correctly)
    spec_call = next(c for c in call_log if c["output_suffix"] not in ("planning", "code-entry-points", "risk"))
    assert spec_call["stage"] == "specification"


def test_discovery_blocked_when_planning_fails(tmp_path):
    stages = [
        {"stage": "discovery", "expansion": "tracks"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    blocked_planning = {"stage": "discovery-planning", "status": "blocked", "message": "No overview"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=blocked_planning),
        patch("orchestrator.orchestrate.update_plan_md") as mock_plan,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert exc_info.value.code == 1
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "blocked") in plan_calls


def test_discovery_blocked_when_any_track_fails(tmp_path):
    stages = [
        {"stage": "discovery", "expansion": "tracks"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    planning_signal = {
        "stage": "discovery-planning",
        "status": "passed",
        "tracks": [
            {"name": "code", "prompt_file": "/tmp/code-prompt.md", "focus": "x"},
            {"name": "risk", "prompt_file": "/tmp/risk-prompt.md", "focus": "y"},
        ],
    }
    track_ok = {"stage": "discovery-code", "status": "passed", "findings_file": "/tmp/code.md", "summary": "ok"}
    track_fail = {"stage": "discovery-risk", "status": "blocked", "message": "Cannot access repo"}

    sig_iter = iter([planning_signal, track_ok, track_fail])

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=lambda *a, **kw: next(sig_iter)),
        patch("orchestrator.orchestrate.update_plan_md") as mock_plan,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert exc_info.value.code == 1
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "blocked") in plan_calls


# ── non-slice artifacts are filtered from slice_files before implementation ───


def test_implementation_filters_non_slice_files(tmp_path):
    stages = [
        {
            "stage": "implementation",
            "prompt": "prompts/implementation/default.md",
            "expansion": "slices",
            "slices_from_stage": "decomposition",
        },
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    real_slice = str(run_folder_path / "decomposition" / "S-01-do-the-thing.md")
    artifact = str(run_folder_path / "decomposition" / "dependency-graph.md")

    import yaml as _yaml

    (run_folder_path / "_state.yaml").write_text(
        _yaml.dump(
            {
                "stages": {},
                "signals": {
                    "decomposition": {
                        "stage": "decomposition",
                        "status": "passed",
                        "slice_files": [real_slice, artifact],
                    }
                },
            }
        )
    )

    called_with = []

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        called_with.append(variables.get("slice_file"))
        return IMPL_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        _patch_safe_git_state(),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert called_with == [real_slice], f"Expected only the real slice to be dispatched, got: {called_with}"
    assert artifact not in called_with


# ── review_md points to review/review-log.md ─────────────────────────────────


def test_review_md_path_uses_stage_subfolder(tmp_path):
    stages = [
        {"stage": "review", "expansion": "prompts", "prompts": {"architecture": "prompts/review/architecture.md"}},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    captured_vars = {}

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        captured_vars.update(variables)
        return REVIEW_ARCH_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        # This test isolates the review stage with no upstream implementation, so the
        # diff-validator gate would block. Bypass the validator — we only assert path placement.
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    expected = str(run_folder_path / "review" / "review-log.md")
    assert captured_vars.get("review_md") == expected, (
        f"review_md should be {expected!r}, got {captured_vars.get('review_md')!r}"
    )


# ── interactive artifact placed inside stage subfolder ────────────────────────


def test_interactive_artifact_path_in_stage_subfolder(tmp_path):
    stages = [
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    captured_artifact = {}

    def fake_interactive(stage, prompt_path, variables, run_folder, artifact_path, docs_root, project, log_path):
        captured_artifact["path"] = artifact_path
        return {"stage": stage, "status": "blocked", "message": "Artifact not created: alignment-log.md"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=SPEC_SIGNAL),
        patch("orchestrator.orchestrate.run_interactive_stage", side_effect=fake_interactive),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        with pytest.raises(SystemExit):
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    expected = run_folder_path / "alignment" / "alignment-log.md"
    assert captured_artifact.get("path") == expected, (
        f"artifact_path should be {expected}, got {captured_artifact.get('path')}"
    )


# ── project_context_path injected into variables ──────────────────────────────


def test_project_context_path_injected_into_variables(tmp_path):
    stages = [
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    captured_vars = {}

    def fake_run_stage(
        stage,
        impl,
        variables,
        run_folder,
        docs_root,
        project,
        log_path,
        output_suffix="",
        cwd=None,
        prompt_file=None,
        schema_name=None,
        standards=None,
        runner=None,
    ):
        captured_vars.update(variables)
        return SPEC_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    expected = str(tmp_path / "projects" / "myproject" / "context.md")
    assert captured_vars.get("project_context_path") == expected, (
        f"project_context_path should be {expected!r}, got {captured_vars.get('project_context_path')!r}"
    )


# ── project context file created when absent ──────────────────────────────────


def test_project_context_file_created_when_absent(tmp_path):
    stages = [
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    project_context = tmp_path / "projects" / "myproject" / "context.md"
    assert not project_context.exists(), "Precondition: file must not exist before pipeline runs"

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=SPEC_SIGNAL),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert project_context.exists(), "project context.md should be created by run_pipeline when absent"
    assert project_context.read_text() == "", "newly created project context.md should be empty"


# ── orchestrate.py source contains no open() calls ───────────────────────────


def test_orchestrate_source_has_no_open_calls():
    import inspect

    import orchestrator.orchestrate as orch_mod

    source = inspect.getsource(orch_mod)
    # Filter out this very assertion and comments
    lines = [line for line in source.splitlines() if "open(" in line and not line.strip().startswith("#")]
    assert lines == [], "orchestrate.py contains open() calls:\n" + "\n".join(lines)


def test_unhandled_exception_in_dispatcher_is_logged_to_run_log(tmp_path):
    """An unexpected exception escaping a stage dispatcher is written to run.log before propagating."""
    stages = [{"stage": "specification", "prompt": "prompts/specification/default.md"}]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=FileNotFoundError("no such directory")),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        with pytest.raises(FileNotFoundError):
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    run_log = run_folder_path / "run.log"
    assert run_log.exists(), "run.log must be written before the exception propagates"
    log_text = run_log.read_text()
    assert "unhandled exception" in log_text
    assert "FileNotFoundError" in log_text
    assert "no such directory" in log_text


# ── dispatcher unit tests ─────────────────────────────────────────────────────


def _make_ctx(tmp_path):
    logger = MagicMock()
    return _PipelineContext(
        docs_root=str(tmp_path),
        project="myproject",
        project_log_path=str(tmp_path / "projects" / "myproject"),
        logger=logger,
        branch="feat/test",
        project_config={"repo-root": "/tmp"},
        project_standards=[],
        runners={},
        agent_metadata={},
    )


def _make_run_folder(tmp_path):
    rf = tmp_path / "runs" / "run-1"
    rf.mkdir(parents=True)
    return rf


# ── _dispatch_default ─────────────────────────────────────────────────────────


def test_default_dispatcher_returns_run_stage_signal(tmp_path):
    """Single stage execution surfaces the run_stage signal unchanged."""
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="specification", prompt="prompts/specification/default.md")
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    expected = {"stage": "specification", "status": "passed", "prd_path": "/tmp/prd.md"}

    with patch("orchestrator.orchestrate.run_stage", return_value=expected) as mock_rs:
        result = _dispatch_default(stage, {"repo_root": "/tmp"}, run_folder, ctx)

    assert result == expected
    mock_rs.assert_called_once()


def test_default_dispatcher_passes_repo_root_as_cwd_when_configured(tmp_path):
    """Stages with cwd_from_repo_root=True receive repo_root as working directory."""
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="qa", prompt="prompts/qa/default.md", cwd_from_repo_root=True)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    variables = {"repo_root": "/my/repo"}

    with (
        _patch_safe_git_state(),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed"}) as mock_rs,
    ):
        _dispatch_default(stage, variables, run_folder, ctx)

    _, kwargs = mock_rs.call_args
    assert kwargs.get("cwd") == "/my/repo"


def test_default_dispatcher_creates_branch_when_cwd_from_repo_root(tmp_path):
    """Stages with cwd_from_repo_root=True must enter the ctx.branch before run_stage.

    Mirrors the slice dispatcher's invariant: the slice path calls _create_branch
    before fan-out. Single-agent stages running in the repo root rely on the
    default dispatch path, so it must perform the same branch preparation.
    """
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="implementation", prompt="prompts/implementation/minimal.md", cwd_from_repo_root=True)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)

    with (
        _patch_safe_git_state(),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()) as mock_sp,
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed"}) as mock_rs,
    ):
        _dispatch_default(stage, {"repo_root": "/my/repo"}, run_folder, ctx)

    checkout_calls = [
        call.args[0]
        for call in mock_sp.call_args_list
        if call.args and isinstance(call.args[0], list) and "checkout" in call.args[0] and "-b" in call.args[0]
    ]
    assert any("feat/test" in args for args in checkout_calls), (
        f"expected `git checkout -b feat/test`, got {mock_sp.call_args_list!r}"
    )
    mock_rs.assert_called_once()


def test_default_dispatcher_skips_branch_creation_when_not_cwd_from_repo_root(tmp_path):
    """Stages that don't run in the repo root (specification, decomposition) do not touch git."""
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="specification", prompt="prompts/specification/minimal.md", cwd_from_repo_root=False)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)

    with (
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()) as mock_sp,
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed"}),
    ):
        _dispatch_default(stage, {"repo_root": "/my/repo"}, run_folder, ctx)

    assert mock_sp.call_args_list == [], f"specification must not invoke git subprocess, got {mock_sp.call_args_list!r}"


def test_default_dispatcher_blocks_on_git_state_error(tmp_path):
    """A dirty working tree blocks the stage rather than letting it commit to the wrong branch."""
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="implementation", prompt="prompts/implementation/minimal.md", cwd_from_repo_root=True)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)

    with (
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False),
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=False),
        patch("orchestrator.orchestrate.run_stage") as mock_rs,
    ):
        result = _dispatch_default(stage, {"repo_root": "/my/repo"}, run_folder, ctx)

    assert result["status"] == "blocked"
    assert "working tree not clean" in result["message"]
    mock_rs.assert_not_called()


def test_default_dispatcher_omits_cwd_when_stage_does_not_request_it(tmp_path):
    """Stages without cwd_from_repo_root run without a working directory override."""
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="specification", prompt="prompts/specification/default.md", cwd_from_repo_root=False)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)

    with patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed"}) as mock_rs:
        _dispatch_default(stage, {"repo_root": "/my/repo"}, run_folder, ctx)

    _, kwargs = mock_rs.call_args
    assert kwargs.get("cwd") is None


def test_default_dispatcher_injects_standards_when_stage_opts_in(tmp_path):
    """Standards list is forwarded to run_stage when stage.standards is True."""
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="implementation", prompt="prompts/impl/default.md", standards=True)
    ctx = _make_ctx(tmp_path)
    ctx.project_standards = ["harsh-python-engineering-standards"]
    run_folder = _make_run_folder(tmp_path)

    with patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed"}) as mock_rs:
        _dispatch_default(stage, {}, run_folder, ctx)

    _, kwargs = mock_rs.call_args
    assert kwargs.get("standards") == ["harsh-python-engineering-standards"]


def test_default_dispatcher_passes_no_standards_when_stage_does_not_opt_in(tmp_path):
    """Standards are None when stage.standards is False, even if project has standards."""
    from orchestrator.orchestrate import _dispatch_default

    stage = StageConfig(name="specification", prompt="prompts/spec/default.md", standards=False)
    ctx = _make_ctx(tmp_path)
    ctx.project_standards = ["harsh-python-engineering-standards"]
    run_folder = _make_run_folder(tmp_path)

    with patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed"}) as mock_rs:
        _dispatch_default(stage, {}, run_folder, ctx)

    _, kwargs = mock_rs.call_args
    assert kwargs.get("standards") is None


# ── _dispatch_interactive ─────────────────────────────────────────────────────


def test_interactive_returns_passed_when_artifact_already_exists(tmp_path):
    """Existing artifact skips the session and returns a passed signal immediately."""
    from orchestrator.orchestrate import _dispatch_interactive

    stage = StageConfig(
        name="alignment", mode="interactive", artifact="alignment-log.md", prompt="prompts/alignment/interactive.md"
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    artifact_path = run_folder / "alignment" / "alignment-log.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# alignment log\n")

    with patch("orchestrator.orchestrate.run_interactive_stage") as mock_ris:
        result = _dispatch_interactive(stage, {}, run_folder, ctx)

    assert result["status"] == "passed"
    mock_ris.assert_not_called()


def test_interactive_returns_blocked_when_session_exits_without_artifact(tmp_path):
    """Missing artifact after session exits returns a blocked signal."""
    from orchestrator.orchestrate import _dispatch_interactive

    stage = StageConfig(
        name="alignment", mode="interactive", artifact="alignment-log.md", prompt="prompts/alignment/interactive.md"
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)

    with patch(
        "orchestrator.orchestrate.run_interactive_stage",
        return_value={"status": "blocked", "message": "artifact not found"},
    ):
        result = _dispatch_interactive(stage, {}, run_folder, ctx)

    assert result["status"] == "blocked"


def test_interactive_missing_artifact_field_returns_blocked_before_session(tmp_path):
    """Stage with no artifact field returns a blocked signal without launching a session."""
    from orchestrator.orchestrate import _dispatch_interactive

    stage = StageConfig(name="alignment", mode="interactive", artifact=None, prompt="prompts/alignment/interactive.md")
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)

    with patch("orchestrator.orchestrate.run_interactive_stage") as mock_ris:
        result = _dispatch_interactive(stage, {}, run_folder, ctx)

    assert result["status"] == "blocked"
    mock_ris.assert_not_called()


# ── _dispatch_tracks ──────────────────────────────────────────────────────────


def _planning_signal_with_tracks(tracks):
    return {"stage": "discovery-planning", "status": "passed", "tracks": tracks}


def test_tracks_planning_failure_propagates_as_blocked(tmp_path):
    """When the planning stage fails, the dispatcher returns a blocked signal."""
    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    failed_planning = {"stage": "discovery-planning", "status": "failed", "message": "no overview"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=failed_planning),
        patch("orchestrator.orchestrate.update_plan_md"),
    ):
        result = _dispatch_tracks(stage, {}, run_folder, ctx)

    assert result["status"] in ("failed", "blocked")


def test_tracks_no_tracks_from_planning_yields_blocked(tmp_path):
    """Empty tracks list from planning is a blocking failure."""
    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    empty = {"stage": "discovery-planning", "status": "passed", "tracks": []}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=empty),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
    ):
        result = _dispatch_tracks(stage, {}, run_folder, ctx)

    assert result["status"] == "blocked"


def test_tracks_single_track_runs_serially_without_executor(tmp_path):
    """A single track does not use ThreadPoolExecutor."""
    import concurrent.futures as cf

    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    planning = _planning_signal_with_tracks(
        [
            {
                "name": "only-track",
                "prompt_file": "/tmp/prompt.md",
            }
        ]
    )
    track_sig = {
        "stage": "discovery-only-track",
        "status": "passed",
        "findings_file": "/tmp/findings.md",
        "summary": "done",
    }

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=[planning, track_sig]),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes", return_value={"only-track": "node-1"}),
        patch.object(cf, "ThreadPoolExecutor") as mock_exec,
    ):
        result = _dispatch_tracks(stage, {}, run_folder, ctx)

    assert result["status"] == "passed"
    mock_exec.assert_not_called()


def test_tracks_multiple_tracks_run_in_parallel(tmp_path):
    """Multiple tracks are submitted to ThreadPoolExecutor concurrently."""
    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    tracks = [
        {"name": "track-a", "prompt_file": "/tmp/a.md"},
        {"name": "track-b", "prompt_file": "/tmp/b.md"},
    ]
    planning = _planning_signal_with_tracks(tracks)
    track_sig = {"status": "passed", "findings_file": "/tmp/f.md", "summary": "ok"}

    fut_a = MagicMock()
    fut_a.result.return_value = ("track-a", track_sig)
    fut_b = MagicMock()
    fut_b.result.return_value = ("track-b", track_sig)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=planning),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes", return_value={}),
        patch("concurrent.futures.ThreadPoolExecutor") as mock_exec_cls,
    ):
        mock_exec = MagicMock()
        mock_exec.__enter__ = MagicMock(return_value=mock_exec)
        mock_exec.__exit__ = MagicMock(return_value=False)
        submitted = iter([fut_a, fut_b])
        mock_exec.submit.side_effect = lambda *a, **kw: next(submitted)
        mock_exec_cls.return_value = mock_exec

        result = _dispatch_tracks(stage, {}, run_folder, ctx)

    assert mock_exec.submit.call_count == 2


def test_tracks_any_failed_track_yields_failed_signal(tmp_path):
    """One failed track causes the dispatcher to return a failed aggregated signal."""
    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    tracks = [
        {"name": "track-a", "prompt_file": "/tmp/a.md"},
        {"name": "track-b", "prompt_file": "/tmp/b.md"},
    ]
    planning = _planning_signal_with_tracks(tracks)
    passed = {"status": "passed", "findings_file": "/tmp/f.md", "summary": "ok"}
    failed = {"status": "failed", "message": "agent crashed"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=planning),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes", return_value={}),
        patch("concurrent.futures.ThreadPoolExecutor") as mock_exec_cls,
    ):
        mock_exec = MagicMock()
        mock_exec.__enter__ = MagicMock(return_value=mock_exec)
        mock_exec.__exit__ = MagicMock(return_value=False)
        fut_a = MagicMock()
        fut_a.result.return_value = ("track-a", passed)
        fut_b = MagicMock()
        fut_b.result.return_value = ("track-b", failed)
        submitted = iter([fut_a, fut_b])
        mock_exec.submit.side_effect = lambda *a, **kw: next(submitted)
        mock_exec_cls.return_value = mock_exec

        result = _dispatch_tracks(stage, {}, run_folder, ctx)

    assert result["status"] in ("failed", "blocked")


def test_tracks_aggregated_signal_contains_all_findings_files(tmp_path):
    """Passed tracks are aggregated into a single signal with all findings_files."""
    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    planning = _planning_signal_with_tracks([{"name": "t1", "prompt_file": "/tmp/p1.md"}])
    track_sig = {"status": "passed", "findings_file": "/tmp/findings1.md", "summary": "x"}

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=[planning, track_sig]),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes", return_value={}),
    ):
        result = _dispatch_tracks(stage, {}, run_folder, ctx)

    assert result["status"] == "passed"
    assert "/tmp/findings1.md" in result.get("findings_files", [])
    assert len(result.get("tracks", [])) == 1


# ── _dispatch_slices ──────────────────────────────────────────────────────────


def test_slices_creates_git_branch_before_any_slice_runs(tmp_path):
    """Branch is created once before slice execution begins."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {"decomposition": {"slice_files": ["S-01-slice.md"], "slice_groups": [["S-01-slice.md"]]}}

    with (
        patch("orchestrator.orchestrate._create_branch") as mock_cb,
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    mock_cb.assert_called_once()
    assert mock_cb.call_args[0][0] == "feat/test"


def test_slices_single_slice_runs_serially(tmp_path):
    """A group of one slice runs directly without worktrees."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {"decomposition": {"slice_files": ["S-01-slice.md"], "slice_groups": [["S-01-slice.md"]]}}

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate._create_worktree") as mock_wt,
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "passed"
    mock_wt.assert_not_called()


def test_slices_parallel_group_creates_one_worktree_per_slice(tmp_path):
    """A group of multiple slices each get their own git worktree."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md", "S-02-b.md"]],
        }
    }
    impl_sig = {"status": "passed", "commit_hashes": ["c1"]}

    mock_future = MagicMock()
    mock_future.result.return_value = (impl_sig, 1.0)

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate._create_worktree", return_value="/tmp/wt") as mock_wt,
        patch("orchestrator.orchestrate._remove_worktree"),
        patch("orchestrator.orchestrate._merge_worktree_branch"),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("concurrent.futures.ThreadPoolExecutor") as mock_exec_cls,
    ):
        mock_exec = MagicMock()
        mock_exec.__enter__ = MagicMock(return_value=mock_exec)
        mock_exec.__exit__ = MagicMock(return_value=False)
        mock_exec.submit.return_value = mock_future
        mock_exec_cls.return_value = mock_exec

        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert mock_wt.call_count == 2


def test_slices_worktrees_cleaned_up_after_failure(tmp_path):
    """Worktrees are removed in the finally block even when a slice fails."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md", "S-02-b.md"]],
        }
    }
    failed_sig = {"status": "failed", "message": "compile error"}

    mock_future = MagicMock()
    mock_future.result.return_value = (failed_sig, 0.5)

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate._create_worktree", return_value="/tmp/wt"),
        patch("orchestrator.orchestrate._remove_worktree") as mock_rm,
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("concurrent.futures.ThreadPoolExecutor") as mock_exec_cls,
    ):
        mock_exec = MagicMock()
        mock_exec.__enter__ = MagicMock(return_value=mock_exec)
        mock_exec.__exit__ = MagicMock(return_value=False)
        mock_exec.submit.return_value = mock_future
        mock_exec_cls.return_value = mock_exec

        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] in ("failed", "blocked")
    assert mock_rm.call_count == 2


def test_slices_non_slice_files_filtered_with_warning(tmp_path):
    """Files not matching S-\\d+- pattern are dropped and a warning is logged."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-slice.md", "README.md"],
            "slice_groups": [["S-01-slice.md", "README.md"]],
        }
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": []}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    ctx.logger.log.assert_any_call("implementation", "WARN", unittest_mock_any_str())


def unittest_mock_any_str():
    class _AnyStr:
        def __eq__(self, other):
            return isinstance(other, str)

        def __repr__(self):
            return "<AnyStr>"

    return _AnyStr()


def test_slices_failed_slice_propagates_failed_signal(tmp_path):
    """A blocked/failed serial slice causes the dispatcher to return a failed signal."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {"decomposition": {"slice_files": ["S-01-slice.md"], "slice_groups": [["S-01-slice.md"]]}}

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "failed", "message": "tests failed"}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] in ("failed", "blocked")


# ── wave verification (ADR-030) ───────────────────────────────────────────────


def _wave_stage(
    on_failure: Literal["warn", "fix_then_retry", "block"] = "warn",
    enabled: bool = True,
) -> StageConfig:
    from orchestrator.profile import WaveVerification

    return StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
        wave_verification=WaveVerification(enabled=enabled, on_failure=on_failure),
    )


def test_wave_verification_runs_after_each_passed_wave(tmp_path):
    """When slice expansion is enabled, the verifier is invoked once per group."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage()
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md"], ["S-02-b.md"]],
        }
    }
    verify_sig = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "passed",
        "summary": "ok",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }
    (run_folder / "plan.md").write_text("# Plan\n")

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", return_value=verify_sig) as mock_verify,
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "passed"
    # 1 baseline capture (before any slice) + 1 per wave. See ADR-033.
    assert mock_verify.call_count == 3
    # Wave artifact subdirs must be distinct per wave so reports don't overwrite;
    # baseline lives in its own subdir.
    subdirs = sorted(call.kwargs["artifact_subdir"] for call in mock_verify.call_args_list)
    assert subdirs == ["baseline-verification", "wave-verification/wave-1", "wave-verification/wave-2"]
    assert len(result["wave_verifications"]) == 2


def test_wave_verification_warn_policy_continues_on_failure(tmp_path):
    """on_failure=warn records the failure but the dispatcher still passes."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage(on_failure="warn")
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }
    failed_verify = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "failed",
        "summary": "tests broke after merge",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }
    (run_folder / "plan.md").write_text("# Plan\n")

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", return_value=failed_verify),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "passed"
    assert result["wave_verifications"][0]["verification_status"] == "failed"
    # A WARN line is emitted with the failure summary so run.log surfaces wave health.
    warn_calls = [c for c in ctx.logger.log.call_args_list if c.args[1] == "WARN"]
    assert any("wave 1" in c.args[2] and "failed" in c.args[2] for c in warn_calls)


def test_wave_verification_block_policy_halts_dispatcher(tmp_path):
    """on_failure=block returns a blocked signal so the pipeline stops at the wave boundary."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage(on_failure="block")
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md"], ["S-02-b.md"]],
        }
    }
    failed_verify = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "failed",
        "summary": "tests broke after merge",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }
    (run_folder / "plan.md").write_text("# Plan\n")

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", return_value=failed_verify) as mock_verify,
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "blocked"
    # 1 baseline capture + 1 wave-1 run; block prevents wave 2 from starting.
    assert mock_verify.call_count == 2
    assert "wave 1" in result["message"]


def test_wave_verification_disabled_when_config_off(tmp_path):
    """Setting enabled=false on a slice stage skips the verifier entirely."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage(enabled=False)
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify") as mock_verify,
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    mock_verify.assert_not_called()
    assert "wave_verifications" not in result


def test_wave_verification_section_appended_to_plan_md(tmp_path):
    """A 'Wave N Verification' section lands in plan.md per wave."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage()
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }
    verify_sig = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "passed",
        "summary": "toolchain=python, 3 commands",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }
    plan_path = run_folder / "plan.md"
    plan_path.write_text("# Plan\n\n## Run Summary\n")

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", return_value=verify_sig),
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    plan_text = plan_path.read_text()
    assert "## Wave 1 Verification" in plan_text
    assert "`passed`" in plan_text
    assert "wave-verification/wave-1/VERIFY.md" in plan_text
    # Insertion respects existing markers — the new section lives above Run Summary.
    assert plan_text.index("## Wave 1 Verification") < plan_text.index("## Run Summary")


def test_wave_verification_not_run_when_stage_lacks_config(tmp_path):
    """A slice stage with wave_verification=None (e.g. an old profile) is unaffected."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
        wave_verification=None,
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify") as mock_verify,
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    mock_verify.assert_not_called()


# ── baseline vs net-new wave verification (ADR-033) ──────────────────────────


def _baseline_verify_side_effect(run_folder, *, fail_command_ids_per_call):
    """Side effect that writes a baseline-shaped verify.json into the artifact_subdir.

    ``fail_command_ids_per_call`` is a sequence of failure-id lists, one per call,
    so callers can simulate a baseline failure set followed by per-wave runs.
    """
    calls = iter(fail_command_ids_per_call)

    def _fake_verify(repo_root, run_folder_arg, *, artifact_subdir, baseline_path=None):
        fail_ids = next(calls)
        out_dir = Path(run_folder_arg) / artifact_subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        commands = [
            {
                "id": "test",
                "command": "x",
                "required": True,
                "status": "failed" if "test" in fail_ids else "passed",
                "exit_code": 1 if "test" in fail_ids else 0,
                "duration_seconds": 0.0,
                "skipped_reason": None,
                "failure_kind": None,
            }
        ]
        report = {
            "status": "failed" if fail_ids else "passed",
            "toolchain": "node",
            "commands": commands,
            "probes": [],
        }
        (out_dir / "verify.json").write_text(json.dumps(report))
        # Mirror what the real engine returns, including baseline classification fields.
        baseline_failed_command_ids: list[str] = []
        new_failed_command_ids: list[str] = []
        baseline_compared = baseline_path is not None and Path(baseline_path).exists()
        if baseline_compared:
            baseline_data = json.loads(Path(baseline_path).read_text())
            baseline_set = {c["id"] for c in baseline_data.get("commands", []) if c.get("status") == "failed"}
            for cid in fail_ids:
                if cid in baseline_set:
                    baseline_failed_command_ids.append(cid)
                else:
                    new_failed_command_ids.append(cid)
        if not baseline_compared:
            net_new_status = "failed" if fail_ids else "passed"
        else:
            net_new_status = "failed" if new_failed_command_ids else "passed"
        return {
            "stage": "verification",
            "status": "passed",
            "verification_status": "failed" if fail_ids else "passed",
            "net_new_status": net_new_status,
            "summary": f"failures={fail_ids}",
            "toolchain": "node",
            "verify_md_path": str(out_dir / "VERIFY.md"),
            "verify_json_path": str(out_dir / "verify.json"),
            "command_ids": ["test"],
            "failed_command_ids": list(fail_ids),
            "probe_ids": [],
            "failed_probe_ids": [],
            "baseline_failed_command_ids": baseline_failed_command_ids,
            "baseline_failed_probe_ids": [],
            "new_failed_command_ids": new_failed_command_ids,
            "new_failed_probe_ids": [],
            "resolved_command_ids": [],
            "resolved_probe_ids": [],
            "baseline_compared": baseline_compared,
        }

    return _fake_verify


def test_baseline_unchanged_failure_does_not_block_under_default_warn(tmp_path):
    """Pre-existing failures repeated in a wave run must not halt the dispatcher."""
    import json as _json

    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage(on_failure="block")  # even under block, baseline-only must continue
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    (run_folder / "plan.md").write_text("# Plan\n")
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }

    side = _baseline_verify_side_effect(run_folder, fail_command_ids_per_call=[["test"], ["test"]])

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", side_effect=side),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    # Baseline-only failure under block policy must NOT halt — only net-new failures gate the pipeline.
    assert result["status"] == "passed"
    wave = result["wave_verifications"][0]
    assert wave["baseline_compared"] is True
    assert wave["baseline_failed_command_ids"] == ["test"]
    assert wave["new_failed_command_ids"] == []
    _ = _json  # keep import for static checkers


def test_new_failure_blocks_under_block_policy(tmp_path):
    """A net-new failure under block policy halts the pipeline at the wave boundary."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage(on_failure="block")
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    (run_folder / "plan.md").write_text("# Plan\n")
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md"], ["S-02-b.md"]],
        }
    }

    # Baseline clean (no failures); wave 1 introduces a new failure.
    side = _baseline_verify_side_effect(run_folder, fail_command_ids_per_call=[[], ["test"]])

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", side_effect=side),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "blocked"
    wave = result["wave_verifications"][0]
    assert wave["new_failed_command_ids"] == ["test"]
    assert "net-new" in result["message"]


def test_new_failure_under_warn_policy_records_but_continues(tmp_path):
    """Under warn, net-new failures are logged and surfaced but do not block."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage(on_failure="warn")
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    (run_folder / "plan.md").write_text("# Plan\n")
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }

    side = _baseline_verify_side_effect(run_folder, fail_command_ids_per_call=[[], ["test"]])

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", side_effect=side),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "passed"
    wave = result["wave_verifications"][0]
    assert wave["new_failed_command_ids"] == ["test"]
    # Plan section calls out the net-new breakdown.
    plan_text = (run_folder / "plan.md").read_text()
    assert "Net-new failures" in plan_text
    assert "Net-new status" in plan_text


def test_baseline_captured_before_first_slice(tmp_path):
    """The baseline must be captured before any slice has run, written to the standard subdir."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = _wave_stage()
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    (run_folder / "plan.md").write_text("# Plan\n")
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }

    side = _baseline_verify_side_effect(run_folder, fail_command_ids_per_call=[[], []])

    call_order: list[str] = []

    def tracking_run_stage(*args, **kwargs):
        call_order.append("slice")
        return {"status": "passed", "commit_hashes": ["a1"]}

    def tracking_verify(*args, **kwargs):
        call_order.append("verify:" + kwargs.get("artifact_subdir", ""))
        return side(*args, **kwargs)

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", side_effect=tracking_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", side_effect=tracking_verify),
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert call_order == ["verify:baseline-verification", "slice", "verify:wave-verification/wave-1"]


def test_baseline_capture_idempotent_across_resume(tmp_path):
    """A resumed pipeline must not overwrite an existing baseline file."""
    from orchestrator.orchestrate import _dispatch_slices
    from orchestrator.verifiers.engine import BASELINE_SUBDIR

    stage = _wave_stage()
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    (run_folder / "plan.md").write_text("# Plan\n")
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }

    # Pre-populate a baseline so the capture should be skipped.
    baseline_dir = run_folder / BASELINE_SUBDIR
    baseline_dir.mkdir(parents=True)
    pre_existing = {
        "status": "failed",
        "toolchain": "node",
        "commands": [
            {
                "id": "test",
                "command": "x",
                "required": True,
                "status": "failed",
                "exit_code": 1,
                "duration_seconds": 0.0,
                "skipped_reason": None,
                "failure_kind": None,
            }
        ],
        "probes": [],
    }
    (baseline_dir / "verify.json").write_text(json.dumps(pre_existing))

    side = _baseline_verify_side_effect(
        run_folder,
        fail_command_ids_per_call=[[]],  # only one call expected — the wave run
    )

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.verifiers.engine.verify", side_effect=side) as mock_verify,
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    # No baseline capture call — only the wave verify.
    assert mock_verify.call_count == 1
    assert mock_verify.call_args_list[0].kwargs["artifact_subdir"] == "wave-verification/wave-1"
    # Pre-existing baseline file untouched.
    assert json.loads((baseline_dir / "verify.json").read_text())["commands"][0]["status"] == "failed"


# ── slice-completion vs wave-integration distinction (ADR-031) ───────────────


def test_wave_node_stamped_independently_of_slice_status(tmp_path):
    """A passing slice and a failing wave verification yield two distinct node statuses.

    Without this separation a ``passed`` slice would imply integration health.
    See ADR-031.
    """
    from orchestrator.orchestrate import _dispatch_slices
    from orchestrator.plan import init_plan_md
    from orchestrator.plan._graph import load_graph
    from orchestrator.profile import Profile

    stage = _wave_stage(on_failure="warn")
    profile = Profile(name="t", stages=(stage,))
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, profile)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }
    failed_verify = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "failed",
        "summary": "integration broken",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.verifiers.engine.verify", return_value=failed_verify),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "passed"  # warn policy continues
    graph = load_graph(run_folder)
    assert graph is not None
    # Slice node carries local completion.
    assert graph.nodes["impl_1"].status == "passed"
    # Wave node carries integration health — distinct from and not dominated by the slice.
    assert graph.nodes["wave_verify_1"].status == "blocked"


def test_wave_node_passed_when_integration_healthy(tmp_path):
    """Both slice and wave nodes pass when integration verification succeeds."""
    from orchestrator.orchestrate import _dispatch_slices
    from orchestrator.plan import init_plan_md
    from orchestrator.plan._graph import load_graph
    from orchestrator.profile import Profile

    stage = _wave_stage()
    profile = Profile(name="t", stages=(stage,))
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, profile)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md"], ["S-02-b.md"]],
        }
    }
    verify_sig = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "passed",
        "summary": "ok",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.verifiers.engine.verify", return_value=verify_sig),
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    graph = load_graph(run_folder)
    assert graph is not None
    for node_id in ("impl_1", "impl_2", "wave_verify_1", "wave_verify_2"):
        assert graph.nodes[node_id].status == "passed", f"{node_id} should be passed"


def test_wave_nodes_absent_when_verification_disabled(tmp_path):
    """A slice stage with wave_verification disabled has no wave nodes — slice nodes only."""
    from orchestrator.orchestrate import _dispatch_slices
    from orchestrator.plan import init_plan_md
    from orchestrator.plan._graph import load_graph
    from orchestrator.profile import Profile

    stage = _wave_stage(enabled=False)
    profile = Profile(name="t", stages=(stage,))
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, profile)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.verifiers.engine.verify"),
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    graph = load_graph(run_folder)
    assert graph is not None
    assert "impl_1" in graph.nodes
    assert "wave_verify_1" not in graph.nodes


def test_run_summary_includes_wave_verification_entry(tmp_path):
    """The run summary surfaces wave verification time as its own row.

    A passing slice alone must not look like a healthy repo in the run summary —
    the wave-verification row is what represents integration health. See ADR-031.
    """
    from orchestrator.orchestrate import _dispatch_slices
    from orchestrator.plan import init_plan_md
    from orchestrator.profile import Profile

    stage = _wave_stage()
    profile = Profile(name="t", stages=(stage,))
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, profile)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md"],
            "slice_groups": [["S-01-a.md"]],
        }
    }
    verify_sig = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "passed",
        "summary": "ok",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.verifiers.engine.verify", return_value=verify_sig),
    ):
        _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    plan_text = (run_folder / "plan.md").read_text()
    assert "Wave Verify 1" in plan_text


# ── _dispatch_prompts ─────────────────────────────────────────────────────────


def test_prompts_all_reviewers_run_and_statuses_collected(tmp_path):
    """Each reviewer in stage.prompts receives a run_stage call."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"architecture": "prompts/review/architecture.md", "security": "prompts/review/security.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    review_sig = {"status": "passed", "reviewer_statuses": {"architecture": "passed"}, "changes_requested": []}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=review_sig) as mock_rs,
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
    ):
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, {})

    assert mock_rs.call_count == 2
    assert "reviewer_statuses" in result


def test_prompts_fix_cycle_triggered_automatically_when_reviewer_requests_changes(tmp_path):
    """changes_requested triggers review_cycle.run without user approval."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"architecture": "prompts/review/architecture.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    review_sig = {
        "status": "passed",
        "reviewer_statuses": {"architecture": "changes-requested"},
        "changes_requested": ["architecture"],
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=review_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
        patch(
            "orchestrator.orchestrate.review_cycle_mod.run", return_value={"all_passed": True, "reviewers": []}
        ) as mock_cycle,
    ):
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, {})

    mock_cycle.assert_called_once()
    assert result["status"] == "passed"


def test_prompts_passes_stage_runners_to_review_cycle(tmp_path):
    """Fix cycles should keep using the implementation and review runners selected by the profile."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"implementation": "prompts/review/implementation.md"},
    )
    implementation_runner = object()
    review_runner = object()
    ctx = _make_ctx(tmp_path)
    ctx.runners.update({"implementation": implementation_runner, "review": review_runner})
    run_folder = _make_run_folder(tmp_path)
    review_sig = {
        "status": "passed",
        "reviewer_statuses": {"implementation": "changes-requested"},
        "changes_requested": ["implementation"],
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=review_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
        patch(
            "orchestrator.orchestrate.review_cycle_mod.run", return_value={"all_passed": True, "reviewers": []}
        ) as mock_cycle,
    ):
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, {})

    assert result["status"] == "passed"
    assert mock_cycle.call_args.kwargs["implementation_runner"] is implementation_runner
    assert mock_cycle.call_args.kwargs["review_runner"] is review_runner


def test_prompts_returns_final_reviewer_statuses_after_successful_cycle(tmp_path):
    """After a fix cycle resolves changes-requested, the aggregate review signal must reflect the
    terminal reviewer_statuses — not the initial round-1 verdict. Regression for #127: stale
    `changes-requested` was being persisted to _state.yaml even after a successful re-review."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"implementation": "prompts/review/implementation.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    initial_review_sig = {
        "status": "passed",
        "reviewer_statuses": {"implementation": "changes-requested"},
        "changes_requested": ["implementation"],
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=initial_review_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
        patch(
            "orchestrator.orchestrate.review_cycle_mod.run",
            return_value={"all_passed": True, "reviewer_statuses": {"implementation": "approved"}},
        ),
    ):
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, {})

    assert result["status"] == "passed"
    assert result["reviewer_statuses"] == {"implementation": "approved"}
    assert result["changes_requested"] == []


def test_prompts_review_cycle_failure_returns_blocked_signal(tmp_path):
    """When review_cycle.run returns all_passed=False, the dispatcher returns blocked."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"architecture": "prompts/review/architecture.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    review_sig = {
        "status": "passed",
        "reviewer_statuses": {"architecture": "changes-requested"},
        "changes_requested": ["architecture"],
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=review_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
        patch(
            "orchestrator.orchestrate.review_cycle_mod.run",
            return_value={"all_passed": False, "reviewers": ["architecture"]},
        ),
    ):
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, {})

    assert result["status"] == "blocked"


def test_prompts_diff_patch_written_when_commit_hashes_present(tmp_path):
    """A git diff is computed and written to diff-round-1.patch before reviewers run."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"architecture": "prompts/review/architecture.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {"implementation": {"commit_hashes": ["aaa", "bbb"]}}
    review_sig = {"status": "passed", "reviewer_statuses": {"architecture": "passed"}, "changes_requested": []}

    git_diff = MagicMock()
    git_diff.returncode = 0
    git_diff.stdout = "diff --git a/f b/f\n"
    git_diff.stderr = ""

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=review_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.review_cycle.subprocess.run", return_value=git_diff),
    ):
        _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    diff_file = run_folder / "review" / "diff-round-1.patch"
    assert diff_file.exists()
    assert "diff --git" in diff_file.read_text()


def test_prompts_blocks_when_no_commits_for_round_1_diff(tmp_path):
    """Round-1 review must not dispatch reviewers when there is no diff to review.

    A missing diff (no commit_hashes from upstream, or `repo_root` unset) means there's
    nothing to review — fail the stage deterministically rather than send reviewers a
    prose summary or an empty file."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"architecture": "prompts/review/architecture.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)

    with (
        patch("orchestrator.orchestrate.run_stage") as mock_rs,
        patch("orchestrator.orchestrate.update_plan_md"),
    ):
        # No implementation signal → no commit_hashes → diff path is "".
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, {})

    assert result["status"] == "blocked"
    assert "no valid git diff" in result["message"]
    mock_rs.assert_not_called()


def test_prompts_blocks_when_diff_file_is_prose_summary(tmp_path):
    """If the computed diff file contains a prose summary (not a real git diff), block
    the stage deterministically instead of sending reviewers a non-diff input."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"architecture": "prompts/review/architecture.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {"implementation": {"commit_hashes": ["aaa"]}}

    git_summary = MagicMock()
    git_summary.returncode = 0
    git_summary.stdout = "Refactored auth module and added retry tests.\n"
    git_summary.stderr = ""

    with (
        patch("orchestrator.orchestrate.run_stage") as mock_rs,
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=git_summary),
    ):
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert result["status"] == "blocked"
    assert "no valid git diff" in result["message"]
    mock_rs.assert_not_called()


def test_prompts_blocks_when_reviewer_substage_does_not_pass(tmp_path):
    """A reviewer sub-stage that fails to pass (runner crash, missing signal, etc.)
    must propagate as a blocked review stage rather than be silently treated as approval.

    Reproduces the codex/Claude-model cross-backend failure where the sub-stage signal
    was {status: blocked, message: 'Agent runner failed...'} and the review stage
    previously hardcoded status=passed regardless."""
    from orchestrator.orchestrate import _dispatch_prompts

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"implementation": "prompts/review/implementation.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    blocked_sig = {"stage": "review", "status": "blocked", "message": "Agent runner failed with exit code 1"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=blocked_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
        patch("orchestrator.orchestrate.review_cycle_mod.run") as mock_cycle,
    ):
        result = _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, {})

    assert result["status"] == "blocked"
    assert "implementation" in result["message"]
    assert "Agent runner failed with exit code 1" in result["message"]
    mock_cycle.assert_not_called()


# ── git state hardening (issue #79) ───────────────────────────────────────────


def test_create_branch_refuses_when_working_tree_dirty_and_branch_missing(tmp_path):
    """Dirty repo blocks new branch creation with a clear GitStateError."""
    from orchestrator.orchestrate import _create_branch

    with (
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False),
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=False),
    ):
        with pytest.raises(GitStateError, match="working tree not clean"):
            _create_branch("feat/x", "/repo", MagicMock(), "implementation")


def test_create_branch_refuses_when_working_tree_dirty_and_need_switch(tmp_path):
    """Dirty repo blocks switching to an existing branch with a clear GitStateError."""
    from orchestrator.orchestrate import _create_branch

    with (
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=True),
        patch("orchestrator.orchestrate.git_state.current_branch", return_value="main"),
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=False),
    ):
        with pytest.raises(GitStateError, match="working tree not clean"):
            _create_branch("feat/x", "/repo", MagicMock(), "implementation")


def test_create_branch_switches_to_existing_branch_when_not_on_it(tmp_path):
    """If the branch already exists and we're elsewhere, checkout (no -b)."""
    from orchestrator.orchestrate import _create_branch

    ok = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=True),
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=True),
        patch("orchestrator.orchestrate.git_state.current_branch", return_value="main"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=ok) as mock_run,
    ):
        _create_branch("feat/x", "/repo", MagicMock(), "implementation")
    cmd = mock_run.call_args[0][0]
    assert "checkout" in cmd and "-b" not in cmd
    assert "feat/x" in cmd


def test_create_branch_noop_when_already_on_target(tmp_path):
    """Resume case: already on the feature branch with clean tree → no checkout, no error."""
    from orchestrator.orchestrate import _create_branch

    with (
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=True),
        patch("orchestrator.orchestrate.git_state.current_branch", return_value="feat/x"),
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=True) as mock_clean,
        patch("orchestrator.orchestrate.subprocess.run") as mock_run,
    ):
        _create_branch("feat/x", "/repo", MagicMock(), "implementation")
    mock_run.assert_not_called()
    mock_clean.assert_called_once()


def test_create_branch_refuses_when_already_on_target_and_dirty(tmp_path):
    """Already on target branch with dirty tree → raises GitStateError without mutating git."""
    from orchestrator.orchestrate import _create_branch

    with (
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=True),
        patch("orchestrator.orchestrate.git_state.current_branch", return_value="feat/x"),
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=False),
        patch("orchestrator.orchestrate.subprocess.run") as mock_run,
    ):
        with pytest.raises(GitStateError, match="working tree not clean"):
            _create_branch("feat/x", "/repo", MagicMock(), "implementation")
    mock_run.assert_not_called()


def test_create_worktree_refuses_when_temp_branch_already_exists(tmp_path):
    """A pre-existing temp branch is unexpected — refuse to clobber it."""
    from orchestrator.orchestrate import _create_worktree

    with patch("orchestrator.orchestrate.git_state.branch_exists", return_value=True):
        with pytest.raises(GitStateError, match="branch already exists"):
            _create_worktree("/repo", "feat/x-impl_1", "feat/x", MagicMock(), "implementation")


def test_remove_worktree_silent_when_not_registered(tmp_path):
    """Missing worktree → log INFO, no WARN, no subprocess call for `worktree remove`."""
    from orchestrator.orchestrate import _remove_worktree

    logger = MagicMock()
    with (
        patch("orchestrator.orchestrate.git_state.list_worktrees", return_value=[]),
        patch("orchestrator.orchestrate.git_state.worktree_for_branch", return_value=None),
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False),
        patch("orchestrator.orchestrate.subprocess.run") as mock_run,
    ):
        _remove_worktree("/repo", "/tmp/missing", "feat/x-impl_1", logger, "implementation")
    mock_run.assert_not_called()
    levels = [call.args[1] for call in logger.log.call_args_list]
    assert "WARN" not in levels


def test_remove_worktree_uses_git_registry_when_orchestrator_path_drifts(tmp_path):
    """Git reports a worktree on our branch under a different path → remove that one anyway.

    Mirrors the parallel-run failure mode where the orchestrator's recorded
    path no longer matches git's actual registry entry for the same branch.
    """
    from orchestrator.orchestrate import _remove_worktree

    registry = [{"path": "/tmp/wt-drifted", "branch": "feat/x-impl_1"}]
    logger = MagicMock()
    proc_ok = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch("orchestrator.orchestrate.git_state.list_worktrees", return_value=registry),
        patch("orchestrator.orchestrate.git_state.worktree_for_branch", return_value=None),
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False),
        patch("orchestrator.orchestrate.subprocess.run", return_value=proc_ok) as mock_run,
    ):
        _remove_worktree("/repo", "/tmp/missing", "feat/x-impl_1", logger, "implementation")
    cmds = [call.args[0] for call in mock_run.call_args_list]
    assert ["git", "-C", "/repo", "worktree", "remove", "--force", "/tmp/wt-drifted"] in cmds


def test_remove_worktree_skips_branch_delete_when_branch_still_held(tmp_path):
    """If a worktree still references the branch after removal, do NOT run `git branch -D`.

    Avoids the "branch is used by worktree" error reported in #147.
    """
    from orchestrator.orchestrate import _remove_worktree

    registry = [{"path": "/tmp/wt-a", "branch": "feat/x-impl_1"}]
    logger = MagicMock()
    failed_remove = MagicMock(returncode=1, stdout="", stderr="cannot remove")
    with (
        patch("orchestrator.orchestrate.git_state.list_worktrees", return_value=registry),
        patch("orchestrator.orchestrate.git_state.worktree_for_branch", return_value="/tmp/wt-a"),
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=True),
        patch("orchestrator.orchestrate.subprocess.run", return_value=failed_remove) as mock_run,
    ):
        _remove_worktree("/repo", "/tmp/wt-a", "feat/x-impl_1", logger, "implementation")
    cmds = [call.args[0] for call in mock_run.call_args_list]
    assert not any("branch" in c and "-D" in c for c in cmds)
    warn_msgs = [call.args[2] for call in logger.log.call_args_list if call.args[1] == "WARN"]
    assert any("still held by worktree" in m for m in warn_msgs)


def test_remove_worktree_removes_worktree_then_branch_on_happy_path(tmp_path):
    """Happy path: worktree at our path is removed first, then the branch is deleted."""
    from orchestrator.orchestrate import _remove_worktree

    registry = [{"path": "/tmp/wt-a", "branch": "feat/x-impl_1"}]
    logger = MagicMock()
    proc_ok = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch("orchestrator.orchestrate.git_state.list_worktrees", return_value=registry),
        patch("orchestrator.orchestrate.git_state.worktree_for_branch", return_value=None),
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=True),
        patch("orchestrator.orchestrate.subprocess.run", return_value=proc_ok) as mock_run,
    ):
        _remove_worktree("/repo", "/tmp/wt-a", "feat/x-impl_1", logger, "implementation")
    cmds = [call.args[0] for call in mock_run.call_args_list]
    remove_idx = next(i for i, c in enumerate(cmds) if "worktree" in c and "remove" in c)
    delete_idx = next(i for i, c in enumerate(cmds) if "branch" in c and "-D" in c)
    assert remove_idx < delete_idx


def test_merge_worktree_branch_aborts_on_conflict(tmp_path):
    """Merge conflict → call abort_merge, raise GitStateError with conflict message."""
    from orchestrator.orchestrate import _merge_worktree_branch

    failed = MagicMock(returncode=1, stdout="", stderr="CONFLICT (content): ...")
    with (
        patch("orchestrator.orchestrate.subprocess.run", return_value=failed),
        patch("orchestrator.orchestrate.git_state.has_merge_conflicts", return_value=True),
        patch("orchestrator.orchestrate.git_state.abort_merge") as mock_abort,
    ):
        with pytest.raises(GitStateError, match="merge conflict"):
            _merge_worktree_branch("/repo", "feat/x-impl_1", MagicMock(), "implementation")
    mock_abort.assert_called_once_with("/repo")


def test_slices_dispatcher_returns_blocked_on_dirty_tree(tmp_path):
    """Dirty repo at slice dispatch → structured 'blocked' signal, no slice runs."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {"decomposition": {"slice_files": ["S-01-a.md"], "slice_groups": [["S-01-a.md"]]}}

    with (
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False),
        patch("orchestrator.orchestrate.git_state.is_clean", return_value=False),
        patch("orchestrator.orchestrate.run_stage") as mock_rs,
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/repo"}, run_folder, ctx, signals)

    assert result["status"] == "blocked"
    assert "working tree not clean" in result["message"]
    mock_rs.assert_not_called()


def test_slices_dispatcher_converts_merge_conflict_to_blocked_signal(tmp_path):
    """A merge conflict in a parallel group → structured blocked signal; worktrees still cleaned up."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md", "S-02-b.md"]],
        }
    }
    impl_sig = {"status": "passed", "commit_hashes": ["c1"]}

    mock_future = MagicMock()
    mock_future.result.return_value = (impl_sig, 1.0)

    with (
        _patch_safe_git_state(),
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate._create_worktree", return_value="/tmp/wt"),
        patch("orchestrator.orchestrate._remove_worktree") as mock_rm,
        patch(
            "orchestrator.orchestrate._merge_worktree_branch",
            side_effect=GitStateError("merge conflict on 'feat/test-impl_1' — aborted"),
        ),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("concurrent.futures.ThreadPoolExecutor") as mock_exec_cls,
    ):
        mock_exec = MagicMock()
        mock_exec.__enter__ = MagicMock(return_value=mock_exec)
        mock_exec.__exit__ = MagicMock(return_value=False)
        mock_exec.submit.return_value = mock_future
        mock_exec_cls.return_value = mock_exec

        result = _dispatch_slices(stage, {"repo_root": "/repo"}, run_folder, ctx, signals)

    assert result["status"] == "blocked"
    assert "merge conflict" in result["message"]
    assert mock_rm.call_count == 2


def test_slices_dispatcher_blocks_when_worktree_creation_fails(tmp_path):
    """Pre-existing temp branch → worktree creation refuses, slice dispatch is skipped."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md", "S-02-b.md"]],
        }
    }

    with (
        _patch_safe_git_state(),
        patch("orchestrator.orchestrate._create_branch"),
        patch(
            "orchestrator.orchestrate._create_worktree",
            side_effect=GitStateError("branch already exists"),
        ),
        patch("orchestrator.orchestrate._remove_worktree"),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.orchestrate.run_stage") as mock_rs,
    ):
        result = _dispatch_slices(stage, {"repo_root": "/repo"}, run_folder, ctx, signals)

    assert result["status"] == "blocked"
    assert "branch already exists" in result["message"]
    mock_rs.assert_not_called()


# ── ADR-028: executive summary finalisation ───────────────────────────────────


def test_executive_summary_runs_after_successful_pipeline(tmp_path):
    """Always-on finalisation: on a clean run, _finalize_summary fires once with the
    final pr_url (None when no PR was created)."""
    stages = [{"stage": "discovery", "prompt": "prompts/discovery/default.md"}]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    discovery_passed = {"stage": "discovery", "status": "passed", "findings_files": []}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=discovery_passed),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        patch("orchestrator.orchestrate._finalize_summary") as mock_summary,
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    mock_summary.assert_called_once()
    kwargs = mock_summary.call_args.kwargs
    assert kwargs["pr_url"] is None
    assert kwargs["run_folder"] == run_folder_path
    assert kwargs["impl_branch"] == "feat/test"
    assert kwargs["base_branch"] == "main"


def test_executive_summary_runs_after_blocked_stage(tmp_path):
    """The finalisation step is wrapped in try/finally so sys.exit(1) does not skip it."""
    stages = [{"stage": "discovery", "prompt": "prompts/discovery/default.md"}]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=BLOCKED_SIGNAL),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.mark_pr_blocked"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        patch("orchestrator.orchestrate._finalize_summary") as mock_summary,
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    # Pipeline still exits non-zero on a blocked stage — the summary just runs first.
    assert exc_info.value.code == 1
    mock_summary.assert_called_once()


def test_executive_summary_runs_after_interactive_incomplete(tmp_path):
    """sys.exit(0) from an unfinished interactive stage must still fire the summary."""
    stages = [
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)
    blocked_signal = {"stage": "alignment", "status": "blocked", "message": "Artifact not created"}

    with (
        patch("orchestrator.orchestrate.run_interactive_stage", return_value=blocked_signal),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        patch("orchestrator.orchestrate._finalize_summary") as mock_summary,
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert exc_info.value.code == 0
    mock_summary.assert_called_once()


def test_executive_summary_receives_pr_url_when_pr_created(tmp_path):
    """When _finalize_pr returns a URL, that URL is forwarded to _finalize_summary."""
    stages = [{"stage": "discovery", "prompt": "prompts/discovery/default.md"}]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)
    discovery_passed = {"stage": "discovery", "status": "passed", "findings_files": []}

    with (
        patch(
            "orchestrator.orchestrate._git_setup.preflight",
            return_value=GitPreflightResult(
                base_branch="main",
                create_pr=True,
                origin=OriginInfo(url="git@github.com:org/repo.git", is_github=True, gh_repo="org/repo"),
            ),
        ),
        patch("orchestrator.orchestrate._sync_base_and_create_impl_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value=discovery_passed),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
        patch(
            "orchestrator.orchestrate._finalize_pr",
            return_value="https://github.com/org/repo/pull/42",
        ),
        patch("orchestrator.orchestrate._finalize_summary") as mock_summary,
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    mock_summary.assert_called_once()
    assert mock_summary.call_args.kwargs["pr_url"] == "https://github.com/org/repo/pull/42"


def test_executive_summary_failure_logs_warning_and_swallows(tmp_path):
    """Direct call: a run_stage exception during summary must be swallowed (returning None)
    with a warning logged, so the pipeline exit status is unaffected."""
    from orchestrator.agent_runner import AgentConfig
    from orchestrator.logger import OrchestratorLogger

    run_folder = tmp_path / "run"
    run_folder.mkdir()
    (run_folder / "plan.md").write_text("# plan\n")
    feature_dir = tmp_path / "feature"
    feature_dir.mkdir()
    (feature_dir / "overview.md").write_text("# Overview\n")
    project_log_path = str(tmp_path)
    logger = OrchestratorLogger(run_folder, project_log_path)
    agent_config = AgentConfig(backend="claude_code", model=None)

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=RuntimeError("boom")),
        patch("orchestrator.orchestrate.build_runner"),
    ):
        # Must not raise — exception is logged and swallowed.
        _real_finalize_summary(
            run_folder=run_folder,
            docs_root=str(tmp_path),
            project="myproject",
            project_log_path=project_log_path,
            feature_path="feature",
            repo_root="/tmp",
            impl_branch="feat/test",
            base_branch="main",
            pr_url=None,
            logger=logger,
            agent_config=agent_config,
        )


def test_executive_summary_blocked_status_logs_warning(tmp_path):
    """If the summary stage returns a blocked signal, no exception is raised and the
    signal is not saved to state."""
    from orchestrator.agent_runner import AgentConfig
    from orchestrator.logger import OrchestratorLogger

    run_folder = tmp_path / "run"
    run_folder.mkdir()
    (run_folder / "plan.md").write_text("# plan\n")
    feature_dir = tmp_path / "feature"
    feature_dir.mkdir()
    (feature_dir / "overview.md").write_text("# Overview\n")
    project_log_path = str(tmp_path)
    logger = OrchestratorLogger(run_folder, project_log_path)
    agent_config = AgentConfig(backend="claude_code", model=None)

    blocked = {"stage": "executive_summary", "status": "blocked", "message": "plan unreadable"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=blocked),
        patch("orchestrator.orchestrate.build_runner"),
        patch("orchestrator.orchestrate.state_mod.save_stage_signal") as mock_save_sig,
    ):
        _real_finalize_summary(
            run_folder=run_folder,
            docs_root=str(tmp_path),
            project="myproject",
            project_log_path=project_log_path,
            feature_path="feature",
            repo_root="/tmp",
            impl_branch="feat/test",
            base_branch="main",
            pr_url=None,
            logger=logger,
            agent_config=agent_config,
        )

    # Blocked summary must not be saved as if it had succeeded.
    mock_save_sig.assert_not_called()


def test_executive_summary_passes_pr_url_into_variables(tmp_path):
    """The pr_url argument flows into the stage template variables verbatim, falling back
    to 'not created' when None."""
    from orchestrator.agent_runner import AgentConfig
    from orchestrator.logger import OrchestratorLogger

    run_folder = tmp_path / "run"
    run_folder.mkdir()
    feature_dir = tmp_path / "feature"
    feature_dir.mkdir()
    (feature_dir / "overview.md").write_text("# Overview\n")
    logger = OrchestratorLogger(run_folder, str(tmp_path))
    agent_config = AgentConfig(backend="claude_code", model=None)

    summary_signal = {
        "stage": "executive_summary",
        "status": "passed",
        "summary_path": str(run_folder / "executive_summary.md"),
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=summary_signal) as mock_rs,
        patch("orchestrator.orchestrate.build_runner"),
        patch("orchestrator.orchestrate.state_mod.save_stage_signal"),
        patch("orchestrator.orchestrate.state_mod.save_stage_agent"),
    ):
        _real_finalize_summary(
            run_folder=run_folder,
            docs_root=str(tmp_path),
            project="myproject",
            project_log_path=str(tmp_path),
            feature_path="feature",
            repo_root="/tmp",
            impl_branch="feat/test",
            base_branch="main",
            pr_url="https://github.com/org/repo/pull/42",
            logger=logger,
            agent_config=agent_config,
        )

    variables = mock_rs.call_args.args[2]
    assert variables["pr_url"] == "https://github.com/org/repo/pull/42"
    assert variables["summary_path"] == str(run_folder / "executive_summary.md")

    # Now confirm the None fallback.
    with (
        patch("orchestrator.orchestrate.run_stage", return_value=summary_signal) as mock_rs2,
        patch("orchestrator.orchestrate.build_runner"),
        patch("orchestrator.orchestrate.state_mod.save_stage_signal"),
        patch("orchestrator.orchestrate.state_mod.save_stage_agent"),
    ):
        _real_finalize_summary(
            run_folder=run_folder,
            docs_root=str(tmp_path),
            project="myproject",
            project_log_path=str(tmp_path),
            feature_path="feature",
            repo_root="/tmp",
            impl_branch="feat/test",
            base_branch="main",
            pr_url=None,
            logger=logger,
            agent_config=agent_config,
        )

    assert mock_rs2.call_args.args[2]["pr_url"] == "not created"


# ── discovery unresolved-items aggregation (ADR-032) ──────────────────────────


def test_dispatch_tracks_aggregates_unresolved_items_across_tracks(tmp_path):
    """Each track's unresolved_questions/risks/assumptions_needed are flattened
    into one merged list per category on the parent discovery signal — those
    lists are the structured alignment inputs the next stage consumes.
    """
    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    logger = MagicMock()
    ctx = _PipelineContext(
        docs_root=str(tmp_path),
        project="myproject",
        project_log_path=str(tmp_path / "log.log"),
        logger=logger,
        branch="feat/test",
        project_config={"repo-root": "/tmp"},
        project_standards=[],
        runners={},
        agent_metadata={},
    )
    variables = {"run_folder": str(run_folder), "docs_root": str(tmp_path)}

    planning_signal = {
        "stage": "discovery-planning",
        "status": "passed",
        "tracks": [
            {"name": "track-a", "prompt_file": "/tmp/a.md", "focus": "x"},
            {"name": "track-b", "prompt_file": "/tmp/b.md", "focus": "y"},
        ],
    }

    track_a_signal = {
        "stage": "discovery-track-a",
        "status": "passed",
        "findings_file": "/tmp/a-findings.md",
        "summary": "track-a summary",
        "unresolved_questions": ["Which auth flow to reuse?"],
        "risks": ["Rate limiter may need tuning"],
        "assumptions_needed": ["Assume background jobs run in the existing worker"],
    }
    track_b_signal = {
        "stage": "discovery-track-b",
        "status": "passed",
        "findings_file": "/tmp/b-findings.md",
        "summary": "track-b summary",
        "unresolved_questions": ["What is the timeout?"],
        "risks": [],
        "assumptions_needed": [],
    }

    signals_in_order = iter([planning_signal, track_a_signal, track_b_signal])

    def fake_run_stage(*args, **kwargs):
        return next(signals_in_order)

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.expand_nodes", return_value={"track-a": "t1", "track-b": "t2"}),
        patch("orchestrator.orchestrate.update_plan_md"),
    ):
        sig = _dispatch_tracks(stage, variables, run_folder, ctx)

    assert sig["status"] == "passed"
    assert set(sig["unresolved_questions"]) == {"Which auth flow to reuse?", "What is the timeout?"}
    assert sig["risks"] == ["Rate limiter may need tuning"]
    assert sig["assumptions_needed"] == ["Assume background jobs run in the existing worker"]


def test_dispatch_tracks_returns_empty_lists_when_no_tracks_surface_items(tmp_path):
    """A passing discovery with no unresolved items still emits the three keys
    as empty lists, so alignment receives a well-formed contract."""
    from orchestrator.orchestrate import _dispatch_tracks

    stage = StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    ctx = _PipelineContext(
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
    variables = {"run_folder": str(run_folder), "docs_root": str(tmp_path)}

    planning_signal = {
        "stage": "discovery-planning",
        "status": "passed",
        "tracks": [{"name": "track-a", "prompt_file": "/tmp/a.md", "focus": "x"}],
    }
    track_a_signal = {
        "stage": "discovery-track-a",
        "status": "passed",
        "findings_file": "/tmp/a-findings.md",
        "summary": "ok",
    }

    signals_in_order = iter([planning_signal, track_a_signal])

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=lambda *a, **k: next(signals_in_order)),
        patch("orchestrator.orchestrate.expand_nodes", return_value={"track-a": "t1"}),
        patch("orchestrator.orchestrate.update_plan_md"),
    ):
        sig = _dispatch_tracks(stage, variables, run_folder, ctx)

    assert sig["unresolved_questions"] == []
    assert sig["risks"] == []
    assert sig["assumptions_needed"] == []


# ── alignment policy gate (ADR-032) ───────────────────────────────────────────


def test_apply_alignment_policy_noop_when_no_unresolved():
    """No unresolved_remaining → signal passes through untouched, no log emitted."""
    from orchestrator.orchestrate import _apply_alignment_policy
    from orchestrator.profile import AlignmentPolicy

    stage = StageConfig(name="alignment", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {"stage": "alignment", "status": "passed", "unresolved_remaining": []}
    logger = MagicMock()
    out = _apply_alignment_policy(stage, sig, logger)
    assert out is sig
    logger.log.assert_not_called()


def test_apply_alignment_policy_warns_by_default_when_unresolved_remain():
    """Default policy (None → warn) logs a warning but keeps status=passed."""
    from orchestrator.orchestrate import _apply_alignment_policy

    stage = StageConfig(name="alignment")  # no alignment_policy → default warn
    sig = {
        "stage": "alignment",
        "status": "passed",
        "unresolved_remaining": ["Decide caching strategy"],
    }
    logger = MagicMock()
    out = _apply_alignment_policy(stage, sig, logger)
    assert out["status"] == "passed"
    logger.log.assert_called_once()
    args = logger.log.call_args.args
    assert args[0] == "alignment"
    assert args[1] == "WARN"


def test_apply_alignment_policy_blocks_when_policy_block_and_items_remain():
    """policy=block + non-empty unresolved_remaining → signal flipped to blocked."""
    from orchestrator.orchestrate import _apply_alignment_policy
    from orchestrator.profile import AlignmentPolicy

    stage = StageConfig(name="alignment", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {
        "stage": "alignment",
        "status": "passed",
        "unresolved_remaining": ["Decide caching strategy", "Who owns retries?"],
    }
    logger = MagicMock()
    out = _apply_alignment_policy(stage, sig, logger)
    assert out["status"] == "blocked"
    assert "2 unresolved items" in out["message"]
    assert "Decide caching strategy" in out["message"]
    # Original signal must not be mutated — tests downstream of the gate may
    # still rely on the passed signal shape.
    assert sig["status"] == "passed"
    logger.log.assert_called_once()
    args = logger.log.call_args.args
    assert args[0] == "alignment"
    assert args[1] == "ERROR"


def test_apply_alignment_policy_treats_whitespace_only_entries_as_empty():
    """Empty strings and whitespace-only entries are not real residue — they
    must not trigger a block, and they must not emit a warn-log either."""
    from orchestrator.orchestrate import _apply_alignment_policy
    from orchestrator.profile import AlignmentPolicy

    stage = StageConfig(name="alignment", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {
        "stage": "alignment",
        "status": "passed",
        "unresolved_remaining": ["", "   ", "\t\n"],
    }
    logger = MagicMock()
    out = _apply_alignment_policy(stage, sig, logger)
    assert out is sig
    logger.log.assert_not_called()


def test_apply_alignment_policy_counts_only_non_empty_entries():
    """Whitespace-only entries are dropped from the count and preview, so a list
    like ["", "real item"] reports one residue item — not two."""
    from orchestrator.orchestrate import _apply_alignment_policy
    from orchestrator.profile import AlignmentPolicy

    stage = StageConfig(name="alignment", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {
        "stage": "alignment",
        "status": "passed",
        "unresolved_remaining": ["", "   ", "Decide caching strategy"],
    }
    logger = MagicMock()
    out = _apply_alignment_policy(stage, sig, logger)
    assert out["status"] == "blocked"
    assert "1 unresolved item:" in out["message"]
    assert "Decide caching strategy" in out["message"]


def test_apply_alignment_policy_noop_for_non_alignment_stage():
    """The gate only fires for the alignment stage — other stages pass through."""
    from orchestrator.orchestrate import _apply_alignment_policy
    from orchestrator.profile import AlignmentPolicy

    stage = StageConfig(name="specification", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {"stage": "specification", "status": "passed", "unresolved_remaining": ["irrelevant"]}
    out = _apply_alignment_policy(stage, sig, MagicMock())
    assert out is sig


def test_apply_alignment_policy_noop_when_status_not_passed():
    """Failed/blocked signals reach the normal halt path untouched."""
    from orchestrator.orchestrate import _apply_alignment_policy
    from orchestrator.profile import AlignmentPolicy

    stage = StageConfig(name="alignment", alignment_policy=AlignmentPolicy(on_unresolved="block"))
    sig = {"stage": "alignment", "status": "blocked", "message": "agent error"}
    out = _apply_alignment_policy(stage, sig, MagicMock())
    assert out is sig


def test_pipeline_blocks_when_alignment_policy_is_block_and_items_remain(tmp_path):
    """End-to-end gate: a passing alignment signal with unresolved_remaining
    halts the pipeline at the alignment stage when policy is block."""
    stages = [
        {
            "stage": "alignment",
            "prompt": "prompts/alignment/autonomous.md",
            "alignment_policy": {"on_unresolved": "block"},
        },
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    alignment_passed_with_residue = {
        "stage": "alignment",
        "status": "passed",
        "alignment_log": "/tmp/alignment-log.md",
        "qa_pair_count": 3,
        "qualifying_decisions": 1,
        "accepted_assumptions": [],
        "unresolved_remaining": ["Choose retry backoff strategy"],
    }
    spec_called = []

    def fake_run_stage(stage, *args, **kwargs):
        if stage == "alignment":
            return alignment_passed_with_residue
        spec_called.append(stage)
        return SPEC_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md") as mock_plan,
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert exc_info.value.code == 1
    assert spec_called == [], "specification must not run when alignment is blocked by policy"
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("alignment", "blocked") in plan_calls
    state = yaml.safe_load((run_folder_path / "_state.yaml").read_text())
    assert state.get("blocked_at") == "alignment"


def test_pipeline_proceeds_when_alignment_policy_is_warn_and_items_remain(tmp_path):
    """Default warn policy: pipeline continues past alignment even with leftovers."""
    stages = [
        {"stage": "alignment", "prompt": "prompts/alignment/autonomous.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    alignment_passed_with_residue = {
        "stage": "alignment",
        "status": "passed",
        "alignment_log": "/tmp/alignment-log.md",
        "qa_pair_count": 2,
        "qualifying_decisions": 1,
        "accepted_assumptions": ["Assume the new endpoint reuses existing auth middleware"],
        "unresolved_remaining": ["Decide retry backoff"],
    }
    called_stages: list[str] = []

    def fake_run_stage(stage, *args, **kwargs):
        called_stages.append(stage)
        if stage == "alignment":
            return alignment_passed_with_residue
        return SPEC_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert called_stages == ["alignment", "specification"]
    state = yaml.safe_load((run_folder_path / "_state.yaml").read_text())
    assert state.get("blocked_at") is None or state.get("blocked_at") == ""


def test_pipeline_proceeds_when_alignment_resolves_all_unresolved_via_assumption(tmp_path):
    """Acceptance: discovery emits an unresolved question, alignment resolves it
    with an accepted assumption, ``unresolved_remaining`` is empty, and the
    pipeline advances under the default policy."""
    stages = [
        {"stage": "alignment", "prompt": "prompts/alignment/autonomous.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    alignment_resolved = {
        "stage": "alignment",
        "status": "passed",
        "alignment_log": "/tmp/alignment-log.md",
        "qa_pair_count": 1,
        "qualifying_decisions": 0,
        "accepted_assumptions": ["Assume background jobs run in the existing worker"],
        "unresolved_remaining": [],
    }
    called_stages: list[str] = []

    def fake_run_stage(stage, *args, **kwargs):
        called_stages.append(stage)
        if stage == "alignment":
            return alignment_resolved
        return SPEC_SIGNAL

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert called_stages == ["alignment", "specification"]
