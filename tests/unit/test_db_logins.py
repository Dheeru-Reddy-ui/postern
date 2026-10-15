"""Component database logins: SCRAM verifiers and connection strings (ADR-012)."""

from typing import Any
from urllib.parse import unquote, urlsplit

import psycopg
import pytest
from psycopg import sql

from postern.core.db_logins import component_url, new_password, scram_sha256_verifier

OPERATOR = (
    "postgresql://postgres.abcdefghijklmnopqrst:operator-password"
    "@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)


def test_component_url_changes_only_the_login() -> None:
    url = component_url(OPERATOR, "postern_worker", "p@ss/w:rd")
    parts = urlsplit(url)
    assert parts.username == "postern_worker.abcdefghijklmnopqrst"
    assert unquote(parts.password or "") == "p@ss/w:rd"
    assert (parts.hostname, parts.port, parts.path) == (
        "aws-0-us-east-1.pooler.supabase.com",
        5432,
        "/postgres",
    )
    assert parts.query == "sslmode=require"
    assert "operator-password" not in url
    with pytest.raises(ValueError, match="not a Postern component role"):
        component_url(OPERATOR, "postgres", "x")


def test_verifier_is_what_postgres_stores_and_lets_the_role_log_in(database_url: str) -> None:
    """Sets postern_api's password on the local test database (only the verifier is sent),
    logs in with the plaintext, then removes the password again."""
    password = new_password()
    verifier = scram_sha256_verifier(password)
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert password not in verifier

    target = urlsplit(database_url)
    login: dict[str, Any] = {"host": target.hostname, "port": target.port, "dbname": "postgres"}
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("alter role postern_api with login password {}").format(sql.Literal(verifier))
        )
        try:
            with psycopg.connect(**login, user="postern_api", password=password) as conn:
                assert conn.execute("select current_user").fetchone() == ("postern_api",)
            with pytest.raises(psycopg.OperationalError):
                psycopg.connect(**login, user="postern_api", password="not-the-password")
        finally:
            admin.execute("alter role postern_api password null")
