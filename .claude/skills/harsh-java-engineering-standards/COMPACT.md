---
name: harsh-java-engineering-standards
description: Compact, prompt-injection form of the Java engineering standards. Hard rules only.
---

# Java Engineering Standards

## Mandatory commands

```bash
./mvnw test
./mvnw verify
```

or

```bash
./gradlew test
./gradlew check
```

Plus formatter/checkstyle/spotless/spotbugs/pmd if configured.

## Language and style

- Use project's configured Java version; no unsupported features.
- Immutability: final fields, records for DTOs/value carriers, unmodifiable collections when exposing data.
- Constructor injection only — no field injection.
- Focused classes, package-private where possible.
- `Optional` for return values only — not for fields, DTOs, or parameters.
- No `null` when absence/error should be explicit.
- No static mutable state.
- No broad `catch (Exception)` unless mapping at a boundary.

## Spring/API architecture

- Layers: controller, dto, service, repository, domain, config, exception.
- Controllers: HTTP only (parse, validate, map response).
- Services own business rules and transactions.
- Repositories own persistence.
- DTOs isolate API contracts from domain/persistence.
- Global exception handling maps expected errors consistently.
- Validation annotations on request DTOs.
- Never expose JPA entities directly as API responses.
- `@Transactional` at service boundaries.

## Status codes

- 201 create, 200 read/update, 204 delete-no-body.
- 400 validation/malformed; 404 missing; 409 conflict.
- Consistent error response shape; never leak stack traces or SQL.

## Testing

- JUnit 5.
- Unit-test services with fakes or Mockito where it clarifies behavior.
- Slice tests: `@WebMvcTest` for controllers, `@DataJpaTest` for repositories.
- `@SpringBootTest` only when full context is necessary.
- Testcontainers when real infrastructure matters.
- Cover validation, not found, conflicts, malformed input, happy paths.

## Immediate rejection signals

- Field injection.
- Controller contains business logic.
- Entity returned directly from controller.
- No global exception mapping.
- Tests require full Spring context for simple logic.
- Validation only in comments or frontend assumptions.
- `mvn test`/`gradle test` fails from fresh checkout.
- Archive includes `target`, `build`, `.git`, `.gradle/`, IDE files, OS metadata.
