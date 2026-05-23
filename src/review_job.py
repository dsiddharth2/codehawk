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
from models.review_models import ReviewMode
from smart_diff import summarize_diff, format_summary_for_agent

logger = logging.getLogger("codehawk.review_job")


def _filter_rules_for_version(rules_text: str, lang: str, frameworks: dict) -> str:
    """
    Filter a lang-rules markdown file to only include sections applicable to the
    detected version. Always includes "All Versions" sections. Version-specific
    sections (e.g. ".NET 8+", "Python 3.10+") are included only when the detected
    version meets or exceeds the section's version requirement.

    Returns the filtered markdown string (empty string if nothing applicable).
    """
    import re as _re

    lines = rules_text.splitlines()
    output: list[str] = []
    in_section = True  # top-level content is always included
    current_section_included = True

    def _section_applies(heading: str) -> bool:
        """Decide if a ## heading's version section should be included."""
        heading_lower = heading.lower()
        # "All Versions" and "All" sections always apply
        if "all versions" in heading_lower or heading_lower.strip("# ").startswith("all"):
            return True
        return _version_heading_matches(heading, lang, frameworks)

    for line in lines:
        if line.startswith("## "):
            current_section_included = _section_applies(line)
            if current_section_included:
                output.append(line)
        elif current_section_included:
            output.append(line)

    return "\n".join(output).strip()


def _version_heading_matches(heading: str, lang: str, frameworks: dict) -> bool:
    """Return True if the version heading applies given detected framework versions."""
    import re as _re

    # Extract a version requirement like ".NET 8+", "Python 3.10+", "TS 5.x", etc.
    heading_clean = heading.lstrip("# ").strip()

    # Patterns like "3.10+" or "8+"
    req_match = _re.search(r"(\d+(?:\.\d+)?)[\s]*\+", heading_clean)
    if not req_match:
        # Section like "Edition 2021", "TS 4.x" — include by default (version-specific but not gate-able)
        return True

    req_ver_str = req_match.group(1)

    def _parse(v: str) -> tuple:
        parts = v.lstrip("^~>=<! ").split(".")
        result = []
        for p in parts[:3]:
            try:
                result.append(int(p))
            except ValueError:
                break
        return tuple(result)

    req_ver = _parse(req_ver_str)

    # Determine which detected version to compare against based on language and heading keywords
    heading_lower = heading_clean.lower()
    detected_ver_str = ""

    if lang == "csharp" or ".net" in heading_lower:
        detected_ver_str = frameworks.get("dotnet", "")
    elif lang == "python" or "python" in heading_lower:
        detected_ver_str = frameworks.get("python", "")
    elif lang in ("javascript", "typescript") or "ts" in heading_lower:
        detected_ver_str = frameworks.get("typescript", "")
    elif lang == "java" or "java" in heading_lower:
        detected_ver_str = frameworks.get("java", "")
    elif lang == "go" or "go" in heading_lower:
        detected_ver_str = frameworks.get("go", "")
    elif lang == "rust" or "edition" in heading_lower:
        # For Rust editions compare edition year
        detected_ver_str = frameworks.get("edition", "")
    else:
        # No version info available — include the section
        return True

    if not detected_ver_str:
        return True  # version unknown — include to be safe

    detected_ver = _parse(detected_ver_str)
    if not detected_ver or not req_ver:
        return True

    return detected_ver >= req_ver


def _extract_title_from_comment(comment_text: str) -> str:
    """Extract bold title from structured comment markdown."""
    import re
    match = re.search(r'\*\*(.+?)\*\*', comment_text or "")
    if match:
        return match.group(1)[:80]
    return (comment_text or "")[:60].replace("\n", " ")


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
    source_commit_id: str = ""
    target_commit_id: str = ""
    previous_findings: Optional[list] = None
    review_mode: ReviewMode = ReviewMode.FULL

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
        if self.config.review_mode != ReviewMode.CHECK_NEW and self.config.previous_findings is None:
            try:
                from activities.fetch_pr_comments_activity import FetchPRCommentsActivity
                activity = FetchPRCommentsActivity(self.settings)
                threads = activity.execute(pr_id=self.config.pr_id, repository_id=self.config.repo or None)
                prev = [t for t in threads if t.cr_id]
                if prev:
                    self.config.previous_findings = prev
                    logger.info("Re-push detected (standalone): %d prior findings", len(prev))
            except Exception as exc:
                logger.debug("Previous findings fetch skipped: %s", exc)

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

        source_commit = getattr(pr_details, "source_commit_id", "") if pr_details else self.config.source_commit_id
        target_commit = getattr(pr_details, "target_commit_id", "") if pr_details else self.config.target_commit_id

        # Extract path strings for the runner (file_subset items may already be path strings)
        if self.config.file_subset is not None:
            changed_file_paths = [
                fc if isinstance(fc, str) else fc.path for fc in changed_files
            ]
        else:
            changed_file_paths = [fc.path for fc in changed_files]

        # Pre-compute graph analysis and fetch all diffs to inject into prompt
        analysis = self._pre_compute_analysis(graph_store, changed_file_paths)
        diffs, failed_diffs = self._pre_fetch_diffs(changed_file_paths, source_commit, target_commit)

        two_pass_enabled = getattr(self.settings, "two_pass_enabled", True)

        if two_pass_enabled:
            try:
                self._agent_result = self._run_two_pass(
                    changed_files, changed_file_paths, diffs, failed_diffs, analysis, graph_store,
                    source_commit, target_commit,
                )
            except Exception as exc:
                logger.warning("Two-pass flow failed (%s) — falling back to single-pass", exc)
                prompt = self._build_single_pass_prompt(
                    changed_files, skipped_count, analysis, diffs, failed_diffs,
                )
                self._agent_result = self._run_single_pass(
                    prompt, graph_store, changed_file_paths, source_commit, target_commit,
                )
        else:
            prompt = self._build_single_pass_prompt(
                changed_files, skipped_count, analysis, diffs, failed_diffs,
            )
            self._agent_result = self._run_single_pass(
                prompt, graph_store, changed_file_paths, source_commit, target_commit,
            )

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
    # Pre-computation — inject context to reduce agent tool calls
    # ------------------------------------------------------------------

    def _pre_compute_analysis(self, graph_store, file_paths: list[str]) -> dict:
        """Run graph analysis once and return structured results."""
        if not graph_store or not file_paths:
            return {}
        try:
            abs_paths = [str(self.config.workspace / f.lstrip("/")) for f in file_paths]
            result = graph_store.get_impact_radius(abs_paths)
            changed_nodes = result.get("changed_nodes", [])
            impacted_nodes = result.get("impacted_nodes", [])

            non_test_impacted = [
                n for n in list(changed_nodes) + list(impacted_nodes)
                if n.kind in ("Function", "Method") and not n.is_test
            ]
            risk_score = min(1.0, len(non_test_impacted) / 20.0) if non_test_impacted else 0.0

            review_priorities = [
                {"name": n.name, "file": n.file_path, "kind": n.kind}
                for n in changed_nodes
                if n.kind in ("Function", "Method", "Class")
            ]

            test_gaps = []
            for node in changed_nodes:
                if node.kind in ("Function", "Method") and not node.is_test:
                    tests = graph_store.get_transitive_tests(node.qualified_name)
                    if not tests:
                        test_gaps.append({"name": node.name, "file": node.file_path})

            impacted_functions = [
                {"name": n.name, "file": n.file_path, "kind": n.kind}
                for n in impacted_nodes
                if n.kind in ("Function", "Method")
            ]

            return {
                "risk_score": round(risk_score, 2),
                "review_priorities": review_priorities,
                "test_gaps": test_gaps,
                "impacted_files": list(result.get("impacted_files", [])),
                "impacted_functions": impacted_functions,
            }
        except Exception as exc:
            logger.warning("Pre-compute analysis failed: %s", exc)
            return {}

    def _pre_fetch_diffs(
        self, file_paths: list[str], source_commit: str, target_commit: str
    ) -> tuple[dict[str, str], list[str]]:
        """Fetch diffs for all files in one go. Returns ({path: diff_text}, failed_paths)."""
        if not source_commit or not target_commit:
            logger.warning("Cannot pre-fetch diffs: missing commit SHAs")
            return {}, []

        from activities.fetch_file_diff_activity import FetchFileDiffActivity, FetchFileDiffInput

        diff_activity = FetchFileDiffActivity(settings=self.settings)
        diffs: dict[str, str] = {}
        failed_diffs: list[str] = []
        threshold_kb = 30

        for fp in file_paths:
            try:
                result = diff_activity.execute(FetchFileDiffInput(
                    file_path=fp,
                    source_commit_id=source_commit,
                    target_commit_id=target_commit,
                    repository_id=self.config.repo,
                ))
                diff_text = result.diff_text
                if not diff_text:
                    continue
                summary = summarize_diff(diff_text, fp, threshold_kb)
                if summary.is_summarized:
                    diffs[fp] = format_summary_for_agent(summary)
                else:
                    diffs[fp] = diff_text
            except Exception as exc:
                logger.warning("Failed to pre-fetch diff for %s: %s", fp, exc)
                failed_diffs.append(fp)

        logger.info("Pre-fetched diffs for %d/%d files", len(diffs), len(file_paths))
        if failed_diffs:
            logger.warning("Failed to pre-fetch diffs for %d file(s): %s", len(failed_diffs), failed_diffs)
        return diffs, failed_diffs

    def _build_review_context(
        self, analysis: dict, diffs: dict[str, str], changed_files, failed_diffs: list[str] | None = None
    ) -> str:
        """Build a markdown context block with analysis + diffs for prompt injection."""
        lines = ["", "---", "", "## Pre-computed Review Context", ""]

        if analysis:
            lines.append(f"### Change Analysis (risk score: {analysis.get('risk_score', 0)})")
            lines.append("")

            priorities = analysis.get("review_priorities", [])
            if priorities:
                lines.append("**Review priorities** (ordered by impact):")
                for p in priorities[:20]:
                    lines.append(f"- `{p['name']}` ({p['kind']}) in `{p['file']}`")
                lines.append("")

            test_gaps = analysis.get("test_gaps", [])
            if test_gaps:
                lines.append("**Test gaps** (changed functions with no test coverage):")
                for tg in test_gaps[:15]:
                    lines.append(f"- `{tg['name']}` in `{tg['file']}`")
                lines.append("")

            impacted = analysis.get("impacted_functions", [])
            if impacted:
                lines.append("**Blast radius** (functions affected by these changes):")
                for imp in impacted[:20]:
                    lines.append(f"- `{imp['name']}` ({imp['kind']}) in `{imp['file']}`")
                lines.append("")

        # Risk classification table (100% coverage required)
        if changed_files:
            try:
                import risk_classifier as rc
                high_thresh = getattr(self.settings, "risk_high_threshold", 0.6)
                med_thresh = getattr(self.settings, "risk_medium_threshold", 0.3)
                risk_map = rc.classify(
                    changed_files,
                    graph_analysis=analysis or None,
                    high_threshold=high_thresh,
                    medium_threshold=med_thresh,
                )
                lines.append("### File Risk Classification (100% coverage required)")
                lines.append("")
                lines.append("| File | Risk | Depth | Reason |")
                lines.append("|------|------|-------|--------|")
                depth_label = {
                    "HIGH": "Full review + verify",
                    "MEDIUM": "Diff review + read if needed",
                    "LOW": "Diff scan",
                }
                for fp, fr in risk_map.items():
                    reason_str = ", ".join(fr.reasons) if fr.reasons else "—"
                    lines.append(
                        f"| `{fp}` | {fr.risk} | {depth_label[fr.risk]} | {reason_str} |"
                    )
                lines.append("")
                lines.append(
                    "You MUST review every file above. HIGH files get deep review with full-file reads. "
                    "LOW files get a diff-level scan. Every file must appear in `findings[]` or `files_clean[]`."
                )
                lines.append("")
            except Exception as exc:
                logger.warning("Risk classification failed (skipping table): %s", exc)

        if diffs:
            lines.append("### File Diffs")
            lines.append("")
            lines.append("All diffs are pre-fetched below. Do NOT call `get_file_diff` — review these directly.")
            lines.append("")

            for fp, diff_text in diffs.items():
                lines.append(f"#### `{fp}`")
                if diff_text.startswith("[DIFF SUMMARY]"):
                    lines.append(diff_text)
                else:
                    lines.append(f"```diff\n{diff_text}\n```")
                lines.append("")

        if not analysis and not diffs:
            lines.append("_No pre-computed context available. Use tools to fetch diffs and analysis._")

        if failed_diffs:
            lines.append("### Failed Diff Fetches")
            lines.append("")
            lines.append(
                "The following files could not be pre-fetched. You MUST fetch them via "
                "`read_local_file` or `get_file_content` during review: "
                + ", ".join(f"`{fp}`" for fp in failed_diffs)
                + ". These files still count toward 100% coverage."
            )
            lines.append("")

        lines.append("Do NOT call `get_change_analysis`, `get_blast_radius`, or `get_file_diff` — all data is above.")
        lines.append("Use `get_callers` or `get_file_content` only if you need additional context for a specific finding.")
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_verify_only_instructions(self) -> str:
        return (
            "\n\n---\n\n"
            "## VERIFY-ONLY MODE\n\n"
            "**This is a fix-verification run. You MUST follow these constraints:**\n\n"
            "- ONLY verify prior findings listed in the table above\n"
            "- `findings[]` MUST be empty — do NOT add new findings\n"
            "- Populate `fix_verifications[]` for EVERY cr_id in the table above\n"
            "- Skip Steps 3-5 of the standard review process\n"
            "- Each `fix_verifications` entry requires: `cr_id`, `status` (fixed/still_present/not_relevant), `reason`\n"
        )

    def _build_check_new_instructions(self) -> str:
        return (
            "\n\n---\n\n"
            "## FRESH REVIEW MODE\n\n"
            "**This is a fresh-review run. You MUST follow these constraints:**\n\n"
            "- There are no prior findings to verify\n"
            "- `fix_verifications[]` MUST be empty\n"
            "- Skip Step 6 (fix verification) of the standard review process\n"
            "- Focus entirely on identifying new code issues in the changed files\n"
        )

    def _build_previous_findings_section(self, previous_findings: list) -> str:
        capped = previous_findings[:30]
        lines = [
            "", "---", "",
            "## Previous Review Findings (Pre-fetched)",
            "",
            f"This PR has **{len(previous_findings)} existing review comment(s)** from a prior run."
            + (f" _(showing first {len(capped)})_" if len(capped) < len(previous_findings) else ""),
            "You MUST verify each one. Do NOT call `list_threads` -- data is below.",
            "",
            "| cr_id | File | Line | Severity | Title |",
            "|-------|------|------|----------|-------|",
        ]
        for f in capped:
            title = _extract_title_from_comment(f.comment_text)
            lines.append(
                f"| `{f.cr_id}` | `{f.file_path}` | {f.line_number} | {f.severity or '-'} | {title} |"
            )

        lines += [
            "",
            "### Instructions",
            "- For each cr_id, check current code state at the file/line",
            "- Classify as `fixed`, `still_present`, or `not_relevant`",
            "- Write into `fix_verifications[]` in findings.json",
            "- Do NOT re-flag `still_present` issues as new findings",
            "- Only add new `findings[]` for NEW code issues not in the table above",
            "",
        ]
        return "\n".join(lines)

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

        if self.config.previous_findings:
            text += self._build_previous_findings_section(self.config.previous_findings)

        if self.config.review_mode == ReviewMode.VERIFY_FIXES:
            text += self._build_verify_only_instructions()
        elif self.config.review_mode == ReviewMode.CHECK_NEW:
            text += self._build_check_new_instructions()

        text += self._build_review_modes_section()
        text += self._build_config_section()

        return text

    def _build_review_modes_section(self) -> str:
        """Inline review-mode checklists so the model sees the actual rules."""
        commands_dir = Path(__file__).resolve().parent.parent / "commands"
        mode_files = [
            "review-mode-standard.md",
            "review-mode-security.md",
            "review-mode-architecture.md",
            "review-mode-performance.md",
            "review-mode-migration.md",
        ]
        lines = ["", "---", "", "## Review Mode Checklists", ""]
        loaded = 0
        for name in mode_files:
            path = commands_dir / name
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
                lines.append(content)
                lines.append("")
                loaded += 1
            except Exception as exc:
                logger.debug("Failed to load review mode %s: %s", name, exc)
        if loaded:
            logger.info("Injected %d review mode checklists", loaded)
        return "\n".join(lines)

    def _build_config_section(self) -> str:
        """Pre-load project config files so the agent doesn't waste turns reading them.

        If .codereview.md is present, load it (project-specific rules take precedence).
        If not, run stack detection and inject version-filtered lang-rules.
        """
        config_files = [".codereview.md", ".codereview.yml", "AGENTS.md"]
        lines = ["", "---", "", "## Pre-loaded Project Config", ""]

        has_codereview_md = False
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
                    if name == ".codereview.md":
                        has_codereview_md = True
                except Exception:
                    pass

        if not has_codereview_md:
            # Auto-detect stack and inject version-appropriate rules
            lang_rules = self._build_lang_rules_section()
            if lang_rules:
                lines.extend(lang_rules)
                found_any = True

        if not found_any:
            lines.append("No project config files found (.codereview.md, .codereview.yml, AGENTS.md).")
            lines.append("Skip Step 1 — proceed directly to Step 2.")

        lines.append("")
        lines.append("Do NOT call `read_local_file` for these config files — they are already loaded above (or confirmed missing).")
        lines.append("")
        return "\n".join(lines)

    def _build_lang_rules_section(self) -> list[str]:
        """Run stack detection and return prompt lines with injected lang rules."""
        try:
            import stack_detector as sd
            profile = sd.detect(self.config.workspace)
        except Exception as exc:
            logger.warning("Stack detection failed: %s", exc)
            return []

        if not profile.languages:
            return []

        # Build version summary string for each detected language
        lang_summaries = []
        for lang in profile.languages:
            fw = profile.frameworks.get(lang, {})
            if fw:
                fw_str = ", ".join(f"{k}={v}" for k, v in fw.items())
                lang_summaries.append(f"{lang} ({fw_str})")
            else:
                lang_summaries.append(lang)

        lines = [
            f"### Auto-detected Stack",
            "",
            f"Detected: {', '.join(lang_summaries)}",
            f"Config files read: {', '.join(profile.detected_from) if profile.detected_from else 'none'}",
            "",
            "The following language-specific review rules apply:",
            "",
        ]

        commands_dir = Path(__file__).resolve().parent.parent / "commands"
        rules_injected = 0

        for lang in profile.languages:
            rules_file = commands_dir / "lang-rules" / f"{lang}.md"
            if not rules_file.is_file():
                continue
            try:
                full_rules = rules_file.read_text(encoding="utf-8")
                fw = profile.frameworks.get(lang, {})
                filtered = _filter_rules_for_version(full_rules, lang, fw)
                if filtered:
                    lines.append(filtered)
                    lines.append("")
                    rules_injected += 1
            except Exception as exc:
                logger.debug("Failed to load rules for %s: %s", lang, exc)

        if rules_injected == 0:
            return []

        logger.info("Injected lang-rules for: %s", ", ".join(profile.languages[:rules_injected]))
        return lines

    def _build_single_pass_prompt(
        self, changed_files, skipped_count, analysis, diffs, failed_diffs,
    ) -> str:
        """Build the full prompt for single-pass mode (or two-pass fallback)."""
        prompt = self._build_prompt(changed_files=changed_files, skipped_count=skipped_count)
        if analysis or diffs or failed_diffs:
            prompt += self._build_review_context(analysis, diffs, changed_files, failed_diffs)
        return prompt

    # ------------------------------------------------------------------
    # Two-pass review helpers
    # ------------------------------------------------------------------

    def _run_single_pass(
        self, prompt, graph_store, changed_file_paths, source_commit, target_commit,
    ) -> AgentResult:
        """Original single-pass agent loop (fallback path)."""
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
        return runner.run(prompt, max_turns=self.config.max_turns)

    def _run_two_pass(
        self, changed_files, changed_file_paths, diffs, failed_diffs, analysis,
        graph_store, source_commit, target_commit,
    ) -> AgentResult:
        """Two-pass review: Pass 1 (scan) → Pass 2 (verify)."""
        # --- Pass 1: Scan ---
        scan_prompt = self._build_scan_prompt(changed_files, diffs, analysis, failed_diffs)
        candidates, files_clean, scan_result = self._run_scan_pass(scan_prompt)

        # Store for fallback use in _run_verify_pass
        self._scan_candidates = candidates
        self._scan_files_clean = files_clean

        # If no candidates, skip Pass 2
        if not candidates:
            logger.info("Pass 1 found no candidates — skipping Pass 2")
            result = AgentResult()
            result.model = scan_result.model if hasattr(scan_result, 'model') else self.config.model
            result.input_tokens = scan_result.input_tokens
            result.output_tokens = scan_result.output_tokens
            result.total_tokens = scan_result.total_tokens
            result.duration_seconds = scan_result.duration_seconds if hasattr(scan_result, 'duration_seconds') else 0
            result.findings_data = {
                "pr_id": self.config.pr_id,
                "repo": self.config.repo,
                "vcs": self.config.vcs,
                "review_modes": ["standard"],
                "findings": [],
                "files_clean": files_clean,
                "fix_verifications": [],
            }
            return result

        # --- Pass 2: Verify ---
        verify_prompt = self._build_verify_prompt(candidates, diffs, changed_file_paths)
        verify_result = self._run_verify_pass(verify_prompt)

        # Merge token usage from both passes
        verify_result.input_tokens += scan_result.input_tokens
        verify_result.output_tokens += scan_result.output_tokens
        verify_result.total_tokens += scan_result.total_tokens

        return verify_result

    def _parse_candidates(self, raw_text: str) -> tuple[list, list[str]]:
        """Parse Pass 1 output into ScanCandidate list and files_clean list.

        Raises ValueError if JSON is malformed or missing required keys.
        """
        from agents.openai_runner import _extract_findings_json
        from models.review_models import ScanCandidate

        data = _extract_findings_json(raw_text)
        if data is None:
            raise ValueError(f"Failed to parse candidate JSON from Pass 1 output")

        if "candidates" not in data:
            raise ValueError("Missing 'candidates' key in Pass 1 output")

        candidates = []
        for c in data["candidates"]:
            candidates.append(ScanCandidate(
                file=c["file"],
                line=c.get("line", 0),
                category=c.get("category", "best_practices"),
                severity=c.get("severity", "suggestion"),
                title=c.get("title", ""),
                message=c.get("message", ""),
                needs_verification=c.get("needs_verification", False),
                verification_hint=c.get("verification_hint"),
                checklist_source=c.get("checklist_source"),
            ))

        files_clean = data.get("files_clean", [])
        return candidates, files_clean

    def _build_scan_prompt(
        self,
        changed_files,
        diffs: dict[str, str],
        analysis: dict,
        failed_diffs: list[str] | None = None,
    ) -> str:
        """Build the user prompt for Pass 1 (scan) — diffs + checklists + risk context."""
        commands_dir = Path(__file__).resolve().parent.parent / "commands"
        scan_template = commands_dir / "review-scan.md"

        lines = []
        if scan_template.is_file():
            lines.append(scan_template.read_text(encoding="utf-8"))
        else:
            lines.append("Identify candidate findings from the diffs below. Output JSON with candidates[] and files_clean[].")

        lines.append("")

        # Review mode checklists (same as single-pass — agent needs these to apply)
        lines.append(self._build_review_modes_section())

        # Lang-specific rules
        lines.append(self._build_config_section())

        # Risk context
        if analysis:
            lines.append("## Risk Context")
            lines.append("")
            lines.append(f"Risk score: {analysis.get('risk_score', 0)}")
            priorities = analysis.get("review_priorities", [])
            if priorities:
                lines.append("Review priorities:")
                for p in priorities[:10]:
                    lines.append(f"- `{p['name']}` in `{p['file']}`")
            test_gaps = analysis.get("test_gaps", [])
            if test_gaps:
                lines.append("Test gaps (no test coverage):")
                for tg in test_gaps[:10]:
                    lines.append(f"- `{tg['name']}` in `{tg['file']}`")
            lines.append("")

        # Diffs
        if diffs:
            lines.append("## File Diffs")
            lines.append("")
            for fp, diff_text in diffs.items():
                lines.append(f"### `{fp}`")
                if diff_text.startswith("[DIFF SUMMARY]"):
                    lines.append(diff_text)
                else:
                    lines.append(f"```diff\n{diff_text}\n```")
                lines.append("")

        if failed_diffs:
            lines.append("## Files With Failed Diff Fetch")
            lines.append("Mark these as `needs_verification: true` with hint 'Diff could not be pre-fetched':")
            for fp in failed_diffs:
                lines.append(f"- `{fp}`")
            lines.append("")

        return "\n".join(lines)

    def _build_verify_prompt(
        self,
        candidates: list,
        diffs: dict[str, str],
        all_file_paths: list[str],
    ) -> str:
        """Build the user prompt for Pass 2 (verify) — candidates + relevant diffs + scoring rules."""
        commands_dir = Path(__file__).resolve().parent.parent / "commands"
        verify_template = commands_dir / "review-verify.md"

        lines = []
        if verify_template.is_file():
            lines.append(verify_template.read_text(encoding="utf-8"))
        else:
            lines.append("Verify the candidate findings below. Output findings.json.")

        lines.append("")

        # Token substitution
        lines.append(f"PR ID: {self.config.pr_id}")
        lines.append(f"Repository: {self.config.repo}")
        lines.append(f"VCS: {self.config.vcs}")
        lines.append("")

        # Candidate findings table
        lines.append("## Candidate Findings to Verify")
        lines.append("")
        if candidates:
            lines.append("| # | File | Line | Category | Severity | Title | Needs Verification | Hint |")
            lines.append("|---|------|------|----------|----------|-------|--------------------|------|")
            for i, c in enumerate(candidates, 1):
                hint = c.verification_hint or "—"
                lines.append(
                    f"| {i} | `{c.file}` | {c.line} | {c.category} | {c.severity} | {c.title} | {c.needs_verification} | {hint} |"
                )
            lines.append("")
            lines.append("### Candidate Details")
            lines.append("")
            for i, c in enumerate(candidates, 1):
                lines.append(f"**Candidate {i}: {c.title}**")
                lines.append(f"- File: `{c.file}`, line {c.line}")
                lines.append(f"- Category: {c.category}, severity: {c.severity}")
                lines.append(f"- Message: {c.message}")
                if c.needs_verification and c.verification_hint:
                    lines.append(f"- **Verification needed:** {c.verification_hint}")
                lines.append("")
        else:
            lines.append("No candidates to verify. Produce empty findings.")
            lines.append("")

        # Files clean from Pass 1 (informational — carry forward)
        clean_files_from_candidates = set(all_file_paths) - {c.file for c in candidates}
        if clean_files_from_candidates:
            lines.append("## Files Already Clean (from Pass 1)")
            lines.append("These files had no candidates. Include them in `files_clean[]`:")
            for fp in sorted(clean_files_from_candidates):
                lines.append(f"- `{fp}`")
            lines.append("")

        # Relevant diffs only (files with candidates)
        candidate_files = {c.file for c in candidates}
        relevant_diffs = {fp: d for fp, d in diffs.items() if fp in candidate_files}
        if relevant_diffs:
            lines.append("## Diffs (candidate files only)")
            lines.append("")
            for fp, diff_text in relevant_diffs.items():
                lines.append(f"### `{fp}`")
                if diff_text.startswith("[DIFF SUMMARY]"):
                    lines.append(diff_text)
                else:
                    lines.append(f"```diff\n{diff_text}\n```")
                lines.append("")

        # Scoring rules
        scoring_path = commands_dir / "scoring.md"
        if scoring_path.is_file():
            try:
                scoring_content = scoring_path.read_text(encoding="utf-8")
                lines.append("## Scoring Reference")
                lines.append("")
                lines.append(scoring_content)
                lines.append("")
            except Exception:
                pass

        return "\n".join(lines)

    def _run_scan_pass(self, scan_prompt: str) -> tuple[list, list[str], "AgentResult"]:
        """Run Pass 1 (scan) — single-turn API call to identify candidates.

        Returns (candidates, files_clean, agent_result).
        Raises ValueError if all retries exhausted.
        """
        scan_system_prompt = (
            "You are a code review scanner. Identify candidate findings from pre-injected diffs. "
            "Output structured JSON with candidates[] and files_clean[]. "
            "You have NO tools available — work only from the diffs provided."
        )

        runner = OpenAIAgentRunner(
            settings=self.settings,
            workspace=self.config.workspace,
            model=self.config.model,
            pr_id=self.config.pr_id,
            repo=self.config.repo,
        )

        max_retries = getattr(self.settings, "scan_pass_max_retries", 1)
        last_error = None

        for attempt in range(1 + max_retries):
            prompt = scan_prompt
            if attempt > 0:
                prompt += (
                    "\n\n---\n\n**Your previous response was not valid JSON. "
                    "You MUST output a single JSON object in a ```json code fence "
                    "with keys 'candidates' (array) and 'files_clean' (array). Try again.**"
                )

            result = runner.run_single_turn(scan_system_prompt, prompt)

            if result.returncode != 0:
                last_error = ValueError(f"Pass 1 API call failed (attempt {attempt + 1})")
                logger.warning("Pass 1 attempt %d failed: API error", attempt + 1)
                continue

            try:
                candidates, files_clean = self._parse_candidates(result.raw_final_message)
                logger.info(
                    "Pass 1 complete: %d candidates (%d need verification), %d clean files",
                    len(candidates),
                    sum(1 for c in candidates if c.needs_verification),
                    len(files_clean),
                )
                return candidates, files_clean, result
            except ValueError as e:
                last_error = e
                logger.warning("Pass 1 attempt %d: %s", attempt + 1, e)

        raise ValueError(f"Pass 1 failed after {1 + max_retries} attempts: {last_error}")

    def _run_verify_pass(self, verify_prompt: str) -> "AgentResult":
        """Run Pass 2 (verify) — short agent loop with full history.

        If Pass 2 fails, falls back to using Pass 1 candidates as unverified findings.
        """
        verify_system_prompt = (
            "You are verifying candidate code review findings. "
            "For each candidate with needs_verification=true, use tool calls to check full file context. "
            "Confirm, refine, or drop each candidate. Add concrete suggestion code blocks. "
            f"You have {getattr(self.settings, 'verify_pass_max_turns', 10)} turns. "
            "Reserve the last 2 for output."
        )

        source_commit = self.config.source_commit_id
        target_commit = self.config.target_commit_id

        runner = OpenAIAgentRunner(
            settings=self.settings,
            workspace=self.config.workspace,
            model=self.config.model,
            pr_id=self.config.pr_id,
            repo=self.config.repo,
            graph_store=self.config.pre_built_graph,
            changed_files=[c.file for c in getattr(self, "_scan_candidates", [])],
            source_commit_id=source_commit,
            target_commit_id=target_commit,
        )

        max_turns = getattr(self.settings, "verify_pass_max_turns", 10)
        result = runner.run(verify_prompt, max_turns=max_turns, use_sliding_window=False)

        if result.findings_data is None and hasattr(self, "_scan_candidates"):
            logger.warning("Pass 2 failed to produce findings — using Pass 1 candidates as fallback")
            result.findings_data = self._candidates_to_findings()

        return result

    def _candidates_to_findings(self) -> dict:
        """Convert Pass 1 candidates to findings format as a fallback."""
        findings = []
        for i, c in enumerate(getattr(self, "_scan_candidates", []), 1):
            findings.append({
                "id": f"cr-{i:03d}",
                "file": c.file,
                "line": c.line,
                "severity": c.severity,
                "category": c.category,
                "title": c.title,
                "message": c.message + " (unverified — Pass 2 fallback)",
                "confidence": 0.6 if c.needs_verification else 0.75,
            })
        return {
            "pr_id": self.config.pr_id,
            "repo": self.config.repo,
            "vcs": self.config.vcs,
            "review_modes": ["standard"],
            "findings": findings,
            "files_clean": getattr(self, "_scan_files_clean", []),
            "fix_verifications": [],
        }

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
        data["pr_id"] = self.config.pr_id
        data["repo"] = self.config.repo
        data["vcs"] = self.config.vcs

        # Defense-in-depth mode guards: strip fields the agent should not have populated
        review_modes = data.setdefault("review_modes", [])
        if self.config.review_mode == ReviewMode.VERIFY_FIXES:
            data["findings"] = []
            if "verify_fixes" not in review_modes:
                review_modes.append("verify_fixes")
        elif self.config.review_mode == ReviewMode.CHECK_NEW:
            data["fix_verifications"] = []
            if "check_new" not in review_modes:
                review_modes.append("check_new")

        self._findings_path.parent.mkdir(parents=True, exist_ok=True)
        self._findings_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        count = len(data.get("findings", []))
        logger.info("Wrote %s (%d findings)", self._findings_path, count)
