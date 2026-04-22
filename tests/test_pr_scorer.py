"""
Unit tests for PRScorer — penalty-based scoring with mode multipliers.
"""

import pytest
from models.review_models import Finding
from pr_scorer import PRScorer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_finding(
    id="cr-001",
    file="src/foo.py",
    line=1,
    severity="suggestion",
    category="best_practices",
    confidence=0.9,
):
    return Finding(
        id=id,
        file=file,
        line=line,
        severity=severity,
        category=category,
        title="test finding",
        message="test message",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Basic scoring
# ---------------------------------------------------------------------------

class TestCalculatePRScore:
    def test_zero_findings_yields_five_stars(self, pr_scorer):
        score = pr_scorer.calculate_pr_score([])
        assert score.total_penalty == 0.0
        assert score.overall_stars == "⭐⭐⭐⭐⭐"
        assert score.quality_level == "Perfect"

    def test_security_critical_applies_max_penalty(self, pr_scorer):
        f = make_finding(severity="critical", category="security")
        score = pr_scorer.calculate_pr_score([f])
        # security critical = 5.0 in default matrix
        assert score.total_penalty == 5.0
        assert score.category_penalties.get("security") == 5.0

    def test_security_warning_penalty(self, pr_scorer):
        f = make_finding(severity="warning", category="security")
        score = pr_scorer.calculate_pr_score([f])
        assert score.total_penalty == 4.0

    def test_performance_critical_penalty(self, pr_scorer):
        f = make_finding(severity="critical", category="performance")
        score = pr_scorer.calculate_pr_score([f])
        assert score.total_penalty == 3.0

    def test_code_style_has_zero_penalty(self, pr_scorer):
        f = make_finding(severity="critical", category="code_style")
        score = pr_scorer.calculate_pr_score([f])
        assert score.total_penalty == 0.0

    def test_multiple_findings_accumulate_penalty(self, pr_scorer):
        findings = [
            make_finding("cr-001", severity="critical", category="security"),   # 5.0
            make_finding("cr-002", severity="warning", category="performance"),  # 2.0
            make_finding("cr-003", severity="suggestion", category="best_practices"),  # 0.5
        ]
        score = pr_scorer.calculate_pr_score(findings)
        assert score.total_penalty == pytest.approx(7.5)

    def test_star_rating_thresholds(self, pr_scorer):
        # penalty=0 → 5 stars
        assert pr_scorer._penalty_to_stars(0.0) == "⭐⭐⭐⭐⭐"
        # penalty=3 → 4 stars (0 < 3 <= 5)
        assert pr_scorer._penalty_to_stars(3.0) == "⭐⭐⭐⭐☆"
        # penalty=10 → 3 stars (5 < 10 <= 15)
        assert pr_scorer._penalty_to_stars(10.0) == "⭐⭐⭐☆☆"
        # penalty=20 → 2 stars (15 < 20 <= 30)
        assert pr_scorer._penalty_to_stars(20.0) == "⭐⭐☆☆☆"
        # penalty=40 → 1 star (30 < 40 <= 50)
        assert pr_scorer._penalty_to_stars(40.0) == "⭐☆☆☆☆"
        # penalty=60 → 0 stars (60 > 50)
        assert pr_scorer._penalty_to_stars(60.0) == "☆☆☆☆☆"

    def test_issues_by_severity_populated(self, pr_scorer):
        findings = [
            make_finding("cr-001", severity="critical", category="security"),
            make_finding("cr-002", severity="warning", category="performance"),
            make_finding("cr-003", severity="warning", category="performance"),
        ]
        score = pr_scorer.calculate_pr_score(findings)
        assert score.issues_by_severity["critical"] == 1
        assert score.issues_by_severity["warning"] == 2
        assert score.issues_by_severity["suggestion"] == 0

    def test_scoring_breakdown_contains_total(self, pr_scorer):
        f = make_finding(severity="critical", category="security")
        score = pr_scorer.calculate_pr_score([f])
        breakdown_text = " ".join(score.scoring_breakdown)
        assert "5.0" in breakdown_text
        assert "Total penalty" in breakdown_text


# ---------------------------------------------------------------------------
# Mode multipliers
# ---------------------------------------------------------------------------

class TestApplyModeMultipliers:
    def test_no_modes_returns_unchanged(self, pr_scorer):
        findings = [make_finding(severity="warning", category="security")]
        result = pr_scorer.apply_mode_multipliers(findings, [])
        assert result[0].severity == "warning"

    def test_security_mode_elevates_warning_to_critical(self, pr_scorer):
        f = make_finding(severity="warning", category="security")
        result = pr_scorer.apply_mode_multipliers([f], ["security"])
        assert result[0].severity == "critical"

    def test_security_mode_leaves_suggestion_unchanged(self, pr_scorer):
        f = make_finding(severity="suggestion", category="security")
        result = pr_scorer.apply_mode_multipliers([f], ["security"])
        assert result[0].severity == "suggestion"

    def test_performance_mode_elevates_warning_to_critical(self, pr_scorer):
        f = make_finding(severity="warning", category="performance")
        result = pr_scorer.apply_mode_multipliers([f], ["performance"])
        assert result[0].severity == "critical"

    def test_security_mode_does_not_affect_non_security_findings(self, pr_scorer):
        f = make_finding(severity="warning", category="performance")
        result = pr_scorer.apply_mode_multipliers([f], ["security"])
        assert result[0].severity == "warning"

    def test_architecture_mode_elevates_suggestion_to_warning(self, pr_scorer):
        f = make_finding(severity="suggestion", category="best_practices")
        result = pr_scorer.apply_mode_multipliers([f], ["architecture"])
        assert result[0].severity == "warning"

    def test_migration_mode_elevates_all_to_critical(self, pr_scorer):
        findings = [
            make_finding("cr-001", severity="suggestion", category="best_practices"),
            make_finding("cr-002", severity="warning", category="performance"),
            make_finding("cr-003", severity="critical", category="security"),
        ]
        result = pr_scorer.apply_mode_multipliers(findings, ["migration"])
        assert all(f.severity == "critical" for f in result)

    def test_mode_multipliers_do_not_mutate_originals(self, pr_scorer):
        f = make_finding(severity="warning", category="security")
        pr_scorer.apply_mode_multipliers([f], ["security"])
        assert f.severity == "warning"  # original unchanged

    def test_security_mode_doubles_effective_penalty(self, pr_scorer):
        """Security warning elevated to critical → penalty 5.0 instead of 4.0."""
        f = make_finding(severity="warning", category="security")
        adjusted = pr_scorer.apply_mode_multipliers([f], ["security"])
        score = pr_scorer.calculate_pr_score(adjusted)
        assert score.total_penalty == 5.0  # critical security penalty
