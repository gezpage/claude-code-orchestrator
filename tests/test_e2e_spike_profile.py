"""End-to-end happy-path test for the bundled `spike` profile.

The spike profile has a single discovery stage with `expansion: tracks`, which
dispatches as a planning prompt plus N parallel track prompts. Mocks only
`orchestrator.run_stage._run_claude`; everything else runs for real. Set
`ORCH_E2E_OUTPUT_DIR` to pin run artefacts to a stable path for inspection.
See `tests/e2e_harness.py` for the shared scaffolding.
"""

from unittest.mock import patch

import yaml

from orchestrator import orchestrate
from tests import e2e_harness as h


def test_spike_profile_e2e_happy_path(tmp_path):
    out_dir = h.resolve_output_dir(tmp_path)
    docs_root, feature_path = h.setup_docs(out_dir)

    run_folder = out_dir / "projects" / "myproject" / "workflow" / "runs" / "demo" / "2026-05-14-run-1"
    track_prompt = h.write_track_prompt(out_dir)

    fake = h.make_fake_run_claude(h.default_signals(out_dir, track_prompt))

    with (
        patch("orchestrator.run_stage._run_claude", side_effect=fake) as mock_claude,
        patch("orchestrator.orchestrate.subprocess.run", return_value=h.git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(
            docs_root,
            project="myproject",
            feature_path=feature_path,
            branch="feat/test",
            profile_name="spike",
        )

    state = yaml.safe_load((run_folder / "_state.yaml").read_text())
    assert "blocked_at" not in state
    assert state.get("profile") == "spike"

    stages = state.get("stages", {})
    assert stages.get("discovery") == "passed"

    # Stages the spike profile does NOT include must not be recorded.
    for name in ("alignment", "specification", "decomposition", "implementation", "qa", "review", "harvest"):
        assert name not in stages, f"unexpected stage {name!r} recorded in spike run"

    signals = state.get("signals", {})
    assert signals["discovery"]["findings_files"], "discovery did not surface findings_files"

    # planning prompt + 1 track = 2
    assert mock_claude.call_count == 2

    assert (run_folder / "plan.md").exists()
    plan_md = (run_folder / "plan.md").read_text().lower()
    assert "discovery" in plan_md
    for name in ("alignment", "specification", "decomposition", "implementation", "qa", "review", "harvest"):
        assert name not in plan_md
