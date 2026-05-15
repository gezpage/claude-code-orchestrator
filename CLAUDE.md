# Orchestrator — Architectural Invariants

Developer-facing reference. Read before touching any orchestrator code.

---

## Invariants

- **All autonomous stage dispatch goes through an `AgentRunner`.** `run_stage()` calls `runner.run(AgentRunRequest(...))` — it does not invoke `claude` or any CLI directly. New backends are added by implementing the Protocol in `orchestrator/agent_runner/`, not by editing call sites. See ADR-018.

- **`ClaudeCodePrintRunner` always passes `--bare` and `--dangerously-skip-permissions`.** These were the ADR-003 and ADR-012 invariants; they are now invariants of the runner. Removing either flag from the runner breaks unattended stage dispatch and re-enables MCP/hook side effects. See ADR-018.

- **Sterile context is the default for stage runners.** `ClaudeCodePrintRunner` sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` unless a profile opts out with `agent.sterile_context: false`. Ambient auto-memory is not allowed to leak into pipeline runs by default. See ADR-018.

- **The main orchestration session never reads stage output file contents.** `orchestrate.py` receives file paths and status values via signal JSON only. It must not `open()` or `Read` any stage output file. Adding a file read to `orchestrate.py` violates the token-minimisation invariant and will cause unbounded context growth across long pipelines. See ADR-004.

- **All context a downstream stage needs must be surfaced in the signal JSON.** Stage output schemas are designed around this constraint. If a downstream stage appears to need file content from a prior stage, the solution is to add a reference field to the upstream signal JSON — not to read the file in `orchestrate.py`. See ADR-004.

- **`workflow/` paths are fixed convention — do not add config for them.** Python derives all orchestrator paths from `{docs-root}/projects/{project}/workflow/`. Do not introduce a `project.yaml.folders` key or any path override mechanism. See ADR-006.

- **Interactive stages (`mode: interactive`) are dispatched through `run_interactive_stage()` in `run_stage.py` — not `run_stage()`.** Python launches an interactive `claude` session (no `--bare`, no `--dangerously-skip-permissions`), waits for it to exit, then checks for the declared `artifact` file. Interactive stages do not go through the `AgentRunner` seam; the ADR-018 runner invariants apply only to `run_stage()`. See ADR-007.

- **Stage output schemas are the interface contract — they belong to the stage, not the implementation.** All implementations of a stage must satisfy the same schema. See ADR-008.

- **Fix cycles run in the current run folder — do not create a new run.** `_state.yaml`, `review.md`, and all fix-cycle output accumulate in the existing run folder. See ADR-009.

- **The fix cycle limit is 2 and is enforced in `review_cycle.py` via `_MAX_CYCLES`.** Not configurable via `project.yaml`. See ADR-011.

- **The mermaid block in `plan.md` is a projection of the `Graph` in `orchestrator/plan/_graph.py`** — persisted as `_plan_graph.yaml`. All mutations go: load graph → mutate typed objects → save → re-render via `render_block`. Do not parse or rewrite mermaid text with regex; the renderer is the only code that knows mermaid syntax. See ADR-016.

- **`_render.py` materialises `{id}_prompt` and `{id}_panel` partner nodes around each rect-shape stage at render time, plus a single `overview` node before the first stage.** Edge endpoints are rewritten on serialisation (`A → B` becomes `A_panel --> B_prompt`; `Start → first` is split through `overview`; chain edges are broken into per-pair edges). The graph model itself contains no prompt/panel/overview nodes — they exist only in the rendered output. New behaviour around stage edges goes through this rewriting, not through new graph edges. See ADR-020.

- **Deterministic stages (`mode: deterministic`) are dispatched through `run_deterministic_stage()` in `run_stage.py` — not `run_stage()`.** They execute Python in-process and never invoke Claude. The `--bare` / `--dangerously-skip-permissions` invariants apply only to `run_stage()` and have no meaning for deterministic stages. See ADR-017.

- **Toolchain-specific verification logic lives in `orchestrator/verifiers/recipes/` (data) and `orchestrator/verifiers/probes/` (Python).** Orchestration code (`orchestrate.py`, `run_stage.py`, profile parsing) must contain no `if node` / `if go` / `if python` branches. Adding a new ecosystem means adding a recipe and any probes it needs — nothing else. See ADR-017.

- **`verification_status: "failed"` triggers a fix-verification cycle before review.** When a deterministic verification stage returns `verification_status: "failed"`, `orchestrate.py` dispatches a `fix-verification` agent (using the profile's implementation runner) then re-runs verification. If the fix makes no commits or re-verification still fails, the pipeline blocks. Probe failures are resolved here — not in the review fix cycles. See ADR-021.

- **PR creation is a post-pipeline finalisation step, not a profile stage.** It runs only when `create-pr` is true and origin is a recognised GitHub repo. The `pr_draft` Claude stage that produces title/body, plus the `gh pr create` call, execute after the stage loop completes. Failures in this phase log warnings and write a manual-command fallback into `plan.md`; they do not change the pipeline exit status. See ADR-019.

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
5. **Branch housekeeping** — prune branches whose PRs have been merged since the last task:
   ```
   git fetch --prune
   for branch in $(gh pr list --state merged --json headRefName --jq '.[].headRefName'); do
     git push origin --delete "$branch" 2>/dev/null || true
     git branch -D "$branch" 2>/dev/null || true
   done
   ```
   `-D` is safe here because the loop only runs for branches confirmed merged via `gh`. Also run `git worktree prune` to clear any stale worktree entries.
6. Enter a worktree: call `EnterWorktree` with name `<type>/<short-description>`
7. Make the change and verify (`uv run pytest tests/` from repo root).
8. **ADR gate** — before committing, ask: is this decision hard to reverse, surprising
   without context, and the result of genuine trade-offs? If yes to all three, write an
   ADR first. Use the template at `docs/adrs/_template.md`.
   New ADRs must have YAML frontmatter (`status`, `date`, `affects`). If the decision
   is load-bearing for everyday edits, add an invariant here too (with an ADR reference).

   **Does not warrant an ADR:** simple bug fixes, naming or formatting choices, adding
   tests, dependency updates, documentation changes, performance tweaks that don't
   change observable behaviour or interface contracts.
9. Commit: `git commit -m "type: message"`
10. Push: `git push -u origin <branch>`
11. Open PR — do NOT merge, that is always left to the user:
    `gh pr create --title "<commit message>" --body "<one or two sentence rationale>"`
    PR body: why the change was made, nothing else. No file references, no code snippets — the diff covers what changed. Add inline code comments for anything that warrants reviewer attention.
12. Exit and remove the worktree: call `ExitWorktree` with `action: remove`

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

Releases are cut manually via the `Release` workflow (`.github/workflows/release.yml`), triggered from the GitHub Actions UI with `workflow_dispatch`. Merges to `main` do **not** auto-tag. See [ADR-014](docs/adrs/ADR-014-explicit-release-workflow.md).

The release workflow scans all commits between the last `vX.Y.Z` tag and `HEAD` and computes the bump from conventional-commit prefixes:

- `feat!:` / `BREAKING CHANGE` footer (anywhere in the range) → major bump
- `feat:` → minor bump
- `fix:` → patch bump
- `chore:`, `docs:`, `ci:`, `refactor:`, `test:` → no contribution to the bump

If the range contains no `feat!:`/`feat:`/`fix:`/`BREAKING CHANGE` commits, the workflow fails with a clear message — a dispatch is an assertion that there is something to release.

The workflow re-runs the full quality gate (lint, format, type, test, build wheel/sdist, install, `orchestrator --help` smoke) before tagging. On success it pushes the tag and creates a GitHub Release with auto-generated notes.

The lockfile (`uv.lock`) must be committed by developers — CI does not commit it.

