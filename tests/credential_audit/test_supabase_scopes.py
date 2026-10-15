"""Threat-model tests: TM-06. See docs/threat-model.md."""

import contextlib
import inspect
import json
import re
from typing import Any

import httpx
import psycopg
import pytest
from support import REPO_ROOT, FakeSupabase, jwt

from postern.integrations.supabase_mgmt.catalog import STATEMENTS
from postern.integrations.supabase_mgmt.client import (
    ALLOWED_ENDPOINTS,
    ManagementClient,
    ManagementError,
)
from postern.integrations.supabase_mgmt.keys import KeyRejected, classify_key, store_public_key
from postern.integrations.supabase_mgmt.oauth import SCOPES, authorize_url, new_pkce

REF = "abcdefghijklmnopqrst"
CONTRACT = json.loads(
    (REPO_ROOT / "tests/fixtures/supabase/management-api-contract.json").read_text(encoding="utf-8")
)
READ_ONLY_SCOPES = {"projects:read", "database:read", "storage:read"}


def test_onboarding_rejects_service_role_and_secret_keys(
    db: psycopg.Connection[Any], seed: Any
) -> None:
    """The onboarding key classifier rejects sb_secret_ keys and any JWT whose role isn't anon,
    before persistence. A rejected value is never stored or logged."""
    secret = "sb_secret_" + "a1B2c3D4e5F6g7H8i9J0"
    service_role = jwt({"role": "service_role", "ref": REF})
    for bad in (secret, service_role, jwt({"role": "authenticated"}), "not-a-key", ""):
        with pytest.raises(KeyRejected) as rejected:
            classify_key(bad)
        if bad:
            assert bad not in str(rejected.value), "the message never repeats the key"
    assert classify_key("sb_publishable_" + "x" * 20) == "publishable"
    assert classify_key(jwt({"role": "anon", "ref": REF})) == "legacy_anon"

    # Rejected before any request or write; the stored key is unchanged.
    connection = seed.active_connection(seed.user())
    fake = FakeSupabase(seed.project_ref(connection), [])
    before = db.execute(
        "select publishable_key from postern.connections where id = %s", (connection,)
    ).fetchone()
    for bad in (secret, service_role):
        with pytest.raises(KeyRejected):
            store_public_key(db, connection, bad, fake.client())
    assert fake.requests == []
    after = db.execute(
        "select publishable_key, (select count(*) from postern.audit_log a"
        "   where a.payload::text like '%%' || %s || '%%')"
        " from postern.connections where id = %s",
        (secret[:20], connection),
    ).fetchone()
    assert before is not None
    assert after is not None
    assert after[0] == before[0]
    assert after[1] == 0, "the rejected key is nowhere in the audit log"

    # The database enforces the same rule if anything bypasses the classifier.
    for bad in (secret, service_role):
        with pytest.raises(psycopg.errors.CheckViolation, match="publishable_key_is_public"):
            db.execute("select postern.set_publishable_key(%s, %s)", (connection, bad))
        db.rollback()
        connection = seed.active_connection(seed.user())


def test_oauth_scopes_are_within_read_only_allowlist() -> None:
    """Requested scopes must be a subset of the fixed allowlist in ADR-005. No secrets:*,
    auth:read, rest:read or *:write scope appears."""
    assert set(SCOPES) <= READ_ONLY_SCOPES
    for scope in SCOPES:
        assert not scope.endswith(":write")
        assert not scope.startswith(("secrets:", "auth:", "rest:"))

    # Postern asks for exactly these scopes on every authorization.
    url = httpx.URL(authorize_url("client", "https://postern.test/cb", "state", new_pkce()))
    assert set(url.params["scope"].split(" ")) == set(SCOPES)
    assert "scope" in CONTRACT["oauth_authorize_parameters"], "the spec documents `scope`"

    # ADR-005, the threat model and the setup runbook name the same three scopes.
    for document in (
        "docs/adr/ADR-005-supabase-oauth-scopes.md",
        "docs/threat-model.md",
        "docs/runbooks/supabase-setup.md",
    ):
        text = (REPO_ROOT / document).read_text(encoding="utf-8")
        for scope in SCOPES:
            assert f"`{scope}`" in text, (document, scope)

    # Nothing reachable with these scopes returns a secret key: the API-keys endpoint needs
    # secrets:read, and rest:read (which can return the JWT secret) isn't among them.
    reachable = {
        e for endpoints in CONTRACT["reachable_with_postern_scopes"].values() for e in endpoints
    }
    assert not any("api-keys" in endpoint or "/postgrest" in endpoint for endpoint in reachable)


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-06 · prevention lands in Phase 4"
)
def test_supabase_token_cannot_read_api_keys() -> None:
    """A live test confirms a real Postern token gets a refusal from GET /v1/projects/{ref}/api-
    keys."""
    raise NotImplementedError


def test_supabase_client_calls_only_allowlisted_endpoints() -> None:
    """The Supabase client implements only allowlisted endpoints: GETs, plus the read-only
    query's POST. Endpoints inside a granted scope that Postern doesn't need (SQL snippets,
    backups, pooler config, temporary database access) aren't callable."""
    # Each allowlisted endpoint exists in the spec, and needs only a scope Postern holds.
    for method, path in ALLOWED_ENDPOINTS:
        entry = CONTRACT["endpoints"][f"{method} {path}"]
        assert entry["x-oauth-scope"] in SCOPES, (method, path)
        assert method == "GET" or path.endswith("/database/query/read-only")

    # Endpoints the scopes reach but Postern must never call aren't on the list.
    reachable = {
        e for endpoints in CONTRACT["reachable_with_postern_scopes"].values() for e in endpoints
    }
    never = {
        e
        for e in reachable
        if re.search(r"snippets|backups|pooler|pgbouncer|/database/jit$|network-bans|restore", e)
    }
    assert "POST /v1/projects/{ref}/database/jit" in never
    assert never.isdisjoint({f"{m} {p}" for m, p in ALLOWED_ENDPOINTS})

    # Every public method of the client stays on the list.
    fake = FakeSupabase(REF, [])
    client = ManagementClient("token", fake.client(), STATEMENTS)
    for name, bound in inspect.getmembers(client, inspect.ismethod):
        if name.startswith("_"):
            continue
        args = {"ref": REF, "statement_name": "relations"}
        params = inspect.signature(bound).parameters
        # The fake doesn't serve every endpoint; the request is recorded either way.
        with contextlib.suppress(ManagementError):
            bound(**{k: v for k, v in args.items() if k in params})
    templates = {(r.method, r.url.path.replace(REF, "{ref}")) for r in fake.requests}
    assert templates <= ALLOWED_ENDPOINTS
    assert templates == ALLOWED_ENDPOINTS, "every allowlisted endpoint is used by a method"

    # And the private request path refuses anything else before sending it.
    with pytest.raises(PermissionError):
        client._call("GET", "/v1/projects/{ref}/api-keys", REF)
    with pytest.raises(PermissionError):
        client._call("POST", "/v1/projects/{ref}/database/jit", REF)
