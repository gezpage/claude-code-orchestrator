# Orchestrator — Architectural Invariants

Developer-facing reference. Read before touching any orchestrator code.

---

## Invariants

- **`--dangerously-skip-permissions` is mandatory in every `run_stage()` call.** Not a shortcut — it is the documented use case for unattended, trusted pipeline execution. Removing it breaks all unattended stage dispatch. See ADR-003.

- **`--bare` is mandatory in every `run_stage()` call.** Skips MCP server loading and hook execution at stage startup. Stage agents have no access to MCP tools by design. See ADR-012.

- **The main orchestration session never reads stage output file contents.** `orchestrate.py` receives file paths and status values via signal JSON only. It must not `open()` or `Read` any stage output file. Adding a file read to `orchestrate.py` violates the token-minimisation invariant and will cause unbounded context growth across long pipelines. See ADR-004.

- **All context a downstream stage needs must be surfaced in the signal JSON.** Stage output schemas are designed around this constraint. If a downstream stage appears to need file content from a prior stage, the solution is to add a reference field to the upstream signal JSON — not to read the file in `orchestrate.py`. See ADR-004.

- **`workflow/` paths are fixed convention — do not add config for them.** Python derives all orchestrator paths from `{docs-root}/projects/{project}/workflow/`. Do not introduce a `project.yaml.folders` key or any path override mechanism. See ADR-006.

- **Interactive stages (`mode: interactive`) are dispatched through `run_interactive_stage()` in `run_stage.py` — not `run_stage()`.** Python launches an interactive `claude` session (no `--bare`, no `--dangerously-skip-permissions`), waits for it to exit, then checks for the declared `artifact` file. The `--bare`/`--dangerously-skip-permissions` invariants apply only to `run_stage()`. See ADR-007.

- **Stage output schemas are the interface contract — they belong to the stage, not the implementation.** All implementations of a stage must satisfy the same schema. See ADR-008.

- **Fix cycles run in the current run folder — do not create a new run.** `_state.yaml`, `review.md`, and all fix-cycle output accumulate in the existing run folder. See ADR-009.

- **The fix cycle limit is 2 and is enforced in `review_cycle.py` via `_MAX_CYCLES`.** Not configurable via `project.yaml`. See ADR-011.

---

## Path Resolution Rules

- `{docs-root}` — passed as `--docs-root` at runtime; required, no default.
- `{docs-root}/projects/{project}/project.yaml` — project config; `repo-root` field must point to an existing path or Python fails immediately.
- `{docs-root}/projects/{project}/workflow/profiles/` — profile YAML files.
- `{docs-root}/projects/{project}/workflow/prompts/` — project prompt extensions.
- `{docs-root}/projects/{project}/workflow/runs/{feature-slug}/{YYYY-MM-DD}-run-{N}/` — run folder.
- Core stage prompts: `prompts/{stage}/{implementation}.md` inside the orchestrator package.
- Project prompt extensions: `workflow/prompts/{stage}.md` in the docs repo; appended to core prompt if present, ignored silently if absent.

Python pre-validates all required paths before any Claude invocation. Missing required files are hard failures.

---

## Change Workflow

Follow this for every change — bugfix, feature, or refactor.

1. Read this file (done).
2. Read the relevant ADR(s) from `docs/adrs/`.
3. Read only the affected module file(s) — not the whole package.
4. Ensure main is current: `git pull origin main`
5. Enter a worktree: call `EnterWorktree` with name `<type>/<short-description>`
6. Make the change and verify (`uv run pytest tests/` from repo root).
7. **ADR gate** — before committing, ask: is this decision hard to reverse, surprising
   without context, and the result of genuine trade-offs? If yes to all three, write an
   ADR first. Use the template at `docs/adrs/_template.md`.
   New ADRs must have YAML frontmatter (`status`, `date`, `affects`). If the decision
   is load-bearing for everyday edits, add an invariant here too (with an ADR reference).

   **Does not warrant an ADR:** simple bug fixes, naming or formatting choices, adding
   tests, dependency updates, documentation changes, performance tweaks that don't
   change observable behaviour or interface contracts.
8. Commit: `git commit -m "type: message"`
9. Push: `git push -u origin <branch>`
10. Open PR — do NOT merge, that is always left to the user:
    `gh pr create --title "<commit message>" --body "<one or two sentence rationale>"`
    PR body: why the change was made, nothing else. No file references, no code snippets — the diff covers what changed. Add inline code comments for anything that warrants reviewer attention.
11. Exit and remove the worktree: call `ExitWorktree` with `action: remove`

---

## Tests

Run `uv run pytest tests/` from the repo root.

---

## Auto-Commit

After each discrete task, open a pull request — do not commit to main directly.

- Pull main and enter a worktree before touching any files: `git pull origin main`, then call `EnterWorktree`
- Add a CHANGELOG.md entry (one line, current date heading) before committing
- Stage files by name — never `git add -A` or `git add .`
- Commit message: conventional format (`fix:`, `feat:`, `chore:`, `docs:`), one sentence, no ticket refs, no emoji
- Push and open PR: `gh pr create --title "<msg>" --body "<one or two sentence rationale — why, not what>"`
- Do not merge — leave that to the user
- Exit and remove the worktree: call `ExitWorktree` with `action: remove`
- "Task complete" = PR is open, tests pass

## Versioning

`.github/workflows/version-tag.yml` auto-tags every push to main:

- `feat!:` / `BREAKING CHANGE` footer → major bump
- `feat:` → minor bump
- everything else → patch bump

No manual tagging needed.

