from pathlib import Path

from orchestrator.verifiers.probes._types import ProbeContext
from orchestrator.verifiers.probes.go_module_sanity import run


def test_no_go_mod_passes(tmp_path: Path):
    assert run(ProbeContext(repo_root=tmp_path)).status == "passed"


def test_well_formed_no_deps_passes(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "passed"


def test_missing_module_declaration_flagged(tmp_path: Path):
    (tmp_path / "go.mod").write_text("go 1.22\n")
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "failed"
    assert any("module" in f for f in result.findings)


def test_deps_without_go_sum_flagged(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n\nrequire (\n  example.com/dep v1.0.0\n)\n")
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "failed"
    assert any("go.sum" in f for f in result.findings)


def test_deps_with_go_sum_passes(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n\nrequire example.com/dep v1.0.0\n")
    (tmp_path / "go.sum").write_text("example.com/dep v1.0.0/go.mod h1:abc\n")
    result = run(ProbeContext(repo_root=tmp_path))
    assert result.status == "passed"
