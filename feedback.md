# CodeHawk Documentation -- Code Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-05-01 12:00:00+00:00
**Verdict:** CHANGES NEEDED

> See the recent git history of this file to understand the context of this review.

---

## Phase 1 Scope

Four tasks completed on branch `docs/comprehensive-documentation` (commits ab52292 through fb48ce3):

| Task | File | Lines | Commits |
|------|------|-------|---------|
| Task 1 | README.md (full rewrite) | 463 | ab52292 |
| Task 2 | docs/README.md (index) | 41 | 74acdcc |
| Task 3 | docs/features/graph-tools.md | 268 | 915151b |
| Task 4 | docs/features/agent-runner.md | 309 | bbb9e2e |

---

## Task 1: README.md -- Full Rewrite

**PASS** (with one factual gap, see below)

Cross-checked against source files:

- **Environment variable table** matches `src/config.py` defaults: `VCS` default "ado", `MIN_CONFIDENCE_SCORE` 0.7, `MAX_COMMENTS_PER_FILE` 5, `UPDATE_EXISTING_SUMMARY` true, `LOG_LEVEL` "INFO", `LOG_FORMAT` "json", `ENABLE_GRAPH` true, `ENABLE_PR_SCORING` true, `AUTH_MODE` "auto". Docker-level env vars (`OPENAI_MODEL`, `MAX_TURNS`, `DRY_RUN`, `COMMIT_ID`) confirmed in `entrypoint.sh` lines 30-44. PASS.

- **Scoring penalty matrix** matches `src/config.py` defaults (lines 115-137): Security 5.0/4.0/2.0, Performance 3.0/2.0/1.0, Best Practices 2.0/1.0/0.5, Code Style 0.0/0.0/0.0, Documentation 0.0/0.0/0.0. PASS.

- **Star rating thresholds** match `src/config.py` lines 140-144: 0.0, 5.0, 15.0, 30.0, 50.0. PASS.

- **Findings schema** matches `commands/findings-schema.json` and actual agent output structure. JSON example is valid and realistic. PASS.

- **Project structure tree** verified against actual filesystem -- all listed files and directories exist (`src/run_agent.py`, `src/review_job.py`, `src/post_findings.py`, `src/pr_scorer.py`, `src/graph_builder.py`, `src/config.py`, `src/score_comparison.py`, `src/agents/openai_runner.py`, `src/tools/registry.py`, `src/tools/graph_tools.py`, `src/tools/vcs_tools.py`, `src/tools/workspace_tools.py`, `src/activities/`, `src/models/`, `commands/review-pr-core.md`, `commands/findings-schema.json`, `docs/`, `ci/`, `templates/`, `entrypoint.sh`, `Dockerfile`, `pyproject.toml`). PASS.

- **Developer controls** (`# cr: intentional`, `# cr: ignore-next-line`, `# cr: ignore-block start/end`) confirmed in `commands/review-pr-core.md`. PASS.

- **Pipeline caps** ("30 total, 5 per file", confidence 0.7) confirmed in `src/post_findings.py` lines 31-33: `MAX_TOTAL_FINDINGS = 30`, `MAX_PER_FILE = 5`, `MIN_CONFIDENCE = 0.7`. PASS.

- **Quick Start CLI example** matches `src/run_agent.py` argparse definitions. PASS.

- **3 Mermaid diagrams** (flowchart LR architecture, flowchart TD scoring, sequenceDiagram pipeline flow) -- all syntactically correct with proper code fences. PASS.

- **9 documentation links** at bottom of README all resolve to existing files in `docs/`. PASS.

**FAIL -- Cost table missing `gpt-4.1-nano` model.** The README lists 12 models in the Cost Tracking table. The source `src/post_findings.py` `MODEL_COST_TABLE` (line 39) has 13 entries -- the README omits `gpt-4.1-nano` ($0.10 input / $0.40 output per 1M tokens). All other 12 entries have correct prices. This is a verifiable factual gap against the source.

---

## Task 2: docs/README.md -- Documentation Index

**PASS**

- **8 feature doc links** all resolve to existing files in `docs/features/` (agent-runner.md, graph-tools.md, review-modes.md, scoring.md, post-findings.md, fix-verification.md, ci-integration.md, vcs-cli.md). PASS.

- **Architecture link** resolves to `docs/architecture.md`. PASS.

- **Quick Links** all resolve: `../commands/findings-schema.json`, `../commands/review-pr-core.md`, `features/scoring.md`, `features/ci-integration.md`, `../ci/`, `../templates/`. PASS.

- **Descriptions** are accurate summaries of each document's content. PASS.

- No content duplication -- this file serves purely as an index/navigation hub. PASS.

---

## Task 3: docs/features/graph-tools.md -- Graph Analysis Deep-Dive

**PASS**

Cross-checked all 4 tool implementations against `src/tools/graph_tools.py`:

- **`get_change_analysis`** (lines 173-227): Input schema `changed_files` array matches. Risk score formula documented as `min(1.0, non_test_impacted / 20.0)` -- confirmed at source line 184. Review priorities filter to Function/Method/Class -- matches line 189. Test gaps check uses `get_transitive_tests` -- matches lines 193-197. JSON output structure matches handler return. PASS.

- **`get_blast_radius`** (lines 23-67): Input schema matches. Output includes `impacted_files`, `impacted_functions`, `test_gaps` -- matches handler. Implementation calls `get_impact_radius`, filters `impacted_nodes` to Function/Method -- confirmed at lines 27-31. PASS.

- **`get_callers`** (lines 71-121): Input schema matches (function_name required, file_path optional). Qualified name construction `file_path::function_name` documented and confirmed at source line 79. Dual lookup (edges_by_target + search_edges_by_target_name) documented and confirmed. Dedup by `source_qualified` confirmed at line 82. PASS.

- **`get_dependents`** (lines 125-169): Input schema matches. Strips leading `/` -- confirmed at source line 128. Filters to `IMPORTS_FROM` edges -- confirmed at line 130. Fallback to `search_edges_by_target_name` -- confirmed at lines 132-133. Groups by source file -- confirmed. PASS.

- **Graph Builder section** matches `src/graph_builder.py`: package name `code-review-graph`, entry point `build_or_update_graph(full_rebuild=True, repo_root=workspace, postprocess="minimal")`, SQLite storage via `get_db_path`, 30s timeout via `ThreadPoolExecutor`, all 6 failure modes documented match the actual exception handling. PASS.

- **Mermaid flowchart** (graph-first strategy) syntactically correct. PASS.

- **Tier-based depth table** consistent with `commands/review-pr-core.md` tier definitions. PASS.

- **Graceful degradation section** accurately describes no-graph system prompt fallback and per-tool error handling via `{"error": "..."}`. PASS.

---

## Task 4: docs/features/agent-runner.md -- Agent Runner Deep-Dive

**PASS**

Cross-checked against `src/agents/openai_runner.py` and `src/tools/registry.py`:

- **API detection**: `RESPONSES_API_MODELS = {"gpt-5-codex", "codex-mini-latest"}` -- exact match to source line 90. Property `_use_responses_api` matches line 131-132. PASS.

- **ToolRegistry class diagram** (Mermaid classDiagram): `Tool` dataclass fields (name, schema, handler) match `registry.py` line 13. `ToolRegistry` methods (_tools dict, register, get, openai_definitions, responses_definitions, dispatch) all match source. PASS.

- **Tool registration order** matches `__init__` in source lines 118-128: vcs_tools, workspace_tools, then conditional graph_tools. PASS.

- **API definition formats**: `openai_definitions()` shape `[{"type": "function", "function": {...}}]` matches source line 33-36. `responses_definitions()` shape `[{"type": "function", "name": ..., ...}]` matches source lines 40-48. PASS.

- **System prompt** (`build_system_prompt`): Role statement, turn budget, graph/no-graph strategy blocks, tool mapping table -- all confirmed in source lines 27-71. PASS.

- **Conversation loop flowchart** (Mermaid flowchart TD): Accurately depicts the turn loop, finish_reason branching, tool dispatch, turn counter suffix, deadline injection at N-3, and break conditions. PASS.

- **Turn budget 3 layers**: Layer 1 (continuous counter) at source lines 238-239/378-379. Layer 2 (deadline injection at N-3) at source lines 162-169/294-301. Layer 3 (30k truncation) at source lines 235-236/375-376. PASS.

- **Findings extraction cascade** (Mermaid flowchart TD): Tier 1 (`_extract_findings_json`) 4-step process matches source lines 487-511: code fence regex, `pr_id` regex, full-text parse, strict=False retry. Tier 2 (`_scan_history_for_findings`) matches source lines 461-484: code fence + brace-balanced extraction, reversed iteration, largest-wins selection. Tier 3 (emergency synthesis) dict matches source lines 260-268 exactly. PASS.

- **AgentResult fields**: All 10 fields documented match the class definition at source lines 74-87 (findings_data, input_tokens, output_tokens, total_tokens, tool_calls_count, duration_seconds, model, turns, raw_final_message, returncode). PASS.

- **ReviewJob usage example**: Code snippet matches `review_job.py` `create_findings()` flow at source lines 65-114. PASS.

---

## Mermaid Diagram Verification (7 diagrams total)

All diagrams use correct syntax: proper ` ```mermaid ` code fences, valid diagram type keywords, proper node/edge syntax.

| File | Diagram | Type | Status |
|------|---------|------|--------|
| README.md | Architecture overview | flowchart LR | PASS |
| README.md | Scoring flow | flowchart TD | PASS |
| README.md | Pipeline sequence | sequenceDiagram | PASS |
| graph-tools.md | Graph-first strategy | flowchart TD | PASS |
| agent-runner.md | Tool system | classDiagram | PASS |
| agent-runner.md | Conversation loop | flowchart TD | PASS |
| agent-runner.md | Findings extraction | flowchart TD | PASS |

---

## Internal Link Verification

All internal links across all 4 files resolve:

| Source File | Link Target | Exists |
|-------------|-------------|--------|
| README.md | docs/architecture.md | Yes |
| README.md | docs/features/agent-runner.md | Yes |
| README.md | docs/features/graph-tools.md | Yes |
| README.md | docs/features/review-modes.md | Yes |
| README.md | docs/features/scoring.md | Yes |
| README.md | docs/features/post-findings.md | Yes |
| README.md | docs/features/fix-verification.md | Yes |
| README.md | docs/features/ci-integration.md | Yes |
| README.md | docs/features/vcs-cli.md | Yes |
| docs/README.md | architecture.md | Yes |
| docs/README.md | features/*.md (8 links) | Yes |
| docs/README.md | ../commands/findings-schema.json | Yes |
| docs/README.md | ../commands/review-pr-core.md | Yes |
| docs/README.md | ../ci/ | Yes |
| docs/README.md | ../templates/ | Yes |

---

## Summary

**3 of 4 tasks fully pass.** Task 1 (README.md) has one factual gap: the Cost Tracking table is missing the `gpt-4.1-nano` model ($0.10 input / $0.40 output per 1M tokens) which is present in `src/post_findings.py` `MODEL_COST_TABLE`. All other factual claims, diagrams, links, and cross-references are verified correct.

**Required fix:**
- Add `gpt-4.1-nano` row to the Cost Tracking table in README.md (between `gpt-4.1` and `gpt-4o`)

No other issues found. Documentation depth is excellent -- both deep-dive docs (graph-tools.md and agent-runner.md) go well beyond surface-level descriptions, documenting implementation internals, field-level semantics, and failure modes with source-verified accuracy.
