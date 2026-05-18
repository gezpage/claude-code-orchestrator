# Executive Summary Stage

You are writing a post-run executive summary for the developer who launched this pipeline. The audience is *not* a PR reviewer — it is whoever is about to read the run folder and decide what to do next. Keep it concise enough to read in under 3 minutes.

This summary is a *synthesizer and linker*, not a new source of truth. Authoritative status lives in `plan.md`, `_state.yaml`, `review-log.md`, and the verifier artifacts. Your job is to make those readable in one pass — quote and link to them, do not re-derive their verdicts.

## Inputs

- **Run folder:** `{{ run_folder }}`
- **Run plan:** `{{ plan_md_path }}`
- **Feature overview:** `{{ overview_md_path }}`
- **State file:** `{{ state_yaml_path }}`
- **Branch:** `{{ branch }}`
- **Base branch:** `{{ base_branch }}`
- **PR URL (if created):** `{{ pr_url }}`
- **Output target:** `{{ summary_path }}`

## Instructions

1. Read `{{ plan_md_path }}` for the stage-by-stage outcome, timings, commit messages, and any review findings.
2. Read `{{ overview_md_path }}` for the original intent of the feature.
3. Read `{{ state_yaml_path }}` for the authoritative per-stage status, `blocked_at`, and any fix-cycle outcome. If `blocked_at` is set, the pipeline did **not** complete; lead with that.
4. Open the stage subfolders under `{{ run_folder }}` for any output files you need — review summaries, verification logs, QA notes. Do **not** quote them verbatim; synthesise.
5. Write the summary to `{{ summary_path }}` using the structure below. Adapt section headings if any section is empty — never include an empty section just to satisfy the template.

## Required sections

Render exactly this markdown structure, in this order:

```markdown
# Executive Summary — <feature name>

**Status:** <one of: completed, blocked at `<stage>`, incomplete>
**Branch:** `<branch>` → `<base_branch>`
**PR:** <PR URL or "not created">

## Original request

<2–4 sentences synthesised from the feature overview. Do not copy verbatim.>

## What was done

<3–6 bullets summarising the implementation. Each bullet is a concrete change, not a stage name.>

## Requirements checklist

<Bullet list mirroring the acceptance criteria from the feature overview. Mark each with [x] (done), [ ] (not done), or [~] (partial). If the overview has no AC, omit this section.>

## Product readiness

This section is the honesty gate. Fill in each line with what the run actually demonstrated — not what the tests/build implied. Distinguish clearly between *verified internals* and *product usability*; the difference is the point of this section.

- **Verified internals:** <what unit/component tests, typecheck, lint, and build actually cover. Be specific (e.g. "graph rendering, plan loader, signal parser") rather than "tests pass".>
- **Product usability:** <state plainly whether the finished product is usable for its primary purpose by an end user, or whether it is a partially-wired skeleton. "V1 usable" / "demoable only with stubbed data" / "not yet usable end-to-end" are all acceptable answers; "complete" is only acceptable when primary workflow evidence below is present.>
- **Primary workflow evidence:** <quote or link the QA report's "Primary workflow evidence" section. If QA recorded "not exercised", say so here too — do not paraphrase it as "covered by tests".>
- **Skipped / warned verification:** <list any verification commands that were skipped or warned (e.g. "clean-install skipped — no lockfile", "wave verification warned — baseline failures"). Write "none" only when verification status is `passed` with no skipped required-any-of group.>
- **Unresolved blockers:** <reviewer findings that were not addressed, alignment items still unresolved, placeholder adapters on the primary path that survived. Write "none" only when no such items exist.>

## Commands & tests

- **Commands run:** <list of significant commands executed by the pipeline — verification, test runners, linters. Skip trivial git/`mkdir` plumbing.>
- **Tests:** <passed/failed counts and toolchain if known from verification output; otherwise "not run">
- **Deterministic verification:** <state honestly whether deterministic verification actually ran. Use exactly one of: "passed", "failed", "skipped — <reason>", "warned — <reason>", or "not run". A stage that selected a recipe but ran zero eligible commands is "skipped" — do not collapse that into "passed". A `warned` policy outcome stays "warned" even if the pipeline continued.>

## Reviews

<For each review stage that ran: reviewer name → verdict (approved / changes-requested / blocked). One line each. Omit the section if no reviews ran.>

## Issues fixed during the run

<Bullets for each fix cycle that produced commits. Reference the reviewer or finding that triggered the fix. Omit if no fix cycles ran.>

## Open risks & known limitations

<Bullets. Surface anything reviewers flagged but didn't block on, deferred TODOs in commits, or follow-up items called out in plan.md. If nothing notable, write "None identified".>

## Recommended next actions

<2–5 numbered bullets. Concrete, in priority order. If the pipeline is blocked, the first action is what unblocks it.>
```

## Constraints

- Total length: **target under 400 words**. Hard cap 700 words.
- Plain markdown only — no HTML, no embedded images, no code fences except for inline shell commands.
- Do not invent facts. If you cannot determine something from the run artifacts, say so or omit the bullet.
- Do not override or contradict the status recorded in `_state.yaml` / `plan.md`. The `Status:` line at the top must match whatever those files say.
- Link to underlying artifacts (e.g. `[review log](review/review-log.md)`, `[plan](plan.md)`) rather than restating their contents at length.
- Do not include the `Co-Authored-By` trailer or any signature.
- **No overclaiming.** Do not describe the change as "production ready", "fully tested", "V1 ready", or "complete" unless: deterministic verification passed (not skipped, not warned), every requirement is checked `[x]`, no reviewer left blocking findings, and the QA report records primary workflow evidence for any user-facing surface. Internal tests passing is not the same thing as the product being usable — keep the two separate in `## Product readiness`. If verification was skipped or warned, or reviewers flagged unresolved risks, say so in `## Product readiness` and `## Open risks & known limitations` — do not bury it.
- **Surface unresolved alignment items.** If alignment recorded accepted assumptions or unresolved-remaining items, list them under `## Open risks & known limitations`. An accepted assumption is not a failure, but it must be visible so the reader knows what was *not* confirmed.

## Signal

After writing the file, end your output with exactly one line:

```
SIGNAL_JSON: {"stage": "executive_summary", "status": "passed", "summary_path": "{{ summary_path }}"}
```

If you cannot write the summary (e.g. plan.md missing or unreadable), emit:

```
SIGNAL_JSON: {"stage": "executive_summary", "status": "blocked", "message": "<reason>"}
```
