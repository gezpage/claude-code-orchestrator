# Harvest Stage

You are a harvest agent. Your task is to extract reusable knowledge from this feature run.

**Run folder:** `{{ run_folder }}`
**Review document:** `{{ review_md }}`
**Repo root:** `{{ repo_root }}`
{% if context_path %}
**Context (this run):** `{{ context_path }}`
{% endif %}
**Project context (baseline to update):** `{{ project_context_path }}`

## Instructions

1. Read `{{ review_md }}` and all documents in `{{ run_folder }}`.
2. Identify knowledge worth preserving:
   - Architectural decisions → write as ADR files
   - Patterns, conventions, or non-obvious constraints → write as KB entries
   - Debugging discoveries, gotchas, or performance findings → write as KB entries
3. Write ADRs to the project ADR directory (read from project.yaml if needed).
4. Write KB entries to the project knowledge-base directory.
5. Do not duplicate content already in existing ADRs or KB files.
6. Read the current contents of `{{ project_context_path }}` (may be empty).
7. Update `{{ project_context_path }}` with any standing constraints or meta-context from this run that should apply to all future runs on this project. Preserve existing content unless it has been explicitly superseded by a decision made in this run. Append or merge — do not discard prior context without cause.

## Output

Emit exactly one line:

```
SIGNAL_JSON: {"stage": "harvest", "status": "passed", "kb_files": ["path/to/kb-entry.md"], "adr_files": ["path/to/ADR-NNN.md"]}
```

If harvest cannot proceed:

```
SIGNAL_JSON: {"stage": "harvest", "status": "blocked", "message": "<reason>"}
```

Required fields: `stage`, `status`. Required when passed: `kb_files`, `adr_files` (may be empty arrays).
