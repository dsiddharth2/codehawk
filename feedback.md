# Sprint 3 — Review Quality + Coverage Enforcement — Code Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-05-22 21:45:00+05:30
**Verdict:** CHANGES NEEDED

> See the recent git history of this file to understand the context of this review.

---

## Phase 1 Regression Check

**PASS.** Phase 1 (Tasks 1-6, approved in commit 63ebc04) was re-verified:
- `temperature=0.3` present in both API call sites (`openai_runner.py:199`, `openai_runner.py:332`)
- `seed=42` present in `_run_chat_completions` only (correct — Responses API doesn't support seed)
- "ZERO or minimal" absent from `SYSTEM_PROMPT`
- "max 10" absent from `review-pr-core.md`; "max 40" present in Step 0 constraints
- Step 5e verify-before-CRITICAL present
- `failed_diffs` injection recommends `read_local_file`/`get_file_content` only (no contradiction with line 374)
- No regressions detected from Phase 2 changes.

---

## Task 7: Risk Classifier

**PASS.** `src/risk_classifier.py` implements the exact formula from PLAN.md with correct weights (0.25/0.25/0.15/0.15/0.10/0.10). Path sensitivity patterns match the plan (HIGH: auth/, crypto/, etc.; MEDIUM: services/, models/, etc.; LOW: tests/, docs/, etc.). Thresholds are configurable via function args and wired to `config.py` at the call site in `review_job.py:350-355`. Graceful degradation confirmed — when `graph_analysis` is None, `caller_count` and `no_test_coverage` default to 0.0. The `FileRisk` dataclass returns `risk`, `score`, and `reasons` as specified.

Done criteria: `risk_classifier.classify(files)` returns HIGH/MEDIUM/LOW for a mock file list — **met** (10 unit tests verify this). Thresholds configurable in `config.py` — **met** (`risk_high_threshold=0.6`, `risk_medium_threshold=0.3` at `config.py:143-154`).

---

## Task 8: Inject Risk Table into Prompt

**PASS.** `review_job.py:346-380` calls `risk_classifier.classify()` inside `_build_review_context` and injects a markdown table with the exact headers from the plan: `| File | Risk | Depth | Reason |`. The depth column maps correctly (HIGH → "Full review + verify", MEDIUM → "Diff review + read if needed", LOW → "Diff scan"). The table footer includes the mandatory instruction: "You MUST review every file above."

Done criteria: Risk classification table injected into review context prompt — **met**.

---

## Task 9: Risk-Based Depth Instructions in Prompt

**PASS.** `review-pr-core.md` Step 4, lines 106-115 contain the 100% coverage requirement and all three risk-tier depth instructions (HIGH/MEDIUM/LOW) matching the plan text verbatim. The budget instruction ("Budget your 40 turns wisely") and the T1-T5 + risk tier coexistence note are both present.

Done criteria: Risk-based depth instructions and 100% coverage requirement in Step 4 — **met**.

---

## Task 10: Add `files_clean[]` to Findings Schema

**PARTIAL PASS — 1 FAIL item.**

What works:
- `review_models.py:245`: `files_clean: List[str] = field(default_factory=list)` — model field exists. **PASS.**
- `review-pr-core.md:314`: Example JSON includes `"files_clean": [...]`. **PASS.**
- `review-pr-core.md:319`: Instruction text includes `files_clean` requirement. **PASS.**
- `post_findings.py:228`: `files_clean=data.get("files_clean", [])` — parsing works. **PASS.**

**FAIL — `commands/findings-schema.json` not updated.** The JSON schema file has `"additionalProperties": false` (line 8) but does not define a `files_clean` property. When the agent writes `files_clean` into `findings.json` (as now instructed by the prompt), schema validation in `post_findings.py:839` (`_validate_schema`) will reject it and raise `SystemExit(1)`. This is a production-blocking bug — the pipeline will crash every time the agent follows the prompt instructions.

**Fix:** Add `files_clean` to `findings-schema.json` properties:
```json
"files_clean": {
  "type": "array",
  "items": { "type": "string" },
  "description": "Files reviewed with no findings — every code file must appear in either findings[].file or files_clean[]"
}
```

---

## Task 11: Coverage Gate and Penalty

**PASS.** All five sub-changes verified:

1. **Coverage calculation** (`post_findings.py:943-964`): Uses `files_with_findings | files_clean_set` as `files_reviewed_set`, divides by `total_code_files`. Backward-compatible: only fires coverage gate when `files_clean` key is present in the raw JSON (`agent_uses_coverage_tracking` flag at line 848). **PASS.**

2. **Gate rule** (`post_findings.py:797-809`): `coverage_gate_mode="hard"` fails gate with correct message format. `"log"` mode appends a warning but does not fail. Both tested and verified. **PASS.**

3. **Summary display** (`post_findings.py:534-541`, used at line 640): Shows `"X / Y (Z%)"` format for 100%, and `"X / Y (Z%) — N file(s) not reviewed"` for partial coverage. **PASS.**

4. **Coverage penalty** (`pr_scorer.py:262-280`): `apply_coverage_penalty` adds `(1 - coverage_ratio) * 50` points. Full coverage returns unchanged score. Zero coverage adds exactly 50. Tests verify 0%, 50%, 62.5%, 100%. **PASS.**

5. **Config fields** (`config.py:137-154`): `coverage_gate_mode: str = "hard"`, `risk_high_threshold: float = 0.6`, `risk_medium_threshold: float = 0.3` — all present with correct defaults and descriptions. **PASS.**

Done criteria: All sub-items met.

---

## Task 12: Align Batch Turn Budget to 40

**PASS.** `config.py:126`: `batch_max_turns` default is `40` (was 15). `review-pr-core.md` references 40 turns consistently (Step 0: "max 40 tool calls", Step 4: "Budget your 40 turns wisely"). Grep of the entire codebase for turn/tool-call defaults confirms all production values are 40. Integration test cap of 15 (`tests/integration/conftest.py:30`) is correct — that's a test-only limit, not production.

Done criteria: `batch_max_turns` default is 40; all codebase turn limits are 40 — **met**.

---

## Task 13 VERIFY: Test Suite

**PASS.** 249 tests passed, 13 skipped, 0 failures in 3.07s. The 28 new Phase 2 tests in `tests/unit/test_coverage_system.py` cover:
- Risk classifier (10 tests): HIGH/MEDIUM/LOW classification, graph analysis, graceful degradation, configurable thresholds, string paths
- files_clean parsing (3 tests): correct parsing, default to empty, model field existence
- Coverage calculation (7 tests): 100% and 62.5% ratios, hard/log gate modes, display formatting
- Coverage penalty (4 tests): 0%, 50%, 62.5%, 100% coverage scenarios
- Config defaults (4 tests): batch_max_turns=40, coverage_gate_mode="hard", risk thresholds

Test quality is good — meaningful coverage of happy paths, edge cases, and the specific PLAN verification scenarios (5 findings + 3 clean = 100%, 5 findings + 0 clean = 62.5%). No redundant tests detected.

**NOTE:** The schema validation gap (findings-schema.json missing `files_clean`) is not caught by the test suite because `test_files_clean_parsed_correctly` calls `_parse_findings_file` directly without running `_validate_schema` first. A test that runs the full `run_post_findings` path with `files_clean` in the input would have caught this.

---

## Summary

**6 of 7 tasks pass.** Task 10 has one production-blocking issue: `commands/findings-schema.json` does not include `files_clean` in its properties, but has `additionalProperties: false`. Since the prompt now instructs the agent to include `files_clean` in findings.json, schema validation will reject the output and crash the pipeline at `post_findings.py:839`.

**Must fix before approval:**
1. Add `files_clean` property to `commands/findings-schema.json`

**Recommended (not blocking):**
1. Add an end-to-end test that validates a findings.json containing `files_clean` against the schema to prevent similar schema/model drift.
