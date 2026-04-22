"""
Fetch File Content Activity.

Fetches file content from Azure DevOps at a specific commit.
"""

from azure.devops.connection import Connection
from azure.devops.v7_1.git import GitClient
from azure.devops.v7_1.git.models import GitVersionDescriptor
from msrest.authentication import BasicAuthentication

from activities.base_activity import BaseActivity
from models.review_models import FetchFileContentInput
from config import Settings, get_settings


class FetchFileContentActivity(BaseActivity[FetchFileContentInput, str]):
    """Activity to fetch file content from Azure DevOps."""

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

    def execute(self, input_data: FetchFileContentInput) -> str:
        """
        Fetch file content at a specific commit.

        Args:
            input_data: Input containing file_path, commit_id, and optional repository_id

        Returns:
            File content as string
        """
        file_path = input_data.file_path
        commit_id = input_data.commit_id
        if not file_path:
            raise ValueError("file_path is required")
        if not commit_id:
            raise ValueError("commit_id is required")
        repo_id = input_data.repository_id or self.settings.azure_devops_repo
        project = self.settings.azure_devops_project

        self._log_start(file_path=file_path, commit_id=commit_id)

        try:
            version_descriptor = GitVersionDescriptor(
                version=commit_id,
                version_type='commit'
            )

            content_stream = self.git_client.get_item_text(
                repository_id=repo_id,
                path=file_path,
                project=project,
                version_descriptor=version_descriptor,
            )
            content = ''.join(chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk
                              for chunk in content_stream)

            self._log_success(file_path=file_path, content_length=len(content))
            return content

        except Exception as e:
            self._log_error(e, file_path=file_path)
            raise
