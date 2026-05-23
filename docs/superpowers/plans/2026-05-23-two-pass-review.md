# Two-Pass Review Architecture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single 38-turn agent loop with two focused passes (Pass 1: single-turn scan, Pass 2: short verify loop) to reduce token cost from 11.3M to ~1.9M for a 98-file PR.

**Architecture:** Pass 1 is a single-turn, tool-free API call that reads pre-injected diffs and outputs candidate findings as structured JSON. Pass 2 is a short agent loop (max 10 turns, full history, no sliding window) that verifies only candidates marked `needs_verification=true`. Both passes run per-batch through the existing `BatchReviewJob` orchestrator. A fallback path preserves the current single-pass architecture when either pass fails.

**Tech Stack:** Python 3.11+, OpenAI API (Chat Completions + Responses), pytest + unittest.mock

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/agents/openai_runner.py` | Modify | Add `run_single_turn()` method; add `use_sliding_window` param to `_run_responses()` and `run()` |
| `src/review_job.py` | Modify | Replace `create_findings()` internals with two-pass flow; add `_build_scan_prompt()`, `_build_verify_prompt()`, `_run_scan_pass()`, `_run_verify_pass()`, `_parse_candidates()`, `_merge_pass_results()` |
| `src/models/review_models.py` | Modify | Add `ScanCandidate` dataclass |
| `src/config.py` | Modify | Add `two_pass_enabled`, `scan_pass_max_retries`, `verify_pass_max_turns` fields |
| `commands/review-scan.md` | Create | Pass 1 prompt template — scan instructions |
| `commands/review-verify.md` | Create | Pass 2 prompt template — verification instructions |
| `tests/unit/test_two_pass_review.py` | Create | All unit tests for the two-pass flow |

---

## Task 1: Config Fields + ScanCandidate Model

**Files:**
- Modify: `src/config.py:125-155`
- Modify: `src/models/review_models.py:200-250`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write test for ScanCandidate model**

```python
# tests/unit/test_two_pass_review.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestScanCandidateModel -v`
Expected: FAIL with `ImportError: cannot import name 'ScanCandidate'`

- [ ] **Step 3: Add ScanCandidate to review_models.py**

Add after the `FindingsFile` class (after line 250 in `src/models/review_models.py`):

```python
@dataclass
class ScanCandidate:
    """A candidate finding from Pass 1 (scan). May need verification in Pass 2."""
    file: str
    line: int
    category: str
    severity: str
    title: str
    message: str
    needs_verification: bool = False
    verification_hint: Optional[str] = None
    checklist_source: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestScanCandidateModel -v`
Expected: PASS

- [ ] **Step 5: Write test for config fields**

Add to `tests/unit/test_two_pass_review.py`:

```python
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
```

- [ ] **Step 6: Add config fields to Settings**

Add after `risk_medium_threshold` field (around line 155 in `src/config.py`):

```python
    # Two-pass review configuration
    two_pass_enabled: bool = Field(
        default=True,
        description="Enable two-pass review (Pass 1: scan, Pass 2: verify). When False, uses single-pass agent loop."
    )
    scan_pass_max_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Max retries for Pass 1 JSON parsing failures before falling back to single-pass"
    )
    verify_pass_max_turns: int = Field(
        default=10,
        ge=5,
        le=40,
        description="Max agent turns for Pass 2 (verify) — shorter than single-pass since candidates are pre-identified"
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_two_pass_review.py -v`
Expected: PASS (both test classes)

- [ ] **Step 8: Commit**

```
git add src/models/review_models.py src/config.py tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add ScanCandidate model and config fields"
```

---

## Task 2: `run_single_turn()` in OpenAIAgentRunner

**Files:**
- Modify: `src/agents/openai_runner.py:160-210`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write test for run_single_turn with Chat Completions model**

Add to `tests/unit/test_two_pass_review.py`:

```python
from agents.openai_runner import OpenAIAgentRunner, AgentResult


class TestRunSingleTurn:
    """Tests for the single-turn (no tools) API call used in Pass 1."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.OpenAI")
    def test_single_turn_chat_completions(self, mock_openai_cls):
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
    @patch("agents.openai_runner.OpenAI")
    def test_single_turn_responses_api(self, mock_openai_cls):
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
    @patch("agents.openai_runner.OpenAI")
    def test_single_turn_api_failure_returns_error(self, mock_openai_cls):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestRunSingleTurn -v`
Expected: FAIL with `AttributeError: 'OpenAIAgentRunner' object has no attribute 'run_single_turn'`

- [ ] **Step 3: Implement run_single_turn()**

Add to `OpenAIAgentRunner` class in `src/agents/openai_runner.py`, after the `run()` method (after line 208):

```python
    def run_single_turn(self, system_prompt: str, user_prompt: str) -> AgentResult:
        """Single-turn API call with no tools. Used for Pass 1 (scan)."""
        result = AgentResult()
        result.model = self.model
        result.turns = 1
        start_time = time.time()

        try:
            if self._use_responses_api:
                kwargs = {
                    "model": self.model,
                    "instructions": system_prompt,
                    "input": [{"type": "message", "role": "user", "content": user_prompt}],
                }
                if "codex" not in self.model:
                    kwargs["temperature"] = 0.3
                response = self.client.responses.create(**kwargs)

                if response.usage:
                    result.input_tokens = response.usage.input_tokens
                    result.output_tokens = response.usage.output_tokens
                    result.total_tokens = response.usage.input_tokens + response.usage.output_tokens

                for item in response.output:
                    if item.type == "message":
                        for content in item.content:
                            if hasattr(content, "text"):
                                result.raw_final_message = content.text
            else:
                chat_kwargs = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                if "codex" not in self.model:
                    chat_kwargs["temperature"] = 0.3
                    chat_kwargs["seed"] = 42
                response = self.client.chat.completions.create(**chat_kwargs)

                if response.usage:
                    result.input_tokens = response.usage.prompt_tokens
                    result.output_tokens = response.usage.completion_tokens
                    result.total_tokens = response.usage.total_tokens

                if response.choices and response.choices[0].message.content:
                    result.raw_final_message = response.choices[0].message.content

        except Exception as e:
            logger.error("Single-turn API call failed: %s", e)
            result.returncode = 1

        result.duration_seconds = round(time.time() - start_time, 1)
        logger.info(
            "Single-turn complete: tokens=%s (in:%s out:%s), duration=%.1fs",
            f"{result.total_tokens:,}", f"{result.input_tokens:,}", f"{result.output_tokens:,}",
            result.duration_seconds,
        )
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestRunSingleTurn -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/agents/openai_runner.py tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add run_single_turn() for tool-free Pass 1"
```

---

## Task 3: Full-History Mode for `_run_responses()`

**Files:**
- Modify: `src/agents/openai_runner.py:205-208, 371-393`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write test for full-history mode**

Add to `tests/unit/test_two_pass_review.py`:

```python
class TestFullHistoryMode:
    """Tests that use_sliding_window=False sends full history in Responses API."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.OpenAI")
    def test_responses_full_history_no_sliding_window(self, mock_openai_cls):
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
                # Return a tool call for the first 4 turns
                fc = MagicMock()
                fc.type = "function_call"
                fc.call_id = f"call-{call_count}"
                fc.name = "get_file_content"
                fc.arguments = json.dumps({"file_path": f"file{call_count}.cs"})
                mock_response.output = [fc]
                mock_response.status = "incomplete"
            else:
                # Return findings on turn 5
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

        # On the 5th call (turn 5), all 4 prior tool exchanges should be in input
        # With sliding window=3, only 3 would be included. With full history, all 4.
        fifth_call_kwargs = mock_client.responses.create.call_args_list[4][1]
        input_items = fifth_call_kwargs["input"]
        # Count function_call_output items (each tool exchange produces one)
        tool_outputs = [i for i in input_items if i.get("type") == "function_call_output"]
        assert len(tool_outputs) == 4, f"Expected 4 tool outputs in full history, got {len(tool_outputs)}"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("agents.openai_runner.OpenAI")
    def test_chat_completions_ignores_sliding_window_flag(self, mock_openai_cls):
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
        # Should not raise — Chat Completions path ignores use_sliding_window
        result = runner.run("Review", max_turns=5, use_sliding_window=False)
        assert result.findings_data is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestFullHistoryMode -v`
Expected: FAIL with `TypeError: run() got an unexpected keyword argument 'use_sliding_window'`

- [ ] **Step 3: Add use_sliding_window parameter to run() and _run_responses()**

In `src/agents/openai_runner.py`:

Change the `run()` method signature (line 205):

```python
    def run(self, prompt: str, max_turns: int = 40, use_sliding_window: bool = True) -> AgentResult:
        if self._use_responses_api:
            return self._run_responses(prompt, max_turns, use_sliding_window=use_sliding_window)
        return self._run_chat_completions(prompt, max_turns)
```

Change the `_run_responses()` method signature (line 371):

```python
    def _run_responses(self, prompt: str, max_turns: int = 40, use_sliding_window: bool = True) -> AgentResult:
```

Then change the `_build_sliding_window_input` call inside `_run_responses()` (around line 388):

```python
            window_size = SLIDING_WINDOW_SIZE if use_sliding_window else 0
            input_items = _build_sliding_window_input(
                original_prompt=prompt,
                tool_history=tool_history,
                findings_draft=findings_draft,
                window_size=window_size,
            )
```

Also update `_build_sliding_window_input` to handle `window_size=0` (include ALL history). Change line 152:

```python
    recent = tool_history[-window_size:] if window_size > 0 else tool_history
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestFullHistoryMode -v`
Expected: PASS

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `python -m pytest tests/unit/ -v`
Expected: All tests PASS (existing behavior unchanged — default `use_sliding_window=True`)

- [ ] **Step 6: Commit**

```
git add src/agents/openai_runner.py tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add use_sliding_window param for full-history Pass 2"
```

---

## Task 4: Pass 1 Prompt Template (`review-scan.md`)

**Files:**
- Create: `commands/review-scan.md`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write test that the prompt file exists and contains required sections**

Add to `tests/unit/test_two_pass_review.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestScanPromptTemplate -v`
Expected: FAIL with `AssertionError: Missing: ...review-scan.md`

- [ ] **Step 3: Create review-scan.md**

Create `commands/review-scan.md`:

```markdown
# Pass 1 — Scan: Identify Candidate Findings

You are a code review scanner. Your job is to identify candidate code review findings from pre-injected diffs. You do NOT have access to tools — you cannot read full files, search code, or call any external functions.

## Instructions

1. Read every diff provided below carefully.
2. Apply ALL review mode checklists (standard, security, architecture, performance, migration) against each file's diff.
3. For each potential issue, create a candidate finding.
4. Decide whether each candidate can be confirmed from the diff alone, or needs full-file context:
   - `needs_verification: false` — The issue is clear from the diff (e.g., missing tests, unused imports, obvious naming issues, hardcoded credentials visible in the diff).
   - `needs_verification: true` — You suspect an issue but need full-file context to confirm (e.g., a function call that might be handled elsewhere, a null check that might exist in a caller, a pattern that might be intentional given the broader class structure).
   - **When in doubt, set `needs_verification: true`** — bias toward false positives over missed issues.
5. For verification candidates, write a specific `verification_hint` explaining exactly what to check in the full file.
6. Track which checklist produced each finding in `checklist_source`.
7. List all files with no issues in `files_clean`.

## Output Format

Output a single JSON object in a ```json code fence:

```json
{
  "candidates": [
    {
      "file": "src/SummaryRenderer.js",
      "line": 74,
      "category": "correctness",
      "severity": "warning",
      "title": "Risk badge disappears when only parsedSummary data is available",
      "message": "The renderer now shows the badge only when riskScore prop is non-null, but parsedSummary.risk_assessment may provide a valid score. This could hide the badge for PRs processed before the riskScore prop was added.",
      "needs_verification": true,
      "verification_hint": "Read full file to check if parsedSummary.risk_assessment fallback exists elsewhere in the render method",
      "checklist_source": "standard/correctness"
    }
  ],
  "files_clean": ["src/constants/AppColors.cs", "src/utils/DateHelper.cs"]
}
```

## Rules

- Every file in the diffs must appear in either `candidates[].file` or `files_clean[]`.
- Do NOT output findings.json format — output the candidates format above.
- Do NOT attempt to call any tools — you have none available.
- Keep messages concise but specific enough for a verifier to understand the issue.
- Use the correct category from: security, performance, best_practices, architecture, correctness, error_handling, code_style, documentation, testing.
- Use the correct severity: critical (only if high confidence from diff alone), warning, suggestion.
- `checklist_source` format: `<mode>/<check>` — e.g., `security/sql_injection`, `standard/error_handling`, `performance/n_plus_one`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestScanPromptTemplate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add commands/review-scan.md tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add Pass 1 scan prompt template"
```

---

## Task 5: Pass 2 Prompt Template (`review-verify.md`)

**Files:**
- Create: `commands/review-verify.md`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write test that the prompt file exists and contains required sections**

Add to `tests/unit/test_two_pass_review.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestVerifyPromptTemplate -v`
Expected: FAIL with `AssertionError: Missing: ...review-verify.md`

- [ ] **Step 3: Create review-verify.md**

Create `commands/review-verify.md`:

```markdown
# Pass 2 — Verify: Confirm Candidate Findings

You are verifying candidate code review findings identified in a previous scan pass. The scan pass reviewed diffs without access to full file context. Your job is to verify each candidate, refine messages, add concrete code suggestions, and produce the final findings.json.

## Tools Available

You have access to: `get_file_content`, `search_code`, `read_local_file`, `get_callers`, `get_dependents`.

## Instructions

1. For each candidate with `needs_verification: true`:
   - Use the `verification_hint` to guide your investigation.
   - Call `read_local_file` or `get_file_content` to read the full file.
   - Determine if the issue is real (confirm) or a false positive (drop).
   - If confirmed, refine the message with full-file context and add a concrete `suggestion` code block.
   - Adjust severity based on what you find in the full file.
   - Set confidence: 0.7-0.79 for likely issues, 0.8-0.89 for clear issues, 0.9+ for certain issues.

2. For candidates with `needs_verification: false`:
   - Include them directly in findings — they were confirmed from the diff alone.
   - Still add a concrete `suggestion` code block if possible.
   - Set confidence based on the category: code_style/docs 0.85+, verified issues 0.7+.

3. Produce the final `findings.json` output.

## Turn Budget

You have a limited turn budget. Reserve the last 2 turns for output.
- Prioritize HIGH-risk files first (shown in risk classification table if available).
- Group tool calls by file — read one file, verify all candidates in it, then move to the next.
- Do not re-read files you've already read.

## Output Format

Output findings.json in a ```json code fence. The schema matches the standard findings format:

```json
{
  "pr_id": "$PR_ID",
  "repo": "$REPO",
  "vcs": "$VCS",
  "review_modes": ["standard", "security", "architecture", "performance", "migration"],
  "summary": "4-8 sentence summary of the PR changes and assessment.",
  "findings": [
    {
      "id": "cr-001",
      "file": "src/auth/Login.cs",
      "line": 42,
      "severity": "critical",
      "category": "security",
      "title": "SQL injection in login query",
      "message": "Full description with context from file verification.",
      "confidence": 0.92,
      "suggestion": "Use parameterized query:\n```csharp\nvar cmd = new SqlCommand(\"SELECT * FROM Users WHERE Username = @user\", conn);\ncmd.Parameters.AddWithValue(\"@user\", username);\n```"
    }
  ],
  "files_clean": ["src/utils/DateHelper.cs"],
  "fix_verifications": [],
  "tool_calls": 0,
  "agent": "openai-api"
}
```

## Rules

- Every candidate must result in either a finding in `findings[]` or be dropped (with the file appearing in `files_clean[]` if no other findings remain for it).
- Dropped candidates are false positives — the full file context showed the issue doesn't exist.
- All findings MUST include a concrete `suggestion` with a copy-pasteable code fix.
- Confidence must be 0.0-1.0. Findings below 0.7 will be filtered out by post-processing.
- Max 30 findings total, max 5 per file.
- IDs must be sequential: cr-001, cr-002, etc.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestVerifyPromptTemplate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add commands/review-verify.md tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add Pass 2 verify prompt template"
```

---

## Task 6: Candidate Parsing (`_parse_candidates`)

**Files:**
- Modify: `src/review_job.py`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write tests for candidate parsing**

Add to `tests/unit/test_two_pass_review.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestParseCandidates -v`
Expected: FAIL with `AttributeError: 'ReviewJob' object has no attribute '_parse_candidates'`

- [ ] **Step 3: Implement _parse_candidates()**

Add to `ReviewJob` class in `src/review_job.py`, after the `_build_config_section` method:

```python
    def _parse_candidates(self, raw_text: str) -> tuple[list, list[str]]:
        """Parse Pass 1 output into ScanCandidate list and files_clean list.

        Raises ValueError if JSON is malformed or missing required keys.
        """
        from agents.openai_runner import _extract_findings_json
        from models.review_models import ScanCandidate

        data = _extract_findings_json(raw_text)
        if data is None:
            raise ValueError(f"Failed to parse candidate JSON from Pass 1 output")

        if "candidates" not in data:
            raise ValueError("Missing 'candidates' key in Pass 1 output")

        candidates = []
        for c in data["candidates"]:
            candidates.append(ScanCandidate(
                file=c["file"],
                line=c.get("line", 0),
                category=c.get("category", "best_practices"),
                severity=c.get("severity", "suggestion"),
                title=c.get("title", ""),
                message=c.get("message", ""),
                needs_verification=c.get("needs_verification", False),
                verification_hint=c.get("verification_hint"),
                checklist_source=c.get("checklist_source"),
            ))

        files_clean = data.get("files_clean", [])
        return candidates, files_clean
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestParseCandidates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/review_job.py tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add _parse_candidates for Pass 1 output parsing"
```

---

## Task 7: Prompt Builders (`_build_scan_prompt`, `_build_verify_prompt`)

**Files:**
- Modify: `src/review_job.py`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write tests for prompt builders**

Add to `tests/unit/test_two_pass_review.py`:

```python
class _FakeFileChange:
    def __init__(self, path, additions=10, deletions=5, change_type="edit"):
        self.path = path
        self.additions = additions
        self.deletions = deletions
        self.change_type = change_type


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
        commands_dir = Path(__file__).parent.parent.parent / "commands"
        config = ReviewJobConfig(
            pr_id=123, repo="test-repo", workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        prompt = job._build_scan_prompt([], {}, {})
        # Should include inlined checklists
        assert "Review Mode" in prompt or "checklist" in prompt.lower()

    def test_scan_prompt_includes_lang_rules_when_available(self):
        from review_job import ReviewJob, ReviewJobConfig
        config = ReviewJobConfig(
            pr_id=123, repo="test-repo",
            workspace=Path("/tmp/test"),
            prompt_text="test prompt",
        )
        job = ReviewJob(config, settings=MagicMock())

        prompt = job._build_scan_prompt([], {}, {})
        # Should attempt to include lang rules (may be empty if no config files in /tmp)
        assert isinstance(prompt, str)


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
        # Helper.cs and Other.cs have no candidates — their diffs should NOT be in the verify prompt
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
        # Should include scoring reference
        assert "confidence" in prompt.lower() or "severity" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestBuildScanPrompt -v`
Expected: FAIL with `AttributeError: 'ReviewJob' object has no attribute '_build_scan_prompt'`

- [ ] **Step 3: Implement _build_scan_prompt()**

Add to `ReviewJob` class in `src/review_job.py`:

```python
    def _build_scan_prompt(
        self,
        changed_files,
        diffs: dict[str, str],
        analysis: dict,
        failed_diffs: list[str] | None = None,
    ) -> str:
        """Build the user prompt for Pass 1 (scan) — diffs + checklists + risk context."""
        commands_dir = Path(__file__).resolve().parent.parent / "commands"
        scan_template = commands_dir / "review-scan.md"

        lines = []
        if scan_template.is_file():
            lines.append(scan_template.read_text(encoding="utf-8"))
        else:
            lines.append("Identify candidate findings from the diffs below. Output JSON with candidates[] and files_clean[].")

        lines.append("")

        # Review mode checklists (same as single-pass — agent needs these to apply)
        lines.append(self._build_review_modes_section())

        # Lang-specific rules
        lines.append(self._build_config_section())

        # Risk context
        if analysis:
            lines.append("## Risk Context")
            lines.append("")
            lines.append(f"Risk score: {analysis.get('risk_score', 0)}")
            priorities = analysis.get("review_priorities", [])
            if priorities:
                lines.append("Review priorities:")
                for p in priorities[:10]:
                    lines.append(f"- `{p['name']}` in `{p['file']}`")
            test_gaps = analysis.get("test_gaps", [])
            if test_gaps:
                lines.append("Test gaps (no test coverage):")
                for tg in test_gaps[:10]:
                    lines.append(f"- `{tg['name']}` in `{tg['file']}`")
            lines.append("")

        # Diffs
        if diffs:
            lines.append("## File Diffs")
            lines.append("")
            for fp, diff_text in diffs.items():
                lines.append(f"### `{fp}`")
                if diff_text.startswith("[DIFF SUMMARY]"):
                    lines.append(diff_text)
                else:
                    lines.append(f"```diff\n{diff_text}\n```")
                lines.append("")

        if failed_diffs:
            lines.append("## Files With Failed Diff Fetch")
            lines.append("Mark these as `needs_verification: true` with hint 'Diff could not be pre-fetched':")
            for fp in failed_diffs:
                lines.append(f"- `{fp}`")
            lines.append("")

        return "\n".join(lines)
```

- [ ] **Step 4: Implement _build_verify_prompt()**

Add to `ReviewJob` class in `src/review_job.py`:

```python
    def _build_verify_prompt(
        self,
        candidates: list,
        diffs: dict[str, str],
        all_file_paths: list[str],
    ) -> str:
        """Build the user prompt for Pass 2 (verify) — candidates + relevant diffs + scoring rules."""
        commands_dir = Path(__file__).resolve().parent.parent / "commands"
        verify_template = commands_dir / "review-verify.md"

        lines = []
        if verify_template.is_file():
            lines.append(verify_template.read_text(encoding="utf-8"))
        else:
            lines.append("Verify the candidate findings below. Output findings.json.")

        lines.append("")

        # Token substitution
        lines.append(f"PR ID: {self.config.pr_id}")
        lines.append(f"Repository: {self.config.repo}")
        lines.append(f"VCS: {self.config.vcs}")
        lines.append("")

        # Candidate findings table
        lines.append("## Candidate Findings to Verify")
        lines.append("")
        if candidates:
            lines.append("| # | File | Line | Category | Severity | Title | Needs Verification | Hint |")
            lines.append("|---|------|------|----------|----------|-------|--------------------|------|")
            for i, c in enumerate(candidates, 1):
                hint = c.verification_hint or "—"
                lines.append(
                    f"| {i} | `{c.file}` | {c.line} | {c.category} | {c.severity} | {c.title} | {c.needs_verification} | {hint} |"
                )
            lines.append("")
            lines.append("### Candidate Details")
            lines.append("")
            for i, c in enumerate(candidates, 1):
                lines.append(f"**Candidate {i}: {c.title}**")
                lines.append(f"- File: `{c.file}`, line {c.line}")
                lines.append(f"- Category: {c.category}, severity: {c.severity}")
                lines.append(f"- Message: {c.message}")
                if c.needs_verification and c.verification_hint:
                    lines.append(f"- **Verification needed:** {c.verification_hint}")
                lines.append("")
        else:
            lines.append("No candidates to verify. Produce empty findings.")
            lines.append("")

        # Files clean from Pass 1 (informational — carry forward)
        clean_files_from_candidates = set(all_file_paths) - {c.file for c in candidates}
        if clean_files_from_candidates:
            lines.append("## Files Already Clean (from Pass 1)")
            lines.append("These files had no candidates. Include them in `files_clean[]`:")
            for fp in sorted(clean_files_from_candidates):
                lines.append(f"- `{fp}`")
            lines.append("")

        # Relevant diffs only (files with candidates)
        candidate_files = {c.file for c in candidates}
        relevant_diffs = {fp: d for fp, d in diffs.items() if fp in candidate_files}
        if relevant_diffs:
            lines.append("## Diffs (candidate files only)")
            lines.append("")
            for fp, diff_text in relevant_diffs.items():
                lines.append(f"### `{fp}`")
                if diff_text.startswith("[DIFF SUMMARY]"):
                    lines.append(diff_text)
                else:
                    lines.append(f"```diff\n{diff_text}\n```")
                lines.append("")

        # Scoring rules
        scoring_path = commands_dir / "scoring.md"
        if scoring_path.is_file():
            try:
                scoring_content = scoring_path.read_text(encoding="utf-8")
                lines.append("## Scoring Reference")
                lines.append("")
                lines.append(scoring_content)
                lines.append("")
            except Exception:
                pass

        return "\n".join(lines)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestBuildScanPrompt tests/unit/test_two_pass_review.py::TestBuildVerifyPrompt -v`
Expected: PASS

- [ ] **Step 6: Commit**

```
git add src/review_job.py tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add _build_scan_prompt and _build_verify_prompt"
```

---

## Task 8: Pass Runners (`_run_scan_pass`, `_run_verify_pass`)

**Files:**
- Modify: `src/review_job.py`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write tests for _run_scan_pass**

Add to `tests/unit/test_two_pass_review.py`:

```python
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
```

- [ ] **Step 2: Write tests for _run_verify_pass**

Add to `tests/unit/test_two_pass_review.py`:

```python
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

        # Verify it was called with max_turns=10 and use_sliding_window=False
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
        mock_result.findings_data = None  # Pass 2 failed to produce findings
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

        # Store candidates for fallback
        job._scan_candidates = [
            ScanCandidate(
                file="a.cs", line=10, category="security", severity="warning",
                title="Issue", message="desc",
            ),
        ]
        job._scan_files_clean = ["b.cs"]

        result = job._run_verify_pass("verify prompt")

        # Should return fallback findings from Pass 1 candidates
        assert result.findings_data is not None
        assert len(result.findings_data["findings"]) == 1
        assert result.findings_data["findings"][0]["file"] == "a.cs"
        assert result.findings_data["files_clean"] == ["b.cs"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestRunScanPass tests/unit/test_two_pass_review.py::TestRunVerifyPass -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 4: Implement _run_scan_pass()**

Add to `ReviewJob` class in `src/review_job.py`:

```python
    def _run_scan_pass(self, scan_prompt: str) -> tuple[list, list[str], AgentResult]:
        """Run Pass 1 (scan) — single-turn API call to identify candidates.

        Returns (candidates, files_clean, agent_result).
        Raises ValueError if all retries exhausted.
        """
        scan_system_prompt = (
            "You are a code review scanner. Identify candidate findings from pre-injected diffs. "
            "Output structured JSON with candidates[] and files_clean[]. "
            "You have NO tools available — work only from the diffs provided."
        )

        runner = OpenAIAgentRunner(
            settings=self.settings,
            workspace=self.config.workspace,
            model=self.config.model,
            pr_id=self.config.pr_id,
            repo=self.config.repo,
        )

        max_retries = getattr(self.settings, "scan_pass_max_retries", 1)
        last_error = None

        for attempt in range(1 + max_retries):
            prompt = scan_prompt
            if attempt > 0:
                prompt += (
                    "\n\n---\n\n**Your previous response was not valid JSON. "
                    "You MUST output a single JSON object in a ```json code fence "
                    "with keys 'candidates' (array) and 'files_clean' (array). Try again.**"
                )

            result = runner.run_single_turn(scan_system_prompt, prompt)

            if result.returncode != 0:
                last_error = ValueError(f"Pass 1 API call failed (attempt {attempt + 1})")
                logger.warning("Pass 1 attempt %d failed: API error", attempt + 1)
                continue

            try:
                candidates, files_clean = self._parse_candidates(result.raw_final_message)
                logger.info(
                    "Pass 1 complete: %d candidates (%d need verification), %d clean files",
                    len(candidates),
                    sum(1 for c in candidates if c.needs_verification),
                    len(files_clean),
                )
                return candidates, files_clean, result
            except ValueError as e:
                last_error = e
                logger.warning("Pass 1 attempt %d: %s", attempt + 1, e)

        raise ValueError(f"Pass 1 failed after {1 + max_retries} attempts: {last_error}")
```

- [ ] **Step 5: Implement _run_verify_pass()**

Add to `ReviewJob` class in `src/review_job.py`:

```python
    def _run_verify_pass(self, verify_prompt: str) -> AgentResult:
        """Run Pass 2 (verify) — short agent loop with full history.

        If Pass 2 fails, falls back to using Pass 1 candidates as unverified findings.
        """
        verify_system_prompt = (
            "You are verifying candidate code review findings. "
            "For each candidate with needs_verification=true, use tool calls to check full file context. "
            "Confirm, refine, or drop each candidate. Add concrete suggestion code blocks. "
            f"You have {getattr(self.settings, 'verify_pass_max_turns', 10)} turns. "
            "Reserve the last 2 for output."
        )

        source_commit = self.config.source_commit_id
        target_commit = self.config.target_commit_id

        runner = OpenAIAgentRunner(
            settings=self.settings,
            workspace=self.config.workspace,
            model=self.config.model,
            pr_id=self.config.pr_id,
            repo=self.config.repo,
            graph_store=self.config.pre_built_graph,
            changed_files=[c.file for c in getattr(self, "_scan_candidates", [])],
            source_commit_id=source_commit,
            target_commit_id=target_commit,
        )

        max_turns = getattr(self.settings, "verify_pass_max_turns", 10)
        result = runner.run(verify_prompt, max_turns=max_turns, use_sliding_window=False)

        if result.findings_data is None and hasattr(self, "_scan_candidates"):
            logger.warning("Pass 2 failed to produce findings — using Pass 1 candidates as fallback")
            result.findings_data = self._candidates_to_findings()

        return result

    def _candidates_to_findings(self) -> dict:
        """Convert Pass 1 candidates to findings format as a fallback."""
        findings = []
        for i, c in enumerate(getattr(self, "_scan_candidates", []), 1):
            findings.append({
                "id": f"cr-{i:03d}",
                "file": c.file,
                "line": c.line,
                "severity": c.severity,
                "category": c.category,
                "title": c.title,
                "message": c.message + " (unverified — Pass 2 fallback)",
                "confidence": 0.6 if c.needs_verification else 0.75,
            })
        return {
            "pr_id": self.config.pr_id,
            "repo": self.config.repo,
            "vcs": self.config.vcs,
            "review_modes": ["standard"],
            "findings": findings,
            "files_clean": getattr(self, "_scan_files_clean", []),
            "fix_verifications": [],
        }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestRunScanPass tests/unit/test_two_pass_review.py::TestRunVerifyPass -v`
Expected: PASS

- [ ] **Step 7: Commit**

```
git add src/review_job.py tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): add _run_scan_pass and _run_verify_pass"
```

---

## Task 9: Wire Two-Pass into `create_findings()`

**Files:**
- Modify: `src/review_job.py:180-283`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write test for two-pass flow end-to-end**

Add to `tests/unit/test_two_pass_review.py`:

```python
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
        # Fallback single-pass was used (run() called with max_turns=40)
        mock_runner.run.assert_called_once()
        call_args = mock_runner.run.call_args
        assert call_args[1].get("max_turns", call_args[0][1] if len(call_args[0]) > 1 else None) == 40

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestCreateFindingsTwoPass -v`
Expected: FAIL (create_findings still uses single-pass)

- [ ] **Step 3: Modify create_findings() to use two-pass flow**

Replace the agent execution section of `create_findings()` in `src/review_job.py` (the block from the `runner = OpenAIAgentRunner(...)` line through `self._write_findings(...)`, approximately lines 259-281). The new logic:

```python
        two_pass_enabled = getattr(self.settings, "two_pass_enabled", True)

        if two_pass_enabled:
            try:
                self._agent_result = self._run_two_pass(
                    changed_files, changed_file_paths, diffs, failed_diffs, analysis, graph_store,
                    source_commit, target_commit,
                )
            except Exception as exc:
                logger.warning("Two-pass flow failed (%s) — falling back to single-pass", exc)
                self._agent_result = self._run_single_pass(
                    prompt, graph_store, changed_file_paths, source_commit, target_commit,
                )
        else:
            self._agent_result = self._run_single_pass(
                prompt, graph_store, changed_file_paths, source_commit, target_commit,
            )

        if not self._agent_result.findings_data:
            import warnings
            warnings.warn(
                "Agent did not produce extractable findings JSON; emergency findings were generated.",
                stacklevel=2,
            )

        self._stamp_usage(self._agent_result)
        self._write_findings(self._agent_result.findings_data)

        return self._findings_path
```

- [ ] **Step 4: Add _run_two_pass() and _run_single_pass() helper methods**

Add to `ReviewJob` class:

```python
    def _run_single_pass(
        self, prompt, graph_store, changed_file_paths, source_commit, target_commit,
    ) -> AgentResult:
        """Original single-pass agent loop (fallback path)."""
        runner = OpenAIAgentRunner(
            settings=self.settings,
            workspace=self.config.workspace,
            model=self.config.model,
            pr_id=self.config.pr_id,
            repo=self.config.repo,
            graph_store=graph_store,
            changed_files=changed_file_paths,
            source_commit_id=source_commit,
            target_commit_id=target_commit,
        )
        return runner.run(prompt, max_turns=self.config.max_turns)

    def _run_two_pass(
        self, changed_files, changed_file_paths, diffs, failed_diffs, analysis,
        graph_store, source_commit, target_commit,
    ) -> AgentResult:
        """Two-pass review: Pass 1 (scan) → Pass 2 (verify)."""
        # --- Pass 1: Scan ---
        scan_prompt = self._build_scan_prompt(changed_files, diffs, analysis, failed_diffs)
        candidates, files_clean, scan_result = self._run_scan_pass(scan_prompt)

        # Store for fallback use in _run_verify_pass
        self._scan_candidates = candidates
        self._scan_files_clean = files_clean

        # If no candidates, skip Pass 2
        if not candidates:
            logger.info("Pass 1 found no candidates — skipping Pass 2")
            result = AgentResult()
            result.model = scan_result.model if hasattr(scan_result, 'model') else self.config.model
            result.input_tokens = scan_result.input_tokens
            result.output_tokens = scan_result.output_tokens
            result.total_tokens = scan_result.total_tokens
            result.duration_seconds = scan_result.duration_seconds if hasattr(scan_result, 'duration_seconds') else 0
            result.findings_data = {
                "pr_id": self.config.pr_id,
                "repo": self.config.repo,
                "vcs": self.config.vcs,
                "review_modes": ["standard"],
                "findings": [],
                "files_clean": files_clean,
                "fix_verifications": [],
            }
            return result

        # --- Pass 2: Verify ---
        verify_prompt = self._build_verify_prompt(candidates, diffs, changed_file_paths)
        verify_result = self._run_verify_pass(verify_prompt)

        # Merge token usage from both passes
        verify_result.input_tokens += scan_result.input_tokens
        verify_result.output_tokens += scan_result.output_tokens
        verify_result.total_tokens += scan_result.total_tokens

        return verify_result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestCreateFindingsTwoPass -v`
Expected: PASS

- [ ] **Step 6: Run all unit tests to verify no regressions**

Run: `python -m pytest tests/unit/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```
git add src/review_job.py tests/unit/test_two_pass_review.py
git commit -m "feat(two-pass): wire two-pass flow into create_findings with fallback"
```

---

## Task 10: Token Usage Aggregation and Merge

**Files:**
- Modify: `src/review_job.py`
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Write test for _candidates_to_findings merge**

Add to `tests/unit/test_two_pass_review.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_two_pass_review.py::TestCandidatesToFindings tests/unit/test_two_pass_review.py::TestTokenUsageAggregation -v`
Expected: PASS (these methods were implemented in prior tasks)

- [ ] **Step 3: Commit**

```
git add tests/unit/test_two_pass_review.py
git commit -m "test(two-pass): add token usage aggregation and candidate merge tests"
```

---

## Task 11: Final Integration Test + Full Suite

**Files:**
- Test: `tests/unit/test_two_pass_review.py`

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest tests/unit/ -v --tb=short`
Expected: All tests PASS including all new two-pass tests

- [ ] **Step 2: Verify no regressions in existing tests**

Run: `python -m pytest tests/unit/test_review_job.py tests/unit/test_batch_review.py tests/unit/test_turn_budget.py -v`
Expected: All existing tests PASS (single-pass path unchanged)

- [ ] **Step 3: Count new tests**

Run: `python -m pytest tests/unit/test_two_pass_review.py -v --co`
Expected: At least 20 tests collected

- [ ] **Step 4: Final commit with all changes**

```
git add -A
git commit -m "feat(two-pass): complete two-pass review architecture

Pass 1 (scan): single-turn, no tools — identifies candidates from diffs
Pass 2 (verify): short agent loop, full history — verifies candidates with tools
Fallback: reverts to single-pass 40-turn loop on any failure
Config: two_pass_enabled, scan_pass_max_retries, verify_pass_max_turns"
```

---

## Verification Checklist

After all tasks complete, verify:

- [ ] `run_single_turn()` makes one API call with no tools registered
- [ ] `run()` accepts `use_sliding_window=False` and sends full tool history on Responses API
- [ ] Chat Completions path is unaffected by `use_sliding_window` flag
- [ ] `_build_scan_prompt()` includes diffs, review mode checklists, risk context, and lang-rules
- [ ] `_build_verify_prompt()` includes only diffs for candidate files (not all files)
- [ ] `_parse_candidates()` handles valid JSON, code-fenced JSON, empty candidates, and malformed input
- [ ] `create_findings()` runs two-pass when `two_pass_enabled=True`
- [ ] `create_findings()` falls back to single-pass when Pass 1 fails after retries
- [ ] `create_findings()` falls back to single-pass when `two_pass_enabled=False`
- [ ] Empty candidates from Pass 1 skip Pass 2 and produce empty findings with files_clean
- [ ] Token usage is aggregated across both passes
- [ ] Pass 2 uses `use_sliding_window=False` (full history)
- [ ] Pass 2 uses `verify_pass_max_turns` (default 10, not 40)
- [ ] All existing unit tests still pass (no regressions)
