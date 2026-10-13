"""Threat-model tests: TM-05. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-05 · prevention lands in Phase 3"
)
def test_oauth_tokens_encrypted_at_rest() -> None:
    """User OAuth tokens are encrypted at rest with a key the database never holds (envelope
    encryption, key in Secret Manager)."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-05 · prevention lands in Phase 3"
)
def test_secrets_never_appear_in_logs_or_prompts() -> None:
    """Logs and prompts pass a redaction filter, and the gateway refuses prompts containing
    secret-shaped strings."""
    raise NotImplementedError
