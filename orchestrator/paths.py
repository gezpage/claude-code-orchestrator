# Path utilities; resolves and validates all well-known orchestrator filesystem locations.
from pathlib import Path


def require_file(path) -> Path:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Required file not found: {p}")
    return p


def require_dir(path) -> Path:
    p = Path(path)
    if not p.is_dir():
        raise FileNotFoundError(f"Required directory not found: {p}")
    return p


def resolve_workflow_root(docs_root, project) -> Path:
    return require_dir(Path(docs_root) / "projects" / project / "workflow")


def resolve_run_folder(docs_root, project, feature_slug, date, n) -> Path:
    return Path(docs_root) / "projects" / project / "workflow" / "runs" / feature_slug / f"{date}-run-{n}"


def resolve_profiles_dir(docs_root, project) -> Path:
    return require_dir(Path(docs_root) / "projects" / project / "workflow" / "profiles")


def resolve_prompts_dir(docs_root, project) -> Path:
    return Path(docs_root) / "projects" / project / "workflow" / "prompts"
