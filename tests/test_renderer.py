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
    assert "PRD artifact" in result
    # The blocking policy must refer to the generated context artifact through
    # the path variable, not a literal filename, so reviewers cannot look for
    # a stray `context.md` in the working directory.
    assert "generated context artifact" in result
    assert "/tmp/run/specification/context.md" in result
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


@pytest.mark.parametrize("reviewer", ["architecture", "implementation", "tests"])
def test_review_prompts_have_no_literal_context_md(tmp_path, reviewer):
    # Reviewers receive the context artifact via `{{ context_path }}` and must
    # not be told to look for a literal `context.md` file — that file does not
    # exist in the working directory and the reference is misleading.
    review_vars = {
        **VARS,
        "review_md": "/tmp/run/review/review-log.md",
        "diff": "/tmp/run/review/diff-round-1.patch",
        "round": "1",
        "context_path": "/tmp/run/specification/context.md",
    }

    result = render_prompt("review", reviewer, review_vars, str(tmp_path), "myproject")

    # The rendered context_path itself ends in "context.md" and is fine —
    # we only forbid the bare `context.md` token used as policy wording.
    assert "`context.md`" not in result


def test_decomposition_minimal_refers_to_generated_context_artifact(tmp_path):
    decomposition_vars = {
        **VARS,
        "prd_path": "/tmp/run/specification/prd.md",
        "context_path": "/tmp/run/specification/context.md",
        "run_glossary_path": "",
        "canonical_glossary_path": "",
    }

    result = render_prompt("decomposition", "minimal", decomposition_vars, str(tmp_path), "myproject")

    assert "generated context artifact" in result
    assert "/tmp/run/specification/context.md" in result
    # No bare `context.md` wording — downstream prompts must reference the
    # artifact through the path variable or by canonical conceptual name.
    assert "`context.md`" not in result


def test_implementation_minimal_refers_to_generated_context_artifact(tmp_path):
    impl_vars = {
        **VARS,
        "plan_file": "/tmp/run/decomposition/implementation-plan.md",
        "prd_path": "/tmp/run/specification/prd.md",
        "context_path": "/tmp/run/specification/context.md",
        "branch": "feature/test",
        "run_glossary_path": "",
    }

    result = render_prompt("implementation", "minimal", impl_vars, str(tmp_path), "myproject")

    assert "generated context artifact" in result
    assert "/tmp/run/specification/context.md" in result
    assert "`context.md`" not in result


def test_specification_minimal_keeps_writer_path_literal(tmp_path):
    # The specification stage writes the artifact, so it legitimately names
    # `$RUN_FOLDER/specification/context.md` in writer instructions.
    spec_vars = {
        **VARS,
        "feature_path": "myfeature",
        "project_context_path": "/tmp/docs/projects/myproject/project-context.md",
        "run_glossary_path": "",
        "canonical_glossary_path": "",
    }

    result = render_prompt("specification", "minimal", spec_vars, str(tmp_path), "myproject")

    # The writer instruction (and template heading) keep the literal filename.
    assert "$RUN_FOLDER/specification/context.md" in result
    assert "context.md template" in result


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


def test_executive_summary_calls_out_verification_honestly(tmp_path):
    summary_vars = {
        **VARS,
        "plan_md_path": "/tmp/run/plan.md",
        "overview_md_path": "/tmp/docs/projects/myproject/feature/overview.md",
        "state_yaml_path": "/tmp/run/_state.yaml",
        "summary_path": "/tmp/run/executive_summary.md",
        "pr_url": "not created",
        "base_branch": "main",
        "branch": "feature/test",
        "feature_path": "myfeature",
        "project": "myproject",
    }

    result = render_prompt("executive_summary", "default", summary_vars, str(tmp_path), "myproject")

    # Verification must be reported honestly, with skipped/warned states named
    # so a recipe that ran zero commands cannot pass as green.
    assert "Deterministic verification" in result
    assert "skipped" in result
    assert "warned" in result
    # The no-overclaim rule must be explicit so the summary cannot describe a
    # warned-or-skipped-verification run as "production ready" or "complete".
    assert "overclaim" in result.lower() or "do not describe" in result.lower()
    assert "production ready" in result
    # Accepted assumptions from alignment must surface, not be buried.
    assert "accepted assumptions" in result.lower()


def test_executive_summary_separates_internals_from_product_usability(tmp_path):
    """Issue #200: the executive summary must distinguish verified internals
    from product usability and link primary workflow evidence — passing unit
    tests cannot be paraphrased as a working product."""
    summary_vars = {
        **VARS,
        "plan_md_path": "/tmp/run/plan.md",
        "overview_md_path": "/tmp/docs/projects/myproject/feature/overview.md",
        "state_yaml_path": "/tmp/run/_state.yaml",
        "summary_path": "/tmp/run/executive_summary.md",
        "pr_url": "not created",
        "base_branch": "main",
        "branch": "feature/test",
        "feature_path": "myfeature",
        "project": "myproject",
    }

    result = render_prompt("executive_summary", "default", summary_vars, str(tmp_path), "myproject")

    assert "## Product readiness" in result
    assert "Verified internals" in result
    assert "Product usability" in result
    assert "Primary workflow evidence" in result
    assert "Skipped / warned verification" in result
    assert "Unresolved blockers" in result
    # The no-overclaim rule must call out product usability as distinct from
    # tests passing.
    assert "V1 ready" in result or "Internal tests passing is not the same" in result


def test_qa_prompt_requires_primary_workflow_evidence(tmp_path):
    qa_vars = {
        **VARS,
        "branch": "feature/test",
        "base_branch": "main",
        "slice_files": ["/tmp/run/decomposition/slice-1.md"],
        "context_path": "/tmp/run/specification/context.md",
    }

    result = render_prompt("qa", "default", qa_vars, str(tmp_path), "myproject")

    assert "## Primary user workflow evidence" in result
    # Pure unit coverage without primary-workflow evidence is blocking for
    # user-facing projects.
    assert "blocking" in result
    assert "primary user workflow" in result.lower()
    # Three evidence tiers must be enumerated so QA does not have to invent
    # a policy each time.
    assert "integration or component-level test" in result
    assert "documented manual repro" in result


def test_qa_prompt_treats_placeholder_adapters_as_blocking(tmp_path):
    qa_vars = {
        **VARS,
        "branch": "feature/test",
        "base_branch": "main",
        "slice_files": ["/tmp/run/decomposition/slice-1.md"],
        "context_path": "/tmp/run/specification/context.md",
    }

    result = render_prompt("qa", "default", qa_vars, str(tmp_path), "myproject")

    assert "## Placeholder runtime adapters" in result
    # The concrete idioms that motivated the rule (issue #200) must be named.
    assert "Promise.resolve(false)" in result
    assert "TODO: wire real adapter" in result or "TODO: implement" in result
    assert 'throw new Error("not implemented")' in result


def test_qa_prompt_has_readme_deliverable_check(tmp_path):
    qa_vars = {
        **VARS,
        "branch": "feature/test",
        "base_branch": "main",
        "slice_files": ["/tmp/run/decomposition/slice-1.md"],
        "context_path": "/tmp/run/specification/context.md",
    }

    result = render_prompt("qa", "default", qa_vars, str(tmp_path), "myproject")

    assert "## README deliverable check" in result
    for needle in (
        "what the app does",
        "setup instructions",
        "run instructions",
        "test instructions",
        "known limitations",
    ):
        assert needle in result, f"missing README requirement wording: {needle!r}"


def test_implementation_review_blocks_on_placeholder_adapters_and_lockfile_mismatch(tmp_path):
    review_vars = {
        **VARS,
        "review_md": "/tmp/run/review/review-log.md",
        "diff": "/tmp/run/review/diff-round-1.patch",
        "round": "1",
        "context_path": "/tmp/run/specification/context.md",
    }

    result = render_prompt("review", "implementation", review_vars, str(tmp_path), "myproject")

    # Lockfile / declared-dependency mismatch must be a named blocking case so
    # reviewers cross-check package.json and the lockfile when the verifier
    # also flags it.
    assert "lockfile" in result.lower()
    assert "clean install" in result.lower() or "clean-install" in result.lower()
    # Placeholder adapters on the primary user path are blocking.
    assert "Placeholder runtime adapters" in result
    assert "primary user path" in result
    # README deliverable rule for generated applications.
    assert "README deliverable" in result


def test_fix_implementation_requires_tests_and_summary(tmp_path):
    fix_vars = {
        **VARS,
        "branch": "feature/test",
        "changes_brief": "- implementation: dummy finding\n",
    }

    result = render_prompt("fix-implementation", "default", fix_vars, str(tmp_path), "myproject")

    # Every bug fix gets a regression test that would have caught the bug.
    assert "fail before the fix and pass after" in result
    # Fix cycles must rerun the test suite before signalling.
    assert "rerun the project's tests" in result
    # The agent must summarise which blocking finding each commit addressed so
    # the next reviewer can audit the fix loop quickly.
    assert "blocking finding" in result
    assert "commit hash" in result


@pytest.mark.parametrize("template", ["minimal", "default"])
def test_specification_prompts_guard_against_scope_expansion(tmp_path, template):
    if template == "minimal":
        spec_vars = {
            **VARS,
            "feature_path": "myfeature",
            "project_context_path": "/tmp/docs/projects/myproject/project-context.md",
            "run_glossary_path": "",
            "canonical_glossary_path": "",
        }
    else:
        spec_vars = {
            **VARS,
            "alignment_log": "/tmp/run/alignment/alignment-log.md",
            "project_context_path": "/tmp/docs/projects/myproject/project-context.md",
            "run_glossary_path": "",
            "canonical_glossary_path": "",
        }

    result = render_prompt("specification", template, spec_vars, str(tmp_path), "myproject")

    # The PRD must stay proportional — small tasks must not become platforms.
    assert "Do not expand" in result
    assert "enterprise system" in result
    # Rubric/judging criteria from the overview are binding.
    assert "rubric" in result
    # User-facing inputs default to a graceful-error contract unless overridden.
    assert "graceful" in result
    assert "5xx" in result
