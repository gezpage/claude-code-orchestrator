import pytest
from pathlib import Path
from unittest.mock import patch

from orchestrator.plan import init_plan_md, expand_impl_nodes, expand_discovery_nodes, update_plan_md, add_fix_cycle_node


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
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md"},
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


def test_init_plan_md_review_fan_in_to_next_stage(tmp_path):
    """Reviewer sub-nodes must fan-in to the stage after review, not via review --> next."""
    run_folder = _make_run_folder(tmp_path)
    profile = {
        "stages": [
            {"stage": "implementation", "prompt": "prompts/implementation/default.md"},
            {"stage": "review", "prompts": {
                "arch": "prompts/review/arch.md",
                "security": "prompts/review/security.md",
            }},
            {"stage": "harvest", "prompt": "prompts/harvest/default.md"},
        ]
    }
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    # Fan-out from review parent to sub-nodes
    assert "review --> review_arch & review_security" in content
    # Fan-in from sub-nodes to harvest
    assert "review_arch & review_security --> harvest" in content
    # review must NOT directly connect to harvest
    assert "review --> harvest" not in content


def test_init_plan_md_start_done_nodes(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery", "specification")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert 'Start([' in content
    assert 'Done([' in content
    assert "class Start startend" in content
    assert "class Done startend" in content
    assert "Start --> discovery" in content
    assert "specification --> Done" in content


def test_init_plan_md_start_done_single_stage(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "Start --> discovery" in content
    assert "discovery --> Done" in content


def test_init_plan_md_start_done_with_review(tmp_path):
    """Done should connect from the stage after review fan-in, not from review parent."""
    run_folder = _make_run_folder(tmp_path)
    profile = {
        "stages": [
            {"stage": "implementation", "prompt": "prompts/implementation/default.md"},
            {"stage": "review", "prompts": {"arch": "prompts/review/arch.md"}},
            {"stage": "harvest", "prompt": "prompts/harvest/default.md"},
        ]
    }
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "harvest --> Done" in content
    assert "review --> Done" not in content


def test_update_plan_md_run_summary_section(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery", "specification")
    init_plan_md(run_folder, profile)
    update_plan_md(run_folder, "discovery", "passed", elapsed_secs=90, output_summary="1 finding", signal={})
    content = (run_folder / "plan.md").read_text()
    assert "## Run Summary" in content
    assert "⏱ Total elapsed:" in content
    assert "1m 30s" in content


def test_update_plan_md_run_summary_accumulates(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery", "specification")
    init_plan_md(run_folder, profile)
    update_plan_md(run_folder, "discovery", "passed", elapsed_secs=90, output_summary="x", signal={})
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=60, output_summary="y", signal={})
    content = (run_folder / "plan.md").read_text()
    # Total should be 90+60 = 150s = 2m 30s
    assert "2m 30s" in content


def test_update_plan_md_file_manifest_timestamp_column(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    # Create a file in a stage subdirectory
    stage_dir = run_folder / "discovery"
    stage_dir.mkdir()
    (stage_dir / "findings.md").write_text("findings")
    update_plan_md(run_folder, "discovery", "passed", signal={})
    content = (run_folder / "plan.md").read_text()
    assert "| Prompt | Output | Time |" in content
    assert "| Stage |" not in content
    assert "| **discovery** | | |" in content


def test_expand_discovery_nodes_rewrites_start_edge(tmp_path):
    """Start --> discovery must be rewritten to Start --> discovery_planning after expansion."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    assert "Start --> discovery" in (run_folder / "plan.md").read_text()
    expand_discovery_nodes(run_folder, [{"name": "risk"}])
    content = (run_folder / "plan.md").read_text()
    assert "Start --> discovery_planning" in content
    assert "Start --> discovery\n" not in content


def test_update_plan_md_commit_messages_in_section(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("implementation")
    init_plan_md(run_folder, profile)
    signal = {"commit_hashes": ["abc12345def67890"]}
    with patch("orchestrator.plan._fetch_commit_messages", return_value=["feat: add auth (abc12345)"]):
        update_plan_md(
            run_folder, "implementation", "passed",
            elapsed_secs=30, output_summary="1 commit",
            signal=signal, repo_root="/some/repo",
        )
    content = (run_folder / "plan.md").read_text()
    assert "`feat: add auth (abc12345)`" in content


# --- expand_discovery_nodes ---

def _discovery_profile():
    return {"stages": [
        {"stage": "discovery"},
        {"stage": "alignment", "mode": "interactive", "artifact": "alignment-log.md"},
        {"stage": "specification", "prompt": "prompts/specification/default.md"},
    ]}


def test_expand_discovery_nodes_multi_track(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    tracks = [{"name": "architecture"}, {"name": "product-requirements"}]
    result = expand_discovery_nodes(run_folder, tracks)
    content = (run_folder / "plan.md").read_text()
    assert 'discovery_planning[' in content
    assert 'disc_architecture[' in content
    assert 'disc_product_requirements[' in content
    assert 'disc_fanout' in content
    assert 'disc_fanin' in content
    assert 'discovery_planning --> disc_fanout' in content
    assert 'disc_fanout --> disc_architecture & disc_product_requirements' in content
    assert 'disc_architecture & disc_product_requirements --> disc_fanin' in content
    assert 'disc_fanin --> alignment' in content
    assert 'class discovery_planning complete' in content
    assert 'class disc_architecture pending' in content
    assert 'class disc_product_requirements pending' in content
    assert 'class disc_fanout fannode' in content
    assert 'class disc_fanin fannode' in content
    assert '    discovery["' not in content


def test_expand_discovery_nodes_single_track(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    tracks = [{"name": "risk"}]
    expand_discovery_nodes(run_folder, tracks)
    content = (run_folder / "plan.md").read_text()
    assert 'discovery_planning[' in content
    assert 'disc_risk[' in content
    assert 'disc_fanout' not in content
    assert 'disc_fanin' not in content
    assert 'discovery_planning --> disc_risk --> alignment' in content
    assert 'class discovery_planning complete' in content
    assert 'class disc_risk pending' in content


def test_expand_discovery_nodes_noop_when_no_tracks(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    original = (run_folder / "plan.md").read_text()
    result = expand_discovery_nodes(run_folder, [])
    assert (run_folder / "plan.md").read_text() == original
    assert result == {}


def test_expand_discovery_nodes_noop_when_no_plan(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    result = expand_discovery_nodes(run_folder, [{"name": "architecture"}])
    assert not (run_folder / "plan.md").exists()
    assert result == {}


def test_expand_discovery_nodes_returns_node_id_map(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    tracks = [{"name": "product-requirements"}, {"name": "code-entry-points"}]
    result = expand_discovery_nodes(run_folder, tracks)
    assert result == {
        "product-requirements": "disc_product_requirements",
        "code-entry-points": "disc_code_entry_points",
    }


def test_expand_discovery_nodes_planning_elapsed_in_label(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    expand_discovery_nodes(run_folder, [{"name": "architecture"}], planning_elapsed_secs=90)
    content = (run_folder / "plan.md").read_text()
    assert "1m 30s" in content


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
    assert '    implementation["' not in content
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
    # output_summary appears in the markdown stage section (not the diagram node), so signal must be provided
    update_plan_md(run_folder, "discovery", "passed", elapsed_secs=90, output_summary="3 files", signal={})
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


# --- add_fix_cycle_node ---

def _profile_with_review():
    return {
        "stages": [
            {"stage": "implementation", "prompt": "prompts/implementation/default.md"},
            {"stage": "review", "prompts": {
                "tests": "prompts/review/tests.md",
            }},
            {"stage": "harvest", "prompt": "prompts/harvest/default.md"},
        ]
    }


def test_add_fix_cycle_node_cycle1_single_reviewer(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    content = (run_folder / "plan.md").read_text()
    assert 'fix_impl_1[' in content
    assert 'review_tests_2[' in content
    assert 'review_tests --> fix_impl_1' in content
    assert 'fix_impl_1 --> review_tests_2' in content
    assert 'class fix_impl_1 active' in content
    assert 'class review_tests_2 pending' in content


def test_add_fix_cycle_node_cycle2_sources_previous_round(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    add_fix_cycle_node(run_folder, cycle_num=2, reviewers=["tests"])
    content = (run_folder / "plan.md").read_text()
    assert 'fix_impl_2[' in content
    assert 'review_tests_3[' in content
    # cycle 2 source must be the round-2 re-review node, not the original
    assert 'review_tests_2 --> fix_impl_2' in content
    assert 'fix_impl_2 --> review_tests_3' in content


def test_add_fix_cycle_node_multiple_reviewers(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = {
        "stages": [
            {"stage": "review", "prompts": {
                "architecture": "prompts/review/architecture.md",
                "tests": "prompts/review/tests.md",
            }},
        ]
    }
    init_plan_md(run_folder, profile)
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["architecture", "tests"])
    content = (run_folder / "plan.md").read_text()
    assert 'fix_impl_1[' in content
    assert 'review_architecture_2[' in content
    assert 'review_tests_2[' in content
    # Both sources fan into fix_impl_1
    assert 'review_architecture & review_tests --> fix_impl_1' in content
    # fix_impl_1 fans out to both re-review nodes
    assert 'fix_impl_1 --> review_architecture_2 & review_tests_2' in content


def test_add_fix_cycle_node_noop_when_no_plan(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    # No plan.md created — should silently do nothing
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    assert not (run_folder / "plan.md").exists()


def test_add_fix_cycle_node_noop_when_no_reviewers(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    original = (run_folder / "plan.md").read_text()
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=[])
    assert (run_folder / "plan.md").read_text() == original
