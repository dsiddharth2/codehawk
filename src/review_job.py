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
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from agents.openai_runner import AgentResult, OpenAIAgentRunner
from config import Settings, get_settings


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
        prompt = self._build_prompt()

        # Phase 0: Build code graph (best-effort)
        graph_store = None
        try:
            from graph_builder import build_graph
            graph_store = build_graph(self.config.workspace)
            if graph_store:
                print(f"  Code graph built successfully.")
        except Exception as exc:
            print(f"  Graph build skipped: {exc}")

        runner = OpenAIAgentRunner(
            settings=self.settings,
            workspace=self.config.workspace,
            model=self.config.model,
            pr_id=self.config.pr_id,
            repo=self.config.repo,
            graph_store=graph_store,
            changed_files=[],
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

    def _build_prompt(self) -> str:
        if self.config.prompt_text:
            text = self.config.prompt_text
        else:
            text = self.config.prompt_path.read_text(encoding="utf-8")

        ws_posix = str(self.config.workspace).replace("\\", "/")
        text = text.replace("/workspace/", ws_posix + "/")
        text = text.replace("$PR_ID", str(self.config.pr_id))
        text = text.replace("$REPO", self.config.repo)
        text = text.replace("$VCS", self.config.vcs)

        return text

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
        print(f"  Wrote {self._findings_path} ({count} findings)")
