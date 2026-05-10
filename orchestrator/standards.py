import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / ".claude" / "skills"
_PREFIX = "harsh-"
_SUFFIX = "-engineering-standards"
_GENERAL_ID = "general"


def discover() -> dict[str, Path]:
    """Return {identifier: SKILL.md path} for every harsh-*-engineering-standards dir found."""
    result = {}
    if not _SKILLS_DIR.is_dir():
        return result
    for entry in _SKILLS_DIR.iterdir():
        name = entry.name
        if name.startswith(_PREFIX) and name.endswith(_SUFFIX):
            identifier = name[len(_PREFIX): -len(_SUFFIX)]
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
    return text[end + 4:].lstrip("\n")


def load(requested: list[str]) -> str:
    """Load general (always first) + each requested identifier; return stripped joined markdown.

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

    sections = [_strip_frontmatter(available[i].read_text()) for i in identifiers]
    return "\n\n---\n\n".join(sections)
