---
name: harsh-nodejs-engineering-standards
description: Compact, prompt-injection form of the Node.js engineering standards. Hard rules only.
---

# Node.js Engineering Standards

## Mandatory commands

```bash
npm test
npm run lint
npm run format:check
npm audit
```

Use `pnpm`/`yarn` equivalents if the repo does.

## Hard rules

- Respect existing module system (CommonJS or ESM).
- Pin/document Node version: `.nvmrc`, `engines`, Volta, or README.
- Export app/server factory separately from the process listener — never listen during import.
- No hidden process-global mutable state; fresh app/repository instances in tests.
- Business rules out of route handlers.
- Centralized error middleware; async error handling consistent; no unhandled rejected promises.
- Reject malformed JSON; enforce request body size limits.
- Helmet/security headers where appropriate.
- `AbortController`/timeouts for outbound calls.
- `crypto` for tokens/session IDs — never `Math.random()`.
- Never store plaintext passwords.
- No floats for money.
- Parameterized DB queries only.
- Supertest or equivalent for HTTP APIs; test async failure paths and rejected promises.
- No console spam or debug output in final submissions.

## Validation and integrity

- Trim strings, reject whitespace-only values, canonicalize unique keys.
- Non-atomic `exists()`-then-`save()` only when protected by lock, transaction, atomic method, or DB constraint.
- Stable error fields: `code`, `message`, `fieldErrors`, optional `requestId`.

## Immediate rejection signals

- App starts listening during import.
- No validation or normalization.
- Duplicate rules implemented only in handlers.
- Non-atomic duplicate/unique checks.
- Tests share state.
- `npm test` fails from clean checkout.
- Archive includes `.git`, `node_modules`, build output, `.env`, logs, `.DS_Store`, `._*`, IDE files, caches.
