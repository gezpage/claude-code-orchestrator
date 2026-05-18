import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from orchestrator.verifiers.engine import (
    BASELINE_SUBDIR,
    VerificationError,
    baseline_path_for,
    capture_baseline,
    verify,
)


def _make_repo(tmp_path: Path, manifest: dict | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    if manifest is not None:
        (repo / "package.json").write_text(json.dumps(manifest))
    return repo


def _make_run_folder(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    return run


def _completed(code: int, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = code
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_passing_node_repo(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)
    assert sig["status"] == "passed"
    assert sig["verification_status"] == "passed"
    assert sig["toolchain"] == "node"
    assert mock_run.called
    assert (run_folder / "verification" / "VERIFY.md").exists()
    assert (run_folder / "verification" / "verify.json").exists()


def test_failed_required_command_marks_failed(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)
    # Stage status is still "passed" — verification is not a hard gate (ADR-017).
    assert sig["status"] == "passed"
    assert sig["verification_status"] == "failed"
    assert "test" in sig["failed_command_ids"]


def test_missing_script_skips_command(tmp_path: Path):
    # No scripts in manifest → test command must be skipped, not failed.
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run") as mock_run:
        sig = verify(repo, run_folder)
    # All commands have if_script_exists in node.yaml; none present → none invoked.
    assert mock_run.call_count == 0
    # All required commands skipped → verification not "passed", but probes still run.
    # No required failures and no probe failures, so "warned" (non-required skipped).
    assert sig["verification_status"] in {"passed", "warned"}


def test_noop_lint_script_caught_by_probe(tmp_path: Path):
    repo = _make_repo(
        tmp_path,
        {"name": "x", "version": "0.0.1", "scripts": {"test": "jest", "lint": "echo skipped"}},
    )
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "node_manifest_sanity" in sig["failed_probe_ids"]
    assert sig["verification_status"] == "failed"


def test_timeout_marks_failed(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch(
        "orchestrator.verifiers.engine.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="npm test", timeout=600),
    ):
        sig = verify(repo, run_folder)
    assert sig["verification_status"] == "failed"
    assert "test" in sig["failed_command_ids"]


def test_no_toolchain_returns_skipped_not_blocked(tmp_path: Path):
    """Repos without recognised markers (greenfield, prose-only) must not block the pipeline."""
    repo = _make_repo(tmp_path)  # no manifest
    run_folder = _make_run_folder(tmp_path)
    sig = verify(repo, run_folder)
    assert sig["status"] == "passed"
    assert sig["verification_status"] == "skipped"
    assert sig["toolchain"] == "none"
    assert (run_folder / "verification" / "VERIFY.md").exists()


def _make_python_repo(tmp_path: Path, marker: str = "pyproject.toml") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / marker).write_text("")
    return repo


def test_python_repo_detected_and_pytest_invoked(tmp_path: Path):
    repo = _make_python_repo(tmp_path, "pyproject.toml")
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)
    assert sig["toolchain"] == "python"
    assert sig["verification_status"] == "passed"
    assert sig["command_ids"] == ["test"]
    invoked = [c.kwargs.get("cmd") or c.args[0] for c in mock_run.call_args_list]
    assert any("python -m pytest" in cmd for cmd in invoked)


def test_python_repo_detected_via_test_file_glob(tmp_path: Path):
    # A `.py` file under tests/ is enough to identify the project as Python — bare
    # `tests/` is intentionally not a marker on its own.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_quote.py").write_text("")
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert sig["toolchain"] == "python"


def test_python_bare_tests_dir_alone_does_not_select_python(tmp_path: Path):
    # Bare `tests/` with no .py files inside must not mis-detect as Python; the
    # verifier falls through to skipped instead of dispatching pytest.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    run_folder = _make_run_folder(tmp_path)
    sig = verify(repo, run_folder)
    assert sig["toolchain"] == "none"
    assert sig["verification_status"] == "skipped"


def test_python_failing_pytest_marks_failed(tmp_path: Path):
    repo = _make_python_repo(tmp_path, "pyproject.toml")
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)
    assert sig["verification_status"] == "failed"
    assert "test" in sig["failed_command_ids"]


def test_python_verify_json_records_command(tmp_path: Path):
    repo = _make_python_repo(tmp_path, "requirements.txt")
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        verify(repo, run_folder)
    data = json.loads((run_folder / "verification" / "verify.json").read_text())
    assert data["toolchain"] == "python"
    assert data["status"] == "passed"
    assert any(c["command"] == "python -m pytest" for c in data["commands"])
    test_cmd = next(c for c in data["commands"] if c["id"] == "test")
    assert test_cmd["status"] == "passed"
    assert test_cmd["exit_code"] == 0
    assert "duration_seconds" in test_cmd


def test_unknown_pinned_toolchain_raises(tmp_path: Path):
    """A `.cco.yaml` pin for a recipe that doesn't exist is a user config error, not a benign skip."""
    repo = _make_repo(tmp_path)
    (repo / ".cco.yaml").write_text(yaml.dump({"verification": {"toolchain": "rustacean"}}))
    run_folder = _make_run_folder(tmp_path)
    with pytest.raises(VerificationError, match="unknown toolchain 'rustacean'"):
        verify(repo, run_folder)


def test_explicit_toolchain_via_cco_yaml(tmp_path: Path):
    # No markers → would normally fail to detect; .cco.yaml pins node.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".cco.yaml").write_text(yaml.dump({"verification": {"toolchain": "node", "commands": [], "probes": []}}))
    run_folder = _make_run_folder(tmp_path)
    sig = verify(repo, run_folder)
    # An empty Node override would normally produce no signal at all, but the
    # clean-install audit fires for any Node/TS override missing an install
    # step — so the report surfaces as `warned` instead of `skipped`. The
    # appended `clean-install-audit` row lifts the status, preserving the
    # core intent ("an empty pin must not masquerade as a clean verification").
    assert sig["toolchain"] == "node"
    assert sig["verification_status"] == "warned"
    assert "clean-install-audit" in sig["failed_command_ids"]


def test_command_override_replaces_recipe_commands(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    (repo / ".cco.yaml").write_text(
        yaml.dump(
            {
                "verification": {
                    # Include a clean-install command so the audit stays silent
                    # for this test — the assertions below are about override
                    # replacement, not the audit behaviour (see dedicated tests
                    # further down).
                    "commands": [
                        {"id": "install", "command": "npm ci", "required": True},
                        {"id": "custom", "command": "true", "required": True},
                    ],
                }
            }
        )
    )
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)
    assert sig["command_ids"] == ["install", "custom"]
    # Recipe's `test` command must NOT have been invoked.
    invoked = [c.kwargs.get("cmd") or c.args[0] for c in mock_run.call_args_list]
    assert all("npm test" not in cmd for cmd in invoked)


def test_artifacts_contain_machine_and_human_summary(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        verify(repo, run_folder)
    data = json.loads((run_folder / "verification" / "verify.json").read_text())
    assert data["toolchain"] == "node"
    assert data["status"] in {"passed", "warned", "failed"}
    md = (run_folder / "verification" / "VERIFY.md").read_text()
    assert "Verification Report" in md
    assert "node" in md


# ---------------------------------------------------------------------------
# Baseline vs net-new classification (ADR-033)
# ---------------------------------------------------------------------------


def test_capture_baseline_writes_to_baseline_subdir(tmp_path: Path):
    """The baseline capture lands under baseline-verification/ so wave reports don't collide."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        capture_baseline(repo, run_folder)
    assert (run_folder / BASELINE_SUBDIR / "verify.json").exists()
    assert baseline_path_for(run_folder) == run_folder / BASELINE_SUBDIR / "verify.json"


def test_baseline_unchanged_failure_classified_as_baseline(tmp_path: Path):
    """A failing command that already failed in baseline must be marked baseline, not net_new."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    # Baseline: test fails.
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        capture_baseline(repo, run_folder)
    # Wave run: same test still fails.
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder, baseline_path=baseline_path_for(run_folder))
    assert sig["baseline_compared"] is True
    assert "test" in sig["baseline_failed_command_ids"]
    assert "test" not in sig["new_failed_command_ids"]
    # Verification status reflects the actual command result; net-new is clean.
    assert sig["verification_status"] == "failed"
    assert sig["net_new_status"] == "passed"


def test_net_new_failure_classified_as_net_new(tmp_path: Path):
    """A command that passed in baseline but fails now must be flagged as a regression."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    # Baseline: test passes.
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        capture_baseline(repo, run_folder)
    # Wave: test fails.
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder, baseline_path=baseline_path_for(run_folder))
    assert sig["baseline_compared"] is True
    assert "test" in sig["new_failed_command_ids"]
    assert "test" not in sig["baseline_failed_command_ids"]
    assert sig["verification_status"] == "failed"
    assert sig["net_new_status"] == "failed"


def test_resolved_baseline_failure_listed(tmp_path: Path):
    """A baseline failure that no longer fails appears in resolved_command_ids."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        capture_baseline(repo, run_folder)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder, baseline_path=baseline_path_for(run_folder))
    assert sig["baseline_compared"] is True
    assert "test" in sig["resolved_command_ids"]
    assert sig["verification_status"] == "passed"
    assert sig["net_new_status"] == "passed"


def test_missing_baseline_falls_back_to_no_classification(tmp_path: Path):
    """A missing baseline file must not raise — verify just runs without comparison."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    bogus = tmp_path / "does-not-exist.json"
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder, baseline_path=bogus)
    assert sig["baseline_compared"] is False
    assert sig["new_failed_command_ids"] == []
    assert sig["baseline_failed_command_ids"] == []
    # Without classification, net_new_status mirrors verification_status.
    assert sig["net_new_status"] == sig["verification_status"] == "failed"


def test_corrupt_baseline_falls_back_to_no_classification(tmp_path: Path):
    """A malformed baseline file is treated as missing, never crashes the verifier."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    baseline = run_folder / BASELINE_SUBDIR / "verify.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("{not valid json")
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder, baseline_path=baseline)
    assert sig["baseline_compared"] is False


def test_artifacts_show_baseline_comparison_section(tmp_path: Path):
    """VERIFY.md surfaces the baseline-vs-net-new breakdown so reviewers see it directly."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        capture_baseline(repo, run_folder)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        verify(repo, run_folder, baseline_path=baseline_path_for(run_folder))
    md = (run_folder / "verification" / "VERIFY.md").read_text()
    assert "Baseline Comparison" in md
    assert "Net-new status" in md
    # Failure kind column appears in the command table.
    assert "Kind" in md


# ---------------------------------------------------------------------------
# PHP recipe — composer / phpunit precondition behaviour
# ---------------------------------------------------------------------------


def _make_php_repo(tmp_path: Path, composer: dict | None = None, with_phpunit_binary: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    if composer is not None:
        (repo / "composer.json").write_text(json.dumps(composer))
    if with_phpunit_binary:
        bin_dir = repo / "vendor" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "phpunit").write_text("#!/bin/sh\nexit 0\n")
    return repo


def test_php_repo_with_composer_test_script_runs_composer(tmp_path: Path):
    repo = _make_php_repo(tmp_path, composer={"scripts": {"test": "phpunit"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)
    assert sig["toolchain"] == "php"
    invoked = [c.args[0] for c in mock_run.call_args_list]
    assert "composer test" in invoked
    # phpunit binary not present → that command is skipped, not invoked.
    assert "vendor/bin/phpunit" not in invoked
    # A required_any_of member ran and passed → passed.
    assert sig["verification_status"] == "passed"


def test_php_repo_without_composer_test_falls_back_to_phpunit(tmp_path: Path):
    repo = _make_php_repo(tmp_path, composer={}, with_phpunit_binary=True)
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)
    invoked = [c.args[0] for c in mock_run.call_args_list]
    # composer test must be skipped because scripts.test is absent.
    assert "composer test" not in invoked
    assert "vendor/bin/phpunit" in invoked
    assert sig["verification_status"] == "passed"


def test_php_repo_with_neither_downgrades_to_warned(tmp_path: Path):
    """Composer.json without scripts.test AND no installed phpunit → no eligible
    test command ran. The recipe must NOT report 'passed' — that would be silent
    false confidence. required_any_of escalates the all-skipped case to 'warned'."""
    repo = _make_php_repo(tmp_path, composer={})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run") as mock_run:
        sig = verify(repo, run_folder)
    assert mock_run.call_count == 0
    assert sig["toolchain"] == "php"
    assert sig["verification_status"] == "warned"
    assert "no eligible test command ran" in sig["summary"]


def test_php_phpunit_failure_marks_failed(tmp_path: Path):
    """When phpunit (a required_any_of member) actually runs and fails, the
    recipe is asserting that this IS the test — failure must surface as
    'failed', not be softened to 'warned' by the per-command non-required flag."""
    repo = _make_php_repo(tmp_path, composer={}, with_phpunit_binary=True)
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)
    assert sig["verification_status"] == "failed"
    assert "phpunit" in sig["failed_command_ids"]


def test_php_composer_test_failure_marks_failed(tmp_path: Path):
    """Composer test failure is the same hard-failure path as phpunit."""
    repo = _make_php_repo(tmp_path, composer={"scripts": {"test": "phpunit"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)
    assert sig["verification_status"] == "failed"
    assert "composer-test" in sig["failed_command_ids"]


def test_php_verify_md_surfaces_no_test_ran_note(tmp_path: Path):
    """VERIFY.md must explicitly call out the 'no test ran' case so reviewers
    aren't misled by an empty Commands table next to a 'warned' badge."""
    repo = _make_php_repo(tmp_path, composer={})
    run_folder = _make_run_folder(tmp_path)
    verify(repo, run_folder)
    md = (run_folder / "verification" / "VERIFY.md").read_text()
    assert "none of the expected test commands ran" in md
    assert "composer-test" in md
    assert "phpunit" in md


def test_no_baseline_path_means_no_classification(tmp_path: Path):
    """Calling verify without baseline_path leaves classification fields empty."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)
    assert sig["baseline_compared"] is False
    assert sig["baseline_failed_command_ids"] == []
    assert sig["new_failed_command_ids"] == []


# ---------------------------------------------------------------------------
# TypeScript recipe engine tests
# ---------------------------------------------------------------------------


def _make_ts_repo(tmp_path: Path, manifest: dict | None = None) -> Path:
    """Create a TypeScript repo (package.json + tsconfig.json)."""
    repo = _make_repo(tmp_path, manifest)
    (repo / "tsconfig.json").write_text("{}")
    return repo


def test_typescript_repo_detected_and_test_runs(tmp_path: Path):
    repo = _make_ts_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "vitest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert sig["toolchain"] == "typescript"
    assert sig["verification_status"] == "passed"
    assert "test" in sig["command_ids"]


def test_typescript_repo_typecheck_runs_when_script_present(tmp_path: Path):
    repo = _make_ts_repo(
        tmp_path,
        {"name": "x", "version": "0.0.1", "scripts": {"test": "vitest", "typecheck": "tsc --noEmit"}},
    )
    run_folder = _make_run_folder(tmp_path)
    invoked: list[str] = []

    def _capture(cmd: str, **_: object) -> MagicMock:
        invoked.append(cmd)
        return _completed(0)

    with patch("orchestrator.verifiers.engine.subprocess.run", side_effect=_capture):
        sig = verify(repo, run_folder)
    assert sig["verification_status"] == "passed"
    assert any("typecheck" in c for c in invoked)


def test_typescript_repo_typecheck_skipped_when_script_absent(tmp_path: Path):
    # No `typecheck` script → command must be skipped, not failed.
    repo = _make_ts_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "vitest"}})
    run_folder = _make_run_folder(tmp_path)
    invoked: list[str] = []

    def _capture(cmd: str, **_: object) -> MagicMock:
        invoked.append(cmd)
        return _completed(0)

    with patch("orchestrator.verifiers.engine.subprocess.run", side_effect=_capture):
        sig = verify(repo, run_folder)
    assert not any("typecheck" in c for c in invoked)
    assert sig["verification_status"] == "passed"


def test_typescript_repo_missing_test_script_downgrades_to_warned(tmp_path: Path):
    """A TypeScript repo with no `test` script ran zero deterministic commands.
    `required_any_of` must downgrade the report to `warned` — a `passed` result
    here is silent false confidence (same shape as the PHP fix in #173)."""
    repo = _make_ts_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run") as mock_run:
        sig = verify(repo, run_folder)
    assert mock_run.call_count == 0
    assert sig["toolchain"] == "typescript"
    assert sig["verification_status"] == "warned"
    assert "no eligible test command ran" in sig["summary"]


def test_typescript_failing_test_marks_failed(tmp_path: Path):
    """The test command failing is still a hard failure — required_any_of doesn't soften it."""
    repo = _make_ts_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "vitest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)
    assert sig["verification_status"] == "failed"
    assert "test" in sig["failed_command_ids"]


def test_plain_js_repo_not_misdetected_as_typescript(tmp_path: Path):
    # No tsconfig.json or other TS markers → must fall through to Node recipe.
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert sig["toolchain"] == "node"


# ---------------------------------------------------------------------------
# Clean-install audit for custom Node/TS verification.commands (issue #200
# follow-up to PR #201). A project that fully overrides the bundled recipe
# bypasses the recipe-level npm ci protection; the audit catches the omission.
# ---------------------------------------------------------------------------


def _write_cco(repo: Path, commands: list[dict]) -> None:
    (repo / ".cco.yaml").write_text(yaml.dump({"verification": {"commands": commands}}))


def test_clean_install_audit_fires_for_node_override_without_clean_install(tmp_path: Path):
    """Node project with custom verification.commands that omit npm ci must
    surface a non-required failed audit row so the report aggregates to
    `warned` and the executive summary's skipped/warned section catches it."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    _write_cco(repo, [{"id": "test", "command": "npm test", "required": True}])
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "clean-install-audit" in sig["command_ids"]
    assert "clean-install-audit" in sig["failed_command_ids"]
    # Audit is non-required, real test passed → aggregate warned, not failed.
    assert sig["verification_status"] == "warned"
    data = json.loads((run_folder / "verification" / "verify.json").read_text())
    audit = next(c for c in data["commands"] if c["id"] == "clean-install-audit")
    assert audit["required"] is False
    assert audit["note"] is not None
    assert "npm ci" in audit["note"]
    assert "yarn install --frozen-lockfile" in audit["note"]
    assert "pnpm install --frozen-lockfile" in audit["note"]


def test_clean_install_audit_silent_when_npm_ci_in_override(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    _write_cco(
        repo,
        [
            {"id": "install", "command": "npm ci", "required": True},
            {"id": "test", "command": "npm test", "required": True},
        ],
    )
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "clean-install-audit" not in sig["command_ids"]
    assert sig["verification_status"] == "passed"


def test_clean_install_audit_silent_when_yarn_frozen_in_override(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    _write_cco(
        repo,
        [
            {"id": "install", "command": "yarn install --frozen-lockfile", "required": True},
            {"id": "test", "command": "yarn test", "required": True},
        ],
    )
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "clean-install-audit" not in sig["command_ids"]


def test_clean_install_audit_silent_when_pnpm_frozen_in_override(tmp_path: Path):
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    _write_cco(
        repo,
        [
            {"id": "install", "command": "pnpm install --frozen-lockfile", "required": True},
            {"id": "test", "command": "pnpm test", "required": True},
        ],
    )
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "clean-install-audit" not in sig["command_ids"]


def test_clean_install_audit_fires_for_typescript_override(tmp_path: Path):
    repo = _make_ts_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "vitest"}})
    _write_cco(repo, [{"id": "test", "command": "npm test", "required": True}])
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert sig["toolchain"] == "typescript"
    assert "clean-install-audit" in sig["failed_command_ids"]
    assert sig["verification_status"] == "warned"


def test_clean_install_audit_silent_for_non_node_toolchain(tmp_path: Path):
    """Python/Go/Java/PHP overrides must not fire the audit — they have no
    bundled clean-install step and the audit semantics don't apply."""
    repo = _make_python_repo(tmp_path, "pyproject.toml")
    _write_cco(repo, [{"id": "test", "command": "python -m pytest", "required": True}])
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert sig["toolchain"] == "python"
    assert "clean-install-audit" not in sig["command_ids"]


def test_clean_install_audit_silent_when_no_override(tmp_path: Path):
    """No .cco.yaml override → bundled recipe handles the clean install itself,
    audit is unnecessary."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "clean-install-audit" not in sig["command_ids"]


def test_clean_install_audit_silent_when_probes_only_overridden(tmp_path: Path):
    """Probe override without command override leaves the bundled commands in
    place, so the bundled clean-install runs as normal — no audit needed."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    (repo / ".cco.yaml").write_text(yaml.dump({"verification": {"probes": []}}))
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)
    assert "clean-install-audit" not in sig["command_ids"]


def test_clean_install_audit_note_appears_in_verify_md(tmp_path: Path):
    """The audit's `note` field must render as a `- note: ...` line in
    VERIFY.md so reviewers see the explanation, not just the failing row."""
    repo = _make_repo(tmp_path, {"name": "x", "version": "0.0.1", "scripts": {"test": "jest"}})
    _write_cco(repo, [{"id": "test", "command": "npm test", "required": True}])
    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        verify(repo, run_folder)
    md = (run_folder / "verification" / "VERIFY.md").read_text()
    assert "clean-install-audit" in md
    assert "note:" in md
    assert "npm ci" in md
