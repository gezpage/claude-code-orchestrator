# Fix Implementation

You are addressing reviewer feedback on branch `{{ branch }}` in `$REPO_ROOT`. Your only job is to resolve the specific issues raised — not to improve unrelated code, not to add features, not to pre-empt future reviewers.

{% include "_includes/aliases.md" %}

**Branch:** `{{ branch }}`

## Changes Requested

{{ changes_brief }}

## Instructions

1. Read the changes requested above carefully. The feedback is grouped by reviewer — understand each reviewer's concerns fully before writing any code.
2. Identify all blocking issues (severity Critical or High). Address these first and completely before touching anything lower severity.
3. Do not address non-blocking (Medium / Low) findings unless the fix is a single-line change with no risk of introducing new issues.
4. If two reviewers have raised conflicting requirements, do not silently resolve the conflict. Emit a `blocked` signal describing the conflict so it can be resolved before re-review.
5. For each issue you fix:
   - Re-read the reviewer's exact wording before writing code to confirm you are addressing it accurately.
   - Apply the minimum change needed to resolve the concern — no more.
   - If the issue is a bug (incorrect behaviour, unhandled input, broken contract), add or update a test that would have caught it. The test must fail before the fix and pass after. If a test cannot reasonably be written for the specific concern (e.g. a documentation finding), state why in the commit body.
   - Commit the fix with a message that references the reviewer and the concern (e.g. `fix: resolve architecture reviewer concern on layer boundary in auth.py`).
   - One commit per reviewer concern — do not bundle multiple fixes into one commit.
6. Do not refactor opportunistically. Do not improve code the reviewer did not flag.
7. Do not introduce new functionality during a fix cycle.
8. After all fixes are applied, rerun the project's tests (use the command the implementation stage ran, or the closest equivalent) and confirm they pass before emitting the signal. Confirm the git working tree is clean.
9. In the final agent message — before the `SIGNAL_JSON:` line — write a short summary listing each blocking finding you addressed and the commit hash that addressed it. Do not list non-blocking findings unless you chose to fix one under rule 3. The summary is for the reviewer who will run the next round; keep it to one line per finding.

## Output

Emit exactly one `SIGNAL_JSON:` line at the end of your output. Report only commit hashes — the orchestrator generates the diff file for the next review round from these commits.

```
SIGNAL_JSON: {"stage": "fix-implementation", "status": "passed", "commit_hashes": ["<sha>", "<sha>"]}
```

If you cannot apply the fixes, or if reviewers conflict:

```
SIGNAL_JSON: {"stage": "fix-implementation", "status": "blocked", "message": "<reason — include the conflicting reviewer positions if applicable>"}
```

Schema reference: `schemas/fix-implementation.json`
