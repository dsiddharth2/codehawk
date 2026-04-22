# Feature: Review Modes

## Overview

Codehawk supports 6 review modes. The agent auto-detects the active mode(s) from changed file paths and PR labels. Multiple modes can be active simultaneously; the strictest mode multiplier per finding wins.

## Modes

| Mode | File: | Detection signals |
|------|-------|-------------------|
| standard | `commands/review-mode-standard.md` | Default; active when no other mode is detected |
| security | `commands/review-mode-security.md` | Auth/crypto files, secrets patterns in diff |
| architecture | `commands/review-mode-architecture.md` | Interface files, API contracts, breaking changes |
| performance | `commands/review-mode-performance.md` | Hot paths, queries, memory-sensitive code |
| migration | `commands/review-mode-migration.md` | `.sql` files, migration scripts, DDL changes |
| docs/chore | `commands/review-mode-docs-chore.md` | Only `.md`/`.yml`/`.json` files changed, or PR label "docs"/"chore" |

## Auto-Detection Rules (Step 3 of review-pr-core.md)

The agent evaluates changed file paths and PR labels to set `review_modes[]` in `findings.json`. Detection is checked in order; multiple modes can match:
- **migration**: any `.sql` file or file path matching `*migration*`, `*migrate*`, `*schema*`
- **security**: auth files, crypto files, secrets handling, `*password*`, `*token*`, `*secret*`
- **docs/chore**: all changed files are `.md`, `.yml`, `.json`, `.txt`, or PR label is "docs" or "chore"
- **standard**: always active unless docs/chore mode is the only mode detected

## Scoring Multipliers

Mode multipliers are applied in `PRScorer.apply_mode_multipliers(findings, review_modes)`:

| Mode | Affected category | Multiplier |
|------|------------------|------------|
| security | security | ×2 |
| performance | performance | ×2 |
| architecture | best_practices | ×1.5 |
| migration | all | elevate to minimum critical |

When multiple multipliers apply to the same finding, the strictest (highest) multiplier is used.

## Mode Prompt Files

Each mode file contains a focused checklist. The agent reads the relevant mode file(s) during Step 3 and uses them to guide its file-by-file review in Step 5. The docs/chore mode explicitly specifies light-touch review — deep code analysis is skipped.

## Intent Markers

Code can opt out of review using inline markers. The agent checks for these before flagging findings:

| Marker | Effect |
|--------|--------|
| `# cr: intentional` | Skip this line |
| `# cr: ignore-next-line` | Skip the next line |
| `# cr: ignore-block start` … `# cr: ignore-block end` | Skip the entire block |
