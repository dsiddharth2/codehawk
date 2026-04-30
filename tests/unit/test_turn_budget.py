"""
Unit tests for deadline injection and turn budget (Phase 4 — Task 8).

Covers:
  - build_system_prompt() content for graph/no-graph variants
  - Deadline message injected at turn N-3 in chat completions loop
  - Deadline message NOT injected before turn N-3
  - Deadline message injected at turn N-3 in responses API loop
  - Turn counter appended to tool result content
  - Emergency fallback findings when agent produces no JSON
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

from agents.openai_runner import build_system_prompt, _extract_findings_json

DEADLINE_PHRASE = "DEADLINE: You have 3 turns remaining"
SAMPLE_FINDINGS_TEXT = (
    "```json\n"
    '{"pr_id": 1, "repo": "test", "vcs": "ado", "review_modes": ["standard"],'
    ' "findings": [], "fix_verifications": []}\n'
    "```"
)


# ---------------------------------------------------------------------------
# Helpers — mock OpenAI response objects
# ---------------------------------------------------------------------------

def _make_tool_call(call_id="tc-1", fn_name="dummy_tool", fn_args="{}"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = fn_name
    tc.function.arguments = fn_args
    return tc


def _make_chat_response(finish_reason="tool_calls", content=None, tool_calls=None):
    """Build a mock chat.completions.create() response."""
    resp = MagicMock()
    resp.usage.prompt_tokens = 5
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 10
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message.content = content
    if tool_calls is None and finish_reason == "tool_calls":
        tool_calls = [_make_tool_call()]
    choice.message.tool_calls = tool_calls or []
    choice.message.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": [],
    }
    resp.choices = [choice]
    return resp


def _make_responses_response(fn_calls=None, text=None):
    """Build a mock responses.create() response.

    If fn_calls is provided, output contains function_call items.
    If text is provided (and no fn_calls), output contains a message item.
    """
    resp = MagicMock()
    resp.id = "resp-mock"
    resp.status = "completed"
    resp.usage.input_tokens = 5
    resp.usage.output_tokens = 5

    output_items = []

    if fn_calls:
        for fn_name, call_id in fn_calls:
            fc = MagicMock()
            fc.type = "function_call"
            fc.name = fn_name
            fc.arguments = "{}"
            fc.call_id = call_id
            output_items.append(fc)
    else:
        msg_item = MagicMock()
        msg_item.type = "message"
        content_piece = MagicMock()
        content_piece.text = text or ""
        msg_item.content = [content_piece]
        output_items.append(msg_item)

    resp.output = output_items
    return resp


# ---------------------------------------------------------------------------
# Fixture — runner with all external deps mocked
# ---------------------------------------------------------------------------

def _make_runner(mock_openai_cls, mock_registry_cls, model="gpt-4o-mini"):
    """Create an OpenAIAgentRunner with mocked client and registry."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    mock_registry = MagicMock()
    mock_registry_cls.return_value = mock_registry
    mock_registry.openai_definitions.return_value = []
    mock_registry.responses_definitions.return_value = []
    mock_registry.dispatch.return_value = '{"ok": true}'

    from agents.openai_runner import OpenAIAgentRunner

    runner = OpenAIAgentRunner(
        settings=MagicMock(),
        workspace=Path("/tmp"),
        model=model,
        pr_id=1,
        repo="test-repo",
    )
    return runner, mock_client


# ---------------------------------------------------------------------------
# Tests — build_system_prompt
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def test_turn_budget_stated(self):
        prompt = build_system_prompt(40, has_graph=False)
        assert "40 turns total" in prompt
        assert "Reserve the last 3" in prompt

    def test_graph_first_strategy_when_has_graph(self):
        prompt = build_system_prompt(40, has_graph=True)
        assert "FIRST tool call MUST be `get_change_analysis`" in prompt
        assert "Do NOT read files one-by-one" in prompt

    def test_no_graph_instructs_diffs(self):
        prompt = build_system_prompt(40, has_graph=False)
        assert "get_file_diff" in prompt
        assert "search_code" in prompt

    def test_turn_count_reflects_parameter(self):
        p20 = build_system_prompt(20, has_graph=False)
        p40 = build_system_prompt(40, has_graph=False)
        assert "20 turns total" in p20
        assert "40 turns total" in p40
        # Ensure the two prompts differ only in the budget line
        assert "20 turns" not in p40
        assert "40 turns" not in p20


# ---------------------------------------------------------------------------
# Tests — deadline injection in chat completions loop
# ---------------------------------------------------------------------------

def _snapshot_side_effect(mock_client, responses):
    """Wire up a side_effect that snapshots messages at each call.

    The runner mutates the same `messages` list in-place, so entries in
    call_args_list all reference the same (final-state) object.  Capturing
    a shallow copy at call time is the only way to see the state at each turn.

    Returns the snapshots list (populated after the run completes).
    """
    snapshots = []
    queue = list(responses)

    def _side_effect(**kwargs):
        snapshots.append(list(kwargs["messages"]))
        return queue.pop(0)

    mock_client.chat.completions.create.side_effect = _side_effect
    return snapshots


class TestDeadlineInjectionChatCompletions:
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.ToolRegistry")
    @patch("agents.openai_runner.OpenAI")
    def test_deadline_injected_at_n_minus_3(
        self, mock_openai_cls, mock_registry_cls, _rv, _rw
    ):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            runner, mock_client = _make_runner(mock_openai_cls, mock_registry_cls)

        max_turns = 6  # deadline fires at turn index 3 (== 6 - 3)

        responses = [
            _make_chat_response("tool_calls"),
            _make_chat_response("tool_calls"),
            _make_chat_response("tool_calls"),
            _make_chat_response("stop", content=SAMPLE_FINDINGS_TEXT, tool_calls=[]),
        ]
        snapshots = _snapshot_side_effect(mock_client, responses)

        runner._run_chat_completions("test prompt", max_turns=max_turns)

        # snapshots[3] is the messages list as it was when the turn-N-3 call was made
        deadline_count = sum(
            1 for m in snapshots[3]
            if DEADLINE_PHRASE in (m.get("content") or "")
        )
        assert deadline_count == 1, (
            f"Expected exactly 1 deadline message at turn N-3, found {deadline_count}"
        )

    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.ToolRegistry")
    @patch("agents.openai_runner.OpenAI")
    def test_deadline_not_injected_before_n_minus_3(
        self, mock_openai_cls, mock_registry_cls, _rv, _rw
    ):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            runner, mock_client = _make_runner(mock_openai_cls, mock_registry_cls)

        max_turns = 6

        responses = [
            _make_chat_response("tool_calls"),
            _make_chat_response("tool_calls"),
            _make_chat_response("tool_calls"),
            _make_chat_response("stop", content=SAMPLE_FINDINGS_TEXT, tool_calls=[]),
        ]
        snapshots = _snapshot_side_effect(mock_client, responses)

        runner._run_chat_completions("test prompt", max_turns=max_turns)

        # snapshots[0..2] are before the deadline turn — must be free of the phrase
        for i in range(max_turns - 3):
            early_deadline = [
                m for m in snapshots[i]
                if DEADLINE_PHRASE in (m.get("content") or "")
            ]
            assert early_deadline == [], (
                f"Deadline message found at turn {i} (before N-3)"
            )


# ---------------------------------------------------------------------------
# Tests — deadline injection in responses API loop
# ---------------------------------------------------------------------------

class TestDeadlineInjectionResponsesAPI:
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.ToolRegistry")
    @patch("agents.openai_runner.OpenAI")
    def test_deadline_injected_at_n_minus_3_responses(
        self, mock_openai_cls, mock_registry_cls, _rv, _rw
    ):
        # Use a model that routes to responses API
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            runner, mock_client = _make_runner(
                mock_openai_cls, mock_registry_cls, model="codex-mini-latest"
            )

        max_turns = 6  # deadline at turn index 3

        responses = [
            _make_responses_response(fn_calls=[("dummy_tool", "call-1")]),
            _make_responses_response(fn_calls=[("dummy_tool", "call-2")]),
            _make_responses_response(fn_calls=[("dummy_tool", "call-3")]),
            _make_responses_response(text=SAMPLE_FINDINGS_TEXT),
        ]
        mock_client.responses.create.side_effect = responses

        runner._run_responses("test prompt", max_turns=max_turns)

        calls = mock_client.responses.create.call_args_list
        # At turn N-3, the `input` kwarg must include the deadline message
        input_items_at_deadline = calls[3].kwargs["input"]
        deadline_msgs = [
            item for item in input_items_at_deadline
            if DEADLINE_PHRASE in (item.get("content") or "")
        ]
        assert len(deadline_msgs) == 1, (
            f"Expected 1 deadline message in responses input at turn N-3, found {len(deadline_msgs)}"
        )


# ---------------------------------------------------------------------------
# Tests — turn counter in tool results
# ---------------------------------------------------------------------------

class TestTurnCounter:
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.ToolRegistry")
    @patch("agents.openai_runner.OpenAI")
    def test_tool_result_has_turn_counter(
        self, mock_openai_cls, mock_registry_cls, _rv, _rw
    ):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            runner, mock_client = _make_runner(mock_openai_cls, mock_registry_cls)

        max_turns = 5
        # Turn 0: one tool call; turn 1: stop
        responses = [
            _make_chat_response("tool_calls"),
            _make_chat_response("stop", content=SAMPLE_FINDINGS_TEXT, tool_calls=[]),
        ]
        mock_client.chat.completions.create.side_effect = responses

        runner._run_chat_completions("test prompt", max_turns=max_turns)

        # After turn 0's tool call, a tool result is appended before turn 1's call.
        calls = mock_client.chat.completions.create.call_args_list
        messages_turn1 = calls[1].kwargs["messages"]
        tool_msgs = [m for m in messages_turn1 if m.get("role") == "tool"]

        assert len(tool_msgs) >= 1, "Expected at least one tool result message at turn 1"
        # Turn index 0 → "[Turn 1/5 used. 4 remaining.]"
        assert "[Turn 1/5 used. 4 remaining.]" in tool_msgs[0]["content"]

    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.ToolRegistry")
    @patch("agents.openai_runner.OpenAI")
    def test_turn_counter_correct_at_second_turn(
        self, mock_openai_cls, mock_registry_cls, _rv, _rw
    ):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            runner, mock_client = _make_runner(mock_openai_cls, mock_registry_cls)

        max_turns = 10
        # Turn 0 and turn 1: tool calls; turn 2: stop
        responses = [
            _make_chat_response("tool_calls"),
            _make_chat_response("tool_calls"),
            _make_chat_response("stop", content=SAMPLE_FINDINGS_TEXT, tool_calls=[]),
        ]
        mock_client.chat.completions.create.side_effect = responses

        runner._run_chat_completions("test prompt", max_turns=max_turns)

        calls = mock_client.chat.completions.create.call_args_list
        messages_turn2 = calls[2].kwargs["messages"]
        tool_msgs = [m for m in messages_turn2 if m.get("role") == "tool"]

        # There should be 2 tool results; the last one is from turn index 1
        # "[Turn 2/10 used. 8 remaining.]"
        assert len(tool_msgs) >= 2
        assert "[Turn 2/10 used. 8 remaining.]" in tool_msgs[-1]["content"]


# ---------------------------------------------------------------------------
# Tests — emergency fallback findings
# ---------------------------------------------------------------------------

class TestEmergencyFallback:
    @patch("agents.openai_runner.register_workspace_tools")
    @patch("agents.openai_runner.register_vcs_tools")
    @patch("agents.openai_runner.ToolRegistry")
    @patch("agents.openai_runner.OpenAI")
    def test_emergency_findings_produced_when_no_json(
        self, mock_openai_cls, mock_registry_cls, _rv, _rw
    ):
        """When agent produces no parseable JSON, emergency findings are synthesized."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            runner, mock_client = _make_runner(mock_openai_cls, mock_registry_cls)

        max_turns = 3
        # All turns return stop with non-JSON content
        responses = [
            _make_chat_response("stop", content="Sorry, I ran out of time.", tool_calls=[]),
        ]
        mock_client.chat.completions.create.side_effect = responses

        result = runner._run_chat_completions("test prompt", max_turns=max_turns)

        assert result.findings_data is not None
        assert "error" in result.findings_data
        assert result.findings_data["findings"] == []
        assert result.findings_data["pr_id"] == 1
        assert result.findings_data["repo"] == "test-repo"
        assert result.findings_data["vcs"] == "ado"
