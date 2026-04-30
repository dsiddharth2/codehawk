# Agent Prompt Strategy Fix — Phase 4 Review (Final)

**Reviewer:** codehawk-reviewer
**Date:** 2026-04-30 06:44 UTC
**Phase:** Phase 4 — Test Coverage (Final Sprint Review)
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.

---

## Task 8: Turn Budget Unit Tests

**PASS**

`tests/unit/test_turn_budget.py` provides thorough coverage of the harness-level safety features introduced in Phase 1:

- **`TestBuildSystemPrompt`** (4 tests): Validates that `build_system_prompt()` includes the turn budget statement, graph-first strategy when `has_graph=True`, diff-based fallback when `has_graph=False`, and that the turn count parameter is correctly interpolated. Covers both code paths of the graph/no-graph conditional.

- **`TestDeadlineInjectionChatCompletions`** (2 tests): Uses a snapshot side-effect pattern to capture the `messages` list at each API call, verifying the deadline message appears exactly once at turn N-3 and does **not** appear in any earlier turn. The snapshot approach is well-designed — it handles the in-place mutation of the messages list correctly, which is a subtle detail.

- **`TestDeadlineInjectionResponsesAPI`** (1 test): Verifies deadline injection in the Responses API path by inspecting `call_args_list` on the mock client. Uses `codex-mini-latest` model to route to the correct API path.

- **`TestTurnCounter`** (2 tests): Confirms the turn counter suffix (`[Turn X/Y used. Z remaining.]`) appears in tool result messages at turn 1 and turn 2, validating the counter increments correctly.

- **`TestEmergencyFallback`** (1 test): Verifies that when the agent produces no parseable JSON, emergency findings are synthesized with the correct schema keys (`pr_id`, `repo`, `vcs`, `findings`, `error`).

Mock design is appropriate — mocks target the OpenAI client and ToolRegistry at the boundary, not internal implementation details. The `_make_runner` helper is clean and reusable. No flaky patterns (no time-dependent assertions, no network calls).

---

## Task 9: Data Flow + Fallback Unit Tests

**PASS**

`tests/unit/test_review_job.py` covers both the `_scan_history_for_findings` pure function and the `ReviewJob.create_findings()` integration with the PR data pre-fetch:

- **`TestScanHistoryForFindings`** (8 tests): Exercises the fallback extraction logic thoroughly:
  - Code-fenced JSON with `findings` key
  - Bare JSON with `findings` key (relies on `_brace_balanced_extract`)
  - Graceful `None` return for no-match, empty list, and empty strings
  - Prefers larger JSON when multiple candidates exist
  - Scans multiple texts and picks the best match
  - Handles `cr-` ID patterns in code fences
  - Ignores invalid JSON gracefully

  This test class is notably well-structured — it tests a pure function with no mocking needed, making the tests fast and deterministic.

- **`TestChangedFilesPropagation`** (3 tests): Validates the critical data flow fix (Bug 1 from requirements):
  - `changed_files` is correctly extracted from `FetchPRDetailsActivity` and passed to `OpenAIAgentRunner.__init__`
  - When the pre-fetch fails (e.g., `ConnectionError`), `create_findings()` degrades gracefully with `changed_files=[]`
  - When files are available, the prompt contains the "Pre-fetched PR Data" section with file paths and `get_change_analysis` instruction

  The `_inject_activity_module` helper is a pragmatic solution for mocking the ADO SDK dependency without requiring it to be installed.

- **`TestEmergencyFindingsSchema`** (4 tests): Verifies the emergency findings structure has all required keys, empty findings list, and error field. One test (`test_create_findings_writes_valid_json`) goes end-to-end through `create_findings()` and validates the written `findings.json` file.

---

## Task 10: Integration Test Cost Control

**PASS**

`tests/integration/conftest.py` defines `MAX_TURNS_INTEGRATION = 15` as a module-level constant. `test_full_pipeline.py` imports and uses it in the `ReviewJobConfig` constructor (`max_turns=MAX_TURNS_INTEGRATION`), replacing the previous hardcoded 40-turn budget. This reduces integration test cost by ~62% while still providing enough turns for meaningful agent behavior.

The constant is properly exported in the `conftest.py` imports at the top of `test_full_pipeline.py`, making it easy to find and adjust if needed.

---

## Sprint Completeness

All 10 tasks across 4 phases are implemented and committed:

| Phase | Task | Status | Commit |
|-------|------|--------|--------|
| Phase 1 | Task 1: Deadline injection at turn N-3 | Done | `421b131` |
| Phase 1 | Task 2: Turn-count reporting in tool results | Done | `421b131` |
| Phase 1 | Task 3: Fallback extraction + emergency findings | Done | `421b131`, `6c44117` (fix) |
| Phase 2 | Task 4: Pre-fetch PR data, inject changed_files | Done | `3cf83cf` |
| Phase 2 | Task 5: build_system_prompt() with graph/no-graph | Done | `a0cdd6d` |
| Phase 3 | Task 6: Step 2b mandatory, remove T1-T2 skip | Done | `119a437` |
| Phase 3 | Task 7: Tier-based strategy + deadline warning | Done | `10ca3fd` |
| Phase 4 | Task 8: Turn budget unit tests | Done | `44daa86`, `07cd5e8` (fix) |
| Phase 4 | Task 9: changed_files + fallback unit tests | Done | `dbc106a`, `07cd5e8` (fix) |
| Phase 4 | Task 10: MAX_TURNS_INTEGRATION = 15 | Done | `49cfd66` |

Key implementation details verified across all phases:

- **Deadline injection** fires at `turn == max_turns - 3` in both Chat Completions and Responses API loops (openai_runner.py lines 162-169, 294-301).
- **Turn counter** appended to every tool result in both loops (lines 238-239, 378-379).
- **Fallback extraction** scans assistant message history using `_scan_history_for_findings` with brace-balanced JSON extraction (lines 250-257, 390-392).
- **Emergency findings** synthesized as last resort with proper schema and error field (lines 258-268, 393-403).
- **changed_files** propagated from `FetchPRDetailsActivity` through `ReviewJob.create_findings()` to `OpenAIAgentRunner.__init__` (review_job.py lines 67-99).
- **RuntimeError removed** — replaced with `warnings.warn()` (review_job.py lines 104-109).
- **build_system_prompt()** dynamically generates system prompt based on `max_turns` and `has_graph` (openai_runner.py lines 27-71).
- **Review prompt** Step 2b is mandatory with graph-first instructions (review-pr-core.md lines 101-118), Step 5 has tier-based strategy table (lines 169-178), Step 7 has deadline warning (line 350).

---

## Summary

**All 10 tasks pass review.** The sprint delivers a comprehensive fix for the agent prompt strategy failure on PR #6435:

1. **Harness safety net** (Phase 1): Deadline injection, turn counter, and fallback extraction ensure the pipeline never crashes regardless of agent behavior.
2. **Data flow** (Phase 2): Pre-fetched PR data eliminates the bootstrapping problem; dynamic system prompt mandates graph-first strategy.
3. **Review prompt** (Phase 3): Mandatory Step 2b, tier-based review depth, and deadline warnings guide the agent toward efficient behavior.
4. **Test coverage** (Phase 4): 25+ unit tests covering all key scenarios with clean mock design and no flaky patterns.

No changes needed. Sprint is complete and ready for integration testing against a live PR.
