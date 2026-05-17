# Alignment Stage — Interactive Mode

<!-- Dispatched via run_interactive_stage() — not run_stage(). -->
<!-- This prompt is rendered and passed as the initial message to an interactive Claude session. -->

## Alignment Stage

The pipeline has launched this interactive session to conduct alignment before specification.

{% include "_includes/aliases.md" %}

Work through the following with the developer:

1. Review the Discovery findings:
{% for f in findings_files %}   - `{{ f }}`
{% endfor %}
2. Review the structured unresolved items the discovery stage surfaced:
{% if unresolved_questions %}   - **Unresolved questions:**
{% for q in unresolved_questions %}     - {{ q }}
{% endfor %}{% else %}   - No unresolved questions from discovery.
{% endif %}{% if risks %}   - **Risks:**
{% for r in risks %}     - {{ r }}
{% endfor %}{% else %}   - No risks from discovery.
{% endif %}{% if assumptions_needed %}   - **Assumptions discovery suggests adopting:**
{% for a in assumptions_needed %}     - {{ a }}
{% endfor %}{% else %}   - No working assumptions suggested by discovery.
{% endif %}
3. Align on scope boundaries — what is explicitly in and out of scope
4. Resolve key design decisions that will shape the specification
5. For every discovery unresolved item, agree on one of:
   - a concrete decision
   - a documented working assumption (record verbatim under **Accepted Assumptions**)
   - explicit deferral (record under **Unresolved Items Remaining** with what would unblock it)
6. Identify risks and agree on mitigations

Once all questions are resolved, read back the decisions made to confirm nothing was missed before writing the log.

Write the alignment log to `$RUN_FOLDER/alignment/alignment-log.md`. The log must be complete enough that someone who was not in this session could reconstruct every decision made — include the question, the decision, the reasoning, and any alternatives that were ruled out. Include explicit **Accepted Assumptions** and **Unresolved Items Remaining** sections so the orchestrator can apply the configured alignment policy.

When done, exit this session (`/exit`). The pipeline will detect `alignment-log.md` and advance to Specification.
