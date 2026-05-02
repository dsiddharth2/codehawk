# codehawk-batched-review — Implementation Plan

> Enable full-coverage review of large PRs (50+ files) by adding file filtering (skip non-code files), smart diff summarization (large diffs return hunk summaries with drill-in), and batched agent sessions (split code files into batches, run each as an independent ReviewJob with shared graph, merge findings).

---

## Tasks

### Phase 1: Foundation Modules (No Dependencies)

#### Task 1: Add config fields to Settings
- **Change:** Add 6 new fields to the `Settings` class in `src/config.py`: `skip_extensions` (str, comma-separated, default includes .md,.json,.yaml,.yml,.xml,.lock,.png,.jpg,.jpeg,.gif,.svg,.ico,.csproj,.sln,.config,.env,.gitignore,.dockerignore,.editorconfig,.prettierrc,.eslintignore), `smart_diff_threshold_kb` (int, ge=1, le=500, default 30), `batch_size` (int, ge=5, le=100, default 25), `batch_max_turns` (int, ge=10, le=100, default 40), `max_total_findings` (int, default 50), `max_per_file_findings` (int, default 5).
- **Files:** `src/config.py`
- **Tier:** cheap
- **Done when:** `Settings()` loads with default values for all 6 new fields; existing tests still pass (`pytest tests/`).
- **Blockers:** None

#### Task 2: Create file_filter module
- **Change:** Create `src/file_filter.py` with two functions: `parse_skip_extensions(csv: str) -> set[str]` normalizes a comma-separated extension list (lowercase, leading dot), and `filter_changed_files(file_changes, skip_extensions) -> tuple[list, list]` splits file changes into `(code_files, skipped_files)`. Deleted files (`change_type='delete'`) are always skipped.
- **Files:** `src/file_filter.py` (new)
- **Tier:** cheap
- **Done when:** Module importable from `src/`; `parse_skip_extensions` handles edge cases (no dot prefix, mixed case, whitespace); `filter_changed_files` correctly partitions based on extension and change type.
- **Blockers:** None

#### Task 3: Create smart_diff module
- **Change:** Create `src/smart_diff.py` with: `DiffSummary` dataclass (file_path, total_size_bytes, hunks list, is_summarized bool), `summarize_diff(diff_text, file_path, threshold_kb)` returns `is_summarized=False` for small diffs or parsed hunk summaries for large diffs, `format_summary_for_agent(summary)` formats hunk info as readable text for the agent, and `extract_hunks_in_range(diff_text, start_line, end_line)` filters diff text to only hunks overlapping a line range.
- **Files:** `src/smart_diff.py` (new)
- **Tier:** standard
- **Done when:** Module importable; small diffs return `is_summarized=False`; large diffs parse `@@ -N,M +N,M @@` hunk headers correctly with counts; `extract_hunks_in_range` returns only relevant hunks; `format_summary_for_agent` produces readable output.
- **Blockers:** None — pure functions, no external dependencies.

#### VERIFY: Phase 1 — Foundation Modules
- Run `pytest tests/` — all existing tests pass
- Import `file_filter`, `smart_diff`, confirm no import errors
- Verify `Settings()` has new fields with correct defaults
- Report: tests passing, any regressions, any issues found

---

### Phase 2: Integration (Depends on Phase 1)

#### Task 4: Integrate smart diff into vcs_tools.py
- **Change:** Modify `handle_get_file_diff` in `src/tools/vcs_tools.py`: (1) Remove hardcoded `[:10000]` truncation at line 210. (2) Import and call `summarize_diff()` — if `is_summarized`, return `format_summary_for_agent()` text with `is_summary: true` in JSON response and a hint to drill in. (3) Add `start_line`/`end_line` optional integer parameters to the `get_file_diff` tool schema. When provided, call `extract_hunks_in_range()` to return only the relevant diff portion (drill-in mode, capped at 30KB). (4) For normal unsummarized diffs, raise safety cap from 10KB to 30KB. (5) Thread `settings.smart_diff_threshold_kb` through — `settings` is already passed to `register_vcs_tools`.
- **Files:** `src/tools/vcs_tools.py`
- **Tier:** standard
- **Done when:** `get_file_diff` tool schema includes `start_line`/`end_line` optional params; diffs under threshold returned in full (up to 30KB); diffs over threshold return structured summary with `is_summary: true`; drill-in with `start_line`/`end_line` returns filtered hunks; existing tests pass.
- **Blockers:** Task 3 (smart_diff.py must exist)

#### Task 5: Integrate filtering + batch fields into review_job.py
- **Change:** (1) In `create_findings()`, after PR pre-fetch (line 79), insert filtering: import `parse_skip_extensions`/`filter_changed_files`, apply to `changed_files`, log filtered/kept counts. Use `self.settings.skip_extensions`. (2) Remove `MAX_FILES = 100` cap in `_build_changed_files_section()` (line 212) — show ALL code files. Add a summary line for skipped non-code files count. (3) Add optional fields to `ReviewJobConfig` dataclass: `batch_index: Optional[int] = None`, `batch_total: Optional[int] = None`, `file_subset: Optional[list] = None`, `pre_built_graph = None`, `skipped_files: Optional[list] = None`. (4) When `file_subset` is set in `create_findings()`, skip PR pre-fetch and use subset directly as `changed_files`. When `pre_built_graph` is set, skip `build_graph()` and use it. When `batch_index` is set, append batch context to prompt ("Batch N/M — reviewing N files of M total code files").
- **Files:** `src/review_job.py`
- **Tier:** standard
- **Done when:** `ReviewJobConfig` accepts new optional fields; `create_findings()` filters non-code files when no `file_subset` provided; `_build_changed_files_section()` shows all files (no 100-file cap); batch mode fields are respected (file_subset bypasses pre-fetch, pre_built_graph bypasses graph build, batch_index adds context to prompt); existing tests pass.
- **Blockers:** Task 2 (file_filter.py must exist)

#### Task 6: Update system prompt, raise truncation limits
- **Change:** (1) In `src/agents/openai_runner.py`, modify `build_system_prompt()`: remove "review top 10-15 files" from both graph branch (line 45) and no-graph branch (line 51). Add new instructions: "Review ALL files in your assigned batch — do not skip files. The orchestrator has already filtered non-code files and split the PR into manageable batches." and "When get_file_diff returns is_summary=true, the diff was too large to return in full. Read the hunk summary to identify high-risk sections, then call get_file_diff again with start_line and end_line to drill into those sections." (2) Raise tool result cap from 30000 to 50000 at lines 256 and 395. (3) In `src/tools/workspace_tools.py`: raise search_code output truncation from 15000 to 25000 (line 104); raise read_local_file default max_lines from 500 to 1000 (line 148). (4) In `src/graph_builder.py`: add a new tier `(100, 600)` to `_TIMEOUT_TIERS` for 51-100 file PRs, keeping 300s for <=50 files.
- **Files:** `src/agents/openai_runner.py`, `src/tools/workspace_tools.py`, `src/graph_builder.py`
- **Tier:** cheap
- **Done when:** System prompt no longer mentions "top 10-15 files"; includes "Review ALL files" and smart diff instructions; tool result cap is 50KB in both API paths; search_code cap is 25KB; read_local_file default is 1000 lines; graph timeout for 51-100 files is 600s; existing tests pass.
- **Blockers:** None

#### VERIFY: Phase 2 — Integration
- Run `pytest tests/` — all existing tests pass
- Verify `get_file_diff` tool schema has `start_line`/`end_line`
- Verify system prompt changes via inspecting `build_system_prompt()` output
- Verify `ReviewJobConfig` accepts batch fields
- Report: tests passing, any regressions, any issues found

---

### Phase 3: Orchestrator + Entry Point (Depends on Phase 2)

#### Task 7: Create BatchReviewJob orchestrator
- **Change:** Create `src/batch_review_job.py` with class `BatchReviewJob`:
  - `__init__` accepts pr_id, repo, workspace, model, prompt_path, vcs, settings.
  - `run(dry_run, commit_id)` method: (1) Pre-fetch PR data once via `FetchPRDetailsActivity`. (2) Filter non-code files via `file_filter.filter_changed_files`. (3) Build graph once via `graph_builder.build_graph`. (4) If code files <= `settings.batch_size`, delegate to single-session `ReviewJob` (backward compatible shortcut). (5) Split into batches via `_split_into_batches()` using round-robin by churn descending. (6) Run each batch sequentially via `ReviewJob` with `file_subset`, `pre_built_graph`, `batch_index`, `batch_total` set on `ReviewJobConfig`. (7) Merge findings via `_merge_results()`: concatenate all findings, dedup by `(file, line, title)`, re-sequence cr-ids as `cr-001`, `cr-002`, ..., sum usage stats (input_tokens, output_tokens, duration), union review_modes. (8) Write merged `findings.json`. (9) If any batch fails, catch exception, log error, continue with remaining batches.
  - `_split_into_batches(code_files, batch_size)`: sort by `additions + deletions` descending, round-robin distribute.
  - `_merge_results(batch_results)`: dedup, re-sequence, sum usage.
- **Files:** `src/batch_review_job.py` (new)
- **Tier:** premium
- **Done when:** `BatchReviewJob` importable; `_split_into_batches` produces balanced batches; `_merge_results` re-sequences cr-ids and deduplicates; single-session shortcut works for <= batch_size files; failed batch handling doesn't crash; full `run()` method works end-to-end with mocked dependencies.
- **Blockers:** Tasks 2, 5 (file_filter + batch-aware ReviewJob). Risk: GraphStore reuse across batches.

#### Task 8: Update run_agent.py, review prompt, and post_findings caps
- **Change:** (1) Modify `src/run_agent.py`: import `BatchReviewJob` from `batch_review_job`; construct it with CLI args instead of direct `ReviewJobConfig`/`ReviewJob`; call `batch_job.run(dry_run, commit_id)`. BatchReviewJob auto-delegates to single ReviewJob for small PRs — backward compatible. (2) Update `commands/review-pr-core.md`: In Step 4 T4/T5 rows, replace "Focus on highest-risk paths only" with "Review ALL files in your batch"; add note "Non-code files have been pre-filtered. You will only see code files."; add in Step 5: "When get_file_diff returns is_summary, drill into suspicious hunks with start_line/end_line." (3) In `src/post_findings.py`: replace module-level `MAX_TOTAL_FINDINGS = 30` → read from `get_settings().max_total_findings` (default 50); replace `MAX_PER_FILE = 5` → read from `get_settings().max_per_file_findings` (default 5). Update `cap_findings()` calls and `_build_summary_markdown` reference to use the dynamic values.
- **Files:** `src/run_agent.py`, `commands/review-pr-core.md`, `src/post_findings.py`
- **Tier:** standard
- **Done when:** `run_agent.py` uses `BatchReviewJob`; prompt no longer says "highest-risk paths only" for T4/T5; prompt includes smart diff drill-in guidance; `post_findings.py` reads caps from settings; existing tests pass.
- **Blockers:** Task 7 (BatchReviewJob must exist)

#### VERIFY: Phase 3 — Orchestrator
- Run `pytest tests/` — all existing tests pass
- Verify `run_agent.py` imports and uses `BatchReviewJob`
- Verify `post_findings.py` reads caps from settings
- Verify review prompt updated with batch and smart diff guidance
- Report: tests passing, any regressions, any issues found

---

### Phase 4: Tests (Depends on Phase 3)

#### Task 9: Unit tests for file_filter and smart_diff
- **Change:** Create `tests/unit/test_file_filter.py`: test `parse_skip_extensions` with standard CSV, leading-dot normalization, mixed case, whitespace, empty string; test `filter_changed_files` keeps .py/.cs/.ts/.css, skips .md/.json/.yaml/.lock/.png, skips deleted files regardless of extension, handles empty list, handles all-skipped scenario. Create `tests/unit/test_smart_diff.py`: test small diff returns `is_summarized=False`; large diff returns parsed hunks with correct add/remove counts; `extract_hunks_in_range` returns only overlapping hunks and empty for non-overlapping range; `format_summary_for_agent` output contains file path and hunk details; empty diff handled.
- **Files:** `tests/unit/test_file_filter.py` (new), `tests/unit/test_smart_diff.py` (new)
- **Tier:** standard
- **Done when:** `pytest tests/unit/test_file_filter.py tests/unit/test_smart_diff.py` passes; at least 8 test cases for file_filter and 6 for smart_diff.
- **Blockers:** Tasks 2, 3

#### Task 10: Unit tests for BatchReviewJob merge logic
- **Change:** Create `tests/unit/test_batch_review.py`: test `_split_into_batches` round-robin produces balanced batches; test `_merge_results` re-sequences cr-ids correctly (cr-001, cr-002, ...); test dedup by (file, line, title) removes duplicates across batches; test usage stats sum correctly; test single-batch shortcut for small PRs; test failed batch handling preserves other findings; test empty code files produces clean findings.
- **Files:** `tests/unit/test_batch_review.py` (new)
- **Tier:** standard
- **Done when:** `pytest tests/unit/test_batch_review.py` passes; at least 7 test cases.
- **Blockers:** Task 7

#### VERIFY: Phase 4 — Tests
- Run full `pytest tests/ -v` — all tests pass (old + new)
- Confirm new test files exist and cover core logic
- Report: total test count, pass rate, any failures

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| GraphStore not safe for sequential reuse across batches | High — batch 2+ gets stale/corrupt graph data | Task 7 tests graph reuse explicitly; fallback: rebuild graph per batch (slower but safe) |
| Smart diff hunk parsing breaks on unusual diff formats (binary, no-newline markers) | Med — agent gets garbled summaries | Task 3 handles edge cases; `is_summarized=False` fallback for unparseable diffs |
| Batch findings merge produces duplicate cr-ids | Med — post_findings.py confused | Task 7 re-sequences after dedup; unit test verifies sequential IDs |
| Raising tool result cap to 50KB causes context window overflow | Med — agent runs out of context | Monitor in testing; can lower to 40KB if needed |
| `run_agent.py` change breaks existing pipeline | High — CI pipeline failures | BatchReviewJob delegates to ReviewJob for small PRs — backward compatible by design |
| Round-robin splitting puts related files in different batches | Low — cross-file issues missed | Graph is shared — agent still uses `get_callers`/`get_blast_radius` across boundaries |

## Notes
- Each task should result in a git commit
- Verify tasks are checkpoints — stop and report after each one
- Base branch: main
- Branch: feat/large-pr-batched-review
