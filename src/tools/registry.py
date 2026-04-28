"""
Tool registry — maps tool names to schemas and handler functions.

Each tool is a (schema, handler) pair. The agent runner iterates the registry
to build the OpenAI tools list and dispatches calls by name.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List


@dataclass
class Tool:
    name: str
    schema: dict
    handler: Callable[[dict], str]


class ToolRegistry:
    """Collects tools and exposes them for the agent runner."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def openai_definitions(self) -> List[dict]:
        return [
            {"type": "function", "function": {"name": t.name, **t.schema}}
            for t in self._tools.values()
        ]

    def dispatch(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            import json
            return json.dumps({"error": f"Unknown tool: {name}"})
        return tool.handler(args)
