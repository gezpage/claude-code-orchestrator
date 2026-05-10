import pytest
from pathlib import Path

from orchestrator.plan import init_plan_md, expand_impl_nodes, update_plan_md


def _make_run_folder(tmp_path):
    run_folder = tmp_path / "2026-05-09-run-1"
    run_folder.mkdir()
    return run_folder


def _simple_profile(*stage_names):
    return {"stages": [{"stage": s, "prompt": f"prompts/{s}/default.md"} for s in stage_names]}


# --- init_plan_md ---

def test_init_plan_md_creates_file(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery", "specification")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "flowchart TD" in content
    assert 'discovery[' in content
    assert 'specification[' in content
    assert "classDef pending" in content


def test_init_plan_md_idempotent(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    original = (run_folder / "plan.md").read_text()
    init_plan_md(run_folder, profile)
    assert (run_folder / "plan.md").read_text() == original


def test_init_plan_md_alignment_gate_shape(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = {"stages": [
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
        {"stage": "alignment"},
        {"stage": "decomposition", "prompt": "prompts/decomposition/default.md"},
    ]}
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert 'alignment{{"✋ Alignment"}}' in content
    assert "class alignment gate" in content


def test_init_plan_md_review_stage_with_prompts(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = {
        "stages": [
            {"stage": "implementation", "prompt": "prompts/implementation/default.md"},
            {"stage": "review", "prompts": {
                "arch": "prompts/review/arch.md",
                "security": "prompts/review/security.md",
            }},
        ]
    }
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert 'review_arch[' in content
    assert 'review_security[' in content
    assert "review --> review_arch & review_security" in content


# --- expand_impl_nodes ---

def test_expand_impl_nodes_replaces_node_and_chain(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("decomposition", "implementation", "qa")
    init_plan_md(run_folder, profile)
    expand_impl_nodes(run_folder, ["slice-1.md", "slice-2.md", "slice-3.md"])
    content = (run_folder / "plan.md").read_text()
    assert 'impl_1[' in content
    assert 'impl_2[' in content
    assert 'impl_3[' in content
    assert 'implementation[' not in content
    assert "impl_1 --> impl_2 --> impl_3" in content
    assert "class impl_1 pending" in content
    assert "class implementation" not in content


def test_expand_impl_nodes_noop_when_no_slices(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("decomposition", "implementation")
    init_plan_md(run_folder, profile)
    original = (run_folder / "plan.md").read_text()
    expand_impl_nodes(run_folder, [])
    assert (run_folder / "plan.md").read_text() == original


# --- update_plan_md ---

def test_update_plan_md_updates_style(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    update_plan_md(run_folder, "discovery", "passed")
    content = (run_folder / "plan.md").read_text()
    assert "class discovery complete" in content
    assert "✅" in content


def test_update_plan_md_adds_elapsed_and_summary(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    update_plan_md(run_folder, "discovery", "passed", elapsed_secs=90, output_summary="3 files")
    content = (run_folder / "plan.md").read_text()
    assert "✅" in content
    assert "1m 30s" in content
    assert "3 files" in content


def test_update_plan_md_creates_file_when_missing(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    update_plan_md(run_folder, "discovery", "in_progress")
    content = (run_folder / "plan.md").read_text()
    assert "flowchart TD" in content
    assert "class discovery active" in content
    assert "classDef active" in content
