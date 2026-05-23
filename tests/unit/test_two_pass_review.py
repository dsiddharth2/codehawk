"""Unit tests for the two-pass review architecture."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_src = Path(__file__).parent.parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


class TestScanCandidateModel:
    def test_create_candidate_all_fields(self):
        from models.review_models import ScanCandidate
        c = ScanCandidate(
            file="src/auth/Login.cs",
            line=42,
            category="security",
            severity="critical",
            title="SQL injection in login query",
            message="Raw string interpolation used in SQL query",
            needs_verification=True,
            verification_hint="Check if parameterized query used elsewhere",
            checklist_source="standard/security",
        )
        assert c.file == "src/auth/Login.cs"
        assert c.needs_verification is True
        assert c.verification_hint == "Check if parameterized query used elsewhere"

    def test_candidate_defaults(self):
        from models.review_models import ScanCandidate
        c = ScanCandidate(
            file="src/utils/Helper.cs",
            line=1,
            category="testing",
            severity="suggestion",
            title="Missing tests",
            message="No test coverage",
        )
        assert c.needs_verification is False
        assert c.verification_hint is None
        assert c.checklist_source is None


class TestTwoPassConfig:
    def test_default_config_values(self):
        from config import Settings
        s = Settings(
            openai_api_key="test-key",
            azure_devops_org="org",
            azure_devops_project="proj",
            azure_devops_pat="pat",
        )
        assert s.two_pass_enabled is True
        assert s.scan_pass_max_retries == 1
        assert s.verify_pass_max_turns == 10

    def test_config_override_from_env(self):
        from config import Settings
        s = Settings(
            openai_api_key="test-key",
            azure_devops_org="org",
            azure_devops_project="proj",
            azure_devops_pat="pat",
            two_pass_enabled=False,
            verify_pass_max_turns=15,
        )
        assert s.two_pass_enabled is False
        assert s.verify_pass_max_turns == 15


from agents.openai_runner import OpenAIAgentRunner, AgentResult


class TestRunSingleTurn:
    """Tests for the single-turn (no tools) API call used in Pass 1."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.OpenAI")
    def test_single_turn_chat_completions(self, mock_openai_cls, _vcs, _ws):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"candidates": [{"file": "a.cs", "line": 1}]}'
        mock_response.usage.prompt_tokens = 5000
        mock_response.usage.completion_tokens = 500
        mock_response.usage.total_tokens = 5500
        mock_client.chat.completions.create.return_value = mock_response

        runner = OpenAIAgentRunner(
            settings=MagicMock(),
            workspace=Path("/tmp/test"),
            model="o3",
        )
        result = runner.run_single_turn(
            system_prompt="You are a scanner.",
            user_prompt="Review these diffs.",
        )

        assert result.raw_final_message == '{"candidates": [{"file": "a.cs", "line": 1}]}'
        assert result.input_tokens == 5000
        assert result.output_tokens == 500
        assert result.turns == 1
        assert result.tool_calls_count == 0

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "tools" not in call_kwargs
        assert call_kwargs["temperature"] == 0.3

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.OpenAI")
    def test_single_turn_responses_api(self, mock_openai_cls, _vcs, _ws):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_msg_content = MagicMock()
        mock_msg_content.text = '{"candidates": []}'
        mock_msg_item = MagicMock()
        mock_msg_item.type = "message"
        mock_msg_item.content = [mock_msg_content]
        mock_response = MagicMock()
        mock_response.output = [mock_msg_item]
        mock_response.usage.input_tokens = 4000
        mock_response.usage.output_tokens = 300
        mock_client.responses.create.return_value = mock_response

        runner = OpenAIAgentRunner(
            settings=MagicMock(),
            workspace=Path("/tmp/test"),
            model="codex-mini-latest",
        )
        result = runner.run_single_turn(
            system_prompt="You are a scanner.",
            user_prompt="Review these diffs.",
        )

        assert result.raw_final_message == '{"candidates": []}'
        assert result.input_tokens == 4000
        assert result.turns == 1

        call_kwargs = mock_client.responses.create.call_args[1]
        assert "tools" not in call_kwargs

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.OpenAI")
    def test_single_turn_api_failure_returns_error(self, mock_openai_cls, _vcs, _ws):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API down")

        runner = OpenAIAgentRunner(
            settings=MagicMock(),
            workspace=Path("/tmp/test"),
            model="o3",
        )
        result = runner.run_single_turn(
            system_prompt="test",
            user_prompt="test",
        )

        assert result.returncode == 1
        assert result.raw_final_message == ""


class TestFullHistoryMode:
    """Tests that use_sliding_window=False sends full history in Responses API."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.OpenAI")
    def test_responses_full_history_no_sliding_window(self, mock_openai_cls, _vcs, _ws):
        """When use_sliding_window=False, all tool exchanges should be in input (not just last 3)."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        call_count = 0
        def fake_responses_create(**kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            mock_response.usage.input_tokens = 1000
            mock_response.usage.output_tokens = 200

            if call_count <= 4:
                fc = MagicMock()
                fc.type = "function_call"
                fc.call_id = f"call-{call_count}"
                fc.name = "get_file_content"
                fc.arguments = json.dumps({"file_path": f"file{call_count}.cs"})
                mock_response.output = [fc]
                mock_response.status = "incomplete"
            else:
                msg_content = MagicMock()
                msg_content.text = '```json\n{"findings": [], "files_clean": []}\n```'
                msg_item = MagicMock()
                msg_item.type = "message"
                msg_item.content = [msg_content]
                mock_response.output = [msg_item]
                mock_response.status = "completed"

            return mock_response

        mock_client.responses.create.side_effect = fake_responses_create

        runner = OpenAIAgentRunner(
            settings=MagicMock(),
            workspace=Path("/tmp/test"),
            model="codex-mini-latest",
        )
        runner.registry = MagicMock()
        runner.registry.responses_definitions.return_value = []
        runner.registry.dispatch.return_value = '{"content": "file content"}'

        result = runner.run("Review these files", max_turns=10, use_sliding_window=False)

        fifth_call_kwargs = mock_client.responses.create.call_args_list[4][1]
        input_items = fifth_call_kwargs["input"]
        tool_outputs = [i for i in input_items if i.get("type") == "function_call_output"]
        assert len(tool_outputs) == 4, f"Expected 4 tool outputs in full history, got {len(tool_outputs)}"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.OpenAI")
    def test_chat_completions_ignores_sliding_window_flag(self, mock_openai_cls, _vcs, _ws):
        """Chat Completions always uses full history — the flag should have no effect."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '```json\n{"findings": []}\n```'
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage.prompt_tokens = 1000
        mock_response.usage.completion_tokens = 200
        mock_response.usage.total_tokens = 1200
        mock_client.chat.completions.create.return_value = mock_response

        runner = OpenAIAgentRunner(
            settings=MagicMock(),
            workspace=Path("/tmp/test"),
            model="o3",
        )
        result = runner.run("Review", max_turns=5, use_sliding_window=False)
        assert result.findings_data is not None


class TestScanPromptTemplate:
    def test_review_scan_md_exists(self):
        commands_dir = Path(__file__).parent.parent.parent / "commands"
        scan_path = commands_dir / "review-scan.md"
        assert scan_path.is_file(), f"Missing: {scan_path}"

    def test_review_scan_md_contains_required_sections(self):
        commands_dir = Path(__file__).parent.parent.parent / "commands"
        content = (commands_dir / "review-scan.md").read_text(encoding="utf-8")
        assert "candidates" in content.lower()
        assert "needs_verification" in content
        assert "verification_hint" in content
        assert "files_clean" in content
        assert "checklist_source" in content
        assert "JSON" in content


class TestVerifyPromptTemplate:
    def test_review_verify_md_exists(self):
        commands_dir = Path(__file__).parent.parent.parent / "commands"
        verify_path = commands_dir / "review-verify.md"
        assert verify_path.is_file(), f"Missing: {verify_path}"

    def test_review_verify_md_contains_required_sections(self):
        commands_dir = Path(__file__).parent.parent.parent / "commands"
        content = (commands_dir / "review-verify.md").read_text(encoding="utf-8")
        assert "needs_verification" in content
        assert "suggestion" in content.lower()
        assert "findings" in content.lower()
        assert "files_clean" in content
        assert "confidence" in content


class _FakeFileChange:
    def __init__(self, path, additions=10, deletions=5, change_type="edit"):
        self.path = path
        self.additions = additions
        self.deletions = deletions
        self.change_type = change_type


class TestParseCandidates:
    """Tests for _parse_candidates() — extracting structured candidates from Pass 1 output."""

    def test_parse_valid_candidates_json(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        raw = json.dumps({
            "candidates": [
                {
                    "file": "src/Auth.cs",
                    "line": 42,
                    "category": "security",
                    "severity": "critical",
                    "title": "SQL injection",
                    "message": "Raw interpolation in query",
                    "needs_verification": True,
                    "verification_hint": "Check for parameterized query",
                    "checklist_source": "security/sql_injection",
                },
                {
                    "file": "src/Helper.cs",
                    "line": 1,
                    "category": "testing",
                    "severity": "suggestion",
                    "title": "Missing tests",
                    "message": "No test coverage",
                    "needs_verification": False,
                },
            ],
            "files_clean": ["src/Constants.cs"],
        })

        candidates, files_clean = job._parse_candidates(raw)
        assert len(candidates) == 2
        assert candidates[0].file == "src/Auth.cs"
        assert candidates[0].needs_verification is True
        assert candidates[1].needs_verification is False
        assert files_clean == ["src/Constants.cs"]

    def test_parse_candidates_from_code_fence(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        raw = '```json\n{"candidates": [{"file": "a.cs", "line": 1, "category": "testing", "severity": "suggestion", "title": "t", "message": "m"}], "files_clean": []}\n```'

        candidates, files_clean = job._parse_candidates(raw)
        assert len(candidates) == 1
        assert candidates[0].file == "a.cs"

    def test_parse_candidates_empty_returns_empty(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        raw = json.dumps({"candidates": [], "files_clean": ["a.cs", "b.cs"]})
        candidates, files_clean = job._parse_candidates(raw)
        assert len(candidates) == 0
        assert len(files_clean) == 2

    def test_parse_candidates_malformed_json_raises(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        with pytest.raises(ValueError, match="Failed to parse"):
            job._parse_candidates("this is not json at all {{{")

    def test_parse_candidates_missing_candidates_key_raises(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        with pytest.raises(ValueError, match="Missing 'candidates'"):
            job._parse_candidates(json.dumps({"findings": []}))


class TestBuildScanPrompt:
    def test_scan_prompt_includes_diffs(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=123, repo="test-repo", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        diffs = {"src/Auth.cs": "- old\n+ new", "src/Helper.cs": "+ added"}
        changed_files = [_FakeFileChange("src/Auth.cs"), _FakeFileChange("src/Helper.cs")]
        analysis = {"risk_score": 0.5, "review_priorities": [], "test_gaps": []}

        prompt = job._build_scan_prompt(changed_files, diffs, analysis)

        assert "src/Auth.cs" in prompt
        assert "src/Helper.cs" in prompt
        assert "- old" in prompt
        assert "+ new" in prompt
        assert "risk" in prompt.lower()

    def test_scan_prompt_includes_review_mode_checklists(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=123, repo="test-repo", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        prompt = job._build_scan_prompt([], {}, {})
        assert "Review Mode" in prompt or "checklist" in prompt.lower()


class TestBuildVerifyPrompt:
    def test_verify_prompt_includes_candidates(self):
        from review_job import ReviewJob, ReviewJobConfig
        from models.review_models import ScanCandidate
        config = ReviewJobConfig(
            pr_id=123, repo="test-repo", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        candidates = [
            ScanCandidate(
                file="src/Auth.cs", line=42, category="security",
                severity="critical", title="SQL injection",
                message="Raw interpolation", needs_verification=True,
                verification_hint="Check parameterized query",
            ),
        ]
        diffs = {"src/Auth.cs": "- old\n+ new"}

        prompt = job._build_verify_prompt(candidates, diffs, ["src/Auth.cs", "src/Other.cs"])

        assert "SQL injection" in prompt
        assert "Check parameterized query" in prompt
        assert "src/Auth.cs" in prompt
        assert "- old" in prompt

    def test_verify_prompt_only_includes_diffs_for_candidate_files(self):
        from review_job import ReviewJob, ReviewJobConfig
        from models.review_models import ScanCandidate
        config = ReviewJobConfig(
            pr_id=123, repo="test-repo", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        candidates = [
            ScanCandidate(
                file="src/Auth.cs", line=42, category="security",
                severity="critical", title="Issue", message="msg",
                needs_verification=True,
            ),
        ]
        diffs = {"src/Auth.cs": "auth diff", "src/Helper.cs": "helper diff", "src/Other.cs": "other diff"}

        prompt = job._build_verify_prompt(candidates, diffs, ["src/Auth.cs", "src/Helper.cs", "src/Other.cs"])

        assert "auth diff" in prompt
        assert "helper diff" not in prompt
        assert "other diff" not in prompt

    def test_verify_prompt_includes_scoring_rules(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=123, repo="test-repo", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        prompt = job._build_verify_prompt([], {}, [])
        assert "confidence" in prompt.lower() or "severity" in prompt.lower()


class TestRunScanPass:
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_scan_pass_returns_candidates(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        mock_result = MagicMock()
        mock_result.raw_final_message = json.dumps({
            "candidates": [
                {"file": "a.cs", "line": 10, "category": "security", "severity": "warning",
                 "title": "Issue", "message": "desc", "needs_verification": True,
                 "verification_hint": "check full file"},
            ],
            "files_clean": ["b.cs"],
        })
        mock_result.input_tokens = 5000
        mock_result.output_tokens = 500
        mock_result.total_tokens = 5500
        mock_result.returncode = 0
        mock_runner.run_single_turn.return_value = mock_result

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        candidates, files_clean, scan_result = job._run_scan_pass("scan prompt")
        assert len(candidates) == 1
        assert candidates[0].file == "a.cs"
        assert files_clean == ["b.cs"]
        assert scan_result.input_tokens == 5000

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_scan_pass_retries_on_malformed_json(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        bad_result = MagicMock()
        bad_result.raw_final_message = "This is not JSON at all"
        bad_result.returncode = 0
        bad_result.input_tokens = 0
        bad_result.output_tokens = 0
        bad_result.total_tokens = 0

        good_result = MagicMock()
        good_result.raw_final_message = json.dumps({
            "candidates": [], "files_clean": ["a.cs"],
        })
        good_result.returncode = 0
        good_result.input_tokens = 5000
        good_result.output_tokens = 500
        good_result.total_tokens = 5500

        mock_runner.run_single_turn.side_effect = [bad_result, good_result]

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        settings = MagicMock()
        settings.scan_pass_max_retries = 1
        job = ReviewJob(config, settings=settings)

        candidates, files_clean, scan_result = job._run_scan_pass("scan prompt")
        assert len(candidates) == 0
        assert files_clean == ["a.cs"]
        assert mock_runner.run_single_turn.call_count == 2

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_scan_pass_raises_after_all_retries_exhausted(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        bad_result = MagicMock()
        bad_result.raw_final_message = "garbage"
        bad_result.returncode = 0
        bad_result.input_tokens = 0
        bad_result.output_tokens = 0
        bad_result.total_tokens = 0
        mock_runner.run_single_turn.return_value = bad_result

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        settings = MagicMock()
        settings.scan_pass_max_retries = 1
        job = ReviewJob(config, settings=settings)

        with pytest.raises(ValueError, match="Pass 1 failed"):
            job._run_scan_pass("scan prompt")


class TestRunVerifyPass:
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_verify_pass_returns_findings_data(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        mock_result = MagicMock()
        mock_result.findings_data = {
            "findings": [{"id": "cr-001", "file": "a.cs", "line": 10}],
            "files_clean": ["b.cs"],
        }
        mock_result.input_tokens = 10000
        mock_result.output_tokens = 2000
        mock_result.total_tokens = 12000
        mock_result.tool_calls_count = 5
        mock_result.duration_seconds = 30.0
        mock_result.model = "o3"
        mock_result.returncode = 0
        mock_runner.run.return_value = mock_result

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        settings = MagicMock()
        settings.verify_pass_max_turns = 10
        job = ReviewJob(config, settings=settings)

        result = job._run_verify_pass("verify prompt")
        assert result.findings_data is not None
        assert len(result.findings_data["findings"]) == 1

        mock_runner.run.assert_called_once_with(
            "verify prompt", max_turns=10, use_sliding_window=False,
        )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_verify_pass_uses_pass1_candidates_on_failure(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig
        from models.review_models import ScanCandidate

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        mock_result = MagicMock()
        mock_result.findings_data = None
        mock_result.returncode = 1
        mock_result.input_tokens = 0
        mock_result.output_tokens = 0
        mock_result.total_tokens = 0
        mock_result.tool_calls_count = 0
        mock_result.duration_seconds = 0
        mock_result.model = "o3"
        mock_runner.run.return_value = mock_result

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        settings = MagicMock()
        settings.verify_pass_max_turns = 10
        job = ReviewJob(config, settings=settings)

        job._scan_candidates = [
            ScanCandidate(
                file="a.cs", line=10, category="security", severity="warning",
                title="Issue", message="desc",
            ),
        ]
        job._scan_files_clean = ["b.cs"]

        result = job._run_verify_pass("verify prompt")

        assert result.findings_data is not None
        assert len(result.findings_data["findings"]) == 1
        assert result.findings_data["findings"][0]["file"] == "a.cs"
        assert result.findings_data["files_clean"] == ["b.cs"]


class TestCreateFindingsTwoPass:
    """Tests that create_findings() uses two-pass when enabled, falls back to single-pass on failure."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_two_pass_flow_produces_findings(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        # Pass 1: scan returns candidates
        scan_result = MagicMock()
        scan_result.raw_final_message = json.dumps({
            "candidates": [
                {"file": "a.cs", "line": 10, "category": "security", "severity": "warning",
                 "title": "Issue A", "message": "desc A", "needs_verification": True,
                 "verification_hint": "check full file"},
            ],
            "files_clean": ["b.cs"],
        })
        scan_result.returncode = 0
        scan_result.input_tokens = 5000
        scan_result.output_tokens = 500
        scan_result.total_tokens = 5500
        scan_result.duration_seconds = 3.0
        scan_result.model = "o3"
        mock_runner.run_single_turn.return_value = scan_result

        # Pass 2: verify returns findings
        verify_result = MagicMock()
        verify_result.findings_data = {
            "findings": [{"id": "cr-001", "file": "a.cs", "line": 10, "severity": "warning",
                          "category": "security", "title": "Issue A", "message": "verified",
                          "confidence": 0.85}],
            "files_clean": ["b.cs"],
            "fix_verifications": [],
            "review_modes": ["standard"],
        }
        verify_result.input_tokens = 10000
        verify_result.output_tokens = 2000
        verify_result.total_tokens = 12000
        verify_result.tool_calls_count = 5
        verify_result.duration_seconds = 30.0
        verify_result.model = "o3"
        verify_result.returncode = 0
        mock_runner.run.return_value = verify_result

        settings = MagicMock()
        settings.two_pass_enabled = True
        settings.scan_pass_max_retries = 1
        settings.verify_pass_max_turns = 10
        settings.skip_extensions = ".md,.json"

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test_two_pass"),
            prompt_text="test prompt",
            file_subset=[_FakeFileChange("a.cs"), _FakeFileChange("b.cs")],
        )
        job = ReviewJob(config, settings=settings)

        path = job.create_findings()

        # Verify Pass 1 was called
        mock_runner.run_single_turn.assert_called_once()
        # Verify Pass 2 was called with use_sliding_window=False
        mock_runner.run.assert_called_once()
        call_kwargs = mock_runner.run.call_args
        assert call_kwargs[1].get("use_sliding_window") is False

        # Verify findings were written
        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data["findings"]) == 1
        assert data["findings"][0]["file"] == "a.cs"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_falls_back_to_single_pass_on_scan_failure(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        # Pass 1 fails — returns garbage both times
        bad_result = MagicMock()
        bad_result.raw_final_message = "not json"
        bad_result.returncode = 0
        bad_result.input_tokens = 0
        bad_result.output_tokens = 0
        bad_result.total_tokens = 0
        mock_runner.run_single_turn.return_value = bad_result

        # Fallback single-pass returns findings
        fallback_result = MagicMock()
        fallback_result.findings_data = {
            "findings": [{"id": "cr-001", "file": "a.cs"}],
            "files_clean": [],
            "fix_verifications": [],
            "review_modes": ["standard"],
        }
        fallback_result.input_tokens = 50000
        fallback_result.output_tokens = 5000
        fallback_result.total_tokens = 55000
        fallback_result.tool_calls_count = 20
        fallback_result.duration_seconds = 120.0
        fallback_result.model = "o3"
        mock_runner.run.return_value = fallback_result

        settings = MagicMock()
        settings.two_pass_enabled = True
        settings.scan_pass_max_retries = 1
        settings.verify_pass_max_turns = 10
        settings.batch_max_turns = 40
        settings.skip_extensions = ".md,.json"

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test_fallback"),
            prompt_text="test prompt",
            file_subset=[_FakeFileChange("a.cs")],
        )
        job = ReviewJob(config, settings=settings)

        path = job.create_findings()

        # Pass 1 was tried (1 + 1 retry = 2 calls)
        assert mock_runner.run_single_turn.call_count == 2
        # Fallback single-pass was used
        mock_runner.run.assert_called_once()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_single_pass_when_two_pass_disabled(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        single_result = MagicMock()
        single_result.findings_data = {
            "findings": [], "files_clean": ["a.cs"],
            "fix_verifications": [], "review_modes": ["standard"],
        }
        single_result.input_tokens = 50000
        single_result.output_tokens = 5000
        single_result.total_tokens = 55000
        single_result.tool_calls_count = 20
        single_result.duration_seconds = 120.0
        single_result.model = "o3"
        mock_runner.run.return_value = single_result

        settings = MagicMock()
        settings.two_pass_enabled = False
        settings.batch_max_turns = 40

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test_disabled"),
            prompt_text="test prompt",
            file_subset=[_FakeFileChange("a.cs")],
        )
        job = ReviewJob(config, settings=settings)

        path = job.create_findings()

        # run_single_turn should NOT have been called
        mock_runner.run_single_turn.assert_not_called()
        # run() should have been called (single-pass)
        mock_runner.run.assert_called_once()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_scan_pass_no_candidates_skips_pass2(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        # Pass 1: returns empty candidates (all files clean)
        scan_result = MagicMock()
        scan_result.raw_final_message = json.dumps({
            "candidates": [],
            "files_clean": ["a.cs", "b.cs", "c.cs"],
        })
        scan_result.returncode = 0
        scan_result.input_tokens = 5000
        scan_result.output_tokens = 200
        scan_result.total_tokens = 5200
        scan_result.duration_seconds = 2.0
        scan_result.model = "o3"
        mock_runner.run_single_turn.return_value = scan_result

        settings = MagicMock()
        settings.two_pass_enabled = True
        settings.scan_pass_max_retries = 1
        settings.verify_pass_max_turns = 10

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test_no_candidates"),
            prompt_text="test prompt",
            file_subset=[_FakeFileChange("a.cs"), _FakeFileChange("b.cs"), _FakeFileChange("c.cs")],
        )
        job = ReviewJob(config, settings=settings)

        path = job.create_findings()

        # Pass 2 should NOT have been called — no candidates to verify
        mock_runner.run.assert_not_called()

        data = json.loads(path.read_text())
        assert len(data["findings"]) == 0
        assert set(data["files_clean"]) == {"a.cs", "b.cs", "c.cs"}


class TestCandidatesToFindings:
    def test_converts_candidates_to_findings_format(self):
        from review_job import ReviewJob, ReviewJobConfig
        from models.review_models import ScanCandidate

        config = ReviewJobConfig(
            pr_id=99, repo="test-repo", workspace=Path("/tmp/test"),
            prompt_text="test",
        )
        job = ReviewJob(config, settings=MagicMock())

        job._scan_candidates = [
            ScanCandidate(
                file="a.cs", line=10, category="security", severity="warning",
                title="Issue A", message="desc A", needs_verification=True,
            ),
            ScanCandidate(
                file="b.cs", line=20, category="testing", severity="suggestion",
                title="Issue B", message="desc B", needs_verification=False,
            ),
        ]
        job._scan_files_clean = ["c.cs"]

        data = job._candidates_to_findings()
        assert data["pr_id"] == 99
        assert data["repo"] == "test-repo"
        assert len(data["findings"]) == 2
        assert data["findings"][0]["id"] == "cr-001"
        assert data["findings"][1]["id"] == "cr-002"
        # Unverified candidate gets lower confidence
        assert data["findings"][0]["confidence"] == 0.6
        # Pre-verified candidate gets higher confidence
        assert data["findings"][1]["confidence"] == 0.75
        assert data["files_clean"] == ["c.cs"]
        assert "(unverified" in data["findings"][0]["message"]


class TestTokenUsageAggregation:
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("review_job.OpenAIAgentRunner")
    def test_merged_token_usage_includes_both_passes(self, mock_runner_cls):
        from review_job import ReviewJob, ReviewJobConfig

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        scan_result = MagicMock()
        scan_result.raw_final_message = json.dumps({
            "candidates": [{"file": "a.cs", "line": 1, "category": "security",
                            "severity": "warning", "title": "t", "message": "m",
                            "needs_verification": True}],
            "files_clean": [],
        })
        scan_result.returncode = 0
        scan_result.input_tokens = 28000
        scan_result.output_tokens = 2000
        scan_result.total_tokens = 30000
        scan_result.duration_seconds = 3.0
        scan_result.model = "o3"
        mock_runner.run_single_turn.return_value = scan_result

        verify_result = MagicMock()
        verify_result.findings_data = {
            "findings": [{"id": "cr-001", "file": "a.cs"}],
            "files_clean": [], "fix_verifications": [],
            "review_modes": ["standard"],
        }
        verify_result.input_tokens = 100000
        verify_result.output_tokens = 10000
        verify_result.total_tokens = 110000
        verify_result.tool_calls_count = 8
        verify_result.duration_seconds = 45.0
        verify_result.model = "o3"
        verify_result.returncode = 0
        mock_runner.run.return_value = verify_result

        settings = MagicMock()
        settings.two_pass_enabled = True
        settings.scan_pass_max_retries = 1
        settings.verify_pass_max_turns = 10

        config = ReviewJobConfig(
            pr_id=1, repo="test", workspace=Path("/tmp/test_tokens"),
            prompt_text="test prompt",
            file_subset=[_FakeFileChange("a.cs")],
        )
        job = ReviewJob(config, settings=settings)
        job.create_findings()

        # The usage in findings.json should include both passes
        data = json.loads(job.findings_path.read_text())
        usage = data["usage"]
        assert usage["input_tokens"] == 128000  # 28000 + 100000
        assert usage["output_tokens"] == 12000  # 2000 + 10000
        assert usage["total_tokens"] == 140000  # 30000 + 110000
