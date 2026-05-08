# Fix Implementation

You are addressing reviewer feedback. Apply targeted fixes to the code on branch
`{{ branch }}` in `{{ repo_root }}` to resolve the changes requested below.

## Changes Requested

{{ changes_brief }}

## Instructions

1. Read the changes-requested sections above carefully.
2. Identify the specific files and lines that need to change.
3. Apply the minimum changes needed to satisfy each reviewer concern.
4. Commit your changes with a descriptive message referencing the review round.
5. Do not introduce unrelated changes.

## Output

Emit exactly one `SIGNAL_JSON:` line at the end of your output:

```
SIGNAL_JSON: {"stage": "fix-implementation", "status": "passed", "commit_hashes": ["<sha>"], "diff": "<summary of changes>"}
```

If you cannot apply the fixes, emit:

```
SIGNAL_JSON: {"stage": "fix-implementation", "status": "blocked", "message": "<reason>"}
```

Schema reference: `schemas/fix-implementation.json`
