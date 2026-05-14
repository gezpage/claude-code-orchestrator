from unittest.mock import patch

from orchestrator.plan import add_fix_cycle_node, expand_nodes, init_plan_md, update_plan_md
from orchestrator.profile import ExpansionKind, Profile, StageConfig


def _make_run_folder(tmp_path):
    run_folder = tmp_path / "2026-05-09-run-1"
    run_folder.mkdir()
    return run_folder


def _simple_profile(*stage_names) -> Profile:
    return Profile(
        name="test",
        stages=tuple(StageConfig(name=s, prompt=f"prompts/{s}/default.md") for s in stage_names),
    )


def _discovery_stage() -> StageConfig:
    return StageConfig(name="discovery", expansion=ExpansionKind.TRACKS)


def _impl_stage() -> StageConfig:
    return StageConfig(name="implementation", expansion=ExpansionKind.SLICES, slices_from_stage="decomposition")


# --- init_plan_md ---


def test_init_plan_md_creates_file(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery", "specification")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "flowchart TD" in content
    assert "discovery[" in content
    assert "specification[" in content
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
    profile = Profile(
        name="test",
        stages=(
            StageConfig(name="specification", prompt="prompts/specification/default.md"),
            StageConfig(name="alignment", mode="interactive", artifact="alignment-log.md"),
            StageConfig(name="decomposition", prompt="prompts/decomposition/default.md"),
        ),
    )
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    # Hex shape is preserved; the label now also carries a Mode line.
    assert 'alignment{{"✋ Alignment' in content
    assert "Mode: interactive" in content
    assert "class alignment gate" in content


def test_init_plan_md_review_stage_with_prompts(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(
            StageConfig(name="implementation", prompt="prompts/implementation/default.md"),
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={
                    "arch": "prompts/review/arch.md",
                    "security": "prompts/review/security.md",
                },
            ),
        ),
    )
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "review_arch[" in content
    assert "review_security[" in content
    assert "review --> review_arch & review_security" in content


def test_init_plan_md_review_fan_in_to_next_stage(tmp_path):
    """Reviewer sub-nodes must fan-in to the stage after review, not via review --> next."""
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(
            StageConfig(name="implementation", prompt="prompts/implementation/default.md"),
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={
                    "arch": "prompts/review/arch.md",
                    "security": "prompts/review/security.md",
                },
            ),
            StageConfig(name="harvest", prompt="prompts/harvest/default.md"),
        ),
    )
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "review --> review_arch & review_security" in content
    assert "review_arch & review_security --> harvest" in content
    assert "review --> harvest" not in content


def test_init_plan_md_start_done_nodes(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery", "specification")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "Start([" in content
    assert "Done([" in content
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
    profile = Profile(
        name="test",
        stages=(
            StageConfig(name="implementation", prompt="prompts/implementation/default.md"),
            StageConfig(name="review", expansion=ExpansionKind.PROMPTS, prompts={"arch": "prompts/review/arch.md"}),
            StageConfig(name="harvest", prompt="prompts/harvest/default.md"),
        ),
    )
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
    assert "2m 30s" in content


def test_update_plan_md_file_manifest_timestamp_column(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    stage_dir = run_folder / "discovery"
    stage_dir.mkdir()
    (stage_dir / "findings.md").write_text("findings")
    update_plan_md(run_folder, "discovery", "passed", signal={})
    content = (run_folder / "plan.md").read_text()
    assert "| Prompt | Output | Time |" in content
    assert "| Stage |" not in content
    assert "| **discovery** | | |" in content


def test_expand_nodes_tracks_rewrites_start_edge(tmp_path):
    """Start --> discovery must be rewritten to Start --> discovery_planning after expansion."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    assert "Start --> discovery" in (run_folder / "plan.md").read_text()
    expand_nodes(run_folder, _discovery_stage(), tracks=[{"name": "risk"}])
    content = (run_folder / "plan.md").read_text()
    assert "Start --> discovery_planning" in content
    assert "Start --> discovery\n" not in content


def test_update_plan_md_commit_messages_in_section(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("implementation")
    init_plan_md(run_folder, profile)
    signal = {"commit_hashes": ["abc12345def67890"]}
    with patch("orchestrator.plan._update._fetch_commit_messages", return_value=["feat: add auth (abc12345)"]):
        update_plan_md(
            run_folder,
            "implementation",
            "passed",
            elapsed_secs=30,
            output_summary="1 commit",
            signal=signal,
            repo_root="/some/repo",
        )
    content = (run_folder / "plan.md").read_text()
    assert "`feat: add auth (abc12345)`" in content


# --- expand_nodes (TRACKS) ---


def _discovery_profile() -> Profile:
    return Profile(
        name="test",
        stages=(
            StageConfig(name="discovery", expansion=ExpansionKind.TRACKS),
            StageConfig(name="alignment", mode="interactive", artifact="alignment-log.md"),
            StageConfig(name="specification", prompt="prompts/specification/default.md"),
        ),
    )


def test_expand_nodes_tracks_multi_track(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    tracks = [{"name": "architecture"}, {"name": "product-requirements"}]
    result = expand_nodes(run_folder, _discovery_stage(), tracks=tracks)
    content = (run_folder / "plan.md").read_text()
    assert "discovery_planning[" in content
    assert "discovery_architecture[" in content
    assert "discovery_product_requirements[" in content
    assert "discovery_fanout" in content
    assert "discovery_fanin" in content
    assert "discovery_planning --> discovery_fanout" in content
    assert "discovery_fanout --> discovery_architecture & discovery_product_requirements" in content
    assert "discovery_architecture & discovery_product_requirements --> discovery_fanin" in content
    assert "discovery_fanin --> alignment" in content
    assert "class discovery_planning complete" in content
    assert "class discovery_architecture pending" in content
    assert "class discovery_product_requirements pending" in content
    assert "class discovery_fanout fannode" in content
    assert "class discovery_fanin fannode" in content
    assert '    discovery["' not in content


def test_expand_nodes_tracks_single_track(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    tracks = [{"name": "risk"}]
    expand_nodes(run_folder, _discovery_stage(), tracks=tracks)
    content = (run_folder / "plan.md").read_text()
    assert "discovery_planning[" in content
    assert "discovery_risk[" in content
    assert "discovery_fanout" not in content
    assert "discovery_fanin" not in content
    assert "discovery_planning --> discovery_risk --> alignment" in content
    assert "class discovery_planning complete" in content
    assert "class discovery_risk pending" in content


def test_expand_nodes_tracks_noop_when_no_tracks(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    original = (run_folder / "plan.md").read_text()
    result = expand_nodes(run_folder, _discovery_stage(), tracks=[])
    assert (run_folder / "plan.md").read_text() == original
    assert result == {}


def test_expand_nodes_tracks_noop_when_no_plan(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    result = expand_nodes(run_folder, _discovery_stage(), tracks=[{"name": "architecture"}])
    assert not (run_folder / "plan.md").exists()
    assert result == {}


def test_expand_nodes_tracks_returns_node_id_map(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    tracks = [{"name": "product-requirements"}, {"name": "code-entry-points"}]
    result = expand_nodes(run_folder, _discovery_stage(), tracks=tracks)
    assert result == {
        "product-requirements": "discovery_product_requirements",
        "code-entry-points": "discovery_code_entry_points",
    }


def test_expand_nodes_tracks_planning_elapsed_in_label(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    expand_nodes(run_folder, _discovery_stage(), tracks=[{"name": "architecture"}], planning_elapsed_secs=90)
    content = (run_folder / "plan.md").read_text()
    assert "1m 30s" in content


# --- expand_nodes (SLICES) ---


def test_expand_nodes_slices_replaces_node_and_chain(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("decomposition", "implementation", "qa")
    init_plan_md(run_folder, profile)
    expand_nodes(run_folder, _impl_stage(), slice_files=["slice-1.md", "slice-2.md", "slice-3.md"])
    content = (run_folder / "plan.md").read_text()
    assert "impl_1[" in content
    assert "impl_2[" in content
    assert "impl_3[" in content
    assert '    implementation["' not in content
    assert "impl_1 --> impl_2 --> impl_3" in content
    assert "class impl_1 pending" in content
    assert "class implementation" not in content


def test_expand_nodes_slices_noop_when_no_slices(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("decomposition", "implementation")
    init_plan_md(run_folder, profile)
    original = (run_folder / "plan.md").read_text()
    expand_nodes(run_folder, _impl_stage(), slice_files=[])
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


def _profile_with_review() -> Profile:
    return Profile(
        name="test",
        stages=(
            StageConfig(name="implementation", prompt="prompts/implementation/default.md"),
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={
                    "tests": "prompts/review/tests.md",
                },
            ),
            StageConfig(name="harvest", prompt="prompts/harvest/default.md"),
        ),
    )


def test_add_fix_cycle_node_cycle1_single_reviewer(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    content = (run_folder / "plan.md").read_text()
    assert "fix_impl_1[" in content
    assert "review_tests_2[" in content
    assert "review_tests --> fix_impl_1" in content
    assert "fix_impl_1 --> review_tests_2" in content
    assert "class fix_impl_1 active" in content
    assert "class review_tests_2 pending" in content


def test_add_fix_cycle_node_cycle2_sources_previous_round(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    add_fix_cycle_node(run_folder, cycle_num=2, reviewers=["tests"])
    content = (run_folder / "plan.md").read_text()
    assert "fix_impl_2[" in content
    assert "review_tests_3[" in content
    assert "review_tests_2 --> fix_impl_2" in content
    assert "fix_impl_2 --> review_tests_3" in content


def test_add_fix_cycle_node_multiple_reviewers(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={
                    "architecture": "prompts/review/architecture.md",
                    "tests": "prompts/review/tests.md",
                },
            ),
        ),
    )
    init_plan_md(run_folder, profile)
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["architecture", "tests"])
    content = (run_folder / "plan.md").read_text()
    assert "fix_impl_1[" in content
    assert "review_architecture_2[" in content
    assert "review_tests_2[" in content
    assert "review_architecture & review_tests --> fix_impl_1" in content
    assert "fix_impl_1 --> review_architecture_2 & review_tests_2" in content


def test_add_fix_cycle_node_redirects_failing_reviewer_away_from_downstream(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={
                    "architecture": "prompts/review/architecture.md",
                    "implementation": "prompts/review/implementation.md",
                    "tests": "prompts/review/tests.md",
                },
            ),
            StageConfig(name="harvest", prompt="prompts/harvest/default.md"),
        ),
    )
    init_plan_md(run_folder, profile)
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["architecture"])
    content = (run_folder / "plan.md").read_text()
    assert "review_implementation & review_tests --> harvest" in content
    assert "review_architecture & review_implementation & review_tests --> harvest" not in content
    assert "review_architecture --> fix_impl_1" in content
    assert "review_architecture_2 --> harvest" in content


def test_add_fix_cycle_node_removes_edge_when_all_reviewers_fail(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={
                    "architecture": "prompts/review/architecture.md",
                    "tests": "prompts/review/tests.md",
                },
            ),
            StageConfig(name="harvest", prompt="prompts/harvest/default.md"),
        ),
    )
    init_plan_md(run_folder, profile)
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["architecture", "tests"])
    content = (run_folder / "plan.md").read_text()
    assert "review_architecture & review_tests --> harvest" not in content
    assert "review_architecture_2 & review_tests_2 --> harvest" in content


def test_add_fix_cycle_node_cycle2_re_redirects_through_new_fix(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    add_fix_cycle_node(run_folder, cycle_num=2, reviewers=["tests"])
    content = (run_folder / "plan.md").read_text()
    assert "review_tests_2 --> harvest" not in content
    assert "review_tests_2 --> fix_impl_2" in content
    assert "review_tests_3 --> harvest" in content


def test_add_fix_cycle_node_noop_when_no_plan(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    assert not (run_folder / "plan.md").exists()


def test_add_fix_cycle_node_noop_when_no_reviewers(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    original = (run_folder / "plan.md").read_text()
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=[])
    assert (run_folder / "plan.md").read_text() == original


# --- node label: Mode line, file links, legend ---


def test_node_label_shows_mode_line(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(
            StageConfig(name="specification", prompt="prompts/specification/default.md"),
            StageConfig(name="alignment", mode="interactive", artifact="alignment-log.md"),
        ),
    )
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "Mode: auto" in content
    assert "Mode: interactive" in content


def test_render_inlines_file_links_per_node(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    spec_dir = run_folder / "specification"
    spec_dir.mkdir()
    (spec_dir / "specification-prompt.md").write_text("p")
    (spec_dir / "specification-output.md").write_text("o")
    (spec_dir / "prd.md").write_text("prd")
    # Trigger a re-render through the public API.
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    content = (run_folder / "plan.md").read_text()
    style = "color:inherit;text-decoration:underline"
    assert f"<a href='specification/specification-prompt.md' style='{style}'>Prompt</a>" in content
    assert f"<a href='specification/specification-output.md' style='{style}'>Output</a>" in content
    assert f"<a href='specification/prd.md' style='{style}'>prd</a>" in content


def test_render_legend_floats_after_main_flow(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    (run_folder / "run.log").write_text("log")
    (run_folder / "stray.txt").write_text("stray")
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    content = (run_folder / "plan.md").read_text()
    # No more "Legend" subgraph wrapper — the bare node sits in the diagram body.
    assert 'subgraph sg_legend["Legend"]' not in content
    assert "Other files<br/>" in content
    # Extensions are stripped from the link display.
    assert "<a href='run.log'" in content
    assert ">run</a>" in content
    assert "<a href='stray.txt'" in content
    assert ">stray</a>" in content
    # Legend hangs off Done's predecessor so mermaid lays it out as a sibling of Done
    # rather than floating it above the flow.
    assert "specification ~~~ legend_files" in content


def test_render_reviewer_subnode_links_to_per_reviewer_files(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    review_dir = run_folder / "review"
    review_dir.mkdir()
    (review_dir / "review-tests-prompt.md").write_text("p")
    (review_dir / "review-tests-output.md").write_text("o")
    update_plan_md(run_folder, "review_tests", "passed", elapsed_secs=5)
    content = (run_folder / "plan.md").read_text()
    style = "color:inherit;text-decoration:underline"
    assert f"<a href='review/review-tests-prompt.md' style='{style}'>Prompt</a>" in content
    assert f"<a href='review/review-tests-output.md' style='{style}'>Output</a>" in content


def test_render_slice_node_links_to_implementation_files(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("decomposition", "implementation")
    init_plan_md(run_folder, profile)
    expand_nodes(run_folder, _impl_stage(), slice_files=["slice-1.md", "slice-2.md"])
    impl_dir = run_folder / "implementation"
    impl_dir.mkdir()
    (impl_dir / "implementation-impl_1-prompt.md").write_text("p")
    (impl_dir / "implementation-impl_1-output.md").write_text("o")
    update_plan_md(run_folder, "impl_1", "passed", elapsed_secs=5)
    content = (run_folder / "plan.md").read_text()
    style = "color:inherit;text-decoration:underline"
    assert f"<a href='implementation/implementation-impl_1-prompt.md' style='{style}'>Prompt</a>" in content
    assert f"<a href='implementation/implementation-impl_1-output.md' style='{style}'>Output</a>" in content


def test_render_link_hrefs_use_docs_root_prefix(tmp_path):
    # When the run folder lives under a ``projects/`` segment, link hrefs should be
    # prefixed with the full path from that anchor so mermaid SVG anchors resolve
    # correctly regardless of the page URL.
    docs_root = tmp_path / "team-hub"
    run_folder = docs_root / "projects" / "demo-project" / "workflow" / "runs" / "feature-x" / "2026-05-14-run-1"
    run_folder.mkdir(parents=True)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    spec = run_folder / "specification"
    spec.mkdir()
    (spec / "specification-prompt.md").write_text("p")
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=5)
    content = (run_folder / "plan.md").read_text()
    expected_prefix = "projects/demo-project/workflow/runs/feature-x/2026-05-14-run-1/"
    assert f"<a href='{expected_prefix}specification/specification-prompt.md'" in content
