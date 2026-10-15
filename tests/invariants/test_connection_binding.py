"""Threat-model tests: TM-04. See docs/threat-model.md."""

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from support import FakeSupabase, Relation, SavepointStore, create_connect_link, jwt

from postern.agents.toolkit import build_toolkit
from postern.config import load_config
from postern.core.runtime import RunContext
from postern.core.store import ConnectionRecord
from postern.integrations.probe import ExposureProbe, ProbeRefused
from postern.integrations.supabase_mgmt.keys import (
    KeyRejected,
    key_belongs_to_project,
    store_public_key,
)

REF = "abcdefghijklmnopqrst"
OTHER = "zyxwvutsrqponmlkjihg"
KEY = "sb_publishable_belongs_to_project_123"


def _record(**changes: Any) -> ConnectionRecord:
    values = {
        "connection_id": "c1",
        "user_id": "u1",
        "project_ref": REF,
        "status": "active",
        "publishable_key": KEY,
        "verified_at": "2026-10-14T00:00:00+00:00",
    } | changes
    return ConnectionRecord(**values)


def test_probe_refuses_project_without_verified_connection(
    db: psycopg.Connection[Any], seed: Any, connect_client: Callable[..., Any]
) -> None:
    """Every project-scoped operation requires a connections row created from a verified OAuth
    grant that includes that project ref."""
    fake = FakeSupabase(REF, [Relation("public", "notes", rls_enabled=False, visible_rows=3)])
    probe = ExposureProbe(fake.client(), load_config().probe, sleep=lambda seconds: None)
    for connection in (
        None,
        _record(status="revoked"),
        _record(status="error"),
        _record(verified_at=None),
        _record(publishable_key=None),
    ):
        with pytest.raises(ProbeRefused):
            probe.probe(connection, "public", "notes")
    assert fake.requests == [], "nothing was sent for an unverified connection"

    # Management API tools refuse too.
    tools = build_toolkit(lambda connection_id: "token", fake.client(), probe)
    for connection in (None, _record(status="revoked")):
        ctx = RunContext("run", "security_sentinel", "u1", connection)
        with pytest.raises(PermissionError):
            tools["supabase.project.read"](ctx, {}, SavepointStore(db))

    # A connection is only ever created by the OAuth callback, and only for a project that
    # appears in the signed-in owner's own project list.
    user = seed.user()
    client = connect_client(fake)
    token = create_connect_link(db, user, OTHER)  # the owner's account can't see OTHER
    start = client.get("/connect/supabase", params={"token": token})
    state = httpx.URL(start.headers["location"]).params["state"]
    callback = client.get("/oauth/supabase/callback", params={"code": "c0de", "state": state})
    assert callback.status_code == 403
    saved = db.execute(
        "select count(*) from postern.connections where user_id = %s", (user,)
    ).fetchone()
    assert saved == (0,)


def test_pasted_key_must_belong_to_connected_project(
    db: psycopg.Connection[Any], seed: Any
) -> None:
    """A pasted key is checked against the connected project before it is stored. For a legacy JWT
    that means its ref claim; for a publishable key, a test request to that project."""
    fake = FakeSupabase(REF, [], accepted_keys={KEY})
    http = fake.client()

    # Legacy anon JWT: decided by its ref claim, with no request at all.
    assert key_belongs_to_project(jwt({"role": "anon", "ref": REF}), REF, http)
    assert not key_belongs_to_project(jwt({"role": "anon", "ref": OTHER}), REF, http)
    assert fake.requests == []

    # Publishable key: one request to the connected project's own API.
    assert key_belongs_to_project(KEY, REF, http)
    assert not key_belongs_to_project("sb_publishable_from_some_other_project", REF, http)
    assert {host for _, host, _ in fake.calls()} == {f"{REF}.supabase.co"}

    # End to end: a key for another project is refused, and nothing is stored.
    user = seed.user()
    connection_id = seed.active_connection(user)
    ref = seed.project_ref(connection_id)
    other_fake = FakeSupabase(ref, [], accepted_keys={KEY})
    before = db.execute(
        "select publishable_key from postern.connections where id = %s", (connection_id,)
    ).fetchone()
    with pytest.raises(KeyRejected, match="isn't valid for this project"):
        store_public_key(
            db, connection_id, jwt({"role": "anon", "ref": OTHER}), other_fake.client()
        )
    with pytest.raises(KeyRejected, match="isn't valid for this project"):
        store_public_key(db, connection_id, "sb_publishable_wrong_one", other_fake.client())
    after = db.execute(
        "select publishable_key from postern.connections where id = %s", (connection_id,)
    ).fetchone()
    assert after == before
    assert store_public_key(db, connection_id, KEY, other_fake.client()) == "publishable"


def test_agent_cannot_choose_target_project(
    db: psycopg.Connection[Any], seed: Any, tier0: str
) -> None:
    """The target project and repo come from the run context. No tool takes a project or repo
    argument that the model can set."""
    fake = FakeSupabase(REF, [Relation("public", "notes", rls_enabled=False, visible_rows=3)])
    probe = ExposureProbe(fake.client(), load_config().probe, sleep=lambda seconds: None)
    tools = build_toolkit(lambda connection_id: "token", fake.client(), probe)
    ctx = RunContext("run", "security_sentinel", "u1", _record())
    redirect = {
        "ref": OTHER,
        "project_ref": OTHER,
        "connection_id": "someone-elses",
        "url": f"https://{OTHER}.supabase.co",
    }
    store = SavepointStore(db)
    tools["supabase.project.read"](ctx, redirect, store)
    tools["supabase.advisors.security"](ctx, redirect, store)
    tools["probe.exposure"](ctx, redirect | {"schema": "public", "table": "notes"}, store)
    hosts_and_paths = fake.calls()
    assert hosts_and_paths, "the tools made their requests"
    for _, host, path in hosts_and_paths:
        assert OTHER not in host
        assert OTHER not in path
        assert REF in host or REF in path

    # In the database, a finding goes to the run's own connection; there is no parameter to
    # name another one, and an explanation can't be attached to another project's finding.
    seed.registry()
    mine = seed.active_connection(seed.user())
    theirs_user = seed.user()
    theirs = seed.active_connection(theirs_user)
    their_finding = seed.finding(theirs_user, theirs, "security_sentinel")
    run_id = seed.run("security_sentinel", connection=mine)
    claimed = store.claim_next_run(tier0, 60, 5)
    store.commit()
    assert claimed is not None
    assert claimed.run_id == run_id
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.set_finding_explanation(run_id, their_finding, "hijacked", "model")
