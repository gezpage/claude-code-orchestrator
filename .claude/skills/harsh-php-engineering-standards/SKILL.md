---
name: harsh-php-engineering-standards
description: Enforce strict PHP quality rules. Use when writing, refactoring, testing, or reviewing PHP applications, Laravel/Symfony apps, APIs, Composer packages, and production submissions.
---

# Harsh PHP Engineering Standards

## Mandatory commands

```bash
composer test
composer analyse
composer cs-check
composer audit
```

If scripts differ, use PHPUnit/Pest, PHPStan/Psalm, PHP-CS-Fixer/PHP_CodeSniffer, and Composer audit equivalents.


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


## PHP-specific hard rules

- Use `declare(strict_types=1);` in new PHP source files unless project convention forbids it.
- Follow PSR-12 unless the project has a stricter standard.
- Use namespaces and Composer autoloading.
- Prefer typed properties, typed parameters, typed returns, readonly properties/classes, enums, and value objects where appropriate.
- Avoid dynamic properties, magic arrays for domain data, global state, hidden singletons, and service locators unless framework convention requires them.
- Keep controllers thin; business rules belong in services/use cases.
- Do not suppress errors with `@`.
- Never interpolate untrusted input into SQL.
- Use `password_hash()` and `password_verify()`.
- Generate secure tokens with `random_bytes()` or framework-secure helpers.
- Do not use floats for money; use integer minor units or a money/decimal value object.
- Use transactions for multi-write operations.
- Use PHPUnit/Pest feature tests for HTTP endpoints and unit tests for domain/services.
- Use PHPStan/Psalm at a meaningful level; avoid `mixed` except at boundaries.

## Immediate rejection signals

- Missing `strict_types` in new files.
- Raw SQL with user input.
- Business logic in controller.
- Untyped public arrays used as domain objects.
- Validation accepts whitespace-only input.
- Non-atomic duplicate/unique checks.
- Composer scripts fail from fresh checkout.
