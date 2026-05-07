"""
Batched pipeline integration test — mirrors what run_agent.py does.

Uses BatchReviewJob to run the full pipeline (pre-fetch, filter, graph,
batch, merge, Phase 2 scoring) end-to-end.

Run:
    pytest tests/integration/test_batched_pipeline.py -v -m integration -s
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytest

from batch_review_job import BatchReviewJob

from .conftest import (
    PR_ID, REPO, REVIEW_PROMPT,
    PROJECT_ROOT,
    integration, needs_ado,
    setup_ado_env, clone_pr_workspace,
    save_findings_artifact, log_phase2_summary,
)

_log = logging.getLogger(__name__)


def _setup_file_logging() -> tuple[Path, logging.FileHandler]:
    results_dir = PROJECT_ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = results_dir / f"batched-run-{PR_ID}-{timestamp}.log"

    handler = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Attach to root logger so ALL loggers (codehawk.*, activity loggers, etc.) are captured
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    codehawk_root = logging.getLogger("codehawk")
    codehawk_root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    ))
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in codehawk_root.handlers):
        codehawk_root.addHandler(console)

    _log.info("Logging to: %s", log_path)
    return log_path, handler


def _teardown_file_logging(handler: logging.FileHandler):
    logging.getLogger().removeHandler(handler)
    handler.close()


@integration
@needs_ado
class TestBatchedPipeline:
    """BatchReviewJob end-to-end — same flow as run_agent.py."""

    @pytest.fixture(autouse=True, scope="class")
    def pipeline_result(self, request):
        """Run BatchReviewJob once, share results across all tests."""
        log_path, log_handler = _setup_file_logging()

        settings = setup_ado_env()
        workspace, source_branch = clone_pr_workspace()
        _log.info("Branch: %s", source_branch)

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        job = BatchReviewJob(
            pr_id=PR_ID,
            repo=REPO,
            workspace=workspace,
            model=model,
            prompt_path=REVIEW_PROMPT,
            settings=settings,
        )

        try:
            # Read findings even if post_findings (Phase 2) fails
            phase2_error = None
            findings_path = workspace / ".cr" / "findings.json"

            try:
                output = job.run(dry_run=False)
            except Exception as exc:
                _log.error("Pipeline error (Phase 2 may have failed): %s", exc)
                phase2_error = exc
                output = None

            if findings_path.exists():
                findings_data = json.loads(findings_path.read_text(encoding="utf-8"))
            else:
                findings_data = {"findings": []}

            save_findings_artifact(findings_data, "batched")
            if output:
                log_phase2_summary(output)

            results_dir = PROJECT_ROOT / "results"
            results_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            run_log = {
                "run_date": datetime.now().isoformat(),
                "pr_id": PR_ID,
                "repo": REPO,
                "findings": findings_data,
                "phase2": output,
                "error": str(phase2_error) if phase2_error else None,
            }
            log_file = results_dir / f"batched-run-{PR_ID}-{timestamp}.json"
            log_file.write_text(json.dumps(run_log, indent=2), encoding="utf-8")
            _log.info("Run log saved: %s", log_file)

            request.cls.findings_data = findings_data
            request.cls.phase2_output = output
            request.cls.workspace = workspace
            request.cls.phase2_error = phase2_error
        finally:
            _teardown_file_logging(log_handler)
            _log.info("Full log saved: %s", log_path)

    def test_pipeline_ran(self):
        """Verify the pipeline produced output."""
        assert self.findings_data is not None, "Pipeline did not produce findings"
        _log.info("Findings: %d, Phase 2 error: %s",
                   len(self.findings_data.get("findings", [])), self.phase2_error)
