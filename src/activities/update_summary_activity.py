"""
Update Summary Activity.

Updates existing summary comment instead of creating a new one.
If no existing summary is found, creates a new one.
"""

from typing import Optional
from azure.devops.connection import Connection
from azure.devops.v7_1.git import GitClient
from azure.devops.v7_1.git.models import Comment
from msrest.authentication import BasicAuthentication

from activities.base_activity import BaseActivity
from config import Settings, get_settings

# Marker used to identify codehawk summary comments
SUMMARY_MARKER = "<!-- codehawk-summary -->"


class UpdateSummaryInput:
    """Input for updating summary comment."""

    def __init__(
        self,
        pr_id: int,
        new_content: str,
        repository_id: Optional[str] = None
    ):
        self.pr_id = pr_id
        self.new_content = new_content
        self.repository_id = repository_id


class UpdateSummaryResult:
    """Result of updating summary comment."""

    def __init__(
        self,
        pr_id: int,
        thread_id: int,
        updated: bool,
        created_new: bool
    ):
        self.pr_id = pr_id
        self.thread_id = thread_id
        self.updated = updated
        self.created_new = created_new


class UpdateSummaryActivity(BaseActivity[UpdateSummaryInput, UpdateSummaryResult]):
    """Activity to update existing summary comment on a PR."""

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

    def execute(self, input_data: UpdateSummaryInput) -> UpdateSummaryResult:
        """
        Update existing summary comment or create new one.

        Args:
            input_data: UpdateSummaryInput with PR details and content

        Returns:
            UpdateSummaryResult with update status
        """
        pr_id = input_data.pr_id
        repo_id = input_data.repository_id or self.settings.azure_devops_repo
        project = self.settings.azure_devops_project

        self._log_start(pr_id=pr_id, repository=repo_id)

        try:
            summary_thread = self._find_summary_thread(pr_id, repo_id, project)

            if summary_thread and self.settings.update_existing_summary:
                self.logger.info(f"Found existing summary thread {summary_thread.id}, adding new comment")

                new_comment = Comment(content=input_data.new_content)
                self.git_client.create_comment(
                    comment=new_comment,
                    repository_id=repo_id,
                    pull_request_id=pr_id,
                    thread_id=summary_thread.id,
                    project=project
                )

                result = UpdateSummaryResult(
                    pr_id=pr_id,
                    thread_id=summary_thread.id,
                    updated=True,
                    created_new=False
                )
                self._log_success(pr_id=pr_id, thread_id=summary_thread.id, action="updated")

            else:
                self.logger.info("Creating new summary thread")
                thread_id = self._create_new_summary(pr_id, repo_id, project, input_data.new_content)

                result = UpdateSummaryResult(
                    pr_id=pr_id,
                    thread_id=thread_id,
                    updated=False,
                    created_new=True
                )
                self._log_success(pr_id=pr_id, thread_id=thread_id, action="created")

            return result

        except Exception as e:
            self._log_error(e, pr_id=pr_id)
            raise

    def _find_summary_thread(
        self,
        pr_id: int,
        repository_id: str,
        project: str
    ) -> Optional[any]:
        """
        Find existing summary thread for the PR.

        Summary threads are identified by the SUMMARY_MARKER or legacy content markers.
        """
        try:
            threads = self.git_client.get_threads(
                repository_id=repository_id,
                pull_request_id=pr_id,
                project=project
            )

            summary_markers = [
                SUMMARY_MARKER,
                "AI Code Review",
                "Code Review Summary",
                "Code Review Update",
                "Fix Verification",
                "📊 PR Quality Score"
            ]

            for thread in threads:
                if thread.thread_context and thread.thread_context.file_path:
                    continue

                if thread.comments and len(thread.comments) > 0:
                    content = thread.comments[0].content or ""
                    for marker in summary_markers:
                        if marker in content:
                            self.logger.info(f"Found summary thread {thread.id} with marker '{marker}'")
                            return thread

            self.logger.info("No existing summary thread found")
            return None

        except Exception as e:
            self.logger.warning(f"Failed to find existing summary thread: {e}")
            return None

    def _create_new_summary(
        self,
        pr_id: int,
        repository_id: str,
        project: str,
        comment_text: str
    ) -> int:
        from azure.devops.v7_1.git.models import CommentThread

        # Inject summary marker so future updates can find this thread
        content_with_marker = f"{comment_text}\n\n{SUMMARY_MARKER}"

        comment = Comment(content=content_with_marker)
        thread = CommentThread(comments=[comment], status=1)

        created_thread = self.git_client.create_thread(
            comment_thread=thread,
            repository_id=repository_id,
            pull_request_id=pr_id,
            project=project
        )

        return created_thread.id
