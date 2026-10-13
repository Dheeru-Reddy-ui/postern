"""Threat-model tests: TM-06. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-06 · prevention lands in Phase 3"
)
def test_onboarding_rejects_service_role_and_secret_keys() -> None:
    """The onboarding key classifier rejects sb_secret_ keys and any JWT whose role isn't anon,
    before persistence. A rejected value is never stored or logged."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-06 · prevention lands in Phase 3"
)
def test_oauth_scopes_are_within_read_only_allowlist() -> None:
    """Requested scopes must be a subset of a fixed allowlist (decision D2). No secrets:* or
    *:write scope appears."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-06 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_read_api_keys() -> None:
    """A live test confirms a real Postern token gets a refusal from GET /v1/projects/{ref}/api-
    keys."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-06 · prevention lands in Phase 3"
)
def test_supabase_client_calls_only_allowlisted_endpoints() -> None:
    """The Supabase client implements only allowlisted GET endpoints. Endpoints inside a granted
    scope that Postern doesn't need (SQL snippets, backups, pooler config) aren't callable."""
    raise NotImplementedError
