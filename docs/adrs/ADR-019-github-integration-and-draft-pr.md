---
status: accepted
date: 2026-05-14
affects: [orchestrator/_git_setup.py, orchestrator/_github.py, orchestrator/_prompts.py, orchestrator/_cli_tui.py, orchestrator/cli.py, orchestrator/orchestrate.py, orchestrator/plan/_helpers.py, orchestrator/plan/_update.py, orchestrator/prompts/pr_draft/, orchestrator/schemas/pr_draft.json]
---

# ADR-019: GitHub Integration and Draft PR Finalisation

**Status:** Accepted
**Date:** 2026-05-14

## Context

Before this change, the orchestrator validated only that `project.yaml.repo-root` existed and was a git repository, then dropped straight into the pipeline. Two friction points kept recurring:

1. The base branch the implementation branch forks from was implicit (whatever HEAD pointed at), and there was no upstream sync — easy to branch from stale main without noticing.
2. After the pipeline finished, the developer still had to push the branch and open a PR by hand, copy/pasting context from `plan.md` and `overview.md`.

The orchestrator already commits per slice and produces a structured run artifact (`plan.md`). The missing step is the bridge from "branch with commits" to "draft PR awaiting human review."

In addition, the CLI required `--docs-root`, `--project`, `--feature-path`, `--branch`, and optionally `--profile` to be supplied as flags. There were no interactive prompts. New users had to remember the full invocation and the docs-root layout up front.

## Decision

Add a richer pre-flight and a post-pipeline finalisation step. Specifically:

1. **TUI for all `run` inputs.** All current required flags become optional. When stdin is a TTY and a flag is missing, prompt for it via `questionary`. When non-TTY (CI), fall back to persisted `project.yaml` values, then fail with a clear error listing missing inputs. Flags always win over prompts and persistence.

2. **Base branch sync.** Resolve the base branch (flag → `project.yaml.base-branch` → prompt with default `main`). Before creating the implementation branch, `git fetch origin && git checkout <base> && git pull --ff-only origin <base>`, then `git checkout -b <impl>`. The working-tree-clean check is preserved.

3. **GitHub integration via `gh` CLI.** Detect whether `origin` points to GitHub. If yes and the user wants a PR, validate that `gh` is installed and authenticated. If origin is missing or non-GitHub and the user wants a PR, present three options: (A) link an existing GitHub URL (`git remote add origin <url>`), (B) create a new GitHub repo via `gh repo create` (collect name/visibility/description), or (C) continue without the PR feature. None of these are forced — the user can always opt out.

4. **Draft PR on completion.** When `create-pr` is true and origin is GitHub, after the last stage passes the orchestrator: (a) runs a small `pr_draft` Claude stage that reads `plan.md` and `overview.md` and emits `{title, body}`; (b) pushes the implementation branch to `origin`; (c) calls `gh pr create --draft`; (d) replaces the "Draft PR" notice line in `plan.md` with the PR URL.

5. **Persistence.** `base-branch` and `create-pr` are written back to `project.yaml` on first resolution and reused on subsequent runs unless flags override.

6. **Independence from pipeline status.** PR creation runs only when the pipeline has already passed all stages. Any failure during the finalisation phase (gh missing, network error, auth expired) is logged as a warning, written into `plan.md` as a fallback notice with the manual `gh pr create` command, and does not change the pipeline exit code.

### Why `gh` rather than the GitHub REST API

`gh` is already authenticated in developer shells (the same place the user typed `orchestrator run`). Shelling out reuses that auth, avoids embedding a GitHub HTTP client, and keeps the integration thin. The only operations needed are `gh auth status`, `gh repo create`, and `gh pr create` — all stable, scriptable subcommands.

### Why post-pipeline, not a profile stage

Draft PR creation is conditional (`create-pr` may be false) and profile-independent (the same finalisation behaviour applies whether the profile is `full`, `minimal`, or a project-specific YAML). Adding it as a stage would mean either every profile carries an opt-in stage, or the orchestrator silently injects a stage into the user's profile — both awkward. Treating it as a finalisation phase outside the stage loop keeps profiles describing pipeline work and keeps PR creation orthogonal.

### Why `questionary` over `click.prompt`

The pre-flight needs multi-choice selects (project picker, feature-path picker, "link/create/skip" branch for missing origin). `click.prompt` can do free text and confirm, but multi-choice requires custom validation and offers no keyboard navigation. `questionary` provides both, with consistent UX, at the cost of one new dependency.

## Consequences

- `project.yaml` may now carry two new optional fields: `base-branch` and `create-pr`. They are not required; absence means "ask".
- The orchestrator now optionally depends on the `gh` CLI being on `PATH`. The dependency only matters when the user opts into PR creation. Users without `gh` are pointed at install instructions and can opt out.
- The pipeline now has a "finalisation phase" after the stage loop. This phase is intentionally fenced off — its failures are warnings, never pipeline-level failures.
- The `pr_draft` stage is a real Claude stage with its own prompt template (`orchestrator/prompts/pr_draft/default.md`) and JSON schema (`orchestrator/schemas/pr_draft.json`). It is invoked through the existing `AgentRunner` machinery; ADR-018 invariants apply.
- All current required CLI flags become optional. Existing scripted invocations that pass every flag continue to work unchanged.
- Plan.md gains a single PR-notice line near the top (between the run header and the mermaid block), updated in-place via the same thread-safe lock that guards every other plan mutation. The mermaid block surgery in `replace_mermaid_block` is unaffected because the notice lives outside the fenced block.
- Non-TTY runs that omit any required input fail fast with a structured error rather than hanging or producing a `PromptNotAvailable` traceback.
