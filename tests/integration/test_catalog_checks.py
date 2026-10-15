"""Postern's catalog SQL, run against real Postgres catalogs with the testbed schema applied.

The fake Supabase in tests/support.py models what these statements return; this test proves
the statements themselves work on Supabase's Postgres and find exactly the planted issues in
docs/runbooks/testbed.sql. They run as `supabase_read_only_user`, the role Supabase's
read-only query endpoint uses, because what a catalog query can see depends on the role:
information_schema's grant views, for example, are empty for that role.

The local stack's `supabase_admin` (password `postgres`, local only) creates the testbed
schema and switches to the read-only role. Everything is rolled back.
"""

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row
from support import PLANTED, REPO_ROOT

from postern.integrations.supabase_mgmt.catalog import STATEMENTS, derive_lints

TESTBED_TABLES = {
    "notes",
    "profiles",
    "orders",
    "order_summary",
    "todos",
    "countries",
    "audit_trail",
}


def _as_admin(database_url: str) -> str:
    parts = urlsplit(database_url)
    netloc = f"supabase_admin:postgres@{parts.hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def test_catalog_checks_find_exactly_the_planted_issues(database_url: str) -> None:
    with psycopg.connect(_as_admin(database_url)) as conn:
        try:
            conn.execute((REPO_ROOT / "docs/runbooks/testbed.sql").read_text(encoding="utf-8"))
            conn.execute("set local role supabase_read_only_user")
            with conn.cursor(row_factory=dict_row) as cursor:
                rows: dict[str, list[dict[str, Any]]] = {
                    name: cursor.execute(sql).fetchall() for name, sql in STATEMENTS.items()
                }
        finally:
            conn.rollback()

    exposed = [row["schema"] for row in rows["exposed_schemas"]]
    assert exposed == ["public"]
    readable = {(r["name"], r["grantee"]) for r in rows["api_role_grants"]}
    assert ("order_summary", "anon") in readable, "grants are visible to the read-only role"
    assert ("staff_notes", "anon") not in readable, "no USAGE on the private schema"

    lints = derive_lints(exposed, rows["relations"], rows["policies"], rows["api_role_grants"])
    found = {
        (lint["name"], lint["metadata"]["name"])
        for lint in lints
        if lint["metadata"]["name"] in TESTBED_TABLES
    }
    assert found == PLANTED

    # Every row these statements return is schema metadata. None selects from a user table,
    # so the made-up row values can't appear.
    returned = repr(rows)
    assert "planted note" not in returned
    assert "planted person" not in returned
