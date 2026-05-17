from pathlib import Path
from unittest.mock import patch

from orchestrator.standards import _canonical, _extract_h1, _strip_frontmatter, discover, load


def _make_skill(skills_dir: Path, identifier: str, content: str) -> None:
    d = skills_dir / f"harsh-{identifier}-engineering-standards"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(content)


GENERAL_CONTENT = "---\nname: harsh-general-engineering-standards\ndescription: General rules.\n---\n\n# General Standards\n\nDo good work.\n"
PHP_CONTENT = (
    "---\nname: harsh-php-engineering-standards\ndescription: PHP rules.\n---\n\n# PHP Standards\n\nUse strict_types.\n"
)
GO_CONTENT = "---\nname: harsh-go-engineering-standards\ndescription: Go rules.\n---\n\n# Go Standards\n\nUse gofmt.\n"
NODEJS_CONTENT = (
    "---\nname: harsh-nodejs-engineering-standards\ndescription: Node rules.\n---\n\n# Node Standards\n\nUse npm.\n"
)
TYPESCRIPT_CONTENT = "---\nname: harsh-typescript-engineering-standards\ndescription: TS rules.\n---\n\n# TypeScript Standards\n\nUse strict.\n"
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

    def test_prefers_compact_over_skill(self, tmp_path):
        d = tmp_path / "harsh-python-engineering-standards"
        d.mkdir()
        (d / "SKILL.md").write_text("# Full skill")
        (d / "COMPACT.md").write_text("# Compact rules")
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = discover()
        assert result["python"].name == "COMPACT.md"

    def test_falls_back_to_skill_when_no_compact(self, tmp_path):
        d = tmp_path / "harsh-python-engineering-standards"
        d.mkdir()
        (d / "SKILL.md").write_text("# Full skill")
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = discover()
        assert result["python"].name == "SKILL.md"


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


class TestCanonical:
    def test_golang_aliases_to_go(self):
        assert _canonical("golang") == "go"

    def test_node_aliases_to_nodejs(self):
        assert _canonical("node") == "nodejs"

    def test_nodejs_unchanged(self):
        assert _canonical("nodejs") == "nodejs"

    def test_node_dot_js_aliases_to_nodejs(self):
        assert _canonical("node.js") == "nodejs"

    def test_javascript_aliases_to_nodejs(self):
        assert _canonical("javascript") == "nodejs"

    def test_js_aliases_to_nodejs(self):
        assert _canonical("js") == "nodejs"

    def test_ts_aliases_to_typescript(self):
        assert _canonical("ts") == "typescript"

    def test_py_aliases_to_python(self):
        assert _canonical("py") == "python"

    def test_canonical_names_unchanged(self):
        for name in ["go", "python", "php", "typescript", "java", "nodejs"]:
            assert _canonical(name) == name

    def test_unknown_identifier_returned_lowercased(self):
        assert _canonical("Rust") == "rust"

    def test_mixed_case_alias_resolved(self):
        assert _canonical("Golang") == "go"
        assert _canonical("JavaScript") == "nodejs"


class TestLoadAliases:
    def test_golang_alias_resolves_to_go_skill(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "go", GO_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["golang"])
        assert "Go Standards" in result

    def test_node_alias_resolves_to_nodejs_skill(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "nodejs", NODEJS_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["node"])
        assert "Node Standards" in result

    def test_javascript_alias_resolves_to_nodejs_skill(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "nodejs", NODEJS_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["javascript"])
        assert "Node Standards" in result

    def test_ts_alias_resolves_to_typescript_skill(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "typescript", TYPESCRIPT_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["ts"])
        assert "TypeScript Standards" in result

    def test_alias_and_canonical_not_duplicated(self, tmp_path):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        _make_skill(tmp_path, "go", GO_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            result = load(["go", "golang"])
        assert result.count("Go Standards") == 1

    def test_unknown_identifier_warning_preserves_original_spelling(self, tmp_path, caplog):
        _make_skill(tmp_path, "general", GENERAL_CONTENT)
        with patch("orchestrator.standards._SKILLS_DIR", tmp_path):
            import logging

            with caplog.at_level(logging.WARNING, logger="orchestrator.standards"):
                load(["RustOnSteroids"])
        assert "RustOnSteroids" in caplog.text


class TestCommittedSkills:
    """Verify the real .claude/skills/ tree ships the canonical language skills."""

    def test_canonical_language_skills_discoverable(self):
        result = discover()
        # These all live in this repo on disk; alias forms are tested above via _canonical.
        for ident in ["general", "go", "java", "nodejs", "php", "python", "typescript"]:
            assert ident in result, f"missing skill: harsh-{ident}-engineering-standards"

    def test_committed_skills_use_compact_form(self):
        result = discover()
        for ident in ["general", "go", "java", "nodejs", "php", "python", "typescript"]:
            assert result[ident].name == "COMPACT.md", f"skill {ident} should use COMPACT.md for prompt injection"
