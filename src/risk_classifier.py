"""
Risk classifier — assigns HIGH/MEDIUM/LOW risk to changed files before agent review.

Pure Python, zero LLM cost. Runs pre-review using data already available from
FetchPRDetailsActivity and _pre_compute_analysis.

All weights and thresholds are configurable via config.py fields so tuning
doesn't require code changes. Initial weights are heuristic; recalibrate after
20-30 production runs by correlating risk scores with actual false-positive rates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FileRisk:
    risk: str           # "HIGH" | "MEDIUM" | "LOW"
    score: float        # 0.0–1.0
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path sensitivity tables (applied to lowercase forward-slash paths)
# ---------------------------------------------------------------------------

_HIGH_PATH_PATTERNS = (
    "auth/", "crypto/", "permissions/", "security/",
    "middleware/", "controllers/", "api/",
)
_MEDIUM_PATH_PATTERNS = ("services/", "models/", "database/", "migrations/")
_LOW_PATH_PATTERNS = ("tests/", "docs/", "utils/", "helpers/", "constants/")

# Code file extensions carry higher inherent risk than config/markup files
_HIGH_RISK_EXTENSIONS = {
    ".cs", ".py", ".js", ".ts", ".java", ".go", ".rb", ".php",
    ".kt", ".swift", ".rs", ".cpp", ".cc", ".c",
}
_LOW_RISK_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".xml",
    ".css", ".scss", ".html", ".lock",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(value: float, max_val: float) -> float:
    if max_val <= 0:
        return 0.0
    return min(1.0, value / max_val)


def _path_sensitivity(file_path: str) -> tuple[float, str]:
    """Return (sensitivity_score 0.0-1.0, human-readable label)."""
    path_norm = file_path.lower().replace("\\", "/")
    for pattern in _HIGH_PATH_PATTERNS:
        if pattern in path_norm:
            return 1.0, f"high-sensitivity path ({pattern.rstrip('/')})"
    for pattern in _MEDIUM_PATH_PATTERNS:
        if pattern in path_norm:
            return 0.5, f"medium-sensitivity path ({pattern.rstrip('/')})"
    for pattern in _LOW_PATH_PATTERNS:
        if pattern in path_norm:
            return 0.0, f"low-sensitivity path ({pattern.rstrip('/')})"
    return 0.25, "standard path"


def _file_type_risk(file_path: str) -> float:
    if "." not in file_path.rsplit("/", 1)[-1]:
        return 0.5
    ext = "." + file_path.rsplit(".", 1)[-1].lower()
    if ext in _HIGH_RISK_EXTENSIONS:
        return 1.0
    if ext in _LOW_RISK_EXTENSIONS:
        return 0.0
    return 0.5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify(
    changed_files,
    graph_analysis: Optional[Dict] = None,
    high_threshold: float = 0.6,
    medium_threshold: float = 0.3,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, FileRisk]:
    """
    Classify each changed file's risk level.

    Args:
        changed_files: Iterable of FileChange objects or plain path strings.
        graph_analysis: Optional pre-computed analysis dict from _pre_compute_analysis.
            Used to derive caller_count and no_test_coverage per file.
            When absent, those components default to 0.0 (graceful degradation).
        high_threshold: Score >= this → HIGH risk  (default 0.6, configurable).
        medium_threshold: Score >= this → MEDIUM risk (default 0.3, configurable).
        weights: Component weight overrides.  Keys: lines_changed, path_sensitivity,
            new_file, no_test_coverage, caller_count, file_type.
            Missing keys use defaults: 0.25, 0.25, 0.15, 0.15, 0.10, 0.10.

    Returns:
        Dict mapping file_path → FileRisk.
    """
    default_weights = {
        "lines_changed":    0.25,
        "path_sensitivity": 0.25,
        "new_file":         0.15,
        "no_test_coverage": 0.15,
        "caller_count":     0.10,
        "file_type":        0.10,
    }
    if weights:
        default_weights.update(weights)
    w = default_weights

    # Derive per-file caller counts and coverage gaps from graph analysis
    caller_count_by_file: Dict[str, int] = {}
    no_coverage_files: set = set()
    if graph_analysis:
        for fn_info in graph_analysis.get("impacted_functions", []):
            fp = fn_info.get("file", "")
            if fp:
                caller_count_by_file[fp] = caller_count_by_file.get(fp, 0) + 1
        for tg in graph_analysis.get("test_gaps", []):
            fp = tg.get("file", "")
            if fp:
                no_coverage_files.add(fp)

    result: Dict[str, FileRisk] = {}

    for fc in changed_files:
        if hasattr(fc, "path"):
            file_path: str = fc.path
            additions: int = getattr(fc, "additions", 0)
            deletions: int = getattr(fc, "deletions", 0)
            change_type: str = getattr(fc, "change_type", "edit")
        else:
            file_path = str(fc)
            additions = 0
            deletions = 0
            change_type = "edit"

        lines_changed = additions + deletions
        reasons: List[str] = []

        lines_score = _normalize(lines_changed, max_val=500)
        if lines_changed >= 50:
            reasons.append(f"{lines_changed} lines changed")

        path_score, path_reason = _path_sensitivity(file_path)
        if path_score >= 0.5:
            reasons.append(path_reason)

        new_file_score = 1.0 if change_type == "add" else 0.0
        if change_type == "add":
            reasons.append("new file")

        no_test_score = 1.0 if file_path in no_coverage_files else 0.0
        if file_path in no_coverage_files:
            reasons.append("no test coverage")

        callers = caller_count_by_file.get(file_path, 0)
        caller_score = _normalize(callers, max_val=10)
        if callers > 0:
            reasons.append(f"{callers} caller(s)")

        type_score = _file_type_risk(file_path)

        score = round(
            w["lines_changed"]    * lines_score
            + w["path_sensitivity"] * path_score
            + w["new_file"]         * new_file_score
            + w["no_test_coverage"] * no_test_score
            + w["caller_count"]     * caller_score
            + w["file_type"]        * type_score,
            3,
        )

        if score >= high_threshold:
            risk = "HIGH"
        elif score >= medium_threshold:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        if not reasons:
            reasons.append("low change volume, standard path")

        result[file_path] = FileRisk(risk=risk, score=score, reasons=reasons)

    return result
