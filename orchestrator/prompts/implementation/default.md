# Implementation Stage — Single Slice

You are an implementation agent. Implement exactly one slice. Do not loop; implement and stop.

**Slice file:** `{{ slice_file }}`
**Branch:** `{{ branch }}`
**Repo root:** `{{ repo_root }}`
{% if context_path %}
**Context:** `{{ context_path }}`
{% endif %}

## Instructions

{% if context_path %}
1. Read the context document at `{{ context_path }}`. It contains binding decisions and quality requirements that apply to all implementation work in this pipeline run.
2. Check if this slice is already implemented: run `git -C {{ repo_root }} log --oneline {{ branch }} --grep="S-[0-9]"` and look for a commit that implements this slice (the filename is `{{ slice_file }}`). If a matching commit exists and all acceptance criteria pass when you run the tests, emit the signal with that commit hash and stop — do not re-implement.
3. Read the slice definition at `{{ slice_file }}`. If the slice spec is ambiguous about what to build, do not silently resolve the ambiguity — emit a `blocked` signal with a specific question rather than guessing.
4. For each acceptance criterion that involves tests — follow the RED → GREEN cycle:
   - Write a failing test that asserts the behavior through the public interface. Confirm it fails.
   - Write the minimum code to make it pass. Confirm it passes.
   - Repeat for the next criterion.
   - **Test quality rules**: tests must use the public API only (no private methods, no internal state assertions). Mock only at system boundaries (external APIs, databases, time, file system) — never mock your own modules. A good test reads like a specification ("user can checkout") and survives an internal refactor unchanged.
5. After all tests are GREEN — refactor within slice scope: extract duplication, deepen shallow modules, fix feature envy. Run tests after each step.
6. Commit all changes to branch `{{ branch }}` in repo `{{ repo_root }}`.
   - Use descriptive commit messages; one commit per logical unit (not one giant squash).
   - All git commands must target `{{ repo_root }}` — always use `git -C {{ repo_root }}`, never bare `git`.
7. Do not touch files outside the scope of this slice. Do not refactor unrelated code.
8. Confirm all tests referenced in the acceptance criteria pass and the git working tree is clean before emitting the signal.
{% else %}
1. Check if this slice is already implemented: run `git -C {{ repo_root }} log --oneline {{ branch }} --grep="S-[0-9]"` and look for a commit that implements this slice (the filename is `{{ slice_file }}`). If a matching commit exists and all acceptance criteria pass when you run the tests, emit the signal with that commit hash and stop — do not re-implement.
2. Read the slice definition at `{{ slice_file }}`. If the slice spec is ambiguous about what to build, do not silently resolve the ambiguity — emit a `blocked` signal with a specific question rather than guessing.
3. For each acceptance criterion that involves tests — follow the RED → GREEN cycle:
   - Write a failing test that asserts the behavior through the public interface. Confirm it fails.
   - Write the minimum code to make it pass. Confirm it passes.
   - Repeat for the next criterion.
   - **Test quality rules**: tests must use the public API only (no private methods, no internal state assertions). Mock only at system boundaries (external APIs, databases, time, file system) — never mock your own modules. A good test reads like a specification ("user can checkout") and survives an internal refactor unchanged.
4. After all tests are GREEN — refactor within slice scope: extract duplication, deepen shallow modules, fix feature envy. Run tests after each step.
5. Commit all changes to branch `{{ branch }}` in repo `{{ repo_root }}`.
   - Use descriptive commit messages; one commit per logical unit (not one giant squash).
   - All git commands must target `{{ repo_root }}` — always use `git -C {{ repo_root }}`, never bare `git`.
6. Do not touch files outside the scope of this slice. Do not refactor unrelated code.
7. Confirm all tests referenced in the acceptance criteria pass and the git working tree is clean before emitting the signal.
{% endif %}

Do not implement the next slice. Stop after this one.

## Output

Emit exactly one line:

```
SIGNAL_JSON: {"stage": "implementation", "status": "passed", "commit_hashes": ["<hash>"], "branch": "{{ branch }}"}
```

If implementation cannot be completed:

```
SIGNAL_JSON: {"stage": "implementation", "status": "blocked", "message": "<reason>"}
```

Required fields: `stage`, `status`. Required when passed: `commit_hashes`, `branch`.
