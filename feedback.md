# Code Reviewer v3.1 — Phase 4 Code Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-04-22 16:30:00+05:30
**Verdict:** CHANGES NEEDED

> See the recent git history of this file to understand the context of this review. Phase 4 (Tasks 14–16) delivered review mode prompts, starter templates, and the entrypoint.sh fix from Phase 3 Finding 4.1. Phase 3 was approved in commit 419e3e9. This review covers commits ee202be through fe3bf38.

---

## 1. review-mode-standard.md — Standard Mode Checklist

**Status: PASS**

Verified against PLAN.md Task 14 "done when" criteria:

- **Complete checklist:** 6 sections (Correctness, Code Patterns, Test Coverage, Naming and API Design, Error Handling, Edge Cases) with 22 actionable items. Each item is specific enough for an LLM agent to evaluate — describes the pattern to look for, not just a category name. PASS.
- **Severity multipliers:** Correctly states "None (1x)" — standard mode uses base penalty matrix. PASS.
- **Quality bar section:** Instructs agent to respect `# cr: intentional` markers and `.codereview.md` conventions. Good — prevents noise. PASS.
- **No duplication with other modes:** Standard checklist focuses on general correctness; security/migration items are delegated to their respective mode files. PASS.

---

## 2. review-mode-security.md — Security Mode Checklist

**Status: PASS**

- **OWASP coverage:** 7 sections covering A01 (Auth/Access Control), A02 (Secrets, Crypto), A03 (Injection), A05 (Insecure Defaults), A06 (Dependency CVEs), A07 (Authentication/Session). 26 checklist items total. PASS.
- **Severity multiplier:** States "warning → critical (x2 penalty)" — matches `apply_mode_multipliers` in `pr_scorer.py`. PASS.
- **Additive behavior:** States "This mode is additive — standard checklist still applies." PASS — agent won't skip general correctness checks in security mode.
- **Confidence guidance:** Instructs to flag at 0.7+ when blast radius is high, and not to flag unreachable theoretical vulnerabilities. Good calibration. PASS.
- **Specificity:** Each item names the concrete pattern to grep for (e.g., `subprocess`, `os.system`, `random.random()`, `verify=False`). LLM-actionable. PASS.

---

## 3. review-mode-migration.md — Migration Mode Checklist

**Status: PASS**

- **Data loss risk:** 5 items covering DROP, column narrowing, NOT NULL, default changes, cascades. PASS.
- **Rollback safety:** 4 items covering down migration existence, safety, downtime, and round-trip. PASS.
- **Lock duration:** 4 items — ALTER TABLE locks, CONCURRENTLY for indexes, batching for backfills, duration estimate for large tables. PASS.
- **Idempotency:** 3 items — IF NOT EXISTS guards, sequence resets. PASS.
- **Zero-downtime compatibility:** 4 items — additive-only, old code compat, column rename dual-write, FK on existing data. PASS.
- **Data integrity:** 3 items — unique constraints on existing data, check constraints, expression indexes. PASS.
- **Severity multiplier:** States "All findings elevated to critical" — matches `apply_mode_multipliers` in `pr_scorer.py` (migration elevates all severities to critical). PASS.
- **Confidence guidance:** Flags at 0.75+ with rationale for accepting false positives. Good. PASS.

---

## 4. review-mode-docs-chore.md — Docs/Chore Mode Checklist

**Status: PASS**

- **Light-touch behavior:** Line 7 states "Max 10 findings (not 30). Skip deep code analysis entirely." Line 13 says "Do not analyze code logic, performance, or security unless a config file contains an obviously dangerous value." PASS — this directly answers review criterion #2.
- **Detection rule:** Line 3 specifies "All changed files have extensions `.md`, `.yml`, `.yaml`, `.json`, `.txt`, `.rst` — AND no `.py`, `.js`, `.ts`, `.cs`, `.java` files." Matches `review-pr-core.md` Step 3 table exactly. PASS.
- **Checklist sections:** Documentation Accuracy (5 items), Changelog Completeness (3 items), Config File Correctness (5 items), Formatting and Consistency (4 items). 17 items total. PASS.
- **"What to Skip" section:** Explicitly excludes code style in snippets, missing tests, performance/security analysis, writing style opinions. Good — prevents an LLM agent from scope-creeping into code analysis on a docs PR. PASS.
- **No severity multiplier:** Correct — docs/chore findings stay at base severity. PASS.

---

## 5. review-pr-core.md Step 3 — Mode Detection Rules

**Status: PASS**

All 6 modes present in the detection table (`review-pr-core.md:107-114`):

| Mode | Present | File signal | Label signal |
|------|---------|-------------|-------------|
| `migration` | Yes | `**/migrations/**`, `*.sql`, `**/alembic/**` | `migration`, `db-change` |
| `security` | Yes | `**/auth/**`, `**/crypto/**`, `**/permissions/**` | `security` |
| `architecture` | Yes | `**/api/**`, `**/interfaces/**`, `**/contracts/**`, >10 files | `architecture` |
| `performance` | Yes | `**/queries/**`, `**/cache/**`, `**/indexes/**` | `performance` |
| `docs_chore` | Yes | All files `.md/.yml/.yaml/.json/.txt/.rst` only | `docs`, `chore` |
| `standard` | Yes | Default fallback | — |

Line 116 correctly states "Multiple modes may be active" — so a PR touching auth AND migrations gets `["security", "migration"]`. PASS.

Line 118 restates the docs_chore light-touch constraint (max 10 findings). PASS.

Line 120 requires `review_modes` in findings.json to be at least `["standard"]`. PASS.

---

## 6. templates/.codereview.yml — Gate Thresholds

**Status: PASS**

- `min_star_rating: 3` — reasonable default (allows warnings, blocks criticals). PASS.
- `fail_on_critical: true` — sensible default. PASS.
- Commented optional fields: `max_findings: 30`, `max_per_file: 5`, `min_confidence: 0.7` — all match the agent's internal defaults. Good documentation. PASS.
- Comments explain what each threshold means. PASS.

---

## 7. templates/.codereview.md — Conventions Template

**Status: PASS**

5 sections with HTML comment placeholders showing examples: Languages/Frameworks, Named Anti-Patterns, Focus Areas, Paths to Always Review Carefully, Paths to Skip. Each section has 3 concrete examples inside comments. Users can uncomment/modify. Well-structured starter template. PASS.

---

## 8. templates/dismissed.jsonl — Empty Dismissed File

**Status: PASS**

Empty file (0 lines). This is the expected initial state — findings get appended as JSONL when users dismiss them. PASS.

---

## 9. entrypoint.sh Fix — Phase 3 Finding 4.1

**Status: FAIL — Must fix**

The Phase 3 review (Finding 4.1) identified that `entrypoint.sh` doesn't copy `PROJECT-CLAUDE.md` for the Claude agent. The suggested fix was:

```bash
if [[ "$AGENT" == "claude" && -f "/app/PROJECT-CLAUDE.md" && ! -f "/workspace/CLAUDE.md" ]]; then
    cp /app/PROJECT-CLAUDE.md /workspace/CLAUDE.md
fi
```

The actual fix at `entrypoint.sh:48-50` is:

```bash
if [[ "$AGENT" == "claude" && -f "/app/PROJECT-CLAUDE.md" && ! -f "/workspace/PROJECT-CLAUDE.md" ]]; then
    cp /app/PROJECT-CLAUDE.md /workspace/PROJECT-CLAUDE.md
fi
```

**Problem:** The file is copied as `/workspace/PROJECT-CLAUDE.md`, but Claude Code discovers project instructions from `CLAUDE.md` (not `PROJECT-CLAUDE.md`). The whole point of Finding 4.1 was that the Claude agent won't see codehawk's instructions unless they land at `/workspace/CLAUDE.md`. The current fix copies the file but with the wrong destination name, so Claude still won't read it.

**Required fix:** Change the destination to `/workspace/CLAUDE.md` and update the existence check accordingly:

```bash
if [[ "$AGENT" == "claude" && -f "/app/PROJECT-CLAUDE.md" && ! -f "/workspace/CLAUDE.md" ]]; then
    cp /app/PROJECT-CLAUDE.md /workspace/CLAUDE.md
fi
```

---

## 10. Test Suite — No Regressions

**Status: PASS**

```
PYTHONPATH=src pytest tests/ -v → 66 passed in 0.40s
```

All 66 tests from Phase 2 continue to pass. Phase 4 deliverables are markdown and template files — no new Python code, so no new tests expected. No regressions. PASS.

---

## 11. Cross-Mode Consistency

**Status: PASS**

All four mode files follow a consistent structure:
1. **Header:** Mode name, "Applies when" trigger, severity multipliers
2. **Separator line**
3. **Checklist:** Markdown checkbox items grouped by subsection
4. **Confidence Guidance** (security and migration) or **Quality Bar** (standard) or **What to Skip** (docs/chore)

Severity multiplier documentation is consistent across modes and matches `pr_scorer.py`:
- Standard: none (1x) — correct
- Security: warning→critical — matches `apply_mode_multipliers`
- Migration: all→critical — matches `apply_mode_multipliers`
- Docs/chore: none — correct (no multiplier in scorer)

No duplicated checklist items across modes. Each mode targets a distinct concern. PASS.

---

## 12. Consistency with PLAN.md and requirements.md

**Status: PASS with one exception (Finding 9)**

- Task 14 "done when": Each mode file has a complete checklist; files follow consistent format; docs/chore mode specifies light-touch behavior. **PASS.**
- Task 15 "done when": Prompt has detection rules for all 6 modes; post_findings.py passes review_modes to scorer (verified in Phase 2); templates exist with reasonable defaults. **PASS.**
- Task 16 "done when": Prompt documents all three marker types with examples; agent instructions clearly state to skip marked code. **PASS** (was done in Phase 3, verified present).
- Entrypoint fix from Phase 3 Finding 4.1: **FAIL** — wrong destination filename (see Finding 9 above).

---

## Summary

Phase 4 deliverables are high quality. All four review mode files have complete, actionable checklists that an LLM agent can follow. The docs/chore mode correctly specifies light-touch behavior with a 10-finding cap. Templates provide sensible defaults. Mode detection in review-pr-core.md Step 3 covers all 6 modes. Cross-mode consistency is excellent — no duplication, clear severity guidance, consistent structure.

**1 must-fix finding:**
- **Finding 9:** `entrypoint.sh:48-50` copies `PROJECT-CLAUDE.md` to `/workspace/PROJECT-CLAUDE.md` instead of `/workspace/CLAUDE.md`. Claude Code reads `CLAUDE.md`, not `PROJECT-CLAUDE.md`. The fix addresses Phase 3 Finding 4.1 but uses the wrong destination filename, so the Claude agent still won't receive codehawk project instructions.

**0 LOW findings.**

All tests pass (66/66). No regressions from prior phases.

**Phase 4 verdict: CHANGES NEEDED.** Fix the entrypoint.sh destination filename, then request re-review.
