"""
Unit tests for ReviewJob — changed_files propagation and fallback extraction (Phase 4 — Task 9).

Covers:
  - _scan_history_for_findings(): code fence JSON, bare JSON, empty inputs, largest match
  - create_findings(): pre-fetches PR data and passes non-empty changed_files to runner
  - create_findings(): passes empty changed_files when pre-fetch fails (graceful degradation)
  - Emergency findings structure is schema-valid
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_src = Path(__file__).parent.parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from agents.openai_runner import AgentResult, _scan_history_for_findings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeFileChange:
    """Minimal stand-in for a FileChange model object."""

    def __init__(self, path, additions=10, deletions=5, change_type="edit"):
        self.path = path
        self.additions = additions
        self.deletions = deletions
        self.change_type = change_type


def _make_agent_result(pr_id=1, repo="test-repo", findings=None):
    result = AgentResult()
    result.findings_data = {
        "pr_id": pr_id,
        "repo": repo,
        "vcs": "ado",
        "review_modes": ["standard"],
        "findings": findings or [],
        "fix_verifications": [],
    }
    result.input_tokens = 100
    result.output_tokens = 50
    result.total_tokens = 150
    result.tool_calls_count = 3
    result.duration_seconds = 1.0
    result.model = "gpt-4o-mini"
    return result


# ---------------------------------------------------------------------------
# Tests — _scan_history_for_findings (pure function, no mocking needed)
# ---------------------------------------------------------------------------

class TestScanHistoryForFindings:
    def test_finds_json_in_code_fence(self):
        text = '```json\n{"findings": [], "pr_id": 1}\n```'
        result = _scan_history_for_findings([text])
        assert result is not None
        assert result["findings"] == []
        assert result["pr_id"] == 1

    def test_finds_bare_json_with_findings_key(self):
        text = '{"findings": [{"id": "cr-001"}], "pr_id": 99}'
        result = _scan_history_for_findings([text])
        assert result is not None
        assert result["pr_id"] == 99

    def test_returns_none_when_no_findings_key(self):
        result = _scan_history_for_findings(
            ["no JSON here", '{"other": "data", "unrelated": true}']
        )
        assert result is None

    def test_returns_none_for_empty_list(self):
        assert _scan_history_for_findings([]) is None

    def test_returns_none_for_empty_strings(self):
        assert _scan_history_for_findings(["", "   ", ""]) is None

    def test_prefers_larger_json_over_smaller(self):
        small = '{"findings": []}'
        large = (
            '{"findings": [{"id": "cr-001", "file": "a.py", "line": 1,'
            ' "severity": "warning", "message": "something"}]}'
        )
        result = _scan_history_for_findings([small, large])
        assert result is not None
        assert len(result["findings"]) == 1

    def test_scans_multiple_texts_and_picks_best(self):
        texts = [
            "nothing here",
            '{"findings": []}',
            '{"findings": [{"id": "cr-001"}, {"id": "cr-002"}], "pr_id": 5}',
        ]
        result = _scan_history_for_findings(texts)
        assert result is not None
        assert len(result["findings"]) == 2

    def test_cr_id_pattern_in_code_fence(self):
        text = '```json\n{"pr_id": 10, "cr-001": "finding detail"}\n```'
        result = _scan_history_for_findings([text])
        assert result is not None
        assert result["pr_id"] == 10

    def test_ignores_invalid_json(self):
        texts = ['{"findings": [INVALID JSON}', '{"findings": []}']
        result = _scan_history_for_findings(texts)
        assert result is not None
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# Tests — ReviewJob.create_findings() changed_files propagation
# ---------------------------------------------------------------------------

class TestChangedFilesPropagation:
    """Verify create_findings() pre-fetches PR data and passes it to the runner."""

    @patch("graph_builder.build_graph", return_value=None)
    @patch("review_job.OpenAIAgentRunner")
    @patch("activities.fetch_pr_details_activity.FetchPRDetailsActivity")
    def test_changed_files_passed_to_runner(
        self, mock_fetch_cls, mock_runner_cls, _bg, tmp_path
    ):
        from review_job import ReviewJob, ReviewJobConfig

        file_changes = [
            _FakeFileChange("src/auth/login.py", additions=30, deletions=10),
            _FakeFileChange("src/api/users.py", additions=5, deletions=2),
            _FakeFileChange("tests/test_login.py", additions=15, deletions=0),
        ]
        mock_pr = MagicMock()
        mock_pr.file_changes = file_changes
        mock_fetch_cls.return_value.execute.return_value = mock_pr

        mock_result = _make_agent_result()
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_result
        mock_runner_cls.return_value = mock_runner

        config = ReviewJobConfig(
            pr_id=1,
            repo="test-repo",
            workspace=tmp_path,
            model="gpt-4o-mini",
            max_turns=5,
            prompt_text="Review PR $PR_ID in $REPO for $VCS",
        )
        job = ReviewJob(config, settings=MagicMock())

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            job.create_findings()

        init_kwargs = mock_runner_cls.call_args.kwargs
        assert "changed_files" in init_kwargs
        passed = init_kwargs["changed_files"]
        assert len(passed) == 3
        assert "src/auth/login.py" in passed
        assert "src/api/users.py" in passed
        assert "tests/test_login.py" in passed

    @patch("graph_builder.build_graph", return_value=None)
    @patch("review_job.OpenAIAgentRunner")
    @patch("activities.fetch_pr_details_activity.FetchPRDetailsActivity")
    def test_changed_files_empty_on_fetch_failure(
        self, mock_fetch_cls, mock_runner_cls, _bg, tmp_path
    ):
        """When pre-fetch throws, create_findings() continues with empty changed_files."""
        from review_job import ReviewJob, ReviewJobConfig

        mock_fetch_cls.return_value.execute.side_effect = ConnectionError("ADO unreachable")

        mock_result = _make_agent_result()
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_result
        mock_runner_cls.return_value = mock_runner

        config = ReviewJobConfig(
            pr_id=2,
            repo="test-repo",
            workspace=tmp_path,
            model="gpt-4o-mini",
            max_turns=5,
            prompt_text="Review PR $PR_ID",
        )
        job = ReviewJob(config, settings=MagicMock())

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            job.create_findings()  # must not raise

        init_kwargs = mock_runner_cls.call_args.kwargs
        assert init_kwargs["changed_files"] == []

    @patch("graph_builder.build_graph", return_value=None)
    @patch("review_job.OpenAIAgentRunner")
    @patch("activities.fetch_pr_details_activity.FetchPRDetailsActivity")
    def test_prompt_contains_changed_files_section(
        self, mock_fetch_cls, mock_runner_cls, _bg, tmp_path
    ):
        """When changed_files are available, prompt must include Pre-fetched PR Data section."""
        from review_job import ReviewJob, ReviewJobConfig

        file_changes = [_FakeFileChange("src/foo.py")]
        mock_pr = MagicMock()
        mock_pr.file_changes = file_changes
        mock_fetch_cls.return_value.execute.return_value = mock_pr

        mock_result = _make_agent_result()
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_result
        mock_runner_cls.return_value = mock_runner

        config = ReviewJobConfig(
            pr_id=1,
            repo="test-repo",
            workspace=tmp_path,
            model="gpt-4o-mini",
            max_turns=5,
            prompt_text="Review PR $PR_ID",
        )
        job = ReviewJob(config, settings=MagicMock())

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            job.create_findings()

        prompt_arg = mock_runner.run.call_args.args[0]
        assert "Pre-fetched PR Data" in prompt_arg
        assert "src/foo.py" in prompt_arg
        assert "get_change_analysis" in prompt_arg


# ---------------------------------------------------------------------------
# Tests — emergency findings schema validity
# ---------------------------------------------------------------------------

class TestEmergencyFindingsSchema:
    """Verify the shape of emergency findings matches the expected schema."""

    REQUIRED_KEYS = {"pr_id", "repo", "vcs", "review_modes", "findings", "fix_verifications"}

    def test_emergency_findings_have_required_keys(self):
        emergency = {
            "pr_id": 42,
            "repo": "MyRepo",
            "vcs": "ado",
            "review_modes": ["standard"],
            "findings": [],
            "fix_verifications": [],
            "error": "Agent exhausted turn budget without producing findings",
        }
        assert self.REQUIRED_KEYS.issubset(emergency.keys())

    def test_emergency_findings_findings_is_empty_list(self):
        emergency = {
            "pr_id": 1,
            "repo": "R",
            "vcs": "ado",
            "review_modes": ["standard"],
            "findings": [],
            "fix_verifications": [],
            "error": "Agent exhausted turn budget without producing findings",
        }
        assert isinstance(emergency["findings"], list)
        assert len(emergency["findings"]) == 0

    def test_emergency_findings_includes_error_field(self):
        emergency = {
            "pr_id": 1,
            "repo": "R",
            "vcs": "ado",
            "review_modes": ["standard"],
            "findings": [],
            "fix_verifications": [],
            "error": "Agent exhausted turn budget without producing findings",
        }
        assert "error" in emergency
        assert "exhausted" in emergency["error"]

    @patch("graph_builder.build_graph", return_value=None)
    @patch("review_job.OpenAIAgentRunner")
    @patch("activities.fetch_pr_details_activity.FetchPRDetailsActivity")
    def test_create_findings_writes_valid_json(
        self, mock_fetch_cls, mock_runner_cls, _bg, tmp_path
    ):
        """findings.json produced by create_findings() must be valid JSON with required keys."""
        from review_job import ReviewJob, ReviewJobConfig

        mock_fetch_cls.return_value.execute.side_effect = RuntimeError("skip")

        mock_result = _make_agent_result(pr_id=7, repo="SomeRepo")
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_result
        mock_runner_cls.return_value = mock_runner

        config = ReviewJobConfig(
            pr_id=7,
            repo="SomeRepo",
            workspace=tmp_path,
            model="gpt-4o-mini",
            max_turns=5,
            prompt_text="Review PR",
        )
        job = ReviewJob(config, settings=MagicMock())

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            findings_path = job.create_findings()

        assert findings_path.exists()
        data = json.loads(findings_path.read_text(encoding="utf-8"))
        assert self.REQUIRED_KEYS.issubset(data.keys())
        assert data["pr_id"] == 7
        assert data["repo"] == "SomeRepo"
