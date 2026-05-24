"""
End-to-end integration test: full review → fix verification, both posting to PR.

Phase 1: Run a full code review (NOT dry-run) against the target PR.
         Posts inline findings as ADO comment threads.
Phase 2: Re-run the pipeline in FULL mode. Because Phase 1 left CodeHawk
         comments (cr-id markers), the pipeline auto-detects a re-push and
         switches to verify-only mode. This also posts results to the PR.

Target: PR 6697 in BluSKYFunctionApps (blub0x / BluSKY Git)

Run:
    pytest tests/integration/test_e2e_review_then_verify.py -v -m integration -s
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
# Test-specific constants
# ---------------------------------------------------------------------------

E2E_PR_ID = 6698
E2E_REPO = "BluSKYFunctionApps"
E2E_ADO_ORG = "blub0x"
E2E_ADO_PROJECT = "BluSKY Git"

RESULTS_DIR = PROJECT_ROOT / "results"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_env() -> Settings:
    env = {
        "VCS": "ado",
        "AZURE_DEVOPS_ORG": E2E_ADO_ORG,
        "AZURE_DEVOPS_PROJECT": E2E_ADO_PROJECT,
        "AZURE_DEVOPS_PAT": os.environ.get("ADO_PAT", ""),
        "AZURE_DEVOPS_REPO": E2E_REPO,
        "AUTH_MODE": "pat",
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "text",
        "ENABLE_GRAPH": "true",
    }
    for k, v in env.items():
        os.environ[k] = v
    reset_settings()
    return Settings()


def _clone_workspace() -> tuple[Path, str]:
    settings = _setup_env()

    from activities.fetch_pr_details_activity import FetchPRDetailsActivity
    from models.review_models import FetchPRDetailsInput

    activity = FetchPRDetailsActivity(settings=settings)
    pr = activity.execute(FetchPRDetailsInput(pr_id=E2E_PR_ID, repository_id=E2E_REPO))
    source_branch = pr.source_branch

    workspace = RESULTS_DIR / "workspace-e2e"
    pat = os.environ["AZURE_DEVOPS_PAT"]
    project_encoded = E2E_ADO_PROJECT.replace(" ", "%20")
    repo_encoded = E2E_REPO.replace(" ", "%20")
    auth_url = f"https://{pat}@dev.azure.com/{E2E_ADO_ORG}/{project_encoded}/_git/{repo_encoded}"

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
        _log.info("Cloning %s (branch: %s)...", E2E_REPO, source_branch)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--branch", source_branch, "--depth", "1",
             auth_url, str(workspace)],
            check=True, capture_output=True, text=True, timeout=600,
        )

    (workspace / ".cr").mkdir(exist_ok=True)
    return workspace, source_branch


def _setup_file_logging(label: str) -> tuple[Path, logging.FileHandler]:
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = RESULTS_DIR / f"e2e-{label}-{E2E_PR_ID}-{timestamp}.log"

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
    from activities.fetch_pr_comments_activity import FetchPRCommentsActivity

    activity = FetchPRCommentsActivity(settings=settings)
    all_threads = activity.execute(pr_id=pr_id, repository_id=repo)
    codehawk_threads = [t for t in all_threads if t.cr_id]
    _log.info(
        "CodeHawk comments: %d total threads, %d with cr-id markers",
        len(all_threads), len(codehawk_threads),
    )
    return len(codehawk_threads)


def _save_run_log(label: str, data: dict):
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = RESULTS_DIR / f"e2e-{label}-{E2E_PR_ID}-{timestamp}.json"
    log_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _log.info("Run log saved: %s", log_file)


# ---------------------------------------------------------------------------
# Phase 1: Full review (posts comments to PR)
# ---------------------------------------------------------------------------

@integration
@needs_ado
class TestE2EFullReview:
    """Phase 1 — full code review, dry_run=False, posts findings to the PR."""

    @pytest.fixture(autouse=True, scope="class")
    def pipeline_result(self, request):
        log_path, log_handler = _setup_file_logging("full-review")

        settings = _setup_env()
        workspace, source_branch = _clone_workspace()
        _log.info("Phase 1 — Full review on branch: %s", source_branch)

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        job = BatchReviewJob(
            pr_id=E2E_PR_ID,
            repo=E2E_REPO,
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
                phase2_output = job.run(dry_run=False)
            except SystemExit as exc:
                _log.warning("Quality gate exit code %s", exc.code)
                phase2_error = exc
            except Exception as exc:
                _log.error("Pipeline error: %s", exc)
                phase2_error = exc

            if findings_path.exists():
                findings_data = json.loads(findings_path.read_text(encoding="utf-8"))
            else:
                findings_data = {"findings": []}

            save_findings_artifact(findings_data, "e2e-full-review")

            if phase2_output:
                log_phase2_summary(phase2_output)

            _save_run_log("full-review", {
                "run_date": datetime.now().isoformat(),
                "pr_id": E2E_PR_ID,
                "repo": E2E_REPO,
                "mode": "full-review (dry_run=False)",
                "findings": findings_data,
                "phase2": phase2_output,
                "error": str(phase2_error) if phase2_error else None,
            })

            request.cls.findings_data = findings_data
            request.cls.phase2_output = phase2_output
            request.cls.phase2_error = phase2_error
            request.cls.workspace = workspace
        finally:
            _teardown_file_logging(log_handler)
            _log.info("Phase 1 log saved: %s", log_path)

    # ------------------------------------------------------------------
    # Phase 1 assertions
    # ------------------------------------------------------------------

    def test_pipeline_ran(self):
        assert self.findings_data is not None, "Pipeline did not produce findings"

    def test_findings_produced(self):
        findings = self.findings_data.get("findings", [])
        _log.info("Phase 1 produced %d findings", len(findings))
        assert len(findings) > 0, "Full review should produce at least one finding"

    def test_findings_have_required_fields(self):
        for f in self.findings_data.get("findings", []):
            assert f.get("file"), f"Finding missing 'file': {f}"
            assert f.get("title"), f"Finding missing 'title': {f}"
            assert f.get("severity"), f"Finding missing 'severity': {f}"

    def test_phase2_ran(self):
        assert self.phase2_output is not None or self.phase2_error is not None, (
            "Neither phase2 output nor error — pipeline may not have run"
        )

    def test_comments_posted_to_pr(self):
        """After dry_run=False, the PR should have CodeHawk comment threads."""
        settings = _setup_env()
        count = _count_codehawk_comments(settings, E2E_PR_ID, E2E_REPO)
        _log.info("Post-review cr-id comment count: %d", count)
        assert count > 0, (
            "No CodeHawk comments found on PR after full review with dry_run=False"
        )


# ---------------------------------------------------------------------------
# Phase 2: Fix verification (posts results to PR)
# ---------------------------------------------------------------------------

@integration
@needs_ado
class TestE2EFixVerification:
    """Phase 2 — re-run pipeline; auto-detects prior comments, runs verify-only, posts results."""

    @pytest.fixture(autouse=True, scope="class")
    def pipeline_result(self, request):
        log_path, log_handler = _setup_file_logging("fix-verify")

        settings = _setup_env()
        workspace, source_branch = _clone_workspace()
        _log.info("Phase 2 — Fix verification on branch: %s", source_branch)

        prior_count = _count_codehawk_comments(settings, E2E_PR_ID, E2E_REPO)
        if prior_count == 0:
            pytest.skip(
                f"PR {E2E_PR_ID} has no existing CodeHawk comments — "
                "run TestE2EFullReview first to create them."
            )
        _log.info("PR has %d prior CodeHawk comments — should auto-detect re-push", prior_count)

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        job = BatchReviewJob(
            pr_id=E2E_PR_ID,
            repo=E2E_REPO,
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
                phase2_output = job.run(dry_run=False)
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

            save_findings_artifact(findings_data, "e2e-fix-verify")

            _log.info("fix_verifications count: %d", len(findings_data.get("fix_verifications", [])))
            for fv in findings_data.get("fix_verifications", []):
                _log.info("  %s → %s: %s", fv.get("cr_id"), fv.get("status"), fv.get("reason", "")[:100])

            if phase2_output:
                log_phase2_summary(phase2_output)

            _save_run_log("fix-verify", {
                "run_date": datetime.now().isoformat(),
                "pr_id": E2E_PR_ID,
                "repo": E2E_REPO,
                "mode": "fix-verify (auto-detect, dry_run=False)",
                "prior_codehawk_comments": prior_count,
                "findings": findings_data,
                "phase2": phase2_output,
                "error": str(phase2_error) if phase2_error else None,
            })

            request.cls.findings_data = findings_data
            request.cls.phase2_output = phase2_output
            request.cls.phase2_error = phase2_error
            request.cls.prior_count = prior_count
            request.cls.workspace = workspace
        finally:
            _teardown_file_logging(log_handler)
            _log.info("Phase 2 log saved: %s", log_path)

    # ------------------------------------------------------------------
    # Phase 2 assertions
    # ------------------------------------------------------------------

    def test_pipeline_ran(self):
        assert self.findings_data is not None, "Pipeline did not produce findings"

    def test_no_new_findings(self):
        findings = self.findings_data.get("findings", [])
        assert findings == [], (
            f"Verify-only should produce 0 new findings, got {len(findings)}"
        )

    def test_verify_fixes_mode_set(self):
        modes = self.findings_data.get("review_modes", [])
        assert "verify_fixes" in modes, f"Expected 'verify_fixes' in review_modes, got {modes}"

    def test_fix_verifications_present(self):
        fv_list = self.findings_data.get("fix_verifications", [])
        assert len(fv_list) > 0, (
            f"Expected fix_verifications to be non-empty — pipeline should have "
            f"auto-detected {self.prior_count} prior CodeHawk comments"
        )

    def test_fix_verifications_cover_prior_findings(self):
        fv_list = self.findings_data.get("fix_verifications", [])
        assert len(fv_list) >= self.prior_count * 0.5, (
            f"Only {len(fv_list)} fix_verifications for {self.prior_count} prior comments — "
            f"expected at least 50% coverage"
        )

    def test_fix_verification_statuses_valid(self):
        valid_statuses = {"fixed", "still_present", "not_relevant"}
        for fv in self.findings_data.get("fix_verifications", []):
            assert fv.get("status") in valid_statuses, (
                f"Invalid status '{fv.get('status')}' for {fv.get('cr_id')}"
            )

    def test_fix_verifications_have_reasons(self):
        for fv in self.findings_data.get("fix_verifications", []):
            reason = fv.get("reason", "")
            assert reason and len(reason) > 5, (
                f"Missing or too-short reason for {fv.get('cr_id')}: '{reason}'"
            )

    def test_phase2_output_includes_fix_verifications(self):
        if self.phase2_output is None:
            pytest.skip("Phase 2 failed — cannot check output structure")
        fv_out = self.phase2_output.get("fix_verifications", [])
        assert len(fv_out) > 0, "Phase 2 output should include fix_verifications"

    def test_phase2_has_comparison_flag(self):
        if self.phase2_output is None:
            pytest.skip("Phase 2 failed — cannot check output structure")
        assert "has_comparison" in self.phase2_output, (
            "Phase 2 output missing 'has_comparison' key"
        )

    def test_verify_comments_posted_to_pr(self):
        """After fix verification with dry_run=False, PR should still have CodeHawk threads."""
        settings = _setup_env()
        count = _count_codehawk_comments(settings, E2E_PR_ID, E2E_REPO)
        _log.info("Post-verify cr-id comment count: %d", count)
        assert count > 0, "Expected CodeHawk comments on PR after fix verification"
