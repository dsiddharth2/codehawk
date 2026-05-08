# Review Modes — Requirements

## Base Branch
`main`

## Goal
Add two mutually exclusive CLI flags (`--verify-fixes` and `--check-new-findings`) to CodeHawk's review pipeline so users can control whether a review run verifies prior findings, looks for new issues, or does both (current default).

## Scope
- New `ReviewMode` enum (`full`, `verify_fixes`, `check_new`) in `src/models/review_models.py`
- CLI flags `--verify-fixes` and `--check-new-findings` in `src/run_agent.py` (mutually exclusive via argparse)
- Thread `review_mode` through `BatchReviewJob` and `ReviewJobConfig` → `ReviewJob`
- Conditional behavior:
  - `--verify-fixes`: skip new finding generation, only verify prior cr_id findings, write `fix_verifications[]`, empty `findings[]`
  - `--check-new-findings`: skip `_fetch_previous_findings()`, no fix verification, fresh review only
  - Default (no flag): current behavior unchanged (verify + find new)
- Agent prompt modifications: mode-specific instruction blocks appended dynamically in `_build_prompt()`
- Defense-in-depth: `_write_findings()` strips findings/fix_verifications that shouldn't be present for the active mode
- `post_findings.py` adjustments: handle verify-only mode (no findings to score/gate, fix verifications still processed, summary title changes)
- Unit tests for all new behavior

## Out of Scope
- New review mode prompts in `commands/review-pr-core.md` — dynamic prompt injection is sufficient
- Integration tests with real OpenAI API calls
- Batch optimization for verify-only mode (only batching files with prior findings) — future optimization

## Constraints
- All 190 existing tests must continue to pass
- Default behavior (no flags) must be identical to current behavior — zero regression
- Both members are Claude on Windows
- Python 3.11+, no new dependencies

## Key Files
- `src/models/review_models.py` — data models, add ReviewMode enum
- `src/review_job.py` — ReviewJobConfig, ReviewJob, prompt building, _write_findings()
- `src/batch_review_job.py` — BatchReviewJob orchestrator, _fetch_previous_findings()
- `src/run_agent.py` — CLI entry point, argparse
- `src/post_findings.py` — Phase 2 scoring/gating/summary
- `src/config.py` — Settings (no changes expected)
- `tests/unit/test_review_job.py` — existing ReviewJob tests
- `tests/unit/test_batch_review.py` — existing batch tests
- `tests/unit/test_post_findings.py` — existing post_findings tests

## Acceptance Criteria
- [ ] `python run_agent.py --help` shows both new flags with descriptions
- [ ] `--verify-fixes` and `--check-new-findings` are mutually exclusive (argparse error when both provided)
- [ ] `--verify-fixes` produces findings.json with empty `findings[]` and populated `fix_verifications[]`
- [ ] `--check-new-findings` produces findings.json with empty `fix_verifications[]` and populated `findings[]`
- [ ] Default mode (no flag) behavior is identical to current
- [ ] Summary markdown title changes for verify-only mode
- [ ] All 190 existing tests pass
- [ ] New unit tests cover: enum, CLI parsing, conditional fetch, prompt injection, write guards, post_findings verify-only path
