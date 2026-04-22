# Feature: Penalty-Based Scoring

## Overview

The PR scorer converts a list of findings into a 1–5 star rating. Scoring is penalty-based: each finding subtracts from a perfect score. The scorer is deterministic and has no LLM dependency.

## Severity Penalty Weights

| Severity | Penalty |
|----------|---------|
| critical | highest |
| warning | medium |
| suggestion | small |
| good | 0 (positive signal, no deduction) |

Exact penalty values are defined in the penalty matrix in `src/config.py` (ported from the old codebase).

## Star Rating Thresholds

Star thresholds are configurable via `src/config.py`. Default mapping (approximate):
- 5 stars — no or minimal findings
- 4 stars — a few warnings, no criticals
- 3 stars — several warnings
- 2 stars — critical findings present
- 1 star — multiple criticals or score below floor

## Mode Multipliers

`PRScorer.apply_mode_multipliers(findings, review_modes)` modifies penalty values before summing:

```python
# security mode: double security-category penalties
# performance mode: double performance-category penalties
# architecture mode: best_practices × 1.5
# migration mode: all findings elevated to at least critical severity
```

When multiple modes are active, the strictest multiplier per finding is applied.

## Score Comparison (Re-push)

`ScoreComparisonService.format_as_markdown()` produces a before/after comparison when fix verifications are present:

```
Before fix:  ★★★☆☆  3/5 stars  (4 findings: 2 critical, 2 warning)
After fix:   ★★★★☆  4/5 stars  (2 findings: 0 critical, 2 warning)
Improvement: +1 star — 2 findings resolved
```

This comparison is included in the PR summary comment on re-push.

## Zero-Finding Baseline

0 findings = 5 stars. The scorer never exceeds 5 stars even with "good" signals.

## API

```python
from pr_scorer import PRScorer
scorer = PRScorer(settings)
scorer.apply_mode_multipliers(findings, review_modes=["security"])
result = scorer.calculate_pr_score(findings)
# result.star_rating: int 1-5
# result.penalty_total: float
# result.findings_by_severity: dict
```
