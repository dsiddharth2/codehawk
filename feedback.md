# Graph Integration — Final Code Review (Phase 3 + Cumulative)

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-04-28 22:15:00+05:30
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review.
> Prior reviews: Phase 1 APPROVED (979b887), Phase 2 APPROVED after re-review (77144d6).

---

## 1. Prompt Update — `commands/review-pr-core.md` (Task 9)

### 1a. Step 2b — Change Impact Analysis — PASS

Lines 101-117: Step 2b is inserted between Step 2 (PR data fetch) and Step 3 (mode detection). This is the correct placement — the agent needs the changed file list from Step 2 before it can call `get_change_analysis`.

Content matches PLAN.md specification:
- Calls `get_change_analysis(changed_files=[...])` with file paths from Step 2
- Documents the three return fields: `risk_score`, `review_priorities`, `test_gaps`
- T1-T2 skip guidance is sensible — small PRs don't need prioritization
- T3+ guidance directs focus to high-risk files and test gaps
- Fallback instruction ("continue to Step 3 normally") is present

### 1b. Step 5c — Graph-First Caller Lookups — PASS

Lines 189-201: Step 5c now shows graph tools as the preferred path with ripgrep as fallback.

- `get_callers(function_name=..., file_path=...)` example is correct and matches the current schema (no `max_depth` — consistent with the Phase 2 fix in da5981f)
- `get_blast_radius(changed_files=[...])` suggested for broader impact view — correct
- Fallback `rg` command preserved for when graph tools are unavailable
- Clear "Preferred" vs "Fallback" labeling — the agent can distinguish which path to take

### 1c. Factual Accuracy — PASS

- Tool names match exactly: `get_change_analysis`, `get_callers`, `get_blast_radius` — verified against `src/tools/graph_tools.py`
- Parameter names match current schemas: `changed_files`, `function_name`, `file_path`
- No references to removed `max_depth` parameter — good

---

## 2. Unit Tests — `tests/unit/test_graph_builder.py` (Task 10, Part 1)

### 2a. Test Coverage vs PLAN.md — PASS

PLAN.md specifies 5 tests for `TestBuildGraph`. All 5 are present:

| Plan requirement | Test method | Status |
|-----------------|------------|--------|
| `test_returns_none_when_package_not_installed` | Line 49 | PASS |
| `test_returns_none_when_build_raises_exception` | Line 66 | PASS |
| `test_returns_store_on_success` | Line 75 | PASS |
| `test_returns_none_when_enable_graph_is_false` | Line 42 | PASS |
| `test_prints_diagnostic_on_failure` | Line 89 | PASS |

### 2b. Mocking Strategy — PASS

The `_crg_sys_modules()` helper (lines 23-38) patches `sys.modules` with a mock hierarchy for `code_review_graph` and its sub-packages (`tools.build`, `graph`, `incremental`). This is the correct approach for mocking a package that uses lazy imports inside a function body — `mocker.patch` on module-level names wouldn't work since the imports happen at call time inside `_build()`.

The `test_returns_none_when_package_not_installed` test (lines 49-64) uses `patch.dict(sys.modules, cleaned, clear=True)` and then explicitly pops `code_review_graph` entries. This correctly simulates the package being absent. The `clear=True` followed by selective pops is slightly heavy-handed but functional — it ensures no stale module references leak through.

### 2c. Test Quality — PASS

- `test_returns_store_on_success` asserts identity (`is mock_store`), not just truthiness — correct
- `test_prints_diagnostic_on_failure` uses `capsys` to capture stdout and checks for the diagnostic message — matches the `print()` convention in `graph_builder.py`
- No redundant tests — each covers a distinct code path (disabled, ImportError, RuntimeError, success, diagnostic output)

### 2d. Missing Coverage — NOTE

The timeout path (`concurrent.futures.TimeoutError` at line 58 of `graph_builder.py`) is not tested. This is a gap, but it's a difficult path to test reliably in a unit test (would require either a slow mock or time manipulation). The code is straightforward (`return None` on timeout), so the risk of a latent bug is low. Not blocking.

---

## 3. Unit Tests — `tests/unit/test_graph_tools.py` (Task 10, Part 2)

### 3a. Test Coverage vs PLAN.md — PASS

PLAN.md specifies 11 tests across 5 classes. All 11 are present:

| Class | Plan requirement | Test method | Status |
|-------|-----------------|------------|--------|
| `TestRegisterGraphTools` | `test_registers_four_tools` | Line 42 | PASS |
| `TestRegisterGraphTools` | `test_tool_names_correct` | Line 48 | PASS |
| `TestGetBlastRadius` | `test_returns_impacted_files` | Line 68 | PASS |
| `TestGetBlastRadius` | `test_returns_error_on_store_exception` | Line 86 | PASS |
| `TestGetCallers` | `test_returns_callers_list` | Line 108 | PASS |
| `TestGetCallers` | `test_returns_empty_for_unknown_function` | Line 125 | PASS |
| `TestGetCallers` | `test_error_on_store_failure` | Line 135 | PASS |
| `TestGetDependents` | `test_returns_dependent_files` | Line 156 | PASS |
| `TestGetDependents` | `test_error_on_store_failure` | Line 169 | PASS |
| `TestGetChangeAnalysis` | `test_returns_risk_and_priorities` | Line 190 | PASS |
| `TestGetChangeAnalysis` | `test_error_on_store_failure` | Line 208 | PASS |

### 3b. Mocking Strategy — PASS

Tests mock `graph_store` as a `MagicMock()` and configure return values for specific methods (`get_impact_radius`, `get_transitive_tests`, `search_edges_by_target_name`, `get_edges_by_target`, `get_node`). This is the right approach — the graph store is an external dependency and the tests verify handler logic, not graph store behavior.

Helper functions `_make_node` and `_make_edge` (lines 17-34) create mock objects with the same attribute interface the handlers expect. This keeps test setup clean and consistent.

### 3c. Test Quality — PASS

- Registration tests verify both count (`len == 4`) and names — catches both missing and misnamed tools
- Success-path tests verify JSON structure (key presence + type checks), not just non-emptiness
- Error-path tests verify the `{"error": ...}` envelope is returned — matches the handler pattern
- `test_returns_callers_list` verifies deep structure (`callers[0]["name"]`, `callers[0]["file"]`) — good
- `test_returns_risk_and_priorities` verifies `risk_score` is bounded `0.0 <= score <= 1.0` — validates the `min(1.0, ...)` logic
- No redundant tests — each covers a distinct scenario

### 3d. Test Isolation — PASS

Tests use `registry.dispatch()` to invoke handlers through the same path the real runner uses. This exercises both registration and dispatch, not just the handler function directly. Each test creates a fresh `ToolRegistry` via `_registry_with_store()` — no shared state between tests.

### 3e. Missing Coverage — NOTE

The `get_callers` handler has two code paths: file_path-based lookup (line 78-85 in graph_tools.py) and name-based search (line 87-92). The `test_returns_callers_list` test only exercises the name-based path because `file_path` is not passed. The file_path path is untested. Low risk since the logic is similar, but worth noting for future expansion.

---

## 4. Cumulative Check — All Phases

### 4a. Phase 1 Files — No Regressions

- `src/graph_builder.py` — 60 lines, unchanged since Phase 1 approval. Defensive coding intact: lazy imports, `enable_graph` check, 30s timeout, `None` on all failures.
- `src/config.py` — `enable_graph` field present
- `requirements.txt` — `code-review-graph>=2.3,<3.0` present
- `pyproject.toml` — dependency present
- `Dockerfile` — dependency in pip install chain
- `docker-compose.yml` — `ENABLE_GRAPH=${ENABLE_GRAPH:-1}` present

### 4b. Phase 2 Files — No Regressions

- `src/tools/graph_tools.py` — 227 lines, 4 tools. `max_depth` removed from `get_callers` schema per Phase 2 review fix (da5981f). All handlers have try/except with error JSON.
- `src/agents/openai_runner.py` — SYSTEM_PROMPT includes graph tool mapping. Constructor accepts `graph_store` and `changed_files`. Conditional registration at lines 95-97.
- `src/review_job.py` — Phase 0 graph build at lines 69-77, passed to runner at lines 79-86.

### 4c. Phase 3 Files — New

- `commands/review-pr-core.md` — Step 2b and Step 5c additions only. No changes to existing steps. Remaining prompt is identical to pre-graph state.
- `tests/unit/test_graph_builder.py` — 5 tests, all pass
- `tests/unit/test_graph_tools.py` — 11 tests, all pass

### 4d. Cross-Phase Consistency — PASS

- Tool names in SYSTEM_PROMPT (Phase 2) match tool registrations in `graph_tools.py` (Phase 2) match prompt instructions in `review-pr-core.md` (Phase 3) match test assertions in `test_graph_tools.py` (Phase 3): `get_blast_radius`, `get_callers`, `get_dependents`, `get_change_analysis`
- Parameter names in prompt examples match schemas: `changed_files`, `function_name`, `file_path`
- No stale `max_depth` references anywhere — clean removal across all files

---

## 5. Test Results

```
100 passed, 13 failed, 8 skipped (2.80s)
```

- **100 passed** — up from 84 in Phase 2, reflecting 16 new graph tests (5 builder + 11 tools)
- **13 failed** — all pre-existing (`'suggestion' is a required property` in test fixtures). 2 unit failures (`test_gate_passes_with_no_criticals`, `test_github_dry_run_produces_valid_output`) + 11 integration failures (all in `test_ado_pr_review.py`). These are unrelated to graph integration — the fixtures predate this sprint.
- **8 skipped** — unchanged from prior phases

**No new test failures introduced by Phase 3. No regressions from Phases 1 or 2.**

---

## 6. Security — PASS

- No shell execution in any new code
- No secrets in code or tests
- `file_path.lstrip("/")` in graph tools prevents absolute path injection
- All graph store interactions are read-only queries against a local SQLite database
- Mock-based tests don't touch real filesystems or networks

---

## 7. Pattern Consistency — PASS

- Test files follow existing patterns: `pytest` with `mocker`/`capsys` fixtures, class-based test grouping, `_make_*` helpers for test data
- `test_graph_builder.py` imports the module directly (`import graph_builder`) — consistent with how `test_pr_scorer.py` imports `pr_scorer`
- `test_graph_tools.py` imports from `tools.graph_tools` and `tools.registry` — consistent with production import paths
- No `conftest.py` changes needed — new tests are self-contained

---

## Summary

Phase 3 (Tasks 9-10) is complete and correct. The prompt update in `review-pr-core.md` gives the agent clear graph-first instructions with proper fallback guidance. Unit tests cover all cases specified in PLAN.md with proper mocking — no `code-review-graph` package required to run them.

**Non-blocking notes carried forward from Phase 2:**
- SYSTEM_PROMPT graph tool instructions are unconditional (shown even when graph is absent) — cosmetic, defer to future cleanup
- `get_dependents` assumes `edge.file_path` exists on edge objects — acceptable given empirical API discovery

**Non-blocking notes from Phase 3:**
- Timeout path in `graph_builder.py` is untested (difficult to unit test reliably)
- `get_callers` file_path-based lookup path is untested (only name-based path exercised)

**All 3 phases are APPROVED. Sprint 2 graph integration is complete.**
