"""TTY-aware wrappers around questionary.

Every prompt raises PromptNotAvailable when stdin/stdout is not a TTY so that
callers in non-interactive contexts (CI, piped scripts) get a structured error
rather than a hang or library traceback. Callers must always provide a fallback
or upfront validation when a value cannot be supplied via flags or persistence.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import questionary


class PromptNotAvailable(RuntimeError):
    """Raised when an interactive prompt is requested in a non-TTY environment."""


def is_interactive() -> bool:
    """True iff both stdin and stdout are attached to a terminal."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _require_tty(label: str) -> None:
    if not is_interactive():
        raise PromptNotAvailable(
            f"Cannot prompt for {label!r} — stdin/stdout is not a terminal. "
            f"Pass the value via a CLI flag or set it in project.yaml."
        )


def ask_text(
    message: str,
    default: str | None = None,
    validate: Callable[[str], bool | str] | None = None,
) -> str:
    _require_tty(message)
    answer = questionary.text(message, default=default or "", validate=validate).unsafe_ask()
    return str(answer).strip()


def ask_path(message: str, default: str | None = None, must_exist: bool = True) -> str:
    def _validate(value: str) -> bool | str:
        if not value.strip():
            return "A path is required"
        if must_exist and not Path(value).expanduser().exists():
            return f"Path does not exist: {value}"
        return True

    _require_tty(message)
    answer = questionary.path(message, default=default or "", validate=_validate).unsafe_ask()
    return str(answer).strip()


def ask_confirm(message: str, default: bool = False) -> bool:
    _require_tty(message)
    answer = questionary.confirm(message, default=default).unsafe_ask()
    return bool(answer)


def ask_select(message: str, choices: list[str], default: str | None = None) -> str:
    if not choices:
        raise ValueError("ask_select requires at least one choice")
    _require_tty(message)
    answer = questionary.select(message, choices=choices, default=default).unsafe_ask()
    return str(answer)
