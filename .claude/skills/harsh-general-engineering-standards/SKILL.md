---
name: harsh-general-engineering-standards
description: Apply language-agnostic engineering rules for production-quality code. Use before implementing, refactoring, reviewing, or finalising any codebase where correctness, maintainability, testing, security, and reviewer-grade polish matter.
---

# Harsh General Engineering Standards

Use this skill whenever working on a project where the result must survive a strict hiring-manager or production-readiness review.

## Prime directive

Deliver the smallest correct solution that is easy to run, easy to test, easy to review, and safe to change. Do not trade correctness, clarity, or tests for cleverness.

## Mandatory workflow

1. Read the README, package/build files, tests, entry points, and requirements before editing.
2. Run the existing test/build command before changing code when practical.
3. Identify the minimal core path needed to satisfy requirements.
4. Implement in small, coherent changes.
5. Add regression tests for every fixed bug and edge-case tests for every new rule.
6. Run format, lint, type/static checks, tests, and security/dependency checks available in the repo.
7. Final pass: remove debug output, dead code, commented junk, secrets, generated artifacts, and machine-local files.

## Review gates

Reject or fix code that has any of these:

- Requirement not explicitly satisfied.
- Code cannot be run by a reviewer using documented commands.
- Tests missing for new behavior, bug fixes, validation, and failure paths.
- Inconsistent status codes, errors, or API semantics.
- Unvalidated external input.
- Hardcoded credentials, tokens, hostnames, or environment-specific assumptions.
- Global mutable state unless intentionally scoped and documented.
- Silent failures, swallowed errors, or ambiguous return values.
- Large functions/classes that mix IO, validation, business logic, and persistence.
- Abstractions that do not reduce complexity or improve testability.
- Formatting/lint/type errors.
- Dirty submission archive containing `.git`, `node_modules`, `target`, `dist`, `.DS_Store`, `._*`, logs, caches, or local IDE files.

## Architecture rules

- Keep domain/business logic independent from transport, persistence, and framework code.
- Prefer clear modules: handlers/controllers, services/use-cases, repositories/adapters, models/types, config, middleware, tests.
- Inject dependencies at boundaries. Avoid hidden singletons.
- Use interfaces/ports only when they enable testing or real substitution.
- Keep public APIs stable and documented.
- Prefer explicit errors and explicit data flow over magic behavior.

## API/backend rules

- Validate and normalize input at boundaries.
- Return precise status codes:
  - 200 for successful reads/updates
  - 201 for creation
  - 204 for successful deletes with no body
  - 400 for malformed/invalid input
  - 401/403 for auth failures
  - 404 for missing resources
  - 409 for conflicts
  - 422 only if the project already uses semantic validation status
  - 500 only for unexpected server faults
- Return consistent JSON error objects.
- Never leak stack traces or internal details to clients.
- Use request IDs or structured logs when production-readiness matters.
- Add health/readiness endpoint only when appropriate to the app.

## Testing rules

- Tests must prove behavior, not implementation details.
- Include unit tests for domain/service logic.
- Include integration or handler tests for API behavior.
- Include edge cases: empty input, whitespace input, invalid types, duplicates, missing IDs, malformed JSON, not found, conflicts, and concurrency where relevant.
- Tests must be deterministic, isolated, and runnable with one command.
- Avoid sleeps and network dependencies unless explicitly part of the test.
- Add regression tests before or alongside bug fixes.

## Security and reliability rules

- Treat all user input as hostile.
- Enforce size limits on request bodies where relevant.
- Use timeouts for network, database, and model/API calls.
- Close resources; handle cancellation/shutdown.
- Do dependency audits when tooling exists.
- Store secrets in environment/config, never source.
- Avoid logging secrets, tokens, PII, or raw sensitive payloads.

## Final submission checklist

- Requirements checklist completed.
- Tests pass.
- Lint/format/type/static checks pass.
- Dependency/security audit run if available.
- README explains run/test commands, assumptions, tradeoffs, and future work.
- Submission archive excludes build artifacts, dependency folders, VCS internals, OS metadata, and local config.
