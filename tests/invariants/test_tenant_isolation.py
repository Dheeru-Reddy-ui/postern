"""Threat-model tests: TM-25. See docs/threat-model.md."""

import json
from typing import Any

import psycopg
import pytest

CONTROL_PLANE_TABLES = {
    "agents",
    "audit_log",
    "connections",
    "findings",
    "ledger",
    "notifications",
    "proposals",
    "runs",
    "steps",
    "users",
}


def test_every_postern_table_has_rls_enabled(db: Any) -> None:
    """RLS is enabled on every Postern table, checked against the catalog."""
    rows = db.execute(
        "select c.relname, c.relrowsecurity from pg_class c"
        " join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'postern' and c.relkind in ('r', 'p')"
    ).fetchall()
    tables = dict(rows)
    assert set(tables) >= CONTROL_PLANE_TABLES, "a control-plane table is missing"
    without_rls = sorted(name for name, rls in tables.items() if not rls)
    assert not without_rls, f"tables without row-level security: {without_rls}"


def test_user_cannot_read_another_users_findings(db: Any, seed: Any) -> None:
    """One user can't read another user's findings through the API."""
    agent = seed.agent("tenant_probe")
    alice, bob = seed.user(), seed.user()
    alice_finding = seed.finding(alice, seed.connection(alice), agent)
    seed.finding(bob, seed.connection(bob), agent)

    # Signed in as Alice, exactly as a Supabase API request is.
    db.execute("savepoint as_alice")
    db.execute("set local role authenticated")
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": alice, "role": "authenticated"}),),
    )
    visible = db.execute("select id::text, user_id::text from postern.findings").fetchall()
    assert visible == [(alice_finding, alice)]
    # Nor can she read connections (and their token ciphertext) at all.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("select count(*) from postern.connections")
    db.execute("rollback to savepoint as_alice")

    # Anonymous callers get nothing.
    db.execute("set local role anon")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("select count(*) from postern.findings")


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-25 · prevention lands in Phase 8"
)
def test_report_pages_require_authentication() -> None:
    """Report pages require authentication. There are no unguessable-link-only reports."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-25 · prevention lands in Phase 6"
)
def test_mcp_tools_return_only_callers_projects() -> None:
    """postern-mcp tools return only the caller's projects."""
    raise NotImplementedError
