"""
Fetch PR Comments Activity.

Fetches existing PR comment threads from Azure DevOps and parses them
into structured ExistingCommentThread objects. Extracts cr-id markers
from comment text (<!-- cr-id: xxx -->).
"""

import re
from typing import List, Optional
from azure.devops.connection import Connection
from azure.devops.v7_1.git import GitClient
from msrest.authentication import BasicAuthentication

from activities.base_activity import BaseActivity
from models.review_models import ExistingCommentThread
from config import Settings, get_settings


class FetchPRCommentsActivity(BaseActivity[int, List[ExistingCommentThread]]):
    """Activity to fetch existing PR comment threads from Azure DevOps."""

    _CODEHAWK_PATTERNS = (
        '## 🔴', '## ⚠️', '## 💡',
        '# 🤖 AI Code Review', '# 🔍 AI Code Review', '# 🔄 AI Code Re-Review',
        '**Issue Fixed**', '**Still present**',
        '✅ **Accepted**', '❌ **Finding stands**',
        '✅ **Dismissal accepted**',
    )

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

    def execute(self, pr_id: int, repository_id: Optional[str] = None) -> List[ExistingCommentThread]:
        """
        Fetch existing comment threads from Azure DevOps.

        Args:
            pr_id: Pull request ID
            repository_id: Repository ID (uses default from settings if None)

        Returns:
            List of ExistingCommentThread objects with cr_id populated where present
        """
        repo_id = repository_id or self.settings.azure_devops_repo
        project = self.settings.azure_devops_project

        self._log_start(pr_id=pr_id, repository=repo_id)

        try:
            threads = self.git_client.get_threads(
                repository_id=repo_id,
                pull_request_id=pr_id,
                project=project
            )

            existing_comments = []
            skipped_no_context = 0
            skipped_wrong_status = 0
            skipped_no_line = 0

            for thread in threads:
                if not thread.thread_context or not thread.thread_context.file_path:
                    skipped_no_context += 1
                    continue

                if thread.status not in ['active', 'fixed', 1, 2]:
                    skipped_wrong_status += 1
                    continue

                if not thread.comments or len(thread.comments) == 0:
                    continue

                first_comment = thread.comments[0]
                comment_text = first_comment.content

                if not comment_text:
                    continue

                line_number = self._extract_line_number(thread.thread_context)
                if line_number is None:
                    skipped_no_line += 1
                    continue

                parsed_data = self._parse_comment_markdown(comment_text)
                cr_id = self._extract_cr_id_from_properties(thread) or self._extract_cr_id(comment_text)

                existing_comment = ExistingCommentThread(
                    thread_id=thread.id,
                    file_path=thread.thread_context.file_path,
                    line_number=line_number,
                    status=thread.status,
                    comment_text=comment_text,
                    created_date=first_comment.published_date.isoformat() if first_comment.published_date else "",
                    severity=parsed_data.get('severity'),
                    category=parsed_data.get('category'),
                    message=parsed_data.get('message', comment_text),
                    confidence=parsed_data.get('confidence', 0.0),
                    cr_id=cr_id
                )

                existing_comments.append(existing_comment)

            self._log_success(
                pr_id=pr_id,
                total_threads=len(threads),
                active_line_comments=len(existing_comments),
                skipped_no_context=skipped_no_context,
                skipped_wrong_status=skipped_wrong_status,
                skipped_no_line=skipped_no_line
            )

            return existing_comments

        except Exception as e:
            self._log_error(e, pr_id=pr_id)
            raise

    def _extract_cr_id_from_properties(self, thread) -> Optional[str]:
        """Extract cr-id from ADO thread properties (preferred over comment text)."""
        props = getattr(thread, 'properties', None)
        if not props:
            return None
        cr_id_prop = props.get('CodeHawk.CrId')
        if isinstance(cr_id_prop, dict):
            return cr_id_prop.get('$value')
        if isinstance(cr_id_prop, str):
            return cr_id_prop
        return None

    def _extract_cr_id(self, comment_text: str) -> Optional[str]:
        """
        Extract cr-id from HTML comment marker <!-- cr-id: xxx -->.
        Backward compat fallback for threads posted before properties support.
        """
        match = re.search(r'<!--\s*cr-id:\s*(\S+)\s*-->', comment_text)
        return match.group(1) if match else None

    def _extract_line_number(self, thread_context) -> Optional[int]:
        try:
            if hasattr(thread_context, 'right_file_end') and thread_context.right_file_end:
                if hasattr(thread_context.right_file_end, 'line'):
                    return thread_context.right_file_end.line

            if hasattr(thread_context, 'right_file_start') and thread_context.right_file_start:
                if hasattr(thread_context.right_file_start, 'line'):
                    return thread_context.right_file_start.line

            if hasattr(thread_context, 'left_file_end') and thread_context.left_file_end:
                if hasattr(thread_context.left_file_end, 'line'):
                    return thread_context.left_file_end.line

            return None
        except Exception as e:
            self.logger.warning(f"Failed to extract line number from thread context: {e}")
            return None

    def _parse_comment_markdown(self, markdown: str) -> dict:
        """
        Parse markdown comment to extract review metadata.

        Args:
            markdown: Comment markdown text

        Returns:
            Dictionary with extracted severity, category, message, confidence
        """
        result = {
            'severity': None,
            'category': None,
            'message': markdown,
            'confidence': 0.0
        }

        try:
            severity_pattern = r'##\s*(?:🔴|⚠️|💡)?\s*(CRITICAL|WARNING|SUGGESTION|GOOD)'
            severity_match = re.search(severity_pattern, markdown, re.IGNORECASE)
            if severity_match:
                result['severity'] = severity_match.group(1).lower()

            category_pattern = r'##\s*(?:🔴|⚠️|💡)?\s*(?:CRITICAL|WARNING|SUGGESTION|GOOD)[:\s]+(\w+)'
            category_match = re.search(category_pattern, markdown, re.IGNORECASE)
            if category_match:
                category_text = category_match.group(1).lower()
                category_map = {
                    'security': 'security',
                    'performance': 'performance',
                    'practices': 'best_practices',
                    'best': 'best_practices',
                    'style': 'code_style',
                    'documentation': 'documentation',
                    'docs': 'documentation'
                }
                result['category'] = category_map.get(category_text, category_text)

            confidence_pattern = r'[Cc]onfidence[:\s=]+(\d+\.?\d*)'
            confidence_match = re.search(confidence_pattern, markdown)
            if confidence_match:
                try:
                    result['confidence'] = float(confidence_match.group(1))
                except ValueError:
                    pass

            message_parts = []
            in_code_block = False
            for line in markdown.split('\n'):
                if line.strip().startswith('#'):
                    continue
                if line.strip().startswith('```'):
                    in_code_block = not in_code_block
                    continue
                if line.strip() and not in_code_block:
                    message_parts.append(line.strip())

            if message_parts:
                result['message'] = ' '.join(message_parts)

        except Exception as e:
            self.logger.warning(f"Failed to parse comment markdown: {e}")

        return result

    def get_developer_replies(
        self, pr_id: int, repository_id: Optional[str] = None,
    ) -> dict[str, str]:
        """Fetch full conversation thread for each CodeHawk thread with developer replies.

        Returns {cr_id: formatted_conversation} where the conversation includes
        all comments labeled as [CodeHawk] or [Developer Name].
        Only includes threads where at least one developer reply exists.
        """
        repo_id = repository_id or self.settings.azure_devops_repo
        project = self.settings.azure_devops_project

        try:
            threads = self.git_client.get_threads(
                repository_id=repo_id,
                pull_request_id=pr_id,
                project=project,
            )
        except Exception as e:
            self.logger.warning("Failed to fetch threads for developer replies: %s", e)
            return {}

        replies: dict[str, str] = {}
        for thread in threads:
            if not thread.comments or len(thread.comments) < 2:
                continue

            cr_id = self._extract_cr_id_from_properties(thread) or self._extract_cr_id(
                thread.comments[0].content or ""
            )
            if not cr_id:
                continue

            has_developer_reply = False
            conversation_lines: list[str] = []

            for comment in thread.comments:
                content = (comment.content or "").strip()
                if not content:
                    continue
                author = getattr(comment, "author", None)
                author_unique = getattr(author, "unique_name", "") or ""
                author_display = getattr(author, "display_name", "Unknown") or "Unknown"

                if self._is_codehawk_comment(content, author_unique):
                    conversation_lines.append(f"[CodeHawk] {content}")
                else:
                    conversation_lines.append(f"[{author_display}] {content}")
                    has_developer_reply = True

            if has_developer_reply:
                replies[cr_id] = "\n\n".join(conversation_lines)

        self.logger.info(
            "Developer replies: %d threads with replies out of %d total",
            len(replies), len(threads),
        )
        return replies

    def _is_codehawk_comment(self, content: str, author_unique_name: str) -> bool:
        if not author_unique_name:
            return True
        return any(content.startswith(p) for p in self._CODEHAWK_PATTERNS)
