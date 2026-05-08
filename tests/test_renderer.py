import pytest
from pathlib import Path
from orchestrator.renderer import render_prompt


VARS = {
    "run_folder": "/tmp/run",
    "feature_path": "/tmp/docs/projects/myproject/feature",
    "docs_root": "/tmp/docs",
}


def test_core_only_render(tmp_path):
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    assert "/tmp/run" in result
    assert "## Project conventions" not in result


def test_core_plus_extension(tmp_path):
    ext_dir = tmp_path / "projects" / "myproject" / "workflow" / "prompts"
    ext_dir.mkdir(parents=True)
    (ext_dir / "discovery.md").write_text("Use the internal wiki for discovery.")
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    assert "## Project conventions" in result
    assert "internal wiki" in result


def test_missing_core_template_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Core prompt template not found"):
        render_prompt("discovery", "nonexistent", VARS, str(tmp_path), "myproject")


def test_missing_extension_no_error(tmp_path):
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    assert "## Project conventions" not in result


def test_variable_substitution(tmp_path):
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    assert "/tmp/run" in result
    assert "/tmp/docs/projects/myproject/feature" in result
    # Variables are substituted — no raw Jinja2 tags remain
    assert "{{" not in result


def test_extension_appended_after_core(tmp_path):
    ext_dir = tmp_path / "projects" / "myproject" / "workflow" / "prompts"
    ext_dir.mkdir(parents=True)
    (ext_dir / "discovery.md").write_text("Project rule here.")
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    # Core content precedes project conventions
    assert "/tmp/run" in result
    conventions_pos = result.index("## Project conventions")
    run_pos = result.index("/tmp/run")
    assert run_pos < conventions_pos
