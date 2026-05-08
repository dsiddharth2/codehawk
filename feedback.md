# Review Modes — Phase 1 Code Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-05-08
**Verdict:** APPROVED

> Phase 1: Foundation — Enum, Config, CLI

---

## Checklist

| Item | Status | Notes |
|------|--------|-------|
| ReviewMode enum has FULL, VERIFY_FIXES, CHECK_NEW | PASS | `ReviewMode(str, Enum)` with correct values `"full"`, `"verify_fixes"`, `"check_new"` |
| review_mode threaded through ReviewJobConfig | PASS | `review_mode: ReviewMode = ReviewMode.FULL` field present, default correct |
| review_mode threaded through BatchReviewJob.__init__ | PASS | Accepted as kwarg, stored as `self.review_mode` |
| CLI flags mutually exclusive via argparse | PASS | `add_mutually_exclusive_group()` used; passing both flags raises argparse error |
| CHECK_NEW mode skips _fetch_previous_findings() | PASS | `batch_review_job.py:88-90` — conditional skip with log message |
| review_mode passed to ALL ReviewJobConfig constructors | PASS | Both single-session path (line 117) and batched path (line 252) include `review_mode=self.review_mode` |
| No regressions in existing behavior | PASS | 190 tests pass, 13 skipped (same as baseline) |
| Code quality consistent with existing patterns | PASS | See notes below |

---

## Code Quality Notes

1. **ReviewMode enum** (`src/models/review_models.py:12-15`): Clean `(str, Enum)` pattern enables JSON serialization and string comparison. Placed at module top before other models — good organization.

2. **ReviewJobConfig** (`src/review_job.py:88`): `review_mode` field added at the end of the dataclass with a sensible default (`ReviewMode.FULL`), preserving backward compatibility for all existing callers.

3. **BatchReviewJob** (`src/batch_review_job.py:46`): `review_mode` parameter follows the same default pattern. The CHECK_NEW conditional (lines 88-93) is clear and correctly placed before `_fetch_previous_findings()`.

4. **CLI** (`src/run_agent.py:31-43`): `add_mutually_exclusive_group()` is the correct argparse pattern. Flag-to-enum resolution (lines 46-52) is straightforward. The `review_mode` is correctly passed to the `BatchReviewJob` constructor.

5. **Consistency**: Both `ReviewJobConfig` constructors in `BatchReviewJob` (single-session at line 105 and batched at line 237) correctly pass `review_mode=self.review_mode`.

---

## Observations (non-blocking)

- The standalone `create_findings()` path in `ReviewJob` (lines 715-726) still fetches previous findings when `previous_findings is None`, regardless of `review_mode`. This is acceptable for Phase 1 since the CHECK_NEW guard is in `BatchReviewJob.run()` which is the primary entry point. Phase 2 (Task 3) will add the mode-conditional guard inside `ReviewJob.create_findings()` as specified in the plan.

---

## Summary

Phase 1 is correctly implemented. The `ReviewMode` enum is well-structured, properly threaded through config and CLI, flags are mutually exclusive, and CHECK_NEW mode correctly skips previous findings fetch in the batch orchestrator. All 190 existing tests pass with no regressions. Ready to proceed to Phase 2.

---

# Review Modes — Phase 2 Code Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-05-08
**Verdict:** APPROVED

> Phase 2: Conditional Behavior — Prompt, Write Guards, Post-Findings

---

## Checklist

| Item | Status | Notes |
|------|--------|-------|
| create_findings() skips prior-findings fetch for CHECK_NEW | PASS | `review_job.py:89` — condition `self.config.review_mode != ReviewMode.CHECK_NEW` guards the standalone fetch path |
| _build_prompt() injects VERIFY_FIXES instructions | PASS | `review_job.py:450-451` — appends `_build_verify_only_instructions()` containing "VERIFY-ONLY MODE" |
| _build_prompt() injects CHECK_NEW instructions | PASS | `review_job.py:452-453` — appends `_build_check_new_instructions()` containing "FRESH REVIEW MODE" |
| _build_verify_only_instructions() clear and forceful | PASS | Lines 368-378 — explicit constraints: findings[] MUST be empty, populate fix_verifications[], skip Steps 3-5 |
| _build_check_new_instructions() clear and forceful | PASS | Lines 380-389 — explicit constraints: fix_verifications[] MUST be empty, skip Step 6, no prior findings |
| _write_findings() strips findings[] for VERIFY_FIXES | PASS | `review_job.py:541-544` — forces `data["findings"] = []` and appends `"verify_fixes"` to review_modes |
| _write_findings() strips fix_verifications[] for CHECK_NEW | PASS | `review_job.py:545-548` — forces `data["fix_verifications"] = []` and appends `"check_new"` to review_modes |
| post_findings detects verify-only mode | PASS | `post_findings.py:816` — `is_verify_only = "verify_fixes" in review_modes and not findings_file.findings` |
| Verify-only skips inline comments | PASS | `post_findings.py:876` — entire inline posting loop guarded by `if not is_verify_only` |
| Verify-only still processes fix verifications | PASS | `post_findings.py:888-893` — fix verification handling is unconditional, not gated by is_verify_only |
| Summary title "Fix Verification Only" for verify-only | PASS | `post_findings.py:557-558` — title set to "AI Code Review — Fix Verification Only" |
| Gate passes for verify-only (no new findings) | PASS | `post_findings.py:897-898` — hard-coded `{"passed": True, "reasons": []}` |
| FULL mode behavior completely unchanged | PASS | All mode-conditional code uses explicit VERIFY_FIXES/CHECK_NEW checks; FULL falls through to existing paths |
| Confidence filtering skipped for verify-only | PASS | `post_findings.py:819-821` — short-circuits to empty list |
| Capping skipped for verify-only | PASS | `post_findings.py:851` — ternary sets capped to `[]` |
| CR-ID dedup fetch skipped for verify-only | PASS | `post_findings.py:858` — `is_verify_only or dry_run` produces empty set |
| Findings section suppressed in summary for verify-only | PASS | `post_findings.py:716` — `if filtered_findings and not is_verify_only` |
| Phase 1 not regressed | PASS | Phase 1 files (review_models.py, run_agent.py, batch_review_job.py) untouched in Phase 2 commits |
| All existing tests pass | PASS | 190 passed, 13 skipped (identical to baseline) |

---

## Code Quality Notes

1. **create_findings() guard** (`review_job.py:89`): Clean single-line condition addition. The `CHECK_NEW` guard at both the `BatchReviewJob` level (Phase 1) and the standalone `ReviewJob` level (Phase 2) provides complete coverage — addresses the Phase 1 observation.

2. **Prompt injection** (`review_job.py:450-453`): Mode instructions are appended after `previous_findings` section and before config section, which is the correct position — the agent sees mode constraints immediately after the prior findings table. Both instruction methods use bold headers and imperative language.

3. **Defense-in-depth** (`review_job.py:539-548`): `setdefault` for review_modes is defensive against missing keys. Strips the correct field for each mode and ensures the mode tag is present in review_modes for downstream consumers (post_findings).

4. **post_findings verify-only detection** (`post_findings.py:816`): Uses `not findings_file.findings` rather than checking review_modes alone — this means a VERIFY_FIXES run where the agent somehow produced findings (before write guards strip them) would still be detected correctly by the double condition. Good defensive design.

5. **Gate bypass** (`post_findings.py:897-898`): Hard-coded pass is correct — verify-only runs have no findings to gate on. The gate still loads .codereview.yml (line 896) which is slightly wasteful but harmless.

---

## Observations (non-blocking)

1. **Duplicate step number comment** (`post_findings.py:895,902`): Steps 11 and 12 in the `run()` function both start at `# 11.` after the Phase 2 renumbering. The second `# 11` (line 902) should be `# 12`. Cosmetic only — no logic impact.

2. **Scoring still runs for verify-only** (`post_findings.py:870-871`): `apply_mode_multipliers` and `calculate_pr_score` run on the empty `capped` list, producing a perfect 5-star score. This is correct behavior (no penalty deductions = best score), but the score is somewhat meaningless for verify-only runs. The summary title already disambiguates this for users.

---

## Summary

Phase 2 is correctly implemented. All three components — prompt injection, write guards, and post_findings mode awareness — work together as a defense-in-depth chain: the prompt tells the agent what to do, write guards enforce it regardless of agent compliance, and post_findings adapts its behavior to the resulting mode-tagged findings file. FULL mode code paths are untouched. All 190 existing tests pass with no regressions. Ready to proceed to Phase 3 (tests).
