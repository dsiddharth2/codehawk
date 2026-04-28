"""
VCS tools — fetch PR data, file content, threads, and diffs via activity classes.
"""

import json
from typing import Optional

from config import Settings
from tools.registry import Tool, ToolRegistry


def register_vcs_tools(
    registry: ToolRegistry,
    settings: Settings,
    default_pr_id: int = 0,
    default_repo: str = "",
):
    from activities.fetch_pr_details_activity import FetchPRDetailsActivity
    from activities.fetch_file_content_activity import FetchFileContentActivity
    from activities.fetch_pr_comments_activity import FetchPRCommentsActivity
    from activities.fetch_file_diff_activity import FetchFileDiffActivity

    pr_activity = FetchPRDetailsActivity(settings=settings)
    file_activity = FetchFileContentActivity(settings=settings)
    comments_activity = FetchPRCommentsActivity(settings=settings)
    diff_activity = FetchFileDiffActivity(settings=settings)

    _known_paths: list[str] = []

    def _resolve_file_path(path: str) -> str:
        """Match a potentially truncated path against known PR file paths."""
        if not _known_paths:
            return path
        cleaned = path.lstrip("/")
        for known in _known_paths:
            if known.lstrip("/") == cleaned:
                return known
            if known.lstrip("/").endswith("/" + cleaned) or known.lstrip("/").endswith("\\" + cleaned):
                return known
        return path

    # -- get_pr ---------------------------------------------------------------

    def handle_get_pr(args: dict) -> str:
        from models.review_models import FetchPRDetailsInput

        pr_id = args.get("pr_id", default_pr_id)
        repo = args.get("repo", default_repo) or None
        result = pr_activity.execute(FetchPRDetailsInput(pr_id=pr_id, repository_id=repo))

        _known_paths.clear()
        _known_paths.extend(fc.path for fc in result.file_changes)

        return json.dumps({
            "pr_id": result.pr_id,
            "title": result.title,
            "description": result.description,
            "source_branch": result.source_branch,
            "target_branch": result.target_branch,
            "author": result.author,
            "source_commit_id": result.source_commit_id,
            "target_commit_id": result.target_commit_id,
            "total_additions": result.total_additions,
            "total_deletions": result.total_deletions,
            "file_changes": [
                {
                    "path": fc.path,
                    "change_type": fc.change_type,
                    "old_path": fc.old_path,
                    "additions": fc.additions,
                    "deletions": fc.deletions,
                }
                for fc in result.file_changes
            ],
        }, indent=2)

    registry.register(Tool(
        name="get_pr",
        schema={
            "description": (
                "Fetch pull request metadata: title, description, branches, author, "
                "and list of changed files with change types and line counts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pr_id": {"type": "integer", "description": "Pull request number"},
                    "repo": {"type": "string", "description": "Repository name (optional, uses default if omitted)"},
                },
                "required": ["pr_id"],
            },
        },
        handler=handle_get_pr,
    ))

    # -- get_file_content -----------------------------------------------------

    def handle_get_file_content(args: dict) -> str:
        from models.review_models import FetchFileContentInput

        file_path = _resolve_file_path(args["file_path"])
        commit_id = args["commit_id"]
        content = file_activity.execute(FetchFileContentInput(
            file_path=file_path,
            commit_id=commit_id,
            repository_id=args.get("repo") or None,
        ))
        return json.dumps({"file": file_path, "commit_id": commit_id, "content": content})

    registry.register(Tool(
        name="get_file_content",
        schema={
            "description": (
                "Fetch the content of a file at a specific commit SHA from the VCS. "
                "Returns the full file text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "File path relative to repo root"},
                    "commit_id": {"type": "string", "description": "Git commit SHA to read from"},
                },
                "required": ["file_path", "commit_id"],
            },
        },
        handler=handle_get_file_content,
    ))

    # -- list_threads ---------------------------------------------------------

    def handle_list_threads(args: dict) -> str:
        pr_id = args.get("pr_id", default_pr_id)
        repo = args.get("repo") or None
        threads = comments_activity.execute(pr_id=pr_id, repository_id=repo)

        return json.dumps([
            {
                "thread_id": t.thread_id,
                "file_path": t.file_path,
                "line_number": t.line_number,
                "status": t.status,
                "comment_text": t.comment_text[:500],
                "cr_id": t.cr_id,
            }
            for t in threads
        ], indent=2)

    registry.register(Tool(
        name="list_threads",
        schema={
            "description": (
                "Fetch existing review comment threads on a PR. "
                "Returns thread IDs, file paths, line numbers, status, cr_id markers, and comment text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pr_id": {"type": "integer", "description": "Pull request number"},
                    "repo": {"type": "string", "description": "Repository name (optional)"},
                },
                "required": ["pr_id"],
            },
        },
        handler=handle_list_threads,
    ))

    # -- get_file_diff --------------------------------------------------------

    def handle_get_file_diff(args: dict) -> str:
        from activities.fetch_file_diff_activity import FetchFileDiffInput

        result = diff_activity.execute(FetchFileDiffInput(
            file_path=_resolve_file_path(args["file_path"]),
            source_commit_id=args["source_commit_id"],
            target_commit_id=args["target_commit_id"],
            repository_id=args.get("repo") or None,
        ))
        return json.dumps({
            "file_path": result.file_path,
            "diff_text": result.diff_text[:10000],
            "added_lines_count": len(result.added_lines),
            "removed_lines_count": len(result.removed_lines),
            "changed_sections": result.changed_sections,
        }, indent=2)

    registry.register(Tool(
        name="get_file_diff",
        schema={
            "description": (
                "Get the unified diff for a file between two commits. "
                "Returns diff text, added lines, removed lines, and changed sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "File path relative to repo root"},
                    "source_commit_id": {"type": "string", "description": "Source (new) commit SHA"},
                    "target_commit_id": {"type": "string", "description": "Target (old/base) commit SHA"},
                },
                "required": ["file_path", "source_commit_id", "target_commit_id"],
            },
        },
        handler=handle_get_file_diff,
    ))
