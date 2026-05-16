from unittest.mock import patch

from orchestrator.plan import (
    add_fix_cycle_node,
    expand_nodes,
    init_plan_md,
    mark_pipeline_done,
    mark_pr_blocked,
    rerender_plan_md,
    resolve_review_subnode_statuses,
    set_pr_node,
    update_plan_md,
    worst_status,
)
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


def test_init_plan_md_start_shows_profile_name(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="full",
        stages=(StageConfig(name="discovery", prompt="prompts/discovery/default.md"),),
    )
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    # Start carries "▶ Start" inside the title span; profile name renders below
    # at plain-text size so it doesn't compete with the title.
    assert "▶ Start" in content
    assert "Profile: Full" in content
    # Subtitle is appended below the title span, not inside it.
    title_idx = content.find("▶ Start")
    profile_idx = content.find("Profile: Full")
    assert 0 < title_idx < profile_idx


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
    # Hex shape is preserved; the title is wrapped in the prominent-title span
    # used by every node, and the Mode line is appended below.
    assert "alignment{{" in content
    assert "✋ Alignment" in content
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
    # Renderer rewrites edge endpoints to the materialised prompt/panel partners.
    assert "review_panel --> review_arch_prompt & review_security_prompt" in content
    # Sub-node display is suffixed with the parent stage's display so review agents
    # read as "<Name> Review" rather than colliding with sibling stages of the same name.
    assert "Arch Review" in content
    assert "Security Review" in content


def test_init_plan_md_review_sub_node_display_suffixed_with_parent(tmp_path):
    """The 'implementation' reviewer must render as 'Implementation Review' so it is
    visually distinct from the actual Implementation stage node."""
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(
            StageConfig(name="implementation", prompt="prompts/implementation/default.md"),
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={
                    "implementation": "prompts/review/implementation.md",
                    "tests": "prompts/review/tests.md",
                },
            ),
        ),
    )
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "Implementation Review" in content
    assert "Tests Review" in content


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
    assert "review_panel --> review_arch_prompt & review_security_prompt" in content
    assert "review_arch_panel & review_security_panel --> harvest_prompt" in content
    assert "review --> harvest" not in content
    assert "review_panel --> harvest_prompt" not in content


def test_init_plan_md_start_done_nodes(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery", "specification")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "Start([" in content
    assert "Done([" in content
    assert "class Start startend" in content
    assert "class Done startend" in content
    # Start now passes through the overview input before the first stage's prompt.
    assert "Start --> overview" in content
    assert "overview --> discovery_prompt" in content
    # Done is reached from the last stage's panel, not the stage node.
    assert "specification_panel --> Done" in content


def test_init_plan_md_start_done_single_stage(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "Start --> overview" in content
    assert "overview --> discovery_prompt" in content
    assert "discovery_panel --> Done" in content


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
    assert "harvest_panel --> Done" in content
    assert "review --> Done" not in content
    assert "review_panel --> Done" not in content


def test_rerender_plan_md_picks_up_new_prompt_file(tmp_path):
    """A prompt file written between init and stage completion should appear in the
    mermaid block after rerender_plan_md, without changing any node status."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("discovery")
    init_plan_md(run_folder, profile)
    before = (run_folder / "plan.md").read_text()
    # No prompt link yet — the parallelogram should show the literal "Prompt" text.
    assert "discovery-prompt.md" not in before

    stage_dir = run_folder / "discovery"
    stage_dir.mkdir()
    (stage_dir / "discovery-prompt.md").write_text("prompt body")
    rerender_plan_md(run_folder)

    after = (run_folder / "plan.md").read_text()
    assert "discovery-prompt.md" in after


def test_rerender_plan_md_noop_when_no_plan(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    # No plan.md exists yet — rerender should silently do nothing.
    rerender_plan_md(run_folder)
    assert not (run_folder / "plan.md").exists()


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


def _run_summary_stage_rows(plan_text: str) -> list[str]:
    """Extract the stage-name column from the Run Summary table, in order."""
    lines = plan_text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("| Stage |"))
    rows: list[str] = []
    for line in lines[start + 2 :]:  # skip header and separator
        if not line.startswith("| "):
            break
        rows.append(line.split("|")[1].strip())
    return rows


def test_run_summary_preserves_chronological_completion_order(tmp_path):
    """Run summary lists stages in the order they completed, not alphabetically (#130).

    YAML's default sort_keys=True was alphabetising the elapsed map on every save,
    so the table showed e.g. ``Decomposition`` before ``Specification`` even though
    specification ran first. This regresses if state.save_state stops preserving
    insertion order."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification", "decomposition", "implementation")
    init_plan_md(run_folder, profile)
    # Save in the chronological (non-alphabetical) order they would run.
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10, signal={})
    update_plan_md(run_folder, "decomposition", "passed", elapsed_secs=20, signal={})
    update_plan_md(run_folder, "implementation", "passed", elapsed_secs=30, signal={})
    rows = _run_summary_stage_rows((run_folder / "plan.md").read_text())
    assert rows == ["Specification", "Decomposition", "Implementation"]


def test_run_summary_review_fix_cycle_chronological(tmp_path):
    """Fix-impl and re-review rows appear in execution order, not interleaved alphabetically."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    update_plan_md(run_folder, "implementation", "passed", elapsed_secs=10, signal={})
    update_plan_md(run_folder, "review_tests", "passed", elapsed_secs=5, signal={})
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    update_plan_md(run_folder, "fix_impl_1", "passed", elapsed_secs=15, signal={})
    update_plan_md(run_folder, "review_tests_2", "passed", elapsed_secs=5, signal={})
    add_fix_cycle_node(run_folder, cycle_num=2, reviewers=["tests"])
    update_plan_md(run_folder, "fix_impl_2", "passed", elapsed_secs=15, signal={})
    update_plan_md(run_folder, "review_tests_3", "passed", elapsed_secs=5, signal={})
    rows = _run_summary_stage_rows((run_folder / "plan.md").read_text())
    assert rows == [
        "Implementation",
        "Review Tests",
        "Fix Impl 1",
        "Review Tests 2",
        "Fix Impl 2",
        "Review Tests 3",
    ]


def test_run_summary_pr_draft_appears_after_final_review(tmp_path):
    """The PR stage runs after fix cycles complete and must render last."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review(), create_pr=True)
    update_plan_md(run_folder, "implementation", "passed", elapsed_secs=10, signal={})
    update_plan_md(run_folder, "review_tests", "passed", elapsed_secs=5, signal={})
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    update_plan_md(run_folder, "fix_impl_1", "passed", elapsed_secs=15, signal={})
    update_plan_md(run_folder, "review_tests_2", "passed", elapsed_secs=5, signal={})
    update_plan_md(run_folder, "harvest", "passed", elapsed_secs=2, signal={})
    update_plan_md(run_folder, "pr", "passed", elapsed_secs=3, signal={})
    rows = _run_summary_stage_rows((run_folder / "plan.md").read_text())
    assert rows[-1] == "Pr"
    assert rows.index("Pr") > rows.index("Fix Impl 1")
    assert rows.index("Pr") > rows.index("Review Tests 2")


def test_run_summary_render_is_deterministic(tmp_path):
    """Re-running update_plan_md with the same state produces an identical table."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification", "decomposition", "implementation")
    init_plan_md(run_folder, profile)
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10, signal={})
    update_plan_md(run_folder, "decomposition", "passed", elapsed_secs=20, signal={})
    first_rows = _run_summary_stage_rows((run_folder / "plan.md").read_text())
    update_plan_md(run_folder, "implementation", "passed", elapsed_secs=30, signal={})
    second_rows = _run_summary_stage_rows((run_folder / "plan.md").read_text())
    assert second_rows[: len(first_rows)] == first_rows
    assert second_rows == ["Specification", "Decomposition", "Implementation"]


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
    """The Start-anchored edge target must shift from discovery to discovery_planning after expansion."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _discovery_profile())
    # Initial layout: Start → overview → discovery_prompt
    assert "overview --> discovery_prompt" in (run_folder / "plan.md").read_text()
    expand_nodes(run_folder, _discovery_stage(), tracks=[{"name": "risk"}])
    content = (run_folder / "plan.md").read_text()
    # After expansion: the entry edge now targets the materialised planning prompt.
    assert "overview --> discovery_planning_prompt" in content
    assert "overview --> discovery_prompt" not in content


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
    expand_nodes(run_folder, _discovery_stage(), tracks=tracks)
    content = (run_folder / "plan.md").read_text()
    assert "discovery_planning[" in content
    assert "discovery_architecture[" in content
    assert "discovery_product_requirements[" in content
    assert "discovery_fanout" in content
    assert "discovery_fanin" in content
    # Fan-out/in circles have no prompt/panel partners; rect tracks do, so edges
    # touching tracks rewrite to the materialised endpoints.
    assert "discovery_planning_panel --> discovery_fanout" in content
    assert "discovery_fanout --> discovery_architecture_prompt & discovery_product_requirements_prompt" in content
    assert "discovery_architecture_panel & discovery_product_requirements_panel --> discovery_fanin" in content
    # alignment is an interactive hex gate (no prompt/panel) so the edge into it
    # keeps the bare target id.
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
    # Chain edges are emitted as one mermaid edge per consecutive pair (so a
    # middle node can serve as both source-with-panel and target-with-prompt).
    assert "discovery_planning_panel --> discovery_risk_prompt" in content
    assert "discovery_risk_panel --> alignment" in content
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
    # The slice chain is emitted as per-pair edges, with rewritten panel/prompt endpoints.
    assert "impl_1_panel --> impl_2_prompt" in content
    assert "impl_2_panel --> impl_3_prompt" in content
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
    assert "review_tests_panel --> fix_impl_1_prompt" in content
    assert "fix_impl_1_panel --> review_tests_2_prompt" in content
    assert "class fix_impl_1 active" in content
    assert "class review_tests_2 pending" in content
    # Re-review nodes carry the same parent-stage suffix as the original review sub-nodes.
    assert "Tests Review" in content


def test_add_fix_cycle_node_cycle2_sources_previous_round(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    add_fix_cycle_node(run_folder, cycle_num=2, reviewers=["tests"])
    content = (run_folder / "plan.md").read_text()
    assert "fix_impl_2[" in content
    assert "review_tests_3[" in content
    assert "review_tests_2_panel --> fix_impl_2_prompt" in content
    assert "fix_impl_2_panel --> review_tests_3_prompt" in content


def test_add_fix_cycle_node_subgraph_label_uses_cycle_num(tmp_path):
    """Subgraph display must read "Fix Cycle 1"/"Fix Cycle 2", not Round numbers (#129).

    The historical bug labelled the subgraph as ``Fix Cycle {round_num}`` where
    round_num = cycle_num + 1, producing ``Fix Cycle 3`` after only two fix runs.
    Subgraphs are not rendered to mermaid (see ADR-020), so this asserts on the
    persisted graph model in _plan_graph.yaml — that is the only place the label
    survives, and it must still be correct for future renderers and tooling."""
    from orchestrator.plan._graph import load_graph

    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    add_fix_cycle_node(run_folder, cycle_num=2, reviewers=["tests"])
    graph = load_graph(run_folder)
    assert graph is not None
    displays = {sg.display for sg in graph.subgraphs.values()}
    assert "Fix Cycle 1" in displays
    assert "Fix Cycle 2" in displays
    assert "Fix Cycle 3" not in displays


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
    assert "review_architecture_panel & review_tests_panel --> fix_impl_1_prompt" in content
    assert "fix_impl_1_panel --> review_architecture_2_prompt & review_tests_2_prompt" in content


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
    assert "review_implementation_panel & review_tests_panel --> harvest_prompt" in content
    assert (
        "review_architecture_panel & review_implementation_panel & review_tests_panel --> harvest_prompt" not in content
    )
    assert "review_architecture_panel --> fix_impl_1_prompt" in content
    assert "review_architecture_2_panel --> harvest_prompt" in content


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
    assert "review_architecture_panel & review_tests_panel --> harvest_prompt" not in content
    assert "review_architecture_2_panel & review_tests_2_panel --> harvest_prompt" in content


def test_add_fix_cycle_node_cycle2_re_redirects_through_new_fix(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    add_fix_cycle_node(run_folder, cycle_num=2, reviewers=["tests"])
    content = (run_folder / "plan.md").read_text()
    assert "review_tests_2_panel --> harvest_prompt" not in content
    assert "review_tests_2_panel --> fix_impl_2_prompt" in content
    assert "review_tests_3_panel --> harvest_prompt" in content


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
    # Prompt link lives inside the materialised prompt input parallelogram.
    assert (
        "<a href='specification/specification-prompt.md' style='color:inherit;text-decoration:underline;'>Prompt</a>"
    ) in content
    # Output link is the bold header at the top of the panel — distinct style.
    assert "<a href='specification/specification-output.md' style='font-size:16px;font-weight:bold" in content
    assert ">Output</a>" in content
    # Other stage artefacts render as pill-style buttons inside the panel.
    assert "<a href='specification/prd.md' style='display:inline-block;" in content
    assert ">prd</a>" in content


def test_other_files_buttons_rendered_outside_mermaid_fence(tmp_path):
    """Unmatched files render as button-style links in a section directly below
    the ``` mermaid fence, not as a node inside the diagram. The section is
    wrapped in comment markers so subsequent renders can refresh it cleanly."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    (run_folder / "run.log").write_text("log")
    (run_folder / "stray.txt").write_text("stray")
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    content = (run_folder / "plan.md").read_text()
    # No legend node in the diagram any more.
    assert "legend_files" not in content
    assert "Other files" not in content
    # Buttons live outside the mermaid fence.
    fence_end = content.rfind("```")
    assert "<!-- other-files-begin -->" in content
    assert content.index("<!-- other-files-begin -->") > fence_end
    assert "<a href='run.log'" in content
    assert ">run</a>" in content
    assert "<a href='stray.txt'" in content
    assert ">stray</a>" in content


def test_other_files_section_refreshes_without_growing(tmp_path):
    """Repeated renders must replace the prior other-files section in-place,
    not append a new one each time."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    (run_folder / "run.log").write_text("log")
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=11)
    content = (run_folder / "plan.md").read_text()
    assert content.count("<!-- other-files-begin -->") == 1
    assert content.count("<!-- other-files-end -->") == 1


def test_render_reviewer_subnode_links_to_per_reviewer_files(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    review_dir = run_folder / "review"
    review_dir.mkdir()
    (review_dir / "review-tests-prompt.md").write_text("p")
    (review_dir / "review-tests-output.md").write_text("o")
    update_plan_md(run_folder, "review_tests", "passed", elapsed_secs=5)
    content = (run_folder / "plan.md").read_text()
    assert (
        "<a href='review/review-tests-prompt.md' style='color:inherit;text-decoration:underline;'>Prompt</a>"
    ) in content
    assert "<a href='review/review-tests-output.md' style='font-size:16px;font-weight:bold" in content


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
    assert (
        "<a href='implementation/implementation-impl_1-prompt.md' "
        "style='color:inherit;text-decoration:underline;'>Prompt</a>"
    ) in content
    assert (
        "<a href='implementation/implementation-impl_1-output.md' style='font-size:16px;font-weight:bold"
    ) in content


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
    expected_prefix = "/#projects/demo-project/workflow/runs/feature-x/2026-05-14-run-1/"
    assert f"<a href='{expected_prefix}specification/specification-prompt.md'" in content


def test_render_link_hrefs_when_docs_root_lives_under_projects_dir(tmp_path):
    # Regression: a docs root that itself sits under a directory called ``projects``
    # (e.g. ``~/Dev/projects/docs``) used to produce hrefs anchored on the leftmost
    # ``projects`` segment, leaking the host path into the URL. The renderer must
    # anchor on the structural tail instead.
    host = tmp_path / "projects" / "docs-root"
    run_folder = host / "projects" / "demo-project" / "workflow" / "runs" / "feature-x" / "2026-05-14-run-1"
    run_folder.mkdir(parents=True)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    spec = run_folder / "specification"
    spec.mkdir()
    (spec / "specification-prompt.md").write_text("p")
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=5)
    content = (run_folder / "plan.md").read_text()
    expected_prefix = "/#projects/demo-project/workflow/runs/feature-x/2026-05-14-run-1/"
    assert f"<a href='{expected_prefix}specification/specification-prompt.md'" in content
    assert "projects/docs-root/" not in content


# --- runner / model in stage labels ---


def test_node_label_shows_runner_and_model_on_separate_lines(tmp_path):
    """Each stage's sub-line shows the resolved runner + model on separate lines
    (one fact per <br/>) instead of a single dot-separated string. The runner
    backend is rendered with its friendly name (claude / codex)."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(
        run_folder,
        profile,
        agent_metadata={"specification": {"backend": "claude_code", "model": "claude-opus-4-7"}},
    )
    content = (run_folder / "plan.md").read_text()
    assert "claude · claude-opus-4-7" in content
    # Sub-line is multi-line — runner line and Mode line both present, joined by <br/>.
    assert "claude · claude-opus-4-7<br/>Mode: auto" in content


def test_node_label_no_minimal_impl_token(tmp_path):
    """The legacy `impl` token (e.g. 'minimal' from prompt filename) is no longer
    shown in the sub-line — it duplicated information already captured by the
    runner/model fields."""
    run_folder = _make_run_folder(tmp_path)
    profile = Profile(
        name="test",
        stages=(StageConfig(name="implementation", prompt="prompts/implementation/minimal.md"),),
    )
    init_plan_md(
        run_folder,
        profile,
        agent_metadata={"implementation": {"backend": "codex_cli", "model": "gpt-5"}},
    )
    content = (run_folder / "plan.md").read_text()
    # The implementation node's sub-line should NOT contain the prompt-stem 'minimal'.
    assert "minimal · Mode" not in content
    assert "codex · gpt-5" in content


# --- panel output text ---


def test_panel_shows_prose_from_output_file(tmp_path):
    """Once a stage's output file exists with prose above the SIGNAL_JSON code
    block, the panel renders that prose in place of the prior {status: ok}
    placeholder. The fenced ```json``` signal block is stripped."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    spec = run_folder / "specification"
    spec.mkdir()
    (spec / "specification-prompt.md").write_text("p")
    (spec / "specification-output.md").write_text(
        'Drafted the PRD covering the auth refactor.\n\n```json\n{\n  "status": "passed"\n}\n```\n'
    )
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    content = (run_folder / "plan.md").read_text()
    assert "Drafted the PRD covering the auth refactor." in content
    # The placeholder JSON is gone.
    assert "&quot;status&quot;: &quot;ok&quot;" not in content


def test_stage_to_panel_visible_arrow(tmp_path):
    """The stage → panel chain edge renders as a visible arrow so the data-flow
    (stage produces the panel output) reads clearly in the diagram."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "specification --> specification_panel" in content
    assert "specification ~~~ specification_panel" not in content


def test_panel_prose_escapes_html_and_mermaid_breaking_chars(tmp_path):
    """Agent prose with awkward but realistic characters must not break the
    mermaid block. Specifically:
    - `&` becomes `&amp;` first, so subsequent entity insertions are not
      re-escaped into `&amp;lt;` etc.
    - `<` / `>` become `&lt;` / `&gt;` so HTML-looking text doesn't get parsed
      as markup, and stray `-->` in prose can't be mistaken for a mermaid edge.
    - `"` becomes `&quot;` because the panel label is itself wrapped in `"..."`.
    - Newlines become `<br/>` so multi-line prose stays on one logical label.
    - Square brackets and backticks pass through verbatim — no mermaid meaning
      inside a quoted label.
    """
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    spec = run_folder / "specification"
    spec.mkdir()
    (spec / "specification-prompt.md").write_text("p")
    gnarly = (
        "Reviewed `auth/login.ts` & flagged the `<script>` injection at line 42, "
        'plus a "double-quoted" comment.\n'
        "Diagram sketch: A --> B [optional].\n"
        '\n```json\n{"status": "passed"}\n```\n'
    )
    (spec / "specification-output.md").write_text(gnarly)
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    content = (run_folder / "plan.md").read_text()

    # Entities are escaped — diagram survives.
    assert "&amp;" in content
    assert "&lt;script&gt;" in content
    assert "&quot;double-quoted&quot;" in content
    # Newline inside the paragraph collapses to <br/>.
    assert "comment.<br/>Diagram sketch:" in content
    # A literal `-->` arrow in prose is neutralised so mermaid can't read it
    # as an edge token.
    assert "A --&gt; B [optional]." in content
    # Square brackets and backticks pass through unescaped.
    assert "`auth/login.ts`" in content
    assert "[optional]." in content
    # Raw, unescaped versions of the HTML-looking text must not appear anywhere.
    assert "<script>" not in content
    # No double-encoded entities introduced by the &-first replacement order.
    assert "&amp;amp;" not in content
    assert "&amp;lt;" not in content


def test_panel_prose_truncates_very_long_first_paragraph(tmp_path):
    """Prose longer than the panel cap is truncated with an ellipsis so the
    diagram stays readable; the full text remains reachable via the Output link."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    spec = run_folder / "specification"
    spec.mkdir()
    (spec / "specification-prompt.md").write_text("p")
    long_para = "word " * 200  # ~1000 chars
    (spec / "specification-output.md").write_text(f'{long_para}\n\n```json\n{{"status": "passed"}}\n```\n')
    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    content = (run_folder / "plan.md").read_text()
    # Ellipsis marker is present, full 1000-char paragraph is not.
    assert "…" in content
    assert long_para.strip() not in content


# --- set_pr_node ---


def test_set_pr_node_splices_box_before_done(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    set_pr_node(run_folder, "https://github.com/example/repo/pull/42")
    content = (run_folder / "plan.md").read_text()
    # PR node is a stadium with the URL as a large-text link.
    assert "pr([" in content
    assert "https://github.com/example/repo/pull/42" in content
    assert "font-size:18px" in content
    # Spliced into the edge graph: predecessor → pr → Done, with no direct → Done.
    assert "pr --> Done" in content or "pr_panel --> Done" in content
    assert "specification_panel --> Done" not in content


def test_set_pr_node_idempotent_on_refresh(tmp_path):
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    set_pr_node(run_folder, "https://github.com/example/repo/pull/42")
    set_pr_node(run_folder, "https://github.com/example/repo/pull/99")
    content = (run_folder / "plan.md").read_text()
    # Single PR node, refreshed label.
    assert content.count("pr([") == 1
    assert "/pull/99" in content
    assert "/pull/42" not in content


def test_set_pr_node_splices_after_fix_cycle_predecessor(tmp_path):
    """When the predecessor of Done is a re-review node from a fix cycle (not
    the profile's last regular stage), set_pr_node must still splice the PR
    box between *that* re-review node and Done — not between the original
    last stage and Done. Edge rewriting around fan-in / fan-out is where this
    historically gets weird."""
    run_folder = _make_run_folder(tmp_path)
    # Review is the final stage so Done's predecessor will become the re-review
    # node after the fix cycle.
    profile = Profile(
        name="test",
        stages=(
            StageConfig(name="implementation", prompt="prompts/implementation/default.md"),
            StageConfig(
                name="review",
                expansion=ExpansionKind.PROMPTS,
                prompts={"tests": "prompts/review/tests.md"},
            ),
        ),
    )
    init_plan_md(run_folder, profile)
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    set_pr_node(run_folder, "https://github.com/example/repo/pull/77")
    content = (run_folder / "plan.md").read_text()

    # PR node exists and links to the supplied URL.
    assert "pr([" in content
    assert "/pull/77" in content
    # The re-review node from the fix cycle is the predecessor of pr (not Done).
    assert "review_tests_2_panel --> pr" in content
    assert "pr --> Done" in content
    # Nothing else should still point directly at Done.
    assert "review_tests_2_panel --> Done" not in content
    assert "review_tests_panel --> Done" not in content


# --- create_pr=True at init time ---


def test_init_plan_md_renders_pr_stage_when_create_pr_true(tmp_path):
    """With create_pr=True, the PR stage node appears in the diagram alongside
    other stages (rect shape, prompt/panel partners) — spliced between the last
    stage and Done."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification", "implementation")
    init_plan_md(run_folder, profile, create_pr=True)
    content = (run_folder / "plan.md").read_text()
    # The pr node is a rect-shape stage (not stadium), with a panel partner.
    assert "pr[" in content
    assert "pr([" not in content
    assert "pr --> pr_panel" in content
    # Last stage panel flows into pr; pr_panel flows into Done.
    assert "implementation_panel --> pr" in content
    assert "pr_panel --> Done" in content
    # Direct last-stage→Done edge must not survive the splice.
    assert "implementation_panel --> Done" not in content


def test_init_plan_md_omits_pr_stage_when_create_pr_false(tmp_path):
    """With create_pr=False (default), no PR node is added to the diagram."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification", "implementation")
    init_plan_md(run_folder, profile)
    content = (run_folder / "plan.md").read_text()
    assert "pr[" not in content
    assert "pr_panel" not in content


def test_set_pr_node_updates_existing_pr_stage_panel(tmp_path):
    """When the PR stage was added at init time (create_pr=True), set_pr_node
    only stamps the URL into the existing node's panel — it does not splice a
    new stadium box."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile, create_pr=True)
    set_pr_node(run_folder, "https://github.com/example/repo/pull/42")
    content = (run_folder / "plan.md").read_text()
    assert "https://github.com/example/repo/pull/42" in content
    # No legacy stadium PR node introduced; the rect-shape stage persists.
    assert "pr([" not in content
    assert "pr[" in content
    # PR node is now in 'complete' class.
    assert "class pr complete" in content


def test_bold_link_style_appears_for_passed_stages(tmp_path):
    """Once a stage transitions to passed, the edges on its completed path
    render with a thicker stroke via a linkStyle directive."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification", "implementation")
    init_plan_md(run_folder, profile)
    # No passed stages yet → no linkStyle directive in the rendered block.
    assert "linkStyle" not in (run_folder / "plan.md").read_text()

    update_plan_md(run_folder, "specification", "passed", elapsed_secs=10)
    content = (run_folder / "plan.md").read_text()
    assert "linkStyle" in content
    assert "stroke-width:3px" in content


def test_mark_pipeline_done_flips_done_node_to_complete(tmp_path):
    """When the pipeline reaches Done successfully, mark_pipeline_done flips
    Done from the startend class into the green 'complete' class."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile)
    # Before: Done is in the startend class.
    assert "class Done startend" in (run_folder / "plan.md").read_text()

    mark_pipeline_done(run_folder)
    content = (run_folder / "plan.md").read_text()
    assert "class Done complete" in content
    assert "class Done startend" not in content


# --- terminal-status precedence (ADR-026) ---


def test_worst_status_picks_failed_over_everything():
    assert worst_status("failed", "passed", "pending") == "failed"


def test_worst_status_blocked_beats_changes_requested_in_progress_passed():
    assert worst_status("passed", "blocked", "in_progress") == "blocked"
    assert worst_status("blocked", "changes-requested") == "blocked"


def test_worst_status_changes_requested_beats_in_progress_and_below():
    assert worst_status("in_progress", "changes-requested", "passed") == "changes-requested"


def test_worst_status_in_progress_beats_passed_skipped_pending():
    assert worst_status("passed", "in_progress", "pending") == "in_progress"


def test_worst_status_passed_beats_skipped_and_pending():
    assert worst_status("pending", "passed", "skipped") == "passed"


def test_worst_status_empty_returns_pending():
    assert worst_status() == "pending"


def test_worst_status_unknown_loses_to_known():
    # Unknown statuses must not beat a recognised state — we'd rather render a
    # known result than propagate a typo into the diagram.
    assert worst_status("mystery", "passed") == "passed"


def test_resolve_review_subnode_status_flips_blocked_round1_to_passed_when_approved(tmp_path):
    """An approved final cycle re-stamps the round-1 sub-node to passed so it
    no longer renders red beside a green round-N sibling. See ADR-026."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    # Round-1 reviewer requested changes → blocked.
    update_plan_md(run_folder, "review_tests", "blocked", elapsed_secs=1.0, output_summary="changes-requested")
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    update_plan_md(run_folder, "review_tests_2", "passed", elapsed_secs=1.0, output_summary="approved")

    resolve_review_subnode_statuses(run_folder, {"tests": "approved"})
    content = (run_folder / "plan.md").read_text()
    # The round-1 sub-node and the round-2 sub-node now agree.
    assert "class review_tests complete" in content
    assert "class review_tests blocked" not in content
    assert "class review_tests_2 complete" in content


def test_resolve_review_subnode_status_keeps_blocked_when_changes_still_requested(tmp_path):
    """A reviewer that still has changes-requested at the end of cycles must
    remain blocked — the worst status wins, not the final-round value."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    update_plan_md(run_folder, "review_tests", "blocked", elapsed_secs=1.0)

    resolve_review_subnode_statuses(run_folder, {"tests": "changes-requested"})
    content = (run_folder / "plan.md").read_text()
    assert "class review_tests blocked" in content


def test_resolve_review_subnode_status_noop_when_subnode_missing(tmp_path):
    """A reviewer that never registered a round-1 sub-node (e.g. the original
    review stage produced no node) is silently ignored — the helper must not
    create nodes it doesn't already own."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    resolve_review_subnode_statuses(run_folder, {"unknown_reviewer": "approved"})
    content = (run_folder / "plan.md").read_text()
    assert "review_unknown_reviewer" not in content


def test_panel_body_passed_renders_done_not_pending(tmp_path):
    """A passed node with no output prose must show ``done``, not ``pending`` —
    the prior fallback rendered "pending" for stages that completed but didn't
    emit a *-output.md file."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    update_plan_md(run_folder, "review", "passed", elapsed_secs=1.0)
    content = (run_folder / "plan.md").read_text()
    # Isolate the review parent's panel line so other pending nodes' panels
    # don't pollute the assertion.
    review_panel_lines = [line for line in content.splitlines() if line.lstrip().startswith("review_panel[")]
    assert len(review_panel_lines) == 1
    panel = review_panel_lines[0]
    assert ">pending</div>" not in panel
    assert ">done</div>" in panel


# --- review-log fallback for review panel bodies (issue #128) ---


def _seed_review_run_with_log(tmp_path, *, output_body: str, review_log: str | None) -> tuple:
    """Build a run folder where the review sub-node 'tests' has completed.

    Writes the per-reviewer output file plus an optional review-log.md so we can
    assert how _panel_body falls back when the output is SIGNAL_JSON-only.
    """
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    review_dir = run_folder / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "review-tests-output.md").write_text(output_body)
    if review_log is not None:
        (review_dir / "review-log.md").write_text(review_log)
    update_plan_md(run_folder, "review_tests", "passed", elapsed_secs=1.0)
    content = (run_folder / "plan.md").read_text()
    panel_line = next(line for line in content.splitlines() if line.lstrip().startswith("review_tests_panel["))
    return run_folder, panel_line


def test_review_panel_uses_direct_output_when_prose_present(tmp_path):
    """When the per-reviewer output file has real prose, the panel renders that
    prose verbatim — no need to fall through to review-log.md."""
    output_body = 'Direct prose from the reviewer agent.\n\n```json\n{"stage": "review"}\n```\n'
    _, panel = _seed_review_run_with_log(
        tmp_path,
        output_body=output_body,
        review_log="---\nreviewer_statuses: {}\n---\n\n## Tests Review — Round 1\n\nLog prose that must NOT win.\n",
    )
    assert "Direct prose from the reviewer agent." in panel
    assert "Log prose that must NOT win." not in panel


def test_review_panel_falls_back_to_review_log_when_output_is_signal_only(tmp_path):
    """When the output is just SIGNAL_JSON, the panel must render prose from
    review-log.md instead of bare ``done``."""
    output_body = '```json\n{"stage": "review", "status": "passed", "reviewer_statuses": {"tests": "approved"}}\n```\n'
    review_log = (
        "---\nreviewer_statuses:\n  tests: approved\n---\n\n"
        "## Tests Review — Round 1\n\n"
        "**Verdict**: approved — coverage is sufficient.\n"
    )
    _, panel = _seed_review_run_with_log(tmp_path, output_body=output_body, review_log=review_log)
    assert "approved — coverage is sufficient" in panel
    # Status-word fallback must not leak in when prose was found.
    assert ">done</div>" not in panel


def test_review_panel_picks_correct_round_section(tmp_path):
    """Multiple rounds in review-log.md must not bleed into one another — round 2
    nodes must only see round 2 prose."""
    run_folder = _make_run_folder(tmp_path)
    init_plan_md(run_folder, _profile_with_review())
    add_fix_cycle_node(run_folder, cycle_num=1, reviewers=["tests"])
    review_dir = run_folder / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    # Both round outputs are SIGNAL_JSON only so the renderer is forced to use review-log.
    signal_only = '```json\n{"stage": "review", "status": "passed"}\n```\n'
    (review_dir / "review-tests-output.md").write_text(signal_only)
    (review_dir / "review-tests-round2-output.md").write_text(signal_only)
    (review_dir / "review-log.md").write_text(
        "---\nreviewer_statuses:\n  tests: approved\n---\n\n"
        "## Tests Review — Round 1\n\nFirst round verdict prose.\n\n"
        "## Tests Review — Round 2\n\nSecond round verdict prose.\n"
    )
    update_plan_md(run_folder, "review_tests", "passed", elapsed_secs=1.0)
    update_plan_md(run_folder, "review_tests_2", "passed", elapsed_secs=1.0)
    content = (run_folder / "plan.md").read_text()
    round1 = next(line for line in content.splitlines() if line.lstrip().startswith("review_tests_panel["))
    round2 = next(line for line in content.splitlines() if line.lstrip().startswith("review_tests_2_panel["))
    assert "First round verdict prose." in round1
    assert "Second round verdict prose." not in round1
    assert "Second round verdict prose." in round2
    assert "First round verdict prose." not in round2


def test_review_panel_falls_back_to_status_when_no_review_log(tmp_path):
    """No review-log.md and no prose in output — the panel still renders the
    status word, not a hard error."""
    output_body = '```json\n{"stage": "review", "status": "passed"}\n```\n'
    _, panel = _seed_review_run_with_log(tmp_path, output_body=output_body, review_log=None)
    assert ">done</div>" in panel


def test_review_panel_falls_back_to_status_when_log_section_missing(tmp_path):
    """A review-log.md that has prose for another reviewer/round must not match —
    the panel falls back to the status word for the unmatched reviewer."""
    output_body = '```json\n{"stage": "review", "status": "passed"}\n```\n'
    review_log = (
        "---\nreviewer_statuses:\n  arch: approved\n---\n\n## Arch Review — Round 1\n\nWrong reviewer's prose.\n"
    )
    _, panel = _seed_review_run_with_log(tmp_path, output_body=output_body, review_log=review_log)
    assert "Wrong reviewer's prose." not in panel
    assert ">done</div>" in panel


# --- PR node terminal-state semantics (ADR-026) ---


def test_mark_pr_blocked_flips_init_time_pr_node(tmp_path):
    """When the pipeline fails before the PR finalisation step runs, mark_pr_blocked
    flips the init-time PR node from pending to blocked so the diagram does not
    show a pending PR after a failed run."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile, create_pr=True)
    assert "class pr pending" in (run_folder / "plan.md").read_text()

    mark_pr_blocked(run_folder)
    content = (run_folder / "plan.md").read_text()
    assert "class pr blocked" in content
    assert "class pr pending" not in content


def test_mark_pr_blocked_noop_when_no_pr_node(tmp_path):
    """Without create_pr the diagram has no PR node — mark_pr_blocked is a no-op."""
    run_folder = _make_run_folder(tmp_path)
    profile = _simple_profile("specification")
    init_plan_md(run_folder, profile, create_pr=False)
    before = (run_folder / "plan.md").read_text()
    mark_pr_blocked(run_folder)
    assert (run_folder / "plan.md").read_text() == before
