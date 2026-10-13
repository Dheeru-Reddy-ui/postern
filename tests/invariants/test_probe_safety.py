"""Threat-model tests: TM-26. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-26 · prevention lands in Phase 3"
)
def test_probe_rate_limit_is_enforced() -> None:
    """A per-project rate limit and a concurrency of one."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-26 · prevention lands in Phase 3"
)
def test_probe_uses_only_read_http_methods() -> None:
    """Probes use only read methods (GET or HEAD). Write exposure is inferred from the schema,
    never tested by writing."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-26 · prevention lands in Phase 3"
)
def test_probe_count_is_bounded_and_times_out() -> None:
    """The count strategy is bounded (estimated rather than exact on large tables), with a hard
    timeout."""
    raise NotImplementedError
