"""
Post PR Comment Activity.

Posts comments to Azure DevOps pull requests including:
- Line-specific thread comments on files
- Overall summary comments on the PR

Appends <!-- cr-id: {cr_id} --> markers to track findings across re-pushes.
"""

from typing import List, Optional
from azure.devops.connection import Connection
from azure.devops.v7_1.git import GitClient
from azure.devops.v7_1.git.models import (
    Comment,
    CommentThread,
    CommentPosition,
    CommentThreadContext
)
from msrest.authentication import BasicAuthentication

from activities.base_activity import BaseActivity
from models.review_models import ReviewComment
from config import Settings, get_settings


class CommentThreadStatus:
    """Comment thread status constants for Azure DevOps."""
    ACTIVE = 1
    FIXED = 2
    WONT_FIX = 3
    CLOSED = 4


class PostPRCommentInput:
    """Input for posting PR comments."""

    def __init__(
        self,
        pr_id: int,
        comment_text: Optional[str] = None,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
        thread_comments: Optional[List[ReviewComment]] = None,
        repository_id: Optional[str] = None
    ):
        self.pr_id = pr_id
        self.comment_text = comment_text
        self.file_path = file_path
        self.line_number = line_number
        self.thread_comments = thread_comments or []
        self.repository_id = repository_id


class PostPRCommentResult:
    """Result of posting PR comments."""

    def __init__(
        self,
        pr_id: int,
        comments_posted: int,
        summary_posted: bool,
        thread_ids: List[int] = None,
        errors: List[str] = None
    ):
        self.pr_id = pr_id
        self.comments_posted = comments_posted
        self.summary_posted = summary_posted
        self.thread_ids = thread_ids or []
        self.errors = errors or []


class PostPRCommentActivity(BaseActivity[PostPRCommentInput, PostPRCommentResult]):
    """Activity to post comments to Azure DevOps pull requests."""

    def __init__(self, settings: Settings = None):
        super().__init__()
        self.settings = settings or get_settings()

        access_token = self.settings.get_azure_devops_token()
        credentials = BasicAuthentication('', access_token)
        self.connection = Connection(
            base_url=self.settings.azure_devops_url,
            creds=credentials
        )
        self.git_client: GitClient = self.connection.clients.get_git_client()

    def execute(self, input_data: PostPRCommentInput) -> PostPRCommentResult:
        """
        Post comments to Azure DevOps PR.

        Args:
            input_data: PostPRCommentInput with comment details

        Returns:
            PostPRCommentResult with posting status
        """
        pr_id = input_data.pr_id
        repo_id = input_data.repository_id or self.settings.azure_devops_repo
        project = self.settings.azure_devops_project

        self._log_start(pr_id=pr_id, repository=repo_id)

        comments_posted = 0
        summary_posted = False
        thread_ids = []
        errors = []

        try:
            if input_data.comment_text:
                try:
                    thread_id = self._post_summary_comment(
                        pr_id, repo_id, project, input_data.comment_text
                    )
                    thread_ids.append(thread_id)
                    summary_posted = True
                    comments_posted += 1
                except Exception as e:
                    error_msg = f"Failed to post summary comment: {str(e)}"
                    errors.append(error_msg)
                    self.logger.error(error_msg)

            if input_data.file_path and input_data.line_number:
                try:
                    thread_id = self._post_line_comment(
                        pr_id, repo_id, project,
                        input_data.file_path,
                        input_data.line_number,
                        input_data.comment_text or "Review comment"
                    )
                    thread_ids.append(thread_id)
                    comments_posted += 1
                except Exception as e:
                    error_msg = f"Failed to post line comment: {str(e)}"
                    errors.append(error_msg)
                    self.logger.error(error_msg)

            if input_data.thread_comments:
                posted, failed = self._post_thread_comments(
                    pr_id, repo_id, project, input_data.thread_comments
                )
                comments_posted += posted
                errors.extend(failed)

            result = PostPRCommentResult(
                pr_id=pr_id,
                comments_posted=comments_posted,
                summary_posted=summary_posted,
                thread_ids=thread_ids,
                errors=errors
            )

            self._log_success(
                pr_id=pr_id,
                comments_posted=comments_posted,
                summary_posted=summary_posted,
                errors_count=len(errors)
            )

            return result

        except Exception as e:
            self._log_error(e, pr_id=pr_id)
            raise

    def _post_summary_comment(
        self, pr_id: int, repository_id: str, project: str, comment_text: str
    ) -> int:
        comment = Comment(content=comment_text)
        thread = CommentThread(comments=[comment], status=CommentThreadStatus.ACTIVE)
        created_thread = self.git_client.create_thread(
            comment_thread=thread,
            repository_id=repository_id,
            pull_request_id=pr_id,
            project=project
        )
        return created_thread.id

    def _post_line_comment(
        self,
        pr_id: int,
        repository_id: str,
        project: str,
        file_path: str,
        line_number: int,
        comment_text: str,
        severity: str = "suggestion",
        cr_id: Optional[str] = None
    ) -> int:
        """
        Post a comment on a specific line in a file.

        If cr_id is provided, appends <!-- cr-id: {cr_id} --> to the comment body
        so it can be matched on future re-pushes.
        """
        if not file_path.startswith('/'):
            file_path = '/' + file_path

        # Inject cr-id marker if provided
        if cr_id:
            comment_text = f"{comment_text}\n\n<!-- cr-id: {cr_id} -->"

        comment = Comment(content=comment_text)
        thread_context = CommentThreadContext(
            file_path=file_path,
            right_file_start=CommentPosition(line=line_number, offset=1),
            right_file_end=CommentPosition(line=line_number, offset=1)
        )
        thread = CommentThread(
            comments=[comment],
            status=CommentThreadStatus.ACTIVE,
            thread_context=thread_context
        )
        created_thread = self.git_client.create_thread(
            comment_thread=thread,
            repository_id=repository_id,
            pull_request_id=pr_id,
            project=project
        )
        return created_thread.id

    def _post_thread_comments(
        self,
        pr_id: int,
        repository_id: str,
        project: str,
        review_comments: List[ReviewComment]
    ) -> tuple[int, List[str]]:
        posted_count = 0
        errors = []

        for review_comment in review_comments:
            try:
                formatted_comment = self._format_review_comment(review_comment)
                cr_id = getattr(review_comment, 'id', None)

                thread_id = self._post_line_comment(
                    pr_id=pr_id,
                    repository_id=repository_id,
                    project=project,
                    file_path=review_comment.file_path,
                    line_number=review_comment.line_number,
                    comment_text=formatted_comment,
                    severity=review_comment.severity,
                    cr_id=cr_id
                )
                posted_count += 1
                self.logger.info(
                    f"Posted {review_comment.severity} comment to "
                    f"{review_comment.file_path}:{review_comment.line_number}"
                )
            except Exception as e:
                error_msg = (
                    f"Failed to post comment at {review_comment.file_path}:{review_comment.line_number}: "
                    f"{str(e)}"
                )
                errors.append(error_msg)
                self.logger.error(error_msg)

        return posted_count, errors

    def _format_review_comment(self, review_comment: ReviewComment) -> str:
        """Format a ReviewComment into markdown for Azure DevOps."""
        severity_icons = {
            'critical': '🔴',
            'warning': '⚠️',
            'suggestion': '💡',
            'info': 'ℹ️'
        }

        icon = severity_icons.get(review_comment.severity, '📝')
        severity_title = review_comment.severity.upper()

        if hasattr(review_comment, 'line_range') and review_comment.line_range:
            start, end = review_comment.line_range
            location = f"Lines {start}-{end}"
        else:
            location = f"Line {review_comment.line_number}"

        lines = [
            f"## {icon} {severity_title}: {review_comment.category.replace('_', ' ').title()}",
            f"**{location}**",
            "",
            review_comment.message,
        ]

        suggested_code = (
            getattr(review_comment, 'suggested_fix', None) or
            getattr(review_comment, 'suggested_code', None) or
            getattr(review_comment, 'suggestion', None)
        )

        if suggested_code:
            lines.extend([
                "",
                "### 💡 Suggested Fix:",
                "",
                "```" + (getattr(review_comment, 'language', '') or ""),
                suggested_code.strip(),
                "```"
            ])

        if hasattr(review_comment, 'explanation') and review_comment.explanation:
            lines.extend([
                "",
                "### 📖 Explanation:",
                "",
                review_comment.explanation
            ])

        if hasattr(review_comment, 'references') and review_comment.references:
            lines.extend(["", "### 🔗 References:", ""])
            for ref in review_comment.references:
                lines.append(f"- {ref}")

        confidence = getattr(review_comment, 'confidence_score', None) or getattr(review_comment, 'confidence', 0.8)
        confidence_pct = int(confidence * 100)
        lines.extend(["", f"*Confidence: {confidence_pct}%*"])

        return "\n".join(lines)
