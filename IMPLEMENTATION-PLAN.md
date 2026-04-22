# Implementation Plan: Code Reviewer v3.1 (Multi-Agent, Docker-Based, Two-Phase)

## Overview

A Docker-based code review product with two phases: (1) an LLM agent (Codex/Claude/Gemini) reads PR code and writes `findings.json`, (2) a deterministic Python script scores, deduplicates, and posts comments to ADO or GitHub. The old codebase at `C:\2_WorkSpace\BluB0X\BBX_AI - Doer\Pipelines\CodeReviewer\src\` provides battle-tested ADO activities, scoring, and models that get ported into the new structure.

**Target directory:** `C:\2_WorkSpace\Fleet_Projects\code-reviewer\`

## Requirements

- Two-phase architecture: agent writes `findings.json`, Python posts results
- Idempotent: cr-id dedup means re-runs never duplicate comments
- 6 review modes: standard, security, architecture, performance, migration, docs/chore
- ADO via `python vcs.py` (wrapping ported activities), GitHub via `gh` CLI
- Penalty-based scoring (ported from existing `pr_scorer.py`)
- **Local CLI (`cr`)**: installable via `pip install -e .`, run reviews from terminal
- Docker container with Codex CLI, Python, gh, ripgrep, repomix pre-installed
- CI integration for both ADO Pipelines and GitHub Actions

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
| `config.py` | `src/config.py` | Strip to ADO auth + penalty matrix only; remove OpenAI/AI settings (agent handles that now) |
| `utils/pr_scorer.py` | `src/pr_scorer.py` | Adapt input from `List[ReviewResult]` to `List[Finding]` (from findings.json) |
| `utils/score_comparison.py` | `src/score_comparison.py` | Adapt to work with findings.json data instead of FixVerificationResult |
| `utils/comment_exporter.py` | `src/utils/comment_exporter.py` | Low priority, port later |
| `utils/markdown_formatter.py` | `src/utils/markdown_formatter.py` | Reuse for summary formatting in post_findings.py |
| `utils/logger.py` | `src/utils/logger.py` | Port as-is, optional coloredlogs |
| `utils/url_sanitizer.py` | `src/utils/url_sanitizer.py` | Port as-is |

**NOT ported (replaced by agent + two-phase design):**

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

### New code

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `Dockerfile` | ~40 | Container image: Node 22 + Codex + Python + gh + rg + repomix |
| `entrypoint.sh` | ~50 | Env validation, Phase 1 agent dispatch, Phase 2 poster |
| `docker-compose.yml` | ~20 | Local dev convenience |
| `pyproject.toml` | ~30 | Python package definition + deps |
| `src/cli.py` | ~120 | Local CLI entry point (`cr review`, `cr score`, `cr post`) |
| `src/vcs.py` | ~250 | CLI wrapper: subcommands over ADO activities, JSON output |
| `src/post_findings.py` | ~350 | Phase 2: read findings.json, filter, score, dedup, post, summarize |
| `commands/review-pr-core.md` | ~200 | THE PRODUCT: agent instructions for reviewing a PR |
| `commands/review-mode-standard.md` | ~50 | Standard review checklist |
| `commands/review-mode-security.md` | ~60 | OWASP/secrets/auth checklist |
| `commands/review-mode-architecture.md` | ~50 | Coupling/contracts/breaking changes |
| `commands/review-mode-performance.md` | ~50 | Queries/memory/hot paths |
| `commands/review-mode-migration.md` | ~50 | DDL safety/rollback/data loss |
| `commands/scoring.md` | ~40 | Penalty matrix reference (for agent context) |
| `commands/findings-schema.json` | ~30 | JSON schema for findings.json |
| `commands/review-pr.claude.md` | ~10 | Claude skill wrapper |
| `templates/.codereview.md` | ~30 | Starter conventions template |
| `templates/.codereview.yml` | ~30 | Starter settings template |
| `templates/dismissed.jsonl` | ~5 | Empty dismissed-feedback file |
| `ci/azure-pipelines-pr-review.yml` | ~55 | ADO pipeline definition |
| `ci/github-review.yml` | ~40 | GitHub Actions definition |
| `AGENTS.md` | ~20 | Codex project instructions |
| `CLAUDE.md` | ~20 | Claude project instructions |
| `README.md` | ~80 | Usage docs |
| `tests/test_pr_scorer.py` | ~100 | Scorer unit tests |
| `tests/test_post_findings.py` | ~150 | Poster unit tests (mock VCS) |
| `tests/test_vcs_cli.py` | ~80 | VCS CLI unit tests |
| `tests/conftest.py` | ~40 | Shared fixtures |

---

## Implementation Steps

### Phase 1: Scaffold + Port Foundation

**Commit:** `scaffold: project structure + ported foundation`

**Goal:** Get the project structure standing with all ported code adapted to the new layout. Nothing runs end-to-end yet, but imports resolve and unit tests pass on ported modules.

**Step 1.1 — Project scaffold** (New files)
- Action: Create directory structure under `C:\2_WorkSpace\Fleet_Projects\code-reviewer\`
  ```
  code-reviewer/
  ├── src/
  │   ├── __init__.py
  │   ├── activities/
  │   │   └── __init__.py
  │   ├── models/
  │   │   └── __init__.py
  │   └── utils/
  │       └── __init__.py
  ├── commands/
  ├── templates/
  ├── ci/
  ├── tests/
  │   └── __init__.py
  └── ...
  ```
- Dependencies: None
- Risk: Low

**Step 1.2 — pyproject.toml** (`code-reviewer/pyproject.toml`)
- Action: Create pyproject.toml with:
  - `azure-devops>=7.1` (ADO SDK)
  - `pydantic>=2.0` and `pydantic-settings>=2.0` (config)
  - `msrest` (ADO auth)
  - Dev deps: `pytest`, `pytest-mock`
- Dependencies: Step 1.1
- Risk: Low

**Step 1.3 — Port utility modules** (4 files)
- Files: `src/utils/logger.py`, `src/utils/url_sanitizer.py`
- Action: Copy as-is. These have no project-specific imports.
- Dependencies: Step 1.1
- Risk: Low

**Step 1.4 — Port models** (`src/models/review_models.py`)
- Action: Copy from old `models/review_models.py`. Add new dataclasses:
  - `Finding` — matches a single finding in findings.json (cr_id, file, line, severity, category, confidence, title, body, suggestion, trace, line_range)
  - `FixVerification` — matches fix_verifications entry (cr_id, status, reason)
  - `FindingsFile` — top-level findings.json structure (pr_id, repo, project, vcs, review_modes, tier, agent, model, tool_calls, existing_cr_ids, findings, fix_verifications)
- Dependencies: Step 1.1
- Risk: Low

**Step 1.5 — Port and adapt config.py** (`src/config.py`)
- Action: Copy from old `config.py`. **Remove** all OpenAI/AI settings, context extraction settings, language support, parallel processing, and consolidation settings. **Keep**: ADO auth (PAT + system token + auto-detect), penalty matrix, star thresholds, confidence threshold, and review focus toggles. **Add**: `VCS` field (ado/github), `GH_TOKEN` optional field, `AZURE_DEVOPS_URL` (replacing org-derived URL).
- Dependencies: Step 1.2 (pydantic-settings)
- Risk: Medium — must not break activity imports. The old activities reference `self.settings.azure_devops_org` and `self.settings.azure_devops_url` (property). Ensure these remain.

**Step 1.6 — Port activities** (8 files)
- Files under `src/activities/`:
  - `base_activity.py` — copy, update import path for logger/url_sanitizer
  - `fetch_pr_details_activity.py` — copy, update imports
  - `fetch_pr_comments_activity.py` — copy, update imports, **add cr-id extraction**: parse `<!-- cr-id: xxx -->` from comment text and store in a new field
  - `post_pr_comment_activity.py` — copy, update imports, **modify `_format_review_comment`** to append `<!-- cr-id: {cr_id} -->` marker to comment body
  - `post_fix_reply_activity.py` — copy, update imports
  - `fetch_file_content_activity.py` — copy, update imports
  - `fetch_file_diff_activity.py` — copy, update imports
  - `update_summary_activity.py` — copy, update imports, update summary markers to match new format
- Action: Copy each file, fix import paths. Use `src/` as PYTHONPATH root so imports are like `from activities.base_activity import BaseActivity`.
- Dependencies: Steps 1.3, 1.4, 1.5
- Risk: Medium — import path changes could break. Verify with `python -c "from activities.fetch_pr_details_activity import FetchPRDetailsActivity"` after porting.

**Step 1.7 — Port and adapt pr_scorer.py** (`src/pr_scorer.py`)
- Action: Copy from old `utils/pr_scorer.py`. Adapt `calculate_pr_score` to accept `List[Finding]` instead of `List[ReviewResult]`. Add **mode multiplier application**: `apply_mode_multipliers(findings, review_modes)` that modifies penalty values before summing.
  - Security mode: security category × 2
  - Performance mode: performance × 2
  - Architecture mode: best_practices × 1.5
  - Migration mode: elevate all findings to minimum critical severity
- Dependencies: Step 1.4 (Finding model)
- Risk: Medium — the multiplier logic is new. Must unit test.

**Step 1.8 — Port and adapt score_comparison.py** (`src/score_comparison.py`)
- Action: Copy from old `utils/score_comparison.py`. Adapt `format_as_markdown` to work with data from findings.json `fix_verifications[]` instead of old `FixVerificationResult`.
- Dependencies: Step 1.4
- Risk: Low

---

### Phase 2: VCS CLI + Post Findings

**Commit:** `feat: vcs.py CLI + post_findings.py Phase 2 engine`

**Goal:** The two key new Python files that enable the two-phase architecture.

**Step 2.1 — Write vcs.py** (`src/vcs.py`)
- Action: Create CLI using `argparse` with subcommands:
  - `get-pr <pr_id> --repo --project` — wraps FetchPRDetailsActivity, outputs JSON
  - `list-threads <pr_id> --repo --project` — wraps FetchPRCommentsActivity, outputs JSON (including extracted cr-ids)
  - `post-comment <pr_id> --repo --project --file --line --body` — wraps PostPRCommentActivity
  - `resolve-thread <pr_id> --repo --project --thread-id --reply --status` — wraps PostFixReplyActivity
  - `get-file --repo --project --path --commit` — wraps FetchFileContentActivity
  - `post-summary <pr_id> --repo --project --body` — wraps UpdateSummaryActivity
- All subcommands output JSON to stdout. Errors go to stderr.
- Auth: reads from env vars, instantiates Settings.
- Dependencies: Phase 1 (all activities ported)
- Risk: Medium

**Step 2.2 — Write findings-schema.json** (`commands/findings-schema.json`)
- Action: Create JSON schema matching the FindingsFile dataclass from Step 1.4. This is the contract between Phase 1 and Phase 2.
- Dependencies: Step 1.4
- Risk: Low

**Step 2.3 — Write post_findings.py** (`src/post_findings.py`)
- Action: Create the Phase 2 engine (~350 lines). CLI script:
  ```
  python post_findings.py --findings .cr/findings.json --pr 42 --repo X --project Y --vcs ado [--dry-run]
  ```
  Logic:
  1. Read and validate `.cr/findings.json` (parse into FindingsFile)
  2. Filter findings below `min_confidence_score` (default 0.7)
  3. Cap: max 30 findings, 5 per file (prioritize by severity: critical > warning > suggestion > good)
  4. Fetch existing threads via `vcs.py list-threads` (ADO) or `gh api` (GitHub)
  5. Extract posted cr-ids from existing threads
  6. Diff: skip findings whose cr-id already exists in posted set
  7. Score: instantiate PRScorer, apply mode multipliers, calculate score
  8. **Post new inline comments** (only net-new findings):
     - ADO: import activities directly (faster than subprocess)
     - GitHub: call `gh api repos/{repo}/pulls/{pr}/comments` via subprocess
     - Format: severity icon + category + body + suggestion + `<!-- cr-id: {id} -->` marker
  9. **Fix verification** (if fix_verifications[] present):
     - ADO: call activities to resolve threads
     - GitHub: call `gh api` to reply + resolve
  10. **Post/update summary** with score + category breakdown + fix comparison (if re-push)
  11. **Output structured JSON to stdout** for CI gating:
     ```json
     {"star_rating": 4, "findings_count": {"critical": 0, "warning": 3, "suggestion": 5}, "findings_posted": true, "summary_posted": true, "cost_estimate": "$0.23"}
     ```
  12. If `--dry-run`: do steps 1-6 (read, filter, score), print what would be posted, but skip all VCS writes.
- Dependencies: Steps 1.7 (scorer), 1.8 (score_comparison), 2.1 (vcs.py), 2.2 (schema)
- Risk: **High** — most complex new file. VCS abstraction needs careful error handling. cr-id dedup is correctness-critical.

**Step 2.4 — Unit tests for Phase 2 engine**
- Files: `tests/conftest.py`, `tests/test_pr_scorer.py`, `tests/test_post_findings.py`, `tests/test_vcs_cli.py`
- Coverage:
  - Scorer: 0 findings = 5 stars, security critical = 5.0 penalty, mode multipliers double security penalties
  - Poster: findings below 0.7 confidence are dropped, cap at 30 total and 5 per file, cr-ids already posted are skipped
  - VCS CLI: `get-pr` subcommand returns valid JSON, `post-comment` invokes activity correctly
- Dependencies: Steps 2.1, 2.3
- Risk: Low

---

### Phase 3: Core Prompt + Docker

**Commit:** `feat: review-pr-core.md prompt + Docker image`

**Goal:** The agent instructions and the container that runs everything.

**Step 3.1 — Write review-pr-core.md** (`commands/review-pr-core.md`)
- Action: Write ~200-line agent prompt following the spec in architecture doc section 5.1. Key sections:
  - Step 1: Load project context (.codereview.md, .codereview.yml, AGENTS.md, etc.)
  - Step 2: Fetch PR data (VCS-specific: `python vcs.py` for ADO, `gh` for GitHub). Extract existing cr-ids.
  - Step 3: Detect review mode (from file paths + labels)
  - Step 4: Assess scale (T1-T5 tier assignment, repomix for T2+)
  - Step 5: Review each changed file (read, check intent markers, grep callers, git blame)
  - Step 6: Fix verification (re-push only: classify prior findings)
  - Step 7: Write `/workspace/.cr/findings.json`
  - Constraints: no posting, max 30 findings, 5 per file, 40 tool calls, confidence scores
- Dependencies: Step 2.2 (schema reference)
- Risk: **High** — prompt quality determines review quality. Will need iterative tuning.

**Step 3.2 — Write scoring.md** (`commands/scoring.md`)
- Action: Reference document for the agent explaining penalty matrix, severity levels, category definitions, confidence expectations.
- Risk: Low

**Step 3.3 — Write Dockerfile** (`Dockerfile`)
- Action: Create multi-layer Dockerfile:
  - Base: `node:22-slim`
  - System: python3, git, curl, jq, ripgrep
  - GitHub CLI: install gh
  - NPM: `@openai/codex`, `repomix`
  - Python venv: azure-devops SDK, pydantic
  - Copy: `commands/`, `src/`, `templates/`, `AGENTS.md`
  - Env: PATH includes venv, PYTHONPATH includes src
  - Workdir: `/workspace`
  - Entrypoint: `entrypoint.sh`
- Dependencies: Steps 1-2 (all Python code must be ready)
- Risk: Medium — Codex sandbox-in-Docker compatibility (open question #6). Test early.

**Step 3.4 — Write entrypoint.sh** (`entrypoint.sh`)
- Action: Two-phase orchestrator:
  - Validate required env vars
  - `mkdir -p /workspace/.cr`
  - Phase 1: dispatch to `codex exec`, `claude --print`, or `gemini -p` based on `$AGENT` env var
  - Verify `findings.json` was produced
  - Phase 2: `python /app/src/post_findings.py --findings /workspace/.cr/findings.json ...`
- Dependencies: Steps 2.3, 3.1, 3.3
- Risk: Low

**Step 3.5 — Write docker-compose.yml** (`docker-compose.yml`)
- Dependencies: Step 3.3
- Risk: Low

**Step 3.6 — Write AGENTS.md and CLAUDE.md**
- Dependencies: None
- Risk: Low

**Step 3.7 — Build and smoke test**
- Action: `docker build -t code-reviewer:local .` then `docker run --rm -e DRY_RUN=1 ...` against a real BluB0X ADO PR
- Verify: Agent produces findings.json, post_findings.py scores it, dry-run outputs structured JSON
- Dependencies: All of Phase 3
- Risk: Medium — first real integration test

---

### Phase 4: Review Modes + Conventions

**Commit:** `feat: review modes + .codereview templates`

**Step 4.1 — Write review-mode-standard.md** (`commands/review-mode-standard.md`)
- Checklist: correctness, patterns, test coverage, naming, error handling, edge cases

**Step 4.2 — Write review-mode-security.md** (`commands/review-mode-security.md`)
- Checklist: OWASP Top 10, injection, auth bypass, secrets in code, insecure defaults, dependency CVEs

**Step 4.3 — Write review-mode-migration.md** (`commands/review-mode-migration.md`)
- Checklist: data loss, rollback safety, destructive DDL, lock duration, idempotency

**Step 4.4 — Add mode auto-detection to review-pr-core.md**
- Action: Update the prompt's Step 3 with explicit detection rules from architecture doc section 3.2

**Step 4.5 — Add mode multiplier support to post_findings.py**
- Action: Ensure `post_findings.py` reads `review_modes` from findings.json and passes them to the scorer

**Step 4.6 — Write templates**
- Files: `templates/.codereview.md`, `templates/.codereview.yml`, `templates/dismissed.jsonl`

**Step 4.7 — Write intent marker handling**
- Action: Update review-pr-core.md Step 5 for `# cr: intentional`, `# cr: ignore-block start/end`, `# cr: ignore-next-line`

- Dependencies: Phase 3
- Risk: Low — mostly prompt content

---

### Phase 5: Fix Verification + Re-push

**Commit:** `feat: fix verification + score comparison on re-push`

**Step 5.1 — Update review-pr-core.md for re-push**
- Action: Expand Step 6 with explicit fix verification logic: detect existing cr-ids, classify each as fixed/still_present/not_relevant, write `fix_verifications[]` into findings.json

**Step 5.2 — Add fix verification to post_findings.py**
- Action: When `fix_verifications[]` is present:
  - For "fixed" items: resolve/close threads via activities (ADO) or gh (GitHub)
  - Generate score comparison markdown (before/after) using `ScoreComparisonService`
  - Include comparison in summary comment

**Step 5.3 — Add delta-only review logic to prompt**
- Action: Update prompt to explain that on re-push, the agent reviews only the git diff between old and new head commits

**Step 5.4 — Test re-push flow**
- Action: Unit test with mock findings.json containing fix_verifications

- Dependencies: Phases 3-4
- Risk: Medium — cr-id matching must be exact

---

### Phase 6: GitHub Integration

**Commit:** `feat: GitHub VCS path`

**Step 6.1 — Add GitHub path to post_findings.py**
- Action: When `--vcs github`:
  - Read existing comments: `gh api repos/{repo}/pulls/{pr}/comments`
  - Post inline comments: `gh api` with file, line, body, commit_id, side
  - Post summary: `gh pr comment {pr} --body "..."`
  - Reply to comment: `gh api repos/{repo}/pulls/comments/{id}/replies`
- All via `subprocess.run` calling `gh` CLI

**Step 6.2 — Write CI pipelines**
- Files: `ci/azure-pipelines-pr-review.yml`, `ci/github-review.yml`

**Step 6.3 — Write review-pr.claude.md** (`commands/review-pr.claude.md`)
- Action: 10-line Claude skill wrapper that loads review-pr-core.md

- Dependencies: Phase 3
- Risk: Medium — `gh` CLI output parsing, error handling

---

### Phase 7: Remaining Review Modes

**Commit:** `feat: architecture + performance review modes`

**Step 7.1 — Write review-mode-architecture.md** (`commands/review-mode-architecture.md`)
- Checklist: breaking changes, coupling, API contract changes, migration safety, backwards compat

**Step 7.2 — Write review-mode-performance.md** (`commands/review-mode-performance.md`)
- Checklist: N+1 queries, missing indexes, unbounded loops, memory allocation, caching

**Step 7.3 — Add mode stacking to prompt and scorer**
- Action: Multiple modes can be active simultaneously. Strictest multiplier per finding when modes overlap.

- Dependencies: Phase 4
- Risk: Low

---

### Phase 8: Claude + Gemini support

**Commit:** `feat: multi-agent support (Claude + Gemini)`

**Step 8.1 — Add Claude and Gemini CLIs to Dockerfile**
- Action: Optional installs (`claude`, `gemini`) in the Docker image
- Risk: Low — additive, doesn't change existing Codex path

**Step 8.2 — Write GEMINI.md** (`GEMINI.md`)
- Action: Gemini project instructions (equivalent of AGENTS.md and CLAUDE.md)

**Step 8.3 — Update entrypoint.sh AGENT switching**
- Action: `AGENT=codex|claude|gemini` env var dispatches to the right CLI. Already drafted.

**Step 8.4 — Test same PR across all three agents**
- Action: Compare findings.json quality. Same `post_findings.py` handles all.
- Dependencies: Phases 1-3
- Risk: Medium — different agents may produce different findings.json quality

---

### Phase 9: Container Registry + CI Optimization

**Commit:** `feat: container registry + image versioning`

**Step 9.1 — Push image to Azure Container Registry**
- Action: `docker push your-registry.azurecr.io/code-reviewer:latest`
- CI pulls pre-built image instead of building each run (~2 min saved)

**Step 9.2 — Add image versioning/tagging strategy**
- Action: Tag by git SHA + semver. CI pipelines reference pinned tags.
- Dependencies: Phase 3
- Risk: Low

---

### Phase 10: Scale (Repomix Tiers) + QoL

**Commit:** `feat: tier-based repomix + QoL improvements`

**Step 10.1 — Add tier assessment to prompt**
- Action: Explicit T1-T5 tier logic, repomix commands for T2-T4, T5 "too large" handling

**Step 10.2 — Add T4 directory chunking**
- Action: Classify directories by risk, deep-review high-risk, skim medium, skip low-risk

**Step 10.3 — Dismissed feedback learning**
- Action: Check `.codereview/dismissed.jsonl` for known false positives

**Step 10.4 — Cost footer in summary**
- Action: Include agent/model/tool_calls from findings.json in the summary footer

**Step 10.5 — Error reporting to PR**
- Action: If review fails, post an error comment on the PR (not just fail CI silently)

**Step 10.6 — README.md**

- Dependencies: Phase 3
- Risk: Low

---

### Phase 11: Local CLI + PyPI Package

**Commit:** `feat: cr CLI + publishable Python package`

**Goal:** Install locally via `pip install` and run reviews from the terminal without Docker. Same two-phase engine, no container required.

**Step 11.1 — Restructure to proper Python package**
- Action: Move `src/` contents into `code_reviewer/` package. Bundle `commands/` and `templates/` inside the package so they ship with `pip install`.
  ```
  Before:                          After:
  src/                             code_reviewer/
  ├── post_findings.py             ├── __init__.py
  ├── vcs.py                       ├── cli.py          ← NEW
  ├── pr_scorer.py                 ├── post_findings.py
  ├── activities/                  ├── vcs.py
  └── models/                      ├── pr_scorer.py
                                   ├── score_comparison.py
  commands/                        ├── config.py
  ├── review-pr-core.md            ├── activities/
  └── ...                          ├── models/
                                   ├── utils/
  templates/                       ├── commands/        ← bundled
  └── ...                          │   ├── review-pr-core.md
                                   │   └── ...
                                   └── templates/       ← bundled
                                       └── ...
  ```
- Update all internal imports from `from activities.X` to `from code_reviewer.activities.X`
- Update Dockerfile `COPY` and `PYTHONPATH` to match new layout
- Dependencies: Phases 1-3 (must work before restructuring)
- Risk: Medium — import paths change everywhere. Run full test suite after.

**Step 11.2 — Write cli.py** (`code_reviewer/cli.py`, ~120 lines)
- Action: Create CLI using argparse with subcommands:
  ```bash
  # Full review (Phase 1 agent + Phase 2 poster)
  cr review 42 --repo MyRepo --project MyProject --vcs ado
  cr review 42 --repo MyRepo --project MyProject --vcs ado --dry-run
  cr review 42 --repo MyRepo --project MyProject --vcs ado --agent claude

  # Score only (no LLM, no VCS — just reads findings.json and scores)
  cr score .cr/findings.json

  # Post only (Phase 2 — reads findings.json, posts to PR)
  cr post .cr/findings.json --pr 42 --repo MyRepo --project MyProject --vcs ado
  cr post .cr/findings.json --pr 42 --repo MyRepo --vcs ado --dry-run

  # Init (copy starter templates to current repo)
  cr init
  ```
- `cr review` dispatches Phase 1 (agent) then Phase 2 (poster) — same as entrypoint.sh but in Python
- `cr score` is useful for testing the scoring engine against hand-crafted findings
- `cr post` lets you run Phase 2 separately (e.g., agent ran in CI, you want to post locally)
- `cr init` copies `.codereview.md` and `.codereview.yml` templates into the current directory
- At startup, check for required tools (git, at least one agent CLI) and warn if missing
- Dependencies: Step 11.1
- Risk: Low

**Step 11.3 — Update pyproject.toml for publishing**
```toml
[project]
name = "code-reviewer"
version = "0.1.0"
description = "AI code review for Azure DevOps and GitHub PRs"
requires-python = ">=3.11"
dependencies = [
    "azure-devops>=7.1,<8.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "msrest",
]

[project.scripts]
cr = "code_reviewer.cli:main"

[project.optional-dependencies]
dev = ["pytest", "pytest-mock"]
github = []  # gh CLI is external, no Python dep

[tool.setuptools.package-data]
code_reviewer = ["commands/*.md", "commands/*.json", "templates/*"]
```
- Dependencies: Step 11.1
- Risk: Low

**Step 11.4 — Test local install**
- Action:
  ```bash
  pip install -e .
  cr --help
  cr review 42 --repo MyRepo --project MyProject --vcs ado --dry-run
  ```
- Verify: `cr` is on PATH, finds bundled commands/*.md, runs Phase 1 + Phase 2
- Dependencies: Steps 9.1-9.3
- Risk: Low

**Step 11.5 — Publish to Azure Artifacts (private PyPI)**
- Action: `python -m build && twine upload --repository azure dist/*`
- BluB0X team installs via: `pip install code-reviewer --index-url https://pkgs.dev.azure.com/...`
- Dependencies: Step 11.4
- Risk: Low

**Step 11.6 — (Future) Publish to public PyPI**
- Action: `twine upload dist/*` to pypi.org
- Anyone installs via: `pip install code-reviewer`
- Decision: only when ready for open source
- Dependencies: Step 11.5

**Prerequisites for local use (user must have installed):**

| Tool | Required? | Install |
|------|-----------|---------|
| Python 3.11+ | Yes | System package manager |
| git | Yes | System package manager |
| At least one agent CLI | Yes (for `cr review`) | `npm i -g @openai/codex` or Claude Code or Gemini CLI |
| gh CLI | For GitHub PRs | `brew install gh` / `winget install GitHub.cli` |
| ripgrep | Recommended | `brew install ripgrep` / `winget install BurntSushi.ripgrep` |
| repomix | For T2+ large PRs | `npm i -g repomix` |

`cr score` and `cr post` need only Python — no agent CLI required.

---

## Gaps, Risks, and Ambiguities

### Gaps in the Architecture Doc

1. **cr-id generation — agents can't compute SHA1.** Resolution: The agent sets `cr_id: null` in findings.json. `post_findings.py` computes it deterministically: `hashlib.sha1(f"{file}:{line}:{category}".encode()).hexdigest()[:8]` — 8-char hex, stable across agents. This avoids requiring LLMs to compute hashes.

2. **How post_findings.py invokes vcs.py is not specified.** Resolution: Import activities directly for ADO (faster, no subprocess). `vcs.py` CLI is for the agent; `post_findings.py` uses activity classes directly.

3. **Error handling for partial findings.json.** Resolution: Validate structure on read. If it parses as valid JSON with required fields, process whatever findings are present.

4. **`line_range` posting for ADO.** Resolution: When `line_range` is present, set `right_file_start.line` to start and `right_file_end.line` to end.

5. **GitHub comment resolution.** GitHub does not have native thread resolution like ADO. Resolution: Reply with "Fixed" and optionally minimize via GraphQL. Document this limitation.

6. **`.codereview.yml` parsing in post_findings.py.** Resolution: `post_findings.py` reads `.codereview.yml` from `/workspace/` if it exists, extracts gate thresholds.

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Codex sandbox conflicts with Docker | High | Test early in Phase 3. If broken, use `--sandbox=none` and rely on container. |
| Prompt quality determines review quality | High | Iterative tuning against real PRs. Compare across agents. |
| cr-id stability across file renames | Medium | cr-id uses file path. Renames break matching. Accept for v1. |
| Azure DevOps SDK version compatibility | Medium | Pin `azure-devops>=7.1,<8.0`. |
| Agent exceeds tool call cap | Medium | Phase 2 still works. Monitor via `tool_calls` in findings.json. |

### Open Decisions

1. **Container registry:** ACR or GHCR? (Needed Phase 6+)
2. **OPENAI_API_KEY management:** Separate key with spend caps? (Before CI integration)
3. **Read-only mount:** Agent needs to write `.cr/findings.json`. Mount read-write, agent only writes to `.cr/`.

---

## Testing Strategy

- **Unit tests (Phase 2):** pr_scorer.py, post_findings.py filtering/dedup, vcs.py CLI parsing. All mock VCS. Run via `pytest`.
- **Integration test (Phase 3):** Docker build + dry-run against real ADO PR.
- **E2E test (Phase 5+):** Full review cycle: agent reviews, poster posts, verify comments appear. Then push fix, re-run, verify fix verification.
- **Cost monitoring:** Track tool_calls and token usage from findings.json.

**Cost awareness:** Integration/E2E tests hit real LLM APIs. Run sparingly. Unit tests are free. `--dry-run` skips all VCS writes.

## Commit Strategy

One commit per phase (8 phases = 8 commits max on feature branch). Each commit leaves the project in a working state:
- Phase 1: imports resolve, unit tests pass on ported modules
- Phase 2: `python vcs.py --help` works, `python post_findings.py --dry-run` works with sample data
- Phase 3: `docker build` succeeds, dry-run against real PR works
- Phases 4-8: incremental feature additions, each independently testable

## Success Criteria

- [ ] `docker build -t code-reviewer:local .` succeeds
- [ ] Dry-run against a real BluB0X ADO PR produces valid findings.json with scored output
- [ ] Re-run of same PR posts zero duplicate comments (cr-id dedup works)
- [ ] Penalty scoring matches old codebase behavior for equivalent findings
- [ ] Security mode doubles security penalties
- [ ] Fix verification resolves threads for fixed findings
- [ ] GitHub path works via `gh` CLI
- [ ] All unit tests pass
- [ ] Summary comment includes score breakdown, category stars, and quality level
