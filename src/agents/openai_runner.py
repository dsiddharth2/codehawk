"""
OpenAI API Agent Runner — pure orchestration.

Uses the OpenAI Chat Completions API with function calling to run the
code review agent. Tools are registered via the ToolRegistry from src/tools/.
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


SYSTEM_PROMPT = """\
You are a code review agent. You have access to tools that let you fetch PR data, \
read files, search code, and run git blame.

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


class OpenAIAgentRunner:
    """Runs the code review agent via OpenAI Chat Completions API with function calling."""

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

    def run(self, prompt: str, max_turns: int = 40) -> AgentResult:
        result = AgentResult()
        result.model = self.model
        start_time = time.time()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        tool_defs = self.registry.openai_definitions()

        print(f"\n  OpenAI API runner: model={self.model}, max_turns={max_turns}")
        print(f"  Prompt length: {len(prompt)} chars")
        print(f"  --- agent conversation below ---\n", flush=True)

        for turn in range(max_turns):
            result.turns = turn + 1

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

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        result.duration_seconds = round(time.time() - start_time, 1)

        print(f"\n  --- agent conversation above ---")
        print(f"  Turns: {result.turns}, Tool calls: {result.tool_calls_count}")
        print(f"  Tokens: {result.total_tokens:,} (in:{result.input_tokens:,} out:{result.output_tokens:,})")
        print(f"  Duration: {result.duration_seconds}s")

        result.findings_data = _extract_findings_json(result.raw_final_message)
        if result.findings_data:
            print(f"  Findings extracted: {len(result.findings_data.get('findings', []))} findings")
        else:
            print(f"  WARNING: Could not extract findings JSON from agent output")
            result.returncode = 1

        return result


def _summarize_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


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
