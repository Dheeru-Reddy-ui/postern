"""Threat-model tests: TM-02, TM-03. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-02, TM-03 · prevention lands in Phase 3"
)
def test_untrusted_content_is_wrapped_and_labeled() -> None:
    """Untrusted text is a distinct type, wrapped and labeled at the integration boundary. It
    can't be concatenated into instructions."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-02 · prevention lands in Phase 3"
)
def test_untrusted_content_cannot_change_tool_grants() -> None:
    """Grants are compiled from the registry before a run starts and are immutable during it."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-03 · prevention lands in Phase 5"
)
def test_comments_from_non_collaborators_are_ignored() -> None:
    """Comments and issue text from accounts without write access to the repo are dropped before
    any agent sees them."""
    raise NotImplementedError
