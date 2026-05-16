from pathlib import Path

from orchestrator.plan._graph import Edge, Graph, Node, Subgraph, save_graph
from orchestrator.plan._helpers import _run_header
from orchestrator.plan._render import write_plan_md
from orchestrator.profile import ExpansionKind, Profile

_INIT_DIRECTIVE = "%%{init: {'theme': 'base', 'themeVariables': {'fontSize': '14px', 'lineColor': '#6b7280'}}}%%"


def init_plan_md(
    run_folder: Path,
    profile: Profile,
    pr_notice: str | None = None,
    agent_metadata: dict[str, dict[str, str | None]] | None = None,
    create_pr: bool = False,
) -> None:
    run_folder = Path(run_folder)
    plan_path = run_folder / "plan.md"
    if plan_path.exists():
        return

    graph = build_initial_graph(profile, agent_metadata=agent_metadata, create_pr=create_pr)
    save_graph(run_folder, graph)
    write_plan_md(plan_path, _run_header(run_folder, pr_notice=pr_notice), graph)


def build_initial_graph(
    profile: Profile,
    agent_metadata: dict[str, dict[str, str | None]] | None = None,
    create_pr: bool = False,
) -> Graph:
    graph = Graph(init_directive=_INIT_DIRECTIVE)
    graph.add_node(Node(id="Start", shape="stadium", raw_label="▶ Start", css_class="startend"))

    chain_ids: list[str] = []
    parents: dict[str, list[str]] = {}

    def _meta(stage_name: str) -> tuple[str, str]:
        info = (agent_metadata or {}).get(stage_name) or {}
        return (info.get("backend") or "", info.get("model") or "")

    for stage in profile.stages:
        name = stage.name
        display_name = name.replace("_", " ").title()
        graph.add_subgraph(Subgraph(id=f"sg_{name}", display=display_name))
        backend, model = _meta(name)

        if stage.mode == "interactive":
            graph.add_node(
                Node(
                    id=name,
                    shape="hex",
                    raw_label=f"✋ {name.title()}",
                    css_class="gate",
                    subgraph=f"sg_{name}",
                    mode=stage.mode,
                    stage_dir=name,
                    backend=backend,
                    model=model,
                )
            )
            chain_ids.append(name)

        elif stage.expansion == ExpansionKind.PROMPTS:
            graph.add_node(
                Node(
                    id=name,
                    display=name.title(),
                    css_class="pending",
                    subgraph=f"sg_{name}",
                    mode=stage.mode,
                    stage_dir=name,
                    backend=backend,
                    model=model,
                )
            )
            for reviewer, prompt_path in stage.prompts.items():
                reviewer_impl = Path(prompt_path).stem
                sub_id = f"{name}_{reviewer}"
                # Suffix sub-node labels with the parent stage display so review
                # sub-nodes read as e.g. "Implementation Review", distinct from the
                # actual Implementation stage. Derived (not enumerated) so newly
                # added reviewers pick it up automatically.
                graph.add_node(
                    Node(
                        id=sub_id,
                        display=f"{reviewer.title()} {display_name}",
                        impl=reviewer_impl,
                        css_class="pending",
                        subgraph=f"sg_{name}",
                        mode=stage.mode,
                        stage_dir=name,
                        file_suffix=reviewer,
                        backend=backend,
                        model=model,
                    )
                )
                parents.setdefault(name, []).append(sub_id)
            chain_ids.append(name)

        else:
            prompt = stage.prompt or f"prompts/{name}/default.md"
            impl = Path(prompt).stem
            graph.add_node(
                Node(
                    id=name,
                    display=name.title(),
                    impl=impl,
                    css_class="pending",
                    subgraph=f"sg_{name}",
                    mode=stage.mode,
                    stage_dir=name,
                    backend=backend,
                    model=model,
                )
            )
            chain_ids.append(name)

    if create_pr:
        graph.add_node(
            Node(
                id="pr",
                display="PR",
                css_class="pending",
                mode="deterministic",
                stage_dir="pr_draft",
            )
        )
        chain_ids.append("pr")

    graph.add_node(Node(id="Done", shape="stadium", raw_label="■ Done", css_class="startend"))

    if not chain_ids:
        graph.edges.append(Edge(steps=[["Start"], ["Done"]]))
        return graph

    graph.edges.append(Edge(steps=[["Start"], [chain_ids[0]]]))
    for i, cur in enumerate(chain_ids):
        nxt = chain_ids[i + 1] if i + 1 < len(chain_ids) else "Done"
        if cur in parents:
            sub_ids = parents[cur]
            graph.edges.append(Edge(steps=[[cur], list(sub_ids)]))
            graph.edges.append(Edge(steps=[list(sub_ids), [nxt]]))
        else:
            graph.edges.append(Edge(steps=[[cur], [nxt]]))

    return graph
