"""Tests for the plan.md PR-notice helper added by ADR-019."""

from pathlib import Path

from orchestrator.plan import set_pr_notice
from orchestrator.plan._helpers import _PR_NOTICE_MARKER


def _write_plan(tmp_path: Path, body: str) -> Path:
    rf: Path = tmp_path / "run"
    rf.mkdir()
    plan = rf / "plan.md"
    plan.write_text(body)
    return rf


def test_set_pr_notice_inserts_before_orchestration_heading(tmp_path):
    rf = _write_plan(
        tmp_path,
        "# p · feat\n\n**Run:** run-1\n\n## Orchestration Flow\n\n```mermaid\nflowchart TD\n```\n",
    )
    set_pr_notice(rf, "https://example.test/pr/1")
    text = (rf / "plan.md").read_text()
    assert f"{_PR_NOTICE_MARKER} https://example.test/pr/1" in text
    # Notice line precedes the Orchestration Flow heading.
    assert text.index(_PR_NOTICE_MARKER) < text.index("## Orchestration Flow")


def test_set_pr_notice_replaces_existing(tmp_path):
    rf = _write_plan(
        tmp_path,
        "# p · feat\n\n**Run:** run-1\n\n**Draft PR:** _drafting…_\n\n## Orchestration Flow\n\n```mermaid\n```\n",
    )
    set_pr_notice(rf, "https://example.test/pr/2")
    text = (rf / "plan.md").read_text()
    assert "_drafting…_" not in text
    assert "https://example.test/pr/2" in text
    # Only one notice line — no duplication.
    assert text.count(_PR_NOTICE_MARKER) == 1


def test_set_pr_notice_no_op_on_missing_plan(tmp_path):
    rf = tmp_path / "no-plan"
    rf.mkdir()
    set_pr_notice(rf, "anything")  # should not raise
    assert not (rf / "plan.md").exists()
