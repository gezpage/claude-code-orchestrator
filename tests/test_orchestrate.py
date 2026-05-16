from unittest.mock import MagicMock, patch

import pytest
import yaml

from orchestrator import orchestrate
from orchestrator._git import GitStateError
from orchestrator._git_setup import GitPreflightResult, OriginInfo
from orchestrator.orchestrate import _PipelineContext
from orchestrator.profile import ExpansionKind, StageConfig


@pytest.fixture(autouse=True)
def _stub_preflight_and_sync():
    """Stub the ADR-019 preflight and base-branch sync for every test in this file.

    Tests that explicitly exercise the preflight paths re-patch within their own
    `with` block; autouse means the rest of the suite is unaffected by the new
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
        patch("orchestrator.orchestrate.git_state.worktree_registered", return_value=False),
        patch("orchestrator.orchestrate.git_state.branch_exists", return_value=False),
        patch("orchestrator.orchestrate.subprocess.run") as mock_run,
    ):
        _remove_worktree("/repo", "/tmp/missing", "feat/x-impl_1", logger, "implementation")
    mock_run.assert_not_called()
    levels = [call.args[1] for call in logger.log.call_args_list]
    assert "WARN" not in levels


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
