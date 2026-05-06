"""
Unit tests for src/smart_diff.py

Covers:
  - summarize_diff: small diff returns is_summarized=False, large diff parses
    hunk headers with correct add/remove counts
  - extract_hunks_in_range: returns only overlapping hunks, empty for
    non-overlapping range
  - format_summary_for_agent: output contains file path and hunk details
  - Edge cases: empty diff, diff without hunk headers
"""

import sys
from pathlib import Path

import pytest

_src = Path(__file__).parent.parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from smart_diff import (
    DiffSummary,
    HunkInfo,
    summarize_diff,
    format_summary_for_agent,
    extract_hunks_in_range,
)


# ---------------------------------------------------------------------------
# Sample diff fixtures
# ---------------------------------------------------------------------------

SMALL_DIFF = """\
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 def hello():
+    print("world")
     return 42
"""

LARGE_DIFF_TEMPLATE = """\
--- a/src/big.py
+++ b/src/big.py
@@ -1,5 +1,7 @@ def func_one():
 context line
+added line 1
+added line 2
-removed line 1
 context line
@@ -100,4 +102,3 @@ def func_two():
 another context
-removed line 2
+added line 3
 end context
"""


def _make_large_diff(size_kb: int = 50) -> str:
    """Produce a diff that exceeds the given KB threshold."""
    padding = "# " + "x" * 100 + "\n"
    repeats = (size_kb * 1024) // len(padding.encode()) + 1
    return LARGE_DIFF_TEMPLATE + padding * repeats


# ---------------------------------------------------------------------------
# Tests for summarize_diff
# ---------------------------------------------------------------------------

class TestSummarizeDiff:
    def test_small_diff_not_summarized(self):
        summary = summarize_diff(SMALL_DIFF, "src/main.py", threshold_kb=30)
        assert summary.is_summarized is False
        assert summary.file_path == "src/main.py"
        assert summary.total_size_bytes == len(SMALL_DIFF.encode("utf-8"))
        assert summary.hunks == []

    def test_large_diff_is_summarized(self):
        large = _make_large_diff(size_kb=50)
        summary = summarize_diff(large, "src/big.py", threshold_kb=10)
        assert summary.is_summarized is True
        assert len(summary.hunks) >= 2

    def test_large_diff_hunk_counts(self):
        large = _make_large_diff(size_kb=50)
        summary = summarize_diff(large, "src/big.py", threshold_kb=10)
        first = summary.hunks[0]
        # First hunk has 2 added lines and 1 removed line
        assert first.added_lines == 2
        assert first.removed_lines == 1
        assert first.new_start == 1

    def test_empty_diff_not_summarized(self):
        summary = summarize_diff("", "src/empty.py", threshold_kb=30)
        assert summary.is_summarized is False
        assert summary.total_size_bytes == 0
        assert summary.hunks == []

    def test_diff_exactly_at_threshold_not_summarized(self):
        # A diff that is exactly at the threshold (not over) should not be summarized
        threshold_kb = 1
        diff = "x" * (threshold_kb * 1024)
        summary = summarize_diff(diff, "file.py", threshold_kb=threshold_kb)
        assert summary.is_summarized is False

    def test_hunk_context_captured(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -10,3 +10,4 @@ def my_func():\n context\n+added\n"
        summary = summarize_diff(diff, "f.py", threshold_kb=0)
        assert summary.is_summarized is True
        assert len(summary.hunks) == 1
        assert summary.hunks[0].context == "def my_func():"


# ---------------------------------------------------------------------------
# Tests for extract_hunks_in_range
# ---------------------------------------------------------------------------

MULTI_HUNK_DIFF = """\
--- a/src/file.py
+++ b/src/file.py
@@ -1,5 +1,6 @@
 line 1
+added
 line 2
 line 3
 line 4
@@ -50,4 +51,3 @@
 line 50
-removed
 line 51
@@ -200,3 +200,4 @@
 line 200
+new line
 line 201
"""


class TestExtractHunksInRange:
    def test_overlapping_range_returns_hunk(self):
        result = extract_hunks_in_range(MULTI_HUNK_DIFF, start_line=1, end_line=10)
        assert "@@ -1,5 +1,6 @@" in result
        assert "@@ -50,4 +51,3 @@" not in result

    def test_non_overlapping_range_returns_empty(self):
        result = extract_hunks_in_range(MULTI_HUNK_DIFF, start_line=300, end_line=400)
        assert result == ""

    def test_middle_hunk_selected(self):
        result = extract_hunks_in_range(MULTI_HUNK_DIFF, start_line=50, end_line=55)
        assert "@@ -50,4 +51,3 @@" in result
        assert "@@ -1,5 +1,6 @@" not in result

    def test_empty_diff_returns_empty(self):
        assert extract_hunks_in_range("", start_line=1, end_line=100) == ""

    def test_all_hunks_in_broad_range(self):
        result = extract_hunks_in_range(MULTI_HUNK_DIFF, start_line=1, end_line=300)
        assert "@@ -1,5 +1,6 @@" in result
        assert "@@ -50,4 +51,3 @@" in result
        assert "@@ -200,3 +200,4 @@" in result


# ---------------------------------------------------------------------------
# Tests for format_summary_for_agent
# ---------------------------------------------------------------------------

class TestFormatSummaryForAgent:
    def _make_summary(self) -> DiffSummary:
        return DiffSummary(
            file_path="src/module.py",
            total_size_bytes=50 * 1024,
            is_summarized=True,
            hunks=[
                HunkInfo(old_start=1, old_count=5, new_start=1, new_count=6,
                         context="def foo():", added_lines=2, removed_lines=1),
                HunkInfo(old_start=100, old_count=3, new_start=101, new_count=3,
                         context="", added_lines=1, removed_lines=1),
            ],
        )

    def test_contains_file_path(self):
        output = format_summary_for_agent(self._make_summary())
        assert "src/module.py" in output

    def test_contains_hunk_count(self):
        output = format_summary_for_agent(self._make_summary())
        assert "2 hunk(s)" in output

    def test_contains_hunk_details(self):
        output = format_summary_for_agent(self._make_summary())
        assert "+2" in output
        assert "-1" in output

    def test_contains_drill_in_guidance(self):
        output = format_summary_for_agent(self._make_summary())
        assert "start_line" in output or "get_file_diff" in output

    def test_contains_size_info(self):
        output = format_summary_for_agent(self._make_summary())
        assert "KB" in output
