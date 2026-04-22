"""
Score Comparison Service.

Generates before/after score comparisons from FindingsFile.fix_verifications[]
and formats them as markdown for display in PR comments.
"""

from typing import Dict, List, Optional
import logging

from models.review_models import (
    PRScore,
    ScoreComparison,
    FindingsFile,
    FixVerification,
    FixVerificationResult,
    CommentMatchResult,
)


class ScoreComparisonService:
    """Service for generating score comparisons and formatting as markdown."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def generate_comparison(
        self,
        old_score: PRScore,
        new_score: PRScore
    ) -> Optional[ScoreComparison]:
        """
        Generate score comparison from old and new PRScore objects.

        Args:
            old_score: Score from the previous review
            new_score: Score from the current review

        Returns:
            ScoreComparison object or None if either score is missing
        """
        if not old_score or not new_score:
            return None

        delta = old_score.total_penalty - new_score.total_penalty
        improved = delta > 0
        category_changes = self._calculate_category_changes(old_score, new_score)

        return ScoreComparison(
            old_penalty=old_score.total_penalty,
            new_penalty=new_score.total_penalty,
            delta=delta,
            old_stars=old_score.overall_stars,
            new_stars=new_score.overall_stars,
            old_quality=old_score.quality_level,
            new_quality=new_score.quality_level,
            improved=improved,
            category_changes=category_changes
        )

    def generate_comparison_from_verification(
        self,
        verification_result: FixVerificationResult
    ) -> Optional[ScoreComparison]:
        """
        Generate score comparison from a FixVerificationResult.

        Args:
            verification_result: Fix verification result with old_score and new_score

        Returns:
            ScoreComparison object or None if no scores available
        """
        return self.generate_comparison(
            verification_result.old_score,
            verification_result.new_score
        )

    def summarize_fix_verifications(
        self,
        fix_verifications: List[FixVerification]
    ) -> Dict[str, int]:
        """
        Summarize fix_verifications[] from a FindingsFile.

        Args:
            fix_verifications: List of FixVerification objects

        Returns:
            Dict with counts: {fixed, still_present, not_relevant}
        """
        counts: Dict[str, int] = {'fixed': 0, 'still_present': 0, 'not_relevant': 0}
        for fv in fix_verifications:
            key = fv.status if fv.status in counts else 'still_present'
            counts[key] += 1
        return counts

    def format_as_markdown(
        self,
        old_score: Optional[PRScore],
        new_score: Optional[PRScore],
        fix_verifications: Optional[List[FixVerification]] = None,
        pr_title: str = "Pull Request"
    ) -> str:
        """
        Format score comparison as markdown for a PR comment.

        Args:
            old_score: Previous review score
            new_score: Current review score
            fix_verifications: Optional list of fix verifications from FindingsFile
            pr_title: PR title for context

        Returns:
            Formatted markdown string
        """
        lines = [
            "# 🔄 Code Review Update - Fix Verification",
            "",
            f"**PR:** {pr_title}",
            "",
        ]

        if old_score and new_score:
            comparison = self.generate_comparison(old_score, new_score)
            if comparison:
                lines.extend(self._format_score_comparison(comparison))
                lines.append("")

        if fix_verifications:
            lines.extend(self._format_fix_verifications(fix_verifications))

        return "\n".join(lines)

    def format_from_verification_result(
        self,
        verification_result: FixVerificationResult,
        pr_title: str = "Pull Request"
    ) -> str:
        """
        Format verification result (legacy FixVerificationResult) as markdown.

        Args:
            verification_result: Fix verification result
            pr_title: PR title for context

        Returns:
            Formatted markdown string
        """
        lines = [
            "# 🔄 Code Review Update - Fix Verification",
            "",
            f"**PR:** {pr_title}",
            "",
        ]

        if verification_result.old_score and verification_result.new_score:
            comparison = self.generate_comparison_from_verification(verification_result)
            if comparison:
                lines.extend(self._format_score_comparison(comparison))
                lines.append("")

        lines.extend(self._format_fix_summary_legacy(verification_result))
        lines.append("")
        lines.extend(self._format_detailed_breakdown_legacy(verification_result))

        return "\n".join(lines)

    def _calculate_category_changes(
        self,
        old_score: PRScore,
        new_score: PRScore
    ) -> Dict[str, Dict[str, float]]:
        category_changes = {}
        all_categories = set(old_score.category_penalties.keys()) | set(new_score.category_penalties.keys())

        for category in all_categories:
            old_penalty = old_score.category_penalties.get(category, 0.0)
            new_penalty = new_score.category_penalties.get(category, 0.0)
            delta = old_penalty - new_penalty

            if old_penalty > 0 or new_penalty > 0:
                category_changes[category] = {
                    'old': old_penalty,
                    'new': new_penalty,
                    'delta': delta
                }

        return category_changes

    def _format_score_comparison(self, comparison: ScoreComparison) -> List[str]:
        lines = ["## 📊 Score Comparison", ""]

        if comparison.improved:
            lines.append("### ✅ Quality Score IMPROVED")
        elif comparison.delta < 0:
            lines.append("### ⚠️ Quality Score DECREASED")
        else:
            lines.append("### ➡️ Quality Score UNCHANGED")

        lines.append("")
        lines.append(f"**Previous:** {comparison.old_stars} ({comparison.old_quality}) - {comparison.old_penalty:.1f} penalty points")
        lines.append(f"**Current:** {comparison.new_stars} ({comparison.new_quality}) - {comparison.new_penalty:.1f} penalty points")

        delta_sign = "+" if comparison.delta > 0 else ""
        delta_emoji = "📉" if comparison.delta > 0 else "📈" if comparison.delta < 0 else "➡️"
        lines.append(f"**Change:** {delta_emoji} {delta_sign}{comparison.delta:.1f} points")

        if comparison.category_changes:
            lines.extend(["", "### Category Changes", ""])
            for category, changes in comparison.category_changes.items():
                old_val = changes['old']
                new_val = changes['new']
                delta = changes['delta']
                category_name = category.replace('_', ' ').title()
                emoji = "📉" if delta > 0 else "📈" if delta < 0 else "➡️"
                delta_str = f"{delta:+.1f}" if delta != 0 else "0.0"
                lines.append(f"- {emoji} **{category_name}:** {old_val:.1f} → {new_val:.1f} ({delta_str})")

        return lines

    def _format_fix_verifications(self, fix_verifications: List[FixVerification]) -> List[str]:
        counts = self.summarize_fix_verifications(fix_verifications)
        total = len(fix_verifications)

        lines = [
            "## 🔧 Fix Summary",
            "",
            f"- ✅ **Issues Fixed:** {counts['fixed']} / {total}",
            f"- ⚠️ **Issues Still Present:** {counts['still_present']} / {total}",
        ]

        if counts['not_relevant'] > 0:
            lines.append(f"- ➡️ **Not Relevant:** {counts['not_relevant']} / {total}")

        if total > 0 and counts['fixed'] > 0:
            fix_pct = (counts['fixed'] / total) * 100
            lines.extend(["", f"**Fix Rate:** {fix_pct:.1f}%"])

        # Collapsible details
        fixed = [fv for fv in fix_verifications if fv.status == 'fixed']
        still = [fv for fv in fix_verifications if fv.status == 'still_present']

        if fixed:
            lines.extend(["", "<details>", f"<summary>✅ Fixed ({len(fixed)})</summary>", ""])
            for fv in fixed:
                lines.append(f"- `{fv.cr_id}` — {fv.reason}")
            lines.extend(["", "</details>"])

        if still:
            lines.extend(["", "<details>", f"<summary>⚠️ Still Present ({len(still)})</summary>", ""])
            for fv in still:
                lines.append(f"- `{fv.cr_id}` — {fv.reason}")
            lines.extend(["", "</details>"])

        return lines

    def _format_fix_summary_legacy(self, verification_result: FixVerificationResult) -> List[str]:
        lines = ["## 🔧 Fix Summary", ""]
        total = verification_result.total_old_comments
        fixed = verification_result.comments_fixed
        still_present = verification_result.comments_still_present
        new = verification_result.comments_new

        lines.append(f"- ✅ **Issues Fixed:** {fixed} / {total}")
        lines.append(f"- ⚠️ **Issues Still Present:** {still_present} / {total}")
        if new > 0:
            lines.append(f"- 🆕 **New Issues Found:** {new}")

        if total > 0:
            fix_pct = (fixed / total) * 100
            lines.extend(["", f"**Fix Rate:** {fix_pct:.1f}%"])

        return lines

    def _format_detailed_breakdown_legacy(self, verification_result: FixVerificationResult) -> List[str]:
        lines = []
        match_results = verification_result.match_results

        fixed_results = [r for r in match_results if r.is_fixed]
        still_present_results = [r for r in match_results if not r.is_fixed]

        if fixed_results:
            lines.extend(["<details>", f"<summary>✅ Fixed Issues ({len(fixed_results)})</summary>", ""])
            for result in fixed_results:
                old = result.old_comment
                lines.append(f"- `{old.file_path}:{old.line_number}` - **{old.category or 'unknown'}** ({old.severity or 'unknown'})")
                lines.append(f"  - *Reason:* {result.reason}")
            lines.extend(["", "</details>", ""])

        if still_present_results:
            lines.extend(["<details>", f"<summary>⚠️ Issues Still Present ({len(still_present_results)})</summary>", ""])
            for result in still_present_results:
                old = result.old_comment
                lines.append(f"- `{old.file_path}:{old.line_number}` - **{old.category or 'unknown'}** ({old.severity or 'unknown'})")
                lines.append(f"  - *Reason:* {result.reason}")
                if result.matching_new_issue:
                    new_line = result.matching_new_issue.line_number
                    if new_line and new_line != old.line_number:
                        lines.append(f"  - *Now at line:* {new_line}")
            lines.extend(["", "</details>"])

        return lines
