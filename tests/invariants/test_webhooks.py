"""Threat-model tests: TM-19. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-19 · prevention lands in Phase 5"
)
def test_webhook_with_invalid_signature_is_rejected() -> None:
    """The signature is verified over the raw body before parsing, using a constant-time compare
    (GitHub's X-Hub-Signature-256; Resend's scheme to be verified in Phase 6)."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-19 · prevention lands in Phase 5"
)
def test_replayed_webhook_delivery_is_ignored() -> None:
    """Delivery IDs are stored with a unique constraint and a timestamp window, so replays are
    ignored."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-19 · prevention lands in Phase 5"
)
def test_webhook_events_are_rechecked_against_source_api() -> None:
    """A webhook is only a hint. State is re-read from the source API before anything acts on it."""
    raise NotImplementedError
