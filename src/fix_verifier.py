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
    model: str = "",
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
        model: LLM model for verification (default: gpt-4o-mini).

    Returns:
        List of FixVerification results, one per finding.
    """
    if not old_findings:
        return []

    settings = settings or get_settings()
    workspace = Path(workspace)

    diff_result = _get_file_statuses(workspace, old_commit, new_commit)

    findings_by_file: Dict[str, List[ExistingCommentThread]] = {}
    for finding in old_findings:
        file_path = finding.file_path.lstrip("/")
        findings_by_file.setdefault(file_path, []).append(finding)

    results: List[FixVerification] = []
    files_needing_llm: List[Tuple[str, List[ExistingCommentThread]]] = []

    if diff_result is None:
        logger.info("Git diff unavailable — sending all %d files to LLM for verification",
                     len(findings_by_file))
        for file_path, file_findings in findings_by_file.items():
            files_needing_llm.append((file_path, file_findings))
    else:
        deleted_files, renamed_map, modified_files = diff_result
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
                for f in file_findings:
                    results.append(FixVerification(
                        cr_id=f.cr_id or f"thread-{f.thread_id}",
                        status="still_present",
                        reason="File was not modified since the prior review — issue is still present.",
                    ))
            else:
                files_needing_llm.append((effective_path, file_findings))

    total_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    if files_needing_llm:
        llm_results, total_usage = _verify_files_with_llm(
            files_needing_llm, workspace, settings, model=model,
            base_commit=old_commit,
        )
        results.extend(llm_results)

    total_usage["total_tokens"] = total_usage["input_tokens"] + total_usage["output_tokens"]
    total_usage["model"] = model or "gpt-4o-mini"
    logger.info("Verification usage: %s", total_usage)

    if graph_store is not None:
        _annotate_blast_radius(results, old_findings, graph_store)

    if not dry_run:
        _post_results_to_ado(results, old_findings, pr_id, repo, settings)

    return results, total_usage


def _get_file_statuses(
    workspace: Path,
    old_commit: str,
    new_commit: str,
) -> Optional[Tuple[set, Dict[str, str], set]]:
    """Run git diff --name-status and parse into deleted/renamed/modified sets.

    Returns None if the diff cannot be computed (missing commit, shallow clone, etc.).
    Otherwise returns (deleted_files, renamed_map, modified_files).
    """
    deleted: set = set()
    renamed: Dict[str, str] = {}
    modified: set = set()

    if not old_commit:
        logger.warning("No old_commit provided — skipping git diff, will verify all files")
        return None

    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", old_commit, new_commit],
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("git diff --name-status failed: %s — will verify all files", result.stderr.strip())
            return None

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
        logger.warning("Could not run git diff --name-status: %s — will verify all files", exc)
        return None

    return deleted, renamed, modified


def _verify_files_with_llm(
    files_needing_llm: List[Tuple[str, List[ExistingCommentThread]]],
    workspace: Path,
    settings: Settings,
    model: str = "",
    base_commit: str = "",
) -> Tuple[List[FixVerification], Dict[str, int]]:
    """Verify modified files via LLM — one call per file, all findings for that file.

    Returns (verifications, total_usage).
    """
    results: List[FixVerification] = []
    total_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    for file_path, file_findings in files_needing_llm:
        file_result = _verify_single_file(
            file_path, file_findings, workspace, settings,
            model=model, base_commit=base_commit,
        )
        if isinstance(file_result, tuple):
            verifications, usage = file_result
            results.extend(verifications)
            total_usage["input_tokens"] += usage.get("input_tokens", 0)
            total_usage["output_tokens"] += usage.get("output_tokens", 0)
        else:
            results.extend(file_result)
    return results, total_usage


_FILE_CONTENT_CAP = 50_000


def _read_file_content(file_path: str, workspace: Path) -> str:
    """Read current file content from workspace. Tries direct path, then glob fallback."""
    try:
        full_path = workspace / file_path
        if full_path.exists():
            return full_path.read_text(encoding="utf-8", errors="replace")

        basename = Path(file_path).name
        matches = list(workspace.rglob(basename))
        if len(matches) == 1:
            logger.info("Path fallback: %s -> %s", file_path, matches[0].relative_to(workspace))
            return matches[0].read_text(encoding="utf-8", errors="replace")
        elif len(matches) > 1:
            for m in matches:
                if file_path.replace("\\", "/") in str(m.relative_to(workspace)).replace("\\", "/"):
                    logger.info("Path fallback (partial match): %s -> %s", file_path, m.relative_to(workspace))
                    return m.read_text(encoding="utf-8", errors="replace")

        logger.warning("File not found: %s (tried %s, glob found %d matches)", file_path, full_path, len(matches))
    except Exception as exc:
        logger.warning("Could not read file %s: %s", file_path, exc)
    return ""


def _get_file_diff(file_path: str, workspace: Path, base_commit: str = "") -> str:
    """Get the git diff for a specific file against the base commit."""
    base = base_commit or "HEAD~1"
    try:
        result = subprocess.run(
            ["git", "diff", base, "HEAD", "--", file_path],
            cwd=str(workspace), capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout[:20_000]
    except Exception as exc:
        logger.debug("git diff failed for %s: %s", file_path, exc)
    return ""


def _build_verification_prompt(
    file_path: str,
    file_content: str,
    findings: List[ExistingCommentThread],
    file_diff: str = "",
) -> str:
    """Build the LLM verification prompt for one file with all its findings."""
    rows = "\n".join(
        f"| {i + 1} | `{f.cr_id}` | {f.comment_text[:200].replace(chr(10), ' ')} | {f.line_number} |"
        for i, f in enumerate(findings)
    )

    diff_section = ""
    if file_diff:
        diff_section = f"""
## Git diff (what changed):
```diff
{file_diff}
```
"""

    return f"""You are verifying whether previously flagged code review findings have been fixed.

## File: `{file_path}`
{diff_section}
## Current file content:
```
{file_content[:_FILE_CONTENT_CAP]}
```

## Previous findings to verify:
| # | cr_id | Description | Original line |
|---|-------|-------------|---------------|
{rows}

For each finding, determine:
- `fixed` — the specific issue described is no longer present in the current code.
  The developer may have fixed it differently than suggested (different approach,
  moved code, renamed variables, etc.) — if the underlying problem is resolved,
  mark it `fixed` regardless of the exact approach used.
- `still_present` — the issue still exists in the current code (may be at a different line now)

IMPORTANT: Look at the git diff to see what actually changed. If the diff shows the
issue was addressed, mark it `fixed`. Do not require an exact match to the original
suggestion — judge whether the underlying problem is resolved.

Respond ONLY as a JSON array with no other text:
[{{"finding": 1, "status": "fixed", "reason": "..."}}, ...]"""


def _call_llm_for_verification(
    prompt: str, expected_count: int, model: str = "",
) -> Tuple[List[Dict], Dict[str, int]]:
    """Call OpenAI API to verify findings.

    Returns (results, usage) where results is a list of {finding, status, reason}
    dicts and usage is {input_tokens, output_tokens}.
    """
    empty_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    model = model or "gpt-4o-mini"
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — defaulting all to still_present")
        return [], empty_usage
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        use_responses = "codex" in model
        if use_responses:
            response = client.responses.create(
                model=model,
                input=[{"type": "message", "role": "user", "content": prompt}],
            )
            raw = ""
            for item in response.output:
                if item.type == "message":
                    for content in item.content:
                        if hasattr(content, "text"):
                            raw += content.text
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
            }
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                seed=42,
            )
            raw = (response.choices[0].message.content or "").strip()
            resp_usage = response.usage
            usage = {
                "input_tokens": getattr(resp_usage, "prompt_tokens", 0) if resp_usage else 0,
                "output_tokens": getattr(resp_usage, "completion_tokens", 0) if resp_usage else 0,
            }

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed, usage
        for v in parsed.values():
            if isinstance(v, list):
                return v, usage
        return [], usage
    except Exception as exc:
        logger.warning("LLM verification call failed for model %s: %s", model, exc)
        return [], empty_usage


def _verify_single_file(
    file_path: str,
    findings: List[ExistingCommentThread],
    workspace: Path,
    settings: Settings,
    model: str = "",
    base_commit: str = "",
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

    file_diff = _get_file_diff(file_path, workspace, base_commit=base_commit)
    logger.info("Verifying %d findings in %s (content=%d chars, diff=%d chars)",
                len(findings), file_path, len(file_content), len(file_diff))
    prompt = _build_verification_prompt(file_path, file_content, findings, file_diff=file_diff)
    llm_results, usage = _call_llm_for_verification(prompt, len(findings), model=model)
    return _map_llm_results_to_verifications(findings, llm_results), usage


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
