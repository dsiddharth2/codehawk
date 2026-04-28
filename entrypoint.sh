#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# codehawk entrypoint
#
# Runs the ReviewJob pipeline:
#   Phase 1: OpenAI agent produces /workspace/.cr/findings.json
#   Phase 2: Score, gate, and post comments to VCS
# ---------------------------------------------------------------------------

required_vars=(PR_ID REPO VCS OPENAI_API_KEY)
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

if [[ "$VCS" != "ado" && "$VCS" != "github" ]]; then
    echo "ERROR: VCS must be 'ado' or 'github', got: $VCS" >&2
    exit 1
fi

OPENAI_MODEL="${OPENAI_MODEL:-o3}"
MAX_TURNS="${MAX_TURNS:-40}"
DRY_RUN_FLAG="${DRY_RUN:+--dry-run}"

echo "==> codehawk: PR=$PR_ID REPO=$REPO VCS=$VCS MODEL=$OPENAI_MODEL"

python3 /app/src/run_agent.py \
    --pr-id "$PR_ID" \
    --repo "$REPO" \
    --workspace /workspace \
    --model "$OPENAI_MODEL" \
    --max-turns "$MAX_TURNS" \
    --prompt-file /app/commands/review-pr-core.md \
    --commit-id "${COMMIT_ID:-}" \
    ${DRY_RUN_FLAG:-}

echo "==> codehawk complete."
