# Review Modes — Implementation Plan

> Add two mutually exclusive CLI flags (--verify-fixes and --check-new-findings) to control whether a review run verifies prior findings, looks for new issues, or does both (current default). Introduces a ReviewMode enum threaded through the pipeline, with mode-specific agent prompt injection and defense-in-depth guards in _write_findings().

---

## Tasks

### Phase 1: Foundation — Enum, Config, CLI

#### Task 1: Add ReviewMode enum and thread through config
- **Change:** (1) Add ReviewMode(str, Enum) to src/models/review_models.py with values FULL = "full", VERIFY_FIXES = "verify_fixes", CHECK_NEW = "check_new". (2) Add review_mode: ReviewMode = ReviewMode.FULL field to ReviewJobConfig in src/review_job.py. Import ReviewMode. (3) Add review_mode: ReviewMode = ReviewMode.FULL parameter to BatchReviewJob.__init__ in src/batch_review_job.py, store as self.review_mode. Import ReviewMode.
- **Files:** src/models/review_models.py, src/review_job.py, src/batch_review_job.py
- **Tier:** cheap
- **Done when:** from models.review_models import ReviewMode works; ReviewMode.FULL, .VERIFY_FIXES, .CHECK_NEW are valid; ReviewJobConfig(review_mode=ReviewMode.VERIFY_FIXES) accepted; BatchReviewJob accepts review_mode kwarg; all 190 existing tests pass.
- **Blockers:** None

#### Task 2: Add CLI flags and thread review_mode through BatchReviewJob
- **Change:** (1) In src/run_agent.py, add a mutually exclusive argparse group with --verify-fixes (store_true) and --check-new-findings (store_true). Resolve to ReviewMode enum and pass review_mode= to BatchReviewJob(...). (2) In BatchReviewJob.run(), conditionally skip _fetch_previous_findings() when review_mode == CHECK_NEW. Pass review_mode=self.review_mode to every ReviewJobConfig(...) constructor (both single-session path ~line 92 and batched path ~line 223).
- **Files:** src/run_agent.py, src/batch_review_job.py
- **Tier:** standard
- **Done when:** python src/run_agent.py --help shows both flags; passing both raises argparse error; --verify-fixes sets review_mode=VERIFY_FIXES; --check-new-findings sets review_mode=CHECK_NEW; default is FULL; BatchReviewJob.run() skips _fetch_previous_findings() for CHECK_NEW; all 190 existing tests pass.
- **Blockers:** Task 1

#### VERIFY: Phase 1 — Foundation
- Run pytest tests/ — all 190 existing tests pass
- Verify ReviewMode enum imports correctly
- Verify CLI flags appear in --help and are mutually exclusive
- Verify review_mode threads through BatchReviewJob to ReviewJobConfig
- Report: tests passing, any regressions

---

### Phase 2: Conditional Behavior — Prompt, Write Guards, Post-Findings

#### Task 3: ReviewJob mode-specific prompt injection and write guards
- **Change:** (1) In ReviewJob.create_findings() (~line 87), wrap the standalone prior-findings fetch in if self.config.review_mode != ReviewMode.CHECK_NEW. (2) In ReviewJob._build_prompt() (~line 422), after the existing previous_findings section, add mode-specific instruction blocks: for VERIFY_FIXES append verify-only instructions ("ONLY verify prior findings, findings[] MUST be empty, populate fix_verifications[] for every prior cr_id, skip Steps 3-5"); for CHECK_NEW append fresh-review instructions ("no prior findings, fix_verifications[] MUST be empty, skip Step 6"). Add two new private methods _build_verify_only_instructions() and _build_check_new_instructions(). (3) In ReviewJob._write_findings() (or wherever findings.json is written), add defense-in-depth guards: if VERIFY_FIXES, strip findings[] to empty and set review_modes to include "verify_fixes"; if CHECK_NEW, strip fix_verifications[] to empty and set review_modes to include "check_new".
- **Files:** src/review_job.py
- **Tier:** standard
- **Done when:** VERIFY_FIXES mode: prompt contains "VERIFY-ONLY MODE", _write_findings strips any agent-produced findings; CHECK_NEW mode: prompt contains "FRESH REVIEW MODE", prior-findings fetch skipped, _write_findings strips fix_verifications; FULL mode: no changes to current behavior; all 190 existing tests pass.
- **Blockers:** Tasks 1-2

#### Task 4: post_findings.py mode-aware summary and gating
- **Change:** (1) In post_findings.run() (~line 803), detect verify-only mode: is_verify_only = "verify_fixes" in findings_file.review_modes and not findings_file.findings. (2) When is_verify_only: skip confidence filtering/capping (no findings), skip posting inline comments, still handle fix verifications and generate score comparison. Gate passes (no new findings to fail on). (3) In _build_summary_markdown() (~line 533), when "verify_fixes" in review_modes, use title "AI Code Review — Fix Verification Only" instead of standard title. Suppress the "Findings" section when there are no findings.
- **Files:** src/post_findings.py
- **Tier:** standard
- **Done when:** verify-only mode with empty findings: no inline comments posted, fix verifications processed, summary title says "Fix Verification Only", gate passes; check-new mode: normal scoring/gating with no fix_verifications section; full mode unchanged; all 190 existing tests pass.
- **Blockers:** Task 3

#### VERIFY: Phase 2 — Conditional Behavior
- Run pytest tests/ — all 190 existing tests pass
- Verify prompt injection for each mode by inspecting _build_prompt() output
- Verify write guards strip correct fields per mode
- Verify post_findings summary title changes for verify-only
- Report: tests passing, any regressions

---

### Phase 3: Tests

#### Task 5: Unit tests for all new review mode behavior
- **Change:** Create tests/unit/test_review_modes.py with these test groups: Enum tests (ReviewMode values, string comparison, all three modes exist); CLI tests (--verify-fixes parsed, --check-new-findings parsed, both-together rejected, default is FULL); BatchReviewJob tests (CHECK_NEW skips _fetch_previous_findings(), VERIFY_FIXES and FULL call it, review_mode passed to ReviewJobConfig); Prompt tests (VERIFY_FIXES prompt contains "VERIFY-ONLY MODE", CHECK_NEW contains "FRESH REVIEW MODE", FULL contains neither); Write guard tests (VERIFY_FIXES strips findings[], CHECK_NEW strips fix_verifications[], FULL preserves both); post_findings tests (verify-only mode: no inline comments, fix verifications processed, summary title correct; gate passes with zero findings in verify-only).
- **Files:** tests/unit/test_review_modes.py (new)
- **Tier:** standard
- **Done when:** pytest tests/unit/test_review_modes.py -v passes; at least 15 test cases covering all groups above; all 190+ existing tests still pass.
- **Blockers:** Tasks 1-4

#### VERIFY: Phase 3 — Tests
- Run full pytest tests/ -v — all tests pass (old + new)
- Confirm test_review_modes.py covers enum, CLI, batch, prompt, write guards, post_findings
- Report: total test count, pass rate, any failures

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| Agent ignores verify-only prompt instructions, still produces findings | Med | Defense-in-depth: _write_findings() strips findings regardless of agent output |
| --verify-fixes with no prior findings (first push) produces empty output | Low | Log warning "no prior findings to verify", produce valid empty findings.json with note in summary |
| 5-star score in verify-only mode is misleading | Low | Summary title makes mode explicit; comparison markdown shows fix delta |
| review_modes list gets verify_fixes/check_new values that PRScorer.apply_mode_multipliers() doesn't recognize | Low | Scorer ignores unrecognized modes — no side effects |
| Default behavior regression — existing pipeline breaks | High | All 190 existing tests must pass; FULL mode code paths are untouched |

## Notes
- Each task should result in a git commit
- Verify tasks are checkpoints — stop and report after each one
- Base branch: main
- Branch: feat/review-modes
