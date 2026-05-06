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
    source_commit_id: str = "",
    target_commit_id: str = "",
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
    _commit_ids: dict[str, str] = {
        "source": source_commit_id,
        "target": target_commit_id,
    }

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

        _commit_ids["source"] = result.source_commit_id or ""
        _commit_ids["target"] = result.target_commit_id or ""

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

    def _resolve_commit_id(raw: str) -> str:
        """Resolve symbolic refs like HEAD/source/target to actual SHAs."""
        normalized = raw.strip().lower()
        if normalized in ("head", "source", "latest"):
            return _commit_ids.get("source", raw)
        if normalized in ("base", "target", "main", "master"):
            return _commit_ids.get("target", raw)
        return raw

    def handle_get_file_content(args: dict) -> str:
        from models.review_models import FetchFileContentInput

        file_path = _resolve_file_path(args["file_path"])
        commit_id = _resolve_commit_id(args["commit_id"])
        if not commit_id or len(commit_id) < 7:
            return json.dumps({
                "error": f"Invalid commit_id '{args['commit_id']}'. Use the source_commit_id or target_commit_id from get_pr.",
            })
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
                "Returns the full file text. "
                "Use 'source' for the PR head commit or 'target' for the base commit "
                "(or pass a full 40-char SHA from get_pr's source_commit_id/target_commit_id)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "File path relative to repo root"},
                    "commit_id": {
                        "type": "string",
                        "description": (
                            "Commit reference: 'source' (PR head), 'target' (PR base), "
                            "or a full 40-character SHA from get_pr output"
                        ),
                    },
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
        from smart_diff import summarize_diff, format_summary_for_agent, extract_hunks_in_range

        result = diff_activity.execute(FetchFileDiffInput(
            file_path=_resolve_file_path(args["file_path"]),
            source_commit_id=_resolve_commit_id(args["source_commit_id"]),
            target_commit_id=_resolve_commit_id(args["target_commit_id"]),
            repository_id=args.get("repo") or None,
        ))

        diff_text = result.diff_text
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        # Drill-in mode: return only hunks overlapping the requested line range
        if start_line is not None and end_line is not None:
            diff_text = extract_hunks_in_range(diff_text, int(start_line), int(end_line))
            if len(diff_text) > 30000:
                diff_text = diff_text[:30000] + "\n... [truncated at 30KB]"
            return json.dumps({
                "file_path": result.file_path,
                "diff_text": diff_text,
                "added_lines_count": len(result.added_lines),
                "removed_lines_count": len(result.removed_lines),
                "drill_in": True,
                "start_line": start_line,
                "end_line": end_line,
            }, indent=2)

        # Smart diff: summarize large diffs instead of truncating
        summary = summarize_diff(
            diff_text,
            file_path=result.file_path,
            threshold_kb=settings.smart_diff_threshold_kb,
        )
        if summary.is_summarized:
            return json.dumps({
                "file_path": result.file_path,
                "is_summary": True,
                "summary": format_summary_for_agent(summary),
                "added_lines_count": len(result.added_lines),
                "removed_lines_count": len(result.removed_lines),
                "hint": (
                    "Diff was too large to return in full. "
                    "Review the hunk summary above, identify high-risk sections, "
                    "then call get_file_diff again with start_line and end_line to drill in."
                ),
            }, indent=2)

        # Normal diff: return full text up to 30KB safety cap
        if len(diff_text) > 30000:
            diff_text = diff_text[:30000] + "\n... [truncated at 30KB]"
        return json.dumps({
            "file_path": result.file_path,
            "diff_text": diff_text,
            "added_lines_count": len(result.added_lines),
            "removed_lines_count": len(result.removed_lines),
            "changed_sections": result.changed_sections,
        }, indent=2)

    registry.register(Tool(
        name="get_file_diff",
        schema={
            "description": (
                "Get the unified diff for a file between two commits. "
                "Returns diff text, added lines, removed lines, and changed sections. "
                "Use 'source' and 'target' as shortcuts for PR head/base commits. "
                "If the diff is too large, returns is_summary=true with hunk summaries — "
                "use start_line/end_line to drill into specific sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "File path relative to repo root"},
                    "source_commit_id": {
                        "type": "string",
                        "description": "Source (new) commit: 'source' or a 40-char SHA",
                    },
                    "target_commit_id": {
                        "type": "string",
                        "description": "Target (base) commit: 'target' or a 40-char SHA",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": (
                            "Start line number for drill-in mode. "
                            "When combined with end_line, returns only hunks overlapping this range."
                        ),
                    },
                    "end_line": {
                        "type": "integer",
                        "description": (
                            "End line number for drill-in mode. "
                            "When combined with start_line, returns only hunks overlapping this range."
                        ),
                    },
                },
                "required": ["file_path", "source_commit_id", "target_commit_id"],
            },
        },
        handler=handle_get_file_diff,
    ))
