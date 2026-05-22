# CodeHawk Sprint 3 — Review Quality + Coverage Enforcement

**Date:** 2026-05-22
**Prerequisite:** Sprint 2 merged (PR #3 — graph integration)
**Evidence:** `code-root-cause-analysis.md` (all claims verified against `origin/main @ ec3512e`)
**Branch:** `feat/review-quality` (from `origin/main` after PR #3 merge)

---

## The Core Problem

CodeHawk was built to be turn-efficient (minimize LLM API calls). Three sources give the agent conflicting tool-call budgets:

- `review-pr-core.md` Step 0: "max 10 tool calls"
- `docker-compose.yml`: `MAX_TURNS=40`
- `openai_runner.py` TURN EFFICIENCY block: "ZERO or minimal tool calls"

The agent interprets the most restrictive guidance and self-limits. It guesses instead of calling `read_local_file` to verify — producing ~30% false positives. Meanwhile, there's no enforcement that every file gets reviewed — the agent can skip files silently, and the scorer doesn't penalize incomplete coverage.

This sprint fixes the quality/coverage gap by balancing three constraints: **cost, accuracy, and coverage**. 100% file coverage is non-negotiable — every code file in a PR gets reviewed. Within that constraint, the risk classifier controls how much depth the agent spends per file so the 40-turn budget is used wisely. High-risk files get full-file reads and caller checks; low-risk files get a diff-level scan. The agent has 40 turns max — no more, no less — and every source of turn limits must agree on that number.

---

## Measurement

Each problem has a before/after metric. Measure by sampling 10 production PRs reviewed before and after the sprint.

| Problem | Before (baseline) | Target | How to Measure |
|---------|-------------------|--------|----------------|
| P1 — False positives | ~30% of findings | <10% | Manual classification of findings as true/false positive |
| P2 — Test noise | ~40% of comments are "add tests" | 0% gate impact | Verify `testing` findings have 0 penalty points in score breakdown |
| P3 — Run inconsistency | Findings vary across runs | >80% overlap | Run same PR twice, compute Jaccard similarity on finding (file, title) pairs |
| P4 — Silent file skips | ~60-70% file coverage | 100% | `files_reviewed / total_code_files` from summary output |
| P7 — Category imbalance | 85% best_practices | <50% best_practices | Category distribution from findings.json |

---

## Coverage Gate Policy

**100% file coverage is enforced from day one.** If the agent doesn't review every code file in the PR, the CI gate fails. No soft mode, no gradual rollout.

A `coverage_gate_mode` config field exists (`"hard"` / `"log"`) but defaults to `"hard"`. The `"log"` mode is for debugging only — if a production issue with the coverage system needs investigation, ops can temporarily switch to `"log"` to stop blocking PRs while the bug is fixed. It is not a rollout mechanism.

---

## Phase 1 — Prompt Alignment + Determinism

**Goal:** Fix the conflicting tool-call guidance and add deterministic LLM parameters. Zero code logic changes — prompt and API parameter fixes only.

**Commit:** `fix(prompts): align tool-call limits and add temperature/seed`

### 1.1 — Align tool-call limit

**File:** `commands/review-pr-core.md`, Step 0 constraints block
**Change:** Replace "max 10 tool calls" with "max 40 tool calls" to match the deployment budget in `docker-compose.yml` (`MAX_TURNS=40`) and the Claude wrapper (`review-pr.claude.md`: "max 40 tool calls").

### 1.2 — Rewrite TURN EFFICIENCY block

**File:** `src/agents/openai_runner.py`, `SYSTEM_PROMPT` constant, the block starting with "TURN EFFICIENCY"
**Change:** Keep "Do NOT call `get_file_diff`, `get_change_analysis`, `get_blast_radius`, or `get_pr` — all data is pre-injected." Remove "Your goal: review all pre-injected diffs and produce findings with ZERO or minimal tool calls." Replace with:

> You have 40 tool calls available. Use `read_local_file` or `get_file_content` when you need full-file context to verify a finding. Use `get_callers` to check blast radius on high-risk changes. The pre-injected diffs save you from fetching diffs — but reading full files for verification is expected and encouraged. Spend your turns where they matter most: verify before flagging, prioritize high-risk files, and ensure every file in your batch is covered.

### 1.3 — Add verify-before-CRITICAL rule

**File:** `commands/review-pr-core.md`, after Step 5d (the existing review steps)
**Change:** Insert new Step 5e:

> **5e — Verify before flagging CRITICAL.** Before emitting any finding with severity `critical`, you MUST call `read_local_file` or `get_file_content` to verify the issue exists in the full file. Do not flag CRITICAL findings based on diff context alone. If verification shows the issue doesn't exist, downgrade to `suggestion` or drop the finding.

### 1.4 — Set temperature and seed on API calls

**File:** `src/agents/openai_runner.py`
**Change 1:** In the `_run_chat_completions` method, at the `client.chat.completions.create()` call — add `temperature=0.3` and `seed=42`.
**Change 2:** In the `_run_responses_api` method, at the `client.responses.create()` call — add `temperature=0.3` to the kwargs dict.

**Why 0.3:** Matches the old pipeline (pipeline 268) which produced more consistent results at this temperature. `temperature=0` risks degenerate repetition patterns with long prompts.

**Note on `seed`:** OpenAI's seed parameter improves but does not guarantee determinism. The primary consistency gain comes from temperature. Expect reduced variance, not identical outputs across runs.

### 1.5 — Surface failed diff fetches

**File:** `src/review_job.py`, `_pre_fetch_diffs` method
**Change:** When the `except` block catches a diff fetch failure, instead of only logging a warning, also append the file path to a `failed_diffs` list. After the loop, if `failed_diffs` is non-empty, inject into the prompt context:

> The following files could not be pre-fetched. You MUST fetch them via `get_file_diff` or `read_local_file` during review: [list]. These files still count toward 100% coverage.

### VERIFY (1.6)

- `python -m pytest tests/ -v` — all existing tests pass
- Grep `SYSTEM_PROMPT` for "ZERO or minimal" — must not appear
- Grep `review-pr-core.md` for "max 10" — must not appear
- Confirm `temperature=0.3` appears in both API call sites

---

## Phase 2 — 100% Coverage System

**Goal:** Guarantee every code file in a PR is reviewed. The risk classifier controls depth (how much time per file), not breadth (whether a file is reviewed).

**Commit:** `feat(coverage): add risk classifier, files_clean[], and coverage gate`

### 2.1 — Risk classifier (`src/risk_classifier.py` — new file)

Pure Python, zero LLM cost. Runs before the agent session using data already available from `FetchPRDetailsActivity` and `_pre_compute_analysis`.

**Input:** List of changed files with their stats (additions, deletions, change_type) + optional graph analysis (caller count, test gaps).

**Output:** Dict mapping each file path to `{ risk: "HIGH" | "MEDIUM" | "LOW", score: float, reasons: [str] }`.

**Formula:**

```
file_risk = (
    0.25 * normalize(lines_changed, max=500)
  + 0.25 * path_sensitivity
  + 0.15 * (1.0 if change_type == "add" else 0.0)
  + 0.15 * (1.0 if no_test_coverage else 0.0)
  + 0.10 * normalize(caller_count, max=10)
  + 0.10 * file_type_risk
)
```

**Calibration note:** These weights are heuristic — initial values based on the pattern that the verified false positives (getRiskLevelColor, LogBookSummary join, SignalR stale closure) all involved files with high `path_sensitivity` or high `caller_count` that the agent didn't verify. Expect to recalibrate after 20-30 production runs by correlating risk scores with actual false positive rates. All weights and thresholds should be configurable via `config.py` fields so tuning doesn't require code changes.

**Thresholds (configurable in `config.py`):**
- `risk_high_threshold: float = 0.6`
- `risk_medium_threshold: float = 0.3`
- Below 0.3 = LOW

**Path sensitivity patterns (configurable, loaded from `languages.yml` or a `risk-patterns` section):**
- HIGH (1.0): `auth/`, `crypto/`, `permissions/`, `security/`, `middleware/`, `Controllers/`, `api/`
- MEDIUM (0.5): `services/`, `models/`, `database/`, `migrations/`
- LOW (0.0): `tests/`, `docs/`, `utils/`, `helpers/`, `constants/`

**Graceful degradation:** When graph analysis is unavailable, `caller_count` and `no_test_coverage` default to 0.0. The classifier still works — it relies more on lines changed, path sensitivity, and file type. Every PR gets risk classification regardless of graph availability.

### 2.2 — Inject risk table into prompt

**File:** `src/review_job.py`, `_build_review_context` method
**Change:** After the existing analysis and diffs sections, call `risk_classifier.classify(...)` and inject the result as a markdown table:

```markdown
### File Risk Classification (100% coverage required)

| File | Risk | Depth | Reason |
|------|------|-------|--------|
| src/auth/LoginController.cs | HIGH | Full review + verify | auth path, 120 lines changed, 8 callers |
| src/models/User.cs | MEDIUM | Diff review + read if needed | 45 lines changed, model layer |
| src/utils/DateHelper.cs | LOW | Diff scan | 3 lines changed, utility |

You MUST review every file above. HIGH files get deep review with full-file reads. LOW files get a diff-level scan. Every file must appear in `findings[]` or `files_clean[]`.
```

### 2.3 — Add risk-based depth instructions to prompt

**File:** `commands/review-pr-core.md`, Step 4 (after the existing T1-T5 tier table)
**Change:** Insert:

> **100% coverage is required regardless of tier.** Every code file in your batch must be reviewed. You must either produce a finding for a file or list it in `files_clean[]` in findings.json. Files that appear in neither are considered skipped — skipped files fail the CI gate.
>
> **Within your tier strategy, depth per file is determined by risk tier** (shown in the pre-computed risk table):
> - **HIGH risk** — Full review. Read the full file via `read_local_file` before flagging. Check callers via `get_callers`. Verify CRITICAL findings against full context. Spend multiple turns if needed.
> - **MEDIUM risk** — Review from the pre-injected diff. Flag obvious issues. Use `read_local_file` only if something looks wrong but you need more context.
> - **LOW risk** — Scan the diff for security issues and critical bugs only. If nothing critical, add to `files_clean[]` and move on.
>
> Budget your 40 turns wisely: spend more on HIGH, less on LOW. But every file must appear in the output.
>
> T1-T5 sets the overall strategy (e.g., T5 = use repomix, prioritize by churn). Risk tiers set depth per file within that strategy. Both apply together.

### 2.4 — Add `files_clean[]` to findings schema

**File:** `commands/review-pr-core.md`, Step 7 (findings.json schema definition)
**Change:** Add `files_clean` array to the schema:

```json
{
  "findings": [ ... ],
  "files_clean": ["src/utils/DateHelper.cs", "src/constants/AppColors.cs"]
}
```

Instruction: "Include a `files_clean` array listing every file path you reviewed and found no issues in. Every code file in your batch MUST appear in either `findings[].file` or `files_clean[]`."

**File:** `src/models/review_models.py`
**Change:** Add `files_clean: List[str] = []` to the findings output model. Update the JSON parsing in `review_job.py` (the `_extract_findings` method) to read this field.

### 2.5 — Coverage gate and penalty

**File:** `src/post_findings.py`

**Change 1 — Coverage calculation.** In the summary construction section (near `_build_summary_markdown`), replace the current `files_reviewed` derivation with:

```python
files_with_findings = set(f.file for f in findings_file.findings)
files_clean = set(findings_file.files_clean or [])
files_reviewed = files_with_findings | files_clean
coverage = len(files_reviewed) / total_code_files if total_code_files > 0 else 1.0
```

**Change 2 — Gate rule.** In `_evaluate_gate`, add a coverage check controlled by `coverage_gate_mode`:
- `"hard"` (default): Fail gate if coverage < 100%
- `"log"`: Log coverage % but don't fail gate (debugging/emergency use only)

Gate failure message: "Gate failed: {n} file(s) not reviewed ({list}). 100% code review coverage is required."

**Change 3 — Summary display.** Change "Files Reviewed" in summary markdown to show coverage explicitly: `"Files Reviewed: 12 / 12 (100%)"` or `"Files Reviewed: 8 / 12 (67%) — 4 files not reviewed"`.

**File:** `src/pr_scorer.py`
**Change:** Add coverage penalty method. For coverage below 100%, add penalty: `(1 - coverage_ratio) * 50`. This ensures incomplete reviews score poorly even if the gate mode is `"log"`.

**File:** `src/config.py`
**Change:** Add fields:

```python
coverage_gate_mode: str = Field(default="hard", description="Coverage gate: hard (default) | log (debugging only)")
batch_max_turns: int = Field(default=40, description="Max turns per batch — must match MAX_TURNS")
risk_high_threshold: float = Field(default=0.6)
risk_medium_threshold: float = Field(default=0.3)
```

### 2.6 — Align batch turn budget to 40

**File:** `src/config.py`, `batch_max_turns` field
**Change:** Change the default from `15` to `40`. The existing code at `config.py` has `batch_max_turns: int = 15` — this artificially limits batched reviews to 15 turns per batch while the deployment allows 40. Every batch gets the full 40-turn budget.

**File:** `commands/review-pr-core.md`, any reference to batch-specific turn limits
**Change:** Ensure no prompt text contradicts the 40-turn budget. Every context where the agent runs — single review, batched review, Claude wrapper — must say 40.

### VERIFY (2.7)

- `python -m pytest tests/ -v` — all tests pass including new risk classifier tests
- `risk_classifier.py` produces HIGH/MEDIUM/LOW for a mock file list
- `files_clean` parses from findings JSON
- Coverage calculation: 5 findings files + 3 clean files / 8 total = 100%
- Coverage calculation: 5 findings files + 0 clean files / 8 total = 62.5%
- Gate mode `"hard"` (default) DOES fail gate on incomplete coverage
- Gate mode `"log"` does NOT fail gate (for debugging only)
- `config.py` `batch_max_turns` default is 40, not 15
- Grep entire codebase for turn/tool-call limits — every value must be 40

---

## Phase 3 — Scoring + Categories

**Goal:** Stop test findings from affecting the gate. Expand category taxonomy so findings aren't funneled into `best_practices`.

**Commit:** `fix(scoring): add testing category, expand valid categories, add review checklists`

### 3.1 — New `testing` category with zero-weight scoring

**File:** `src/post_findings.py`, `VALID_CATEGORIES` and `CATEGORY_REMAP`
**Change:** Add `"testing"` to `VALID_CATEGORIES`. Remove `"testing": "best_practices"` from `CATEGORY_REMAP`.

**File:** `commands/scoring.md`, penalty matrix
**Change:** Add a `testing` row with zero-weight penalties:

| Category | critical | warning | suggestion |
|----------|----------|---------|------------|
| testing  | 0.0      | 0.0     | 0.0        |

Test findings are always posted as inline comments but never affect the star rating or CI gate. Zero-weight penalties make the mode multiplier exemption unnecessary — any multiplier applied to 0.0 is still 0.0.

**File:** `commands/review-pr-core.md`, the test-gap instruction (currently: "Flag missing test coverage from the test gaps list as findings.")
**Change:** Append: "Use category `testing` (not `best_practices`) for all test-gap findings. These are informational — they appear as inline comments but do not affect the CI gate or star rating."

### 3.2 — Expand valid categories

**File:** `src/post_findings.py`, `VALID_CATEGORIES` and `CATEGORY_REMAP`
**Change:** Add `"architecture"`, `"correctness"`, and `"error_handling"` to `VALID_CATEGORIES`. Remove those keys from `CATEGORY_REMAP`.

Updated sets:

```python
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
```

**File:** `src/models/review_models.py`
**Change:** Update any category validation/enum to include the new categories.

**File:** `commands/scoring.md`, penalty matrix
**Change:** Add rows for new categories:

| Category | critical | warning | suggestion |
|----------|----------|---------|------------|
| architecture | 2.0 | 1.0 | 0.5 |
| correctness | 2.0 | 1.0 | 0.5 |
| error_handling | 1.5 | 0.75 | 0.25 |

### 3.3 — Architecture review checklist

**File:** `commands/review-mode-architecture.md` (new)
**Content:**

```markdown
# Architecture Review Checklist

## API Design
- [ ] Public API changes are backward-compatible or versioned
- [ ] Breaking changes documented in PR description
- [ ] Return types use interfaces, not concrete classes (e.g., IReadOnlyList over List)

## Coupling + Cohesion
- [ ] No circular dependencies between modules
- [ ] New dependencies follow the existing dependency direction
- [ ] Cross-layer calls go through defined interfaces

## Separation of Concerns
- [ ] Business logic not in controllers/handlers
- [ ] Data access not in UI layer
- [ ] Configuration not hardcoded

## Contracts
- [ ] DTOs/models don't expose internal implementation
- [ ] Serialization attributes present on public models
- [ ] Nullable annotations consistent
```

**File:** `commands/review-pr-core.md`, the mode references (currently "architecture — (inline in scoring.md)")
**Change:** Replace with reference to `commands/review-mode-architecture.md`.

### 3.4 — Performance review checklist

**File:** `commands/review-mode-performance.md` (new)
**Content:**

```markdown
# Performance Review Checklist

## Database
- [ ] No N+1 queries — use .Include() or projection
- [ ] Bulk operations use ExecuteUpdate/ExecuteDelete (not loop + SaveChanges)
- [ ] Read-only queries use .AsNoTracking()
- [ ] Pagination present on list endpoints

## I/O
- [ ] No synchronous I/O on hot paths (use async/await)
- [ ] HTTP calls use HttpClientFactory, not new HttpClient()
- [ ] File operations buffered or streamed for large payloads

## Collections + Algorithms
- [ ] No O(n^2) loops on unbounded collections
- [ ] LINQ queries materialized once (.ToList()) not re-enumerated
- [ ] Large read-only collections use FrozenSet/FrozenDictionary (.NET 8+)

## Caching
- [ ] Frequently-read, rarely-changed data cached appropriately
- [ ] Cache keys include all discriminating parameters
- [ ] Cache expiry set (no indefinite caching)
```

**File:** `commands/review-pr-core.md`, the mode references (currently "performance — (inline in scoring.md)")
**Change:** Replace with reference to `commands/review-mode-performance.md`.

### VERIFY (3.5)

- `python -m pytest tests/ -v` — all tests pass
- Create a finding with `category: "testing"` — verify 0 penalty points
- Create a finding with `category: "architecture"` — verify it is NOT remapped to `best_practices`
- Both new checklist files exist and are referenced from `review-pr-core.md`

---

## Phase 4 — Noise Reduction + Audit

**Goal:** Merge duplicate findings before posting. Add audit trail for review transparency.

**Commit:** `feat(quality): add comment merging and review audit export`

### 4.1 — Comment merging

**File:** `src/post_findings.py`, between the existing `filter_by_confidence` step and `cap_findings` step
**Change:** Add a `merge_similar_findings(findings)` function.

**Merge criteria (ALL must match):**
- Same `file` path
- Line numbers within +/- 5 of each other
- Title + message similarity > 0.7 using `difflib.SequenceMatcher.ratio()`

**Why SequenceMatcher:** It's stdlib (no new dependency), operates on character sequences (good for code-related text), and 0.7 threshold is conservative enough to avoid false merges. If semantic similarity becomes needed later, switch to embedding-based comparison — but start simple.

**Merge behavior:** Keep the finding with higher severity. Append the merged finding's message as a footnote: "(Also flagged at line {n}: {merged_title})". Reduce total finding count.

### 4.2 — Audit trail export

**File:** `src/post_findings.py`, after the summary posting step, before the output construction
**Change:** Write a timestamped markdown file:

```python
export_path = Path(workspace) / ".cr" / f"review_{pr_id}_{datetime.now():%Y%m%d_%H%M%S}.md"
```

**Contents:**
- PR metadata (ID, repo, branch, author)
- Score breakdown (total penalty, per-category penalties)
- Files reviewed vs skipped (with coverage %)
- All findings (posted and filtered/capped)
- Token usage (from agent runner stats)
- Risk classification table (from Phase 2)
- Gate decision and reason

### VERIFY (4.3)

- `python -m pytest tests/ -v` — all tests pass
- Given 3 findings on same file with lines 10, 12, 50 — the first two merge, the third stays separate
- Given 2 findings on different files with same title — they do NOT merge
- Audit file written to `.cr/` with correct timestamp format
- Audit file contains all expected sections

---

## Phase 5 — Language Intelligence

**Goal:** Auto-detect project stack and inject version-appropriate review rules. All 15 languages ship in this sprint.

**Commit:** `feat(lang): add stack detector and version-aware review rules`

### 5.1 — Language registry

**File:** `commands/languages.yml` (new)
**Content:** Full registry — 15 language entries:

```yaml
languages:
  csharp:
    extensions: [.cs]
    config_files: ["*.csproj", "NuGet.config", "packages.config"]
    rules_file: commands/lang-rules/csharp.md
  javascript:
    extensions: [.js, .jsx, .mjs]
    config_files: ["package.json"]
    rules_file: commands/lang-rules/javascript.md
  typescript:
    extensions: [.ts, .tsx]
    config_files: ["package.json", "tsconfig.json"]
    rules_file: commands/lang-rules/typescript.md
  react:
    extensions: []  # detected via package.json deps, overlays on javascript/typescript
    config_files: ["package.json"]
    rules_file: commands/lang-rules/react.md
  python:
    extensions: [.py]
    config_files: ["requirements.txt", "pyproject.toml", "Pipfile", "setup.py"]
    rules_file: commands/lang-rules/python.md
  java:
    extensions: [.java]
    config_files: ["pom.xml", "build.gradle", "build.gradle.kts"]
    rules_file: commands/lang-rules/java.md
  kotlin:
    extensions: [.kt, .kts]
    config_files: ["build.gradle.kts", "build.gradle"]
    rules_file: commands/lang-rules/kotlin.md
  go:
    extensions: [.go]
    config_files: ["go.mod"]
    rules_file: commands/lang-rules/go.md
  rust:
    extensions: [.rs]
    config_files: ["Cargo.toml"]
    rules_file: commands/lang-rules/rust.md
  cpp:
    extensions: [.cpp, .cc, .cxx, .c, .h, .hpp, .hxx]
    config_files: ["CMakeLists.txt", "*.vcxproj", "Makefile"]
    rules_file: commands/lang-rules/cpp.md
  swift:
    extensions: [.swift]
    config_files: ["Package.swift", "Podfile"]
    rules_file: commands/lang-rules/swift.md
  android:
    extensions: []  # detected via AndroidManifest.xml, overlays on java/kotlin
    config_files: ["AndroidManifest.xml"]
    rules_file: commands/lang-rules/android.md
  ruby:
    extensions: [.rb]
    config_files: ["Gemfile"]
    rules_file: commands/lang-rules/ruby.md
  php:
    extensions: [.php]
    config_files: ["composer.json"]
    rules_file: commands/lang-rules/php.md
  sql:
    extensions: [.sql]
    config_files: []
    rules_file: commands/lang-rules/sql.md
```

Adding a new language = one YAML entry + one rules file.

**Overlay languages (React, Android):** These have no file extensions of their own — they're detected from config files (`package.json` with `react` in dependencies, `AndroidManifest.xml` presence) and their rules are injected alongside the base language (JS/TS for React, Java/Kotlin for Android).

### 5.2 — Stack detector (`src/stack_detector.py` — new file)

Reads project config files from the workspace to extract exact framework versions.

**Config file parsing:**

| Config File | Extract |
|------------|---------|
| `*.csproj` | `<TargetFramework>` -> .NET version, `<PackageReference>` -> EF Core / ASP.NET version |
| `package.json` | `dependencies` -> React/Angular/Vue version, `devDependencies` -> TypeScript version, `engines.node` |
| `tsconfig.json` | TypeScript `target`, `strict` mode |
| `requirements.txt` / `pyproject.toml` / `Pipfile` | Python version, Django/Flask/FastAPI version |
| `pom.xml` | Java version (`<maven.compiler.source>`), Spring Boot version |
| `build.gradle` / `build.gradle.kts` | Java/Kotlin version, Android SDK level, Spring Boot version, Compose usage |
| `go.mod` | Go version, module dependencies |
| `Cargo.toml` | Rust edition (2018/2021/2024), dependency crates |
| `CMakeLists.txt` / `*.vcxproj` | C/C++ standard (C++11/14/17/20/23), compiler flags |
| `Package.swift` / `Podfile` / `*.xcodeproj` | Swift version, iOS deployment target, SwiftUI vs UIKit |
| `AndroidManifest.xml` | minSdkVersion, targetSdkVersion (combined with build.gradle) |
| `Gemfile` | Ruby version, Rails version |
| `composer.json` | PHP version, Laravel/Symfony version |

**Output:** `StackProfile` dataclass:

```python
@dataclass
class StackProfile:
    languages: list[str]
    frameworks: dict[str, dict[str, str]]
    detected_from: list[str]
```

**Monorepo handling:** The detector walks the workspace looking for config files at any depth. When multiple config files exist for the same language (e.g., `/frontend/package.json` with React 18 and `/legacy/package.json` with Angular 14), it produces per-directory profiles. The rule injection then matches each reviewed file to the nearest parent directory's profile. A file at `/frontend/src/App.tsx` gets React 18 rules; a file at `/legacy/src/app.component.ts` gets Angular 14 rules.

If this adds too much complexity for the initial implementation, the fallback is simpler: detect from the repo root config files only, ignore nested configs. Note this limitation in a code comment and address in a future sprint.

### 5.3 — Version-aware rules files

**Directory:** `commands/lang-rules/` (new)

Each file uses version-sectioned headings. The `_build_config_section` method reads the `StackProfile`, then loads the matching rules file and injects only the sections that apply to the detected version.

**All 15 rules files:**

| Rules File | Version Sections | Source |
|-----------|-----------------|--------|
| `commands/lang-rules/csharp.md` | All Versions, .NET 6+, .NET 8+, EF Core 6+, ASP.NET Core | Microsoft .NET coding conventions, Framework Design Guidelines, Roslyn analyzers |
| `commands/lang-rules/javascript.md` | All Versions, ES2020+, ES2022+ | ESLint recommended, Airbnb style guide (patterns only, not formatting) |
| `commands/lang-rules/typescript.md` | All Versions, TS 4.x, TS 5.x | typescript-eslint recommended rules |
| `commands/lang-rules/react.md` | React 16, React 17+, React 18+ (hooks, Suspense, Server Components) | React official docs (Rules of Hooks), eslint-plugin-react-hooks |
| `commands/lang-rules/python.md` | All Versions, Python 3.8+, Python 3.10+ (match/case), Python 3.12+ | PEP 8, PEP 484, ruff rules |
| `commands/lang-rules/java.md` | All Versions, Java 11+, Java 17+ (records, sealed, pattern matching), Java 21+ | Google Java Style Guide, Effective Java (Bloch), SonarQube rules |
| `commands/lang-rules/kotlin.md` | All Versions, Kotlin 1.5+, Kotlin 2.0+ | Kotlin official coding conventions, Android Kotlin style guide |
| `commands/lang-rules/go.md` | All Versions, Go 1.18+ (generics), Go 1.21+ | Effective Go, Go Code Review Comments (official wiki), staticcheck rules |
| `commands/lang-rules/rust.md` | All Versions, Edition 2021, Edition 2024 | Rust API Guidelines, Clippy lints |
| `commands/lang-rules/cpp.md` | All Versions, C++14, C++17, C++20 (concepts, ranges, coroutines), C++23 | C++ Core Guidelines (Stroustrup/Sutter), CERT C++ Coding Standard |
| `commands/lang-rules/swift.md` | All Versions, Swift 5.5+ (async/await, actors), SwiftUI vs UIKit | Swift API Design Guidelines, SwiftLint rules |
| `commands/lang-rules/android.md` | All, API 26+, Jetpack Compose, Kotlin-first | Android developer guides, Kotlin Android style guide |
| `commands/lang-rules/ruby.md` | All Versions, Ruby 3.0+, Rails 7+ | Ruby Style Guide (community), Rails Best Practices |
| `commands/lang-rules/php.md` | All Versions, PHP 8.0+ (enums, fibers, named args), Laravel 10+, Symfony 6+ | PSR-12, PHP-FIG standards, Laravel/Symfony official docs |
| `commands/lang-rules/sql.md` | All (version-agnostic) | OWASP SQL Injection Prevention, migration safety patterns |

Each rules file should be 30-50 checklist items max. These are review prompts, not exhaustive style guides. Focus on patterns that produce real bugs or security issues, not formatting preferences.

### 5.4 — Integration into review_job.py

**File:** `src/review_job.py`, `_build_config_section` method
**Change:** Update the flow:

1. If `.codereview.md` exists -> load it (project-specific overrides always win)
2. If no `.codereview.md` -> call `stack_detector.detect(workspace)` to get the `StackProfile`
3. For each detected language + version -> load the matching `commands/lang-rules/<lang>.md`, filter to only the applicable version sections
4. Inject into the prompt: "Auto-detected stack: .NET 8.0 + React 18.2 + TypeScript 5.3. The following language-specific review rules apply:" followed by the filtered rule sections
5. If `.codereview.md` exists AND auto-detection runs, the `.codereview.md` rules take precedence where they conflict

**File:** `src/file_filter.py`
**Change:** Replace the extension blacklist with a registry lookup. Any file whose extension matches a registered language in `languages.yml` gets reviewed; everything else is skipped. Log which files were skipped and why (unregistered extension).

### 5.5 — Update review-pr-core.md Step 1

**File:** `commands/review-pr-core.md`, Step 1 (project config loading)
**Change:** "If `.codereview.md` exists, load it. Otherwise, the system reads your project config files (package.json, *.csproj, etc.) to detect exact framework versions and injects version-appropriate review rules. The detected stack and active rules are shown in the 'Pre-loaded Project Config' section below."

### VERIFY (5.6)

- `python -m pytest tests/ -v` — all tests pass
- `stack_detector.detect()` on a workspace with a `.csproj` targeting `net8.0` returns `{"languages": ["csharp"], "frameworks": {"csharp": {"dotnet": "8.0"}}}`
- The prompt for a .NET 8 project includes `.NET 8+` rules but NOT `.NET 6+ only` rules that conflict
- The prompt for a .NET 6 project does NOT include `.NET 8+` rules
- A workspace with `.codereview.md` ignores auto-detection (existing behavior preserved)
- `file_filter.py` skips `.json` files and reviews `.cs` files when `csharp` is registered

---

## Phase 6 — Fix Verification + Parallelism

**Goal:** Move fix verification out of the agent prompt into deterministic Python. Parallelize batch execution.

**Commit:** `feat(verify): deterministic fix verifier and parallel batches`

### 6.1 — Fix verifier (`src/fix_verifier.py` — new file)

Move fix verification OUT of the agent prompt (`review-pr-core.md` Step 6) and into deterministic Python code.

**Why not line-number matching:** The old pipeline and the original spec both tried to match findings by file + line proximity (+/- N lines). This is fundamentally brittle — a fix that moves code to a different method, a refactor that restructures the file, or even adding a few lines above the finding all break the match. You end up with the same false-positive "assume fixed" problem the old pipeline had, just with a different heuristic. Line numbers are an artifact of a point-in-time snapshot, not a stable identifier for a code issue.

**Approach: File-level verification with full context.** Group old findings by file, read the entire current file, and ask the LLM to verify all findings in that file in a single call. This is how a human reviewer would do it — you don't check line 42 +/- 10, you read the file and see if the problem is still there.

**Algorithm:**

1. **Load and group.**
   - Load old findings (from previous review's findings.json or ADO thread data)
   - Group old findings by file path -> `Dict[file_path, List[Finding]]`
   - Run `git diff --name-status` between old review commit and current HEAD to get file renames/deletes

2. **Deterministic resolution (no LLM needed).**
   - If a file was **deleted** -> all findings in that file are `not_relevant`
   - If a file was **renamed** -> remap findings to the new path, continue to Step 3
   - If a file has **zero diff** since the last review (unchanged) -> all findings are `still_present` (nothing changed, nothing fixed)

3. **File-level LLM verification (one call per file with unresolved findings).**
   For each file that was modified and still has unresolved findings:
   - Read the **full current file** via workspace file read
   - Build a verification prompt:

   ```
   You are verifying whether previously flagged code review findings have been fixed.

   ## File: {file_path}

   ## Current file content:
   {full_file_content}

   ## Previous findings to verify:
   | # | Title | Original description | Original line |
   |---|-------|---------------------|---------------|
   | 1 | Missing null check on request param | The endpoint accepts ... | 42 |
   | 2 | SQL injection in search query | Raw string interpolation ... | 118 |

   For each finding, determine:
   - `fixed` — the specific issue described is no longer present in the current code
   - `still_present` — the issue still exists (may be at a different line now)

   Respond as JSON array: [{"finding": 1, "status": "fixed", "reason": "..."}, ...]
   ```

   **Why full file, not a line window:** The fix might have moved the code, extracted it to a helper, or restructured the method entirely. A +/- 20 line window would miss all of these. The LLM reads the whole file and understands whether the *issue* (not the *line*) is resolved. This is the same approach the review agent itself uses for HIGH-risk files — read the full file for accurate analysis.

4. **Blast radius check (when graph is available).**
   After file-level verification, for any finding originally flagged as `critical` or `architecture` category:
   - Use `get_blast_radius` (from Sprint 2 graph tools) to check if the fix introduced new issues in dependent files
   - If the finding was in a function whose callers changed since the last review, flag for attention: "Finding #{n} was fixed, but callers of `{function_name}` also changed — verify the fix didn't break dependents."
   - This is informational only (logged in the audit trail, posted as a note on the thread) — it doesn't change the fixed/still_present verdict

5. **Post results to ADO threads.**
   - `fixed` -> resolve the thread with "Verified fixed: {reason}"
   - `still_present` -> reply on the thread with "Still present: {reason}"
   - `not_relevant` -> resolve the thread with "File deleted/removed"
   - Blast radius notes -> post as a new reply on the thread

**LLM call budget:** One call per modified file with old findings (not per finding). A PR that had findings in 5 files = 5 verification calls. Each call handles all findings in that file at once. Blast radius checks use the graph tools (no additional LLM cost).

**Cap:** If more than 15 files need verification, batch the remaining files into groups of 5 per LLM call (send multiple file contents + their findings in one prompt). This caps at ~18 calls maximum (15 individual + ~1-2 batched). Batching trades some accuracy for cost control on unusually large re-reviews — but each batch still sees full file content, not line windows.

**Default for any LLM call that fails or returns ambiguous results: `still_present`** (never "assume fixed"). This is the critical difference from the old pipeline, which defaulted to "assume fixed" and had a known false-positive bug.

**File:** `commands/review-pr-core.md`, Step 6
**Change:** Remove the fix verification instructions from the agent prompt. Replace with: "Fix verification is handled automatically by the system after your review completes. Do not attempt to verify old findings — focus on reviewing the current code."

### 6.2 — Parallel batch execution

**File:** `src/batch_review_job.py`, the batch loop (currently sequential `for i, batch_files in enumerate(batches)`)
**Change:** Replace with `concurrent.futures.ThreadPoolExecutor`:

```python
max_workers = min(3, len(batches))
with ThreadPoolExecutor(max_workers=max_workers) as pool:
    futures = {pool.submit(self._run_batch, i, batch): i for i, batch in enumerate(batches, 1)}
    for future in as_completed(futures):
        batch_num = futures[future]
        result = future.result()
        # ... collect results
```

**Max parallelism: 3 concurrent batches.** Balances speed, cost, and API rate limits. If rate limit errors occur, the `_run_batch` method should retry with exponential backoff (1s, 2s, 4s, max 3 retries) before failing. Each batch is independent — a failure in one batch doesn't affect others.

**Why 3:** OpenAI's tier 3+ rate limits allow ~5000 RPM. Each batch makes up to 40 API calls over 2-5 minutes. 3 concurrent batches = ~120 calls/minute, well within limits. Increase to 5 if rate limit headroom is confirmed in production.

### VERIFY (6.3)

- `python -m pytest tests/ -v` — all tests pass
- Fix verifier: file deleted since last review -> all findings in that file return `not_relevant`
- Fix verifier: file unchanged since last review -> all findings return `still_present` (no LLM call)
- Fix verifier: file modified, 3 old findings -> one LLM call with full file + all 3 findings, returns per-finding verdicts
- Fix verifier: 5 files with findings, each modified -> 5 LLM calls (one per file, not per finding)
- Fix verifier: 20 files with findings -> 15 individual calls + 1 batched call (files 16-20 grouped)
- Fix verifier: LLM call fails or returns garbage -> default is `still_present`, NOT `fixed`
- Fix verifier: critical finding marked fixed + graph available -> blast radius check logged in audit
- Parallel batches: 3 batches complete in less wall-clock time than sequential (use mock delays in test)
- `review-pr-core.md` Step 6 no longer contains fix verification instructions

---

## New Files Summary

| File | Purpose | Phase |
|------|---------|-------|
| `src/risk_classifier.py` | Pure Python per-file risk scoring (HIGH/MEDIUM/LOW) | 2 |
| `src/stack_detector.py` | Reads config files to detect framework versions | 5 |
| `src/fix_verifier.py` | File-level fix verification — full-file LLM scan + blast radius check | 6 |
| `commands/languages.yml` | Language registry (15 entries) | 5 |
| `commands/lang-rules/csharp.md` | C# review rules, version-sectioned | 5 |
| `commands/lang-rules/javascript.md` | JavaScript review rules | 5 |
| `commands/lang-rules/typescript.md` | TypeScript review rules | 5 |
| `commands/lang-rules/react.md` | React review rules (hooks, lifecycle, Server Components) | 5 |
| `commands/lang-rules/python.md` | Python review rules | 5 |
| `commands/lang-rules/java.md` | Java review rules | 5 |
| `commands/lang-rules/kotlin.md` | Kotlin review rules | 5 |
| `commands/lang-rules/go.md` | Go review rules | 5 |
| `commands/lang-rules/rust.md` | Rust review rules | 5 |
| `commands/lang-rules/cpp.md` | C/C++ review rules | 5 |
| `commands/lang-rules/swift.md` | Swift review rules | 5 |
| `commands/lang-rules/android.md` | Android review rules (overlay on Java/Kotlin) | 5 |
| `commands/lang-rules/ruby.md` | Ruby review rules | 5 |
| `commands/lang-rules/php.md` | PHP review rules | 5 |
| `commands/lang-rules/sql.md` | SQL review rules (version-agnostic) | 5 |
| `commands/review-mode-architecture.md` | Architecture review checklist | 3 |
| `commands/review-mode-performance.md` | Performance review checklist | 3 |

## Modified Files Summary

| File | Changes | Phase |
|------|---------|-------|
| `commands/review-pr-core.md` | Tool limit 10->40, verify-before-CRITICAL, risk depth instructions, files_clean schema, testing category note, fix verification removal | 1, 2, 3, 6 |
| `src/agents/openai_runner.py` | TURN EFFICIENCY rewrite, temperature=0.3 + seed=42 on both API calls | 1 |
| `src/review_job.py` | Failed diffs surfacing, risk table injection, stack detection integration, files_clean parsing | 1, 2, 5 |
| `src/post_findings.py` | VALID_CATEGORIES expansion, CATEGORY_REMAP cleanup, coverage calculation, coverage gate, summary display, comment merging, audit export | 3, 4 |
| `src/pr_scorer.py` | Coverage penalty method | 2 |
| `src/config.py` | coverage_gate_mode, risk thresholds, risk weights | 2 |
| `src/models/review_models.py` | files_clean field, new categories validation | 2, 3 |
| `src/file_filter.py` | Extension blacklist -> registry lookup | 5 |
| `src/batch_review_job.py` | Sequential -> parallel batch execution | 6 |
| `commands/scoring.md` | testing (zero-weight), architecture, correctness, error_handling rows | 3 |

---

## Commit Strategy

1 commit per phase, per codehawk conventions.

| Phase | Commit Message |
|-------|---------------|
| 1 | `fix(prompts): align tool-call limits and add temperature/seed` |
| 2 | `feat(coverage): add risk classifier, files_clean[], and coverage gate` |
| 3 | `fix(scoring): add testing category, expand valid categories, add review checklists` |
| 4 | `feat(quality): add comment merging and review audit export` |
| 5 | `feat(lang): add stack detector and version-aware review rules` |
| 6 | `feat(verify): deterministic fix verifier and parallel batches` |

---

## What's NOT in This Sprint

- **Cross-project repo support** (Problem 5) — deferred pending root cause confirmation
- **Monorepo per-directory profiles** — simplified to root-config detection in initial implementation; noted for future sprint
- **Embedding-based similarity** for comment merging — start with SequenceMatcher, upgrade if false merge/miss rate is too high
