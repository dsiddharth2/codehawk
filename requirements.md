# Code Reviewer v3.1 — Requirements

## Overview

A Docker-based code review product with two phases: (1) an LLM agent (Codex/Claude/Gemini) reads PR code and writes `findings.json`, (2) a deterministic Python script scores, deduplicates, and posts comments to ADO or GitHub. The old codebase at `C:\2_WorkSpace\BluB0X\BBX_AI - Doer\Pipelines\CodeReviewer\src\` provides battle-tested ADO activities, scoring, and models that get ported into the new structure.

**Target directory:** `C:\2_WorkSpace\codehawk\` (doer) / `C:\2_WorkSpace\codehawk_reviewer\` (reviewer)

## Functional Requirements

1. **Two-phase architecture:** agent writes `findings.json`, Python posts results
2. **Idempotent:** cr-id dedup means re-runs never duplicate comments
3. **6 review modes:** standard, security, architecture, performance, migration, docs/chore
4. **ADO via `python vcs.py`** (wrapping ported activities), **GitHub via `gh` CLI**
5. **Penalty-based scoring** (ported from existing `pr_scorer.py`)
6. **Local CLI (`cr`):** installable via `pip install -e .`, run reviews from terminal
7. **Docker container** with Codex CLI, Python, gh, ripgrep, repomix pre-installed
8. **CI integration** for both ADO Pipelines and GitHub Actions

## Architecture: What Gets Ported vs What Is New

### Ported from old codebase (copy + adapt)

| Old file | New location | Adaptation needed |
|----------|-------------|-------------------|
| `activities/base_activity.py` | `src/activities/base_activity.py` | Simplify logger import to use stdlib |
| `activities/fetch_pr_details_activity.py` | `src/activities/fetch_pr_details_activity.py` | Update imports to new package layout |
| `activities/fetch_pr_comments_activity.py` | `src/activities/fetch_pr_comments_activity.py` | Add cr-id extraction from `<!-- cr-id: xxx -->` markers |
| `activities/post_pr_comment_activity.py` | `src/activities/post_pr_comment_activity.py` | Add cr-id marker injection into comment body |
| `activities/post_fix_reply_activity.py` | `src/activities/post_fix_reply_activity.py` | Minor import updates |
| `activities/fetch_file_content_activity.py` | `src/activities/fetch_file_content_activity.py` | Minor import updates |
| `activities/fetch_file_diff_activity.py` | `src/activities/fetch_file_diff_activity.py` | Minor import updates |
| `activities/update_summary_activity.py` | `src/activities/update_summary_activity.py` | Update summary markers to match new format |
| `models/review_models.py` | `src/models/review_models.py` | Add Finding/FindingsFile dataclasses for JSON schema |
| `config.py` | `src/config.py` | Strip to ADO auth + penalty matrix only; remove OpenAI/AI settings |
| `utils/pr_scorer.py` | `src/pr_scorer.py` | Adapt input from `List[ReviewResult]` to `List[Finding]` |
| `utils/score_comparison.py` | `src/score_comparison.py` | Adapt to work with findings.json data |
| `utils/comment_exporter.py` | `src/utils/comment_exporter.py` | Low priority, port later |
| `utils/markdown_formatter.py` | `src/utils/markdown_formatter.py` | Reuse for summary formatting |
| `utils/logger.py` | `src/utils/logger.py` | Port as-is, optional coloredlogs |
| `utils/url_sanitizer.py` | `src/utils/url_sanitizer.py` | Port as-is |

### NOT ported (replaced by agent + two-phase design)

| Old file | Why not ported |
|----------|---------------|
| `activities/review_code_activity.py` | Replaced by the LLM agent |
| `activities/review_file_activity.py` | Replaced by the agent's file-by-file loop |
| `jobs/*.py` (all 4 files) | Replaced by entrypoint.sh / cli.py |
| `main.py` | Replaced by entrypoint.sh / cli.py |
| `utils/openai_api_client.py` | Replaced by agent CLI |
| `utils/prompt_builder.py`, `prompt_loader.py` | Replaced by commands/*.md |
| `utils/response_parser.py` | Replaced by agent writing findings.json |
| `utils/comment_consolidator.py`, `comment_merger.py`, `comment_matcher.py` | Replaced by cr-id dedup |
| `utils/language_detector.py` | Agent detects natively |
| `prompts/` (all .txt files) | Replaced by review-mode-*.md |

## Key Design Decisions

1. **cr-id generation:** The agent sets `cr_id: null` in findings.json. `post_findings.py` computes it deterministically: `hashlib.sha1(f"{file}:{line}:{category}".encode()).hexdigest()[:8]`
2. **VCS abstraction:** Import activities directly for ADO (faster). `vcs.py` CLI is for the agent; `post_findings.py` uses activity classes directly.
3. **Error handling:** Validate structure on read. If it parses as valid JSON with required fields, process whatever findings are present.
4. **`line_range` posting for ADO:** When `line_range` is present, set `right_file_start.line` to start and `right_file_end.line` to end.
5. **GitHub comment resolution:** Reply with "Fixed" and optionally minimize via GraphQL (no native thread resolution like ADO).

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Codex sandbox conflicts with Docker | High | Test early in Phase 3. If broken, use `--sandbox=none` and rely on container. |
| Prompt quality determines review quality | High | Iterative tuning against real PRs. Compare across agents. |
| cr-id stability across file renames | Medium | cr-id uses file path. Renames break matching. Accept for v1. |
| Azure DevOps SDK version compatibility | Medium | Pin `azure-devops>=7.1,<8.0`. |
| Agent exceeds tool call cap | Medium | Phase 2 still works. Monitor via `tool_calls` in findings.json. |

## Testing Strategy

- **Unit tests:** pr_scorer.py, post_findings.py filtering/dedup, vcs.py CLI parsing. All mock VCS. Run via `pytest`.
- **Integration test:** Docker build + dry-run against real ADO PR.
- **E2E test:** Full review cycle: agent reviews, poster posts, verify comments appear.
- **Cost awareness:** Integration/E2E tests hit real LLM APIs. Run sparingly. Unit tests are free. `--dry-run` skips all VCS writes.

## Commit Strategy

One commit per phase. Each commit leaves the project in a working state.

## Source Material

Full implementation plan with step-by-step details: `IMPLEMENTATION-PLAN.md` (in the repo root after send)
Old codebase location: `C:\2_WorkSpace\BluB0X\BBX_AI - Doer\Pipelines\CodeReviewer\src\`
