# Fix Verification

You are fixing verification failures on branch `{{ branch }}` in `$REPO_ROOT`. The automated verification stage found issues that must be resolved before the code can proceed to review.

{% include "_includes/aliases.md" %}

**Branch:** `{{ branch }}`

## Verification Report

Read the full report at `{{ verify_md_path }}`. It records which commands passed or were skipped, and the findings of any failed probes.

The **Probes** section is your primary target: each failed probe names the specific issue and provides the exact evidence (e.g. the offending script value). The probe output is authoritative — resolve the root cause; do not work around it.

## Instructions

1. Read the verification report at `{{ verify_md_path }}`.
2. Read `{{ verify_json_path }}` for machine-readable probe findings if you need the raw data.
3. For each failed probe, apply the minimum fix needed to resolve it. Common cases:
   - **No-op lint script** (`"lint": "echo add eslint"`): install the linter as a dev dependency (`npm install --save-dev eslint`) and replace the script with a command that exits non-zero on violations (e.g. `eslint .`). Add a minimal config (`.eslintrc.json`) if the linter requires one to run.
   - **No-op or stub build/typecheck script**: replace with the correct command for the project's toolchain.
4. Do not change passing commands or unrelated scripts.
5. Do not refactor code the probe did not flag.
6. After all fixes are applied, confirm the git working tree is clean before emitting the signal.
7. One commit per probe failure resolved. Use a message that references the probe and the fix (e.g. `fix: replace no-op lint script with eslint`).

## Output

Emit exactly one `SIGNAL_JSON:` line at the end of your output:

```
SIGNAL_JSON: {"stage": "fix-verification", "status": "passed", "commit_hashes": ["<sha>"]}
```

If you cannot apply the fixes:

```
SIGNAL_JSON: {"stage": "fix-verification", "status": "blocked", "message": "<reason>"}
```

Schema reference: `schemas/fix-verification.json`
