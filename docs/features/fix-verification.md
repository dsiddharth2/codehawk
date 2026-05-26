# Feature: Fix Verification and Re-push Flow

## Overview

When a developer pushes a fix after an initial review, Codehawk detects which prior findings were addressed and closes the corresponding PR threads. The summary shows a before/after score comparison.

## How It Works

### Phase 1 — Agent (re-push)

On re-push, the agent performs a **delta-only review**: it reviews only the git diff between the old and new head commits, not the full PR diff. This keeps the review focused and reduces tool call usage.

The agent also runs **fix verification** (Step 6 of `review-pr-core.md`):
1. Fetch the existing cr-ids from current PR threads.
2. For each prior finding, classify it as:
   - `fixed` — change addressed the issue
   - `still_present` — issue remains in the new diff
   - `not_relevant` — file changed so much the finding no longer applies
3. Write `fix_verifications[]` into `findings.json`.

### Phase 2 — Poster

When `fix_verifications[]` is present in `findings.json`, `post_findings.py`:
1. Resolves/closes threads for "fixed" items (ADO: `PostFixReplyActivity`; GitHub: reply with "Fixed" via `gh api`).
2. Generates a before/after score comparison using `ScoreComparisonService`.
3. Includes the score comparison in the updated summary comment.

## findings.json Structure (re-push)

```json
{
  "pr_id": 42,
  "fix_verifications": [
    {"cr_id": "abc12345", "status": "fixed", "reason": "Null check added on line 17"},
    {"cr_id": "def67890", "status": "still_present", "reason": "Input still not sanitized"}
  ],
  "findings": [...]
}
```

## GitHub Resolution

GitHub has no native thread resolution API (unlike ADO). Resolution is handled by:
1. Replying to the comment with "Fixed" via `gh api repos/{repo}/pulls/comments/{id}/replies`.
2. Optionally minimizing the comment via GraphQL (not yet implemented; planned for a future sprint).

## Developer Dismissals

Developers can reply to a CodeHawk comment thread with a reason explaining why the finding is invalid. On the next push, CodeHawk evaluates each dismissal:

1. The fix verifier reads developer replies from existing PR threads.
2. Each dismissal is evaluated per-file using a deterministic LLM call (not the agent loop).
3. If the reasoning is valid, the finding is classified as `dismissed` in `fix_verifications[]`.
4. `post_findings.py` posts an acceptance reply and resolves the thread with `WONT_FIX` status (not `FIXED`).
5. If the reasoning doesn't hold, the finding remains `still_present` and an explanation is posted.

### Thread Status

`PostFixReplyActivity` accepts a `status` parameter in `input_data`:
- **Fixed findings** — thread resolved with `CommentThreadStatus.FIXED` (default)
- **Dismissed findings** — thread resolved with `CommentThreadStatus.WONT_FIX`

The dismissal reply includes the accepted explanation and optionally suggests a `.codereview.md` rule to prevent the same pattern from being flagged in future reviews.

## cr-id Matching

Fix verification matches on `cr_id` (8-char SHA1 hex). For matching to work across runs, the cr-id must be stable — it is computed from `file:line:category` and does not change unless the file is renamed or the finding's line number shifts substantially.
