---
name: harsh-general-engineering-standards
description: Compact, prompt-injection form of the general engineering standards. Hard rules only.
---

# General Engineering Standards

## Review gates — reject or fix on sight

- Requirement not explicitly satisfied.
- Reviewer cannot run the code using documented commands.
- Tests missing for new behavior, bug fixes, validation, or failure paths.
- Inconsistent status codes, errors, or API semantics.
- Unvalidated external input; whitespace-only values accepted.
- Hardcoded credentials, tokens, hostnames, or environment assumptions.
- Global mutable state without explicit scope.
- Silent failures, swallowed errors, ambiguous return values.
- Large functions mixing IO, validation, business logic, persistence.
- Abstractions that do not reduce complexity or improve testability.
- Formatting/lint/type errors.
- Submission archive contains `.git`, dependency folders, `target`, `dist`, `.DS_Store`, `._*`, logs, caches, or local IDE files.

## Architecture

- Domain/business logic independent from transport, persistence, framework code.
- Modules: handlers/controllers, services/use-cases, repositories/adapters, models/types, config, middleware, tests.
- Inject dependencies at boundaries; no hidden singletons.
- Interfaces only when they enable testing or real substitution.
- Explicit errors and data flow over magic behavior.

## API/backend

- Validate and normalize input at boundaries.
- Status codes: 200 read/update, 201 create, 204 delete-no-body, 400 invalid, 401/403 auth, 404 missing, 409 conflict, 422 only if project uses semantic validation, 500 unexpected only.
- Consistent JSON error objects; never leak stack traces or internals.
- Request IDs or structured logs for production-readiness.

## Testing

- Prove behavior, not implementation details.
- Unit tests for domain/services; integration/handler tests for API behavior.
- Edge cases: empty, whitespace, invalid types, duplicates, missing IDs, malformed JSON, not found, conflicts, concurrency.
- Deterministic, isolated, one-command runnable. No sleeps. No real network unless explicitly tested.
- Regression tests alongside bug fixes.

## Security and reliability

- Treat all user input as hostile.
- Body size limits where relevant.
- Timeouts for network, DB, model/API calls.
- Close resources; handle cancellation/shutdown.
- Secrets in environment/config, never source.
- No logging secrets, tokens, PII, or raw sensitive payloads.
