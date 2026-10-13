"""Threat-model tests: TM-25. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-25 · prevention lands in Phase 2"
)
def test_every_postern_table_has_rls_enabled() -> None:
    """RLS is enabled on every Postern table, checked against the catalog."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-25 · prevention lands in Phase 2"
)
def test_user_cannot_read_another_users_findings() -> None:
    """One user can't read another user's findings through the API."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-25 · prevention lands in Phase 8"
)
def test_report_pages_require_authentication() -> None:
    """Report pages require authentication. There are no unguessable-link-only reports."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-25 · prevention lands in Phase 6"
)
def test_mcp_tools_return_only_callers_projects() -> None:
    """postern-mcp tools return only the caller's projects."""
    raise NotImplementedError
