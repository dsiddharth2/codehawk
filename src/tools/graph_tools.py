"""
Graph tools — AST-based structural analysis using code-review-graph.

Registered only when a graph_store is available (passed by OpenAIAgentRunner).
Each handler wraps in try/except and returns {"error": ...} on failure.
"""

import json
from pathlib import Path
from typing import Any, List

from tools.registry import Tool, ToolRegistry


def register_graph_tools(
    registry: ToolRegistry,
    workspace: Path,
    graph_store: Any,
    changed_files: List[str],
):
    # -- get_blast_radius -------------------------------------------------------

    def handle_get_blast_radius(args: dict) -> str:
        files = args["changed_files"]
        try:
            result = graph_store.get_impact_radius(files)
            impacted_nodes = result.get("impacted_nodes", [])
            impacted_functions = [
                {"name": n.name, "file": n.file_path, "kind": n.kind}
                for n in impacted_nodes
                if n.kind in ("Function", "Method")
            ]
            test_gaps = []
            for node in result.get("changed_nodes", []):
                if node.kind in ("Function", "Method") and not node.is_test:
                    tests = graph_store.get_transitive_tests(node.qualified_name)
                    if not tests:
                        test_gaps.append({"name": node.name, "file": node.file_path})
            return json.dumps({
                "impacted_files": list(result.get("impacted_files", [])),
                "impacted_functions": impacted_functions,
                "test_gaps": test_gaps,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    registry.register(Tool(
        name="get_blast_radius",
        schema={
            "description": (
                "Find all files, functions, and tests affected by a set of changed files. "
                "Returns impacted_files, impacted_functions, and test_gaps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of changed file paths (relative to workspace root)",
                    },
                },
                "required": ["changed_files"],
            },
        },
        handler=handle_get_blast_radius,
    ))

    # -- get_callers ------------------------------------------------------------

    def handle_get_callers(args: dict) -> str:
        function_name = args["function_name"]
        file_path = args.get("file_path")
        try:
            callers = []
            seen: set = set()

            if file_path:
                qn = f"{file_path.lstrip('/')}::{function_name}"
                for edge in graph_store.get_edges_by_target(qn):
                    if edge.kind == "CALLS" and edge.source_qualified not in seen:
                        seen.add(edge.source_qualified)
                        node = graph_store.get_node(edge.source_qualified)
                        if node:
                            callers.append({"name": node.name, "file": node.file_path, "line": node.line_start})

            for edge in graph_store.search_edges_by_target_name(function_name, kind="CALLS"):
                if edge.source_qualified not in seen:
                    seen.add(edge.source_qualified)
                    node = graph_store.get_node(edge.source_qualified)
                    if node:
                        callers.append({"name": node.name, "file": node.file_path, "line": node.line_start})

            return json.dumps({"callers": callers})
        except Exception as e:
            return json.dumps({"error": str(e)})

    registry.register(Tool(
        name="get_callers",
        schema={
            "description": (
                "Find all functions that call the specified function. "
                "More precise than text search — uses AST CALLS edges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function to find callers of",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "File path containing the function (optional, improves precision)",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum call-graph depth to traverse. Default 1.",
                    },
                },
                "required": ["function_name"],
            },
        },
        handler=handle_get_callers,
    ))

    # -- get_dependents ---------------------------------------------------------

    def handle_get_dependents(args: dict) -> str:
        file_path = args["file_path"]
        try:
            cleaned = file_path.lstrip("/")
            edges = graph_store.get_edges_by_target(cleaned)
            imports_edges = [e for e in edges if e.kind == "IMPORTS_FROM"]

            if not imports_edges:
                imports_edges = graph_store.search_edges_by_target_name(cleaned, kind="IMPORTS_FROM")

            by_file: dict = {}
            for edge in imports_edges:
                src_file = edge.file_path
                if src_file not in by_file:
                    by_file[src_file] = []
                by_file[src_file].append(edge.target_qualified)

            dependents = [
                {"file": f, "imports": list(set(targets))}
                for f, targets in by_file.items()
            ]
            return json.dumps({"dependents": dependents})
        except Exception as e:
            return json.dumps({"error": str(e)})

    registry.register(Tool(
        name="get_dependents",
        schema={
            "description": (
                "Find all files that import the specified module or file. "
                "Uses IMPORTS_FROM edges from the AST graph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path to find dependents for (relative to workspace root)",
                    },
                },
                "required": ["file_path"],
            },
        },
        handler=handle_get_dependents,
    ))

    # -- get_change_analysis ----------------------------------------------------

    def handle_get_change_analysis(args: dict) -> str:
        files = args["changed_files"]
        try:
            result = graph_store.get_impact_radius(files)
            changed_nodes = result.get("changed_nodes", [])
            impacted_nodes = result.get("impacted_nodes", [])

            non_test_impacted = [
                n for n in list(changed_nodes) + list(impacted_nodes)
                if n.kind in ("Function", "Method") and not n.is_test
            ]
            risk_score = min(1.0, len(non_test_impacted) / 20.0) if non_test_impacted else 0.0

            review_priorities = [
                {"name": n.name, "file": n.file_path, "kind": n.kind}
                for n in changed_nodes
                if n.kind in ("Function", "Method", "Class")
            ]

            test_gaps = []
            for node in changed_nodes:
                if node.kind in ("Function", "Method") and not node.is_test:
                    tests = graph_store.get_transitive_tests(node.qualified_name)
                    if not tests:
                        test_gaps.append({"name": node.name, "file": node.file_path})

            return json.dumps({
                "risk_score": round(risk_score, 2),
                "review_priorities": review_priorities,
                "test_gaps": test_gaps,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    registry.register(Tool(
        name="get_change_analysis",
        schema={
            "description": (
                "Analyze changed files for risk, review priorities, and test coverage gaps. "
                "Returns risk_score (0.0–1.0), review_priorities, and test_gaps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of changed file paths (relative to workspace root)",
                    },
                },
                "required": ["changed_files"],
            },
        },
        handler=handle_get_change_analysis,
    ))
