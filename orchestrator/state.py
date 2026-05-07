from pathlib import Path
import yaml


def load_state(run_folder) -> dict:
    p = Path(run_folder) / "_state.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def save_state(run_folder, state: dict) -> None:
    p = Path(run_folder) / "_state.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(state, default_flow_style=False))


def update_stage_status(run_folder, stage: str, status: str) -> None:
    state = load_state(run_folder)
    state.setdefault("stages", {})[stage] = status
    save_state(run_folder, state)
