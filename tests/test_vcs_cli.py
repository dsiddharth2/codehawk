"""
Unit tests for vcs.py CLI — subcommand parsing and activity invocation.
"""

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest


def _run_vcs(argv: list, capture_stdout=True):
    """Import and run vcs.main() with given argv, capturing stdout."""
    import vcs

    saved_argv = sys.argv
    saved_stdout = sys.stdout

    try:
        sys.argv = ["vcs.py"] + argv
        if capture_stdout:
            sys.stdout = captured = StringIO()
        vcs.main()
        if capture_stdout:
            return captured.getvalue()
    finally:
        sys.argv = saved_argv
        if capture_stdout:
            sys.stdout = saved_stdout


# ---------------------------------------------------------------------------
# --help: verify all subcommands are listed
# ---------------------------------------------------------------------------

class TestHelpOutput:
    def test_top_level_help_lists_all_subcommands(self):
        import vcs
        import argparse
        parser = vcs.build_parser()
        help_text = parser.format_help()
        for cmd in ("get-pr", "list-threads", "post-comment", "resolve-thread", "get-file", "post-summary"):
            assert cmd in help_text

    def test_get_pr_subcommand_help(self):
        import vcs
        parser = vcs.build_parser()
        sub_parser = parser._subparsers._actions[-1].choices["get-pr"]
        help_text = sub_parser.format_help()
        assert "--pr-id" in help_text

    def test_post_comment_subcommand_help(self):
        import vcs
        parser = vcs.build_parser()
        sub_parser = parser._subparsers._actions[-1].choices["post-comment"]
        help_text = sub_parser.format_help()
        assert "--text" in help_text
        assert "--file" in help_text
        assert "--line" in help_text

    def test_resolve_thread_subcommand_help(self):
        import vcs
        parser = vcs.build_parser()
        sub_parser = parser._subparsers._actions[-1].choices["resolve-thread"]
        help_text = sub_parser.format_help()
        assert "--thread-id" in help_text


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

class TestArgumentParsing:
    def test_get_pr_parses_pr_id(self):
        import vcs
        parser = vcs.build_parser()
        args = parser.parse_args(["get-pr", "--pr-id", "42"])
        assert args.pr_id == 42
        assert args.command == "get-pr"

    def test_get_pr_parses_repo_override(self):
        import vcs
        parser = vcs.build_parser()
        args = parser.parse_args(["get-pr", "--pr-id", "1", "--repo", "MyRepo"])
        assert args.repo == "MyRepo"

    def test_list_threads_parses_correctly(self):
        import vcs
        parser = vcs.build_parser()
        args = parser.parse_args(["list-threads", "--pr-id", "99"])
        assert args.pr_id == 99
        assert args.command == "list-threads"

    def test_post_comment_requires_text(self):
        import vcs
        parser = vcs.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["post-comment", "--pr-id", "1"])  # missing --text

    def test_post_comment_full_args(self):
        import vcs
        parser = vcs.build_parser()
        args = parser.parse_args([
            "post-comment", "--pr-id", "5",
            "--text", "My comment",
            "--file", "src/foo.py",
            "--line", "42",
            "--severity", "warning",
            "--cr-id", "cr-001",
        ])
        assert args.pr_id == 5
        assert args.text == "My comment"
        assert args.file == "src/foo.py"
        assert args.line == 42
        assert args.severity == "warning"
        assert args.cr_id == "cr-001"

    def test_resolve_thread_parses_correctly(self):
        import vcs
        parser = vcs.build_parser()
        args = parser.parse_args(["resolve-thread", "--pr-id", "3", "--thread-id", "77"])
        assert args.pr_id == 3
        assert args.thread_id == 77

    def test_get_file_requires_file_and_commit(self):
        import vcs
        parser = vcs.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["get-file"])  # missing required args
        args = parser.parse_args(["get-file", "--file", "foo.py", "--commit-id", "abc123"])
        assert args.file == "foo.py"
        assert args.commit_id == "abc123"

    def test_post_summary_requires_pr_id(self):
        import vcs
        parser = vcs.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["post-summary"])  # missing --pr-id


# ---------------------------------------------------------------------------
# Activity invocation via cmd_* functions
# ---------------------------------------------------------------------------

class TestActivityInvocation:
    def test_cmd_get_pr_invokes_activity(self):
        import vcs
        import argparse

        mock_result = MagicMock()
        mock_result.pr_id = 42
        mock_result.title = "Test PR"
        mock_result.description = ""
        mock_result.source_branch = "feat/x"
        mock_result.target_branch = "main"
        mock_result.author = "Alice"
        mock_result.repository = "MyRepo"
        mock_result.project = "MyProject"
        mock_result.organization = "MyOrg"
        mock_result.total_additions = 5
        mock_result.total_deletions = 2
        mock_result.source_commit_id = "abc"
        mock_result.target_commit_id = "def"
        mock_result.file_changes = []

        # Activities are locally imported inside cmd_* functions — patch the source module
        with patch("vcs._load_settings"), \
             patch("activities.fetch_pr_details_activity.FetchPRDetailsActivity") as MockActivity:
            MockActivity.return_value.execute.return_value = mock_result
            args = argparse.Namespace(pr_id=42, repo=None)
            import io
            captured = io.StringIO()
            sys.stdout, _orig = captured, sys.stdout
            try:
                vcs.cmd_get_pr(args)
            finally:
                sys.stdout = _orig

            output = json.loads(captured.getvalue())
            assert output["pr_id"] == 42
            assert output["title"] == "Test PR"

    def test_cmd_list_threads_invokes_activity(self):
        import vcs
        import argparse
        from models.review_models import ExistingCommentThread

        mock_thread = ExistingCommentThread(
            thread_id=1,
            file_path="src/foo.py",
            line_number=10,
            status=1,
            comment_text="## WARNING: Issue",
            created_date="2026-01-01T00:00:00",
            severity="warning",
            category="security",
            message="SQL injection",
            confidence=0.9,
            cr_id="cr-001",
        )

        with patch("vcs._load_settings"), \
             patch("activities.fetch_pr_comments_activity.FetchPRCommentsActivity") as MockActivity:
            MockActivity.return_value.execute.return_value = [mock_thread]
            args = argparse.Namespace(pr_id=42, repo=None)
            import io
            captured = io.StringIO()
            sys.stdout, _orig = captured, sys.stdout
            try:
                vcs.cmd_list_threads(args)
            finally:
                sys.stdout = _orig

            output = json.loads(captured.getvalue())
            assert len(output) == 1
            assert output[0]["cr_id"] == "cr-001"
            assert output[0]["file_path"] == "src/foo.py"

    def test_cmd_resolve_thread_invokes_activity(self):
        import vcs
        import argparse

        with patch("vcs._load_settings"), \
             patch("activities.post_fix_reply_activity.PostFixReplyActivity") as MockActivity:
            MockActivity.return_value.execute.return_value = True
            args = argparse.Namespace(pr_id=42, thread_id=7, repo=None, message=None)
            import io
            captured = io.StringIO()
            sys.stdout, _orig = captured, sys.stdout
            try:
                vcs.cmd_resolve_thread(args)
            finally:
                sys.stdout = _orig

            output = json.loads(captured.getvalue())
            assert output["success"] is True
            assert output["thread_id"] == 7

    def test_cmd_post_summary_invokes_activity(self):
        import vcs
        import argparse

        mock_result = MagicMock()
        mock_result.pr_id = 42
        mock_result.thread_id = 99
        mock_result.updated = False
        mock_result.created_new = True

        with patch("vcs._load_settings"), \
             patch("activities.update_summary_activity.UpdateSummaryActivity") as MockActivity:
            MockActivity.return_value.execute.return_value = mock_result
            args = argparse.Namespace(pr_id=42, content="# Summary", content_file=None, repo=None)
            import io
            captured = io.StringIO()
            sys.stdout, _orig = captured, sys.stdout
            try:
                vcs.cmd_post_summary(args)
            finally:
                sys.stdout = _orig

            output = json.loads(captured.getvalue())
            assert output["pr_id"] == 42
            assert output["created_new"] is True

    def test_activity_error_exits_with_code_1(self):
        """Error in a cmd handler propagates via main() as exit code 1."""
        import vcs

        with patch("vcs._load_settings", side_effect=ValueError("No auth configured")), \
             patch("sys.argv", ["vcs.py", "get-pr", "--pr-id", "42"]):
            import io
            captured_err = io.StringIO()
            sys.stderr, _orig_err = captured_err, sys.stderr
            try:
                with pytest.raises(SystemExit) as exc_info:
                    vcs.main()
                assert exc_info.value.code == 1
            finally:
                sys.stderr = _orig_err
