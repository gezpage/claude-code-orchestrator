"""End-to-end happy-path test for the bundled `minimal-codex` profile.

Mirrors `test_e2e_minimal_profile.py` but additionally asserts that the
profile-level `agent.backend: codex_cli` flows through `_build_stage_runners`
and is recorded in `_state.yaml` for every autonomous stage. The deterministic
verification stage bypasses the runner seam (ADR-017) and must record
`backend: deterministic` regardless of the profile's agent config.
"""

from unittest.mock import MagicMock, patch

import yaml

from orchestrator import orchestrate
from tests import e2e_harness as h


def _decomposition_override(default_sig, ctx):
    run_folder = ctx["run_folder"]
    decomp_dir = run_folder / "decomposition"
    decomp_dir.mkdir(parents=True, exist_ok=True)
    for stub in decomp_dir.glob("S-*.md"):
        stub.unlink()
    plan_path = decomp_dir / "implementation-plan.md"
    plan_path.write_text("# Implementation plan (synthesised)\n")
    out = dict(default_sig)
    out["plan_file"] = str(plan_path)
    out["slice_files"] = []
    out["slice_groups"] = []
    return out


def test_minimal_codex_profile_e2e_records_codex_backend(tmp_path):
    out_dir = h.resolve_output_dir(tmp_path)
    docs_root, feature_path = h.setup_docs(out_dir)

    run_folder = out_dir / "projects" / "myproject" / "workflow" / "runs" / "demo" / "2026-05-14-run-1"

    subprocess_mock = MagicMock(return_value=h.git_ok())

    with (
        h.patch_run_stage(overrides={"decomposition": _decomposition_override}) as fake,
        patch("orchestrator.orchestrate.subprocess.run", subprocess_mock),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(
            docs_root,
            project="myproject",
            feature_path=feature_path,
            branch="feat/test",
            profile_name="minimal-codex",
        )

    state = yaml.safe_load((run_folder / "_state.yaml").read_text())
    assert "blocked_at" not in state
    assert state.get("profile") == "minimal-codex"

    stages = state.get("stages", {})
    for name in ("specification", "decomposition", "implementation", "review"):
        assert stages.get(name) == "passed", f"{name} did not pass (got {stages.get(name)!r})"

    # Backend metadata — the core acceptance criterion for this profile.
    agent = state.get("agent", {})
    for autonomous in ("specification", "decomposition", "implementation", "review"):
        assert agent.get(autonomous, {}).get("backend") == "codex_cli", (
            f"{autonomous} did not record codex_cli backend (got {agent.get(autonomous)!r})"
        )
        assert agent.get(autonomous, {}).get("model") is None
    assert agent.get("verification", {}).get("backend") == "deterministic"

    # spec + decomp + 1 impl + 1 reviewer + executive_summary finalisation = 5 —
    # same single-agent shape as `minimal`.
    assert fake.call_count == 5

    # No slice fan-out: no worktree creation, no fan-in/fan-out plan nodes,
    # no per-slice output filenames.
    for call in subprocess_mock.call_args_list:
        args = call.args[0] if call.args else call.kwargs.get("args", [])
        assert not (isinstance(args, list) and "worktree" in args and "add" in args), (
            f"unexpected `git worktree add` invocation: {args}"
        )
    plan_md = (run_folder / "plan.md").read_text()
    assert "fanout_" not in plan_md
    assert "fanin_" not in plan_md
    impl_outputs = sorted((run_folder / "implementation").glob("implementation*-output.md"))
    assert [p.name for p in impl_outputs] == ["implementation-output.md"]
    for path in run_folder.rglob("*"):
        if path.is_file():
            assert not path.name.startswith("S-")
