# codehawk — Sprint 3: Review Quality + Coverage Enforcement

> Fix review quality by aligning tool-call limits, adding 100% coverage enforcement with risk-based depth, expanding finding categories, reducing noise via comment merging, adding language intelligence for 15 languages, and implementing deterministic fix verification with parallel batch execution.

---

## Tasks

### Phase 1: Prompt Alignment + Determinism

#### Task 1: Align tool-call limit
- **Change:** In `commands/review-pr-core.md`, Step 0 constraints block — replace "max 10 tool calls" with "max 40 tool calls". This aligns with the deployment budget in `docker-compose.yml` (`MAX_TURNS=40`) and the Claude wrapper (`review-pr.claude.md`: "max 40 tool calls").
- **Files:** `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** Grep for "max 10" in `review-pr-core.md` returns zero matches; "max 40" appears in Step 0.
- **Blockers:** none

#### Task 2: Rewrite TURN EFFICIENCY block
- **Change:** In `src/agents/openai_runner.py`, `SYSTEM_PROMPT` constant, the block starting with "TURN EFFICIENCY" — keep "Do NOT call `get_file_diff`, `get_change_analysis`, `get_blast_radius`, or `get_pr` — all data is pre-injected." Remove "Your goal: review all pre-injected diffs and produce findings with ZERO or minimal tool calls." Replace with:

  > You have 40 tool calls available. Use `read_local_file` or `get_file_content` when you need full-file context to verify a finding. Use `get_callers` to check blast radius on high-risk changes. The pre-injected diffs save you from fetching diffs — but reading full files for verification is expected and encouraged. Spend your turns where they matter most: verify before flagging, prioritize high-risk files, and ensure every file in your batch is covered.

- **Files:** `src/agents/openai_runner.py`
- **Tier:** standard
- **Done when:** Grep for "ZERO or minimal" in `SYSTEM_PROMPT` returns zero matches; the new text appears in its place.
- **Blockers:** none

#### Task 3: Add verify-before-CRITICAL rule
- **Change:** In `commands/review-pr-core.md`, after Step 5d (the existing review steps) — insert new Step 5e:

  > **5e — Verify before flagging CRITICAL.** Before emitting any finding with severity `critical`, you MUST call `read_local_file` or `get_file_content` to verify the issue exists in the full file. Do not flag CRITICAL findings based on diff context alone. If verification shows the issue doesn't exist, downgrade to `suggestion` or drop the finding.

- **Files:** `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** Step 5e exists in `review-pr-core.md` with the verify-before-CRITICAL text.
- **Blockers:** none

#### Task 4: Set temperature and seed on API calls
- **Change 1:** In `src/agents/openai_runner.py`, `_run_chat_completions` method, at the `client.chat.completions.create()` call — add `temperature=0.3` and `seed=42`.
- **Change 2:** In `src/agents/openai_runner.py`, `_run_responses_api` method, at the `client.responses.create()` call — add `temperature=0.3` to the kwargs dict.
- **Why 0.3:** Matches the old pipeline (pipeline 268) which produced more consistent results at this temperature. `temperature=0` risks degenerate repetition patterns with long prompts.
- **Note on `seed`:** OpenAI's seed parameter improves but does not guarantee determinism. The primary consistency gain comes from temperature. Expect reduced variance, not identical outputs across runs.
- **Files:** `src/agents/openai_runner.py`
- **Tier:** standard
- **Done when:** `temperature=0.3` appears in both API call sites; `seed=42` appears in `_run_chat_completions`.
- **Blockers:** none

#### Task 5: Surface failed diff fetches
- **Change:** In `src/review_job.py`, `_pre_fetch_diffs` method — when the `except` block catches a diff fetch failure, instead of only logging a warning, also append the file path to a `failed_diffs` list. After the loop, if `failed_diffs` is non-empty, inject into the prompt context:

  > The following files could not be pre-fetched. You MUST fetch them via `get_file_diff` or `read_local_file` during review: [list]. These files still count toward 100% coverage.

- **Files:** `src/review_job.py`
- **Tier:** standard
- **Done when:** Failed diff file paths are collected and injected into prompt context when non-empty.
- **Blockers:** none

#### VERIFY: Prompt Alignment + Determinism
- `python -m pytest tests/ -v` — MOCKED UNIT TESTS ONLY. Use unittest.mock / pytest-mock for ALL external dependencies (API calls, file I/O, etc). Do NOT run integration tests.
- Grep `SYSTEM_PROMPT` for "ZERO or minimal" — must not appear
- Grep `review-pr-core.md` for "max 10" — must not appear
- Confirm `temperature=0.3` appears in both API call sites
- Step 5e (verify-before-CRITICAL) exists in `review-pr-core.md`
- Failed diffs surfacing logic exists in `review_job.py`

---

### Phase 2: 100% Coverage System

#### Task 7: Risk classifier
- **Change:** Create `src/risk_classifier.py` — pure Python, zero LLM cost. Runs before the agent session using data already available from `FetchPRDetailsActivity` and `_pre_compute_analysis`.

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

  **Thresholds (configurable in `config.py`):**
  - `risk_high_threshold: float = 0.6` → HIGH
  - `risk_medium_threshold: float = 0.3` → MEDIUM
  - Below 0.3 → LOW

  **Path sensitivity patterns (configurable, loaded from `languages.yml` or a `risk-patterns` section):**
  - HIGH (1.0): `auth/`, `crypto/`, `permissions/`, `security/`, `middleware/`, `Controllers/`, `api/`
  - MEDIUM (0.5): `services/`, `models/`, `database/`, `migrations/`
  - LOW (0.0): `tests/`, `docs/`, `utils/`, `helpers/`, `constants/`

  **Graceful degradation:** When graph analysis is unavailable, `caller_count` and `no_test_coverage` default to 0.0. The classifier still works — it relies more on lines changed, path sensitivity, and file type. Every PR gets risk classification regardless of graph availability.

  **Calibration note:** These weights are heuristic — initial values based on the pattern that verified false positives all involved files with high `path_sensitivity` or high `caller_count` that the agent didn't verify. Expect to recalibrate after 20-30 production runs. All weights and thresholds should be configurable via `config.py` fields so tuning doesn't require code changes.

- **Files:** `src/risk_classifier.py` (new), `src/config.py` (risk thresholds + weights)
- **Tier:** standard
- **Done when:** `risk_classifier.classify(files)` returns HIGH/MEDIUM/LOW for a mock file list; thresholds configurable in `config.py`.
- **Blockers:** none

#### Task 8: Inject risk table into prompt
- **Change:** In `src/review_job.py`, `_build_review_context` method — after the existing analysis and diffs sections, call `risk_classifier.classify(...)` and inject the result as a markdown table:

  ```markdown
  ### File Risk Classification (100% coverage required)

  | File | Risk | Depth | Reason |
  |------|------|-------|--------|
  | src/auth/LoginController.cs | HIGH | Full review + verify | auth path, 120 lines changed, 8 callers |
  | src/models/User.cs | MEDIUM | Diff review + read if needed | 45 lines changed, model layer |
  | src/utils/DateHelper.cs | LOW | Diff scan | 3 lines changed, utility |

  You MUST review every file above. HIGH files get deep review with full-file reads. LOW files get a diff-level scan. Every file must appear in `findings[]` or `files_clean[]`.
  ```

- **Files:** `src/review_job.py`
- **Tier:** standard
- **Done when:** Risk classification table is injected into the review context prompt.
- **Blockers:** Task 7

#### Task 9: Add risk-based depth instructions to prompt
- **Change:** In `commands/review-pr-core.md`, Step 4 (after the existing T1-T5 tier table) — insert:

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

- **Files:** `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** Risk-based depth instructions and 100% coverage requirement appear in Step 4 of `review-pr-core.md`.
- **Blockers:** none

#### Task 10: Add `files_clean[]` to findings schema
- **Change 1:** In `commands/review-pr-core.md`, Step 7 (findings.json schema definition) — add `files_clean` array to the schema:

  ```json
  {
    "findings": [ ... ],
    "files_clean": ["src/utils/DateHelper.cs", "src/constants/AppColors.cs"]
  }
  ```

  Instruction: "Include a `files_clean` array listing every file path you reviewed and found no issues in. Every code file in your batch MUST appear in either `findings[].file` or `files_clean[]`."

- **Change 2:** In `src/models/review_models.py` — add `files_clean: List[str] = []` to the findings output model.

- **Change 3:** Update the JSON parsing in `src/review_job.py` (the `_extract_findings` method) to read the `files_clean` field.

- **Files:** `commands/review-pr-core.md`, `src/models/review_models.py`, `src/review_job.py`
- **Tier:** standard
- **Done when:** `files_clean` parses from findings JSON; the field exists in the model; the prompt schema includes it.
- **Blockers:** none

#### Task 11: Coverage gate and penalty
- **Change 1 — Coverage calculation.** In `src/post_findings.py`, summary construction section (near `_build_summary_markdown`) — replace the current `files_reviewed` derivation with:

  ```python
  files_with_findings = set(f.file for f in findings_file.findings)
  files_clean = set(findings_file.files_clean or [])
  files_reviewed = files_with_findings | files_clean
  coverage = len(files_reviewed) / total_code_files if total_code_files > 0 else 1.0
  ```

- **Change 2 — Gate rule.** In `src/post_findings.py`, `_evaluate_gate` — add a coverage check controlled by `coverage_gate_mode`:
  - `"hard"` (default): Fail gate if coverage < 100%
  - `"log"`: Log coverage % but don't fail gate (debugging/emergency use only)

  Gate failure message: "Gate failed: {n} file(s) not reviewed ({list}). 100% code review coverage is required."

- **Change 3 — Summary display.** Change "Files Reviewed" in summary markdown to show coverage explicitly: `"Files Reviewed: 12 / 12 (100%)"` or `"Files Reviewed: 8 / 12 (67%) — 4 files not reviewed"`.

- **Change 4 — Coverage penalty.** In `src/pr_scorer.py` — add coverage penalty method. For coverage below 100%, add penalty: `(1 - coverage_ratio) * 50`. This ensures incomplete reviews score poorly even if the gate mode is `"log"`.

- **Change 5 — Config fields.** In `src/config.py` — add fields:

  ```python
  coverage_gate_mode: str = Field(default="hard", description="Coverage gate: hard (default) | log (debugging only)")
  batch_max_turns: int = Field(default=40, description="Max turns per batch — must match MAX_TURNS")
  risk_high_threshold: float = Field(default=0.6)
  risk_medium_threshold: float = Field(default=0.3)
  ```

- **Files:** `src/post_findings.py`, `src/pr_scorer.py`, `src/config.py`
- **Tier:** standard
- **Done when:** Coverage calculation uses `files_clean`; gate mode `"hard"` fails on incomplete coverage; `"log"` does not fail; summary shows `X / Y (Z%)`; coverage penalty applied in scorer; config fields added with correct defaults.
- **Blockers:** Task 10

#### Task 12: Align batch turn budget to 40
- **Change 1:** In `src/config.py`, `batch_max_turns` field — change the default from `15` to `40`. The existing code has `batch_max_turns: int = 15` — this artificially limits batched reviews to 15 turns per batch while the deployment allows 40.

- **Change 2:** In `commands/review-pr-core.md` — ensure no prompt text contradicts the 40-turn budget. Every context where the agent runs — single review, batched review, Claude wrapper — must say 40.

- **Files:** `src/config.py`, `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** `config.py` `batch_max_turns` default is 40; grep entire codebase for turn/tool-call limits — every value must be 40.
- **Blockers:** Task 11 (config fields added there)

#### VERIFY: 100% Coverage System
- `python -m pytest tests/ -v` — MOCKED UNIT TESTS ONLY. Use unittest.mock / pytest-mock for ALL external dependencies (API calls, file I/O, etc). Do NOT run integration tests.
- `risk_classifier.py` produces HIGH/MEDIUM/LOW for a mock file list
- `files_clean` parses from findings JSON
- Coverage calculation: 5 findings files + 3 clean files / 8 total = 100%
- Coverage calculation: 5 findings files + 0 clean files / 8 total = 62.5%
- Gate mode `"hard"` (default) DOES fail gate on incomplete coverage
- Gate mode `"log"` does NOT fail gate (for debugging only)
- `config.py` `batch_max_turns` default is 40, not 15
- Grep entire codebase for turn/tool-call limits — every value must be 40

---

### Phase 3: Scoring + Categories

#### Task 14: New `testing` category with zero-weight scoring
- **Change 1:** In `src/post_findings.py`, `VALID_CATEGORIES` — add `"testing"`. In `CATEGORY_REMAP` — remove `"testing": "best_practices"`.

- **Change 2:** In `commands/scoring.md`, penalty matrix — add a `testing` row with zero-weight penalties:

  | Category | critical | warning | suggestion |
  |----------|----------|---------|------------|
  | testing  | 0.0      | 0.0     | 0.0        |

  Test findings are always posted as inline comments but never affect the star rating or CI gate.

- **Change 3:** In `commands/review-pr-core.md`, the test-gap instruction (currently: "Flag missing test coverage from the test gaps list as findings.") — append: "Use category `testing` (not `best_practices`) for all test-gap findings. These are informational — they appear as inline comments but do not affect the CI gate or star rating."

- **Files:** `src/post_findings.py`, `commands/scoring.md`, `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** `"testing"` is in `VALID_CATEGORIES`, not in `CATEGORY_REMAP`; scoring.md has zero-weight testing row; review-pr-core.md instructs to use `testing` category for test-gap findings.
- **Blockers:** none

#### Task 15: Expand valid categories
- **Change 1:** In `src/post_findings.py`, `VALID_CATEGORIES` — add `"architecture"`, `"correctness"`, and `"error_handling"`. Remove those keys from `CATEGORY_REMAP`.

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

- **Change 2:** In `src/models/review_models.py` — update any category validation/enum to include the new categories.

- **Change 3:** In `commands/scoring.md`, penalty matrix — add rows for new categories:

  | Category | critical | warning | suggestion |
  |----------|----------|---------|------------|
  | architecture | 2.0 | 1.0 | 0.5 |
  | correctness | 2.0 | 1.0 | 0.5 |
  | error_handling | 1.5 | 0.75 | 0.25 |

- **Files:** `src/post_findings.py`, `src/models/review_models.py`, `commands/scoring.md`
- **Tier:** standard
- **Done when:** All 9 categories in `VALID_CATEGORIES`; `CATEGORY_REMAP` only has 4 entries; new penalty rows in scoring.md; model updated.
- **Blockers:** Task 14

#### Task 16: Architecture review checklist
- **Change:** Create `commands/review-mode-architecture.md` with this content:

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

  In `commands/review-pr-core.md`, the mode references (currently "architecture — (inline in scoring.md)") — replace with reference to `commands/review-mode-architecture.md`.

- **Files:** `commands/review-mode-architecture.md` (new), `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** Architecture checklist file exists; `review-pr-core.md` references it.
- **Blockers:** none

#### Task 17: Performance review checklist
- **Change:** Create `commands/review-mode-performance.md` with this content:

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

  In `commands/review-pr-core.md`, the mode references (currently "performance — (inline in scoring.md)") — replace with reference to `commands/review-mode-performance.md`.

- **Files:** `commands/review-mode-performance.md` (new), `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** Performance checklist file exists; `review-pr-core.md` references it.
- **Blockers:** none

#### VERIFY: Scoring + Categories
- `python -m pytest tests/ -v` — MOCKED UNIT TESTS ONLY. Use unittest.mock / pytest-mock for ALL external dependencies (API calls, file I/O, etc). Do NOT run integration tests.
- Create a finding with `category: "testing"` — verify 0 penalty points
- Create a finding with `category: "architecture"` — verify it is NOT remapped to `best_practices`
- Both new checklist files exist and are referenced from `review-pr-core.md`

---

### Phase 4: Noise Reduction + Audit

#### Task 19: Comment merging
- **Change:** In `src/post_findings.py`, between the existing `filter_by_confidence` step and `cap_findings` step — add a `merge_similar_findings(findings)` function.

  **Merge criteria (ALL must match):**
  - Same `file` path
  - Line numbers within +/- 5 of each other
  - Title + message similarity > 0.7 using `difflib.SequenceMatcher.ratio()`

  **Why SequenceMatcher:** It's stdlib (no new dependency), operates on character sequences (good for code-related text), and 0.7 threshold is conservative enough to avoid false merges. If semantic similarity becomes needed later, switch to embedding-based comparison — but start simple.

  **Merge behavior:** Keep the finding with higher severity. Append the merged finding's message as a footnote: "(Also flagged at line {n}: {merged_title})". Reduce total finding count.

- **Files:** `src/post_findings.py`
- **Tier:** standard
- **Done when:** Given 3 findings on same file with lines 10, 12, 50 — the first two merge, the third stays separate. Given 2 findings on different files with same title — they do NOT merge.
- **Blockers:** none

#### Task 20: Audit trail export
- **Change:** In `src/post_findings.py`, after the summary posting step, before the output construction — write a timestamped markdown file:

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

- **Files:** `src/post_findings.py`
- **Tier:** standard
- **Done when:** Audit file written to `.cr/` with correct timestamp format; contains all expected sections (PR metadata, score breakdown, files reviewed, findings, token usage, risk table, gate decision).
- **Blockers:** none

#### VERIFY: Noise Reduction + Audit
- `python -m pytest tests/ -v` — MOCKED UNIT TESTS ONLY. Use unittest.mock / pytest-mock for ALL external dependencies (API calls, file I/O, etc). Do NOT run integration tests.
- Given 3 findings on same file with lines 10, 12, 50 — the first two merge, the third stays separate
- Given 2 findings on different files with same title — they do NOT merge
- Audit file written to `.cr/` with correct timestamp format
- Audit file contains all expected sections

---

### Phase 5: Language Intelligence

#### Task 22: Language registry
- **Change:** Create `commands/languages.yml` — full registry with 15 language entries:

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
      extensions: []
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
      extensions: []
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

  Overlay languages (React, Android) have no file extensions — detected from config files (`package.json` with `react` in dependencies, `AndroidManifest.xml` presence) and their rules are injected alongside the base language.

- **Files:** `commands/languages.yml` (new)
- **Tier:** standard
- **Done when:** `languages.yml` exists with all 15 entries; each entry has extensions, config_files, and rules_file.
- **Blockers:** none

#### Task 23: Stack detector
- **Change:** Create `src/stack_detector.py` — reads project config files from the workspace to extract exact framework versions.

  **Config file parsing:**

  | Config File | Extract |
  |------------|---------|
  | `*.csproj` | `<TargetFramework>` → .NET version, `<PackageReference>` → EF Core / ASP.NET version |
  | `package.json` | `dependencies` → React/Angular/Vue version, `devDependencies` → TypeScript version, `engines.node` |
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

  **Monorepo handling:** For initial implementation, detect from the repo root config files only, ignore nested configs. Note this limitation in a code comment and address in a future sprint.

- **Files:** `src/stack_detector.py` (new)
- **Tier:** standard
- **Done when:** `stack_detector.detect(workspace)` on a workspace with a `.csproj` targeting `net8.0` returns `{"languages": ["csharp"], "frameworks": {"csharp": {"dotnet": "8.0"}}}`.
- **Blockers:** Task 22

#### Task 24: Version-aware rules files (batch 1 — csharp, javascript, typescript, react, python, java, kotlin, go)
- **Change:** Create `commands/lang-rules/` directory and the first 8 rules files. Each file uses version-sectioned headings. 30-50 checklist items max per file. Focus on patterns that produce real bugs or security issues, not formatting preferences.

  | Rules File | Version Sections | Source |
  |-----------|-----------------|--------|
  | `csharp.md` | All Versions, .NET 6+, .NET 8+, EF Core 6+, ASP.NET Core | Microsoft .NET coding conventions, Framework Design Guidelines |
  | `javascript.md` | All Versions, ES2020+, ES2022+ | ESLint recommended, Airbnb style guide (patterns only) |
  | `typescript.md` | All Versions, TS 4.x, TS 5.x | typescript-eslint recommended rules |
  | `react.md` | React 16, React 17+, React 18+ (hooks, Suspense, Server Components) | React official docs, eslint-plugin-react-hooks |
  | `python.md` | All Versions, Python 3.8+, Python 3.10+, Python 3.12+ | PEP 8, PEP 484, ruff rules |
  | `java.md` | All Versions, Java 11+, Java 17+, Java 21+ | Google Java Style Guide, Effective Java |
  | `kotlin.md` | All Versions, Kotlin 1.5+, Kotlin 2.0+ | Kotlin official coding conventions |
  | `go.md` | All Versions, Go 1.18+, Go 1.21+ | Effective Go, Go Code Review Comments |

- **Files:** `commands/lang-rules/csharp.md`, `commands/lang-rules/javascript.md`, `commands/lang-rules/typescript.md`, `commands/lang-rules/react.md`, `commands/lang-rules/python.md`, `commands/lang-rules/java.md`, `commands/lang-rules/kotlin.md`, `commands/lang-rules/go.md` (all new)
- **Tier:** standard
- **Done when:** All 8 files exist with version-sectioned headings and 30-50 checklist items each.
- **Blockers:** none

#### Task 25: Version-aware rules files (batch 2 — rust, cpp, swift, android, ruby, php, sql)
- **Change:** Create the remaining 7 rules files:

  | Rules File | Version Sections | Source |
  |-----------|-----------------|--------|
  | `rust.md` | All Versions, Edition 2021, Edition 2024 | Rust API Guidelines, Clippy lints |
  | `cpp.md` | All Versions, C++14, C++17, C++20, C++23 | C++ Core Guidelines, CERT C++ |
  | `swift.md` | All Versions, Swift 5.5+ (async/await, actors), SwiftUI vs UIKit | Swift API Design Guidelines |
  | `android.md` | All, API 26+, Jetpack Compose, Kotlin-first | Android developer guides |
  | `ruby.md` | All Versions, Ruby 3.0+, Rails 7+ | Ruby Style Guide, Rails Best Practices |
  | `php.md` | All Versions, PHP 8.0+, Laravel 10+, Symfony 6+ | PSR-12, PHP-FIG standards |
  | `sql.md` | All (version-agnostic) | OWASP SQL Injection Prevention, migration safety |

- **Files:** `commands/lang-rules/rust.md`, `commands/lang-rules/cpp.md`, `commands/lang-rules/swift.md`, `commands/lang-rules/android.md`, `commands/lang-rules/ruby.md`, `commands/lang-rules/php.md`, `commands/lang-rules/sql.md` (all new)
- **Tier:** standard
- **Done when:** All 7 files exist with version-sectioned headings and 30-50 checklist items each.
- **Blockers:** none

#### Task 26: Integration into review_job.py
- **Change 1:** In `src/review_job.py`, `_build_config_section` method — update the flow:
  1. If `.codereview.md` exists → load it (project-specific overrides always win)
  2. If no `.codereview.md` → call `stack_detector.detect(workspace)` to get the `StackProfile`
  3. For each detected language + version → load the matching `commands/lang-rules/<lang>.md`, filter to only the applicable version sections
  4. Inject into the prompt: "Auto-detected stack: .NET 8.0 + React 18.2 + TypeScript 5.3. The following language-specific review rules apply:" followed by the filtered rule sections
  5. If `.codereview.md` exists AND auto-detection runs, the `.codereview.md` rules take precedence where they conflict

- **Change 2:** In `src/file_filter.py` — replace the extension blacklist with a registry lookup. Any file whose extension matches a registered language in `languages.yml` gets reviewed; everything else is skipped. Log which files were skipped and why (unregistered extension).

- **Change 3:** In `commands/review-pr-core.md`, Step 1 (project config loading) — update to: "If `.codereview.md` exists, load it. Otherwise, the system reads your project config files (package.json, *.csproj, etc.) to detect exact framework versions and injects version-appropriate review rules. The detected stack and active rules are shown in the 'Pre-loaded Project Config' section below."

- **Files:** `src/review_job.py`, `src/file_filter.py`, `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** Stack detection auto-injects version-filtered rules into prompt; `.codereview.md` overrides auto-detection; `file_filter.py` uses registry lookup; `review-pr-core.md` Step 1 updated.
- **Blockers:** Tasks 22, 23, 24, 25

#### VERIFY: Language Intelligence
- `python -m pytest tests/ -v` — MOCKED UNIT TESTS ONLY. Use unittest.mock / pytest-mock for ALL external dependencies (API calls, file I/O, etc). Do NOT run integration tests.
- `stack_detector.detect()` on a workspace with a `.csproj` targeting `net8.0` returns `{"languages": ["csharp"], "frameworks": {"csharp": {"dotnet": "8.0"}}}`
- The prompt for a .NET 8 project includes `.NET 8+` rules but NOT `.NET 6+ only` rules that conflict
- The prompt for a .NET 6 project does NOT include `.NET 8+` rules
- A workspace with `.codereview.md` ignores auto-detection (existing behavior preserved)
- `file_filter.py` skips `.json` files and reviews `.cs` files when `csharp` is registered
- All 15 lang-rules files exist in `commands/lang-rules/`

---

### Phase 6: Fix Verification + Parallelism

#### Task 28: Fix verifier
- **Change:** Create `src/fix_verifier.py` — move fix verification OUT of the agent prompt (`review-pr-core.md` Step 6) and into deterministic Python code.

  **Algorithm:**

  1. **Load and group.**
     - Load old findings (from previous review's findings.json or ADO thread data)
     - Group old findings by file path → `Dict[file_path, List[Finding]]`
     - Run `git diff --name-status` between old review commit and current HEAD to get file renames/deletes

  2. **Deterministic resolution (no LLM needed).**
     - If a file was **deleted** → all findings in that file are `not_relevant`
     - If a file was **renamed** → remap findings to the new path, continue to Step 3
     - If a file has **zero diff** since the last review (unchanged) → all findings are `still_present` (nothing changed, nothing fixed)

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

  4. **Blast radius check (when graph is available).**
     After file-level verification, for any finding originally flagged as `critical` or `architecture` category:
     - Use `get_blast_radius` (from Sprint 2 graph tools) to check if the fix introduced new issues in dependent files
     - If the finding was in a function whose callers changed since the last review, flag for attention: "Finding #{n} was fixed, but callers of `{function_name}` also changed — verify the fix didn't break dependents."
     - This is informational only (logged in the audit trail, posted as a note on the thread) — doesn't change the fixed/still_present verdict

  5. **Post results to ADO threads.**
     - `fixed` → resolve the thread with "Verified fixed: {reason}"
     - `still_present` → reply on the thread with "Still present: {reason}"
     - `not_relevant` → resolve the thread with "File deleted/removed"
     - Blast radius notes → post as a new reply on the thread

  **LLM call budget:** One call per modified file with old findings (not per finding). Cap: If more than 15 files need verification, batch the remaining files into groups of 5 per LLM call. Max ~18 calls (15 individual + ~1-2 batched).

  **Default for any LLM call that fails or returns ambiguous results: `still_present`** (never "assume fixed").

  **Prompt change:** In `commands/review-pr-core.md`, Step 6 — remove the fix verification instructions. Replace with: "Fix verification is handled automatically by the system after your review completes. Do not attempt to verify old findings — focus on reviewing the current code."

- **Files:** `src/fix_verifier.py` (new), `commands/review-pr-core.md`
- **Tier:** standard
- **Done when:** Fix verifier handles deleted (not_relevant), unchanged (still_present), and modified (LLM verification) files correctly; defaults to `still_present` on failure; respects 15-file cap with batching; `review-pr-core.md` Step 6 no longer contains fix verification instructions.
- **Blockers:** none

#### Task 29: Parallel batch execution
- **Change:** In `src/batch_review_job.py`, the batch loop (currently sequential `for i, batch_files in enumerate(batches)`) — replace with `concurrent.futures.ThreadPoolExecutor`:

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

  **Why 3:** OpenAI's tier 3+ rate limits allow ~5000 RPM. Each batch makes up to 40 API calls over 2-5 minutes. 3 concurrent batches = ~120 calls/minute, well within limits.

- **Files:** `src/batch_review_job.py`
- **Tier:** standard
- **Done when:** Batches execute in parallel with max 3 workers; retry with exponential backoff on rate limit errors; batch failure isolated from other batches; parallel batches complete faster than sequential (testable with mock delays).
- **Blockers:** none

#### VERIFY: Fix Verification + Parallelism
- `python -m pytest tests/ -v` — MOCKED UNIT TESTS ONLY. Use unittest.mock / pytest-mock for ALL external dependencies (API calls, file I/O, etc). Do NOT run integration tests.
- Fix verifier: file deleted since last review → all findings in that file return `not_relevant`
- Fix verifier: file unchanged since last review → all findings return `still_present` (no LLM call)
- Fix verifier: file modified, 3 old findings → one LLM call with full file + all 3 findings, returns per-finding verdicts
- Fix verifier: 5 files with findings, each modified → 5 LLM calls (one per file, not per finding)
- Fix verifier: 20 files with findings → 15 individual calls + 1 batched call (files 16-20 grouped)
- Fix verifier: LLM call fails or returns garbage → default is `still_present`, NOT `fixed`
- Fix verifier: critical finding marked fixed + graph available → blast radius check logged in audit
- Parallel batches: 3 batches complete in less wall-clock time than sequential (use mock delays in test)
- `review-pr-core.md` Step 6 no longer contains fix verification instructions

---

## Constraints

| Constraint | Value |
|-----------|-------|
| Branch | `feat/review-quality` |
| Base | `origin/main` |
| Commits | 1 per phase (not per task) |
| Tests | Mocked unit tests ONLY — NO integration tests |
| Protected branches | NEVER push to main, master, or development |

### Commit messages (1 per phase):
1. `fix(prompts): align tool-call limits and add temperature/seed`
2. `feat(coverage): add risk classifier, files_clean[], and coverage gate`
3. `fix(scoring): add testing category, expand valid categories, add review checklists`
4. `feat(quality): add comment merging and review audit export`
5. `feat(lang): add stack detector and version-aware review rules`
6. `feat(verify): deterministic fix verifier and parallel batches`

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| OpenAI seed parameter doesn't guarantee determinism | Findings may still vary across runs | Primary consistency gain comes from temperature=0.3; seed is supplementary. Measure with Jaccard similarity on 10 PR sample. |
| Risk classifier weights are heuristic | May over/under-classify file risk initially | All weights configurable in config.py. Recalibrate after 20-30 production runs by correlating risk scores with actual false positive rates. |
| SequenceMatcher may not catch semantic duplicates | Some duplicate comments may slip through | Start with 0.7 threshold (conservative). Upgrade to embedding-based similarity if false merge/miss rate is too high. |
| Rate limits with parallel batches (3 concurrent) | API rate limit errors under heavy load | Exponential backoff retry (1s, 2s, 4s, max 3 retries). 3 workers stays well within tier 3+ limits (~120 calls/min vs 5000 RPM). |
| Monorepo per-directory profiles deferred | Multi-framework monorepos get root-config rules only | Root-config detection covers most repos. Per-directory profiles noted for future sprint. |
| Fix verifier LLM calls add cost | Each re-review costs 1 LLM call per modified file | Capped at 18 calls max (15 individual + batched). Deterministic resolution handles deleted/unchanged files with zero LLM cost. |
