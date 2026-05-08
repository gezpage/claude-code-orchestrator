# Orchestrator Skill

Delegates to the `orchestrator` CLI. No orchestration logic lives here.

## Commands

### Run a full pipeline

```
orchestrator run \
  --docs-root <path-to-team-hub> \
  --project <project-name> \
  --feature-path <docs-relative-path-to-feature-file> \
  --branch <git-branch-name> \
  [--profile full]
```

### Run a single stage directly

```
orchestrator stage \
  --stage <stage-name> \
  --input <path-to-input-json> \
  --run-folder <path-to-run-folder> \
  --docs-root <path-to-team-hub> \
  --project <project-name> \
  --project-log-path <path-for-logs>
```

### Resume a blocked pipeline

```
orchestrator resume \
  --run-folder <path-to-existing-run-folder> \
  --docs-root <path-to-team-hub>
```

## Required args

All `--docs-root` and `--project` values are validated before any work begins.
Missing paths produce a clear error message — not a Python traceback.

## Notes

- `--profile` defaults to `full` (all 8 stages)
- The alignment stage always pauses for manual review; `resume` continues after it
- Run folders live at `{docs-root}/projects/{project}/workflow/runs/{slug}/{date}-run-{N}/`
