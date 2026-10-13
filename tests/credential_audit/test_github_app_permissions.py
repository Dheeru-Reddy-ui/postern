"""Threat-model tests: TM-08, TM-10. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-08 · prevention lands in Phase 5"
)
def test_github_app_cannot_merge_pull_request() -> None:
    """Live: the App tries to merge a PR on a test repo that has a ruleset, and fails."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-08 · prevention lands in Phase 5"
)
def test_github_app_cannot_push_to_default_branch() -> None:
    """Live: the App tries to push to main on that test repo, and fails."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-10 · prevention lands in Phase 5"
)
def test_github_app_has_no_workflows_permission() -> None:
    """The GitHub App manifest doesn't request the workflows permission, so it can't modify
    .github/workflows/."""
    raise NotImplementedError
