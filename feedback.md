# Code Reviewer v3.1 — Phase 3 Code Review

**Reviewer:** local-codehawk-reviewer
**Date:** 2026-04-22 12:45:00+05:30
**Verdict:** APPROVED

> See the recent git history of this file to understand the context of this review. Phase 3 (Tasks 10–12) delivered the core agent prompt, scoring reference, Docker infrastructure, and project instruction files. Phase 2 was approved in the prior review (commit 02d271d). This review covers commits 449ad22 through 2b9e94a.

---

## 1. review-pr-core.md — Core Agent Prompt

**Status: PASS**

Verified against PLAN.md Task 10 "done when" criteria:

- **All 7 numbered steps present:** Step 1 (Load Project Context), Step 2 (Fetch PR Data), Step 3 (Detect Review Mode), Step 4 (Assess Scale T1–T5), Step 5 (Review Each Changed File), Step 6 (Fix Verification), Step 7 (Write findings.json). PASS.
- **VCS-conditional blocks:** Step 2 has ADO (`vcs.py get-pr`) and GitHub (`gh pr view`) blocks. Step 5 has ADO and GitHub read paths. Step 6 has ADO (`vcs.py list-threads`) and GitHub (`gh api`) blocks. PASS.
- **Verbatim constraints:** "max 30 findings" (lines 7, 209, 278), "max 5 per file" (lines 8, 209, 279), "max 40 tool calls" (line 6), confidence 0.0-1.0 (line 9). PASS.
- **Schema reference:** `commands/findings-schema.json` referenced at lines 12 and 250. PASS.
- **Line count:** 295 lines (plan specified ~200). Acceptable — the extra length comes from thorough VCS-conditional blocks and detailed examples.

### Quality observations

- Step 5b intent marker handling is well-structured with all three marker types (`cr: intentional`, `cr: ignore-next-line`, `cr: ignore-block start/end`). NOTE: This was originally scoped for Task 16 (Phase 4) but was included early. Not a problem — Phase 4 just needs to verify it's complete rather than adding it fresh.
- Step 5e references `commands/review-mode-<mode>.md` files that don't exist yet (Phase 4 Task 14). This is correct — the prompt says "if it exists," so no runtime failure.
- The example findings.json in Step 7 includes `tool_calls` and `agent` fields that are defined in the schema but not listed in the `required` array. Consistent with the schema (they're optional). PASS.

---

## 2. scoring.md — Penalty Matrix Reference

**Status: PASS**

- **5 categories × 3 severities:** security (5.0/4.0/2.0), performance (3.0/2.0/1.0), best_practices (2.0/1.0/0.5), code_style (0.0/0.0/0.0), documentation (0.0/0.0/0.0). All 15 cells present. PASS.
- **Star rating thresholds:** 6 tiers from 5-star (0.0) to 0-star (50.1+). Matches `pr_scorer.py` implementation. PASS.
- **Mode multipliers:** security, performance, architecture, migration all documented with correct escalation rules matching `apply_mode_multipliers` in `pr_scorer.py`. PASS.
- **Confidence expectations:** Filter threshold 0.7 documented, calibration guidance provided. Matches `post_findings.py` behavior. PASS.
- **Hard caps:** Restated here for agent awareness (max 30 / max 5 per file). PASS.

---

## 3. Dockerfile

**Status: PASS with notes**

- **Base image:** `node:22-slim`. PASS.
- **System dependencies:** python3, python3-venv, python3-pip, git, curl, jq, ripgrep, ca-certificates. PASS.
- **GitHub CLI:** Installed via official apt repository. PASS.
- **Node tools:** `@openai/codex` and `repomix` via npm global install. PASS.
- **Python venv:** Created at `/opt/venv`, deps installed (azure-devops, pydantic, pydantic-settings, msrest, jsonschema). PASS.
- **PYTHONPATH:** Set to `/app/src`. PASS.
- **COPY templates/ /app/templates/** (line 43): The `templates/` directory currently contains only `.gitkeep`. This will work (Docker copies the empty dir), and templates are populated in Phase 4 Task 15. NOTE — not a blocker.

### Finding 3.1 — LOW: `COPY templates/` will fail if directory is deleted

`Dockerfile:43` — If someone removes the `templates/` directory before templates are created in Phase 4, `docker build` breaks. Current state is fine (`.gitkeep` exists), and Phase 4 will populate it. Informational only.

---

## 4. entrypoint.sh

**Status: PASS**

- **Env var validation:** Checks PR_ID, REPO, VCS, AGENT. Validates VCS ∈ {ado, github}, AGENT ∈ {codex, claude, gemini}. PASS.
- **Phase 1 dispatch:** Three agent cases with correct CLI invocations. Codex uses `--sandbox=none` and `--approval-policy auto-edit` (required for non-interactive use). Claude uses `--print` mode. Gemini has basic invocation. PASS.
- **findings.json verification:** Checks file exists, validates JSON with `python3 -c "import json; json.load(...)"`, prints finding count. PASS.
- **Phase 2 dispatch:** Runs `post_findings.py` with correct args. Supports `DRY_RUN` env var. PASS.
- **AGENTS.md copy:** Copies `/app/AGENTS.md` to `/workspace/AGENTS.md` if not already present — correct bootstrap behavior. PASS.

### Finding 4.1 — MEDIUM: No PROJECT-CLAUDE.md copy for Claude agent

`entrypoint.sh:43-46` — The script copies `AGENTS.md` into `/workspace/` for Codex, but does not copy `PROJECT-CLAUDE.md` for the Claude agent. Claude Code looks for `CLAUDE.md` in the project root. If the repo under review doesn't have its own `CLAUDE.md`, the Claude agent won't receive the codehawk project instructions.

**Suggested fix:** Add a conditional copy for Claude:
```bash
if [[ "$AGENT" == "claude" && -f "/app/PROJECT-CLAUDE.md" && ! -f "/workspace/CLAUDE.md" ]]; then
    cp /app/PROJECT-CLAUDE.md /workspace/CLAUDE.md
fi
```

### Finding 4.2 — LOW: No Gemini instruction file handling

`entrypoint.sh` — The Gemini agent path doesn't have a corresponding project instruction file copy. The prompt at line 76 mentions "GEMINI.md in workspace provides project instructions" but no such file is created or copied. This is consistent with Gemini being a secondary target, but worth tracking for Phase 6.

### Finding 4.3 — LOW: Prompt passed as CLI argument may hit ARG_MAX on edge cases

`entrypoint.sh:57-79` — The entire review prompt (~295 lines, ~10KB) is expanded inline via `$(<"$REVIEW_PROMPT_PATH")` as a positional argument. Linux ARG_MAX is typically 2MB+, so this is safe in practice. Just noting for awareness — if the prompt grows significantly, consider `--file` or stdin piping.

---

## 5. docker-compose.yml

**Status: PASS with note**

- **Environment passthrough:** All required vars (PR_ID, REPO, VCS, AGENT, auth tokens, API keys, model overrides, DRY_RUN). PASS.
- **Volume mounts:** Workspace mounted read-only (`:ro`), findings output to `./findings-output`. PASS.
- **Usage examples in comments:** Clear. PASS.

### Finding 5.1 — LOW: `version: "3.9"` is deprecated

`docker-compose.yml:1` — Docker Compose V2 ignores the `version` field and shows a warning. Not breaking, but newer projects typically omit it. Cosmetic.

---

## 6. AGENTS.md (Codex) and PROJECT-CLAUDE.md (Claude)

**Status: PASS with note**

Both files verified against PLAN.md Task 12 criteria:

- **Two-phase architecture:** Explained with ASCII diagram. PASS.
- **Primary directive:** "Read and follow `commands/review-pr-core.md`". PASS.
- **Available tools:** `vcs.py` subcommands, `gh`, `rg`, `git show/blame`, `repomix`. PASS.
- **Output location:** `/workspace/.cr/findings.json`. PASS.
- **Constraint reminders:** 40 tool calls, 30 findings, 5 per file, confidence 0.0-1.0, no posting. PASS.
- **Environment variables:** Documented (VCS, PR_ID, REPO, branch vars, auth tokens). PASS.

### Finding 6.1 — LOW: AGENTS.md says workspace is "read-only" without `.cr/` exception

`AGENTS.md:53` — States "Do NOT modify files in `/workspace/` (read-only)" but the agent must create `/workspace/.cr/` and write `findings.json`. `PROJECT-CLAUDE.md:55` correctly says "(read-only, except writing to `/workspace/.cr/`)". AGENTS.md should match.

---

## 7. Test Suite — No Regressions

**Status: PASS**

```
PYTHONPATH=src pytest tests/ -v → 66 passed in 0.26s
```

All 66 tests from Phase 2 continue to pass. No regressions. Phase 3 deliverables are primarily markdown and Docker files, so no new Python tests were expected.

---

## 8. Code Quality

**Status: PASS**

- No dead code found in Phase 3 deliverables.
- No unused imports (Phase 3 is mostly non-Python).
- No hardcoded values that should be configurable — model defaults in entrypoint.sh use `${CODEX_MODEL:-o3}` / `${CLAUDE_MODEL:-claude-opus-4-7}` / `${GEMINI_MODEL:-gemini-2.5-pro}` pattern with env overrides. PASS.
- Docker layer ordering is correct: system deps → node tools → python venv → app code. Maximizes cache reuse. PASS.

---

## Summary

Phase 3 deliverables are solid. The core prompt (`review-pr-core.md`) covers all 7 required steps with proper VCS-conditional blocks and verbatim constraints. The penalty matrix in `scoring.md` is complete and consistent with the Python implementation. Docker infrastructure is well-structured with proper layer caching. Project instruction files clearly communicate the two-phase architecture and constraints to each agent.

**1 MEDIUM finding:**
- **4.1:** `entrypoint.sh` doesn't copy `PROJECT-CLAUDE.md` to workspace for the Claude agent. Should fix before Phase 4.

**4 LOW findings (non-blocking):**
- **3.1:** `COPY templates/` depends on directory existing (fine today, fragile if .gitkeep removed)
- **4.2:** No Gemini instruction file handling (track for later)
- **5.1:** `version: "3.9"` in docker-compose.yml is deprecated (cosmetic)
- **6.1:** AGENTS.md says workspace is fully read-only but agent writes to `.cr/` (inconsistency with PROJECT-CLAUDE.md)

**Docker build:** Not verified (Docker Desktop not available in this environment). Structural review of Dockerfile shows no issues. Docker build must be verified in CI or manually.

**Phase 3 (Core Prompt + Docker) is approved for Phase 4 to proceed.** The MEDIUM finding (4.1) should be addressed early in Phase 4 work.
