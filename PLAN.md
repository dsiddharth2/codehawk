# CodeHawk Agent Prompt Strategy Fix — Implementation Plan

> Fix agent prompt strategy to use graph tools first, enforce turn budgets, and add fallback extraction.

---

## Tasks

### Phase 1: Harness Safety Net (Deadline Injection + Fallback Extraction)

*Rationale: Front-load the hard backstops first. Even if prompt changes don't alter agent behavior, these guarantee the pipeline never crashes and partial results are recovered.*

#### Task 1: Add deadline injection at turn N-3

- **Change:** In both `_run_chat_completions` (line 135 loop) and `_run_responses` (line 235 loop), when `turn == max_turns - 3`, inject a user/system message before the next API call:
  ```
  DEADLINE: You have 3 turns remaining. You MUST output your findings JSON NOW.
  Do not make any more tool calls. Produce the ```json findings block immediately
  with whatever findings you have collected so far. Partial output is required.
  ```
  For Chat Completions: append `{"role": "user", "content": deadline_msg}` to `messages`.
  For Responses: append a user message to `input_items`.
- **Files:** `src/agents/openai_runner.py` (lines 135-215, 235-318)
- **Tier:** cheap
- **Done when:** When `turn == max_turns - 3`, the deadline message appears in the conversation. Unit test confirms injection.
- **Blockers:** None — this is the riskiest assumption (will the agent obey a mid-loop injection?) so we validate it first.

#### Task 2: Add turn-count reporting in tool results

- **Change:** After each tool dispatch result, append `\n[Turn {turn+1}/{max_turns} used. {remaining} remaining.]` to the tool result content. Apply in both chat completions loop (after tool dispatch ~line 209) and responses loop (after tool dispatch ~line 316).
- **Files:** `src/agents/openai_runner.py`
- **Tier:** cheap
- **Done when:** Every tool result message includes the turn counter suffix. Verified by unit test.
- **Blockers:** None

#### Task 3: Fallback extraction — scan history + emergency findings

- **Change:**
  1. When `result.findings_data is None` after `_extract_findings_json` on the final message, scan **all** assistant messages in conversation history for JSON blocks containing `"findings"` or `"cr-"` patterns. Use the last/largest match.
  2. If still None, synthesize minimal valid findings: `{"pr_id": ..., "repo": ..., "vcs": "ado", "review_modes": ["standard"], "findings": [], "fix_verifications": [], "error": "Agent exhausted turn budget without producing findings"}`. Store `pr_id` and `repo` on the runner instance (already available from `__init__`).
  3. Remove the `RuntimeError` raise in `review_job.py` line 92 — replace with a warning log, since fallback findings are now always produced.
- **Files:** `src/agents/openai_runner.py` (after lines 213, 320), `src/review_job.py` (line 92)
- **Tier:** standard
- **Done when:** Pipeline never raises `RuntimeError` on missing findings. Emergency findings are valid JSON matching the schema. Unit test covers both history-scan and emergency paths.
- **Blockers:** None

#### VERIFY: Phase 1 — Harness Safety Net
- Run full test suite (`pytest tests/`)
- Confirm deadline injection fires at correct turn
- Confirm fallback extraction produces valid findings on simulated failure
- Report: tests passing, any regressions

---

### Phase 2: Data Flow + System Prompt (Graph-First Strategy)

#### Task 4: Pre-fetch PR data and inject changed_files into prompt

- **Change:**
  1. In `review_job.py` `create_findings()`, before constructing `OpenAIAgentRunner`, call `FetchPRDetailsActivity(self.settings).execute(FetchPRDetailsInput(pr_id=self.config.pr_id, repository_id=self.config.repo))` to get PR details. Extract `changed_files = [fc.path for fc in result.file_changes]`. Pass to runner.
  2. Replace `changed_files=[]` (line 86) with the actual list.
  3. In `_build_prompt()`, append a "Pre-fetched PR Data" section listing changed files with change type and line counts. For PRs with >100 files, truncate to top 100 by `additions + deletions` and note "... and N more files". Add instruction: "Use these paths with `get_change_analysis`. Do NOT call `get_pr` — the data is already above."
- **Files:** `src/review_job.py` (lines 65-97, 136-148)
- **Tier:** standard
- **Done when:** `changed_files` is non-empty when passed to runner. Prompt contains file list section. Unit test mocks `FetchPRDetailsActivity` and verifies propagation.
- **Blockers:** None — `FetchPRDetailsActivity` already exists and is used in integration tests.

#### Task 5: Rewrite SYSTEM_PROMPT as graph-first, budget-aware function

- **Change:** Replace the module-level `SYSTEM_PROMPT` constant with a function `build_system_prompt(max_turns: int, has_graph: bool) -> str`. The new prompt must:
  1. State the turn budget: "You have {max_turns} turns total. Reserve the last 3 for producing findings JSON."
  2. When `has_graph=True`: mandate graph-first strategy — "Your FIRST tool call MUST be `get_change_analysis`. Use its output to prioritize your review." Forbid sequential reading: "Do NOT read files one-by-one. Use graph analysis to identify high-risk files, then read only those."
  3. When `has_graph=False`: instruct to use diffs instead of full file reads.
  4. Add `self.has_graph = graph_store is not None` in `__init__`. Pass to `build_system_prompt()` in both `_run_chat_completions` and `_run_responses`.
  5. Update all call sites referencing `SYSTEM_PROMPT` constant.
- **Files:** `src/agents/openai_runner.py` (lines 27-50, __init__, run methods)
- **Tier:** standard
- **Done when:** System prompt is dynamically generated. Graph-first instructions appear when graph is available. Turn budget is stated. Unit test verifies both graph/no-graph variants.
- **Blockers:** None

#### VERIFY: Phase 2 — Data Flow + System Prompt
- Run full test suite
- Confirm `changed_files` flows from PR fetch through to runner
- Confirm system prompt adapts based on graph availability
- Report: tests passing, any regressions

---

### Phase 3: Review Prompt Updates

#### Task 6: Make Step 2b mandatory, remove T1-T2 skip clause

- **Change:**
  1. In `commands/review-pr-core.md` line 113, remove "For T1-T2 PRs: skip this step".
  2. Change heading to "Step 2b — Analyze Change Impact (MANDATORY when graph tools available)".
  3. Add: "This step is REQUIRED for all PRs where graph tools are available."
  4. Add explicit instruction: "Call `get_change_analysis` with the changed file paths listed in the PR Data section above."
  5. Add: "From the response, create a ranked review plan: review files with `risk_score > 0.5` first, then files with `test_gaps`, then remaining files in priority order."
- **Files:** `commands/review-pr-core.md` (lines 101-118)
- **Tier:** cheap
- **Done when:** Step 2b no longer contains any skip clauses. Mandatory framing is clear.
- **Blockers:** None

#### Task 7: Add tier-based review strategy to Step 5 and deadline warning to Step 7

- **Change:**
  1. Add preamble to Step 5 with tier-based review depth:
     - T1-T2 (1-10 files): Read each file. Graph optional.
     - T3 (11-25 files): Use graph priorities. Read top 15, skim rest via diffs.
     - T4 (26-50 files): Use graph priorities. Read top 10 high-risk. Diffs for rest.
     - T5 (51+ files): Use graph priorities. Read top 8. Use `get_blast_radius` for cascading risks. Diffs only for rest.
     - Budget rule: finish reading by turn {max_turns - 5}.
  2. Add deadline warning at top of Step 7:
     ```
     **CRITICAL: If you are on turn 35+ (of 40), STOP reading files and produce findings NOW.
     Partial findings are infinitely better than no findings. Output what you have.**
     ```
- **Files:** `commands/review-pr-core.md` (Step 5 ~line 164, Step 7 ~line 337)
- **Tier:** cheap
- **Done when:** Step 5 has tier-based strategy preamble. Step 7 has deadline warning.
- **Blockers:** None

#### VERIFY: Phase 3 — Review Prompt Updates
- Run full test suite (prompt changes shouldn't break tests)
- Manual review of prompt text for consistency
- Report: tests passing, prompt reads naturally

---

### Phase 4: Test Coverage

#### Task 8: Unit tests for deadline injection and turn budget

- **Change:** Create `tests/unit/test_turn_budget.py`:
  1. Mock OpenAI client, simulate loop reaching turn `max_turns - 3`, verify deadline message appears in messages list.
  2. Verify turn counter appears in tool result content.
  3. Verify deadline message is NOT injected before turn N-3.
- **Files:** `tests/unit/test_turn_budget.py` (new)
- **Tier:** cheap
- **Done when:** Tests pass, cover both chat completions and responses paths.
- **Blockers:** Tasks 1-2 must be complete.

#### Task 9: Unit tests for changed_files propagation and fallback extraction

- **Change:**
  1. Create or extend `tests/unit/test_review_job.py`: mock `FetchPRDetailsActivity` and `OpenAIAgentRunner`, verify `create_findings` fetches PR data and passes non-empty file list to runner.
  2. Add tests to verify fallback extraction: conversation-history scan finds partial JSON; emergency findings are schema-valid.
- **Files:** `tests/unit/test_review_job.py` (new or extend), `tests/unit/test_turn_budget.py` (extend)
- **Tier:** cheap
- **Done when:** Tests pass, cover happy path and failure paths.
- **Blockers:** Tasks 3-4 must be complete.

#### Task 10: Integration test cost control

- **Change:** Add `MAX_TURNS_INTEGRATION = 15` to `tests/integration/conftest.py`. Update the integration test fixture in `test_full_pipeline.py` to use this constant instead of hardcoded `max_turns=40`.
- **Files:** `tests/integration/conftest.py`, `tests/integration/test_full_pipeline.py`
- **Tier:** cheap
- **Done when:** Integration tests use 15-turn budget. Existing integration tests still pass (or are updated for new budget).
- **Blockers:** None (can be done in parallel).

#### VERIFY: Phase 4 — Test Coverage
- Run full test suite (`pytest tests/ -v`)
- Confirm all new tests pass
- Confirm no regressions in existing tests
- Report: full test results

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| Prompt changes don't alter agent behavior (LLMs are unpredictable) | High | Harness-level deadline injection (Task 1) is a hard backstop independent of prompt compliance. Fallback extraction (Task 3) ensures pipeline never crashes. |
| Pre-fetching PR data adds latency | Low | Single ADO API call (~200ms). Saves 1+ turns and ~$0.50 in tokens. Net positive. |
| Injecting changed_files bloats prompt for very large PRs | Med | Truncate to top 100 files by line-change count. Adds ~5KB max. |
| Turn counter in tool results confuses the model | Low | Single line at end of each result. Models handle metadata lines gracefully. |
| Deadline injection at N-3 triggers too late (agent ignores it) | Med | Combined with continuous turn counter (Task 2) and prompt-level budget awareness (Task 5), the agent has 3 layers of budget signals. |
| Emergency fallback findings mask real failures | Med | Include `"error"` field in emergency findings. Downstream scoring/reporting can flag zero-findings reviews for human triage. |

## Notes
- Each task should result in a git commit
- Verify tasks are checkpoints — stop and report after each one
- Base branch: main
- Phase 1 is front-loaded because it provides the safety net: even if all other phases fail, the pipeline won't crash
- Phases 2 and 3 can be developed in parallel by different developers if needed
- The recommended single-developer order follows the task numbering (1 through 10)
