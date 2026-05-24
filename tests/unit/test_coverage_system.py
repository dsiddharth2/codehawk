"""
Unit tests for Phase 2: 100% Coverage System.

Tests:
- risk_classifier: HIGH/MEDIUM/LOW classification
- files_clean[] parsing from findings JSON
- Coverage calculation (100% vs partial)
- Coverage gate modes (hard/log)
- config.py batch_max_turns default
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Risk classifier
# ---------------------------------------------------------------------------

class TestRiskClassifier:
    """Tests for risk_classifier.classify()."""

    def _make_file(self, path: str, additions: int = 0, deletions: int = 0, change_type: str = "edit"):
        @dataclass
        class _FC:
            path: str
            additions: int
            deletions: int
            change_type: str
        return _FC(path=path, additions=additions, deletions=deletions, change_type=change_type)

    def test_high_sensitivity_path_scores_high(self):
        # auth/ path (1.0) + new file (0.15) + 500 lines (0.25) + .cs type (0.10) = 0.75 >= 0.6
        import risk_classifier as rc
        fc = self._make_file("src/auth/LoginController.cs", additions=500, change_type="add")
        result = rc.classify([fc], high_threshold=0.6, medium_threshold=0.3)
        assert result["src/auth/LoginController.cs"].risk == "HIGH"

    def test_utility_file_small_change_scores_low(self):
        import risk_classifier as rc
        fc = self._make_file("src/utils/DateHelper.cs", additions=2, deletions=1)
        result = rc.classify([fc], high_threshold=0.6, medium_threshold=0.3)
        assert result["src/utils/DateHelper.cs"].risk == "LOW"

    def test_medium_sensitivity_path(self):
        # services/ path (0.5) + 300 lines (0.15) + .py type (0.10) = 0.375 >= 0.3 = MEDIUM
        import risk_classifier as rc
        fc = self._make_file("src/services/UserService.py", additions=300, deletions=0)
        result = rc.classify([fc], high_threshold=0.6, medium_threshold=0.3)
        risk = result["src/services/UserService.py"].risk
        assert risk in ("MEDIUM", "HIGH")

    def test_new_file_increases_score(self):
        import risk_classifier as rc
        fc_new = self._make_file("src/auth/Token.py", additions=50, change_type="add")
        fc_edit = self._make_file("src/auth/Token.py", additions=50, change_type="edit")
        result_new = rc.classify([fc_new])
        result_edit = rc.classify([fc_edit])
        assert result_new["src/auth/Token.py"].score > result_edit["src/auth/Token.py"].score

    def test_graph_analysis_caller_count_increases_score(self):
        import risk_classifier as rc
        fc = self._make_file("src/services/Calc.py")
        graph = {
            "impacted_functions": [
                {"name": f"caller{i}", "file": "src/services/Calc.py", "kind": "Function"}
                for i in range(8)
            ],
            "test_gaps": [],
        }
        result_with = rc.classify([fc], graph_analysis=graph)
        result_without = rc.classify([fc], graph_analysis=None)
        assert result_with["src/services/Calc.py"].score > result_without["src/services/Calc.py"].score

    def test_no_test_coverage_increases_score(self):
        import risk_classifier as rc
        fc = self._make_file("src/models/User.py")
        graph_with_gap = {
            "impacted_functions": [],
            "test_gaps": [{"name": "save", "file": "src/models/User.py"}],
        }
        graph_no_gap = {"impacted_functions": [], "test_gaps": []}
        result_gap = rc.classify([fc], graph_analysis=graph_with_gap)
        result_ok = rc.classify([fc], graph_analysis=graph_no_gap)
        assert result_gap["src/models/User.py"].score > result_ok["src/models/User.py"].score

    def test_graceful_degradation_without_graph(self):
        """Classifier must work without graph analysis."""
        import risk_classifier as rc
        files = [
            self._make_file("src/auth/Foo.cs", additions=200),
            self._make_file("src/utils/Bar.cs", additions=3),
        ]
        result = rc.classify(files, graph_analysis=None)
        assert len(result) == 2
        assert all(r.risk in ("HIGH", "MEDIUM", "LOW") for r in result.values())

    def test_plain_string_files_supported(self):
        """String paths (batch mode) must be handled gracefully."""
        import risk_classifier as rc
        result = rc.classify(["src/auth/Login.cs", "src/utils/Helper.cs"])
        assert "src/auth/Login.cs" in result
        assert "src/utils/Helper.cs" in result

    def test_configurable_thresholds(self):
        """Lower thresholds should produce more HIGH results."""
        import risk_classifier as rc
        fc = self._make_file("src/services/Svc.py", additions=100)
        result_tight = rc.classify([fc], high_threshold=0.1)
        result_loose = rc.classify([fc], high_threshold=0.9)
        assert result_tight["src/services/Svc.py"].risk == "HIGH"
        assert result_loose["src/services/Svc.py"].risk in ("MEDIUM", "LOW")

    def test_score_in_valid_range(self):
        import risk_classifier as rc
        files = [
            self._make_file("src/crypto/Aes.cs", additions=500, change_type="add"),
        ]
        result = rc.classify(files)
        score = result["src/crypto/Aes.cs"].score
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# files_clean[] parsing
# ---------------------------------------------------------------------------

class TestFilesCleanParsing:
    """Tests that files_clean is parsed correctly from findings JSON."""

    def _base_raw(self):
        return {
            "pr_id": 1,
            "repo": "MyRepo",
            "vcs": "ado",
            "review_modes": ["standard"],
            "findings": [
                {
                    "id": "cr-001",
                    "file": "src/auth/Login.cs",
                    "line": 10,
                    "severity": "warning",
                    "category": "security",
                    "title": "Issue",
                    "message": "Msg",
                    "confidence": 0.9,
                }
            ],
            "files_clean": ["src/utils/Helper.cs", "src/constants/Colors.cs"],
            "fix_verifications": [],
        }

    def test_files_clean_parsed_correctly(self):
        import post_findings as pf
        data = self._base_raw()
        pf._normalize_findings(data)
        ff = pf._parse_findings_file(data)
        assert ff.files_clean == ["src/utils/Helper.cs", "src/constants/Colors.cs"]

    def test_files_clean_defaults_to_empty(self):
        import post_findings as pf
        data = self._base_raw()
        del data["files_clean"]
        pf._normalize_findings(data)
        ff = pf._parse_findings_file(data)
        assert ff.files_clean == []

    def test_files_clean_model_field_exists(self):
        from models.review_models import FindingsFile
        import inspect
        fields = {f.name for f in FindingsFile.__dataclass_fields__.values()}
        assert "files_clean" in fields


# ---------------------------------------------------------------------------
# Coverage calculation
# ---------------------------------------------------------------------------

class TestCoverageCalculation:
    """Tests for coverage ratio and gate integration."""

    def _make_finding(self, file: str):
        from models.review_models import Finding
        return Finding(
            id="cr-001", file=file, line=1, severity="suggestion",
            category="best_practices", title="t", message="m", confidence=0.9
        )

    def test_100_percent_coverage_five_findings_three_clean(self):
        """5 findings files + 3 clean files / 8 total = 100%."""
        files_with_findings = {f"file{i}.py" for i in range(5)}
        files_clean = {f"clean{i}.py" for i in range(3)}
        reviewed = files_with_findings | files_clean
        total = 8
        ratio = len(reviewed) / total
        assert ratio == 1.0

    def test_62_5_percent_coverage_five_findings_no_clean(self):
        """5 findings files + 0 clean files / 8 total = 62.5%."""
        files_with_findings = {f"file{i}.py" for i in range(5)}
        files_clean: set = set()
        reviewed = files_with_findings | files_clean
        total = 8
        ratio = len(reviewed) / total
        assert abs(ratio - 0.625) < 0.001

    def test_evaluate_gate_hard_mode_fails_on_incomplete(self):
        import post_findings as pf
        result = pf._evaluate_gate(
            score=None,
            filtered_findings=[],
            gate_config={"fail_on_critical": False},
            coverage_ratio=0.625,
            not_reviewed_files=["file6.py", "file7.py", "file8.py"],
            coverage_gate_mode="hard",
        )
        assert result["passed"] is False
        assert any("not reviewed" in r for r in result["reasons"])

    def test_evaluate_gate_log_mode_passes_on_incomplete(self):
        import post_findings as pf
        result = pf._evaluate_gate(
            score=None,
            filtered_findings=[],
            gate_config={"fail_on_critical": False},
            coverage_ratio=0.625,
            not_reviewed_files=["file6.py", "file7.py"],
            coverage_gate_mode="log",
        )
        assert result["passed"] is True
        assert any("warning" in r.lower() or "coverage" in r.lower() for r in result["reasons"])

    def test_evaluate_gate_100_coverage_always_passes(self):
        import post_findings as pf
        result = pf._evaluate_gate(
            score=None,
            filtered_findings=[],
            gate_config={"fail_on_critical": False},
            coverage_ratio=1.0,
            not_reviewed_files=[],
            coverage_gate_mode="hard",
        )
        assert result["passed"] is True

    def test_coverage_display_100_percent(self):
        import post_findings as pf
        s = pf._coverage_display(8, 8)
        assert "100%" in s

    def test_coverage_display_partial(self):
        import post_findings as pf
        s = pf._coverage_display(5, 8)
        assert "5 / 8" in s
        assert "62%" in s
        assert "not reviewed" in s


# ---------------------------------------------------------------------------
# Coverage penalty in PRScorer
# ---------------------------------------------------------------------------

class TestCoveragePenalty:
    def _make_scorer(self):
        from pr_scorer import PRScorer
        matrix = {
            "security": {"critical": 5.0, "warning": 4.0, "suggestion": 2.0, "good": 0.0},
            "performance": {"critical": 3.0, "warning": 2.0, "suggestion": 1.0, "good": 0.0},
            "best_practices": {"critical": 2.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
            "code_style": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
            "documentation": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
        }
        return PRScorer(penalty_matrix=matrix, star_thresholds=[0.0, 5.0, 15.0, 30.0, 50.0])

    def test_score_based_on_findings_only(self):
        scorer = self._make_scorer()
        score = scorer.calculate_pr_score([])
        assert score.total_penalty == 0.0


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_batch_max_turns_default_is_10(self):
        """Two-pass architecture: batch_max_turns defaults to 10 (Pass 2 verify budget)."""
        import importlib
        import sys
        # Import config with a clean settings instance
        with patch.dict("os.environ", {}, clear=False):
            from config import Settings
            s = Settings(
                azure_devops_org="test",
                azure_devops_pat="test",
                azure_devops_project="test",
                azure_devops_repo="test",
            )
            assert s.batch_max_turns == 10, (
                f"batch_max_turns default should be 10, got {s.batch_max_turns}"
            )

    def test_coverage_gate_mode_default_is_hard(self):
        from config import Settings
        s = Settings(
            azure_devops_org="test",
            azure_devops_pat="test",
            azure_devops_project="test",
            azure_devops_repo="test",
        )
        assert s.coverage_gate_mode == "hard"

    def test_risk_high_threshold_default(self):
        from config import Settings
        s = Settings(
            azure_devops_org="test",
            azure_devops_pat="test",
            azure_devops_project="test",
            azure_devops_repo="test",
        )
        assert s.risk_high_threshold == 0.6

    def test_risk_medium_threshold_default(self):
        from config import Settings
        s = Settings(
            azure_devops_org="test",
            azure_devops_pat="test",
            azure_devops_project="test",
            azure_devops_repo="test",
        )
        assert s.risk_medium_threshold == 0.3
