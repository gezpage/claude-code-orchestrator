"""Characterization tests for plan.md update behavior driven by orchestrate.py.

Pins observable contracts that must survive future extraction of plan-update
side-effects (issue #154 refactor prep). Tight contract locks; does not
duplicate assertions already in tests/test_orchestrate.py. Profile behaviour
is config-driven: tests never branch on profile *names*. See ADRs 026/031/036.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from orchestrator import orchestrate
from orchestrator._git_setup import GitPreflightResult, OriginInfo
from orchestrator.orchestrate import _PipelineContext
from orchestrator.plan import init_plan_md, update_plan_md
from orchestrator.plan._graph import load_graph
from orchestrator.profile import (
    ExecutiveSummary,
    ExpansionKind,
    Profile,
    StageConfig,
    WaveVerification,
)

_PREFLIGHT_OFF = GitPreflightResult(
    base_branch="main",
    create_pr=False,
    origin=OriginInfo(url=None, is_github=False, gh_repo=None),
)


@pytest.fixture(autouse=True)
def _stub_finalisers():
    """Stub preflight, base-branch sync, and executive summary so pipeline-
    driven tests don't accidentally exercise those finalisers."""
    with (
        patch("orchestrator.orchestrate._git_setup.preflight", return_value=_PREFLIGHT_OFF),
        patch("orchestrator.orchestrate._sync_base_and_create_impl_branch"),
        patch("orchestrator.orchestrate._finalize_summary"),
    ):
        yield


def _git_ok():
    r = MagicMock()
    r.returncode, r.stderr, r.stdout = 0, "", "diff --git a/f b/f\n"
    return r


def _setup_docs(tmp_path, stages, *, executive_summary=True):
    pd = tmp_path / "projects" / "myproject"
    pd.mkdir(parents=True)
    (pd / "project.yaml").write_text("repo-root: /tmp\nlog_level: DEBUG\n")
    doc: dict = {"name": "test", "stages": stages}
    if executive_summary:
        doc["executive_summary"] = {}
    (tmp_path / "test.yaml").write_text(yaml.dump(doc))
    fd = tmp_path / "feature"
    fd.mkdir(parents=True, exist_ok=True)
    (fd / "overview.md").write_text("# Feature Overview\n")
    return str(tmp_path)


def _ctx(tmp_path):
    return _PipelineContext(
        docs_root=str(tmp_path),
        project="myproject",
        project_log_path=str(tmp_path / "projects" / "myproject"),
        logger=MagicMock(),
        branch="feat/test",
        project_config={"repo-root": "/tmp"},
        project_standards=[],
        runners={},
        agent_metadata={},
        resume=False,
    )


def test_stage_status_persists_to_graph_and_renders_to_plan(tmp_path):
    """update_plan_md(stage, status) -> node carries status in graph AND
    plan.md surfaces the matching css-class token. See ADR-026."""
    rf = tmp_path / "run"
    rf.mkdir()
    init_plan_md(rf, Profile(name="t", stages=(StageConfig(name="discovery", prompt="prompts/discovery/default.md"),)))

    update_plan_md(rf, "discovery", "passed")

    graph = load_graph(rf)
    assert graph is not None and graph.nodes["discovery"].status == "passed"
    # ``passed`` -> css_class ``complete``. Locks the projection without
    # depending on whitespace, columns, or mermaid syntax.
    assert "class discovery complete" in (rf / "plan.md").read_text()


def test_round1_review_subnode_receives_terminal_verdict_after_fix(tmp_path):
    """resolve_review_subnode_statuses MUST receive the *terminal* per-reviewer
    verdicts after a successful fix cycle (not the round-1 dict). See ADR-026."""
    docs_root = _setup_docs(
        tmp_path,
        [{"stage": "review", "expansion": "prompts", "prompts": {"architecture": "prompts/review/architecture.md"}}],
        executive_summary=False,
    )
    rf = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feat" / "2026-01-01-run-1"
    rf.mkdir(parents=True)
    round1 = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"architecture": "changes-requested"},
        "reviewer_findings": {"architecture": ["x"]},
        "non_blocking_findings": [],
    }
    final = {"all_passed": True, "reviewer_statuses": {"architecture": "approved"}}

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=round1),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=rf),
        patch("orchestrator.orchestrate.review_cycle_mod.is_valid_diff_file", return_value=True),
        patch("orchestrator.orchestrate.review_cycle_mod.run", return_value=final),
        patch("orchestrator.orchestrate.resolve_review_subnode_statuses") as mock_resolve,
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    mock_resolve.assert_called_once_with(rf, {"architecture": "approved"})


def test_wave_node_blocked_in_plan_when_warn_policy_lets_pipeline_continue(tmp_path):
    """on_failure=warn: failed integration check stamps wave_verify_{N}
    blocked while slice stays passed. Distinct from the graph-only test in
    test_orchestrate.py — ALSO locks projection into plan.md. See ADR-031."""
    from orchestrator.orchestrate import _dispatch_slices

    stage = StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
        wave_verification=WaveVerification(enabled=True, on_failure="warn"),
    )
    ctx = _ctx(tmp_path)
    rf = tmp_path / "run"
    rf.mkdir()
    init_plan_md(rf, Profile(name="t", stages=(stage,)))
    signals = {"decomposition": {"slice_files": ["S-01-a.md"], "slice_groups": [["S-01-a.md"]]}}
    wv = rf / "wave-verification" / "wave-1"
    failed = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "failed",
        "summary": "integration broke",
        "verify_md_path": str(wv / "VERIFY.md"),
        "verify_json_path": str(wv / "verify.json"),
    }

    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.verifiers.engine.verify", return_value=failed),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, rf, ctx, signals)

    assert result["status"] == "passed"
    text = (rf / "plan.md").read_text()
    # Two distinct classes — never collapsed onto a single node.
    assert "class impl_1 complete" in text
    assert "class wave_verify_1 blocked" in text


def test_pr_node_flipped_to_blocked_in_plan_when_pipeline_blocks(tmp_path):
    """create-pr=True at init -> ``pr`` starts pending; pipeline failure before
    PR finalisation flips it to ``blocked`` via the REAL mark_pr_blocked
    (catches wiring AND helper persistence). See ADR-026."""
    rf = tmp_path / "run"
    rf.mkdir()
    init_plan_md(
        rf,
        Profile(name="t", stages=(StageConfig(name="discovery", prompt="prompts/discovery/default.md"),)),
        create_pr=True,
    )
    before = load_graph(rf)
    assert before is not None and before.nodes["pr"].status == "pending"

    docs_root = _setup_docs(
        tmp_path / "docs",
        [{"stage": "discovery", "prompt": "prompts/discovery/default.md"}],
        executive_summary=False,
    )

    preflight_on = GitPreflightResult(
        base_branch="main",
        create_pr=True,
        origin=OriginInfo(url="git@github.com:x/y.git", is_github=True, gh_repo="x/y"),
    )
    blocked = {"stage": "discovery", "status": "blocked", "message": "no"}
    with (
        patch("orchestrator.orchestrate._git_setup.preflight", return_value=preflight_on),
        patch("orchestrator.orchestrate._sync_base_and_create_impl_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value=blocked),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=rf),
    ):
        with pytest.raises(SystemExit) as exc_info:
            orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(Path(docs_root) / "test.yaml"))

    assert exc_info.value.code == 1
    after = load_graph(rf)
    assert after is not None and after.nodes["pr"].status == "blocked"
    assert "class pr blocked" in (rf / "plan.md").read_text()


def test_executive_summary_node_presence_is_config_driven_not_name_driven(tmp_path):
    """ADR-036: two profiles with IDENTICAL names but different
    ``executive_summary`` declarations render different graphs — locks the
    rule that plan code never branches on profile *name*."""
    name = "shared"
    stages = (StageConfig(name="discovery", prompt="prompts/discovery/default.md"),)

    rf_with = tmp_path / "with"
    rf_with.mkdir()
    init_plan_md(rf_with, Profile(name=name, stages=stages, executive_summary=ExecutiveSummary()))

    rf_without = tmp_path / "without"
    rf_without.mkdir()
    init_plan_md(rf_without, Profile(name=name, stages=stages))

    g_with = load_graph(rf_with)
    g_without = load_graph(rf_without)
    assert g_with is not None and g_without is not None
    assert "executive_summary" in g_with.nodes
    assert "executive_summary" not in g_without.nodes
    # Projection into plan.md mirrors the graph — search only inside the
    # mermaid fence to avoid matching tmp-path or other surrounding text.
    text = (rf_without / "plan.md").read_text()
    start = text.find("```mermaid")
    end = text.find("```", start + 10)
    assert "executive_summary" not in text[start:end]
