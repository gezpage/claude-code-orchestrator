from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.renderer import render_prompt

VARS = {
    "run_folder": "/tmp/run",
    "feature_path": "/tmp/docs/projects/myproject/feature",
    "docs_root": "/tmp/docs",
    "repo_root": "/tmp/repo",
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


def _make_skill(skills_dir: Path, identifier: str, body: str, h1: str = "") -> None:
    d = skills_dir / f"harsh-{identifier}-engineering-standards"
    d.mkdir(parents=True)
    h1_line = f"# {h1}\n\n" if h1 else ""
    (d / "SKILL.md").write_text(f"---\nname: harsh-{identifier}-engineering-standards\n---\n\n{h1_line}{body}")


def test_standards_none_produces_no_block(tmp_path):
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject", standards=None)
    assert "## Engineering Standards" not in result


def test_standards_injected_when_provided(tmp_path):
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    _make_skill(skills_dir, "general", "Do good work.\n", h1="General Rules")
    _make_skill(skills_dir, "php", "Use strict_types.\n", h1="PHP Rules")
    with patch("orchestrator.standards._SKILLS_DIR", skills_dir):
        result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject", standards=["php"])
    assert "## Engineering Standards" in result
    assert "### General Rules" in result
    assert "### PHP Rules" in result


def test_standards_block_before_project_conventions(tmp_path):
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    _make_skill(skills_dir, "general", "Do good work.\n", h1="General Rules")
    ext_dir = tmp_path / "projects" / "myproject" / "workflow" / "prompts"
    ext_dir.mkdir(parents=True)
    (ext_dir / "discovery.md").write_text("Project rule here.")
    with patch("orchestrator.standards._SKILLS_DIR", skills_dir):
        result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject", standards=[])
    standards_pos = result.index("## Engineering Standards")
    conventions_pos = result.index("## Project conventions")
    assert standards_pos < conventions_pos


def test_standards_empty_list_injects_only_general(tmp_path):
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    _make_skill(skills_dir, "general", "Do good work.\n", h1="General Rules")
    _make_skill(skills_dir, "php", "Use strict_types.\n", h1="PHP Rules")
    with patch("orchestrator.standards._SKILLS_DIR", skills_dir):
        result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject", standards=[])
    assert "General Rules" in result
    assert "PHP Rules" not in result


def test_standards_no_block_when_no_skills_available(tmp_path):
    empty_skills_dir = tmp_path / ".claude" / "skills"
    empty_skills_dir.mkdir(parents=True)
    with patch("orchestrator.standards._SKILLS_DIR", empty_skills_dir):
        result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject", standards=[])
    assert "## Engineering Standards" not in result


def test_path_aliases_section_included(tmp_path):
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    assert "## Path aliases" in result
    assert "`$REPO_ROOT` → `/tmp/repo`" in result
    assert "`$RUN_FOLDER` → `/tmp/run`" in result
    assert "`$DOCS_ROOT` → `/tmp/docs`" in result


def test_path_aliases_used_in_body_prose(tmp_path):
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    # Body prose references aliases rather than repeating absolute paths
    assert "$REPO_ROOT" in result
    assert "$RUN_FOLDER" in result


def test_signal_json_keeps_absolute_paths(tmp_path):
    # Agents emit absolute paths in SIGNAL_JSON, so those examples remain Jinja-expanded.
    result = render_prompt("discovery", "default", VARS, str(tmp_path), "myproject")
    assert '"findings_files": ["/tmp/run/discovery/findings.md"]' in result


@pytest.mark.parametrize("reviewer", ["architecture", "implementation", "tests"])
def test_review_prompts_render_without_verification_context(tmp_path, reviewer):
    review_vars = {
        **VARS,
        "review_md": "/tmp/run/review/review-log.md",
        "diff": "/tmp/run/review/diff-round-1.patch",
        "round": "1",
        "context_path": "",
    }

    result = render_prompt("review", reviewer, review_vars, str(tmp_path), "myproject")

    assert "Deterministic verification context" not in result


@pytest.mark.parametrize("reviewer", ["architecture", "implementation", "tests"])
def test_review_prompts_include_blocking_policy(tmp_path, reviewer):
    review_vars = {
        **VARS,
        "review_md": "/tmp/run/review/review-log.md",
        "diff": "/tmp/run/review/diff-round-1.patch",
        "round": "1",
        "context_path": "/tmp/run/specification/context.md",
    }

    result = render_prompt("review", reviewer, review_vars, str(tmp_path), "myproject")

    assert "## Blocking policy" in result
    assert "confirmed violation" in result
    assert "PRD" in result
    assert "context.md" in result
    assert "acceptance criteria" in result
    assert "deterministic verification" in result
    assert "documented user-facing behaviour" in result
    # The "do not downgrade" framing must be present so reviewers know the
    # listed excuses (happy path works, tests pass, edge case is uncommon, fix
    # is small, found manually) do not justify dropping a confirmed violation.
    assert "downgrade" in result
    assert "happy path" in result
    assert "edge case is uncommon" in result
    assert "found manually" in result
    # Confirmed-only — speculative concerns are still non-blocking.
    assert "Speculative" in result or "speculative" in result


def test_implementation_review_calls_out_unhandled_exception_5xx(tmp_path):
    review_vars = {
        **VARS,
        "review_md": "/tmp/run/review/review-log.md",
        "diff": "/tmp/run/review/diff-round-1.patch",
        "round": "1",
        "context_path": "/tmp/run/specification/context.md",
    }

    result = render_prompt("review", "implementation", review_vars, str(tmp_path), "myproject")

    # The bug class that motivated this rule: user-controlled input parsing into
    # infinity/NaN, then producing an unhandled exception or 5xx.
    assert "unhandled exception" in result
    assert "5xx" in result
    assert "1e500" in result


@pytest.mark.parametrize("template", ["default", "minimal"])
def test_decomposition_prompts_include_numeric_edge_cases(tmp_path, template):
    decomposition_vars = {
        **VARS,
        "prd_path": "/tmp/run/specification/prd.md",
        "context_path": "/tmp/run/specification/context.md",
        "run_glossary_path": "",
        "canonical_glossary_path": "",
    }

    result = render_prompt("decomposition", template, decomposition_vars, str(tmp_path), "myproject")

    assert "Numeric input edge cases" in result
    # The full edge-case checklist must appear so implementation plans can
    # explicitly enumerate or justify omission.
    for needle in (
        "negative values",
        "zero where disallowed",
        "non-numeric text",
        "decimal values where integers are required",
        "NaN",
        "Infinity",
        "1e500",
        "extremely large values",
        "whitespace",
    ):
        assert needle in result, f"missing edge case wording: {needle!r}"
