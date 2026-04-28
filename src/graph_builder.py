"""Best-effort AST graph builder wrapping code-review-graph.

Returns a GraphStore on success or None on any failure (missing package,
unsupported workspace, timeout). The rest of the pipeline always checks
for None before using the store.
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any, Optional

_GRAPH_BUILD_TIMEOUT = 30  # seconds


def build_graph(workspace: Path) -> Optional[Any]:
    """Build an AST graph for *workspace* and return an open GraphStore.

    Returns None if:
    - enable_graph is False
    - code-review-graph is not installed
    - the workspace is not a git repository
    - any exception occurs during the build
    - the build exceeds the timeout

    Caller is responsible for calling store.close() when done.
    """
    from config import get_settings
    settings = get_settings()
    if not settings.enable_graph:
        return None

    def _build() -> Optional[Any]:
        try:
            from code_review_graph.tools.build import build_or_update_graph
            from code_review_graph.graph import GraphStore
            from code_review_graph.incremental import get_db_path

            build_or_update_graph(
                full_rebuild=True,
                repo_root=str(workspace),
                postprocess="minimal",
            )
            db_path = get_db_path(Path(workspace))
            return GraphStore(db_path)
        except ImportError:
            print("  Graph build skipped: code-review-graph not installed.")
            return None
        except Exception as exc:
            print(f"  Graph build skipped: {exc}")
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_build)
        try:
            return future.result(timeout=_GRAPH_BUILD_TIMEOUT)
        except concurrent.futures.TimeoutError:
            print(f"  Graph build skipped: timed out after {_GRAPH_BUILD_TIMEOUT}s.")
            return None
