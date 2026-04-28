"""
Unit tests for graph_tools.register_graph_tools().

The graph_store is mocked entirely — no code-review-graph package required.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.graph_tools import register_graph_tools
from tools.registry import ToolRegistry


def _make_node(name, file_path, kind="Function", is_test=False, line_start=10, qualified_name=None):
    node = MagicMock()
    node.name = name
    node.file_path = file_path
    node.kind = kind
    node.is_test = is_test
    node.line_start = line_start
    node.qualified_name = qualified_name or f"{file_path}::{name}"
    return node


def _make_edge(kind, source_qualified, file_path=None, target_qualified=None):
    edge = MagicMock()
    edge.kind = kind
    edge.source_qualified = source_qualified
    edge.file_path = file_path or "src/caller.py"
    edge.target_qualified = target_qualified or source_qualified
    return edge


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

class TestRegisterGraphTools:
    def test_registers_four_tools(self):
        registry = ToolRegistry()
        store = MagicMock()
        register_graph_tools(registry, Path("/workspace"), store, [])
        assert len(registry._tools) == 4

    def test_tool_names_correct(self):
        registry = ToolRegistry()
        store = MagicMock()
        register_graph_tools(registry, Path("/workspace"), store, [])
        assert "get_blast_radius" in registry._tools
        assert "get_callers" in registry._tools
        assert "get_dependents" in registry._tools
        assert "get_change_analysis" in registry._tools


# ---------------------------------------------------------------------------
# get_blast_radius
# ---------------------------------------------------------------------------

class TestGetBlastRadius:
    def _registry_with_store(self, store):
        registry = ToolRegistry()
        register_graph_tools(registry, Path("/workspace"), store, [])
        return registry

    def test_returns_impacted_files(self):
        node = _make_node("do_thing", "src/a.py")
        store = MagicMock()
        store.get_impact_radius.return_value = {
            "impacted_files": ["src/b.py"],
            "impacted_nodes": [node],
            "changed_nodes": [],
        }
        store.get_transitive_tests.return_value = []

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_blast_radius", {"changed_files": ["src/a.py"]})
        result = json.loads(raw)

        assert "src/b.py" in result["impacted_files"]
        assert isinstance(result["impacted_functions"], list)
        assert isinstance(result["test_gaps"], list)

    def test_returns_error_on_store_exception(self):
        store = MagicMock()
        store.get_impact_radius.side_effect = RuntimeError("db error")

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_blast_radius", {"changed_files": ["src/a.py"]})
        result = json.loads(raw)

        assert "error" in result
        assert "db error" in result["error"]


# ---------------------------------------------------------------------------
# get_callers
# ---------------------------------------------------------------------------

class TestGetCallers:
    def _registry_with_store(self, store):
        registry = ToolRegistry()
        register_graph_tools(registry, Path("/workspace"), store, [])
        return registry

    def test_returns_callers_list(self):
        caller_node = _make_node("caller_fn", "src/caller.py", line_start=20)
        edge = _make_edge("CALLS", "src/caller.py::caller_fn")

        store = MagicMock()
        store.search_edges_by_target_name.return_value = [edge]
        store.get_node.return_value = caller_node

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_callers", {"function_name": "my_fn"})
        result = json.loads(raw)

        assert "callers" in result
        assert len(result["callers"]) == 1
        assert result["callers"][0]["name"] == "caller_fn"
        assert result["callers"][0]["file"] == "src/caller.py"

    def test_returns_empty_for_unknown_function(self):
        store = MagicMock()
        store.search_edges_by_target_name.return_value = []

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_callers", {"function_name": "nonexistent_fn"})
        result = json.loads(raw)

        assert result == {"callers": []}

    def test_error_on_store_failure(self):
        store = MagicMock()
        store.search_edges_by_target_name.side_effect = RuntimeError("index corrupt")

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_callers", {"function_name": "my_fn"})
        result = json.loads(raw)

        assert "error" in result


# ---------------------------------------------------------------------------
# get_dependents
# ---------------------------------------------------------------------------

class TestGetDependents:
    def _registry_with_store(self, store):
        registry = ToolRegistry()
        register_graph_tools(registry, Path("/workspace"), store, [])
        return registry

    def test_returns_dependent_files(self):
        edge = _make_edge("IMPORTS_FROM", "src/consumer.py::import", file_path="src/consumer.py")

        store = MagicMock()
        store.get_edges_by_target.return_value = [edge]

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_dependents", {"file_path": "src/utils.py"})
        result = json.loads(raw)

        assert "dependents" in result
        assert any(d["file"] == "src/consumer.py" for d in result["dependents"])

    def test_error_on_store_failure(self):
        store = MagicMock()
        store.get_edges_by_target.side_effect = RuntimeError("store closed")

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_dependents", {"file_path": "src/utils.py"})
        result = json.loads(raw)

        assert "error" in result


# ---------------------------------------------------------------------------
# get_change_analysis
# ---------------------------------------------------------------------------

class TestGetChangeAnalysis:
    def _registry_with_store(self, store):
        registry = ToolRegistry()
        register_graph_tools(registry, Path("/workspace"), store, [])
        return registry

    def test_returns_risk_and_priorities(self):
        changed_node = _make_node("process_data", "src/processor.py", kind="Function")
        store = MagicMock()
        store.get_impact_radius.return_value = {
            "changed_nodes": [changed_node],
            "impacted_nodes": [],
        }
        store.get_transitive_tests.return_value = []

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_change_analysis", {"changed_files": ["src/processor.py"]})
        result = json.loads(raw)

        assert "risk_score" in result
        assert 0.0 <= result["risk_score"] <= 1.0
        assert "review_priorities" in result
        assert "test_gaps" in result

    def test_error_on_store_failure(self):
        store = MagicMock()
        store.get_impact_radius.side_effect = RuntimeError("graph unavailable")

        registry = self._registry_with_store(store)
        raw = registry.dispatch("get_change_analysis", {"changed_files": ["src/a.py"]})
        result = json.loads(raw)

        assert "error" in result
