"""End-to-end happy-path test for the bundled `minimal` profile.

Patches `run_stage()` itself; signals are synthesised from each stage's JSON
schema. See `tests/e2e_harness.py` for the harness contract.

The minimal profile runs a single-agent decomposition + implementation flow:
decomposition emits one `plan_file` (not slices), implementation runs once
with `expansion: none`. This test asserts the absence of every slice-flow
artefact (worktrees, fan-in/fan-out plan nodes, S-NN filenames) in addition
to the positive contract assertions.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from orchestrator import orchestrate
from tests import e2e_harness as h


def _decomposition_override(default_sig, ctx):
    """Force the synthesised decomposition signal to match the real prompt contract.

    The real `prompts/decomposition/minimal.md` writes one plan at
    `$RUN_FOLDER/decomposition/implementation-plan.md` and emits no
    slice_files / slice_groups. We materialise that file here so the
    downstream assertion that the path exists is meaningful, and we clean
    up the harness's auto-synthesised `S-NN-auto.md` stubs (which the
    real minimal prompt never produces) so the "no slice naming anywhere"
    assertion reflects orchestrator behaviour only.
    """
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


def test_minimal_profile_e2e_happy_path(tmp_path):
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
            profile_name="minimal",
        )

    state = yaml.safe_load((run_folder / "_state.yaml").read_text())
    assert "blocked_at" not in state
    assert state.get("profile") == "minimal"

    stages = state.get("stages", {})
    for name in ("specification", "decomposition", "implementation", "review"):
        assert stages.get(name) == "passed", f"{name} did not pass (got {stages.get(name)!r})"

    # Stages the minimal profile does NOT include must not be recorded.
    for name in ("discovery", "alignment", "qa", "harvest"):
        assert name not in stages, f"unexpected stage {name!r} recorded in minimal run"

    signals = state.get("signals", {})
    assert signals["specification"]["prd_path"]
    assert signals["specification"]["context_path"]

    # Decomposition contract: one plan file, no slice fan-out.
    plan_file = Path(signals["decomposition"]["plan_file"])
    assert plan_file.exists(), f"plan file {plan_file} does not exist"
    assert plan_file.name.endswith("implementation-plan.md")
    assert not signals["decomposition"].get("slice_files"), "minimal decomposition must not emit slice_files"
    assert not signals["decomposition"].get("slice_groups"), "minimal decomposition must not emit slice_groups"

    # Implementation contract: one commit, one output.
    assert len(signals["implementation"]["commit_hashes"]) == 1
    assert signals["review"]["reviewer_statuses"] == {"implementation": "approved"}
    assert signals["review"]["changes_requested"] == []

    # spec + decomp + 1 impl + 1 reviewer + executive_summary finalisation = 5
    assert fake.call_count == 5

    assert (run_folder / "plan.md").exists()
    plan_md = (run_folder / "plan.md").read_text()
    plan_md_lower = plan_md.lower()
    for name in ("specification", "decomposition", "implementation", "review"):
        assert name in plan_md_lower
    for name in ("discovery", "alignment", "qa", "harvest"):
        assert name not in plan_md_lower

    # No slice fan-out occurred — the plan.md must not contain the fan-in/fan-out
    # node IDs emitted by `orchestrator/plan/_expand.py:_expand_slices`.
    assert "fanout_" not in plan_md, "minimal profile must not produce fan-out plan nodes"
    assert "fanin_" not in plan_md, "minimal profile must not produce fan-in plan nodes"

    # Single-agent implementation writes one un-suffixed output. The slice
    # dispatcher would have written per-slice files with names like
    # `implementation-S-01-foo-output.md` — none of those should appear.
    impl_outputs = sorted((run_folder / "implementation").glob("implementation*-output.md"))
    assert len(impl_outputs) == 1, f"expected exactly one impl output, got {[p.name for p in impl_outputs]}"
    assert impl_outputs[0].name == "implementation-output.md"

    review_outputs = sorted((run_folder / "review").glob("review-*-output.md"))
    assert {p.name for p in review_outputs} == {"review-implementation-output.md"}

    # No fix-cycle artefacts on the happy path.
    assert not any((run_folder / "review").glob("fix-implementation-*.md"))

    # No slice-specific filenames anywhere under the run folder. The slice
    # dispatcher writes `S-NN-*.md` artefacts and per-slice output files
    # tagged with the slice name — none of those should appear here.
    for path in run_folder.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        assert not name.startswith("S-"), f"unexpected slice-style filename {path}"
        assert "slice" not in name.lower(), f"unexpected slice-named file {path}"

    # No worktree creation occurred — the slice dispatcher in
    # `orchestrate.py:_create_worktree` is the only path that calls
    # `git ... worktree add`. With expansion: none, it must never run.
    for call in subprocess_mock.call_args_list:
        args = call.args[0] if call.args else call.kwargs.get("args", [])
        assert not (isinstance(args, list) and "worktree" in args and "add" in args), (
            f"unexpected `git worktree add` invocation: {args}"
        )

    # The implementation stage must run on `ctx.branch` — the harness's git
    # state patches make `branch_exists` return False, so `_dispatch_default`
    # is expected to call `git ... checkout -b feat/test`. Without branch
    # preparation, the implementation agent would commit to whatever branch
    # was checked out at orchestrator launch.
    checkout_calls = [
        call.args[0]
        for call in subprocess_mock.call_args_list
        if call.args
        and isinstance(call.args[0], list)
        and "checkout" in call.args[0]
        and "-b" in call.args[0]
        and "feat/test" in call.args[0]
    ]
    assert checkout_calls, "expected `git ... checkout -b feat/test` before implementation dispatch"
