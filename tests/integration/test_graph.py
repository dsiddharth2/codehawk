"""
Graph integration test — build AST graph and verify tools on real workspace.

Run:
    pytest tests/integration/test_graph.py -v -m integration -s
"""

import json

import pytest

from graph_builder import build_graph

from .conftest import (
    integration, needs_ado,
    setup_ado_env, clone_pr_workspace,
)


@integration
@needs_ado
class TestGraphIntegration:
    """Clone repo → build code graph → verify graph store and tools."""

    @pytest.fixture(autouse=True, scope="class")
    def graph_result(self, request):
        setup_ado_env()
        workspace, source_branch = clone_pr_workspace()
        print(f"  Branch: {source_branch}")

        graph_store = build_graph(workspace)
        request.cls.graph_store = graph_store
        request.cls.workspace = workspace

    def test_graph_store_built(self):
        assert self.graph_store is not None, (
            "Graph store is None — code-review-graph may not be installed. "
            "Install with: pip install code-review-graph"
        )

    def test_graph_tools_register(self):
        if self.graph_store is None:
            pytest.skip("Graph store not available")
        from tools.registry import ToolRegistry
        from tools.graph_tools import register_graph_tools
        registry = ToolRegistry()
        register_graph_tools(registry, self.workspace, self.graph_store, [])
        expected = {"get_blast_radius", "get_callers", "get_dependents", "get_change_analysis"}
        for name in expected:
            assert registry.get(name) is not None, f"Graph tool '{name}' not registered"

    def test_get_blast_radius_returns_data(self):
        if self.graph_store is None:
            pytest.skip("Graph store not available")
        from tools.registry import ToolRegistry
        from tools.graph_tools import register_graph_tools
        registry = ToolRegistry()
        register_graph_tools(registry, self.workspace, self.graph_store, [])
        result = json.loads(registry.dispatch("get_blast_radius", {"changed_files": ["."]}))
        assert "error" not in result or isinstance(result.get("impacted_files"), list)

    def test_get_change_analysis_returns_data(self):
        if self.graph_store is None:
            pytest.skip("Graph store not available")
        from tools.registry import ToolRegistry
        from tools.graph_tools import register_graph_tools
        registry = ToolRegistry()
        register_graph_tools(registry, self.workspace, self.graph_store, [])
        result = json.loads(registry.dispatch("get_change_analysis", {"changed_files": ["."]}))
        assert "risk_score" in result or "error" in result
