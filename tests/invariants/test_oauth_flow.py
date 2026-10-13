"""Threat-model tests: TM-20. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-20 · prevention lands in Phase 3"
)
def test_oauth_callback_rejects_missing_or_reused_state() -> None:
    """state is bound to the session, single-use and short-lived."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-20 · prevention lands in Phase 3"
)
def test_oauth_flow_uses_pkce() -> None:
    """The flow uses PKCE."""
    raise NotImplementedError
