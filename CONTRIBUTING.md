# Contributing

## Getting started

```bash
git clone https://github.com/gezpage/claude-code-orchestrator
cd claude-code-orchestrator
uv sync
uv run pytest tests/
```

## Making changes

Read `CLAUDE.md` before touching any code — it documents the architectural invariants and change workflow that all contributors follow.

The short version:

1. Branch from `main`: `git checkout -b <type>/<description> origin/main`
2. Make your change and run `uv run pytest tests/`
3. Commit with a conventional message: `fix:`, `feat:`, `chore:`, `docs:`
4. Open a PR — describe *why* the change was made, not what changed (the diff covers that)

## ADR gate

Before committing an architectural decision, ask: is it hard to reverse, surprising without context, and the result of genuine trade-offs? If yes to all three, write an ADR first using the template at `docs/adrs/_template.md`. Existing ADRs are in `docs/adrs/`.

Decisions that do **not** need an ADR: bug fixes, naming changes, adding tests, dependency updates, documentation edits.

## Tests

```bash
uv run pytest tests/
```

All PRs must keep tests green.

## Claude configuration policy

The orchestrator ships no Claude-specific project assets. Everything under `.claude/` — settings, sessions, MCP configs, worktree scratch space, custom skills — is treated as **local user state** and is ignored by `.gitignore`. The same applies to `.vscode/`, `.idea/`, and `.env*`.

If a future change needs a Claude asset tracked in the repo (for example, a project-specific MCP config or a skill that ships with the orchestrator), the asset must:

- Be added to `.gitignore` as a negation (e.g. `!.claude/<path>`) so only the intended file is tracked
- Be documented here, with the rationale for tracking it
- Pass review against the safety boundary in `SECURITY.md` — tracked assets become part of the supply chain

The default is **not tracked**. Do not commit machine-specific paths, symlinks, or session state.
