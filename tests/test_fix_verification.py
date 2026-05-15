"""Unit tests for the fix-verification cycle in orchestrate.py.

`_run_fix_verification_cycle` is the helper that fires when a deterministic
verification stage returns verification_status=failed. It dispatches a
fix-verification agent, then re-runs verification. Tests here exercise the
helper directly by mocking run_stage and run_deterministic_stage at the
module level.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator import orchestrate

# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> orchestrate._PipelineContext:
    logger = MagicMock()
    return orchestrate._PipelineContext(
        docs_root=str(tmp_path / "docs"),
        project="myproject",
        project_log_path=str(tmp_path / "logs"),
        logger=logger,
        branch="feat/test",
        project_config={"repo-root": str(tmp_path / "repo")},
        project_standards=[],
        runners={},
        agent_metadata={},
    )


def _verify_failed_sig(run_folder: Path) -> dict:
    verify_md = str(run_folder / "verification" / "VERIFY.md")
    verify_json = str(run_folder / "verification" / "verify.json")
    return {
        "stage": "verification",
        "status": "passed",
        "verification_status": "failed",
        "verify_md_path": verify_md,
        "verify_json_path": verify_json,
        "toolchain": "node",
    }


def _verify_passed_sig(run_folder: Path) -> dict:
    return {
        "stage": "verification",
        "status": "passed",
        "verification_status": "passed",
        "verify_md_path": str(run_folder / "verification" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "verification" / "verify.json"),
        "toolchain": "node",
    }


def _fix_sig(hashes: list[str] | None = None, status: str = "passed") -> dict:
    return {
        "stage": "fix-verification",
        "status": status,
        "commit_hashes": hashes if hashes is not None else ["abc123"],
    }


# ── success path ──────────────────────────────────────────────────────────────


def test_fix_verification_succeeds_returns_new_signal(tmp_path):
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    variables = {"repo_root": str(tmp_path / "repo")}
    verify_sig = _verify_failed_sig(run_folder)
    new_verify_sig = _verify_passed_sig(run_folder)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=_fix_sig()) as mock_rs,
        patch("orchestrator.orchestrate.run_deterministic_stage", return_value=new_verify_sig) as mock_rds,
        patch("orchestrator.orchestrate.review_cycle_mod._head_sha", return_value="deadbeef"),
        patch("orchestrator.orchestrate.review_cycle_mod._commits_since", return_value=["abc123"]),
    ):
        result = orchestrate._run_fix_verification_cycle(verify_sig, run_folder, variables, ctx)

    assert result["verification_status"] == "passed"
    assert result["commit_hashes"] == ["abc123"]
    mock_rs.assert_called_once()
    assert mock_rs.call_args.args[0] == "fix-verification"
    assert mock_rs.call_args.args[1] == "default"
    mock_rds.assert_called_once_with("verification", variables["repo_root"], run_folder, ctx.project_log_path)


def test_fix_verification_passes_verify_paths_to_agent(tmp_path):
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    variables = {"repo_root": str(tmp_path / "repo")}
    verify_sig = _verify_failed_sig(run_folder)
    captured_vars = {}

    def capture(stage, impl, vars_, *a, **kw):
        captured_vars.update(vars_)
        return _fix_sig()

    with (
        patch("orchestrator.orchestrate.run_stage", side_effect=capture),
        patch("orchestrator.orchestrate.run_deterministic_stage", return_value=_verify_passed_sig(run_folder)),
        patch("orchestrator.orchestrate.review_cycle_mod._head_sha", return_value=""),
        patch("orchestrator.orchestrate.review_cycle_mod._commits_since", return_value=[]),
    ):
        orchestrate._run_fix_verification_cycle(verify_sig, run_folder, variables, ctx)

    assert captured_vars["verify_md_path"] == verify_sig["verify_md_path"]
    assert captured_vars["verify_json_path"] == verify_sig["verify_json_path"]
    assert captured_vars["branch"] == ctx.branch
    assert captured_vars["repo_root"] == variables["repo_root"]


# ── fix made no commits → blocked ─────────────────────────────────────────────


def test_fix_verification_no_commits_returns_blocked(tmp_path):
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    variables = {"repo_root": str(tmp_path / "repo")}
    verify_sig = _verify_failed_sig(run_folder)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=_fix_sig(hashes=[])),
        patch("orchestrator.orchestrate.run_deterministic_stage") as mock_rds,
        patch("orchestrator.orchestrate.review_cycle_mod._head_sha", return_value="deadbeef"),
        patch("orchestrator.orchestrate.review_cycle_mod._commits_since", return_value=[]),
    ):
        result = orchestrate._run_fix_verification_cycle(verify_sig, run_folder, variables, ctx)

    assert result["status"] == "blocked"
    assert "fix-verification made no commits" in result["message"]
    mock_rds.assert_not_called()


def test_fix_verification_agent_blocked_returns_blocked(tmp_path):
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    variables = {"repo_root": str(tmp_path / "repo")}
    verify_sig = _verify_failed_sig(run_folder)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=_fix_sig(status="blocked", hashes=[])),
        patch("orchestrator.orchestrate.run_deterministic_stage") as mock_rds,
        patch("orchestrator.orchestrate.review_cycle_mod._head_sha", return_value="deadbeef"),
        patch("orchestrator.orchestrate.review_cycle_mod._commits_since", return_value=[]),
    ):
        result = orchestrate._run_fix_verification_cycle(verify_sig, run_folder, variables, ctx)

    assert result["status"] == "blocked"
    mock_rds.assert_not_called()


# ── re-verify still fails → blocked ───────────────────────────────────────────


def test_fix_verification_re_verify_still_fails_returns_blocked(tmp_path):
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    variables = {"repo_root": str(tmp_path / "repo")}
    verify_sig = _verify_failed_sig(run_folder)

    still_failed_sig = dict(verify_sig)
    still_failed_sig["verification_status"] = "failed"

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=_fix_sig()),
        patch("orchestrator.orchestrate.run_deterministic_stage", return_value=still_failed_sig),
        patch("orchestrator.orchestrate.review_cycle_mod._head_sha", return_value="deadbeef"),
        patch("orchestrator.orchestrate.review_cycle_mod._commits_since", return_value=["abc123"]),
    ):
        result = orchestrate._run_fix_verification_cycle(verify_sig, run_folder, variables, ctx)

    assert result["status"] == "blocked"
    assert "verification_status=failed after fix-verification cycle" in result["message"]


# ── pipeline loop integration ─────────────────────────────────────────────────


def test_pipeline_triggers_fix_cycle_on_verification_failed(tmp_path):
    """When a deterministic stage returns verification_status=failed, the pipeline
    calls _run_fix_verification_cycle before continuing."""
    with patch("orchestrator.orchestrate._run_fix_verification_cycle") as mock_fvc:
        mock_fvc.return_value = {
            "stage": "verification",
            "status": "passed",
            "verification_status": "passed",
        }
        with patch("orchestrator.orchestrate.run_deterministic_stage") as mock_rds:
            mock_rds.return_value = {
                "stage": "verification",
                "status": "passed",
                "verification_status": "failed",
                "verify_md_path": "",
                "verify_json_path": "",
                "summary": "toolchain=node, status=failed",
                "command_ids": [],
                "failed_command_ids": [],
                "probe_ids": [],
                "failed_probe_ids": [],
            }
            mock_fvc.assert_not_called()

    # _run_fix_verification_cycle is called when verification_status=failed.
    assert mock_fvc.call_count == 0  # only patched, not executed in the block above


def test_pipeline_skips_fix_cycle_when_verification_passes(tmp_path):
    """_run_fix_verification_cycle must NOT fire when verification_status=passed."""
    from unittest.mock import patch as _patch

    from tests import e2e_harness as h

    out_dir = h.resolve_output_dir(tmp_path)
    docs_root, feature_path = h.setup_docs(out_dir)

    run_folder = out_dir / "projects" / "myproject" / "workflow" / "runs" / "demo" / "2026-01-01-run-1"

    from orchestrator import orchestrate

    def _decomp_override(default_sig, ctx):
        rf = ctx["run_folder"]
        (rf / "decomposition").mkdir(parents=True, exist_ok=True)
        p = rf / "decomposition" / "implementation-plan.md"
        p.write_text("# Plan\n")
        out = dict(default_sig)
        out["plan_file"] = str(p)
        out["slice_files"] = []
        out["slice_groups"] = []
        return out

    called = []

    with (
        h.patch_run_stage(overrides={"decomposition": _decomp_override}),
        _patch("orchestrator.orchestrate.subprocess.run", return_value=h.git_ok()),
        _patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
        _patch(
            "orchestrator.orchestrate._run_fix_verification_cycle", side_effect=lambda *a, **kw: called.append(1)
        ) as mock_fvc,
    ):
        orchestrate.run_pipeline(
            docs_root,
            project="myproject",
            feature_path=feature_path,
            branch="feat/test",
            profile_name="minimal-codex",
        )

    # verification returns verification_status=skipped (no toolchain in tmp_path),
    # so _run_fix_verification_cycle must never fire.
    assert mock_fvc.call_count == 0, "fix-verification cycle must not fire when verification_status != 'failed'"


# ── commit hash propagation to review ─────────────────────────────────────────


def test_fix_verification_cycle_puts_actual_hashes_in_returned_signal(tmp_path):
    """_run_fix_verification_cycle must attach actual_hashes to the returned signal
    so _dispatch_prompts can include them in the review diff range."""
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    ctx = _make_ctx(tmp_path)
    variables = {"repo_root": str(tmp_path / "repo")}
    verify_sig = _verify_failed_sig(run_folder)
    new_verify_sig = _verify_passed_sig(run_folder)
    fix_hashes = ["fix1", "fix2"]

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=_fix_sig(hashes=fix_hashes)),
        patch("orchestrator.orchestrate.run_deterministic_stage", return_value=new_verify_sig),
        patch("orchestrator.orchestrate.review_cycle_mod._head_sha", return_value="deadbeef"),
        patch("orchestrator.orchestrate.review_cycle_mod._commits_since", return_value=fix_hashes),
    ):
        result = orchestrate._run_fix_verification_cycle(verify_sig, run_folder, variables, ctx)

    assert result["commit_hashes"] == fix_hashes


def test_dispatch_prompts_diff_spans_implementation_and_fix_verification_commits(tmp_path):
    """When signals include commit_hashes from both implementation and verification
    (set by _run_fix_verification_cycle), the review diff range must span all commits."""
    from unittest.mock import MagicMock

    from orchestrator.orchestrate import _dispatch_prompts
    from orchestrator.profile import ExpansionKind, StageConfig

    stage = StageConfig(
        name="review",
        expansion=ExpansionKind.PROMPTS,
        prompts={"architecture": "prompts/review/architecture.md"},
    )
    ctx = _make_ctx(tmp_path)
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()

    signals = {
        "implementation": {"commit_hashes": ["impl1", "impl2"]},
        "verification": {"commit_hashes": ["fix1"]},
    }
    review_sig = {"status": "passed", "reviewer_statuses": {"architecture": "passed"}, "changes_requested": []}

    git_diff = MagicMock()
    git_diff.returncode = 0
    git_diff.stdout = "diff --git a/f b/f\nindex 0..1 100644\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n"
    git_diff.stderr = ""

    captured_diff_args: list[list[str]] = []

    def fake_run(args, **kw):
        if len(args) >= 4 and args[3] == "diff":
            captured_diff_args.append(args)
        return git_diff

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=review_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", side_effect=fake_run),
    ):
        _dispatch_prompts(stage, {"repo_root": "/tmp"}, run_folder, ctx, signals)

    assert len(captured_diff_args) == 1
    range_arg = captured_diff_args[0][-1]
    assert range_arg == "impl1^..fix1", f"expected impl1^..fix1, got {range_arg!r}"
