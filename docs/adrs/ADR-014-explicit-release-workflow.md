---
status: accepted
date: 2026-05-13
affects: [.github/workflows/version-tag.yml, README.md, CLAUDE.md]
---

# ADR-014: Explicit, Manually-Dispatched Release Workflow

**Status:** Accepted
**Date:** 2026-05-13

## Context

The original `version-tag.yml` workflow auto-tagged every push to `main` whose commit message began with `feat!:`, `feat:`, `fix:`, or carried a `BREAKING CHANGE` footer. The intent was to keep releases low-friction: any merge of a release-bearing PR produced a new tag immediately, with no maintainer action needed.

In practice this coupled "merged a fix" to "shipped a release" in a way that became uncomfortable as the project approached OSS launch readiness (milestone M1, [issue #42](https://github.com/gezpage/claude-code-orchestrator/issues/42)):

- A single typo-fix PR with a `fix:` message produced a patch tag visible to anyone watching releases.
- A batch of merged PRs each produced their own tag, fragmenting what should have been one release.
- The decision to release was made by whoever phrased the most recent commit message — not by a maintainer reviewing the state of `main`.
- The auto-tag job had `contents: write` permission and ran on every push, widening the blast radius if a workflow step or its dependencies were ever compromised.
- Nothing gated tag creation on the package actually building and installing cleanly; the gate was the upstream CI run on the merge commit, which is logically separate from "this is releasable".

Alternatives considered:

- **Keep auto-tag, narrow the trigger further** (e.g., only `feat:`/`feat!:`). Reduces noise but does not address the "who decided to release" problem, and still couples merge to release.
- **Tag-push-driven release.** Maintainer pushes a `vX.Y.Z` tag from their machine; the workflow runs gates and creates the GitHub Release. Clean, but moves version computation off CI and makes it easy to forget to push the tag, or to push one without the gates having passed.
- **Manual `workflow_dispatch`.** Maintainer fires the release from the Actions UI. Version is still computed from conventional-commit messages between the last tag and `HEAD`, so commit hygiene continues to drive version bumps — but the *act* of releasing is a deliberate, audited button press.

## Decision

Replace the push-triggered `version-tag.yml` with a `workflow_dispatch`-only release workflow (`release.yml`):

- **Trigger:** `workflow_dispatch` only. The `push: main` trigger is removed entirely. There is no automatic path from a merge to a tag.
- **Version computation:** scan all commits between the most recent `vX.Y.Z` tag and `HEAD` for conventional-commit bump signals. `feat!:` or a `BREAKING CHANGE` footer in any commit in the range → major bump. Otherwise `feat:` → minor. Otherwise `fix:` → patch. If none of those signals appear in the range, the workflow **fails with a clear message** rather than silently no-op'ing — a dispatch is a maintainer assertion that there is something to release, and "nothing to release" is a real error worth surfacing.
- **Gates inside the release workflow:** the workflow re-runs the full `ci.yml` quality gate (`ruff check`, `ruff format --check`, `mypy orchestrator/`, `pytest tests/`), then builds the wheel and sdist with `uv build`, installs the wheel with `pip`, and runs `orchestrator --help` as a smoke test. All gates must pass before the tag is created. This duplicates work the CI run already did, but makes the release workflow self-contained: a release proves the released commit passes every gate at release time, independent of whether earlier CI runs were green.
- **Outputs:** a `vX.Y.Z` git tag and a GitHub Release. Release notes are auto-generated from the commit messages in the released range using `gh release create --generate-notes`.
- **Documentation:** the release process is documented in a "Releasing" section in `README.md`, and the "Versioning" section in `CLAUDE.md` is updated to describe the manual flow.

The `feat!:`/`feat:`/`fix:` conventional-commit convention is preserved — it still drives the bump computation. What changes is who decides *when* the computation runs.

## Consequences

- Releases become a deliberate maintainer action: the act of cutting a release is now visible in the Actions tab as a named, audited event.
- A merge to `main` produces no tag and no release. CHANGELOG entries continue to live under `[Unreleased]` until a maintainer dispatches a release, at which point they describe what was released.
- Multiple merged PRs roll up into a single release. Version bump is determined by the strongest signal in the range (any `feat!:` → major regardless of how many `fix:` commits also landed).
- The release workflow's gates re-run on the released commit. This adds ~2–3 minutes of CI time per release in exchange for a self-contained release process that does not depend on the freshness of an earlier CI run.
- Maintainers must remember to dispatch the release after merging. There is no automation that prompts them. This is the intended trade-off — "I forgot to release" is a recoverable error; "we shipped a release we didn't mean to" is harder to walk back.
- If a maintainer dispatches a release with no `feat:`/`fix:`/`feat!:` commits in the range, the workflow fails loudly rather than tagging with no bump or silently exiting. This forces an explicit acknowledgement of what would have been released.
- The `contents: write` permission is still required to push the tag and create the Release, but now only runs when explicitly invoked rather than on every push.
- Future change: if PyPI publishing is added, it becomes a job that depends on the tag job in the same workflow file. The gating gates apply to publishing too.
