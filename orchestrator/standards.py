import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / ".claude" / "skills"
_PREFIX = "harsh-"
_SUFFIX = "-engineering-standards"
_GENERAL_ID = "general"


def discover() -> dict[str, Path]:
    """Return {identifier: SKILL.md path} for every harsh-*-engineering-standards dir found."""
    result: dict[str, Path] = {}
    if not _SKILLS_DIR.is_dir():
        return result
    for entry in _SKILLS_DIR.iterdir():
        name = entry.name
        if name.startswith(_PREFIX) and name.endswith(_SUFFIX):
            identifier = name[len(_PREFIX) : -len(_SUFFIX)]
            skill_file = entry / "SKILL.md"
            if skill_file.exists():
                result[identifier] = skill_file
    return result


def _strip_frontmatter(text: str) -> str:
    """Remove leading YAML frontmatter block (--- ... ---) if present."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def _extract_h1(text: str) -> tuple[str, str]:
    """Return (h1_text, remaining_body) stripping the leading H1 line if present."""
    if not text.startswith("# "):
        return "", text
    newline = text.find("\n")
    if newline == -1:
        return text[2:].strip(), ""
    return text[2:newline].strip(), text[newline:].lstrip("\n")


def load(requested: list[str]) -> str:
    """Load general (always first) + each requested identifier; return joined markdown.

    Each standard becomes a ### subsection using the skill's own H1 as its label.
    Returns "" when no skills are found (safe — caller skips the empty block).
    """
    available = discover()
    identifiers = []
    if _GENERAL_ID in available:
        identifiers.append(_GENERAL_ID)
    for ident in requested:
        if ident == _GENERAL_ID:
            continue
        if ident in available:
            identifiers.append(ident)
        else:
            logger.warning("standards: no skill found for identifier '%s' — skipping", ident)

    sections = []
    for ident in identifiers:
        body = _strip_frontmatter(available[ident].read_text())
        label, body = _extract_h1(body)
        if not label:
            label = ident.replace("-", " ").title()
        sections.append(f"### {label}\n\n{body.strip()}")
    return "\n\n---\n\n".join(sections)
