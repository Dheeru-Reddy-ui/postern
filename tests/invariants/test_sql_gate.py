"""Threat-model tests: TM-09. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-09 · prevention lands in Phase 5"
)
def test_sql_gate_rejects_forbidden_statements() -> None:
    """Every drafted migration is parsed with Postgres's own parser (libpg_query; library choice
    is an ADR in Phase 5) and checked against an allowlist of statement types and a
    denylist of weakening patterns."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-09 · prevention lands in Phase 5"
)
def test_no_pr_opened_when_sql_gate_fails() -> None:
    """A gate failure means no branch and no PR, and the rejection is audit-logged."""
    raise NotImplementedError
