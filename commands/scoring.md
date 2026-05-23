# Scoring Reference — codehawk

This file is the authoritative reference for how findings are weighted into a PR quality score. `post_findings.py` reads penalty values from environment variables (defaulting to the values below). The review agent uses this file to calibrate severity assignments.

---

## Severity Levels

| Severity | Meaning | Action required |
|----------|---------|-----------------|
| `critical` | Defect that will cause a bug, security vulnerability, or data loss if merged | Must fix before merge. Blocks pipeline when `fail_on_critical: true`. |
| `warning` | Significant code quality issue that should be addressed, but does not break correctness immediately | Should fix before merge. Contributes to star rating. |
| `suggestion` | Minor improvement — style, clarity, or optional best practice | Nice to have. Low weight in scoring. |

**Assignment rules for the review agent:**
- Use `critical` only when you have high confidence (≥0.85) the defect will cause a real problem.
- Use `warning` for issues that are clearly wrong but may be mitigated by other code.
- Use `suggestion` when the code works but could be improved.
- Do not use severity to express urgency for the developer to read the finding — severity is a scoring input.

---

## Category Definitions

| Category | Applies to | Examples |
|----------|-----------|---------|
| `security` | Vulnerabilities, auth flaws, injection, secrets, insecure defaults | SQL injection, XSS, hardcoded credentials, broken access control, insecure deserialization |
| `performance` | Algorithmic complexity, N+1 queries, cache misses, blocking I/O | O(n²) loop, unbatched DB query, synchronous HTTP call on hot path |
| `best_practices` | Robustness, design patterns, general correctness | Swallowed exception, race condition, unvalidated input at boundary |
| `architecture` | API design, interfaces, coupling, separation of concerns | Circular dependency, business logic in controller, DTO leaking internals |
| `correctness` | Logic errors, wrong behavior, data loss | Off-by-one, incorrect state transition, wrong formula |
| `error_handling` | Missing or incorrect error handling | Unhandled exception, silent failure, missing null check on external data |
| `code_style` | Naming, formatting, readability | Poor variable name, inconsistent indentation, unused import |
| `documentation` | Missing or inaccurate docs, changelog gaps | Wrong docstring, missing `@param`, inaccurate README claim |
| `testing` | Test coverage gaps identified from test gap analysis | Missing test for new public method, untested edge case, missing integration test |

**Scoring note:** `code_style`, `documentation`, and `testing` categories have a default penalty of 0.0 (zero weight in the star rating) unless overridden in `.codereview.yml`. They are still posted as inline comments but do not affect the CI gate or star rating.

---

## Penalty Matrix (Default Values)

These are the default penalty points per finding. Values can be overridden via environment variables.

| Category | critical | warning | suggestion |
|----------|----------|---------|------------|
| `security` | **5.0** | 4.0 | 2.0 |
| `performance` | **3.0** | 2.0 | 1.0 |
| `best_practices` | **2.0** | 1.0 | 0.5 |
| `architecture` | **2.0** | 1.0 | 0.5 |
| `correctness` | **2.0** | 1.0 | 0.5 |
| `error_handling` | **1.5** | 0.75 | 0.25 |
| `code_style` | 0.0 | 0.0 | 0.0 |
| `documentation` | 0.0 | 0.0 | 0.0 |
| `testing` | 0.0 | 0.0 | 0.0 |

Penalty points accumulate additively. The total penalty maps to a star rating:

| Total Penalty | Star Rating | Quality Level |
|--------------|-------------|--------------|
| 0.0 | ⭐⭐⭐⭐⭐ | Perfect |
| 0.1 – 5.0 | ⭐⭐⭐⭐☆ | Excellent |
| 5.1 – 15.0 | ⭐⭐⭐☆☆ | Good |
| 15.1 – 30.0 | ⭐⭐☆☆☆ | Needs Work |
| 30.1 – 50.0 | ⭐☆☆☆☆ | Poor |
| 50.1+ | ☆☆☆☆☆ | Critical |

---

## Review Mode Multipliers

When a review mode is active, severity is escalated before scoring:

| Mode | Affected category | Escalation |
|------|------------------|-----------|
| `security` | `security` | `warning` → `critical` (effective ×2 penalty) |
| `performance` | `performance` | `warning` → `critical` (effective ×2 penalty) |
| `architecture` | `best_practices` | `suggestion` → `warning` (effective ×1.5 penalty) |
| `migration` | all | All findings → `critical` (maximum gating) |

Multipliers are applied by `post_findings.py` (`apply_mode_multipliers` in `pr_scorer.py`). The review agent does not apply multipliers — it assigns the base severity. The agent should calibrate severity knowing that mode multipliers will escalate where applicable.

---

## Confidence Expectations

All findings must include a `confidence` score: 0.0-1.0.

| Confidence | Meaning |
|-----------|---------|
| 0.90 – 1.00 | Certain. The issue is unambiguous. Would fail any reasonable code review. |
| 0.80 – 0.89 | High confidence. The pattern is almost certainly wrong given context. |
| 0.70 – 0.79 | Moderate confidence. The issue is likely real, but context may explain it. |
| 0.50 – 0.69 | Low confidence. Suspicious pattern, but may be intentional or handled elsewhere. Still posted but contributes less to scoring. |
| < 0.50 | Speculative. Do not include in findings. |

**Filter threshold:** `post_findings.py` drops all findings with `confidence < 0.5` before posting. Set confidence honestly — do not inflate to bypass the filter. Security findings at 0.6+ confidence are worth posting — the cost of missing a real vulnerability exceeds the cost of a false positive comment.

**Calibration guidance:**
- If you need to read 3+ additional files to confirm a finding is real, it's probably ≤0.75.
- If the issue is a known vulnerability class (OWASP Top 10) and you can see the exact input → output flow, it's probably ≥0.90.
- If a project convention in `.codereview.md` explicitly names this as a required pattern and it's missing, it's probably ≥0.85.

---

## Hard Caps (enforced by post_findings.py)

- **max 30 findings** — excess findings are dropped (lowest confidence first, then lowest severity)
- **max 5 per file** — excess findings in a single file are dropped (lowest confidence first)

The review agent must also respect these caps when composing findings.json. Do not rely on post_findings.py to trim — the agent should self-cap to ensure the most important findings survive.
