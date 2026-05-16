from pathlib import Path

from orchestrator import glossary

# ── parse_glossary_text ───────────────────────────────────────────────────────


def test_parse_empty_returns_empty_prologue_and_terms():
    prologue, terms = glossary.parse_glossary_text("")
    assert prologue == ""
    assert terms == {}


def test_parse_prologue_only():
    text = "# Domain language\n\nIntro paragraph.\n"
    prologue, terms = glossary.parse_glossary_text(text)
    assert "Intro paragraph." in prologue
    assert terms == {}


def test_parse_terms_split_on_h2():
    text = (
        "# Domain language\n"
        "\n"
        "Intro.\n"
        "\n"
        "## Alpha\n"
        "\n"
        "Alpha is the first letter.\n"
        "\n"
        "## Beta\n"
        "\n"
        "Beta is the second letter.\n"
    )
    prologue, terms = glossary.parse_glossary_text(text)
    assert "Intro." in prologue
    assert terms["Alpha"] == "Alpha is the first letter."
    assert terms["Beta"] == "Beta is the second letter."


def test_parse_term_with_multiline_definition():
    text = "## Term\n\nLine one.\n\nLine two.\n"
    _, terms = glossary.parse_glossary_text(text)
    assert terms["Term"] == "Line one.\n\nLine two."


# ── prepare_run_glossary ──────────────────────────────────────────────────────


def test_prepare_run_glossary_copies_canonical(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n\n## A\n\none\n")
    run_path = tmp_path / "run" / "specification" / "glossary.md"
    copied = glossary.prepare_run_glossary(canonical, run_path)
    assert copied is True
    assert run_path.read_text() == "# Domain language\n\n## A\n\none\n"


def test_prepare_run_glossary_writes_placeholder_when_canonical_missing(tmp_path):
    run_path = tmp_path / "run" / "specification" / "glossary.md"
    copied = glossary.prepare_run_glossary(tmp_path / "missing.md", run_path)
    assert copied is False
    assert run_path.is_file()
    assert "No canonical glossary" in run_path.read_text()


def test_prepare_run_glossary_with_none_writes_placeholder(tmp_path):
    run_path = tmp_path / "run" / "specification" / "glossary.md"
    copied = glossary.prepare_run_glossary(None, run_path)
    assert copied is False
    assert "No canonical glossary" in run_path.read_text()


# ── reconcile: append-only safety ─────────────────────────────────────────────


def test_reconcile_appends_new_term(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n\n## Existing\n\nold definition.\n")
    result = glossary.reconcile(canonical, {"Fresh": "A brand new term."})
    assert result.appended == ("Fresh",)
    assert result.conflicts == ()
    text = canonical.read_text()
    assert "## Existing" in text
    assert "old definition." in text
    assert "## Fresh" in text
    assert "A brand new term." in text


def test_reconcile_preserves_existing_definition_when_proposed_matches(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n\n## Same\n\nbody\n")
    result = glossary.reconcile(canonical, {"Same": "body"})
    assert result.appended == ()
    assert result.unchanged == ("Same",)
    assert result.conflicts == ()
    # File not rewritten with a duplicate section
    assert canonical.read_text().count("## Same") == 1


def test_reconcile_records_conflict_without_overwriting(tmp_path):
    canonical = tmp_path / "canon.md"
    original = "# Domain language\n\n## Term\n\nORIGINAL definition.\n"
    canonical.write_text(original)
    result = glossary.reconcile(canonical, {"Term": "DIFFERENT definition."})
    assert result.appended == ()
    assert len(result.conflicts) == 1
    assert result.conflicts[0].name == "Term"
    assert result.conflicts[0].existing == "ORIGINAL definition."
    assert result.conflicts[0].proposed == "DIFFERENT definition."
    # The canonical file is *not* modified — append-only invariant.
    assert canonical.read_text() == original


def test_reconcile_mixed_terms(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n\n## Existing\n\nkept\n\n## Same\n\nidentical\n")
    result = glossary.reconcile(
        canonical,
        {
            "Existing": "rewritten",  # conflict
            "Same": "identical",  # unchanged
            "New": "brand new",  # appended
        },
    )
    assert result.appended == ("New",)
    assert result.unchanged == ("Same",)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].name == "Existing"
    text = canonical.read_text()
    assert "## New" in text
    assert "brand new" in text
    # Existing definition preserved verbatim despite conflict
    assert "kept" in text
    assert "rewritten" not in text


def test_reconcile_creates_canonical_when_missing(tmp_path):
    canonical = tmp_path / "subdir" / "glossary.md"
    result = glossary.reconcile(canonical, {"New": "first term"})
    assert result.canonical_existed is False
    assert result.appended == ("New",)
    assert canonical.is_file()
    text = canonical.read_text()
    assert "## New" in text
    assert "first term" in text


def test_reconcile_skips_empty_definitions(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n")
    result = glossary.reconcile(canonical, {"Blank": "   \n  ", "Real": "thing"})
    assert result.appended == ("Real",)
    assert result.skipped_empty == ("Blank",)
    assert "Blank" not in canonical.read_text()


def test_reconcile_no_op_when_proposed_empty(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n")
    before = canonical.read_text()
    result = glossary.reconcile(canonical, {})
    assert result.appended == ()
    assert canonical.read_text() == before


def test_reconcile_whitespace_normalised_comparison(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n\n## Term\n\nbody text\n")
    # Trailing/leading whitespace and surrounding blank lines should not register as a conflict
    result = glossary.reconcile(canonical, {"Term": "  body text  \n\n"})
    assert result.unchanged == ("Term",)
    assert result.conflicts == ()


# ── resolve_canonical_path ────────────────────────────────────────────────────


def test_resolve_canonical_path_not_configured():
    assert glossary.resolve_canonical_path({}, "/tmp/repo") is None


def test_resolve_canonical_path_relative_to_repo_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = {"domain_language": {"path": "docs/domain.md"}}
    resolved = glossary.resolve_canonical_path(cfg, str(repo))
    assert resolved == repo / "docs" / "domain.md"


def test_resolve_canonical_path_absolute_preserved(tmp_path):
    abs_path = tmp_path / "abs" / "glossary.md"
    cfg = {"domain_language": {"path": str(abs_path)}}
    resolved = glossary.resolve_canonical_path(cfg, "/tmp/repo")
    assert resolved == abs_path


def test_resolve_canonical_path_ignores_blank_string():
    cfg = {"domain_language": {"path": "   "}}
    assert glossary.resolve_canonical_path(cfg, "/tmp/repo") is None


def test_resolve_canonical_path_ignores_non_dict_value():
    cfg = {"domain_language": "docs/glossary.md"}
    assert glossary.resolve_canonical_path(cfg, "/tmp/repo") is None


# ── setup_for_run ─────────────────────────────────────────────────────────────


def test_setup_for_run_no_config_returns_none(tmp_path):
    assert glossary.setup_for_run({}, str(tmp_path), tmp_path / "run") is None


def test_setup_for_run_copies_when_canonical_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    canon = repo / "docs" / "glossary.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Domain language\n\n## A\n\none\n")
    run_folder = tmp_path / "run"
    run_folder.mkdir()

    cfg = {"domain_language": {"path": "docs/glossary.md"}}
    paths = glossary.setup_for_run(cfg, str(repo), run_folder)
    assert paths is not None
    assert paths.canonical == canon
    assert paths.canonical_existed is True
    assert paths.run_local.read_text() == canon.read_text()


def test_setup_for_run_writes_placeholder_when_canonical_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_folder = tmp_path / "run"
    run_folder.mkdir()

    cfg = {"domain_language": {"path": "docs/glossary.md"}}
    paths = glossary.setup_for_run(cfg, str(repo), run_folder)
    assert paths is not None
    assert paths.canonical_existed is False
    assert paths.run_local.is_file()
    assert "No canonical glossary" in paths.run_local.read_text()


# ── render_conflicts_report ───────────────────────────────────────────────────


def test_render_conflicts_report_lists_each_section(tmp_path):
    result = glossary.ReconcileResult(
        appended=("New",),
        conflicts=(glossary.GlossaryConflict(name="X", existing="old", proposed="new"),),
        unchanged=("Same",),
        skipped_empty=("Blank",),
    )
    text = glossary.render_conflicts_report(result)
    assert "## Appended" in text
    assert "`New`" in text
    assert "## Unchanged" in text
    assert "`Same`" in text
    assert "## Skipped" in text
    assert "`Blank`" in text
    assert "## Conflicts" in text
    assert "### X" in text
    assert "old" in text
    assert "new" in text


# ── format_term ───────────────────────────────────────────────────────────────


def test_format_term_renders_h2_section():
    out = glossary.format_term("Alpha", "First letter.")
    assert out.startswith("## Alpha\n")
    assert "First letter." in out
    assert out.endswith("\n")


def test_format_term_handles_blank_body():
    out = glossary.format_term("Empty", "")
    assert out == "## Empty\n"


# ── Path type acceptance ──────────────────────────────────────────────────────


def test_reconcile_accepts_Path(tmp_path):
    canonical = tmp_path / "canon.md"
    canonical.write_text("# Domain language\n")
    result = glossary.reconcile(Path(canonical), {"T": "def"})
    assert result.appended == ("T",)
