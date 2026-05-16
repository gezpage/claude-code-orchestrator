"""Pipeline-level wiring for the optional codebase-backed domain glossary.

These tests focus on the orchestration glue: `_build_variables` injects glossary
path variables, `run_pipeline` materialises the run-local copy when configured,
and the post-harvest reconciliation hook appends new terms without overwriting
existing ones. The glossary helper module is unit-tested separately in
`test_glossary.py`.
"""

from unittest.mock import patch

import pytest
import yaml

from orchestrator import orchestrate
from orchestrator._git_setup import GitPreflightResult, OriginInfo
from orchestrator.orchestrate import _build_variables


@pytest.fixture(autouse=True)
def _stub_preflight_and_sync():
    """Mirror the autouse fixture from test_orchestrate.py so each integration
    test does not need to know about ADR-019 preflight discovery."""
    with (
        patch(
            "orchestrator.orchestrate._git_setup.preflight",
            return_value=GitPreflightResult(
                base_branch="main",
                create_pr=False,
                origin=OriginInfo(url=None, is_github=False, gh_repo=None),
            ),
        ),
        patch("orchestrator.orchestrate._sync_base_and_create_impl_branch"),
    ):
        yield


def _setup_docs(tmp_path, stages, repo_root, project_config_extra=""):
    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    cfg = f"repo-root: {repo_root}\nlog_level: DEBUG\n"
    if project_config_extra:
        cfg += project_config_extra
    (project_dir / "project.yaml").write_text(cfg)
    profile_path = tmp_path / "test.yaml"
    profile_path.write_text(yaml.dump({"name": "test", "stages": stages}))
    feature_dir = tmp_path / "feature"
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "overview.md").write_text("# Feature Overview\n")
    return str(tmp_path)


# ── _build_variables: glossary path variables ────────────────────────────────


def test_build_variables_glossary_paths_empty_when_not_configured(tmp_path):
    project_config = {"repo-root": str(tmp_path)}
    vars_dict = _build_variables(
        "specification",
        signals={},
        branch="feat/test",
        base_branch="main",
        feature_path="feature",
        docs_root=str(tmp_path),
        project="myproject",
        run_folder=tmp_path / "run",
        project_config=project_config,
    )
    assert vars_dict["canonical_glossary_path"] == ""
    assert vars_dict["run_glossary_path"] == ""


def test_build_variables_glossary_paths_populated_when_canonical_exists(tmp_path):
    repo = tmp_path / "repo"
    canon = repo / "docs" / "glossary.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Domain language\n\n## A\n\none\n")
    run_folder = tmp_path / "run"

    project_config = {
        "repo-root": str(repo),
        "domain_language": {"path": "docs/glossary.md"},
    }

    vars_dict = _build_variables(
        "specification",
        signals={},
        branch="feat/test",
        base_branch="main",
        feature_path="feature",
        docs_root=str(tmp_path),
        project="myproject",
        run_folder=run_folder,
        project_config=project_config,
    )
    assert vars_dict["canonical_glossary_path"] == str(canon)
    assert vars_dict["run_glossary_path"] == str(run_folder / "specification" / "glossary.md")


def test_build_variables_canonical_empty_when_configured_but_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_folder = tmp_path / "run"

    project_config = {
        "repo-root": str(repo),
        "domain_language": {"path": "docs/glossary.md"},
    }

    vars_dict = _build_variables(
        "specification",
        signals={},
        branch="feat/test",
        base_branch="main",
        feature_path="feature",
        docs_root=str(tmp_path),
        project="myproject",
        run_folder=run_folder,
        project_config=project_config,
    )
    # run-local path still surfaced so prompts can branch on a single variable
    assert vars_dict["canonical_glossary_path"] == ""
    assert vars_dict["run_glossary_path"] == str(run_folder / "specification" / "glossary.md")


# ── run_pipeline: run-local glossary materialisation ─────────────────────────


def _git_ok():
    from unittest.mock import MagicMock

    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    r.stdout = "diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
    return r


HARVEST_PASS = {
    "stage": "harvest",
    "status": "passed",
    "kb_files": [],
    "adr_files": [],
}


def test_run_pipeline_copies_canonical_glossary_when_configured(tmp_path):
    repo = tmp_path / "repo"
    canon = repo / "docs" / "glossary.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Domain language\n\n## A\n\none\n")

    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(
        tmp_path,
        stages,
        repo_root=str(repo),
        project_config_extra="domain_language:\n  path: docs/glossary.md\n",
    )
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=HARVEST_PASS),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    run_glossary = run_folder / "specification" / "glossary.md"
    assert run_glossary.is_file()
    assert run_glossary.read_text() == canon.read_text()


def test_run_pipeline_writes_placeholder_when_canonical_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(
        tmp_path,
        stages,
        repo_root=str(repo),
        project_config_extra="domain_language:\n  path: docs/glossary.md\n",
    )
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=HARVEST_PASS),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    run_glossary = run_folder / "specification" / "glossary.md"
    assert run_glossary.is_file()
    assert "No canonical glossary" in run_glossary.read_text()


def test_run_pipeline_skips_glossary_setup_when_not_configured(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(tmp_path, stages, repo_root=str(repo))
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=HARVEST_PASS),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    # No glossary feature => no run-local copy materialised
    assert not (run_folder / "specification" / "glossary.md").exists()
    # And the canonical codebase remains untouched
    assert not (repo / "docs" / "glossary.md").exists()


# ── post-harvest reconciliation hook ─────────────────────────────────────────


def test_post_harvest_reconciliation_appends_new_term(tmp_path):
    repo = tmp_path / "repo"
    canon = repo / "docs" / "glossary.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Domain language\n\n## Existing\n\nold definition.\n")

    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(
        tmp_path,
        stages,
        repo_root=str(repo),
        project_config_extra="domain_language:\n  path: docs/glossary.md\n",
    )
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    harvest_sig = {
        "stage": "harvest",
        "status": "passed",
        "kb_files": [],
        "adr_files": [],
        "proposed_glossary_terms": {"Fresh": "brand-new concept"},
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=harvest_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    canon_text = canon.read_text()
    assert "## Existing" in canon_text
    assert "old definition." in canon_text  # preserved
    assert "## Fresh" in canon_text
    assert "brand-new concept" in canon_text

    report = run_folder / "glossary-reconciliation.md"
    assert report.is_file()
    assert "Appended" in report.read_text()


def test_post_harvest_reconciliation_records_conflict_without_overwriting(tmp_path):
    repo = tmp_path / "repo"
    canon = repo / "docs" / "glossary.md"
    canon.parent.mkdir(parents=True)
    original = "# Domain language\n\n## Existing\n\nORIGINAL meaning.\n"
    canon.write_text(original)

    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(
        tmp_path,
        stages,
        repo_root=str(repo),
        project_config_extra="domain_language:\n  path: docs/glossary.md\n",
    )
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    harvest_sig = {
        "stage": "harvest",
        "status": "passed",
        "kb_files": [],
        "adr_files": [],
        "proposed_glossary_terms": {"Existing": "DIFFERENT meaning."},
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=harvest_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    # Canonical file untouched — append-only invariant.
    assert canon.read_text() == original
    report = run_folder / "glossary-reconciliation.md"
    assert report.is_file()
    report_text = report.read_text()
    assert "Conflicts" in report_text
    assert "DIFFERENT meaning." in report_text
    assert "ORIGINAL meaning." in report_text


def test_post_harvest_no_reconciliation_when_glossary_not_configured(tmp_path):
    """Sanity: reconciliation hook is a no-op when the project hasn't opted in."""
    repo = tmp_path / "repo"
    repo.mkdir()
    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(tmp_path, stages, repo_root=str(repo))
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    harvest_sig = {
        "stage": "harvest",
        "status": "passed",
        "kb_files": [],
        "adr_files": [],
        # Proposing terms is ignored when no glossary is configured.
        "proposed_glossary_terms": {"Ghost": "would-be definition"},
    }

    with (
        patch("orchestrator.orchestrate.run_stage", return_value=harvest_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert not (run_folder / "glossary-reconciliation.md").exists()


def test_post_harvest_skips_when_proposed_terms_empty(tmp_path):
    repo = tmp_path / "repo"
    canon = repo / "docs" / "glossary.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Domain language\n\n## A\n\nbody\n")
    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(
        tmp_path,
        stages,
        repo_root=str(repo),
        project_config_extra="domain_language:\n  path: docs/glossary.md\n",
    )
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    harvest_sig = {
        "stage": "harvest",
        "status": "passed",
        "kb_files": [],
        "adr_files": [],
    }

    before = canon.read_text()
    with (
        patch("orchestrator.orchestrate.run_stage", return_value=harvest_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert canon.read_text() == before
    # The reconciliation helper is skipped entirely when no proposals are made,
    # so no report is produced.
    assert not (run_folder / "glossary-reconciliation.md").exists()


# ── safety: existing term identical proposal does not rewrite file ───────────


def test_post_harvest_unchanged_term_does_not_rewrite_canonical(tmp_path):
    repo = tmp_path / "repo"
    canon = repo / "docs" / "glossary.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Domain language\n\n## Same\n\nidentical body\n")
    stages = [{"stage": "harvest", "prompt": "prompts/harvest/default.md"}]
    docs_root = _setup_docs(
        tmp_path,
        stages,
        repo_root=str(repo),
        project_config_extra="domain_language:\n  path: docs/glossary.md\n",
    )
    run_folder = tmp_path / "projects" / "myproject" / "workflow" / "runs" / "feature" / "2026-05-16-run-1"
    run_folder.mkdir(parents=True)

    harvest_sig = {
        "stage": "harvest",
        "status": "passed",
        "kb_files": [],
        "adr_files": [],
        "proposed_glossary_terms": {"Same": "identical body"},
    }

    before = canon.read_text()
    with (
        patch("orchestrator.orchestrate.run_stage", return_value=harvest_sig),
        patch("orchestrator.orchestrate.update_plan_md"),
        patch("orchestrator.orchestrate.subprocess.run", return_value=_git_ok()),
        patch("orchestrator.orchestrate._resolve_run_folder", return_value=run_folder),
    ):
        orchestrate.run_pipeline(docs_root, "myproject", "feature", "feat/test", str(tmp_path / "test.yaml"))

    assert canon.read_text() == before  # no duplication, no reformatting
    report = run_folder / "glossary-reconciliation.md"
    assert report.is_file()
    assert "Unchanged" in report.read_text()


# ── renderer wiring: prompts compile with glossary variables ──────────────────


def test_specification_prompt_renders_with_glossary_block(tmp_path):
    """Specification template references run_glossary_path/canonical_glossary_path
    inside `{% if %}` blocks. Smoke test: renders without StrictUndefined errors
    both with and without the glossary configured."""
    from orchestrator.renderer import render_prompt

    base = {
        "run_folder": "/tmp/run",
        "docs_root": str(tmp_path),
        "repo_root": "/tmp/repo",
        "feature_path": "feature",
        "alignment_log": "/tmp/run/alignment/alignment-log.md",
        "project_context_path": "/tmp/docs/projects/myproject/context.md",
        "canonical_glossary_path": "",
        "run_glossary_path": "",
    }
    # Off: no glossary
    out_off = render_prompt("specification", "default", base, str(tmp_path), "myproject")
    assert "Domain-language glossary" not in out_off

    on = {
        **base,
        "canonical_glossary_path": "/tmp/repo/docs/glossary.md",
        "run_glossary_path": "/tmp/run/specification/glossary.md",
    }
    out_on = render_prompt("specification", "default", on, str(tmp_path), "myproject")
    assert "Domain-language glossary" in out_on
    assert "/tmp/run/specification/glossary.md" in out_on
    assert "Candidate glossary terms" in out_on


def test_harvest_prompt_renders_with_glossary_block(tmp_path):
    from orchestrator.renderer import render_prompt

    base = {
        "run_folder": "/tmp/run",
        "docs_root": str(tmp_path),
        "repo_root": "/tmp/repo",
        "review_md": "/tmp/run/review/review-log.md",
        "context_path": "/tmp/run/specification/context.md",
        "project_context_path": "/tmp/docs/projects/myproject/context.md",
        "canonical_glossary_path": "",
        "run_glossary_path": "",
    }
    out_off = render_prompt("harvest", "default", base, str(tmp_path), "myproject")
    assert "proposed_glossary_terms" not in out_off

    on = {
        **base,
        "canonical_glossary_path": "/tmp/repo/docs/glossary.md",
        "run_glossary_path": "/tmp/run/specification/glossary.md",
    }
    out_on = render_prompt("harvest", "default", on, str(tmp_path), "myproject")
    assert "proposed_glossary_terms" in out_on
    assert "append-only" in out_on
