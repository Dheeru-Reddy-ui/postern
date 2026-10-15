"""Threat-model tests: TM-07. See docs/threat-model.md."""

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from support import FakeSupabase, TransactionDatabase, create_connect_link

from postern.integrations.supabase_mgmt.broker import ConnectionUnavailable, TokenService
from postern.integrations.supabase_mgmt.tokens import TokenCipher


def test_connection_refused_when_temporary_access_enabled(
    db: psycopg.Connection[Any], seed: Any, connect_client: Callable[..., Any]
) -> None:
    """`database:read` can't write, but a project with temporary database access turned on
    lets access tokens log into Postgres, where a role can write (ADR-005). Postern refuses to
    connect such a project, and stops using a connection the moment the setting appears."""
    ref = "abcdefghijklmnopqrst"
    enabled = FakeSupabase(ref, [], temporary_access="enabled")

    # At connect time: nothing is saved.
    user = seed.user()
    client = connect_client(enabled)
    start = client.get("/connect/supabase", params={"token": create_connect_link(db, user, ref)})
    state = httpx.URL(start.headers["location"]).params["state"]
    callback = client.get("/oauth/supabase/callback", params={"code": "c0de", "state": state})
    assert callback.status_code == 409
    assert "Temporary database access" in callback.text
    saved = db.execute(
        "select count(*) from postern.connections where user_id = %s", (user,)
    ).fetchone()
    assert saved == (0,)

    # Later: the broker checks on every token it issues, marks the connection and destroys
    # its tokens.
    cipher = TokenCipher(os.urandom(32))
    connection = seed.active_connection(seed.user())
    db.execute(
        "update postern.connections set supabase_project_ref = %s,"
        " access_token_ciphertext = %s, token_expires_at = %s where id = %s",
        (ref, cipher.encrypt("access"), datetime.now(UTC) + timedelta(hours=1), connection),
    )
    database: Any = TransactionDatabase(db)
    service = TokenService(database, cipher, enabled.client(), "id", "secret")
    with pytest.raises(ConnectionUnavailable, match="temporary_access_enabled"):
        service.access_token_for(connection)
    row = db.execute(
        "select status, status_reason, access_token_ciphertext, refresh_token_ciphertext"
        " from postern.connections where id = %s",
        (connection,),
    ).fetchone()
    assert row == ("error", "temporary_access_enabled", None, None)


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_oauth_token_cannot_log_into_postgres() -> None:
    """A live test confirms a real Postern token can't open a Postgres session on a project
    without temporary access enabled."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_execute_write_sql() -> None:
    """A Postern token can't run write SQL. The database:write endpoint refuses it."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_read_only_query_endpoint_rejects_writes() -> None:
    """The read-only query endpoint rejects INSERT, UPDATE, DELETE, ALTER (including DISABLE ROW
    LEVEL SECURITY) and DROP."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_create_or_delete_api_keys() -> None:
    """A Postern token can't create or delete API keys."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_change_project_config() -> None:
    """A Postern token can't change auth, PostgREST, network or project settings, or pause the
    project."""
    raise NotImplementedError
