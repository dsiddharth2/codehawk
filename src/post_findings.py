"""
post_findings.py — Phase 2 engine for codehawk.

Reads a findings.json produced by the review agent, validates it against
findings-schema.json, filters/caps/deduplicates findings, scores the PR,
posts inline comments and a summary to the VCS, and outputs structured JSON
for CI gating.

Usage:
    python src/post_findings.py --findings /workspace/.cr/findings.json [--dry-run]
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, replace as dc_replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("codehawk.post_findings")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_PATH = Path(__file__).parent.parent / "commands" / "findings-schema.json"
MIN_CONFIDENCE = 0.5
MAX_TOTAL_FINDINGS = 50  # Default; overridden by settings.max_total_findings at runtime
MAX_PER_FILE = 5  # Default; overridden by settings.max_per_file_findings at runtime
CODEREVIEW_YML = ".codereview.yml"

# Cost per 1M tokens: (input, output) in USD.
# Sorted longest-prefix-first so "gpt-4.1-mini" matches before "gpt-4.1".
MODEL_COST_TABLE: Dict[str, Tuple[float, float]] = {
    "gpt-5-codex":           (2.00, 8.00),
    "gpt-4.1-mini":          (0.40, 1.60),
    "gpt-4.1-nano":          (0.10, 0.40),
    "gpt-4.1":               (2.00, 8.00),
    "gpt-4o-mini":           (0.15, 0.60),
    "gpt-4o":                (2.50, 10.00),
    "o4-mini":               (1.10, 4.40),
    "o3":                    (2.00, 8.00),
    "claude-opus-4":         (15.00, 75.00),
    "claude-sonnet-4":       (3.00, 15.00),
    "claude-haiku-3.5":      (0.80, 4.00),
    "gemini-2.5-pro":        (1.25, 10.00),
    "gemini-2.5-flash":      (0.15, 0.60),
    "gemini-2.0-flash":      (0.10, 0.40),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_cost(usage) -> Optional[Dict[str, Any]]:
    """Estimate USD cost from a Usage object using MODEL_COST_TABLE."""
    if usage is None or not usage.model:
        return None

    model = usage.model
    cost_per_m = None
    for prefix, rates in MODEL_COST_TABLE.items():
        if model == prefix or model.startswith(prefix + "-") or model.startswith(prefix + " "):
            cost_per_m = rates
            break

    if cost_per_m is None:
        return {"model": model, "input_cost_usd": None, "output_cost_usd": None, "total_cost_usd": None, "note": "unknown model"}

    input_cost = usage.input_tokens * cost_per_m[0] / 1_000_000
    output_cost = usage.output_tokens * cost_per_m[1] / 1_000_000
    total_cost = input_cost + output_cost
    return {
        "model": model,
        "input_cost_usd": round(input_cost, 4),
        "output_cost_usd": round(output_cost, 4),
        "total_cost_usd": round(total_cost, 4),
    }


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _gh_run_with_retry(cmd, max_retries: int = 3, base_delay: float = 1.0, **kwargs):
    """
    subprocess.run wrapper with exponential backoff on GitHub rate-limit errors.

    Retries on CalledProcessError when stderr indicates a rate limit (HTTP 429
    or secondary rate limit). All other errors are re-raised immediately.
    """
    import time

    last_exc: Optional[subprocess.CalledProcessError] = None
    for attempt in range(max_retries):
        try:
            return subprocess.run(cmd, **kwargs)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").lower()
            is_rate_limit = (
                "rate limit" in stderr
                or "429" in stderr
                or "secondary rate" in stderr
                or "api rate" in stderr
            )
            if is_rate_limit and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                _eprint(
                    f"GitHub rate limit hit; retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                last_exc = exc
            else:
                raise
    raise last_exc  # type: ignore[misc]


VALID_CATEGORIES = {
    "security", "performance", "best_practices", "code_style",
    "documentation", "testing", "architecture", "correctness", "error_handling"
}
CATEGORY_REMAP = {
    "reliability": "best_practices",
    "maintainability": "best_practices",
    "naming": "code_style",
    "formatting": "code_style",
}

VALID_SEVERITIES = {"critical", "warning", "suggestion"}
SEVERITY_REMAP = {
    "major": "warning",
    "minor": "suggestion",
    "error": "critical",
    "high": "critical",
    "medium": "warning",
    "low": "suggestion",
    "info": "suggestion",
    "blocker": "critical",
}


_VALID_FINDING_KEYS = {"id", "file", "line", "severity", "category", "title", "message", "confidence", "suggestion"}


def _normalize_findings(data: dict) -> None:
    """Defensive normalization — treat model output as untrusted input.

    Guarantees every finding has all required fields with correct types.
    Coerces, defaults, and logs rather than crashing.
    """
    if not isinstance(data.get("findings"), list):
        data["findings"] = []
    if not isinstance(data.get("fix_verifications"), list):
        data["fix_verifications"] = []

    valid_findings = []
    for i, f in enumerate(data["findings"]):
        if not isinstance(f, dict):
            logger.warning("findings[%d] is not a dict — skipping", i)
            continue

        f.setdefault("id", f"cr-{i + 1:03d}")
        f.setdefault("file", "unknown")
        f.setdefault("title", "")
        f.setdefault("message", f.get("title", ""))
        f.setdefault("severity", "warning")
        f.setdefault("category", "best_practices")
        f.setdefault("confidence", 0.7)

        if not f["title"] and f["message"]:
            f["title"] = (f["message"][:80] + "...") if len(f["message"]) > 80 else f["message"]

        if not isinstance(f.get("line"), (int, float)):
            try:
                f["line"] = int(f.get("line", 0))
            except (TypeError, ValueError):
                f["line"] = 0

        if not isinstance(f.get("confidence"), (int, float)):
            try:
                f["confidence"] = float(f.get("confidence", 0.7))
            except (TypeError, ValueError):
                f["confidence"] = 0.7
        f["confidence"] = max(0.0, min(1.0, float(f["confidence"])))

        cat = f["category"]
        if cat not in VALID_CATEGORIES:
            f["category"] = CATEGORY_REMAP.get(cat, "best_practices")

        sev = f["severity"]
        if sev not in VALID_SEVERITIES:
            original = sev
            f["severity"] = SEVERITY_REMAP.get(str(sev).lower(), "warning")
            logger.warning("Remapped invalid severity %r → %r for %s", original, f["severity"], f.get("id"))

        extra = set(f.keys()) - _VALID_FINDING_KEYS
        for k in extra:
            del f[k]

        valid_findings.append(f)

    data["findings"] = valid_findings

    valid_fv = []
    for i, fv in enumerate(data["fix_verifications"]):
        if not isinstance(fv, dict):
            continue
        fv.setdefault("cr_id", f"unknown-{i}")
        fv.setdefault("status", "not_relevant")
        fv.setdefault("reason", "")
        valid_fv.append(fv)
    data["fix_verifications"] = valid_fv


def _validate_schema(data: dict) -> List[str]:
    """
    Validate findings.json against findings-schema.json.

    Returns a list of error messages (empty list = valid).
    Uses jsonschema if available; otherwise falls back to manual required-field check.
    """
    try:
        import jsonschema
        schema = _load_json(str(SCHEMA_PATH))
        try:
            jsonschema.validate(data, schema)
            return []
        except jsonschema.ValidationError as exc:
            return [str(exc.message)]
    except ImportError:
        errors = []
        required = ["pr_id", "repo", "vcs", "review_modes", "findings"]
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: '{field}'")
        if "vcs" in data and data["vcs"] not in ("ado", "github"):
            errors.append(f"Invalid vcs value: '{data['vcs']}'. Must be 'ado' or 'github'.")
        if "findings" in data:
            for i, f in enumerate(data["findings"]):
                for req in ("id", "file", "line", "severity", "category", "title", "message", "confidence"):
                    if req not in f:
                        errors.append(f"findings[{i}] missing required field '{req}'")
        return errors


def _parse_findings_file(data: dict):
    """
    Parse raw dict into FindingsFile dataclass.
    """
    from models.review_models import Finding, FindingsFile, FixVerification, Usage

    findings = []
    for f in data.get("findings", []):
        try:
            findings.append(Finding(
                id=f.get("id", "cr-???"),
                file=f.get("file", "unknown"),
                line=int(f.get("line", 0)),
                severity=f.get("severity", "warning"),
                category=f.get("category", "best_practices"),
                title=f.get("title", ""),
                message=f.get("message", ""),
                confidence=float(f.get("confidence", 0.7)),
                suggestion=f.get("suggestion"),
            ))
        except Exception as exc:
            logger.warning("Skipping unparseable finding %s: %s", f.get("id", "?"), exc)

    fix_verifications = []
    for fv in data.get("fix_verifications", []):
        try:
            fix_verifications.append(FixVerification(
                cr_id=fv.get("cr_id", "unknown"),
                status=fv.get("status", "not_relevant"),
                reason=fv.get("reason", ""),
                severity=fv.get("severity"),
                category=fv.get("category"),
            ))
        except Exception as exc:
            logger.warning("Skipping unparseable fix_verification: %s", exc)

    usage = None
    raw_usage = data.get("usage")
    if raw_usage and isinstance(raw_usage, dict):
        try:
            usage = Usage(
                input_tokens=int(raw_usage.get("input_tokens", 0)),
                output_tokens=int(raw_usage.get("output_tokens", 0)),
                total_tokens=int(raw_usage.get("total_tokens", 0)),
                model=raw_usage.get("model"),
                duration_seconds=raw_usage.get("duration_seconds"),
            )
        except Exception as exc:
            logger.warning("Failed to parse usage data: %s", exc)

    return FindingsFile(
        pr_id=data.get("pr_id", 0),
        repo=data.get("repo", ""),
        vcs=data.get("vcs", "ado"),
        review_modes=data.get("review_modes", []),
        summary=data.get("summary"),
        findings=findings,
        files_clean=data.get("files_clean", []),
        fix_verifications=fix_verifications,
        tool_calls=data.get("tool_calls", 0),
        agent=data.get("agent"),
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Filtering and capping
# ---------------------------------------------------------------------------

def filter_by_confidence(findings, min_confidence: float = MIN_CONFIDENCE):
    """Drop findings below min_confidence threshold."""
    return [f for f in findings if f.confidence >= min_confidence]


def cap_findings(findings, max_total: int = MAX_TOTAL_FINDINGS, max_per_file: int = MAX_PER_FILE):
    """
    Cap findings to max_per_file per file (highest severity first) and
    max_total overall (highest severity first).

    Severity order: critical > warning > suggestion
    """
    severity_order = {"critical": 0, "warning": 1, "suggestion": 2}

    sorted_findings = sorted(findings, key=lambda f: (severity_order.get(f.severity, 9), f.file, f.line))

    per_file: Dict[str, int] = defaultdict(int)
    capped: list = []

    for f in sorted_findings:
        if per_file[f.file] >= max_per_file:
            continue
        if len(capped) >= max_total:
            break
        per_file[f.file] += 1
        capped.append(f)

    return capped


def merge_similar_findings(findings):
    """
    Merge near-duplicate findings on the same file and nearby lines.

    Merge criteria (ALL must match):
    - Same file path
    - Line numbers within +/- 5 of each other
    - Title + message similarity > 0.7 (difflib.SequenceMatcher)

    Keep the finding with higher severity. Append the merged finding's message
    as a footnote: "(Also flagged at line {n}: {merged_title})".
    """
    if not findings:
        return findings

    severity_order = {"critical": 0, "warning": 1, "suggestion": 2}

    def _similarity(a, b):
        text_a = f"{a.title} {a.message}"
        text_b = f"{b.title} {b.message}"
        return difflib.SequenceMatcher(None, text_a, text_b).ratio()

    by_file: Dict[str, list] = defaultdict(list)
    for f in findings:
        by_file[f.file].append(f)

    result = []
    for _, file_findings in by_file.items():
        sorted_findings = sorted(file_findings, key=lambda f: f.line)
        merged_flags = [False] * len(sorted_findings)

        for i, fi in enumerate(sorted_findings):
            if merged_flags[i]:
                continue
            current = fi
            anchor_line = fi.line
            for j in range(i + 1, len(sorted_findings)):
                if merged_flags[j]:
                    continue
                fj = sorted_findings[j]
                if abs(fj.line - anchor_line) > 5:
                    break  # sorted by line; no more candidates within range
                if _similarity(current, fj) > 0.7:
                    merged_flags[j] = True
                    fi_rank = severity_order.get(current.severity, 9)
                    fj_rank = severity_order.get(fj.severity, 9)
                    if fj_rank < fi_rank:
                        # fj has higher severity — make it the keeper
                        new_message = fj.message + f"\n\n*(Also flagged at line {current.line}: {current.title})*"
                        current = dc_replace(fj, message=new_message)
                    else:
                        # current has higher or equal severity — keep it
                        new_message = current.message + f"\n\n*(Also flagged at line {fj.line}: {fj.title})*"
                        current = dc_replace(current, message=new_message)
            result.append(current)

    return result


# ---------------------------------------------------------------------------
# .codereview.yml gate thresholds
# ---------------------------------------------------------------------------

def _load_codereview_yml(workspace: str) -> Dict[str, Any]:
    """
    Read .codereview.yml from workspace directory.

    Expected keys (all optional):
        min_star_rating: int  (1-5, default 3)
        fail_on_critical: bool  (default true)
    """
    path = Path(workspace) / CODEREVIEW_YML
    if not path.exists():
        return {}

    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        # Minimal YAML parser: only handle simple key: value lines
        config: Dict[str, Any] = {}
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if val.lower() == "true":
                        config[key] = True
                    elif val.lower() == "false":
                        config[key] = False
                    elif val.isdigit():
                        config[key] = int(val)
                    else:
                        try:
                            config[key] = float(val)
                        except ValueError:
                            config[key] = val
        return config
    except Exception as exc:
        _eprint(f"Warning: failed to parse .codereview.yml: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Fetching posted cr-ids from existing threads
# ---------------------------------------------------------------------------

def _fetch_posted_cr_ids_ado(pr_id: int, repo: str) -> Set[str]:
    """Fetch already-posted cr-ids from Azure DevOps threads."""
    from activities.fetch_pr_comments_activity import FetchPRCommentsActivity
    from config import get_settings

    try:
        settings = get_settings()
        activity = FetchPRCommentsActivity(settings=settings)
        threads = activity.execute(pr_id=pr_id, repository_id=repo or None)
        return {t.cr_id for t in threads if t.cr_id}
    except Exception as exc:
        _eprint(f"Warning: failed to fetch existing threads (will post all): {exc}")
        return set()


def _fetch_posted_cr_ids_github(pr_id: int, repo: str) -> Set[str]:
    """Fetch already-posted cr-ids from GitHub PR review comments."""
    import re

    try:
        result = _gh_run_with_retry(
            ["gh", "api", f"repos/{repo}/pulls/{pr_id}/comments", "--jq", ".[].body"],
            capture_output=True, text=True, check=True
        )
        cr_ids: Set[str] = set()
        for body in result.stdout.splitlines():
            match = re.search(r"<!--\s*cr-id:\s*(\S+)\s*-->", body)
            if match:
                cr_ids.add(match.group(1))
        return cr_ids
    except Exception as exc:
        _eprint(f"Warning: failed to fetch GitHub comments (will post all): {exc}")
        return set()


# ---------------------------------------------------------------------------
# Posting comments
# ---------------------------------------------------------------------------

def _post_inline_ado(finding, pr_id: int, repo: str, dry_run: bool) -> bool:
    """Post a single inline comment to Azure DevOps. Returns True on success."""
    if dry_run:
        return True

    from activities.post_pr_comment_activity import PostPRCommentActivity
    from config import get_settings

    severity_icons = {"critical": "🔴", "warning": "⚠️", "suggestion": "💡"}
    icon = severity_icons.get(finding.severity, "📝")
    body = (
        f"## {icon} {finding.severity.upper()}: {finding.category.replace('_', ' ').title()}\n\n"
        f"**{finding.title}**\n\n"
        f"{finding.message}"
    )
    if finding.suggestion:
        body += f"\n\n**Suggestion:** {finding.suggestion}"
    body += f"\n\n*Confidence: {int(finding.confidence * 100)}%*"

    settings = get_settings()
    activity = PostPRCommentActivity(settings=settings)
    try:
        activity._post_line_comment(
            pr_id=pr_id,
            repository_id=repo or settings.azure_devops_repo,
            project=settings.azure_devops_project,
            file_path=finding.file,
            line_number=finding.line,
            comment_text=body,
            severity=finding.severity,
            cr_id=finding.id,
        )
        return True
    except Exception as exc:
        _eprint(f"Warning: failed to post ADO comment for {finding.id}: {exc}")
        return False


def _post_inline_github(finding, pr_id: int, repo: str, commit_id: str, dry_run: bool) -> bool:
    """Post a single inline comment to GitHub via gh CLI. Returns True on success."""
    if dry_run:
        return True

    severity_icons = {"critical": "🔴", "warning": "⚠️", "suggestion": "💡"}
    icon = severity_icons.get(finding.severity, "📝")
    body = (
        f"## {icon} {finding.severity.upper()}: {finding.category.replace('_', ' ').title()}\n\n"
        f"**{finding.title}**\n\n"
        f"{finding.message}"
    )
    if finding.suggestion:
        body += f"\n\n**Suggestion:** {finding.suggestion}"
    body += f"\n\n*Confidence: {int(finding.confidence * 100)}%*"
    body += f"\n\n<!-- cr-id: {finding.id} -->"

    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": finding.file,
        "line": finding.line,
        "side": "RIGHT",
    }

    try:
        _gh_run_with_retry(
            ["gh", "api", f"repos/{repo}/pulls/{pr_id}/comments",
             "--method", "POST", "--input", "-"],
            input=json.dumps(payload),
            text=True, check=True, capture_output=True
        )
        return True
    except subprocess.CalledProcessError as exc:
        _eprint(f"Warning: failed to post GitHub comment for {finding.id}: {exc.stderr}")
        return False


# ---------------------------------------------------------------------------
# Fix verifications
# ---------------------------------------------------------------------------

def _handle_fix_verifications_ado(fix_verifications, pr_id: int, repo: str, dry_run: bool):
    """Resolve ADO threads for cr-ids classified as fixed."""
    if not fix_verifications or dry_run:
        return

    from activities.fetch_pr_comments_activity import FetchPRCommentsActivity
    from activities.post_fix_reply_activity import PostFixReplyActivity
    from config import get_settings

    try:
        settings = get_settings()
        fetch_activity = FetchPRCommentsActivity(settings=settings)
        resolve_activity = PostFixReplyActivity(settings=settings)

        threads = fetch_activity.execute(pr_id=pr_id, repository_id=repo or None)
        thread_by_cr_id = {t.cr_id: t for t in threads if t.cr_id}

        fixed_ids = {fv.cr_id for fv in fix_verifications if fv.status == "fixed"}

        for cr_id in fixed_ids:
            thread = thread_by_cr_id.get(cr_id)
            if thread:
                try:
                    resolve_activity.execute({
                        "thread_id": thread.thread_id,
                        "pr_id": pr_id,
                        "repository_id": repo or None,
                    })
                except Exception as exc:
                    _eprint(f"Warning: failed to resolve thread for {cr_id}: {exc}")
    except Exception as exc:
        _eprint(f"Warning: fix verification resolution failed: {exc}")


def _handle_fix_verifications_github(fix_verifications, pr_id: int, repo: str, dry_run: bool):
    """Reply to GitHub PR review comments for cr-ids classified as fixed."""
    if not fix_verifications or dry_run:
        return

    import re

    fixed_ids = {fv.cr_id for fv in fix_verifications if fv.status == "fixed"}
    if not fixed_ids:
        return

    try:
        result = _gh_run_with_retry(
            ["gh", "api", f"repos/{repo}/pulls/{pr_id}/comments",
             "--jq", "[.[] | {id: .id, body: .body}]"],
            capture_output=True, text=True, check=True
        )
        comments = json.loads(result.stdout)

        for comment in comments:
            body = comment.get("body", "")
            match = re.search(r"<!--\s*cr-id:\s*(\S+)\s*-->", body)
            if match and match.group(1) in fixed_ids:
                comment_id = comment["id"]
                try:
                    _gh_run_with_retry(
                        ["gh", "api",
                         f"repos/{repo}/pulls/comments/{comment_id}/replies",
                         "--method", "POST",
                         "-f", "body=✅ **Issue Fixed** — Resolved in the latest changes."],
                        capture_output=True, text=True, check=True
                    )
                except subprocess.CalledProcessError as exc:
                    _eprint(f"Warning: failed to reply to GitHub comment {comment_id}: {exc.stderr}")
    except Exception as exc:
        _eprint(f"Warning: GitHub fix verification failed: {exc}")


def _generate_comparison_md(score, fix_verifications, pr_id: int) -> str:
    """Generate before/after score comparison markdown using ScoreComparisonService."""
    try:
        from score_comparison import ScoreComparisonService
        svc = ScoreComparisonService()
        return svc.format_as_markdown(
            old_score=None,
            new_score=score,
            fix_verifications=fix_verifications,
            pr_title=f"PR #{pr_id}",
        )
    except Exception as exc:
        _eprint(f"Warning: failed to generate score comparison: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------

def _coverage_display(files_reviewed: int, total_code_files: int) -> str:
    """Return a human-readable coverage string like '8 / 12 (67%) — 4 files not reviewed'."""
    if total_code_files <= 0 or total_code_files == files_reviewed:
        pct = 100 if total_code_files <= 0 else int(files_reviewed / total_code_files * 100)
        return f"{files_reviewed} / {max(files_reviewed, total_code_files)} ({pct}%)"
    pct = int(files_reviewed / total_code_files * 100)
    not_reviewed = total_code_files - files_reviewed
    return f"{files_reviewed} / {total_code_files} ({pct}%) — {not_reviewed} file(s) not reviewed"


def _build_summary_markdown(
    findings_file,
    filtered_findings: list,
    score,
    gate_result: Dict[str, Any],
    fix_verifications: list,
    comparison_md: str = "",
    usage=None,
    cost_estimate: Optional[Dict[str, Any]] = None,
    max_total_findings: int = MAX_TOTAL_FINDINGS,
    pr_details=None,
    files_reviewed: int = 0,
    files_skipped: int = 0,
    total_code_files: int = 0,
) -> str:
    severity_counts = {"critical": 0, "warning": 0, "suggestion": 0}
    category_counts = {"security": 0, "performance": 0, "best_practices": 0, "code_style": 0, "documentation": 0}
    for f in filtered_findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
        category_counts[f.category] = category_counts.get(f.category, 0) + 1

    review_modes = getattr(findings_file, "review_modes", [])
    is_verify_only = "verify_fixes" in review_modes and not filtered_findings
    is_rereview = bool(fix_verifications)

    if is_verify_only:
        title = "# 🔍 AI Code Review — Fix Verification Only"
    elif is_rereview:
        title = "# 🔄 AI Code Re-Review — Fix Verification"
    else:
        title = "# 🤖 AI Code Review"

    lines = [
        title,
    ]

    if is_rereview:
        fixed = sum(1 for fv in fix_verifications if fv.status == "fixed")
        still = sum(1 for fv in fix_verifications if fv.status == "still_present")
        not_relevant = sum(1 for fv in fix_verifications if fv.status == "not_relevant")
        total_verified = fixed + still + not_relevant
        fix_rate = (fixed / total_verified * 100) if total_verified > 0 else 0
        lines += [
            "",
            "## 🔧 Fix Summary",
            f"- ✅ **Issues Fixed:** {fixed} / {total_verified}",
            f"- ❌ **Still Present:** {still} / {total_verified}" if still else "",
            f"- ➖ **Not Relevant:** {not_relevant}" if not_relevant else "",
            f"- **Fix Rate:** {fix_rate:.0f}%",
            "",
        ]
        lines = [l for l in lines if l is not None and l != ""]
        lines.append("")

    # PR Quality Score
    if score:
        lines += [
            "## 📈 PR Quality Score",
            f"**Overall Rating: {score.overall_stars} ({score.quality_level})**",
            f"**Total Penalty: {score.total_penalty:.1f} points** _(Lower is better!)_",
            "",
        ]
        if score.category_penalties:
            category_icons = {
                "security": "🔒", "performance": "⚡", "best_practices": "✨",
                "code_style": "🎨", "documentation": "📚",
            }
            lines.append("**Category Penalties**")
            for cat, penalty in score.category_penalties.items():
                if penalty > 0:
                    icon = category_icons.get(cat, "📝")
                    cat_label = cat.replace("_", " ").title()
                    lines.append(f"- {icon} {cat_label}: ⭐{'⭐' * max(0, 4 - int(penalty))}{'☆' * min(5, int(penalty))} {penalty:.1f} penalty points")
            lines.append("")

    # Scoring details (collapsible)
    if pr_details:
        pr_title = getattr(pr_details, "title", "") or f"PR #{findings_file.pr_id}"
        author = getattr(pr_details, "author", "") or "Unknown"
        source_branch = getattr(pr_details, "source_branch", "") or ""
        target_branch = getattr(pr_details, "target_branch", "") or ""

        lines += [
            "<details>",
            "<summary>📊 Scoring Details (click to expand)</summary>",
            "",
            f"**PR {findings_file.pr_id}: {pr_title}**",
            "",
            f"- 👤 Author: {author}",
        ]
        if source_branch and target_branch:
            lines.append(f"- 🌿 Branch: `{source_branch}` → `{target_branch}`")
        lines += [
            "",
            "### 📊 Review Statistics",
            f"- ✅ Files Reviewed: {_coverage_display(files_reviewed, total_code_files)}",
            f"- ⏭️ Files Skipped: {files_skipped}",
            f"- ❌ Files Failed: 0",
            f"- 💬 Total Comments: {len(filtered_findings)}",
            "",
        ]
    else:
        lines += [
            "<details>",
            "<summary>📊 Scoring Details (click to expand)</summary>",
            "",
            f"**PR #{findings_file.pr_id}** · `{findings_file.repo}`",
            "",
            f"- 💬 Total Comments: {len(filtered_findings)}",
            "",
        ]

    # Severity breakdown
    lines += [
        "### 🎯 Comment Breakdown by Severity",
        f"- 🔴 Critical: {severity_counts.get('critical', 0)}",
        f"- ⚠️ Warning: {severity_counts.get('warning', 0)}",
        f"- 💡 Suggestion: {severity_counts.get('suggestion', 0)}",
        "",
    ]

    # Category breakdown
    lines += [
        "### 📂 Comment Breakdown by Category",
        f"- 🔒 Security: {category_counts.get('security', 0)}",
        f"- ⚡ Performance: {category_counts.get('performance', 0)}",
        f"- ✨ Best Practices: {category_counts.get('best_practices', 0)}",
        f"- 🎨 Code Style: {category_counts.get('code_style', 0)}",
        f"- 📚 Documentation: {category_counts.get('documentation', 0)}",
        "",
        "</details>",
        "",
    ]

    # Fix verification details
    if comparison_md:
        lines += [comparison_md, ""]
    elif fix_verifications:
        lines += [
            "## 🔄 Fix Verification Details",
            "",
        ]
        for fv in fix_verifications:
            if fv.status == "fixed":
                lines.append(f"- ✅ **{fv.cr_id}** — Fixed: {fv.reason}")
            elif fv.status == "still_present":
                lines.append(f"- ❌ **{fv.cr_id}** — Still present: {fv.reason}")
            elif fv.status == "not_relevant":
                lines.append(f"- ➖ **{fv.cr_id}** — Not relevant: {fv.reason}")
        lines.append("")

    # CI Gate (internal use only — not shown in PR summary)

    # Overall summary — agent-generated narrative
    if getattr(findings_file, "summary", None):
        lines += [
            "## 📝 Overall Summary",
            findings_file.summary,
            "",
        ]

    # Findings list (suppressed when verify-only or when there are no findings)
    if filtered_findings and not is_verify_only:
        lines += ["## 🔍 Findings"]
        for f in filtered_findings:
            sev_icon = {"critical": "🔴", "warning": "⚠️", "suggestion": "💡"}.get(f.severity, "📝")
            lines.append(f"- {sev_icon} **{f.title}** (`{f.file}:{f.line}`)")
        lines.append("")

    # Token usage & cost
    if usage:
        lines += [
            "## 📊 Token Usage",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Model | `{usage.model or 'unknown'}` |",
            f"| Input tokens | {usage.input_tokens:,} |",
            f"| Output tokens | {usage.output_tokens:,} |",
            f"| Total tokens | {usage.total_tokens:,} |",
        ]
        if usage.duration_seconds is not None:
            lines.append(f"| Duration | {usage.duration_seconds:.1f}s |")
        if cost_estimate and cost_estimate.get("total_cost_usd") is not None:
            lines.append(f"| Estimated cost | **${cost_estimate['total_cost_usd']:.4f}** |")
        lines.append("")

    lines.append("---")
    lines.append("*This review was automatically generated using AI. Please use your judgment when applying suggestions.*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CI gate
# ---------------------------------------------------------------------------

def _evaluate_gate(
    score,
    filtered_findings: list,
    gate_config: Dict[str, Any],
    coverage_ratio: float = 1.0,
    not_reviewed_files: Optional[List[str]] = None,
    coverage_gate_mode: str = "hard",
) -> Dict[str, Any]:
    """
    Evaluate CI gate conditions against .codereview.yml thresholds.

    Returns dict: { "passed": bool, "reasons": List[str] }
    """
    passed = True
    reasons = []

    # fail_on_critical (default: True)
    fail_on_critical = gate_config.get("fail_on_critical", True)
    if fail_on_critical:
        critical_count = sum(1 for f in filtered_findings if f.severity == "critical")
        if critical_count > 0:
            passed = False
            reasons.append(f"Gate failed: {critical_count} critical finding(s) present")

    # min_star_rating (default: 0 = disabled)
    min_stars = gate_config.get("min_star_rating", 0)
    if min_stars and score:
        actual_stars = score.overall_stars.count("⭐")
        if actual_stars < min_stars:
            passed = False
            reasons.append(
                f"Gate failed: star rating {actual_stars} below minimum {min_stars}"
            )

    # Coverage gate (controlled by coverage_gate_mode)
    if coverage_ratio < 1.0:
        n = len(not_reviewed_files) if not_reviewed_files else 0
        file_list = ", ".join(not_reviewed_files[:5]) if not_reviewed_files else "unknown"
        if n > 5:
            file_list += f", ... ({n - 5} more)"
        msg = f"Gate failed: {n} file(s) not reviewed ({file_list}). 100% code review coverage is required."
        if coverage_gate_mode == "hard":
            passed = False
            reasons.append(msg)
        else:
            # "log" mode: report as a warning but don't fail
            reasons.append(f"Coverage warning ({coverage_ratio:.0%}): {msg}")

    return {"passed": passed, "reasons": reasons}


# ---------------------------------------------------------------------------
# Audit trail export
# ---------------------------------------------------------------------------

def _write_audit_trail(
    workspace: str,
    findings_file,
    all_raw_findings: list,
    capped_findings: list,
    score,
    gate_result: Dict[str, Any],
    files_reviewed_set: set,
    all_code_paths: set,
    coverage_ratio: float,
    usage=None,
    cost_estimate: Optional[Dict[str, Any]] = None,
    pr_details=None,
    risk_table_md: str = "",
) -> Optional[str]:
    """Write a timestamped audit trail markdown file to .cr/ in the workspace."""
    cr_dir = Path(workspace) / ".cr"
    cr_dir.mkdir(parents=True, exist_ok=True)

    pr_id = findings_file.pr_id
    now = datetime.now()
    export_path = cr_dir / f"review_{pr_id}_{now:%Y%m%d_%H%M%S}.md"

    lines = [
        f"# Code Review Audit Trail — PR #{pr_id}",
        "",
        f"**Generated:** {now:%Y-%m-%d %H:%M:%S}",
        "",
        "---",
        "",
        "## PR Metadata",
        "",
        f"- **PR ID:** {pr_id}",
        f"- **Repo:** {findings_file.repo}",
        f"- **VCS:** {findings_file.vcs}",
        f"- **Review Modes:** {', '.join(findings_file.review_modes)}",
    ]

    if pr_details:
        lines += [
            f"- **Branch:** `{getattr(pr_details, 'source_branch', 'N/A')}` → `{getattr(pr_details, 'target_branch', 'N/A')}`",
            f"- **Author:** {getattr(pr_details, 'author', 'N/A')}",
            f"- **Title:** {getattr(pr_details, 'title', 'N/A')}",
        ]

    lines += ["", "---", "", "## Score Breakdown", ""]

    if score:
        lines += [
            f"- **Overall Rating:** {score.overall_stars} ({score.quality_level})",
            f"- **Total Penalty:** {score.total_penalty:.1f} points",
            "",
            "### Category Penalties",
            "",
            "| Category | Penalty |",
            "|----------|---------|",
        ]
        for cat, penalty in sorted(score.category_penalties.items()):
            lines.append(f"| {cat} | {penalty:.1f} |")
    else:
        lines.append("*(Score not available)*")

    not_reviewed = sorted(all_code_paths - files_reviewed_set)
    lines += [
        "", "---", "", "## Files Reviewed", "",
        f"- **Reviewed:** {_coverage_display(len(files_reviewed_set), len(all_code_paths))}",
        f"- **Coverage:** {coverage_ratio:.1%}",
    ]
    if not_reviewed:
        lines += ["", "### Files Not Reviewed", ""]
        for fp in not_reviewed:
            lines.append(f"- `{fp}`")

    lines += ["", "---", "", "## Findings", "",
              f"*(Total raw: {len(all_raw_findings)}, After filtering/capping: {len(capped_findings)})*", ""]

    if capped_findings:
        lines += [
            "| Severity | Category | File | Line | Title | Confidence |",
            "|----------|----------|------|------|-------|------------|",
        ]
        for f in capped_findings:
            lines.append(f"| {f.severity} | {f.category} | `{f.file}` | {f.line} | {f.title} | {f.confidence:.0%} |")
    else:
        lines.append("*(No findings after filtering)*")

    capped_ids = {f.id for f in capped_findings}
    filtered_out = [f for f in all_raw_findings if f.id not in capped_ids]
    if filtered_out:
        lines += [
            "", "### Filtered/Capped Findings (not posted)", "",
            "| Severity | Category | File | Line | Title | Confidence |",
            "|----------|----------|------|------|-------|------------|",
        ]
        for f in filtered_out:
            lines.append(f"| {f.severity} | {f.category} | `{f.file}` | {f.line} | {f.title} | {f.confidence:.0%} |")

    lines += ["", "---", "", "## Token Usage", ""]
    if usage:
        lines += [
            f"- **Model:** {usage.model or 'unknown'}",
            f"- **Input tokens:** {usage.input_tokens:,}",
            f"- **Output tokens:** {usage.output_tokens:,}",
            f"- **Total tokens:** {usage.total_tokens:,}",
        ]
        if usage.duration_seconds is not None:
            lines.append(f"- **Duration:** {usage.duration_seconds:.1f}s")
        if cost_estimate and cost_estimate.get("total_cost_usd") is not None:
            lines.append(f"- **Estimated cost:** ${cost_estimate['total_cost_usd']:.4f}")
    else:
        lines.append("*(Usage not available)*")

    lines += ["", "---", "", "## Risk Classification", ""]
    if risk_table_md:
        lines.append(risk_table_md)
    else:
        lines.append("*(Risk classification not available for this review)*")

    lines += ["", "---", "", "## Gate Decision", ""]
    gate_passed = gate_result.get("passed", True)
    lines.append(f"**Result:** {'PASSED ✅' if gate_passed else 'FAILED 🚨'}")
    if gate_result.get("reasons"):
        lines += ["", "**Reasons:**", ""]
        for reason in gate_result["reasons"]:
            lines.append(f"- {reason}")
    lines.append("")

    content = "\n".join(lines)
    try:
        export_path.write_text(content, encoding="utf-8")
        return str(export_path)
    except Exception as exc:
        _eprint(f"Warning: failed to write audit trail: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    findings_path: str,
    dry_run: bool = False,
    workspace: str = ".",
    commit_id: str = "",
) -> Dict[str, Any]:
    """
    Core post_findings logic.

    Args:
        findings_path: Path to findings.json
        dry_run: If True, skip all VCS writes
        workspace: Workspace directory (for .codereview.yml lookup)
        commit_id: Source commit SHA (needed for GitHub inline comments)

    Returns:
        Structured output dict for CI gating
    """
    # 1. Load + normalize + validate
    raw = _load_json(findings_path)
    _normalize_findings(raw)
    errors = _validate_schema(raw)
    if errors:
        logger.warning("findings.json has schema issues (continuing with best-effort):")
        for e in errors:
            logger.warning("  - %s", e)

    findings_file = _parse_findings_file(raw)
    # Track whether agent explicitly included files_clean (opted into coverage tracking)
    agent_uses_coverage_tracking = "files_clean" in raw

    # 2. Detect verify-only mode
    is_verify_only = "verify_fixes" in findings_file.review_modes and not findings_file.findings

    # 3. Filter by confidence (skip for verify-only — no findings to filter)
    if is_verify_only:
        after_confidence = []
        filtered_count = 0
    else:
        after_confidence = filter_by_confidence(findings_file.findings, MIN_CONFIDENCE)
        filtered_count = len(findings_file.findings) - len(after_confidence)

    # 3b. Merge near-duplicate findings (skip for verify-only)
    if not is_verify_only:
        after_confidence = merge_similar_findings(after_confidence)

    # 4. Apply mode multipliers (adjusts severity for scoring)
    from pr_scorer import PRScorer
    from config import get_settings

    try:
        settings = get_settings()
        penalty_matrix = settings.get_penalty_matrix()
        star_thresholds = settings.get_star_thresholds()
    except Exception:
        # Fallback defaults if env not configured (dry-run / test scenarios)
        penalty_matrix = {
            "security": {"critical": 5.0, "warning": 4.0, "suggestion": 2.0, "good": 0.0},
            "performance": {"critical": 3.0, "warning": 2.0, "suggestion": 1.0, "good": 0.0},
            "best_practices": {"critical": 2.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
            "architecture": {"critical": 3.0, "warning": 2.0, "suggestion": 1.0, "good": 0.0},
            "correctness": {"critical": 2.0, "warning": 1.0, "suggestion": 0.5, "good": 0.0},
            "error_handling": {"critical": 1.5, "warning": 0.75, "suggestion": 0.25, "good": 0.0},
            "code_style": {"critical": 1.0, "warning": 0.5, "suggestion": 0.25, "good": 0.0},
            "documentation": {"critical": 1.0, "warning": 0.5, "suggestion": 0.25, "good": 0.0},
            "testing": {"critical": 1.5, "warning": 0.75, "suggestion": 0.25, "good": 0.0},
        }
        star_thresholds = [0.0, 5.0, 15.0, 30.0, 50.0]
        settings = None

    scorer = PRScorer(penalty_matrix=penalty_matrix, star_thresholds=star_thresholds)

    # 5. Cap findings (read limits from settings when available; skip for verify-only)
    max_total = settings.max_total_findings if settings else MAX_TOTAL_FINDINGS
    max_per_file = settings.max_per_file_findings if settings else MAX_PER_FILE
    capped = [] if is_verify_only else cap_findings(after_confidence, max_total, max_per_file)

    # 6. Fetch existing cr-ids for dedup (skip for verify-only — no new comments to post)
    vcs = findings_file.vcs
    repo = findings_file.repo
    pr_id = findings_file.pr_id

    if is_verify_only or dry_run:
        posted_cr_ids: Set[str] = set()
    elif vcs == "ado":
        posted_cr_ids = _fetch_posted_cr_ids_ado(pr_id, repo)
    else:
        posted_cr_ids = _fetch_posted_cr_ids_github(pr_id, repo)

    # 7. Dedup: skip already-posted cr-ids
    new_findings = [f for f in capped if f.id not in posted_cr_ids]
    deduped_count = len(capped) - len(new_findings)

    # 8. Score (apply mode multipliers so severity is consistent for scoring AND posting)
    all_adjusted = capped
    if is_verify_only and findings_file.fix_verifications:
        score = scorer.calculate_verify_score(findings_file.fix_verifications, findings_file.review_modes)
    else:
        all_adjusted = scorer.apply_mode_multipliers(capped, findings_file.review_modes)
        score = scorer.calculate_pr_score(all_adjusted)
        # Rebuild new_findings from adjusted list so posted comments reflect adjusted severity
        adjusted_by_id = {f.id: f for f in all_adjusted}
        new_findings = [adjusted_by_id[f.id] for f in new_findings if f.id in adjusted_by_id]

    # 9. Post inline comments (skip entirely for verify-only)
    posted_count = 0
    post_errors = []
    if not is_verify_only:
        for finding in new_findings:
            if vcs == "ado":
                ok = _post_inline_ado(finding, pr_id, repo, dry_run)
            else:
                ok = _post_inline_github(finding, pr_id, repo, commit_id, dry_run)

            if ok:
                posted_count += 1
            else:
                post_errors.append(finding.id)

    # 10. Handle fix verifications
    if findings_file.fix_verifications:
        if vcs == "ado":
            _handle_fix_verifications_ado(findings_file.fix_verifications, pr_id, repo, dry_run)
        else:
            _handle_fix_verifications_github(findings_file.fix_verifications, pr_id, repo, dry_run)

    # 11a. Fetch PR details for summary + coverage (title, author, branch)
    pr_details = None
    files_skipped = 0
    if vcs == "ado" and settings:
        try:
            from activities.fetch_pr_details_activity import FetchPRDetailsActivity
            from models.review_models import FetchPRDetailsInput
            pr_activity = FetchPRDetailsActivity(settings=settings)
            pr_details = pr_activity.execute(FetchPRDetailsInput(pr_id=pr_id, repository_id=repo))
        except Exception:
            pass

    # 11b. Compute coverage
    files_with_findings = set(f.file for f in findings_file.findings)
    files_clean_set = set(findings_file.files_clean or [])
    files_reviewed_set = files_with_findings | files_clean_set

    from file_filter import parse_skip_extensions, filter_changed_files
    skip_exts_str = settings.skip_extensions if settings else ""
    skip_exts = parse_skip_extensions(skip_exts_str) if skip_exts_str else set()
    if pr_details and pr_details.file_changes:
        code_files, _ = filter_changed_files(pr_details.file_changes, skip_exts)
        total_code_files = len(code_files)
        all_code_paths = {fc.path for fc in code_files}
    else:
        total_code_files = len(files_reviewed_set) or 1
        all_code_paths = files_reviewed_set.copy()

    not_reviewed_files = sorted(all_code_paths - files_reviewed_set)
    files_reviewed = len(files_reviewed_set)
    # Coverage gate only fires when the agent explicitly included files_clean[] in the output,
    # signalling that it has opted into coverage tracking. Old-style findings.json without
    # this key get no coverage gate (backward compatible).
    if agent_uses_coverage_tracking and total_code_files > 0:
        coverage_ratio = len(files_reviewed_set) / total_code_files
    else:
        coverage_ratio = 1.0
    coverage_gate_mode = getattr(settings, "coverage_gate_mode", "hard") if settings else "hard"

    # 11c. Coverage is tracked for display but does not affect the penalty score

    # 12. Gate evaluation from .codereview.yml (verify-only always passes — no new findings)
    gate_config = _load_codereview_yml(workspace)
    if is_verify_only:
        gate_result = {"passed": True, "reasons": []}
    else:
        gate_result = _evaluate_gate(
            score,
            all_adjusted,
            gate_config,
            coverage_ratio=coverage_ratio,
            not_reviewed_files=not_reviewed_files,
            coverage_gate_mode=coverage_gate_mode,
        )

    # 12b. Generate score comparison markdown when fix verifications are present
    comparison_md = ""
    if findings_file.fix_verifications:
        comparison_md = _generate_comparison_md(score, findings_file.fix_verifications, pr_id)

    # 12c. Estimate cost from usage
    cost_estimate = _estimate_cost(findings_file.usage)

    # 13. Post/update summary
    summary_md = _build_summary_markdown(
        findings_file=findings_file,
        filtered_findings=all_adjusted if not is_verify_only else capped,
        score=score,
        gate_result=gate_result,
        fix_verifications=findings_file.fix_verifications,
        comparison_md=comparison_md,
        usage=findings_file.usage,
        cost_estimate=cost_estimate,
        max_total_findings=max_total,
        pr_details=pr_details,
        files_reviewed=files_reviewed,
        files_skipped=files_skipped,
        total_code_files=total_code_files,
    )

    if not dry_run and settings:
        try:
            if vcs == "ado":
                from activities.post_pr_comment_activity import PostPRCommentActivity
                comment_activity = PostPRCommentActivity(settings=settings)
                comment_activity._post_summary_comment(
                    pr_id=pr_id,
                    repository_id=repo or settings.azure_devops_repo,
                    project=settings.azure_devops_project,
                    comment_text=summary_md,
                )
            else:
                _gh_run_with_retry(
                    ["gh", "pr", "comment", str(pr_id), "--body", summary_md, "--repo", repo],
                    capture_output=True, text=True, check=True
                )
        except Exception as exc:
            _eprint(f"Warning: failed to post summary: {exc}")

    # 13b. Write audit trail
    _write_audit_trail(
        workspace=workspace,
        findings_file=findings_file,
        all_raw_findings=findings_file.findings,
        capped_findings=capped,
        score=score,
        gate_result=gate_result,
        files_reviewed_set=files_reviewed_set,
        all_code_paths=all_code_paths,
        coverage_ratio=coverage_ratio,
        usage=findings_file.usage,
        cost_estimate=cost_estimate,
        pr_details=pr_details,
    )

    # Build output
    output = {
        "pr_id": pr_id,
        "repo": repo,
        "vcs": vcs,
        "review_modes": findings_file.review_modes,
        "agent": findings_file.agent,
        "tool_calls": findings_file.tool_calls,
        "filtering": {
            "total_raw": len(findings_file.findings),
            "after_confidence_filter": len(after_confidence),
            "filtered_low_confidence": filtered_count,
            "after_cap": len(capped),
            "deduped_already_posted": deduped_count,
            "new_findings_posted": posted_count,
            "post_errors": post_errors,
        },
        "score": {
            "total_penalty": score.total_penalty,
            "overall_stars": score.overall_stars,
            "quality_level": score.quality_level,
            "issues_by_severity": score.issues_by_severity,
            "category_penalties": score.category_penalties,
        },
        "gate": gate_result,
        "dry_run": dry_run,
        "findings": [
            {
                "id": f.id,
                "file": f.file,
                "line": f.line,
                "severity": f.severity,
                "category": f.category,
                "title": f.title,
                "confidence": f.confidence,
            }
            for f in capped
        ],
        "fix_verifications": [
            {"cr_id": fv.cr_id, "status": fv.status, "reason": fv.reason}
            for fv in findings_file.fix_verifications
        ],
        "has_comparison": bool(comparison_md),
        "usage": {
            "input_tokens": findings_file.usage.input_tokens,
            "output_tokens": findings_file.usage.output_tokens,
            "total_tokens": findings_file.usage.total_tokens,
            "model": findings_file.usage.model,
            "duration_seconds": findings_file.usage.duration_seconds,
        } if findings_file.usage else None,
        "cost_estimate": cost_estimate,
    }

    return output


def _redirect_logging_to_stderr():
    """
    Redirect all logging output to stderr so stdout stays clean JSON.

    Must be called before any logging setup occurs. Installs a root handler
    on stderr, then patches utils.logger.setup_logger to also use stderr.
    """
    import logging

    # Install a root-level stderr handler that will catch any logger that
    # propagates to root (most do by default).
    root = logging.getLogger()
    if not any(
        hasattr(h, "stream") and h.stream is sys.stderr
        for h in root.handlers
    ):
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG)
        root.addHandler(stderr_handler)

    # Monkey-patch utils.logger so future loggers also write to stderr
    try:
        import utils.logger as _ul
        import functools

        _orig_setup = _ul.setup_logger

        @functools.wraps(_orig_setup)
        def _patched_setup(name="codehawk", level="INFO", log_file=None, log_format="json", force=False):
            logger = _orig_setup(name=name, level=level, log_file=log_file, log_format=log_format, force=force)
            for handler in logger.handlers:
                if hasattr(handler, "stream") and handler.stream is sys.stdout:
                    handler.stream = sys.stderr
            return logger

        _ul.setup_logger = _patched_setup
    except ImportError:
        pass


def main():
    _redirect_logging_to_stderr()
    parser = argparse.ArgumentParser(
        prog="post_findings.py",
        description="codehawk Phase 2 engine — post findings from findings.json to VCS"
    )
    parser.add_argument(
        "--findings",
        required=True,
        help="Path to findings.json produced by the review agent"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Read, filter, score — but skip all VCS writes. Outputs scored JSON to stdout."
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root directory (for .codereview.yml lookup)"
    )
    parser.add_argument(
        "--commit-id",
        default="",
        help="Source commit SHA (required for GitHub inline comments)"
    )

    args = parser.parse_args()

    try:
        output = run(
            findings_path=args.findings,
            dry_run=args.dry_run,
            workspace=args.workspace,
            commit_id=args.commit_id,
        )
        print(json.dumps(output, indent=2))
        if not output["gate"]["passed"]:
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        _eprint(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
