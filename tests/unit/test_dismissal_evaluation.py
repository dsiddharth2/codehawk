"""
Tests for developer dismissal evaluation in fix verification.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import fix_verifier as fv
from models.review_models import ExistingCommentThread


def _make_thread(
    thread_id: int, file_path: str, cr_id: str = "",
    line_number: int = 10, comment_text: str = "Test finding",
    severity: str = "warning", category: str = "best_practices",
) -> ExistingCommentThread:
    return ExistingCommentThread(
        thread_id=thread_id,
        file_path=file_path,
        line_number=line_number,
        status=1,
        comment_text=comment_text,
        created_date="2026-01-01T00:00:00",
        severity=severity,
        category=category,
        cr_id=cr_id,
    )


_MOCK_USAGE = {"input_tokens": 100, "output_tokens": 50}


class TestIsCodehawkComment:
    """Test _is_codehawk_comment heuristic."""

    def _activity(self):
        with patch("activities.fetch_pr_comments_activity.FetchPRCommentsActivity.__init__",
                    return_value=None):
            from activities.fetch_pr_comments_activity import FetchPRCommentsActivity
            act = FetchPRCommentsActivity.__new__(FetchPRCommentsActivity)
            return act

    def test_severity_emoji_header(self):
        act = self._activity()
        assert act._is_codehawk_comment("## 🔴 CRITICAL: SQL injection", "dev@company.com")

    def test_summary_header(self):
        act = self._activity()
        assert act._is_codehawk_comment("# 🤖 AI Code Review\nSummary...", "dev@company.com")

    def test_fix_verified(self):
        act = self._activity()
        assert act._is_codehawk_comment("**Issue Fixed** — import removed", "dev@company.com")

    def test_dismissal_accepted_reply(self):
        act = self._activity()
        assert act._is_codehawk_comment("✅ **Dismissal accepted** — valid JS", "dev@company.com")

    def test_empty_unique_name_is_codehawk(self):
        act = self._activity()
        assert act._is_codehawk_comment("anything at all", "")

    def test_plain_developer_text_is_not(self):
        act = self._activity()
        assert not act._is_codehawk_comment(
            "This is intentional, the spread on undefined is valid JS",
            "developer@company.com",
        )


class TestExtractCodeSnippet:
    def test_extracts_around_line(self):
        content = "\n".join(f"line {i}" for i in range(1, 101))
        snippet = fv._extract_code_snippet(content, 50, context=5)
        assert "  45 |" in snippet
        assert "  55 |" in snippet
        assert "  44 |" not in snippet

    def test_handles_start_of_file(self):
        content = "\n".join(f"line {i}" for i in range(1, 20))
        snippet = fv._extract_code_snippet(content, 1, context=5)
        assert "   1 |" in snippet

    def test_handles_end_of_file(self):
        content = "\n".join(f"line {i}" for i in range(1, 20))
        snippet = fv._extract_code_snippet(content, 19, context=5)
        assert "  19 |" in snippet


class TestEvaluateDismissal:
    def _patch_file(self, content="function foo() { return 42; }"):
        return patch("fix_verifier._read_file_content", return_value=content)

    def test_valid_technical_dismissal_accepted(self):
        thread = _make_thread(1, "src/app.js", cr_id="cr-001")
        llm_response = [{"accepted": True, "explanation": "Valid JS semantics",
                         "suggested_rule": "Do not flag spread on undefined"}]
        with self._patch_file():
            with patch("fix_verifier._call_llm_for_verification",
                       return_value=(llm_response, _MOCK_USAGE)):
                result, _usage = fv._evaluate_dismissal(
                    thread, "spreading undefined is valid JS",
                    "src/app.js", Path("/workspace"),
                )
        assert result.status == "dismissed"
        assert "Valid JS semantics" in result.reason
        assert "suggested_rule" not in result.reason or ".codereview.md" in result.reason

    def test_lazy_dismissal_rejected(self):
        thread = _make_thread(1, "src/app.js", cr_id="cr-001")
        llm_response = [{"accepted": False, "explanation": "No technical reason given",
                         "suggested_rule": None}]
        with self._patch_file():
            with patch("fix_verifier._call_llm_for_verification",
                       return_value=(llm_response, _MOCK_USAGE)):
                result, _usage = fv._evaluate_dismissal(
                    thread, "Invalid",
                    "src/app.js", Path("/workspace"),
                )
        assert result.status == "still_present"
        assert "rejected" in result.reason.lower()

    def test_file_not_found_defaults_to_still_present(self):
        thread = _make_thread(1, "src/deleted.js", cr_id="cr-001")
        with patch("fix_verifier._read_file_content", return_value=""):
            result, _usage = fv._evaluate_dismissal(
                thread, "This is fine",
                "src/deleted.js", Path("/workspace"),
            )
        assert result.status == "still_present"

    def test_llm_failure_defaults_to_still_present(self):
        thread = _make_thread(1, "src/app.js", cr_id="cr-001")
        with self._patch_file():
            with patch("fix_verifier._call_llm_for_verification",
                       return_value=([], _MOCK_USAGE)):
                result, _usage = fv._evaluate_dismissal(
                    thread, "This is intentional",
                    "src/app.js", Path("/workspace"),
                )
        assert result.status == "still_present"

    def test_suggested_rule_included_when_accepted(self):
        thread = _make_thread(1, "src/app.js", cr_id="cr-001")
        llm_response = [{"accepted": True, "explanation": "Valid pattern",
                         "suggested_rule": "Do not flag sequential uploads in useFilePreviewUpload"}]
        with self._patch_file():
            with patch("fix_verifier._call_llm_for_verification",
                       return_value=(llm_response, _MOCK_USAGE)):
                result, _usage = fv._evaluate_dismissal(
                    thread, "Sequential uploads are intentional",
                    "src/app.js", Path("/workspace"),
                )
        assert result.status == "dismissed"
        assert "Do not flag sequential uploads" in result.reason


class TestVerifyFixesWithDismissals:
    """Test that verify_fixes integrates dismissal evaluation for unchanged files."""

    def _patch_git_modified(self, *file_paths):
        stdout = "\n".join(f"M\t{p}" for p in file_paths)
        mock_run = MagicMock()
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        return patch("fix_verifier.subprocess.run", mock_run)

    def test_no_reply_stays_still_present(self):
        threads = [_make_thread(1, "src/utils.js", cr_id="cr-001")]
        with self._patch_git_modified("src/other.js"):
            results, _usage = fv.verify_fixes(
                old_findings=threads,
                workspace=Path("/workspace"),
                pr_id=1, repo="repo",
                old_commit="abc123",
                dry_run=True,
                developer_replies={},
            )
        assert results[0].status == "still_present"
        assert "not modified" in results[0].reason

    def test_reply_triggers_evaluation(self):
        threads = [_make_thread(1, "src/utils.js", cr_id="cr-001")]
        verify_response = ([{"finding": 1, "status": "still_present", "reason": "Issue remains"}], _MOCK_USAGE)
        dismissal_response = ([{"accepted": True, "explanation": "Valid design",
                                "suggested_rule": "Allow sequential uploads"}], _MOCK_USAGE)
        with self._patch_git_modified("src/other.js"):
            with patch("fix_verifier._read_file_content", return_value="code here"):
                with patch("fix_verifier._call_llm_for_verification",
                           side_effect=[verify_response, dismissal_response]):
                    results, _usage = fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1, repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                        developer_replies={"cr-001": "[CodeHawk] Finding\n\n[Dev] This is intentional"},
                    )
        assert results[0].status == "dismissed"

    def test_rejected_reply_stays_still_present(self):
        threads = [_make_thread(1, "src/utils.js", cr_id="cr-001")]
        verify_response = ([{"finding": 1, "status": "still_present", "reason": "Issue remains"}], _MOCK_USAGE)
        dismissal_response = ([{"accepted": False, "explanation": "No technical reason",
                                "suggested_rule": None}], _MOCK_USAGE)
        with self._patch_git_modified("src/other.js"):
            with patch("fix_verifier._read_file_content", return_value="code here"):
                with patch("fix_verifier._call_llm_for_verification",
                           side_effect=[verify_response, dismissal_response]):
                    results, _usage = fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1, repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                        developer_replies={"cr-001": "[CodeHawk] Finding\n\n[Dev] Invalid"},
                    )
        assert results[0].status == "still_present"
        assert "rejected" in results[0].reason.lower()


class TestDismissedScoring:
    """Verify that dismissed findings get 0 penalty in the scorer."""

    def test_dismissed_gets_zero_penalty(self):
        from pr_scorer import PRScorer
        from models.review_models import FixVerification

        matrix = {
            "best_practices": {"critical": 5.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
        }
        scorer = PRScorer(penalty_matrix=matrix, star_thresholds=[0.0, 5.0, 15.0, 30.0, 50.0])
        verifications = [
            FixVerification(cr_id="cr-001", status="still_present", reason="",
                          severity="warning", category="best_practices"),
            FixVerification(cr_id="cr-002", status="dismissed", reason="Valid dismissal",
                          severity="warning", category="best_practices"),
            FixVerification(cr_id="cr-003", status="fixed", reason="Fixed",
                          severity="warning", category="best_practices"),
        ]
        score = scorer.calculate_verify_score(verifications)
        # Only cr-001 (still_present) should contribute penalty
        assert score.issues_by_severity["warning"] == 1
