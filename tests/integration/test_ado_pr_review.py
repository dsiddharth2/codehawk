"""
Integration test for codehawk — full pipeline via ReviewJob.

Flow:
  1. Clone repo into results/workspace/, checkout PR branch
  2. ReviewJob.create_findings() — agent produces findings.json
  3. ReviewJob.publish_results() — Phase 2 scores/gates in dry-run mode

Requires:
    ADO_PAT        — Azure DevOps PAT with Code (Read) scope
    OPENAI_API_KEY — OpenAI API key

Run:
    pytest tests/integration/test_ado_pr_review.py -v -m integration -s

Run offline (Phase 2 only, no API keys needed):
    pytest tests/integration/test_ado_pr_review.py -v -m "not integration" -s
"""

import json
import os
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

import post_findings as pf
from config import Settings, reset_settings
from models.review_models import Finding
from review_job import ReviewJob, ReviewJobConfig

load_dotenv(Path(__file__).parent.parent.parent / ".env")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

_needs_ado = pytest.mark.skipif(
    not os.environ.get("ADO_PAT"),
    reason="ADO_PAT not set — skipping live ADO integration tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_ado_env():
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
    }
    for k, v in env.items():
        os.environ[k] = v
    reset_settings()
    return Settings()


def _clone_pr_workspace() -> tuple[Path, str]:
    """Clone the target repo and checkout the PR source branch."""
    settings = _setup_ado_env()

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
        print(f"\n  Re-using existing clone at {workspace}")
        subprocess.run(
            ["git", "fetch", "origin", source_branch],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=60,
        )
        subprocess.run(
            ["git", "checkout", source_branch],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(workspace), check=True, capture_output=True, text=True, timeout=60,
        )
    else:
        print(f"\n  Cloning {REPO} (branch: {source_branch})...")
        workspace.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--branch", source_branch, "--depth", "50",
             "--single-branch", auth_url, str(workspace)],
            check=True, capture_output=True, text=True, timeout=120,
        )

    (workspace / ".cr").mkdir(exist_ok=True)
    return workspace, source_branch


def _save_findings_artifact(findings_data: dict, label: str) -> Path:
    FINDINGS_OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = FINDINGS_OUTPUT_DIR / f"findings-pr{PR_ID}-{label}-{timestamp}.json"
    path.write_text(json.dumps(findings_data, indent=2), encoding="utf-8")
    print(f"\n  Artifact saved: {path}")
    return path


def _print_phase2_summary(output: dict):
    print(f"\n  Phase 2 results:")
    print(f"    Raw findings:      {output['filtering']['total_raw']}")
    print(f"    After confidence:  {output['filtering']['after_confidence_filter']}")
    print(f"    After cap:         {output['filtering']['after_cap']}")
    print(f"    Penalty:           {output['score']['total_penalty']} pts")
    stars_str = output['score']['overall_stars'].encode('ascii', 'replace').decode('ascii')
    print(f"    Stars:             {stars_str}")
    print(f"    Quality:           {output['score']['quality_level']}")
    print(f"    Gate passed:       {output['gate']['passed']}")
    if output.get("usage"):
        u = output["usage"]
        print(f"    Tokens:            {u['total_tokens']:,} (in:{u['input_tokens']:,} out:{u['output_tokens']:,})")
        if u.get("model"):
            print(f"    Model:             {u['model']}")
        if u.get("duration_seconds"):
            print(f"    Duration:          {u['duration_seconds']:.1f}s")
    if output.get("cost_estimate") and output["cost_estimate"].get("total_cost_usd") is not None:
        print(f"    Estimated cost:    ${output['cost_estimate']['total_cost_usd']:.4f}")


# ---------------------------------------------------------------------------
# Offline test helpers
# ---------------------------------------------------------------------------

def _write_findings(tmp_path, findings, fix_verifications=None, review_modes=None):
    data = {
        "pr_id": PR_ID,
        "repo": REPO,
        "vcs": "ado",
        "review_modes": review_modes or ["standard"],
        "tool_calls": len(findings),
        "agent": "test-harness",
        "findings": findings,
        "fix_verifications": fix_verifications or [],
    }
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(path)


# ===========================================================================
# Phase 1+2 — Full Pipeline via ReviewJob
# ===========================================================================


@integration
@_needs_ado
class TestFullPipeline:
    """Clone repo → ReviewJob.create_findings() → ReviewJob.publish_results()."""

    @pytest.fixture(autouse=True, scope="class")
    def pipeline_result(self, request):
        """Run the pipeline once, share results across all tests in this class."""
        settings = _setup_ado_env()
        workspace, source_branch = _clone_pr_workspace()
        print(f"  Branch: {source_branch}")

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        config = ReviewJobConfig(
            pr_id=PR_ID,
            repo=REPO,
            workspace=workspace,
            model=model,
            max_turns=40,
            prompt_path=REVIEW_PROMPT,
        )

        job = ReviewJob(config, settings=settings)

        # Phase 1
        findings_path = job.create_findings()
        findings_data = json.loads(findings_path.read_text(encoding="utf-8"))
        _save_findings_artifact(findings_data, "openai-api")

        # Phase 2
        phase2_output = job.publish_results(dry_run=True)
        _print_phase2_summary(phase2_output)

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


# ===========================================================================
# Phase 1 — Live ADO Activity Tests (no agent needed)
# ===========================================================================


@integration
@_needs_ado
class TestFetchPRDetails:
    def test_returns_valid_structure(self):
        from activities.fetch_pr_details_activity import FetchPRDetailsActivity
        from models.review_models import FetchPRDetailsInput

        settings = _setup_ado_env()
        activity = FetchPRDetailsActivity(settings=settings)
        result = activity.execute(FetchPRDetailsInput(pr_id=PR_ID, repository_id=REPO))

        assert result.pr_id == PR_ID
        assert result.title
        assert result.source_branch
        assert result.target_branch
        assert result.author
        assert isinstance(result.file_changes, list)
        assert len(result.file_changes) > 0

    def test_source_commit_id_present(self):
        from activities.fetch_pr_details_activity import FetchPRDetailsActivity
        from models.review_models import FetchPRDetailsInput

        settings = _setup_ado_env()
        activity = FetchPRDetailsActivity(settings=settings)
        result = activity.execute(FetchPRDetailsInput(pr_id=PR_ID, repository_id=REPO))

        assert result.source_commit_id
        assert len(result.source_commit_id) >= 7


@integration
@_needs_ado
class TestFetchPRComments:
    def test_returns_list(self):
        from activities.fetch_pr_comments_activity import FetchPRCommentsActivity

        settings = _setup_ado_env()
        activity = FetchPRCommentsActivity(settings=settings)
        threads = activity.execute(pr_id=PR_ID, repository_id=REPO)
        assert isinstance(threads, list)


# ===========================================================================
# Phase 2 — Offline Pipeline Tests (no ADO, no agent needed)
# ===========================================================================


class TestOfflineEmptyFindings:
    def test_empty_findings_perfect_score(self, tmp_path):
        path = _write_findings(tmp_path, [])
        output = pf.run(findings_path=path, dry_run=True)
        assert output["score"]["total_penalty"] == 0.0
        assert output["score"]["quality_level"] == "Perfect"
        assert output["gate"]["passed"] is True

    def test_empty_findings_output_structure(self, tmp_path):
        path = _write_findings(tmp_path, [])
        output = pf.run(findings_path=path, dry_run=True)
        assert output["pr_id"] == PR_ID
        assert output["repo"] == REPO
        assert output["dry_run"] is True
        assert output["filtering"]["total_raw"] == 0
        assert len(output["findings"]) == 0


class TestOfflineGateLogic:
    def test_single_critical_fails_gate(self, tmp_path):
        findings = [{
            "id": "cr-gate-001", "file": "src/foo.cs", "line": 1,
            "severity": "critical", "category": "security",
            "title": "Test critical", "message": "Should fail gate",
            "confidence": 0.9, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["gate"]["passed"] is False
        assert any("critical" in r.lower() for r in output["gate"]["reasons"])

    def test_suggestions_only_passes_gate(self, tmp_path):
        findings = [{
            "id": "cr-gate-002", "file": "src/bar.cs", "line": 5,
            "severity": "suggestion", "category": "best_practices",
            "title": "Minor style", "message": "Consider renaming",
            "confidence": 0.9, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["gate"]["passed"] is True

    def test_warnings_only_passes_gate(self, tmp_path):
        findings = [{
            "id": "cr-gate-003", "file": "src/baz.cs", "line": 10,
            "severity": "warning", "category": "performance",
            "title": "Slow loop", "message": "O(n^2) complexity",
            "confidence": 0.85, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["gate"]["passed"] is True


class TestOfflineConfidenceFilter:
    def test_low_confidence_filtered(self, tmp_path):
        findings = [{
            "id": "cr-low-001", "file": "src/bar.cs", "line": 5,
            "severity": "warning", "category": "performance",
            "title": "Low confidence", "message": "Should be filtered",
            "confidence": 0.5, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["filtering"]["filtered_low_confidence"] == 1
        assert output["filtering"]["after_confidence_filter"] == 0

    def test_at_threshold_kept(self, tmp_path):
        findings = [{
            "id": "cr-thresh-001", "file": "src/edge.cs", "line": 1,
            "severity": "warning", "category": "best_practices",
            "title": "At threshold", "message": "Confidence exactly 0.7",
            "confidence": 0.7, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["filtering"]["filtered_low_confidence"] == 0
        assert len(output["findings"]) == 1


class TestOfflinePerFileCap:
    def test_caps_at_five_per_file(self, tmp_path):
        findings = [
            {
                "id": f"cr-cap-{i:03d}", "file": "src/same_file.cs", "line": i * 10,
                "severity": "warning", "category": "best_practices",
                "title": f"Finding {i}", "message": f"Cap test {i}",
                "confidence": 0.9, "suggestion": None,
            }
            for i in range(10)
        ]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["filtering"]["after_cap"] <= 5


class TestOfflineOutputStructure:
    def test_findings_include_expected_fields(self, tmp_path):
        findings = [{
            "id": "cr-field-001", "file": "src/test.cs", "line": 42,
            "severity": "warning", "category": "security",
            "title": "Field check", "message": "Verifying output structure",
            "confidence": 0.95, "suggestion": "Fix it",
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert len(output["findings"]) == 1
        f = output["findings"][0]
        assert f["id"] == "cr-field-001"
        assert f["file"] == "src/test.cs"
        assert f["line"] == 42
        assert f["severity"] == "warning"
        assert f["category"] == "security"
        assert f["confidence"] == 0.95

    def test_score_fields_present(self, tmp_path):
        path = _write_findings(tmp_path, [])
        output = pf.run(findings_path=path, dry_run=True)
        score = output["score"]
        assert "total_penalty" in score
        assert "overall_stars" in score
        assert "quality_level" in score

    def test_filtering_fields_present(self, tmp_path):
        path = _write_findings(tmp_path, [])
        output = pf.run(findings_path=path, dry_run=True)
        filt = output["filtering"]
        assert "total_raw" in filt
        assert "after_confidence_filter" in filt
        assert "after_cap" in filt
        assert "deduped_already_posted" in filt
