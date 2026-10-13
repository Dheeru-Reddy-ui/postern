"""Threat-model tests: TM-01, TM-02. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-01, TM-02 · prevention lands in Phase 3"
)
def test_read_only_query_runs_only_allowlisted_statements() -> None:
    """The read-only query tool runs only reviewed, allowlisted statements (splinter lints and
    catalog introspection), keyed by hash. No agent grant includes free-form SQL."""
    raise NotImplementedError
