"""
Unit tests for src/batch_review_job.py — BatchReviewJob merge logic and batch splitting.

Covers:
  - _split_into_batches: round-robin produces balanced batches
  - _merge_results: re-sequences cr-ids correctly (cr-001, cr-002, ...)
  - _merge_results: dedup by (file, line, title) removes cross-batch duplicates
  - _merge_results: usage stats sum correctly across batches
  - _merge_results: empty batch_results produces clean output
  - _merge_results: review_modes unioned
  - _split_into_batches: single-batch for files <= batch_size
  - _split_into_batches: empty code files returns []
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

_src = Path(__file__).parent.parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from batch_review_job import BatchReviewJob


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeFileChange:
    path: str
    change_type: str = "edit"
    additions: int = 0
    deletions: int = 0


def _make_finding(file: str, line: int, title: str, severity: str = "warning") -> Dict[str, Any]:
    return {
        "id": "cr-999",  # will be re-sequenced
        "file": file,
        "line": line,
        "title": title,
        "severity": severity,
        "category": "best_practices",
        "message": "test finding",
        "confidence": 0.9,
        "suggestion": "fix it",
    }


def _make_batch_result(findings: List[Dict], input_tokens: int = 100,
                       output_tokens: int = 50, duration: float = 10.0,
                       model: str = "o3", review_mode: str = "",
                       summary: str = "") -> Dict[str, Any]:
    result = {
        "findings": findings,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_seconds": duration,
            "model": model,
        },
    }
    if review_mode:
        result["review_modes"] = [review_mode]
    if summary:
        result["summary"] = summary
    return result


# ---------------------------------------------------------------------------
# Tests for _split_into_batches
# ---------------------------------------------------------------------------

class TestSplitIntoBatches:
    def test_empty_files_returns_empty(self):
        assert BatchReviewJob._split_into_batches([], batch_size=25) == []

    def test_single_batch_for_small_set(self):
        files = [FakeFileChange(f"file{i}.py") for i in range(5)]
        batches = BatchReviewJob._split_into_batches(files, batch_size=25)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_two_batches_for_larger_set(self):
        files = [FakeFileChange(f"file{i}.py") for i in range(30)]
        batches = BatchReviewJob._split_into_batches(files, batch_size=25)
        # 30 files / 25 per batch = 2 batches
        assert len(batches) == 2

    def test_round_robin_produces_balanced_batches(self):
        # 10 files, batch_size=3 => ceil(10/3)=4 batches
        # Round-robin: batch 0 gets files 0,4,8; batch 1 gets 1,5,9; batch 2 gets 2,6; batch 3 gets 3,7
        files = [FakeFileChange(f"f{i}.py", additions=100 - i) for i in range(10)]
        batches = BatchReviewJob._split_into_batches(files, batch_size=3)
        assert len(batches) == 4
        sizes = [len(b) for b in batches]
        # Max difference between any two batch sizes should be at most 1 (balanced)
        assert max(sizes) - min(sizes) <= 1

    def test_sorted_by_churn_descending(self):
        # Highest churn file should be in batch 0 (first to be distributed)
        files = [
            FakeFileChange("low.py", additions=5, deletions=5),
            FakeFileChange("high.py", additions=100, deletions=50),
            FakeFileChange("mid.py", additions=20, deletions=10),
        ]
        batches = BatchReviewJob._split_into_batches(files, batch_size=2)
        # With 3 files and batch_size=2, we get 2 batches
        # Sorted by churn desc: high(150), mid(30), low(10)
        # Round-robin: batch0=[high, low], batch1=[mid]
        assert len(batches) == 2
        batch0_paths = {f.path for f in batches[0]}
        assert "high.py" in batch0_paths

    def test_all_files_in_one_batch_if_equal_to_batch_size(self):
        files = [FakeFileChange(f"f{i}.py") for i in range(25)]
        batches = BatchReviewJob._split_into_batches(files, batch_size=25)
        assert len(batches) == 1
        assert len(batches[0]) == 25


# ---------------------------------------------------------------------------
# Tests for _merge_results
# ---------------------------------------------------------------------------

class TestMergeResults:
    def test_empty_results_returns_clean_output(self):
        merged = BatchReviewJob._merge_results([])
        assert merged["findings"] == []
        assert merged["usage"]["input_tokens"] == 0
        assert merged["usage"]["output_tokens"] == 0
        assert merged["usage"]["total_tokens"] == 0

    def test_resequences_cr_ids(self):
        batch1 = _make_batch_result([
            _make_finding("a.py", 1, "Issue A"),
            _make_finding("b.py", 2, "Issue B"),
        ])
        batch2 = _make_batch_result([
            _make_finding("c.py", 3, "Issue C"),
        ])
        merged = BatchReviewJob._merge_results([batch1, batch2])
        ids = [f["id"] for f in merged["findings"]]
        assert ids == ["cr-001", "cr-002", "cr-003"]

    def test_dedup_by_file_line_title(self):
        # Same finding in two batches
        finding = _make_finding("src/app.py", 42, "SQL injection")
        batch1 = _make_batch_result([finding])
        batch2 = _make_batch_result([dict(finding)])  # identical copy
        merged = BatchReviewJob._merge_results([batch1, batch2])
        assert len(merged["findings"]) == 1
        assert merged["findings"][0]["id"] == "cr-001"

    def test_dedup_different_findings_not_removed(self):
        batch1 = _make_batch_result([_make_finding("a.py", 1, "Issue A")])
        batch2 = _make_batch_result([_make_finding("a.py", 2, "Issue B")])  # same file, different line
        merged = BatchReviewJob._merge_results([batch1, batch2])
        assert len(merged["findings"]) == 2

    def test_usage_stats_summed(self):
        batch1 = _make_batch_result([], input_tokens=1000, output_tokens=500, duration=30.0)
        batch2 = _make_batch_result([], input_tokens=2000, output_tokens=800, duration=45.0)
        merged = BatchReviewJob._merge_results([batch1, batch2])
        assert merged["usage"]["input_tokens"] == 3000
        assert merged["usage"]["output_tokens"] == 1300
        assert merged["usage"]["total_tokens"] == 4300
        assert merged["usage"]["duration_seconds"] == pytest.approx(75.0, abs=0.1)

    def test_model_taken_from_last_batch_with_model(self):
        batch1 = _make_batch_result([], model="o3")
        batch2 = _make_batch_result([], model="gpt-4o")
        merged = BatchReviewJob._merge_results([batch1, batch2])
        assert merged["usage"]["model"] == "gpt-4o"

    def test_review_modes_unioned(self):
        batch1 = _make_batch_result([], review_mode="security")
        batch2 = _make_batch_result([], review_mode="performance")
        batch3 = _make_batch_result([], review_mode="security")  # duplicate
        merged = BatchReviewJob._merge_results([batch1, batch2, batch3])
        assert "review_modes" in merged
        assert set(merged["review_modes"]) == {"security", "performance"}

    def test_no_review_modes_key_when_none_set(self):
        batch1 = _make_batch_result([])
        merged = BatchReviewJob._merge_results([batch1])
        assert "review_modes" not in merged

    def test_findings_from_multiple_batches_concatenated(self):
        batch1 = _make_batch_result([
            _make_finding("a.py", 1, "Issue A"),
            _make_finding("b.py", 2, "Issue B"),
        ])
        batch2 = _make_batch_result([
            _make_finding("c.py", 3, "Issue C"),
            _make_finding("d.py", 4, "Issue D"),
        ])
        batch3 = _make_batch_result([
            _make_finding("e.py", 5, "Issue E"),
        ])
        merged = BatchReviewJob._merge_results([batch1, batch2, batch3])
        assert len(merged["findings"]) == 5
        # All cr-ids are sequential
        for i, f in enumerate(merged["findings"], start=1):
            assert f["id"] == f"cr-{i:03d}"

    def test_summaries_merged_from_batches(self):
        batch1 = _make_batch_result([], summary="Batch 1 has security issues.")
        batch2 = _make_batch_result([], summary="Batch 2 refactors the auth module.")
        merged = BatchReviewJob._merge_results([batch1, batch2])
        assert "summary" in merged
        assert "Batch 1 has security issues." in merged["summary"]
        assert "Batch 2 refactors the auth module." in merged["summary"]

    def test_no_summary_when_batches_lack_summary(self):
        batch1 = _make_batch_result([])
        batch2 = _make_batch_result([])
        merged = BatchReviewJob._merge_results([batch1, batch2])
        assert "summary" not in merged

    def test_summary_skips_empty_batch_summaries(self):
        batch1 = _make_batch_result([], summary="Only this batch has a summary.")
        batch2 = _make_batch_result([])
        merged = BatchReviewJob._merge_results([batch1, batch2])
        assert merged["summary"] == "Only this batch has a summary."
