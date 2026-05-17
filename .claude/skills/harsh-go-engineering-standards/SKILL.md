---
name: harsh-go-engineering-standards
description: Enforce strict Go backend quality rules. Use when writing, refactoring, testing, or reviewing Go services, APIs, CLI tools, stores, handlers, concurrency code, or production submissions.
---

# Harsh Go Engineering Standards

## Mandatory commands

```bash
gofmt -w .
go test ./...
go test -race ./...
go vet ./...
```

Also run `golangci-lint run`, coverage, integration tests, and security/dependency checks when configured.


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


## Go-specific hard rules

- Prefer simple idiomatic Go over framework-heavy abstractions.
- Keep packages cohesive and named by purpose.
- Avoid package-level mutable state except intentionally scoped test/demo state.
- Use typed structs instead of `map[string]any` for API/domain data.
- Define interfaces at the consumer boundary and keep them small.
- Wrap errors with `%w`; use `errors.Is` / `errors.As` for domain errors.
- Use a dedicated `http.ServeMux` or router; avoid `http.DefaultServeMux`.
- Handlers parse/validate HTTP and delegate business logic.
- Always set JSON content type for JSON responses.
- Do not write response bodies for `204 No Content`.
- Protect shared mutable state with a mutex, channel, or immutable snapshot.
- If multiple maps/indexes maintain one invariant, update/read them under one lock.
- Return copies/snapshots from stores.
- Pass `context.Context` through request-scoped, IO, DB, network, and long-running operations.
- Use timeouts for outbound calls.
- Implement graceful shutdown when production readiness is in scope.
- Use table-driven tests, `httptest`, `t.Helper()`, and fresh store/service/app instances.
- Never use sleeps to hide races.

## Immediate rejection signals

- `gofmt -l .` produces files.
- Race detector fails.
- Validation accepts `"   "`.
- Handler contains persistence details.
- Store exposes internal mutable state.
- Comments overclaim concurrency guarantees.
