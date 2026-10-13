"""Threat-model tests: TM-07. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_execute_write_sql() -> None:
    """A Postern token can't run write SQL. The database:write endpoint refuses it."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_read_only_query_endpoint_rejects_writes() -> None:
    """The read-only query endpoint rejects INSERT, UPDATE, DELETE, ALTER (including DISABLE ROW
    LEVEL SECURITY) and DROP."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_create_or_delete_api_keys() -> None:
    """A Postern token can't create or delete API keys."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_change_project_config() -> None:
    """A Postern token can't change auth, PostgREST, network or project settings, or pause the
    project."""
    raise NotImplementedError
