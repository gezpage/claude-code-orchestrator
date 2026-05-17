---
name: harsh-python-engineering-standards
description: Compact, prompt-injection form of the Python engineering standards. Hard rules only.
---

# Python Engineering Standards

## Mandatory commands

```bash
python --version
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy .
python -m pip_audit
```

Plus coverage/bandit/tox/nox when configured.

## Language rules

- Respect project Python version; no unsupported features.
- Explicit types on public APIs, service boundaries, domain models, complex data.
- `dataclass(frozen=True)`, `NamedTuple`, `attrs`, or Pydantic for invariants.
- No raw `dict[str, Any]` as domain model.
- `Any` only at external boundary, narrowed immediately.
- No mutable default arguments.
- No broad `except Exception` (except at boundaries, logged + mapped); never bare `except:`.
- Never silently swallow exceptions.
- No global mutable state (unless explicitly scoped and documented).
- No import-time side effects: no network/DB/process startup at import time.
- No metaprogramming/monkeypatching/reflection without strong justification.

## Typing

- mypy or pyright per project; new code typed.
- `Protocol` for structural interfaces where it improves testability.
- `TypedDict`/Pydantic/dataclasses for structured external data.
- `Literal`/`Enum`/value objects for domain states; `NewType` for ID disambiguation.
- Handle `Optional[T]` explicitly; no casts unless crossing a boundary.
- Narrow, justified `# type: ignore[code]  # reason` only.

## Config and security

- Centralized typed config; validate env at startup.
- Never read `os.environ` throughout the codebase.
- `.env.example` yes, `.env` no.
- Never `eval`/`exec`, unsafe deserialization, unsafe YAML loaders, shell with untrusted input.
- `subprocess.run([...], shell=False)`; parameterized queries.
- `secrets` for tokens (never `random`); standard password hashing.
- No logging PII/credentials/tokens.

## API/backend

- Thin HTTP/framework layer; business logic in services; persistence in repositories.
- Normalize input: trim strings, reject whitespace-only, canonicalize unique keys.
- Status codes: 200/201/202/204/400/401/403/404/409/422 (only if project uses it)/500.
- Consistent error fields: `code`, `message`, `field_errors`, `request_id`.
- No leak of stack traces, SQL, hostnames, secrets, PII.
- Body size limits; health/readiness when production-readiness is in scope.

## Persistence

- Non-atomic `exists()`-then-`save()` only when protected by transaction/lock/atomic method/DB constraint.
- Enforce uniqueness/referential integrity in DB where possible.
- Transactions for multi-write operations; avoid N+1; parameterized SQL only.

## Async and concurrency

- Don't block the event loop.
- Timeouts on every external call (HTTP, DB, queues, LLM).
- Propagate cancellation; never swallow `asyncio.CancelledError`.
- Bound concurrency (semaphores/pools/queues/rate limits); no unbounded `gather()` on user input.

## Testing

- pytest; deterministic, isolated, one-command runnable.
- Fakes/small doubles over excessive mocking; patch where looked up.
- No real external networks/DBs/LLMs in tests.
- Cover validation, normalization, not-found, conflicts, timeouts, retries, persistence edges.
- No sleeps — fake clocks or controlled fakes.

## Immediate rejection signals

- Tests pass but type checking fails.
- Ruff/formatter reports changes needed.
- Import-time side effects start services or contact external systems.
- Runtime input trusted because of type hints.
- `Any`/raw dicts as domain models without justification.
- Validation accepts `"   "` for required fields.
- Non-atomic uniqueness checks.
- Secrets or local paths committed.
- `print` debugging remains.
- Broad exceptions swallowed.
- Blocking IO in async endpoints.
- Real external services in tests.
- Archive includes `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.nox`, `dist`, `build`, `*.egg-info`, `.coverage`, `.env`, `.git`, `.DS_Store`, `._*`, IDE files.
