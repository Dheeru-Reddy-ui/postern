"""Threat-model tests: TM-01. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-01 · prevention lands in Phase 3"
)
def test_probe_result_has_only_allowed_fields() -> None:
    """The probe result type has exactly four fields: table, HTTP status, row count capped at
    1000, and timestamp. Nothing else can be persisted."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-01 · prevention lands in Phase 3"
)
def test_probe_never_reads_response_rows() -> None:
    """Probes ask PostgREST for a count only (a HEAD request with a count preference, to verify in
    Phase 3), so no row body is ever received."""
    raise NotImplementedError
