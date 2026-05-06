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
import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_PATH = Path(__file__).parent.parent / "commands" / "findings-schema.json"
MIN_CONFIDENCE = 0.7
MAX_TOTAL_FINDINGS = 50  # Default; overridden by settings.max_total_findings at runtime
MAX_PER_FILE = 5  # Default; overridden by settings.max_per_file_findings at runtime
CODEREVIEW_YML = ".codereview.yml"

# Cost per 1M tokens: (input, output) in USD.
# Sorted longest-prefix-first so "gpt-4.1-mini" matches before "gpt-4.1".
MODEL_COST_TABLE: Dict[str, Tuple[float, float]] = {
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

    findings = [
        Finding(
            id=f["id"],
            file=f["file"],
            line=f["line"],
            severity=f["severity"],
            category=f["category"],
            title=f["title"],
            message=f["message"],
            confidence=f["confidence"],
            suggestion=f.get("suggestion"),
        )
        for f in data.get("findings", [])
    ]

    fix_verifications = [
        FixVerification(
            cr_id=fv["cr_id"],
            status=fv["status"],
            reason=fv["reason"],
        )
        for fv in data.get("fix_verifications", [])
    ]

    usage = None
    raw_usage = data.get("usage")
    if raw_usage and isinstance(raw_usage, dict):
        usage = Usage(
            input_tokens=raw_usage["input_tokens"],
            output_tokens=raw_usage["output_tokens"],
            total_tokens=raw_usage["total_tokens"],
            model=raw_usage.get("model"),
            duration_seconds=raw_usage.get("duration_seconds"),
        )

    return FindingsFile(
        pr_id=data["pr_id"],
        repo=data["repo"],
        vcs=data["vcs"],
        review_modes=data.get("review_modes", []),
        findings=findings,
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

    from activities.post_pr_comment_activity import PostPRCommentActivity, PostPRCommentInput
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
    body += f"\n\n<!-- cr-id: {finding.id} -->"

    settings = get_settings()
    activity = PostPRCommentActivity(settings=settings)
    inp = PostPRCommentInput(
        pr_id=pr_id,
        comment_text=body,
        file_path=finding.file,
        line_number=finding.line,
        repository_id=repo or None,
    )
    try:
        activity.execute(inp)
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
) -> str:
    severity_counts = {"critical": 0, "warning": 0, "suggestion": 0}
    for f in filtered_findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    lines = [
        "<!-- codehawk-summary -->",
        "# 🤖 codehawk Code Review",
        "",
        f"**PR #{findings_file.pr_id}** · `{findings_file.repo}` · modes: `{', '.join(findings_file.review_modes)}`",
        "",
    ]

    if score:
        lines += [
            "## 📈 PR Quality Score",
            "",
            f"### Overall Rating: {score.overall_stars} ({score.quality_level})",
            f"**Total Penalty: {score.total_penalty:.1f} points** _(Lower is better!)_",
            "",
        ]

    lines += [
        "## 📊 Findings Summary",
        "",
        f"- 🔴 Critical: {severity_counts.get('critical', 0)}",
        f"- ⚠️ Warning: {severity_counts.get('warning', 0)}",
        f"- 💡 Suggestion: {severity_counts.get('suggestion', 0)}",
        f"- Total posted: {len(filtered_findings)} / {max_total_findings} max",
        "",
    ]

    if comparison_md:
        lines += [
            "---",
            "",
            comparison_md,
            "",
        ]
    elif fix_verifications:
        fixed = sum(1 for fv in fix_verifications if fv.status == "fixed")
        still = sum(1 for fv in fix_verifications if fv.status == "still_present")
        lines += [
            "## 🔄 Fix Verification",
            "",
            f"- ✅ Fixed: {fixed}",
            f"- ❌ Still present: {still}",
            "",
        ]

    gate_passed = gate_result.get("passed", True)
    gate_icon = "✅" if gate_passed else "🚨"
    lines += [
        "## 🚦 CI Gate",
        "",
        f"{gate_icon} Gate: **{'PASSED' if gate_passed else 'FAILED'}**",
        "",
    ]

    if gate_result.get("reasons"):
        for reason in gate_result["reasons"]:
            lines.append(f"- {reason}")
        lines.append("")

    if usage:
        lines += [
            "## 📊 Token Usage",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
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
    lines.append("*Generated by [codehawk](https://github.com/your-org/codehawk)*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CI gate
# ---------------------------------------------------------------------------

def _evaluate_gate(score, filtered_findings: list, gate_config: Dict[str, Any]) -> Dict[str, Any]:
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

    return {"passed": passed, "reasons": reasons}


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
    # 1. Load + validate
    raw = _load_json(findings_path)
    errors = _validate_schema(raw)
    if errors:
        _eprint("ERROR: findings.json failed schema validation:")
        for e in errors:
            _eprint(f"  - {e}")
        raise SystemExit(1)

    findings_file = _parse_findings_file(raw)

    # 2. Filter by confidence
    after_confidence = filter_by_confidence(findings_file.findings, MIN_CONFIDENCE)
    filtered_count = len(findings_file.findings) - len(after_confidence)

    # 3. Apply mode multipliers (adjusts severity for scoring)
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
            "code_style": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
            "documentation": {"critical": 0.0, "warning": 0.0, "suggestion": 0.0, "good": 0.0},
        }
        star_thresholds = [0.0, 5.0, 15.0, 30.0, 50.0]
        settings = None

    scorer = PRScorer(penalty_matrix=penalty_matrix, star_thresholds=star_thresholds)

    # 4. Cap findings (read limits from settings when available)
    max_total = settings.max_total_findings if settings else MAX_TOTAL_FINDINGS
    max_per_file = settings.max_per_file_findings if settings else MAX_PER_FILE
    capped = cap_findings(after_confidence, max_total, max_per_file)

    # 5. Fetch existing cr-ids for dedup
    vcs = findings_file.vcs
    repo = findings_file.repo
    pr_id = findings_file.pr_id

    if dry_run:
        posted_cr_ids: Set[str] = set()
    elif vcs == "ado":
        posted_cr_ids = _fetch_posted_cr_ids_ado(pr_id, repo)
    else:
        posted_cr_ids = _fetch_posted_cr_ids_github(pr_id, repo)

    # 6. Dedup: skip already-posted cr-ids
    new_findings = [f for f in capped if f.id not in posted_cr_ids]
    deduped_count = len(capped) - len(new_findings)

    # 7. Score (use mode-adjusted findings)
    all_adjusted = scorer.apply_mode_multipliers(capped, findings_file.review_modes)
    score = scorer.calculate_pr_score(all_adjusted)

    # 8. Post inline comments
    posted_count = 0
    post_errors = []
    for finding in new_findings:
        if vcs == "ado":
            ok = _post_inline_ado(finding, pr_id, repo, dry_run)
        else:
            ok = _post_inline_github(finding, pr_id, repo, commit_id, dry_run)

        if ok:
            posted_count += 1
        else:
            post_errors.append(finding.id)

    # 9. Handle fix verifications
    if findings_file.fix_verifications:
        if vcs == "ado":
            _handle_fix_verifications_ado(findings_file.fix_verifications, pr_id, repo, dry_run)
        else:
            _handle_fix_verifications_github(findings_file.fix_verifications, pr_id, repo, dry_run)

    # 10. Gate evaluation from .codereview.yml (use mode-adjusted severity for consistency with score)
    gate_config = _load_codereview_yml(workspace)
    gate_result = _evaluate_gate(score, all_adjusted, gate_config)

    # 11. Generate score comparison markdown when fix verifications are present
    comparison_md = ""
    if findings_file.fix_verifications:
        comparison_md = _generate_comparison_md(score, findings_file.fix_verifications, pr_id)

    # 12. Estimate cost from usage
    cost_estimate = _estimate_cost(findings_file.usage)

    # 13. Post/update summary
    summary_md = _build_summary_markdown(
        findings_file=findings_file,
        filtered_findings=capped,
        score=score,
        gate_result=gate_result,
        fix_verifications=findings_file.fix_verifications,
        comparison_md=comparison_md,
        usage=findings_file.usage,
        cost_estimate=cost_estimate,
        max_total_findings=max_total,
    )

    if not dry_run and settings:
        try:
            if vcs == "ado":
                from activities.update_summary_activity import UpdateSummaryActivity, UpdateSummaryInput
                summary_activity = UpdateSummaryActivity(settings=settings)
                summary_activity.execute(UpdateSummaryInput(
                    pr_id=pr_id,
                    new_content=summary_md,
                    repository_id=repo or None,
                ))
            else:
                _gh_run_with_retry(
                    ["gh", "pr", "comment", str(pr_id), "--body", summary_md, "--repo", repo],
                    capture_output=True, text=True, check=True
                )
        except Exception as exc:
            _eprint(f"Warning: failed to post summary: {exc}")

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
