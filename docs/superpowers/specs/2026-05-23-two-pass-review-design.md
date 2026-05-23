# Two-Pass Review Architecture

**Date:** 2026-05-23
**Status:** Draft
**Author:** Siddharth Deshpande

## Problem Statement

CodeHawk's current review pipeline uses a multi-turn agent loop (up to 38 turns per batch) where conversation history accumulates on every turn. For a 98-file PR split into 10 batches, this costs **11.3M tokens** to produce **20 findings** — most of which are duplicates of the same 3 bugs.

Three root causes drive the cost:

1. **Prompt replay:** The ~28K token prompt is resent on every turn. Across 38 turns × 10 batches, this alone accounts for ~10M tokens.
2. **Sliding window re-fetches:** The Responses API sliding window (keep last 3 exchanges) drops prior tool results, causing the agent to re-fetch files it already read. 68% of `get_file_content` calls (119 of 175) were duplicates — only 56 unique files were read.
3. **Missing review mode checklists:** The security, architecture, performance, and migration checklists were referenced by path but never injected into the prompt, producing zero non-standard findings across 100+ PRs. *(Fixed: checklists are now injected via `_build_review_modes_section()`.)*

### Cost Target

Under **2M tokens** for a 98-file PR (80%+ reduction).

## Architecture

Replace the single 38-turn agent loop with two focused passes:

```
Pre-fetch PR data (diffs, risk scores, graph analysis)
         |
         v
  +------+------+
  |   Pass 1    |  Single-turn, no tools
  |   (Scan)    |  Identify candidate findings from diffs
  +------+------+
         |
         v
   Candidate list (JSON)
         |
         v
  +------+------+
  |   Pass 2    |  Short agent loop (5-10 turns), full history
  |  (Verify)   |  Verify candidates with tool calls
  +------+------+
         |
         v
   findings.json
```

Both passes run per-batch (10 batches for a 98-file PR). Batching is unchanged.

### Pass 1 — Scan (single-turn, no tools)

**Purpose:** Identify candidate findings from pre-injected diffs alone. This is a reasoning-only pass — the model applies all review mode checklists against the diffs and outputs structured candidates.

**Input:**
- System prompt (review-focused, ~2K tokens)
- User prompt containing:
  - Review instructions (abridged from `review-pr-core.md`, ~3K tokens)
  - Review mode checklists — all 5 inlined (~3.5K tokens)
  - Pre-injected diffs for this batch (~15K tokens)
  - Pre-computed risk context (risk scores, priorities, test gaps, ~1.5K tokens)
  - Lang-rules if applicable (~1K tokens)
- **Total input:** ~26K tokens

**Output:** JSON array of candidate findings:

```json
{
  "candidates": [
    {
      "file": "src/SummaryRenderer.js",
      "line": 74,
      "category": "correctness",
      "severity": "warning",
      "title": "Risk badge disappears when only parsedSummary data is available",
      "message": "The renderer now shows the badge only when riskScore prop is non-null...",
      "needs_verification": true,
      "verification_hint": "Need full file to check if parsedSummary.risk_assessment fallback exists elsewhere",
      "checklist_source": "standard/correctness"
    },
    {
      "file": "src/FilePreviewGallery.js",
      "line": 1,
      "category": "testing",
      "severity": "suggestion",
      "title": "Missing test coverage for FilePreviewGallery",
      "message": "No tests exercise drag-and-drop uploads, modal launch, or ref API helpers.",
      "needs_verification": false,
      "verification_hint": null,
      "checklist_source": "standard/test_coverage"
    }
  ],
  "files_clean": ["src/constants/AppColors.cs", "src/utils/DateHelper.cs"]
}
```

**Key design decisions:**
- `needs_verification` flag lets Pass 2 skip candidates that are clear from the diff alone (e.g., missing tests, unused imports, naming issues). Only candidates needing full-file context proceed to Pass 2's tool loop.
- `verification_hint` tells Pass 2 exactly what to look for, so it doesn't waste turns exploring.
- `checklist_source` tracks which review mode produced the finding, ensuring all modes are exercised.
- `files_clean` is produced here too — files with no candidates are marked clean immediately.

**No tools available.** The model cannot call `get_file_content` or any other tool. This constrains Pass 1 to what's visible in the diff, which is the right tradeoff: diffs are sufficient to identify candidates, even if some need verification.

**Output tokens:** ~2K per batch (structured JSON, no code suggestions yet).

**Cost per batch:** ~28K tokens. **Cost for 10 batches: ~280K tokens.**

### Pass 2 — Verify (short agent loop, full history)

**Purpose:** Verify candidates that need full-file context, refine messages, add concrete code suggestions, adjust confidence scores.

**Input:**
- System prompt (verification-focused, ~1.5K tokens):
  - Role: "You are verifying candidate code review findings."
  - Task: "For each candidate with `needs_verification: true`, use tool calls to check the full file context. Confirm, refine, or drop each candidate. Add concrete `suggestion` code blocks."
  - Budget: "You have 10 turns. Reserve the last 2 for output."
- User prompt containing:
  - Candidate findings from Pass 1 (~2-3K tokens)
  - Relevant diffs (only for files with candidates, ~8-10K tokens)
  - Scoring rules from `commands/scoring.md` (~1K tokens)
- **Total input:** ~15K tokens

**Tools available:** `get_file_content`, `search_code`, `read_local_file`, `get_callers`, `get_dependents`.

**Turn limit:** 10 (not 40).

**History mode:** Full conversation history (no sliding window). With only 5-10 turns, cumulative replay is affordable (~163K tokens per batch) and eliminates the re-fetch problem entirely.

**Output:** Final `findings.json` for this batch, conforming to the existing schema.

**Cost per batch:** ~163K tokens. **Cost for 10 batches: ~1.63M tokens.**

### Total Cost

| Phase | Per batch | × 10 batches |
|-------|-----------|--------------|
| Pass 1 (scan) | ~28K | ~280K |
| Pass 2 (verify) | ~163K | ~1.63M |
| **Total** | **~191K** | **~1.91M** |

**vs. current: 11.3M → 1.9M (83% reduction)**

## Components

### Modified Files

#### `src/agents/openai_runner.py`

Add a new method `run_single_turn()` for Pass 1:
- Takes system prompt + user prompt
- Makes one API call, no tool loop
- Returns the raw text output
- No tools registered

Modify `_run_responses()` to support a `use_sliding_window` flag:
- When `use_sliding_window=False`, build input items with full conversation history instead of the 3-exchange sliding window. This eliminates the re-fetch problem for short loops.
- `run()` accepts `max_turns` override (default 10 for Pass 2) and `use_sliding_window` (default `False` for Pass 2)
- The Chat Completions path (`_run_chat_completions`) already uses full history and needs no changes — this is about fixing the Responses API path used by Codex models.

#### `src/review_job.py`

Modify `create_findings()` to implement the two-pass flow:

```python
def create_findings(self):
    # Phase 0: Pre-fetch (unchanged)
    changed_files = self._prefetch_pr_data()
    diffs = self._prefetch_diffs(changed_files)
    
    # Pass 1: Scan — identify candidates
    scan_prompt = self._build_scan_prompt(changed_files, diffs)
    candidates = self._run_scan_pass(scan_prompt)
    
    # Pass 2: Verify — targeted agent loop
    verify_prompt = self._build_verify_prompt(candidates, diffs)
    findings = self._run_verify_pass(verify_prompt)
    
    return findings
```

New methods:
- `_build_scan_prompt()` — assembles the Pass 1 prompt with diffs + checklists
- `_build_verify_prompt()` — assembles the Pass 2 prompt with candidates + relevant diffs
- `_run_scan_pass()` — calls `run_single_turn()`, parses candidate JSON
- `_run_verify_pass()` — calls `run()` with `max_turns=10`, returns findings

#### `commands/review-scan.md` (new)

Pass 1 instructions template. Abridged from `review-pr-core.md`:
- Apply all review mode checklists
- Output structured candidates JSON
- Mark each with `needs_verification` and `verification_hint`
- Produce `files_clean` list

#### `commands/review-verify.md` (new)

Pass 2 instructions template:
- Verify each candidate against full file context
- Add concrete code suggestions
- Adjust confidence scores
- Output final `findings.json`

### Unchanged Components

- `src/batch_review_job.py` — batching logic unchanged, still splits into N batches
- `src/post_findings.py` — Phase 2 scoring unchanged
- `src/tools/` — all tool registrations unchanged
- `commands/review-mode-*.md` — checklists unchanged (now injected in Pass 1)
- `commands/findings-schema.json` — output schema unchanged

## Data Flow

```
batch_review_job.py
  |
  |-- for each batch (parallel, 3 workers):
  |     |
  |     |-- review_job.create_findings()
  |     |     |
  |     |     |-- _prefetch_pr_data()          [unchanged]
  |     |     |-- _prefetch_diffs()             [unchanged]
  |     |     |
  |     |     |-- Pass 1: _run_scan_pass()
  |     |     |     |-- openai_runner.run_single_turn(scan_prompt)
  |     |     |     |-- parse candidate JSON
  |     |     |     |-- filter candidates with needs_verification=true
  |     |     |
  |     |     |-- Pass 2: _run_verify_pass()
  |     |     |     |-- openai_runner.run(verify_prompt, max_turns=10)
  |     |     |     |-- full conversation history (no sliding window)
  |     |     |     |-- tools: get_file_content, search_code, etc.
  |     |     |
  |     |     |-- merge Pass 1 clean candidates + Pass 2 verified candidates
  |     |     |-- write findings.json
  |     |
  |     |-- return findings
  |
  |-- merge all batch findings
  |-- post_findings.py (Phase 2 scoring, unchanged)
```

## Error Handling

### Pass 1 produces no candidates
If Pass 1 returns an empty candidate list or malformed JSON:
- Skip Pass 2
- Produce findings.json with empty findings and all files in `files_clean`
- Log warning for investigation

### Pass 1 JSON parsing fails
- Retry once with a "fix your JSON" prompt appended
- If still fails, fall back to current single-pass architecture for this batch
- Log error with the raw output for debugging

### Pass 2 exhausts turns without findings JSON
- Reuse existing emergency synthesis logic from `_run_chat_completions`
- Scan conversation history for findings
- If nothing found, use Pass 1 candidates as-is (without verification refinement)

### Fallback to current architecture
If both passes fail for a batch, fall back to the existing single-pass 40-turn loop. This ensures no batch is silently dropped.

## Testing Strategy

### Unit Tests

- `test_build_scan_prompt()` — verify Pass 1 prompt includes checklists, diffs, risk context
- `test_build_verify_prompt()` — verify Pass 2 prompt includes candidates and relevant diffs only
- `test_parse_candidates()` — verify candidate JSON parsing with valid, malformed, and empty inputs
- `test_merge_findings()` — verify clean candidates (no verification needed) merge with verified ones
- `test_run_single_turn()` — verify single-turn API call with mocked OpenAI client
- `test_fallback_on_failure()` — verify fallback to current architecture when passes fail

### Integration Tests

- Run against a small real PR (~5 files) with mocked OpenAI API
- Verify end-to-end flow: Pass 1 → candidates → Pass 2 → findings.json
- Verify token usage is within expected range
- Compare finding quality against current architecture output for the same PR

## Migration Plan

1. Implement Pass 1 (`run_single_turn`, `_build_scan_prompt`, candidate parsing)
2. Implement Pass 2 (`_build_verify_prompt`, `_run_verify_pass` with `max_turns=10`)
3. Wire into `create_findings()` with fallback to current architecture
4. Run both architectures in parallel on 5 real PRs, compare findings quality and token cost
5. If quality is comparable and cost target met, switch default
6. Remove old single-pass code path after 2 weeks of production use

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Pass 1 misses candidates that need full-file context | Medium | Some findings lost | Pass 1 prompt explicitly says "when in doubt, mark needs_verification=true" — bias toward false positives |
| Pass 2 with 10 turns is insufficient for complex batches | Low | Truncated verification | Monitor turn exhaustion rate; increase to 15 if >10% of batches hit the limit |
| Two API call chains per batch increases latency | Low | Slower reviews | Pass 1 is single-turn (~3s). Net latency increase is small vs. 38-turn loop |
| Candidate JSON format breaks across model updates | Low | Parse failures | Schema validation + fallback to current architecture |

## Open Questions

1. Should Pass 1 and Pass 2 use the same model, or could Pass 1 use a cheaper model (e.g., `codex-mini`) since it's reasoning-only?
2. Should `needs_verification=false` candidates skip Pass 2 entirely and go directly into findings.json, or should all candidates flow through Pass 2 for consistency?
3. Should we add cross-batch deduplication as a Phase 3 step? (6 of 20 findings in PR #6658 were the same bug in different files.)
