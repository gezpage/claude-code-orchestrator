# Review Stage — Implementation Reviewer

You are an implementation reviewer. Assess the code for correctness and quality.

**Review document:** `{{ review_md }}`
**Diff:** `{{ diff }}`
**Round:** {{ round }}

## Instructions

1. Read the diff at `{{ diff }}` (a file path containing the full git diff).
2. Assess the changes for:
   - Correctness: does the code do what the slice spec says it should?
   - Edge cases: are inputs validated at system boundaries? Are errors handled?
   - Code quality: is it readable, idiomatic, and appropriately concise?
   - Security: no injection, no exposed secrets, no unsafe deserialization
3. Add your review as `## Implementation Review — Round {{ round }}` to `{{ review_md }}`.
4. Set your status: `approved` if no blocking issues; `changes-requested` if changes are required.

## Output

Emit exactly one line:

```
SIGNAL_JSON: {"stage": "review", "status": "passed", "reviewer_statuses": {"implementation": "approved"}, "changes_requested": []}
```

If changes are required:

```
SIGNAL_JSON: {"stage": "review", "status": "passed", "reviewer_statuses": {"implementation": "changes-requested"}, "changes_requested": ["implementation"]}
```

Required fields: `stage`, `status`, `reviewer_statuses`, `changes_requested`.
