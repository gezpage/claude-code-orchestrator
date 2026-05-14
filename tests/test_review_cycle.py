from unittest.mock import patch

import pytest

from orchestrator import review_cycle

# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _bypass_diff_validator(request, monkeypatch):
    """Cycle unit tests mock `run_stage` and don't set up a real git repo, so
    `_write_round_diff` returns '' and `is_valid_diff_file` would reject it. Bypass the
    validator here so the existing tests exercise the orchestration logic. Tests marked
    `@pytest.mark.real_validator` (validator unit tests, and tests for the new gate)
    keep the real implementation."""
    if request.node.get_closest_marker("real_validator"):
        return
    monkeypatch.setattr("orchestrator.review_cycle.is_valid_diff_file", lambda p: True)


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
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

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
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

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
        _reviewer_sig("architecture", "approved"),  # cycle 2: resolved
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

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
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

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
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    fix_calls = [c for c in calls if c[0] == "fix-implementation"]
    assert len(fix_calls) == 2, "Must run exactly 2 fix cycles"
    assert result["all_passed"] is False


# ── only changes-requested reviewers re-run ───────────────────────────────────


def test_only_failed_reviewers_rerun(tmp_path):
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal(
        {
            "architecture": "approved",
            "implementation": "changes-requested",
            "tests": "approved",
        }
    )

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
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    assert result == {"all_passed": True}
    assert called_reviewers == ["implementation"], f"Only 'implementation' should be re-run, got: {called_reviewers}"
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
        _reviewer_sig("architecture", "approved"),  # cycle 2
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
            run_folder,
            "/docs",
            "proj",
            "feat/x",
            signal,
            log_path,
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
            run_folder,
            "/docs",
            "proj",
            "feat/x",
            signal,
            log_path,
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


# ── fix divider injection ─────────────────────────────────────────────────────


def test_fix_divider_injected_between_rounds(tmp_path):
    """A fix-commit divider is appended to review-log.md before the next review round."""
    run_folder, log_path = _setup(tmp_path)
    review_md = run_folder / "review" / "review-log.md"
    review_md.parent.mkdir()
    review_md.write_text("---\nreviewer_statuses: {}\n---\n## Tests Review — Round 1\nsome content\n")

    signal = _review_signal({"tests": "changes-requested"})
    stage_returns = [
        {
            "stage": "fix-implementation",
            "status": "passed",
            "commit_hashes": ["abc123"],
            "commit_messages": ["fix: add async dlq test (abc123)"],
            "diff": "",
        },
        _reviewer_sig("tests", "approved"),
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = review_md.read_text()
    assert "Fix Cycle 2" in content
    assert "fix: add async dlq test (abc123)" in content
    # Divider appears before the Round 2 section
    fix_pos = content.index("Fix Cycle 2")
    round2_pos = content.index("Round 2")
    assert fix_pos < round2_pos


def test_fix_divider_not_injected_when_review_log_absent(tmp_path):
    """If review-log.md doesn't exist yet, inject is a no-op — no error raised."""
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"tests": "changes-requested"})

    stage_returns = [_fix_sig(), _reviewer_sig("tests", "approved")]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    assert result == {"all_passed": True}


# ── findings summary written to plan.md ──────────────────────────────────────


def _signal_with_findings(statuses: dict, findings: dict) -> dict:
    return {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": statuses,
        "reviewer_findings": findings,
        "changes_requested": [r for r, s in statuses.items() if s == "changes-requested"],
    }


def test_findings_summary_appended_to_plan_md(tmp_path):
    """After all cycles resolve, a findings table is written to plan.md."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n\n## Review\n_some content_\n")

    signal = _signal_with_findings(
        {"tests": "changes-requested"},
        {"tests": ["Async onDeadLetter await contract untested", "withRetry has no direct unit tests"]},
    )
    stage_returns = [_fix_sig(), _reviewer_sig("tests", "approved")]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = plan_md.read_text()
    assert "## Review Findings" in content
    assert "Async onDeadLetter await contract untested" in content
    assert "withRetry has no direct unit tests" in content
    # resolved_cycle=1 (first loop iteration) → label "Fix Cycle 2" matching diagram convention
    assert "Fix Cycle 2" in content


def test_findings_summary_marks_unresolved_after_max_cycles(tmp_path):
    """Findings still open after all cycles are marked Unresolved."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n")

    signal = _signal_with_findings(
        {"tests": "changes-requested"},
        {"tests": ["Critical untested contract"]},
    )
    stage_returns = [
        _fix_sig(),
        _reviewer_sig("tests", "changes-requested"),
        _fix_sig(),
        _reviewer_sig("tests", "changes-requested"),
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = plan_md.read_text()
    assert "Critical untested contract" in content
    assert "Unresolved" in content
    assert "Fix Cycle" not in content.split("Critical untested contract")[1].split("\n")[0]


def test_findings_summary_appended_at_end_when_no_markers(tmp_path):
    """When plan.md has no File Manifest/Run Summary markers, findings are appended at end."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n")

    signal = _signal_with_findings(
        {"tests": "changes-requested"},
        {"tests": ["Some blocking issue"]},
    )
    stage_returns = [_fix_sig(), _reviewer_sig("tests", "approved")]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = plan_md.read_text()
    assert "## Review Findings" in content
    assert "Some blocking issue" in content


def test_append_findings_summary_else_branch_no_markers(tmp_path):
    """Directly tests _append_findings_summary when plan.md has no section markers."""
    from orchestrator.review_cycle import _append_findings_summary

    plan_md = tmp_path / "plan.md"
    plan_md.write_text("# Project\n")
    findings_map = {"tests": [("Some issue", None)]}
    _append_findings_summary(plan_md, findings_map, {"tests": "changes-requested"})

    content = plan_md.read_text()
    assert "## Review Findings" in content
    assert "Some issue → Unresolved" in content
    assert content.index("# Project") < content.index("## Review Findings")


def test_write_round_diff_persists_real_patch(tmp_path):
    """The orchestrator writes a real git diff to review/diff-round-N.patch after fix cycles —
    reviewers must not see a prose summary masquerading as a diff."""
    import subprocess as sp

    from orchestrator.review_cycle import _write_round_diff

    # Build a tiny git repo with two commits so the diff command produces output.
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "-C", str(repo), "init", "-q"], check=True)
    sp.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("a\n")
    sp.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
    (repo / "f.txt").write_text("a\nb\n")
    sp.run(["git", "-C", str(repo), "commit", "-q", "-am", "change"], check=True)
    sha = sp.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    run_folder = tmp_path / "run"
    run_folder.mkdir()
    diff_path = _write_round_diff(run_folder, str(repo), [sha], 2)

    assert diff_path == str(run_folder / "review" / "diff-round-2.patch")
    written = (run_folder / "review" / "diff-round-2.patch").read_text()
    assert "+b" in written
    assert "diff --git" in written


def test_write_round_diff_no_commits_returns_empty(tmp_path):
    from orchestrator.review_cycle import _write_round_diff

    assert _write_round_diff(tmp_path, "/tmp/anywhere", [], 2) == ""
    assert _write_round_diff(tmp_path, "", ["abc123"], 2) == ""


def test_accepted_risks_written_when_cycle_runs(tmp_path):
    """Non-blocking findings from round 1 are persisted as accepted risks even when
    a fix cycle resolves the blocking findings."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n")

    signal = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"tests": "changes-requested"},
        "reviewer_findings": {"tests": ["Blocking gap"]},
        "reviewer_non_blocking_findings": {"tests": ["Names could be clearer", "Helper duplicates fixture"]},
        "changes_requested": ["tests"],
    }
    stage_returns = [_fix_sig(), _reviewer_sig("tests", "approved")]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = plan_md.read_text()
    assert "Accepted Risks (non-blocking)" in content
    assert "Names could be clearer" in content
    assert "Helper duplicates fixture" in content


def test_accepted_risks_persisted_when_cycle_exhausts_max_iterations(tmp_path):
    """Non-blocking findings must still be written to plan.md when the fix cycle exhausts
    its iteration limit without resolving blocking findings. This exercises the second
    `append_findings_summary` call site in review_cycle.run."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n")

    signal = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"tests": "changes-requested"},
        "reviewer_findings": {"tests": ["Critical untested contract"]},
        "reviewer_non_blocking_findings": {"tests": ["Risk recorded in round 1"]},
        "changes_requested": ["tests"],
    }
    # Both fix cycles fail to resolve the blocking finding — exhausts _MAX_CYCLES.
    stage_returns = [
        _fix_sig(),
        _reviewer_sig("tests", "changes-requested"),
        _fix_sig(),
        _reviewer_sig("tests", "changes-requested"),
    ]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    assert result["all_passed"] is False
    content = plan_md.read_text()
    assert "Accepted Risks (non-blocking)" in content
    assert "Risk recorded in round 1" in content


def test_accepted_risks_persisted_when_cycle_aborts_on_invalid_diff(tmp_path):
    """Non-blocking findings from round 1 must survive even when the fix cycle aborts
    early on an invalid diff."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n")

    signal = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"tests": "changes-requested"},
        "reviewer_findings": {"tests": ["Blocking gap"]},
        "reviewer_non_blocking_findings": {"tests": ["Risk recorded before fix-cycle abort"]},
        "changes_requested": ["tests"],
    }

    # Force the validator to reject the diff so the cycle aborts on its first iteration.
    with (
        patch("orchestrator.review_cycle.is_valid_diff_file", return_value=False),
        patch("orchestrator.review_cycle.run_stage", return_value=_fix_sig()),
    ):
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    assert result["all_passed"] is False
    content = plan_md.read_text()
    assert "Risk recorded before fix-cycle abort" in content


def test_accepted_risks_merge_from_later_round(tmp_path):
    """Non-blocking findings raised by a reviewer in a later round are merged into accepted risks."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n")

    signal = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"tests": "changes-requested"},
        "reviewer_findings": {"tests": ["Blocking gap"]},
        "reviewer_non_blocking_findings": {},
        "changes_requested": ["tests"],
    }

    round_2_sig = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"tests": "approved"},
        "non_blocking_findings": ["Late-surfacing risk worth noting"],
    }
    stage_returns = [_fix_sig(), round_2_sig]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = plan_md.read_text()
    assert "Accepted Risks (non-blocking)" in content
    assert "Late-surfacing risk worth noting" in content


def test_append_findings_summary_with_only_accepted_risks(tmp_path):
    """append_findings_summary writes an Accepted Risks section when given no blocking findings."""
    from orchestrator.review_cycle import append_findings_summary

    plan_md = tmp_path / "plan.md"
    plan_md.write_text("# Project\n")
    append_findings_summary(
        plan_md,
        findings_map={},
        reviewer_statuses={"tests": "approved"},
        accepted_risks={"tests": ["Risk one", "Risk two"]},
    )

    content = plan_md.read_text()
    assert "## Review Findings" in content
    assert "Accepted Risks (non-blocking)" in content
    assert "Risk one" in content
    assert "Risk two" in content


def test_findings_summary_inserted_before_file_manifest(tmp_path):
    """Findings section is inserted before the File Manifest marker when present."""
    run_folder, log_path = _setup(tmp_path)
    plan_md = run_folder / "plan.md"
    plan_md.write_text("# Project\n\n## Review\ncontent\n\n## File Manifest\n| file |\n| --- |\n")

    signal = _signal_with_findings(
        {"tests": "changes-requested"},
        {"tests": ["Missing assertion on error message"]},
    )
    stage_returns = [_fix_sig(), _reviewer_sig("tests", "approved")]
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)):
        review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    content = plan_md.read_text()
    findings_pos = content.index("## Review Findings")
    manifest_pos = content.index("## File Manifest")
    assert findings_pos < manifest_pos


# ── deterministic diff validator ──────────────────────────────────────────────


@pytest.mark.real_validator
def test_is_valid_diff_file_rejects_empty_path():
    from orchestrator.review_cycle import is_valid_diff_file

    assert is_valid_diff_file("") is False


@pytest.mark.real_validator
def test_is_valid_diff_file_rejects_missing_file(tmp_path):
    from orchestrator.review_cycle import is_valid_diff_file

    assert is_valid_diff_file(str(tmp_path / "nope.patch")) is False


@pytest.mark.real_validator
def test_is_valid_diff_file_rejects_empty_file(tmp_path):
    from orchestrator.review_cycle import is_valid_diff_file

    p = tmp_path / "empty.patch"
    p.write_text("")
    assert is_valid_diff_file(str(p)) is False


@pytest.mark.real_validator
def test_is_valid_diff_file_rejects_prose_summary(tmp_path):
    from orchestrator.review_cycle import is_valid_diff_file

    p = tmp_path / "summary.patch"
    p.write_text("Refactored auth module and added tests for retry logic.\n")
    assert is_valid_diff_file(str(p)) is False


@pytest.mark.real_validator
def test_is_valid_diff_file_accepts_real_diff(tmp_path):
    from orchestrator.review_cycle import is_valid_diff_file

    p = tmp_path / "real.patch"
    p.write_text("diff --git a/foo.py b/foo.py\nindex 1..2 100644\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert is_valid_diff_file(str(p)) is True


@pytest.mark.real_validator
def test_cycle_aborts_when_diff_invalid(tmp_path):
    """If a fix cycle produces no usable diff, abort deterministically rather than dispatch
    a reviewer against an invalid input."""
    run_folder, log_path = _setup(tmp_path)
    signal = _review_signal({"tests": "changes-requested"})

    # _write_round_diff returns "" because repo_root is empty; validator rejects it.
    stage_returns = [_fix_sig()]  # only one call expected — reviewer must NOT run
    ret_iter = iter(stage_returns)

    with patch("orchestrator.review_cycle.run_stage", side_effect=lambda *a, **kw: next(ret_iter)) as mock_rs:
        result = review_cycle.run(run_folder, "/docs", "proj", "feat/x", signal, log_path)

    assert result["all_passed"] is False
    assert result["blocked"] is True
    assert "no valid git diff" in result.get("message", "")
    # Only fix-implementation ran; reviewer was NOT dispatched.
    assert mock_rs.call_count == 1
    assert mock_rs.call_args.args[0] == "fix-implementation"
