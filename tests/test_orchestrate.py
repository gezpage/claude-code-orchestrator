import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import yaml

from orchestrator import orchestrate


# ── helpers ──────────────────────────────────────────────────────────────────

def _setup_docs(tmp_path, stages, profile_name="test", feature_path="feature"):
    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(
        "repo-root: /tmp\nlog_level: DEBUG\n"
    )
    profiles = project_dir / "workflow" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / f"{profile_name}.yaml").write_text(
        yaml.dump({"name": profile_name, "stages": stages})
    )
    feature_dir = tmp_path / feature_path
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "overview.md").write_text("# Feature Overview\n")
    return str(tmp_path)


def _git_ok():
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    r.stdout = ""
    return r


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
    "tracks": [{"name": "code-entry-points", "summary": "Found 3 relevant entry points in the auth module.", "findings_file": "/tmp/discovery-code-entry-points.md"}],
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
        {"stage": "discovery", "prompt": "prompts/discovery/default.md"},
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md", "prompt": "prompts/alignment/interactive.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
        {"stage": "decomposition", "prompt": "prompts/decomposition/default.md"},
        {
            "stage": "implementation",
            "prompt": "prompts/implementation/default.md",
        },
        {"stage": "qa", "prompt": "prompts/qa/default.md"},
        {
            "stage": "review",
            "prompts": {"architecture": "prompts/review/architecture.md"},
        },
        {"stage": "harvest", "prompt": "prompts/harvest/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages, feature_path="feature-xyz")

    # Create alignment-log.md so alignment auto-skips (simulates resumed run)
    runs_base = tmp_path / "projects" / "myproject" / "workflow" / "runs"

    stage_signals = [
        DISCOVERY_PLANNING_SIGNAL,   # discovery — planning phase
        DISCOVERY_TRACK_SIGNAL,      # discovery — single track (parallel dispatch)
        SPEC_SIGNAL,
        DECOMP_SIGNAL,
        IMPL_SIGNAL,  # called twice (2 slices)
        IMPL_SIGNAL,
        QA_SIGNAL,
        REVIEW_ARCH_SIGNAL,
        HARVEST_SIGNAL,
    ]
    signal_iter = iter(stage_signals)

    def fake_run_stage(stage, impl, variables, run_folder, docs_root, project, log_path, output_suffix="", cwd=None, prompt_file=None, schema_name=None):
        if stage == "review":
            assert "review_md" in variables
            assert "diff" in variables
            assert variables["round"] == "1"
        return next(signal_iter)

    git_mock = MagicMock(return_value=_git_ok())

    with patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage) as mock_rs, \
         patch("orchestrator.orchestrate.run_interactive_stage") as mock_ris, \
         patch("orchestrator.orchestrate.update_plan_md") as mock_plan, \
         patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()):
        # alignment-log.md must exist inside the actual run folder; patch resolve to a known path
        run_folder_path = runs_base / "feature-xyz" / "2026-01-01-run-1"
        run_folder_path.mkdir(parents=True)
        (run_folder_path / "alignment-log.md").write_text("# Alignment\n")

        with patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
            orchestrate.run_pipeline(
                docs_root, "myproject", "feature-xyz", "feat/test", "test"
            )

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
        {"stage": "discovery", "prompt": "prompts/discovery/default.md"},
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)
    # No alignment-log.md → interactive session launched but returns blocked (user didn't create artifact)
    blocked_signal = {"stage": "alignment", "status": "blocked", "message": "Artifact not created: alignment-log.md"}

    with patch("orchestrator.orchestrate.run_stage", side_effect=[DISCOVERY_PLANNING_SIGNAL, DISCOVERY_TRACK_SIGNAL]) as mock_rs, \
         patch("orchestrator.orchestrate.run_interactive_stage", return_value=blocked_signal), \
         patch("orchestrator.orchestrate.update_plan_md"), \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(
                docs_root, "myproject", "feature", "feat/test", "test"
            )

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

    with patch("orchestrator.orchestrate.run_stage", return_value=BLOCKED_SIGNAL), \
         patch("orchestrator.orchestrate.update_plan_md") as mock_plan, \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(
                docs_root, "myproject", "feature", "feat/test", "test"
            )

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
    (run_folder_path / "_state.yaml").write_text(
        _yaml.dump({"stages": {"discovery": "passed"}})
    )

    called_stages = []

    def fake_run_stage(stage, impl, variables, run_folder, docs_root, project, log_path, output_suffix="", cwd=None, prompt_file=None, schema_name=None):
        called_stages.append(stage)
        return SPEC_SIGNAL

    with patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage), \
         patch("orchestrator.orchestrate.update_plan_md"), \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        orchestrate.run_pipeline(
            docs_root, "myproject", "feature", "feat/test", "test", resume=True
        )

    assert "discovery" not in called_stages
    assert "specification" in called_stages


# ── branch created at implementation start ────────────────────────────────────

def test_branch_created_at_implementation_start(tmp_path):
    stages = [
        {"stage": "discovery", "prompt": "prompts/discovery/planning.md"},
        {"stage": "decomposition", "prompt": "prompts/decomposition/default.md"},
        {"stage": "implementation", "prompt": "prompts/implementation/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    call_order = []
    git_cmds = []
    sig_iter = iter([DISCOVERY_PLANNING_SIGNAL, DISCOVERY_TRACK_SIGNAL, DECOMP_SIGNAL, IMPL_SIGNAL, IMPL_SIGNAL])

    def fake_run_stage(stage, impl, variables, run_folder, docs_root, project, log_path, output_suffix="", cwd=None, prompt_file=None, schema_name=None):
        call_order.append(("run_stage", stage))
        return next(sig_iter)

    def fake_git(cmd, **kwargs):
        git_cmds.append(cmd)
        if "checkout" in cmd:
            call_order.append(("git_checkout",))
        return _git_ok()

    with patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage), \
         patch("orchestrator.orchestrate.update_plan_md"), \
         patch("orchestrator.orchestrate.subprocess.run", side_effect=fake_git), \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        orchestrate.run_pipeline(
            docs_root, "myproject", "feature", "feat/test", "test"
        )

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
    (run_folder_path / "alignment-log.md").write_text("# Alignment\n")

    called_stages = []

    def fake_run_stage(stage, impl, variables, run_folder, docs_root, project, log_path, output_suffix="", cwd=None, prompt_file=None, schema_name=None):
        called_stages.append(stage)
        return SPEC_SIGNAL

    with patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage), \
         patch("orchestrator.orchestrate.run_interactive_stage") as mock_ris, \
         patch("orchestrator.orchestrate.update_plan_md"), \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        orchestrate.run_pipeline(
            docs_root, "myproject", "feature", "feat/test", "test"
        )

    assert "alignment" not in called_stages
    mock_ris.assert_not_called()


# ── update_plan_md called after each stage ────────────────────────────────────

def test_plan_md_updated_after_each_stage(tmp_path):
    stages = [
        {"stage": "discovery", "prompt": "prompts/discovery/default.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    signals = [DISCOVERY_PLANNING_SIGNAL, DISCOVERY_TRACK_SIGNAL, SPEC_SIGNAL]
    sig_iter = iter(signals)

    with patch("orchestrator.orchestrate.run_stage", side_effect=lambda *a, **kw: next(sig_iter)), \
         patch("orchestrator.orchestrate.update_plan_md") as mock_plan, \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        orchestrate.run_pipeline(
            docs_root, "myproject", "feature", "feat/test", "test"
        )

    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "passed") in plan_calls
    assert ("specification", "passed") in plan_calls


# ── discovery fan-out ─────────────────────────────────────────────────────────

def test_discovery_fanout_calls_planning_then_tracks(tmp_path):
    stages = [
        {"stage": "discovery", "prompt": "prompts/discovery/planning.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    planning_signal = {
        "stage": "discovery-planning",
        "status": "passed",
        "tracks": [
            {"name": "code-entry-points", "prompt_file": "/tmp/stages/discovery-code-entry-points-prompt.md", "focus": "Find entry points"},
            {"name": "risk", "prompt_file": "/tmp/stages/discovery-risk-prompt.md", "focus": "Identify risks"},
        ],
    }
    track_signal_a = {"stage": "discovery-code-entry-points", "status": "passed", "findings_file": "/tmp/code.md", "summary": "Found 2 entry points"}
    track_signal_b = {"stage": "discovery-risk", "status": "passed", "findings_file": "/tmp/risk.md", "summary": "Low risk"}

    call_log = []
    sig_iter = iter([planning_signal, track_signal_a, track_signal_b, SPEC_SIGNAL])

    def fake_run_stage(stage, impl, variables, run_folder, docs_root, project, log_path, output_suffix="", cwd=None, prompt_file=None, schema_name=None):
        call_log.append({"stage": stage, "output_suffix": output_suffix, "schema_name": schema_name, "prompt_file": prompt_file})
        return next(sig_iter)

    with patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage), \
         patch("orchestrator.orchestrate.update_plan_md"), \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", "test")

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
    import yaml as _yaml
    sig_file = run_folder_path / "_state.yaml"
    # Just verify the pipeline reached specification (discovery signal saved correctly)
    spec_call = next(c for c in call_log if c["output_suffix"] not in ("planning", "code-entry-points", "risk"))
    assert spec_call["stage"] == "specification"


def test_discovery_blocked_when_planning_fails(tmp_path):
    stages = [
        {"stage": "discovery", "prompt": "prompts/discovery/planning.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    blocked_planning = {"stage": "discovery-planning", "status": "blocked", "message": "No overview"}

    with patch("orchestrator.orchestrate.run_stage", return_value=blocked_planning), \
         patch("orchestrator.orchestrate.update_plan_md") as mock_plan, \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", "test")

    assert exc_info.value.code == 1
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "blocked") in plan_calls


def test_discovery_blocked_when_any_track_fails(tmp_path):
    stages = [
        {"stage": "discovery", "prompt": "prompts/discovery/planning.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    planning_signal = {
        "stage": "discovery-planning", "status": "passed",
        "tracks": [
            {"name": "code", "prompt_file": "/tmp/code-prompt.md", "focus": "x"},
            {"name": "risk", "prompt_file": "/tmp/risk-prompt.md", "focus": "y"},
        ],
    }
    track_ok = {"stage": "discovery-code", "status": "passed", "findings_file": "/tmp/code.md", "summary": "ok"}
    track_fail = {"stage": "discovery-risk", "status": "blocked", "message": "Cannot access repo"}

    sig_iter = iter([planning_signal, track_ok, track_fail])

    with patch("orchestrator.orchestrate.run_stage", side_effect=lambda *a, **kw: next(sig_iter)), \
         patch("orchestrator.orchestrate.update_plan_md") as mock_plan, \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", "test")

    assert exc_info.value.code == 1
    plan_calls = [(c.args[1], c.args[2]) for c in mock_plan.call_args_list]
    assert ("discovery", "blocked") in plan_calls


# ── non-slice artifacts are filtered from slice_files before implementation ───

def test_implementation_filters_non_slice_files(tmp_path):
    stages = [
        {"stage": "implementation", "prompt": "prompts/implementation/default.md"},
    ]
    docs_root = _setup_docs(tmp_path, stages)
    run_folder_path = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    run_folder_path.mkdir(parents=True)

    real_slice = str(run_folder_path / "slices" / "S-01-do-the-thing.md")
    artifact = str(run_folder_path / "slices" / "dependency-graph.md")

    import yaml as _yaml
    (run_folder_path / "_state.yaml").write_text(_yaml.dump({
        "stages": {},
        "signals": {
            "decomposition": {
                "stage": "decomposition",
                "status": "passed",
                "slice_files": [real_slice, artifact],
            }
        },
    }))

    called_with = []

    def fake_run_stage(stage, impl, variables, run_folder, docs_root, project, log_path, output_suffix="", cwd=None, prompt_file=None, schema_name=None):
        called_with.append(variables.get("slice_file"))
        return IMPL_SIGNAL

    with patch("orchestrator.orchestrate.run_stage", side_effect=fake_run_stage), \
         patch("orchestrator.orchestrate.update_plan_md"), \
         patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()), \
         patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder_path):
        orchestrate.run_pipeline(
            docs_root, "myproject", "feature", "feat/test", "test"
        )

    assert called_with == [real_slice], (
        f"Expected only the real slice to be dispatched, got: {called_with}"
    )
    assert artifact not in called_with


# ── orchestrate.py source contains no open() calls ───────────────────────────

def test_orchestrate_source_has_no_open_calls():
    import orchestrator.orchestrate as orch_mod
    import inspect
    source = inspect.getsource(orch_mod)
    # Filter out this very assertion and comments
    lines = [
        line for line in source.splitlines()
        if "open(" in line and not line.strip().startswith("#")
    ]
    assert lines == [], f"orchestrate.py contains open() calls:\n" + "\n".join(lines)
