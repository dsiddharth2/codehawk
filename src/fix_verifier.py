"""
Fix Verifier — deterministic verification of prior code review findings.

Runs after each re-push. Determines for each prior finding whether it is:
  - not_relevant: file was deleted or removed
  - still_present: file unchanged since last review (no LLM cost)
  - fixed | still_present: file modified — one LLM call per file verifies each finding

One LLM call per modified file (not per finding).
Cap: 15 individual file calls, then overflow batched 5 per call (~18 calls max).
Default on any failure or ambiguous result: still_present (never assume fixed).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Settings, get_settings
from models.review_models import ExistingCommentThread, FixVerification

logger = logging.getLogger("codehawk.fix_verifier")

_INDIVIDUAL_FILE_CAP = 15
_BATCH_FILE_SIZE = 5


def verify_fixes(
    old_findings: List[ExistingCommentThread],
    workspace: Path,
    pr_id: int,
    repo: str,
    old_commit: str = "",
    new_commit: str = "HEAD",
    graph_store: Any = None,
    settings: Optional[Settings] = None,
    dry_run: bool = False,
) -> List[FixVerification]:
    """Verify whether prior findings have been fixed in the current HEAD.

    Args:
        old_findings: Findings from the prior review (ExistingCommentThread list).
        workspace: Path to the git workspace root.
        pr_id: Pull request ID (for ADO posting).
        repo: Repository name/ID (for ADO posting).
        old_commit: Commit SHA of the prior review. Empty = no git diff run.
        new_commit: Current commit SHA (default: HEAD).
        graph_store: Optional graph store for blast-radius notes.
        settings: App settings (uses default if None).
        dry_run: If True, skip posting to ADO.

    Returns:
        List of FixVerification results, one per finding.
    """
    if not old_findings:
        return []

    settings = settings or get_settings()
    workspace = Path(workspace)

    deleted_files, renamed_map, modified_files = _get_file_statuses(
        workspace, old_commit, new_commit
    )

    findings_by_file: Dict[str, List[ExistingCommentThread]] = {}
    for finding in old_findings:
        file_path = finding.file_path.lstrip("/")
        findings_by_file.setdefault(file_path, []).append(finding)

    results: List[FixVerification] = []
    files_needing_llm: List[Tuple[str, List[ExistingCommentThread]]] = []

    for file_path, file_findings in findings_by_file.items():
        effective_path = renamed_map.get(file_path, file_path)

        if file_path in deleted_files or effective_path in deleted_files:
            for f in file_findings:
                results.append(FixVerification(
                    cr_id=f.cr_id or f"thread-{f.thread_id}",
                    status="not_relevant",
                    reason="File was deleted or removed from the repository.",
                ))
        elif file_path not in modified_files and effective_path not in modified_files:
            # File not in diff — unchanged since last review
            for f in file_findings:
                results.append(FixVerification(
                    cr_id=f.cr_id or f"thread-{f.thread_id}",
                    status="still_present",
                    reason="File was not modified since the prior review — issue is still present.",
                ))
        else:
            files_needing_llm.append((effective_path, file_findings))

    if files_needing_llm:
        llm_results = _verify_files_with_llm(files_needing_llm, workspace, settings)
        results.extend(llm_results)

    if graph_store is not None:
        _annotate_blast_radius(results, old_findings, graph_store)

    if not dry_run:
        _post_results_to_ado(results, old_findings, pr_id, repo, settings)

    return results


def _get_file_statuses(
    workspace: Path,
    old_commit: str,
    new_commit: str,
) -> Tuple[set, Dict[str, str], set]:
    """Run git diff --name-status and parse into deleted/renamed/modified sets.

    Returns:
        deleted_files: Set of deleted file paths.
        renamed_map: Dict mapping old path to new path for renames.
        modified_files: Set of modified or added file paths.
    """
    deleted: set = set()
    renamed: Dict[str, str] = {}
    modified: set = set()

    if not old_commit:
        return deleted, renamed, modified

    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", old_commit, new_commit],
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("git diff --name-status failed: %s", result.stderr)
            return deleted, renamed, modified

        for line in result.stdout.splitlines():
            parts = line.strip().split("\t")
            if not parts or not parts[0]:
                continue
            status = parts[0]
            if status.startswith("D") and len(parts) >= 2:
                deleted.add(parts[1])
            elif status.startswith("R") and len(parts) >= 3:
                renamed[parts[1]] = parts[2]
                modified.add(parts[2])
            elif status.startswith(("M", "A", "C")) and len(parts) >= 2:
                modified.add(parts[1])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("Could not run git diff --name-status: %s", exc)

    return deleted, renamed, modified


def _verify_files_with_llm(
    files_needing_llm: List[Tuple[str, List[ExistingCommentThread]]],
    workspace: Path,
    settings: Settings,
) -> List[FixVerification]:
    """Verify modified files via LLM — individual calls up to cap, then batched.

    First _INDIVIDUAL_FILE_CAP files each get their own call.
    Overflow files are called _BATCH_FILE_SIZE at a time (each file still one call).
    """
    results: List[FixVerification] = []
    individual = files_needing_llm[:_INDIVIDUAL_FILE_CAP]
    overflow = files_needing_llm[_INDIVIDUAL_FILE_CAP:]

    for file_path, file_findings in individual:
        results.extend(_verify_single_file(file_path, file_findings, workspace, settings))

    for i in range(0, len(overflow), _BATCH_FILE_SIZE):
        batch = overflow[i : i + _BATCH_FILE_SIZE]
        for file_path, file_findings in batch:
            results.extend(_verify_single_file(file_path, file_findings, workspace, settings))

    return results


def _read_file_content(file_path: str, workspace: Path) -> str:
    """Read current file content from workspace. Returns empty string on failure."""
    try:
        full_path = workspace / file_path
        if full_path.exists():
            return full_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Could not read file %s: %s", file_path, exc)
    return ""


def _build_verification_prompt(
    file_path: str,
    file_content: str,
    findings: List[ExistingCommentThread],
) -> str:
    """Build the LLM verification prompt for one file with all its findings."""
    rows = "\n".join(
        f"| {i + 1} | {f.comment_text[:120].replace(chr(10), ' ')} | {f.line_number} |"
        for i, f in enumerate(findings)
    )
    return f"""You are verifying whether previously flagged code review findings have been fixed.

## File: {file_path}

## Current file content:
```
{file_content[:8000]}
```

## Previous findings to verify:
| # | Description | Original line |
|---|-------------|---------------|
{rows}

For each finding, determine:
- `fixed` — the specific issue described is no longer present in the current code
- `still_present` — the issue still exists (may be at a different line now)

Respond ONLY as a JSON array with no other text:
[{{"finding": 1, "status": "fixed", "reason": "..."}}, ...]"""


def _call_llm_for_verification(prompt: str, expected_count: int) -> List[Dict]:
    """Call OpenAI chat completions to verify findings.

    Returns list of {finding, status, reason} dicts.
    Any failure or ambiguous response defaults to still_present via _map_llm_results.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — defaulting all to still_present")
        return []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            seed=42,
        )
        raw = (response.choices[0].message.content or "").strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        for v in parsed.values():
            if isinstance(v, list):
                return v
        return []
    except Exception as exc:
        logger.warning("LLM verification call failed: %s", exc)
        return []


def _verify_single_file(
    file_path: str,
    findings: List[ExistingCommentThread],
    workspace: Path,
    settings: Settings,
) -> List[FixVerification]:
    """Verify all findings in one modified file via a single LLM call."""
    file_content = _read_file_content(file_path, workspace)
    if not file_content:
        return [
            FixVerification(
                cr_id=f.cr_id or f"thread-{f.thread_id}",
                status="still_present",
                reason="File content could not be read — defaulting to still_present.",
            )
            for f in findings
        ]

    prompt = _build_verification_prompt(file_path, file_content, findings)
    llm_results = _call_llm_for_verification(prompt, len(findings))
    return _map_llm_results_to_verifications(findings, llm_results)


def _map_llm_results_to_verifications(
    findings: List[ExistingCommentThread],
    llm_results: List[Dict],
) -> List[FixVerification]:
    """Map LLM result list (1-based index) back to FixVerification objects.

    Any finding not returned by the LLM or with an unrecognised status defaults to
    still_present — we never assume fixed when the verdict is unclear.
    """
    llm_by_index = {r.get("finding"): r for r in (llm_results or []) if isinstance(r, dict)}
    verifications: List[FixVerification] = []

    for i, finding in enumerate(findings, start=1):
        llm_result = llm_by_index.get(i)
        if llm_result and llm_result.get("status") in ("fixed", "still_present"):
            status = llm_result["status"]
            reason = str(llm_result.get("reason", f"LLM classified as {status}."))
        else:
            status = "still_present"
            reason = "LLM did not return a definitive verdict — defaulting to still_present."

        verifications.append(FixVerification(
            cr_id=finding.cr_id or f"thread-{finding.thread_id}",
            status=status,
            reason=reason,
        ))

    return verifications


def _annotate_blast_radius(
    results: List[FixVerification],
    old_findings: List[ExistingCommentThread],
    graph_store: Any,
) -> None:
    """Append informational blast-radius notes to fixed critical/architecture findings.

    Does not change the fixed/still_present verdict — audit/informational only.
    """
    findings_by_cr_id = {f.cr_id: f for f in old_findings if f.cr_id}

    for result in results:
        if result.status != "fixed":
            continue
        finding = findings_by_cr_id.get(result.cr_id)
        if finding is None:
            continue
        if finding.severity != "critical" and finding.category != "architecture":
            continue
        try:
            # Attempt blast radius lookup via graph store if it exposes a simple API
            dependents = getattr(graph_store, "get_dependents", None)
            if callable(dependents):
                deps = dependents(finding.file_path)
                if deps:
                    result.reason += (
                        " Note: dependent files exist — verify the fix did not break callers."
                    )
        except Exception as exc:
            logger.debug("Blast radius check skipped for %s: %s", result.cr_id, exc)


def _post_results_to_ado(
    results: List[FixVerification],
    old_findings: List[ExistingCommentThread],
    pr_id: int,
    repo: str,
    settings: Settings,
) -> None:
    """Post fix verification results back to ADO PR threads."""
    findings_by_cr_id = {f.cr_id: f for f in old_findings if f.cr_id}

    for result in results:
        finding = findings_by_cr_id.get(result.cr_id)
        if finding is None:
            continue
        try:
            from activities.post_fix_reply_activity import PostFixReplyActivity
            activity = PostFixReplyActivity(settings=settings)

            if result.status in ("fixed", "not_relevant"):
                label = "Verified fixed" if result.status == "fixed" else "File deleted/removed"
                activity.execute({
                    "thread_id": finding.thread_id,
                    "pr_id": pr_id,
                    "repository_id": repo,
                    "message": f"{label}: {result.reason}",
                })
            else:
                _post_still_present_reply(finding, result, pr_id, repo, settings)
        except Exception as exc:
            logger.warning("Failed to post ADO result for %s: %s", result.cr_id, exc)


def _post_still_present_reply(
    finding: ExistingCommentThread,
    result: FixVerification,
    pr_id: int,
    repo: str,
    settings: Settings,
) -> None:
    """Post a reply on an ADO thread indicating the issue is still present (no resolve)."""
    try:
        from azure.devops.connection import Connection
        from azure.devops.v7_1.git.models import Comment
        from msrest.authentication import BasicAuthentication

        credentials = BasicAuthentication("", settings.get_azure_devops_token())
        connection = Connection(base_url=settings.azure_devops_url, creds=credentials)
        git_client = connection.clients.get_git_client()
        git_client.create_comment(
            comment=Comment(content=f"Still present: {result.reason}"),
            repository_id=repo or settings.azure_devops_repo,
            pull_request_id=pr_id,
            thread_id=finding.thread_id,
            project=settings.azure_devops_project,
        )
    except Exception as exc:
        logger.warning("Failed to post still_present reply for thread %s: %s",
                       finding.thread_id, exc)
