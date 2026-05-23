"""
Unit tests for Phase 4: Noise Reduction + Audit.

Tests:
- merge_similar_findings: same-file nearby-line dedup, cross-file non-merge,
  severity prioritisation, footnote format
- _write_audit_trail: file created at correct path, contains all required sections
"""

import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.review_models import Finding, FindingsFile, Usage
import post_findings as pf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_finding(id, file="src/foo.py", line=10, severity="warning",
                  category="best_practices", title="Null check missing",
                  message="The parameter may be null"):
    return Finding(
        id=id,
        file=file,
        line=line,
        severity=severity,
        category=category,
        title=title,
        message=message,
        confidence=0.85,
    )


# ---------------------------------------------------------------------------
# merge_similar_findings
# ---------------------------------------------------------------------------

class TestMergeSimilarFindings:
    def test_nearby_similar_findings_on_same_file_merge(self):
        """3 findings: lines 10, 12, 50 on same file — first two should merge, third stays."""
        f1 = _make_finding("cr-001", line=10, title="Null check missing", message="Parameter may be null here")
        f2 = _make_finding("cr-002", line=12, title="Null check missing", message="Parameter may be null here")
        f3 = _make_finding("cr-003", line=50, title="Null check missing", message="Parameter may be null here")

        result = pf.merge_similar_findings([f1, f2, f3])

        assert len(result) == 2, f"Expected 2 findings after merge, got {len(result)}"
        lines = {f.line for f in result}
        assert 50 in lines, "Finding at line 50 should survive as-is"

    def test_different_files_do_not_merge(self):
        """2 findings on different files with identical title/message must NOT merge."""
        f1 = _make_finding("cr-001", file="src/foo.py", line=10, title="Missing null check",
                            message="The parameter may be null")
        f2 = _make_finding("cr-002", file="src/bar.py", line=10, title="Missing null check",
                            message="The parameter may be null")

        result = pf.merge_similar_findings([f1, f2])

        assert len(result) == 2

    def test_keeps_higher_severity_finding(self):
        """When merging, the critical finding is kept and the suggestion is absorbed."""
        f1 = _make_finding("cr-001", line=10, severity="suggestion",
                            title="Null check missing", message="Parameter may be null here")
        f2 = _make_finding("cr-002", line=12, severity="critical",
                            title="Null check missing", message="Parameter may be null here")

        result = pf.merge_similar_findings([f1, f2])

        assert len(result) == 1
        assert result[0].severity == "critical"

    def test_footnote_added_to_merged_finding(self):
        """Merged finding message includes footnote referencing the absorbed line."""
        f1 = _make_finding("cr-001", line=10, title="Null check missing",
                            message="Parameter may be null here")
        f2 = _make_finding("cr-002", line=12, title="Null check missing",
                            message="Parameter may be null here")

        result = pf.merge_similar_findings([f1, f2])

        assert len(result) == 1
        assert "Also flagged at line" in result[0].message

    def test_line_gap_exactly_5_merges(self):
        """Line gap of exactly 5 is within threshold — should merge."""
        f1 = _make_finding("cr-001", line=10, title="Same issue", message="Same description here now")
        f2 = _make_finding("cr-002", line=15, title="Same issue", message="Same description here now")

        result = pf.merge_similar_findings([f1, f2])
        assert len(result) == 1

    def test_line_gap_of_6_does_not_merge(self):
        """Line gap of 6 exceeds threshold — should NOT merge."""
        f1 = _make_finding("cr-001", line=10, title="Same issue", message="Same description here now")
        f2 = _make_finding("cr-002", line=16, title="Same issue", message="Same description here now")

        result = pf.merge_similar_findings([f1, f2])
        assert len(result) == 2

    def test_dissimilar_findings_on_same_file_nearby_lines_do_not_merge(self):
        """Even if on same file and nearby, very different findings must not merge."""
        f1 = _make_finding("cr-001", line=10, title="SQL injection risk",
                            message="Raw string interpolation in query leads to injection")
        f2 = _make_finding("cr-002", line=11, title="Missing async keyword",
                            message="Method performs I/O but lacks async await pattern")

        result = pf.merge_similar_findings([f1, f2])
        assert len(result) == 2

    def test_empty_input_returns_empty(self):
        assert pf.merge_similar_findings([]) == []

    def test_single_finding_returned_unchanged(self):
        f = _make_finding("cr-001")
        result = pf.merge_similar_findings([f])
        assert len(result) == 1
        assert result[0].id == "cr-001"

    def test_third_finding_far_away_not_merged(self):
        """Verify three findings: two close (merge) + one far (stays separate)."""
        f1 = _make_finding("cr-001", line=10, title="Null check issue",
                            message="Parameter may be null causing NPE")
        f2 = _make_finding("cr-002", line=13, title="Null check issue",
                            message="Parameter may be null causing NPE")
        f3 = _make_finding("cr-003", line=50, title="Unrelated issue",
                            message="Completely different finding about something else")

        result = pf.merge_similar_findings([f1, f2, f3])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _write_audit_trail
# ---------------------------------------------------------------------------

class TestWriteAuditTrail:
    def _make_findings_file(self, pr_id=99, usage=None):
        return FindingsFile(
            pr_id=pr_id,
            repo="MyOrg/MyRepo",
            vcs="ado",
            review_modes=["standard"],
            summary="Test run",
            findings=[],
            usage=usage,
        )

    def _make_score(self):
        from pr_scorer import PRScorer
        matrix = {
            "security": {"critical": 5.0, "warning": 4.0, "suggestion": 2.0, "good": 0.0},
            "performance": {"critical": 3.0, "warning": 2.0, "suggestion": 1.0, "good": 0.0},
            "best_practices": {"critical": 2.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
            "code_style": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
            "documentation": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
        }
        scorer = PRScorer(penalty_matrix=matrix, star_thresholds=[0.0, 5.0, 15.0, 30.0, 50.0])
        findings = [
            _make_finding("cr-001", severity="warning", category="security"),
        ]
        return scorer.calculate_pr_score(findings)

    def test_audit_file_is_created_in_cr_directory(self, tmp_path):
        ff = self._make_findings_file()
        score = self._make_score()
        gate_result = {"passed": True, "reasons": []}
        files_reviewed = {"src/foo.py"}
        all_code = {"src/foo.py", "src/bar.py"}

        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=score,
            gate_result=gate_result,
            files_reviewed_set=files_reviewed,
            all_code_paths=all_code,
            coverage_ratio=0.5,
        )

        assert path is not None
        assert Path(path).exists()
        assert Path(path).parent == tmp_path / ".cr"

    def test_audit_file_timestamp_format(self, tmp_path):
        """Filename must match review_{pr_id}_{YYYYMMDD_HHMMSS}.md pattern."""
        ff = self._make_findings_file(pr_id=42)
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=None,
            gate_result={"passed": True, "reasons": []},
            files_reviewed_set=set(),
            all_code_paths=set(),
            coverage_ratio=1.0,
        )
        filename = Path(path).name
        assert re.match(r"review_42_\d{8}_\d{6}\.md$", filename), \
            f"Unexpected filename format: {filename}"

    def test_audit_file_contains_pr_metadata_section(self, tmp_path):
        ff = self._make_findings_file(pr_id=55)
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=None,
            gate_result={"passed": True, "reasons": []},
            files_reviewed_set=set(),
            all_code_paths=set(),
            coverage_ratio=1.0,
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "PR Metadata" in content
        assert "PR ID" in content or "55" in content
        assert "MyOrg/MyRepo" in content

    def test_audit_file_contains_score_breakdown_section(self, tmp_path):
        ff = self._make_findings_file()
        score = self._make_score()
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=score,
            gate_result={"passed": True, "reasons": []},
            files_reviewed_set=set(),
            all_code_paths=set(),
            coverage_ratio=1.0,
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "Score Breakdown" in content
        assert "Total Penalty" in content

    def test_audit_file_contains_files_reviewed_section(self, tmp_path):
        ff = self._make_findings_file()
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=None,
            gate_result={"passed": True, "reasons": []},
            files_reviewed_set={"src/foo.py"},
            all_code_paths={"src/foo.py", "src/bar.py"},
            coverage_ratio=0.5,
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "Files Reviewed" in content
        assert "50%" in content or "50.0%" in content

    def test_audit_file_contains_findings_section(self, tmp_path):
        ff = self._make_findings_file()
        f = _make_finding("cr-001", severity="critical", category="security")
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[f],
            capped_findings=[f],
            score=None,
            gate_result={"passed": False, "reasons": ["critical finding"]},
            files_reviewed_set={"src/foo.py"},
            all_code_paths={"src/foo.py"},
            coverage_ratio=1.0,
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "Findings" in content
        assert "critical" in content

    def test_audit_file_contains_token_usage_section(self, tmp_path):
        usage = Usage(input_tokens=10_000, output_tokens=2_000, total_tokens=12_000,
                      model="gpt-4o", duration_seconds=30.0)
        ff = self._make_findings_file(usage=usage)
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=None,
            gate_result={"passed": True, "reasons": []},
            files_reviewed_set=set(),
            all_code_paths=set(),
            coverage_ratio=1.0,
            usage=usage,
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "Token Usage" in content
        assert "10,000" in content
        assert "gpt-4o" in content

    def test_audit_file_contains_risk_classification_section(self, tmp_path):
        ff = self._make_findings_file()
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=None,
            gate_result={"passed": True, "reasons": []},
            files_reviewed_set=set(),
            all_code_paths=set(),
            coverage_ratio=1.0,
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "Risk Classification" in content

    def test_audit_file_contains_gate_decision_section(self, tmp_path):
        ff = self._make_findings_file()
        gate_result = {"passed": False, "reasons": ["Gate failed: 1 critical finding(s) present"]}
        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=None,
            gate_result=gate_result,
            files_reviewed_set=set(),
            all_code_paths=set(),
            coverage_ratio=1.0,
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "Gate Decision" in content
        assert "FAILED" in content
        assert "critical finding" in content

    def test_audit_written_during_dry_run(self, tmp_path):
        """Audit trail is written even in dry-run mode."""
        data = {
            "pr_id": 7,
            "repo": "Org/R",
            "vcs": "ado",
            "review_modes": ["standard"],
            "summary": "Test",
            "findings": [],
        }
        path = tmp_path / "findings.json"
        path.write_text(json.dumps(data))

        pf.run(findings_path=str(path), dry_run=True, workspace=str(tmp_path))

        cr_dir = tmp_path / ".cr"
        audit_files = list(cr_dir.glob("review_7_*.md"))
        assert len(audit_files) == 1, "Expected exactly one audit file for PR #7"

    def test_audit_file_contains_all_required_sections(self, tmp_path):
        """All seven required sections must be present in the audit file."""
        usage = Usage(input_tokens=5000, output_tokens=1000, total_tokens=6000, model="o3")
        ff = self._make_findings_file(pr_id=11, usage=usage)
        score = self._make_score()
        gate_result = {"passed": True, "reasons": []}

        path = pf._write_audit_trail(
            workspace=str(tmp_path),
            findings_file=ff,
            all_raw_findings=[],
            capped_findings=[],
            score=score,
            gate_result=gate_result,
            files_reviewed_set={"src/foo.py"},
            all_code_paths={"src/foo.py"},
            coverage_ratio=1.0,
            usage=usage,
            risk_table_md="| File | Risk |\n|------|------|\n| src/foo.py | HIGH |",
        )
        content = Path(path).read_text(encoding="utf-8")

        required_sections = [
            "PR Metadata",
            "Score Breakdown",
            "Files Reviewed",
            "Findings",
            "Token Usage",
            "Risk Classification",
            "Gate Decision",
        ]
        for section in required_sections:
            assert section in content, f"Missing required section: {section}"
