import pytest
from pathlib import Path
from unittest.mock import patch

from orchestrator.standards import discover, _strip_frontmatter, _extract_h1, load, _SKILLS_DIR


def _make_skill(skills_dir: Path, identifier: str, content: str) -> None:
    d = skills_dir / f"harsh-{identifier}-engineering-standards"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(content)


GENERAL_CONTENT = "---\nname: harsh-general-engineering-standards\ndescription: General rules.\n---\n\n# General Standards\n\nDo good work.\n"
PHP_CONTENT = "---\nname: harsh-php-engineering-standards\ndescription: PHP rules.\n---\n\n# PHP Standards\n\nUse strict_types.\n"
ORPHAN_DIR_CONTENT = "# Not a skill"  # dir without SKILL.md


class TestExtractH1:
    def test_extracts_h1_and_returns_body(self):
        text = "# My Title\n\nSome body text.\n"
        label, body = _extract_h1(text)
        assert label == "My Title"
        assert body == "Some body text.\n"

    def test_no_h1_returns_empty_label(self):
        text = "## Not an H1\n\nBody.\n"
        label, body = _extract_h1(text)
        assert label == ""
        assert body == text

    def test_h1_only_no_body(self):
        text = "# Just a title"
        label, body = _extract_h1(text)
        assert label == "Just a title"
        assert body == ""

    def test_h1_label_stripped(self):
        text = "# Title With Spaces  \n\nBody.\n"
        label, _ = _extract_h1(text)
        assert label == "Title With Spaces"


class TestStripFrontmatter:
    def test_strips_standard_frontmatter(self):
        text = "---\nname: foo\ndescription: bar\n---\n\n# Body\n"
        assert _strip_frontmatter(text) == "# Body\n"

    def test_no_frontmatter_unchanged(self):
        text = "# Just content\nNo frontmatter here.\n"
        assert _strip_frontmatter(text) == text

    def test_missing_closing_fence_unchanged(self):
        text = "---\nname: foo\n# not closed"
        assert _strip_frontmatter(text) == text

    def test_strips_leading_blank_lines_after_fence(self):
        text = "---\nname: foo\n---\n\n\n# Body\n"
        assert _strip_frontmatter(text) == "# Body\n"


class TestDiscover:
    def test_finds_harsh_skills(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "php", PHP_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = discover()
        assert set(result.keys()) == {"general", "php"}

    def test_ignores_non_harsh_dirs(self, tmp_path):
        (tmp_path / "commenting").mkdir()
        (tmp_path / "commenting" / "SKILL.md").write_text("# Commenting")
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = discover()
        assert result == {}

    def test_ignores_dirs_without_skill_md(self, tmp_path):
        d = tmp_path / "harsh-go-engineering-standards"
        d.mkdir()
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = discover()
        assert result == {}

    def test_returns_empty_when_skills_dir_missing(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with patch("orchestrator.standards._SKILLS_DIR", missing):
            result = discover()
        assert result == {}


class TestLoad:
    def test_h1_promoted_to_h3_subsection(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load([])
        assert "### General Standards" in result
        # No bare H1 lines remain (only ### prefix, not # or ##)
        import re
        assert not re.search(r"^# ", result, re.MULTILINE)

    def test_identifier_used_as_label_when_no_h1(self, tmp_path):
        content = "---\nname: x\n---\n\nNo heading here.\n"
        _make_skill(tmp_path, "general", content)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load([])
        assert "### General" in result

    def test_general_always_included(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load([])
        assert "General Standards" in result

    def test_requested_skill_included(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "php", PHP_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["php"])
        assert "General Standards" in result
        assert "PHP Standards" in result

    def test_general_appears_before_requested(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "php", PHP_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["php"])
        assert result.index("General Standards") < result.index("PHP Standards")

    def test_frontmatter_stripped(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load([])
        assert "name: harsh-general" not in result
        assert "description:" not in result

    def test_unknown_identifier_skipped_with_warning(self, tmp_path, caplog):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            import logging
            with caplog.at_level(logging.WARNING, logger="orchestrator.standards"):
                result = load(["nonexistent"])
        assert "nonexistent" in caplog.text
        assert "PHP Standards" not in result

    def test_explicit_general_in_list_not_duplicated(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["general"])
        assert result.count("General Standards") == 1

    def test_returns_empty_when_no_skills_found(self, tmp_path):
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["php"])
        assert result == ""

    def test_sections_separated_by_hr(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "php", PHP_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["php"])
        assert "\n\n---\n\n" in result
