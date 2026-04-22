# Code Reviewer v3.1 — Plan Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-04-22 11:45:00+05:30
**Verdict:** CHANGES NEEDED

> See the recent git history of this file to understand the context of this review.

---

## 1. Done Criteria Clarity

**PASS with exceptions.** Most tasks have concrete, verifiable done criteria — import checks, CLI help output, pytest passing. Two tasks are weak:

- **Task 7 (review-pr-core.md):** "Prompt covers all 7 steps from architecture" is subjective. Two implementers could produce very different prompts and both claim they "covered" the steps. Done criteria should specify measurable outputs — e.g., "prompt contains VCS-conditional blocks for both ADO and GitHub", "includes findings.json schema reference", "includes the 40-tool-call constraint verbatim".

- **Task 9 (project instructions + smoke test):** "dry-run produces structured JSON output" is good, but the task says "or with mock data if no live PR available" — this introduces ambiguity about what counts as a passing smoke test. If there's no live PR, the smoke test validates Docker-builds-and-runs but not agent-produces-findings. The done criteria should split these: Docker build passes (hard gate), and dry-run with sample findings.json passes (hard gate), and live PR test is a stretch goal.

All other tasks have clear, mechanically verifiable done criteria (import checks, `--help` output, pytest). **PASS.**

---

## 2. Cohesion and Coupling

**FAIL.** Task 2 is overloaded. It ports models, config, AND all 8 activity files in a single task. That's 10+ files requiring adaptation, including two non-trivial changes (cr-id extraction in fetch_pr_comments, cr-id injection in post_pr_comment). This is low cohesion — config adaptation and activity porting are separate concerns with different risk profiles.

**Recommendation:** Split Task 2 into:
- Task 2a: Port models + config (the data layer — 2 files, medium risk due to config stripping)
- Task 2b: Port activities (the VCS layer — 8 files, medium risk due to import path changes + cr-id additions)

This also improves checkpoint granularity — if config porting breaks, you don't lose progress on activities.

Coupling between tasks is generally low and well-managed. Tasks share interfaces (Finding model, Settings) that are established in Phase 1. **Inter-task coupling is PASS.**

---

## 3. Key Abstractions Early

**PASS.** Phase 1 establishes all shared abstractions: `Finding`/`FindingsFile` models, `Settings` config, activity base class, scorer. Phase 2 and later build on these without redefining them. The `findings-schema.json` (Task 5) serves as the contract between Phase 1 (agent) and Phase 2 (poster), and it's placed right where post_findings.py needs it.

---

## 4. Riskiest Assumption Validated Early

**PASS.** The plan correctly front-loads the three highest risks:
- **R3 (post_findings.py complexity):** Front-loaded to Phase 2, Task 5, with premium tier and extensive testing in Task 6.
- **R1 (Codex sandbox + Docker):** Tested in Phase 3 smoke test with documented fallback.
- **R7 (Import path breakage):** Validated in Phase 1 VERIFY with `python -c` import checks.

The riskiest *assumption* is that the two-phase architecture works end-to-end — agent writes valid findings.json, poster reads and posts correctly. This is validated by Phase 3's smoke test, which is early enough to course-correct.

---

## 5. DRY / Reuse of Early Abstractions

**PASS.** `Finding` model is defined once (Task 2) and used by scorer (Task 3), post_findings (Task 5), fix verification (Task 13), and GitHub path (Task 15). `PRScorer` is defined once (Task 3) and used by post_findings. Activities are imported directly by both vcs.py and post_findings.py. No duplication spotted.

---

## 6. Phase Structure (2-3 work tasks + VERIFY)

**PASS.** Every phase follows the pattern:
- Phase 1: 3 work + VERIFY
- Phase 2: 3 work + VERIFY
- Phase 3: 3 work + VERIFY
- Phase 4: 3 work + VERIFY
- Phase 5: 2 work + VERIFY
- Phase 6: 2 work + VERIFY

All VERIFY checkpoints include running the full test suite AND checking for regressions. Phase 3 VERIFY adds a Docker-specific check (Codex sandbox compatibility), which is appropriate.

---

## 7. Single-Session Completability

**FAIL.** Task 2 (port models + config + 8 activities) is too large for a reliable single session. Each activity file requires reading the old source, adapting imports, and in two cases (fetch_pr_comments, post_pr_comment) adding new functionality (cr-id extraction/injection). That's 10+ files with non-trivial adaptation. Even at "standard" tier, this is a session-and-a-half task.

Task 5 (post_findings.py, ~350 lines, premium tier) is appropriately sized for a premium session.

Task 8 (Dockerfile + entrypoint.sh + docker-compose, 3 files, ~110 lines total) is fine — these are mostly boilerplate with known patterns.

**Recommendation:** Split Task 2 as described in section 2. Each sub-task becomes a clean single-session unit.

---

## 8. Dependencies Satisfied in Order

**FAIL.** One hidden dependency:

- **Task 15 (GitHub path in post_findings.py)** declares dependency on "Phase 2 (post_findings base)" only. But Phase 5 (Task 13) adds fix verification logic to post_findings.py, including thread resolution. Task 15 must extend the GitHub path to handle fix verification (reply + minimize via GraphQL). If Task 15 is implemented against the Phase 2 version of post_findings.py, it will miss the fix verification code path and either break or produce incomplete GitHub support.

The sequential phase ordering means this works in practice (Phase 5 runs before Phase 6), but the declared dependency is wrong. A developer looking at Task 15's blockers would think they only need Phase 2 done. **Fix:** Change Task 15's blockers to "Phase 5 (fix verification in post_findings)".

All other declared dependencies are correct and satisfied in order.

---

## 9. Vague Tasks

**FAIL.** Two tasks would be interpreted differently by different developers:

- **Task 7 (review-pr-core.md):** "Write ~200 lines" of agent instructions. The done criteria say "covers all 7 steps from architecture" but the architecture doc (IMPLEMENTATION-PLAN.md Step 3.1) lists high-level bullets, not specific instructions. Two developers would write very different prompts. The task should include a skeleton outline or at minimum specify the required sections and constraints that must appear verbatim (e.g., "must include the 40 tool call cap", "must include VCS-conditional blocks", "must reference findings-schema.json by path").

- **Task 9 (project instructions + smoke test):** "AGENTS.md" and "CLAUDE.md" content is not specified. What project-level instructions should they contain? The done criteria say "contain correct project-level instructions" — but correct relative to what? This task needs a brief spec of what these files should tell the agent.

---

## 10. Hidden Dependencies

**FAIL.** Beyond the Task 15 dependency issue (section 8), there are two gaps:

1. **`utils/markdown_formatter.py`:** Listed in requirements.md porting table as "Reuse for summary formatting in post_findings.py" but absent from every Sprint 1 task. If post_findings.py needs it for summary formatting, it should be ported in Phase 1 (Task 2) or Phase 2 (Task 5). If it's not needed, remove it from the requirements porting table.

2. **`utils/comment_exporter.py`:** Listed as "Low priority, port later" in requirements but not in the DEFERRED section of PLAN.md. It's in limbo — neither scheduled nor explicitly deferred.

3. **`.codereview.yml` parsing in post_findings.py:** Requirements (Key Design Decision #6) say post_findings.py reads `.codereview.yml` for gate thresholds. This parsing is not mentioned in any task's change description. Task 11 creates the template `.codereview.yml` but doesn't mention adding the parsing logic to post_findings.py. Where does this get implemented?

---

## 11. Risk Register

**PASS with gaps.** The risk register covers the major technical risks (Codex sandbox, prompt quality, cr-id stability, SDK compat, tool call cap, import breakage, gh CLI parsing). Three risks are missing:

- **GitHub API rate limiting:** `post_findings.py` makes multiple `gh api` calls per finding (post comment, read threads, reply, minimize). A PR with 30 findings could hit GitHub's secondary rate limit (especially the GraphQL minimize calls). Severity: Medium. Mitigation: batch where possible, add retry-with-backoff.

- **Docker image size:** The image includes Node 22, Python, Codex CLI, gh CLI, ripgrep, repomix, azure-devops SDK. This could easily be 2GB+, making CI pulls slow and defeating the purpose of pre-building. Severity: Medium. Mitigation: multi-stage build, track image size in CI.

- **Schema drift between agent output and post_findings.py:** The agent writes findings.json based on the prompt instructions, but there's no runtime validation that the agent's output matches findings-schema.json. A small agent hallucination (wrong field name, missing required field) could cause post_findings.py to crash silently or skip findings. Severity: Medium. Mitigation: validate against schema in post_findings.py (actually mentioned in Step 2.3 of IMPLEMENTATION-PLAN.md but not in the risk register).

**Recommendation:** Add these three risks to the register.

---

## 12. Alignment with Requirements Intent

**PASS.** The plan solves the right problem — porting a monolithic AI code reviewer into a two-phase Docker-based architecture that's agent-agnostic. Key alignment points:

- Requirements ask for two-phase architecture (agent writes, Python posts) — plan delivers this as the central design.
- Requirements ask for idempotent cr-id dedup — plan addresses this in Tasks 2 (extraction/injection), 5 (dedup logic), and throughout.
- Requirements ask for 6 review modes — Sprint 1 delivers 3 (standard, security, migration), remaining 2 deferred to Phase 7. `docs/chore` mode is not mentioned in either Sprint 1 or the DEFERRED section. **NOTE:** Is docs/chore a mode or a detection result that skips deep review? This needs clarification.
- Requirements ask for both ADO and GitHub — plan delivers both in Sprint 1 (ADO in Phase 2, GitHub in Phase 6).
- Requirements ask for penalty-based scoring — plan ports existing scorer in Task 3.
- Requirements ask for local CLI — correctly deferred to Phase 11 (Sprint 2+).
- Requirements ask for Docker + CI — delivered in Phases 3 and 6.

The plan is not just technically clean — it solves what the requirements asked for and in the right priority order.

---

## Tier Assignments

**PASS.** The tier assignments are reasonable:
- **Cheap** (Tasks 1, 12, 16): Scaffold, intent markers, CI YAML — all mechanical work with clear specs. Correct.
- **Standard** (Tasks 2, 3, 4, 6, 8, 9, 10, 11, 13, 14, 15): Clear-spec work with moderate complexity. Correct.
- **Premium** (Tasks 5, 7): post_findings.py (~350 lines, highest complexity, correctness-critical dedup) and review-pr-core.md (THE PRODUCT, quality-determines-everything). These are the right two premium tasks.

---

## Sprint 1 E2E Product

**PASS.** After Phase 6, Sprint 1 delivers:
- Docker container that reviews PRs via Codex agent
- Deterministic scoring + dedup + posting for both ADO and GitHub
- 3 review modes (standard, security, migration) with auto-detection
- Fix verification on re-push with score comparison
- CI pipelines for both ADO and GitHub
- `--dry-run` for safe testing

This is a working E2E product. The deferred items (architecture/performance modes, multi-agent, local CLI, PyPI) are genuinely additive — Sprint 1 stands alone.

---

## Deferred Section Completeness

**FAIL.** Three items are missing from the DEFERRED section:

1. **`docs/chore` review mode:** Requirements list 6 modes including "docs/chore". The plan implements 3 in Sprint 1 (standard, security, migration) and defers 2 in Phase 7 (architecture, performance). `docs/chore` is not in Sprint 1 tasks or in the DEFERRED section. It's dropped entirely.

2. **`utils/comment_exporter.py`:** Requirements say "Low priority, port later" but it's not in DEFERRED.

3. **`utils/markdown_formatter.py`:** Requirements say "Reuse for summary formatting" — if it's needed by post_findings.py in Sprint 1, it should be in a Sprint 1 task. If not needed until later, it should be in DEFERRED. Currently it's in neither.

4. **README.md:** Listed in Phase 10 (deferred) of IMPLEMENTATION-PLAN.md but not in PLAN.md's DEFERRED section. Minor omission.

---

## Summary

**Verdict: CHANGES NEEDED**

### Must fix before implementation begins:

1. **Split Task 2** into models+config and activities — it's too large for a single session and has low cohesion (sections 2, 7)
2. **Fix Task 15's declared dependency** — should be Phase 5, not Phase 2 (section 8)
3. **Account for `markdown_formatter.py`** — either add to a Sprint 1 task or explicitly defer (section 10)
4. **Add `.codereview.yml` parsing** to a specific task's change description (section 10)
5. **Add `docs/chore` mode** to either Sprint 1 or DEFERRED section (section 12, deferred completeness)
6. **Add missing items to DEFERRED section** — comment_exporter.py, README.md (deferred completeness)

### Should fix (quality improvements):

7. Strengthen Task 7 done criteria with specific required sections/constraints (section 9)
8. Strengthen Task 9 done criteria — split Docker build gate from live PR test (section 1)
9. Add three missing risks to register: GitHub rate limiting, Docker image size, schema drift (section 11)
10. Clarify Task 9's AGENTS.md/CLAUDE.md content expectations (section 9)

### What passed:

- Key abstractions established in Phase 1 (section 3)
- Riskiest work front-loaded correctly (section 4)
- Phase structure is consistent and well-checkpointed (section 6)
- Tier assignments are reasonable (tier section)
- Sprint 1 delivers a genuine E2E product (E2E section)
- Plan aligns with requirements intent (section 12)
- DRY / reuse patterns are clean (section 5)
