from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def render_prompt(stage: str, implementation: str, variables: dict, docs_root: str, project: str) -> str:
    core_path = _PROMPTS_DIR / stage / f"{implementation}.md"
    if not core_path.exists():
        raise FileNotFoundError(f"Core prompt template not found: {core_path}")

    env = Environment(
        loader=FileSystemLoader(str(_PROMPTS_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    template = env.get_template(f"{stage}/{implementation}.md")
    rendered = template.render(**variables)

    ext_path = Path(docs_root) / "projects" / project / "workflow" / "prompts" / f"{stage}.md"
    if ext_path.exists():
        ext_env = Environment(
            loader=FileSystemLoader(str(ext_path.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        ext_template = ext_env.get_template(ext_path.name)
        ext_rendered = ext_template.render(**variables)
        rendered = rendered.rstrip("\n") + "\n\n## Project conventions\n\n" + ext_rendered

    return rendered
