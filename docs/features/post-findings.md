# Feature: Post Findings Engine (`post_findings.py`)

## Purpose

`post_findings.py` is the Phase 2 engine. It reads `findings.json` written by the agent, filters and deduplicates findings, scores the PR, posts inline comments, and updates the PR summary. It is fully deterministic and can run without any LLM involvement.

## CLI

```bash
python src/post_findings.py \
    --findings .cr/findings.json \
    --pr 42 \
    --repo MyRepo \
    --project MyProject \
    --vcs ado \
    --commit-id <sha>
    [--dry-run]
```

`--dry-run` executes all read/filter/score/dedup logic but skips all VCS writes. Output JSON is still produced.

## Processing Pipeline

1. **Read and validate** — parse `findings.json` against `commands/findings-schema.json`. Reject if required fields are missing.
2. **Confidence filter** — drop findings below 0.7 confidence (default). Configurable via `.codereview.yml`.
3. **Cap** — max 30 findings total, max 5 per file. Priority order when over cap: critical → warning → suggestion → good.
4. **Fetch existing cr-ids** — read current PR threads (ADO: activity class; GitHub: `gh api`), extract `<!-- cr-id: xxx -->` markers.
5. **Dedup** — skip findings whose cr-id already appears in posted threads.
6. **Score** — instantiate `PRScorer`, apply mode multipliers from `findings.review_modes`, calculate star rating.
7. **Post inline comments** — ADO: direct activity class import; GitHub: `gh api` via `_gh_run_with_retry`.
8. **Fix verification** — if `fix_verifications[]` present, resolve threads for "fixed" items and generate before/after score comparison.
9. **Post/update summary** — formatted markdown via `markdown_formatter`, includes score breakdown and fix comparison if re-push.
10. **Output CI JSON** — structured JSON to stdout for pipeline gating:

```json
{
  "star_rating": 4,
  "findings_count": {"critical": 0, "warning": 3, "suggestion": 5},
  "findings_posted": true,
  "summary_posted": true,
  "cost_estimate": "$0.23"
}
```

## Gate Thresholds

If `/workspace/.codereview.yml` exists, `post_findings.py` reads:
- `min_star_rating` — CI fails if score falls below this value
- `fail_on_critical` — CI fails if any critical findings are unresolved

## ADO vs GitHub VCS Paths

| Operation | ADO | GitHub |
|-----------|-----|--------|
| Fetch existing threads | `FetchPRCommentsActivity` | `gh api repos/{repo}/pulls/{pr}/comments` |
| Post inline comment | `PostPRCommentActivity` | `gh api repos/{repo}/pulls/{pr}/comments` (JSON body) |
| Resolve thread | `PostFixReplyActivity` | `gh api` reply to comment |
| Post summary | `UpdateSummaryActivity` | `gh pr comment {pr} --body "..."` |

All GitHub calls go through `_gh_run_with_retry()` which applies exponential backoff on rate-limit responses (HTTP 429, "secondary rate limit" in stderr).

## Comment Format

Every posted inline comment includes:
- Severity icon + category header
- Finding body + suggestion
- `<!-- cr-id: {id} -->` HTML comment at the end (used for dedup on next run)

## Inline Comment — GitHub API Payload

```json
{
  "body": "<formatted comment> <!-- cr-id: abc12345 -->",
  "commit_id": "<PR head SHA>",
  "path": "src/foo.py",
  "line": 42,
  "side": "RIGHT"
}
```

`commit_id` is required by the GitHub API; passed via `--commit-id` CLI argument (set from `COMMIT_ID` env var in CI).
