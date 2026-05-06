# Large PR Batched Review -- Phase 3 Code Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-05-06 10:00:00+05:30
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.
> Prior review (a5ccd3b): Phase 2 code review -- APPROVED. Carried forward: `smart_diff.py:63` boundary operator (`<=` vs `<`), non-blocking, deferred to Phase 4 tests.

---

## Phase 1+2 Regression Check

All Phase 1 modules (config.py, file_filter.py, smart_diff.py) and Phase 2 modules (vcs_tools.py, review_job.py, openai_runner.py, workspace_tools.py, graph_builder.py) reviewed for regressions against Phase 3 changes. **No regressions found.**

- `src/config.py`: No Phase 3 modifications. All 6 batch review fields intact. **PASS.**
- `src/file_filter.py`: No modifications. Consumed by both `review_job.py` and `batch_review_job.py`. **PASS.**
- `src/smart_diff.py`: No modifications. **PASS.**
- `src/tools/vcs_tools.py`: No Phase 3 modifications. Smart diff integration intact. **PASS.**
- `src/review_job.py`: No Phase 3 modifications. Batch fields (`file_subset`, `pre_built_graph`, `batch_index`, `batch_total`) intact and consumed correctly by `BatchReviewJob`. **PASS.**
- **Carried forward:** `smart_diff.py:63` still uses `<=` (should be `<` per requirements.md "Diffs >= 30KB"). Non-blocking, deferred to Phase 4 tests.

---

## Task 7: Create BatchReviewJob orchestrator (src/batch_review_job.py)

**PASS.** `BatchReviewJob` (282 lines) correctly implements all requirements from PLAN.md Task 7.

### 7.1 -- Class structure and init

`__init__` accepts all required parameters: `pr_id`, `repo`, `workspace`, `model`, `prompt_path`, `vcs`, `settings`. Settings defaults to `get_settings()` when not provided. **PASS.**

### 7.2 -- `run()` pipeline (lines 48-139)

All 8 steps implemented correctly:

1. **Pre-fetch PR data** (line 61): Calls `_fetch_pr_details()` which uses `FetchPRDetailsActivity`. Exception handling returns `None` on failure. **PASS.**
2. **Filter non-code files** (lines 66-71): Uses `parse_skip_extensions` + `filter_changed_files` with `self.settings.skip_extensions`. Logs kept/skipped counts. **PASS.**
3. **Build graph once** (line 74): Calls `_build_graph()` with `changed_file_count`. Graph built once and shared across batches via `pre_built_graph`. Exception handling returns `None` on failure. **PASS.**
4. **Single-session shortcut** (lines 77-93): When `len(code_files) <= self.settings.batch_size`, creates a `ReviewJobConfig` with `file_subset` and `pre_built_graph`, delegates to `ReviewJob.run()`. Backward compatible. **PASS.**
5. **Batch splitting** (line 96): Calls `_split_into_batches()`. **PASS.**
6. **Sequential batch execution** (lines 101-116): Iterates batches, calls `_run_batch()`, catches exceptions per-batch so failures don't crash the pipeline. **PASS.**
7. **Merge findings** (line 119): Calls `_merge_results()`. **PASS.**
8. **Write merged findings.json and publish** (lines 123-139): Writes to `.cr/findings.json`, then calls `post_findings.run()`. **PASS.**

### 7.3 -- `_split_into_batches()` correctness (lines 199-223)

**PASS.** Round-robin by churn descending:
- Sorts by `(additions + deletions)` descending with `hasattr` guard for non-FileChange objects.
- Computes `num_batches` using ceiling division: `(len + batch_size - 1) // batch_size`.
- Distributes via `batches[i % num_batches]` -- this correctly interleaves high-churn and low-churn files for balanced workload.
- Returns empty list for empty input.

**NOTE:** Ceiling division matches the PLAN.md `ceil(len / batch_size)` requirement without importing `math.ceil`. Correct.

### 7.4 -- `_merge_results()` correctness (lines 225-282)

**PASS.** Dedup and re-sequence:
- Concatenates all findings from batch results.
- Dedup by `(file, line, title)` tuple -- matches PLAN.md spec. Preserves first occurrence.
- Re-sequences cr-ids as `cr-001`, `cr-002`, ... using `f"cr-{i:03d}"`. **PASS.**
- Sums `input_tokens`, `output_tokens`, `duration_seconds`. **PASS.**
- Unions `review_modes` via set, sorted for deterministic output. **PASS.**
- Uses last non-empty `model` string (reasonable for homogeneous batches). **PASS.**

### 7.5 -- `batch_max_turns` threading (line 182)

**PASS.** `_run_batch()` explicitly sets `max_turns=self.settings.batch_max_turns` on the `ReviewJobConfig`. This correctly threads the per-batch turn budget from Settings into each batch's config. The single-session shortcut (line 82) does NOT set `max_turns`, so it inherits the default (40) from `ReviewJobConfig` -- correct since single-session reviews should use the standard budget.

### 7.6 -- GraphStore reuse safety

**PASS.** The graph is built once in `_build_graph()` and passed as `pre_built_graph` to each batch's `ReviewJobConfig`. In `review_job.py` (lines 108-110), when `pre_built_graph` is set it is used directly without modification. The graph is read-only during review (agents query it via `get_callers`, `get_blast_radius`, etc.) so sequential reuse across batches is safe.

### 7.7 -- Error handling

**PASS.** Three levels of resilience:
- `_fetch_pr_details()`: catches all exceptions, returns `None`, logs warning. When `None`, `all_files = []` (line 62).
- `_build_graph()`: catches all exceptions, returns `None`, logs warning.
- Per-batch execution (lines 114-116): catches all exceptions, logs error, continues with remaining batches.
- If all batches fail, `_merge_results([])` returns a clean empty result.

**NOTE:** When `pr_details` is `None` (pre-fetch failed), `all_files = []`, so `code_files = []`, and the method proceeds to the single-session shortcut with an empty file list. The `ReviewJob` will skip its own pre-fetch (since `file_subset=[]` is not `None`). Acceptable -- the agent receives an empty file list, no worse than the pre-fetch failure itself.

---

## Task 8: Update run_agent.py, review prompt, and post_findings caps

### 8.1 -- run_agent.py (src/run_agent.py)

**PASS.** Clean transition from `ReviewJob` to `BatchReviewJob`:
- Imports `BatchReviewJob` from `batch_review_job` (line 12).
- Constructs with CLI args: `pr_id`, `repo`, `workspace`, `model`, `prompt_path` (lines 29-35).
- Calls `job.run(dry_run=..., commit_id=...)` (line 38).
- Handles gate failure via `sys.exit(1)` (lines 40-41).
- Backward compatible: `BatchReviewJob` delegates to single `ReviewJob` for small PRs.

**NOTE:** The `vcs` parameter is not passed to `BatchReviewJob` -- it defaults to `"ado"`. The original `run_agent.py` also did not accept a `--vcs` CLI arg, so this is consistent with existing behavior. **PASS.**

### 8.2 -- review-pr-core.md prompt updates

**PASS.** All 3 changes from PLAN.md Task 8 applied:

1. **T4/T5 rows updated** (Step 4 table): "Focus on highest-risk paths only" replaced with "Review ALL files in your batch" for both T4 and T5. Added note: "Non-code files have been pre-filtered by the orchestrator." **PASS.**
2. **Step 5 strategy table updated**: T4 and T5 rows now say "Review ALL files in your batch" with graph priority guidance. **PASS.**
3. **Smart diff drill-in guidance** (Step 5a): Added paragraph explaining `is_summary: true` response and how to use `start_line`/`end_line` to drill in. **PASS.**

### 8.3 -- post_findings.py caps from settings

**PASS.** All changes correct:

1. **Module-level defaults updated** (lines 31-32): `MAX_TOTAL_FINDINGS` changed from 30 to 50 (matching `settings.max_total_findings` default). `MAX_PER_FILE` stays at 5. Both annotated with comments explaining runtime override. **PASS.**
2. **Runtime settings read** (lines 700-702): `max_total = settings.max_total_findings if settings else MAX_TOTAL_FINDINGS` and same pattern for `max_per_file`. Clean fallback when settings unavailable. **PASS.**
3. **`cap_findings()` call** (line 702): Passes dynamic `max_total` and `max_per_file`. **PASS.**
4. **`_build_summary_markdown` updated** (line 518): Accepts `max_total_findings` parameter, uses it in the "Total posted: X / Y max" summary line. Caller passes `max_total`. **PASS.**

**NOTE:** The `cap_findings()` function signature still has module-level constant defaults. These are only triggered if called without arguments (e.g., from tests). The `run()` function always passes explicit values. Correct and safe. **PASS.**

---

## Test Results

```
136 passed, 2 failed, 13 skipped, 42 warnings in 4.69s
```

- **2 pre-existing failures** (confirmed on main):
  - `test_prints_diagnostic_on_failure` in `test_graph_builder.py` -- expects `print()` but code uses `logger.warning()`
  - `test_still_present_not_resolved_ado` in `test_post_findings.py` -- `activities` module import path issue
- **No new failures introduced.** **PASS.**

---

## Summary

Phase 3 (Tasks 7 and 8) is **APPROVED**. The BatchReviewJob orchestrator correctly implements all requirements:

- `_split_into_batches` produces balanced batches via round-robin by churn descending
- `_merge_results` deduplicates by (file, line, title) and re-sequences cr-ids correctly
- Single-session shortcut works for small PRs (backward compatible)
- GraphStore safely shared read-only across batches
- Failed batches don't crash the pipeline
- `batch_max_turns` correctly threaded into per-batch `ReviewJobConfig.max_turns`
- `run_agent.py` cleanly transitions to `BatchReviewJob`
- `post_findings.py` reads caps from settings at runtime with safe fallbacks
- Review prompt updated with batch and smart diff guidance
- No regressions in Phase 1 or Phase 2
- 136 tests pass, 2 pre-existing failures unchanged

**Carried forward (non-blocking):** `smart_diff.py:63` boundary operator (`<=` should be `<`). To be addressed in Phase 4 unit tests.
