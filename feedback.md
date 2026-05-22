# Sprint 3: Review Quality + Coverage — Plan Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-05-22T00:00:00+05:30
**Verdict:** CHANGES NEEDED

---

## 1. Does every task have clear "done" criteria?

**PASS.** Every task in the plan specifies grep-verifiable or output-verifiable "Done when" criteria. Examples of strong criteria: Task 1 ("Grep for 'max 10' returns zero matches; 'max 40' appears in Step 0"), Task 7 ("risk_classifier.classify(files) returns HIGH/MEDIUM/LOW for a mock file list"), Task 19 ("Given 3 findings on same file with lines 10, 12, 50 — the first two merge, the third stays separate"). No task leaves the implementer guessing what "done" means.

---

## 2. High cohesion within each task, low coupling between tasks?

**PASS with NOTE.** Each phase is thematically cohesive:

- Phase 1: Prompt/parameter alignment only (no code logic changes)
- Phase 2: Coverage system (classifier, schema, gate, scoring)
- Phase 3: Scoring taxonomy and checklists
- Phase 4: Noise reduction and audit
- Phase 5: Language intelligence
- Phase 6: Fix verification and parallelism

**NOTE:** Phase 6 bundles two fairly distinct concerns — deterministic fix verification (Task 28) and parallel batch execution (Task 29). These share no code, no data structures, and no abstractions. The justification for co-locating them is thin — "execution efficiency" is a stretch. However, splitting them into separate phases would add a 7th commit, and both are self-contained. The coupling cost is low enough that this is acceptable as-is.

---

## 3. Are key abstractions and shared interfaces in the earliest tasks?

**PASS.** The plan correctly sequences foundational abstractions before their consumers:

- `risk_classifier.py` (Task 7) created before injection into prompt (Task 8), coverage gate usage (Task 11), and audit trail inclusion (Task 20)
- `files_clean` field (Task 10) created before coverage calculation (Task 11)
- `languages.yml` registry (Task 22) created before stack detector (Task 23) and integration (Task 26)
- Config fields (Task 11) created before Task 12 adjusts defaults

---

## 4. Is the riskiest assumption validated in Task 1?

**PASS.** The spec's root cause analysis identifies conflicting tool-call budgets as the primary driver of ~30% false positives. Phase 1 addresses this directly: aligning the limit to 40 (Task 1), rewriting the turn efficiency guidance (Task 2), adding verify-before-CRITICAL (Task 3), and setting temperature/seed for consistency (Task 4). This is the right ordering — if the turn budget alignment doesn't reduce false positives, the later phases (coverage gate, risk classifier) would be optimizing the wrong thing.

---

## 5. Later tasks reuse early abstractions (DRY)?

**PASS.** Clear reuse chains:

- `risk_classifier.classify()` -> Task 8 (prompt injection) -> Task 20 (audit trail includes risk table)
- `files_clean` field -> Task 11 (coverage calculation) -> Task 20 (audit trail files reviewed)
- `config.py` fields -> Task 12 (batch_max_turns), and used by risk_classifier thresholds
- `languages.yml` -> Task 23 (stack detector), Task 26 (file_filter.py registry lookup)

No duplicated abstractions detected.

---

## 6. 2-3 work tasks per phase, then a VERIFY checkpoint?

**PASS with NOTE.** Task counts per phase:

| Phase | Work Tasks | Within Range? |
|-------|-----------|---------------|
| 1 | 5 | No — but each is a small, isolated edit (one grep, one constant, one step insertion) |
| 2 | 6 | No — but this is the most complex system (classifier + schema + gate + scoring + alignment) |
| 3 | 4 | No — but 2 are new checklist files (content, not logic) |
| 4 | 2 | Yes |
| 5 | 5 | No — 15 lang files drive the count; splitting would fragment the registry |
| 6 | 2 | Yes |

Every phase ends with a VERIFY checkpoint that specifies mocked unit tests and grep checks. The oversized phases are justified by spec complexity — Phase 1 tasks are each <10 lines of changes, Phase 5's bulk is content files not logic. Acceptable.

---

## 7. Each task completable in one session?

**PASS with NOTE.** Most tasks are targeted single-file edits. Two tasks warrant attention:

- **Task 24** (8 rules files, 30-50 items each): ~240-400 checklist items of content. Heavy but mechanical — each file follows the same template. One session is tight but feasible.
- **Task 25** (7 rules files, 30-50 items each): Same pattern, slightly smaller. Feasible.
- **Task 28** (fix verifier): Most complex new file. However, the algorithm is exhaustively specified with 5 steps, clear input/output, and explicit edge cases. A senior dev could implement this in one session.

---

## 8. Dependencies satisfied in order?

**PASS.** All declared blockers are sequenced correctly:

- Task 8 -> depends on Task 7 (risk classifier must exist before injection)
- Task 11 -> depends on Task 10 (files_clean must exist before coverage calc)
- Task 12 -> depends on Task 11 (config fields must exist)
- Task 15 -> depends on Task 14 (testing category first, then expand)
- Task 23 -> depends on Task 22 (registry before detector)
- Task 26 -> depends on Tasks 22, 23, 24, 25 (all pieces before integration)

Cross-phase dependencies are also handled by sequential phase order (e.g., Task 20's audit trail references Phase 2's risk table, and Phase 4 runs after Phase 2).

---

## 9. Any vague tasks that two developers would interpret differently?

**FAIL — one issue found.**

**Task 11 vs Task 12 — `batch_max_turns` contradiction.** Task 11 (Change 5) adds `batch_max_turns: int = Field(default=40, ...)` as a new config field. Task 12 (Change 1) says "change the default from `15` to `40`" and states "The existing code has `batch_max_turns: int = 15`."

These contradict each other:
- If the field already exists at 15 in the codebase, Task 11 should not list it as a new field — it should only add the other three fields (`coverage_gate_mode`, `risk_high_threshold`, `risk_medium_threshold`).
- If the field does not exist, Task 11 creates it at 40 and Task 12 is a no-op.

**Fix required:** Determine whether `batch_max_turns` already exists in `config.py`. If yes, remove it from Task 11's new-field list and let Task 12 handle the value change. If no, add it in Task 11 at 40 and remove Task 12's Change 1 (keep only Change 2 — the grep check).

**Doer:** fixed in commit to follow — `batch_max_turns` confirmed to exist at `src/config.py:125` (default=15). Removed it from Task 11's new-field list; Task 11 now adds only `coverage_gate_mode`, `risk_high_threshold`, `risk_medium_threshold`. Task 12 Change 1 correctly owns the `default=15 → default=40` modification and now explicitly references line 125-127.

---

## 10. Any hidden dependencies between tasks?

**PASS with NOTE.**

- **Task 9** says "Blockers: none" but references the "pre-computed risk table" which is injected by Task 8. However, Task 9 only adds prompt text that describes how to interpret the table — it doesn't depend on the table injection code. The instructions work even if the table doesn't exist yet (the agent just won't see a table). Not a true blocker. OK.

- **Task 20** (audit trail, Phase 4) includes the risk classification table from Phase 2. Since Phase 4 executes after Phase 2, this dependency is satisfied by phase ordering. However, it's not declared as a blocker — it should at minimum note "Requires Phase 2 risk classifier to be complete."

---

## 11. Does the plan include a risk register? If missing or incomplete, identify the risks yourself.

**PASS with NOTE.** The plan includes a 6-item risk register covering: seed non-determinism, heuristic weights, SequenceMatcher limitations, rate limits, monorepo deferral, and fix verifier LLM costs. Each has an impact assessment and mitigation.

**Missing risks the register should include:**

1. **Prompt token budget blow-up.** 15 lang-rules files at 30-50 items each could inject thousands of tokens when multiple languages are detected (e.g., a full-stack repo with C#, TypeScript, React, SQL). This could crowd out diff context in the prompt. **Mitigation:** Cap total injected rules tokens; only inject rules for languages with changed files in the PR.

2. **Coverage gate blocking PRs on agent bugs.** The plan mandates 100% hard gate from day one with no gradual rollout. If the agent has an edge case (e.g., a file type it doesn't recognize, a diff fetch failure it doesn't recover from), the gate blocks the PR and the team has to manually switch to `"log"` mode. **Mitigation:** Run in `"log"` mode for the first 5-10 production PRs to validate, then switch to `"hard"`.

3. **Temperature change regression.** Moving from the current default (likely 1.0 or unset) to 0.3 changes model behavior. While the spec cites pipeline 268 as precedent, the current codebase may produce different results at 0.3 due to prompt changes since that pipeline. **Mitigation:** Compare finding quality on 3-5 PRs before and after the temperature change.

---

## 12. Does the plan align with the spec's intent — solving the right problem?

**PASS.** The spec identifies five measured problems (P1-P4, P7) and the plan addresses each:

| Problem | Spec Target | Plan Solution |
|---------|------------|---------------|
| P1 — False positives (~30%) | <10% | Phase 1: turn limit alignment, verify-before-CRITICAL, temperature |
| P2 — Test noise (~40% "add tests") | 0% gate impact | Phase 3: testing category with zero-weight scoring |
| P3 — Run inconsistency | >80% Jaccard overlap | Phase 1: temperature=0.3, seed=42 |
| P4 — Silent file skips (~60-70% coverage) | 100% coverage | Phase 2: risk classifier, coverage gate, files_clean |
| P7 — Category imbalance (85% best_practices) | <50% best_practices | Phase 3: expanded categories (9 total) |

The plan also includes spec-aligned additions beyond the core problems: language intelligence (15 languages), deterministic fix verification, parallel batch execution, and audit trail. All trace back to spec sections.

---

## Plan VERIFY Sections vs Spec VERIFY Sections

**FAIL — Phase 1 VERIFY is incomplete.** The plan's Phase 1 VERIFY (section 1.6) checks only 4 items:

1. pytest passes
2. Grep for "ZERO or minimal" — gone
3. Grep for "max 10" — gone
4. temperature=0.3 in both API call sites

The spec's Phase 1 VERIFY additionally checks:
5. Step 5e (verify-before-CRITICAL) exists in `review-pr-core.md`
6. Failed diffs surfacing logic exists in `review_job.py`

Tasks 1.3 and 1.5 have no verification coverage in the plan's VERIFY step. Both tasks could be implemented, break, or be skipped without the VERIFY step catching it.

**Fix required:** Add items 5 and 6 to the Phase 1 VERIFY section.

**Doer:** fixed in commit to follow — Phase 1 VERIFY now includes: grep `review-pr-core.md` for "5e" or "Verify before flagging CRITICAL" (Task 3 check), and grep `review_job.py` `_pre_fetch_diffs` for `failed_diffs` list collection and prompt injection (Task 5 check).

---

## Constraints Verification

| Constraint | Status |
|-----------|--------|
| Branch: `feat/review-quality` from `origin/main` | **PASS** — stated in plan header and constraints table |
| 1 commit per phase (not per task) | **PASS** — commit strategy table lists exactly 6 commits for 6 phases |
| MOCKED UNIT TESTS ONLY in VERIFY steps | **PASS** — every VERIFY step begins with "MOCKED UNIT TESTS ONLY" |
| NEVER push to main, master, or development | **PASS** — constraints table explicitly states this |
| Commit messages match the spec | **PASS** — plan and spec commit messages are identical |

---

## Summary

**Verdict: CHANGES NEEDED** — two issues require fixes before implementation begins.

### Must fix (blocking):

1. **Task 11 / Task 12 `batch_max_turns` contradiction.** Determine whether the field exists in the current codebase. If yes, remove from Task 11's new-field list. If no, remove Task 12's Change 1. Two developers reading this plan today would make different assumptions about which task owns this field.

2. **Phase 1 VERIFY missing two checks.** Add verification of Step 5e (verify-before-CRITICAL) and failed diffs surfacing to the Phase 1 VERIFY section. Without these, two of five Phase 1 tasks have no verification coverage.

### Should fix (non-blocking):

3. **Risk register gaps.** Add prompt token budget risk (multi-language repos blowing up prompt size) and coverage gate day-one risk (agent bugs blocking PRs with no gradual rollout). These are the two most likely production incidents from this sprint.

**Doer:** addressed in commit to follow — added both risks to the Risk Register table: (a) prompt token blow-up with mitigation "only inject rules for languages with changed files in the PR", (b) hard-gate day-one risk with mitigation "run in log mode for first 5-10 PRs to validate, then switch to hard".

4. **Task 20 undeclared dependency.** Note that the audit trail export depends on Phase 2's risk classifier being complete.

**Doer:** addressed in commit to follow — Task 20 Blockers note updated to: "none (Phase 4 runs after Phase 2; risk classifier from Task 7 must be complete for the risk table section of the audit — satisfied by phase ordering)".

### Passed without issues:

- All tasks have clear done criteria
- Cohesion within phases is strong
- Key abstractions sequenced before consumers
- Riskiest assumption (turn budget) validated first
- Later tasks reuse early abstractions (DRY)
- Dependencies satisfied in declared order
- Each task is one-session scoped
- Plan aligns with spec intent across all 5 measured problems
- All explicit constraints (branch, commits, test type, protected branches, commit messages) satisfied
