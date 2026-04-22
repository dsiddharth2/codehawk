"""
PR Scoring Utility.

Calculates penalty-based quality scores for pull requests (lower is better).
Supports both legacy ReviewResult lists and new Finding lists.
"""

from typing import List, Dict
from models.review_models import Finding, PRScore
from utils.logger import get_logger


class PRScorer:
    """Calculates PR penalty scores based on review findings (lower is better)."""

    def __init__(
        self,
        penalty_matrix: Dict[str, Dict[str, float]],
        star_thresholds: List[float],
        enable_scoring: bool = True
    ):
        """
        Initialize PR scorer.

        Args:
            penalty_matrix: Nested dict {category: {severity: penalty_points}}
            star_thresholds: List of 5 threshold values for star ratings
            enable_scoring: Whether scoring is enabled
        """
        self.penalty_matrix = penalty_matrix
        self.star_thresholds = star_thresholds
        self.enable_scoring = enable_scoring
        self.logger = get_logger(__name__)

    def calculate_pr_score(
        self,
        findings: List[Finding],
        statistics: Dict[str, int] = None
    ) -> PRScore:
        """
        Calculate penalty-based PR score from a list of Finding objects.

        Args:
            findings: List of Finding dataclass objects
            statistics: Optional pre-computed statistics dict

        Returns:
            PRScore object with all scoring details
        """
        if not self.enable_scoring:
            return self._create_disabled_score()

        if statistics is None:
            statistics = self._build_statistics(findings)

        category_penalties = self._calculate_category_penalties_from_findings(findings)
        total_penalty = sum(category_penalties.values())

        overall_stars = self._penalty_to_stars(total_penalty)
        category_stars = {
            cat: self._penalty_to_stars(penalty)
            for cat, penalty in category_penalties.items()
        }

        quality_level = self._get_quality_level(total_penalty)

        breakdown = self._generate_breakdown(
            category_penalties,
            statistics,
            total_penalty,
            quality_level
        )

        score = PRScore(
            total_penalty=round(total_penalty, 1),
            overall_stars=overall_stars,
            category_penalties=category_penalties,
            category_stars=category_stars,
            issues_by_severity=self._extract_severity_counts(statistics),
            scoring_breakdown=breakdown,
            quality_level=quality_level
        )

        self.logger.info(
            f"PR Penalty Score: {total_penalty:.1f} points ({quality_level}) - "
            f"Security: {category_penalties.get('security', 0):.1f}, "
            f"Performance: {category_penalties.get('performance', 0):.1f}, "
            f"Best Practices: {category_penalties.get('best_practices', 0):.1f}"
        )

        return score

    def apply_mode_multipliers(
        self,
        findings: List[Finding],
        review_modes: List[str]
    ) -> List[Finding]:
        """
        Apply review-mode severity multipliers to findings.

        Multipliers:
          - security mode: penalty ×2 for security findings
          - performance mode: penalty ×2 for performance findings
          - architecture mode: penalty ×1.5 for architecture/best_practices findings
          - migration mode: elevates all findings to "critical" severity

        This method returns a new list of Finding objects with severity
        adjusted where applicable. The original list is not mutated.

        Args:
            findings: List of Finding objects
            review_modes: List of active review mode names

        Returns:
            New list of Finding objects with adjusted severities
        """
        if not review_modes:
            return findings

        modes = {m.lower() for m in review_modes}
        adjusted = []

        for f in findings:
            # Work on a copy to avoid mutating originals
            severity = f.severity

            if 'migration' in modes:
                # Migration mode: all findings treated as critical for gating
                severity = 'critical'
            elif 'security' in modes and f.category == 'security':
                # Security mode doubles effective penalty — elevate warning→critical
                if severity == 'warning':
                    severity = 'critical'
            elif 'performance' in modes and f.category == 'performance':
                if severity == 'warning':
                    severity = 'critical'
            elif 'architecture' in modes and f.category in ('best_practices', 'architecture'):
                # Architecture mode: suggestion→warning (×1.5 effect via tier bump)
                if severity == 'suggestion':
                    severity = 'warning'

            if severity != f.severity:
                from dataclasses import replace
                adjusted.append(replace(f, severity=severity))
            else:
                adjusted.append(f)

        return adjusted

    def _calculate_issue_penalty(self, severity: str, category: str) -> float:
        if severity == 'good':
            return 0.0
        return self.penalty_matrix.get(category, {}).get(severity, 0.0)

    def _calculate_category_penalties_from_findings(
        self,
        findings: List[Finding]
    ) -> Dict[str, float]:
        category_penalties: Dict[str, float] = {}

        active_categories = [
            cat for cat in self.penalty_matrix.keys()
            if any(penalty > 0 for penalty in self.penalty_matrix[cat].values())
        ]

        for category in active_categories:
            total_penalty = sum(
                self._calculate_issue_penalty(f.severity, f.category)
                for f in findings
                if f.category == category
            )
            if total_penalty > 0:
                category_penalties[category] = round(total_penalty, 1)

        return category_penalties

    def _build_statistics(self, findings: List[Finding]) -> Dict[str, int]:
        stats: Dict[str, int] = {
            'critical': 0,
            'warning': 0,
            'suggestion': 0,
            'good': 0,
        }
        for f in findings:
            stats[f.severity] = stats.get(f.severity, 0) + 1
            stats[f.category] = stats.get(f.category, 0) + 1
        return stats

    def _penalty_to_stars(self, penalty: float) -> str:
        thresholds = self.star_thresholds
        if penalty <= thresholds[0]:
            return "⭐⭐⭐⭐⭐"
        elif penalty <= thresholds[1]:
            return "⭐⭐⭐⭐☆"
        elif penalty <= thresholds[2]:
            return "⭐⭐⭐☆☆"
        elif penalty <= thresholds[3]:
            return "⭐⭐☆☆☆"
        elif penalty <= thresholds[4]:
            return "⭐☆☆☆☆"
        else:
            return "☆☆☆☆☆"

    def _get_quality_level(self, penalty: float) -> str:
        thresholds = self.star_thresholds
        if penalty <= thresholds[0]:
            return "Perfect"
        elif penalty <= thresholds[1]:
            return "Excellent"
        elif penalty <= thresholds[2]:
            return "Good"
        elif penalty <= thresholds[3]:
            return "Needs Work"
        elif penalty <= thresholds[4]:
            return "Poor"
        else:
            return "Critical"

    def _generate_breakdown(
        self,
        category_penalties: Dict[str, float],
        statistics: Dict[str, int],
        total_penalty: float,
        quality_level: str
    ) -> List[str]:
        breakdown = ["Starting penalty: 0 points (perfect PR)"]

        for category, penalty in category_penalties.items():
            category_count = statistics.get(category, 0)
            if penalty > 0 and category_count > 0:
                breakdown.append(
                    f"{category.replace('_', ' ').title()}: +{penalty:.1f} points "
                    f"({category_count} issue{'s' if category_count > 1 else ''})"
                )

        breakdown.append(f"Total penalty: {total_penalty:.1f} points")
        breakdown.append(f"Quality level: {quality_level}")

        if total_penalty == 0:
            breakdown.append("🎉 Perfect PR - no issues found!")
        elif total_penalty <= 5:
            breakdown.append("✅ Excellent code quality with minimal issues")
        elif total_penalty <= 15:
            breakdown.append("👍 Good code quality, minor improvements suggested")
        elif total_penalty <= 30:
            breakdown.append("⚠️ Several issues need attention")
        elif total_penalty <= 50:
            breakdown.append("❌ Significant issues detected")
        else:
            breakdown.append("🚨 Critical issues require immediate attention")

        return breakdown

    def _extract_severity_counts(self, statistics: Dict[str, int]) -> Dict[str, int]:
        return {
            'critical': statistics.get('critical', 0),
            'warning': statistics.get('warning', 0),
            'suggestion': statistics.get('suggestion', 0),
            'good': statistics.get('good', 0)
        }

    def _create_disabled_score(self) -> PRScore:
        return PRScore(
            total_penalty=0.0,
            overall_stars="",
            category_penalties={},
            category_stars={},
            issues_by_severity={},
            scoring_breakdown=["Scoring disabled in configuration"],
            quality_level="Unknown"
        )
