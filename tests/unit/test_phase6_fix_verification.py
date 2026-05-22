"""
Unit tests for Phase 6: Fix Verification + Parallelism.

Tests:
- fix_verifier: deleted file → all findings not_relevant (no LLM call)
- fix_verifier: unchanged file → all findings still_present (no LLM call)
- fix_verifier: modified file, 3 findings → 1 LLM call with all 3 findings
- fix_verifier: 5 modified files → 5 LLM calls (one per file)
- fix_verifier: 20 modified files → 15 individual + 5 remaining (overflow)
- fix_verifier: LLM call fails → defaults to still_present
- fix_verifier: LLM returns garbage JSON → defaults to still_present
- fix_verifier: critical finding fixed + graph available → blast radius annotated
- batch parallelism: 3 batches complete via ThreadPoolExecutor
- batch parallelism: rate-limit error triggers exponential backoff retry
- review-pr-core.md Step 6: no longer contains fix verification instructions
"""

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pytest

_src = Path(__file__).parent.parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import fix_verifier as fv
from batch_review_job import BatchReviewJob
from models.review_models import ExistingCommentThread, FixVerification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_thread(
    thread_id: int,
    file_path: str,
    line_number: int = 10,
    severity: str = "warning",
    category: str = "best_practices",
    cr_id: str = "",
    comment_text: str = "Null check missing",
) -> ExistingCommentThread:
    return ExistingCommentThread(
        thread_id=thread_id,
        file_path=file_path,
        line_number=line_number,
        status=1,
        comment_text=comment_text,
        created_date="2024-01-01",
        severity=severity,
        category=category,
        cr_id=cr_id or f"cr-{thread_id:03d}",
    )


def _git_diff_output(*lines: str) -> str:
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# _get_file_statuses
# ---------------------------------------------------------------------------

class TestGetFileStatuses:
    def test_deleted_file_detected(self):
        output = _git_diff_output("D\tsrc/auth/Login.cs")
        with patch("fix_verifier.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            deleted, renamed, modified = fv._get_file_statuses(
                Path("/workspace"), "abc123", "HEAD"
            )
        assert "src/auth/Login.cs" in deleted
        assert "src/auth/Login.cs" not in modified

    def test_modified_file_detected(self):
        output = _git_diff_output("M\tsrc/services/UserService.cs")
        with patch("fix_verifier.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            deleted, renamed, modified = fv._get_file_statuses(
                Path("/workspace"), "abc123", "HEAD"
            )
        assert "src/services/UserService.cs" in modified
        assert "src/services/UserService.cs" not in deleted

    def test_renamed_file_detected(self):
        output = _git_diff_output("R100\told/path/File.cs\tnew/path/File.cs")
        with patch("fix_verifier.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            deleted, renamed, modified = fv._get_file_statuses(
                Path("/workspace"), "abc123", "HEAD"
            )
        assert renamed.get("old/path/File.cs") == "new/path/File.cs"
        assert "new/path/File.cs" in modified

    def test_empty_old_commit_returns_empty_sets(self):
        deleted, renamed, modified = fv._get_file_statuses(Path("/workspace"), "", "HEAD")
        assert not deleted
        assert not renamed
        assert not modified

    def test_git_failure_returns_empty_sets(self):
        with patch("fix_verifier.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fatal error")
            deleted, renamed, modified = fv._get_file_statuses(
                Path("/workspace"), "abc123", "HEAD"
            )
        assert not deleted
        assert not modified


# ---------------------------------------------------------------------------
# verify_fixes — deterministic paths
# ---------------------------------------------------------------------------

class TestVerifyFixesDeterministic:
    def _patch_git(self, stdout: str):
        mock_run = MagicMock()
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        return patch("fix_verifier.subprocess.run", mock_run)

    def test_deleted_file_all_findings_not_relevant(self):
        threads = [
            _make_thread(1, "src/auth/Login.cs", cr_id="cr-001"),
            _make_thread(2, "src/auth/Login.cs", cr_id="cr-002"),
        ]
        with self._patch_git("D\tsrc/auth/Login.cs"):
            results = fv.verify_fixes(
                old_findings=threads,
                workspace=Path("/workspace"),
                pr_id=1,
                repo="repo",
                old_commit="abc123",
                dry_run=True,
            )
        assert len(results) == 2
        assert all(r.status == "not_relevant" for r in results)

    def test_unchanged_file_all_findings_still_present_no_llm(self):
        threads = [
            _make_thread(1, "src/utils/Helper.cs", cr_id="cr-001"),
            _make_thread(2, "src/utils/Helper.cs", cr_id="cr-002"),
        ]
        # git diff returns only a different file — Helper.cs not in diff
        with self._patch_git("M\tsrc/other/File.cs"):
            with patch("fix_verifier._call_llm_for_verification") as mock_llm:
                results = fv.verify_fixes(
                    old_findings=threads,
                    workspace=Path("/workspace"),
                    pr_id=1,
                    repo="repo",
                    old_commit="abc123",
                    dry_run=True,
                )
        assert len(results) == 2
        assert all(r.status == "still_present" for r in results)
        mock_llm.assert_not_called()

    def test_empty_old_findings_returns_empty(self):
        results = fv.verify_fixes(
            old_findings=[],
            workspace=Path("/workspace"),
            pr_id=1,
            repo="repo",
            dry_run=True,
        )
        assert results == []


# ---------------------------------------------------------------------------
# verify_fixes — LLM path
# ---------------------------------------------------------------------------

class TestVerifyFixesLLM:
    def _patch_git_modified(self, *file_paths: str):
        stdout = "\n".join(f"M\t{p}" for p in file_paths)
        mock_run = MagicMock()
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        return patch("fix_verifier.subprocess.run", mock_run)

    def test_modified_file_3_findings_one_llm_call(self):
        threads = [
            _make_thread(1, "src/api/Controller.cs", cr_id="cr-001"),
            _make_thread(2, "src/api/Controller.cs", cr_id="cr-002"),
            _make_thread(3, "src/api/Controller.cs", cr_id="cr-003"),
        ]
        llm_response = [
            {"finding": 1, "status": "fixed", "reason": "Fixed."},
            {"finding": 2, "status": "still_present", "reason": "Still there."},
            {"finding": 3, "status": "fixed", "reason": "Also fixed."},
        ]
        with self._patch_git_modified("src/api/Controller.cs"):
            with patch("fix_verifier._read_file_content", return_value="class Ctrl {}"):
                with patch("fix_verifier._call_llm_for_verification", return_value=llm_response) as mock_llm:
                    results = fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1,
                        repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                    )
        assert mock_llm.call_count == 1
        assert len(results) == 3
        statuses = {r.cr_id: r.status for r in results}
        assert statuses["cr-001"] == "fixed"
        assert statuses["cr-002"] == "still_present"
        assert statuses["cr-003"] == "fixed"

    def test_5_modified_files_5_llm_calls(self):
        file_paths = [f"src/file{i}.cs" for i in range(5)]
        threads = [_make_thread(i + 1, fp, cr_id=f"cr-{i+1:03d}") for i, fp in enumerate(file_paths)]
        with self._patch_git_modified(*file_paths):
            with patch("fix_verifier._read_file_content", return_value="class X {}"):
                with patch("fix_verifier._call_llm_for_verification",
                           return_value=[{"finding": 1, "status": "fixed", "reason": "ok"}]) as mock_llm:
                    fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1,
                        repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                    )
        assert mock_llm.call_count == 5

    def test_20_modified_files_respects_cap(self):
        """First 15 files get individual calls; remaining 5 also get individual calls (overflow)."""
        file_paths = [f"src/file{i}.cs" for i in range(20)]
        threads = [_make_thread(i + 1, fp, cr_id=f"cr-{i+1:03d}") for i, fp in enumerate(file_paths)]
        with self._patch_git_modified(*file_paths):
            with patch("fix_verifier._read_file_content", return_value="class X {}"):
                with patch("fix_verifier._call_llm_for_verification",
                           return_value=[{"finding": 1, "status": "fixed", "reason": "ok"}]) as mock_llm:
                    fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1,
                        repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                    )
        # 15 individual + 5 overflow (1 per file) = 20 calls total
        assert mock_llm.call_count == 20

    def test_llm_failure_defaults_to_still_present(self):
        threads = [_make_thread(1, "src/api/Login.cs", cr_id="cr-001")]
        with self._patch_git_modified("src/api/Login.cs"):
            with patch("fix_verifier._read_file_content", return_value="class X {}"):
                with patch("fix_verifier._call_llm_for_verification", return_value=[]):
                    results = fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1,
                        repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                    )
        assert results[0].status == "still_present"

    def test_llm_garbage_json_defaults_to_still_present(self):
        threads = [_make_thread(1, "src/api/Login.cs", cr_id="cr-001")]
        with self._patch_git_modified("src/api/Login.cs"):
            with patch("fix_verifier._read_file_content", return_value="class X {}"):
                with patch("fix_verifier._call_llm_for_verification", return_value=None):
                    results = fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1,
                        repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                    )
        # _call_llm_for_verification returning None is handled by _map_llm_results
        assert results[0].status == "still_present"

    def test_unreadable_file_defaults_to_still_present(self):
        threads = [_make_thread(1, "src/api/Login.cs", cr_id="cr-001")]
        with self._patch_git_modified("src/api/Login.cs"):
            with patch("fix_verifier._read_file_content", return_value=""):
                with patch("fix_verifier._call_llm_for_verification") as mock_llm:
                    results = fv.verify_fixes(
                        old_findings=threads,
                        workspace=Path("/workspace"),
                        pr_id=1,
                        repo="repo",
                        old_commit="abc123",
                        dry_run=True,
                    )
        mock_llm.assert_not_called()
        assert results[0].status == "still_present"


# ---------------------------------------------------------------------------
# _map_llm_results_to_verifications
# ---------------------------------------------------------------------------

class TestMapLLMResults:
    def _make_threads(self, n: int):
        return [_make_thread(i + 1, "file.cs", cr_id=f"cr-{i+1:03d}") for i in range(n)]

    def test_maps_fixed_status(self):
        threads = self._make_threads(1)
        llm = [{"finding": 1, "status": "fixed", "reason": "Patched."}]
        results = fv._map_llm_results_to_verifications(threads, llm)
        assert results[0].status == "fixed"
        assert results[0].reason == "Patched."

    def test_maps_still_present_status(self):
        threads = self._make_threads(1)
        llm = [{"finding": 1, "status": "still_present", "reason": "Still there."}]
        results = fv._map_llm_results_to_verifications(threads, llm)
        assert results[0].status == "still_present"

    def test_missing_llm_result_defaults_to_still_present(self):
        threads = self._make_threads(2)
        llm = [{"finding": 1, "status": "fixed", "reason": "ok"}]  # finding 2 missing
        results = fv._map_llm_results_to_verifications(threads, llm)
        assert results[1].status == "still_present"

    def test_unknown_status_defaults_to_still_present(self):
        threads = self._make_threads(1)
        llm = [{"finding": 1, "status": "unknown_status", "reason": "?"}]
        results = fv._map_llm_results_to_verifications(threads, llm)
        assert results[0].status == "still_present"

    def test_empty_llm_results_all_still_present(self):
        threads = self._make_threads(3)
        results = fv._map_llm_results_to_verifications(threads, [])
        assert all(r.status == "still_present" for r in results)


# ---------------------------------------------------------------------------
# Blast radius annotation
# ---------------------------------------------------------------------------

class TestBlastRadiusAnnotation:
    def test_blast_radius_annotated_for_fixed_critical_finding(self):
        finding = _make_thread(1, "src/auth/Login.cs", severity="critical", cr_id="cr-001")
        result = FixVerification(cr_id="cr-001", status="fixed", reason="Fixed.")

        graph_store = MagicMock()
        graph_store.get_dependents.return_value = ["src/api/AuthController.cs"]

        fv._annotate_blast_radius([result], [finding], graph_store)

        assert "dependent" in result.reason.lower() or "caller" in result.reason.lower()

    def test_blast_radius_not_annotated_for_still_present(self):
        finding = _make_thread(1, "src/auth/Login.cs", severity="critical", cr_id="cr-001")
        result = FixVerification(cr_id="cr-001", status="still_present", reason="Still there.")

        graph_store = MagicMock()

        fv._annotate_blast_radius([result], [finding], graph_store)

        graph_store.get_dependents.assert_not_called()

    def test_blast_radius_skipped_if_graph_returns_empty(self):
        finding = _make_thread(1, "src/auth/Login.cs", severity="critical", cr_id="cr-001")
        original_reason = "Fixed."
        result = FixVerification(cr_id="cr-001", status="fixed", reason=original_reason)

        graph_store = MagicMock()
        graph_store.get_dependents.return_value = []

        fv._annotate_blast_radius([result], [finding], graph_store)

        assert result.reason == original_reason


# ---------------------------------------------------------------------------
# Parallel batch execution
# ---------------------------------------------------------------------------

class TestParallelBatchExecution:
    @dataclass
    class FakeFileChange:
        path: str
        change_type: str = "edit"
        additions: int = 0
        deletions: int = 0

    def _make_job(self) -> BatchReviewJob:
        settings = MagicMock()
        settings.batch_size = 5
        settings.batch_max_turns = 40
        settings.skip_extensions = ""
        job = BatchReviewJob.__new__(BatchReviewJob)
        job.pr_id = 1
        job.repo = "repo"
        job.workspace = Path("/workspace")
        job.model = "o3"
        job.prompt_path = None
        job.vcs = "ado"
        job.settings = settings
        job.review_mode = MagicMock()
        return job

    def test_parallel_batches_complete_faster_than_sequential(self):
        """3 batches with 0.1s artificial delay each: parallel ~0.1s, sequential ~0.3s."""
        job = self._make_job()
        call_times = []

        def slow_batch(**kwargs):
            call_times.append(time.time())
            time.sleep(0.1)
            return {"findings": []}

        job._run_batch = slow_batch

        batches = [[self.FakeFileChange(f"f{i}.py")] for i in range(3)]

        start = time.time()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(
                    job._run_batch_with_retry,
                    batch_files=b,
                    batch_index=i + 1,
                    batch_total=3,
                    graph_store=None,
                ): i
                for i, b in enumerate(batches)
            }
            results = [future.result() for future in as_completed(futures)]
        elapsed = time.time() - start

        assert len(results) == 3
        # Parallel should be ~0.1s, not 3×0.1s=0.3s
        assert elapsed < 0.25, f"Parallel execution took {elapsed:.2f}s, expected < 0.25s"

    def test_rate_limit_error_retries_with_backoff(self):
        job = self._make_job()
        attempt_times = []

        def flaky_batch(**kwargs):
            attempt_times.append(time.time())
            if len(attempt_times) < 2:
                raise Exception("rate limit exceeded — 429")
            return {"findings": []}

        job._run_batch = flaky_batch

        with patch("batch_review_job.time.sleep") as mock_sleep:
            result = job._run_batch_with_retry(
                batch_files=[self.FakeFileChange("f.py")],
                batch_index=1,
                batch_total=1,
                graph_store=None,
            )

        assert result == {"findings": []}
        assert len(attempt_times) == 2
        mock_sleep.assert_called_once_with(1)  # first retry delay is 1s

    def test_non_rate_limit_error_not_retried(self):
        job = self._make_job()
        call_count = [0]

        def failing_batch(**kwargs):
            call_count[0] += 1
            raise ValueError("unexpected KeyError in batch processing")

        job._run_batch = failing_batch

        with pytest.raises(ValueError, match="unexpected KeyError"):
            job._run_batch_with_retry(
                batch_files=[self.FakeFileChange("f.py")],
                batch_index=1,
                batch_total=1,
                graph_store=None,
            )

        assert call_count[0] == 1  # no retry for non-rate-limit errors

    def test_exhausted_retries_raises_last_exception(self):
        job = self._make_job()
        call_count = [0]

        def always_rate_limited(**kwargs):
            call_count[0] += 1
            raise Exception("429 rate limit")

        job._run_batch = always_rate_limited

        with patch("batch_review_job.time.sleep"):
            with pytest.raises(Exception, match="rate limit"):
                job._run_batch_with_retry(
                    batch_files=[self.FakeFileChange("f.py")],
                    batch_index=1,
                    batch_total=1,
                    graph_store=None,
                )

        assert call_count[0] == 3  # initial + 2 retries (3 attempts total for 3 delays)


# ---------------------------------------------------------------------------
# Import to allow ThreadPoolExecutor in test above
# ---------------------------------------------------------------------------

from concurrent.futures import ThreadPoolExecutor, as_completed


# ---------------------------------------------------------------------------
# review-pr-core.md Step 6 instruction check
# ---------------------------------------------------------------------------

class TestReviewPrCoreStep6:
    def _load_review_pr_core(self) -> str:
        path = Path(__file__).parent.parent.parent / "commands" / "review-pr-core.md"
        return path.read_text(encoding="utf-8")

    def test_step6_no_longer_has_fix_verification_instructions(self):
        content = self._load_review_pr_core()
        # These step labels were removed
        assert "### 6a" not in content
        assert "### 6b" not in content
        assert "### 6c" not in content
        assert "### 6d" not in content

    def test_step6_says_handled_automatically(self):
        content = self._load_review_pr_core()
        assert "handled automatically" in content.lower()

    def test_step6_no_fix_verifications_array_instruction(self):
        content = self._load_review_pr_core()
        # The old "Write fix_verifications[]" instruction should be gone
        assert "Write fix_verifications" not in content
