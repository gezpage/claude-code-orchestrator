"""Characterization tests for ``_dispatch_slices`` (issue #154).

Pins observable contracts that protect a future extraction of slice
execution. Avoids asserting threading internals and never branches on
profile names — slice behaviour is config-driven via ``StageConfig`` and
the ``slice_files`` / ``slice_groups`` signal contract.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.orchestrate import _dispatch_slices, _PipelineContext
from orchestrator.profile import ExpansionKind, StageConfig


def _make_ctx(tmp_path: Path) -> _PipelineContext:
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


def _make_run_folder(tmp_path: Path) -> Path:
    rf = tmp_path / "runs" / "run-1"
    rf.mkdir(parents=True)
    return rf


def _slice_stage() -> StageConfig:
    return StageConfig(
        name="implementation",
        prompt="prompts/implementation/default.md",
        expansion=ExpansionKind.SLICES,
        slices_from_stage="decomposition",
    )


@pytest.fixture
def patched():
    """Patch every external collaborator so the test only observes the
    dispatcher's own behaviour. Wave verification is stubbed to no-op via
    a patched ``verify`` so this file pins slice mechanics, not wave health.
    """
    with (
        patch("orchestrator.orchestrate._create_branch") as cb,
        patch("orchestrator.orchestrate._create_worktree", return_value="/tmp/wt") as cwt,
        patch("orchestrator.orchestrate._remove_worktree") as rwt,
        patch("orchestrator.orchestrate._merge_worktree_branch") as mwt,
        patch("orchestrator.orchestrate.expand_nodes"),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.run_stage") as rs,
        patch("orchestrator.verifiers.engine.verify", return_value={"status": "passed"}),
    ):
        rs.return_value = {"status": "passed", "commit_hashes": []}
        yield {"cb": cb, "cwt": cwt, "rwt": rwt, "mwt": mwt, "rs": rs}


def _exec_stub(submit_side_effect):
    """Build a stubbed ThreadPoolExecutor that runs ``submit`` synchronously
    via the supplied side_effect (callable that returns a Future-like mock).
    """
    exec_mock = MagicMock()
    exec_mock.__enter__ = MagicMock(return_value=exec_mock)
    exec_mock.__exit__ = MagicMock(return_value=False)
    exec_mock.submit.side_effect = submit_side_effect
    return exec_mock


# ── tests ─────────────────────────────────────────────────────────────────────


def test_non_slice_entries_filtered_keep_inputs_aligned_with_warning(tmp_path, patched):
    """Non-S-NN entries are dropped with a WARN log; the index alignment between
    ``slice_files`` and ``slice_inputs`` survives — the surviving slice keeps
    its own inputs, not a dropped entry's.
    """
    ctx = _make_ctx(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["README.md", "S-01-real.md", "notes.txt"],
            "slice_inputs": [["junk-a"], ["real-input"], ["junk-b"]],
            "slice_groups": [["README.md", "S-01-real.md", "notes.txt"]],
        }
    }
    patched["rs"].return_value = {"status": "passed", "commit_hashes": ["abc"]}

    result = _dispatch_slices(_slice_stage(), {"repo_root": "/tmp"}, _make_run_folder(tmp_path), ctx, signals)

    assert result["status"] == "passed"
    assert patched["rs"].call_count == 1, "two non-slice entries must be filtered out"
    args, kwargs = patched["rs"].call_args
    assert args[2]["slice_file"] == "S-01-real.md"
    assert kwargs.get("inputs") == ["real-input"], (
        f"surviving slice must keep its own inputs, got {kwargs.get('inputs')!r}"
    )
    log_mock: MagicMock = ctx.logger.log  # type: ignore[assignment]
    warns = [c for c in log_mock.call_args_list if c.args[1] == "WARN" and "filtered" in c.args[2]]
    assert warns, "expected a WARN log about non-slice filtering — must not be silent"


def test_multiple_slice_groups_run_serially_one_wave_at_a_time(tmp_path, patched):
    """Two ``slice_groups`` => two waves. Each single-slice wave dispatches
    via the serial path (no worktrees) and waves execute in declaration order.
    """
    ctx = _make_ctx(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md"], ["S-02-b.md"]],
        }
    }
    patched["rs"].return_value = {"status": "passed", "commit_hashes": ["c"]}

    result = _dispatch_slices(_slice_stage(), {"repo_root": "/tmp"}, _make_run_folder(tmp_path), ctx, signals)

    assert result["status"] == "passed"
    seen = [c.args[2]["slice_file"] for c in patched["rs"].call_args_list]
    assert seen == ["S-01-a.md", "S-02-b.md"], f"waves must dispatch in order, got {seen!r}"
    patched["cwt"].assert_not_called()


def test_parallel_wave_isolates_slice_file_per_dispatch_no_crossover(tmp_path, patched):
    """In a multi-slice wave each parallel dispatch sees its own ``slice_file``
    in ``variables`` — the per-slice ``dict(variables)`` copy is what makes
    cross-contamination impossible.
    """
    ctx = _make_ctx(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md", "S-03-c.md"],
            "slice_groups": [["S-01-a.md", "S-02-b.md", "S-03-c.md"]],
        }
    }
    submitted: list[str] = []

    def _submit(_fn, _stage, _impl, vars_copy, *rest, **kw):
        submitted.append(vars_copy["slice_file"])
        f = MagicMock()
        f.result.return_value = ({"status": "passed", "commit_hashes": ["x"]}, 1.0)
        return f

    with patch("concurrent.futures.ThreadPoolExecutor", return_value=_exec_stub(_submit)):
        result = _dispatch_slices(_slice_stage(), {"repo_root": "/tmp"}, _make_run_folder(tmp_path), ctx, signals)

    assert result["status"] == "passed"
    assert sorted(submitted) == ["S-01-a.md", "S-02-b.md", "S-03-c.md"]
    assert len(submitted) == len(set(submitted)), f"duplicate slice_file values seen: {submitted!r}"


def test_passed_slices_aggregate_commit_hashes_into_passed_signal(tmp_path, patched):
    """All-passed slices => single ``status=passed`` signal whose
    ``commit_hashes`` is the concatenation in wave order, with branch preserved.
    """
    ctx = _make_ctx(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md"], ["S-02-b.md"]],
        }
    }
    patched["rs"].side_effect = [
        {"status": "passed", "commit_hashes": ["h-aaa", "h-bbb"]},
        {"status": "passed", "commit_hashes": ["h-ccc"]},
    ]

    result = _dispatch_slices(_slice_stage(), {"repo_root": "/tmp"}, _make_run_folder(tmp_path), ctx, signals)

    assert result == {
        "stage": "implementation",
        "status": "passed",
        "commit_hashes": ["h-aaa", "h-bbb", "h-ccc"],
        "branch": "feat/test",
    }


def test_failed_parallel_slice_aggregates_to_non_passing_signal(tmp_path, patched):
    """A single failed slice in a parallel wave => aggregated signal is
    non-passing. The dispatcher must not silently swallow a slice failure.
    """
    ctx = _make_ctx(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md"],
            "slice_groups": [["S-01-a.md", "S-02-b.md"]],
        }
    }
    pass_f = MagicMock()
    pass_f.result.return_value = ({"status": "passed", "commit_hashes": ["ok"]}, 1.0)
    fail_f = MagicMock()
    fail_f.result.return_value = ({"status": "failed", "message": "tests"}, 0.5)
    queue = [pass_f, fail_f]

    with patch("concurrent.futures.ThreadPoolExecutor", return_value=_exec_stub(lambda *a, **kw: queue.pop(0))):
        result = _dispatch_slices(_slice_stage(), {"repo_root": "/tmp"}, _make_run_folder(tmp_path), ctx, signals)

    assert result["status"] in ("failed", "blocked"), f"expected non-passing aggregate, got {result!r}"


def test_parallel_wave_creates_and_removes_one_worktree_per_slice(tmp_path, patched):
    """N parallel slices => N worktrees created and N removed. Pins the
    observable create/remove count contract — no threading internals.
    """
    ctx = _make_ctx(tmp_path)
    signals = {
        "decomposition": {
            "slice_files": ["S-01-a.md", "S-02-b.md", "S-03-c.md"],
            "slice_groups": [["S-01-a.md", "S-02-b.md", "S-03-c.md"]],
        }
    }

    def _submit(*_a, **_kw):
        f = MagicMock()
        f.result.return_value = ({"status": "passed", "commit_hashes": ["c"]}, 1.0)
        return f

    with patch("concurrent.futures.ThreadPoolExecutor", return_value=_exec_stub(_submit)):
        result = _dispatch_slices(_slice_stage(), {"repo_root": "/tmp"}, _make_run_folder(tmp_path), ctx, signals)

    assert result["status"] == "passed"
    assert patched["cwt"].call_count == 3
    assert patched["rwt"].call_count == 3
    assert patched["mwt"].call_count == 3, "successful slices must each merge their worktree branch"
