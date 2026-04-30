"""
Full pipeline integration test — end-to-end code review.

Flow:
  1. Clone repo → build code graph
  2. ReviewJob.create_findings() — agent produces findings.json
  3. ReviewJob.publish_results() — Phase 2 scores/gates (dry-run)

Run:
    pytest tests/integration/test_full_pipeline.py -v -m integration -s
"""

import json
import os
from collections import Counter

import pytest

import post_findings as pf
from review_job import ReviewJob, ReviewJobConfig

from .conftest import (
    PR_ID, REPO, REVIEW_PROMPT,
    MAX_TURNS_INTEGRATION,
    integration, needs_ado,
    setup_ado_env, clone_pr_workspace,
    save_findings_artifact, print_phase2_summary,
)


@integration
@needs_ado
class TestFullPipeline:
    """Clone repo → ReviewJob.create_findings() → ReviewJob.publish_results()."""

    @pytest.fixture(autouse=True, scope="class")
    def pipeline_result(self, request):
        """Run the pipeline once, share results across all tests in this class."""
        settings = setup_ado_env()
        workspace, source_branch = clone_pr_workspace()
        print(f"  Branch: {source_branch}")

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        config = ReviewJobConfig(
            pr_id=PR_ID,
            repo=REPO,
            workspace=workspace,
            model=model,
            max_turns=MAX_TURNS_INTEGRATION,
            prompt_path=REVIEW_PROMPT,
        )

        job = ReviewJob(config, settings=settings)

        # Phase 1
        findings_path = job.create_findings()
        findings_data = json.loads(findings_path.read_text(encoding="utf-8"))
        save_findings_artifact(findings_data, "openai-api")

        # Phase 2
        phase2_output = job.publish_results(dry_run=True)
        print_phase2_summary(phase2_output)

        request.cls.findings_data = findings_data
        request.cls.phase2_output = phase2_output
        request.cls.workspace = workspace

    def test_findings_valid_schema(self):
        errors = pf._validate_schema(self.findings_data)
        assert errors == [], f"Schema validation failed: {errors}"
        assert self.findings_data["pr_id"] == PR_ID
        assert self.findings_data["repo"] == REPO
        assert self.findings_data["vcs"] == "ado"

    def test_phase2_scores_and_gates(self):
        output = self.phase2_output
        assert output["pr_id"] == PR_ID
        assert output["dry_run"] is True
        assert output["score"]["overall_stars"] is not None
        assert isinstance(output["gate"]["passed"], bool)

    def test_findings_reference_real_files(self):
        for f in self.findings_data.get("findings", []):
            raw = f["file"].lstrip("/")
            candidates = [
                self.workspace / raw,
                self.workspace / REPO / raw,
            ]
            assert any(p.exists() for p in candidates), (
                f"Finding {f['id']} references {f['file']} but it doesn't exist "
                f"(tried: {[str(p) for p in candidates]})"
            )

    def test_findings_respect_caps(self):
        findings = self.findings_data.get("findings", [])
        assert len(findings) <= 30, f"Returned {len(findings)} findings (max 30)"
        per_file = Counter(f["file"] for f in findings)
        for path, count in per_file.items():
            assert count <= 5, f"{path} has {count} findings (max 5)"

    def test_findings_have_valid_enums(self):
        valid_sev = {"critical", "warning", "suggestion"}
        valid_cat = {"security", "performance", "best_practices", "code_style", "documentation"}
        for f in self.findings_data.get("findings", []):
            assert f["severity"] in valid_sev, f"Invalid severity: {f['severity']}"
            assert f["category"] in valid_cat, f"Invalid category: {f['category']}"
            assert 0.0 <= f["confidence"] <= 1.0, f"Invalid confidence: {f['confidence']}"
