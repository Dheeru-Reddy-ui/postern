"""Passwords for Postern's own component roles (ADR-012).

A component's password is generated here, sent to Postgres only as a SCRAM-SHA-256 verifier
(RFC 5802, RFC 7677), so the plaintext can't appear in any server-side log, and goes into
Secret Manager only as part of the component's connection string. Nothing is printed.
"""

import base64
import hashlib
import hmac
import os
import secrets
from urllib.parse import quote, urlsplit, urlunsplit

COMPONENT_ROLES = {
    "postern_api": "control-plane-db-url-api",
    "postern_worker": "control-plane-db-url-worker",
}
SCRAM_ITERATIONS = 4096  # Postgres's default (scram_iterations)


def new_password() -> str:
    return secrets.token_urlsafe(32)


def scram_sha256_verifier(
    password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """The value Postgres stores in pg_authid.rolpassword for this password."""
    salt = salt if salt is not None else os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def component_url(operator_url: str, role: str, password: str) -> str:
    """The operator's connection string, rewritten to log in as `role`.

    Supabase's pooler names users `<role>.<project ref>`; a direct connection uses the bare
    role name. Host, port and database are kept; TLS is required.
    """
    if role not in COMPONENT_ROLES:
        raise ValueError(f"{role} is not a Postern component role")
    parts = urlsplit(operator_url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ValueError("not a Postgres connection string")
    user = parts.username or ""
    _, dot, project_ref = user.partition(".")
    login = f"{role}.{project_ref}" if dot else role
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(login, safe='.')}:{quote(password, safe='')}@{parts.hostname}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/postgres", "sslmode=require", ""))
