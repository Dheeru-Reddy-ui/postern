"""Threat-model tests: TM-27. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-27 · prevention lands in Phase 3"
)
def test_missed_nightly_run_raises_alert() -> None:
    """A dead-man's switch alerts the operator when a connection's nightly run is missing."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-27 · prevention lands in Phase 3"
)
def test_revoked_connection_is_reported_not_skipped() -> None:
    """A revoked or expired connection is reported to the user ("we can't see your project
    anymore"), never skipped quietly."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-27 · prevention lands in Phase 3"
)
def test_reports_show_last_successful_check_time() -> None:
    """Every report and digest shows the time of the last successful check."""
    raise NotImplementedError
