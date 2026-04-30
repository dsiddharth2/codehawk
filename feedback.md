# Agent Prompt Strategy Fix — Phase 3 Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-04-30T12:45:00+05:30
**Phase:** Phase 3 — Review Prompt Updates
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.

---

## Task 6: Mandatory Step 2b

**PASS**

The T1-T2 skip clause has been fully removed. The original line "For T1-T2 PRs: skip this step (full review is cheap enough)" is gone from the file, along with the adjacent "For T3+ PRs: focus your review budget..." bullet. The heading was correctly renamed from "Step 2b — Analyze Change Impact (if graph tools available)" to "Step 2b — Analyze Change Impact (MANDATORY when graph tools available)" — the emphasis is clear and unambiguous.

The new mandatory framing reads well: "This step is REQUIRED for all PRs where graph tools are available" followed by a direct instruction to call `get_change_analysis` with file paths from "the PR Data section above." This phrasing correctly references the Pre-fetched PR Data section injected by Task 4 (Phase 2), which provides the changed file list up-front.

The ranked review plan rule is present: "review files with `risk_score > 0.5` first, then files with `test_gaps`, then remaining files in priority order." This matches the requirements spec exactly. The fallback clause ("If the tool returns an error or is unavailable, continue to Step 3 normally") is retained — correct, since it provides resilience without undermining the mandatory posture.

No formatting issues. Logical flow from Step 2 → Step 2b → Step 3 is clean.

## Task 7: Tier-Based Strategy + Deadline Warning

**PASS**

The tier-based review depth table was added to Step 5, positioned after the re-push note and before the "For each file within your tier budget" instruction. This is the correct location — the agent reads its strategy before entering the per-file loop. The table matches the requirements spec:

- T1-T2 (1–10 files): Read each file fully, graph optional — correct
- T3 (11–25 files): Graph priorities, read top 15, skim rest via diffs — correct
- T4 (26–50 files): Graph priorities, read top 10 high-risk, diffs for rest — correct
- T5 (51+ files): Graph priorities, read top 8, `get_blast_radius` for cascading risks, diffs only — correct

The budget rule is present: "Finish all file reading by turn {max_turns - 5}. Reserve the remaining turns for findings synthesis and writing output." The `{max_turns - 5}` placeholder is consistent with the PLAN.md spec. This is a literal template string in the markdown, not a dynamically resolved value — acceptable because the system prompt (Phase 2, Task 5) already injects the concrete turn budget, and the review prompt serves as a reinforcing heuristic.

The Step 7 deadline warning is prominent and correctly positioned at the very top of the step:

> **CRITICAL: If you are on turn 35+ (of 40), STOP reading files and produce findings NOW. Partial findings are infinitely better than no findings. Output what you have.**

Bold formatting + "CRITICAL" prefix makes this unmissable. The specific numbers (35+ of 40) match the default `max_turns=40` configuration. This works in concert with the harness-level deadline injection at N-3 (Phase 1, Task 1).

No markdown formatting issues. The table renders correctly.

## Integration with Phases 1-2

**PASS**

The Phase 3 prompt changes align well with the earlier phases:

1. **Phase 1 (Harness Safety Net):** The Step 7 deadline warning ("turn 35+") complements the harness deadline injection at `max_turns - 3` (turn 37). The agent gets a soft prompt warning at 35 and a hard injected message at 37. Three layers of budget awareness: continuous turn counter in tool results (Task 2), prompt-level warning (Task 7), harness injection (Task 1).

2. **Phase 2 (Data Flow + System Prompt):**
   - Task 4 injects a "Pre-fetched PR Data" section into the prompt. Step 2b now references "the PR Data section above" — this cross-reference is correct and the agent can find the file list without discovering it via `get_pr`.
   - Task 5's `build_system_prompt()` mandates graph-first strategy and states the turn budget. Step 2b reinforces this by making `get_change_analysis` mandatory. Step 5's tier table reinforces graph usage for T3+. No contradictions between system prompt and review prompt instructions.
   - The system prompt's graph-first mandate ("Your FIRST tool call MUST be `get_change_analysis`") and Step 2b's mandatory framing are consistent — both push the agent toward graph tools immediately.

3. **No regressions detected:** The review prompt still references the correct tool names (`get_change_analysis`, `get_blast_radius`, `get_callers`, `get_dependents`). The fallback clause in Step 2b aligns with the system prompt's fallback instruction ("If a graph tool returns an error, fall back to `search_code`").

---

## Summary

Both Task 6 and Task 7 pass review. The T1-T2 skip clause is fully removed, Step 2b is clearly mandatory, the tier table matches spec, the budget rule is present, and the deadline warning is prominent. All Phase 3 changes integrate cleanly with the harness safety net (Phase 1) and system prompt rewrite (Phase 2) — no contradictions, correct cross-references, and reinforcing layered budget awareness. No changes needed.
