# Feature: CI Integration

## Azure DevOps Pipeline (`ci/azure-pipelines-pr-review.yml`)

Triggers on any PR branch. Uses two ADO tasks:
- `Docker@2` — pulls the codehawk Docker image
- `AzureCLI@2` — runs the container with PR-specific env vars

**Environment variables passed to container:**

| Variable | Source |
|----------|--------|
| `PR_ID` | `$(System.PullRequest.PullRequestId)` |
| `REPO` | `$(Build.Repository.Name)` |
| `VCS` | `ado` (hardcoded) |
| `AGENT` | Pipeline variable |
| `ADO_TOKEN` | Secret variable |
| `ADO_ORGANIZATION` | Pipeline variable |
| `ADO_PROJECT` | Pipeline variable |

**Workspace mount:** `-v "$(Build.SourcesDirectory):/workspace"` — the agent reads PR files from here and writes `.cr/findings.json` here.

**Artifact publish:** `PublishBuildArtifacts@1` with `condition: always()` — publishes `.cr/` directory even on review failure, so findings are accessible for debugging.

## GitHub Actions Workflow (`ci/github-review.yml`)

Triggers on `pull_request` events: opened, synchronize, reopened.

**Permissions:** `pull-requests: write`, `contents: read` — minimal required set.

**Environment variables passed to container:**

| Variable | Source |
|----------|--------|
| `PR_ID` | `${{ github.event.pull_request.number }}` |
| `REPO` | `${{ github.repository }}` |
| `VCS` | `github` (hardcoded) |
| `AGENT` | `codex` (default) |
| `GH_TOKEN` | `${{ secrets.GITHUB_TOKEN }}` |
| `COMMIT_ID` | `${{ github.event.pull_request.head.sha }}` |
| `OPENAI_API_KEY` | `${{ secrets.OPENAI_API_KEY }}` |

**`COMMIT_ID` is required** by the GitHub API when posting inline PR review comments (field `commit_id`). `entrypoint.sh` forwards it to `post_findings.py` via `--commit-id "${COMMIT_ID:-}"`.

**Artifact upload:** `upload-artifact@v4` with `if-no-files-found: ignore` — robust for dry-run cases.

## Gate Output

Both pipelines consume the structured JSON output from `post_findings.py` (written to stdout) to determine pass/fail:

```json
{
  "star_rating": 4,
  "findings_count": {"critical": 0, "warning": 3, "suggestion": 5},
  "findings_posted": true,
  "summary_posted": true
}
```

Gate thresholds are read from `/workspace/.codereview.yml`. A project without this file gets permissive defaults (no hard fail).

## Claude Skill Wrapper (`commands/review-pr.claude.md`)

A 10-line Claude Code skill wrapper that loads `commands/review-pr-core.md` as its primary directive. Lists available tools (`python vcs.py`, `gh`, `rg`, `git`, `python src/post_findings.py`) and restates hard constraints (max 30 findings, max 5 per file, max 40 tool calls). Used when invoking the review from Claude Code directly rather than from CI.
