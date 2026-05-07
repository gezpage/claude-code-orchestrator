# Orchestrator — Architectural Invariants

Developer-facing reference. Read before touching any orchestrator code.

---

## Invariants

- **`--dangerously-skip-permissions` is mandatory in every `run_stage()` call.** Not a shortcut — it is the documented use case for unattended, trusted pipeline execution. Removing it breaks all unattended stage dispatch.

- **The main orchestration session never reads stage output file contents.** `orchestrate.py` receives file paths and status values via signal JSON only. It must not `open()` or `Read` any stage output file. Adding a file read to `orchestrate.py` violates the token-minimisation invariant and will cause unbounded context growth across long pipelines.

- **All context a downstream stage needs must be surfaced in the signal JSON.** Stage output schemas are designed around this constraint. If a downstream stage appears to need file content from a prior stage, the solution is to add a reference field to the upstream signal JSON — not to read the file in `orchestrate.py`.

- **`workflow/` paths are fixed convention — do not add config for them.** Python derives all orchestrator paths from `{docs-root}/projects/{project}/workflow/`. Do not introduce a `project.yaml.folders` key or any path override mechanism.

- **Alignment is not dispatched through `run_stage.py`.** When Python encounters the alignment stage in the profile, it pauses and surfaces manual instructions. It does not call `subprocess.run()` for alignment.

- **Stage output schemas are the interface contract — they belong to the stage, not the implementation.** All implementations of a stage must satisfy the same schema.

- **Fix cycles run in the current run folder — do not create a new run.** `_state.yaml`, `review.md`, and all fix-cycle output accumulate in the existing run folder.

- **The fix cycle limit is 2 and is enforced in `orchestrate.py`.** Not configurable via `project.yaml`.

---

## Path Resolution Rules

- `{docs-root}` — passed as `--docs-root` at runtime; required, no default.
- `{docs-root}/projects/{project}/project.yaml` — project config; `repo-root` field must point to an existing path or Python fails immediately.
- `{docs-root}/projects/{project}/workflow/profiles/` — profile YAML files.
- `{docs-root}/projects/{project}/workflow/prompts/` — project prompt extensions.
- `{docs-root}/projects/{project}/workflow/runs/{feature-slug}/{YYYY-MM-DD}-run-{N}/` — run folder.
- Core stage prompts: `prompts/{stage}/{implementation}.md` inside the orchestrator package.
- Project prompt extensions: `workflow/prompts/{stage}.md` in the docs repo; appended to core prompt if present, ignored silently if absent.

Python pre-validates all required paths before any Claude invocation. Missing required files are hard failures.
