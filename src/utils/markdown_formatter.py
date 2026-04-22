"""
Markdown Formatter Utility.

Formats review results into markdown for PR comments and summaries.
"""

from typing import List, Dict
from models.review_models import PRReviewJobResult, ReviewResult, ReviewComment, PRScore


class MarkdownFormatter:
    """Formatter for converting review results to markdown."""

    @staticmethod
    def format_pr_summary(result: PRReviewJobResult) -> str:
        """
        Format PR review result into a comprehensive summary markdown.

        Args:
            result: PRReviewJobResult object

        Returns:
            Formatted markdown string
        """
        lines = [
            "# 🤖 AI Code Review",
            "",
        ]

        if result.score and result.score.total_penalty >= 0:
            lines.extend(MarkdownFormatter._format_score_section(result.score))

        lines.extend([
            f"**PR {result.pr_id}: {result.pr_title}**",
            "",
            f"👤 **Author:** {result.author}",
            f"🌿 **Branch:** `{result.source_branch}` → `{result.target_branch}`",
            "",
        ])

        lines.extend([
            "## 📊 Review Statistics",
            "",
            f"- ✅ **Files Reviewed:** {result.files_reviewed}",
            f"- ⏭️ **Files Skipped:** {result.files_skipped}",
            f"- ❌ **Files Failed:** {result.files_failed}",
            f"- 💬 **Total Comments:** {result.total_comments}",
            "",
        ])

        if result.statistics:
            critical = result.statistics.get('critical', 0)
            warning = result.statistics.get('warning', 0)
            suggestion = result.statistics.get('suggestion', 0)

            lines.extend([
                "### 🎯 Comment Breakdown by Severity",
                "",
                f"- 🔴 **Critical:** {critical}",
                f"- ⚠️ **Warning:** {warning}",
                f"- 💡 **Suggestion:** {suggestion}",
                "",
            ])

            security = result.statistics.get('security', 0)
            performance = result.statistics.get('performance', 0)
            best_practices = result.statistics.get('best_practices', 0)
            code_style = result.statistics.get('code_style', 0)
            documentation = result.statistics.get('documentation', 0)

            lines.extend([
                "### 📂 Comment Breakdown by Category",
                "",
                f"- 🔒 **Security:** {security}",
                f"- ⚡ **Performance:** {performance}",
                f"- ✨ **Best Practices:** {best_practices}",
                f"- 🎨 **Code Style:** {code_style}",
                f"- 📚 **Documentation:** {documentation}",
                "",
            ])

        if result.failed_files:
            lines.extend([
                "## ❌ Failed Reviews",
                "",
                f"The following {len(result.failed_files)} file(s) could not be reviewed:",
                "",
            ])
            for failed in result.failed_files[:5]:
                lines.append(f"- `{failed['path']}`: {failed['error']}")

            if len(result.failed_files) > 5:
                lines.append(f"- ... and {len(result.failed_files) - 5} more")

            lines.append("")

        lines.extend([
            "## 🚀 Next Steps",
            "",
            "1. Review the inline comments on specific files",
            "2. Address critical and warning items",
            "3. Consider implementing suggestions for code quality",
            "4. Reply to any comments if you need clarification",
            "",
        ])

        if result.overall_summary:
            lines.extend([
                "---",
                "",
                "## 📝 Overall Summary",
                "",
                result.overall_summary,
                "",
            ])

        lines.extend([
            "---",
            "",
            "*This review was automatically generated using AI. Please use your judgment when applying suggestions.*",
        ])

        return "\n".join(lines)

    @staticmethod
    def format_file_summary(file_result: ReviewResult) -> str:
        """
        Format file review result into markdown summary.

        Args:
            file_result: ReviewResult object for a single file

        Returns:
            Formatted markdown string
        """
        lines = [
            f"## 📄 Review: `{file_result.file_path}`",
            "",
            f"**Language:** {file_result.language}",
            f"**Comments:** {len(file_result.comments)}",
            "",
        ]

        if file_result.summary:
            lines.extend([
                "### 📝 Summary",
                "",
                file_result.summary,
                "",
            ])

        if file_result.comments:
            lines.extend([
                "### 💬 Comments",
                "",
            ])

            critical_comments = [c for c in file_result.comments if c.severity == 'critical']
            warning_comments = [c for c in file_result.comments if c.severity == 'warning']
            suggestion_comments = [c for c in file_result.comments if c.severity == 'suggestion']

            if critical_comments:
                lines.extend(["#### 🔴 Critical Issues", ""])
                for comment in critical_comments:
                    lines.append(f"- Line {comment.line_number}: {comment.message[:100]}...")
                lines.append("")

            if warning_comments:
                lines.extend(["#### ⚠️ Warnings", ""])
                for comment in warning_comments:
                    lines.append(f"- Line {comment.line_number}: {comment.message[:100]}...")
                lines.append("")

            if suggestion_comments:
                lines.extend(["#### 💡 Suggestions", ""])
                for comment in suggestion_comments:
                    lines.append(f"- Line {comment.line_number}: {comment.message[:100]}...")
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def format_comment_summary(comments: List[ReviewComment]) -> str:
        """
        Format a list of comments into a summary table.

        Args:
            comments: List of ReviewComment objects

        Returns:
            Formatted markdown table
        """
        if not comments:
            return "No comments found."

        lines = [
            "| Severity | Category | Line | Message |",
            "|----------|----------|------|---------|",
        ]

        for comment in comments:
            severity_icon = {
                'critical': '🔴',
                'warning': '⚠️',
                'suggestion': '💡'
            }.get(comment.severity, '📝')

            category = comment.category.replace('_', ' ').title()
            message_preview = comment.message[:50] + "..." if len(comment.message) > 50 else comment.message

            lines.append(
                f"| {severity_icon} {comment.severity} | {category} | {comment.line_number} | {message_preview} |"
            )

        return "\n".join(lines)

    @staticmethod
    def format_statistics_card(statistics: Dict[str, int]) -> str:
        """
        Format statistics into a card-style markdown.

        Args:
            statistics: Dictionary of statistics

        Returns:
            Formatted markdown string
        """
        total = statistics.get('total_comments', 0)
        critical = statistics.get('critical', 0)
        warning = statistics.get('warning', 0)
        suggestion = statistics.get('suggestion', 0)

        lines = [
            "```",
            "╔════════════════════════════════╗",
            "║     CODE REVIEW SUMMARY        ║",
            "╠════════════════════════════════╣",
            f"║ Total Comments: {total:>14} ║",
            f"║ 🔴 Critical:    {critical:>14} ║",
            f"║ ⚠️  Warning:     {warning:>14} ║",
            f"║ 💡 Suggestion:  {suggestion:>14} ║",
            "╚════════════════════════════════╝",
            "```",
        ]

        return "\n".join(lines)

    @staticmethod
    def create_collapsible_section(title: str, content: str) -> str:
        """
        Create a collapsible markdown section.

        Args:
            title: Section title
            content: Section content

        Returns:
            Formatted collapsible markdown
        """
        lines = [
            f"### {title}",
            "",
            content,
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_score_section(score: PRScore) -> List[str]:
        """
        Format the PR score section for markdown display.

        Args:
            score: PRScore object

        Returns:
            List of markdown lines
        """
        lines = [
            "## 📈 PR Quality Score",
            "",
            f"### Overall Rating: {score.overall_stars} ({score.quality_level})",
            f"**Total Penalty: {score.total_penalty:.1f} points** _(Lower is better!)_",
            "",
        ]

        if score.category_penalties:
            lines.extend(["### Category Penalties", ""])

            category_display = {
                'security': '🔒 Security',
                'performance': '⚡ Performance',
                'best_practices': '✨ Best Practices'
            }

            for category_key, display_name in category_display.items():
                if category_key in score.category_penalties:
                    cat_penalty = score.category_penalties[category_key]
                    cat_stars = score.category_stars.get(category_key, '')
                    lines.append(
                        f"- **{display_name}:** {cat_stars} {cat_penalty:.1f} penalty points"
                    )

            lines.append("")

        if score.scoring_breakdown:
            lines.extend([
                "<details>",
                "<summary>📊 Scoring Details (click to expand)</summary>",
                "",
            ])

            for detail in score.scoring_breakdown:
                lines.append(f"- {detail}")

            lines.extend(["", "</details>", ""])

        lines.extend(["---", ""])

        return lines
