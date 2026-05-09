# Alignment Stage — Interactive Mode

<!-- THIS FILE IS NEVER DISPATCHED VIA run_stage.py -->
<!-- It is a reference document only. The alignment stage triggers a pipeline PAUSE. -->
<!-- Python prints these instructions and exits; the human conducts alignment manually. -->

## Pipeline Pause — Manual Alignment Required

The pipeline has paused at the alignment stage. This stage requires a live conversation.

You (the developer) should now:

1. Open a new Claude session (or use the current one interactively).
2. Share the findings from Discovery: `{{ run_folder }}/findings.md`
3. Work through the alignment questions covering:
   - Scope boundaries and what is explicitly out of scope
   - Key design decisions that will shape the specification
   - Risks and how to mitigate them
   - Any open questions that must be resolved before speccing
4. Write the alignment log to `{{ run_folder }}/alignment-log.md` — this is the hand-off artifact.
5. The log should include: all decisions made, the reasoning, and any remaining open items.

## Resuming the Pipeline

Once `{{ run_folder }}/alignment-log.md` exists, resume the pipeline:

```
orchestrator resume --run-folder {{ run_folder }} --docs-root <docs-root>
```

The pipeline will detect the alignment log and advance to Specification.
