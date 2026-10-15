"""The token broker (ADR-012): the worker gets short-lived access tokens, nothing more.

postern-api holds the OAuth client secret and the token-encryption key. When the worker needs
to call the Management API for a connection, it asks the API, authenticating with its own
Google-signed identity token. The API decrypts the stored tokens, refreshes them if needed,
checks the temporary-access guard (ADR-005), and hands back only the current access token.
So the worker, which also runs LLM code, never holds the client secret, refresh tokens or the
encryption key (TM-05, TM-28).
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from psycopg import Connection

from postern.integrations.supabase_mgmt.client import ManagementClient, Unauthorized
from postern.integrations.supabase_mgmt.oauth import OAuthError, refresh
from postern.integrations.supabase_mgmt.tokens import TokenCipher


class ConnectionUnavailable(Exception):
    """The connection is revoked, errored or refused (the reason is in the message)."""


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    project_ref: str
    expires_at: datetime


class TokenService:
    """API-side broker logic. Runs as postern_api."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[Connection[Any]]],
        cipher: TokenCipher,
        http: httpx.Client,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._connect = connect
        self._cipher = cipher
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret

    def _refuse(
        self, conn: Connection[Any], connection_id: str, status: str, reason: str
    ) -> ConnectionUnavailable:
        conn.execute(
            "select postern.set_connection_status(%s, %s, %s)", (connection_id, status, reason)
        )
        conn.commit()
        return ConnectionUnavailable(reason)

    def access_token_for(self, connection_id: str) -> IssuedToken:
        with self._connect() as conn:
            # One refresh at a time per connection, since refresh tokens may rotate.
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (connection_id,))
            row = conn.execute(
                "select status, supabase_project_ref, access_token_ciphertext,"
                " refresh_token_ciphertext, token_expires_at from postern.connections"
                " where id = %s and kind = 'supabase_project'",
                (connection_id,),
            ).fetchone()
            if row is None:
                raise ConnectionUnavailable("connection_not_found")
            status, ref, access_ct, refresh_ct, expires_at = row
            if status != "active" or access_ct is None:
                raise ConnectionUnavailable(f"connection_{status}")

            access = self._cipher.decrypt(bytes(access_ct))
            if expires_at is None or expires_at - datetime.now(UTC) < timedelta(minutes=5):
                if refresh_ct is None:
                    raise self._refuse(conn, connection_id, "revoked", "no_refresh_token")
                try:
                    tokens = refresh(
                        self._http,
                        self._client_id,
                        self._client_secret,
                        self._cipher.decrypt(bytes(refresh_ct)),
                    )
                except OAuthError:
                    raise self._refuse(conn, connection_id, "revoked", "refresh_failed") from None
                conn.execute(
                    "select postern.store_refreshed_tokens(%s, %s, %s, %s, %s)",
                    (
                        connection_id,
                        self._cipher.encrypt(tokens.access_token),
                        self._cipher.encrypt(tokens.refresh_token)
                        if tokens.refresh_token
                        else None,
                        self._cipher.key_version,
                        tokens.expires_at,
                    ),
                )
                access, expires_at = tokens.access_token, tokens.expires_at

            client = ManagementClient(access, self._http, allowed_statements={})
            try:
                state = client.temporary_access_state(str(ref))
            except Unauthorized:
                raise self._refuse(conn, connection_id, "revoked", "token_rejected") from None
            if state == "enabled":
                raise self._refuse(conn, connection_id, "error", "temporary_access_enabled")
            conn.commit()
            return IssuedToken(access, str(ref), expires_at)


class BrokerClient:
    """Worker-side: fetches access tokens from postern-api with the worker's identity."""

    def __init__(
        self, api_url: str, http: httpx.Client, identity_token: Callable[[str], str]
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._http = http
        self._identity_token = identity_token

    def access_token(self, connection_id: str) -> IssuedToken:
        response = self._http.post(
            f"{self._api_url}/internal/supabase-token",
            json={"connection_id": connection_id},
            headers={"Authorization": f"Bearer {self._identity_token(self._api_url)}"},
            timeout=30,
        )
        if response.status_code == 409:
            raise ConnectionUnavailable(str(response.json().get("reason", "refused")))
        response.raise_for_status()
        body = response.json()
        return IssuedToken(
            str(body["access_token"]),
            str(body["project_ref"]),
            datetime.fromisoformat(str(body["expires_at"])),
        )


def google_identity_token(audience: str) -> str:
    """An identity token for the running service account, from the metadata server."""
    import google.auth.transport.requests
    import google.oauth2.id_token

    return str(
        google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)
    )
