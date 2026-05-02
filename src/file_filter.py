"""
File filtering utilities for batched PR review.

Provides functions to skip non-code files (docs, images, config files, etc.)
so review agents focus only on meaningful code changes.
"""

from pathlib import Path
from typing import List, Tuple


def parse_skip_extensions(csv: str) -> set:
    """
    Normalize a comma-separated extension list into a set of lowercase dotted extensions.

    Args:
        csv: Comma-separated string of extensions (e.g. ".md,json,.YAML, .lock")

    Returns:
        Set of normalized extensions with leading dot (e.g. {'.md', '.json', '.yaml', '.lock'})
    """
    if not csv or not csv.strip():
        return set()

    result = set()
    for ext in csv.split(","):
        ext = ext.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        result.add(ext)
    return result


def filter_changed_files(
    file_changes: List, skip_extensions: set
) -> Tuple[List, List]:
    """
    Split file changes into code files and skipped files.

    Deleted files are always skipped regardless of extension.
    Files whose extension matches skip_extensions are skipped.

    Args:
        file_changes: List of file change objects with .path (or ['path']) and .change_type
        skip_extensions: Set of normalized extensions to skip (e.g. {'.md', '.json'})

    Returns:
        Tuple of (code_files, skipped_files)
    """
    code_files = []
    skipped_files = []

    for fc in file_changes:
        # Support both object-style and dict-style access
        if isinstance(fc, dict):
            path = fc.get("path", fc.get("file_path", ""))
            change_type = fc.get("change_type", "")
        else:
            path = getattr(fc, "path", getattr(fc, "file_path", ""))
            change_type = getattr(fc, "change_type", "")

        # Always skip deleted files
        if change_type == "delete":
            skipped_files.append(fc)
            continue

        ext = Path(path).suffix.lower()
        if ext in skip_extensions:
            skipped_files.append(fc)
        else:
            code_files.append(fc)

    return code_files, skipped_files
