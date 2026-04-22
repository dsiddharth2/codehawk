"""
Review data models.

Dataclasses representing code review results, comments, findings, and scores.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field


# ============================================================================
# PR Analysis Models
# ============================================================================

@dataclass
class FileChange:
    """Represents a file change in a pull request."""
    path: str
    change_type: str  # 'add', 'edit', 'delete', 'rename'
    old_path: Optional[str] = None
    additions: int = 0
    deletions: int = 0
    changed_lines: List[tuple] = None

    def __post_init__(self):
        if self.changed_lines is None:
            self.changed_lines = []


@dataclass
class PullRequestDetails:
    """Represents pull request details."""
    pr_id: int
    title: str
    description: str
    source_branch: str
    target_branch: str
    author: str
    repository: str
    project: str
    organization: str
    file_changes: List[FileChange]
    total_additions: int = 0
    total_deletions: int = 0
    source_commit_id: Optional[str] = None
    target_commit_id: Optional[str] = None


# ============================================================================
# Activity Input/Output Models
# ============================================================================

@dataclass
class FetchPRDetailsInput:
    """Input for FetchPRDetailsActivity."""
    pr_id: int
    repository_id: Optional[str] = None


@dataclass
class FetchFileContentInput:
    """Input for FetchFileContentActivity."""
    file_path: str
    commit_id: str
    repository_id: Optional[str] = None


@dataclass
class ReviewPRInput:
    """Input for ReviewPRJob."""
    pr_id: int
    repository_id: Optional[str] = None
    specific_file: Optional[str] = None


# ============================================================================
# Review Models
# ============================================================================

@dataclass
class ReviewComment:
    """Represents a single review comment."""
    file_path: str
    line_number: Optional[int]
    severity: str  # 'critical', 'warning', 'suggestion', 'good'
    category: str  # 'security', 'performance', 'best_practices', 'code_style', 'documentation'
    message: str
    suggestion: Optional[str] = None
    confidence: float = 0.8

    title: Optional[str] = None
    type: Optional[str] = None
    context: Optional[str] = None
    root_cause: Optional[str] = None
    fix_suggestion: Optional[str] = None

    suggested_code: Optional[str] = None
    criticality: Optional[str] = None
    merged_from_comments: Optional[List[str]] = None
    line_range: Optional[tuple] = None

    language: Optional[str] = None
    explanation: Optional[str] = None
    references: Optional[List[str]] = None

    @property
    def confidence_score(self) -> float:
        return self.confidence

    @property
    def suggested_fix(self) -> Optional[str]:
        return self.suggested_code or self.suggestion

    def __post_init__(self):
        if self.references is None:
            self.references = []


@dataclass
class ReviewResult:
    """Represents the complete review result for a file or PR."""
    summary: str
    overall_assessment: str  # 'good', 'needs_attention', 'critical_issues'
    comments: List[ReviewComment]
    statistics: Dict[str, int]
    file_path: Optional[str] = None
    language: Optional[str] = None


@dataclass
class PRScore:
    """Represents the penalty-based scoring results for a PR (lower is better)."""
    total_penalty: float
    overall_stars: str
    category_penalties: Dict[str, float]
    category_stars: Dict[str, str]
    issues_by_severity: Dict[str, int]
    scoring_breakdown: List[str]
    quality_level: str  # "Perfect", "Excellent", "Good", "Needs Work", "Poor", "Critical"


@dataclass
class PRReviewJobResult:
    """Aggregated result of reviewing an entire PR."""
    pr_id: int
    pr_title: str
    pr_description: str
    source_branch: str
    target_branch: str
    author: str
    files_reviewed: int
    files_skipped: int
    files_failed: int
    total_comments: int
    file_results: List[ReviewResult]
    skipped_files: List[str]
    failed_files: List[Dict[str, str]]
    overall_summary: str
    statistics: Dict[str, int]
    score: Optional['PRScore'] = None


# ============================================================================
# Fix Verification Models (legacy — used by activities)
# ============================================================================

@dataclass
class ExistingCommentThread:
    """Represents an existing comment thread from Azure DevOps."""
    thread_id: int
    file_path: str
    line_number: int
    status: int  # 1=Active, 2=Fixed, 3=WontFix, 4=Closed
    comment_text: str
    created_date: str
    severity: Optional[str] = None
    category: Optional[str] = None
    message: Optional[str] = None
    confidence: float = 0.0
    cr_id: Optional[str] = None  # extracted <!-- cr-id: xxx --> marker


@dataclass
class CommentMatchResult:
    """Result of matching an old comment with current state."""
    old_comment: ExistingCommentThread
    is_fixed: bool
    match_confidence: float
    matching_new_issue: Optional[ReviewComment] = None
    reason: str = ""


@dataclass
class FixVerificationResult:
    """Result of fix verification process."""
    total_old_comments: int
    comments_fixed: int
    comments_still_present: int
    comments_new: int
    match_results: List[CommentMatchResult]
    old_score: Optional[PRScore] = None
    new_score: Optional[PRScore] = None
    score_delta: float = 0.0
    quality_improved: bool = False


@dataclass
class ScoreComparison:
    """Comparison between old and new scores."""
    old_penalty: float
    new_penalty: float
    delta: float
    old_stars: str
    new_stars: str
    old_quality: str
    new_quality: str
    improved: bool
    category_changes: Dict[str, Dict[str, float]]


# ============================================================================
# New v3 Models — findings.json schema
# ============================================================================

@dataclass
class Finding:
    """Represents a single code review finding from the agent."""
    id: str                          # cr-id, e.g. "cr-001"
    file: str                        # file path relative to repo root
    line: int                        # line number
    severity: str                    # "critical" | "warning" | "suggestion"
    category: str                    # "security" | "performance" | "best_practices" | "code_style" | "documentation"
    title: str                       # short summary
    message: str                     # full description
    confidence: float                # 0.0 – 1.0
    suggestion: Optional[str] = None  # optional fix hint


@dataclass
class FixVerification:
    """Represents a fix verification result for a prior finding."""
    cr_id: str                       # matches a Finding.id from prior review
    status: str                      # "fixed" | "still_present" | "not_relevant"
    reason: str                      # human-readable explanation


@dataclass
class FindingsFile:
    """Top-level structure of findings.json written by the agent."""
    pr_id: int
    repo: str
    vcs: str                         # "ado" | "github"
    review_modes: List[str]          # e.g. ["standard", "security"]
    findings: List[Finding] = field(default_factory=list)
    fix_verifications: List[FixVerification] = field(default_factory=list)
    tool_calls: int = 0
    agent: Optional[str] = None      # "codex" | "claude" | "gemini"
