"""
Fetch PR Details Activity.

Fetches pull request details from Azure DevOps.
"""

from azure.devops.connection import Connection
from azure.devops.v7_1.git import GitClient
from msrest.authentication import BasicAuthentication

from activities.base_activity import BaseActivity
from models.review_models import FetchPRDetailsInput, PullRequestDetails, FileChange
from config import Settings, get_settings


class FetchPRDetailsActivity(BaseActivity[FetchPRDetailsInput, PullRequestDetails]):
    """Activity to fetch PR details from Azure DevOps."""

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

    def execute(self, input_data: FetchPRDetailsInput) -> PullRequestDetails:
        """
        Fetch PR details from Azure DevOps.

        Args:
            input_data: Input containing pr_id and optional repository_id

        Returns:
            PullRequestDetails object
        """
        pr_id = input_data.pr_id
        repo_id = input_data.repository_id or self.settings.azure_devops_repo
        project = self.settings.azure_devops_project

        self._log_start(pr_id=pr_id, repository=repo_id)

        try:
            pr = self.git_client.get_pull_request(
                pull_request_id=pr_id,
                repository_id=repo_id,
                project=project
            )

            source_commit_id = pr.last_merge_source_commit.commit_id if pr.last_merge_source_commit else None
            target_commit_id = pr.last_merge_target_commit.commit_id if pr.last_merge_target_commit else None

            file_changes = self._extract_file_changes(pr_id, repo_id, project)

            total_additions = sum(fc.additions for fc in file_changes)
            total_deletions = sum(fc.deletions for fc in file_changes)

            pr_details = PullRequestDetails(
                pr_id=pr_id,
                title=pr.title,
                description=pr.description or "",
                source_branch=pr.source_ref_name.replace('refs/heads/', ''),
                target_branch=pr.target_ref_name.replace('refs/heads/', ''),
                author=pr.created_by.display_name,
                repository=repo_id,
                project=project,
                organization=self.settings.azure_devops_org,
                file_changes=file_changes,
                total_additions=total_additions,
                total_deletions=total_deletions,
                source_commit_id=source_commit_id,
                target_commit_id=target_commit_id
            )

            self._log_success(
                pr_id=pr_id,
                files_changed=len(file_changes),
                total_additions=total_additions,
                total_deletions=total_deletions
            )

            return pr_details

        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__

            if "TF401180" in error_msg:
                helpful_msg = (
                    f"Pull request #{pr_id} not found in repository '{repo_id}'. "
                    f"Verify PR ID, repository, and project in your configuration."
                )
                self.logger.error(helpful_msg)
                raise ValueError(helpful_msg) from e

            elif "TF401019" in error_msg or "403" in error_msg or "unauthorized" in error_msg.lower():
                helpful_msg = (
                    f"Access denied to PR #{pr_id}. "
                    f"Check that AZURE_DEVOPS_PAT has 'Code (Read)' permissions and has not expired."
                )
                self.logger.error(helpful_msg)
                raise PermissionError(helpful_msg) from e

            elif "404" in error_msg:
                helpful_msg = (
                    f"Resource not found. Verify repository '{repo_id}', project '{project}', "
                    f"and organization in your configuration."
                )
                self.logger.error(helpful_msg)
                raise ValueError(helpful_msg) from e

            self._log_error(e, pr_id=pr_id, error_type=error_type)
            raise

    def _extract_file_changes(
        self, pr_id: int, repository_id: str, project: str
    ) -> list[FileChange]:
        try:
            commits = self.git_client.get_pull_request_commits(
                pull_request_id=pr_id,
                repository_id=repository_id,
                project=project
            )

            if not commits:
                self.logger.warning(f"No commits found for PR #{pr_id}")
                return []

            all_file_changes = {}

            for commit in commits:
                commit_changes = self.git_client.get_changes(
                    commit_id=commit.commit_id,
                    repository_id=repository_id,
                    project=project
                )

                if not hasattr(commit_changes, 'changes') or not commit_changes.changes:
                    continue

                for change in commit_changes.changes:
                    if not isinstance(change, dict):
                        continue

                    item = change.get('item', {})
                    if not item:
                        continue

                    if item.get('gitObjectType', '') != 'blob':
                        continue

                    path = item.get('path', '')
                    if not path:
                        continue

                    change_type = self._map_change_type(change.get('changeType', 'edit'))

                    if path not in all_file_changes or change_type != 'edit':
                        all_file_changes[path] = FileChange(
                            path=path,
                            change_type=change_type,
                            old_path=None,
                            additions=0,
                            deletions=0,
                            changed_lines=[]
                        )

            file_changes = list(all_file_changes.values())
            self._populate_diff_details(pr_id, repository_id, project, file_changes)
            return file_changes

        except Exception as e:
            self.logger.exception("Failed to extract file changes for PR #%s: %s", pr_id, e)
            raise RuntimeError(f"Failed to extract file changes for PR #{pr_id}: {e}") from e

    def _populate_diff_details(
        self, pr_id: int, repository_id: str, project: str, file_changes: list[FileChange]
    ):
        for file_change in file_changes:
            if file_change.change_type in ['edit', 'add']:
                file_change.changed_lines = [(1, 9999)]
                file_change.additions = 1
                file_change.deletions = 0 if file_change.change_type == 'add' else 1

    def _map_change_type(self, azure_change_type: str) -> str:
        change_map = {
            'add': 'add',
            'edit': 'edit',
            'delete': 'delete',
            'rename': 'rename',
            'sourcerename': 'rename',
            'targetrename': 'rename',
        }
        return change_map.get(str(azure_change_type).lower(), 'edit')
