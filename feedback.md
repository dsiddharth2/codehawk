# Large PR Batched Review — Plan Review

**Reviewer:** codehawk-reviewer
**Date:** 2026-05-02 10:30:00+00:00
**Verdict:** CHANGES NEEDED

> See the recent git history of this file to understand the context of this review.

---

## 1. Clear "Done" Criteria

**PASS.** Every task has explicit, testable done-when conditions. Task 1 specifies "Settings() loads with default values for all 6 new fields; existing tests still pass." Task 9 specifies minimum test case counts (8 for file_filter, 6 for smart_diff). Task 7 lists five distinct acceptance checks. No task relies on subjective judgment for completion — all can be verified mechanically.

---

## 2. High Cohesion / Low Coupling

**FAIL — Task 6 is a grab-bag.** Tasks 1-5 and 7-10 are well-scoped. However, Task 6 ("Update system prompt, raise truncation limits") bundles four unrelated changes across three files:

- System prompt rewording in `openai_runner.py` (semantic change to agent behavior)
- Tool result cap raise in `openai_runner.py` (numeric limit change)
- Search/read limits in `workspace_tools.py` (unrelated file, numeric limits)
- Graph timeout tier in `graph_builder.py` (unrelated file, timeout config)

These have no logical cohesion. The system prompt change affects agent behavior; the limit raises are mechanical. The graph timeout is entirely independent. Two developers working on "Task 6" would step on each other. Recommend splitting into: (a) system prompt update in openai_runner.py, and (b) raise truncation/timeout limits across files. Alternatively, keep as-is but rename to "Raise all caps and update system prompt" so the grab-bag nature is explicit and a single developer owns all of it.

**Doer:** fixed — split Task 6 into Task 6a (system prompt update in openai_runner.py) and Task 6b (raise truncation/timeout limits across openai_runner.py, workspace_tools.py, graph_builder.py). Updated progress.json with both new task entries.

---

## 3. Key Abstractions in Earliest Tasks

**PASS.** Phase 1 creates all three foundation modules that later phases depend on: Settings fields (Task 1), file_filter (Task 2), smart_diff (Task 3). Phase 2 consumes these. Phase 3 builds the orchestrator on top. The dependency graph flows cleanly downward.

---

## 4. Riskiest Assumption Validated Early

**NOTE — acceptable but could be stronger.** The risk register identifies "GraphStore not safe for sequential reuse across batches" as the highest-impact risk. This isn't validated until Task 7 (Phase 3). Ideally, a quick smoke test for GraphStore reuse (open DB, query from two ReviewJob-like contexts sequentially) would appear in Phase 1 or as a Phase 2 verify step. However, the plan does provide a fallback (rebuild graph per batch) and Task 7's done criteria explicitly require testing graph reuse. The risk is acknowledged and mitigated, just not front-loaded.

---

## 5. Later Tasks Reuse Early Abstractions (DRY)

**PASS.** Task 4 imports `summarize_diff`/`format_summary_for_agent`/`extract_hunks_in_range` from Task 3's `smart_diff.py`. Task 5 imports `parse_skip_extensions`/`filter_changed_files` from Task 2's `file_filter.py`. Task 7's `BatchReviewJob` reuses the batch-aware `ReviewJob` from Task 5. Task 8 consumes `BatchReviewJob` from Task 7. No logic is duplicated across tasks.

---

## 6. Phase Structure (2-3 Tasks + VERIFY)

**PASS.** Phase 1: 3 tasks + VERIFY. Phase 2: 3 tasks + VERIFY. Phase 3: 2 tasks + VERIFY. Phase 4: 2 tasks + VERIFY. All within the 2-3 range. Each VERIFY checkpoint specifies concrete checks (pytest, import verification, schema inspection).

---

## 7. Each Task Completable in One Session

**PASS (borderline on Tasks 5 and 7).** Task 5 packs four distinct changes into `review_job.py`: insert filtering, remove MAX_FILES cap, add 5 optional fields to ReviewJobConfig, and implement bypass logic for file_subset/pre_built_graph/batch_index. This is ambitious for one session but all changes are in a single file with clear specifications. Task 7 is marked "premium" tier appropriately — it creates the orchestrator with splitting, merging, error handling, and the single-session shortcut. The pseudocode in requirements.md provides enough guidance to complete in one focused session.

---

## 8. Dependencies Satisfied in Order

**PASS.** Verified all dependency chains:
- Task 4 (vcs_tools) depends on Task 3 (smart_diff) — Phase 2 after Phase 1 ✓
- Task 5 (review_job) depends on Task 2 (file_filter) — Phase 2 after Phase 1 ✓
- Task 7 (BatchReviewJob) depends on Tasks 2 and 5 — Phase 3 after Phase 2 ✓
- Task 8 (run_agent + prompt + post_findings) depends on Task 7 — same phase, sequential ✓
- Tasks 9-10 (tests) depend on Tasks 2, 3, 7 — Phase 4 after Phase 3 ✓

No task references a module that hasn't been created in a prior phase.

---

## 9. Vague or Ambiguous Tasks

**FAIL — two gaps where developers would diverge.**

**(a) `batch_max_turns` threading is unspecified.** Task 1 adds `batch_max_turns` (default 40) to Settings. Task 7 creates `BatchReviewJob` which constructs `ReviewJobConfig` per batch. But neither Task 5 nor Task 7 specifies that `ReviewJobConfig.max_turns` should be set to `settings.batch_max_turns` for per-batch jobs. Currently `ReviewJobConfig.max_turns` defaults to 40 (which happens to match), but the connection is implicit. One developer might thread it, another might leave the default. The plan should explicitly state in Task 7's change description: "Set `max_turns=self.settings.batch_max_turns` on per-batch ReviewJobConfig."

**Doer:** fixed — Task 7's `run()` step (6) now explicitly states: "and `max_turns=self.settings.batch_max_turns` set on `ReviewJobConfig` (this explicitly threads the per-batch turn budget from Settings into each batch's config)."

**(b) `skipped_files` on ReviewJobConfig is dangling.** Task 5 adds `skipped_files: Optional[list] = None` to `ReviewJobConfig`, but no task ever sets this field. Task 5 says `_build_changed_files_section()` should "add a summary line for skipped non-code files count" — but the skipped files come from `filter_changed_files()` in `create_findings()`, not from the config. Either remove `skipped_files` from ReviewJobConfig (the count is already available in `create_findings()` scope), or specify in Task 7 that `BatchReviewJob` passes `skipped_files` through the config so batch-mode jobs can display the count without re-filtering. Currently ambiguous.

**Doer:** fixed — removed `skipped_files` from ReviewJobConfig. Task 5 now specifies that the skipped count is stored locally in `create_findings()` scope and passed as a parameter to `_build_changed_files_section()`. ReviewJobConfig has 4 optional fields (batch_index, batch_total, file_subset, pre_built_graph).

---

## 10. Hidden Dependencies Between Tasks

**NOTE — one implicit dependency.** Task 8 modifies `post_findings.py` to read `get_settings().max_total_findings` instead of the hardcoded constant. This requires `get_settings()` to return a Settings instance with the `max_total_findings` field — which is added in Task 1. Task 8's blockers list only Task 7, but it also implicitly depends on Task 1. Since Task 1 is in Phase 1 and Task 8 is in Phase 3, the ordering is naturally satisfied, but the dependency should be documented for completeness.

**Doer:** fixed — Task 8 blockers now reads: "Task 1 (Settings fields for `max_total_findings`/`max_per_file_findings`), Task 7 (BatchReviewJob must exist)."

---

## 11. Risk Register

**PASS (with additions needed).** The existing 6 risks are well-identified with impact ratings and mitigations. However, three risks are missing:

| Risk | Impact | Suggested Mitigation |
|------|--------|---------------------|
| `batch_max_turns` vs `max_turns` confusion — two settings controlling the same thing | Med — per-batch turn budget silently wrong | Explicitly thread `batch_max_turns` into per-batch config; add comment explaining the relationship |
| Sequential batch execution on very large PRs (500+ files, ~20 batches) takes too long | Med — review times out in CI | Log per-batch timing; note in plan that parallel execution is a future enhancement (already mentioned in requirements edge cases) |
| Related files split across batches miss cross-file bugs | Med — false negatives | Already partially mitigated by shared graph, but worth noting that intra-batch context doesn't span batches |

**Doer:** fixed — all three risks added to the risk register in PLAN.md: (1) `batch_max_turns` vs `max_turns` confusion, (2) sequential execution timeout on very large PRs, (3) intra-batch context gap for cross-file bugs.

---

## 12. Alignment with Requirements Intent

**PASS.** The plan faithfully implements all three layers from requirements.md:

- **Layer 1 (File Filtering):** Tasks 1-2 and 5 implement skip_extensions config, file_filter module, and review_job integration. `.css` is explicitly kept (not in skip list). ✓
- **Layer 2 (Smart Diffs):** Tasks 1, 3, and 4 implement threshold config, smart_diff module, and vcs_tools integration with drill-in. 30KB threshold matches requirements. ✓
- **Layer 3 (Batched Sessions):** Tasks 1, 5, 7, and 8 implement batch_size config, batch-aware ReviewJob, BatchReviewJob orchestrator, and entry point update. Round-robin splitting, sequential execution, shared graph, and finding merge all match requirements. ✓

All 9 current bottlenecks from the requirements table are addressed by specific plan tasks. All 12 success criteria from requirements are covered. The plan reorganizes requirements steps into a cleaner phased structure (12 steps → 10 tasks in 4 phases) without losing any functionality.

**One minor deviation:** Requirements list `max_total_findings` and `max_per_file_findings` only in post_findings.py changes (Step 11), not as env vars. The plan promotes them to Settings fields in Task 1 — this is a reasonable improvement over requirements that makes caps configurable, not a misalignment.

---

## Codebase Verification

Verified all plan references against the actual codebase:

| Reference | Plan Says | Actual | Status |
|-----------|-----------|--------|--------|
| `review_job.py:212` MAX_FILES=100 | Remove this cap | `MAX_FILES = 100` at line 212 | ✓ Confirmed |
| `vcs_tools.py:210` [:10000] truncation | Replace with smart diff | `result.diff_text[:10000]` at line 210 | ✓ Confirmed |
| `openai_runner.py:44-51` "top 10-15 files" | Remove mention | Present in both graph/no-graph branches | ✓ Confirmed |
| `openai_runner.py:256,395` 30KB cap | Raise to 50KB | `30000` at both locations | ✓ Confirmed |
| `workspace_tools.py:104` 15KB search cap | Raise to 25KB | `15000` at line 104 | ✓ Confirmed |
| `workspace_tools.py:148` 500-line default | Raise to 1000 | `500` at line 148 | ✓ Confirmed |
| `graph_builder.py:18-24` timeout tiers | Add (100, 600) | 4 tiers, max 300s for 999999 files | ✓ Confirmed |
| `post_findings.py:31-32` hardcoded caps | Make dynamic | `MAX_TOTAL_FINDINGS=30`, `MAX_PER_FILE=5` | ✓ Confirmed |
| `file_filter.py`, `smart_diff.py`, `batch_review_job.py` | Create new | Do not exist yet | ✓ Confirmed |

All line numbers and values match. The plan accurately describes the current codebase state.

---

## Summary

**Verdict: CHANGES NEEDED** — The plan is architecturally sound, well-phased, and aligned with requirements. Two issues must be fixed before implementation begins:

**Must fix:**
1. **`batch_max_turns` threading** — Task 7 must explicitly specify that per-batch `ReviewJobConfig.max_turns` is set to `self.settings.batch_max_turns`. Without this, the connection between the config field (Task 1) and its usage (Task 7) is implicit and error-prone. **Doer:** fixed — see §9(a) annotation above.
2. **`skipped_files` on ReviewJobConfig** — Either remove this dangling field from Task 5 (the count is already available in `create_findings()` scope without config plumbing), or specify which task sets it and how batch-mode jobs use it. Currently no task ever populates it. **Doer:** fixed — see §9(b) annotation above.

**Should fix:**
3. **Task 6 cohesion** — Consider splitting or explicitly acknowledging the grab-bag nature. Not blocking, but increases session risk for the implementer. **Doer:** fixed — see §2 annotation above.
4. **Risk register additions** — Add the three missing risks identified above (batch_max_turns confusion, sequential execution time, cross-batch context gap). **Doer:** fixed — see §11 annotation above.
5. **Task 8 hidden dependency on Task 1** — Add Task 1 to Task 8's blockers list for documentation completeness. **Doer:** fixed — see §10 annotation above.
