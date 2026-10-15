"""Supabase OAuth with PKCE (TM-20).

Endpoints and parameters are from Supabase's "Build a Supabase OAuth integration" guide and
the Management API OpenAPI spec (both checked 2026-10-14): authorize at /v1/oauth/authorize
with response_type=code, state, an S256 code challenge and an optional space-separated
`scope`; exchange at /v1/oauth/token with HTTP Basic client authentication.

The scopes are fixed on the OAuth app itself (ADR-005), and Postern also requests exactly
those scopes on every authorization, so a misconfigured app can't silently widen a grant.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://api.supabase.com/v1/oauth/authorize"
TOKEN_URL = "https://api.supabase.com/v1/oauth/token"  # noqa: S105 -- a URL, not a secret

# ADR-005. Nothing that can return a secret (secrets:*, rest:read, auth:read) and nothing
# that can write (*:write).
SCOPES = ("projects:read", "database:read", "storage:read")


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


class OAuthError(Exception):
    pass


def new_pkce() -> Pkce:
    verifier = secrets.token_urlsafe(64)  # 86 characters, inside RFC 7636's 43-128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return Pkce(verifier, challenge)


def new_state() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def authorize_url(client_id: str, redirect_uri: str, state: str, pkce: Pkce) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def _token_request(
    http: httpx.Client, client_id: str, client_secret: str, form: dict[str, str]
) -> TokenSet:
    response = http.post(
        TOKEN_URL,
        data=form,
        auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        raise OAuthError(f"token endpoint returned HTTP {response.status_code}")
    body = response.json()
    if "access_token" not in body:
        raise OAuthError("token response has no access_token")
    expires_in = int(body.get("expires_in", 3600))
    return TokenSet(
        access_token=str(body["access_token"]),
        refresh_token=body.get("refresh_token"),
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


def exchange_code(
    http: httpx.Client,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> TokenSet:
    return _token_request(
        http,
        client_id,
        client_secret,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
    )


def refresh(http: httpx.Client, client_id: str, client_secret: str, refresh_token: str) -> TokenSet:
    return _token_request(
        http,
        client_id,
        client_secret,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
