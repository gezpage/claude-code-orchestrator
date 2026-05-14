import json
from pathlib import Path

from orchestrator.verifiers.probes._types import ProbeContext
from orchestrator.verifiers.probes.node_manifest_sanity import run


def _write_manifest(tmp_path: Path, scripts: dict, *, name: str = "x", version: str = "0.0.1") -> Path:
    manifest = {"name": name, "version": version, "scripts": scripts}
    (tmp_path / "package.json").write_text(json.dumps(manifest))
    return tmp_path


def test_no_manifest_passes(tmp_path: Path):
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "passed"
    assert result.findings == []


def test_clean_manifest_passes(tmp_path: Path):
    _write_manifest(tmp_path, {"test": "jest", "lint": "eslint ."})
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "passed"


def test_echo_only_lint_is_noop(tmp_path: Path):
    _write_manifest(tmp_path, {"lint": "echo skipped"})
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "failed"
    assert any("lint" in f and "no-op" in f for f in result.findings)


def test_exit_zero_test_is_noop(tmp_path: Path):
    _write_manifest(tmp_path, {"test": "exit 0"})
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "failed"
    assert any("test" in f for f in result.findings)


def test_bare_true_is_noop(tmp_path: Path):
    _write_manifest(tmp_path, {"test": "true"})
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "failed"


def test_echo_with_real_command_is_not_noop(tmp_path: Path):
    # `echo X && jest` does real work — must not flag.
    _write_manifest(tmp_path, {"test": "echo running && jest"})
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "passed"


def test_missing_name_field_flagged(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"version": "0.0.1", "scripts": {}}))
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "failed"
    assert any("'name'" in f for f in result.findings)


def test_invalid_json_flagged(tmp_path: Path):
    (tmp_path / "package.json").write_text("{ not json")
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "failed"
    assert any("not valid JSON" in f for f in result.findings)
