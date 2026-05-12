---
status: accepted
date: 2026-05-07
affects: [paths.py]
---

# ADR-006: `workflow/` as Fixed Directory Convention

**Status:** Accepted
**Date:** 2026-05-07

## Context

The existing orchestrator uses configurable folder names in `project.yaml.folders`, requiring each project to declare where profiles, prompts, and run folders live. This creates misconfiguration risk and forces Python to perform folder enumeration or validation against config values.

A fixed convention eliminates both problems: Python derives all paths deterministically from `{docs-root}/projects/{project}/workflow/` without consulting config.

## Decision

All orchestrator-specific folders under a project consolidate into a fixed `workflow/` directory:

```
projects/{project}/workflow/
  profiles/       — profile YAML files
  prompts/        — project-specific prompt extensions ({stage}.md)
  runs/           — run folders, keyed by feature slug
    {feature-slug}/
      {YYYY-MM-DD}-run-{N}/
```

`project.yaml.folders` config is eliminated. Subpaths are conventional, not configurable. The same applies to other docs folders (knowledge-base, adrs, etc.) — Python uses conventional paths throughout.

Feature categorisation folders (feature-requests, bugs, enhancements) are not imposed by the orchestrator. The orchestrator accepts any path that contains an `overview.md`; projects organise work items however they like.

## Consequences

- Zero misconfiguration risk from folder naming: Python always knows exactly where to look.
- Path derivation in Python is simple and testable with no config dependency.
- Every project adopting the orchestrator must use the `workflow/{profiles|prompts|runs}` layout — zero flexibility for projects with existing folder conventions.
- Migrating existing runs to the new layout requires a one-time rename per project.
- The convention is hard to reverse once multiple projects are on-boarded.
