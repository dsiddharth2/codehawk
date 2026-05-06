# Review Mode: Standard

**Applies when:** No specialized mode signal detected (default mode).

**Severity multipliers:** None (1×). All findings use base penalty matrix from `commands/scoring.md`.

---

## Checklist

### Correctness
- [ ] Logic errors: off-by-one, null dereferences, incorrect operator precedence, wrong conditional direction
- [ ] Error paths: exceptions swallowed silently, missing error propagation, incorrect return values on failure
- [ ] Concurrency: race conditions on shared state, missing locks, non-atomic check-then-act patterns
- [ ] State management: stale state used after mutation, missing resets, incorrect initialization order

### Code Patterns
- [ ] Duplication: copy-pasted logic that diverges in maintenance — extract if the copies will evolve together
- [ ] Naming: misleading variable/function names that contradict actual behavior
- [ ] Complexity: functions exceeding single-responsibility; deeply nested conditionals that can be flattened
- [ ] Dead code: unreachable branches, unused parameters, leftover debug code

### Test Coverage
- [ ] Changed logic has corresponding test changes — if behavior changed, tests must change
- [ ] Happy path AND failure path covered for new functions
- [ ] Edge cases: empty collections, zero values, maximum values, concurrent calls
- [ ] Tests are testing behavior, not implementation (not brittle to refactors)

### Naming and API Design
- [ ] Public API additions: are they necessary? Do they belong at this layer?
- [ ] Parameter types: are any `Any`, `object`, or `dict` where a typed dataclass would be safer?
- [ ] Return types: consistent — do not mix `None` and exception for the same error class

### Error Handling
- [ ] Exceptions caught and re-raised with context (`raise X from e`)
- [ ] No bare `except:` or `except Exception:` without logging
- [ ] Validation at system boundaries (user input, external API responses) — not deep inside business logic

### Edge Cases
- [ ] Empty input: empty string, empty list, zero, None
- [ ] Boundary values: first/last element, min/max int, exact threshold
- [ ] Missing resources: file not found, DB row missing, API returning 404 or 5xx

---

## Quality Bar

Flag every issue a thorough senior reviewer would raise, including:
- **Code style:** unused imports/usings, inconsistent naming conventions, null-forgiving operators, terse parameter names (`ct` vs `cancellationToken`)
- **Documentation:** missing XML docs on public APIs, misleading method/test names
- **Interface design:** `List<>` vs `IReadOnlyList<>`, mutable vs immutable collections, missing guard clauses

Use `code_style` or `documentation` categories and `suggestion` severity for these. They don't affect the CI gate score but provide valuable feedback.

Do not flag:
- Patterns the developer marked with `# cr: intentional`
- Patterns that are standard for this codebase (check `.codereview.md`)
