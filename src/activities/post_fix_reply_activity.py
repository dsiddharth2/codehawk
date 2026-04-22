"""
Post Fix Reply Activity.

Posts reply comments to existing Azure DevOps PR comment threads to indicate
issues are fixed.
"""

from typing import Optional
from azure.devops.connection import Connection
from azure.devops.v7_1.git import GitClient
from azure.devops.v7_1.git.models import Comment
from msrest.authentication import BasicAuthentication

from activities.base_activity import BaseActivity
from config import Settings, get_settings


class PostFixReplyActivity(BaseActivity[dict, bool]):
    """Activity to post reply comments indicating issues are fixed."""

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

    def execute(self, input_data: dict) -> bool:
        """
        Post a reply comment to indicate an issue is fixed.

        Args:
            input_data: Dictionary containing:
                - thread_id: Thread ID to reply to
                - pr_id: Pull request ID
                - repository_id: Repository ID (optional)
                - message: Optional custom message

        Returns:
            True if successful, False otherwise
        """
        thread_id = input_data['thread_id']
        pr_id = input_data['pr_id']
        repository_id = input_data.get('repository_id') or self.settings.azure_devops_repo
        message = input_data.get('message', '✅ **Issue Fixed** - This issue has been resolved in the latest changes.')

        self._log_start(thread_id=thread_id, pr_id=pr_id, repository=repository_id)

        try:
            reply_comment = Comment(content=message)
            self.git_client.create_comment(
                comment=reply_comment,
                repository_id=repository_id,
                pull_request_id=pr_id,
                thread_id=thread_id,
                project=self.settings.azure_devops_project
            )

            from activities.post_pr_comment_activity import CommentThreadStatus
            from azure.devops.v7_1.git.models import CommentThread

            thread_update = CommentThread(status=CommentThreadStatus.FIXED)
            self.git_client.update_thread(
                comment_thread=thread_update,
                repository_id=repository_id,
                pull_request_id=pr_id,
                thread_id=thread_id,
                project=self.settings.azure_devops_project
            )

            self._log_success(thread_id=thread_id, pr_id=pr_id)
            return True

        except Exception as e:
            self._log_error(e, thread_id=thread_id, pr_id=pr_id)
            raise
