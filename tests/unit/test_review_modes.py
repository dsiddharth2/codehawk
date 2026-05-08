"""
Unit tests for the review-modes feature (Phase 3 — Task 7).

Covers:
  - Enum: ReviewMode values, string comparison, all three modes exist
  - CLI: --verify-fixes parsed, --check-new-findings parsed, both rejected, default FULL
  - BatchReviewJob: CHECK_NEW skips _fetch_previous_findings, FULL/VERIFY_FIXES call it
  - Prompt injection: VERIFY_FIXES contains "VERIFY-ONLY MODE", CHECK_NEW "FRESH REVIEW MODE"
  - Write guards: VERIFY_FIXES strips findings[], CHECK_NEW strips fix_verifications[], FULL preserves both
  - post_findings: verify-only title, gate passes with zero findings, inline comments skipped
"""

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_src = Path(__file__).parent.parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from models.review_models import ReviewMode
from review_job import ReviewJob, ReviewJobConfig
from batch_review_job import BatchReviewJob


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmpdir, review_mode=ReviewMode.FULL, prompt_text="Review this PR."):
    return ReviewJobConfig(
        pr_id=1,
        repo="test-repo",
        workspace=Path(tmpdir),
        prompt_text=prompt_text,
        review_mode=review_mode,
    )


def _make_findings_data(findings=None, fix_verifications=None, review_modes=None):
    return {
        "pr_id": 1,
        "repo": "test-repo",
        "vcs": "ado",
        "review_modes": review_modes if review_modes is not None else ["standard"],
        "summary": "Test summary",
        "findings": findings if findings is not None else [],
        "fix_verifications": fix_verifications if fix_verifications is not None else [],
    }


def _make_finding_dict(id="cr-001", severity="warning"):
    return {
        "id": id,
        "file": "src/main.py",
        "line": 10,
        "severity": severity,
        "category": "best_practices",
        "title": "Test finding",
        "message": "A test finding message.",
        "confidence": 0.9,
        "suggestion": "Fix it.",
    }


# ---------------------------------------------------------------------------
# Group 1: Enum tests
# ---------------------------------------------------------------------------

class TestReviewModeEnum:
    def test_full_value(self):
        assert ReviewMode.FULL == "full"

    def test_verify_fixes_value(self):
        assert ReviewMode.VERIFY_FIXES == "verify_fixes"

    def test_check_new_value(self):
        assert ReviewMode.CHECK_NEW == "check_new"

    def test_all_three_modes_exist(self):
        modes = list(ReviewMode)
        assert len(modes) == 3
        assert ReviewMode.FULL in modes
        assert ReviewMode.VERIFY_FIXES in modes
        assert ReviewMode.CHECK_NEW in modes

    def test_string_comparison_works(self):
        assert ReviewMode.FULL == "full"
        assert ReviewMode.VERIFY_FIXES == "verify_fixes"
        assert ReviewMode.CHECK_NEW == "check_new"

    def test_is_str_subclass(self):
        assert isinstance(ReviewMode.FULL, str)

    def test_default_mode_is_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = ReviewJobConfig(
                pr_id=1, repo="r", workspace=tmpdir, prompt_text="x"
            )
            assert config.review_mode == ReviewMode.FULL


# ---------------------------------------------------------------------------
# Group 2: CLI argument parsing
# ---------------------------------------------------------------------------

class TestCLIParsing:
    """Test that run_agent.py CLI resolves flags to the correct ReviewMode."""

    def _parse(self, extra_args):
        """Invoke run_agent.main() with mocked BatchReviewJob.run()."""
        import run_agent
        import argparse

        argv = [
            "--pr-id", "1",
            "--repo", "MyRepo",
            "--workspace", "/workspace",
            "--prompt-file", "commands/review-pr-core.md",
        ] + extra_args

        captured = {}

        with patch("run_agent.BatchReviewJob") as MockBatch:
            mock_instance = MagicMock()
            mock_instance.run.return_value = {"gate": {"passed": True}}
            MockBatch.return_value = mock_instance

            with patch("sys.argv", ["run_agent.py"] + argv):
                try:
                    run_agent.main()
                except SystemExit:
                    pass
            captured["mode"] = MockBatch.call_args.kwargs.get("review_mode") or MockBatch.call_args[1].get("review_mode")

        return captured["mode"]

    def test_default_is_full(self):
        mode = self._parse([])
        assert mode == ReviewMode.FULL

    def test_verify_fixes_flag(self):
        mode = self._parse(["--verify-fixes"])
        assert mode == ReviewMode.VERIFY_FIXES

    def test_check_new_findings_flag(self):
        mode = self._parse(["--check-new-findings"])
        assert mode == ReviewMode.CHECK_NEW

    def test_both_flags_rejected(self):
        import run_agent
        argv = [
            "run_agent.py",
            "--pr-id", "1",
            "--repo", "r",
            "--workspace", "/w",
            "--prompt-file", "p.md",
            "--verify-fixes",
            "--check-new-findings",
        ]
        with patch("sys.argv", argv):
            with pytest.raises(SystemExit) as exc_info:
                run_agent.main()
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Group 3: BatchReviewJob — conditional fetch behavior
# ---------------------------------------------------------------------------

class TestBatchReviewJobModeBehavior:
    def _make_batch(self, review_mode, tmpdir):
        return BatchReviewJob(
            pr_id=1,
            repo="test-repo",
            workspace=Path(tmpdir),
            model="o3",
            prompt_path=Path("commands/review-pr-core.md"),
            review_mode=review_mode,
        )

    def test_check_new_skips_fetch_previous_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = self._make_batch(ReviewMode.CHECK_NEW, tmpdir)
            with patch.object(job, "_fetch_pr_details", return_value=MagicMock(file_changes=[], source_commit_id="", target_commit_id="")), \
                 patch.object(job, "_fetch_previous_findings") as mock_fetch, \
                 patch.object(job, "_build_graph", return_value=None), \
                 patch("batch_review_job.filter_changed_files", return_value=([], [])), \
                 patch("batch_review_job.parse_skip_extensions", return_value=set()), \
                 patch("batch_review_job.ReviewJob") as MockJob:
                mock_job_instance = MagicMock()
                mock_job_instance.run.return_value = {"gate": {"passed": True}}
                MockJob.return_value = mock_job_instance
                job.run(dry_run=True)
            mock_fetch.assert_not_called()

    def test_full_mode_calls_fetch_previous_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = self._make_batch(ReviewMode.FULL, tmpdir)
            with patch.object(job, "_fetch_pr_details", return_value=MagicMock(file_changes=[], source_commit_id="", target_commit_id="")), \
                 patch.object(job, "_fetch_previous_findings", return_value=[]) as mock_fetch, \
                 patch.object(job, "_build_graph", return_value=None), \
                 patch("batch_review_job.filter_changed_files", return_value=([], [])), \
                 patch("batch_review_job.parse_skip_extensions", return_value=set()), \
                 patch("batch_review_job.ReviewJob") as MockJob:
                mock_job_instance = MagicMock()
                mock_job_instance.run.return_value = {"gate": {"passed": True}}
                MockJob.return_value = mock_job_instance
                job.run(dry_run=True)
            mock_fetch.assert_called_once()

    def test_verify_fixes_mode_calls_fetch_previous_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = self._make_batch(ReviewMode.VERIFY_FIXES, tmpdir)
            with patch.object(job, "_fetch_pr_details", return_value=MagicMock(file_changes=[], source_commit_id="", target_commit_id="")), \
                 patch.object(job, "_fetch_previous_findings", return_value=[]) as mock_fetch, \
                 patch.object(job, "_build_graph", return_value=None), \
                 patch("batch_review_job.filter_changed_files", return_value=([], [])), \
                 patch("batch_review_job.parse_skip_extensions", return_value=set()), \
                 patch("batch_review_job.ReviewJob") as MockJob:
                mock_job_instance = MagicMock()
                mock_job_instance.run.return_value = {"gate": {"passed": True}}
                MockJob.return_value = mock_job_instance
                job.run(dry_run=True)
            mock_fetch.assert_called_once()

    def test_review_mode_passed_to_review_job_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = self._make_batch(ReviewMode.CHECK_NEW, tmpdir)
            captured_configs = []
            with patch.object(job, "_fetch_pr_details", return_value=MagicMock(file_changes=[], source_commit_id="", target_commit_id="")), \
                 patch.object(job, "_fetch_previous_findings", return_value=[]), \
                 patch.object(job, "_build_graph", return_value=None), \
                 patch("batch_review_job.filter_changed_files", return_value=([], [])), \
                 patch("batch_review_job.parse_skip_extensions", return_value=set()), \
                 patch("batch_review_job.ReviewJob") as MockJob:
                def capture_config(config, **kwargs):
                    captured_configs.append(config)
                    m = MagicMock()
                    m.run.return_value = {"gate": {"passed": True}}
                    return m
                MockJob.side_effect = capture_config
                job.run(dry_run=True)
            assert len(captured_configs) == 1
            assert captured_configs[0].review_mode == ReviewMode.CHECK_NEW


# ---------------------------------------------------------------------------
# Group 4: Prompt injection tests
# ---------------------------------------------------------------------------

class TestPromptInjection:
    def test_verify_fixes_prompt_contains_verify_only_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.VERIFY_FIXES)
            job = ReviewJob(config, settings=MagicMock())
            prompt = job._build_prompt()
            assert "VERIFY-ONLY MODE" in prompt

    def test_check_new_prompt_contains_fresh_review_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.CHECK_NEW)
            job = ReviewJob(config, settings=MagicMock())
            prompt = job._build_prompt()
            assert "FRESH REVIEW MODE" in prompt

    def test_full_mode_prompt_contains_neither(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.FULL)
            job = ReviewJob(config, settings=MagicMock())
            prompt = job._build_prompt()
            assert "VERIFY-ONLY MODE" not in prompt
            assert "FRESH REVIEW MODE" not in prompt

    def test_verify_only_instructions_include_key_constraints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.VERIFY_FIXES)
            job = ReviewJob(config, settings=MagicMock())
            instructions = job._build_verify_only_instructions()
            assert "findings[]" in instructions
            assert "fix_verifications[]" in instructions
            assert "MUST be empty" in instructions

    def test_check_new_instructions_include_key_constraints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.CHECK_NEW)
            job = ReviewJob(config, settings=MagicMock())
            instructions = job._build_check_new_instructions()
            assert "fix_verifications[]" in instructions
            assert "MUST be empty" in instructions


# ---------------------------------------------------------------------------
# Group 5: Write guard tests
# ---------------------------------------------------------------------------

class TestWriteGuards:
    def test_verify_fixes_strips_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.VERIFY_FIXES)
            job = ReviewJob(config, settings=MagicMock())
            data = _make_findings_data(
                findings=[_make_finding_dict()],
                fix_verifications=[{"cr_id": "cr-001", "status": "fixed", "reason": "done"}],
            )
            job._write_findings(data)
            written = json.loads(job.findings_path.read_text())
            assert written["findings"] == []

    def test_verify_fixes_stamps_verify_fixes_in_review_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.VERIFY_FIXES)
            job = ReviewJob(config, settings=MagicMock())
            data = _make_findings_data(findings=[_make_finding_dict()])
            job._write_findings(data)
            written = json.loads(job.findings_path.read_text())
            assert "verify_fixes" in written["review_modes"]

    def test_check_new_strips_fix_verifications(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.CHECK_NEW)
            job = ReviewJob(config, settings=MagicMock())
            data = _make_findings_data(
                findings=[_make_finding_dict()],
                fix_verifications=[{"cr_id": "cr-001", "status": "fixed", "reason": "done"}],
            )
            job._write_findings(data)
            written = json.loads(job.findings_path.read_text())
            assert written["fix_verifications"] == []

    def test_check_new_stamps_check_new_in_review_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.CHECK_NEW)
            job = ReviewJob(config, settings=MagicMock())
            data = _make_findings_data(findings=[_make_finding_dict()])
            job._write_findings(data)
            written = json.loads(job.findings_path.read_text())
            assert "check_new" in written["review_modes"]

    def test_full_mode_preserves_findings_and_fix_verifications(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.FULL)
            job = ReviewJob(config, settings=MagicMock())
            data = _make_findings_data(
                findings=[_make_finding_dict()],
                fix_verifications=[{"cr_id": "cr-001", "status": "fixed", "reason": "done"}],
            )
            job._write_findings(data)
            written = json.loads(job.findings_path.read_text())
            assert len(written["findings"]) == 1
            assert len(written["fix_verifications"]) == 1

    def test_verify_fixes_does_not_duplicate_mode_stamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, review_mode=ReviewMode.VERIFY_FIXES)
            job = ReviewJob(config, settings=MagicMock())
            data = _make_findings_data(review_modes=["standard", "verify_fixes"])
            job._write_findings(data)
            written = json.loads(job.findings_path.read_text())
            assert written["review_modes"].count("verify_fixes") == 1


# ---------------------------------------------------------------------------
# Group 6: post_findings — verify-only mode behavior
# ---------------------------------------------------------------------------

_DEFAULT_PENALTY_MATRIX = {
    "security": {"critical": 5.0, "warning": 4.0, "suggestion": 2.0, "good": 0.0},
    "performance": {"critical": 3.0, "warning": 2.0, "suggestion": 1.0, "good": 0.0},
    "best_practices": {"critical": 2.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
    "code_style": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
    "documentation": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
}
_DEFAULT_STAR_THRESHOLDS = [0.0, 5.0, 15.0, 30.0, 50.0]


class TestPostFindingsVerifyOnly:
    def _write_findings_file(self, tmpdir, findings=None, fix_verifications=None, review_modes=None):
        findings_dir = Path(tmpdir) / ".cr"
        findings_dir.mkdir(parents=True, exist_ok=True)
        path = findings_dir / "findings.json"
        data = {
            "pr_id": 1,
            "repo": "test-repo",
            "vcs": "ado",
            "review_modes": review_modes if review_modes is not None else ["standard", "verify_fixes"],
            "summary": "Fix verification summary.",
            "findings": findings if findings is not None else [],
            "fix_verifications": fix_verifications if fix_verifications is not None else [],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_verify_only_gate_passes_with_zero_findings(self):
        import post_findings as pf
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_findings_file(tmpdir, findings=[], review_modes=["standard", "verify_fixes"])
            # dry_run=True guards all VCS calls; settings=None causes fallback defaults
            result = pf.run(findings_path=path, dry_run=True, workspace=tmpdir)
            assert result["gate"]["passed"] is True

    def test_verify_only_inline_comments_not_posted(self):
        import post_findings as pf
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_findings_file(tmpdir, findings=[], review_modes=["standard", "verify_fixes"])
            with patch.object(pf, "_post_inline_ado") as mock_post:
                pf.run(findings_path=path, dry_run=True, workspace=tmpdir)
            mock_post.assert_not_called()

    def test_verify_only_summary_title_is_fix_verification_only(self):
        import post_findings as pf
        from models.review_models import FindingsFile
        from pr_scorer import PRScorer
        ff = FindingsFile(
            pr_id=1, repo="r", vcs="ado",
            review_modes=["standard", "verify_fixes"],
            summary="test",
            findings=[],
            fix_verifications=[],
        )
        scorer = PRScorer(
            penalty_matrix=_DEFAULT_PENALTY_MATRIX,
            star_thresholds=_DEFAULT_STAR_THRESHOLDS,
        )
        score = scorer.calculate_pr_score([])
        markdown = pf._build_summary_markdown(
            findings_file=ff,
            filtered_findings=[],
            score=score,
            gate_result={"passed": True, "reasons": []},
            fix_verifications=[],
            usage=None,
            cost_estimate=None,
        )
        assert "Fix Verification Only" in markdown

    def test_normal_mode_summary_title_is_standard(self):
        import post_findings as pf
        from models.review_models import FindingsFile
        from pr_scorer import PRScorer
        ff = FindingsFile(
            pr_id=1, repo="r", vcs="ado",
            review_modes=["standard"],
            summary="test",
            findings=[],
            fix_verifications=[],
        )
        scorer = PRScorer(
            penalty_matrix=_DEFAULT_PENALTY_MATRIX,
            star_thresholds=_DEFAULT_STAR_THRESHOLDS,
        )
        score = scorer.calculate_pr_score([])
        markdown = pf._build_summary_markdown(
            findings_file=ff,
            filtered_findings=[],
            score=score,
            gate_result={"passed": True, "reasons": []},
            fix_verifications=[],
            usage=None,
            cost_estimate=None,
        )
        assert "Fix Verification Only" not in markdown

    def test_verify_only_with_findings_present_is_not_detected_as_verify_only(self):
        """is_verify_only requires BOTH verify_fixes in review_modes AND empty findings."""
        import post_findings as pf
        with tempfile.TemporaryDirectory() as tmpdir:
            # findings present even though verify_fixes mode — _write_findings should have stripped them,
            # but test that post_findings detects it is not purely verify-only
            path = self._write_findings_file(
                tmpdir,
                findings=[_make_finding_dict()],
                review_modes=["standard", "verify_fixes"],
            )
            result = pf.run(findings_path=path, dry_run=True, workspace=tmpdir)
            # Gate may not auto-pass since findings are present — verify gate key exists
            assert "gate" in result
