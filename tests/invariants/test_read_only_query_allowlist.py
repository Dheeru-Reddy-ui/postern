"""Threat-model tests: TM-01, TM-02. See docs/threat-model.md."""

import json
import re

import pytest
from support import FakeSupabase

from postern.core.tools import TOOL_CATALOG
from postern.integrations.supabase_mgmt.catalog import STATEMENTS
from postern.integrations.supabase_mgmt.client import ManagementClient, StatementNotAllowed

REF = "abcdefghijklmnopqrst"
WRITES = re.compile(
    r"\b(insert|update|delete|merge|alter|drop|create|grant|revoke|truncate|copy|call|do|"
    r"set\s+role|security\s+definer|pg_read_file|lo_import|dblink)\b",
    re.IGNORECASE,
)


def test_read_only_query_runs_only_allowlisted_statements() -> None:
    """The read-only query tool runs only reviewed, allowlisted statements (Postern's own
    catalog checks), keyed by name and hash. No agent grant includes free-form SQL."""
    # Every allowlisted statement reads the system catalog only: no user table, no writes.
    for name, sql in STATEMENTS.items():
        assert sql.lstrip().lower().startswith("select"), name
        assert not WRITES.search(sql), name
        tables = re.findall(r"\bfrom\s+([\w.]+)", sql, re.IGNORECASE)
        for table in tables:
            assert table.startswith(("pg_catalog.", "information_schema.", "unnest")), (name, table)

    fake = FakeSupabase(REF, [])
    client = ManagementClient("token", fake.client(), STATEMENTS)
    client.read_only_query(REF, "relations")
    (sent,) = fake.requests
    assert json.loads(sent.content) == {"query": STATEMENTS["relations"]}

    # Anything else is refused before a request is made.
    for name in ("select * from public.users", "drop_everything", ""):
        with pytest.raises(StatementNotAllowed):
            client.read_only_query(REF, name)
    # Even if the statement map were tampered with after construction, the hash check holds.
    client._statements["relations"] = "select email from public.users"
    with pytest.raises(StatementNotAllowed):
        client.read_only_query(REF, "relations")
    assert len(fake.requests) == 1

    # No tool in the catalog takes SQL: the only SQL tool runs allowlisted statements.
    sql_tools = {name for name in TOOL_CATALOG if "sql" in name or "query" in name}
    assert sql_tools == {"supabase.query.allowlisted"}
