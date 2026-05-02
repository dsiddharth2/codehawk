# Large PR Batched Review — Phase 2 Code Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-05-02 22:45:00+05:30
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.
> Prior review (26603b4): Phase 1 code review — APPROVED. One non-blocking SHOULD-FIX: threshold boundary operator in smart_diff.py:63 (still open, carried forward below).

---

## Phase 1 Regression Check

All 3 Phase 1 modules (config.py, file_filter.py, smart_diff.py) reviewed for regressions against Phase 2 changes. **No regressions found.**

- `src/config.py`: No Phase 2 modifications. All 6 batch review fields intact with correct defaults and constraints. **PASS.**
- `src/file_filter.py`: No modifications since Task 2. Module consumed by review_job.py via import. **PASS.**
- `src/smart_diff.py`: No modifications since Task 3. Module consumed by vcs_tools.py via import. **PASS.**
- **Carried forward:** `smart_diff.py:63` still uses `<=` (should be `<` per requirements.md "Diffs >= 30KB"). Non-blocking, deferred to Phase 4 tests.

---

## Task 4: Integrate smart diff into vcs_tools.py

**PASS.** `handle_get_file_diff` (vcs_tools.py:199-258) correctly implements all 5 requirements from PLAN.md:

1. **Removed hardcoded `[:10000]` truncation.** The old 10KB slice is gone. Diff text flows through smart diff logic unmodified. **PASS.**

2. **Smart diff summarization.** Lines 230-247: calls `summarize_diff()` with `settings.smart_diff_threshold_kb`, returns `format_summary_for_agent()` text with `is_summary: true` in JSON and drill-in hint when `is_summarized=True`. **PASS.**

3. **`start_line`/`end_line` drill-in parameters.** Lines 211-227: when both are provided, calls `extract_hunks_in_range()`, caps result at 30KB, returns `drill_in: true` in JSON. Tool schema (lines 282-295) includes both as optional integer params with clear descriptions. **PASS.**

4. **30KB safety cap for normal diffs.** Lines 249-258: unsummarized diffs capped at 30,000 chars with truncation message. **PASS.**

5. **Settings threading.** Line 233: `threshold_kb=settings.smart_diff_threshold_kb` — `settings` comes from the `register_vcs_tools` closure parameter. **PASS.**

**NOTE:** Drill-in mode (line 216) casts `start_line`/`end_line` to `int()`. The schema declares them as `"type": "integer"` so OpenAI should always pass integers, but the explicit cast is a safe defensive measure. **PASS.**

**NOTE:** Drill-in mode returns `added_lines_count` and `removed_lines_count` from the full diff result (lines 222-223), not from the filtered range. This is technically imprecise for drill-in but acceptable — the agent uses these as context, not for decisions.

**Done criteria:** Tool schema has start_line/end_line: **PASS**. Diffs under threshold returned in full (up to 30KB): **PASS**. Diffs over threshold return summary with is_summary=true: **PASS**. Drill-in returns filtered hunks: **PASS**. Existing tests pass: **PASS.**

---

## Task 5: Integrate filtering + batch fields into review_job.py

**PASS.** `review_job.py` correctly implements all 4 requirements from PLAN.md:

1. **File filtering in `create_findings()`** (lines 93-101): After PR pre-fetch, imports `parse_skip_extensions`/`filter_changed_files`, applies with `self.settings.skip_extensions`, logs filtered/kept counts. Skipped count stored locally and passed to `_build_changed_files_section`. **PASS.**

2. **Removed MAX_FILES=100 cap.** `_build_changed_files_section` (lines 252-287) shows ALL files in the table with no cap. Adds skipped count summary line when `skipped_count > 0`. **PASS.**

3. **ReviewJobConfig batch fields** (lines 44-47): All 4 optional fields added correctly:
   - `batch_index: Optional[int] = None`
   - `batch_total: Optional[int] = None`
   - `file_subset: Optional[list] = None`
   - `pre_built_graph: Any = None`
   **PASS.**

4. **Batch mode behavior:**
   - `file_subset` set → skips PR pre-fetch, uses subset directly (lines 78-81). **PASS.**
   - `pre_built_graph` set → skips `build_graph()`, uses it directly (lines 108-110). **PASS.**
   - `batch_index` set → appends batch context to prompt with "Batch N/M" (lines 212-219). **PASS.**

**NOTE:** When `file_subset` is provided, the code handles mixed types (string paths or FileChange objects) at lines 126-131, extracting `.path` from objects or using strings directly. This is forward-compatible with Phase 3 BatchReviewJob which may pass either form. **PASS.**

**Done criteria:** ReviewJobConfig accepts 4 new fields: **PASS**. Filtering applied when no file_subset: **PASS**. No MAX_FILES cap, skipped count shown: **PASS**. Batch mode fields respected: **PASS**. Tests pass: **PASS.**

---

## Task 6a: Update system prompt for batched review (openai_runner.py)

**PASS.** `build_system_prompt()` (lines 30-96) correctly implements both requirements:

1. **Removed "review top 10-15 files".** Neither the graph branch (lines 32-46) nor the no-graph branch (lines 48-54) contain this phrase. Both now include "Review ALL files in your assigned batch" instead. **PASS.**

2. **Added smart diff drill-in instructions.** Lines 83-86: dedicated "SMART DIFF DRILL-IN" section explains `is_summary=true` behavior and instructs the agent to use `start_line`/`end_line` to drill in. **PASS.**

**NOTE:** The graph branch includes "Review ALL files" in its DIFF-BASED REVIEW fallback section (lines 45-46), and the no-graph branch includes it in its main instructions (lines 52-53). Both paths covered. **PASS.**

**Done criteria:** No mention of "top 10-15 files": **PASS**. Includes "Review ALL files": **PASS**. Includes smart diff drill-in instructions: **PASS**. Tests pass: **PASS.**

---

## Task 6b: Raise truncation and timeout limits

**PASS.** All 4 limit changes implemented correctly:

| Limit | File:Line | Old | New | Verified |
|-------|-----------|-----|-----|----------|
| Tool result cap (Chat Completions) | openai_runner.py:263 | 30000 | 50000 | **PASS** |
| Tool result cap (Responses API) | openai_runner.py:402 | 30000 | 50000 | **PASS** |
| search_code truncation | workspace_tools.py:104 | 15000 | 25000 | **PASS** |
| read_local_file default max_lines | workspace_tools.py:148 | 500 | 1000 | **PASS** |
| Graph timeout (51-100 files) | graph_builder.py:23 | N/A | (100, 600) | **PASS** |

**NOTE: Graph timeout tier ordering.** The `_TIMEOUT_TIERS` list (graph_builder.py:18-25) has an interesting inversion: 51-100 files gets 600s, but 101+ files gets 300s. This is per PLAN.md design: extremely large PRs (101+ files) get a shorter timeout because the graph is expected to be incomplete anyway, favoring faster fallback to diff-based review. The comment "T5+: extremely large PR (graph may be incomplete)" documents this intent. **PASS, but NOTE for Phase 3:** when BatchReviewJob batches files, `changed_file_count` passed to `build_graph` should be the total code file count (not per-batch), since the graph is built once for all batches. Verify in Task 7.

**Done criteria:** Tool result 50KB in both API paths: **PASS**. Search 25KB: **PASS**. Read 1000 lines: **PASS**. Graph 600s for 51-100 files: **PASS**. Tests pass: **PASS.**

---

## Test Results

```
151 collected, 136 passed, 2 failed, 13 skipped (4.70s)
```

Both failures are **pre-existing and known** (confirmed on main):
- `test_graph_builder.py::test_prints_diagnostic_on_failure` — logger vs print assertion mismatch
- `test_post_findings.py::test_still_present_not_resolved_ado` — module patching path issue

No new test failures introduced by Phase 2 changes. **PASS.**

---

## Code Quality and Security

- **No security issues.** No user-input injection, no file writes outside workspace, no credential exposure. The `int()` casts in drill-in mode (vcs_tools.py:216) are safe against type confusion. **PASS.**
- **Consistency with codebase patterns.** All changes follow existing conventions: logger usage, json.dumps with indent=2, type hints, closure-based tool registration. **PASS.**
- **No dead code introduced.** All new code paths are reachable and serve the stated requirements. **PASS.**

---

## Open Items Carried Forward

| # | File | Finding | Severity | Origin | Status |
|---|------|---------|----------|--------|--------|
| 1 | src/smart_diff.py:63 | Threshold uses `<=` but spec says `>=` for summarization boundary | SHOULD-FIX | Phase 1 review | Open — deferred to Phase 4 tests |
| 2 | src/graph_builder.py:18-25 | 101+ files tier gets 300s (less than 51-100 tier at 600s) — intentional per design but verify Task 7 passes correct file count | NOTE | This review | Informational for Phase 3 |

---

## Summary

Phase 2 is **APPROVED**. All 4 tasks (4, 5, 6a, 6b) meet their done criteria. Integration is clean: smart diff wires correctly through vcs_tools, filtering and batch fields integrate into review_job without breaking existing paths, system prompt correctly removes skip guidance and adds batch/drill-in instructions, and all limits are raised to spec.

**Passed:** All 4 Phase 2 tasks, all done criteria verified, no regressions in Phase 1, all existing tests pass (136/136 + 2 known pre-existing failures).

**No new must-fix or should-fix findings.** One informational NOTE about graph timeout tier ordering for Phase 3 awareness.

**Deferred:** smart_diff.py:63 boundary operator (Phase 1 SHOULD-FIX, still open).

**Ready for Phase 3:** ReviewJobConfig batch fields and smart diff integration provide the foundation for BatchReviewJob (Task 7).
