"""Threat-model tests: TM-17. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-17 · prevention lands in Phase 3"
)
def test_advisor_shape_change_is_detected_not_ignored() -> None:
    """The adapter validates responses strictly. Unknown or missing fields raise a schema error;
    they never yield an empty list."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-17 · prevention lands in Phase 3"
)
def test_advisor_failure_falls_back_to_splinter_sql() -> None:
    """On adapter failure, Postern falls back to running splinter's lint SQL through the read-only
    query endpoint."""
    raise NotImplementedError
