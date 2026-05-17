---
name: harsh-python-engineering-standards
description: Enforce strict Python engineering standards for APIs, services, CLIs, workers, data/AI pipelines, libraries, tests, packaging, security, and production submissions. Use when writing, refactoring, testing, or reviewing Python code that must survive harsh senior/staff engineer review.
---

# Harsh Python Engineering Standards

Apply these rules whenever Python code must pass a strict production, staff-level, or hiring-manager review.

## Prime directive

Deliver Python that is correct, typed where it matters, testable, observable, secure, packageable, and easy to operate. Prefer boring, explicit, maintainable code over clever dynamic tricks.

Do not claim production readiness, thread/process safety, async safety, security, idempotency, or transactional guarantees unless the code actually provides them.

## Mandatory commands before final answer

Run or recommend the repo-equivalent commands. Prefer project scripts if present.

```bash
python --version
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy .
python -m pip_audit
```

If configured, also run:

```bash
python -m coverage run -m pytest
python -m coverage report
python -m bandit -r src
tox
nox
```

For final submission archives, inspect contents:

```bash
tar -tf submission.tgz
# or
unzip -l submission.zip
```

## Project and packaging rules

- Use `pyproject.toml` as the primary project/tooling configuration.
- Include a `[build-system]` table for packageable projects.
- Use a `src/` layout for libraries and non-trivial applications unless the existing project convention differs.
- Keep runtime dependencies separate from dev/test dependencies.
- Do not commit virtual environments, caches, build outputs, coverage outputs, local env files, notebooks with sensitive outputs, or machine-local metadata.
- Prefer one documented environment manager per repo: `uv`, Poetry, Hatch, PDM, pip-tools, or plain `venv + pip`.
- Do not casually switch package managers or lockfile strategy.
- Make the project runnable from a clean checkout using documented commands.
- Pin or lock dependencies for applications; define compatible ranges for libraries.
- Document supported Python versions.

## Recommended structure

For services/APIs:

```text
src/<package>/
  api/ or handlers/
  service/
  repository/ or adapters/
  domain/
  config/
  logging/
  errors.py
tests/
pyproject.toml
README.md
NOTES.md
```

For CLIs/workers:

```text
src/<package>/
  cli.py
  worker.py
  service/
  adapters/
  domain/
  config/
tests/
```

For AI/data pipelines:

```text
src/<package>/
  pipeline/
  models/
  prompts/
  evals/
  adapters/
  config/
data/ or fixtures/
tests/
```

## Python language rules

- Use modern Python idioms, but respect the configured Python version.
- Prefer explicit types for public APIs, service boundaries, domain models, and complex data.
- Use `dataclass(frozen=True)`, `typing.NamedTuple`, `attrs`, or Pydantic models where they genuinely clarify invariants.
- Avoid raw `dict[str, Any]` as a domain model.
- Avoid `Any` unless it is contained at an external boundary and narrowed immediately.
- Avoid mutable default arguments.
- Avoid broad `except Exception` except at process/framework boundaries where it is logged and mapped.
- Never use bare `except:`.
- Do not silently swallow exceptions.
- Prefer explicit return types over implicit `None`.
- Avoid global mutable state. If used for a demo/in-memory repo, isolate and document it.
- Avoid import-time side effects: no network calls, DB connections, process startup, or env validation surprises at import time.
- Keep functions small and cohesive.
- Avoid metaprogramming, monkeypatching, dynamic attribute tricks, and reflection unless strongly justified.

## Typing rules

- Run mypy or pyright according to project convention.
- New code should be typed.
- Enable strict-ish typing for new/small projects.
- Do not disable type errors to hide design issues.
- Use `Protocol` for structural interfaces when it improves testability.
- Use `TypedDict` or Pydantic/dataclasses for structured external data.
- Use `Literal`, `Enum`, or constrained value objects for domain states.
- Use `NewType` or small value objects for IDs where mixups are plausible.
- Treat `Optional[T]` seriously: handle `None` explicitly.
- Avoid casts unless crossing a boundary; document why the cast is safe.
- Keep type ignores narrow and justified: `# type: ignore[code]  # reason`
- Do not let mocks erase useful type guarantees.

## Formatting and linting

- Use Ruff for linting and formatting unless the repo already uses Black/isort/Flake8.
- Keep formatting automatic and non-negotiable.
- Fix lint findings rather than suppressing them.
- Do not leave unused imports, unused variables, debug prints, or commented-out code.
- Prefer simple readable expressions over dense comprehensions.
- Follow PEP 8 naming and readability conventions unless project style differs.

## Configuration

- Centralize configuration in one module.
- Validate required environment variables at startup, not deep in business logic.
- Never read `os.environ` throughout the codebase.
- Use typed config objects.
- Do not log secrets.
- Provide `.env.example` where useful, never `.env`.
- Distinguish development, test, and production config.

## API/backend rules

- Keep HTTP/framework code thin.
- Business logic belongs in services/use-cases, not routes.
- Persistence belongs in repositories/adapters, not services or routes.
- Validate and normalize all request body, path, query, and header inputs.
- Normalize before uniqueness checks:
  - trim strings
  - reject whitespace-only values
  - canonicalize emails/usernames when used as unique keys
  - avoid storing polluted input
- Return precise status codes:
  - 200 for successful reads/updates
  - 201 for creation
  - 202 for accepted async work
  - 204 for successful deletes with no body
  - 400 for malformed/invalid input
  - 401/403 for auth failures
  - 404 for missing resources
  - 409 for conflicts
  - 422 only if the framework/project convention uses it for validation
  - 500 only for unexpected server faults
- Return consistent JSON error objects with stable fields:
  - `code`
  - `message`
  - `field_errors` when relevant
  - `request_id` when available
- Never leak stack traces, SQL details, internal hostnames, secrets, tokens, or PII.
- Enforce request body size limits where relevant.
- Add health/readiness endpoints when production-readiness is in scope.
- Use dependency injection or app factories so tests can replace dependencies.

## FastAPI-specific rules

- Use Pydantic models for request/response validation where appropriate.
- Keep route functions thin.
- Use dependencies for cross-cutting concerns, auth, database sessions, and adapters.
- In tests, use dependency overrides rather than global monkeypatching where practical.
- Do not rely solely on framework validation for domain invariants.
- Explicitly test validation errors, dependency failures, and exception mappings.
- Avoid doing blocking IO in async endpoints.

## Flask/Django-specific rules

- Keep views/controllers thin.
- Use forms/serializers/schemas for boundary validation.
- Keep ORM models separate from API response contracts when API stability matters.
- Use transactions for multi-write operations.
- Avoid importing application objects in ways that create circular imports or hidden side effects.
- Use app factories where appropriate.

## Persistence and data integrity

- Do not implement uniqueness as `exists()` followed by `save()` unless protected by a transaction, lock, atomic repository method, or database constraint.
- Enforce uniqueness and referential integrity in the database where possible.
- Map database constraint violations to domain conflicts.
- Use transactions for multi-write operations.
- Avoid N+1 queries.
- Use parameterized SQL or ORM query APIs. Never string-concatenate untrusted SQL.
- Use migrations for schema changes.
- Document isolation/locking assumptions for concurrency-sensitive behavior.
- For in-memory repositories:
  - protect shared state if concurrent access is possible
  - return copies/snapshots
  - document no durability and single-process limitations

## Async and concurrency

- Do not block the event loop with synchronous IO or CPU-heavy work.
- Use timeouts for external calls:
  - HTTP APIs
  - DB operations where supported
  - queues
  - LLM/model calls
- Propagate cancellation correctly.
- Do not swallow `asyncio.CancelledError`.
- Bound concurrency with semaphores, worker pools, queues, or rate limits.
- Avoid unbounded `gather()` over user-controlled input.
- Protect shared mutable state across threads/tasks/processes.
- Test timeout, retry, cancellation, and partial failure paths.

## External calls and resilience

- Wrap external systems behind adapters.
- Use explicit timeouts.
- Retry only transient failures.
- Use exponential backoff with jitter where appropriate.
- Do not retry non-idempotent operations unless idempotency keys or safe semantics exist.
- Add circuit breaker/bulkhead/degraded-mode design notes for production systems.
- Log external dependency failures with enough context, but no secrets.
- Make model/API clients injectable for tests.

## Security

- Treat all input as hostile.
- Never use `eval`, `exec`, unsafe deserialization, unsafe YAML loaders, or shell commands with untrusted input.
- Use `subprocess.run([...], shell=False)` for process execution.
- Use parameterized queries.
- Validate file paths and prevent path traversal.
- Validate uploads: type, extension, content, size, and storage path.
- Store secrets in environment/secret managers, never source.
- Use `secrets` for tokens; do not use `random` for security.
- Hash passwords with a standard password hashing library/algorithm; never roll your own.
- Avoid logging PII, credentials, cookies, auth headers, API keys, or raw sensitive payloads.
- Run dependency vulnerability scanning with `pip-audit` or project equivalent.
- Use Bandit or equivalent static security checks when configured.

## Logging and observability

- Use the standard `logging` module or project logging framework; avoid `print` in production code.
- Log structured events where practical.
- Include request/correlation IDs for services.
- Use logger names per module: `logging.getLogger(__name__)`.
- Configure logging at application entry points, not library import time.
- Do not log secrets or raw sensitive payloads.
- Add metrics/tracing hooks where production-readiness is claimed.
- Document key operational signals:
  - request rate
  - error rate
  - latency
  - queue depth
  - external dependency failures
  - retry counts
  - model/API cost and latency for AI systems

## Testing rules

- Use pytest unless the project uses unittest or another clear standard.
- Tests must be deterministic, isolated, and runnable with one command.
- Use fixtures for setup; keep fixture scope tight.
- Use `tmp_path` for filesystem tests.
- Use monkeypatch/fakes for environment and external dependencies.
- Prefer fakes or small test doubles over excessive mocking.
- Patch where the dependency is looked up, not where it was originally defined.
- Do not call real external networks, production databases, or real LLM APIs in tests.
- Include:
  - unit tests for domain/service logic
  - integration/handler tests for API behavior
  - regression tests for bug fixes
  - edge cases for validation and normalization
  - failure-path tests
  - concurrency/async tests where relevant
- For APIs, assert status code, response body, content type, and error shape.
- Test malformed JSON/input, whitespace-only fields, duplicate conflicts, not found, unauthorized/forbidden, timeout/retry behavior, and persistence edge cases.
- Avoid sleeps; use fake clocks, dependency injection, or timeouts with controlled fakes.
- Coverage should reflect risk, not vanity percentages.

## AI/data pipeline rules

- Separate data loading, validation, transformation, model calls, evaluation, and output.
- Make randomness deterministic with seeded RNGs where reproducibility matters.
- Validate input datasets and schemas.
- Do not commit large/generated datasets unless explicitly required.
- Track prompt/model/config versions for AI workflows.
- Use evals for AI behavior, not only unit tests.
- Capture latency, cost, error rates, and quality metrics.
- Never trust raw model output; parse, validate, and handle invalid output.

## CLI rules

- Provide clear usage and helpful error messages.
- Return meaningful exit codes.
- Do not print stack traces for expected user errors.
- Keep CLI parsing separate from business logic.
- Test CLI behavior via subprocess or CLI runner where practical.
- Handle stdin/stdout/stderr intentionally.

## Review failures to fix immediately

- Tests pass but type checking fails.
- Ruff/formatter reports changes needed.
- Import-time side effects start services or contact external systems.
- Runtime input is trusted because it has type hints.
- `Any`, raw dicts, or dynamic objects used as domain models without justification.
- Validation accepts `"   "` for required fields.
- Non-atomic uniqueness checks.
- Secrets or local paths committed.
- `print` debugging remains.
- Broad exceptions swallowed.
- Blocking IO inside async endpoints.
- Real external services used in tests.
- Archive includes `.venv`, `venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.nox`, `dist`, `build`, `*.egg-info`, `.coverage`, `htmlcov`, `.env`, `.git`, `.DS_Store`, `._*`, notebooks with sensitive outputs, or local IDE files.

## Final Python submission checklist

- `python --version` documented.
- Install/run/test commands verified from clean checkout.
- `pytest` passes.
- Ruff lint and format checks pass.
- mypy/pyright passes or exceptions are documented.
- Dependency audit run if available.
- Security scan run if configured.
- README documents:
  - Python version
  - package manager
  - install/run/test commands
  - architecture summary
  - API/CLI examples
  - assumptions and tradeoffs
  - validation/normalization strategy
  - persistence/concurrency limitations
  - production next steps
- Archive inspected and clean.
