"""
Phase 2 scoring unit tests — offline, no API keys or ADO needed.

Tests the post_findings scoring engine: gate logic, confidence filtering,
per-file caps, and output structure.

Run:
    pytest tests/unit/test_phase2_scoring.py -v
"""

import json

import pytest

import post_findings as pf


PR_ID = 6571
REPO = "BluSKYFunctionApps"


def _write_findings(tmp_path, findings, fix_verifications=None, review_modes=None):
    data = {
        "pr_id": PR_ID,
        "repo": REPO,
        "vcs": "ado",
        "review_modes": review_modes or ["standard"],
        "tool_calls": len(findings),
        "agent": "codex",
        "findings": findings,
        "fix_verifications": fix_verifications or [],
    }
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Empty findings
# ---------------------------------------------------------------------------

class TestEmptyFindings:
    def test_perfect_score(self, tmp_path):
        path = _write_findings(tmp_path, [])
        output = pf.run(findings_path=path, dry_run=True)
        assert output["score"]["total_penalty"] == 0.0
        assert output["score"]["quality_level"] == "Perfect"
        assert output["gate"]["passed"] is True

    def test_output_structure(self, tmp_path):
        path = _write_findings(tmp_path, [])
        output = pf.run(findings_path=path, dry_run=True)
        assert output["pr_id"] == PR_ID
        assert output["repo"] == REPO
        assert output["dry_run"] is True
        assert output["filtering"]["total_raw"] == 0
        assert len(output["findings"]) == 0


# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------

class TestGateLogic:
    def test_single_critical_fails_gate(self, tmp_path):
        findings = [{
            "id": "cr-101", "file": "src/foo.cs", "line": 1,
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
            "id": "cr-102", "file": "src/bar.cs", "line": 5,
            "severity": "suggestion", "category": "best_practices",
            "title": "Minor style", "message": "Consider renaming",
            "confidence": 0.9, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["gate"]["passed"] is True

    def test_warnings_only_passes_gate(self, tmp_path):
        findings = [{
            "id": "cr-103", "file": "src/baz.cs", "line": 10,
            "severity": "warning", "category": "performance",
            "title": "Slow loop", "message": "O(n^2) complexity",
            "confidence": 0.85, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["gate"]["passed"] is True


# ---------------------------------------------------------------------------
# Confidence filter
# ---------------------------------------------------------------------------

class TestConfidenceFilter:
    def test_low_confidence_filtered(self, tmp_path):
        findings = [{
            "id": "cr-201", "file": "src/bar.cs", "line": 5,
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
            "id": "cr-202", "file": "src/edge.cs", "line": 1,
            "severity": "warning", "category": "best_practices",
            "title": "At threshold", "message": "Confidence exactly 0.7",
            "confidence": 0.7, "suggestion": None,
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["filtering"]["filtered_low_confidence"] == 0
        assert len(output["findings"]) == 1


# ---------------------------------------------------------------------------
# Per-file cap
# ---------------------------------------------------------------------------

class TestPerFileCap:
    def test_caps_at_five_per_file(self, tmp_path):
        findings = [
            {
                "id": f"cr-{300 + i}", "file": "src/same_file.cs", "line": (i + 1) * 10,
                "severity": "warning", "category": "best_practices",
                "title": f"Finding {i}", "message": f"Cap test {i}",
                "confidence": 0.9, "suggestion": None,
            }
            for i in range(10)
        ]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert output["filtering"]["after_cap"] <= 5


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_findings_include_expected_fields(self, tmp_path):
        findings = [{
            "id": "cr-401", "file": "src/test.cs", "line": 42,
            "severity": "warning", "category": "security",
            "title": "Field check", "message": "Verifying output structure",
            "confidence": 0.95, "suggestion": "Fix it",
        }]
        path = _write_findings(tmp_path, findings)
        output = pf.run(findings_path=path, dry_run=True)
        assert len(output["findings"]) == 1
        f = output["findings"][0]
        assert f["id"] == "cr-401"
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
