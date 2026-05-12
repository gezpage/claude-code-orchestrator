import pytest

from orchestrator.paths import (
    require_dir,
    require_file,
    resolve_prompts_dir,
    resolve_run_folder,
    resolve_workflow_root,
)


def test_require_file_happy(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert require_file(f) == f


def test_require_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match=str(tmp_path / "missing.txt")):
        require_file(tmp_path / "missing.txt")


def test_require_dir_happy(tmp_path):
    assert require_dir(tmp_path) == tmp_path


def test_require_dir_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="no-such-dir"):
        require_dir(tmp_path / "no-such-dir")


def test_resolve_workflow_root_happy(tmp_path):
    wf = tmp_path / "projects" / "myproject" / "workflow"
    wf.mkdir(parents=True)
    result = resolve_workflow_root(tmp_path, "myproject")
    assert result == wf


def test_resolve_workflow_root_missing_docs_root(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_workflow_root(tmp_path / "nonexistent", "myproject")


def test_resolve_workflow_root_missing_project(tmp_path):
    (tmp_path / "projects").mkdir()
    with pytest.raises(FileNotFoundError):
        resolve_workflow_root(tmp_path, "ghost-project")


def test_resolve_workflow_root_missing_workflow_dir(tmp_path):
    (tmp_path / "projects" / "myproject").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        resolve_workflow_root(tmp_path, "myproject")


def test_resolve_run_folder(tmp_path):
    result = resolve_run_folder(tmp_path, "myproject", "my-feature", "2026-05-08", 1)
    expected = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "my-feature" / "2026-05-08-run-1"
    assert result == expected


def test_resolve_prompts_dir(tmp_path):
    result = resolve_prompts_dir(tmp_path, "myproject")
    expected = tmp_path / "projects" / "myproject" / "workflow" / "prompts"
    assert result == expected
