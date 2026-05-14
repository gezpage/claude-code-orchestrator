"""End-to-end happy-path test for the bundled `full` profile.

Patches `run_stage()` itself; everything below `orchestrate.py` is bypassed and
signals are synthesised from each stage's JSON schema. See `tests/e2e_harness.py`
for the harness contract.
"""

from unittest.mock import patch

import yaml

from orchestrator import orchestrate
from tests import e2e_harness as h


def test_full_profile_e2e_happy_path(tmp_path):
    out_dir = h.resolve_output_dir(tmp_path)
    docs_root, feature_path = h.setup_docs(out_dir)

    run_folder = out_dir / "projects" / "myproject" / "workflow" / "runs" / "demo" / "2026-05-14-run-1"
    h.pre_create_alignment(run_folder)

    with (
        h.patch_run_stage() as fake,
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
    stages = state.get("stages", {})
    for name in (
        "discovery",
        "alignment",
        "specification",
        "decomposition",
        "implementation",
        "qa",
        "review",
        "harvest",
    ):
        assert stages.get(name) == "passed", f"{name} did not pass (got {stages.get(name)!r})"

    signals = state.get("signals", {})
    assert signals["discovery"]["findings_files"], "discovery did not surface findings_files"
    assert signals["specification"]["prd_path"]
    assert signals["specification"]["context_path"]
    assert len(signals["decomposition"]["slice_files"]) == 2
    assert len(signals["implementation"]["commit_hashes"]) == 2
    assert signals["review"]["reviewer_statuses"] == {
        "architecture": "approved",
        "implementation": "approved",
        "tests": "approved",
    }
    assert signals["review"]["changes_requested"] == []

    # planning + 1 track + spec + decomp + 2 impl + qa + 3 reviewers + harvest = 11.
    # alignment is interactive and pre-skipped, so it does not invoke run_stage.
    assert fake.call_count == 11

    assert (run_folder / "plan.md").exists()
    plan_md = (run_folder / "plan.md").read_text().lower()
    for name in ("discovery", "alignment", "specification", "decomposition", "harvest"):
        assert name in plan_md

    impl_outputs = sorted((run_folder / "implementation").glob("implementation-*-output.md"))
    assert len(impl_outputs) == 2

    review_outputs = sorted((run_folder / "review").glob("review-*-output.md"))
    assert {p.name for p in review_outputs} == {
        "review-architecture-output.md",
        "review-implementation-output.md",
        "review-tests-output.md",
    }

    # Review-cycle artefacts must NOT exist on the happy path.
    assert not any((run_folder / "review").glob("fix-implementation-*.md"))
