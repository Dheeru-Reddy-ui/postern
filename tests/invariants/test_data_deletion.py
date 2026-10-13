"""Threat-model tests: TM-30. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-30 · prevention lands in Phase 8"
)
def test_disconnect_deletes_all_user_data() -> None:
    """Disconnect triggers a deletion job that removes the user's rows from Postgres and BigQuery,
    verified by query."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-30 · prevention lands in Phase 8"
)
def test_disconnect_revokes_stored_tokens() -> None:
    """Disconnect revokes and destroys stored tokens immediately, before the deletion job runs."""
    raise NotImplementedError
