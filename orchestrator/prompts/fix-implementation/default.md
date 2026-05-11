# Fix Implementation

You are addressing reviewer feedback on branch `{{ branch }}` in `{{ repo_root }}`. Your only job is to resolve the specific issues raised — not to improve unrelated code, not to add features, not to pre-empt future reviewers.

**Branch:** `{{ branch }}`
**Repo root:** `{{ repo_root }}`

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
   - Commit the fix with a message that references the reviewer and the concern (e.g. `fix: resolve architecture reviewer concern on layer boundary in auth.py`).
   - One commit per reviewer concern — do not bundle multiple fixes into one commit.
6. Do not refactor opportunistically. Do not improve code the reviewer did not flag.
7. Do not introduce new functionality during a fix cycle.
8. After all fixes are applied, confirm the git working tree is clean before emitting the signal.

## Output

Emit exactly one `SIGNAL_JSON:` line at the end of your output:

```
SIGNAL_JSON: {"stage": "fix-implementation", "status": "passed", "commit_hashes": ["<sha>"], "diff": "<summary of changes>"}
```

If you cannot apply the fixes, or if reviewers conflict:

```
SIGNAL_JSON: {"stage": "fix-implementation", "status": "blocked", "message": "<reason — include the conflicting reviewer positions if applicable>"}
```

Schema reference: `schemas/fix-implementation.json`
