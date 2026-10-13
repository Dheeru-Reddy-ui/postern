"""Threat-model tests: TM-13. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-13 · prevention lands in Phase 6"
)
def test_only_notification_service_can_send_messages() -> None:
    """Only the notification service can send. Email and Slack clients are importable only in
    postern/integrations/notify, which ruff enforces too."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-13 · prevention lands in Phase 6"
)
def test_realtime_alert_budget_enforced_per_person() -> None:
    """The per-person budget is enforced by the database, not by caller code: at most one real-
    time alert per 7 days, and only for a verified live exposure."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-13 · prevention lands in Phase 6"
)
def test_no_messages_sent_during_quiet_hours() -> None:
    """Quiet hours are enforced in the user's timezone."""
    raise NotImplementedError
