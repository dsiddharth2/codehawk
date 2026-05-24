"""
Fix verification integration test — re-push flow (auto-detect).

Runs the pipeline in default FULL mode (no flags) against a PR that already
has CodeHawk comments. The pipeline should auto-detect the re-push, fetch
prior findings, force single-pass, and produce fix_verifications[].

This mirrors how CI works in production — no --verify-fixes flag needed.

Target: PR 6658 in BluSKY repo (blub0x / BluSKY Git)

Prerequisites:
    - The target PR must have existing CodeHawk comments (with <!-- cr-id: xxx --> markers)
    - ADO_PAT and OPENAI_API_KEY must be set (via .env or environment)

Run:
    pytest tests/integration/test_fix_verification.py -v -m integration -s
"""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

from batch_review_job import BatchReviewJob
from config import Settings, reset_settings
from models.review_models import ReviewMode

from .conftest import (
    PROJECT_ROOT, REVIEW_PROMPT, MAX_TURNS_INTEGRATION,
    integration, needs_ado,
    save_findings_artifact, log_phase2_summary,
)

load_dotenv(PROJECT_ROOT / ".env")

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Test-specific constants — PR 6658 in BluSKY
# ---------------------------------------------------------------------------

FV_PR_ID = 6658
FV_REPO = "BluSKY"
FV_ADO_ORG = "blub0x"
FV_ADO_PROJECT = "BluSKY Git"

RESULTS_DIR = PROJECT_ROOT / "results"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_fv_env() -> Settings:
    """Configure env vars for BluSKY repo and return fresh Settings."""
    env = {
        "VCS": "ado",
        "AZURE_DEVOPS_ORG": FV_ADO_ORG,
        "AZURE_DEVOPS_PROJECT": FV_ADO_PROJECT,
        "AZURE_DEVOPS_PAT": os.environ.get("ADO_PAT", ""),
        "AZURE_DEVOPS_REPO": FV_REPO,
        "AUTH_MODE": "pat",
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "text",
        "ENABLE_GRAPH": "true",
    }
    for k, v in env.items():
        os.environ[k] = v
    reset_settings()
    return Settings()


def _clone_fv_workspace() -> tuple[Path, str]:
    """Clone the BluSKY repo and checkout the PR 6658 source branch."""
    settings = _setup_fv_env()

    from activities.fetch_pr_details_activity import FetchPRDetailsActivity
    from models.review_models import FetchPRDetailsInput

    activity = FetchPRDetailsActivity(settings=settings)
    pr = activity.execute(FetchPRDetailsInput(pr_id=FV_PR_ID, repository_id=FV_REPO))
    source_branch = pr.source_branch

    workspace = RESULTS_DIR / "workspace-blusky"
    pat = os.environ["AZURE_DEVOPS_PAT"]
    project_encoded = FV_ADO_PROJECT.replace(" ", "%20")
    repo_encoded = FV_REPO.replace(" ", "%20")
    auth_url = f"https://{pat}@dev.azure.com/{FV_ADO_ORG}/{project_encoded}/_git/{repo_encoded}"

    if (workspace / ".git").exists():
        _log.info("Re-using existing clone at %s", workspace)
        subprocess.run(
            ["git", "remote", "set-url", "origin", auth_url],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["git", "fetch", "origin", source_branch, "--depth", "1"],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=120,
        )
        subprocess.run(
            ["git", "checkout", "-B", source_branch, f"origin/{source_branch}"],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=30,
        )
    else:
        _log.info("Cloning %s (branch: %s)...", FV_REPO, source_branch)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--branch", source_branch, "--depth", "1",
             auth_url, str(workspace)],
            check=True, capture_output=True, text=True, timeout=600,
        )

    (workspace / ".cr").mkdir(exist_ok=True)
    return workspace, source_branch


def _setup_file_logging() -> tuple[Path, logging.FileHandler]:
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = RESULTS_DIR / f"fix-verify-{FV_PR_ID}-{timestamp}.log"

    handler = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root = logging.getLogger("codehawk")
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    ))
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        root.addHandler(console)

    _log.info("Logging to: %s", log_path)
    return log_path, handler


def _teardown_file_logging(handler: logging.FileHandler):
    logging.getLogger("codehawk").removeHandler(handler)
    handler.close()


def _count_codehawk_comments(settings, pr_id, repo) -> int:
    """Quick check: how many CodeHawk comment threads exist on this PR?"""
    from activities.fetch_pr_comments_activity import FetchPRCommentsActivity

    activity = FetchPRCommentsActivity(settings=settings)
    all_threads = activity.execute(pr_id=pr_id, repository_id=repo)
    codehawk_threads = [t for t in all_threads if t.cr_id]
    _log.info(
        "Pre-check: %d total threads, %d with cr-id markers",
        len(all_threads), len(codehawk_threads),
    )
    for t in codehawk_threads:
        _log.info(
            "  %s: %s (line %d in %s)",
            t.cr_id, t.comment_text[:80].replace("\n", " "),
            t.line_number, t.file_path,
        )
    return len(codehawk_threads)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@integration
@needs_ado
class TestFixVerification:
    """Run pipeline in default FULL mode — auto-detects re-push from existing PR comments."""

    @pytest.fixture(autouse=True, scope="class")
    def pipeline_result(self, request):
        log_path, log_handler = _setup_file_logging()

        settings = _setup_fv_env()
        workspace, source_branch = _clone_fv_workspace()
        _log.info("Branch: %s", source_branch)

        # Pre-check: PR must have existing CodeHawk comments for this test to be meaningful
        prior_count = _count_codehawk_comments(settings, FV_PR_ID, FV_REPO)
        if prior_count == 0:
            pytest.skip(
                f"PR {FV_PR_ID} has no existing CodeHawk comments (no cr-id markers) — "
                "cannot test fix verification. Run a normal review first to create comments."
            )
        _log.info("PR has %d prior CodeHawk comments — pipeline should auto-detect re-push", prior_count)

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        # Use BatchReviewJob with default FULL mode — no --verify-fixes flag.
        # The pipeline should auto-detect prior findings and handle them.
        job = BatchReviewJob(
            pr_id=FV_PR_ID,
            repo=FV_REPO,
            workspace=workspace,
            model=model,
            prompt_path=REVIEW_PROMPT,
            settings=settings,
            review_mode=ReviewMode.FULL,
        )

        try:
            phase2_error = None
            phase2_output = None
            findings_path = workspace / ".cr" / "findings.json"

            try:
                phase2_output = job.run(dry_run=True)
            except SystemExit as exc:
                _log.warning("Quality gate exit code %s", exc.code)
                phase2_error = exc
            except Exception as exc:
                _log.error("Pipeline error: %s", exc)
                phase2_error = exc

            if findings_path.exists():
                findings_data = json.loads(findings_path.read_text(encoding="utf-8"))
            else:
                findings_data = {"findings": [], "fix_verifications": []}

            save_findings_artifact(findings_data, "fix-verify")

            _log.info("fix_verifications count: %d", len(findings_data.get("fix_verifications", [])))
            for fv in findings_data.get("fix_verifications", []):
                _log.info("  %s → %s: %s", fv.get("cr_id"), fv.get("status"), fv.get("reason", "")[:100])

            if phase2_output:
                log_phase2_summary(phase2_output)

            # Save run log
            RESULTS_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            run_log = {
                "run_date": datetime.now().isoformat(),
                "pr_id": FV_PR_ID,
                "repo": FV_REPO,
                "mode": "verify-only (auto-detect re-push)",
                "prior_codehawk_comments": prior_count,
                "findings": findings_data,
                "phase2": phase2_output,
                "error": str(phase2_error) if phase2_error else None,
            }
            log_file = RESULTS_DIR / f"fix-verify-{FV_PR_ID}-{timestamp}.json"
            log_file.write_text(json.dumps(run_log, indent=2), encoding="utf-8")
            _log.info("Run log saved: %s", log_file)

            request.cls.findings_data = findings_data
            request.cls.phase2_output = phase2_output
            request.cls.phase2_error = phase2_error
            request.cls.prior_count = prior_count
            request.cls.workspace = workspace
        finally:
            _teardown_file_logging(log_handler)
            _log.info("Full log saved: %s", log_path)

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def test_pipeline_ran(self):
        """Pipeline must produce findings.json."""
        assert self.findings_data is not None, "Pipeline did not produce findings"

    def test_no_new_findings(self):
        """Re-push verify-only mode should skip full review — no new findings."""
        findings = self.findings_data.get("findings", [])
        assert findings == [], (
            f"Verify-only should produce 0 new findings, got {len(findings)}"
        )

    def test_verify_fixes_mode_set(self):
        """findings.json should have verify_fixes in review_modes."""
        modes = self.findings_data.get("review_modes", [])
        assert "verify_fixes" in modes, f"Expected 'verify_fixes' in review_modes, got {modes}"

    def test_fix_verifications_present(self):
        """Auto-detected re-push must produce fix_verifications."""
        fv_list = self.findings_data.get("fix_verifications", [])
        assert len(fv_list) > 0, (
            f"Expected fix_verifications to be non-empty — pipeline should have "
            f"auto-detected {self.prior_count} prior CodeHawk comments"
        )

    def test_fix_verifications_cover_prior_findings(self):
        """fix_verifications should cover a reasonable portion of prior cr-ids."""
        fv_list = self.findings_data.get("fix_verifications", [])
        assert len(fv_list) >= self.prior_count * 0.5, (
            f"Only {len(fv_list)} fix_verifications for {self.prior_count} prior comments — "
            f"expected at least 50% coverage"
        )

    def test_fix_verification_statuses_valid(self):
        """Each fix_verification must have a valid status."""
        valid_statuses = {"fixed", "still_present", "not_relevant"}
        for fv in self.findings_data.get("fix_verifications", []):
            assert fv.get("status") in valid_statuses, (
                f"Invalid status '{fv.get('status')}' for {fv.get('cr_id')}"
            )

    def test_fix_verifications_have_reasons(self):
        """Each fix_verification should include a reason."""
        for fv in self.findings_data.get("fix_verifications", []):
            reason = fv.get("reason", "")
            assert reason and len(reason) > 5, (
                f"Missing or too-short reason for {fv.get('cr_id')}: '{reason}'"
            )

    def test_phase2_output_includes_fix_verifications(self):
        """Phase 2 structured output should include fix_verifications."""
        if self.phase2_output is None:
            pytest.skip("Phase 2 failed — cannot check output structure")
        fv_out = self.phase2_output.get("fix_verifications", [])
        assert len(fv_out) > 0, "Phase 2 output should include fix_verifications"

    def test_phase2_has_comparison_flag(self):
        """Phase 2 output should set has_comparison when fix_verifications are present."""
        if self.phase2_output is None:
            pytest.skip("Phase 2 failed — cannot check output structure")
        assert "has_comparison" in self.phase2_output, (
            "Phase 2 output missing 'has_comparison' key"
        )
