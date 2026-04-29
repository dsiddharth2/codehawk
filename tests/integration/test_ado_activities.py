"""
ADO activity integration tests — verify live PR fetch and comments.

Run:
    pytest tests/integration/test_ado_activities.py -v -m integration -s
"""

import pytest

from .conftest import (
    PR_ID, REPO,
    integration, needs_ado,
    setup_ado_env,
)


@integration
@needs_ado
class TestFetchPRDetails:
    def test_returns_valid_structure(self):
        from activities.fetch_pr_details_activity import FetchPRDetailsActivity
        from models.review_models import FetchPRDetailsInput

        settings = setup_ado_env()
        activity = FetchPRDetailsActivity(settings=settings)
        result = activity.execute(FetchPRDetailsInput(pr_id=PR_ID, repository_id=REPO))

        assert result.pr_id == PR_ID
        assert result.title
        assert result.source_branch
        assert result.target_branch
        assert result.author
        assert isinstance(result.file_changes, list)
        assert len(result.file_changes) > 0

    def test_source_commit_id_present(self):
        from activities.fetch_pr_details_activity import FetchPRDetailsActivity
        from models.review_models import FetchPRDetailsInput

        settings = setup_ado_env()
        activity = FetchPRDetailsActivity(settings=settings)
        result = activity.execute(FetchPRDetailsInput(pr_id=PR_ID, repository_id=REPO))

        assert result.source_commit_id
        assert len(result.source_commit_id) >= 7


@integration
@needs_ado
class TestFetchPRComments:
    def test_returns_list(self):
        from activities.fetch_pr_comments_activity import FetchPRCommentsActivity

        settings = setup_ado_env()
        activity = FetchPRCommentsActivity(settings=settings)
        threads = activity.execute(pr_id=PR_ID, repository_id=REPO)
        assert isinstance(threads, list)
