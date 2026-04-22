# Review Mode: Docs / Chore

**Applies when:** All changed files have extensions `.md`, `.yml`, `.yaml`, `.json`, `.txt`, `.rst` — AND no `.py`, `.js`, `.ts`, `.cs`, `.java` files are changed. Or PR label `docs` or `chore`.

**Severity multipliers:** None. This is a light-touch review.

**Hard constraint:** Max 10 findings (not 30). Skip deep code analysis entirely.

---

## What This Mode Covers

Docs/chore PRs are low-risk but still need review. Focus only on the areas below. Do not analyze code logic, performance, or security unless a config file contains an obviously dangerous value.

---

## Checklist

### Documentation Accuracy
- [ ] Technical claims match actual behavior: version numbers, API endpoints, parameter names
- [ ] Code examples are syntactically valid and match the current API (not a stale version)
- [ ] Links are not broken (flag obviously wrong paths — e.g., reference to a deleted file or renamed module)
- [ ] Steps in tutorials/runbooks are in the correct order and complete
- [ ] Removed features: if a feature was removed, docs referencing it should be updated or removed

### Changelog Completeness
- [ ] CHANGELOG.md / HISTORY.md updated if this PR changes user-visible behavior (even docs changes can be notable)
- [ ] Version bumped if this is a release PR — confirm version in `pyproject.toml`, `package.json`, etc. matches CHANGELOG
- [ ] Breaking changes flagged clearly in changelog (not buried in a minor section)

### Config File Correctness (`.yml`, `.yaml`, `.json`)
- [ ] YAML syntax: indentation, quotes, anchors — flag obvious structural errors
- [ ] JSON syntax: valid JSON (no trailing commas, no comments in strict JSON)
- [ ] Required fields present: if schema is known (e.g., GitHub Actions, Azure Pipelines), check required keys exist
- [ ] Environment variable references: `${{ env.VAR }}`, `${VAR}` — confirm variable names match what's defined
- [ ] Obviously dangerous values: `verify: false`, `privileged: true`, `allow-insecure: true` in CI/CD config

### Formatting and Consistency
- [ ] Consistent heading hierarchy in Markdown (H1 → H2 → H3, no jumps)
- [ ] Table formatting: columns align, no broken pipe syntax
- [ ] Code fences: language tag present on code blocks for syntax highlighting (` ```python ` not just ` ``` `)
- [ ] Consistent terminology: same concept referred to by the same name throughout

---

## What to Skip

Do not flag in this mode:
- Code style in example snippets (unless the example is demonstrably wrong/broken)
- Missing tests (docs PRs don't need tests)
- Performance or security analysis of config values (unless critical and obvious)
- Opinions on writing style or word choice
