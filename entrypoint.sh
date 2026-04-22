#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# codehawk entrypoint — two-phase PR review
#
# Phase 1: Agent reviews the PR and writes /workspace/.cr/findings.json
# Phase 2: post_findings.py posts comments and gates the build
# ---------------------------------------------------------------------------

# --- Validate required environment variables --------------------------------
required_vars=(PR_ID REPO VCS AGENT)
missing=()
for var in "${required_vars[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        missing+=("$var")
    fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: Missing required environment variables: ${missing[*]}" >&2
    exit 1
fi

# Validate VCS value
if [[ "$VCS" != "ado" && "$VCS" != "github" ]]; then
    echo "ERROR: VCS must be 'ado' or 'github', got: $VCS" >&2
    exit 1
fi

# Validate AGENT value
if [[ "$AGENT" != "codex" && "$AGENT" != "claude" && "$AGENT" != "gemini" ]]; then
    echo "ERROR: AGENT must be 'codex', 'claude', or 'gemini', got: $AGENT" >&2
    exit 1
fi

echo "==> codehawk starting: PR=$PR_ID REPO=$REPO VCS=$VCS AGENT=$AGENT"

# --- Prepare workspace ------------------------------------------------------
mkdir -p /workspace/.cr

# Copy project instruction files into workspace if not already present
# (CI mounts the repo at /workspace — these are the agent bootstrap files)
if [[ -f "/app/AGENTS.md" && ! -f "/workspace/AGENTS.md" ]]; then
    cp /app/AGENTS.md /workspace/AGENTS.md
fi

# --- Phase 1: Agent reviews the PR ------------------------------------------
echo "==> Phase 1: Running agent ($AGENT)..."

REVIEW_PROMPT_PATH="/app/commands/review-pr-core.md"

case "$AGENT" in
    codex)
        # OpenAI Codex — reads AGENTS.md for project instructions
        # Pass the review prompt as the task; Codex uses AGENTS.md for context
        codex \
            --model "${CODEX_MODEL:-o3}" \
            --approval-policy auto-edit \
            --sandbox=none \
            "$(<"$REVIEW_PROMPT_PATH")"
        ;;

    claude)
        # Anthropic Claude — reads PROJECT-CLAUDE.md for project instructions
        # Pass the review prompt as the initial user message
        claude \
            --model "${CLAUDE_MODEL:-claude-opus-4-7}" \
            --print \
            "$(<"$REVIEW_PROMPT_PATH")"
        ;;

    gemini)
        # Google Gemini CLI
        # GEMINI.md in workspace provides project instructions
        gemini \
            --model "${GEMINI_MODEL:-gemini-2.5-pro}" \
            "$(<"$REVIEW_PROMPT_PATH")"
        ;;
esac

# --- Verify Phase 1 output --------------------------------------------------
echo "==> Verifying findings.json..."

FINDINGS_PATH="/workspace/.cr/findings.json"

if [[ ! -f "$FINDINGS_PATH" ]]; then
    echo "ERROR: Agent did not produce $FINDINGS_PATH" >&2
    exit 2
fi

# Validate JSON syntax
if ! python3 -c "import json; json.load(open('$FINDINGS_PATH'))" 2>/dev/null; then
    echo "ERROR: $FINDINGS_PATH is not valid JSON" >&2
    cat "$FINDINGS_PATH" >&2
    exit 2
fi

echo "==> findings.json verified ($(python3 -c "import json; d=json.load(open('$FINDINGS_PATH')); print(len(d.get('findings', [])), 'findings')"))"

# --- Phase 2: Post findings to VCS ------------------------------------------
echo "==> Phase 2: Posting findings..."

DRY_RUN_FLAG="${DRY_RUN:+--dry-run}"

python3 /app/src/post_findings.py \
    --findings "$FINDINGS_PATH" \
    --vcs "$VCS" \
    --pr "$PR_ID" \
    --repo "$REPO" \
    ${DRY_RUN_FLAG:-}

echo "==> codehawk complete."
