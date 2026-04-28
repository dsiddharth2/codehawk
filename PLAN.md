# Implementation Plan: Integrate `code-review-graph` into CodeHawk

## Overview

Integrate `code-review-graph` (AST-based directed graph using Tree-sitter) into CodeHawk to give the review agent precise structural context instead of regex-based searching. This adds a "Phase 0" graph build step and 4 new agent tools. The integration is purely additive -- if the graph build fails or the package is unavailable, the agent falls back to current tools transparently.

---

## Phase 1: Dependency + Config + Graph Builder

**Commit: 1 commit at end of phase** -- `feat(graph): add code-review-graph dependency and graph builder`

### Task 1: Add `code-review-graph` dependency

**Files:**
- `requirements.txt` -- append `code-review-graph>=2.3,<3.0`
- `pyproject.toml` -- add `"code-review-graph>=2.3,<3.0"` to the dependencies array after `"msrest"` (line 14)
- `Dockerfile` -- add to the `pip install --no-cache-dir` command (lines 22-28), after `jsonschema`

**Dependencies:** None
**Risk:** Medium -- must verify the package actually exists on PyPI with this name and version range. Run `pip install code-review-graph>=2.3,<3.0` in the local venv first to confirm. If it doesn't exist or the API is different, adapt accordingly.

### Task 2: Add `enable_graph` config setting

**File:** `src/config.py`

Add a new field to the `Settings` class, after the `log_format` field (around line 101):

```python
enable_graph: bool = Field(
    default=True,
    description="Enable code-review-graph for AST-based blast-radius analysis"
)
```

**File:** `docker-compose.yml`

Add to the environment block (after line 37, before the `volumes:` section):
```yaml
- ENABLE_GRAPH=${ENABLE_GRAPH:-1}
```

**Dependencies:** None
**Risk:** Low

### Task 3: Create `src/graph_builder.py`

**File:** `src/graph_builder.py` (new)

Create a wrapper module with a single public function `build_graph(workspace: Path) -> Optional[Any]`. Key requirements:

- Import `code-review-graph` inside the function (lazy import) to handle ImportError gracefully
- Return `None` on any failure: `ImportError`, `Exception`, timeout
- Print diagnostic messages to stdout (matching existing convention in `openai_runner.py` lines 99-101)
- Check `Settings.enable_graph` before attempting build -- return `None` immediately if disabled
- **CRITICAL**: The exact import path and function names must be verified against the installed package. Run `pip install code-review-graph` then `python -c "import code_review_graph; help(code_review_graph)"` to discover the real API before writing this module.
- Add a timeout guard (e.g., 30s) to prevent graph build from blocking on very large repos

**Pattern reference:** Follow the same defensive coding style as `_load_codereview_yml()` in `src/post_findings.py` lines 250-292 -- try/except with fallback, warnings to stderr.

**Dependencies:** Task 1 (package must be installable), Task 2 (config field)
**Risk:** High -- the package API is unverified. This task includes an upfront API discovery step.

### VERIFY (Task 4)

- All existing tests still pass: `python -m pytest tests/ -v`
- `graph_builder.py` exists with correct defensive coding
- Config field `enable_graph` is accessible
- Dependency is in requirements.txt, pyproject.toml, and Dockerfile

---

## Phase 2: Graph Tools + Runner Wiring

**Commit: 1 commit at end of phase** -- `feat(graph): add 4 graph tools and wire into agent runner`

### Task 5: Create `src/tools/graph_tools.py`

**File:** `src/tools/graph_tools.py` (new)

Create a `register_graph_tools(registry, workspace, graph_store, changed_files)` function following the exact pattern from `src/tools/workspace_tools.py`:

- Function signature: `def register_graph_tools(registry: ToolRegistry, workspace: Path, graph_store: Any, changed_files: List[str]):`
- Imports at top: `from tools.registry import Tool, ToolRegistry`
- 4 inner handler functions + 4 `registry.register(Tool(...))` calls
- Each handler wraps in `try/except` and returns `json.dumps({"error": str(e)})` on failure

**Tool definitions (matching OpenAI function-calling schema pattern from `vcs_tools.py`):**

1. **`get_blast_radius`**
   - Schema parameters: `changed_files` (array of strings, required)
   - Handler queries graph for all callers, dependents, and tests affected
   - Returns JSON: `{"impacted_files": [...], "impacted_functions": [...], "test_gaps": [...]}`

2. **`get_callers`**
   - Schema parameters: `function_name` (string, required), `file_path` (string, optional), `max_depth` (integer, optional, default 1)
   - Handler queries structural CALLS edges in the graph
   - Returns JSON: `{"callers": [{"name": ..., "file": ..., "line": ...}]}`

3. **`get_dependents`**
   - Schema parameters: `file_path` (string, required)
   - Handler queries IMPORTS_FROM edges
   - Returns JSON: `{"dependents": [{"file": ..., "imports": [...]}]}`

4. **`get_change_analysis`**
   - Schema parameters: `changed_files` (array of strings, required)
   - Handler runs blast-radius + test coverage analysis
   - Returns JSON: `{"risk_score": 0.0-1.0, "review_priorities": [...], "test_gaps": [...]}`

**CRITICAL NOTE:** The actual graph store query API is unknown until the package is inspected. Adapt handler implementations to the real `code-review-graph` API discovered in Task 3.

**Dependencies:** Task 3 (graph_builder must exist for type reference)
**Risk:** High -- same package API uncertainty as Task 3

### Task 6: Wire graph tools into `OpenAIAgentRunner`

**File:** `src/agents/openai_runner.py`

**Action 1:** Modify `__init__` (lines 61-67) to accept optional `graph_store` and `changed_files` params:

```python
def __init__(
    self,
    settings: Settings,
    workspace: Path,
    model: str = "o3",
    pr_id: int = 0,
    repo: str = "",
    graph_store=None,
    changed_files=None,
):
```

After `register_workspace_tools` call, add conditional graph tool registration:

```python
if graph_store is not None:
    from tools.graph_tools import register_graph_tools
    register_graph_tools(self.registry, self.workspace, graph_store, changed_files or [])
```

**Action 2:** Update `SYSTEM_PROMPT` (lines 23-39) to add graph tool mapping. Append after the existing tool mapping block:

```
- To understand change impact → use `get_change_analysis` (risk scores + review priorities)
- To find blast radius of changes → use `get_blast_radius` (all affected files/functions/tests)
- Instead of `search_code("fn_name")` for callers → use `get_callers` (precise structural results)
- To find files importing a module → use `get_dependents`

Note: Graph tools are only available when the codebase graph was built successfully. If a graph tool returns an error, fall back to `search_code` or `read_local_file`.
```

**Dependencies:** Task 5 (graph_tools module must exist)
**Risk:** Low

### Task 7: Wire graph build into `ReviewJob.create_findings()`

**File:** `src/review_job.py`

Modify `create_findings()` (lines 65-85). Insert graph build between `prompt = self._build_prompt()` and the `OpenAIAgentRunner` constructor:

```python
def create_findings(self) -> Path:
    prompt = self._build_prompt()

    # Phase 0: Build code graph (best-effort)
    graph_store = None
    try:
        from graph_builder import build_graph
        graph_store = build_graph(self.config.workspace)
        if graph_store:
            print(f"  Code graph built successfully.")
    except Exception as exc:
        print(f"  Graph build skipped: {exc}")

    runner = OpenAIAgentRunner(
        settings=self.settings,
        workspace=self.config.workspace,
        model=self.config.model,
        pr_id=self.config.pr_id,
        repo=self.config.repo,
        graph_store=graph_store,
        changed_files=[],
    )
    # ... rest unchanged
```

**Dependencies:** Tasks 3, 6
**Risk:** Low

### VERIFY (Task 8)

- All existing tests still pass: `python -m pytest tests/ -v`
- Graph tools module exists with 4 tool registrations
- Runner accepts graph_store param
- ReviewJob wires graph build before runner construction
- SYSTEM_PROMPT updated with graph tool references

---

## Phase 3: Prompt Update + Tests

**Commit: 1 commit at end of phase** -- `feat(graph): update agent prompt and add unit tests`

### Task 9: Update `commands/review-pr-core.md`

**File:** `commands/review-pr-core.md`

**Action 1:** Add Step 2b after Step 2 (after line 98, before the `---` separator at line 99):

```markdown
## Step 2b -- Analyze Change Impact (if graph tools available)

If the `get_change_analysis` tool is available, call it now with the list of changed file paths from Step 2:

get_change_analysis(changed_files=["path/to/file1.py", "path/to/file2.py"])

This returns:
- `risk_score` (0-1) for each changed file
- `review_priorities` -- ranked list of functions to focus on
- `test_gaps` -- functions that changed but have no test coverage

Use this to plan your review:
- For T1-T2 PRs: skip this step (full review is cheap enough)
- For T3+ PRs: focus your review budget on high-risk files and functions first
- Use `test_gaps` to flag missing test coverage in findings

If the tool returns an error or is unavailable, continue to Step 3 normally.
```

**Action 2:** Update Step 5c (around line 173-178). Replace the existing ripgrep-only instruction with a graph-first approach:

```markdown
### 5c -- Check callers and usage

For functions or classes that changed their signature or behavior:

**Preferred (if graph tools available):**
get_callers(function_name="my_function", file_path="src/module.py")

This returns precise structural caller information with no false positives.

Use `get_blast_radius(changed_files=[...])` for a broader impact view.

**Fallback (if graph tools unavailable):**
rg "function_name|ClassName" /workspace/src --type py -l
```

**Dependencies:** None
**Risk:** Low

### Task 10: Create unit tests

**File:** `tests/unit/test_graph_builder.py` (new)

Tests following the pattern from `tests/unit/test_pr_scorer.py`:

- `TestBuildGraph`:
  - `test_returns_none_when_package_not_installed` -- mock import to raise `ImportError`
  - `test_returns_none_when_build_raises_exception` -- mock the package to raise `RuntimeError`
  - `test_returns_store_on_success` -- mock the package to return a mock store
  - `test_returns_none_when_enable_graph_is_false` -- mock `get_settings()` to return `enable_graph=False`
  - `test_prints_diagnostic_on_failure` -- capture stdout, verify message printed

**File:** `tests/unit/test_graph_tools.py` (new)

Tests following the pattern from `tests/unit/test_post_findings.py`:

- `TestRegisterGraphTools`:
  - `test_registers_four_tools` -- create a `ToolRegistry`, call `register_graph_tools` with a mock store, assert 4 tools registered
  - `test_tool_names_correct` -- verify names: `get_blast_radius`, `get_callers`, `get_dependents`, `get_change_analysis`

- `TestGetBlastRadius`:
  - `test_returns_impacted_files` -- mock store query, verify JSON response
  - `test_returns_error_on_store_exception` -- mock store to raise, verify `{"error": ...}` returned

- `TestGetCallers`:
  - `test_returns_callers_list` -- mock store, verify structured JSON
  - `test_returns_empty_for_unknown_function` -- verify `{"callers": []}` when no matches
  - `test_error_on_store_failure` -- verify graceful error

- `TestGetDependents`:
  - `test_returns_dependent_files` -- mock store
  - `test_error_on_store_failure`

- `TestGetChangeAnalysis`:
  - `test_returns_risk_and_priorities` -- mock store
  - `test_error_on_store_failure`

**Pattern reference:** Use `pytest-mock`'s `mocker.patch()` as seen in `tests/conftest.py` lines 173-199.

**Dependencies:** Tasks 3, 5
**Risk:** Low

### VERIFY (Task 11)

- Run: `python -m pytest tests/ -v`
- Verify: all existing tests still pass (no regressions)
- Verify: new tests for graph_builder and graph_tools pass
- Verify: `docker build -t codehawk:graph-test .` succeeds (if Docker available)

---

## Commit Strategy

Per project conventions: **1 commit per phase**, not per step.

| Phase | Commit Message | Files |
|-------|---------------|-------|
| Phase 1 | `feat(graph): add code-review-graph dependency and graph builder` | `requirements.txt`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `src/config.py`, `src/graph_builder.py` |
| Phase 2 | `feat(graph): add 4 graph tools and wire into agent runner` | `src/tools/graph_tools.py`, `src/agents/openai_runner.py`, `src/review_job.py` |
| Phase 3 | `feat(graph): update agent prompt and add unit tests` | `commands/review-pr-core.md`, `tests/unit/test_graph_builder.py`, `tests/unit/test_graph_tools.py` |

---

## Key File References

| Purpose | File | Lines |
|---------|------|-------|
| Tool registration pattern | `src/tools/workspace_tools.py` | 57-248 |
| Tool registration pattern | `src/tools/vcs_tools.py` | 1-204 |
| Registry | `src/tools/registry.py` | 1-42 |
| Runner to modify | `src/agents/openai_runner.py` | 58-86 (constructor), 23-39 (SYSTEM_PROMPT) |
| ReviewJob to modify | `src/review_job.py` | 65-85 (create_findings) |
| Config pattern | `src/config.py` | 18-228 (Settings class) |
| Prompt to update | `commands/review-pr-core.md` | 98-99 (Step 2b insertion), 170-178 (Step 5c) |
| Test patterns | `tests/conftest.py`, `tests/unit/test_pr_scorer.py`, `tests/unit/test_post_findings.py` |
| Dependency files | `requirements.txt`, `pyproject.toml` (line 11), `Dockerfile` (lines 22-28), `docker-compose.yml` (lines 15-37) |

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| `code-review-graph` package API does not match plan assumptions | High | Task 3 includes mandatory API discovery step; `graph_builder.py` isolates all imports behind try/except |
| `code-review-graph` not on PyPI or version mismatch | High | Doer verifies `pip install` before starting; fall back to GitHub install if needed |
| Graph build adds latency (5-15s) | Low | One-time cost per review; saves 3-8 tool calls; controlled by `enable_graph` toggle |
| Tree-sitter doesn't support a language in the workspace | Low | `build_graph` returns `None`; agent falls back to existing tools |
| Docker image size increase | Low | `code-review-graph` with tree-sitter ~15MB; acceptable |
| `changed_files` not available at runner construction time | Low | Graph tools accept `changed_files` as explicit parameter; agent passes them after `get_pr` |

---

## Success Criteria

- [ ] `code-review-graph` installable in venv and in Docker image
- [ ] `build_graph()` returns `None` gracefully when package unavailable
- [ ] `build_graph()` returns a graph store when package is available and workspace is parseable
- [ ] 4 graph tools registered when `graph_store is not None`
- [ ] 0 graph tools registered when `graph_store is None` (no error, no noise)
- [ ] Agent prompt references graph tools with fallback instructions
- [ ] `ENABLE_GRAPH=0` disables graph entirely
- [ ] All existing unit tests continue to pass
- [ ] New unit tests cover graph builder and all 4 graph tool handlers
- [ ] Docker build succeeds with the new dependency
