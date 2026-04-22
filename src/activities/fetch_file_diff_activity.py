"""
Fetch File Diff Activity.

Fetches the diff/changes for a file in a pull request.
"""

import difflib
import re
from dataclasses import dataclass
from typing import Optional
from azure.devops.connection import Connection
from azure.devops.v7_1.git import GitClient
from azure.devops.v7_1.git.models import GitVersionDescriptor
from msrest.authentication import BasicAuthentication

from activities.base_activity import BaseActivity
from config import Settings, get_settings


@dataclass
class FetchFileDiffInput:
    """Input for FetchFileDiffActivity."""
    file_path: str
    source_commit_id: str
    target_commit_id: str
    repository_id: Optional[str] = None


@dataclass
class FileDiff:
    """Represents the diff of a file."""
    file_path: str
    diff_text: str
    added_lines: list[tuple[int, str]]
    removed_lines: list[tuple[int, str]]
    changed_sections: list[dict]


class FetchFileDiffActivity(BaseActivity[FetchFileDiffInput, FileDiff]):
    """Activity to fetch file diff from Azure DevOps."""

    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or get_settings()

        access_token = self.settings.get_azure_devops_token()
        credentials = BasicAuthentication('', access_token)
        self.connection = Connection(
            base_url=self.settings.azure_devops_url,
            creds=credentials
        )
        self.git_client: GitClient = self.connection.clients.get_git_client()

    def execute(self, input_data: FetchFileDiffInput) -> FileDiff:
        """
        Fetch file diff between two commits.

        Args:
            input_data: Input containing file_path, source/target commit IDs

        Returns:
            FileDiff object with diff information
        """
        file_path = input_data.file_path
        source_commit = input_data.source_commit_id
        target_commit = input_data.target_commit_id
        repo_id = input_data.repository_id or self.settings.azure_devops_repo
        project = self.settings.azure_devops_project

        self._log_start(file_path=file_path, source=source_commit[:7], target=target_commit[:7])

        try:
            try:
                source_version = GitVersionDescriptor(version=source_commit, version_type='commit')
                source_item = self.git_client.get_item(
                    repository_id=repo_id,
                    path=file_path,
                    project=project,
                    version_descriptor=source_version,
                    include_content=True
                )
                source_content = source_item.content if source_item.content else ""
            except Exception as e:
                self.logger.warning(f"Could not fetch source content for {file_path}: {str(e)}")
                source_content = ""

            try:
                target_version = GitVersionDescriptor(version=target_commit, version_type='commit')
                target_item = self.git_client.get_item(
                    repository_id=repo_id,
                    path=file_path,
                    project=project,
                    version_descriptor=target_version,
                    include_content=True
                )
                target_content = target_item.content if target_item.content else ""
            except Exception as e:
                self.logger.warning(f"Could not fetch target content for {file_path}: {str(e)}")
                target_content = ""

            parsed_data = self._create_simple_diff(source_content, target_content)

            result = FileDiff(
                file_path=file_path,
                diff_text=parsed_data['diff_text'],
                added_lines=parsed_data['added_lines'],
                removed_lines=parsed_data['removed_lines'],
                changed_sections=parsed_data['changed_sections']
            )

            self._log_success(
                file_path=file_path,
                added_lines=len(parsed_data['added_lines']),
                removed_lines=len(parsed_data['removed_lines']),
                sections=len(parsed_data['changed_sections'])
            )

            return result

        except Exception as e:
            self._log_error(e, file_path=file_path)
            raise

    def _create_simple_diff(self, source_content: str, target_content: str) -> dict:
        source_lines = source_content.splitlines(keepends=False) if source_content else []
        target_lines = target_content.splitlines(keepends=False) if target_content else []

        differ = difflib.unified_diff(
            target_lines,
            source_lines,
            lineterm='',
            n=10
        )

        added_lines = []
        removed_lines = []
        diff_text_parts = []
        changed_sections = []

        current_line_num = 0
        section_start = None
        section_type = None

        for line in differ:
            if line.startswith('+++') or line.startswith('---'):
                continue
            elif line.startswith('@@'):
                diff_text_parts.append(line)
                match = re.match(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
                if match:
                    section_start = int(match.group(2))
                    if section_type:
                        changed_sections.append({
                            'start_line': section_start,
                            'end_line': current_line_num,
                            'type': section_type
                        })
                    section_type = 'modified'
            elif line.startswith('+'):
                current_line_num += 1
                added_lines.append((current_line_num, line[1:]))
                diff_text_parts.append(line)
            elif line.startswith('-'):
                removed_lines.append((current_line_num, line[1:]))
                diff_text_parts.append(line)
            else:
                current_line_num += 1
                diff_text_parts.append(line)

        if section_start and section_type:
            changed_sections.append({
                'start_line': section_start,
                'end_line': current_line_num,
                'type': section_type,
                'modified_lines': len(added_lines),
                'original_lines': len(removed_lines)
            })

        return {
            'added_lines': added_lines,
            'removed_lines': removed_lines,
            'changed_sections': changed_sections,
            'diff_text': '\n'.join(diff_text_parts)
        }
