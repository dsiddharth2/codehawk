# Requirements: Full-Coverage Large PR Review

## Base Branch
`main` — branch from `origin/main`.

## Problem Statement

CodeHawk currently runs a single OpenAI agent session to review PRs. Large PRs (50+ files) hit multiple bottlenecks: a MAX_FILES=100 cap that truncates the file list, a 40-turn budget with system prompt advising "review top 10-15 files", 10KB diff truncation in `handle_get_file_diff`, and context window saturation. This results in incomplete reviews where many files are never examined.

## 3-Layer Strategy

### Layer 1: File Filtering
- Skip non-code files: `.md`, `.json`, `.yaml`, `.yml`, `.xml`, `.lock`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, `.ico`, `.csproj`, `.sln`, `.config`, `.env`, `.gitignore`, `.dockerignore`, `.editorconfig`, `.prettierrc`, `.eslintignore`
- **Keep `.css`** in the review queue
- Configurable via `SKIP_EXTENSIONS` env var
- Skipped files listed in summary but not reviewed

### Layer 2: Smart Diffs (Summary + On-Demand)
- Diffs under 30KB: return full diff (1 turn)
- Diffs >= 30KB: return structured summary (hunk headers, line ranges, preview lines, stats) + agent calls `get_file_diff` with `start_line`/`end_line` to drill into specific sections (1-2 extra turns)
- Most files have normal diffs, so turn budget impact is minimal

### Layer 3: Batched Agent Sessions
- After filtering, if code files exceed `batch_size` (default 25), split into batches
- Each batch gets its own agent session with fresh context window + turn budget
- Graph built ONCE and shared across all batches (cross-file analysis via `get_callers`/`get_blast_radius` still works)
- Findings merged deterministically: re-sequence cr-ids, dedup by file+line+title, sum usage stats
- Sequential execution (parallel can be added later)

## Current Bottlenecks

| Bottleneck | Location | Current Limit | Impact |
|-----------|----------|---------------|--------|
| MAX_FILES cap | `review_job.py:212` | 100 files | Files 101+ omitted from prompt |
| Turn budget | `openai_runner.py:156` | 40 turns | Agent runs out before reviewing all files |
| Skip guidance | `openai_runner.py:44-51` | "top 10-15 files" | Agent intentionally skips files |
| Diff truncation | `vcs_tools.py:210` | 10KB | 90%+ of large diffs invisible |
| Tool result cap | `openai_runner.py:256` | 30KB | Tool outputs silently truncated |
| Search truncation | `workspace_tools.py:104` | 15KB | Search results cut off |
| Read file limit | `workspace_tools.py:148` | 500 lines | Large files truncated |
| Findings caps | `post_findings.py:31-32` | 30 total, 5/file | Hardcoded, not configurable |
| Graph timeout | `graph_builder.py:18-24` | 300s max | May fail for huge repos |

## Implementation Steps

### Step 1: Add config fields (`src/config.py`)

Add to the `Settings` class:

```python
# Large PR handling
skip_extensions: str = Field(
    default=".md,.json,.yaml,.yml,.xml,.lock,.png,.jpg,.jpeg,.gif,.svg,.ico,.csproj,.sln,.config,.env,.gitignore,.dockerignore,.editorconfig,.prettierrc,.eslintignore",
    description="Comma-separated file extensions to skip during review (non-code files)"
)
smart_diff_threshold_kb: int = Field(
    default=30,
    ge=1,
    le=500,
    description="Diff size in KB above which smart summaries are returned instead of full text"
)
batch_size: int = Field(
    default=25,
    ge=5,
    le=100,
    description="Max code files per agent batch. Files beyond this trigger multi-batch review."
)
batch_max_turns: int = Field(
    default=40,
    ge=10,
    le=100,
    description="Max turns per batch agent session"
)
```

### Step 2: Create file filter module (`src/file_filter.py`)

**New file** with two functions:

```python
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.review_models import FileChange


def parse_skip_extensions(skip_extensions_csv: str) -> set[str]:
    """Parse comma-separated extensions into a normalized set (lowercase, with leading dot)."""
    exts = set()
    for ext in skip_extensions_csv.split(","):
        ext = ext.strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext
        if ext:
            exts.add(ext)
    return exts


def filter_changed_files(
    file_changes: list["FileChange"],
    skip_extensions: set[str],
) -> tuple[list["FileChange"], list["FileChange"]]:
    """Split file_changes into (code_files, skipped_files).

    Deleted files (change_type='delete') are always skipped since there's
    nothing to review in the new code.
    """
    code_files = []
    skipped = []
    for fc in file_changes:
        ext = Path(fc.path).suffix.lower()
        if ext in skip_extensions or fc.change_type == "delete":
            skipped.append(fc)
        else:
            code_files.append(fc)
    return code_files, skipped
```

### Step 3: Integrate filtering into ReviewJob (`src/review_job.py`)

**Modify `create_findings()`** — insert filtering between PR pre-fetch (line 79) and prompt build (line 84):

```python
# After changed_files = pr_details.file_changes
from file_filter import filter_changed_files, parse_skip_extensions
skip_exts = parse_skip_extensions(self.settings.skip_extensions)
code_files, skipped_files = filter_changed_files(changed_files, skip_exts)
if skipped_files:
    logger.info("Filtered %d non-code files (kept %d code files)", len(skipped_files), len(code_files))
changed_files = code_files
```

**Modify `_build_changed_files_section()`** — remove `MAX_FILES = 100` cap entirely (lines 212-237). Show all files in the table. Add skipped files summary.

**Add batch mode support** — add optional fields to `ReviewJobConfig`:
```python
batch_index: Optional[int] = None
batch_total: Optional[int] = None
file_subset: Optional[list] = None
pre_built_graph: Optional[Any] = None
```

When `file_subset` is set, skip PR pre-fetch and use subset directly. When `pre_built_graph` is set, skip `build_graph()`. When `batch_index` is set, append batch context to prompt.

### Step 4: Create smart diff module (`src/smart_diff.py`)

**New file** with diff summarization:

```python
import re
from dataclasses import dataclass


@dataclass
class DiffSummary:
    file_path: str
    total_size_bytes: int
    hunks: list[dict]  # [{header, start_line, end_line, added, removed, context, preview_lines}]
    is_summarized: bool


def summarize_diff(diff_text: str, file_path: str, threshold_kb: int = 30) -> DiffSummary:
    """If diff exceeds threshold, return structured summary with hunk details."""
    size_bytes = len(diff_text.encode("utf-8"))
    if size_bytes < threshold_kb * 1024:
        return DiffSummary(file_path=file_path, total_size_bytes=size_bytes, hunks=[], is_summarized=False)

    hunks = []
    current_hunk = None

    for line in diff_text.split("\n"):
        match = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*)', line)
        if match:
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = {
                "header": line,
                "start_line": int(match.group(3)),
                "end_line": int(match.group(3)) + int(match.group(4) or 1),
                "context": match.group(5).strip(),
                "added": 0,
                "removed": 0,
                "preview_lines": [],
            }
        elif current_hunk:
            if line.startswith("+"):
                current_hunk["added"] += 1
                if len(current_hunk["preview_lines"]) < 3:
                    current_hunk["preview_lines"].append(line[:120])
            elif line.startswith("-"):
                current_hunk["removed"] += 1

    if current_hunk:
        hunks.append(current_hunk)

    return DiffSummary(file_path=file_path, total_size_bytes=size_bytes, hunks=hunks, is_summarized=True)


def format_summary_for_agent(summary: DiffSummary) -> str:
    """Format a DiffSummary as text the agent can read."""
    lines = [
        f"LARGE DIFF SUMMARY for {summary.file_path} ({summary.total_size_bytes:,} bytes)",
        f"This diff exceeds the size threshold. {len(summary.hunks)} hunks found.",
        "Call get_file_diff with start_line and end_line to drill into specific sections.",
        "",
        "Hunks:",
    ]
    for i, hunk in enumerate(summary.hunks, 1):
        lines.append(
            f"  [{i}] Lines {hunk['start_line']}-{hunk['end_line']} "
            f"(+{hunk['added']}/-{hunk['removed']}) {hunk.get('context', '')}"
        )
        for preview in hunk.get("preview_lines", []):
            lines.append(f"      {preview}")
    return "\n".join(lines)
```

### Step 5: Modify get_file_diff for smart diffs + drill-in (`src/tools/vcs_tools.py`)

**Modify `handle_get_file_diff`** (line 199):

1. Remove hardcoded `[:10000]` truncation (line 210)
2. Add smart diff logic: if diff > threshold, return summary via `summarize_diff()`
3. Add drill-in support: when `start_line`/`end_line` provided, extract only relevant hunks

**Update tool schema** — add optional parameters:
```python
"start_line": {
    "type": "integer",
    "description": "Start line to drill into a specific section (optional, use with end_line)"
},
"end_line": {
    "type": "integer",
    "description": "End line to drill into a specific section (optional, use with start_line)"
},
```

**Modified handler logic**:
```python
def handle_get_file_diff(args: dict) -> str:
    result = diff_activity.execute(...)

    start_line = args.get("start_line")
    end_line = args.get("end_line")

    if start_line and end_line:
        # Drill-in mode: extract hunks in range
        filtered = _extract_hunks_in_range(result.diff_text, start_line, end_line)
        return json.dumps({"file_path": result.file_path, "diff_text": filtered[:30000], "drill_in": True})

    # Full mode: check if smart summary needed
    summary = summarize_diff(result.diff_text, result.file_path, threshold_kb)
    if summary.is_summarized:
        return json.dumps({
            "file_path": result.file_path,
            "summary": format_summary_for_agent(summary),
            "is_summary": True,
            "hint": "Call get_file_diff with start_line/end_line to drill into specific hunks.",
        })

    # Normal: return full diff (raised from 10KB to 30KB safety cap)
    return json.dumps({
        "file_path": result.file_path,
        "diff_text": result.diff_text[:30000],
        ...
    })
```

### Step 6: Update system prompt (`src/agents/openai_runner.py`)

**Modify `build_system_prompt()`** (lines 30-89):

- Remove: `"For large PRs (50+ files): review top 10-15 files by change volume using diffs, not full reads."` (appears in both graph and no-graph sections)
- Add:
  ```
  - Review ALL files in your assigned batch — do not skip files. The orchestrator has already
    filtered non-code files and split the PR into manageable batches.
  - When get_file_diff returns is_summary=true, the diff was too large to return in full.
    Read the hunk summary to identify high-risk sections, then call get_file_diff again with
    start_line and end_line to drill into those sections.
  ```
- Raise tool result cap from 30KB to 50KB (line 256, line 395)

### Step 7: Raise other truncation limits

- `src/tools/workspace_tools.py:104` — search_code: 15KB to 25KB
- `src/tools/workspace_tools.py:148` — read_local_file: 500 to 1000 lines default
- `src/agents/openai_runner.py:256,395` — tool result cap: 30KB to 50KB

### Step 8: Create BatchReviewJob (`src/batch_review_job.py`)

**New file** — the core orchestrator:

```python
class BatchReviewJob:
    """Orchestrates large PR reviews by filtering, batching, and merging."""

    def __init__(self, pr_id, repo, workspace, model, prompt_path, vcs, settings):
        ...

    def run(self, dry_run=False, commit_id="") -> dict:
        # 1. Pre-fetch PR data (once)
        # 2. Filter non-code files
        # 3. Build graph once (shared across batches)
        # 4. If code files <= batch_size: delegate to ReviewJob (single session)
        # 5. Split into batches (round-robin by churn for balance)
        # 6. Run each batch sequentially via ReviewJob
        # 7. Merge findings (re-sequence cr-ids, dedup, sum usage)
        # 8. Publish results (Phase 2)

    def _split_into_batches(self, code_files, batch_size):
        """Round-robin by churn descending to balance workload across batches."""
        sorted_files = sorted(code_files, key=lambda fc: fc.additions + fc.deletions, reverse=True)
        num_batches = ceil(len(sorted_files) / batch_size)
        batches = [[] for _ in range(num_batches)]
        for i, fc in enumerate(sorted_files):
            batches[i % num_batches].append(fc)
        return batches

    def _run_batch(self, batch_files, batch_index, batch_total, graph_store):
        """Run single batch via ReviewJob with file_subset and pre_built_graph."""
        config = ReviewJobConfig(
            ...,
            batch_index=batch_index,
            batch_total=batch_total,
            file_subset=batch_files,
            pre_built_graph=graph_store,
        )
        job = ReviewJob(config, self.settings)
        job.create_findings()
        return job  # extract findings from written file

    def _merge_results(self, batch_results):
        """Merge findings from all batches."""
        # 1. Concatenate all findings
        # 2. Dedup by (file, line, title)
        # 3. Re-sequence cr-ids: cr-001, cr-002, ...
        # 4. Sum usage: input_tokens, output_tokens, duration
        # 5. Collect batch errors (if any batch failed)
        # 6. Return unified findings dict
```

**Key design decisions**:
- Graph built once, shared via `pre_built_graph` — cross-file analysis still works
- Round-robin splitting ensures high-churn files distributed evenly across batches
- Failed batches don't crash pipeline — other batch findings preserved
- Single-session shortcut for small PRs (< batch_size) — backward compatible

### Step 9: Update run_agent.py entry point

**Modify `main()`** to use `BatchReviewJob` instead of `ReviewJob`:

```python
from batch_review_job import BatchReviewJob

batch_job = BatchReviewJob(
    pr_id=args.pr_id,
    repo=args.repo,
    workspace=Path(args.workspace),
    model=args.model,
    prompt_path=Path(args.prompt_file),
    vcs="ado",
)
output = batch_job.run(dry_run=args.dry_run, commit_id=args.commit_id)
```

BatchReviewJob delegates to single-session ReviewJob for small PRs, so this is backward compatible.

### Step 10: Update review prompt (`commands/review-pr-core.md`)

- Update Step 4 (T4/T5 tiers): remove "focus on highest-risk paths only", replace with "Review ALL files in your batch"
- Add note: "Non-code files have been pre-filtered. You will only see code files."
- Add smart diff instructions: "When get_file_diff returns is_summary, drill into suspicious hunks with start_line/end_line"

### Step 11: Update post_findings.py caps

- Replace hardcoded `MAX_TOTAL_FINDINGS = 30` with `settings.max_total_findings` (default 50)
- Replace hardcoded `MAX_PER_FILE = 5` with `settings.max_per_file_findings` (default 5)

### Step 12: Increase graph timeout (`src/graph_builder.py`)

- T5 timeout: 300s to 600s for very large PRs

## Build Order

```
Phase 1 (parallel — no dependencies):
  Step 1: config.py
  Step 2: file_filter.py
  Step 4: smart_diff.py

Phase 2 (depends on Phase 1):
  Step 3: review_job.py (filtering + batch mode)
  Step 5: vcs_tools.py (smart diff integration)
  Step 6: openai_runner.py (prompt update)
  Step 7: workspace_tools.py (raise limits)
  Step 12: graph_builder.py (timeout increase)

Phase 3 (depends on Phase 2):
  Step 8: batch_review_job.py
  Step 9: run_agent.py
  Step 10: review-pr-core.md
  Step 11: post_findings.py
```

Each phase is independently mergeable and improves the system incrementally.

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Graph build fails | Proceed without graph — each batch runs in diff-only mode |
| Single batch crashes | Catch exception, log error, merge findings from successful batches |
| All files filtered | Write clean findings.json with review_modes=["docs_chore"], zero findings |
| Batch has 1 file | Fine — ReviewJob handles single files normally |
| PR has 500+ files | Creates ~20 batches. Sequential execution. Future: parallelize with asyncio |
| Drill-in range matches no hunks | Return empty diff with helpful message |
| Duplicate findings across batches | Dedup by (file, line, title) in merge step |
| review_modes differ across batches | Union all detected modes |

## New Config/Env Vars

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SKIP_EXTENSIONS` | str | `.md,.json,.yaml,.yml,.xml,.lock,.png,.jpg,.jpeg,.gif,.svg,.ico,.csproj,.sln,.config,.env,.gitignore,.dockerignore,.editorconfig,.prettierrc,.eslintignore` | Extensions to filter out |
| `SMART_DIFF_THRESHOLD_KB` | int | `30` | Diff size (KB) above which summary is returned |
| `BATCH_SIZE` | int | `25` | Max code files per agent batch |
| `BATCH_MAX_TURNS` | int | `40` | Max turns per batch agent session |

## Testing Strategy

### Unit Tests

| Test File | Tests |
|-----------|-------|
| `tests/unit/test_file_filter.py` | parse_skip_extensions, filter keeps .py/.cs/.ts/.css, skips .md/.json/.yaml/.lock/.png, skips deleted files, empty list, all skipped |
| `tests/unit/test_smart_diff.py` | small diff unsummarized, large diff returns hunks, hunk counts correct, drill-in extraction, empty diff, binary diff, format readable |
| `tests/unit/test_batch_merge.py` | cr-id re-sequencing, dedup by file+line+title, usage sum, failed batch handling, round-robin split, single-batch shortcut |

### Integration Tests

| Test | Description |
|------|-------------|
| `test_batch_pipeline_large_pr` | Real PR with 30+ files: verify multi-batch, all files covered, sequential cr-ids, no duplicates, usage summed |
| `test_batch_pipeline_small_pr` | Small PR: verify single-session path, backward compatible |

## Success Criteria

- [ ] Non-code files (.md, .json, .yaml, .lock, images) are never shown to the agent
- [ ] `.css` files are kept (not filtered)
- [ ] Diffs under 30KB are returned in full (no 10KB truncation)
- [ ] Diffs over 30KB return a structured summary with hunk list
- [ ] Agent can drill into specific hunk ranges via start_line/end_line
- [ ] PRs with >25 code files are split into batches
- [ ] Each batch runs as an independent ReviewJob with shared graph
- [ ] Merged findings have sequential cr-ids (cr-001, cr-002, ...)
- [ ] Duplicate findings across batches are removed
- [ ] Token usage is summed across all batches
- [ ] Failed batches do not crash the pipeline
- [ ] Small PRs (<25 code files) follow the same path as today
- [ ] All new code has unit tests
