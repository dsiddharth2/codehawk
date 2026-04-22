# Code Reviewer v3.1 — Plan Review (Re-review)

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-04-22 12:15:00+05:30
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review. Prior review (270a03e) flagged 6 must-fix and 4 should-fix items. Doer addressed all 10 in commit f914966. This re-review verifies each resolution.

---

## Prior Finding Resolution

### Must-fix items

**1. Split Task 2 into models+config and activities — RESOLVED.**
PLAN.md now has Task 2 (Port models + config, 2 files, standard tier) and Task 3 (Port activities, 8 files, standard tier). The split is clean — Task 2 handles the data layer (models + config), Task 3 handles the VCS layer (activities). Task 3 correctly depends on Task 2 ("models + config must exist for activity imports"). Each is a single-session unit. Phase 1 now has 4 work tasks instead of 3, which is a minor deviation from the 2-3 guideline but acceptable since Tasks 1 and 2 are small.

**2. Fix Task 21 dependency to Phase 5 — RESOLVED.**
Task 21 (GitHub path) now declares: "Blockers: Phase 5 (fix verification logic must be in post_findings.py before extending GitHub path)". Change description explicitly includes fix verification for GitHub: "reply with 'Fixed', optionally minimize via GraphQL". Rate-limit retry also added (ties to R9). Correct.

**3. Account for markdown_formatter.py — RESOLVED.**
Added to Task 1 files list and done criteria. Task 7 references it for summary formatting. DEFERRED section notes "advanced formatting deferred" while base version is Sprint 1. Clean resolution.

**4. Add .codereview.yml parsing to a specific task — RESOLVED.**
Added to Task 7 (post_findings.py) as step 9: "read `.codereview.yml` from workspace if present — extract gate thresholds (min_star_rating, fail_on_critical) and apply them to the CI gating output". Done criteria updated. Unit test coverage added in Task 8. Complete.

**5. Add docs/chore mode — RESOLVED.**
Task 14 creates `commands/review-mode-docs-chore.md` with spec: "doc accuracy, changelog completeness, config correctness, no functional logic — light-touch review that skips deep code analysis". Task 15 adds docs/chore detection rules to the prompt ("only .md/.yml/.json files changed, PR label 'docs' or 'chore'"). Done criteria for both tasks reference docs/chore. All 6 modes from requirements are now in Sprint 1 (4 in Sprint 1 tasks + 2 deferred in Phase 7).

Wait — re-checking: requirements list 6 modes: standard, security, architecture, performance, migration, docs/chore. Sprint 1 implements 4: standard, security, migration, docs/chore. Phase 7 (deferred) has architecture + performance. That's 4 + 2 = 6. Correct.

**6. Add missing items to DEFERRED — RESOLVED.**
New "Deferred Utilities" section includes `comment_exporter.py`. Phase 10 now explicitly lists `README.md`. Both gaps closed.

### Should-fix items

**7. Strengthen Task 10 done criteria — RESOLVED.**
Task 10 done criteria now reads: "Prompt contains all 7 numbered steps; includes 'max 30 findings', 'max 5 per file', 'max 40 tool calls' constraints verbatim; references `commands/findings-schema.json` by path; has VCS-conditional blocks (ADO vs GitHub) in Steps 2, 5, and 6; scoring.md has complete penalty matrix with all severity levels and categories." This is specific and mechanically verifiable. Two developers would produce equivalent outputs against these criteria.

**8. Strengthen Task 12 done criteria — RESOLVED.**
Task 12 now has three-tier done criteria: (1) `docker build` succeeds (hard gate), (2) dry-run with `tests/fixtures/sample_findings.json` inside container produces valid structured JSON (hard gate), (3) live PR test is stretch goal. The sample fixture file is also listed in the Files section. No ambiguity remains.

**9. Add three missing risks to register — RESOLVED.**
R9 (GitHub API rate limiting, Medium, Phase 6) — mitigated by retry-with-backoff in Task 21. R10 (Docker image size 2GB+, Medium, Phase 3) — mitigated by tracking in Phase 3 VERIFY, optimization in Phase 9. R11 (Schema drift, Medium, Phase 2) — mitigated by schema validation in Task 7. Each risk is cross-referenced to the task that implements its mitigation. Complete.

**10. Clarify AGENTS.md/CLAUDE.md content — RESOLVED.**
Task 12 change description now specifies: "explain two-phase architecture, instruct agent to read `commands/review-pr-core.md` as its primary directive, list available tools (`python vcs.py`, `gh`, `rg`, `repomix`), specify output location (`/workspace/.cr/findings.json`), include constraint reminders (40 tool calls, no posting)". Done criteria mirror this. No ambiguity.

---

## Re-review Against 12 Criteria

### 1. Done Criteria Clarity — PASS
All tasks now have concrete, verifiable done criteria. The two previously weak tasks (now Tasks 10 and 12) have been strengthened with specific required elements.

### 2. Cohesion and Coupling — PASS
Task 2 split resolved the low-cohesion issue. Each task now has a single responsibility: Task 1 (scaffold + utilities), Task 2 (data models + config), Task 3 (VCS activities), Task 4 (scoring).

### 3. Key Abstractions Early — PASS
No change from prior review. Finding/FindingsFile, Settings, activity base class, and scorer are all established in Phase 1.

### 4. Riskiest Assumption Validated Early — PASS
No change. R3 (post_findings.py) in Phase 2, R1 (Codex sandbox) in Phase 3 smoke test.

### 5. DRY / Reuse — PASS
No change. `markdown_formatter.py` is now explicitly ported in Task 1 and consumed by Task 7, which strengthens this.

### 6. Phase Structure — PASS (with note)
Phase 1 now has 4 work tasks + VERIFY (was 3+1). This exceeds the 2-3 guideline. Acceptable because Tasks 1 (cheap) and 2 (standard, 2 files) are small — the 4-task phase is lighter than many 3-task phases. All other phases remain at 2-3 work + VERIFY.

### 7. Single-Session Completability — PASS
The Task 2 split resolved the only concern. All tasks are now appropriately sized for their tier.

### 8. Dependencies Satisfied in Order — PASS
Task 21's dependency corrected to Phase 5. All other dependencies were already correct. Verified: Task 3 → Task 2 → Task 1 chain is clean. Task 7 → Tasks 4, 6. Task 15 → Tasks 7, 10, 14 (cross-phase but correct).

### 9. Vague Tasks — PASS
Task 10 and Task 12 (previously Tasks 7 and 9) now have specific, unambiguous done criteria. Task 12 also specifies AGENTS.md/CLAUDE.md content.

### 10. Hidden Dependencies — PASS
All three previously hidden dependencies resolved: markdown_formatter.py in Task 1, .codereview.yml parsing in Task 7, comment_exporter.py in DEFERRED. No new hidden dependencies found.

### 11. Risk Register — PASS
11 risks now, up from 8. All three flagged risks added with severity, phase, and mitigation cross-referenced to implementation tasks.

### 12. Alignment with Requirements — PASS
All 6 review modes accounted for (4 Sprint 1 + 2 deferred Phase 7). docs/chore no longer dropped. All 8 functional requirements from requirements.md are addressed in Sprint 1 except local CLI (correctly deferred to Phase 11).

---

## Structural Verification

- **progress.json task count:** 23 entries, sequential IDs 1-23. Correct.
- **Work vs verify split:** 17 work + 6 verify = 23. Correct.
- **PLAN.md ↔ progress.json alignment:** Each progress.json entry matches the corresponding PLAN.md task/verify by name and tier. Verified all 23.
- **Tier distribution:** 3 cheap, 12 standard, 2 premium. Reasonable — premium reserved for the two highest-complexity items (post_findings.py and review-pr-core.md).

---

## Summary

All 6 must-fix and 4 should-fix items from the prior review have been adequately addressed. The plan is now internally consistent, complete against requirements, and ready for implementation.

**Approved for Sprint 1 execution (Phases 1-6, Tasks 1-23).**

One advisory note for the doer: Phase 1 now has 4 work tasks — consider whether the Phase 1 VERIFY should include extra import-chain validation since the models+config and activities are split across two tasks (Task 2 and Task 3) rather than validated together.
