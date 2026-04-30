"""
OpenAI API Agent Runner — pure orchestration.

Supports two OpenAI API modes:
  - Chat Completions API (gpt-4o-mini, gpt-4.1, o3, etc.)
  - Responses API (gpt-5-codex, codex-mini-latest, etc.)

The runner auto-detects which API to use based on the model name.
Tools are registered via the ToolRegistry from src/tools/.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

from config import Settings, get_settings
from tools.registry import ToolRegistry
from tools.vcs_tools import register_vcs_tools
from tools.workspace_tools import register_workspace_tools


def build_system_prompt(max_turns: int, has_graph: bool) -> str:
    """Build the system prompt dynamically based on turn budget and graph availability."""
    graph_strategy = (
        """\
GRAPH-FIRST STRATEGY (graph tools are available):
- Your FIRST tool call MUST be `get_change_analysis`. Use its output to prioritize your review.
- Do NOT read files one-by-one. Use graph analysis to identify high-risk files, then read only those.
- Use `get_blast_radius` for T5 PRs (51+ files) to find cascading risks.
- Use `get_callers` / `get_dependents` for precise structural queries instead of `search_code`."""
        if has_graph
        else """\
NO GRAPH AVAILABLE — use diffs instead of full file reads:
- Use `get_file_diff` to review changes without reading entire files.
- Use `search_code` for structural queries."""
    )

    return f"""\
You are a code review agent. You have access to tools that let you fetch PR data, \
read files, search code, and run git blame.

TURN BUDGET: You have {max_turns} turns total. Reserve the last 3 for producing findings JSON. \
Do not waste turns on redundant tool calls.

{graph_strategy}

IMPORTANT tool mapping — use these tools instead of shell commands:
- Instead of `python vcs.py get-pr ...` → use the `get_pr` tool
- Instead of `python vcs.py get-file ...` → use the `get_file_content` tool
- Instead of `python vcs.py list-threads ...` → use the `list_threads` tool
- Instead of `rg <pattern>` → use the `search_code` tool
- Instead of `cat /workspace/<file>` → use the `read_local_file` tool
- Instead of `git blame <file>` → use the `git_blame` tool
- Instead of `git diff` between commits → use the `get_file_diff` tool
- To understand change impact → use `get_change_analysis` (risk scores + review priorities)
- To find blast radius of changes → use `get_blast_radius` (all affected files/functions/tests)
- Instead of `search_code("fn_name")` for callers → use `get_callers` (precise structural results)
- To find files importing a module → use `get_dependents`

Note: Graph tools are only available when the codebase graph was built successfully. \
If a graph tool returns an error, fall back to `search_code` or `read_local_file`.

When you have completed your review, output the findings JSON as your final message. \
Do NOT attempt to write files — just output the JSON directly in a ```json code fence. \
The harness will write findings.json for you.
"""


class AgentResult:
    """Result from an OpenAI agent run."""

    def __init__(self):
        self.findings_data: Optional[dict] = None
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.total_tokens: int = 0
        self.tool_calls_count: int = 0
        self.duration_seconds: float = 0.0
        self.model: str = ""
        self.turns: int = 0
        self.raw_final_message: str = ""
        self.returncode: int = 0


RESPONSES_API_MODELS = {"gpt-5-codex", "codex-mini-latest"}


class OpenAIAgentRunner:
    """Runs the code review agent via OpenAI API with function calling."""

    def __init__(
        self,
        settings: Settings,
        workspace: Path,
        model: str = "o3",
        pr_id: int = 0,
        repo: str = "",
        graph_store=None,
        changed_files=None,
    ):
        self.settings = settings
        self.workspace = Path(workspace)
        self.model = model
        self.pr_id = pr_id
        self.repo = repo
        self.has_graph = graph_store is not None

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        self.client = OpenAI(api_key=api_key)

        self.registry = ToolRegistry()
        register_vcs_tools(
            self.registry,
            settings=settings,
            default_pr_id=pr_id,
            default_repo=repo,
        )
        register_workspace_tools(self.registry, workspace=self.workspace)
        if graph_store is not None:
            from tools.graph_tools import register_graph_tools
            register_graph_tools(self.registry, self.workspace, graph_store, changed_files or [])

    @property
    def _use_responses_api(self) -> bool:
        return self.model in RESPONSES_API_MODELS

    def run(self, prompt: str, max_turns: int = 40) -> AgentResult:
        if self._use_responses_api:
            return self._run_responses(prompt, max_turns)
        return self._run_chat_completions(prompt, max_turns)

    # ------------------------------------------------------------------
    # Chat Completions API (gpt-4o-mini, gpt-4.1, o3, etc.)
    # ------------------------------------------------------------------

    def _run_chat_completions(self, prompt: str, max_turns: int = 40) -> AgentResult:
        result = AgentResult()
        result.model = self.model
        start_time = time.time()

        messages = [
            {"role": "system", "content": build_system_prompt(max_turns, self.has_graph)},
            {"role": "user", "content": prompt},
        ]

        tool_defs = self.registry.openai_definitions()

        print(f"\n  OpenAI API runner [Chat Completions]: model={self.model}, max_turns={max_turns}")
        print(f"  Prompt length: {len(prompt)} chars")
        print(f"  --- agent conversation below ---\n", flush=True)

        for turn in range(max_turns):
            result.turns = turn + 1

            if turn == max_turns - 3:
                deadline_msg = (
                    "DEADLINE: You have 3 turns remaining. You MUST output your findings JSON NOW. "
                    "Do not make any more tool calls. Produce the ```json findings block immediately "
                    "with whatever findings you have collected so far. Partial output is required."
                )
                messages.append({"role": "user", "content": deadline_msg})
                print(f"  [DEADLINE INJECTION: turn {turn + 1}, 3 turns remaining]", flush=True)

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tool_defs,
                )
            except Exception as e:
                print(f"\n  ERROR: API call failed: {e}")
                result.returncode = 1
                break

            if response.usage:
                result.input_tokens += response.usage.prompt_tokens
                result.output_tokens += response.usage.completion_tokens
                result.total_tokens += response.usage.total_tokens

            choice = response.choices[0]
            assistant_msg = choice.message

            print(f"\n{'='*80}", flush=True)
            print(f"  Turn {turn + 1}  |  finish_reason={choice.finish_reason}", flush=True)
            print(f"{'='*80}", flush=True)

            if assistant_msg.content:
                text = assistant_msg.content
                result.raw_final_message = text
                if choice.finish_reason == "stop" and len(text) > 3000:
                    print(f"\n  [Assistant response — {len(text)} chars, showing first 3000]\n", flush=True)
                    print(text[:3000], flush=True)
                    print(f"\n  ... [{len(text) - 3000} more chars — full JSON in findings.json]\n", flush=True)
                else:
                    print(f"\n{text}\n", flush=True)

            messages.append(assistant_msg.model_dump())

            if choice.finish_reason == "stop":
                print(f"  >>> Agent finished. <<<", flush=True)
                break

            if not assistant_msg.tool_calls:
                print(f"  No tool calls, stopping.", flush=True)
                break

            for tc in assistant_msg.tool_calls:
                result.tool_calls_count += 1
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                print(f"\n  >> Tool call: {fn_name}({_summarize_args(fn_args)})", flush=True)

                try:
                    tool_result = self.registry.dispatch(fn_name, fn_args)
                except Exception as e:
                    tool_result = json.dumps({"error": str(e), "type": type(e).__name__})
                    print(f"     ERROR: {e}", flush=True)

                preview = tool_result[:500]
                if len(tool_result) > 500:
                    preview += f"\n     ... [{len(tool_result) - 500} more chars]"
                print(f"     Result: {preview}", flush=True)

                if len(tool_result) > 30000:
                    tool_result = tool_result[:30000] + "\n... [truncated, too large]"

                remaining = max_turns - (turn + 1)
                tool_result += f"\n[Turn {turn + 1}/{max_turns} used. {remaining} remaining.]"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        result.duration_seconds = round(time.time() - start_time, 1)
        _print_run_summary(result)
        result.findings_data = _extract_findings_json(result.raw_final_message)
        if result.findings_data is None:
            print("  [Fallback] Scanning conversation history for partial findings...", flush=True)
            assistant_texts = [
                msg.get("content") or ""
                for msg in messages
                if isinstance(msg, dict) and msg.get("role") == "assistant"
            ]
            result.findings_data = _scan_history_for_findings(assistant_texts)
        if result.findings_data is None:
            print("  [Emergency] Synthesizing minimal findings — agent exhausted turn budget.", flush=True)
            result.findings_data = {
                "pr_id": self.pr_id,
                "repo": self.repo,
                "vcs": "ado",
                "review_modes": ["standard"],
                "findings": [],
                "fix_verifications": [],
                "error": "Agent exhausted turn budget without producing findings",
            }
        _print_findings_summary(result)
        return result

    # ------------------------------------------------------------------
    # Responses API (gpt-5-codex, codex-mini-latest, etc.)
    # ------------------------------------------------------------------

    def _run_responses(self, prompt: str, max_turns: int = 40) -> AgentResult:
        result = AgentResult()
        result.model = self.model
        start_time = time.time()

        tool_defs = self.registry.responses_definitions()

        print(f"\n  OpenAI API runner [Responses]: model={self.model}, max_turns={max_turns}")
        print(f"  Prompt length: {len(prompt)} chars")
        print(f"  --- agent conversation below ---\n", flush=True)

        input_items = [{"type": "message", "role": "user", "content": prompt}]
        previous_response_id = None
        all_assistant_texts: list[str] = []

        for turn in range(max_turns):
            result.turns = turn + 1

            if turn == max_turns - 3:
                deadline_msg = (
                    "DEADLINE: You have 3 turns remaining. You MUST output your findings JSON NOW. "
                    "Do not make any more tool calls. Produce the ```json findings block immediately "
                    "with whatever findings you have collected so far. Partial output is required."
                )
                input_items.append({"type": "message", "role": "user", "content": deadline_msg})
                print(f"  [DEADLINE INJECTION: turn {turn + 1}, 3 turns remaining]", flush=True)

            try:
                kwargs = {
                    "model": self.model,
                    "instructions": build_system_prompt(max_turns, self.has_graph),
                    "tools": tool_defs,
                }
                if previous_response_id:
                    kwargs["previous_response_id"] = previous_response_id
                    kwargs["input"] = input_items
                else:
                    kwargs["input"] = input_items

                response = self.client.responses.create(**kwargs)
            except Exception as e:
                print(f"\n  ERROR: API call failed: {e}")
                result.returncode = 1
                break

            previous_response_id = response.id

            if response.usage:
                result.input_tokens += response.usage.input_tokens
                result.output_tokens += response.usage.output_tokens
                result.total_tokens += response.usage.input_tokens + response.usage.output_tokens

            print(f"\n{'='*80}", flush=True)
            print(f"  Turn {turn + 1}  |  status={response.status}", flush=True)
            print(f"{'='*80}", flush=True)

            function_calls = []
            for item in response.output:
                if item.type == "message":
                    for content in item.content:
                        if hasattr(content, "text"):
                            text = content.text
                            result.raw_final_message = text
                            all_assistant_texts.append(text)
                            if len(text) > 3000:
                                print(f"\n  [Assistant response — {len(text)} chars, showing first 3000]\n", flush=True)
                                print(text[:3000], flush=True)
                                print(f"\n  ... [{len(text) - 3000} more chars — full JSON in findings.json]\n", flush=True)
                            else:
                                print(f"\n{text}\n", flush=True)
                elif item.type == "function_call":
                    function_calls.append(item)

            if not function_calls:
                print(f"  >>> Agent finished. <<<", flush=True)
                break

            input_items = []
            for fc in function_calls:
                result.tool_calls_count += 1
                fn_name = fc.name
                try:
                    fn_args = json.loads(fc.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                print(f"\n  >> Tool call: {fn_name}({_summarize_args(fn_args)})", flush=True)

                try:
                    tool_result = self.registry.dispatch(fn_name, fn_args)
                except Exception as e:
                    tool_result = json.dumps({"error": str(e), "type": type(e).__name__})
                    print(f"     ERROR: {e}", flush=True)

                preview = tool_result[:500]
                if len(tool_result) > 500:
                    preview += f"\n     ... [{len(tool_result) - 500} more chars]"
                print(f"     Result: {preview}", flush=True)

                if len(tool_result) > 30000:
                    tool_result = tool_result[:30000] + "\n... [truncated, too large]"

                remaining = max_turns - (turn + 1)
                tool_result += f"\n[Turn {turn + 1}/{max_turns} used. {remaining} remaining.]"

                input_items.append({
                    "type": "function_call_output",
                    "call_id": fc.call_id,
                    "output": tool_result,
                })

        result.duration_seconds = round(time.time() - start_time, 1)
        _print_run_summary(result)
        result.findings_data = _extract_findings_json(result.raw_final_message)
        if result.findings_data is None:
            print("  [Fallback] Scanning conversation history for partial findings...", flush=True)
            result.findings_data = _scan_history_for_findings(all_assistant_texts)
        if result.findings_data is None:
            print("  [Emergency] Synthesizing minimal findings — agent exhausted turn budget.", flush=True)
            result.findings_data = {
                "pr_id": self.pr_id,
                "repo": self.repo,
                "vcs": "ado",
                "review_modes": ["standard"],
                "findings": [],
                "fix_verifications": [],
                "error": "Agent exhausted turn budget without producing findings",
            }
        _print_findings_summary(result)
        return result


def _print_run_summary(result: AgentResult):
    print(f"\n  --- agent conversation above ---")
    print(f"  Turns: {result.turns}, Tool calls: {result.tool_calls_count}")
    print(f"  Tokens: {result.total_tokens:,} (in:{result.input_tokens:,} out:{result.output_tokens:,})")
    print(f"  Duration: {result.duration_seconds}s")


def _print_findings_summary(result: AgentResult):
    if result.findings_data:
        print(f"  Findings extracted: {len(result.findings_data.get('findings', []))} findings")
    else:
        print(f"  WARNING: Could not extract findings JSON from agent output")
        result.returncode = 1


def _summarize_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def _brace_balanced_extract(text: str, keyword: str) -> list[str]:
    """Return all brace-balanced substrings of text that start with '{' and contain keyword."""
    results = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] == "{":
            depth = 0
            j = i
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[i : j + 1]
                        if keyword in candidate:
                            results.append(candidate)
                        i = j + 1
                        break
                j += 1
            else:
                i += 1
        else:
            i += 1
    return results


def _scan_history_for_findings(texts: list[str]) -> Optional[dict]:
    """Scan a list of assistant message texts for JSON blocks containing findings data."""
    candidates = []
    for text in texts:
        if not text:
            continue
        # Look for json code fence blocks
        for m in re.finditer(r'```(?:json)?\s*\n(\{.*?\})\s*\n```', text, re.DOTALL):
            block = m.group(1)
            if '"findings"' in block or re.search(r'"cr-\w+', block):
                candidates.append(block)
        # Look for bare JSON objects with findings key (brace-balanced to handle nesting)
        candidates.extend(_brace_balanced_extract(text, '"findings"'))

    # Try candidates from last to first (prefer most recent), pick largest valid one
    best = None
    for block in reversed(candidates):
        try:
            data = json.loads(block)
            if best is None or len(block) > len(json.dumps(best)):
                best = data
        except (json.JSONDecodeError, ValueError):
            pass
    return best


def _extract_findings_json(text: str) -> Optional[dict]:
    if not text:
        return None

    match = re.search(r'```(?:json)?\s*\n(\{.*?\})\s*\n```', text, re.DOTALL)
    if match:
        candidate = match.group(1)
    else:
        match = re.search(r'(\{[^{}]*"pr_id".*\})', text, re.DOTALL)
        if match:
            candidate = match.group(1)
        else:
            candidate = text.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    try:
        return json.loads(candidate, strict=False)
    except (json.JSONDecodeError, ValueError):
        pass

    return None
