"""
ReviewJob — two-phase PR review pipeline.

Phase 1 (create_findings): Run the OpenAI agent to produce findings.json
Phase 2 (publish_results): Score, gate, and post comments to VCS

Usage:
    job = ReviewJob(
        pr_id=123,
        repo="MyRepo",
        workspace=Path("/workspace"),
        model="o3",
        prompt_path=Path("commands/review-pr-core.md"),
    )
    findings_path = job.create_findings()
    output = job.publish_results(dry_run=True)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from agents.openai_runner import AgentResult, OpenAIAgentRunner
from config import Settings, get_settings

logger = logging.getLogger("codehawk.review_job")


@dataclass
class ReviewJobConfig:
    pr_id: int
    repo: str
    workspace: Path
    model: str = "o3"
    max_turns: int = 40
    prompt_path: Optional[Path] = None
    prompt_text: Optional[str] = None
    vcs: str = "ado"
    batch_index: Optional[int] = None
    batch_total: Optional[int] = None
    file_subset: Optional[list] = None
    pre_built_graph: Any = None

    def __post_init__(self):
        self.workspace = Path(self.workspace)
        if not self.prompt_path and not self.prompt_text:
            raise ValueError("Either prompt_path or prompt_text is required")


class ReviewJob:
    """Orchestrates the full codehawk review pipeline."""

    def __init__(self, config: ReviewJobConfig, settings: Settings | None = None):
        self.config = config
        self.settings = settings or get_settings()
        self._agent_result: Optional[AgentResult] = None
        self._findings_path = self.config.workspace / ".cr" / "findings.json"

    @property
    def findings_path(self) -> Path:
        return self._findings_path

    # ------------------------------------------------------------------
    # Phase 1 — create findings.json
    # ------------------------------------------------------------------

    def create_findings(self) -> Path:
        """Run the agent and write findings.json. Returns the path."""
        changed_files = []
        pr_details = None
        skipped_count = 0

        if self.config.file_subset is not None:
            # Batch mode: use the pre-filtered subset directly, skip PR pre-fetch
            changed_files = self.config.file_subset
            logger.info("Batch mode: using file_subset (%d files)", len(changed_files))
        else:
            # Normal mode: pre-fetch PR data so changed_files can be injected into the prompt
            try:
                from activities.fetch_pr_details_activity import FetchPRDetailsActivity
                from models.review_models import FetchPRDetailsInput
                pr_details = FetchPRDetailsActivity(self.settings).execute(
                    FetchPRDetailsInput(pr_id=self.config.pr_id, repository_id=self.config.repo)
                )
                all_files = pr_details.file_changes
                logger.info("Pre-fetched PR data: %d changed files", len(all_files))

                # Filter non-code files
                from file_filter import parse_skip_extensions, filter_changed_files
                skip_exts = parse_skip_extensions(self.settings.skip_extensions)
                changed_files, skipped = filter_changed_files(all_files, skip_exts)
                skipped_count = len(skipped)
                logger.info(
                    "File filtering: %d code files kept, %d non-code/deleted skipped",
                    len(changed_files), skipped_count,
                )
            except Exception as exc:
                logger.warning("PR pre-fetch skipped: %s", exc)

        prompt = self._build_prompt(changed_files=changed_files, skipped_count=skipped_count)

        # Phase 0: Build code graph (or reuse pre-built graph from batch orchestrator)
        graph_store = self.config.pre_built_graph
        if graph_store is not None:
            logger.info("Using pre-built graph from batch orchestrator")
        else:
            try:
                from graph_builder import build_graph
                graph_store = build_graph(self.config.workspace, changed_file_count=len(changed_files))
                if graph_store:
                    logger.info("Code graph built successfully")
                else:
                    logger.warning("Graph build returned None — blast radius unavailable")
            except Exception as exc:
                logger.warning("Graph build failed: %s", exc)

        source_commit = getattr(pr_details, "source_commit_id", "") if pr_details else ""
        target_commit = getattr(pr_details, "target_commit_id", "") if pr_details else ""

        # Extract path strings for the runner (file_subset items may already be path strings)
        if self.config.file_subset is not None:
            changed_file_paths = [
                fc if isinstance(fc, str) else fc.path for fc in changed_files
            ]
        else:
            changed_file_paths = [fc.path for fc in changed_files]

        runner = OpenAIAgentRunner(
            settings=self.settings,
            workspace=self.config.workspace,
            model=self.config.model,
            pr_id=self.config.pr_id,
            repo=self.config.repo,
            graph_store=graph_store,
            changed_files=changed_file_paths,
            source_commit_id=source_commit,
            target_commit_id=target_commit,
        )

        self._agent_result = runner.run(prompt, max_turns=self.config.max_turns)

        if not self._agent_result.findings_data:
            import warnings
            warnings.warn(
                "Agent did not produce extractable findings JSON; emergency findings were generated.",
                stacklevel=2,
            )

        self._stamp_usage(self._agent_result)
        self._write_findings(self._agent_result.findings_data)

        return self._findings_path

    # ------------------------------------------------------------------
    # Phase 2 — score, gate, post comments
    # ------------------------------------------------------------------

    def publish_results(
        self,
        dry_run: bool = False,
        commit_id: str = "",
    ) -> Dict[str, Any]:
        """Run post_findings on the findings.json. Returns the structured output."""
        import post_findings as pf

        if not self._findings_path.exists():
            raise FileNotFoundError(
                f"No findings.json at {self._findings_path}. Run create_findings() first."
            )

        return pf.run(
            findings_path=str(self._findings_path),
            dry_run=dry_run,
            workspace=str(self.config.workspace),
            commit_id=commit_id,
        )

    # ------------------------------------------------------------------
    # Convenience — run both phases
    # ------------------------------------------------------------------

    def run(self, dry_run: bool = False, commit_id: str = "") -> Dict[str, Any]:
        """Run Phase 1 + Phase 2 end-to-end. Returns Phase 2 output."""
        self.create_findings()
        return self.publish_results(dry_run=dry_run, commit_id=commit_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_prompt(self, changed_files=None, skipped_count: int = 0) -> str:
        if self.config.prompt_text:
            text = self.config.prompt_text
        else:
            text = self.config.prompt_path.read_text(encoding="utf-8")

        ws_posix = str(self.config.workspace).replace("\\", "/")
        text = text.replace("/workspace/", ws_posix + "/")
        text = text.replace("$PR_ID", str(self.config.pr_id))
        text = text.replace("$REPO", self.config.repo)
        text = text.replace("$VCS", self.config.vcs)

        if changed_files:
            text += self._build_changed_files_section(changed_files, skipped_count=skipped_count)

        # Append batch context when running as part of a batched review
        if self.config.batch_index is not None and self.config.batch_total is not None:
            total_code_files = len(changed_files) if changed_files else 0
            text += (
                f"\n\n---\n\n**Batch {self.config.batch_index}/{self.config.batch_total}** — "
                f"reviewing {total_code_files} files of the total code files in this PR. "
                "Review ALL files assigned to this batch. "
                "Non-code files have already been pre-filtered by the orchestrator.\n"
            )

        text += self._build_config_section()

        return text

    def _build_config_section(self) -> str:
        """Pre-load project config files so the agent doesn't waste turns reading them."""
        config_files = [".codereview.md", ".codereview.yml", "AGENTS.md"]
        lines = ["", "---", "", "## Pre-loaded Project Config", ""]

        found_any = False
        for name in config_files:
            path = self.config.workspace / name
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")[:5000]
                    lines.append(f"### {name}")
                    lines.append(f"```\n{content}\n```")
                    lines.append("")
                    found_any = True
                except Exception:
                    pass

        if not found_any:
            lines.append("No project config files found (.codereview.md, .codereview.yml, AGENTS.md).")
            lines.append("Skip Step 1 — proceed directly to Step 2.")

        lines.append("")
        lines.append("Do NOT call `read_local_file` for these config files — they are already loaded above (or confirmed missing).")
        lines.append("")
        return "\n".join(lines)

    def _build_changed_files_section(self, file_changes, skipped_count: int = 0) -> str:
        """Build the pre-fetched PR data section to append to the prompt."""
        sorted_changes = sorted(
            file_changes,
            key=lambda fc: (fc.additions + fc.deletions) if hasattr(fc, "additions") else 0,
            reverse=True,
        )

        lines = [
            "",
            "---",
            "",
            "## Pre-fetched PR Data",
            "",
            f"The following {len(file_changes)} code file(s) were changed in this PR (pre-fetched to save turns):",
            "",
            "| File | Change | +Lines | -Lines |",
            "|------|--------|--------|--------|",
        ]
        for fc in sorted_changes:
            if hasattr(fc, "path"):
                lines.append(f"| `{fc.path}` | {fc.change_type} | {fc.additions} | {fc.deletions} |")
            else:
                lines.append(f"| `{fc}` | — | — | — |")

        if skipped_count > 0:
            lines.append(
                f"\n_{skipped_count} non-code/deleted file(s) were filtered out and are not shown._"
            )

        lines += [
            "",
            "Use these paths with `get_change_analysis`. Do NOT call `get_pr` — the data is already above.",
            "",
        ]
        return "\n".join(lines)

    def _stamp_usage(self, result: AgentResult):
        result.findings_data["usage"] = {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens": result.total_tokens,
            "model": result.model,
            "duration_seconds": result.duration_seconds,
        }
        result.findings_data.setdefault("tool_calls", result.tool_calls_count)
        result.findings_data.setdefault("agent", "openai-api")

    def _write_findings(self, data: dict):
        self._findings_path.parent.mkdir(parents=True, exist_ok=True)
        self._findings_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        count = len(data.get("findings", []))
        logger.info("Wrote %s (%d findings)", self._findings_path, count)
