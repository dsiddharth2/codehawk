"""
Shared fixtures and helpers for integration tests.

All integration tests require:
    ADO_PAT        — Azure DevOps PAT with Code (Read) scope
    OPENAI_API_KEY — OpenAI API key (for pipeline tests only)
"""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

from config import Settings, reset_settings

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logger = logging.getLogger("codehawk.test")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TURNS_INTEGRATION = 30  # cap agent turns in integration tests to control cost

# Large PR
#PR_ID = 6435

#Small PR
PR_ID = 6571
REPO = "BluSKYFunctionApps"
ADO_ORG = "blub0x"
ADO_PROJECT = "BluSKY Git"

PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
COMMANDS_DIR = PROJECT_ROOT / "commands"
REVIEW_PROMPT = COMMANDS_DIR / "review-pr-core.md"
RESULTS_DIR = PROJECT_ROOT / "results"
FINDINGS_OUTPUT_DIR = RESULTS_DIR / "findings"


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

integration = pytest.mark.integration

needs_ado = pytest.mark.skipif(
    not os.environ.get("ADO_PAT"),
    reason="ADO_PAT not set — skipping live ADO integration tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def setup_ado_env() -> Settings:
    """Push ADO env vars into os.environ and return a fresh Settings."""
    env = {
        "VCS": "ado",
        "AZURE_DEVOPS_ORG": ADO_ORG,
        "AZURE_DEVOPS_PROJECT": ADO_PROJECT,
        "AZURE_DEVOPS_PAT": os.environ.get("ADO_PAT", ""),
        "AZURE_DEVOPS_REPO": REPO,
        "AUTH_MODE": "pat",
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "text",
        "ENABLE_GRAPH": "true",
    }
    for k, v in env.items():
        os.environ[k] = v
    reset_settings()
    return Settings()


def clone_pr_workspace() -> tuple[Path, str]:
    """Clone the target repo and checkout the PR source branch."""
    settings = setup_ado_env()

    from activities.fetch_pr_details_activity import FetchPRDetailsActivity
    from models.review_models import FetchPRDetailsInput

    activity = FetchPRDetailsActivity(settings=settings)
    pr = activity.execute(FetchPRDetailsInput(pr_id=PR_ID, repository_id=REPO))
    source_branch = pr.source_branch

    workspace = RESULTS_DIR / "workspace"
    pat = os.environ["AZURE_DEVOPS_PAT"]
    project_encoded = ADO_PROJECT.replace(" ", "%20")
    auth_url = f"https://{pat}@dev.azure.com/{ADO_ORG}/{project_encoded}/_git/{REPO}"

    if (workspace / ".git").exists():
        logger.info("Re-using existing clone at %s", workspace)
        # Update remote URL (handles PAT rotation) and open up refspec for any branch
        subprocess.run(
            ["git", "remote", "set-url", "origin", auth_url],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["git", "fetch", "origin", source_branch, "--depth", "50"],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=60,
        )
        subprocess.run(
            ["git", "checkout", "-B", source_branch, f"origin/{source_branch}"],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=30,
        )
    else:
        logger.info("Cloning %s (branch: %s)...", REPO, source_branch)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--branch", source_branch, "--depth", "50",
             auth_url, str(workspace)],
            check=True, capture_output=True, text=True, timeout=120,
        )

    (workspace / ".cr").mkdir(exist_ok=True)
    return workspace, source_branch


def save_findings_artifact(findings_data: dict, label: str) -> Path:
    FINDINGS_OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = FINDINGS_OUTPUT_DIR / f"findings-pr{PR_ID}-{label}-{timestamp}.json"
    path.write_text(json.dumps(findings_data, indent=2), encoding="utf-8")
    logger.info("Artifact saved: %s", path)
    return path


def log_phase2_summary(output: dict):
    logger.info("Phase 2 results:")
    logger.info("  Raw findings:      %d", output["filtering"]["total_raw"])
    logger.info("  After confidence:  %d", output["filtering"]["after_confidence_filter"])
    logger.info("  After cap:         %d", output["filtering"]["after_cap"])
    logger.info("  Penalty:           %s pts", output["score"]["total_penalty"])
    stars_str = output["score"]["overall_stars"].encode("ascii", "replace").decode("ascii")
    logger.info("  Stars:             %s", stars_str)
    logger.info("  Quality:           %s", output["score"]["quality_level"])
    logger.info("  Gate passed:       %s", output["gate"]["passed"])
    if output.get("usage"):
        u = output["usage"]
        logger.info("  Tokens:            %s (in:%s out:%s)",
                     f"{u['total_tokens']:,}", f"{u['input_tokens']:,}", f"{u['output_tokens']:,}")
        if u.get("model"):
            logger.info("  Model:             %s", u["model"])
        if u.get("duration_seconds"):
            logger.info("  Duration:          %.1fs", u["duration_seconds"])
    if output.get("cost_estimate") and output["cost_estimate"].get("total_cost_usd") is not None:
        logger.info("  Estimated cost:    $%.4f", output["cost_estimate"]["total_cost_usd"])
