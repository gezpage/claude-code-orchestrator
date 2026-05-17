---
name: harsh-php-engineering-standards
description: Compact, prompt-injection form of the PHP engineering standards. Hard rules only.
---

# PHP Engineering Standards

## Mandatory commands

```bash
composer test
composer analyse
composer cs-check
composer audit
```

Use PHPUnit/Pest, PHPStan/Psalm, PHP-CS-Fixer/PHP_CodeSniffer equivalents if scripts differ.

## Hard rules

- `declare(strict_types=1);` in new source files unless project forbids.
- PSR-12 unless a stricter standard applies.
- Namespaces and Composer autoloading.
- Typed properties, parameters, returns; readonly, enums, value objects where appropriate.
- No dynamic properties, magic arrays for domain data, global state, hidden singletons, service locators (unless framework requires).
- Thin controllers; business rules in services/use cases.
- No `@` error suppression.
- Never interpolate untrusted input into SQL — parameterized queries only.
- `password_hash()` / `password_verify()`; `random_bytes()` for tokens.
- No floats for money — integer minor units or money/decimal value object.
- Transactions for multi-write operations.
- PHPUnit/Pest feature tests for HTTP endpoints; unit tests for domain/services.
- PHPStan/Psalm at meaningful level; avoid `mixed` except at boundaries.

## Validation and integrity

- Trim strings, reject whitespace-only values, canonicalize unique keys.
- Non-atomic `exists()`-then-`save()` only when protected by lock, transaction, atomic method, or DB constraint.
- Stable error fields: `code`, `message`, `fieldErrors`, optional `requestId`.

## Immediate rejection signals

- Missing `strict_types` in new files.
- Raw SQL with user input.
- Business logic in controller.
- Untyped public arrays used as domain objects.
- Validation accepts whitespace-only input.
- Non-atomic duplicate/unique checks.
- Composer scripts fail from fresh checkout.
- Archive includes `.git`, dependency folders, build output, `.env`, logs, `.DS_Store`, `._*`, IDE files, caches.
