# Architecture Review Checklist

## API Design
- [ ] Public API changes are backward-compatible or versioned
- [ ] Breaking changes documented in PR description
- [ ] Return types use interfaces, not concrete classes (e.g., IReadOnlyList over List)

## Coupling + Cohesion
- [ ] No circular dependencies between modules
- [ ] New dependencies follow the existing dependency direction
- [ ] Cross-layer calls go through defined interfaces

## Separation of Concerns
- [ ] Business logic not in controllers/handlers
- [ ] Data access not in UI layer
- [ ] Configuration not hardcoded

## Contracts
- [ ] DTOs/models don't expose internal implementation
- [ ] Serialization attributes present on public models
- [ ] Nullable annotations consistent
