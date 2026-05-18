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
    # sort_keys=False preserves insertion order so the "elapsed" map records stage
    # completion order. The run-summary table iterates that map verbatim, so a sorted
    # dump (the yaml.dump default) re-emits stages alphabetically and the table loses
    # its chronological story — see issue #130.
    p.write_text(yaml.dump(state, default_flow_style=False, sort_keys=False))


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


def clear_blocked_at(run_folder) -> None:
    """Drop any ``blocked_at`` field from the persisted state.

    Called on successful pipeline completion so a resumed run that earlier
    recorded a blocked stage does not leave stale "blocked at <stage>" metadata
    once the pipeline has reached the end. Issue #200.
    """
    state = load_state(run_folder)
    if "blocked_at" in state:
        del state["blocked_at"]
        save_state(run_folder, state)
