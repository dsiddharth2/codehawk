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
- **Change:** Create directory structure (`src/`, `src/activities/`, `src/models/`, `src/utils/`, `commands/`, `templates/`, `ci/`, `tests/` with `__init__.py` files). Create `pyproject.toml` with deps (`azure-devops>=7.1,<8.0`, `pydantic>=2.0`, `pydantic-settings>=2.0`, `msrest`, dev: `pytest`, `pytest-mock`). Port `utils/logger.py` and `utils/url_sanitizer.py` as-is from old codebase.
- **Files:** `src/__init__.py`, `src/activities/__init__.py`, `src/models/__init__.py`, `src/utils/__init__.py`, `commands/` (dir), `templates/` (dir), `ci/` (dir), `tests/__init__.py`, `pyproject.toml`, `src/utils/logger.py`, `src/utils/url_sanitizer.py`
- **Tier:** cheap
- **Done when:** `python -c "import src"` works from project root; `pyproject.toml` is valid; logger and url_sanitizer modules exist with correct content
- **Blockers:** None

#### Task 2: Port models + config + activities
- **Change:** Port `models/review_models.py` from old codebase, add new `Finding`, `FixVerification`, and `FindingsFile` dataclasses matching findings.json schema. Port `config.py` — strip OpenAI/AI settings, keep ADO auth + penalty matrix + star thresholds, add `VCS` field (ado/github) and `GH_TOKEN`. Port all 8 activity files under `src/activities/`, updating import paths to new layout. Add cr-id extraction to `fetch_pr_comments_activity.py` (parse `<!-- cr-id: xxx -->`). Add cr-id marker injection to `post_pr_comment_activity.py`.
- **Files:** `src/models/review_models.py`, `src/config.py`, `src/activities/base_activity.py`, `src/activities/fetch_pr_details_activity.py`, `src/activities/fetch_pr_comments_activity.py`, `src/activities/post_pr_comment_activity.py`, `src/activities/post_fix_reply_activity.py`, `src/activities/fetch_file_content_activity.py`, `src/activities/fetch_file_diff_activity.py`, `src/activities/update_summary_activity.py`
- **Tier:** standard
- **Done when:** `python -c "from activities.fetch_pr_details_activity import FetchPRDetailsActivity"` resolves (with PYTHONPATH=src); `Finding` and `FindingsFile` dataclasses importable; config loads without OpenAI fields
- **Blockers:** Task 1 (directory structure + deps)

#### Task 3: Port pr_scorer + score_comparison
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

#### Task 4: Write vcs.py CLI
- **Change:** Create `src/vcs.py` with argparse CLI. Subcommands: `get-pr`, `list-threads`, `post-comment`, `resolve-thread`, `get-file`, `post-summary`. Each wraps the corresponding ported activity. All output JSON to stdout, errors to stderr. Auth from env vars via Settings.
- **Files:** `src/vcs.py`
- **Tier:** standard
- **Done when:** `python src/vcs.py --help` shows all subcommands; `python src/vcs.py get-pr --help` shows correct args; code imports activities correctly
- **Blockers:** Phase 1 (all activities ported)

#### Task 5: Write findings-schema.json + post_findings.py
- **Change:** Create `commands/findings-schema.json` matching FindingsFile dataclass (contract between Phase 1 and Phase 2). Create `src/post_findings.py` (~350 lines) — the Phase 2 engine. Logic: (1) read/validate findings.json, (2) filter below min_confidence 0.7, (3) cap 30 findings / 5 per file by severity, (4) fetch existing threads + extract posted cr-ids, (5) skip already-posted cr-ids, (6) score via PRScorer with mode multipliers, (7) post new inline comments (ADO: direct activity import, GitHub: `gh api`), (8) handle fix_verifications if present, (9) post/update summary, (10) output structured JSON for CI gating. Support `--dry-run` (read/filter/score only, skip VCS writes).
- **Files:** `commands/findings-schema.json`, `src/post_findings.py`
- **Tier:** premium
- **Done when:** `python src/post_findings.py --help` works; `--dry-run` with a sample findings.json produces scored output JSON; cr-id dedup logic implemented; cap/filter logic implemented
- **Blockers:** Tasks 3 (scorer), 4 (vcs.py)

#### Task 6: Unit tests for Phase 2
- **Change:** Create `tests/conftest.py` (shared fixtures: sample findings.json, mock activities). Create `tests/test_pr_scorer.py` — test: 0 findings = 5 stars, security critical = 5.0 penalty, mode multipliers double security. Create `tests/test_post_findings.py` — test: confidence filter drops <0.7, cap at 30/5-per-file, cr-id dedup skips posted. Create `tests/test_vcs_cli.py` — test: subcommand parsing, activity invocation.
- **Files:** `tests/conftest.py`, `tests/test_pr_scorer.py`, `tests/test_post_findings.py`, `tests/test_vcs_cli.py`
- **Tier:** standard
- **Done when:** `pytest tests/` passes; scorer, poster, and VCS CLI all have coverage for core logic
- **Blockers:** Tasks 4, 5

#### VERIFY: Phase 2 — VCS CLI + Post Findings
- Run full test suite: `pytest tests/ -v`
- Verify: `python src/vcs.py --help` works; `python src/post_findings.py --dry-run` with sample data produces valid output
- Report: tests passing, any regressions, any issues found

---

## Phase 3: Core Prompt + Docker

**Goal:** The agent instructions and the container that runs everything. Both are high-risk items that need early validation.

#### Task 7: Write review-pr-core.md + scoring.md
- **Change:** Create `commands/review-pr-core.md` (~200 lines) — THE PRODUCT. Agent instructions: (1) load project context, (2) fetch PR data (VCS-specific), (3) detect review mode from files+labels, (4) assess scale T1-T5, (5) review each changed file, (6) fix verification on re-push, (7) write `/workspace/.cr/findings.json`. Constraints: no posting, max 30 findings, 5 per file, 40 tool calls, confidence scores. Create `commands/scoring.md` — penalty matrix reference, severity levels, category definitions, confidence expectations.
- **Files:** `commands/review-pr-core.md`, `commands/scoring.md`
- **Tier:** premium
- **Done when:** Prompt covers all 7 steps from architecture; references findings-schema.json; includes VCS-specific instructions for both ADO and GitHub; scoring.md has complete penalty matrix
- **Blockers:** Task 5 (schema reference)

#### Task 8: Dockerfile + entrypoint.sh + docker-compose
- **Change:** Create `Dockerfile` (~40 lines): base node:22-slim, install python3/git/curl/jq/ripgrep, gh CLI, npm install @openai/codex + repomix, python venv with azure-devops/pydantic, copy commands/src/templates/AGENTS.md, set PATH/PYTHONPATH, workdir /workspace. Create `entrypoint.sh` (~50 lines): validate env vars, mkdir .cr, Phase 1 dispatch (codex/claude/gemini based on $AGENT), verify findings.json produced, Phase 2 run post_findings.py. Create `docker-compose.yml` for local dev.
- **Files:** `Dockerfile`, `entrypoint.sh`, `docker-compose.yml`
- **Tier:** standard
- **Done when:** `docker build -t codehawk:local .` succeeds; `entrypoint.sh` is executable with correct Phase 1/2 dispatch logic
- **Blockers:** Phase 2 (all Python code ready)

#### Task 9: Project instructions + smoke test
- **Change:** Create `AGENTS.md` (Codex project instructions) and project-level `CLAUDE.md` (Claude project instructions). Run `docker build` and `docker run --rm -e DRY_RUN=1 ...` smoke test against a real PR (or with mock data if no live PR available). Verify agent dispatch, findings.json handling, dry-run output.
- **Files:** `AGENTS.md`, project `CLAUDE.md` (distinct from agent context CLAUDE.md)
- **Tier:** standard
- **Done when:** Docker image builds; dry-run produces structured JSON output; AGENTS.md and CLAUDE.md contain correct project-level instructions
- **Blockers:** Task 8

#### VERIFY: Phase 3 — Core Prompt + Docker
- Run full test suite: `pytest tests/ -v`
- Verify: `docker build` succeeds; dry-run against sample data works end-to-end
- **Critical checkpoint:** Codex-in-Docker compatibility. If sandbox conflicts, document workaround (`--sandbox=none`).
- Report: tests passing, Docker build status, any regressions, any issues found

---

## Phase 4: Review Modes + Conventions

**Goal:** All review mode prompts, auto-detection, templates, and intent markers.

#### Task 10: Write review mode prompts
- **Change:** Create `commands/review-mode-standard.md` (correctness, patterns, test coverage, naming, error handling, edge cases). Create `commands/review-mode-security.md` (OWASP Top 10, injection, auth bypass, secrets, insecure defaults, dependency CVEs). Create `commands/review-mode-migration.md` (data loss, rollback safety, destructive DDL, lock duration, idempotency).
- **Files:** `commands/review-mode-standard.md`, `commands/review-mode-security.md`, `commands/review-mode-migration.md`
- **Tier:** standard
- **Done when:** Each mode file has a complete checklist; files follow consistent format; checklists cover all items from architecture doc
- **Blockers:** Task 7 (core prompt exists to reference modes)

#### Task 11: Mode auto-detection + multiplier wiring + templates
- **Change:** Update `commands/review-pr-core.md` Step 3 with explicit mode detection rules from architecture doc (file path patterns, PR labels, etc.). Ensure `post_findings.py` reads `review_modes` from findings.json and passes to scorer's `apply_mode_multipliers`. Create `templates/.codereview.md` (starter conventions), `templates/.codereview.yml` (starter settings with gate thresholds), `templates/dismissed.jsonl` (empty).
- **Files:** `commands/review-pr-core.md` (update), `src/post_findings.py` (update), `templates/.codereview.md`, `templates/.codereview.yml`, `templates/dismissed.jsonl`
- **Tier:** standard
- **Done when:** Prompt has detection rules for all 6 modes; post_findings.py passes review_modes to scorer; templates exist with reasonable defaults
- **Blockers:** Tasks 5 (post_findings), 7 (core prompt), 10 (mode prompts)

#### Task 12: Intent marker handling
- **Change:** Update `commands/review-pr-core.md` Step 5 to handle intent markers: `# cr: intentional` (skip this line), `# cr: ignore-block start/end` (skip block), `# cr: ignore-next-line` (skip next line). Agent must check for markers before flagging findings.
- **Files:** `commands/review-pr-core.md` (update)
- **Tier:** cheap
- **Done when:** Prompt documents all three marker types with examples; agent instructions clearly state to skip marked code
- **Blockers:** Task 7 (core prompt)

#### VERIFY: Phase 4 — Review Modes + Conventions
- Run full test suite: `pytest tests/ -v`
- Verify: all mode files exist and are well-formed; templates exist; intent markers documented in prompt
- Report: tests passing, any regressions, any issues found

---

## Phase 5: Fix Verification + Re-push

**Goal:** When a dev pushes a fix, the system detects fixed findings, resolves threads, and shows score improvement.

#### Task 13: Fix verification in prompt + post_findings
- **Change:** Expand `commands/review-pr-core.md` Step 6: detect existing cr-ids, classify each prior finding as `fixed`/`still_present`/`not_relevant`, write `fix_verifications[]` into findings.json. Update `src/post_findings.py`: when `fix_verifications[]` present, resolve/close threads for "fixed" items (ADO: activity call, GitHub: gh reply), generate score comparison markdown (before/after) using `ScoreComparisonService`, include comparison in summary.
- **Files:** `commands/review-pr-core.md` (update), `src/post_findings.py` (update)
- **Tier:** standard
- **Done when:** Prompt has explicit fix verification instructions; post_findings resolves threads for fixed items; summary includes before/after score comparison
- **Blockers:** Phase 4 (modes wired up)

#### Task 14: Delta-only review logic + re-push tests
- **Change:** Update `commands/review-pr-core.md` to explain that on re-push, the agent reviews only the git diff between old and new head commits (not full PR diff). Add unit tests in `tests/test_post_findings.py` for fix verification: mock findings.json with fix_verifications, verify correct thread resolution calls and score comparison output.
- **Files:** `commands/review-pr-core.md` (update), `tests/test_post_findings.py` (update)
- **Tier:** standard
- **Done when:** Prompt has delta-only review instructions; unit tests for fix verification pass; score comparison produces correct before/after markdown
- **Blockers:** Task 13

#### VERIFY: Phase 5 — Fix Verification + Re-push
- Run full test suite: `pytest tests/ -v`
- Verify: fix verification tests pass; prompt covers re-push flow completely
- Report: tests passing, any regressions, any issues found

---

## Phase 6: GitHub Integration

**Goal:** Full GitHub VCS path — post comments, resolve threads, CI pipelines.

#### Task 15: GitHub path in post_findings.py
- **Change:** When `--vcs github` in `post_findings.py`: read existing comments via `gh api repos/{repo}/pulls/{pr}/comments`, post inline comments via `gh api` (file, line, body, commit_id, side), post summary via `gh pr comment {pr} --body "..."`, reply to comments via `gh api repos/{repo}/pulls/comments/{id}/replies`. All via `subprocess.run` calling `gh` CLI. Add unit tests for GitHub path (mock subprocess).
- **Files:** `src/post_findings.py` (update), `tests/test_post_findings.py` (update)
- **Tier:** standard
- **Done when:** `--vcs github --dry-run` produces correct output; unit tests for GitHub path pass; `gh` CLI calls are correctly constructed
- **Blockers:** Phase 2 (post_findings base)

#### Task 16: CI pipelines + Claude skill wrapper
- **Change:** Create `ci/azure-pipelines-pr-review.yml` (~55 lines) — ADO pipeline that pulls Docker image, runs review on PR trigger. Create `ci/github-review.yml` (~40 lines) — GitHub Actions workflow for PR review. Create `commands/review-pr.claude.md` (~10 lines) — Claude skill wrapper that loads review-pr-core.md.
- **Files:** `ci/azure-pipelines-pr-review.yml`, `ci/github-review.yml`, `commands/review-pr.claude.md`
- **Tier:** cheap
- **Done when:** Pipeline YAML files are valid; Claude skill wrapper correctly references review-pr-core.md
- **Blockers:** Task 8 (Docker image), Task 7 (core prompt)

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
- README.md

### Phase 11: Local CLI + PyPI Package
- Restructure to `code_reviewer/` Python package
- `cr` CLI: `cr review`, `cr score`, `cr post`, `cr init`
- Updated `pyproject.toml` with `[project.scripts]`
- Publish to Azure Artifacts (private PyPI)
- Future: public PyPI

---

## Risk Register

| # | Risk | Severity | Phase | Mitigation |
|---|------|----------|-------|------------|
| R1 | Codex sandbox conflicts with Docker | High | 3 | Test in Phase 3 smoke test. Fallback: `--sandbox=none`, rely on container isolation. |
| R2 | Prompt quality determines review quality | High | 3 | Iterative tuning against real PRs. Compare across agents in Phase 8. |
| R3 | post_findings.py complexity — most complex new file | High | 2 | Front-loaded to Phase 2. Extensive unit tests in Task 6. `--dry-run` for safe testing. |
| R4 | cr-id stability across file renames | Medium | 2 | cr-id uses file path — renames break matching. Accept for v1; document limitation. |
| R5 | Azure DevOps SDK version compatibility | Medium | 1 | Pin `azure-devops>=7.1,<8.0` in pyproject.toml. |
| R6 | Agent exceeds tool call cap (40 calls) | Medium | 3 | Phase 2 still works with partial findings. Monitor via `tool_calls` in findings.json. |
| R7 | Import path breakage during port | Medium | 1 | Verify with `python -c` import checks after each port step. |
| R8 | GitHub `gh` CLI output parsing fragility | Medium | 6 | Use `--json` flag where available. Unit test response parsing. |
