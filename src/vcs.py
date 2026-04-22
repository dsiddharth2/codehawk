"""
VCS CLI — thin wrapper around codehawk activities.

Subcommands:
  get-pr          Fetch PR details
  list-threads    Fetch existing PR comment threads
  post-comment    Post an inline comment to a PR
  resolve-thread  Mark a thread as fixed
  get-file        Fetch file content at a commit
  post-summary    Post or update the PR summary comment

All output is JSON to stdout; errors go to stderr.
Auth is sourced from environment variables via Settings.
"""

import argparse
import json
import sys
from dataclasses import asdict


def _load_settings():
    from config import get_settings
    return get_settings()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_get_pr(args):
    from activities.fetch_pr_details_activity import FetchPRDetailsActivity
    from models.review_models import FetchPRDetailsInput

    settings = _load_settings()
    activity = FetchPRDetailsActivity(settings=settings)
    input_data = FetchPRDetailsInput(
        pr_id=args.pr_id,
        repository_id=args.repo or None
    )
    result = activity.execute(input_data)

    output = {
        "pr_id": result.pr_id,
        "title": result.title,
        "description": result.description,
        "source_branch": result.source_branch,
        "target_branch": result.target_branch,
        "author": result.author,
        "repository": result.repository,
        "project": result.project,
        "organization": result.organization,
        "total_additions": result.total_additions,
        "total_deletions": result.total_deletions,
        "source_commit_id": result.source_commit_id,
        "target_commit_id": result.target_commit_id,
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
    }
    print(json.dumps(output, indent=2))


def cmd_list_threads(args):
    from activities.fetch_pr_comments_activity import FetchPRCommentsActivity

    settings = _load_settings()
    activity = FetchPRCommentsActivity(settings=settings)
    threads = activity.execute(pr_id=args.pr_id, repository_id=args.repo or None)

    output = [
        {
            "thread_id": t.thread_id,
            "file_path": t.file_path,
            "line_number": t.line_number,
            "status": t.status,
            "comment_text": t.comment_text,
            "created_date": t.created_date,
            "severity": t.severity,
            "category": t.category,
            "message": t.message,
            "confidence": t.confidence,
            "cr_id": t.cr_id,
        }
        for t in threads
    ]
    print(json.dumps(output, indent=2))


def cmd_post_comment(args):
    from activities.post_pr_comment_activity import PostPRCommentActivity, PostPRCommentInput

    settings = _load_settings()
    activity = PostPRCommentActivity(settings=settings)
    input_data = PostPRCommentInput(
        pr_id=args.pr_id,
        comment_text=args.text,
        file_path=args.file or None,
        line_number=args.line or None,
        repository_id=args.repo or None,
    )
    result = activity.execute(input_data)

    output = {
        "pr_id": result.pr_id,
        "comments_posted": result.comments_posted,
        "summary_posted": result.summary_posted,
        "thread_ids": result.thread_ids,
        "errors": result.errors,
    }
    print(json.dumps(output, indent=2))


def cmd_resolve_thread(args):
    from activities.post_fix_reply_activity import PostFixReplyActivity

    settings = _load_settings()
    activity = PostFixReplyActivity(settings=settings)
    input_data = {
        "thread_id": args.thread_id,
        "pr_id": args.pr_id,
        "repository_id": args.repo or None,
        "message": args.message or None,
    }
    # Remove None values so activity uses its defaults
    input_data = {k: v for k, v in input_data.items() if v is not None}
    success = activity.execute(input_data)
    print(json.dumps({"success": success, "thread_id": args.thread_id}))


def cmd_get_file(args):
    from activities.fetch_file_content_activity import FetchFileContentActivity
    from models.review_models import FetchFileContentInput

    settings = _load_settings()
    activity = FetchFileContentActivity(settings=settings)
    input_data = FetchFileContentInput(
        file_path=args.file,
        commit_id=args.commit_id,
        repository_id=args.repo or None,
    )
    content = activity.execute(input_data)
    print(json.dumps({"file": args.file, "commit_id": args.commit_id, "content": content}))


def cmd_post_summary(args):
    from activities.update_summary_activity import UpdateSummaryActivity, UpdateSummaryInput

    settings = _load_settings()
    activity = UpdateSummaryActivity(settings=settings)

    content = args.content
    if args.content_file:
        with open(args.content_file, "r", encoding="utf-8") as fh:
            content = fh.read()

    input_data = UpdateSummaryInput(
        pr_id=args.pr_id,
        new_content=content,
        repository_id=args.repo or None,
    )
    result = activity.execute(input_data)
    print(json.dumps({
        "pr_id": result.pr_id,
        "thread_id": result.thread_id,
        "updated": result.updated,
        "created_new": result.created_new,
    }))


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vcs.py",
        description="codehawk VCS CLI — wraps ADO/GitHub activities and outputs JSON"
    )
    parser.add_argument(
        "--vcs",
        choices=["ado", "github"],
        default=None,
        help="VCS provider override (default: read from VCS env var)"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # get-pr
    p_get_pr = sub.add_parser("get-pr", help="Fetch PR details")
    p_get_pr.add_argument("--pr-id", type=int, required=True, help="Pull request ID")
    p_get_pr.add_argument("--repo", default=None, help="Repository name/ID override")

    # list-threads
    p_threads = sub.add_parser("list-threads", help="List existing PR comment threads")
    p_threads.add_argument("--pr-id", type=int, required=True, help="Pull request ID")
    p_threads.add_argument("--repo", default=None, help="Repository name/ID override")

    # post-comment
    p_comment = sub.add_parser("post-comment", help="Post an inline comment to a PR")
    p_comment.add_argument("--pr-id", type=int, required=True, help="Pull request ID")
    p_comment.add_argument("--text", required=True, help="Comment text (markdown)")
    p_comment.add_argument("--file", default=None, help="File path for inline comment")
    p_comment.add_argument("--line", type=int, default=None, help="Line number for inline comment")
    p_comment.add_argument("--severity", default="suggestion",
                           choices=["critical", "warning", "suggestion"],
                           help="Finding severity")
    p_comment.add_argument("--cr-id", default=None, help="cr-id marker for dedup")
    p_comment.add_argument("--repo", default=None, help="Repository name/ID override")

    # resolve-thread
    p_resolve = sub.add_parser("resolve-thread", help="Mark a comment thread as fixed")
    p_resolve.add_argument("--pr-id", type=int, required=True, help="Pull request ID")
    p_resolve.add_argument("--thread-id", type=int, required=True, help="Thread ID to resolve")
    p_resolve.add_argument("--repo", default=None, help="Repository name/ID override")
    p_resolve.add_argument("--message", default=None, help="Custom resolution message")

    # get-file
    p_file = sub.add_parser("get-file", help="Fetch file content at a specific commit")
    p_file.add_argument("--file", required=True, help="File path in repo")
    p_file.add_argument("--commit-id", required=True, help="Commit SHA")
    p_file.add_argument("--repo", default=None, help="Repository name/ID override")

    # post-summary
    p_summary = sub.add_parser("post-summary", help="Post or update the PR summary comment")
    p_summary.add_argument("--pr-id", type=int, required=True, help="Pull request ID")
    p_summary.add_argument("--content", default=None, help="Summary markdown content")
    p_summary.add_argument("--content-file", default=None,
                           help="Path to file containing summary markdown")
    p_summary.add_argument("--repo", default=None, help="Repository name/ID override")

    return parser


_HANDLERS = {
    "get-pr": cmd_get_pr,
    "list-threads": cmd_list_threads,
    "post-comment": cmd_post_comment,
    "resolve-thread": cmd_resolve_thread,
    "get-file": cmd_get_file,
    "post-summary": cmd_post_summary,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        handler = _HANDLERS[args.command]
        handler(args)
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
