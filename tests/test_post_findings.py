"""
Unit tests for post_findings.py — Phase 2 engine.

Tests: confidence filter, cap logic, cr-id dedup, gate thresholds,
fix verifications, and dry-run end-to-end output.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.review_models import Finding, FindingsFile, FixVerification
import post_findings as pf


# ---------------------------------------------------------------------------
# filter_by_confidence
# ---------------------------------------------------------------------------

class TestFilterByConfidence:
    def _make_finding(self, id, confidence):
        return Finding(
            id=id, file="f.py", line=1, severity="suggestion",
            category="best_practices", title="t", message="m",
            confidence=confidence
        )

    def test_drops_below_threshold(self):
        findings = [
            self._make_finding("cr-001", 0.95),
            self._make_finding("cr-002", 0.65),
            self._make_finding("cr-003", 0.70),
        ]
        result = pf.filter_by_confidence(findings, min_confidence=0.70)
        ids = [f.id for f in result]
        assert "cr-001" in ids
        assert "cr-002" not in ids
        assert "cr-003" in ids

    def test_keeps_exactly_at_threshold(self):
        f = self._make_finding("cr-001", 0.70)
        result = pf.filter_by_confidence([f], min_confidence=0.70)
        assert len(result) == 1

    def test_empty_input(self):
        assert pf.filter_by_confidence([], 0.7) == []

    def test_all_filtered(self):
        findings = [self._make_finding("cr-001", 0.5), self._make_finding("cr-002", 0.3)]
        assert pf.filter_by_confidence(findings, 0.9) == []


# ---------------------------------------------------------------------------
# cap_findings
# ---------------------------------------------------------------------------

class TestCapFindings:
    def _make_finding(self, id, file="f.py", severity="suggestion"):
        return Finding(
            id=id, file=file, line=1, severity=severity,
            category="best_practices", title="t", message="m",
            confidence=0.9
        )

    def test_caps_total_at_30(self):
        findings = [self._make_finding(f"cr-{i:03d}", file=f"f{i}.py") for i in range(50)]
        result = pf.cap_findings(findings, max_total=30, max_per_file=100)
        assert len(result) <= 30

    def test_caps_per_file_at_5(self):
        findings = [self._make_finding(f"cr-{i:03d}", file="same.py") for i in range(20)]
        result = pf.cap_findings(findings, max_total=100, max_per_file=5)
        assert sum(1 for f in result if f.file == "same.py") <= 5

    def test_prioritises_critical_over_suggestion(self):
        findings = [
            self._make_finding("cr-001", file="a.py", severity="suggestion"),
            self._make_finding("cr-002", file="b.py", severity="critical"),
        ]
        result = pf.cap_findings(findings, max_total=1, max_per_file=5)
        assert len(result) == 1
        assert result[0].id == "cr-002"

    def test_empty_input(self):
        assert pf.cap_findings([], 30, 5) == []

    def test_respects_both_limits_simultaneously(self):
        findings = []
        for file_idx in range(4):
            for sev_idx in range(10):
                findings.append(self._make_finding(
                    f"cr-{file_idx:02d}-{sev_idx:02d}",
                    file=f"f{file_idx}.py",
                    severity="suggestion"
                ))
        # 4 files × 5 max = 20 max total (under total cap of 30)
        result = pf.cap_findings(findings, max_total=30, max_per_file=5)
        for file in {f.file for f in result}:
            per_file = [f for f in result if f.file == file]
            assert len(per_file) <= 5
        assert len(result) <= 30


# ---------------------------------------------------------------------------
# _validate_schema
# ---------------------------------------------------------------------------

class TestValidateSchema:
    def test_valid_data_returns_no_errors(self, sample_raw):
        errors = pf._validate_schema(sample_raw)
        assert errors == []

    def test_missing_required_field(self, sample_raw):
        del sample_raw["pr_id"]
        errors = pf._validate_schema(sample_raw)
        assert any("pr_id" in e for e in errors)

    def test_invalid_vcs_value(self, sample_raw):
        sample_raw["vcs"] = "bitbucket"
        errors = pf._validate_schema(sample_raw)
        assert len(errors) > 0

    def test_missing_finding_required_field(self, sample_raw):
        del sample_raw["findings"][0]["severity"]
        errors = pf._validate_schema(sample_raw)
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

class TestEvaluateGate:
    def _make_finding(self, severity="suggestion"):
        return Finding(
            id="cr-001", file="f.py", line=1, severity=severity,
            category="security", title="t", message="m", confidence=0.9
        )

    def test_no_criticals_passes_by_default(self):
        findings = [self._make_finding("suggestion")]
        result = pf._evaluate_gate(None, findings, {})
        assert result["passed"] is True

    def test_critical_finding_fails_gate_by_default(self):
        findings = [self._make_finding("critical")]
        result = pf._evaluate_gate(None, findings, {})
        assert result["passed"] is False
        assert any("critical" in r for r in result["reasons"])

    def test_fail_on_critical_false_ignores_criticals(self):
        findings = [self._make_finding("critical")]
        result = pf._evaluate_gate(None, findings, {"fail_on_critical": False})
        assert result["passed"] is True

    def test_min_star_rating_fails_when_below(self):
        from pr_scorer import PRScorer
        matrix = {
            "security": {"critical": 5.0, "warning": 4.0, "suggestion": 2.0, "good": 0.0},
            "performance": {"critical": 3.0, "warning": 2.0, "suggestion": 1.0, "good": 0.0},
            "best_practices": {"critical": 2.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
            "code_style": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
            "documentation": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
        }
        scorer = PRScorer(penalty_matrix=matrix, star_thresholds=[0.0, 5.0, 15.0, 30.0, 50.0])
        # Many criticals → high penalty → low stars
        findings = [
            Finding(id=f"cr-{i}", file="f.py", line=i, severity="critical",
                    category="security", title="t", message="m", confidence=0.9)
            for i in range(20)
        ]
        score = scorer.calculate_pr_score(findings)  # 20 × 5 = 100 penalty → 0 stars
        result = pf._evaluate_gate(score, findings, {"fail_on_critical": False, "min_star_rating": 3})
        assert result["passed"] is False
        assert any("star" in r for r in result["reasons"])

    def test_min_star_rating_passes_when_sufficient(self):
        from pr_scorer import PRScorer
        matrix = {
            "security": {"critical": 5.0, "warning": 4.0, "suggestion": 2.0, "good": 0.0},
            "performance": {"critical": 3.0, "warning": 2.0, "suggestion": 1.0, "good": 0.0},
            "best_practices": {"critical": 2.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
            "code_style": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
            "documentation": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
        }
        scorer = PRScorer(penalty_matrix=matrix, star_thresholds=[0.0, 5.0, 15.0, 30.0, 50.0])
        score = scorer.calculate_pr_score([])  # 0 penalty → 5 stars
        result = pf._evaluate_gate(score, [], {"fail_on_critical": False, "min_star_rating": 3})
        assert result["passed"] is True


# ---------------------------------------------------------------------------
# cr-id dedup (via run in dry-run mode)
# ---------------------------------------------------------------------------

class TestCrIdDedup:
    def test_already_posted_cr_ids_skipped(self, sample_findings_path, tmp_path):
        """When posted_cr_ids contains cr-001, it should not appear in new_findings."""
        with patch("post_findings._fetch_posted_cr_ids_ado", return_value={"cr-001"}):
            with patch("post_findings._post_inline_ado", return_value=True) as mock_post:
                output = pf.run(
                    findings_path=sample_findings_path,
                    dry_run=False,
                    workspace=str(tmp_path),
                )
        # cr-001 should have been deduped
        posted_ids = [f["id"] for f in output["findings"]]
        # Note: findings in output is capped list (before dedup display),
        # but deduped_already_posted count should be 1
        assert output["filtering"]["deduped_already_posted"] == 1

    def test_dry_run_never_calls_fetch_cr_ids(self, sample_findings_path, tmp_path):
        with patch("post_findings._fetch_posted_cr_ids_ado") as mock_fetch:
            pf.run(findings_path=sample_findings_path, dry_run=True, workspace=str(tmp_path))
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# .codereview.yml loading
# ---------------------------------------------------------------------------

class TestLoadCodereviewYml:
    def test_missing_file_returns_empty(self, tmp_path):
        result = pf._load_codereview_yml(str(tmp_path))
        assert result == {}

    def test_reads_fail_on_critical_false(self, tmp_path):
        (tmp_path / ".codereview.yml").write_text(
            "fail_on_critical: false\nmin_star_rating: 3\n"
        )
        result = pf._load_codereview_yml(str(tmp_path))
        assert result.get("fail_on_critical") is False
        assert result.get("min_star_rating") == 3

    def test_reads_min_star_rating(self, tmp_path):
        (tmp_path / ".codereview.yml").write_text("min_star_rating: 4\n")
        result = pf._load_codereview_yml(str(tmp_path))
        assert result.get("min_star_rating") == 4


# ---------------------------------------------------------------------------
# Dry-run end-to-end
# ---------------------------------------------------------------------------

class TestDryRunEndToEnd:
    def test_dry_run_produces_valid_output(self, sample_findings_path, tmp_path):
        output = pf.run(
            findings_path=sample_findings_path,
            dry_run=True,
            workspace=str(tmp_path),
        )
        assert output["dry_run"] is True
        assert output["pr_id"] == 42
        assert "filtering" in output
        assert "score" in output
        assert "gate" in output

    def test_confidence_filter_applied(self, sample_findings_path, tmp_path):
        output = pf.run(
            findings_path=sample_findings_path,
            dry_run=True,
            workspace=str(tmp_path),
        )
        # cr-006 has confidence 0.60 → should be filtered
        assert output["filtering"]["filtered_low_confidence"] == 1
        assert output["filtering"]["after_confidence_filter"] == 5

    def test_low_confidence_finding_not_in_output(self, sample_findings_path, tmp_path):
        output = pf.run(
            findings_path=sample_findings_path,
            dry_run=True,
            workspace=str(tmp_path),
        )
        finding_ids = [f["id"] for f in output["findings"]]
        assert "cr-006" not in finding_ids

    def test_score_present_and_nonzero(self, sample_findings_path, tmp_path):
        output = pf.run(
            findings_path=sample_findings_path,
            dry_run=True,
            workspace=str(tmp_path),
        )
        assert output["score"]["total_penalty"] > 0

    def test_gate_fails_due_to_critical(self, sample_findings_path, tmp_path):
        """Sample has a critical security finding → gate should fail."""
        output = pf.run(
            findings_path=sample_findings_path,
            dry_run=True,
            workspace=str(tmp_path),
        )
        # In security mode, cr-002 warning gets elevated to critical, so gate fails
        assert output["gate"]["passed"] is False

    def test_gate_passes_with_no_criticals(self, tmp_path):
        data = {
            "pr_id": 1,
            "repo": "Org/Repo",
            "vcs": "ado",
            "review_modes": ["standard"],
            "findings": [
                {
                    "id": "cr-001",
                    "file": "f.py",
                    "line": 1,
                    "severity": "suggestion",
                    "category": "best_practices",
                    "title": "t",
                    "message": "m",
                    "confidence": 0.9,
                }
            ],
        }
        path = tmp_path / "findings.json"
        path.write_text(json.dumps(data))
        output = pf.run(
            findings_path=str(path),
            dry_run=True,
            workspace=str(tmp_path),
        )
        assert output["gate"]["passed"] is True

    def test_fix_verifications_in_output(self, tmp_path):
        data = {
            "pr_id": 1,
            "repo": "Org/Repo",
            "vcs": "ado",
            "review_modes": ["standard"],
            "findings": [],
            "fix_verifications": [
                {"cr_id": "cr-001", "status": "fixed", "reason": "Issue resolved"},
                {"cr_id": "cr-002", "status": "still_present", "reason": "Still broken"},
            ],
        }
        path = tmp_path / "findings.json"
        path.write_text(json.dumps(data))
        output = pf.run(
            findings_path=str(path),
            dry_run=True,
            workspace=str(tmp_path),
        )
        assert len(output["fix_verifications"]) == 2
        assert output["fix_verifications"][0]["status"] == "fixed"

    def test_schema_validation_error_raises_system_exit(self, tmp_path):
        bad_data = {"pr_id": 1}  # missing required fields
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad_data))
        with pytest.raises(SystemExit):
            pf.run(findings_path=str(path), dry_run=True, workspace=str(tmp_path))
