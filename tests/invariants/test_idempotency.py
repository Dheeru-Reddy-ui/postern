"""Threat-model tests: TM-18. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-18 · prevention lands in Phase 2"
)
def test_duplicate_idempotency_key_cannot_create_second_run() -> None:
    """runs.idempotency_key is unique."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-18 · prevention lands in Phase 5"
)
def test_one_open_proposal_per_finding_fingerprint() -> None:
    """There is at most one open proposal per finding fingerprint (unique partial index)."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-18 · prevention lands in Phase 6"
)
def test_duplicate_alert_is_suppressed() -> None:
    """Notifications carry a unique dedupe key."""
    raise NotImplementedError
