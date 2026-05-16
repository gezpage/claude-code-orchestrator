"""Codebase-backed domain-language glossary.

Optional, opt-in support for a canonical glossary that lives in the target
codebase. The orchestrator never edits the canonical file destructively:

- Specification reads the canonical glossary (if configured) and the run-local
  copy materialised by ``prepare_run_glossary``.
- Decomposition and implementation read the run-local copy.
- Harvest proposes new terms in its SIGNAL_JSON. The orchestrator runs
  ``reconcile`` after harvest passes — it appends terms that are unambiguously
  new and records conflicts as warnings rather than rewriting existing
  definitions.

Format: a markdown document whose ``## Term`` H2 headings are term names. The
prose under each H2 (up to the next H2) is the definition. Anything before the
first H2 is preserved verbatim as the prologue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_H2_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$")


def parse_glossary_text(text: str) -> tuple[str, dict[str, str]]:
    """Parse a glossary document into (prologue, terms).

    ``prologue`` is the text before the first ``## `` heading — typically the
    document title and any preamble. ``terms`` maps term name → definition
    body (markdown, without the heading line, leading/trailing whitespace
    stripped).
    """
    prologue_lines: list[str] = []
    terms: dict[str, str] = {}
    current_name: str | None = None
    current_body: list[str] = []

    def _flush() -> None:
        if current_name is None:
            return
        body = "\n".join(current_body).strip("\n")
        terms[current_name] = body

    for raw_line in text.splitlines():
        match = _H2_RE.match(raw_line)
        if match:
            _flush()
            current_name = match.group("name").strip()
            current_body = []
        elif current_name is None:
            prologue_lines.append(raw_line)
        else:
            current_body.append(raw_line)
    _flush()

    prologue = "\n".join(prologue_lines).rstrip("\n")
    return prologue, terms


def format_term(name: str, definition: str) -> str:
    """Render one term as a markdown H2 section, including a trailing blank line."""
    body = definition.strip("\n")
    if body:
        return f"## {name}\n\n{body}\n"
    return f"## {name}\n"


@dataclass(frozen=True)
class GlossaryConflict:
    """A proposed term clashes with an existing definition under the same name."""

    name: str
    existing: str
    proposed: str


@dataclass(frozen=True)
class ReconcileResult:
    appended: tuple[str, ...] = ()
    conflicts: tuple[GlossaryConflict, ...] = ()
    unchanged: tuple[str, ...] = ()
    canonical_existed: bool = True
    skipped_empty: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.appended)


def prepare_run_glossary(canonical_path: Path | None, run_path: Path) -> bool:
    """Materialise the run-local glossary copy.

    Returns True if a canonical file was copied, False if no canonical file
    existed (a placeholder run-local file is still written so downstream
    prompts can reference it unconditionally).
    """
    run_path.parent.mkdir(parents=True, exist_ok=True)
    if canonical_path is not None and canonical_path.is_file():
        run_path.write_text(canonical_path.read_text())
        return True
    placeholder = "# Domain language (run-local copy)\n\n_No canonical glossary was available for this run._\n"
    run_path.write_text(placeholder)
    return False


def _normalise(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip("\n").splitlines()).strip()


def reconcile(
    canonical_path: Path,
    proposed_terms: dict[str, str],
) -> ReconcileResult:
    """Append-only reconciliation of agent-proposed terms into the canonical file.

    Rules (per the safety contract in issue #134):

    - A proposed term whose name does not appear in the canonical glossary is
      appended verbatim.
    - A proposed term whose name appears with an identical definition is
      reported as ``unchanged``; the canonical file is not rewritten.
    - A proposed term whose name appears with a different definition is
      recorded as a ``GlossaryConflict``. The canonical definition is never
      overwritten.
    - A proposed term whose definition is blank is skipped — appending an
      empty term would be noise, and the agent likely meant to leave it out.

    The canonical glossary is only opened for writing when at least one term
    is appended. Existing ordering and prose are preserved.
    """
    canonical_existed = canonical_path.is_file()
    if canonical_existed:
        prologue, existing = parse_glossary_text(canonical_path.read_text())
    else:
        prologue, existing = "", {}

    appended: list[str] = []
    conflicts: list[GlossaryConflict] = []
    unchanged: list[str] = []
    skipped_empty: list[str] = []
    new_sections: list[str] = []

    for name, definition in proposed_terms.items():
        name = name.strip()
        if not name:
            continue
        body = definition.strip("\n")
        if not body.strip():
            skipped_empty.append(name)
            continue
        if name in existing:
            if _normalise(existing[name]) == _normalise(body):
                unchanged.append(name)
            else:
                conflicts.append(GlossaryConflict(name=name, existing=existing[name], proposed=body))
            continue
        appended.append(name)
        new_sections.append(format_term(name, body))

    if appended:
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        if canonical_existed:
            base = canonical_path.read_text()
            if not base.endswith("\n"):
                base += "\n"
            if not base.endswith("\n\n"):
                base += "\n"
            canonical_path.write_text(base + "".join(new_sections))
        else:
            seed_prologue = "# Domain language\n"
            canonical_path.write_text(seed_prologue + "\n" + "".join(new_sections))
        # Touch prologue to silence the unused-variable lint while keeping the parse
        # result available for future merge strategies that need it.
        _ = prologue

    return ReconcileResult(
        appended=tuple(appended),
        conflicts=tuple(conflicts),
        unchanged=tuple(unchanged),
        canonical_existed=canonical_existed,
        skipped_empty=tuple(skipped_empty),
    )


def render_conflicts_report(result: ReconcileResult) -> str:
    """Render conflict details into a markdown document for operator review."""
    lines = ["# Glossary reconciliation report", ""]
    if result.appended:
        lines.append("## Appended")
        lines.append("")
        for name in result.appended:
            lines.append(f"- `{name}`")
        lines.append("")
    if result.unchanged:
        lines.append("## Unchanged (already present, identical definition)")
        lines.append("")
        for name in result.unchanged:
            lines.append(f"- `{name}`")
        lines.append("")
    if result.skipped_empty:
        lines.append("## Skipped (empty definition)")
        lines.append("")
        for name in result.skipped_empty:
            lines.append(f"- `{name}`")
        lines.append("")
    if result.conflicts:
        lines.append("## Conflicts — canonical definition preserved")
        lines.append("")
        for conflict in result.conflicts:
            lines.append(f"### {conflict.name}")
            lines.append("")
            lines.append("**Existing definition:**")
            lines.append("")
            lines.append(conflict.existing)
            lines.append("")
            lines.append("**Proposed definition (not applied):**")
            lines.append("")
            lines.append(conflict.proposed)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def resolve_canonical_path(project_config: dict, repo_root: str | None) -> Path | None:
    """Pull ``domain_language.path`` out of ``project.yaml`` and resolve it.

    Relative paths resolve against ``repo-root`` — the canonical glossary
    lives in the target codebase, not the docs repo. Returns None when the
    feature is not configured.
    """
    raw = project_config.get("domain_language")
    if not isinstance(raw, dict):
        return None
    path_value = raw.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    candidate = Path(path_value)
    if not candidate.is_absolute():
        if not repo_root:
            return candidate
        candidate = Path(repo_root) / candidate
    return candidate


@dataclass(frozen=True)
class GlossaryPaths:
    canonical: Path | None
    run_local: Path
    canonical_existed: bool = False


def setup_for_run(
    project_config: dict,
    repo_root: str | None,
    run_folder: Path,
) -> GlossaryPaths | None:
    """If a glossary is configured, materialise the run-local copy.

    Returns ``GlossaryPaths`` when configured (regardless of whether the
    canonical file currently exists on disk), or ``None`` when the feature is
    not enabled.
    """
    canonical = resolve_canonical_path(project_config, repo_root)
    if canonical is None:
        return None
    run_local = run_folder / "specification" / "glossary.md"
    canonical_existed = prepare_run_glossary(canonical, run_local)
    return GlossaryPaths(
        canonical=canonical,
        run_local=run_local,
        canonical_existed=canonical_existed,
    )
