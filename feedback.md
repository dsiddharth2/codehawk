# Agent Prompt Strategy Fix — Phase 1 Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-04-30T19:00:00+05:30
**Phase:** Phase 1 — Harness Safety Net
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.

---

## Task 1: Deadline Injection — PASS

The deadline injection is implemented correctly in both `_run_chat_completions` (line 140) and `_run_responses` (line 272). The condition `turn == max_turns - 3` fires at the correct time — for `max_turns=40`, this fires at turn index 37 (the 38th turn), leaving turns 38, 39, and the current turn 37 for the agent to comply. The message text matches the plan's specification closely.

The injection happens *before* the API call on that turn, which is the correct placement — the agent sees the deadline message as part of its input for that turn's completion.

Both paths append the message in the correct format for their respective APIs: `{"role": "user", "content": ...}` for Chat Completions, and `{"type": "message", "role": "user", "content": ...}` for Responses.

The console log `[DEADLINE INJECTION: turn N, 3 turns remaining]` provides good operator visibility.

**Minor edge case (non-blocking):** When `max_turns <= 3`, the condition `turn == max_turns - 3` evaluates to 0 or negative. For `max_turns=3`, deadline fires on the very first turn (turn 0). For `max_turns < 3`, it never fires because `max_turns - 3` is negative and `range(max_turns)` never yields negative values. This is acceptable for production (`max_turns=40`) but worth noting for test scenarios with small budgets.

**Done-when check:** Deadline message appears in conversation when `turn == max_turns - 3`. **Met.**

---

## Task 2: Turn-Count Reporting — PASS

Turn-count suffixes are appended to every tool result in both loops:
- Chat Completions: line 216-217, appended to `tool_result` before adding to `messages`
- Responses: line 356-357, appended to `tool_result` before adding to `input_items`

The format `\n[Turn {turn+1}/{max_turns} used. {remaining} remaining.]` is correct. At turn 0 (first turn) with max_turns=40, it outputs `[Turn 1/40 used. 39 remaining.]`. The counter correctly uses 1-indexed turn numbers for human readability.

The suffix is appended *after* the 30K truncation check (lines 213-214 and 353-354), meaning the counter is always present even on truncated results. This is the correct order.

**Done-when check:** Every tool result message includes the turn counter suffix. **Met.**

---

## Task 3: Fallback Extraction — PASS (after fix)

The overall structure is correct: try `_extract_findings_json` on the final message, then scan history, then synthesize emergency findings. Both `_run_chat_completions` and `_run_responses` have this three-tier fallback. The `RuntimeError` in `review_job.py` is properly replaced with `warnings.warn()`. The `pr_id` and `repo` are stored on the runner instance. Emergency findings have all required fields (`pr_id`, `repo`, `vcs`, `review_modes`, `findings`, `fix_verifications`, `error`).

### Initial finding (now resolved): Bare JSON regex could not match nested objects

The original implementation used `re.finditer(r'(\{[^{}]*"findings"[^{}]*\})', text, re.DOTALL)` which excluded curly braces via `[^{}]`, making it unable to match any nested JSON. This was flagged in the initial review.

**Fix applied in commit `6c44117`:** Replaced the broken regex with `_brace_balanced_extract()`, a brace-depth tracking function that correctly collects nested JSON objects containing `"findings"`. Also removed the redundant `'"cr-"' in block` literal check — `re.search(r'"cr-\w+', block)` already handles finding IDs.

### Re-review of `_brace_balanced_extract` (commit 6c44117)

The implementation is correct and handles key edge cases:

- **Empty input:** `n=0`, loop never executes, returns `[]`. Correct.
- **Malformed/unbalanced braces** (e.g., `{"findings": [`): The inner loop exhausts `j < n` without reaching `depth == 0`, falls to `else: i += 1`, advancing past the unmatched opening brace. No infinite loop. Correct.
- **Deeply nested structures** (e.g., `{"a": {"b": {"findings": []}}}`): Depth tracks correctly (1->2->3->2->1->0), captures the full balanced substring. Correct.
- **Multiple objects in one text**: Each balanced object is extracted independently. Only those containing the keyword are collected. Correct.
- **Known limitation (acceptable):** Braces inside JSON string values (e.g., `{"findings": "text with { in it"}`) can cause miscounting. However, this is a degenerate case for findings JSON (which contains arrays/objects, not raw brace characters in strings), and any mismatched extraction would fail at `json.loads()` downstream. Acceptable tradeoff vs. implementing a full JSON-aware tokenizer.

### Code-fence regex

The code-fence regex `r'```(?:json)?\s*\n(\{.*?\})\s*\n```'` with `re.DOTALL` correctly matches findings inside fenced blocks. The non-greedy `.*?` combined with the closing-fence anchor resolves to the correct outermost `}` before the triple backticks.

**Done-when check:**
- "Pipeline never raises RuntimeError on missing findings" — **Met** (emergency fallback guarantees this).
- "Emergency findings are valid JSON matching the schema" — **Met**.
- "Unit test covers both history-scan and emergency paths" — **Deferred** to Phase 4 Tasks 8-9 (acceptable per plan).
- History scan correctness for bare JSON — **Met** (after `_brace_balanced_extract` fix).

---

## Cross-Cutting Concerns

### Code quality
- The implementation is clean and well-structured. Changes are minimal and focused.
- Console logging (`[DEADLINE INJECTION]`, `[Fallback]`, `[Emergency]`) provides good operator visibility.
- The `_scan_history_for_findings` function is properly extracted as a standalone helper, making it testable.
- `_brace_balanced_extract` is a clean, single-purpose utility with a clear docstring.

### Consistency between API paths
- Deadline injection: identical logic in both paths. **Consistent.**
- Turn counter: identical logic in both paths. **Consistent.**
- Fallback extraction: Chat Completions extracts assistant texts from the `messages` list, while Responses uses `all_assistant_texts` accumulated during the loop. Both approaches correctly collect assistant message content. **Consistent in behavior, different in mechanism** (appropriate given the different API structures).

### Edge cases
- `max_turns <= 3`: Deadline may fire too early or not at all (see Task 1 note). Low risk.
- Agent produces findings on the final message *and* in history: `_extract_findings_json` succeeds, history scan is skipped. Correct behavior.
- Empty conversation (agent errors on turn 1): `raw_final_message` is empty, history scan finds nothing, emergency findings are synthesized. Correct.
- `review_job.py` warning (line 91-96): Since the runner now always populates `findings_data`, this condition should never trigger. It's effectively dead code but acts as a safety net if the runner behavior changes. Acceptable.

### Minor style notes (non-blocking)
- `import warnings` inside the `if` block in `review_job.py` — stdlib imports are typically at the top of the file.

---

## Summary

**All three tasks pass.** Phase 1 — Harness Safety Net is approved for merge.

- **Task 1 (Deadline Injection):** Correctly injects deadline message at turn N-3 in both API paths.
- **Task 2 (Turn-Count Reporting):** Correctly appends turn counter to every tool result in both API paths.
- **Task 3 (Fallback Extraction):** Three-tier fallback (final message -> history scan -> emergency synthesis) works correctly after the `_brace_balanced_extract` fix. Pipeline never crashes on missing findings.

### What is deferred:
- Unit tests for all three tasks (planned for Phase 4, Tasks 8-9).
- Move `import warnings` to top of `review_job.py` (minor style — optional).
