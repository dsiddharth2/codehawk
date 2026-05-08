"""
run_agent.py — CLI entry point for the codehawk review pipeline.

Thin wrapper that parses CLI args, builds a ReviewJob, and runs it.
"""

import argparse
import json
import sys
from pathlib import Path

from utils.logger import setup_logger
from batch_review_job import BatchReviewJob
from models.review_models import ReviewMode


def main():
    setup_logger("codehawk")
    parser = argparse.ArgumentParser(
        prog="run_agent.py",
        description="codehawk — run the full review pipeline (Phase 1 + Phase 2)",
    )
    parser.add_argument("--pr-id", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model", default="o3")
    parser.add_argument("--prompt-file", required=True, help="Path to review-pr-core.md")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--commit-id", default="")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--verify-fixes",
        action="store_true",
        default=False,
        help="Only verify whether prior findings have been fixed (no new findings).",
    )
    mode_group.add_argument(
        "--check-new-findings",
        action="store_true",
        default=False,
        help="Only look for new findings (skip prior-findings fetch).",
    )
    args = parser.parse_args()

    if args.verify_fixes:
        review_mode = ReviewMode.VERIFY_FIXES
    elif args.check_new_findings:
        review_mode = ReviewMode.CHECK_NEW
    else:
        review_mode = ReviewMode.FULL

    job = BatchReviewJob(
        pr_id=args.pr_id,
        repo=args.repo,
        workspace=Path(args.workspace),
        model=args.model,
        prompt_path=Path(args.prompt_file),
        review_mode=review_mode,
    )

    try:
        output = job.run(dry_run=args.dry_run, commit_id=args.commit_id)
        print(json.dumps(output, indent=2))
        if not output["gate"]["passed"]:
            sys.exit(1)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
