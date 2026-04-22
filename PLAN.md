# Code Reviewer v3.1 — Implementation Plan

**Project:** codehawk
**Branch:** `feat/code-reviewer-v3`
**Base:** `main`
**Sprint 1 scope:** Phases 1–6 (Scaffold → GitHub Integration) — delivers working E2E product
**Source material:** `requirements.md`, `IMPLEMENTATION-PLAN.md`
**Old codebase:** `C:\2_WorkSpace\BluB0X\BBX_AI - Doer\Pipelines\CodeReviewer\src\`
**Target:** `C:\2_WorkSpace\codehawk\`

---

## Phase 1: Scaffold + Port Foundation

**Goal:** Project structure standing with all ported code adapted to new layout. Imports resolve and unit tests pass on ported modules.

#### Task 1: Project scaffold + pyproject.toml + utility modules
- **Change:** Create directory structure (`src/`, `src/activities/`, `src/models/`, `src/utils/`, `commands/`, `templates/`, `ci/`, `tests/` with `__init__.py` files). Create `pyproject.toml` with deps (`azure-devops>=7.1,<8.0`, `pydantic>=2.0`, `pydantic-settings>=2.0`, `msrest`, dev: `pytest`, `pytest-mock`). Port `utils/logger.py` and `utils/url_sanitizer.py` as-is from old codebase. Port `utils/markdown_formatter.py` (needed by `post_findings.py` for summary formatting).
- **Files:** `src/__init__.py`, `src/activities/__init__.py`, `src/models/__init__.py`, `src/utils/__init__.py`, `commands/` (dir), `templates/` (dir), `ci/` (dir), `tests/__init__.py`, `pyproject.toml`, `src/utils/logger.py`, `src/utils/url_sanitizer.py`, `src/utils/markdown_formatter.py`
- **Tier:** cheap
- **Done when:** `python -c "import src"` works from project root; `pyproject.toml` is valid; logger, url_sanitizer, and markdown_formatter modules exist with correct content
- **Blockers:** None

#### Task 2: Port models + config
- **Change:** Port `models/review_models.py` from old codebase, add new `Finding`, `FixVerification`, and `FindingsFile` dataclasses matching findings.json schema. Port `config.py` — strip OpenAI/AI settings, keep ADO auth + penalty matrix + star thresholds, add `VCS` field (ado/github) and `GH_TOKEN`.
- **Files:** `src/models/review_models.py`, `src/config.py`
- **Tier:** standard
- **Done when:** `python -c "from models.review_models import Finding, FindingsFile"` resolves (with PYTHONPATH=src); `Finding` and `FindingsFile` dataclasses importable with all required fields; config loads without OpenAI fields; `VCS` and `GH_TOKEN` fields present
- **Blockers:** Task 1 (directory structure + deps)

#### Task 3: Port activities
- **Change:** Port all 8 activity files under `src/activities/`, updating import paths to new layout. Files: `base_activity.py` (update logger/url_sanitizer imports), `fetch_pr_details_activity.py` (update imports), `fetch_pr_comments_activity.py` (update imports + add cr-id extraction: parse `<!-- cr-id: xxx -->` from comment text), `post_pr_comment_activity.py` (update imports + add cr-id marker injection: append `<!-- cr-id: {cr_id} -->` to comment body), `post_fix_reply_activity.py`, `fetch_file_content_activity.py`, `fetch_file_diff_activity.py` (minor import updates), `update_summary_activity.py` (update imports + update summary markers to new format).
- **Files:** `src/activities/base_activity.py`, `src/activities/fetch_pr_details_activity.py`, `src/activities/fetch_pr_comments_activity.py`, `src/activities/post_pr_comment_activity.py`, `src/activities/post_fix_reply_activity.py`, `src/activities/fetch_file_content_activity.py`, `src/activities/fetch_file_diff_activity.py`, `src/activities/update_summary_activity.py`
- **Tier:** standard
- **Done when:** `python -c "from activities.fetch_pr_details_activity import FetchPRDetailsActivity"` resolves (with PYTHONPATH=src); all 8 activity files import without errors; cr-id extraction logic in fetch_pr_comments returns cr-ids from `<!-- cr-id: xxx -->` markers; cr-id injection in post_pr_comment appends marker to comment body
- **Blockers:** Task 2 (models + config must exist for activity imports)

#### Task 4: Port pr_scorer + score_comparison
- **Change:** Port `utils/pr_scorer.py` → `src/pr_scorer.py`. Adapt `calculate_pr_score` to accept `List[Finding]` instead of `List[ReviewResult]`. Add `apply_mode_multipliers(findings, review_modes)` — security ×2, performance ×2, architecture ×1.5, migration elevates to critical. Port `utils/score_comparison.py` → `src/score_comparison.py`, adapt to work with `FindingsFile.fix_verifications[]`.
- **Files:** `src/pr_scorer.py`, `src/score_comparison.py`
- **Tier:** standard
- **Done when:** `pr_scorer.py` importable, mode multiplier logic implemented; `score_comparison.py` works with new data structures
- **Blockers:** Task 2 (Finding model)

#### VERIFY: Phase 1 — Scaffold + Port Foundation
- Run `pytest` (any existing tests), verify all imports resolve with `PYTHONPATH=src`
- Confirm: ported modules load, config strips AI settings, cr-id extraction/injection in activities
- Report: tests passing, any regressions, any issues found

---

## Phase 2: VCS CLI + Post Findings

**Goal:** The two key new Python files that enable the two-phase architecture. `post_findings.py` is the highest-risk new file — front-loaded here.

#### Task 6: Write vcs.py CLI
- **Change:** Create `src/vcs.py` with argparse CLI. Subcommands: `get-pr`, `list-threads`, `post-comment`, `resolve-thread`, `get-file`, `post-summary`. Each wraps the corresponding ported activity. All output JSON to stdout, errors to stderr. Auth from env vars via Settings.
- **Files:** `src/vcs.py`
- **Tier:** standard
- **Done when:** `python src/vcs.py --help` shows all subcommands; `python src/vcs.py get-pr --help` shows correct args; code imports activities correctly
- **Blockers:** Phase 1 (all activities ported)

#### Task 7: Write findings-schema.json + post_findings.py
- **Change:** Create `commands/findings-schema.json` matching FindingsFile dataclass (contract between Phase 1 and Phase 2). Create `src/post_findings.py` (~350 lines) — the Phase 2 engine. Logic: (1) read and validate findings.json against schema (reject if required fields missing — mitigates schema drift risk R11), (2) filter findings below min_confidence 0.7, (3) cap 30 findings / 5 per file by severity, (4) fetch existing threads + extract posted cr-ids, (5) skip already-posted cr-ids, (6) score via PRScorer with mode multipliers, (7) post new inline comments (ADO: direct activity import, GitHub: `gh api`), (8) handle fix_verifications if present, (9) read `.codereview.yml` from workspace if present — extract gate thresholds (min_star_rating, fail_on_critical) and apply them to the CI gating output, (10) post/update summary (use `markdown_formatter` for summary formatting), (11) output structured JSON for CI gating. Support `--dry-run` (read/filter/score only, skip VCS writes).
- **Files:** `commands/findings-schema.json`, `src/post_findings.py`
- **Tier:** premium
- **Done when:** `python src/post_findings.py --help` works; `--dry-run` with a sample findings.json produces scored output JSON; cr-id dedup logic implemented; cap/filter logic implemented; `.codereview.yml` gate thresholds are read and applied when file exists
- **Blockers:** Tasks 4 (scorer), 6 (vcs.py)

#### Task 8: Unit tests for Phase 2
- **Change:** Create `tests/conftest.py` (shared fixtures: sample findings.json, mock activities). Create `tests/test_pr_scorer.py` — test: 0 findings = 5 stars, security critical = 5.0 penalty, mode multipliers double security. Create `tests/test_post_findings.py` — test: confidence filter drops <0.7, cap at 30/5-per-file, cr-id dedup skips posted, `.codereview.yml` gate thresholds applied. Create `tests/test_vcs_cli.py` — test: subcommand parsing, activity invocation.
- **Files:** `tests/conftest.py`, `tests/test_pr_scorer.py`, `tests/test_post_findings.py`, `tests/test_vcs_cli.py`
- **Tier:** standard
- **Done when:** `pytest tests/` passes; scorer, poster, and VCS CLI all have coverage for core logic
- **Blockers:** Tasks 6, 7

#### VERIFY: Phase 2 — VCS CLI + Post Findings
- Run full test suite: `pytest tests/ -v`
- Verify: `python src/vcs.py --help` works; `python src/post_findings.py --dry-run` with sample data produces valid output
- Report: tests passing, any regressions, any issues found

---

## Phase 3: Core Prompt + Docker

**Goal:** The agent instructions and the container that runs everything. Both are high-risk items that need early validation.

#### Task 10: Write review-pr-core.md + scoring.md
- **Change:** Create `commands/review-pr-core.md` (~200 lines) — THE PRODUCT. Agent instructions with these required sections: (1) Load project context — read `.codereview.md`, `.codereview.yml`, `AGENTS.md`; (2) Fetch PR data — VCS-conditional blocks: ADO path uses `python vcs.py get-pr`, GitHub path uses `gh pr view --json`; (3) Detect review mode from file paths + labels; (4) Assess scale T1-T5 tier assignment; (5) Review each changed file — read, check intent markers, grep callers, git blame; (6) Fix verification on re-push — classify prior findings; (7) Write `/workspace/.cr/findings.json`. Required constraints that must appear verbatim: "max 30 findings", "max 5 per file", "max 40 tool calls", confidence scores 0.0-1.0. Must reference `commands/findings-schema.json` by path. Must include VCS-conditional blocks for both ADO and GitHub throughout. Create `commands/scoring.md` — penalty matrix reference, severity levels, category definitions, confidence expectations.
- **Files:** `commands/review-pr-core.md`, `commands/scoring.md`
- **Tier:** premium
- **Done when:** Prompt contains all 7 numbered steps; includes "max 30 findings", "max 5 per file", "max 40 tool calls" constraints verbatim; references `commands/findings-schema.json` by path; has VCS-conditional blocks (ADO vs GitHub) in Steps 2, 5, and 6; scoring.md has complete penalty matrix with all severity levels and categories
- **Blockers:** Task 7 (schema reference)

#### Task 11: Dockerfile + entrypoint.sh + docker-compose
- **Change:** Create `Dockerfile` (~40 lines): base node:22-slim, install python3/git/curl/jq/ripgrep, gh CLI, npm install @openai/codex + repomix, python venv with azure-devops/pydantic, copy commands/src/templates/AGENTS.md, set PATH/PYTHONPATH, workdir /workspace. Create `entrypoint.sh` (~50 lines): validate env vars, mkdir .cr, Phase 1 dispatch (codex/claude/gemini based on $AGENT), verify findings.json produced, Phase 2 run post_findings.py. Create `docker-compose.yml` for local dev.
- **Files:** `Dockerfile`, `entrypoint.sh`, `docker-compose.yml`
- **Tier:** standard
- **Done when:** `docker build -t codehawk:local .` succeeds; `entrypoint.sh` is executable with correct Phase 1/2 dispatch logic
- **Blockers:** Phase 2 (all Python code ready)

#### Task 12: Project instructions + smoke test
- **Change:** Create `AGENTS.md` — Codex project instructions: explain two-phase architecture, instruct agent to read `commands/review-pr-core.md` as its primary directive, list available tools (`python vcs.py`, `gh`, `rg`, `repomix`), specify output location (`/workspace/.cr/findings.json`), include constraint reminders (40 tool calls, no posting). Create project-level `CLAUDE.md` — same content adapted for Claude: reference `commands/review-pr-core.md`, list tools, output location, constraints. Run smoke tests: (1) `docker build -t codehawk:local .` must succeed (hard gate), (2) create `tests/fixtures/sample_findings.json` with representative data, run `docker run --rm` with sample findings.json to verify post_findings.py dry-run produces structured JSON (hard gate), (3) if a live BluB0X ADO PR is available, run full dry-run against it (stretch goal — document result either way).
- **Files:** `AGENTS.md`, project `CLAUDE.md` (distinct from agent context CLAUDE.md), `tests/fixtures/sample_findings.json`
- **Tier:** standard
- **Done when:** (1) `docker build` succeeds (hard gate); (2) dry-run with `tests/fixtures/sample_findings.json` inside container produces valid structured JSON output (hard gate); (3) AGENTS.md contains: two-phase explanation, path to review-pr-core.md, tool list, output location, constraint reminders; CLAUDE.md contains equivalent content for Claude
- **Blockers:** Task 11

#### VERIFY: Phase 3 — Core Prompt + Docker
- Run full test suite: `pytest tests/ -v`
- Verify: `docker build` succeeds; dry-run with sample findings.json works end-to-end inside container
- **Critical checkpoint:** Codex-in-Docker compatibility. If sandbox conflicts, document workaround (`--sandbox=none`).
- Report: tests passing, Docker build status, image size (track for R10), any regressions, any issues found

---

## Phase 4: Review Modes + Conventions

**Goal:** All Sprint 1 review mode prompts, auto-detection, templates, and intent markers.

#### Task 14: Write review mode prompts
- **Change:** Create `commands/review-mode-standard.md` (correctness, patterns, test coverage, naming, error handling, edge cases). Create `commands/review-mode-security.md` (OWASP Top 10, injection, auth bypass, secrets, insecure defaults, dependency CVEs). Create `commands/review-mode-migration.md` (data loss, rollback safety, destructive DDL, lock duration, idempotency). Create `commands/review-mode-docs-chore.md` (doc accuracy, changelog completeness, config correctness, no functional logic — light-touch review that skips deep code analysis).
- **Files:** `commands/review-mode-standard.md`, `commands/review-mode-security.md`, `commands/review-mode-migration.md`, `commands/review-mode-docs-chore.md`
- **Tier:** standard
- **Done when:** Each mode file has a complete checklist; files follow consistent format; checklists cover all items from architecture doc; docs/chore mode specifies light-touch review behavior
- **Blockers:** Task 10 (core prompt exists to reference modes)

#### Task 15: Mode auto-detection + multiplier wiring + templates
- **Change:** Update `commands/review-pr-core.md` Step 3 with explicit mode detection rules from architecture doc (file path patterns, PR labels, etc.) — include docs/chore detection (e.g., only .md/.yml/.json files changed, PR label "docs" or "chore"). Ensure `post_findings.py` reads `review_modes` from findings.json and passes to scorer's `apply_mode_multipliers`. Create `templates/.codereview.md` (starter conventions), `templates/.codereview.yml` (starter settings with gate thresholds), `templates/dismissed.jsonl` (empty).
- **Files:** `commands/review-pr-core.md` (update), `src/post_findings.py` (update), `templates/.codereview.md`, `templates/.codereview.yml`, `templates/dismissed.jsonl`
- **Tier:** standard
- **Done when:** Prompt has detection rules for all 6 modes (including docs/chore); post_findings.py passes review_modes to scorer; templates exist with reasonable defaults
- **Blockers:** Tasks 7 (post_findings), 10 (core prompt), 14 (mode prompts)

#### Task 16: Intent marker handling
- **Change:** Update `commands/review-pr-core.md` Step 5 to handle intent markers: `# cr: intentional` (skip this line), `# cr: ignore-block start/end` (skip block), `# cr: ignore-next-line` (skip next line). Agent must check for markers before flagging findings.
- **Files:** `commands/review-pr-core.md` (update)
- **Tier:** cheap
- **Done when:** Prompt documents all three marker types with examples; agent instructions clearly state to skip marked code
- **Blockers:** Task 10 (core prompt)

#### VERIFY: Phase 4 — Review Modes + Conventions
- Run full test suite: `pytest tests/ -v`
- Verify: all mode files exist (including docs/chore) and are well-formed; templates exist; intent markers documented in prompt
- Report: tests passing, any regressions, any issues found

---

## Phase 5: Fix Verification + Re-push

**Goal:** When a dev pushes a fix, the system detects fixed findings, resolves threads, and shows score improvement.

#### Task 18: Fix verification in prompt + post_findings
- **Change:** Expand `commands/review-pr-core.md` Step 6: detect existing cr-ids, classify each prior finding as `fixed`/`still_present`/`not_relevant`, write `fix_verifications[]` into findings.json. Update `src/post_findings.py`: when `fix_verifications[]` present, resolve/close threads for "fixed" items (ADO: activity call, GitHub: gh reply), generate score comparison markdown (before/after) using `ScoreComparisonService`, include comparison in summary.
- **Files:** `commands/review-pr-core.md` (update), `src/post_findings.py` (update)
- **Tier:** standard
- **Done when:** Prompt has explicit fix verification instructions; post_findings resolves threads for fixed items; summary includes before/after score comparison
- **Blockers:** Phase 4 (modes wired up)

#### Task 19: Delta-only review logic + re-push tests
- **Change:** Update `commands/review-pr-core.md` to explain that on re-push, the agent reviews only the git diff between old and new head commits (not full PR diff). Add unit tests in `tests/test_post_findings.py` for fix verification: mock findings.json with fix_verifications, verify correct thread resolution calls and score comparison output.
- **Files:** `commands/review-pr-core.md` (update), `tests/test_post_findings.py` (update)
- **Tier:** standard
- **Done when:** Prompt has delta-only review instructions; unit tests for fix verification pass; score comparison produces correct before/after markdown
- **Blockers:** Task 18

#### VERIFY: Phase 5 — Fix Verification + Re-push
- Run full test suite: `pytest tests/ -v`
- Verify: fix verification tests pass; prompt covers re-push flow completely
- Report: tests passing, any regressions, any issues found

---

## Phase 6: GitHub Integration

**Goal:** Full GitHub VCS path — post comments, resolve threads, CI pipelines.

#### Task 21: GitHub path in post_findings.py
- **Change:** When `--vcs github` in `post_findings.py`: read existing comments via `gh api repos/{repo}/pulls/{pr}/comments`, post inline comments via `gh api` (file, line, body, commit_id, side), post summary via `gh pr comment {pr} --body "..."`, reply to comments via `gh api repos/{repo}/pulls/comments/{id}/replies`. Extend GitHub path to handle fix verification (reply with "Fixed", optionally minimize via GraphQL). All via `subprocess.run` calling `gh` CLI. Add retry-with-backoff for rate limiting (mitigates R9). Add unit tests for GitHub path (mock subprocess).
- **Files:** `src/post_findings.py` (update), `tests/test_post_findings.py` (update)
- **Tier:** standard
- **Done when:** `--vcs github --dry-run` produces correct output; unit tests for GitHub path pass; `gh` CLI calls are correctly constructed; fix verification works for GitHub (reply + minimize); rate limit retry logic present
- **Blockers:** Phase 5 (fix verification logic must be in post_findings.py before extending GitHub path)

#### Task 22: CI pipelines + Claude skill wrapper
- **Change:** Create `ci/azure-pipelines-pr-review.yml` (~55 lines) — ADO pipeline that pulls Docker image, runs review on PR trigger. Create `ci/github-review.yml` (~40 lines) — GitHub Actions workflow for PR review. Create `commands/review-pr.claude.md` (~10 lines) — Claude skill wrapper that loads review-pr-core.md.
- **Files:** `ci/azure-pipelines-pr-review.yml`, `ci/github-review.yml`, `commands/review-pr.claude.md`
- **Tier:** cheap
- **Done when:** Pipeline YAML files are valid; Claude skill wrapper correctly references review-pr-core.md
- **Blockers:** Task 11 (Docker image), Task 10 (core prompt)

#### VERIFY: Phase 6 — GitHub Integration
- Run full test suite: `pytest tests/ -v`
- Verify: GitHub path tests pass; CI YAML files are well-formed; Claude skill wrapper exists
- **Sprint 1 complete checkpoint:** full E2E path works for both ADO and GitHub
- Report: tests passing, any regressions, any issues found

---

## DEFERRED: Future Sprints

The following phases from IMPLEMENTATION-PLAN.md are deferred to future sprints:

### Phase 7: Remaining Review Modes (architecture + performance)
- `commands/review-mode-architecture.md` — breaking changes, coupling, API contracts
- `commands/review-mode-performance.md` — N+1 queries, indexes, memory, caching
- Mode stacking (multiple modes active, strictest multiplier wins)

### Phase 8: Multi-Agent Support (Claude + Gemini)
- Add Claude and Gemini CLIs to Dockerfile
- Write `GEMINI.md` project instructions
- Update entrypoint.sh agent switching
- Cross-agent findings.json quality comparison

### Phase 9: Container Registry + CI Optimization
- Push image to Azure Container Registry
- Image versioning/tagging (git SHA + semver)
- CI pulls pre-built image (~2 min saved per run)

### Phase 10: Scale (Repomix Tiers) + QoL
- T1-T5 tier assessment in prompt
- T4 directory chunking (risk-based deep/skim/skip)
- Dismissed feedback learning (`.codereview/dismissed.jsonl`)
- Cost footer in summary
- Error reporting to PR
- `README.md` — usage documentation

### Phase 11: Local CLI + PyPI Package
- Restructure to `code_reviewer/` Python package
- `cr` CLI: `cr review`, `cr score`, `cr post`, `cr init`
- Updated `pyproject.toml` with `[project.scripts]`
- Publish to Azure Artifacts (private PyPI)
- Future: public PyPI

### Deferred Utilities
- `utils/comment_exporter.py` — low priority, port when needed for export features
- `utils/markdown_formatter.py` enhancements — base version ported in Sprint 1 Task 1; advanced formatting deferred

---

## Risk Register

| # | Risk | Severity | Phase | Mitigation |
|---|------|----------|-------|------------|
| R1 | Codex sandbox conflicts with Docker | High | 3 | Test in Phase 3 smoke test. Fallback: `--sandbox=none`, rely on container isolation. |
| R2 | Prompt quality determines review quality | High | 3 | Iterative tuning against real PRs. Compare across agents in Phase 8. |
| R3 | post_findings.py complexity — most complex new file | High | 2 | Front-loaded to Phase 2. Extensive unit tests in Task 8. `--dry-run` for safe testing. |
| R4 | cr-id stability across file renames | Medium | 2 | cr-id uses file path — renames break matching. Accept for v1; document limitation. |
| R5 | Azure DevOps SDK version compatibility | Medium | 1 | Pin `azure-devops>=7.1,<8.0` in pyproject.toml. |
| R6 | Agent exceeds tool call cap (40 calls) | Medium | 3 | Phase 2 still works with partial findings. Monitor via `tool_calls` in findings.json. |
| R7 | Import path breakage during port | Medium | 1 | Verify with `python -c` import checks after each port step. |
| R8 | GitHub `gh` CLI output parsing fragility | Medium | 6 | Use `--json` flag where available. Unit test response parsing. |
| R9 | GitHub API rate limiting | Medium | 6 | 30 findings × multiple `gh api` calls can hit secondary rate limit. Mitigation: batch where possible, add retry-with-backoff in Task 21. |
| R10 | Docker image size (2GB+ risk) | Medium | 3 | Node 22 + Python + Codex + gh + ripgrep + repomix + azure-devops SDK. Mitigation: multi-stage build, track image size in Phase 3 VERIFY, optimize in Phase 9. |
| R11 | Schema drift between agent output and post_findings.py | Medium | 2 | Agent may hallucinate wrong field names or miss required fields. Mitigation: validate findings.json against `findings-schema.json` in post_findings.py (Task 7), reject/warn on schema violations. |
