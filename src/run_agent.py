"""
run_agent.py — CLI entry point for the codehawk review pipeline.

Thin wrapper that parses CLI args, builds a ReviewJob, and runs it.
"""

import argparse
import json
import sys
from pathlib import Path

from batch_review_job import BatchReviewJob


def main():
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
    args = parser.parse_args()

    job = BatchReviewJob(
        pr_id=args.pr_id,
        repo=args.repo,
        workspace=Path(args.workspace),
        model=args.model,
        prompt_path=Path(args.prompt_file),
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
