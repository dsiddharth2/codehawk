"""
BatchReviewJob — orchestrator for batched PR review.

Pre-fetches PR data once, filters non-code files, builds the graph once,
then splits code files into batches and runs each as an independent ReviewJob.
Merges findings from all batches into a single findings.json.

For small PRs (<= batch_size files), delegates to a single ReviewJob session
for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import Settings, get_settings
from file_filter import filter_changed_files, parse_skip_extensions
from models.review_models import ReviewMode
from review_job import ReviewJob, ReviewJobConfig

logger = logging.getLogger("codehawk.batch_review")


class BatchReviewJob:
    """Orchestrates batched review of large PRs."""

    def __init__(
        self,
        pr_id: int,
        repo: str,
        workspace: Path,
        model: str = "o3",
        prompt_path: Optional[Path] = None,
        vcs: str = "ado",
        settings: Optional[Settings] = None,
        review_mode: ReviewMode = ReviewMode.FULL,
    ):
        self.pr_id = pr_id
        self.repo = repo
        self.workspace = Path(workspace)
        self.model = model
        self.prompt_path = prompt_path
        self.vcs = vcs
        self.settings = settings or get_settings()
        self.review_mode = review_mode

    def run(self, dry_run: bool = False, commit_id: str = "") -> Dict[str, Any]:
        """Run the full batched review pipeline.

        1. Pre-fetch PR data
        2. Filter non-code files
        3. Build graph once
        4. If small PR, delegate to single ReviewJob
        5. Otherwise split into batches and run each
        6. Merge findings and publish results
        """
        start_time = time.time()

        # --- Step 1: Pre-fetch PR data ---
        pr_details = self._fetch_pr_details()
        all_files = pr_details.file_changes if pr_details else []
        logger.info("Pre-fetched PR data: %d changed files", len(all_files))

        # --- Step 2: Filter non-code files ---
        skip_exts = parse_skip_extensions(self.settings.skip_extensions)
        code_files, skipped = filter_changed_files(all_files, skip_exts)
        logger.info(
            "File filtering: %d code files kept, %d non-code/deleted skipped",
            len(code_files), len(skipped),
        )

        # --- Step 2b: Extract commit SHAs for tool resolution ---
        source_commit = getattr(pr_details, "source_commit_id", "") or ""
        target_commit = getattr(pr_details, "target_commit_id", "") or ""
        logger.info("Commit SHAs: source=%s target=%s", source_commit[:12], target_commit[:12])

        # --- Step 2c: Fetch previous findings for re-push detection ---
        if self.review_mode == ReviewMode.CHECK_NEW:
            previous_findings = []
            logger.info("CHECK_NEW mode: skipping previous findings fetch")
        else:
            previous_findings = self._fetch_previous_findings()
            if previous_findings:
                logger.info("Re-push detected: %d previous findings", len(previous_findings))

        # --- Step 3: Build graph once ---
        graph_store = self._build_graph(len(code_files))

        # --- Step 4: Single-session shortcut for small PRs ---
        if len(code_files) <= self.settings.batch_size:
            logger.info(
                "Small PR (%d files <= batch_size %d): single-session review",
                len(code_files), self.settings.batch_size,
            )
            config = ReviewJobConfig(
                pr_id=self.pr_id,
                repo=self.repo,
                workspace=self.workspace,
                model=self.model,
                prompt_path=self.prompt_path,
                vcs=self.vcs,
                file_subset=code_files,
                pre_built_graph=graph_store,
                source_commit_id=source_commit,
                target_commit_id=target_commit,
                previous_findings=previous_findings or None,
                review_mode=self.review_mode,
            )
            job = ReviewJob(config, settings=self.settings)
            return job.run(dry_run=dry_run, commit_id=commit_id)

        # --- Step 5: Split into batches ---
        batches = self._split_into_batches(code_files, self.settings.batch_size)
        batch_total = len(batches)
        logger.info("Splitting %d code files into %d batches", len(code_files), batch_total)

        # --- Step 6: Run batches in parallel (max 3 concurrent workers) ---
        batch_results: List[Dict[str, Any]] = []
        max_workers = min(3, batch_total)
        logger.info("Running %d batches with max %d parallel workers", batch_total, max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    self._run_batch_with_retry,
                    batch_files=batch_files,
                    batch_index=i,
                    batch_total=batch_total,
                    graph_store=graph_store,
                    source_commit_id=source_commit,
                    target_commit_id=target_commit,
                    previous_findings=previous_findings,
                ): i
                for i, batch_files in enumerate(batches, start=1)
            }
            for future in as_completed(futures):
                batch_num = futures[future]
                try:
                    result = future.result()
                    batch_results.append(result)
                    logger.info("Batch %d/%d completed: %d findings", batch_num, batch_total,
                                len(result.get("findings", [])))
                except Exception as exc:
                    logger.error("Batch %d/%d failed: %s", batch_num, batch_total, exc)
                    # Failed batches don't crash the pipeline
                # Failed batches don't crash the pipeline

        # --- Step 7: Merge findings ---
        merged = self._merge_results(batch_results)
        merged["pr_id"] = self.pr_id
        merged["repo"] = self.repo
        merged["vcs"] = self.vcs
        merged.setdefault("review_modes", ["standard"])
        merged.setdefault("agent", "openai-api")
        merged.setdefault("tool_calls", sum(r.get("tool_calls", 0) for r in batch_results))
        duration = time.time() - start_time
        merged.setdefault("usage", {})["total_duration_seconds"] = round(duration, 2)

        # --- Step 8: Write merged findings.json ---
        findings_path = self.workspace / ".cr" / "findings.json"
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        logger.info(
            "Wrote merged findings: %d findings in %.1fs",
            len(merged.get("findings", [])), duration,
        )

        # --- Publish results ---
        import post_findings as pf
        return pf.run(
            findings_path=str(findings_path),
            dry_run=dry_run,
            workspace=str(self.workspace),
            commit_id=commit_id,
        )

    def _fetch_pr_details(self):
        """Pre-fetch PR details from VCS."""
        try:
            from activities.fetch_pr_details_activity import FetchPRDetailsActivity
            from models.review_models import FetchPRDetailsInput

            return FetchPRDetailsActivity(self.settings).execute(
                FetchPRDetailsInput(pr_id=self.pr_id, repository_id=self.repo)
            )
        except Exception as exc:
            logger.warning("PR pre-fetch failed: %s", exc)
            return None

    def _fetch_previous_findings(self) -> list:
        """Fetch existing review threads with cr_id markers."""
        try:
            from activities.fetch_pr_comments_activity import FetchPRCommentsActivity
            activity = FetchPRCommentsActivity(settings=self.settings)
            threads = activity.execute(pr_id=self.pr_id, repository_id=self.repo or None)
            return [t for t in threads if t.cr_id]
        except Exception as exc:
            logger.warning("Failed to fetch previous findings: %s", exc)
            return []

    def _build_graph(self, changed_file_count: int):
        """Build the code graph once for reuse across batches."""
        try:
            from graph_builder import build_graph
            graph_store = build_graph(self.workspace, changed_file_count=changed_file_count)
            if graph_store:
                logger.info("Code graph built successfully")
            else:
                logger.warning("Graph build returned None")
            return graph_store
        except Exception as exc:
            logger.warning("Graph build failed: %s", exc)
            return None

    def _run_batch(
        self,
        batch_files: List,
        batch_index: int,
        batch_total: int,
        graph_store: Any,
        source_commit_id: str = "",
        target_commit_id: str = "",
        previous_findings: list | None = None,
    ) -> Dict[str, Any]:
        """Run a single batch as a ReviewJob and return its findings_data."""
        batch_previous = None
        if previous_findings:
            batch_paths = {
                (fc.path if hasattr(fc, 'path') else fc).lstrip('/')
                for fc in batch_files
            }
            batch_previous = [
                f for f in previous_findings
                if f.file_path.lstrip('/') in batch_paths
            ] or None

        config = ReviewJobConfig(
            pr_id=self.pr_id,
            repo=self.repo,
            workspace=self.workspace,
            model=self.model,
            max_turns=self.settings.batch_max_turns,
            prompt_path=self.prompt_path,
            vcs=self.vcs,
            batch_index=batch_index,
            batch_total=batch_total,
            file_subset=batch_files,
            pre_built_graph=graph_store,
            source_commit_id=source_commit_id,
            target_commit_id=target_commit_id,
            previous_findings=batch_previous,
            review_mode=self.review_mode,
        )
        job = ReviewJob(config, settings=self.settings)
        job.create_findings()

        # Read back the findings data
        findings_path = job.findings_path
        if findings_path.exists():
            return json.loads(findings_path.read_text(encoding="utf-8"))
        return {"findings": []}

    def _run_batch_with_retry(
        self,
        batch_files: List,
        batch_index: int,
        batch_total: int,
        graph_store: Any,
        source_commit_id: str = "",
        target_commit_id: str = "",
        previous_findings: list | None = None,
    ) -> Dict[str, Any]:
        """Wrapper around _run_batch with exponential backoff retry on rate-limit errors.

        Retries up to 3 times with delays of 1s, 2s, 4s.
        A failure in one batch does not affect other batches.
        """
        delays = [1, 2, 4]
        last_exc: Exception | None = None

        for attempt, delay in enumerate(delays, start=1):
            try:
                return self._run_batch(
                    batch_files=batch_files,
                    batch_index=batch_index,
                    batch_total=batch_total,
                    graph_store=graph_store,
                    source_commit_id=source_commit_id,
                    target_commit_id=target_commit_id,
                    previous_findings=previous_findings,
                )
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()
                is_rate_limit = (
                    "429" in exc_str
                    or "ratelimit" in exc_str
                    or "too many requests" in exc_str
                    or type(exc).__name__.lower() in ("ratelimiterror", "apierror")
                )
                if is_rate_limit and attempt <= len(delays):
                    logger.warning(
                        "Batch %d/%d rate-limited (attempt %d) — retrying in %ds",
                        batch_index, batch_total, attempt, delay,
                    )
                    time.sleep(delay)
                else:
                    raise

        raise last_exc  # exhausted retries

    @staticmethod
    def _split_into_batches(code_files: List, batch_size: int) -> List[List]:
        """Split code files into balanced batches using round-robin by churn descending.

        Files are sorted by (additions + deletions) descending, then distributed
        round-robin across batches so each batch gets a mix of high-churn and
        low-churn files for balanced workload.
        """
        if not code_files:
            return []

        # Sort by churn descending
        sorted_files = sorted(
            code_files,
            key=lambda fc: (fc.additions + fc.deletions) if hasattr(fc, "additions") else 0,
            reverse=True,
        )

        num_batches = max(1, (len(sorted_files) + batch_size - 1) // batch_size)
        batches: List[List] = [[] for _ in range(num_batches)]

        for i, fc in enumerate(sorted_files):
            batches[i % num_batches].append(fc)

        return batches

    @staticmethod
    def _merge_results(batch_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge findings from multiple batches.

        - Concatenate all findings
        - Dedup by (file, line, title)
        - Re-sequence cr-ids as cr-001, cr-002, ...
        - Sum usage stats
        - Union review_modes
        """
        all_findings = []
        batch_summaries = []
        total_input_tokens = 0
        total_output_tokens = 0
        total_duration = 0.0
        review_modes = set()
        model = ""

        for result in batch_results:
            all_findings.extend(result.get("findings", []))
            if result.get("summary"):
                batch_summaries.append(result["summary"].strip())

            usage = result.get("usage", {})
            total_input_tokens += usage.get("input_tokens", 0)
            total_output_tokens += usage.get("output_tokens", 0)
            total_duration += usage.get("duration_seconds", 0)
            if usage.get("model"):
                model = usage["model"]

            for mode in result.get("review_modes", []):
                review_modes.add(mode)

        # Dedup by (file, line, title)
        seen = set()
        deduped = []
        for f in all_findings:
            key = (f.get("file", ""), f.get("line", 0), f.get("title", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(f)

        # Re-sequence cr-ids
        for i, f in enumerate(deduped, start=1):
            f["id"] = f"cr-{i:03d}"

        merged = {
            "findings": deduped,
            "usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "model": model,
                "duration_seconds": round(total_duration, 2),
            },
        }

        if review_modes:
            merged["review_modes"] = sorted(review_modes)

        if batch_summaries:
            merged["summary"] = "\n\n".join(batch_summaries)

        all_fix_verifications = []
        for result in batch_results:
            all_fix_verifications.extend(result.get("fix_verifications", []))

        seen_cr_ids: set[str] = set()
        deduped_fv = []
        for fv in all_fix_verifications:
            cr_id = fv.get("cr_id", "")
            if cr_id and cr_id not in seen_cr_ids:
                seen_cr_ids.add(cr_id)
                deduped_fv.append(fv)

        merged["fix_verifications"] = deduped_fv

        return merged
