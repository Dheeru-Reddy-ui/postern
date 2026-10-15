"""The key a project owner pastes for exposure probes (TM-04, TM-06).

Only a publishable key or a legacy anon key is accepted. Secret keys and service_role keys
are rejected before anything is stored or logged; the rejection message never repeats the
key. The database enforces the same rule (postern.supabase_key_kind).
"""

import base64
import json
from typing import Any, Literal

import httpx
from psycopg import Connection

KeyKind = Literal["publishable", "legacy_anon"]


class KeyRejected(ValueError):
    pass


def _jwt_claims(key: str) -> dict[str, object] | None:
    parts = key.split(".")
    if len(parts) != 3 or not key.startswith("eyJ"):
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


def classify_key(key: str) -> KeyKind:
    """Accept only public keys. Raises KeyRejected, without echoing the key, otherwise."""
    key = key.strip()
    if key.startswith("sb_secret_"):
        raise KeyRejected(
            "That's a secret key. Postern never accepts secret keys: paste the publishable"
            " (or anon) key instead. Consider rotating the secret key, since it was copied."
        )
    if key.startswith("sb_publishable_"):
        return "publishable"
    claims = _jwt_claims(key)
    if claims is not None:
        if claims.get("role") == "anon":
            return "legacy_anon"
        if claims.get("role") == "service_role":
            raise KeyRejected(
                "That's the service_role key, which can bypass every security rule. Postern"
                " never accepts it: paste the anon key instead, and consider rotating it."
            )
    raise KeyRejected("That doesn't look like a Supabase publishable or anon key.")


def key_belongs_to_project(key: str, project_ref: str, http: httpx.Client) -> bool:
    """True only if the key is valid for this project (TM-04)."""
    claims = _jwt_claims(key)
    if claims is not None:
        return claims.get("ref") == project_ref
    response = http.get(
        f"https://{project_ref}.supabase.co/rest/v1/",
        headers={"apikey": key, "Accept": "application/openapi+json"},
    )
    return response.status_code not in (401, 403)


def store_public_key(
    conn: Connection[Any], connection_id: str, key: str, http: httpx.Client
) -> KeyKind:
    """Classify the pasted key, check it belongs to the connected project, then store it.

    Raises KeyRejected (whose message never contains the key) if any check fails; nothing
    is stored in that case.
    """
    key = key.strip()
    row = conn.execute(
        "select supabase_project_ref from postern.connections"
        " where id = %s and kind = 'supabase_project'",
        (connection_id,),
    ).fetchone()
    if row is None:
        raise KeyRejected("There's no connected project with that id.")
    kind = classify_key(key)
    if not key_belongs_to_project(key, str(row[0]), http):
        raise KeyRejected("That key isn't valid for this project.")
    conn.execute("select postern.set_publishable_key(%s, %s)", (connection_id, key))
    return kind
