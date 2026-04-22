# Codehawk — Architecture

## Overview

Codehawk is a Docker-based AI code review system. An LLM agent (Codex, Claude, or Gemini) reads a PR and writes `findings.json`; a deterministic Python script then scores, deduplicates, and posts comments to ADO or GitHub. The two phases are explicitly separated so the agent never touches the VCS API and the poster never touches the LLM.

---

## Two-Phase Architecture

**Phase 1 — Agent review**
The agent runs inside a Docker container with access to the workspace. It reads the PR diff and changed files using VCS tools, then writes `/workspace/.cr/findings.json`. The agent does not post anything.

**Phase 2 — Deterministic posting**
`post_findings.py` reads `findings.json`, filters by confidence, caps findings, deduplicates via cr-ids, scores the PR, posts inline comments, and updates the summary. This phase is fully deterministic and testable without a live LLM.

**Why this separation?**
- The agent is non-deterministic; the poster must be deterministic for idempotency.
- `--dry-run` can exercise the full poster path without VCS writes.
- Phase 2 can be re-run independently if posting fails.

---

## Idempotency via cr-id Deduplication

Every finding gets a stable identifier computed by `post_findings.py`:

```python
hashlib.sha1(f"{file}:{line}:{category}".encode()).hexdigest()[:8]
```

The agent sets `cr_id: null` in `findings.json`; the poster computes the hash and injects `<!-- cr-id: {id} -->` into every posted comment body. On re-runs, the poster fetches existing thread markers, extracts cr-ids, and skips findings whose cr-id is already present. This makes re-runs safe — no duplicate comments, ever.

**Limitation:** cr-id uses the file path. If a file is renamed between runs, the cr-id changes and prior comments will not be matched. Accepted for v1.

---

## VCS Abstraction

Two distinct invocation patterns are used:

| Caller | ADO | GitHub |
|--------|-----|--------|
| Agent (Phase 1) | `python vcs.py <subcommand>` | `gh pr view`, `gh api` |
| Poster (Phase 2) | Activity classes imported directly | `subprocess.run` calling `gh` |

`vcs.py` is a thin argparse CLI that wraps the ported ADO activity classes and outputs JSON to stdout. It exists so the agent can call it as a shell command without knowing Python internals. `post_findings.py` bypasses `vcs.py` and imports activity classes directly for ADO (avoids subprocess overhead per comment).

For GitHub, all VCS calls in `post_findings.py` go through `_gh_run_with_retry()`, which wraps `subprocess.run` with exponential backoff for rate-limit errors.

---

## Penalty-Based Scoring

The PR receives a 1–5 star rating. Scoring deducts penalties per finding:

- **Critical** — largest penalty
- **Warning** — medium penalty
- **Suggestion** — small penalty
- **Good** — no penalty (positive signal)

**Review mode multipliers** are applied before summing:
- Security mode: security-category findings × 2
- Performance mode: performance-category findings × 2
- Architecture mode: best_practices-category × 1.5
- Migration mode: all findings elevated to minimum critical severity

Mode multipliers stack when multiple modes are active; the strictest multiplier per finding wins.

---

## findings.json Schema

`findings.json` is the contract between Phase 1 and Phase 2. Schema is defined in `commands/findings-schema.json`.

**Top-level fields:**
- `pr_id`, `repo`, `project`, `vcs` — PR identity
- `review_modes` — list of active modes (standard, security, migration, docs/chore, architecture, performance)
- `tier` — T1–T5 scale tier assessed by the agent
- `agent`, `model`, `tool_calls` — observability metadata
- `existing_cr_ids` — cr-ids already posted before this run (agent reads these)
- `findings[]` — list of Finding objects
- `fix_verifications[]` — list of FixVerification objects (only on re-push)

**Finding fields:** `cr_id` (null from agent, filled by poster), `file`, `line`, `line_range`, `severity`, `category`, `confidence`, `title`, `body`, `suggestion`, `trace`

**FixVerification fields:** `cr_id`, `status` (fixed/still_present/not_relevant), `reason`

---

## Post Findings Engine — Filtering and Cap Logic

`post_findings.py` applies filters in order before posting:

1. **Confidence filter** — drop findings below 0.7 (configurable via `.codereview.yml`)
2. **Cap** — max 30 findings total, max 5 per file. When over cap, prioritize by severity (critical → warning → suggestion → good).
3. **Dedup** — skip findings whose cr-id already appears in existing PR threads.

---

## Gate Thresholds

`post_findings.py` reads `/workspace/.codereview.yml` if present and applies gate thresholds to the CI output JSON:
- `min_star_rating` — fail CI if score falls below this
- `fail_on_critical` — fail CI if any critical findings remain unresolved

The structured JSON output to stdout is consumed by CI pipelines to set pass/fail status.

---

## Docker Container

Base image: `node:22-slim`. Layers:
- System: Python 3, git, curl, jq, ripgrep
- GitHub CLI (`gh`)
- NPM globals: `@openai/codex`, `repomix`
- Python venv: `azure-devops`, `pydantic`, `pydantic-settings`, `msrest`
- Copied into image: `commands/`, `src/`, `templates/`, `AGENTS.md`

`PYTHONPATH` points to `/app/src`. `entrypoint.sh` orchestrates Phase 1 (agent dispatch by `$AGENT` env var) and Phase 2 (post_findings.py invocation).

**Image size risk:** Node 22 + Codex + Python + gh + ripgrep + repomix + azure-devops SDK risks exceeding 2 GB. Mitigate with multi-stage builds if needed (Phase 9).

---

## Config Philosophy

`src/config.py` (via pydantic-settings) carries only:
- ADO auth: PAT, system token, organization URL, project
- GitHub: `GH_TOKEN`
- VCS selector: `VCS` (ado/github)
- Penalty matrix and star thresholds

All AI/LLM settings were removed from config — the agent CLI handles its own auth. This keeps the Python layer fully VCS-focused.

---

## Code Port Strategy

The old codebase (`BBX_AI - Doer/Pipelines/CodeReviewer/src/`) provided battle-tested ADO activities, scoring, and models. These were ported with minimal adaptation (import path fixes, cr-id injection). Code that managed LLM calls, prompt building, response parsing, and comment consolidation was deleted entirely — replaced by agent CLI + two-phase design + cr-id dedup.

The port preserves backward-compatible field names on the Settings object (e.g., `azure_devops_org`, `azure_devops_url`) so activity classes work without modification.

---

## Key Trade-offs

| Decision | Chosen approach | Alternative considered | Reason |
|----------|----------------|------------------------|--------|
| VCS invocation in poster | Import ADO activities directly | subprocess vcs.py | Avoids per-comment subprocess overhead |
| cr-id generation | Poster computes SHA1 hash | Agent computes hash | LLMs can't reliably compute SHA1 |
| GitHub thread resolution | Reply "Fixed" + optional GraphQL minimize | Native resolve (not available) | GitHub has no native thread resolution API |
| Agent-to-VCS boundary | Agent never calls post; poster never calls LLM | Merged single script | Enables dry-run, re-run, and deterministic tests |
| Comment consolidation | cr-id dedup in poster | LLM-based comment merging (old approach) | Deterministic, no LLM cost, idempotent |

---

## Risk Register Summary

| Risk | Severity | Status |
|------|----------|--------|
| Codex sandbox conflicts with Docker | High | `--sandbox=none` fallback available |
| Prompt quality determines review quality | High | Iterative tuning; multi-agent comparison planned Phase 8 |
| cr-id instability on file rename | Medium | Accepted limitation for v1; documented |
| ADO SDK version compatibility | Medium | Pinned `azure-devops>=7.1,<8.0` |
| Agent exceeds 40-tool-call cap | Medium | Phase 2 still processes partial findings |
| GitHub API pagination not handled | Low | Max 30 findings cap limits impact; `--paginate` planned |
| Docker image size > 2 GB | Medium | Multi-stage build optimization planned Phase 9 |

---

## Sprint Boundaries

**Sprint 1 (complete):** Phases 1–6 — scaffold, ported foundation, VCS CLI, post findings engine, core prompt, Docker, review modes, fix verification, GitHub integration.

**Deferred (Sprint 2+):**
- Phase 7: architecture and performance review modes
- Phase 8: Claude and Gemini multi-agent support in Dockerfile
- Phase 9: Container registry (ACR/GHCR) + image versioning
- Phase 10: T1–T5 repomix tiers, dismissed-feedback learning, cost footer
- Phase 11: Local `cr` CLI + PyPI package
