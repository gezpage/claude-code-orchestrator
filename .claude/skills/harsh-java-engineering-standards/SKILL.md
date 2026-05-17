---
name: harsh-java-engineering-standards
description: Enforce strict Java backend quality rules. Use when writing, refactoring, testing, or reviewing Java, Spring Boot, Maven/Gradle, REST APIs, services, repositories, and production submissions.
---

# Harsh Java Engineering Standards

Use this for Java services and especially Spring Boot APIs.

## Mandatory commands before final answer

Run or recommend the repo equivalent:

```bash
./mvnw test
./mvnw verify
```

or

```bash
./gradlew test
./gradlew check
```

Also run formatter/checkstyle/spotless/spotbugs/pmd if configured.

## Language and style

- Use the Java version configured by the project. Do not introduce unsupported features.
- Prefer immutability: final fields, records for simple DTOs/value carriers, unmodifiable collections when exposing data.
- Use constructor injection; avoid field injection.
- Keep classes focused and package-private where possible.
- Avoid `Optional` for fields, DTO properties, and method parameters; use it mainly for return values where absence is meaningful.
- Do not return `null` when absence/error should be explicit.
- Avoid static mutable state.
- Avoid broad `catch (Exception)` unless mapping at a boundary.
- Use clear package structure and naming.

## Spring/API architecture

Use clear layers:

```text
controller
dto/request/response
service
repository
domain/entity/model
config
exception
```

Rules:

- Controllers handle HTTP only: request parsing, validation trigger, response mapping.
- Services own business rules and transactions.
- Repositories own persistence.
- DTOs isolate external API contracts from domain/persistence models.
- Global exception handling maps expected errors consistently.
- Validation annotations belong on request DTOs where appropriate.
- Do not expose JPA entities directly as API responses.
- Use `@Transactional` at service boundaries when persistence exists.

## Error and status handling

- 201 for create, 200 for successful read/update, 204 for delete without body.
- 400 for validation/malformed input.
- 404 for missing resources.
- 409 for conflicts.
- Consistent error response shape.
- Do not leak stack traces or SQL/internal details.

## Testing

- Use JUnit 5.
- Unit-test services with fakes or Mockito only where it clarifies behavior.
- Use Spring slice tests where possible:
  - `@WebMvcTest` for controllers
  - `@DataJpaTest` for repositories
  - `@SpringBootTest` only when full context is necessary
- Use Testcontainers when integration with real infrastructure matters and time allows.
- Use builders/fixtures for readable test data.
- Cover validation, not found, conflicts, malformed input, and happy paths.
- Ensure tests are isolated and deterministic.

## Dependency and build hygiene

- Use Maven/Gradle wrapper if present.
- Do not commit `target/`, `build/`, `.gradle/`, IDE files, logs, or local env files.
- Avoid unnecessary dependencies.
- Check dependency vulnerabilities when tooling exists.
- Keep configuration environment-driven.

## Production readiness

- Use structured logging via SLF4J/logback; avoid `System.out.println`.
- Do not log secrets or PII.
- Add actuator health/readiness if Spring Boot production service and dependency is available.
- External calls need timeouts and clear error handling.
- Prefer configuration properties classes for non-trivial config.
- Document persistence assumptions and migrations if relevant.

## Review failures to fix immediately

- Field injection.
- Controller contains business logic.
- Entity returned directly from controller.
- No global exception mapping.
- Tests require a full Spring context for simple logic.
- Validation exists only in comments or frontend assumptions.
- `mvn test`/`gradle test` fails from fresh checkout.
- Archive includes `target`, `build`, `.git`, IDE files, or OS metadata.

## Final Java submission checklist

README must include Java version, Maven/Gradle commands, API examples, assumptions, tradeoffs, and production next steps.
