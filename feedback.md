# Graph Integration — Code Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-04-28 17:30:00+05:30
**Verdict:** CHANGES NEEDED

> See the recent git history of this file to understand the context of this review.
> Prior review: Phase 1 (Tasks 1-3) was APPROVED on 2026-04-28 12:00.

---

## 1. Graph Tools — `src/tools/graph_tools.py` (Task 5)

### 1a. Registration Pattern — PASS

`register_graph_tools(registry, workspace, graph_store, changed_files)` follows the exact pattern from `workspace_tools.py` and `vcs_tools.py`:
- Signature accepts `ToolRegistry` as first arg
- Imports `Tool, ToolRegistry` from `tools.registry`
- Uses inner handler functions with `registry.register(Tool(...))` calls
- 4 tools registered: `get_blast_radius`, `get_callers`, `get_dependents`, `get_change_analysis`

### 1b. Tool Schemas — PASS

All 4 schemas match the OpenAI function-calling format used in `vcs_tools.py`:
- `description` (string) and `parameters` (object with `properties` and `required`)
- `get_blast_radius`: `changed_files` (array of strings, required) — correct
- `get_callers`: `function_name` (string, required), `file_path` (string, optional), `max_depth` (integer, optional) — correct
- `get_dependents`: `file_path` (string, required) — correct
- `get_change_analysis`: `changed_files` (array of strings, required) — correct

All parameter types match PLAN.md specification.

### 1c. Error Handling — PASS

All 4 handlers wrap their logic in `try/except Exception` and return `json.dumps({"error": str(e)})` on failure. This matches the plan requirement and is consistent with error handling in `workspace_tools.py` (e.g., `read_local_file` handler lines 72-73).

### 1d. `max_depth` Parameter Declared But Unused — FIXED

**Doer:** fixed in commit da5981f — removed `max_depth` from `get_callers` schema; handler always does depth-1 traversal and GraphStore has no depth param for CALLS lookup, so the schema param was misleading.

### 1d. Original Finding

`get_callers` declares `max_depth` in its schema (line 126-128) but the handler at lines 78-97 never reads `args.get("max_depth")`. The handler always does depth-1 traversal regardless of what the agent passes.

This is a functional gap: the agent may pass `max_depth=3` expecting transitive callers and get only direct callers. Either:
1. Implement depth traversal (query callers of callers up to `max_depth`), or
2. Remove `max_depth` from the schema so the agent doesn't send a value that gets silently ignored

Option 2 is simpler and honest. The plan listed it as "optional, default 1" which suggests depth-1 was the intended behavior — so removing it from the schema is the cleaner fix.

### 1e. `get_dependents` Edge Query Strategy — NOTE

`handle_get_dependents` (lines 142-162) first queries by exact target path, then falls back to `search_edges_by_target_name`. The `edge.file_path` attribute (line 157) is used for grouping — this assumes `file_path` exists on the edge object. If the `code-review-graph` API returns edges without `file_path`, this will hit the `except` clause and return an error. Since the graph store API is external and was adapted by the doer from real inspection, this is acceptable but worth noting.

### 1f. Security — PASS

No shell execution, no path traversal outside workspace, no secrets. All inputs are strings passed to the graph store API which operates on a local SQLite database. `file_path.lstrip("/")` in `get_dependents` prevents absolute path injection.

---

## 2. Runner Wiring — `src/agents/openai_runner.py` (Task 6)

### 2a. Constructor Params — PASS

`__init__` accepts `graph_store=None` and `changed_files=None` as optional keyword arguments (lines 75-76). These default to `None`, which means existing callers are not affected.

### 2b. Conditional Registration — PASS

Lines 91-93: `if graph_store is not None:` — correct guard. Uses lazy import (`from tools.graph_tools import register_graph_tools`) inside the conditional, matching the pattern from `graph_builder.py`. Passes `changed_files or []` to handle `None`.

Registration happens after `register_workspace_tools` (line 90), which matches the plan: "After `register_workspace_tools` call, add conditional graph tool registration."

### 2c. SYSTEM_PROMPT Update — PASS

Lines 33-41 add the 4 graph tool mappings and the fallback note. The text matches the plan specification exactly:
- `get_change_analysis` → risk scores + review priorities
- `get_blast_radius` → all affected files/functions/tests
- `get_callers` → precise structural results (instead of `search_code`)
- `get_dependents` → files importing a module
- Fallback note about graph tools only being available when graph was built successfully

**NOTE:** The graph tool lines are present in the SYSTEM_PROMPT unconditionally — even when `graph_store is None` and no graph tools are registered. The agent will see instructions about tools that don't exist. This is low-severity because the fallback note tells it to use `search_code` if graph tools return errors, and the agent won't see them in its tool list. But it adds noise to the prompt. Consider making the graph section conditional in a future cleanup — not blocking.

---

## 3. ReviewJob Wiring — `src/review_job.py` (Task 7)

### 3a. Graph Build Placement — PASS

Lines 74-80: Graph build is inserted between `self._build_prompt()` (line 72) and `OpenAIAgentRunner` construction (line 82). This matches the plan exactly.

### 3b. Best-Effort Pattern — PASS

The `try/except Exception` block (lines 75-80) catches any failure and prints a diagnostic, falling back to `graph_store = None`. This is correct — a failed graph build should not block the review.

### 3c. `changed_files=[]` Hardcoded — NOTE

Line 89: `changed_files=[]` is always empty. The plan acknowledged this: "Graph tools accept `changed_files` as explicit parameter; agent passes them after `get_pr`." The agent itself will pass changed files as tool arguments at runtime, so this empty default is correct for construction time. The graph tools receive `changed_files` via their closure from `register_graph_tools`, but `get_blast_radius` and `get_change_analysis` take `changed_files` as explicit handler arguments — so the empty construction-time list is unused by those tools. No bug here.

---

## 4. Phase 1 Regression Check

Re-checked all Phase 1 files against the current branch head:

- `src/graph_builder.py` — unchanged from Phase 1 approval (60 lines, same content)
- `src/config.py` — `enable_graph` field still present, unchanged
- `requirements.txt` — `code-review-graph>=2.3,<3.0` still present
- `pyproject.toml` — dependency still present
- `Dockerfile` — dependency still present
- `docker-compose.yml` — `ENABLE_GRAPH` env var still present

**No Phase 1 regressions.**

---

## 5. Test Results

```
84 passed, 13 failed, 8 skipped (21.62s)
```

All 13 failures are pre-existing (`'suggestion' is a required property` in test fixtures) — same failures documented in Phase 1 review. The count breakdown:
- 2 unit test failures: `test_gate_passes_with_no_criticals`, `test_github_dry_run_produces_valid_output`
- 11 integration test failures: all in `test_ado_pr_review.py`

**No new test failures introduced by Phase 2.**

---

## 6. Pattern Consistency — PASS

- `graph_tools.py` follows the same module structure as `workspace_tools.py` and `vcs_tools.py` (module docstring, imports, single `register_*` function with inner handlers)
- Handler functions use `args["key"]` for required params and `args.get("key")` for optional — consistent with existing tools
- JSON serialization uses `json.dumps()` throughout — consistent
- Diagnostic `print()` statements in `review_job.py` match the stdout convention used elsewhere in the pipeline

---

## Summary

**Phase 2 is substantially correct but has one must-fix item:**

1. **MUST FIX — `max_depth` unused in `get_callers`** (Section 1d): The schema declares `max_depth` but the handler ignores it. Remove `max_depth` from the schema to avoid misleading the agent. (If multi-depth traversal is desired, that's a Phase 3+ enhancement.)

**Non-blocking notes:**
- SYSTEM_PROMPT graph tool instructions are unconditional (always shown even when graph is absent) — cosmetic, defer to Phase 3+
- `get_dependents` assumes `edge.file_path` exists on edge objects — acceptable given API was discovered empirically
- `changed_files=[]` at construction time is correct by design

Phase 1 code has not regressed. No new test failures.
