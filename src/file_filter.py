"""
File filtering utilities for batched PR review.

Provides functions to skip non-code files (docs, images, config files, etc.)
so review agents focus only on meaningful code changes.

Extension filtering uses two strategies (applied in order):
1. Registry lookup — any extension registered in commands/languages.yml is a code file
2. Blacklist fallback — if the registry is unavailable, skip_extensions CSV applies
"""

import logging
from pathlib import Path
from typing import List, Optional, Set, Tuple

logger = logging.getLogger("codehawk.file_filter")


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


def load_registered_extensions(languages_yml: Optional[Path] = None) -> Set[str]:
    """
    Load the set of code file extensions from commands/languages.yml.

    Returns an empty set (no filtering) if the file is missing or unparseable,
    so the caller falls back to the blacklist strategy.
    """
    if languages_yml is None:
        # Walk up from this file to find commands/languages.yml
        here = Path(__file__).resolve().parent
        for candidate in [here.parent / "commands" / "languages.yml", here / "commands" / "languages.yml"]:
            if candidate.is_file():
                languages_yml = candidate
                break

    if not languages_yml or not languages_yml.is_file():
        return set()

    try:
        import yaml  # type: ignore
        data = yaml.safe_load(languages_yml.read_text(encoding="utf-8"))
        extensions: Set[str] = set()
        for lang_cfg in (data or {}).get("languages", {}).values():
            for ext in lang_cfg.get("extensions", []):
                ext = str(ext).lower()
                if ext and not ext.startswith("."):
                    ext = "." + ext
                if ext:
                    extensions.add(ext)
        logger.debug("Loaded %d registered extensions from languages.yml", len(extensions))
        return extensions
    except Exception as exc:
        logger.debug("Failed to load languages.yml: %s — falling back to blacklist", exc)
        return set()


def filter_changed_files(
    file_changes: List, skip_extensions: set, languages_yml: Optional[Path] = None
) -> Tuple[List, List]:
    """
    Split file changes into code files and skipped files.

    Deleted files are always skipped regardless of extension.

    Filtering strategy:
    - If languages.yml is loadable, a file is a code file iff its extension is registered.
      Unregistered extensions are skipped with a debug log.
    - If languages.yml is unavailable, falls back to blacklist: files whose extension
      is in skip_extensions are skipped; everything else is kept.

    Args:
        file_changes: List of file change objects with .path (or ['path']) and .change_type
        skip_extensions: Set of normalized extensions to skip (blacklist fallback)
        languages_yml: Optional explicit path to commands/languages.yml

    Returns:
        Tuple of (code_files, skipped_files)
    """
    registered_exts = load_registered_extensions(languages_yml)
    use_registry = bool(registered_exts)

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

        if use_registry:
            if ext in registered_exts:
                code_files.append(fc)
            else:
                logger.debug("Skipping %s — extension '%s' not in language registry", path, ext)
                skipped_files.append(fc)
        else:
            if ext in skip_extensions:
                skipped_files.append(fc)
            else:
                code_files.append(fc)

    return code_files, skipped_files
