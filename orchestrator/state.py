# State persistence; loads and saves per-run YAML state including stage statuses and upstream signals.
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


def save_stage_signal(run_folder, stage: str, signal: dict) -> None:
    state = load_state(run_folder)
    state.setdefault("signals", {})[stage] = signal
    save_state(run_folder, state)


def load_signals(run_folder) -> dict:
    state = load_state(run_folder)
    return state.get("signals", {})  # type: ignore[no-any-return]


def save_stage_elapsed(run_folder, stage: str, secs: float) -> None:
    state = load_state(run_folder)
    state.setdefault("elapsed", {})[stage] = secs
    save_state(run_folder, state)


def load_elapsed(run_folder) -> dict:
    state = load_state(run_folder)
    return state.get("elapsed", {})  # type: ignore[no-any-return]


def save_stage_agent(run_folder, stage: str, backend: str | None, model: str | None) -> None:
    """Record which agent backend + model executed a stage. Required by ADR-018 so the
    run artifact carries the effective context controls, not just the signal."""
    state = load_state(run_folder)
    state.setdefault("agent", {})[stage] = {"backend": backend, "model": model}
    save_state(run_folder, state)
