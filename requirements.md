# CodeHawk Agent Prompt Strategy Fix

**Date:** 2026-04-29
**Status:** Planning
**Triggered by:** PR #6435 test run — agent burned 1.95M tokens, never used graph tools, failed to produce findings

---

## Problem Statement

On a real integration test against PR #6435 (165-file C# core conversion by Shyamal Agrawal), the CodeHawk agent:
- Used ALL 40 turns reading individual files via `get_file_content` API calls
- **NEVER called any graph tool** (`get_blast_radius`, `get_change_analysis`, `get_callers`, `get_dependents`)
- Burned **1.95M tokens** (~$$$) without producing findings JSON
- Hit `max_turns=40` and crashed with `RuntimeError: Agent did not produce extractable findings JSON`

The graph was built successfully. The tools were registered. The agent simply ignored them.

---

## Root Cause Analysis

### Bug 1: `changed_files=[]` passed empty (`review_job.py` line 86)

The runner receives `changed_files=[]`. Even though graph tool handlers read `args["changed_files"]` from the tool call arguments (not the registration-time list), the agent never calls the tools at all — so this is a secondary issue. However, if we pre-fetch PR data, we can inject the file list into the prompt so the agent doesn't need to discover it.

### Bug 2: System prompt is passive about graph tools (`openai_runner.py` lines 39-45)

Lines 39-42 list graph tools as "instead of" alternatives with the same weight as all other tools. Lines 44-45 add a hedge: "Graph tools are only available when the codebase graph was built successfully. If a graph tool returns an error, fall back..." This framing makes graph tools seem risky/optional.

### Bug 3: Review prompt Step 2b says "skip for T1-T2 PRs" (`review-pr-core.md` line 113)

The agent reads Step 2b, sees the skip condition, and apparently decides to skip graph tools entirely — even on a T5 PR.

### Bug 4: No turn budget awareness

Neither the system prompt nor the review prompt tells the agent how many turns remain. The harness (`openai_runner.py`) silently exhausts turns in a loop with no intervention or deadline warning.

### Bug 5: No fallback extraction

When the loop ends without findings, `_extract_findings_json` runs on `raw_final_message` only. If the agent never produced JSON (because it was still reading files), there's no attempt to scan earlier messages or produce a graceful failure.

---

## Implementation Plan

### Phase 1: Fix the Data Flow (changed_files)

#### Step 1 — Pre-fetch PR data in review_job.py

**File:** `src/review_job.py`, method `create_findings`

Before constructing `OpenAIAgentRunner`, call `FetchPRDetailsActivity` to get changed file paths. Pass these as `changed_files` to the runner.

Currently line 86 passes `changed_files=[]`. Replace with actual list from PR metadata. The job already has `settings`, `pr_id`, and `repo` — instantiate `FetchPRDetailsActivity` directly (same pattern as `conftest.py` line 83) and extract `[fc.path for fc in result.file_changes]`.

- **Dependencies:** None
- **Risk:** Low — straightforward data plumbing
- **Complexity:** Small

#### Step 2 — Inject changed_files summary into the prompt

**File:** `src/review_job.py`, method `_build_prompt`

After building the base prompt text, append a section:

```
## Pre-fetched PR Data
Changed files (165 files):
- path/to/file1.cs (edit, +45/-12)
- path/to/file2.cs (add, +200/-0)
...

Use these paths with get_change_analysis and get_blast_radius.
Do NOT call get_pr — the data is already above.
```

This eliminates the bootstrapping problem — the agent can call `get_change_analysis` on turn 1 instead of spending turns discovering file paths.

For PRs with >100 files, truncate to top 100 by line-change count and note "... and N more files".

- **Dependencies:** Step 1
- **Risk:** Medium — must not bloat prompt excessively

---

### Phase 2: Rewrite the System Prompt

#### Step 3 — Replace SYSTEM_PROMPT with graph-first mandatory strategy

**File:** `src/agents/openai_runner.py`, lines 27-50

Change `SYSTEM_PROMPT` from a module-level constant to a function `build_system_prompt(max_turns: int, has_graph: bool) -> str`.

The new prompt must:
- State the turn budget explicitly: "You have {max_turns} turns total. Reserve the last 3 for producing your findings JSON."
- Make graph tools mandatory first step: "Your FIRST tool call MUST be `get_change_analysis` with the changed file list. Use its output to prioritize your review."
- Explicitly forbid the file-by-file pattern: "Do NOT read files one-by-one sequentially. Use `get_change_analysis` to identify high-risk files, then read only those."
- Move graph tools to the top of the tool mapping with bold emphasis
- Add turn-budget reminder: "When you have used {max_turns - 3} turns, STOP reviewing and output your findings JSON immediately."

- **Dependencies:** None
- **Risk:** Medium — prompt engineering is empirical, may need iteration
- **Complexity:** Medium

#### Step 4 — Add conditional graph-available flag

**File:** `src/agents/openai_runner.py`

System prompt should adapt based on whether `graph_store is not None`. If graph is available, include mandatory graph-first instructions. If not, include a different strategy (read diffs instead of full files).

Store `self.has_graph = graph_store is not None` in `__init__`. Pass to prompt builder in `run()`.

- **Dependencies:** Step 3
- **Risk:** Low

---

### Phase 3: Rewrite the Review Prompt

#### Step 5 — Make Step 2b mandatory, remove T1-T2 skip clause

**File:** `commands/review-pr-core.md`, lines 101-118

Rewrite Step 2b:
- Remove "For T1-T2 PRs: skip this step"
- Change heading to "Step 2b — Analyze Change Impact (MANDATORY when graph tools available)"
- Add: "This step is REQUIRED for all PRs where graph tools are available."
- Add explicit instruction: "Call `get_change_analysis` with the changed file paths listed in the PR Data section above."
- Add: "From the response, create a ranked review plan: review files with `risk_score > 0.5` first, then files with `test_gaps`, then remaining files in priority order."

- **Dependencies:** None (parallel with Phase 2)
- **Risk:** Low

#### Step 6 — Restructure Step 5 to be graph-driven for large PRs

**File:** `commands/review-pr-core.md`, Step 5 section

Add preamble:
```
### Review Strategy by Tier
- T1-T2 (1-10 files): Read each file. Graph analysis is helpful but optional.
- T3 (11-25 files): Use get_change_analysis priorities. Read top 15 files. Skim rest via diffs.
- T4 (26-50 files): Use get_change_analysis priorities. Read top 10 high-risk files. Use get_file_diff for remaining. Do NOT read full file content for low-risk files.
- T5 (51+ files): Use get_change_analysis priorities. Read top 8 high-risk files. Use get_blast_radius to identify cascading risks. Use diffs only for the rest.

BUDGET RULE: You must finish all file reading by turn {max_turns - 5}. Turns after that are for writing findings JSON.
```

- **Dependencies:** Step 5
- **Risk:** Medium

#### Step 7 — Add turn-budget deadline to Step 7

**File:** `commands/review-pr-core.md`, Step 7

Add at the top:
```
**CRITICAL: If you are on turn 35+ (of 40), STOP reading files and produce findings NOW.
Partial findings are infinitely better than no findings. Output what you have.**
```

- **Dependencies:** None
- **Risk:** Low

---

### Phase 4: Turn Budget Enforcement in Harness

#### Step 8 — Add deadline injection at turn N-3

**File:** `src/agents/openai_runner.py`, both `_run_chat_completions` and `_run_responses` methods

Inside the turn loop, when `turn == max_turns - 3`, inject a message before the next API call:
```
DEADLINE: You have 3 turns remaining. You MUST output your findings JSON NOW as your next message.
Do not make any more tool calls. Produce the ```json findings block immediately with whatever
findings you have collected so far. Partial output is required — do NOT skip this.
```

For Chat Completions API: append `{"role": "user", "content": deadline_msg}` to `messages`.
For Responses API: append a user message to `input_items`.

This is a **hard backstop** that works regardless of whether the agent follows prompt instructions.

- **Dependencies:** None
- **Risk:** Low
- **Complexity:** Small

#### Step 9 — Add turn-count reporting in tool results

**File:** `src/agents/openai_runner.py`

After each tool result, append:
```
\n[Turn {turn+1}/{max_turns} used. {remaining} remaining.]
```

This goes into the tool result content so the agent sees its budget continuously without relying on remembering the initial instruction.

Add in both the chat completions loop (after line 208) and responses loop (after line 315).

- **Dependencies:** None
- **Risk:** Low — adds ~50 chars per tool response
- **Complexity:** Trivial

---

### Phase 5: Fallback Extraction

#### Step 10 — Scan conversation history for partial findings

**File:** `src/agents/openai_runner.py`

When `result.findings_data is None` after `_extract_findings_json`, scan the full conversation history for any JSON blocks containing `"findings"` or `"cr-"` patterns.

- For chat completions: scan all messages with `role == "assistant"` that have content
- For responses: accumulate all assistant text outputs during the loop
- Try `_extract_findings_json` on each candidate
- Use the largest/last match if found

- **Dependencies:** None
- **Risk:** Low
- **Complexity:** Small

#### Step 11 — Synthesize emergency empty findings on total failure

**File:** `src/agents/openai_runner.py`

As a last resort, construct a minimal valid findings.json:
```json
{
  "pr_id": <pr_id>,
  "repo": "<repo>",
  "vcs": "ado",
  "review_modes": ["standard"],
  "findings": [],
  "fix_verifications": [],
  "error": "Agent exhausted turn budget without producing findings"
}
```

This prevents the `RuntimeError` on `review_job.py` line 92 and lets Phase 2 report the failure gracefully.

Store `pr_id` and `repo` on the runner instance (already available from `__init__` args).

- **Dependencies:** None
- **Risk:** Low

---

### Phase 6: Test Updates

#### Step 12 — Unit test for deadline injection

**File:** New `tests/unit/test_turn_budget.py`

Mock the OpenAI client, simulate a loop reaching turn 37 of 40, verify the deadline message appears in the messages list.

#### Step 13 — Unit test for changed_files propagation

**File:** New or extend `tests/unit/test_review_job.py`

Mock `FetchPRDetailsActivity` and `OpenAIAgentRunner`. Verify `create_findings` fetches PR data and passes the file list to the runner.

#### Step 14 — Integration test cost control

**File:** `tests/integration/conftest.py`

Add `MAX_TURNS_INTEGRATION = 15` constant. The current 40 turns at ~$50/run is wasteful for testing.

---

## File-Level Change Summary

| File | Lines Affected | Change Type |
|------|---------------|-------------|
| `src/agents/openai_runner.py` L27-50 | SYSTEM_PROMPT | Rewrite to function, graph-first, budget-aware |
| `src/agents/openai_runner.py` L135-215 | Chat completions loop | Deadline injection at N-3, turn counter in tool results |
| `src/agents/openai_runner.py` L235-318 | Responses loop | Same deadline injection + turn counter |
| `src/agents/openai_runner.py` L213,320 | After extraction | Conversation-history fallback scanning |
| `src/review_job.py` L65-87 | `create_findings` | Fetch PR data first, extract changed_files |
| `src/review_job.py` L136-148 | `_build_prompt` | Inject changed_files summary into prompt |
| `commands/review-pr-core.md` L101-118 | Step 2b | Make mandatory, remove T1-T2 skip |
| `commands/review-pr-core.md` L164-175 | Step 5 preamble | Graph-driven review strategy by tier |
| `commands/review-pr-core.md` L337-339 | Step 7 top | Turn-budget deadline warning |
| `tests/integration/conftest.py` | Constants | Add MAX_TURNS_INTEGRATION |

---

## Implementation Order

Steps can be parallelized:
- **Parallel track A:** Steps 1 → 2 (data flow)
- **Parallel track B:** Steps 3 → 4 (system prompt)
- **Parallel track C:** Steps 5 → 6 → 7 (review prompt)
- **Parallel track D:** Steps 8 → 9 (harness enforcement)
- **Parallel track E:** Steps 10 → 11 (fallback)
- **After all above:** Steps 12 → 13 → 14 (tests)

**Single developer order:** 1 → 2 → 3 → 4 → 8 → 9 → 5 → 6 → 7 → 10 → 11 → 12 → 13 → 14

This front-loads the highest-impact fixes (data flow + system prompt + deadline injection) so they can be tested together before polishing the review prompt.

---

## Success Criteria

- [ ] On a T5 PR (50+ files), agent calls `get_change_analysis` within its first 2 turns
- [ ] Agent reads fewer than 20 files via `get_file_content` on a 165-file PR (was 40+)
- [ ] Agent produces valid findings JSON within the turn budget (no more RuntimeError)
- [ ] Total token usage on PR #6435 drops below 800K (from 1.95M)
- [ ] Deadline injection fires at turn N-3 and agent complies
- [ ] Fallback extraction recovers partial findings when agent fails to produce final JSON
- [ ] `changed_files` parameter is non-empty when `graph_store` is available
- [ ] All existing unit tests continue to pass

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Prompt changes don't change agent behavior (LLMs are unpredictable) | Harness-level deadline injection (Step 8) is a hard backstop. Fallback extraction (Steps 10-11) ensures pipeline never crashes. |
| Pre-fetching PR data adds an extra API call | One ADO API call (~200ms). Saves 1 turn + ~$0.50 in tokens. Net positive. |
| Injecting changed_files bloats prompt for very large PRs | Truncate to top 100 files by line-change count for T5+ PRs. |
| Turn counter in tool results adds noise | Single line at end of each result. Models handle this gracefully. |
