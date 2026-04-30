# Agent Prompt Strategy Fix — Phase 1 Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-04-30T18:30:00+05:30
**Phase:** Phase 1 — Harness Safety Net
**Verdict:** CHANGES NEEDED

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

## Task 3: Fallback Extraction — FAIL

The overall structure is correct: try `_extract_findings_json` on the final message, then scan history, then synthesize emergency findings. Both `_run_chat_completions` and `_run_responses` have this three-tier fallback. The `RuntimeError` in `review_job.py` is properly replaced with `warnings.warn()`. The `pr_id` and `repo` are stored on the runner instance. Emergency findings have all required fields (`pr_id`, `repo`, `vcs`, `review_modes`, `findings`, `fix_verifications`, `error`).

However, there is a **bug in `_scan_history_for_findings`** that undermines the history-scanning fallback:

### Bug: Bare JSON regex cannot match nested objects

Line 423 uses the regex:
```python
re.finditer(r'(\{[^{}]*"findings"[^{}]*\})', text, re.DOTALL)
```

The character class `[^{}]` excludes curly braces, meaning this regex can only match **flat** JSON objects with no nesting. A real findings JSON always has nested structures — `"findings": [{"id": "cr-001", ...}]` contains inner `{}` braces. This regex will never match an actual findings payload.

This means the bare-JSON fallback path is dead code. Only the code-fence regex (line 418) can produce candidates. If the agent outputs findings JSON outside a code fence (e.g., as raw text), the history scan will miss it entirely.

**Fix:** Replace the bare JSON regex with a brace-balanced extraction or reuse the same approach as `_extract_findings_json` which uses `r'(\{[^{}]*"pr_id".*\})'` with `re.DOTALL` (which does cross brace boundaries via `.*`).

### Minor: Code-fence regex uses non-greedy `.*?`

Line 418:
```python
re.finditer(r'```(?:json)?\s*\n(\{.*?\})\s*\n```', text, re.DOTALL)
```

The `.*?` (non-greedy) combined with the trailing anchor `\s*\n\`\`\`` will correctly match to the outermost `}` before the closing fence, so this works for well-formed code fences. However, if the agent outputs multiple code-fenced JSON blocks in a single message, the non-greedy match correctly captures each one individually. This is fine.

### Minor: `"cr-"` literal check may miss findings

Line 420 checks for `'"cr-"' in block` — this would only match the literal string `"cr-"` with a closing quote immediately after the hyphen. Real finding IDs like `"cr-001"` won't match this literal. The `re.search(r'"cr-\w+', block)` on the same line does handle this correctly though, so findings with IDs are still caught. The `'"cr-"'` check is simply dead/redundant.

### Assessment

The code-fence path works correctly for the most common case (agent outputs findings in a ````json` fence mid-conversation). The emergency fallback synthesizes valid findings. The bare-JSON fallback is broken but has low practical impact since agents almost always use code fences. The overall safety guarantee (pipeline never crashes) is **met** because the emergency fallback always produces valid findings.

**Done-when check:**
- "Pipeline never raises RuntimeError on missing findings" — **Met** (emergency fallback guarantees this).
- "Emergency findings are valid JSON matching the schema" — **Met**.
- "Unit test covers both history-scan and emergency paths" — **Not met** (no tests in this commit; deferred to Phase 4 Task 8-9, which is acceptable per the plan).
- History scan correctness for bare JSON — **Not met** (regex bug).

**Doer:** fixed in commit — replaced broken `[^{}]*` bare-JSON regex with `_brace_balanced_extract()`, a brace-depth tracker that correctly collects nested JSON objects containing `"findings"`. Also removed the redundant `'"cr-"' in block` literal check; the `re.search(r'"cr-\w+"')` on the same line already handles finding IDs correctly.

---

## Cross-Cutting Concerns

### Code quality
- The implementation is clean and well-structured. Changes are minimal and focused.
- Console logging (`[DEADLINE INJECTION]`, `[Fallback]`, `[Emergency]`) provides good operator visibility.
- The `_scan_history_for_findings` function is properly extracted as a standalone helper, making it testable.

### Consistency between API paths
- Deadline injection: identical logic in both paths. **Consistent.**
- Turn counter: identical logic in both paths. **Consistent.**
- Fallback extraction: Chat Completions extracts assistant texts from the `messages` list (line 230-234), while Responses uses `all_assistant_texts` accumulated during the loop (line 267, 317). Both approaches correctly collect assistant message content. **Consistent in behavior, different in mechanism** (appropriate given the different API structures).

### Edge cases
- `max_turns <= 3`: Deadline may fire too early or not at all (see Task 1 note). Low risk.
- Agent produces findings on the final message *and* in history: `_extract_findings_json` succeeds, history scan is skipped. Correct behavior.
- Empty conversation (agent errors on turn 1): `raw_final_message` is empty, history scan finds nothing, emergency findings are synthesized. Correct.
- `review_job.py` warning (line 91-96): Since the runner now always populates `findings_data`, this condition should never trigger. It's effectively dead code but acts as a safety net if the runner behavior changes. Acceptable.

### `import warnings` placement
Line 92 in `review_job.py` has `import warnings` inside the `if` block. This is a minor style inconsistency (stdlib imports are typically at the top of the file) but has no functional impact.

---

## Summary

**Tasks 1 and 2 pass.** Deadline injection and turn-count reporting are correctly implemented in both API paths, matching the plan's specifications.

**Task 3 needs one fix:** The bare-JSON regex in `_scan_history_for_findings` (line 423) uses `[^{}]` which prevents it from matching any nested JSON. This should be fixed to handle nested braces. The fix is small (one regex change) and does not affect the overall safety guarantee since emergency findings always fire as a last resort.

### What must change before merge:
1. Fix the bare-JSON regex in `_scan_history_for_findings` to handle nested objects (e.g., use `.*` with DOTALL instead of `[^{}]*`, or use a brace-balanced parser).

### What is deferred:
- Unit tests for all three tasks (planned for Phase 4, Tasks 8-9).
- Move `import warnings` to top of `review_job.py` (minor style — optional).
- Remove redundant `'"cr-"' in block` check in line 420 (cosmetic — optional).
