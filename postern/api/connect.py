"""Connecting a Supabase project (OAuth with PKCE), and the token broker (ADR-012).

  GET  /connect/supabase?token=...    single-use link the operator issued (Phase 8's web
                                      onboarding replaces it)
  GET  /oauth/supabase/callback       Supabase redirects here after the owner consents
  POST /internal/supabase-token       the worker asks for a short-lived access token

TM-20: the OAuth state is single-use, short-lived, stored only as a hash, and bound to the
browser by an HttpOnly cookie; PKCE (S256) protects the code exchange. TM-04: the project
must appear in the owner's own project list before a connection is saved, and projects with
temporary database access enabled are refused (ADR-005).
"""

import hmac
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import psycopg
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from postern.integrations.supabase_mgmt.broker import ConnectionUnavailable, TokenService
from postern.integrations.supabase_mgmt.client import ManagementClient, Unauthorized
from postern.integrations.supabase_mgmt.oauth import (
    OAuthError,
    authorize_url,
    exchange_code,
    new_pkce,
    new_state,
    sha256_hex,
)
from postern.integrations.supabase_mgmt.tokens import TokenCipher

STATE_COOKIE = "postern_oauth_state"
router = APIRouter()


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_url: str
    client_id: str
    client_secret: str
    worker_identity: str
    cipher: TokenCipher

    @property
    def redirect_uri(self) -> str:
        return f"{self.api_url}/oauth/supabase/callback"


@lru_cache(maxsize=1)
def settings() -> Settings:
    def env(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise HTTPException(503, "postern-api is not configured for connections yet")
        return value

    return Settings(
        database_url=env("POSTERN_DATABASE_URL"),
        api_url=env("POSTERN_API_URL").rstrip("/"),
        client_id=env("POSTERN_SUPABASE_CLIENT_ID"),
        client_secret=env("POSTERN_SUPABASE_CLIENT_SECRET"),
        worker_identity=env("POSTERN_WORKER_SA_EMAIL"),
        cipher=TokenCipher.from_env(),
    )


def http_client() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30) as client:
        yield client


Database = Callable[[], AbstractContextManager[psycopg.Connection[Any]]]


def database(cfg: Settings = Depends(settings)) -> Database:
    """Opens a connection as postern_api (a dependency, so tests can substitute it)."""
    return lambda: psycopg.connect(cfg.database_url, prepare_threshold=None)


def verify_google_identity(token: str, audience: str) -> str:
    """The email of the Google service account that signed this identity token."""
    import google.auth.transport.requests
    import google.oauth2.id_token

    claims: dict[str, Any] = google.oauth2.id_token.verify_oauth2_token(
        token, google.auth.transport.requests.Request(), audience=audience
    )
    if not claims.get("email_verified"):
        raise ValueError("identity email not verified")
    return str(claims["email"])


IdentityVerifier = Callable[[str, str], str]


def identity_verifier() -> IdentityVerifier:
    return verify_google_identity


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        f"<body style='font-family:system-ui;max-width:36rem;margin:3rem auto;line-height:1.5'>"
        f"<h1>{title}</h1><p>{body}</p></body>",
        status_code=status,
    )


@router.get("/connect/supabase")
def connect_start(
    token: str = Query(min_length=20, max_length=200),
    cfg: Settings = Depends(settings),
    db: Database = Depends(database),
) -> Any:
    state, pkce = new_state(), new_pkce()
    try:
        with db() as conn:
            conn.execute(
                "select postern.start_oauth(%s, %s, %s)",
                (sha256_hex(token), sha256_hex(state), pkce.verifier),
            )
    except psycopg.errors.InvalidAuthorizationSpecification:
        return _page(
            "This link doesn't work",
            "It has already been used or has expired. Ask for a new one.",
            400,
        )
    response = RedirectResponse(
        authorize_url(cfg.client_id, cfg.redirect_uri, state, pkce), status_code=302
    )
    response.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True, secure=True, samesite="lax", path="/oauth"
    )
    return response


@router.get("/oauth/supabase/callback")
def connect_callback(
    code: str = Query(min_length=1, max_length=2048),
    state: str = Query(min_length=20, max_length=200),
    cookie_state: str | None = Cookie(default=None, alias=STATE_COOKIE),
    cfg: Settings = Depends(settings),
    http: httpx.Client = Depends(http_client),
    db: Database = Depends(database),
) -> Any:
    if cookie_state is None or not hmac.compare_digest(cookie_state, state):
        return _page(
            "Please start again",
            "This sign-in didn't start in this browser, or took too long.",
            400,
        )
    with db() as conn:
        try:
            row = conn.execute(
                "select user_id::text, project_ref, code_verifier from postern.finish_oauth(%s)",
                (sha256_hex(state),),
            ).fetchone()
        except psycopg.errors.InvalidAuthorizationSpecification:
            return _page("Please start again", "This sign-in link was already used.", 400)
        conn.commit()
        if row is None:
            return _page("Please start again", "This sign-in link was already used.", 400)
        user_id, project_ref, verifier = row
        try:
            tokens = exchange_code(
                http, cfg.client_id, cfg.client_secret, code, verifier, cfg.redirect_uri
            )
        except OAuthError:
            return _page(
                "Supabase didn't confirm the connection",
                "Nothing was saved. Please try the link again.",
                400,
            )

        client = ManagementClient(tokens.access_token, http, allowed_statements={})
        try:
            owned = {p.get("ref") for p in client.list_projects()}
            if project_ref not in owned:
                return _page(
                    "That project isn't in your account",
                    "Postern only connects to projects the person signing in can "
                    "see. Nothing was saved.",
                    403,
                )
            if client.temporary_access_state(project_ref) == "enabled":
                return _page(
                    "Temporary database access is turned on",
                    "This project lets people sign into its database with access "
                    "tokens. To keep Postern unable to write to your database, it "
                    "won't connect while that's on. Nothing was saved.",
                    409,
                )
        except Unauthorized:
            return _page("Supabase rejected the connection", "Nothing was saved.", 400)

        conn.execute(
            "select postern.save_connection(%s, %s, %s, %s, %s, %s)",
            (
                user_id,
                project_ref,
                cfg.cipher.encrypt(tokens.access_token),
                cfg.cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
                cfg.cipher.key_version,
                tokens.expires_at,
            ),
        )
        conn.commit()
    response = _page(
        "Connected",
        "Postern can now read your project's security checks. It can't change "
        "anything in it. You can close this tab.",
    )
    response.delete_cookie(STATE_COOKIE, path="/oauth")
    return response


class TokenRequest(BaseModel):
    connection_id: str


@router.post("/internal/supabase-token")
def broker_token(
    body: TokenRequest,
    authorization: str = Header(default=""),
    cfg: Settings = Depends(settings),
    http: httpx.Client = Depends(http_client),
    verify: IdentityVerifier = Depends(identity_verifier),
    db: Database = Depends(database),
) -> Any:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        caller = verify(token, cfg.api_url)
    except Exception:
        raise HTTPException(401, "invalid identity") from None
    if not hmac.compare_digest(caller, cfg.worker_identity):
        raise HTTPException(403, "only postern-worker may request tokens")
    service = TokenService(
        db,
        cfg.cipher,
        http,
        cfg.client_id,
        cfg.client_secret,
    )
    try:
        issued = service.access_token_for(body.connection_id)
    except ConnectionUnavailable as error:
        return JSONResponse({"reason": str(error)}, status_code=409)
    return {
        "access_token": issued.access_token,
        "project_ref": issued.project_ref,
        "expires_at": issued.expires_at.isoformat(),
    }
