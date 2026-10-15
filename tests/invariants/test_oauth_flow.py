"""Threat-model tests: TM-20. See docs/threat-model.md."""

import base64
import hashlib
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
from support import FakeSupabase, create_connect_link

from postern.integrations.supabase_mgmt.oauth import AUTHORIZE_URL, sha256_hex

REF = "abcdefghijklmnopqrst"


def _start(client: Any, token: str) -> tuple[httpx.Response, str]:
    response = client.get("/connect/supabase", params={"token": token})
    assert response.status_code == 302, response.text
    return response, httpx.URL(response.headers["location"]).params["state"]


def _connections(db: psycopg.Connection[Any], user: str) -> int:
    row = db.execute("select count(*) from postern.connections where user_id = %s", (user,))
    count = row.fetchone()
    assert count is not None
    return int(count[0])


def test_oauth_callback_rejects_missing_or_reused_state(
    db: psycopg.Connection[Any], seed: Any, connect_client: Callable[..., Any]
) -> None:
    """state is bound to the browser, single-use and short-lived."""
    user = seed.user()
    fake = FakeSupabase(REF, [])
    client = connect_client(fake)
    token = create_connect_link(db, user, REF)
    start, state = _start(client, token)
    cookie = start.cookies["postern_oauth_state"]
    assert cookie == state
    set_cookie = start.headers["set-cookie"].lower()
    for attribute in ("httponly", "secure", "samesite=lax", "path=/oauth", "max-age=600"):
        assert attribute in set_cookie, attribute

    # The connect link itself is single use.
    assert client.get("/connect/supabase", params={"token": token}).status_code == 400

    callback = "/oauth/supabase/callback"
    # No state cookie (a different browser): refused before anything else happens.
    stranger = connect_client(fake)
    stranger.cookies.clear()
    assert stranger.get(callback, params={"code": "c", "state": state}).status_code == 400
    # A state that doesn't match this browser's cookie: refused.
    other = "x" * 43
    assert client.get(callback, params={"code": "c", "state": other}).status_code == 400
    assert fake.token_requests == [], "no code was exchanged for a refused callback"

    # The right state works once...
    assert client.get(callback, params={"code": "c0de", "state": state}).status_code == 200
    assert _connections(db, user) == 1
    # ...and never again, even with its cookie.
    client.cookies.set("postern_oauth_state", state, domain="testserver", path="/oauth")
    replay = client.get(callback, params={"code": "c0de", "state": state})
    assert replay.status_code == 400
    assert len(fake.token_requests) == 1

    # An expired state is refused too (stored only as a hash).
    second_user = seed.user()
    _, late = _start(client, create_connect_link(db, second_user, REF))
    stored = db.execute(
        "update postern.oauth_states set expires_at = now() - interval '1 second'"
        " where state_hash = %s returning state_hash",
        (sha256_hex(late),),
    ).fetchone()
    assert stored is not None, "the state is stored as its SHA-256, never in the clear"
    assert client.get(callback, params={"code": "c0de", "state": late}).status_code == 400
    assert _connections(db, second_user) == 0


def test_oauth_flow_uses_pkce(
    db: psycopg.Connection[Any], seed: Any, connect_client: Callable[..., Any]
) -> None:
    """The flow uses PKCE (S256), and the token exchange proves possession of the verifier."""
    user = seed.user()
    fake = FakeSupabase(REF, [])
    client = connect_client(fake)
    start, state = _start(client, create_connect_link(db, user, REF))
    authorize = httpx.URL(start.headers["location"])
    assert str(authorize).startswith(AUTHORIZE_URL)
    assert authorize.params["code_challenge_method"] == "S256"
    assert authorize.params["response_type"] == "code"
    challenge = authorize.params["code_challenge"]

    row = db.execute(
        "select code_verifier from postern.oauth_states where state_hash = %s",
        (sha256_hex(state),),
    ).fetchone()
    assert row is not None
    verifier = str(row[0])
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == challenge

    assert (
        client.get("/oauth/supabase/callback", params={"code": "c0de", "state": state}).status_code
        == 200
    )
    (exchange,) = fake.token_requests
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code_verifier"] == verifier
    assert exchange["redirect_uri"] == "https://postern-api.test/oauth/supabase/callback"
    assert exchange["authorization"].startswith("Basic "), "client authenticates with Basic auth"
