---
name: harsh-typescript-engineering-standards
description: Compact, prompt-injection form of the TypeScript engineering standards. Hard rules only.
---

# TypeScript Engineering Standards

## Mandatory commands

```bash
npm test
npm run typecheck
npm run lint
npm run format:check
npm audit
```

Use `pnpm`/`yarn` equivalents if present.

## Hard rules

- `strict` enabled; do not loosen tsconfig to make code pass.
- `unknown` for untrusted data, then validate/narrow.
- No `any` unless narrow, unavoidable, justified.
- No unsafe type assertions to silence errors.
- Explicit types, discriminated unions, branded IDs, literal unions for domain modelling.
- DTO/API types separate from domain types.
- Exhaustively handle discriminated unions with `never`.
- TypeScript types do NOT validate runtime input — use Zod/Valibot/Joi/class-validator/io-ts.
- Validate params/query/body/env variables at boundaries.
- Typed/custom errors for validation, not-found, conflict, authorization.
- Caught errors typed as `unknown`; preserve causes.
- No scattered `process.env` — typed config module.
- Domain/service logic framework-independent.
- `tsc --noEmit` must pass even if tests pass.

## Validation and integrity

- Trim strings, reject whitespace-only values, canonicalize unique keys.
- Non-atomic `exists()`-then-`save()` only when protected by lock, transaction, atomic method, or DB constraint.
- Stable error fields: `code`, `message`, `fieldErrors`, optional `requestId`.

## Immediate rejection signals

- `any` as a shortcut.
- Runtime input trusted because of TypeScript type.
- Strict mode disabled in a new/small project.
- Domain logic depends directly on Express/Nest/Fastify/React.
- Validation accepts whitespace-only input.
- Non-atomic unique/conflict logic.
- Archive includes `.git`, `node_modules`, build output, `.env`, logs, `.DS_Store`, `._*`, IDE files, caches.
