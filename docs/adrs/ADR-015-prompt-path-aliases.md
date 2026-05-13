---
status: accepted
date: 2026-05-13
affects: [orchestrator/prompts, orchestrator/renderer.py]
---

# ADR-015: Path aliases in prompt prose

**Status:** Accepted
**Date:** 2026-05-13

## Context

Stage prompts originally referenced runtime paths exclusively through Jinja variables like `{{ run_folder }}` and `{{ repo_root }}`. Rendered against real configs these expand to long absolute paths (50+ characters is common) and the same path can appear five to ten times in a single prompt. The implementation prompt alone repeated `repo_root` seven times. Multiplied across the pipeline and the fix cycle, that is real token spend with no semantic value — the agent does not benefit from re-reading the same absolute path.

Removing the Jinja variables outright is not viable: the orchestrator parses absolute paths back out of `SIGNAL_JSON` lines, and the discovery-planning stage writes per-track prompts that are dispatched to fresh agents who do not see the parent prompt. Both consumers require fully-expanded paths.

## Decision

Introduce short alias tokens (`$REPO_ROOT`, `$RUN_FOLDER`, `$DOCS_ROOT`) defined once per prompt by including `prompts/_includes/aliases.md`. The included partial renders the alias-to-absolute-path mapping using the same Jinja variables, so a single prompt resolves the absolute path exactly once.

Aliases are used **only** in human-readable prose (instruction lists, headers, narrative explanations). Two cases keep `{{ run_folder }}` etc. as Jinja variables:

1. **`SIGNAL_JSON` examples** — parsed by Python; must be fully-expanded paths.
2. **The track-prompt template inside `discovery/planning.md`** — written verbatim into per-track prompts that are sent to fresh agents without the alias block.

## Consequences

- Each prompt resolves any given absolute path once, in the alias section, instead of N times. Implementation-stage prompts shrink by roughly 300 characters per invocation at typical path lengths.
- New prompts must include `{% include "_includes/aliases.md" %}` near the top and use aliases in body prose; `SIGNAL_JSON` examples and any text written into downstream prompts must continue using `{{ ... }}` Jinja.
- Project-level prompt extensions (`workflow/prompts/{stage}.md`) render through a separate Jinja loader and cannot use `{% include %}` against the core prompts directory. Extensions referencing `$REPO_ROOT` etc. work because the alias block from the core prompt is already in the rendered output when the extension is appended.
- The `_includes/` subdirectory matches the existing `prompts/**/*.md` package-data glob, so no `pyproject.toml` change is needed.
