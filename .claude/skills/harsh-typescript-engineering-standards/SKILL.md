---
name: harsh-typescript-engineering-standards
description: Enforce strict TypeScript quality rules. Use when writing, refactoring, testing, or reviewing TypeScript apps, APIs, libraries, frontends, backends, or production submissions.
---

# Harsh TypeScript Engineering Standards

## Mandatory commands

```bash
npm test
npm run typecheck
npm run lint
npm run format:check
npm audit
```

Use `pnpm`/`yarn` equivalents if already present.


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


## TypeScript-specific hard rules

- Keep `strict` enabled; do not loosen tsconfig to make code pass.
- Prefer `unknown` for untrusted data, then validate/narrow.
- No `any` unless narrow, unavoidable, and justified.
- Do not use unsafe type assertions to silence errors.
- Model domain concepts with explicit types, discriminated unions, branded IDs, or literal unions where useful.
- Keep DTO/API types separate from domain types.
- Exhaustively handle discriminated unions with `never`.
- TypeScript types do not validate runtime input: use Zod, Valibot, Joi, class-validator, io-ts, or project-standard validation.
- Validate params/query/body/env variables.
- Use typed/custom errors for validation, not found, conflict, and authorization.
- Treat caught errors as `unknown`.
- Preserve causes where useful.
- Avoid scattered `process.env`; use a typed config module.
- Keep domain/service logic framework-independent.
- Ensure `tsc --noEmit` passes even if tests pass.

## Immediate rejection signals

- `any` used as a shortcut.
- Runtime input trusted because it has a TypeScript type.
- Strict mode disabled in a new/small project.
- Domain logic depends directly on Express/Nest/Fastify/React.
- Validation accepts whitespace-only input.
- Unique/conflict logic is non-atomic.
