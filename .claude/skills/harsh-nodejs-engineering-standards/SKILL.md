---
name: harsh-nodejs-engineering-standards
description: Enforce strict Node.js backend quality rules for JavaScript projects. Use when writing, refactoring, testing, or reviewing Node.js services, Express/Fastify APIs, CLIs, workers, and production submissions.
---

# Harsh Node.js Engineering Standards

## Mandatory commands

```bash
npm test
npm run lint
npm run format:check
npm audit
```

Use `pnpm` or `yarn` equivalents if the repo uses them.


## Cross-cutting harsh review additions

Apply these in addition to the base language rules:

- Inspect the final archive before submission with `tar -tf` or `unzip -l`.
- Reject archives containing `.git`, dependency folders, build output, `.env`, logs, `.DS_Store`, `._*`, IDE files, or local caches.
- Validate and normalize external input: trim strings, reject whitespace-only values, canonicalize unique keys such as email where appropriate, and avoid storing polluted input.
- Avoid non-atomic uniqueness checks such as `exists()` followed by `save()` unless protected by a lock, transaction, atomic repository method, or database constraint.
- Document in-memory storage limitations: no durability, limited multi-process guarantees, and weaker transactional consistency.
- Do not claim thread safety, atomicity, production readiness, or security unless the implementation actually provides it.
- Use stable error response fields such as `code`, `message`, `fieldErrors`, and optional `requestId`.
- Include API/handler/controller tests for HTTP behavior, not just unit tests.
- Include tests for malformed input, whitespace, normalization, duplicates/conflicts, not found, and failure paths.
- Add observability notes: structured logs, request IDs, metrics/tracing, health/readiness where appropriate.
- README/NOTES must include verified run/test commands, assumptions, tradeoffs, known limitations, and production next steps.


## Node.js-specific hard rules

- Respect the existing module system: CommonJS or ESM.
- Pin/document supported Node.js version using `.nvmrc`, `engines`, Volta, or README.
- Export app/server factory separately from the process listener.
- Avoid hidden process-global mutable state; create fresh app/repository instances in tests.
- Keep business rules out of route handlers.
- Use centralized error middleware.
- Use async error handling consistently; no unhandled rejected promises.
- Reject malformed JSON and enforce request body size limits.
- Use Helmet/security headers where appropriate.
- Use `AbortController`/timeouts for outbound calls.
- Avoid `Math.random()` for tokens/session IDs; use `crypto`.
- Never store plaintext passwords.
- Avoid floats for money.
- Use parameterized DB queries.
- Use Supertest or equivalent for HTTP APIs.
- Test async failure paths and rejected promises.
- Avoid console spam and debug output in final submissions.

## Immediate rejection signals

- App starts listening during import.
- No validation or normalization.
- Duplicate rules implemented only in handlers.
- Non-atomic duplicate/unique checks.
- Tests share state.
- `npm test` fails from clean checkout.
