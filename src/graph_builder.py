"""Best-effort AST graph builder wrapping code-review-graph.

Returns a GraphStore on success or None on any failure (missing package,
unsupported workspace, timeout). The rest of the pipeline always checks
for None before using the store.
"""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("codehawk.graph")

# Timeout tiers based on number of changed files in the PR.
_TIMEOUT_TIERS = [
    # (max_files, timeout_seconds)
    (10, 30),    # T1/T2: small PR
    (25, 60),    # T3: medium PR
    (50, 120),   # T4: large PR
    (100, 600),  # T5: very large PR (51-100 files)
    (999999, 300),  # T5+: extremely large PR (graph may be incomplete)
]


def _timeout_for_file_count(file_count: int) -> int:
    for max_files, timeout in _TIMEOUT_TIERS:
        if file_count <= max_files:
            return timeout
    return 300


def build_graph(workspace: Path, changed_file_count: int = 0) -> Optional[Any]:
    """Build an AST graph for *workspace* and return an open GraphStore.

    Args:
        workspace: Path to the cloned repo.
        changed_file_count: Number of changed files in the PR.
            Used to scale the build timeout — large PRs get more time.

    Returns None if:
    - enable_graph is False
    - code-review-graph is not installed
    - the workspace is not a git repository
    - any exception occurs during the build
    - the build exceeds the timeout
    """
    from config import get_settings
    settings = get_settings()
    if not settings.enable_graph:
        logger.info("Graph disabled via settings")
        return None

    timeout = _timeout_for_file_count(changed_file_count)
    logger.info("Graph build starting: %d changed files, timeout=%ds", changed_file_count, timeout)

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
            logger.warning("Graph build skipped: code-review-graph not installed")
            return None
        except Exception as exc:
            logger.warning("Graph build skipped: %s", exc)
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_build)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("Graph build timed out after %ds", timeout)
            return None
