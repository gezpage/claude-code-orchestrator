"""Characterization tests for wave verification in ``orchestrator.orchestrate``
(issue #154). Behaviour is config-driven via ``StageConfig.wave_verification``;
no test branches on profile name. See ADR-030, ADR-031, ADR-033."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest

from orchestrator._git_setup import GitPreflightResult, OriginInfo
from orchestrator.orchestrate import _dispatch_slices, _PipelineContext
from orchestrator.profile import ExpansionKind, StageConfig, WaveVerification
from orchestrator.verifiers.engine import BASELINE_SUBDIR

_PRE = GitPreflightResult(
    base_branch="main",
    create_pr=False,
    origin=OriginInfo(url=None, is_github=False, gh_repo=None),
)


@pytest.fixture(autouse=True)
def _stub_preflight_and_sync():
    with (
        patch("orchestrator.orchestrate._git_setup.preflight", return_value=_PRE),
        patch("orchestrator.orchestrate._sync_base_and_create_impl_branch"),
        patch("orchestrator.orchestrate._finalize_summary"),
    ):
        yield


def _make_ctx(tmp_path: Path, *, resume: bool = False) -> _PipelineContext:
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
        resume=resume,
    )


def _make_run_folder(tmp_path: Path, *, write_plan: bool = True) -> Path:
    rf = tmp_path / "runs" / "run-1"
    rf.mkdir(parents=True)
    if write_plan:
        (rf / "plan.md").write_text("# Plan\n")
    return rf


def _wave_stage(on_failure: Literal["warn", "fix_then_retry", "block"] = "warn") -> StageConfig:
    return StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
        wave_verification=WaveVerification(enabled=True, on_failure=on_failure),
    )


SIGNALS = {"decomposition": {"slice_files": ["S-01-a.md"], "slice_groups": [["S-01-a.md"]]}}


def _verify_side_effect(*, fail_ids_per_call: list[list[str]]):
    """Verifier double: failures in baseline verify.json are baseline_failed; rest are new_failed.
    Only fields read by ``_maybe_run_wave_verification`` and the plan-append code are populated.
    """
    calls = iter(fail_ids_per_call)

    def _fake(repo_root, run_folder_arg, *, artifact_subdir, baseline_path=None):
        fail_ids = next(calls)
        out_dir = Path(run_folder_arg) / artifact_subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        status = "failed" if fail_ids else "passed"
        cmd_status = {"status": status, "exit_code": 1 if fail_ids else 0}
        (out_dir / "verify.json").write_text(
            json.dumps(
                {"status": status, "toolchain": "node", "commands": [{"id": "test", **cmd_status}], "probes": []}
            )
        )
        compared = baseline_path is not None and Path(baseline_path).exists()
        b_failed: list[str] = []
        n_failed: list[str] = []
        if compared:
            base_set = {
                c["id"]
                for c in json.loads(Path(baseline_path).read_text()).get("commands", [])
                if c.get("status") == "failed"
            }
            for cid in fail_ids:
                (b_failed if cid in base_set else n_failed).append(cid)
            net_new = "failed" if n_failed else "passed"
        else:
            net_new = status
        return {
            "stage": "verification",
            "status": "passed",
            "verification_status": status,
            "net_new_status": net_new,
            "summary": f"failures={fail_ids}",
            "toolchain": "node",
            "verify_md_path": str(out_dir / "VERIFY.md"),
            "verify_json_path": str(out_dir / "verify.json"),
            "baseline_failed_command_ids": b_failed,
            "new_failed_command_ids": n_failed,
            "failed_command_ids": list(fail_ids),
            "baseline_compared": compared,
        }

    return _fake


def _dispatch_patches(verify_side_effect=None, verify_return=None, run_stage_side_effect=None):
    """Standard patch stack; returns (ExitStack, verify_mock)."""
    stack = ExitStack()
    e = stack.enter_context
    e(patch("orchestrator.orchestrate._create_branch"))
    rs_kw = (
        {"side_effect": run_stage_side_effect}
        if run_stage_side_effect is not None
        else {"return_value": {"status": "passed", "commit_hashes": ["a1"]}}
    )
    e(patch("orchestrator.orchestrate.run_stage", **rs_kw))
    # ``_wave_fix_then_retry`` lives in ``orchestrator.wave_verification`` and
    # imports ``run_stage`` independently of ``orchestrate``'s binding, so the
    # retry dispatch needs its own patch to stay intercepted. See issue #154.
    e(patch("orchestrator.wave_verification.run_stage", **rs_kw))
    e(patch("orchestrator.orchestrate.update_plan_md"))
    e(patch("orchestrator.orchestrate.expand_nodes"))
    v_kw = {"side_effect": verify_side_effect} if verify_side_effect is not None else {"return_value": verify_return}
    mv = e(patch("orchestrator.verifiers.engine.verify", **v_kw))
    return stack, mv


# ── characterization tests ────────────────────────────────────────────────────


def test_baseline_only_failure_does_not_block_under_block_policy(tmp_path):
    """``on_failure=block`` gates on net-new only; baseline-repeated failures never halt. (ADR-033)"""
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    stack, _ = _dispatch_patches(verify_side_effect=_verify_side_effect(fail_ids_per_call=[["test"], ["test"]]))
    with stack:
        result = _dispatch_slices(_wave_stage("block"), {"repo_root": "/tmp"}, run_folder, ctx, SIGNALS)
    assert result["status"] == "passed"
    wave = result["wave_verifications"][0]
    assert wave["baseline_failed_command_ids"] == ["test"]
    assert wave["new_failed_command_ids"] == []


def test_net_new_failure_blocks_under_block_policy(tmp_path):
    """A failure absent from baseline halts the dispatcher under ``on_failure=block``. (ADR-033)"""
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    stack, _ = _dispatch_patches(verify_side_effect=_verify_side_effect(fail_ids_per_call=[[], ["test"]]))
    with stack:
        result = _dispatch_slices(_wave_stage("block"), {"repo_root": "/tmp"}, run_folder, ctx, SIGNALS)
    assert result["status"] == "blocked"
    assert result["wave_verifications"][0]["new_failed_command_ids"] == ["test"]


def test_fix_then_retry_passes_baseline_path_to_retry_verify(tmp_path):
    """The retry verify call carries the same baseline_path as the initial wave call. (ADR-033)"""
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    rs_iter = iter([{"status": "passed", "commit_hashes": ["a1"]}, {"status": "passed"}])
    stack, mv = _dispatch_patches(
        verify_side_effect=_verify_side_effect(fail_ids_per_call=[[], ["test"], []]),
        run_stage_side_effect=lambda *a, **kw: next(rs_iter),
    )
    with stack:
        _dispatch_slices(_wave_stage("fix_then_retry"), {"repo_root": "/tmp"}, run_folder, ctx, SIGNALS)
    subdirs = [c.kwargs["artifact_subdir"] for c in mv.call_args_list]
    assert subdirs == [BASELINE_SUBDIR, "wave-verification/wave-1", "wave-verification/wave-1/retry"]
    initial = mv.call_args_list[1].kwargs.get("baseline_path")
    retry = mv.call_args_list[2].kwargs.get("baseline_path")
    assert retry is not None and retry == initial and Path(retry).exists()


def test_warn_policy_records_failure_but_dispatcher_passes(tmp_path):
    """``on_failure=warn``: net-new failure surfaces on result but status stays passed."""
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path)
    stack, _ = _dispatch_patches(verify_side_effect=_verify_side_effect(fail_ids_per_call=[[], ["test"]]))
    with stack:
        result = _dispatch_slices(_wave_stage("warn"), {"repo_root": "/tmp"}, run_folder, ctx, SIGNALS)
    assert result["status"] == "passed"
    wave = result["wave_verifications"][0]
    assert wave["verification_status"] == "failed"
    assert wave["new_failed_command_ids"] == ["test"]


def test_missing_baseline_degrades_to_no_classification(tmp_path):
    """Resumed run with no baseline file: dispatcher refuses to fabricate one; wave verify
    gets no baseline_path; net_new_status mirrors verification_status. (ADR-033)"""
    ctx = _make_ctx(tmp_path, resume=True)
    run_folder = _make_run_folder(tmp_path)
    stack, mv = _dispatch_patches(verify_side_effect=_verify_side_effect(fail_ids_per_call=[["test"]]))
    with stack:
        result = _dispatch_slices(_wave_stage("warn"), {"repo_root": "/tmp"}, run_folder, ctx, SIGNALS)
    assert not (run_folder / BASELINE_SUBDIR / "verify.json").exists()
    assert mv.call_count == 1
    assert mv.call_args_list[0].kwargs.get("baseline_path") is None
    wave = result["wave_verifications"][0]
    assert wave["baseline_compared"] is False
    assert wave["net_new_status"] == wave["verification_status"] == "failed"


def test_slice_node_and_wave_node_stamp_independently(tmp_path):
    """``impl_N`` = slice completion; ``wave_verify_N`` = integration health.
    A 'passed' slice can coexist with a 'blocked' wave verify. (ADR-031)"""
    from orchestrator.plan import init_plan_md
    from orchestrator.plan._graph import load_graph
    from orchestrator.profile import Profile

    stage = _wave_stage("warn")
    profile = Profile(name="t", stages=(stage,))
    ctx = _make_ctx(tmp_path)
    run_folder = _make_run_folder(tmp_path, write_plan=False)
    init_plan_md(run_folder, profile)
    failed_verify = {
        "stage": "verification",
        "status": "passed",
        "verification_status": "failed",
        "summary": "integration broken",
        "verify_md_path": str(run_folder / "wave-verification" / "wave-1" / "VERIFY.md"),
        "verify_json_path": str(run_folder / "wave-verification" / "wave-1" / "verify.json"),
    }
    with (
        patch("orchestrator.orchestrate._create_branch"),
        patch("orchestrator.orchestrate.run_stage", return_value={"status": "passed", "commit_hashes": ["a1"]}),
        patch("orchestrator.verifiers.engine.verify", return_value=failed_verify),
    ):
        result = _dispatch_slices(stage, {"repo_root": "/tmp"}, run_folder, ctx, SIGNALS)
    assert result["status"] == "passed"
    graph = load_graph(run_folder)
    assert graph is not None
    assert graph.nodes["impl_1"].status == "passed"
    assert graph.nodes["wave_verify_1"].status == "blocked"
