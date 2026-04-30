# Agent Prompt Strategy Fix — Phase 2 Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-04-30T12:15:00+05:30
**Phase:** Phase 2 — Data Flow + System Prompt
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.

---

## Task 4: Pre-fetch PR Data
**PASS**

The implementation in `src/review_job.py` correctly pre-fetches PR data via `FetchPRDetailsActivity` before building the prompt or constructing the runner. Key observations:

1. **Pattern alignment:** The `FetchPRDetailsActivity` instantiation (`review_job.py:72-74`) follows the same pattern used in `conftest.py:82-83` — create the activity with `self.settings`, call `.execute()` with a `FetchPRDetailsInput`. Correct.

2. **Best-effort try/except:** The entire pre-fetch block (`review_job.py:69-78`) is wrapped in a broad `try/except Exception`, which is appropriate here. If the ADO API call fails, the pipeline falls back to `changed_files=[]` and continues — the agent just won't have the pre-fetched file list. The `print()` on failure provides diagnostic visibility.

3. **100-file truncation:** `_build_changed_files_section()` (`review_job.py:170-204`) sorts file changes by `additions + deletions` descending and truncates to the top 100. The omitted count is correctly calculated as `len(sorted_changes) - MAX_FILES`. The markdown table format is clean and parseable by the LLM.

4. **Data flow to runner:** `changed_files` is passed both to `_build_prompt()` (as the raw `file_changes` objects for the table) and to the runner constructor (as `[fc.path for fc in changed_files]` — string paths). This dual use is correct: the prompt needs full metadata (path, change_type, additions, deletions), while the runner/graph tools just need paths.

5. **Prompt instruction:** The closing line "Use these paths with `get_change_analysis`. Do NOT call `get_pr`" directly addresses the root cause — eliminating the bootstrapping problem where the agent wasted turns discovering file paths.

6. **Minor note:** The truncated-row markdown (`review_job.py:197`) has 5 pipe-delimited columns but the header has 4. This is cosmetically imperfect in strict markdown rendering but functionally harmless — the LLM will parse the intent correctly. Not a blocker.

## Task 5: Dynamic System Prompt
**PASS**

The `build_system_prompt()` function in `src/agents/openai_runner.py:27-71` replaces the old `SYSTEM_PROMPT` constant correctly. Key observations:

1. **Turn budget communication:** The prompt clearly states "You have {max_turns} turns total. Reserve the last 3 for producing findings JSON." This directly addresses Bug 4 from the requirements (no turn budget awareness).

2. **Graph-first mandate (`has_graph=True`):** The graph strategy block (`openai_runner.py:30-35`) mandates `get_change_analysis` as the first tool call and explicitly forbids file-by-file reading. It references `get_blast_radius`, `get_callers`, and `get_dependents`. This is strong, directive language — a significant improvement over the old passive "instead of" framing.

3. **No-graph fallback (`has_graph=False`):** The alternative block (`openai_runner.py:37-40`) redirects to `get_file_diff` and `search_code`. Appropriate — when graph tools aren't available, diffs are the next-best strategy for avoiding full-file reads.

4. **`self.has_graph` flag:** Added at `openai_runner.py:111` as `self.has_graph = graph_store is not None`. Simple and correct.

5. **Both run methods updated:** `_run_chat_completions` (`openai_runner.py:149`) and `_run_responses` (`openai_runner.py:306`) both call `build_system_prompt(max_turns, self.has_graph)`. No stale `SYSTEM_PROMPT` references remain.

6. **Responses API note:** In `_run_responses`, the `build_system_prompt()` call happens inside the turn loop (`openai_runner.py:306`) rather than once before the loop. This means the system prompt is regenerated on every turn. While functionally correct (same inputs produce same output), it's a minor inefficiency. Not a blocker — the string generation cost is negligible compared to API calls.

7. **Existing tool mapping preserved:** The "IMPORTANT tool mapping" section and the graph-tool fallback note are retained, ensuring backward compatibility with the agent's tool usage patterns.

## Integration with Phase 1
**PASS**

Phase 1 (harness safety net — deadline injection, turn counter, fallback extraction) and Phase 2 (data flow + system prompt) integrate cleanly:

1. **Layered budget awareness:** The agent now gets budget signals at three levels:
   - System prompt: "You have N turns total" (Phase 2, Task 5)
   - Tool results: "[Turn X/Y used. Z remaining.]" (Phase 1, Task 2)
   - Deadline injection: Hard backstop at turn N-3 (Phase 1, Task 1)
   This layering is exactly what the requirements called for.

2. **Fallback chain intact:** If the agent ignores all budget signals and fails to produce findings:
   - History scan picks up partial JSON from earlier turns (Phase 1, Task 3)
   - Emergency findings synthesize a valid response (Phase 1, Task 3)
   - `review_job.py:104-109` warns but doesn't crash (Phase 1, Task 3)
   The pre-fetch data from Task 4 doesn't interfere with any of this — it's purely additive to the prompt.

3. **No conflicting instructions:** The system prompt's "Reserve the last 3 for producing findings JSON" aligns with the deadline injection at `turn == max_turns - 3`. Consistent messaging.

4. **`changed_files` flows correctly through both phases:** Pre-fetched in `create_findings()`, passed to `_build_prompt()` for the table, and passed to the runner constructor for graph tool registration. The runner's Phase 1 safety nets (deadline, fallback) operate independently of this data flow.

## Cross-Cutting Concerns
**PASS**

1. **Error handling:** Both new features are best-effort. PR pre-fetch failure falls back gracefully. The `build_system_prompt()` function is pure (no I/O, no exceptions) — it simply formats a string from its inputs.

2. **Edge cases covered:**
   - Empty `changed_files` (pre-fetch fails): no PR data section appended, `changed_files=[]` passed to runner. Agent proceeds without file list — same as before this change.
   - `changed_files` with exactly 100 files: `truncated` is `False`, no "and N more" row. Correct.
   - `changed_files` with 101+ files: top 100 by change volume shown, omitted count noted. Correct.
   - `has_graph=False`: no-graph strategy used, no reference to graph tools. Correct.

3. **Code quality:** Changes are minimal and focused. No unnecessary refactoring. Import-time side effects avoided (lazy imports inside try/except). Print statements for observability are consistent with the existing codebase style.

---

## Summary

**Both Task 4 and Task 5 pass review.** The implementation is clean, follows existing patterns, and integrates correctly with Phase 1's harness safety net.

- **Task 4 (Pre-fetch PR Data):** Correctly plumbs `FetchPRDetailsActivity` data into the prompt and runner. Best-effort error handling. 100-file truncation works correctly.
- **Task 5 (Dynamic System Prompt):** `build_system_prompt()` produces correct, directive prompts for both graph and no-graph scenarios. Turn budget is clearly communicated. Both API paths updated.
- **Integration:** Phase 1 + Phase 2 work together without conflict. Three-layer budget awareness is solid.
- **Deferred:** Unit tests for these changes (Tasks 8-9 in the plan) are scheduled for Phase 4.
