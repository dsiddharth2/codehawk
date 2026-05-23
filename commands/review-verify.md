# Pass 2 — Verify: Confirm Candidate Findings

You are verifying candidate code review findings identified in a previous scan pass. The scan pass reviewed diffs without access to full file context. Your job is to verify each candidate, refine messages, add concrete code suggestions, and produce the final findings.json.

## Tools Available

You have access to: `get_file_content`, `search_code`, `read_local_file`, `get_callers`, `get_dependents`.

## Instructions

1. For each candidate with `needs_verification: true`:
   - Use the `verification_hint` to guide your investigation.
   - Call `read_local_file` or `get_file_content` to read the full file.
   - Determine if the issue is real (confirm) or a false positive (drop).
   - If confirmed, refine the message with full-file context and add a concrete `suggestion` code block.
   - Adjust severity based on what you find in the full file.
   - Set confidence: 0.7-0.79 for likely issues, 0.8-0.89 for clear issues, 0.9+ for certain issues.

2. For candidates with `needs_verification: false`:
   - Include them directly in findings — they were confirmed from the diff alone.
   - Still add a concrete `suggestion` code block if possible.
   - Set confidence based on the category: code_style/docs 0.85+, verified issues 0.7+.

3. Produce the final `findings.json` output.

## Turn Budget

You have a limited turn budget. Reserve the last 2 turns for output.
- Prioritize HIGH-risk files first (shown in risk classification table if available).
- Group tool calls by file — read one file, verify all candidates in it, then move to the next.
- Do not re-read files you've already read.

## Output Format

Output findings.json in a ```json code fence. The schema matches the standard findings format:

```json
{
  "pr_id": "$PR_ID",
  "repo": "$REPO",
  "vcs": "$VCS",
  "review_modes": ["standard", "security", "architecture", "performance", "migration"],
  "summary": "4-8 sentence summary of the PR changes and assessment.",
  "findings": [
    {
      "id": "cr-001",
      "file": "src/auth/Login.cs",
      "line": 42,
      "severity": "critical",
      "category": "security",
      "title": "SQL injection in login query",
      "message": "Full description with context from file verification.",
      "confidence": 0.92,
      "suggestion": "Use parameterized query:\n```csharp\nvar cmd = new SqlCommand(\"SELECT * FROM Users WHERE Username = @user\", conn);\ncmd.Parameters.AddWithValue(\"@user\", username);\n```"
    }
  ],
  "files_clean": ["src/utils/DateHelper.cs"],
  "fix_verifications": [],
  "tool_calls": 0,
  "agent": "openai-api"
}
```

## Rules

- Every candidate must result in either a finding in `findings[]` or be dropped (with the file appearing in `files_clean[]` if no other findings remain for it).
- Dropped candidates are false positives — the full file context showed the issue doesn't exist.
- All findings MUST include a concrete `suggestion` with a copy-pasteable code fix.
- Confidence must be 0.0-1.0. Findings below 0.7 will be filtered out by post-processing.
- Max 30 findings total, max 5 per file.
- IDs must be sequential: cr-001, cr-002, etc.
