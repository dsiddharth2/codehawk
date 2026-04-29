"""
Unit tests for graph_builder.build_graph().

The code-review-graph package is mocked entirely so tests pass
without it installed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import graph_builder


def _mock_settings(enable_graph=True):
    s = MagicMock()
    s.enable_graph = enable_graph
    return s


def _crg_sys_modules(build_fn=None, db_path=None, store_instance=None):
    """Return a sys.modules patch dict for code_review_graph sub-packages."""
    mock_crg = MagicMock()
    if build_fn is not None:
        mock_crg.tools.build.build_or_update_graph = build_fn
    if db_path is not None:
        mock_crg.incremental.get_db_path = MagicMock(return_value=db_path)
    if store_instance is not None:
        mock_crg.graph.GraphStore = MagicMock(return_value=store_instance)
    return {
        "code_review_graph": mock_crg,
        "code_review_graph.tools": mock_crg.tools,
        "code_review_graph.tools.build": mock_crg.tools.build,
        "code_review_graph.graph": mock_crg.graph,
        "code_review_graph.incremental": mock_crg.incremental,
    }


class TestBuildGraph:
    def test_returns_none_when_enable_graph_is_false(self, mocker):
        mocker.patch("config.get_settings", return_value=_mock_settings(enable_graph=False))

        result = graph_builder.build_graph(Path("/workspace"))

        assert result is None

    def test_returns_none_when_package_not_installed(self, mocker):
        mocker.patch("config.get_settings", return_value=_mock_settings())

        # Remove code_review_graph from sys.modules so ImportError is raised
        cleaned = {
            k: v for k, v in sys.modules.items()
            if not k.startswith("code_review_graph")
        }
        with patch.dict(sys.modules, cleaned, clear=True):
            # Ensure code_review_graph is absent so the lazy import raises ImportError
            sys.modules.pop("code_review_graph", None)
            sys.modules.pop("code_review_graph.tools", None)
            sys.modules.pop("code_review_graph.tools.build", None)
            result = graph_builder.build_graph(Path("/workspace"))

        assert result is None

    def test_returns_none_when_build_raises_exception(self, mocker):
        mocker.patch("config.get_settings", return_value=_mock_settings())

        failing_build = MagicMock(side_effect=RuntimeError("git repo not found"))
        with patch.dict(sys.modules, _crg_sys_modules(build_fn=failing_build)):
            result = graph_builder.build_graph(Path("/workspace"))

        assert result is None

    def test_returns_store_on_success(self, mocker):
        mocker.patch("config.get_settings", return_value=_mock_settings())

        mock_store = MagicMock()
        db = Path("/workspace/.crg/graph.db")
        with patch.dict(sys.modules, _crg_sys_modules(
            build_fn=MagicMock(),
            db_path=db,
            store_instance=mock_store,
        )):
            result = graph_builder.build_graph(Path("/workspace"))

        assert result is mock_store

    def test_prints_diagnostic_on_failure(self, mocker, capsys):
        mocker.patch("config.get_settings", return_value=_mock_settings())

        failing_build = MagicMock(side_effect=RuntimeError("workspace parse error"))
        with patch.dict(sys.modules, _crg_sys_modules(build_fn=failing_build)):
            graph_builder.build_graph(Path("/workspace"))

        captured = capsys.readouterr()
        assert "Graph build skipped" in captured.out
