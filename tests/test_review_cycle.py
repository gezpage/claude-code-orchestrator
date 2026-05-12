import pytest
from pathlib import Path
from unittest.mock import patch, call, MagicMock
import yaml

from orchestrator import review_cycle


# ── helpers ───────────────────────────────────────────────────────────────────

def _setup(tmp_path):
    run_folder = tmp_path / "run-1"
    run_folder.mkdir()
    log_path = tmp_path / "logs"
    log_path.mkdir()
    return run_folder, str(log_path)


def _review_signal(statuses: dict) -> dict:
    return {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": statuses,
        "changes_requested": [r for r, s in statuses.items() if s == "changes-requested"],
    }


def _fix_sig():
    return {
        "stage": "fix-implementation",
        "status": "passed",
        "commit_hashes": ["abc123"],
        "diff": "fixed the thing",
    }


def _reviewer_sig(reviewer, verdict):
    return {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {reviewer: verdict},
    }


# ── all-passed on first check — no cycles ────────────────────────────────────

def test_all_passed_immediately(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"architecture": "approved", "tests": "approved"})

    with patch("orchestrator.review_cycle.run_stage") as mock_rs:
        result = review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path
        )

    assert result == {"all_passed": True}
    mock_rs.assert_not_called()


# ── one cycle resolves all reviewers ─────────────────────────────────────────

def test_one_cycle_resolves(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"implementation": "changes-requested"})

    stage_returns = [
        _fix_sig(),
        _reviewer_sig("implementation", "approved"),
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)) as mock_rs:
        result = review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path
        )

    assert result == {"all_passed": True}
    assert mock_rs.call_count == 2
    # Verify only the changes-requested reviewer was re-run
    reviewer_call = mock_rs.call_args_list[1]
    assert reviewer_call.args[0] == "review"
    assert reviewer_call.args[1] == "implementation"


# ── two cycles resolve ────────────────────────────────────────────────────────

def test_two_cycles_resolve(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"architecture": "changes-requested"})

    stage_returns = [
        _fix_sig(),
        _reviewer_sig("architecture", "changes-requested"),  # cycle 1: still wants changes
        _fix_sig(),
        _reviewer_sig("architecture", "approved"),            # cycle 2: resolved
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        result = review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path
        )

    assert result == {"all_passed": True}


# ── two cycles fail → blocked ─────────────────────────────────────────────────

def test_two_cycles_fail_blocked(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"tests": "changes-requested"})

    # Both cycles leave tests at changes-requested
    stage_returns = [
        _fix_sig(),
        _reviewer_sig("tests", "changes-requested"),
        _fix_sig(),
        _reviewer_sig("tests", "changes-requested"),
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)) as mock_rs:
        result = review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path
        )

    assert result["all_passed"] is False
    assert result["blocked"] is True
    assert "tests" in result["reviewers"]
    # Exactly 2 fix + 2 reviewer calls = 4 total; third cycle never dispatched
    assert mock_rs.call_count == 4


# ── max 2 iterations enforced — third cycle never dispatched ─────────────────

def test_max_iterations_exactly_2(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"architecture": "changes-requested", "tests": "changes-requested"})

    calls = []

    def counting_stage(stage, impl, variables, run_folder, docs_root, project, log_path, cwd=None, **kwargs):
        calls.append((stage, impl))
        if stage == "fix-implementation":
            return _fix_sig()
        return _reviewer_sig(impl, "changes-requested")  # never resolves

    with patch("orchestrator.review_cycle.run_stage", side_effect=counting_stage):
        result = review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path
        )

    fix_calls = [c for c in calls if c[0] == "fix-implementation"]
    assert len(fix_calls) == 2, "Must run exactly 2 fix cycles"
    assert result["all_passed"] is False


# ── only changes-requested reviewers re-run ───────────────────────────────────

def test_only_failed_reviewers_rerun(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({
        "architecture": "approved",
        "implementation": "changes-requested",
        "tests": "approved",
    })

    stage_returns = [
        _fix_sig(),
        _reviewer_sig("implementation", "approved"),
    ]
    ret_iter = iter(stage_returns)
    called_reviewers = []

    def tracking_stage(stage, impl, variables, run_folder, docs_root, project, log_path, cwd=None, **kwargs):
        if stage == "review":
            called_reviewers.append(impl)
        return next(ret_iter)

    with patch("orchestrator.review_cycle.run_stage", side_effect=tracking_stage):
        result = review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path
        )

    assert result == {"all_passed": True}
    assert called_reviewers == ["implementation"], (
        f"Only 'implementation' should be re-run, got: {called_reviewers}"
    )
    assert "architecture" not in called_reviewers
    assert "tests" not in called_reviewers


# ── review.md round numbering ─────────────────────────────────────────────────

def test_review_md_round_numbering(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"architecture": "changes-requested"})

    stage_returns = [
        _fix_sig(),
        _reviewer_sig("architecture", "changes-requested"),  # cycle 1
        _fix_sig(),
        _reviewer_sig("architecture", "approved"),            # cycle 2
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = (run_folder / "review" / "review-log.md").read_text()
    assert "Round 2" in content
    assert "Round 3" in content


# ── review log written to review/review-log.md, not run root ─────────────────

def test_review_log_in_review_subfolder(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"architecture": "changes-requested"})

    stage_returns = [_fix_sig(), _reviewer_sig("architecture", "approved")]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    assert (run_folder / "review" / "review-log.md").exists(), "review-log.md should be in review/ subfolder"
    assert not (run_folder / "review.md").exists(), "review.md must not appear at run root"


# ── no new run folder created ─────────────────────────────────────────────────

def test_no_new_run_folder(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"implementation": "changes-requested"})

    stage_returns = [_fix_sig(), _reviewer_sig("implementation", "approved")]
    ret_iter = iter(stage_returns)
    dirs_before = set(tmp_path.iterdir())

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    dirs_after = set(d for d in tmp_path.iterdir() if d.is_dir())
    new_dirs = dirs_after - dirs_before
    assert new_dirs == set(), f"Unexpected new directories created: {new_dirs}"


# ── plan updates during fix cycles ───────────────────────────────────────────

def test_plan_add_fix_cycle_called_per_cycle(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"tests": "changes-requested"})

    stage_returns = [
        _fix_sig(),
        _reviewer_sig("tests", "changes-requested"),
        _fix_sig(),
        _reviewer_sig("tests", "approved"),
    ]
    ret_iter = iter(stage_returns)

    with (
        patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)),
        patch("orchestrator.review_cycle.plan_mod") as mock_plan,
    ):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    add_calls = [c for c in mock_plan.add_fix_cycle_node.call_args_list]
    assert len(add_calls) == 2
    # Cycle 1: reviewers=["tests"]
    assert add_calls[0].args[1] == 1
    assert add_calls[0].args[2] == ["tests"]
    # Cycle 2: same reviewer still changes-requested
    assert add_calls[1].args[1] == 2


def test_reviewer_cwd_set_to_repo_root(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"implementation": "changes-requested"})

    stage_returns = [_fix_sig(), _reviewer_sig("implementation", "approved")]
    ret_iter = iter(stage_returns)
    reviewer_cwds = []

    def tracking_stage(stage, impl, variables, run_folder, docs_root, project, log_path, cwd=None, **kwargs):
        if stage == "review":
            reviewer_cwds.append(cwd)
        return next(ret_iter)

    with patch("orchestrator.review_cycle.run_stage", side_effect=tracking_stage):
        review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path,
            repo_root="/path/to/repo",
        )

    assert reviewer_cwds == ["/path/to/repo"]


def test_reviewer_vars_include_repo_root(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"implementation": "changes-requested"})

    stage_returns = [_fix_sig(), _reviewer_sig("implementation", "approved")]
    ret_iter = iter(stage_returns)
    reviewer_vars_seen = []

    def tracking_stage(stage, impl, variables, run_folder, docs_root, project, log_path, cwd=None, **kwargs):
        if stage == "review":
            reviewer_vars_seen.append(variables.get("repo_root"))
        return next(ret_iter)

    with patch("orchestrator.review_cycle.run_stage", side_effect=tracking_stage):
        review_cycle.run(
            run_folder, "/docs", "proj", "feat/x", signal, log_path,
            repo_root="/path/to/repo",
        )

    assert reviewer_vars_seen == ["/path/to/repo"]


def test_plan_update_called_for_fix_and_rerun_nodes(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"tests": "changes-requested"})

    stage_returns = [_fix_sig(), _reviewer_sig("tests", "approved")]
    ret_iter = iter(stage_returns)

    with (
        patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)),
        patch("orchestrator.review_cycle.plan_mod") as mock_plan,
    ):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    update_calls = mock_plan.update_plan_md.call_args_list
    updated_nodes = [c.args[1] for c in update_calls]
    # fix_impl_1 and review_tests_2 must be updated
    assert "fix_impl_1" in updated_nodes
    assert "review_tests_2" in updated_nodes
    # The re-review node should be marked passed (approved verdict)
    rerun_call = next(c for c in update_calls if c.args[1] == "review_tests_2")
    assert rerun_call.args[2] == "passed"
