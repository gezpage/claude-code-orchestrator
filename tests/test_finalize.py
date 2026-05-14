"""Tests for the post-pipeline PR finalisation phase added by ADR-019."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator import orchestrate
from orchestrator._github import GhError


def _make_run_folder(tmp_path):
    rf = tmp_path / "projects" / "p" / "workflow" / "runs" / "feat" / "run-1"
    rf.mkdir(parents=True)
    (rf / "plan.md").write_text(
        "# p · feat\n\n**Run:** run-1\n\n**Draft PR:** _will be created on completion_\n\n## Orchestration Flow\n\n```mermaid\nflowchart TD\n```\n"
    )
    return rf


def _ctx_agent_metadata():
    return {"pr_draft": {"backend": "claude_code_print", "model": "test"}}


def test_finalize_pr_happy_path(tmp_path):
    rf = _make_run_folder(tmp_path)
    docs = tmp_path
    overview = docs / "feature" / "overview.md"
    overview.parent.mkdir(parents=True)
    overview.write_text("# Feature\n")
    logger = MagicMock()
    pr_sig = {"stage": "pr_draft", "status": "passed", "title": "feat: x", "body": "Body"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=pr_sig),
        patch("orchestrator.orchestrate.git_state.push_branch") as mock_push,
        patch(
            "orchestrator.orchestrate._github.create_draft_pr",
            return_value="https://github.com/me/r/pull/7",
        ) as mock_pr,
    ):
        orchestrate._finalize_pr(
            run_folder=rf,
            docs_root=str(docs),
            project="p",
            project_log_path=str(docs / "projects" / "p"),
            feature_path="feature",
            repo_root="/tmp/repo",
            impl_branch="feat/x",
            base_branch="main",
            gh_repo="me/r",
            logger=logger,
            agent_metadata=_ctx_agent_metadata(),
        )

    mock_push.assert_called_once_with("/tmp/repo", "feat/x", "origin", set_upstream=True)
    mock_pr.assert_called_once_with("me/r", "main", "feat/x", "feat: x", "Body")
    plan_text = (rf / "plan.md").read_text()
    assert "https://github.com/me/r/pull/7" in plan_text
    assert "will be created on completion" not in plan_text


def test_finalize_pr_handles_pr_draft_failure(tmp_path):
    rf = _make_run_folder(tmp_path)
    logger = MagicMock()
    blocked_sig = {"stage": "pr_draft", "status": "blocked", "message": "no plan"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=blocked_sig),
        patch("orchestrator.orchestrate.git_state.push_branch") as mock_push,
        patch("orchestrator.orchestrate._github.create_draft_pr") as mock_pr,
    ):
        orchestrate._finalize_pr(
            run_folder=rf,
            docs_root=str(tmp_path),
            project="p",
            project_log_path=str(tmp_path / "projects" / "p"),
            feature_path="feature",
            repo_root="/tmp/repo",
            impl_branch="feat/x",
            base_branch="main",
            gh_repo="me/r",
            logger=logger,
            agent_metadata=_ctx_agent_metadata(),
        )
    mock_push.assert_not_called()
    mock_pr.assert_not_called()
    plan_text = Path(rf / "plan.md").read_text()
    assert "PR creation failed" in plan_text
    assert "gh pr create --draft --base main --head feat/x" in plan_text


def test_finalize_pr_handles_gh_failure(tmp_path):
    rf = _make_run_folder(tmp_path)
    logger = MagicMock()
    pr_sig = {"stage": "pr_draft", "status": "passed", "title": "feat: x", "body": "Body"}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=pr_sig),
        patch("orchestrator.orchestrate.git_state.push_branch"),
        patch(
            "orchestrator.orchestrate._github.create_draft_pr",
            side_effect=GhError("auth expired"),
        ),
    ):
        orchestrate._finalize_pr(
            run_folder=rf,
            docs_root=str(tmp_path),
            project="p",
            project_log_path=str(tmp_path / "projects" / "p"),
            feature_path="feature",
            repo_root="/tmp/repo",
            impl_branch="feat/x",
            base_branch="main",
            gh_repo="me/r",
            logger=logger,
            agent_metadata=_ctx_agent_metadata(),
        )
    plan_text = Path(rf / "plan.md").read_text()
    assert "PR creation failed" in plan_text
    assert "auth expired" not in plan_text  # error details land in logger, not plan.md
