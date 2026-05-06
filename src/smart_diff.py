"""
Smart diff summarization for large file diffs.

For diffs exceeding the threshold, returns a structured hunk summary
instead of raw text, allowing agents to drill into high-risk sections.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


# Regex matching unified diff hunk headers: @@ -start,count +start,count @@
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)"
)


@dataclass
class HunkInfo:
    """Summary of a single diff hunk."""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    context: str  # trailing context from the @@ header line
    added_lines: int
    removed_lines: int


@dataclass
class DiffSummary:
    """Summary of a file diff, either full or hunk-level."""
    file_path: str
    total_size_bytes: int
    hunks: List[HunkInfo] = field(default_factory=list)
    is_summarized: bool = False


def summarize_diff(diff_text: str, file_path: str, threshold_kb: int) -> DiffSummary:
    """
    Return a DiffSummary for the given diff text.

    If the diff is smaller than threshold_kb, returns is_summarized=False
    (the caller should use the raw diff). If larger, parses hunk headers
    and returns is_summarized=True with hunk statistics.

    Args:
        diff_text: Raw unified diff text
        file_path: Path of the file being diffed (for the summary)
        threshold_kb: Threshold in kilobytes; diffs above this are summarized

    Returns:
        DiffSummary with is_summarized=False for small diffs or
        populated hunks list for large diffs.
    """
    size_bytes = len(diff_text.encode("utf-8"))
    summary = DiffSummary(
        file_path=file_path,
        total_size_bytes=size_bytes,
    )

    if size_bytes <= threshold_kb * 1024:
        return summary  # is_summarized=False by default

    # Parse hunks from the diff
    summary.hunks = _parse_hunks(diff_text)
    summary.is_summarized = True
    return summary


def _parse_hunks(diff_text: str) -> List[HunkInfo]:
    """Parse @@ hunk headers from unified diff text and count add/remove lines."""
    hunks: List[HunkInfo] = []
    current_hunk: Optional[HunkInfo] = None

    for line in diff_text.splitlines():
        m = _HUNK_HEADER_RE.match(line)
        if m:
            if current_hunk is not None:
                hunks.append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            context = m.group(5).strip()
            current_hunk = HunkInfo(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                context=context,
                added_lines=0,
                removed_lines=0,
            )
        elif current_hunk is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current_hunk.added_lines += 1
            elif line.startswith("-") and not line.startswith("---"):
                current_hunk.removed_lines += 1

    if current_hunk is not None:
        hunks.append(current_hunk)

    return hunks


def format_summary_for_agent(summary: DiffSummary) -> str:
    """
    Format a DiffSummary as readable text for the review agent.

    Args:
        summary: DiffSummary (should have is_summarized=True)

    Returns:
        Human-readable string describing the diff structure.
    """
    lines = [
        f"[DIFF SUMMARY] {summary.file_path}",
        f"Total diff size: {summary.total_size_bytes / 1024:.1f} KB — too large to return in full.",
        f"Found {len(summary.hunks)} hunk(s). Use get_file_diff with start_line/end_line to drill in.",
        "",
    ]

    for i, hunk in enumerate(summary.hunks, start=1):
        ctx = f" — {hunk.context}" if hunk.context else ""
        lines.append(
            f"  Hunk {i}: lines {hunk.new_start}–{hunk.new_start + hunk.new_count - 1}"
            f" (+{hunk.added_lines} / -{hunk.removed_lines}){ctx}"
        )

    return "\n".join(lines)


def extract_hunks_in_range(diff_text: str, start_line: int, end_line: int) -> str:
    """
    Return only the hunks from diff_text whose new-file line range overlaps
    [start_line, end_line].

    Args:
        diff_text: Raw unified diff text
        start_line: Start of requested line range (1-indexed, new file)
        end_line: End of requested line range (1-indexed, new file)

    Returns:
        Filtered diff text containing only overlapping hunks, or empty string
        if no hunks overlap.
    """
    if not diff_text:
        return ""

    # Split into header lines (before first @@) and hunk blocks
    result_lines: List[str] = []
    header_lines: List[str] = []
    current_hunk_lines: List[str] = []
    current_hunk_info: Optional[HunkInfo] = None
    in_hunk = False

    def _flush_hunk():
        if current_hunk_info is None:
            return
        hunk_new_end = current_hunk_info.new_start + current_hunk_info.new_count - 1
        # Overlaps if ranges intersect
        if current_hunk_info.new_start <= end_line and hunk_new_end >= start_line:
            result_lines.extend(current_hunk_lines)

    for line in diff_text.splitlines(keepends=True):
        m = _HUNK_HEADER_RE.match(line)
        if m:
            _flush_hunk()
            current_hunk_lines = [line]
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            context = m.group(5).strip()
            current_hunk_info = HunkInfo(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                context=context,
                added_lines=0,
                removed_lines=0,
            )
            in_hunk = True
        elif in_hunk:
            current_hunk_lines.append(line)
        else:
            header_lines.append(line)

    _flush_hunk()

    if not result_lines:
        return ""

    return "".join(header_lines) + "".join(result_lines)
