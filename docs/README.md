# CodeHawk Documentation

Navigation hub for all CodeHawk documentation.

---

## Architecture

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | System design, component interactions, two-phase data flow, and design decisions |

---

## Feature Documentation

| Document | Description |
|----------|-------------|
| [Agent Runner](features/agent-runner.md) | OpenAI agent runner internals: API detection, tool registry, conversation loop, turn budget management, and findings extraction fallback chain |
| [Graph Tools](features/graph-tools.md) | AST-based structural analysis: change analysis, blast radius, caller lookup, file dependency resolution, and graceful degradation |
| [Review Modes](features/review-modes.md) | Mode auto-detection from file paths and PR labels, mode-specific checklists, and severity multiplier effects |
| [Scoring](features/scoring.md) | Penalty-based PR scoring: penalty matrix, star rating thresholds, mode multipliers, and configuration |
| [Post Findings](features/post-findings.md) | Phase 2 engine: schema validation, confidence filtering, finding caps, deduplication, summary generation, and CI gate evaluation |
| [Fix Verification](features/fix-verification.md) | Re-push detection, delta diff analysis, fixed/still-present/not-relevant classification, and thread resolution |
| [CI Integration](features/ci-integration.md) | Pipeline setup for Azure DevOps and GitHub Actions, Docker usage, and environment variable reference |
| [VCS CLI](features/vcs-cli.md) | VCS command wrappers for Azure DevOps and GitHub: get-pr, get-file, list-threads, and authentication |

---

## Quick Links

| Resource | Link |
|----------|------|
| Findings Schema (JSON) | [commands/findings-schema.json](../commands/findings-schema.json) |
| Agent Instruction Set | [commands/review-pr-core.md](../commands/review-pr-core.md) |
| Mode Checklists | [commands/review-pr-core.md — Step 3](../commands/review-pr-core.md) |
| Scoring Matrix | [features/scoring.md](features/scoring.md) |
| `.codereview.yml` config | [features/ci-integration.md](features/ci-integration.md) |
| Docker setup | [features/ci-integration.md](features/ci-integration.md) |
| CI pipeline templates | [ci/](../ci/) |
| Repository templates | [templates/](../templates/) |
