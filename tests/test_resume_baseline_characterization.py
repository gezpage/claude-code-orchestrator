"""Characterization tests for resume + baseline interaction in orchestrate.py.

Pins ADR-033 baseline capture and ADR-030 wave verification contracts so
future refactors (issue #154) cannot silently drift. Tests are config-driven
via ``StageConfig.wave_verification`` — never gated on profile name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast
from unittest.mock import MagicMock, patch

from orchestrator.orchestrate import (
    _maybe_capture_wave_baseline,
    _maybe_run_wave_verification,
    _PipelineContext,
)
from orchestrator.profile import ExpansionKind, StageConfig, WaveVerification
from orchestrator.verifiers.engine import BASELINE_SUBDIR, VerificationError


def _make_ctx(tmp_path: Path, *, resume: bool = False) -> _PipelineContext:
    return _PipelineContext(
        docs_root=str(tmp_path),
        project="p",
        project_log_path=str(tmp_path / "p"),
        logger=MagicMock(),
        branch="feat/test",
        project_config={"repo-root": "/tmp"},
        project_standards=[],
        runners={},
        agent_metadata={},
        resume=resume,
    )


def _make_run_folder(tmp_path: Path) -> Path:
    rf = tmp_path / "runs" / "run-1"
    rf.mkdir(parents=True)
    return rf


def _wave_stage(on_failure: Literal["warn", "fix_then_retry", "block"] = "warn") -> StageConfig:
    return StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
        wave_verification=WaveVerification(enabled=True, on_failure=on_failure),
    )


def _write_baseline(run_folder: Path) -> Path:
    d = run_folder / BASELINE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / "verify.json"
    path.write_text(json.dumps({"status": "passed", "commands": [], "probes": []}))
    return path


def _verify_signal(*, verification_status: str = "passed", net_new_status: str | None = None) -> dict:
    sig: dict = {
        "stage": "verification",
        "status": "passed",
        "verification_status": verification_status,
        "summary": "",
        "verify_md_path": "VERIFY.md",
        "verify_json_path": "verify.json",
    }
    if net_new_status is not None:
        sig["net_new_status"] = net_new_status
    return sig


def _fake_capture(repo_root, rf):
    path = Path(rf) / BASELINE_SUBDIR / "verify.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": "passed", "commands": [], "probes": []}))
    return {"summary": "ok"}


def _warn_messages(ctx: _PipelineContext) -> list[str]:
    """Return WARN-level message bodies recorded on the MagicMock logger."""
    mock_log = cast(MagicMock, ctx.logger).log
    return [c.args[2] for c in mock_log.call_args_list if c.args[1] == "WARN"]


# ── tests ────────────────────────────────────────────────────────────────────


def test_fresh_run_captures_baseline_before_waves(tmp_path):
    """Fresh run writes <run>/baseline-verification/verify.json via capture_baseline."""
    stage = _wave_stage()
    ctx = _make_ctx(tmp_path, resume=False)
    run_folder = _make_run_folder(tmp_path)

    with patch("orchestrator.verifiers.engine.capture_baseline", side_effect=_fake_capture) as mock_capture:
        _maybe_capture_wave_baseline(stage, {"repo_root": "/tmp"}, run_folder, ctx)

    mock_capture.assert_called_once()
    assert (run_folder / "baseline-verification" / "verify.json").exists()


def test_resume_with_existing_baseline_is_idempotent(tmp_path):
    """Resumed run with existing baseline: file untouched; same path threaded into wave verify."""
    stage = _wave_stage()
    ctx = _make_ctx(tmp_path, resume=True)
    run_folder = _make_run_folder(tmp_path)
    baseline_path = _write_baseline(run_folder)
    original = baseline_path.read_text()

    sig = _verify_signal(net_new_status="passed")
    with (
        patch("orchestrator.verifiers.engine.capture_baseline") as mock_capture,
        patch("orchestrator.verifiers.engine.verify", return_value=sig) as mock_verify,
        patch("orchestrator.orchestrate._stamp_wave_node"),
        patch("orchestrator.orchestrate._append_wave_verification_section"),
    ):
        _maybe_capture_wave_baseline(stage, {"repo_root": "/tmp"}, run_folder, ctx)
        _maybe_run_wave_verification(stage, 1, {"repo_root": "/tmp"}, run_folder, ctx)

    mock_capture.assert_not_called()
    assert baseline_path.read_text() == original
    assert mock_verify.call_args.kwargs.get("baseline_path") == baseline_path
    assert mock_verify.call_args.kwargs["artifact_subdir"] == "wave-verification/wave-1"


def test_resume_with_missing_baseline_refuses_capture_and_warns(tmp_path):
    """Resume + missing baseline: no capture (would snapshot regressions); WARN logged;
    wave verify gets baseline_path=None so net_new_status degrades to verification_status."""
    stage = _wave_stage()
    ctx = _make_ctx(tmp_path, resume=True)
    run_folder = _make_run_folder(tmp_path)

    sig = _verify_signal(verification_status="passed")
    with (
        patch("orchestrator.verifiers.engine.capture_baseline") as mock_capture,
        patch("orchestrator.verifiers.engine.verify", return_value=sig) as mock_verify,
        patch("orchestrator.orchestrate._stamp_wave_node"),
        patch("orchestrator.orchestrate._append_wave_verification_section"),
    ):
        _maybe_capture_wave_baseline(stage, {"repo_root": "/tmp"}, run_folder, ctx)
        _maybe_run_wave_verification(stage, 1, {"repo_root": "/tmp"}, run_folder, ctx)

    mock_capture.assert_not_called()
    assert not (run_folder / "baseline-verification" / "verify.json").exists()
    assert mock_verify.call_args.kwargs.get("baseline_path") is None
    assert any("baseline capture skipped on resume" in m for m in _warn_messages(ctx))


def test_baseline_capture_failure_is_swallowed(tmp_path):
    """VerificationError or generic exception during capture is logged, not raised;
    wave verification still proceeds."""
    stage = _wave_stage()
    ctx = _make_ctx(tmp_path, resume=False)
    run_folder = _make_run_folder(tmp_path)

    sig = _verify_signal(verification_status="passed")
    with (
        patch(
            "orchestrator.verifiers.engine.capture_baseline",
            side_effect=VerificationError("recipe missing"),
        ),
        patch("orchestrator.verifiers.engine.verify", return_value=sig) as mock_verify,
        patch("orchestrator.orchestrate._stamp_wave_node"),
        patch("orchestrator.orchestrate._append_wave_verification_section"),
    ):
        _maybe_capture_wave_baseline(stage, {"repo_root": "/tmp"}, run_folder, ctx)
        result = _maybe_run_wave_verification(stage, 1, {"repo_root": "/tmp"}, run_folder, ctx)

    # Also covers the generic-Exception branch via a second call.
    ctx2 = _make_ctx(tmp_path / "second", resume=False)
    rf2 = _make_run_folder(tmp_path / "second")
    with (
        patch("orchestrator.verifiers.engine.capture_baseline", side_effect=RuntimeError("crash")),
    ):
        _maybe_capture_wave_baseline(stage, {"repo_root": "/tmp"}, rf2, ctx2)

    mock_verify.assert_called_once()
    assert result is not None and result["verification_status"] == "passed"
    assert any("baseline capture" in m for m in _warn_messages(ctx))
    assert any("baseline capture" in m for m in _warn_messages(ctx2))


def test_missing_baseline_degrades_block_policy_to_raw_verification_status(tmp_path):
    """No baseline file: orchestrator falls back to verification_status (pre-ADR-033);
    block policy still halts on failed verify."""
    stage = _wave_stage(on_failure="block")
    ctx = _make_ctx(tmp_path, resume=False)
    run_folder = _make_run_folder(tmp_path)

    # Signal omits net_new_status — orchestrator must default to verification_status.
    failed_sig = _verify_signal(verification_status="failed")
    with (
        patch("orchestrator.verifiers.engine.verify", return_value=failed_sig) as mock_verify,
        patch("orchestrator.orchestrate._stamp_wave_node"),
        patch("orchestrator.orchestrate._append_wave_verification_section"),
    ):
        result = _maybe_run_wave_verification(stage, 1, {"repo_root": "/tmp"}, run_folder, ctx)

    assert mock_verify.call_args.kwargs.get("baseline_path") is None
    assert result is not None
    assert result["status"] == "blocked"
    assert "wave 1" in result["message"]


def test_artifact_paths_stable_across_fresh_and_resumed(tmp_path):
    """Baseline path and wave artifact subdir are identical for fresh / resumed runs."""
    stage = _wave_stage()
    sig = _verify_signal(net_new_status="passed")

    # Fresh run.
    fresh = _make_run_folder(tmp_path / "fresh")
    fresh_ctx = _make_ctx(tmp_path / "fresh", resume=False)
    with (
        patch("orchestrator.verifiers.engine.capture_baseline", side_effect=_fake_capture),
        patch("orchestrator.verifiers.engine.verify", return_value=sig) as mock_verify_fresh,
        patch("orchestrator.orchestrate._stamp_wave_node"),
        patch("orchestrator.orchestrate._append_wave_verification_section"),
    ):
        _maybe_capture_wave_baseline(stage, {"repo_root": "/tmp"}, fresh, fresh_ctx)
        _maybe_run_wave_verification(stage, 2, {"repo_root": "/tmp"}, fresh, fresh_ctx)

    assert (fresh / "baseline-verification" / "verify.json").exists()
    assert mock_verify_fresh.call_args.kwargs["artifact_subdir"] == "wave-verification/wave-2"

    # Resumed run with pre-existing baseline.
    resumed = _make_run_folder(tmp_path / "resumed")
    resumed_ctx = _make_ctx(tmp_path / "resumed", resume=True)
    baseline_path = _write_baseline(resumed)
    with (
        patch("orchestrator.verifiers.engine.capture_baseline") as mock_capture_resumed,
        patch("orchestrator.verifiers.engine.verify", return_value=sig) as mock_verify_resumed,
        patch("orchestrator.orchestrate._stamp_wave_node"),
        patch("orchestrator.orchestrate._append_wave_verification_section"),
    ):
        _maybe_capture_wave_baseline(stage, {"repo_root": "/tmp"}, resumed, resumed_ctx)
        _maybe_run_wave_verification(stage, 2, {"repo_root": "/tmp"}, resumed, resumed_ctx)

    mock_capture_resumed.assert_not_called()
    assert baseline_path == resumed / "baseline-verification" / "verify.json"
    assert mock_verify_resumed.call_args.kwargs["artifact_subdir"] == "wave-verification/wave-2"
    assert mock_verify_resumed.call_args.kwargs.get("baseline_path") == baseline_path
