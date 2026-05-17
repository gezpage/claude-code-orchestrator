# Orchestrator — Architectural Invariants

Developer-facing reference. Read before touching any orchestrator code.

---

## Invariants

- **All autonomous stage dispatch goes through an `AgentRunner`.** `run_stage()` calls `runner.run(AgentRunRequest(...))` — it does not invoke `claude` or any CLI directly. New backends are added by implementing the Protocol in `orchestrator/agent_runner/`, not by editing call sites. See ADR-018.

- **`ClaudeCodeRunner` dispatches with `--permission-mode auto` and never passes `--dangerously-skip-permissions`, `--bare`, or `-p`.** `--permission-mode auto` keeps Claude's permission system engaged (next-most-permissive mode short of `bypassPermissions`); ADR-025 supersedes the historical ADR-003 invariant that mandated `--dangerously-skip-permissions`. `--bare` and `-p` were the ADR-012 / ADR-018 invariants but were **reversed** in ADR-022: `--bare` forces `ANTHROPIC_API_KEY`-only auth (excluding OAuth/keychain) and `-p` is redundant under piped stdout. The runner also strips `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the forwarded env so a stale external key cannot override keychain auth. See ADR-022 and ADR-025.

- **Sterile context is the default for stage runners.** The Claude runner sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and passes `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` unless a profile opts out with `agent.sterile_context: false`. Ambient auto-memory and the user's globally-configured MCP servers are not allowed to leak into pipeline runs by default. See ADR-018 and ADR-023.

- **The main orchestration session never reads stage output file contents.** `orchestrate.py` receives file paths and status values via signal JSON only. It must not `open()` or `Read` any stage output file. Adding a file read to `orchestrate.py` violates the token-minimisation invariant and will cause unbounded context growth across long pipelines. See ADR-004.

- **All context a downstream stage needs must be surfaced in the signal JSON.** Stage output schemas are designed around this constraint. If a downstream stage appears to need file content from a prior stage, the solution is to add a reference field to the upstream signal JSON — not to read the file in `orchestrate.py`. See ADR-004.

- **`workflow/` paths are fixed convention — do not add config for them.** Python derives all orchestrator paths from `{docs-root}/projects/{project}/workflow/`. Do not introduce a `project.yaml.folders` key or any path override mechanism. See ADR-006.

- **Interactive stages (`mode: interactive`) are dispatched through `run_interactive_stage()` in `run_stage.py` — not `run_stage()`.** Python launches an interactive `claude` session (no `--permission-mode`, no `--bare`), waits for it to exit, then checks for the declared `artifact` file. Interactive stages do not go through the `AgentRunner` seam; the ADR-018 runner invariants apply only to `run_stage()`. See ADR-007.

- **Stage output schemas are the interface contract — they belong to the stage, not the implementation.** All implementations of a stage must satisfy the same schema. See ADR-008.

- **Fix cycles run in the current run folder — do not create a new run.** `_state.yaml`, `review.md`, and all fix-cycle output accumulate in the existing run folder. See ADR-009.

- **The fix cycle limit is 2 and is enforced in `review_cycle.py` via `_MAX_CYCLES`.** Not configurable via `project.yaml`. See ADR-011.

- **The mermaid block in `plan.md` is a projection of the `Graph` in `orchestrator/plan/_graph.py`** — persisted as `_plan_graph.yaml`. All mutations go: load graph → mutate typed objects → save → re-render via `render_block`. Do not parse or rewrite mermaid text with regex; the renderer is the only code that knows mermaid syntax. See ADR-016.

- **`_render.py` materialises `{id}_prompt` and `{id}_panel` partner nodes around each rect-shape stage at render time, plus a single `overview` node before the first stage.** Edge endpoints are rewritten on serialisation (`A → B` becomes `A_panel --> B_prompt`; `Start → first` is split through `overview`; chain edges are broken into per-pair edges). The graph model itself contains no prompt/panel/overview nodes — they exist only in the rendered output. New behaviour around stage edges goes through this rewriting, not through new graph edges. See ADR-020.

- **Deterministic stages (`mode: deterministic`) are dispatched through `run_deterministic_stage()` in `run_stage.py` — not `run_stage()`.** They execute Python in-process and never invoke Claude. The runner CLI invariants (`--permission-mode auto`, sterile context, etc.) apply only to `run_stage()` and have no meaning for deterministic stages. See ADR-017.

- **Toolchain-specific verification logic lives in `orchestrator/verifiers/recipes/` (data) and `orchestrator/verifiers/probes/` (Python).** Orchestration code (`orchestrate.py`, `run_stage.py`, profile parsing) must contain no `if node` / `if go` / `if python` branches. Adding a new ecosystem means adding a recipe and any probes it needs — nothing else. See ADR-017.

- **`verification_status: "failed"` triggers a fix-verification cycle before review.** When a deterministic verification stage returns `verification_status: "failed"`, `orchestrate.py` dispatches a `fix-verification` agent (using the profile's implementation runner) then re-runs verification. If the fix makes no commits or re-verification still fails, the pipeline blocks. Probe failures are resolved here — not in the review fix cycles. See ADR-021.

- **Post-pipeline finalisation steps run outside the profile loop, swallow their own failures, and never change the pipeline exit status.** Two such steps exist today, both finalisation stages dispatched after the stage loop via the profile's resolved agent (Claude by default, Codex for codex-only profiles). **PR creation** (`_finalize_pr`) runs only when `create-pr` is true and origin is a recognised GitHub repo; failures log warnings and write a manual-command fallback into `plan.md` (ADR-019). **Executive summary** (`_finalize_summary`) is always-on: the stage loop plus PR finalisation are wrapped in `try / finally`, and the summary fires from the `finally` on every exit path — pass, fail, or blocked — writing `executive_summary.md` to the run folder root (ADR-028). The summary is a synthesizer and linker only; authoritative status lives in `plan.md`, `_state.yaml`, and review logs. New finalisation steps must follow the same rules.

- **`run_stage()` passes a `progress_callback` to every `AgentRunner.run()` call.** When the runner supports streaming (the Claude runner does), each parsed event becomes one INFO line in `run.log` so long-running stages emit live "tool X / text Y" breadcrumbs instead of going silent. Callbacks are best-effort — runners must swallow callback exceptions so a logger glitch cannot abort a stage. See ADR-024.

- **`plan.md` status aggregation goes through `worst_status` from `orchestrator/plan/_constants`.** The precedence ordering is `failed > blocked > changes-requested > in_progress > passed > skipped > pending` (lower wins). Concretely: round-1 review sub-nodes are re-stamped via `resolve_review_subnode_statuses` after a fix cycle so they cannot contradict the final verdict; the init-time PR node is flipped via `mark_pr_blocked` when the pipeline fails before finalisation; the panel-body fallback returns the table value (passed → `"done"`) so a passed stage never surfaces as `"pending"`. Adding a new ad-hoc precedence rule instead of using `worst_status` violates this invariant. See ADR-026.

- **Domain-language reconciliation is append-only and lives in `orchestrator/glossary.py`.** When a project opts in via `project.yaml` `domain_language.path`, the canonical glossary in the target codebase is the source of truth. Stages read a run-local copy materialised at run start; only the post-harvest `_reconcile_glossary` step in `orchestrate.py` writes back, and only via `glossary.reconcile`, which appends new terms, leaves existing definitions verbatim, and records conflicts in `$RUN_FOLDER/glossary-reconciliation.md` rather than overwriting. The harvest agent proposes terms via `proposed_glossary_terms` in its signal — it must never edit the canonical file directly. Failures during reconciliation are logged and never change the pipeline exit status. See ADR-027.

- **Wave-level deterministic verification is keyed off `StageConfig.wave_verification` — never off profile or stage name.** Stages with `expansion: slices` default to `wave_verification: {enabled: true, on_failure: warn}`; `_maybe_run_wave_verification` in `orchestrate.py` is called from `_dispatch_slices` after each slice group merges, writing artifacts under `wave-verification/wave-{N}/` via the existing verifier engine. Policy values are `warn` (default — log and continue), `block` (return a blocked signal so the pipeline halts at the wave boundary), and `fix_then_retry` (dispatch the `fix-verification` agent then re-verify under `wave-verification/wave-{N}/retry/`; on still-failing retry, behave as `warn`). Adding profile-name branching in orchestration code to trigger this hook violates the invariant. See ADR-030.

- **Slice completion and wave integration health are distinct graph nodes.** When wave verification is enabled, `_expand_slices` inserts a `wave_verify_{N}` deterministic node after each wave so per-slice `impl_{N}` nodes carry only local-completion status while `wave_verify_{N}` nodes carry the merged-branch verdict. `_maybe_run_wave_verification` stamps `wave_verify_{N}` as `blocked` on any failed integration check — even under `warn` / `fix_then_retry` policy where the pipeline continues — so a passing slice can never visually imply repo health. Collapsing the two concepts back into a single node (e.g. by stamping the slice node with the wave verdict) violates this invariant. See ADR-031.

- **Wave-verification policy gates on `net_new_status` — never on `verification_status`.** When `wave_verification.enabled` is true, `_maybe_capture_wave_baseline` in `orchestrate.py` runs the verifier engine against the pristine integration branch (before any slice has touched it) and writes the report to `run_folder/baseline-verification/verify.json`. Each subsequent wave verifier call passes that file via `verify(baseline_path=...)`, which classifies every failure as `baseline` (already failing pre-pipeline) or `net_new` (newly introduced). The `on_failure: block | fix_then_retry` policies apply only when `net_new_status == "failed"`; baseline-only failures always warn and continue. Missing or unreadable baselines silently fall back to no-classification so `net_new_status` mirrors `verification_status` and the pre-ADR-033 behaviour is preserved. The baseline capture is idempotent (resumed runs do not overwrite it) and best-effort (a failed capture is logged, not raised). Gating `block`/`fix_then_retry` on raw `verification_status` again violates this invariant. See ADR-033.

- **Discovery unresolved items are alignment inputs — not pipeline blockers.** Discovery surfaces `unresolved_questions`, `risks`, and `assumptions_needed` as string arrays on its signal (tracks aggregate these across the fan-out). Discovery prompts must reserve `status: blocked` for "cannot proceed" cases (missing overview, unreadable inputs) — finding an unresolved decision is not one of those cases. Alignment resolves each item by decision, by adopting an explicit assumption, or by deferring, and reports back via `accepted_assumptions` and `unresolved_remaining`. `_apply_alignment_policy` in `orchestrate.py` is the single gate that converts a non-empty `unresolved_remaining` into a pipeline halt — and only when the stage's `alignment_policy.on_unresolved` is `block` (default is `warn`). Adding pipeline-halt logic anywhere else for discovery/alignment unresolved items violates this invariant. See ADR-032.

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

