"""Threat-model tests: TM-14, TM-15, TM-16, TM-17. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-14, TM-15 · prevention lands in Phase 3"
)
def test_critical_severity_requires_confirmed_live_exposure() -> None:
    """Severity "critical" requires a probe-confirmed live exposure."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-16 · prevention lands in Phase 3"
)
def test_llm_cannot_suppress_or_downgrade_deterministic_findings() -> None:
    """Every advisor lint and probe result becomes a finding through code. The model can explain
    and group findings but can't drop or downgrade one."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-16, TM-17 · prevention lands in Phase 3"
)
def test_incomplete_check_never_reports_clean() -> None:
    """A check that didn't complete is reported as "not checked", never "clean" (fail closed)."""
    raise NotImplementedError
