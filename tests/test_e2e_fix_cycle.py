"""End-to-end test for the review fix-implementation cycle.

Architecture reviewer requests changes in round 1; the orchestrator runs the
fix-implementation stage and re-reviews in round 2, where architecture
approves. Implementation and tests reviewers approve in round 1 and are not
re-invoked. Patches `run_stage()` itself; see `tests/e2e_harness.py` for the
harness contract.
"""

from unittest.mock import patch

import yaml

from orchestrator import orchestrate
from tests import e2e_harness as h


def test_fix_cycle_resolves_architecture_changes(tmp_path):
    out_dir = h.resolve_output_dir(tmp_path)
    docs_root, feature_path = h.setup_docs(out_dir)

    run_folder = out_dir / "projects" / "myproject" / "workflow" / "runs" / "demo" / "2026-05-14-run-1"
    h.pre_create_alignment(run_folder)

    overrides = {
        "review:architecture:r1": {
            "reviewer_statuses": {"architecture": "changes-requested"},
            "findings": ["Coupling between dispatcher and review_cycle"],
        },
    }

    with (
        h.patch_run_stage(overrides=overrides) as fake,
        patch("orchestrator.orchestrate.subprocess.run", return_value=h.git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(
            docs_root,
            project="myproject",
            feature_path=feature_path,
            branch="feat/test",
            profile_name="full",
        )

    state = yaml.safe_load((run_folder / "_state.yaml").read_text())
    assert "blocked_at" not in state
    stages = state["stages"]
    assert stages.get("review") == "passed"
    assert stages.get("harvest") == "passed"
    # review_cycle records its own stage row per cycle.
    assert stages.get("review-cycle-1") == "passed"
    assert "review-cycle-2" not in stages  # second cycle not needed

    counts = fake.call_counts
    assert counts["review:architecture:r1"] == 1
    assert counts["review:architecture:r2"] == 1
    assert counts["review:implementation:r1"] == 1
    assert counts["review:tests:r1"] == 1
    assert counts["fix-implementation"] == 1
    # implementation reviewer must NOT be re-run in round 2 — only changes-requested
    # reviewers re-enter the cycle.
    assert "review:implementation:r2" not in counts

    # Happy-path 11 + 1 fix-impl + 1 arch-r2 = 13.
    assert fake.call_count == 13

    # Fix-cycle artefacts exist.
    fix_outputs = sorted((run_folder / "fix-implementation").glob("fix-implementation-*-output.md"))
    assert len(fix_outputs) == 1

    # Round-2 reviewer output written to review-log.md by _update_review_md.
    review_md = (run_folder / "review" / "review-log.md").read_text()
    assert "Architecture Review — Round 2" in review_md

    # plan.md surfaces the findings correlation appended by _append_findings_summary.
    plan_md = (run_folder / "plan.md").read_text()
    assert "Review Findings" in plan_md
    assert "Coupling between dispatcher and review_cycle" in plan_md
    assert "Fixed in Fix Cycle" in plan_md
