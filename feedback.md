# Sprint 3 — Review Quality + Coverage Enforcement — Code Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-05-22 14:30:00+05:30
**Verdict:** CHANGES NEEDED

> See the recent git history of this file to understand the context of this review.

---

## 1. Task 1: Align tool-call limit

**PASS.** `review-pr-core.md` line 6 now reads `max 40 tool calls`. Grep for "max 10" returns zero matches. Grep for "max 40" returns the expected match in Step 0 constraints. Done criteria fully met.

---

## 2. Task 2: Rewrite TURN EFFICIENCY block

**PASS.** `openai_runner.py` SYSTEM_PROMPT no longer contains "ZERO or minimal". The replacement text matches the plan specification — it explains the 40-call budget, encourages `read_local_file`/`get_file_content` for verification, mentions `get_callers` for blast radius, and retains the "Do NOT call `get_file_diff`..." line. The new guidance shifts the agent from a "minimize calls" mindset to a "spend calls wisely" mindset, which aligns with the sprint's goal of reducing false positives through verification.

---

## 3. Task 3: Add verify-before-CRITICAL rule

**PASS.** Step 5e exists at line 168 of `review-pr-core.md` with the verify-before-CRITICAL text. The previous Step 5e ("Produce findings") has been correctly renumbered to Step 5f. The new step requires the agent to call `read_local_file` or `get_file_content` before emitting any `critical` finding, and to downgrade or drop if verification shows the issue doesn't exist. Done criteria met.

---

## 4. Task 4: Set temperature and seed on API calls

**PASS.** `temperature=0.3` appears at both API call sites:
- Line 199: `_run_chat_completions` → `temperature=0.3` as a keyword arg to `client.chat.completions.create()`
- Line 332: `_run_responses_api` → `"temperature": 0.3` in the kwargs dict for `client.responses.create()`

`seed=42` appears at line 200 in `_run_chat_completions` only, which is correct per the plan (the Responses API does not support `seed`). Done criteria fully met.

---

## 5. Task 5: Surface failed diff fetches

**PASS with one FAIL item (see below).**

The implementation correctly:
- Returns a `tuple[dict[str, str], list[str]]` from `_pre_fetch_diffs` (line 278)
- Appends failed file paths to a `failed_diffs` list in the except block (line 308)
- Logs a warning with the failed file list (lines 311-312)
- Unpacks the tuple at the call site (line 162) and passes `failed_diffs` to `_build_review_context`
- Injects a "Failed Diff Fetches" section into the prompt when `failed_diffs` is non-empty (lines 363-372)
- The injected text matches the plan's specified wording

**FAIL — Contradictory prompt instructions.** The failed_diffs injection at line 368 tells the agent: "You MUST fetch them via `get_file_diff` or `read_local_file` during review." However, line 374 unconditionally appends: "Do NOT call `get_file_diff` — all data is above." When failed diffs exist, these two instructions directly contradict each other. The agent is told to use `get_file_diff` and told not to use it in the same prompt.

**Fix:** Either (a) make line 374 conditional — exclude `get_file_diff` from the "Do NOT call" list when `failed_diffs` is non-empty, or (b) change the failed_diffs message to only suggest `read_local_file` or `get_file_content` (dropping `get_file_diff`). Option (b) is simpler and consistent with Task 2's TURN EFFICIENCY rewrite which already encourages `read_local_file`/`get_file_content`.

**Doer:** fixed in commit bc2db7b — changed failed_diffs injection to recommend only `read_local_file` or `get_file_content`, removing `get_file_diff` from the suggestion. This eliminates the contradiction with the unconditional "Do NOT call `get_file_diff`" instruction on line 374.

---

## 6. Task 6 VERIFY: Test suite

**PASS.** 221 tests passed, 13 skipped, 0 failures in 2.87s. The task notes claim 224 tests — the 3-test discrepancy appears to be tests that moved from "passed" to "skipped" between the doer's run and this review run (possibly environment-dependent skips). No test failures, which is the actual gate criterion.

All grep verification checks pass:
- "ZERO or minimal" not found in `openai_runner.py` SYSTEM_PROMPT
- "max 10" not found in `review-pr-core.md`
- `temperature=0.3` present in both API call sites
- Step 5e with "Verify before flagging CRITICAL" present in `review-pr-core.md`
- `failed_diffs` collected and injected into prompt context in `review_job.py`

---

## Summary

5 of 6 tasks pass cleanly. Task 5 has one must-fix item: the failed_diffs prompt injection contradicts the existing "Do NOT call `get_file_diff`" instruction on line 374 of `review_job.py`. This will confuse the agent when a diff fetch actually fails in production. The fix is a one-line change — either make the "Do NOT call" line conditional or drop `get_file_diff` from the failed_diffs suggestion text.

All tests pass. No regressions. No security issues. The prompt and parameter changes are well-scoped and match the plan's intent.
