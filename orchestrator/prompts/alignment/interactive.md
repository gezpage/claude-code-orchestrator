# Alignment Stage — Interactive Mode

<!-- Dispatched via run_interactive_stage() — not run_stage(). -->
<!-- This prompt is rendered and passed as the initial message to an interactive Claude session. -->

## Alignment Stage

The pipeline has launched this interactive session to conduct alignment before specification.

Work through the following with the developer:

1. Review the Discovery findings:
{% for f in findings_files %}   - `{{ f }}`
{% endfor %}
2. Align on scope boundaries — what is explicitly in and out of scope
3. Resolve key design decisions that will shape the specification
4. Identify risks and agree on mitigations
5. Surface any open questions that must be resolved before speccing

Once alignment is complete, write the alignment log to `{{ run_folder }}/alignment/alignment-log.md`. The log should capture: all decisions made, the reasoning, and any remaining open items.

When done, exit this session (`/exit`). The pipeline will detect `alignment-log.md` and advance to Specification.
