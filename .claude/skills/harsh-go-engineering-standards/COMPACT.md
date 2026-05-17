---
name: harsh-go-engineering-standards
description: Compact, prompt-injection form of the Go engineering standards. Hard rules only.
---

# Go Engineering Standards

## Mandatory commands

```bash
gofmt -w .
go test ./...
go test -race ./...
go vet ./...
```

Also `golangci-lint run`, coverage, security/dependency checks when configured.

## Hard rules

- Simple idiomatic Go over framework-heavy abstractions; packages cohesive and named by purpose.
- No package-level mutable state (except intentionally scoped test/demo).
- Typed structs over `map[string]any` for API/domain data.
- Interfaces at the consumer boundary; keep them small.
- Wrap errors with `%w`; use `errors.Is` / `errors.As` for domain errors.
- Dedicated `http.ServeMux` or router; avoid `http.DefaultServeMux`.
- Handlers parse/validate HTTP and delegate business logic.
- Always set JSON content type for JSON responses; no body for `204 No Content`.
- Protect shared mutable state with mutex, channel, or immutable snapshot.
- Update/read related maps/indexes under one lock when they share an invariant.
- Return copies/snapshots from stores.
- Pass `context.Context` through request-scoped, IO, DB, network, long-running operations.
- Timeouts for outbound calls; graceful shutdown for production scope.
- Table-driven tests, `httptest`, `t.Helper()`, fresh store/service/app instances per test.
- Never sleep to hide races.

## Validation and integrity

- Trim strings, reject whitespace-only values, canonicalize unique keys.
- Non-atomic `exists()`-then-`save()` only when protected by lock, transaction, atomic method, or DB constraint.
- Stable error response fields: `code`, `message`, `fieldErrors`, optional `requestId`.

## Immediate rejection signals

- `gofmt -l .` produces files.
- Race detector fails.
- Validation accepts `"   "`.
- Handler contains persistence details.
- Store exposes internal mutable state.
- Comments overclaim concurrency guarantees.
- Archive includes `.git`, dependency folders, build output, `.env`, logs, `.DS_Store`, `._*`, IDE files, caches.
