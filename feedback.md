# Agent Prompt Strategy Fix — Plan Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-04-30T12:00:00+05:30
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.

---

## 1. Clear "Done" Criteria — PASS

Every task in PLAN.md has an explicit "Done when:" block with concrete, testable conditions. Task 1 requires "deadline message appears in the conversation. Unit test confirms injection." Task 3 requires "Pipeline never raises RuntimeError on missing findings. Emergency findings are valid JSON matching the schema." Task 5 requires "System prompt is dynamically generated. Graph-first instructions appear when graph is available." These are binary pass/fail conditions a developer can verify without ambiguity.

---

## 2. Cohesion and Coupling — PASS

Each phase is internally cohesive. Phase 1 groups all harness-level safety nets (deadline injection, turn counters, fallback extraction). Phase 2 groups data flow and system prompt — both concern what the agent sees before its first turn. Phase 3 is purely review prompt text changes. Phase 4 is test coverage. Cross-phase coupling is minimal: Phase 4 tests depend on Phases 1-2 being complete (stated in blockers), and Phase 2 Task 5's `build_system_prompt()` function is self-contained. No hidden shared state between phases.

---

## 3. Key Abstractions in Earliest Tasks — PASS

The plan front-loads Phase 1 with the two core abstractions that later phases build on: deadline injection (the "hard backstop" pattern reused conceptually in Task 5's prompt and Task 7's review prompt) and fallback extraction (the safety net that makes all other changes lower-risk). The `build_system_prompt()` function introduced in Task 5 is the only new shared interface, and it's defined before any consumer (Tasks 6-7) needs it.

---

## 4. Riskiest Assumption Validated First — PASS

Task 1 explicitly acknowledges the riskiest assumption: "will the agent obey a mid-loop injection?" The plan places this first so if the agent ignores deadline messages, the team knows immediately and can escalate to harder enforcement (e.g., forcibly truncating the loop). This is the correct prioritization — the requirements.md placed harness enforcement in Phase 4, but PLAN.md wisely moved it to Phase 1.

---

## 5. DRY / Reuse of Early Abstractions — PASS

Task 5's `build_system_prompt(max_turns, has_graph)` function is called from both `_run_chat_completions` and `_run_responses`, avoiding the current duplication of prompt construction. Task 3's fallback extraction logic is designed once and applied at both extraction points (lines 213 and 320). The turn-counter suffix (Task 2) uses a single format string applied in both loop variants.

---

## 6. Phase Structure with VERIFY Checkpoints — PASS

Phase 1: 3 tasks + VERIFY. Phase 2: 2 tasks + VERIFY. Phase 3: 2 tasks + VERIFY. Phase 4: 3 tasks + VERIFY. Each VERIFY checkpoint specifies what to run (`pytest tests/`) and what to confirm. This is well-structured and prevents phases from bleeding into each other without validation.

---

## 7. Single-Session Completability — PASS

All tasks are scoped to 1-2 files with specific line ranges. The largest task is Task 3 (fallback extraction), which touches `openai_runner.py` (two insertion points) and `review_job.py` (one RuntimeError removal). Even this is a focused change with clear boundaries. Task 5 (system prompt rewrite) is the most creative work but is constrained to a single function with prescribed behavior.

---

## 8. Dependency Order — PASS

Dependencies are explicitly listed in each task's "Blockers" field. Tasks 1-7 all have "None" blockers and can proceed independently. Task 8 depends on Tasks 1-2 (tests for features those tasks introduce). Task 9 depends on Tasks 3-4. Task 10 has no blockers. The dependency graph is a clean DAG with no cycles.

---

## 9. Ambiguity Check — PASS with NOTE

**NOTE:** Task 3's "scan all assistant messages for JSON blocks containing `findings` or `cr-` patterns" leaves the matching strategy slightly open. Should it use regex? JSON parsing with fallback? What if multiple partial JSONs exist? The "use the last/largest match" heuristic is stated but "largest" is ambiguous (longest string? most findings?). Two developers could implement different scanning strategies. However, the done criteria ("Emergency findings are valid JSON matching the schema") constrains the output sufficiently that implementation variance is acceptable.

**NOTE:** Task 5's actual prompt text is left to the implementer. The plan provides intent and structure but not exact wording. This is appropriate for prompt engineering — overly prescriptive prompt text in a plan is counterproductive since prompts need empirical tuning.

---

## 10. Hidden Dependencies — PASS

I checked for implicit coupling: Task 4 injects `changed_files` into the prompt, and Task 5 builds a system prompt that references graph tools. These are in the same phase and the plan correctly sequences them (Task 4 before Task 5 in the recommended single-developer order). Task 7's review prompt references `max_turns` as a hardcoded value (40), not as a dynamic variable — this means it doesn't depend on any runtime plumbing from other tasks.

One observation: `FetchPRDetailsActivity` is not currently imported in `review_job.py` (it's only in `tests/integration/conftest.py`). Task 4 will need to add this import. This is implicit but obvious to any developer implementing the task.

---

## 11. Risk Register — PASS

The plan includes a 6-row risk register covering:
- LLM unpredictability (prompt changes may not work) — mitigated by harness backstops
- Pre-fetch latency — mitigated by net token savings
- Prompt bloat for large PRs — mitigated by 100-file truncation
- Turn counter noise — mitigated by single-line format
- Late deadline injection — mitigated by 3-layer budget signals
- Emergency findings masking failures — mitigated by `error` field for downstream triage

This is comprehensive. One risk not explicitly listed: **regression in existing prompt behavior for small PRs** (T1-T2). The plan makes Step 2b mandatory for all PRs when graph tools are available, which changes behavior for small PRs that previously skipped it. The impact is low (graph analysis on small PRs is fast and helpful) but worth noting.

---

## 12. Alignment with Requirements — PASS

The requirements.md identifies 5 bugs and proposes 14 steps across 6 phases. PLAN.md addresses all 5 bugs and consolidates into 10 tasks across 4 phases:

| Requirements Steps | PLAN Tasks | Notes |
|---|---|---|
| Steps 1+2 (data flow) | Task 4 | Merged — both concern changed_files |
| Steps 3+4 (system prompt) | Task 5 | Merged — both concern prompt builder |
| Step 5 (Step 2b mandatory) | Task 6 | Direct mapping |
| Steps 6+7 (review prompt) | Task 7 | Merged — both concern review-pr-core.md |
| Steps 8+9 (harness enforcement) | Tasks 1+2 | Reordered to Phase 1 (improvement) |
| Steps 10+11 (fallback) | Task 3 | Merged and moved to Phase 1 (improvement) |
| Steps 12-14 (tests) | Tasks 8-10 | Direct mapping |

No requirements are dropped. The reordering from requirements.md (data flow first → harness last) to PLAN.md (harness first → data flow second) is a genuine improvement: it ensures the safety net exists before any prompt changes are attempted. The consolidation of 14 steps into 10 tasks reduces overhead without losing scope.

---

## Summary

**All 12 checks pass.** The plan is well-structured, correctly prioritized, and faithful to the requirements. The decision to front-load harness safety (Phase 1) before prompt engineering (Phases 2-3) is the most significant structural improvement over the raw requirements ordering — it ensures the pipeline never crashes even if prompt changes underperform.

Two minor notes for the implementer:
1. Task 3's JSON scanning heuristic ("last/largest match") could benefit from a one-line clarification of what "largest" means during implementation.
2. Task 4 will need to add `FetchPRDetailsActivity` import to `review_job.py` (not currently imported there).

No changes are required. The plan is approved for implementation.
